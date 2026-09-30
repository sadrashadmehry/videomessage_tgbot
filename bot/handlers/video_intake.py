from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, Message

from bot.config import Config
from bot.keyboards.crop_keyboard import crop_keyboard
from bot.services.crop_state import initial_crop
from bot.services.ffmpeg_service import FFmpegError, extract_preview_frame, probe_video
from bot.services.preview_service import render_crop_preview
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
async def handle_video(message: Message, state: FSMContext, config: Config) -> None:
    await _intake(message, state, config, message.video.file_id, message.video.file_size)


@router.message(F.animation)
async def handle_animation(message: Message, state: FSMContext, config: Config) -> None:
    # GIFs sent the normal way arrive as an Animation - Telegram itself
    # usually already transcodes these to a silent MP4 client-side, but
    # our ffmpeg pipeline handles genuine GIF containers identically (see
    # ARCHITECTURE.md "GIF support"), so no special-casing is needed here.
    await _intake(message, state, config, message.animation.file_id, message.animation.file_size)


@router.message(F.document)
async def handle_document(message: Message, state: FSMContext, config: Config) -> None:
    # Checked in the handler body (rather than a magic-filter chain like
    # `F.document.mime_type.startswith(...)`) because mime_type can be
    # None for some documents, and we want a plain, predictable branch
    # here rather than relying on filter-chain None-handling.
    if is_supported_media(message.document.mime_type):
        await _intake(message, state, config, message.document.file_id, message.document.file_size)
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
    file_id: str,
    file_size: int | None,
) -> None:
    if exceeds_download_limit(file_size, config.max_download_size_mb):
        await message.answer(
            f"⚠️ That file is bigger than the {config.max_download_size_mb}MB "
            "limit the Telegram Bot API allows bots to download. Try a "
            "shorter/smaller clip, or run this bot against a local Bot API "
            "server (see README.md) to remove the limit."
        )
        return

    # If the user was already mid-selection (or mid-render) for a previous
    # video and just sends a new one instead of confirming/cancelling,
    # treat it as "start over with this video" rather than leaking the
    # old session's temp directory forever.
    previous = await state.get_data()
    if previous.get("session_dir"):
        cleanup_session_dir(previous["session_dir"])
    await state.clear()

    session_dir = new_session_dir(config.temp_dir, message.from_user.id)
    # Fixed filename regardless of actual source format (video or GIF):
    # ffmpeg/ffprobe identify the real container by sniffing content, not
    # by file extension, so this is safe for both (verified in
    # ARCHITECTURE.md's GIF-support section).
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

        await state.update_data(
            session_dir=str(session_dir),
            input_path=str(input_path),
            frame_path=str(frame_path),
            preview_path=str(preview_path),
            crop=crop,
            has_audio=meta.has_audio,
            source_duration=meta.duration,
            trim_start=trim_start,
            trim_end=trim_end,
        )
        await state.set_state(CropStates.selecting_crop)

        caption = build_caption(trim_start, trim_end, meta.duration, config.max_video_note_duration)

        await status.delete()
        sent = await message.answer_photo(
            photo=FSInputFile(preview_path),
            caption=caption,
            reply_markup=crop_keyboard(),
        )
        await state.update_data(preview_message_id=sent.message_id)
    except FFmpegError as e:
        logger.warning("ffmpeg/ffprobe error during intake: %s", e)
        cleanup_session_dir(session_dir)
        await status.edit_text(
            "⚠️ I couldn't read that file (it may be corrupted or in an "
            "unsupported format). Please try a different video or GIF."
        )
    except Exception:
        logger.exception("unexpected error during video intake")
        cleanup_session_dir(session_dir)
        await status.edit_text("⚠️ Something went wrong processing that file. Please try again.")
