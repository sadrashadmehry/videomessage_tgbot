"""
Turns free-text like "5-12", "0:05-0:20", "5 to 12", or a lone "5" into a
clamped (start, end) second range, and formats seconds back to mm:ss for
display. No I/O, no Telegram/ffmpeg types - mirrors crop_state.py in that
respect, and is unit tested the same way.
"""
from __future__ import annotations

import re

_NUMBER_RE = re.compile(r"^\d+(\.\d+)?$")


class TimeParseError(ValueError):
    """Raised for any user-facing problem with a timestamp/range string.

    The message is written to be shown to the user as-is.
    """


def parse_timestamp(token: str) -> float:
    """Parse a single timestamp: plain seconds ("12", "12.5") or
    colon-separated mm:ss / h:mm:ss ("1:05", "1:02:03")."""
    token = token.strip()
    if not token:
        raise TimeParseError("Empty timestamp.")

    parts = token.split(":")
    if len(parts) > 3:
        raise TimeParseError(
            f'Couldn\'t understand "{token}" — use seconds (e.g. `12`) or mm:ss (e.g. `1:05`).'
        )

    seconds = 0.0
    multiplier = 1
    for part in reversed(parts):
        if not _NUMBER_RE.match(part):
            raise TimeParseError(
                f'Couldn\'t understand "{token}" — use seconds (e.g. `12`) or mm:ss (e.g. `1:05`).'
            )
        seconds += float(part) * multiplier
        multiplier *= 60
    return seconds


def parse_time_range(
    text: str,
    source_duration: float,
    max_span: float,
    min_span: float = 0.5,
) -> tuple[float, float]:
    """
    Parse "start-end", "start to end", or a lone "start" into a (start, end)
    tuple in seconds, clamped to [0, source_duration] and to at most
    `max_span` seconds long (Telegram's video-message length cap).

    A lone start time keeps the next `max_span` seconds from there (or up
    to the end of the video, whichever is shorter).
    """
    normalized = re.sub(r"\s+to\s+", "-", text.strip(), flags=re.IGNORECASE)

    if "-" in normalized:
        left, _, right = normalized.partition("-")
        tokens = [left.strip(), right.strip()]
    else:
        tokens = normalized.split()

    if len(tokens) not in (1, 2) or not tokens[0]:
        raise TimeParseError(
            "Send a start time, or a start-end range — e.g. `5-12` or `0:05-0:20`."
        )

    start = parse_timestamp(tokens[0])
    end = parse_timestamp(tokens[1]) if len(tokens) == 2 and tokens[1] else None

    if start < 0:
        raise TimeParseError("Start time can't be negative.")
    if start >= source_duration:
        raise TimeParseError(
            f"That's at or after the end of the video ({format_timestamp(source_duration)} long)."
        )

    if end is None:
        end = min(start + max_span, source_duration)
    else:
        if end <= start:
            raise TimeParseError("End time has to be after the start time.")
        end = min(end, source_duration)

    if end - start > max_span:
        end = start + max_span
    if end - start < min_span:
        raise TimeParseError(f"That range is too short — pick at least {min_span:g}s.")

    return round(start, 2), round(end, 2)


def format_timestamp(seconds: float) -> str:
    seconds = max(0, round(seconds))
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_range(start: float, end: float) -> str:
    return f"{format_timestamp(start)}\u2013{format_timestamp(end)}"
