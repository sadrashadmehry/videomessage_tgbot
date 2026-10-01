import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bot.config import Config
from bot.handlers.crop_selector import handle_send_gif_version
from bot.keyboards.crop_keyboard import result_keyboard
from bot.services import ffmpeg_service, round_animation
from telethon import types


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


def test_round_gif_upload_keeps_both_flags_and_unmasked_video(monkeypatch):
    document = types.Document(
        id=1, access_hash=2, file_reference=b'', date=datetime.now(timezone.utc),
        mime_type='video/mp4', size=100, dc_id=2,
        attributes=round_animation.round_animation_attributes(2.8, 496),
    )
    response = SimpleNamespace(updates=[types.UpdateNewMessage(
        message=SimpleNamespace(id=123, media=SimpleNamespace(document=document)),
        pts=1, pts_count=1,
    )])
    client = SimpleNamespace(
        start=AsyncMock(), get_input_entity=AsyncMock(return_value=types.InputPeerUser(7, 0)),
        upload_file=AsyncMock(return_value=types.InputFile(1, 1, 'sample.mp4', '')),
        disconnect=AsyncMock(),
    )
    rpc = AsyncMock(return_value=response)
    class FakeClient:
        def __getattr__(self, name):
            return getattr(client, name)
        async def __call__(self, request):
            return await rpc(request)
    monkeypatch.setattr(round_animation, 'TelegramClient', lambda *a, **kw: FakeClient())
    config = Config(bot_token='test', telegram_api_id=123, telegram_api_hash='test')
    message_id, file_id = asyncio.run(round_animation.send_round_animation(config, 7, 'sample.mp4', 2.8, 496))
    assert message_id == 123 and file_id
    media = rpc.await_args.args[0].media
    assert not media.nosound_video
    assert any(isinstance(a, types.DocumentAttributeAnimated) for a in media.attributes)
    assert any(isinstance(a, types.DocumentAttributeVideo) and a.round_message for a in media.attributes)
    client.disconnect.assert_awaited_once()
    run = AsyncMock(return_value=(0, b'', b''))
    monkeypatch.setattr(ffmpeg_service, '_run', run)
    asyncio.run(ffmpeg_service.render_animation_from_video_note('in.mp4', 'out.mp4', circular_mask=False))
    command = run.await_args.args[0]
    assert '-vf' not in command and command[command.index('-c:v')+1] == 'copy'


def test_cached_round_gif_is_copied_without_changing_media_type():
    call = SimpleNamespace(
        data='result:gif:42', from_user=SimpleNamespace(id=7),
        answer=AsyncMock(), bot=SimpleNamespace(copy_message=AsyncMock()),
        message=SimpleNamespace(chat=SimpleNamespace(id=7)),
    )
    storage = Mock()
    storage.get_job.return_value = {
        'user_id': 7, 'animation_version': 2,
        'animation_message_id': 123, 'animation_chat_id': 7,
    }
    config = Config(bot_token='test', telegram_api_id=123, telegram_api_hash='test')
    asyncio.run(handle_send_gif_version(call, config, storage))
    call.bot.copy_message.assert_awaited_once_with(chat_id=7, from_chat_id=7, message_id=123)
