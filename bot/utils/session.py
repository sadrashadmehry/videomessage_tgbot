from __future__ import annotations

from aiogram.fsm.context import FSMContext

from bot.utils.tempfiles import cleanup_session_dir


async def clear_session(state: FSMContext) -> None:
    """Delete the current session's temp dir (if any) and reset the FSM."""
    data = await state.get_data()
    session_dir = data.get("session_dir")
    if session_dir:
        cleanup_session_dir(session_dir)
    await state.clear()
