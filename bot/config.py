"""
Central configuration for the bot.

Everything here is overridable via environment variables (see .env.example),
so operators can tune limits without touching code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Config:
    bot_token: str
    worker_url: str = ''
    worker_secret: str = ''
    fallback_proxy_url: str = ''

    # Output video note is always square: video_note_size x video_note_size.
    # Telegram's official clients typically produce 384x384; 640 is a safe
    # practical ceiling that still keeps file sizes small.
    video_note_size: int = 384

    # Telegram only supports video *messages* (round notes) up to 60 seconds.
    # Anything longer is trimmed to this many seconds from the start.
    max_video_note_duration: int = 60

    # The public Bot API (api.telegram.org) refuses to let a bot *download*
    # a file bigger than this, regardless of what sendVideoNote itself
    # would accept. Only relevant if you're using the public API rather
    # than a self-hosted Bot API server (see ARCHITECTURE.md).
    max_download_size_mb: int = 20

    # How far each directional button press moves the crop box, as a
    # fraction of the box's current side length.
    move_step_fraction: float = 0.12

    # Multiplicative factor applied to the crop box side length on each
    # zoom button press. "Zoom in" shrinks the crop box (more magnified
    # output); "zoom out" grows it (more of the frame visible).
    zoom_in_factor: float = 0.85
    zoom_out_factor: float = 1.0 / 0.85

    # Smallest allowed crop box, as a fraction of min(video_width, video_height).
    min_crop_fraction: float = 0.15

    # Shortest trim range a user is allowed to pick via the timestamp
    # entry flow, in seconds. Guards against a 0/near-0 length output.
    min_trim_span: float = 0.5

    temp_dir: str = "./data/tmp"
    log_level: str = "INFO"

    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "BOT_TOKEN is not set. Copy .env.example to .env and fill it in."
        )

    return Config(
        bot_token=token,
        worker_url=os.getenv('WORKER_URL', ''),
        worker_secret=os.getenv('WORKER_SECRET', ''),
        fallback_proxy_url=os.getenv('FALLBACK_PROXY_URL', ''),
        video_note_size=_int_env("VIDEO_NOTE_SIZE", 384),
        max_video_note_duration=_int_env("MAX_VIDEO_NOTE_DURATION", 60),
        max_download_size_mb=_int_env("MAX_DOWNLOAD_SIZE_MB", 20),
        temp_dir=os.getenv("TEMP_DIR", "./data/tmp"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        ffmpeg_binary=os.getenv("FFMPEG_BINARY", "ffmpeg"),
        ffprobe_binary=os.getenv("FFPROBE_BINARY", "ffprobe"),
    )
