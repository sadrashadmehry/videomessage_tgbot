from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.services.storage_service import StatsStorage
from bot.utils.session import clear_session

router = Router(name="common")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, stats_db: StatsStorage) -> None:
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


@router.message(F.text)
async def handle_stray_text(message: Message, stats_db: StatsStorage) -> None:
    # Registered last (see main.py) so it only catches messages no other
    # router claimed - i.e. plain text with no active crop/trim session.
    if message.from_user:
        stats_db.upsert_user(message.from_user)
        stats_db.record_prompt(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=message.text or "",
            kind="text",
        )
    await message.answer("Send me a video or GIF and I'll turn it into a round video message. /help for details.")
