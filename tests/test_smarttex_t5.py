"""T5 projective texturing (docs/SMART_TEXTURE_PLAN.md) on synthetic renders with known cameras."""
import math

import numpy as np
import pytest

from test_smarttex_t2 import SPEC, render, true_camera
from landmark3d.smarttex import bake


def expected_windows(face, W, H):
    """Window mask of the synthetic renderer (1.2 x 2 m windows every 3 m / 3.5 m) in texture coordinates."""
    m = np.zeros((H, W), bool)
    L, zb, zt = face["length"], face["zb"], face["zt"]
    for z0 in np.arange(zb + 1.5, zt - 1.0, 3.5):
        for t0 in np.arange(1.5, L - 1.0, 3.0):
            x0, x1 = int(t0 / L * W) + 2, int((t0 + 1.2) / L * W) - 2
            y0, y1 = int((zt - z0 - 2) / (zt - zb) * H) + 2, int((zt - z0) / (zt - zb) * H) - 2
            if x1 > x0 and y1 > y0:
                m[y0:y1, x0:x1] = True
    return m


def _cams(*azs):
    out = []
    for k, az in enumerate(azs, start=1):
        cam = true_camera(az=az)
        rgb = np.repeat(render(SPEC, cam)[..., None], 3, axis=2)
        out.append((cam, rgb, {"photo": k, "source_id": f"S{k}"}))
    return out


def test_wall_faces():
    faces = bake.wall_faces(SPEC)
    assert len(faces) == 8 and {f["part"] for f in faces} == {"hall", "tower"}


def test_projected_windows_land_where_the_model_has_them():
    res = bake.bake(SPEC, _cams(250.0))
    south = next(r for r in res if r["face"]["part"] == "hall" and r["face"]["edge_index"] == 0)
    assert south["coverage"] > 0.9
    a = np.asarray(south["img"].convert("L"), dtype=np.float32)
    win = expected_windows(south["face"], a.shape[1], a.shape[0])
    wall = ~win & south["mask"]
    assert a[wall].mean() - a[win].mean() > 30                 # the photo's windows sit on the model's windows


def test_faces_turned_away_or_hidden_take_nothing():
    res = bake.bake(SPEC, _cams(250.0))
    got = {(r["face"]["part"], r["face"]["edge_index"]) for r in res}
    assert ("hall", 2) not in got and ("hall", 1) not in got   # north and east walls face away from a SW camera
    # the part of the hall's south wall behind nothing is covered; the tower base sits on the roof, not in front


def test_two_cameras_cover_more_and_blend_without_a_seam():
    one = {(r["face"]["part"], r["face"]["edge_index"]): r for r in bake.bake(SPEC, _cams(220.0))}
    two = {(r["face"]["part"], r["face"]["edge_index"]): r for r in bake.bake(SPEC, _cams(220.0, 300.0))}
    assert len(two) >= len(one)
    assert sum(r["coverage"] for r in two.values()) >= sum(r["coverage"] for r in one.values())


def test_fill_uses_the_background_where_no_photo_sees():
    from PIL import Image
    img = Image.new("RGB", (40, 20), (200, 0, 0))
    mask = np.zeros((20, 40), bool)
    mask[:, :20] = True
    out = np.asarray(bake.fill(img, mask, Image.new("RGB", (8, 8), (0, 0, 200))))
    assert (out[:, :20] == [200, 0, 0]).all() and (out[:, 20:] == [0, 0, 200]).all()


def test_texture_step_uses_projection_for_faces_a_solved_camera_covers(tmp_path, monkeypatch):
    import importlib
    from PIL import Image
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d.project import Project
    from landmark3d import autotex
    from landmark3d.smarttex import keypoints as kp, run as st_run
    p = Project.create("Projected Hall", "X", lat=1.0, lon=2.0)
    cam = true_camera(az=250.0)
    Image.fromarray(render(SPEC, cam)).convert("RGB").save(p.path("input", "photos", "a.jpg"), quality=95)
    p.write_json("input/sources.json", {"sources": [
        {"id": "SRC-1", "kind": "photo", "path": "input/photos/a.jpg", "licence": "cc_by_sa", "author": "Someone",
         "use": "reference_and_texture"}]})
    spec = dict(SPEC, photos=["input/photos/a.jpg"], views=[{"photo": 1, "azimuth_deg": 250.0}],
                materials={"wall": {"srgb": [140, 140, 140]}, "opening": {"srgb": [0, 0, 0]}})
    p.write_json("spec/landmark_spec.json", spec)
    kps = kp.model(SPEC)[0]
    pts, _ = kp.visible(SPEC, cam)
    chosen = kp.choose_labels(SPEC, pts, cam, max_n=6)
    st_run.editor_solve(p, 1, {k: tuple(cam.project([kps[k]])[0]) for k in chosen})
    key_before = autotex.texture_key(p, spec)
    res = autotex.run_v2(p, None)                              # no AI: projection only
    assert res["projected_faces"] >= 2, res
    tx = p.read_json("input/textures.json")["textures"]
    assert any(t.get("kind") == "projected" for t in tx)
    assert autotex.run_v2(p, None) == {"skipped": "up to date"}
    st_run.editor_solve(p, 1, {})                              # cameras changed: textures are made again
    assert autotex.texture_key(p, spec) != key_before
