from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiogram.types import User


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def daily_window(now=None):
    local = (now or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=3, minutes=30)))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc).isoformat(timespec="seconds"), end.astimezone(timezone.utc).isoformat(timespec="seconds"), end.strftime("%Y-%m-%d %H:%M")


class StatsStorage:
    """Persistent metadata/statistics store backed by SQLite.

    Only lightweight metadata is stored here. Media bytes are deliberately not
    persisted: source/output Telegram ``file_id`` references are saved instead,
    so Telegram remains the media store while this server keeps only the data
    needed for analytics and later reuse.
    """

    def __init__(self, database_path: str) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT,
                    last_name TEXT,
                    username TEXT,
                    language_code TEXT,
                    is_premium INTEGER,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    start_count INTEGER NOT NULL DEFAULT 0,
                    media_requests INTEGER NOT NULL DEFAULT 0,
                    completed_jobs INTEGER NOT NULL DEFAULT 0,
                    gif_requests INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    source_message_id INTEGER,
                    source_kind TEXT NOT NULL,
                    source_file_id TEXT NOT NULL,
                    source_file_unique_id TEXT,
                    source_file_size INTEGER,
                    source_mime_type TEXT,
                    prompt_text TEXT,
                    trim_input_text TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    gif_generated_at TEXT,
                    status TEXT NOT NULL DEFAULT 'received',
                    source_duration REAL,
                    source_width INTEGER,
                    source_height INTEGER,
                    has_audio INTEGER,
                    crop_json TEXT,
                    trim_start REAL,
                    trim_end REAL,
                    video_note_file_id TEXT,
                    video_note_file_unique_id TEXT,
                    animation_file_id TEXT,
                    animation_file_unique_id TEXT,
                    animation_version INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS prompts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);
                CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);
                CREATE INDEX IF NOT EXISTS idx_jobs_source_unique ON jobs(source_file_unique_id);
                CREATE INDEX IF NOT EXISTS idx_prompts_user_id ON prompts(user_id);
                CREATE INDEX IF NOT EXISTS idx_prompts_created_at ON prompts(created_at);
                CREATE TABLE IF NOT EXISTS traffic (
                    route TEXT PRIMARY KEY,
                    uploaded INTEGER NOT NULL DEFAULT 0,
                    downloaded INTEGER NOT NULL DEFAULT 0,
                    requests INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL
                );
                """
            )
            if "animation_version" not in {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}:
                conn.execute("ALTER TABLE jobs ADD COLUMN animation_version INTEGER NOT NULL DEFAULT 0")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
            for column in ("animation_message_id", "animation_chat_id"):
                if column not in columns:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} INTEGER")

    def upsert_user(self, user: "User", *, increment_start: bool = False) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    user_id, first_name, last_name, username, language_code,
                    is_premium, first_seen_at, last_seen_at, start_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_name=excluded.first_name,
                    last_name=excluded.last_name,
                    username=excluded.username,
                    language_code=excluded.language_code,
                    is_premium=excluded.is_premium,
                    last_seen_at=excluded.last_seen_at,
                    start_count=users.start_count + excluded.start_count
                """,
                (
                    user.id,
                    user.first_name,
                    user.last_name,
                    user.username,
                    user.language_code,
                    int(bool(user.is_premium)) if user.is_premium is not None else None,
                    now,
                    now,
                    1 if increment_start else 0,
                ),
            )

    def record_prompt(
        self,
        *,
        user_id: int,
        chat_id: int,
        message_id: int | None,
        text: str,
        kind: str = "text",
    ) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO prompts (user_id, chat_id, message_id, kind, text, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, chat_id, message_id, kind, text, _utc_now()),
            )

    def daily_quota(self, user_id, limit=10, now=None):
        start, end, reset = daily_window(now)
        with self._connect() as conn:
            used = conn.execute("SELECT count(*) FROM jobs WHERE user_id=? AND created_at>=? AND created_at<? AND status!='rejected_too_large'", (user_id, start, end)).fetchone()[0]
        return {"limit": limit, "used": used, "remaining": max(0, limit-used), "reset_at": reset, "timezone": "Tehran"}

    def record_traffic(self, route, uploaded=0, downloaded=0, requests=0):
        if not (uploaded or downloaded or requests):
            return
        with self._connect() as conn:
            conn.execute("INSERT INTO traffic(route,uploaded,downloaded,requests,started_at) VALUES(?,?,?,?,?) ON CONFLICT(route) DO UPDATE SET uploaded=uploaded+excluded.uploaded,downloaded=downloaded+excluded.downloaded,requests=requests+excluded.requests", (route, uploaded, downloaded, requests, _utc_now()))

    def create_job(
        self,
        *,
        user_id: int,
        chat_id: int,
        source_message_id: int,
        source_kind: str,
        source_file_id: str,
        source_file_unique_id: str | None,
        source_file_size: int | None,
        source_mime_type: str | None,
        prompt_text: str | None,
        daily_limit: int | None = None,
    ) -> int | None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if daily_limit is not None:
                start, end, _ = daily_window(datetime.fromisoformat(now))
                used = conn.execute("SELECT count(*) FROM jobs WHERE user_id=? AND created_at>=? AND created_at<? AND status!='rejected_too_large'", (user_id, start, end)).fetchone()[0]
                if used >= daily_limit:
                    return None
            cur = conn.execute(
                """
                INSERT INTO jobs (
                    user_id, chat_id, source_message_id, source_kind,
                    source_file_id, source_file_unique_id, source_file_size,
                    source_mime_type, prompt_text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    chat_id,
                    source_message_id,
                    source_kind,
                    source_file_id,
                    source_file_unique_id,
                    source_file_size,
                    source_mime_type,
                    prompt_text,
                    now,
                ),
            )
            conn.execute(
                "UPDATE users SET media_requests = media_requests + 1, last_seen_at=? WHERE user_id=?",
                (now, user_id),
            )
            if prompt_text and prompt_text.strip():
                conn.execute(
                    """
                    INSERT INTO prompts (user_id, chat_id, message_id, kind, text, created_at)
                    VALUES (?, ?, ?, 'media_caption', ?, ?)
                    """,
                    (user_id, chat_id, source_message_id, prompt_text.strip(), now),
                )
            return int(cur.lastrowid)

    def update_job(self, job_id: int, **fields: Any) -> None:
        allowed = {
            "status", "source_duration", "source_width", "source_height",
            "has_audio", "crop_json", "trim_start", "trim_end",
            "trim_input_text", "video_note_file_id", "video_note_file_unique_id",
            "animation_file_id", "animation_file_unique_id", "completed_at",
            "gif_generated_at", "animation_version", "animation_message_id", "animation_chat_id",
        }
        payload = {key: value for key, value in fields.items() if key in allowed}
        if not payload:
            return
        assignments = ", ".join(f"{key}=?" for key in payload)
        values = list(payload.values()) + [job_id]
        with self._connect() as conn:
            conn.execute(f"UPDATE jobs SET {assignments} WHERE id=?", values)

    def save_processing_selection(
        self,
        job_id: int,
        *,
        crop: dict,
        trim_start: float,
        trim_end: float,
        source_duration: float,
        source_width: int,
        source_height: int,
        has_audio: bool,
    ) -> None:
        self.update_job(
            job_id,
            status="ready",
            crop_json=json.dumps(crop, separators=(",", ":")),
            trim_start=trim_start,
            trim_end=trim_end,
            source_duration=source_duration,
            source_width=source_width,
            source_height=source_height,
            has_audio=int(has_audio),
        )

    def mark_completed(
        self,
        job_id: int,
        *,
        video_note_file_id: str | None,
        video_note_file_unique_id: str | None,
    ) -> None:
        now = _utc_now()
        with self._connect() as conn:
            row = conn.execute("SELECT user_id, status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return
            conn.execute(
                """
                UPDATE jobs
                SET status='completed', completed_at=?, video_note_file_id=?,
                    video_note_file_unique_id=?
                WHERE id=?
                """,
                (now, video_note_file_id, video_note_file_unique_id, job_id),
            )
            if row["status"] != "completed":
                conn.execute(
                    "UPDATE users SET completed_jobs = completed_jobs + 1, last_seen_at=? WHERE user_id=?",
                    (now, row["user_id"]),
                )

    def mark_gif_requested(self, user_id: int) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET gif_requests = gif_requests + 1, last_seen_at=? WHERE user_id=?",
                (now, user_id),
            )

    def save_animation(
        self,
        job_id: int,
        *,
        animation_file_id: str | None,
        animation_file_unique_id: str | None,
        animation_message_id: int | None = None,
        animation_chat_id: int | None = None,
    ) -> None:
        self.update_job(
            job_id,
            animation_file_id=animation_file_id,
            animation_file_unique_id=animation_file_unique_id,
            gif_generated_at=_utc_now(),
            animation_version=2 if animation_message_id else 1,
            animation_message_id=animation_message_id,
            animation_chat_id=animation_chat_id,
        )

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None
