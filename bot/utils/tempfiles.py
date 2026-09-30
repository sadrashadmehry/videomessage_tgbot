"""
Every user conversation gets its own temp subdirectory
(``<temp_dir>/<user_id>-<uuid4>/``) so concurrent users never collide and
cleanup is a single ``shutil.rmtree`` call.
"""
from __future__ import annotations

import shutil
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
