"""Worker-first Telegram transport, with a private SOCKS proxy as fallback."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from urllib.parse import urlsplit

from aiohttp import ClientError
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.base import BaseSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import ClientDecodeError, TelegramNetworkError, TelegramServerError

logger = logging.getLogger(__name__)


class WorkerQuotaExceeded(Exception):
    pass


def check_worker_quota(status: int, body: str) -> None:
    # Telegram's JSON 429 is a chat/API rate limit, not the Worker daily quota.
    if status >= 400 and not body.lstrip().startswith('{') and re.search(r'\b1027\b', body):
        raise WorkerQuotaExceeded


class WorkerSession(AiohttpSession):
    def __init__(self, url: str, secret: str):
        super().__init__(api=TelegramAPIServer(
            base=url + '/api/{method}', file=url + '/file/{path}',
        ))
        self.secret = secret

    async def create_session(self):
        session = await super().create_session()
        session.headers['X-Relay-Secret'] = self.secret
        return session

    def check_response(self, bot, method, status_code, content):
        check_worker_quota(status_code, content)
        return super().check_response(bot, method, status_code, content)

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        session = await self.create_session()
        async with session.get(url, headers=headers, timeout=timeout, allow_redirects=False) as response:
            if response.status >= 400:
                body = (await response.content.read(65536)).decode(errors='replace')
                check_worker_quota(response.status, body)
                response.raise_for_status()
            async for chunk in response.content.iter_chunked(chunk_size):
                yield chunk


class RoutedSession(BaseSession):
    def __init__(self, worker_url: str = '', worker_secret: str = '', proxy_url: str = ''):
        super().__init__()
        worker_url = worker_url.rstrip('/')
        if worker_url:
            url = urlsplit(worker_url)
            if url.scheme != 'https' or not url.hostname or url.username or url.query or url.fragment or url.path:
                raise ValueError('WORKER_URL must be an HTTPS origin without a path or credentials')
            if not worker_secret:
                raise ValueError('WORKER_SECRET is required with WORKER_URL')
            if not proxy_url:
                raise ValueError('FALLBACK_PROXY_URL is required with WORKER_URL')
        if proxy_url and urlsplit(proxy_url).scheme not in ('socks5', 'socks4', 'http'):
            raise ValueError('FALLBACK_PROXY_URL must be a SOCKS or HTTP proxy URL')
        self.worker = WorkerSession(worker_url, worker_secret) if worker_url else None
        self.fallback = AiohttpSession(proxy=proxy_url or None)
        self.worker_url = worker_url
        self.blocked_until = 0.0

    def worker_available(self):
        return self.worker is not None and time.time() >= self.blocked_until

    def block_worker(self, quota=False):
        now = time.time()
        until = (int(now) // 86400 + 1) * 86400 if quota else now + 60
        self.blocked_until = max(self.blocked_until, until)
        logger.warning('Using fallback proxy: %s', 'Worker quota exhausted until midnight UTC' if quota else 'Worker unavailable; retrying in 60 seconds')

    async def make_request(self, bot, method, timeout=None):
        if self.worker_available():
            try:
                return await self.worker.make_request(bot, method, timeout)
            except WorkerQuotaExceeded:
                # Cloudflare rejected before execution: safe to resend even uploads.
                self.block_worker(quota=True)
            except (TelegramNetworkError, TelegramServerError, ClientDecodeError) as error:
                self.block_worker()
                # An upload may already have reached Telegram. Never blindly replay it.
                if method.__api_method__ not in ('getMe', 'getFile', 'getUpdates', 'deleteWebhook'):
                    raise TelegramNetworkError(method=method, message='Worker request failed; delivery is uncertain') from error
        return await self.fallback.make_request(bot, method, timeout)

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        telegram_prefix = 'https://api.telegram.org/file/bot'
        if not url.startswith(telegram_prefix):
            raise ValueError('Only Telegram file downloads are supported')
        file_path = url[len(telegram_prefix):].split('/', 1)[1]
        if self.worker_available():
            received = False
            try:
                async for chunk in self.worker.stream_content(
                    self.worker_url + '/file/' + file_path, headers, timeout, chunk_size, raise_for_status,
                ):
                    received = True
                    yield chunk
                return
            except WorkerQuotaExceeded:
                self.block_worker(quota=True)
            except (ClientError, asyncio.TimeoutError):
                self.block_worker()
                if received:
                    # Restarting after yielding bytes would corrupt the destination file.
                    raise
        async for chunk in self.fallback.stream_content(url, headers, timeout, chunk_size, raise_for_status):
            yield chunk

    async def close(self):
        await self.fallback.close()
        if self.worker:
            await self.worker.close()
