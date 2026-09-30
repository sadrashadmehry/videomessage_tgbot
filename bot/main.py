from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramNetworkError, TelegramServerError

from bot.config import load_config
from bot.handlers import common, crop_selector, errors, start, video_intake
from bot.transport import RoutedSession


async def main() -> None:
    config = load_config()

    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    Path(config.temp_dir).mkdir(parents=True, exist_ok=True)

    bot = Bot(
        token=config.bot_token,
        session=RoutedSession(config.worker_url, config.worker_secret, config.fallback_proxy_url),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Order matters: more specific routers first, the stray-text catch-all
    # in `common` goes last so it never shadows a real command/handler.
    dp.include_router(start.router)
    dp.include_router(video_intake.router)
    dp.include_router(crop_selector.router)
    dp.include_router(common.router)
    dp.include_router(errors.router)

    try:
        while True:
            try:
                await bot.delete_webhook(drop_pending_updates=False, request_timeout=20)
                break
            except (TelegramNetworkError, TelegramServerError):
                logging.warning('Telegram unavailable at startup; retrying in 10 seconds')
                await asyncio.sleep(10)
        await dp.start_polling(bot, config=config)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
