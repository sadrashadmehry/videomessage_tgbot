"""Worker-first Telegram transport, with a private SOCKS proxy as fallback."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from urllib.parse import urlsplit

from aiohttp import ClientError, TraceConfig
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


class MeteredSession(AiohttpSession):
    def __init__(self, route, stats_db=None, **kwargs):
        super().__init__(**kwargs)
        self.route, self.stats_db = route, stats_db
        self.trace = TraceConfig()
        self.trace.on_request_chunk_sent.append(self.sent)
        self.trace.on_response_chunk_received.append(self.received)
        self.trace.on_request_end.append(self.finished)
        self.trace.on_request_exception.append(self.finished)
        self.trace.freeze()

    async def create_session(self):
        session = await super().create_session()
        if self.stats_db and self.trace not in session.trace_configs:
            session.trace_configs.append(self.trace)
        return session

    async def sent(self, session, context, params):
        context.uploaded = getattr(context, 'uploaded', 0) + len(params.chunk)

    async def received(self, session, context, params):
        self.stats_db.record_traffic(self.route, downloaded=len(params.chunk))

    async def finished(self, session, context, params):
        self.stats_db.record_traffic(self.route, uploaded=getattr(context, 'uploaded', 0), requests=1)


class WorkerSession(MeteredSession):
    def __init__(self, url: str, secret: str, stats_db=None):
        super().__init__('cloudflare', stats_db, api=TelegramAPIServer(
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
    def __init__(self, worker_url: str = '', worker_secret: str = '', proxy_url: str = '', stats_db=None):
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
        self.worker = WorkerSession(worker_url, worker_secret, stats_db) if worker_url else None
        self.fallback_route = 'proxy' if proxy_url else 'direct'
        self.fallback = MeteredSession(self.fallback_route, stats_db, proxy=proxy_url or None)
        self.stats_db = stats_db
        self.worker_url = worker_url
        self.blocked_until = 0.0
        self.proxy_blocked_until = 0.0

    def worker_available(self):
        return self.worker is not None and time.time() >= self.blocked_until

    def block_worker(self, quota=False):
        now = time.time()
        until = (int(now) // 86400 + 1) * 86400 if quota else now + 60
        self.blocked_until = max(self.blocked_until, until)
        logger.warning('Using fallback proxy: %s', 'Worker quota exhausted until midnight UTC' if quota else 'Worker unavailable; retrying in 60 seconds')

    async def make_request(self, bot, method, timeout=None):
        proxy_error = None
        if self.stats_db and self.stats_db.route_priority() == 'proxy' and time.time() >= self.proxy_blocked_until:
            try:
                return await self.fallback.make_request(bot, method, timeout)
            except (TelegramNetworkError, TelegramServerError, ClientDecodeError) as error:
                self.proxy_blocked_until = time.time() + 60
                if not self.worker_available() or method.__api_method__ not in ('getMe', 'getFile', 'getUpdates', 'deleteWebhook'):
                    raise
                proxy_error = error
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
        if proxy_error:
            raise proxy_error
        return await self.fallback.make_request(bot, method, timeout)

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        telegram_prefix = 'https://api.telegram.org/file/bot'
        if not url.startswith(telegram_prefix):
            raise ValueError('Only Telegram file downloads are supported')
        file_path = url[len(telegram_prefix):].split('/', 1)[1]
        proxy_error = None
        if self.stats_db and self.stats_db.route_priority() == 'proxy' and time.time() >= self.proxy_blocked_until:
            downloaded = 0
            try:
                async for chunk in self.fallback.stream_content(url, headers, timeout, chunk_size, raise_for_status):
                    downloaded += len(chunk)
                    yield chunk
                return
            except (ClientError, asyncio.TimeoutError) as error:
                self.proxy_blocked_until = time.time() + 60
                if downloaded or not self.worker_available():
                    raise
                proxy_error = error
            finally:
                self.stats_db.record_traffic(self.fallback_route, downloaded=downloaded)
        if self.worker_available():
            received = False
            downloaded = 0
            try:
                async for chunk in self.worker.stream_content(
                    self.worker_url + '/file/' + file_path, headers, timeout, chunk_size, raise_for_status,
                ):
                    received = True
                    downloaded += len(chunk)
                    yield chunk
                return
            except WorkerQuotaExceeded:
                self.block_worker(quota=True)
            except (ClientError, asyncio.TimeoutError):
                self.block_worker()
                if received:
                    # Restarting after yielding bytes would corrupt the destination file.
                    raise
            finally:
                if self.stats_db:
                    self.stats_db.record_traffic('cloudflare', downloaded=downloaded)
        if proxy_error:
            raise proxy_error
        downloaded = 0
        try:
            async for chunk in self.fallback.stream_content(url, headers, timeout, chunk_size, raise_for_status):
                downloaded += len(chunk)
                yield chunk
        finally:
            if self.stats_db:
                self.stats_db.record_traffic(self.fallback_route, downloaded=downloaded)

    async def close(self):
        await self.fallback.close()
        if self.worker:
            await self.worker.close()
