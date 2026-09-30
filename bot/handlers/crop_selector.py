from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto, Message

from bot.config import Config
from bot.handlers.video_intake import build_caption
from bot.keyboards.crop_keyboard import crop_keyboard, trim_entry_keyboard
from bot.services.crop_state import move_crop, reset_crop, zoom_crop
from bot.services.ffmpeg_service import (
    FFmpegError,
    extract_preview_frame,
    get_output_duration,
    render_video_note,
)
from bot.services.preview_service import render_crop_preview
from bot.services.time_range import TimeParseError, format_timestamp, parse_time_range
from bot.states import CropStates
from bot.utils.session import clear_session
from bot.utils.tempfiles import cleanup_session_dir

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


@router.callback_query(CropStates.selecting_crop, F.data.startswith("crop:"))
async def handle_crop_action(call: CallbackQuery, state: FSMContext, config: Config) -> None:
    action = call.data.split(":", 1)[1]
    data = await state.get_data()
    crop = data["crop"]

    if action == "cancel":
        cleanup_session_dir(data["session_dir"])
        await state.clear()
        await call.message.edit_caption(caption="❌ Cancelled. Send another video whenever you're ready.")
        await call.answer()
        return

    if action == "confirm":
        await state.set_state(CropStates.processing)
        await call.answer("Rendering your round video message…")
        await _render_and_send(call.message, state, config, data)
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
    render_crop_preview(data["frame_path"], crop, data["preview_path"])

    await call.message.edit_media(
        media=InputMediaPhoto(media=FSInputFile(data["preview_path"]), caption=call.message.caption),
        reply_markup=crop_keyboard(),
    )
    await call.answer()


@router.callback_query(CropStates.processing, F.data.startswith("crop:"))
async def handle_crop_action_while_processing(call: CallbackQuery) -> None:
    # Debounce: ignore extra taps while ffmpeg is already rendering, but
    # still ack the callback so Telegram's client stops showing a spinner.
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
async def handle_cancel_during_trim_entry(message: Message, state: FSMContext) -> None:
    await clear_session(state)
    await message.answer("Cancelled. Send a video whenever you're ready to start again.")


@router.message(CropStates.entering_timestamps, F.text)
async def handle_trim_input(message: Message, state: FSMContext, config: Config) -> None:
    data = await state.get_data()
    source_duration = data["source_duration"]

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

    # Re-extract the preview frame from a point inside the new trim range
    # so the preview reflects what will actually be kept, then redraw the
    # crop overlay (the crop box itself is unchanged by trimming).
    preview_ts = start + min(0.5, (end - start) / 2)
    try:
        await extract_preview_frame(
            data["input_path"], data["frame_path"], timestamp=preview_ts,
            ffmpeg_binary=config.ffmpeg_binary,
        )
    except FFmpegError:
        logger.exception("failed to re-extract preview frame after trim")
        # Not fatal - fall back to the existing frame rather than failing
        # the whole interaction over a preview-only step.

    render_crop_preview(data["frame_path"], data["crop"], data["preview_path"])

    await state.update_data(trim_start=start, trim_end=end)
    await state.set_state(CropStates.selecting_crop)

    caption = build_caption(start, end, source_duration, config.max_video_note_duration)
    await message.bot.edit_message_media(
        chat_id=message.chat.id,
        message_id=data["preview_message_id"],
        media=InputMediaPhoto(media=FSInputFile(data["preview_path"]), caption=caption),
        reply_markup=crop_keyboard(),
    )


async def _render_and_send(preview_message: Message, state: FSMContext, config: Config, data: dict) -> None:
    output_path = data["session_dir"] + "/output.mp4"
    trim_start = data.get("trim_start", 0.0)
    trim_end = data.get("trim_end", min(data["source_duration"], config.max_video_note_duration))
    render_duration = max(trim_end - trim_start, config.min_trim_span)

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

        await preview_message.answer_video_note(
            video_note=FSInputFile(output_path),
            duration=round(duration),
            length=config.video_note_size,
        )
        await preview_message.edit_caption(caption="✅ Sent! Send another video any time.")
    except FFmpegError as e:
        logger.warning("ffmpeg render failed: %s", e)
        await preview_message.answer("⚠️ Rendering failed. Please try again with a different video.")
    except Exception:
        logger.exception("unexpected error rendering/sending video note")
        await preview_message.answer("⚠️ Something went wrong sending that. Please try again.")
    finally:
        cleanup_session_dir(data["session_dir"])
        await state.clear()
