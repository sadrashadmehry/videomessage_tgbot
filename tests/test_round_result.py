import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bot.config import Config
from bot.handlers.crop_selector import handle_send_gif_version
from bot.keyboards.crop_keyboard import result_keyboard


def test_only_gif_action_remains_and_legacy_buttons_send_animation():
    keyboard = result_keyboard(42)
    assert [button.callback_data for row in keyboard.inline_keyboard for button in row] == [
        "result:gif:42",
    ]
    for action in ("round", "gif", "square_gif"):
        call = SimpleNamespace(
            data=f"result:{action}:42",
            from_user=SimpleNamespace(id=7),
            answer=AsyncMock(),
            message=SimpleNamespace(answer_animation=AsyncMock(), answer=AsyncMock()),
        )
        storage = Mock()
        storage.get_job.return_value = {"user_id": 7, "animation_file_id": "saved-animation-id", "animation_version": 1}
        asyncio.run(handle_send_gif_version(call, Config(bot_token="test"), storage))
        call.message.answer_animation.assert_awaited_once_with(animation="saved-animation-id")
        storage.mark_gif_requested.assert_called_once_with(7)
