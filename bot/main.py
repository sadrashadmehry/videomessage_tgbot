from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramNetworkError, TelegramServerError
from aiogram.types import BotCommand, MenuButtonCommands

from bot.config import load_config
from bot.handlers import common, crop_selector, errors, start, video_intake
from bot.transport import RoutedSession
from bot.services.storage_service import StatsStorage
from bot.utils.tempfiles import cleanup_stale_session_dirs


async def _temp_cleanup_loop(temp_dir: str, ttl_minutes: int) -> None:
    while True:
        await asyncio.sleep(15 * 60)
        cleanup_stale_session_dirs(temp_dir, max(ttl_minutes, 1) * 60)


async def main() -> None:
    os.umask(0o077)
    config = load_config()

    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    Path(config.temp_dir).mkdir(parents=True, exist_ok=True)
    cleanup_stale_session_dirs(config.temp_dir, 0)
    stats_db = StatsStorage(config.database_path)

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

    cleanup_task = asyncio.create_task(_temp_cleanup_loop(config.temp_dir, config.temp_session_ttl_minutes))
    try:
        while True:
            try:
                await bot.delete_webhook(drop_pending_updates=False, request_timeout=20)
                await bot.set_my_commands([
                    BotCommand(command="start", description="Start / شروع"),
                    BotCommand(command="cancel", description="Cancel / لغو"),
                    BotCommand(command="help", description="Help / راهنما"),
                ], request_timeout=20)
                await bot.set_chat_menu_button(menu_button=MenuButtonCommands(), request_timeout=20)
                break
            except (TelegramNetworkError, TelegramServerError):
                logging.warning('Telegram unavailable at startup; retrying in 10 seconds')
                await asyncio.sleep(10)
        await dp.start_polling(bot, config=config, stats_db=stats_db)
    finally:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        cleanup_stale_session_dirs(config.temp_dir, 0)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
