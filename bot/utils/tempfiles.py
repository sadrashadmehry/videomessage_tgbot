"""Temporary media handling.

Each active conversation gets its own temp subdirectory. Media is kept only
while it is needed for crop/trim/render work, then removed. A TTL cleanup pass
also removes abandoned sessions so source videos cannot accumulate forever.
"""
from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path


def new_session_dir(base_temp_dir: str, user_id: int) -> Path:
    session_dir = Path(base_temp_dir) / f"{user_id}-{uuid.uuid4().hex[:8]}"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def cleanup_session_dir(session_dir: str | Path) -> None:
    path = Path(session_dir)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def cleanup_stale_session_dirs(base_temp_dir: str, max_age_seconds: int) -> int:
    """Delete abandoned per-session directories older than max_age_seconds.

    The newest mtime anywhere inside a session is used so active sessions whose
    preview file was recently rewritten are not removed early.
    """
    base = Path(base_temp_dir)
    if not base.exists():
        return 0

    now = time.time()
    removed = 0
    for child in base.iterdir():
        if not child.is_dir():
            continue
        try:
            newest_mtime = child.stat().st_mtime
            for item in child.rglob("*"):
                try:
                    newest_mtime = max(newest_mtime, item.stat().st_mtime)
                except OSError:
                    pass
            if max_age_seconds <= 0 or now - newest_mtime >= max_age_seconds:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed
