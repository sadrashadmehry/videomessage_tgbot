from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def crop_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️", callback_data="crop:left")
    b.button(text="⬆️", callback_data="crop:up")
    b.button(text="➡️", callback_data="crop:right")
    b.button(text="🔍−", callback_data="crop:zoomout")
    b.button(text="⬇️", callback_data="crop:down")
    b.button(text="🔍+", callback_data="crop:zoomin")
    b.button(text="✂️ Trim", callback_data="crop:trim")
    b.button(text="🔄 Reset", callback_data="crop:reset")
    b.button(text="❌ Cancel", callback_data="crop:cancel")
    b.button(text="✅ Confirm", callback_data="crop:confirm")
    b.adjust(3, 3, 2, 2)
    return b.as_markup()


def trim_entry_keyboard() -> InlineKeyboardMarkup:
    """Shown while we're waiting for the user to type a timestamp range."""
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Back", callback_data="trim:back")
    return b.as_markup()


def result_keyboard(job_id: int) -> InlineKeyboardMarkup:
    """Actions available after the round video has been rendered."""
    b = InlineKeyboardBuilder()
    b.button(text="🎞 Send GIF version", callback_data=f"result:gif:{job_id}")
    return b.as_markup()
