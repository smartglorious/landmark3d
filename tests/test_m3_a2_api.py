"""A2 facade step API: review, save corrections (side, lines, photo-to-part), and the AI run guard. No AI call."""
import importlib

import pytest
from fastapi.testclient import TestClient

MAIN = [[-10.0, -5.0], [10.0, -5.0], [10.0, 5.0], [-10.0, 5.0]]          # east and west sides are 10 m, north/south 20 m


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects"), "facade_observations": True})
    from landmark3d.project import Project
    from landmark3d import pipeline
    from landmark3d.server import app
    prj = Project.create("A2 Hall", "Nowhere", lat=10.0, lon=106.0)
    prj.write_json("spec/landmark_spec.json", {
        "name": "A2 Hall", "footprint": {"local": MAIN}, "photos": ["projects/x/photos/a.jpg"],
        "parts": [{"id": "hall", "footprint": "main", "base_z": 0, "height": 12, "roof": {"type": "flat"},
                   "facade": {"opening_type": "rect", "level_height": 3.5, "bay_spacing": 4.0, "cornice": 0}}],
        "materials": {}, "budgets": {}})
    prj.write_json("input/sources.json", {"sources": [{"id": "SRC-1", "kind": "photo", "path": "projects/x/photos/a.jpg"}]})
    monkeypatch.setattr(pipeline, "invalidate_from", lambda *a, **k: None)
    return prj, TestClient(app)


def test_review_lists_the_sides_and_which_part_each_photo_is_on(env):
    prj, client = env
    r = client.get(f"/api/projects/{prj.id}/observation_review")
    assert r.status_code == 200
    body = r.json()
    assert body["ai_enabled"] is True
    sides = body["parts"][0]["sides"]
    assert [s["length_m"] for s in sides] == [20.0, 10.0, 20.0, 10.0]
    assert body["photos"][0]["source_id"] == "SRC-1" and body["photos"][0]["part"] is None


def test_saving_a_side_and_a_photo_assignment_stores_them_and_redoes_the_grid(env):
    prj, client = env
    r = client.put(f"/api/projects/{prj.id}/observation_corrections",
                   json={"parts": {"hall": {"side": 1}}, "photo_parts": {"SRC-1": "hall"}})
    assert r.status_code == 200, r.text
    stored = prj.read_json("input/observation_corrections.json")
    assert stored["parts"]["hall"]["side"] == 1 and stored["photo_parts"]["SRC-1"] == "hall"
    assert r.json()["grid"][0]["applied"] is False                # no observations yet, so no grid, and it says why
    review = client.get(f"/api/projects/{prj.id}/observation_review").json()
    assert review["parts"][0]["correction"]["side"] == 1
    assert review["photos"][0]["part"] == "hall"


def test_an_invalid_correction_is_refused_and_nothing_is_stored(env):
    prj, client = env
    r = client.put(f"/api/projects/{prj.id}/observation_corrections",
                   json={"parts": {"hall": {"floor_lines": [0, 0.8, 0.3]}}})
    assert r.status_code == 400 and "ascending" in r.text
    assert prj.read_json("input/observation_corrections.json") is None


def test_a_photo_assigned_to_an_unknown_part_is_refused(env):
    prj, client = env
    r = client.put(f"/api/projects/{prj.id}/observation_corrections",
                   json={"parts": {}, "photo_parts": {"SRC-1": "tower"}})
    assert r.status_code == 400 and "no such part" in r.text


def test_the_ai_run_refuses_when_the_setting_is_off(env, monkeypatch):
    prj, client = env
    from landmark3d import settings
    settings.update({"facade_observations": False})
    r = client.post(f"/api/projects/{prj.id}/observations/run")
    assert r.status_code == 409 and "Settings" in r.json()["detail"]


def test_the_ai_run_refuses_when_no_provider_is_signed_in(env, monkeypatch):
    prj, client = env
    from landmark3d.research import ai
    monkeypatch.setattr(ai, "get", lambda task="research", name=None: None)
    r = client.post(f"/api/projects/{prj.id}/observations/run")
    assert r.status_code == 409 and "provider" in r.json()["detail"]
