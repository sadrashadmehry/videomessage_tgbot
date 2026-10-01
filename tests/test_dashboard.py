import asyncio
from types import SimpleNamespace

from aiohttp import BasicAuth
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.dashboard import create_app, password_hash
from bot.services.storage_service import StatsStorage


def test_dashboard_auth_history_download_and_safe_cleanup(tmp_path):
    async def check():
        storage = StatsStorage(str(tmp_path / "bot.sqlite3"))
        user = SimpleNamespace(id=123, first_name="<script>test</script>", last_name=None, username="tester", language_code="en", is_premium=False)
        storage.upsert_user(user, increment_start=True)
        for status in ("completed", "failed", "received"):
            job = storage.create_job(user_id=123, chat_id=123, source_message_id=1, source_kind="video", source_file_id="secret-file-id", source_file_unique_id=None, source_file_size=10, source_mime_type="video/mp4", prompt_text="hello")
            with storage._connect() as db:
                db.execute("UPDATE jobs SET status=? WHERE id=?", (status, job))
        storage.record_prompt(user_id=123, chat_id=123, message_id=1, text="private prompt")
        class FakeBot:
            async def download(self, file_id, destination):
                destination.write_bytes(b"0123456789")
            @property
            def session(self):
                return self
            async def close(self):
                pass
        app = create_app(Config(bot_token="fake", database_path=str(storage.path)), "admin", password_hash("test-password"), FakeBot())
        async with TestClient(TestServer(app)) as client:
            for path in ("/", "/assets/app.js", "/api/overview", "/media/1/source"):
                assert (await client.get(path)).status == 401
            auth = BasicAuth("admin", "test-password")
            result = await (await client.get("/api/overview", auth=auth)).json()
            assert result["stats"]["success_rate"] == 50
            assert result["stats"]["pending"] == 1
            history = await (await client.get("/api/users/123", auth=auth)).json()
            assert history["prompts"][0]["text"] == "private prompt"
            assert "secret-file-id" not in str(history)
            response = await client.get("/media/1/source?download=1", auth=auth)
            assert response.status == 200
            assert await response.read() == b"0123456789"
            assert "attachment" in response.headers["Content-Disposition"]
            response = await client.get("/media/1/source", auth=auth, headers={"Range": "bytes=2-5"})
            assert response.status == 206 and await response.read() == b"2345"
            assert (await client.delete("/api/cache", auth=auth)).status == 403
            headers = {"X-CSRF-Token": result["csrf"]}
            assert (await client.delete("/api/cache", auth=auth, headers={**headers, "Origin": "https://evil.example"})).status == 403
            exports = tmp_path / "exports"
            exports.mkdir()
            (exports / "job-1-source.bin").write_bytes(b"export")
            (exports / "keep.txt").write_text("keep")
            working = tmp_path / "tmp"
            working.mkdir()
            (working / "active.mp4").write_bytes(b"active")
            deleted = await (await client.delete("/media/1/source", auth=auth, headers=headers)).json()
            assert deleted["removed_bytes"] == 16
            await client.get("/media/1/source", auth=auth)
            await client.delete("/api/cache", auth=auth, headers=headers)
            assert not list((tmp_path / "dashboard-cache").iterdir())
            assert storage.get_job(1)["source_file_id"] == "secret-file-id"
            assert (exports / "keep.txt").is_file() and (working / "active.mp4").is_file()
    asyncio.run(check())
