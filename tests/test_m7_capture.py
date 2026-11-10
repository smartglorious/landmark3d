"""Guided capture readiness (plan M7, task 1). Synthetic images only: no licensed photos, no network."""
import random

from PIL import Image, ImageDraw

from landmark3d import capture


def _sharp(path, size=(1400, 1050), mean=120):
    im = Image.new("L", size, mean)
    d = ImageDraw.Draw(im)
    for x in range(0, size[0], 40):                     # strong vertical edges: a sharp frame
        d.rectangle([x, 0, x + 20, size[1]], fill=mean + 60)
    im.convert("RGB").save(path)
    return path


def _soft(path, size=(1200, 900)):
    im = Image.new("L", size, 120)
    im.convert("RGB").save(path)                        # flat grey: no edges at all
    return path


def test_a_frame_with_edges_is_sharp_and_a_flat_one_is_soft(tmp_path):
    s = capture.frame_metrics(_sharp(tmp_path / "s.png"))
    f = capture.frame_metrics(_soft(tmp_path / "f.png"))
    assert s["sharpness"] > capture.BLUR_VARIANCE_MIN > f["sharpness"]


def test_too_few_photos_is_reported_not_passed(tmp_path):
    paths = [_sharp(tmp_path / f"p{i}.png") for i in range(3)]
    r = capture.assess(paths)
    assert r["ready"] is False and any("at least" in p for p in r["problems"])


def test_soft_and_small_frames_are_listed_by_name(tmp_path):
    good = [_sharp(tmp_path / f"g{i}.png") for i in range(capture.MIN_PHOTOS)]
    bad = _soft(tmp_path / "soft.png", size=(600, 400))
    r = capture.assess(good + [bad])
    text = " ".join(r["problems"])
    assert "soft.png" in text and "short side" in text
    assert r["ready"] is False


def test_pose_checks_are_never_claimed_as_passed(tmp_path):
    good = [_sharp(tmp_path / f"g{i}.png") for i in range(capture.MIN_PHOTOS)]
    r = capture.assess(good)
    assert r["ready"] is True                            # every check the pictures allow passes
    assert "overlap between neighbouring photos" in r["pending"]
    assert r["instructions"] and all("Ankh" not in s for s in r["instructions"])


def test_an_unreadable_file_is_reported(tmp_path):
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"not an image")
    r = capture.assess([bad])
    assert r["ready"] is False and any("cannot be read" in p for p in r["problems"])
