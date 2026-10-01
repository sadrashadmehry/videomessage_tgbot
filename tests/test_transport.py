import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientConnectionError
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import GetMe, SendMessage

from bot.transport import RoutedSession, WorkerQuotaExceeded, check_worker_quota
from bot.services.storage_service import StatsStorage


def test_persistent_priority_switch_and_proxy_failover(tmp_path):
    async def run():
        storage = StatsStorage(str(tmp_path / 'bot.sqlite3'))
        session = RoutedSession('https://relay.example', 'secret', 'socks5://localhost:1080', storage)
        bot = Bot('123456:TEST', session=session)
        session.worker.make_request = AsyncMock(return_value='worker')
        session.fallback.make_request = AsyncMock(return_value='proxy')
        assert await session.make_request(bot, GetMe()) == 'proxy'
        session.worker.make_request.assert_not_called()
        session.fallback.make_request.side_effect = TelegramNetworkError(method=GetMe(), message='unavailable')
        assert await session.make_request(bot, GetMe()) == 'worker'
        session.proxy_blocked_until = 0
        with pytest.raises(TelegramNetworkError):
            await session.make_request(bot, SendMessage(chat_id=1,text='test'))
        assert session.worker.make_request.await_count == 1  # uncertain writes are not replayed
        storage.set_route_priority('cloudflare')
        assert StatsStorage(str(storage.path)).route_priority() == 'cloudflare'
        assert await session.make_request(bot, GetMe()) == 'worker'
        storage.set_route_priority('proxy')
        session.proxy_blocked_until = 0
        async def failed(*args):
            raise ClientConnectionError('unavailable')
            yield b''
        async def partial(*args):
            yield b'partial'
            raise ClientConnectionError('lost')
        async def complete(*args):
            yield b'complete'
        session.fallback.stream_content = failed
        session.worker.stream_content = complete
        url='https://api.telegram.org/file/bot123456:TEST/videos/file_1.mp4'
        assert b''.join([c async for c in session.stream_content(url)]) == b'complete'
        session.fallback.stream_content = partial
        session.proxy_blocked_until = 0
        with pytest.raises(ClientConnectionError):
            async for _ in session.stream_content(url):
                pass
        await session.close()
    asyncio.run(run())


def test_routing_and_download_failover():
    async def run():
        session = RoutedSession('https://relay.example', 'secret', 'socks5://localhost:1080')
        bot = Bot('123456:TEST', session=session)
        method = SendMessage(chat_id=1, text='test')
        session.worker.make_request = AsyncMock(return_value='worker')
        session.fallback.make_request = AsyncMock(return_value='proxy')
        assert await session.make_request(bot, method) == 'worker'
        session.fallback.make_request.assert_not_called()

        with patch('bot.transport.time.time', return_value=86410):
            session.worker.make_request.side_effect = WorkerQuotaExceeded
            assert await session.make_request(bot, method) == 'proxy'
            assert session.blocked_until == 172800
            session.worker.make_request.reset_mock(side_effect=True)
            assert await session.make_request(bot, method) == 'proxy'
            session.worker.make_request.assert_not_called()
        with patch('bot.transport.time.time', return_value=172800):
            assert await session.make_request(bot, method) == 'worker'

        session.blocked_until = 0
        session.worker.make_request.side_effect = TelegramRetryAfter(method=method, message='slow down', retry_after=1)
        session.fallback.make_request.reset_mock()
        with pytest.raises(TelegramRetryAfter):
            await session.make_request(bot, method)
        session.fallback.make_request.assert_not_called()
        assert session.blocked_until == 0

        session.worker.make_request.side_effect = TelegramNetworkError(method=method, message='timeout')
        with pytest.raises(TelegramNetworkError):
            await session.make_request(bot, method)
        session.fallback.make_request.assert_not_called()  # no duplicate send
        session.blocked_until = 0
        assert await session.make_request(bot, GetMe()) == 'proxy'

        urls = []
        async def quota(*args):
            raise WorkerQuotaExceeded
            yield b''
        async def download(url, *args):
            urls.append(url)
            yield b'complete'
        session.blocked_until = 0
        session.worker.stream_content = quota
        session.fallback.stream_content = download
        url = 'https://api.telegram.org/file/bot123456:TEST/videos/file_1.mp4'
        assert b''.join([chunk async for chunk in session.stream_content(url)]) == b'complete'
        assert urls == [url]

        async def interrupted(*args):
            yield b'partial'
            raise ClientConnectionError('lost connection')
        session.blocked_until = 0
        session.worker.stream_content = interrupted
        with pytest.raises(ClientConnectionError):
            async for _ in session.stream_content(url):
                pass
        assert urls == [url]  # never append a restarted file to partial bytes
        await session.close()
    asyncio.run(run())


def test_quota_detection_and_configuration():
    with pytest.raises(WorkerQuotaExceeded):
        check_worker_quota(429, '<html>Error 1027: daily quota exceeded</html>')
    check_worker_quota(429, '{"ok":false,"error_code":429,"description":"1027 seconds"}')
    check_worker_quota(200, '1027')
    for url in ('http://relay.example', 'https://relay.example/path', 'https://user:pass@relay.example'):
        with pytest.raises(ValueError):
            RoutedSession(url, 'secret', 'socks5://localhost:1080')
