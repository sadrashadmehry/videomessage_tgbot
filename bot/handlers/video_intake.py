from __future__ import annotations

import contextlib
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, Message

from bot.config import Config
from bot.keyboards.crop_keyboard import crop_keyboard
from bot.services.crop_state import initial_crop
from bot.services.ffmpeg_service import FFmpegError, extract_preview_frame, probe_video
from bot.services.preview_service import render_crop_preview
from bot.services.storage_service import StatsStorage
from bot.services.time_range import format_range, format_timestamp
from bot.states import CropStates
from bot.utils.tempfiles import cleanup_session_dir, new_session_dir
from bot.utils.validators import exceeds_download_limit, is_supported_media

logger = logging.getLogger(__name__)
router = Router(name="video_intake")

BASE_CAPTION = (
    "Position the circle over what you want visible, then tap ✅ Confirm.\n"
    "⬅️⬆️➡️⬇️ move · 🔍−/🔍+ zoom · ✂️ trim · 🔄 reset · ❌ cancel"
)


def build_caption(trim_start: float, trim_end: float, source_duration: float, max_duration: int) -> str:
    """Caption shown under the crop-picker preview, including a line about
    the current trim range whenever it isn't simply "the whole video"."""
    lines = []
    using_partial = (trim_end - trim_start) < (source_duration - 0.05)
    if using_partial:
        lines.append(
            f"✂️ Using {format_range(trim_start, trim_end)} of "
            f"{format_timestamp(source_duration)}. Tap ✂️ Trim to change."
        )
    lines.append(BASE_CAPTION)
    return "\n\n".join(lines)


@router.message(F.video)
async def handle_video(
    message: Message, state: FSMContext, config: Config, stats_db: StatsStorage
) -> None:
    media = message.video
    await _intake(
        message, state, config, stats_db,
        file_id=media.file_id,
        file_unique_id=media.file_unique_id,
        file_size=media.file_size,
        mime_type=media.mime_type,
        source_kind="video",
    )


@router.message(F.animation)
async def handle_animation(
    message: Message, state: FSMContext, config: Config, stats_db: StatsStorage
) -> None:
    media = message.animation
    await _intake(
        message, state, config, stats_db,
        file_id=media.file_id,
        file_unique_id=media.file_unique_id,
        file_size=media.file_size,
        mime_type=media.mime_type,
        source_kind="animation",
    )


@router.message(F.document)
async def handle_document(
    message: Message, state: FSMContext, config: Config, stats_db: StatsStorage
) -> None:
    media = message.document
    if is_supported_media(media.mime_type):
        await _intake(
            message, state, config, stats_db,
            file_id=media.file_id,
            file_unique_id=media.file_unique_id,
            file_size=media.file_size,
            mime_type=media.mime_type,
            source_kind="document",
        )
    else:
        await message.answer(
            "That doesn't look like a video or GIF. Send me a video, an "
            "animated GIF, or a file with a video/* or image/gif MIME "
            "type, and I'll round it for you."
        )


async def _intake(
    message: Message,
    state: FSMContext,
    config: Config,
    stats_db: StatsStorage,
    *,
    file_id: str,
    file_unique_id: str | None,
    file_size: int | None,
    mime_type: str | None,
    source_kind: str,
) -> None:
    if message.from_user:
        stats_db.upsert_user(message.from_user)

    if not message.from_user:
        await message.answer("⚠️ I couldn't identify the sender of this file.")
        return

    # Starting a new file replaces any unfinished local session. The database
    # keeps only metadata/file_id references; no old media bytes are retained.
    previous = await state.get_data()
    if previous.get("session_dir"):
        cleanup_session_dir(previous["session_dir"])
    if previous.get("job_id"):
        stats_db.update_job(previous["job_id"], status="replaced")
    await state.clear()

    job_id = stats_db.create_job(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        source_message_id=message.message_id,
        source_kind=source_kind,
        source_file_id=file_id,
        source_file_unique_id=file_unique_id,
        source_file_size=file_size,
        source_mime_type=mime_type,
        prompt_text=message.caption,
    )

    # Record the Telegram media reference even when the cloud Bot API cannot
    # download the bytes. This keeps analytics/history complete without
    # persisting the actual video on this server.
    if exceeds_download_limit(file_size, config.max_download_size_mb):
        stats_db.update_job(job_id, status="rejected_too_large")
        await message.answer(
            f"⚠️ That file is bigger than the {config.max_download_size_mb}MB "
            "limit the Telegram Bot API allows bots to download. I saved its "
            "Telegram file reference/metadata, but not the video itself. Try a "
            "shorter/smaller clip, or run this bot against a local Bot API "
            "server (see README.md) to remove the limit."
        )
        return

    session_dir = new_session_dir(config.temp_dir, message.from_user.id)
    input_path = session_dir / "input.mp4"
    frame_path = session_dir / "frame.jpg"
    preview_path = session_dir / "preview.jpg"

    status = await message.answer("⬇️ Downloading your file…")

    try:
        await message.bot.download(file_id, destination=input_path)
        meta = await probe_video(str(input_path), ffprobe_binary=config.ffprobe_binary)

        preview_ts = min(1.0, meta.duration / 2)
        await extract_preview_frame(
            str(input_path), str(frame_path), timestamp=preview_ts,
            ffmpeg_binary=config.ffmpeg_binary,
        )

        crop = initial_crop(meta.width, meta.height)
        render_crop_preview(str(frame_path), crop, str(preview_path))

        trim_start = 0.0
        trim_end = min(meta.duration, config.max_video_note_duration)

        stats_db.save_processing_selection(
            job_id,
            crop=crop,
            trim_start=trim_start,
            trim_end=trim_end,
            source_duration=meta.duration,
            source_width=meta.width,
            source_height=meta.height,
            has_audio=meta.has_audio,
        )

        await state.update_data(
            job_id=job_id,
            session_dir=str(session_dir),
            input_path=str(input_path),
            frame_path=str(frame_path),
            preview_path=str(preview_path),
            crop=crop,
            has_audio=meta.has_audio,
            source_duration=meta.duration,
            source_width=meta.width,
            source_height=meta.height,
            trim_start=trim_start,
            trim_end=trim_end,
        )
        await state.set_state(CropStates.selecting_crop)

        caption = build_caption(trim_start, trim_end, meta.duration, config.max_video_note_duration)

        sent = await message.answer_photo(
            photo=FSInputFile(preview_path),
            caption=caption,
            reply_markup=crop_keyboard(),
        )
        await state.update_data(preview_message_id=sent.message_id)
        with contextlib.suppress(Exception):
            await status.delete()
    except FFmpegError as e:
        logger.warning("ffmpeg/ffprobe error during intake: %s", e)
        stats_db.update_job(job_id, status="failed")
        cleanup_session_dir(session_dir)
        await state.clear()
        await status.edit_text(
            "⚠️ I couldn't read that file (it may be corrupted or in an "
            "unsupported format). Please try a different video or GIF."
        )
    except Exception:
        logger.exception("unexpected error during video intake")
        stats_db.update_job(job_id, status="failed")
        cleanup_session_dir(session_dir)
        await state.clear()
        await status.edit_text("⚠️ Something went wrong processing that file. Please try again.")
