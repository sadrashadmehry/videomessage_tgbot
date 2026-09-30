from PIL import Image

from bot.handlers.start import START_KEYBOARD, WELCOME
from bot.services.preview_service import render_crop_preview


def test_persistent_start_button_and_colored_crop_outline(tmp_path):
    assert WELCOME.startswith("سلام و ادب و احترام و تشکر و عرض؛")
    assert START_KEYBOARD.is_persistent
    assert START_KEYBOARD.keyboard[0][0].text == "/start"

    frame = tmp_path / "frame.png"
    preview = tmp_path / "preview.jpg"
    Image.new("RGB", (200, 200), "white").save(frame)
    render_crop_preview(str(frame), {"x": 50, "y": 50, "size": 100}, str(preview))
    red, green, blue = Image.open(preview).convert("RGB").getpixel((100, 50))
    assert red > 170 and green > 130 and blue < 100
