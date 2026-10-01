from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto, Message

from bot.config import Config
from bot.handlers.video_intake import build_caption
from bot.keyboards.crop_keyboard import crop_keyboard, result_keyboard, trim_entry_keyboard
from bot.services.crop_state import move_crop, reset_crop, zoom_crop
from bot.services.ffmpeg_service import (
    FFmpegError,
    extract_preview_frame,
    get_output_duration,
    render_animation_from_video_note,
    render_video_note,
)
from bot.services.preview_service import render_crop_preview
from bot.services.storage_service import StatsStorage
from bot.services.round_animation import send_round_animation
from aiogram.exceptions import TelegramBadRequest
from bot.services.time_range import TimeParseError, format_timestamp, parse_time_range
from bot.states import CropStates
from bot.utils.session import clear_session
from bot.utils.tempfiles import cleanup_session_dir, new_session_dir

logger = logging.getLogger(__name__)
router = Router(name="crop_selector")

_MOVES = {
    "up": {"dy_frac": -1.0},
    "down": {"dy_frac": 1.0},
    "left": {"dx_frac": -1.0},
    "right": {"dx_frac": 1.0},
}


def _trim_prompt(source_duration: float, max_span: float) -> str:
    return (
        "Send the part you want to keep, as `start-end` (seconds or mm:ss) "
        "— e.g. `5-12` or `0:05-0:20`.\n"
        f"Or send just a start time (e.g. `5`) to keep the next {max_span:g}s from there.\n\n"
        f"Video is {format_timestamp(source_duration)} long. Max clip length is {max_span:g}s.\n"
        "⬅️ Back to go back without changing it, or /cancel to abort."
    )


def _save_current_selection(stats_db: StatsStorage, data: dict, crop: dict | None = None) -> None:
    if not data.get("job_id"):
        return
    stats_db.save_processing_selection(
        data["job_id"],
        crop=crop or data["crop"],
        trim_start=data.get("trim_start", 0.0),
        trim_end=data.get("trim_end", data["source_duration"]),
        source_duration=data["source_duration"],
        source_width=data["source_width"],
        source_height=data["source_height"],
        has_audio=data["has_audio"],
    )


@router.callback_query(CropStates.selecting_crop, F.data.startswith("crop:"))
async def handle_crop_action(
    call: CallbackQuery, state: FSMContext, config: Config, stats_db: StatsStorage
) -> None:
    action = call.data.split(":", 1)[1]
    data = await state.get_data()
    crop = data["crop"]

    if action == "cancel":
        cleanup_session_dir(data["session_dir"])
        if data.get("job_id"):
            stats_db.update_job(data["job_id"], status="cancelled")
        await state.clear()
        await call.message.edit_caption(caption="❌ Cancelled. Send another video whenever you're ready.")
        await call.answer()
        return

    if action == "confirm":
        await state.set_state(CropStates.processing)
        _save_current_selection(stats_db, data)
        await call.answer("Rendering your round video message…")
        await _render_and_send(call.message, state, config, stats_db, data)
        return

    if action == "trim":
        await state.set_state(CropStates.entering_timestamps)
        await call.message.edit_caption(
            caption=_trim_prompt(data["source_duration"], config.max_video_note_duration),
            reply_markup=trim_entry_keyboard(),
        )
        await call.answer()
        return

    if action in _MOVES:
        crop = move_crop(crop, min_fraction=config.min_crop_fraction, **{
            "dx_frac": _MOVES[action].get("dx_frac", 0.0) * config.move_step_fraction,
            "dy_frac": _MOVES[action].get("dy_frac", 0.0) * config.move_step_fraction,
        })
    elif action == "zoomin":
        crop = zoom_crop(crop, config.zoom_in_factor, min_fraction=config.min_crop_fraction)
    elif action == "zoomout":
        crop = zoom_crop(crop, config.zoom_out_factor, min_fraction=config.min_crop_fraction)
    elif action == "reset":
        crop = reset_crop(crop)
    else:
        await call.answer()
        return

    await state.update_data(crop=crop)
    data["crop"] = crop
    _save_current_selection(stats_db, data, crop)
    render_crop_preview(data["frame_path"], crop, data["preview_path"])

    await call.message.edit_media(
        media=InputMediaPhoto(media=FSInputFile(data["preview_path"]), caption=call.message.caption),
        reply_markup=crop_keyboard(),
    )
    await call.answer()


