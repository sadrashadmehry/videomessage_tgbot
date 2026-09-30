"""
Renders the still-image preview shown to the user while they position the
circle. Everything *outside* the crop's inscribed circle is dimmed, so the
preview looks close to what the final round video message will look like,
even though under the hood we still just do a square ffmpeg crop.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

DIM_FACTOR = 0.32  # how dark the "will be hidden" area gets (0=black, 1=unchanged)
DISPLAY_MAX_SIDE = 900  # cap preview image size so it stays fast to send/edit


def render_crop_preview(frame_path: str, crop: dict, output_path: str) -> None:
    img = Image.open(frame_path).convert("RGB")
    w, h = img.size

    dimmed = Image.eval(img, lambda p: int(p * DIM_FACTOR))

    # White circle on black = "keep original here", used as a composite mask.
    mask = Image.new("L", (w, h), 0)
    mdraw = ImageDraw.Draw(mask)
    x, y, size = crop["x"], crop["y"], crop["size"]
    mdraw.ellipse([x, y, x + size, y + size], fill=255)

    composed = Image.composite(img, dimmed, mask)

    draw = ImageDraw.Draw(composed)
    outline_width = max(2, size // 150)
    draw.ellipse([x, y, x + size, y + size], outline=(255, 255, 255), width=outline_width)
    draw.rectangle([x, y, x + size, y + size], outline=(255, 255, 255, 128), width=1)

    if max(w, h) > DISPLAY_MAX_SIDE:
        scale = DISPLAY_MAX_SIDE / max(w, h)
        composed = composed.resize((max(1, int(w * scale)), max(1, int(h * scale))))

    composed.save(output_path, "JPEG", quality=88)
