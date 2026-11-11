"""T2 camera solve (docs/SMART_TEXTURE_PLAN.md) on synthetic renders with a known camera."""
import math

import cv2
import numpy as np
import pytest

from landmark3d.smarttex import camera as cm
from landmark3d.smarttex import keypoints as kp

SPEC = {
    "footprint": {"local": [[-20, -10], [20, -10], [20, 10], [-20, 10]]},
    "parts": [
        {"id": "hall", "footprint": {"rect": {"cx": 0, "cy": 0, "w": 40, "d": 20, "rot_deg": 0}},
         "base_z": 0.0, "height": 12.0},
        {"id": "tower", "footprint": {"rect": {"cx": 8, "cy": -4, "w": 8, "d": 8, "rot_deg": 0}},
         "base_z": 12.0, "height": 14.0},
    ],
}
W, H = 1600, 1067


def true_camera(az=240.0, el=6.0, dist=70.0, fov=60.0):
    f = cm.focal_from_fov(W, H, fov)
    a, e = math.radians(az), math.radians(el)
    eye = np.array([dist * math.cos(e) * math.cos(a), dist * math.cos(e) * math.sin(a), 1.7 + dist * math.sin(e)])
    rvec, tvec = kp.look_at(eye, np.array([0.0, 0.0, 9.0]))
    return kp.Camera(W, H, f, rvec, tvec)


def _on_edge(p, a, b):
    a, b, p = np.array(a), np.array(b), np.array(p)
    t = np.dot(p - a, b - a) / np.dot(b - a, b - a)
    return 0 <= t <= 1 and np.linalg.norm(a + t * (b - a) - p) < 1e-6


def render(spec, cam, grid_bands=False):
    """Flat-shaded faces (painter's order) with window rows, on a sky-like background. grid_bands draws the
    facade grid instead: dark floor bands and light pilasters on the bay lines."""
    img = np.full((H, W), 205, np.uint8)
    faces = []
    for pid, (ring, zb, zt) in kp.part_rings(spec).items():
        n = len(ring)
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            quad = np.array([[a[0], a[1], zb], [b[0], b[1], zb], [b[0], b[1], zt], [a[0], a[1], zt]])
            nrm = np.array([b[1] - a[1], -(b[0] - a[0])]) / max(1e-9, math.dist(a, b))
            sun = np.array([0.8, -0.45])                      # lit from the south-east: neighbouring walls differ
            shade = 70 + int(90 * max(0.0, float(nrm @ sun) * 0.5 + 0.5))
            faces.append((quad, shade, (a, b, zb, zt)))
        top = np.array([[x, y, zt] for x, y in ring])
        faces.append((top, 150, None))
    faces.sort(key=lambda f: -cam.depth(f[0]).mean())
    vis = kp.face_visible(kp.part_rings(spec), cam)
    for quad, shade, info in faces:
        if (cam.depth(quad) <= 0).any():
            continue
        uv = cam.project(quad)
        cv2.fillPoly(img, [np.round(uv).astype(np.int32)], int(shade))
        if info is not None and grid_bands:
            a, b, zb, zt = info
            from landmark3d.smarttex import grid as gr
            pid = next(p for p, (rg, z0, z1) in kp.part_rings(spec).items() if z0 == zb and z1 == zt
                       and any(np.allclose(rg[i], a) for i in range(len(rg))))
            for _, _, kind, A, B in [l for l in gr.face_lines(spec) if l[0] == pid]:
                if not (np.allclose(A[:2], a) or np.allclose(A[:2], b) or _on_edge(A[:2], a, b)):
                    continue
                if kind == "h" and zb < A[2] < zt:
                    q = np.array([A, B, B + [0, 0, 0.35], A + [0, 0, 0.35]])
                elif kind == "v" and 0.01 < math.dist(A[:2], a) < math.dist(a, b) - 0.01:
                    d = (np.array(b) - np.array(a)) / math.dist(a, b) * 0.3
                    q = np.array([A, B, B + np.r_[d, 0], A + np.r_[d, 0]])
                else:
                    continue
                cv2.fillPoly(img, [np.round(cam.project(q)).astype(np.int32)], int(shade * 1.35) if kind == "v"
                             else int(shade * 0.6))
        if info is not None and not grid_bands:
            a, b, zb, zt = info
            L = math.dist(a, b)
            for z in np.arange(zb + 1.5, zt - 1.0, 3.5):
                for t in np.arange(1.5, L - 1.0, 3.0):
                    p0 = np.array(a) + (np.array(b) - np.array(a)) * t / L
                    p1 = np.array(a) + (np.array(b) - np.array(a)) * (t + 1.2) / L
                    w = np.array([[p0[0], p0[1], z], [p1[0], p1[1], z], [p1[0], p1[1], z + 2], [p0[0], p0[1], z + 2]])
                    cv2.fillPoly(img, [np.round(cam.project(w)).astype(np.int32)], int(shade * 0.45))
    return img


