"""Deterministic texture-crop fixes (smarttex.quads), from the Đức Bà case study."""
import cv2
import numpy as np
import pytest
from PIL import Image

from landmark3d.smarttex import quads

W, H = 1200, 800
WALL = (170, 90, 60)


def wall_photo(x0=300, y0=200, x1=900, y1=650, sky=True):
    a = np.full((H, W, 3), (120, 160, 220) if sky else (90, 90, 90), np.uint8)
    rng = np.random.default_rng(0)
    wall = np.clip(np.array(WALL, float) + rng.normal(0, 6, (y1 - y0, x1 - x0, 1)), 0, 255).astype(np.uint8)
    a[y0:y1, x0:x1] = wall
    for k in range(5):                                       # windows, centred in 5 bays
        cx = x0 + (k + 0.5) * (x1 - x0) / 5
        a[y0 + 120:y0 + 330, int(cx - 30):int(cx + 30)] = (35, 35, 40)
    return Image.fromarray(a)


def test_quad_corners_snap_onto_the_wall_edges():
    img = wall_photo()
    true = [[300 / W, 200 / H], [900 / W, 200 / H], [900 / W, 650 / H], [300 / W, 650 / H]]
    rough = [[0.27, 0.22], [0.77, 0.24], [0.76, 0.83], [0.24, 0.79]]      # 1-4 % off, as the AI reads a grid
    fixed, n = quads.refine_quad(img, rough)
    assert n == 4
    err = np.abs(np.array(fixed) - np.array(true)) * [W, H]
    assert err.max() < 4, err


def test_quad_without_lines_is_left_alone():
    img = Image.new("RGB", (W, H), (128, 128, 128))
    rough = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]
    fixed, n = quads.refine_quad(img, rough)
    assert n == 0 and fixed == rough


def test_obliqueness():
    assert quads.obliqueness([[0, 0], [1, 0], [1, 1], [0, 1]], (100, 100)) == pytest.approx(1.0)
    assert quads.obliqueness([[0, 0.3], [1, 0], [1, 1], [0, 0.7]], (100, 100)) == pytest.approx(2.5)


def test_centre_bay_moves_the_window_to_the_middle():
    a = np.full((200, 100, 3), WALL, np.uint8)
    rng = np.random.default_rng(1)
    a = np.clip(a + rng.normal(0, 4, a.shape), 0, 255).astype(np.uint8)
    a[50:150, 0:20] = (30, 30, 30)                          # window cut by the tile edge
    a[50:150, 85:100] = (30, 30, 30)
    out, shift = quads.centre_bay(Image.fromarray(a))
    assert shift is not None
    from landmark3d.smarttex import metrics
    o = np.asarray(out)
    assert metrics.bay_phase(np.hstack([o, o, o]), 3)["error"] < 0.05


def test_auto_qa_trims_sky_and_keeps_a_clean_wall():
    a = np.asarray(wall_photo(0, 0, W, H, sky=False)).copy()
    verdict, _, _ = quads.auto_qa(Image.fromarray(a))
    assert verdict == "keep"
    a[:160] = (120, 160, 220)                                # 20 % sky at the top
    verdict, why, top = quads.auto_qa(Image.fromarray(a))
    assert verdict == "trim" and 0.19 < top < 0.26, (why, top)
    a[:600] = (120, 160, 220)                                # 75 % sky
    assert quads.auto_qa(Image.fromarray(a))[0] == "drop"


def test_auto_qa_drops_a_roof_slope():
    a = np.full((400, 600, 3), (150, 80, 60), np.uint8)
    for k in range(-400, 600, 25):                           # tile rows along a 40-degree slope
        cv2.line(a, (k, 400), (k + 477, 0), (90, 40, 30), 3)
    verdict, why, _ = quads.auto_qa(Image.fromarray(a))
    assert verdict == "drop" and "roof" in why


def test_part_colours_are_brought_together():
    face = {"part": "p", "face": "p#0"}
    items = [{"face": face, "img": Image.new("RGB", (40, 40), (200, 100, 80))},
             {"face": face, "img": Image.new("RGB", (40, 40), (160, 80, 64))},
             {"face": face, "img": Image.new("RGB", (40, 40), (180, 90, 72))}]
    quads.match_part_colours(items)
    means = [np.asarray(i["img"]).reshape(-1, 3).mean(axis=0) for i in items]
    assert np.ptp([m[0] for m in means]) < 2


