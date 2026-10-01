"""Read stored bot activity and optionally retrieve one Telegram media file."""
from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
from pathlib import Path

from aiogram import Bot

from bot.config import load_config
from bot.transport import RoutedSession
from bot.services.storage_service import StatsStorage


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("users", "jobs", "download"))
    parser.add_argument("job_id", type=int, nargs="?")
    parser.add_argument("media", nargs="?", choices=("source", "video_note", "animation"))
    parser.add_argument("--user-id", type=int)
    args = parser.parse_args()
    os.umask(0o077)
    config = load_config()
    db = Path(config.database_path).resolve()
    if not db.is_file():
        parser.error(f"database does not exist: {db}")
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        if args.action == "users":
            rows = conn.execute("SELECT user_id, username, first_name, last_name, first_seen_at, last_seen_at, start_count, media_requests, completed_jobs FROM users ORDER BY last_seen_at DESC").fetchall()
        elif args.action == "jobs":
            rows = conn.execute("SELECT id, user_id, created_at, source_kind, status, source_file_size, video_note_file_id IS NOT NULL AS has_video_note, animation_file_id IS NOT NULL AS has_animation FROM jobs WHERE (? IS NULL OR user_id=?) ORDER BY id DESC", (args.user_id, args.user_id)).fetchall()
        else:
            if args.job_id is None or args.media is None:
                parser.error("download requires JOB_ID and source, video_note, or animation")
            field = {"source": "source_file_id", "video_note": "video_note_file_id", "animation": "animation_file_id"}[args.media]
            row = conn.execute(f"SELECT {field} FROM jobs WHERE id=?", (args.job_id,)).fetchone()
            if not row or not row[0]:
                parser.error("job or media reference not found")
            file_id = row[0]
    if args.action != "download":
        for row in rows:
            print(" | ".join(f"{key}={row[key]}" for key in row.keys()))
        return
    output = Path("data/exports") / f"job-{args.job_id}-{args.media}.bin"
    output.parent.mkdir(parents=True, exist_ok=True)
    async with Bot(config.bot_token, session=RoutedSession(config.worker_url, config.worker_secret, config.fallback_proxy_url, StatsStorage(config.database_path))) as bot:
        await bot.download(file_id, destination=output)
    print(output.resolve())


if __name__ == "__main__":
    asyncio.run(main())