def coarse_points(cam, noise_share, n=10, seed=0):
    rng = np.random.default_rng(seed)
    pts, _ = kp.visible(SPEC, cam)
    ids = list(pts)[:n] if len(pts) <= n else [ids for ids in kp.choose(pts, cam, n)]
    return {k: tuple(np.array(pts[k]) + rng.normal(0, noise_share * cam.diag, 2)) for k in ids}


def test_model_keypoints_and_edges():
    kps, edges = kp.model(SPEC)
    assert len(kps) == 16 and len(edges) == 24
    assert kps["tower/0/t"][2] == pytest.approx(26.0)


def test_look_at_points_the_camera_at_the_target():
    cam = true_camera()
    uv = cam.project([[0.0, 0.0, 9.0]])[0]
    assert uv == pytest.approx([W / 2, H / 2], abs=1e-6)


def test_back_faces_are_hidden():
    cam = true_camera(az=270.0)                 # from the south (y < 0)
    fv = kp.face_visible(kp.part_rings(SPEC), cam)
    assert fv[("hall", 0)] and not fv[("hall", 2)]


def test_exact_points_recover_the_camera():
    cam = true_camera()
    pts, _ = kp.visible(SPEC, cam)
    res = cm.solve(SPEC, render(SPEC, cam), {k: v for k, v in pts.items()}, use_search=False)
    assert res["status"] == "solved"
    c = res["camera"]
    assert c.f == pytest.approx(cam.f, rel=0.02)
    assert np.linalg.norm(c.centre - cam.centre) < 0.02 * np.linalg.norm(cam.centre)
    assert res["rms"] < cm.GATE["rms_max"]


@pytest.mark.parametrize("az", [215.0, 250.0, 300.0])
def test_noisy_coarse_points_are_snapped_onto_the_true_corners(az):
    cam = true_camera(az=az)
    img = render(SPEC, cam)
    coarse = coarse_points(cam, 0.015, n=10, seed=int(az))
    res = cm.solve(SPEC, img, coarse, use_search=False)
    assert res["status"] == "solved", res.get("why")
    c = res["camera"]
    # the solved camera puts the visible model corners within 0.5 % (RMS) and 1 % (max) of the diagonal of
    # their true positions
    pts, _ = kp.visible(SPEC, cam)
    X = np.array([kp.model(SPEC)[0][k] for k in pts])
    err = np.linalg.norm(c.project(X) - cam.project(X), axis=1)
    assert np.sqrt((err ** 2).mean()) < 0.005 * cam.diag, (err, res["rms"], res["chamfer"])
    assert err.max() < 0.01 * cam.diag, (err, res["rms"], res["chamfer"])
    assert c.f == pytest.approx(cam.f, rel=0.05)
    assert res["chamfer"] is not None and res["chamfer"] <= cm.GATE["chamfer_max"]


