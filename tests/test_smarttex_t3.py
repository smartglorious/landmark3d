"""T3 camera editor (docs/SMART_TEXTURE_PLAN.md): hand-placed points solve a photo's camera, through the web API."""
import importlib

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from test_smarttex_t2 import SPEC, render, true_camera
from landmark3d.smarttex import camera as cm
from landmark3d.smarttex import keypoints as kp


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d.project import Project
    from landmark3d import server
    p = Project.create("Editor Hall", "X", lat=1.0, lon=2.0)
    cam = true_camera(az=240.0)
    Image.fromarray(render(SPEC, cam)).convert("RGB").save(p.path("input", "photos", "a.jpg"), quality=95)
    p.write_json("input/sources.json", {"sources": [
        {"id": "SRC-1", "kind": "photo", "path": "input/photos/a.jpg", "licence": "cc_by_sa", "author": "Someone",
         "use": "reference_and_texture"}]})
    p.write_json("spec/landmark_spec.json", dict(SPEC, photos=["input/photos/a.jpg"], materials={},
                                                 views=[{"photo": 1, "azimuth_deg": 250.0, "elevation_deg": 8.0}]))
    return TestClient(server.app), p, cam


def _true_points(cam, ids):
    kps = kp.model(SPEC)[0]
    return {k: [float(v) for v in cam.project([kps[k]])[0]] for k in ids}


def test_list_and_open_a_photo_without_camera(env):
    client, p, _ = env
    r = client.get(f"/api/projects/{p.id}/cameras").json()
    assert r["photos"] == [{"photo": 1, "path": "input/photos/a.jpg", "status": "none", "passes": False,
                            "manual_points": 0}]
    v = client.get(f"/api/projects/{p.id}/cameras/1").json()
    assert v["size"] == [1600, 1067] and (p.root / v["image"]).is_file() and (p.root / v["sketch"]).is_file()
    assert v["sketch_labels"] and "edges" not in v                   # no camera yet: nothing projected
    assert v["labels"]["hall/0/t"].startswith("hall")


def test_hand_placed_points_solve_the_camera(env):
    client, p, cam = env
    pts, _ = kp.visible(SPEC, cam)
    chosen = kp.choose_labels(SPEC, pts, cam, max_n=6)
    body = {"points": _true_points(cam, chosen)}
    v = client.put(f"/api/projects/{p.id}/cameras/1", json=body).json()
    assert v["status"] == "manual", v.get("why")
    assert v["metrics"]["rms_px"] < 2.0
    assert not v["gate"], v["gate"]
    assert v["edges"] and v["corners"]
    doc = p.read_json("input/cameras.json")
    rec = doc["photos"][0]
    assert rec["manual"] and all(k["source"] == "manual" for k in rec["keypoints"])
    solved = kp.Camera.from_json(rec["camera"])
    assert np.linalg.norm(solved.centre - cam.centre) < 0.02 * np.linalg.norm(cam.centre)
    from landmark3d.smarttex import run as st_run
    assert st_run.schema_errors(doc) == []
    assert client.get(f"/api/projects/{p.id}/cameras").json()["photos"][0]["passes"] is True


def test_two_points_are_enough_with_building_lines(env):
    client, p, cam = env
    v = client.put(f"/api/projects/{p.id}/cameras/1",
                   json={"points": _true_points(cam, ["hall/0/b", "tower/1/t"])}).json()
    assert v["status"] == "manual", v.get("why")
    assert v["metrics"]["rms_px"] < 3.0


def test_a_misplaced_point_is_named(env):
    client, p, cam = env
    pts, _ = kp.visible(SPEC, cam)
    chosen = kp.choose_labels(SPEC, pts, cam, max_n=7)
    points = _true_points(cam, chosen)
    bad = chosen[0]
    points[bad] = [points[bad][0] + 80, points[bad][1] + 60]
    v = client.put(f"/api/projects/{p.id}/cameras/1", json={"points": points}).json()
    assert bad in v["metrics"]["off_points"]


def test_one_point_fails_with_a_reason_and_clearing_removes_the_points(env):
    client, p, cam = env
    v = client.put(f"/api/projects/{p.id}/cameras/1", json={"points": _true_points(cam, ["hall/0/b"])}).json()
    assert v["status"] == "failed" and "at least 2" in v["why"]
    v = client.put(f"/api/projects/{p.id}/cameras/1", json={"points": {}}).json()
    assert v["manual"] == {}


def test_bad_input_is_refused(env):
    client, p, _ = env
    assert client.put(f"/api/projects/{p.id}/cameras/1", json={"points": {"hall/0/b": [1]}}).status_code == 400
    assert client.get(f"/api/projects/{p.id}/cameras/9").status_code == 404


def test_ai_auto_place_refines_rough_points_and_solves(env):
    """A fake AI: rough points 2-3 % off, refined to the true corner in the zoomed crops."""
    from landmark3d.smarttex import run as st_run
    client, p, cam = env
    kps = kp.model(SPEC)[0]
    rng = np.random.default_rng(4)

    def ask(prj_, provider, photo, sketch, labels, name, stem):
        W_, H_ = photo.size
        out = {}
        for lab, kid in labels.items():
            u, v = cam.project([kps[kid]])[0] + rng.normal(0, 0.025 * cam.diag, 2)
            if 0 <= u < W_ and 0 <= v < H_:
                out[lab] = (u / W_, v / H_, 0.9)
        return out

    def refine(prj_, provider, img, coarse, name, stem):
        return {kid: tuple(cam.project([kps[kid]])[0] + rng.normal(0, 1.0, 2)) for kid in coarse}

    v = st_run.auto_place(p, 1, provider=object(), ask=ask, refine=refine)
    assert v["status"] == "manual" and not v["gate"], (v.get("why"), v.get("gate"))
    assert v["metrics"]["rms_px"] < 3.0
    rec = p.read_json("input/cameras.json")["photos"][0]
    assert rec["points_by"] == "ai" and rec["manual"] and rec["ai_rough"]


def test_ai_auto_place_drops_points_it_got_wrong(env):
    from landmark3d.smarttex import run as st_run
    client, p, cam = env
    kps = kp.model(SPEC)[0]

    def ask(prj_, provider, photo, sketch, labels, name, stem):
        W_, H_ = photo.size
        return {lab: tuple(cam.project([kps[kid]])[0] / [W_, H_]) + (0.9,) for lab, kid in labels.items()}

    def refine(prj_, provider, img, coarse, name, stem):
        out = {kid: tuple(cam.project([kps[kid]])[0]) for kid in coarse}
        bad = list(out)[0]
        out[bad] = (out[bad][0] + 90, out[bad][1] - 70)       # the AI clicked the wrong corner once
        return out

    v = st_run.auto_place(p, 1, provider=object(), ask=ask, refine=refine)
    assert v["status"] == "manual" and len(v["dropped"]) == 1
    assert v["metrics"]["rms_px"] < 2.0


def test_auto_endpoint_needs_an_ai(env, monkeypatch):
    client, p, _ = env
    from landmark3d.research import ai
    monkeypatch.setattr(ai, "get", lambda *a, **k: None)
    assert client.post(f"/api/projects/{p.id}/cameras/1/auto").status_code == 409
