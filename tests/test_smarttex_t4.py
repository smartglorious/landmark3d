"""T4 masks (docs/SMART_TEXTURE_PLAN.md): vegetation, GrabCut from boxes, labels used by the T5 projection."""
import cv2
import numpy as np
import pytest

from landmark3d.smarttex import masks

W, H = 800, 600


def scene(seed=0):
    """Yellow wall with smooth green shutters, a textured tree and a red car; sky on top."""
    rng = np.random.default_rng(seed)
    a = np.full((H, W, 3), (215, 185, 110), np.float32) + rng.normal(0, 4, (H, W, 3))
    a[:120] = (120, 165, 225)                                     # sky
    for x in range(80, W - 60, 120):                              # painted green shutters: smooth
        a[220:330, x:x + 50] = (40, 95, 60)
    yy, xx = np.mgrid[:H, :W]
    tree = (xx - 560) ** 2 / 110 ** 2 + (yy - 330) ** 2 / 150 ** 2 < 1
    leaves = np.stack([60 + rng.normal(0, 30, (H, W)), 120 + rng.normal(0, 35, (H, W)), 50 + rng.normal(0, 25, (H, W))], 2)
    a[tree] = leaves[tree]
    car = (xx > 150) & (xx < 330) & (yy > 470) & (yy < 560)
    a[car] = (190, 30, 30)
    return a.clip(0, 255).astype(np.uint8), tree, car


def iou(a, b):
    return (a & b).sum() / max((a | b).sum(), 1)


def test_vegetation_finds_the_tree_not_the_shutters():
    a, tree, _ = scene()
    v = masks.vegetation(a)
    assert iou(v, tree) > 0.7
    shutters = np.zeros((H, W), bool)
    for x in range(80, W - 60, 120):
        shutters[220:330, x:x + 50] = True
    assert (v & shutters & ~tree).sum() < 0.02 * shutters.sum()


def test_grabcut_cuts_the_object_out_of_its_box():
    a, _, car = scene()
    m = masks.grabcut_box(a, (145, 465, 335, 565))
    assert iou(m, car) > 0.7


def test_labels_combine_sky_vegetation_and_objects():
    a, tree, car = scene()
    lab = masks.build(a, [{"class": "vehicle", "box": [145 / W, 465 / H, 335 / W, 565 / H]},
                          {"class": "other_building", "box": [0.0, 0.85, 0.1, 1.0]}])
    assert (lab[:100] == masks.SKY).mean() > 0.9
    assert (lab[tree] == masks.OCCLUDER).mean() > 0.7
    assert (lab[car] == masks.OCCLUDER).mean() > 0.7
    assert (lab[int(0.9 * H), 10] == masks.BUILDING)
    assert (lab[400, 300] == masks.CLEAR)                         # plain wall stays clear


def test_projection_skips_masked_pixels():
    from test_smarttex_t2 import SPEC, render, true_camera
    from landmark3d.smarttex import bake
    cam = true_camera(az=250.0)
    rgb = np.repeat(render(SPEC, cam)[..., None], 3, axis=2)
    lab = np.zeros(rgb.shape[:2], np.uint8)
    lab[:, : rgb.shape[1] // 2] = masks.OCCLUDER                  # the left half of the photo is "behind a tree"
    full = {(r["face"]["part"], r["face"]["edge_index"]): r for r in bake.bake(SPEC, [(cam, rgb, {"photo": 1})])}
    half = {(r["face"]["part"], r["face"]["edge_index"]): r
            for r in bake.bake(SPEC, [(cam, rgb, {"photo": 1, "_mask": lab})])}
    key = ("hall", 0)
    assert half[key]["coverage"] < full[key]["coverage"] - 0.2


def test_a_green_ornament_band_is_not_foliage():
    rng = np.random.default_rng(5)
    a = np.full((H, W, 3), (215, 185, 110), np.float32) + rng.normal(0, 4, (H, W, 3))
    band = np.zeros((H, W), bool)
    band[300:330, 50:750] = True
    a[band] = (40, 110, 60)
    for x in range(60, 740, 40):                                  # white ornaments make the band textured
        a[305:325, x:x + 14] = (235, 235, 230)
    v = masks.vegetation(a.clip(0, 255).astype(np.uint8))
    assert (v & band).sum() < 0.1 * band.sum()
