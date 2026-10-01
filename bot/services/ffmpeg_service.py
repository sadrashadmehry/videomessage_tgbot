"""
All shelling-out to ffmpeg/ffprobe lives here, behind small async
functions with typed return values. Nothing else in the codebase should
call subprocess directly.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    """Raised when ffmpeg/ffprobe exits non-zero or output is unusable."""


@dataclass
class VideoMeta:
    width: int
    height: int
    duration: float
    has_audio: bool


async def _run(cmd: list[str]) -> tuple[int, bytes, bytes]:
    logger.debug("running: %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout, stderr


async def probe_video(path: str, ffprobe_binary: str = "ffprobe") -> VideoMeta:
    """Read width/height/duration/audio-presence via ffprobe."""
    cmd = [
        ffprobe_binary,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    code, out, err = await _run(cmd)
    if code != 0:
        raise FFmpegError(f"ffprobe failed: {err.decode(errors='replace')}")

    try:
        data = json.loads(out.decode())
    except json.JSONDecodeError as e:
        raise FFmpegError(f"could not parse ffprobe output: {e}") from e

    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"), None
    )
    if video_stream is None:
        raise FFmpegError("no video stream found in file")

    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))

    # Duration can live on the stream or the format section depending on
    # container; fall back sensibly.
    duration_raw = video_stream.get("duration") or data.get("format", {}).get("duration")
    if duration_raw is None:
        raise FFmpegError("could not determine video duration")

    width = int(video_stream["width"])
    height = int(video_stream["height"])

    # Respect rotation metadata (common with phone-shot vertical video) so
    # width/height match what will actually be displayed.
    rotation = 0
    side_data = video_stream.get("side_data_list", [])
    for sd in side_data:
        if "rotation" in sd:
            rotation = abs(int(sd["rotation"])) % 360
    tags_rotate = video_stream.get("tags", {}).get("rotate")
    if tags_rotate:
        rotation = abs(int(tags_rotate)) % 360
    if rotation in (90, 270):
        width, height = height, width

    return VideoMeta(
        width=width,
        height=height,
        duration=float(duration_raw),
        has_audio=has_audio,
    )


async def extract_preview_frame(
    input_path: str,
    output_path: str,
    timestamp: float = 0.0,
    ffmpeg_binary: str = "ffmpeg",
) -> None:
    """Grab a single frame (used as the background for the crop-picker preview)."""
    cmd = [
        ffmpeg_binary,
        "-y",
        "-ss", f"{max(timestamp, 0):.2f}",
        "-i", input_path,
        "-frames:v", "1",
        "-q:v", "2",
        output_path,
    ]
    code, _, err = await _run(cmd)
    if code != 0:
        raise FFmpegError(f"could not extract preview frame: {err.decode(errors='replace')}")


async def render_video_note(
    input_path: str,
    crop: dict,
    output_path: str,
    *,
    has_audio: bool,
    start: float = 0.0,
    duration: float,
    size: int = 384,
    ffmpeg_binary: str = "ffmpeg",
) -> None:
    """
    Crop to the chosen square, scale to `size`x`size`, keep only
    [start, start+duration) seconds of the source, and re-encode to the
    H.264/AAC baseline profile Telegram clients expect for video notes.

    `start`/`duration` come from the user's trim selection (see
    bot/services/time_range.py); by default (no trim chosen) callers pass
    start=0 and duration=min(source_duration, max_video_note_duration).

    We intentionally do NOT add an alpha channel / circular mask here -
    see ARCHITECTURE.md for why sendVideoNote doesn't need one.
    """
    crop_filter = f"crop={crop['size']}:{crop['size']}:{crop['x']}:{crop['y']}"
    scale_filter = f"scale={size}:{size}:flags=lanczos"
    vf = f"{crop_filter},{scale_filter},format=yuv420p"

    cmd = [ffmpeg_binary, "-y"]
    if start > 0:
        # Input-side (fast/keyframe) seeking: quick even on long source
        # videos, at the cost of the cut point possibly landing on the
        # nearest preceding keyframe rather than the exact frame. Good
        # enough for trimming a casual clip; see ARCHITECTURE.md if you
        # need frame-exact cuts.
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", input_path, "-t", f"{duration:.3f}"]

    cmd += [
        "-vf", vf,
        "-c:v", "libx264",
        "-profile:v", "baseline",
        "-level", "3.0",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
    ]

    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "64k", "-ar", "44100", "-ac", "1"]
    else:
        cmd += ["-an"]

    cmd += ["-movflags", "+faststart", output_path]

    code, _, err = await _run(cmd)
    if code != 0:
        raise FFmpegError(f"ffmpeg render failed: {err.decode(errors='replace')}")


async def get_output_duration(path: str, ffprobe_binary: str = "ffprobe") -> float:
    meta = await probe_video(path, ffprobe_binary=ffprobe_binary)
    return meta.duration


async def render_animation_from_video_note(
    input_path: str,
    output_path: str,
    *,
    ffmpeg_binary: str = "ffmpeg",
    circular_mask: bool = True,
) -> None:
    """Turn our rendered video-note MP4 into a Telegram animation.

    Native round GIF uploads only strip audio and preserve video bytes.
    The legacy Bot API animation path uses a dark circular matte.
    """
    inside = "clip((0.25-(X/W-0.5)*(X/W-0.5)-(Y/H-0.5)*(Y/H-0.5))*W,0,1)"
    vf = (
        "format=yuv444p,geq="
        f"lum='40+(lum(X,Y)-40)*{inside}':"
        f"cb='130+(cb(X,Y)-130)*{inside}':"
        f"cr='127+(cr(X,Y)-127)*{inside}',"
        "format=yuv420p"
    )
    cmd = [
        ffmpeg_binary,
        "-y",
        "-i", input_path,
        "-map", "0:v:0",
        "-an",
    ]
    if circular_mask:
        cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-movflags", "+faststart", output_path]
    code, _, err = await _run(cmd)
    if code != 0:
        raise FFmpegError(
            f"ffmpeg animation remux failed: {err.decode(errors='replace')}"
        )
