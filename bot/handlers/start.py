from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from bot.services.storage_service import StatsStorage
from bot.config import Config

router = Router(name="start")

WELCOME = (

	"سلام و ادب و احترام و تشکر و عرض؛\n"

	"ویدیو/گیفتونو اپلود کنید و قبل از کانفیرم، اگر جای خاصی از ویدیو مدنظرتونه که کراپ شه، تو دایره زرد فیت کنید. اگر میخواستید اول و اخر ویدیو حذف شه، زمان شروع و پایان مدنظرتونو در 2 فرمت فقط ثانیه یا دقیقه:ثانیه بفرستین.\n"
	"دکمه send as gif رو بزنید و بعد خروجی رو با hide sender name فوروارد کنین هر جا و بعد به گیفاتون مراجعه کنید."
)
START_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="/start"), KeyboardButton(text="/cancel")], [KeyboardButton(text="📊 Remaining requests")]],
    resize_keyboard=True, is_persistent=False, one_time_keyboard=True,
)


@router.message(Command("quota"))
@router.message(F.text == "📊 Remaining requests")
async def cmd_quota(message: Message, config: Config, stats_db: StatsStorage) -> None:
    if not message.from_user:
        return
    quota = stats_db.daily_quota(message.from_user.id, config.daily_request_limit)
    await message.answer(f"درخواست‌های باقی‌مانده: {quota['remaining']} از {quota['limit']}\nزمان ریست: {quota['reset_at']} (تهران)")


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
