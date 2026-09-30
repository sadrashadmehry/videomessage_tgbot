from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from bot.services.storage_service import StatsStorage

router = Router(name="start")

WELCOME = (
    "سلام و ادب و احترام و تشکر و عرض؛\n"
    "ویدیو/گیفتونو اپلود کنید و قبل از کانفیرم اگر جای خاصی از ویدیو مدنظرتونه که کراپ شه انتخابش کنید. "
    "اگر میخواستید اول و اخر ویدیو حذف بشه زمان شروع و پایان ویدیو رو بفرستین."
)
START_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="/start")]], resize_keyboard=True, is_persistent=True,
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
    await message.answer(WELCOME, reply_markup=START_KEYBOARD)


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
    await message.answer(WELCOME, reply_markup=START_KEYBOARD)
