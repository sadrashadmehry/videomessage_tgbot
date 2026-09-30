from types import SimpleNamespace

from bot.services.storage_service import StatsStorage


def _user(**overrides):
    values = {
        "id": 123456789,
        "first_name": "Test",
        "last_name": "User",
        "username": "tester",
        "language_code": "en",
        "is_premium": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_user_and_job_metadata_are_persistent(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    storage = StatsStorage(str(db_path))
    user = _user()

    storage.upsert_user(user, increment_start=True)
    storage.upsert_user(_user(username="renamed"))
    job_id = storage.create_job(
        user_id=user.id,
        chat_id=user.id,
        source_message_id=10,
        source_kind="video",
        source_file_id="source-file-id",
        source_file_unique_id="source-unique-id",
        source_file_size=12345,
        source_mime_type="video/mp4",
        prompt_text="make this one",
    )
    storage.save_processing_selection(
        job_id,
        crop={"video_w": 1920, "video_h": 1080, "size": 1080, "x": 420, "y": 0},
        trim_start=2.0,
        trim_end=12.0,
        source_duration=30.0,
        source_width=1920,
        source_height=1080,
        has_audio=True,
    )
    storage.mark_completed(
        job_id,
        video_note_file_id="note-file-id",
        video_note_file_unique_id="note-unique-id",
    )
    storage.save_animation(
        job_id,
        animation_file_id="animation-file-id",
        animation_file_unique_id="animation-unique-id",
    )

    # Reopen to prove values are on disk, not only in process memory.
    reopened = StatsStorage(str(db_path))
    job = reopened.get_job(job_id)
    assert job is not None
    assert job["source_file_id"] == "source-file-id"
    assert job["video_note_file_id"] == "note-file-id"
    assert job["animation_file_id"] == "animation-file-id"
    assert job["animation_version"] == 1
    assert job["prompt_text"] == "make this one"
    assert job["status"] == "completed"

    with reopened._connect() as conn:  # narrow DB assertion for stats columns
        saved_user = conn.execute("SELECT * FROM users WHERE user_id=?", (user.id,)).fetchone()
        prompts = conn.execute("SELECT kind, text FROM prompts WHERE user_id=?", (user.id,)).fetchall()

    assert saved_user["username"] == "renamed"
    assert saved_user["start_count"] == 1
    assert saved_user["media_requests"] == 1
    assert saved_user["completed_jobs"] == 1
    assert [(row["kind"], row["text"]) for row in prompts] == [
        ("media_caption", "make this one")
    ]


def test_prompt_logging(tmp_path):
    storage = StatsStorage(str(tmp_path / "bot.sqlite3"))
    user = _user()
    storage.upsert_user(user)
    storage.record_prompt(
        user_id=user.id,
        chat_id=user.id,
        message_id=11,
        text="  5-12  ",
        kind="trim",
    )

    with storage._connect() as conn:
        row = conn.execute("SELECT kind, text FROM prompts").fetchone()

    assert row["kind"] == "trim"
    assert row["text"] == "5-12"
