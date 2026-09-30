from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import ErrorEvent

logger = logging.getLogger(__name__)
router = Router(name="errors")


@router.errors()
async def handle_error(event: ErrorEvent) -> bool:
    logger.exception(
        "Unhandled exception while processing update %s",
        event.update,
        exc_info=event.exception,
    )
    # Returning True tells aiogram the error was handled, so polling continues.
    return True
