import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestServer

from bot.config import Config
from bot.services import storage_service
from bot.services.storage_service import StatsStorage
from bot.transport import MeteredSession
from bot.utils.validators import exceeds_download_limit


def test_daily_quota_is_atomic_persistent_and_resets_at_tehran_midnight(tmp_path, monkeypatch):
    now = datetime(2026, 10, 1, 20, 29, tzinfo=timezone.utc)
    window = storage_service.daily_window
    monkeypatch.setattr(storage_service, "daily_window", lambda supplied=None: window(supplied or now))
    monkeypatch.setattr(storage_service, "_utc_now", lambda: now.isoformat(timespec="seconds"))
    storage = StatsStorage(str(tmp_path / "bot.sqlite3"))
    for user_id in (1, 2):
        storage.upsert_user(SimpleNamespace(id=user_id, first_name="test", last_name=None, username=None, language_code=None, is_premium=False))
    def create(user_id):
        return storage.create_job(user_id=user_id, chat_id=user_id, source_message_id=1, source_kind="video", source_file_id="file", source_file_unique_id=None, source_file_size=10, source_mime_type="video/mp4", prompt_text=None, daily_limit=10)
    with ThreadPoolExecutor(max_workers=4) as pool:
        attempts = list(pool.map(create, [1] * 12))
    assert sum(job is not None for job in attempts) == 10
    assert create(2) is not None
    reopened = StatsStorage(str(storage.path))
    quota = reopened.daily_quota(1, 10, now)
    assert quota["remaining"] == 0 and quota["reset_at"] == "2026-10-02 00:00"
    now = datetime(2026, 10, 1, 20, 30, tzinfo=timezone.utc)
    assert reopened.daily_quota(1, 10, now)["remaining"] == 10
    assert create(1) is not None
    assert reopened.daily_quota(1, 10, now)["remaining"] == 9
    assert Config(bot_token="test").max_download_size_mb == 12
    assert not exceeds_download_limit(12 * 1024 * 1024, 12)
    assert exceeds_download_limit(12 * 1024 * 1024 + 1, 12)


def test_http_traffic_tracks_actual_payloads_per_route(tmp_path):
    async def check():
        storage = StatsStorage(str(tmp_path / "bot.sqlite3"))
        async def receive(request):
            assert await request.read() == b"payload"
            return web.Response(body=b"response")
        app = web.Application()
        app.router.add_post("/", receive)
        async with TestServer(app) as server:
            for route in ("cloudflare", "proxy"):
                metered = MeteredSession(route, storage)
                try:
                    client = await metered.create_session()
                    async with client.post(server.make_url("/"), data=b"payload") as response:
                        assert await response.read() == b"response"
                finally:
                    await metered.close()
        with storage._connect() as conn:
            rows = conn.execute("SELECT route,uploaded,downloaded,requests FROM traffic ORDER BY route").fetchall()
        assert [tuple(row) for row in rows] == [("cloudflare", 7, 8, 1), ("proxy", 7, 8, 1)]
    asyncio.run(check())
