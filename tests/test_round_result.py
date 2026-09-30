import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bot.config import Config
from bot.handlers.crop_selector import handle_send_gif_version
from bot.keyboards.crop_keyboard import result_keyboard


def test_round_copy_and_legacy_button_resend_video_note():
    keyboard = result_keyboard(42)
    assert [button.callback_data for row in keyboard.inline_keyboard for button in row] == [
        "result:round:42", "result:square_gif:42",
    ]
    for action in ("round", "gif"):
        call = SimpleNamespace(
            data=f"result:{action}:42",
            from_user=SimpleNamespace(id=7),
            answer=AsyncMock(),
            message=SimpleNamespace(answer_video_note=AsyncMock(), answer=AsyncMock()),
        )
        storage = Mock()
        storage.get_job.return_value = {"user_id": 7, "video_note_file_id": "saved-note-id"}
        asyncio.run(handle_send_gif_version(call, Config(bot_token="test"), storage))
        call.message.answer_video_note.assert_awaited_once_with(video_note="saved-note-id")
        storage.mark_gif_requested.assert_not_called()