def test_too_few_points_fail_with_a_reason():
    cam = true_camera()
    pts, _ = kp.visible(SPEC, cam)
    res = cm.solve(SPEC, render(SPEC, cam), dict(list(pts.items())[:1]))
    assert res["status"] == "failed" and "at least 2" in res["why"]
    assert cm.gate(res)
    res = cm.solve(SPEC, render(SPEC, cam), dict(list(pts.items())[:3]), use_vp=False)
    assert res["status"] == "failed" and "at least 4" in res["why"]


def test_vanishing_points_give_the_true_rotation_among_the_candidates():
    cam = true_camera(az=235.0)
    img = render(SPEC, cam)
    from landmark3d.smarttex import lens
    vps = cm.scene_vps(lens.segments(img, 0.008 * cam.diag), (W, H))
    assert vps["vertical"] is not None and vps["horizontal"]
    assert cm.model_directions(SPEC) == [0.0]
    rvecs = cm.rotations_from_vps(vps, cam.f, (W, H), cm.model_directions(SPEC), {"azimuth_deg": 235.0})
    angles = [np.degrees(np.linalg.norm(cv2.Rodrigues(cv2.Rodrigues(r)[0] @ cam.R.T)[0])) for r in rvecs]
    assert min(angles) < 1.5


def test_translation_from_two_points_with_a_known_rotation():
    cam = true_camera(az=260.0)
    kps = kp.model(SPEC)[0]
    X = np.array([kps["hall/0/b"], kps["tower/0/t"]])
    t = cm.translation_for(cam.R, cam.f, (W, H), X, cam.project(X))
    assert np.allclose(t, cam.tvec, atol=1e-6)


@pytest.mark.parametrize("az", [215.0, 260.0, 300.0])
def test_three_coarse_points_suffice_with_vanishing_points(az):
    cam = true_camera(az=az)
    img = render(SPEC, cam)
    coarse = coarse_points(cam, 0.015, n=3, seed=int(az))
    res = cm.solve(SPEC, img, coarse, view={"azimuth_deg": az + 8.0}, use_search=False)
    assert res["status"] == "solved", res.get("why")
    pts, _ = kp.visible(SPEC, cam)
    X = np.array([kp.model(SPEC)[0][k] for k in pts])
    err = np.linalg.norm(res["camera"].project(X) - cam.project(X), axis=1)
    assert np.sqrt((err ** 2).mean()) < 0.005 * cam.diag, (err, res.get("start"))
    assert not res["gate"], res["gate"]


def test_wrong_part_geometry_is_reported_as_mismatch():
    cam = true_camera(az=250.0)
    img = render(SPEC, cam)                              # the photo shows the tower 14 m tall ...
    wrong = {"footprint": SPEC["footprint"], "parts": [dict(SPEC["parts"][0]),
                                                       dict(SPEC["parts"][1], height=22.0)]}   # ... the spec says 22
    pts, _ = kp.visible(SPEC, cam)
    true_kps = kp.model(SPEC)[0]
    coarse = {}
    for k, v in pts.items():                          # the AI marks where each corner appears in the photo
        coarse[k] = v
    res = cm.solve(wrong, img, coarse, use_search=False)
    assert res["status"] == "solved"
    assert "tower" in res["mismatch"], res["parts"]
    assert any("geometry_mismatch" in p for p in cm.gate(res))


