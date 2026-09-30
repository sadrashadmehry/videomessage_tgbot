from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.services.storage_service import StatsStorage

router = Router(name="start")

WELCOME = (
    "👋 Send me a video or GIF and I'll turn it into a round Telegram video "
    "message (the circular \"video note\" bubble), just like the one you get "
    "from the camera icon next to the message box.\n\n"
    "After you send a file I'll show you a preview with a circle on it — use "
    "the buttons under the preview to move and resize that circle so it "
    "frames the part you want visible.\n\n"
    "Want to use only part of a longer clip? Tap ✂️ Trim and send a range "
    "like `5-12` or `0:05-0:20` (seconds or mm:ss) — or just a start time "
    "to keep the next 60s from there.\n\n"
    "Once you're happy, hit ✅ Confirm. After the round video is sent, you "
    "can send another round copy, or get a square animation for your GIF library.\n\n"
    "Notes:\n"
    "• Video messages top out at 60 seconds; longer selections get trimmed "
    "to fit.\n"
    "• Because of Telegram Bot API limits, files over 20MB can't be "
    "downloaded by the bot unless it's running against a local Bot API "
    "server (see README).\n\n"
    "Send /cancel any time to abort the current video."
)


@router.message(CommandStart())
async def cmd_start(message: Message, stats_db: StatsStorage) -> None:
    if message.from_user:
        stats_db.upsert_user(message.from_user, increment_start=True)
        stats_db.record_prompt(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=message.text or "/start",
            kind="start",
        )
    await message.answer(WELCOME)


@router.message(Command("help"))
async def cmd_help(message: Message, stats_db: StatsStorage) -> None:
    if message.from_user:
        stats_db.upsert_user(message.from_user)
        stats_db.record_prompt(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=message.text or "/help",
            kind="command",
        )
    await message.answer(WELCOME)
