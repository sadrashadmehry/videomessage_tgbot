"""
Crop-box math.

The crop box is always a square (x, y, size) in *source video pixel
coordinates*. It represents the region that will be cut out of the
original video and scaled to fill the final round video message.

Deliberately implemented as plain dict-in / dict-out functions (not a
class) so the state can be dropped straight into aiogram's FSM storage
(which needs JSON-serializable data) without a custom encoder.

Dict shape::

    {
        "video_w": int,   # original video width, px
        "video_h": int,   # original video height, px
        "size": int,      # current crop box side length, px
        "x": int,         # crop box top-left x, px
        "y": int,         # crop box top-left y, px
    }

Telegram itself clips a square video note into a circle inscribed in
that square (see ARCHITECTURE.md - "Why we don't render transparency
ourselves"). So the *circle* the user sees in the preview is always the
circle inscribed in this square; we never need to store it separately.
"""
from __future__ import annotations

CropBox = dict


def initial_crop(video_w: int, video_h: int) -> CropBox:
    """Centered square crop, as large as the source frame allows."""
    size = min(video_w, video_h)
    x = (video_w - size) // 2
    y = (video_h - size) // 2
    return {"video_w": video_w, "video_h": video_h, "size": size, "x": x, "y": y}


def clamp_crop(crop: CropBox, min_fraction: float = 0.15) -> CropBox:
    """Keep the crop box inside the frame and within sane size bounds."""
    w, h = crop["video_w"], crop["video_h"]
    max_size = min(w, h)
    min_size = max(int(max_size * min_fraction), 32)

    size = max(min_size, min(crop["size"], max_size))
    x = max(0, min(crop["x"], w - size))
    y = max(0, min(crop["y"], h - size))

    crop["size"], crop["x"], crop["y"] = size, x, y
    return crop


def move_crop(
    crop: CropBox, dx_frac: float = 0.0, dy_frac: float = 0.0, min_fraction: float = 0.15
) -> CropBox:
    """Nudge the crop box. dx_frac/dy_frac are fractions of the box's own size."""
    crop["x"] += round(crop["size"] * dx_frac)
    crop["y"] += round(crop["size"] * dy_frac)
    return clamp_crop(crop, min_fraction)


def zoom_crop(crop: CropBox, factor: float, min_fraction: float = 0.15) -> CropBox:
    """
    Resize the crop box by `factor`, keeping its center fixed.

    factor < 1  -> box shrinks  -> more magnified output ("zoom in")
    factor > 1  -> box grows    -> more of the frame visible ("zoom out")
    """
    old_size = crop["size"]
    new_size = round(old_size * factor)

    cx = crop["x"] + old_size / 2
    cy = crop["y"] + old_size / 2

    crop["size"] = new_size
    crop["x"] = round(cx - new_size / 2)
    crop["y"] = round(cy - new_size / 2)
    return clamp_crop(crop, min_fraction)


def reset_crop(crop: CropBox) -> CropBox:
    return initial_crop(crop["video_w"], crop["video_h"])
