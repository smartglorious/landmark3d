"""Joint fit of photo cameras and part heights with the plan fixed (smarttex.heights)."""
import copy

import numpy as np
import pytest

from test_smarttex_t2 import SPEC, render, true_camera
from landmark3d.smarttex import camera as cm
from landmark3d.smarttex import heights as hf
from landmark3d.smarttex import keypoints as kp


def wrong_spec(hall=9.0, tower=20.0):
    sp = copy.deepcopy(SPEC)
    sp["parts"][0]["height"] = hall
    sp["parts"][1]["height"] = tower
    sp["parts"][1]["base_z"] = hall                       # the tower still stands on the hall
    return sp


def photo(az, noise=0.004, seed=0):
    rng = np.random.default_rng(seed)
    cam = true_camera(az=az)
    kps = kp.model(SPEC)[0]
    pts, _ = kp.visible(SPEC, cam)
    obs = {k: tuple(np.array(v) + rng.normal(0, noise * cam.diag, 2)) for k, v in pts.items()}
    return cam, obs


def initial(sp, obs, cam):
    img = render(SPEC, cam)
    r = cm.solve_manual(sp, img, obs, drop_outliers=True)
    return r["camera"]


def test_supports_follow_the_stack():
    assert hf.supports(SPEC) == {"tower": "hall"}


def test_wrong_heights_are_recovered_from_two_photos():
    sp = wrong_spec()
    photos = []
    for az, seed in ((230.0, 1), (300.0, 2)):
        cam, obs = photo(az, seed=seed)
        c0 = initial(sp, obs, cam)
        assert c0 is not None
        photos.append({"size": (cam.width, cam.height), "points": obs, "camera": c0, "f_prior": None})
    res = hf.solve(sp, photos)
    assert res["accepted"], res["rms"]
    assert res["heights"]["hall"] == pytest.approx(12.0, rel=0.06)
    assert res["heights"]["tower"] == pytest.approx(14.0, rel=0.08)
    new = hf.apply(sp, res["heights"])
    tower = next(p for p in new["parts"] if p["id"] == "tower")
    assert tower["base_z"] == pytest.approx(res["heights"]["hall"], abs=0.01)


def test_correct_heights_stay_put():
    photos = []
    for az, seed in ((230.0, 3), (300.0, 4)):
        cam, obs = photo(az, seed=seed)
        photos.append({"size": (cam.width, cam.height), "points": obs, "camera": initial(SPEC, obs, cam),
                       "f_prior": None})
    res = hf.solve(SPEC, photos)
    assert res["heights"]["hall"] == pytest.approx(12.0, rel=0.04)
    assert res["heights"]["tower"] == pytest.approx(14.0, rel=0.06)


def test_one_photo_with_few_points_is_not_trusted():
    sp = wrong_spec()
    cam, obs = photo(230.0, seed=5)
    few = dict(list(obs.items())[:6])
    res = hf.solve(sp, [{"size": (cam.width, cam.height), "points": few, "camera": initial(sp, obs, cam),
                         "f_prior": None}])
    assert not res["accepted"] and "at least 2" in res["why"]
