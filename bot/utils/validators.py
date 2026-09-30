from __future__ import annotations

VIDEO_MIME_PREFIXES = ("video/",)
GIF_MIME_TYPES = ("image/gif",)


def is_video_like(mime_type: str | None) -> bool:
    if not mime_type:
        return False
    return mime_type.startswith(VIDEO_MIME_PREFIXES)


def is_gif_like(mime_type: str | None) -> bool:
    return mime_type in GIF_MIME_TYPES


def is_supported_media(mime_type: str | None) -> bool:
    """True for anything our ffmpeg pipeline can take as input directly:
    real videos, and GIFs (animated or otherwise) sent as raw files."""
    return is_video_like(mime_type) or is_gif_like(mime_type)


def exceeds_download_limit(file_size_bytes: int | None, max_mb: int) -> bool:
    if file_size_bytes is None:
        return False
    return file_size_bytes > max_mb * 1024 * 1024