def test_wall_swatch_takes_a_plain_area_of_the_material_colour():
    img = wall_photo(0, 0, W, H, sky=False)                   # brick-coloured wall with dark windows
    s = quads.wall_swatch([img], WALL)
    assert s is not None and s.width in quads.SWATCH_SIZES and s.width == s.height
    m = np.asarray(s).reshape(-1, 3).mean(axis=0)
    assert np.linalg.norm(m - np.array(WALL)) < 10           # plain wall, not a window
    assert quads.wall_swatch([img], (240, 240, 240)) is None  # no area of that colour


def _bricks(h=256, w=256, seed=2):
    rng = np.random.default_rng(seed)
    a = np.full((h, w, 3), (160, 80, 60), np.float32)
    for y in range(0, h, 8):
        a[y:y + 1] = (110, 100, 95)                          # mortar courses: fine texture
    return np.clip(a + rng.normal(0, 6, a.shape), 0, 255).astype(np.uint8)


def test_structure_tells_fine_brick_from_a_pilaster():
    plain = _bricks()
    pil = plain.copy()
    pil[:, 100:150] = (230, 220, 200)                         # a pale pilaster through the sample
    assert quads.structure(plain) < quads.STRUCTURE_MAX < quads.structure(pil)


def test_seamless_tile_has_no_seam_and_no_blotch():
    a = _bricks().astype(np.float32)
    a *= np.linspace(0.6, 1.2, 256)[None, :, None]            # light falls off across the sample
    t = np.asarray(quads.seamless(Image.fromarray(a.clip(0, 255).astype(np.uint8)), 256), dtype=np.float32)
    lum = t.mean(axis=2)
    assert abs(lum[:, :32].mean() - lum[:, -32:].mean()) < 6     # flattened
    assert abs(lum[:, 0].mean() - lum[:, -1].mean()) < 6         # wraps without a seam
    assert abs(lum[0].mean() - lum[-1].mean()) < 6


def test_roof_band_is_taken_above_the_wall_top():
    a = np.full((400, 600, 3), (120, 160, 220), np.uint8)
    a[150:250, 100:500] = (180, 70, 50)                       # roof band
    a[250:380, 100:500] = (220, 190, 140)                     # wall
    corners = [[100 / 600, 250 / 400], [500 / 600, 250 / 400], [500 / 600, 380 / 400], [100 / 600, 380 / 400]]
    band = quads.roof_region(Image.fromarray(a), corners, rise=0.6)
    m = np.asarray(band).reshape(-1, 3).mean(axis=0)
    assert np.linalg.norm(m - [180, 70, 50]) < 15


def _arcade(W=600, H=200, period=75.0, offset=20.0):
    a = np.full((H, W, 3), (215, 195, 150), np.uint8)
    rng = np.random.default_rng(3)
    a = np.clip(a + rng.normal(0, 4, a.shape), 0, 255).astype(np.uint8)
    x = offset
    while x < W:
        a[50:170, int(x - 15):int(x + 15)] = (40, 40, 45)
        x += period
    return Image.fromarray(a)


def test_window_period_found_and_one_real_bay_cut():
    p, s = quads.window_period(_arcade())
    assert p == pytest.approx(75, abs=3) and s >= quads.PERIOD_MIN_STRENGTH
    tile = np.asarray(quads.real_bay_tile(_arcade(), p))
    from landmark3d.smarttex import metrics
    assert metrics.bay_phase(np.hstack([tile, tile, tile]), 3)["error"] < 0.1


def test_no_period_on_a_plain_wall():
    a = np.full((200, 600, 3), 180, np.uint8)
    assert quads.window_period(Image.fromarray(a))[0] is None


def test_extend_bays_follows_the_facade_and_stops_at_the_photo_edge():
    corners = [[0.4, 0.3], [0.5, 0.3], [0.5, 0.7], [0.4, 0.7]]
    e = quads.extend_bays(corners, 1, 1, (1000, 1000))
    assert e[0] == pytest.approx([0.3, 0.3], abs=1e-6) and e[1] == pytest.approx([0.6, 0.3], abs=1e-6)
    assert quads.extend_bays(corners, 5, 0, (1000, 1000)) is None
