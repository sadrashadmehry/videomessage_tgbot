"""Private, read-only activity dashboard with removable media downloads."""
import asyncio
import base64
import hashlib
import hmac
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import ssl
import time

from aiohttp import web
from aiogram import Bot
from aiogram.methods import GetMe

from bot.config import load_config
from bot.transport import RoutedSession
from bot.services.storage_service import StatsStorage

MEDIA = {"source": "source_file_id", "video_note": "video_note_file_id", "animation": "animation_file_id"}
ASSETS = Path(__file__).with_name("dashboard_static")


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 260000).hex()
    return f"pbkdf2_sha256$260000${salt}${digest}"


def create_app(config, username, hashed_password, bot=None):
    algorithm, iterations, salt, digest = hashed_password.split("$")
    if algorithm != "pbkdf2_sha256" or int(iterations) < 100000 or not username:
        raise ValueError("Invalid dashboard credentials")
    db = Path(config.database_path).resolve()
    stats_db = StatsStorage(str(db))
    cache = db.parent / "dashboard-cache"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    exports = db.parent / "exports"
    csrf = secrets.token_urlsafe(32)
    failures = {}
    lock = asyncio.Lock()  # ponytail: one admin; per-file locks if concurrent downloads matter.
    bot = bot or Bot(config.bot_token, session=RoutedSession(config.worker_url, config.worker_secret, config.fallback_proxy_url, stats_db))

    def query(sql, params=()):
        with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(sql, params)]

    def files():
        for folder in (cache, exports):
            if folder.is_symlink():
                continue
            for path in folder.glob("job-*-*.*"):
                if re.fullmatch(r"job-\d+-(source|video_note|animation)\.(bin|mp4|gif|webm|mov|mkv|jpg|png)", path.name) and path.is_file() and not path.is_symlink():
                    yield path

    def disk():
        saved = list(files())
        return {"files": len(saved), "bytes": sum(p.stat().st_size for p in saved), "free": shutil.disk_usage(db.parent).free}

    def page(request):
        try:
            return max(0, int(request.query.get("page", "0")))
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid page")

    @web.middleware
    async def security(request, handler):
        now = time.monotonic()
        peer = request.remote or "unknown"
        recent = [stamp for stamp in failures.get(peer, []) if now - stamp < 60]
        if len(recent) >= 10:
            raise web.HTTPTooManyRequests(text="Try again in a minute")
        valid = False
        try:
            scheme, encoded = request.headers.get("Authorization", "").split(" ", 1)
            user, password = base64.b64decode(encoded, validate=True).decode().split(":", 1)
            if scheme.lower() == "basic" and len(password) <= 256:
                calculated = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations)).hex()
                valid = hmac.compare_digest(user.encode(), username.encode()) and hmac.compare_digest(calculated, digest)
        except (ValueError, UnicodeError):
            pass
        if not valid:
            if len(failures) > 1024:
                failures.clear()
            failures[peer] = recent + [now]
            raise web.HTTPUnauthorized(headers={"WWW-Authenticate": 'Basic realm="Bot dashboard", charset="UTF-8"'})
        failures.pop(peer, None)
        if request.method in ("DELETE", "PATCH"):
            origin = request.headers.get("Origin")
            if (origin and origin != f"{request.scheme}://{request.host}") or not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), csrf):
                raise web.HTTPForbidden(text="Refresh the dashboard and try again")
        response = await handler(request)
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self'; img-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'"})
        return response

    async def overview(request):
        result = query("SELECT count(*) AS users, coalesce(sum(start_count),0) AS starts, coalesce(sum(gif_requests),0) AS gifs FROM users")[0]
        counts = {r["status"]: r["n"] for r in query("SELECT status,count(*) AS n FROM jobs GROUP BY status")}
        result.update(requests=sum(counts.values()), completed=counts.get("completed", 0), failed=counts.get("failed", 0))
        result["pending"] = sum(counts.get(status, 0) for status in ('received', 'ready', 'processing'))
        result["cancelled"] = counts.get('cancelled', 0) + counts.get('replaced', 0)
        finished = result["completed"] + result["failed"]
        result["success_rate"] = round(100 * result["completed"] / finished, 1) if finished else None
        return web.json_response({"stats": result, "disk": disk(), "csrf": csrf, "traffic": query("SELECT * FROM traffic ORDER BY route"), "cloudflare_enabled": bool(config.worker_url), "route_priority": stats_db.route_priority()})

    async def routing(request):
        try:
            priority = (await request.json())["priority"]
            if priority == 'cloudflare' and not config.worker_url:
                raise ValueError('Cloudflare is not configured')
            if priority == 'cloudflare':
                try:
                    await bot.session.worker.make_request(bot, GetMe(), timeout=5)
                except Exception:
                    raise web.HTTPBadGateway(text="Cloudflare is unreachable from this server. Proxy remains selected.") from None
            stats_db.set_route_priority(priority)
        except (ValueError, KeyError, TypeError):
            raise web.HTTPBadRequest(text="Select proxy or configured Cloudflare")
        return web.json_response({"route_priority": priority})

    async def users(request):
        term = "%" + request.query.get("search", "")[:100] + "%"
        where = " WHERE coalesce(username,'') LIKE ? OR coalesce(first_name,'') LIKE ? OR cast(user_id AS TEXT) LIKE ?"
        params = (term, term, term)
        total = query("SELECT count(*) AS n FROM users" + where, params)[0]["n"]
        rows = query("SELECT user_id,first_name,last_name,username,last_seen_at,media_requests,completed_jobs,start_count FROM users" + where + " ORDER BY last_seen_at DESC LIMIT 50 OFFSET ?", (*params, page(request) * 50))
        return web.json_response({"items": rows, "total": total})

    async def detail(request):
        user_id = int(request.match_info["user_id"])
        user = query("SELECT * FROM users WHERE user_id=?", (user_id,))
        if not user:
            raise web.HTTPNotFound()
        rows = query("SELECT * FROM jobs WHERE user_id=? ORDER BY id DESC LIMIT 25 OFFSET ?", (user_id, page(request) * 25))
        for row in rows:
            row["media"] = {kind: bool(row.pop(field)) for kind, field in MEDIA.items()}
            for key in list(row):
                if "file_" in key:
                    del row[key]
        prompts = query("SELECT kind,text,created_at FROM prompts WHERE user_id=? ORDER BY id DESC LIMIT 25 OFFSET ?", (user_id, page(request) * 25))
        return web.json_response({"user": user[0], "quota": stats_db.daily_quota(user_id, config.daily_request_limit), "jobs": rows, "prompts": prompts, "jobs_total": query("SELECT count(*) AS n FROM jobs WHERE user_id=?", (user_id,))[0]["n"], "prompts_total": query("SELECT count(*) AS n FROM prompts WHERE user_id=?", (user_id,))[0]["n"]})

    async def media(request):
        job_id = int(request.match_info["job_id"])
        kind = request.match_info["kind"]
        if kind not in MEDIA:
            raise web.HTTPNotFound()
        rows = query("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not rows or not rows[0][MEDIA[kind]]:
            raise web.HTTPNotFound(text="Media not available")
        row = rows[0]
        stem = f"job-{job_id}-{kind}"
        async with lock:
            existing = next((p for p in files() if p.stem == stem and p.parent == cache), None)
            if request.method == "DELETE":
                removed = 0
                for path in list(files()):
                    if path.stem == stem:
                        removed += path.stat().st_size
                        path.unlink()
                return web.json_response({"removed_bytes": removed, "disk": disk()})
            if existing is None:
                if kind == "source" and (row["source_file_size"] or 0) > config.max_download_size_mb * 1024 * 1024:
                    raise web.HTTPRequestEntityTooLarge(max_size=config.max_download_size_mb * 1024 * 1024, actual_size=row["source_file_size"])
                mime = row["source_mime_type"] if kind == "source" else "video/mp4"
                extension = {"image/gif": ".gif", "video/webm": ".webm", "video/quicktime": ".mov", "video/x-matroska": ".mkv"}.get(mime, ".mp4")
                existing = cache / (stem + extension)
                partial = cache / (stem + ".part")
                if cache.is_symlink() or existing.is_symlink() or partial.is_symlink():
                    raise web.HTTPForbidden()
                try:
                    await bot.download(row[MEDIA[kind]], destination=partial)
                    partial.replace(existing)
                except Exception:
                    raise web.HTTPBadGateway(text="Telegram download failed. Try again later.") from None
                finally:
                    partial.unlink(missing_ok=True)
            response = web.FileResponse(existing)
            response.content_type = mimetypes.guess_type(existing.name)[0] or "application/octet-stream"
            response.headers["Content-Disposition"] = f'{"attachment" if "download" in request.query else "inline"}; filename="{existing.name}"'
            return response

    async def clear(request):
        async with lock:
            removed = 0
            for path in list(files()):
                removed += path.stat().st_size
                path.unlink()
        return web.json_response({"removed_bytes": removed, "disk": disk()})

    async def asset(request):
        name = request.match_info.get("name", "index.html")
        if name not in ("index.html", "app.js", "style.css"):
            raise web.HTTPNotFound()
        return web.FileResponse(ASSETS / name)

    async def close(app):
        await bot.session.close()

    app = web.Application(middlewares=[security])
    app.router.add_get("/", asset)
    app.router.add_get("/assets/{name}", asset)
    app.router.add_get("/api/overview", overview)
    app.router.add_patch("/api/routing", routing)
    app.router.add_get("/api/users", users)
    app.router.add_get(r"/api/users/{user_id:\d+}", detail)
    app.router.add_get(r"/media/{job_id:\d+}/{kind}", media)
    app.router.add_delete(r"/media/{job_id:\d+}/{kind}", media)
    app.router.add_delete("/api/cache", clear)
    app.on_cleanup.append(close)
    return app


if __name__ == "__main__":
    os.umask(0o077)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(os.environ["DASHBOARD_TLS_CERT"], os.environ["DASHBOARD_TLS_KEY"])
    web.run_app(create_app(load_config(), os.environ["DASHBOARD_USERNAME"], os.environ["DASHBOARD_PASSWORD_HASH"]), host="0.0.0.0", port=int(os.getenv("DASHBOARD_PORT", "8443")), ssl_context=context, access_log=None)