@router.callback_query(CropStates.processing, F.data.startswith("crop:"))
async def handle_crop_action_while_processing(call: CallbackQuery) -> None:
    await call.answer("Still working on your video, one sec…")


@router.callback_query(CropStates.entering_timestamps, F.data == "trim:back")
async def handle_trim_back(call: CallbackQuery, state: FSMContext, config: Config) -> None:
    data = await state.get_data()
    await state.set_state(CropStates.selecting_crop)
    caption = build_caption(
        data["trim_start"], data["trim_end"], data["source_duration"], config.max_video_note_duration
    )
    await call.message.edit_caption(caption=caption, reply_markup=crop_keyboard())
    await call.answer()


@router.message(CropStates.entering_timestamps, Command("cancel"))
async def handle_cancel_during_trim_entry(
    message: Message, state: FSMContext, stats_db: StatsStorage
) -> None:
    if message.from_user:
        stats_db.upsert_user(message.from_user)
        stats_db.record_prompt(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=message.text or "/cancel",
            kind="command",
        )
    data = await state.get_data()
    if data.get("job_id"):
        stats_db.update_job(data["job_id"], status="cancelled")
    await clear_session(state)
    await message.answer("Cancelled. Send a video whenever you're ready to start again.")


@router.message(CropStates.entering_timestamps, F.text)
async def handle_trim_input(
    message: Message, state: FSMContext, config: Config, stats_db: StatsStorage
) -> None:
    data = await state.get_data()
    source_duration = data["source_duration"]
    if message.from_user and message.text:
        stats_db.upsert_user(message.from_user)
        stats_db.record_prompt(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=message.text,
            kind="trim",
        )

    try:
        start, end = parse_time_range(
            message.text,
            source_duration=source_duration,
            max_span=config.max_video_note_duration,
            min_span=config.min_trim_span,
        )
    except TimeParseError as e:
        await message.answer(f"⚠️ {e}\n\nTry again, or /cancel to abort.")
        return

    preview_ts = start + min(0.5, (end - start) / 2)
    try:
        await extract_preview_frame(
            data["input_path"], data["frame_path"], timestamp=preview_ts,
            ffmpeg_binary=config.ffmpeg_binary,
        )
    except FFmpegError:
        logger.exception("failed to re-extract preview frame after trim")

    render_crop_preview(data["frame_path"], data["crop"], data["preview_path"])

    await state.update_data(trim_start=start, trim_end=end)
    data["trim_start"] = start
    data["trim_end"] = end
    if data.get("job_id"):
        stats_db.update_job(data["job_id"], trim_input_text=message.text)
    _save_current_selection(stats_db, data)
    await state.set_state(CropStates.selecting_crop)

    caption = build_caption(start, end, source_duration, config.max_video_note_duration)
    await message.bot.edit_message_media(
        chat_id=message.chat.id,
        message_id=data["preview_message_id"],
        media=InputMediaPhoto(media=FSInputFile(data["preview_path"]), caption=caption),
        reply_markup=crop_keyboard(),
    )


async def _render_and_send(
    preview_message: Message,
    state: FSMContext,
    config: Config,
    stats_db: StatsStorage,
    data: dict,
) -> None:
    output_path = data["session_dir"] + "/output.mp4"
    trim_start = data.get("trim_start", 0.0)
    trim_end = data.get("trim_end", min(data["source_duration"], config.max_video_note_duration))
    render_duration = max(trim_end - trim_start, config.min_trim_span)
    job_id = data.get("job_id")

    try:
        await render_video_note(
            data["input_path"],
            data["crop"],
            output_path,
            has_audio=data["has_audio"],
            start=trim_start,
            duration=render_duration,
            size=config.video_note_size,
            ffmpeg_binary=config.ffmpeg_binary,
        )
        duration = await get_output_duration(output_path, ffprobe_binary=config.ffprobe_binary)

        sent = await preview_message.answer_video_note(
            video_note=FSInputFile(output_path),
            duration=round(duration),
            length=config.video_note_size,
            reply_markup=result_keyboard(job_id) if job_id else None,
        )
        if job_id:
            note = sent.video_note
            stats_db.mark_completed(
                job_id,
                video_note_file_id=note.file_id if note else None,
                video_note_file_unique_id=note.file_unique_id if note else None,
            )
        await preview_message.edit_caption(
            caption="✅ Sent! Tap 🎞 Send as GIF below for a silent version you can save to your GIFs."
        )
    except FFmpegError as e:
        logger.warning("ffmpeg render failed: %s", e)
        if job_id:
            stats_db.update_job(job_id, status="failed")
        await preview_message.answer("⚠️ Rendering failed. Please try again with a different video.")
    except Exception:
        logger.exception("unexpected error rendering/sending video note")
        if job_id:
            stats_db.update_job(job_id, status="failed")
        await preview_message.answer("⚠️ Something went wrong sending that. Please try again.")
    finally:
        cleanup_session_dir(data["session_dir"])
        await state.clear()


