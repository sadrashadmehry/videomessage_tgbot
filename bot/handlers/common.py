from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.utils.session import clear_session

router = Router(name="common")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await clear_session(state)
    await message.answer("Cancelled. Send a video whenever you're ready to start again.")


@router.message(F.text)
async def handle_stray_text(message: Message) -> None:
    # Registered last (see main.py) so it only catches messages no other
    # router claimed - i.e. plain text with no active crop/trim session.
    await message.answer("Send me a video or GIF and I'll turn it into a round video message. /help for details.")