@pytest.fixture()
def prj(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d.project import Project
    from PIL import Image
    p = Project.create("Synthetic Hall", "X", lat=1.0, lon=2.0)
    cam = true_camera(az=240.0)
    Image.fromarray(render(SPEC, cam)).convert("RGB").save(p.path("input", "photos", "a.jpg"), quality=95)
    p.write_json("input/sources.json", {"sources": [
        {"id": "SRC-1", "kind": "photo", "path": "input/photos/a.jpg", "licence": "cc_by_sa", "author": "Someone",
         "use": "reference_and_texture"}]})
    spec = dict(SPEC, photos=["input/photos/a.jpg"], views=[{"photo": 1, "azimuth_deg": 250.0, "elevation_deg": 8.0}],
                materials={})
    p.write_json("spec/landmark_spec.json", spec)
    return p, cam


def test_run_writes_a_valid_cameras_file_with_a_fake_ai(prj):
    from landmark3d.smarttex import run as st_run
    p, cam = prj
    kps = kp.model(SPEC)[0]
    rng = np.random.default_rng(3)

    def fake_ask(prj_, provider, photo, sketch, labels, name, stem):
        W_, H_ = photo.size
        out = {}
        for lab, kid in labels.items():
            u, v = cam.project([kps[kid]])[0] + rng.normal(0, 0.01 * cam.diag, 2)
            if 0 <= u < W_ and 0 <= v < H_:
                out[lab] = (u / W_, v / H_, 0.9)
        return out

    summary = st_run.run(p, provider=object(), ask=fake_ask)
    doc = p.read_json("input/cameras.json")
    assert st_run.schema_errors(doc) == []
    assert summary["solved"] == 1, doc["photos"][0].get("why")
    rec = doc["photos"][0]
    assert rec["metrics"]["rms"] < cm.GATE["rms_max"]
    solved = kp.Camera.from_json(rec["camera"])
    assert np.linalg.norm(solved.centre - cam.centre) < 0.03 * np.linalg.norm(cam.centre)
    assert (p.root / rec["overlay"]).is_file() and (p.root / rec["undistorted"]).is_file()


def test_run_reports_an_ai_failure_instead_of_guessing(prj):
    from landmark3d.smarttex import run as st_run
    p, _ = prj

    def broken(*a, **k):
        raise RuntimeError("quota")
    st_run.run(p, provider=object(), ask=broken)
    rec = p.read_json("input/cameras.json")["photos"][0]
    assert rec["status"] == "failed" and "quota" in rec["why"]


SPEC_GRID = {
    "footprint": SPEC["footprint"],
    "parts": [dict(SPEC["parts"][0], facade={"level_height": 4.5, "bay_spacing": 4.0}),
              dict(SPEC["parts"][1], facade={"level_height": 3.5, "bay_spacing": 4.0})],
}


def test_face_lines_follow_the_facade_grid():
    from landmark3d.smarttex import grid as gr
    lines = [l for l in gr.face_lines(SPEC_GRID) if l[0] == "hall" and l[1] == 0]
    hz = sorted(l[3][2] for l in lines if l[2] == "h")
    assert hz == [0.0, 4.5, 9.0, 12.0]                       # base, two floor lines, wall top
    assert sum(1 for l in lines if l[2] == "v") == 11         # 10 bays of 4 m on a 40 m face: 9 lines + 2 ends


@pytest.mark.parametrize("az", [215.0, 270.0])
def test_position_search_rescues_poor_coarse_points(az):
    """Three coarse points 3-7 % off: no start lands near the truth; the position search (outline against the sky
    plus corner support) recovers it. Known failure kept out: 300 degrees, where the focal sweep misses (see
    docs/SMART_TEXTURE_PLAN.md section 8)."""
    cam = true_camera(az=az)
    img = render(SPEC_GRID, cam, grid_bands=True)
    coarse = coarse_points(cam, 0.03, n=3, seed=int(az) + 7)
    pts, _ = kp.visible(SPEC_GRID, cam)
    X = np.array([kp.model(SPEC_GRID)[0][k] for k in pts])

    def true_rms(res):
        e = np.linalg.norm(res["camera"].project(X) - cam.project(X), axis=1)
        return float(np.sqrt((e ** 2).mean()))
    with_grid = cm.solve(SPEC_GRID, img, coarse, view={"azimuth_deg": az})
    assert with_grid["status"] == "solved", with_grid.get("why")
    assert true_rms(with_grid) < 0.005 * cam.diag, (true_rms(with_grid), with_grid.get("start"))


def test_vanishing_point_focal():
    cam = true_camera(az=225.0, el=15.0)
    img = render(SPEC, cam)
    from landmark3d.smarttex import lens
    segs = lens.segments(img, 0.008 * cam.diag)
    f = cm.focal_from_vps(cm.vanishing_points(segs, (W, H)), (W, H))
    assert f is not None and f == pytest.approx(cam.f, rel=0.15)