@router.callback_query(F.data.startswith("result:"))
async def handle_send_gif_version(
    call: CallbackQuery, config: Config, stats_db: StatsStorage
) -> None:
    try:
        _, kind, job_id_text = call.data.split(":", 2)
        if kind not in ("round", "gif", "square_gif"):
            raise ValueError("unknown result action")
        job_id = int(job_id_text)
    except (TypeError, ValueError):
        await call.answer("This button is invalid.", show_alert=True)
        return

    job = stats_db.get_job(job_id)
    if not job or not call.from_user or job["user_id"] != call.from_user.id:
        await call.answer("This button isn't available for your account.", show_alert=True)
        return

    stats_db.upsert_user(call.from_user)
    stats_db.mark_gif_requested(call.from_user.id)
    native_round_gif = bool(config.telegram_api_id and config.telegram_api_hash)

    if native_round_gif and job.get("animation_version") == 2 and job.get("animation_message_id"):
        try:
            await call.bot.copy_message(
                chat_id=call.message.chat.id, from_chat_id=job["animation_chat_id"],
                message_id=job["animation_message_id"],
            )
            await call.answer("Sent GIF version.")
            return
        except TelegramBadRequest:
            logger.info("Cached round GIF message unavailable; regenerating job %s", job_id)

    # Once generated once, resend the Telegram-hosted animation by file_id.
    # This requires zero media storage or ffmpeg work on our server.
    if not native_round_gif and job.get("animation_file_id") and job.get("animation_version") == 1:
        await call.answer("Sending GIF version…")
        try:
            await call.message.answer_animation(animation=job["animation_file_id"])
        except Exception:
            logger.exception("failed to resend cached Telegram animation for job %s", job_id)
            await call.message.answer("⚠️ I couldn't resend that GIF version. Please try again.")
        return

    # Prefer the already-rendered round-video result, which is small, square,
    # and exactly matches what the user received. We download it temporarily,
    # strip its audio without re-encoding, upload it as an Animation, save the
    # new Telegram file_id, then delete both temporary files.
    video_note_file_id = job.get("video_note_file_id")
    if not video_note_file_id:
        await call.answer("This result is missing its saved Telegram video reference.", show_alert=True)
        return

    await call.answer("Preparing GIF version…")
    session_dir = new_session_dir(config.temp_dir, call.from_user.id)
    note_path = session_dir / "video_note.mp4"
    animation_path = session_dir / "animation.mp4"

    try:
        await call.bot.download(video_note_file_id, destination=note_path)
        await render_animation_from_video_note(
            str(note_path),
            str(animation_path),
            ffmpeg_binary=config.ffmpeg_binary,
            circular_mask=not native_round_gif,
        )
        rendered_duration = await get_output_duration(
            str(animation_path), ffprobe_binary=config.ffprobe_binary
        )
        if native_round_gif:
            message_id, file_id = await send_round_animation(
                config, call.message.chat.id, str(animation_path),
                rendered_duration, config.video_note_size,
                stats_db=stats_db,
            )
            stats_db.save_animation(
                job_id, animation_file_id=file_id, animation_file_unique_id=None,
                animation_message_id=message_id, animation_chat_id=call.message.chat.id,
            )
        else:
            sent = await call.message.answer_animation(
                animation=FSInputFile(animation_path), duration=round(rendered_duration),
                width=config.video_note_size, height=config.video_note_size,
            )
            animation = sent.animation
            stats_db.save_animation(
                job_id, animation_file_id=animation.file_id if animation else None,
                animation_file_unique_id=animation.file_unique_id if animation else None,
            )
    except FFmpegError as e:
        logger.warning("GIF-version remux failed for job %s: %s", job_id, e)
        await call.message.answer("⚠️ I couldn't prepare the GIF version. Please try again.")
    except Exception:
        logger.exception("unexpected error generating GIF version for job %s", job_id)
        await call.message.answer("⚠️ Something went wrong generating the GIF version. Please try again.")
    finally:
        cleanup_session_dir(session_dir)
