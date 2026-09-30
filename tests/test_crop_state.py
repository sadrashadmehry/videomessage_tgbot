from bot.services.crop_state import clamp_crop, initial_crop, move_crop, reset_crop, zoom_crop


def test_initial_crop_centers_square_in_landscape_video():
    crop = initial_crop(1920, 1080)
    assert crop["size"] == 1080
    assert crop["x"] == (1920 - 1080) // 2
    assert crop["y"] == 0


def test_initial_crop_centers_square_in_portrait_video():
    crop = initial_crop(1080, 1920)
    assert crop["size"] == 1080
    assert crop["x"] == 0
    assert crop["y"] == (1920 - 1080) // 2


def test_move_crop_shifts_within_bounds():
    crop = initial_crop(1920, 1080)
    moved = move_crop(dict(crop), dx_frac=-1.0)
    assert moved["x"] < crop["x"]
    assert moved["y"] == crop["y"]
    assert moved["size"] == crop["size"]


def test_move_crop_clamps_at_frame_edge():
    crop = initial_crop(1920, 1080)
    # Push far left/up repeatedly; should never go negative.
    for _ in range(50):
        crop = move_crop(crop, dx_frac=-1.0, dy_frac=-1.0)
    assert crop["x"] >= 0
    assert crop["y"] >= 0


def test_move_crop_clamps_at_opposite_edge():
    crop = initial_crop(1920, 1080)
    for _ in range(50):
        crop = move_crop(crop, dx_frac=1.0, dy_frac=1.0)
    assert crop["x"] + crop["size"] <= 1920
    assert crop["y"] + crop["size"] <= 1080


def test_zoom_in_shrinks_and_keeps_center():
    crop = initial_crop(1000, 1000)
    center_before = (crop["x"] + crop["size"] / 2, crop["y"] + crop["size"] / 2)

    zoomed = zoom_crop(dict(crop), factor=0.5)

    assert zoomed["size"] < crop["size"]
    center_after = (zoomed["x"] + zoomed["size"] / 2, zoomed["y"] + zoomed["size"] / 2)
    assert abs(center_after[0] - center_before[0]) <= 1
    assert abs(center_after[1] - center_before[1]) <= 1


def test_zoom_out_cannot_exceed_frame():
    crop = initial_crop(1000, 800)
    zoomed = zoom_crop(crop, factor=10.0)
    assert zoomed["size"] <= min(1000, 800)


def test_zoom_in_respects_minimum_fraction():
    crop = initial_crop(1000, 1000)
    for _ in range(50):
        crop = zoom_crop(crop, factor=0.5, min_fraction=0.15)
    assert crop["size"] >= 1000 * 0.15


def test_clamp_crop_is_idempotent():
    crop = initial_crop(1920, 1080)
    once = clamp_crop(dict(crop))
    twice = clamp_crop(dict(once))
    assert once == twice


def test_reset_crop_returns_to_initial():
    crop = initial_crop(1920, 1080)
    mutated = zoom_crop(move_crop(dict(crop), dx_frac=0.5), factor=0.5)
    assert mutated != crop
    assert reset_crop(mutated) == initial_crop(1920, 1080)
