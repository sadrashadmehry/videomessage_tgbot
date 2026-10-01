"""Send a silent video with both native round-video and GIF attributes."""
from urllib.parse import unquote, urlparse

import socks
from telethon import TelegramClient, functions, helpers, types, utils
from telethon.sessions import StringSession

from bot.config import Config


def round_animation_attributes(duration: float, size: int) -> list:
    return [
        types.DocumentAttributeVideo(
            duration=duration, w=size, h=size, round_message=True,
            nosound=True, supports_streaming=True,
        ),
        types.DocumentAttributeAnimated(),
    ]


async def send_round_animation(
    config: Config, chat_id: int, path: str, duration: float, size: int,
) -> tuple[int, str]:
    proxy = None
    if config.fallback_proxy_url:
        url = urlparse(config.fallback_proxy_url)
        if url.scheme not in ('socks5', 'socks5h') or not url.hostname or not url.port:
            raise ValueError('Round GIFs require a SOCKS5 fallback proxy')
        proxy = (
            socks.SOCKS5, url.hostname, url.port, True,
            unquote(url.username) if url.username else None,
            unquote(url.password) if url.password else None,
        )
    # ponytail: authenticate per GIF; reuse a client if this becomes a throughput bottleneck.
    client = TelegramClient(
        StringSession(), config.telegram_api_id, config.telegram_api_hash,
        proxy=proxy, receive_updates=False, connection_retries=1,
        request_retries=1, timeout=20, flood_sleep_threshold=0,
    )
    try:
        await client.start(bot_token=config.bot_token)
        peer = await client.get_input_entity(chat_id)
        result = await client(functions.messages.SendMediaRequest(
            peer=peer,
            media=types.InputMediaUploadedDocument(
                file=await client.upload_file(path), mime_type='video/mp4',
                attributes=round_animation_attributes(duration, size),
            ),
            message='', random_id=helpers.generate_random_long(),
        ))
        for update in result.updates:
            if isinstance(update, (types.UpdateNewMessage, types.UpdateNewChannelMessage)):
                sent = update.message
                return sent.id, utils.pack_bot_file_id(sent.media.document)
        raise RuntimeError('Telegram did not return the sent round GIF')
    finally:
        await client.disconnect()
