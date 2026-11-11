"""T1 photo intake (docs/SMART_TEXTURE_PLAN.md): EXIF focal prior, division-model undistortion, panorama check."""
import math

import cv2
import numpy as np
import pytest
from PIL import Image

from landmark3d.smarttex import lens

W, H = 1200, 800


def grid_image():
    a = np.full((H, W, 3), 235, np.uint8)
    for x in range(100, W, 125):
        cv2.line(a, (x, 40), (x, H - 40), (30, 30, 30), 3)
    for y in range(80, H, 110):
        cv2.line(a, (40, y), (W - 40, y), (30, 30, 30), 3)
    return a


def distort(a, k):
    """Image as a lens with division parameter k would record it: sample the clean image at x_u(x_d)."""
    h, w = a.shape[:2]
    c = np.array([w / 2, h / 2])
    s = math.hypot(w, h) / 2
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
    dx, dy = (xs - c[0]) / s, (ys - c[1]) / s
    f = 1 + k * (dx ** 2 + dy ** 2)
    return cv2.remap(a, (c[0] + dx / f * s).astype(np.float32), (c[1] + dy / f * s).astype(np.float32),
                     cv2.INTER_LINEAR, borderValue=(235, 235, 235))


def test_straight_grid_needs_no_correction():
    rec = lens.intake(Image.fromarray(grid_image()))
    assert abs(rec["k"]) < 0.02
    assert rec["straight_after"] < lens.STRAIGHT_MAX
    assert rec["projection"] == "pinhole"


@pytest.mark.parametrize("k", [-0.2, 0.15])
def test_recovers_barrel_and_pincushion(k):
    img = Image.fromarray(distort(grid_image(), k))
    rec = lens.intake(img)
    assert rec["k"] == pytest.approx(k, abs=0.04)
    assert rec["straight_after"] < rec["straight_before"]
    assert rec["straight_after"] < lens.STRAIGHT_MAX
    fixed = lens.undistort_image(img, rec["k"])
    again = lens.intake(fixed)
    assert abs(again["k"]) < 0.04


def test_undistort_points_matches_the_model():
    pts = np.array([[100.0, 100.0], [600.0, 400.0], [1100.0, 700.0]])
    out = lens.undistort_points(pts, 0.0, (W, H))
    assert np.allclose(out, pts)
    out = lens.undistort_points(pts, 0.2, (W, H))
    assert np.allclose(out[1], pts[1])                       # the centre does not move
    assert np.linalg.norm(out[0] - [600, 400]) < np.linalg.norm(pts[0] - [600, 400])


def test_wide_panorama_is_not_projected():
    a = np.hstack([grid_image(), grid_image(), grid_image()])[:, :2900]
    rec = lens.intake(Image.fromarray(a))
    assert rec["projection"] == "panorama" and any("aspect" in w for w in rec["why"])


def test_lines_bent_in_a_non_radial_way_flag_a_panorama():
    a = np.full((H, W, 3), 235, np.uint8)
    xs = np.arange(40, W - 40)
    for y0 in range(120, H - 80, 120):                    # cylindrical-panorama-like sag, not radial
        ys = (y0 + 60 * np.sin(np.pi * (xs - 40) / (W - 80)) * (1 if y0 < H / 2 else -1)).astype(np.int32)
        cv2.polylines(a, [np.stack([xs, ys], 1)], False, (30, 30, 30), 3)
    for x in range(100, W, 125):
        cv2.line(a, (x, 40), (x, H - 40), (30, 30, 30), 3)
    rec = lens.intake(Image.fromarray(a))
    assert rec["projection"] == "panorama"


def test_exif_focal_from_35mm_equivalent():
    img = Image.new("RGB", (4000, 3000))
    exif = img.getexif()
    exif.get_ifd(0x8769)[0xA405] = 24
    img.info["exif"] = exif.tobytes()
    import io
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif.tobytes())
    back = Image.open(io.BytesIO(buf.getvalue()))
    assert lens.exif_focal_px(back) == pytest.approx(24 / 36 * 4000)
    assert lens.exif_focal_px(Image.new("RGB", (10, 10))) is None
