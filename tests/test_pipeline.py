import json
import os

import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    return tmp_path


def _square_footprint(prj, lat=10.0, lon=106.0, half=0.0002):
    ring = [[lon - half, lat - half], [lon + half, lat - half], [lon + half, lat + half], [lon - half, lat + half],
            [lon - half, lat - half]]
    prj.write_json("input/geo/footprint.geojson", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [ring]}}]})


def test_gate_lists_missing_then_clears(home):
    from landmark3d import pipeline
    from landmark3d.project import Project
    prj = Project.create("Test Hall", "Nowhere", lat=10.0, lon=106.0)
    items = {i["id"] for i in pipeline.missing_items(prj)}
    assert {"footprint", "main_wall_height_m", "roof_form", "photos"} <= items
    _square_footprint(prj)
    prj.write_json("input/sources.json", {"sources": [{"id": "SRC-U01", "kind": "photo", "path": "input/photos/a.jpg",
                                                      "origin": "user", "licence": "owned"}]})
    left = pipeline.submit_inputs(prj, {"main_wall_height_m": 9.5, "roof_form": "gable"})
    assert left == []


def test_spec_from_user_values(home):
    from landmark3d import pipeline
    from landmark3d.project import Project
    prj = Project.create("Test Hall", "Nowhere", lat=10.0, lon=106.0)
    _square_footprint(prj)
    pipeline.submit_inputs(prj, {"main_wall_height_m": 9.5, "roof_form": "gable"})
    sp = pipeline.build_spec(prj)
    main = sp["parts"][0]
    assert main["height"] == 9.5 and main["roof"]["type"] == "gable"
    assert 40 < sp["footprint"]["length_m"] < 50
    assert sp["estimated_triangles"] > 0


def test_approval_rewinds(home):
    from landmark3d import pipeline
    from landmark3d.project import Project
    prj = Project.create("Test Hall", "Nowhere", lat=10.0, lon=106.0)
    for p in ("setup", "doctor", "research", "sources_review", "completion", "spec"):
        prj.set_phase(p, "done")
    prj.set_phase("spec_review", "waiting_approval")
    r = pipeline.approve(prj, "spec_review", "changes_requested", "tower is on the south side")
    assert r["rewound_to"] == "spec"
    st = prj.state()
    assert st["phases"]["spec"]["status"] == "pending"
    assert st["approvals"][-1]["notes"] == "tower is on the south side"


def test_extract_json_and_validation():
    from landmark3d.research import ai
    from landmark3d import spec
    obj = ai.extract_json('text before ```json\n{"parts": [{"id": "main", "footprint": "main", "base_z": 0, "height": 8, "roof": {"type": "hip"}}]}\n``` after')
    assert spec.validate_massing(obj) == []
    bad = {"parts": [{"id": "x", "footprint": "main", "base_z": 0, "height": -1, "roof": {"type": "onion"}}]}
    assert spec.validate_massing(bad)


def test_name_matching_rejects_wrong_building():
    from landmark3d.research import sources
    assert sources.name_score("Chợ Bến Thành", ["Chợ Bến Thành"]) > 0.9
    assert sources.name_score("Chợ Bến Thành", ["Ben Thanh Market"]) >= 0.5          # same place, partial match
    assert sources.name_score("Chợ Bến Thành", ["Chợ Bến Thành"]) > sources.name_score("Chợ Bến Thành", ["Ga Bến Thành"])
    assert sources.name_score("Ben Thanh Market", ["Chợ Bến Thành", "Ben Thanh Market"]) > 0.9
    assert sources.name_score("Chợ Bến Thành", ["Toà Nhà Saigon Centre"]) == 0.0
    assert sources.name_score("Chợ Bến Thành", ["Honda Trading Viet Nam Co., Ltd."]) == 0.0
    lat, lon = 10.0, 106.0
    near = {"tags": {"building": "commercial", "name": "Saigon Centre"},
            "ring_lonlat": [[lon + 0.001, lat], [lon + 0.002, lat], [lon + 0.002, lat + 0.001], [lon + 0.001, lat + 0.001]],
            "osm_type": "way", "osm_id": 1}
    assert sources.pick_main_building([near], lat, lon, "Chợ Bến Thành") is None


def test_unconfirmed_footprint_blocks_gate(home):
    from landmark3d import pipeline
    from landmark3d.project import Project
    prj = Project.create("Some Market", "X", lat=10.0, lon=106.0)
    _square_footprint(prj)
    fc = prj.read_json("input/geo/footprint.geojson")
    fc["features"][0]["properties"].update(name="Other Tower", osm_type="way", osm_id=5, l3d_match_score=0.0)
    prj.write_json("input/geo/footprint.geojson", fc)
    ids = {i["id"] for i in pipeline.missing_items(prj)}
    assert "footprint_confirmed" in ids
    pipeline.submit_inputs(prj, {"footprint_confirmed": "no"})
    ids = {i["id"] for i in pipeline.missing_items(prj)}
    assert "footprint" in ids and "footprint_confirmed" not in ids


def test_concurrent_state_reads_and_writes(home):
    import threading
    from landmark3d.project import Project
    prj = Project.create("Race Hall", "X", lat=1.0, lon=2.0)
    errors = []

    def writer():
        for i in range(200):
            prj.set_phase("research", "running", f"step {i}")

    def reader():
        for _ in range(400):
            try:
                prj.state()
            except Exception as exc:          # pragma: no cover - the failure being tested
                errors.append(exc)
    ts = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors


def test_parse_colour():
    from landmark3d.pipeline import parse_colour
    assert parse_colour("#ff8000") == [255, 128, 0]
    assert parse_colour("dark red brick") == [120, 30, 25]
    assert parse_colour("??") is None


def test_api_create_and_get(home):
    from fastapi.testclient import TestClient
    from landmark3d.server import app
    c = TestClient(app)
    r = c.post("/api/projects", json={"name": "Api Hall", "location": "X", "lat": 1.0, "lon": 2.0})
    pid = r.json()["id"]
    d = c.get(f"/api/projects/{pid}").json()
    assert d["state"]["name"] == "Api Hall" and len(d["phases"]) == 11
    assert c.get("/").status_code == 200
    s = c.get("/api/settings").json()
    assert "claude" in s["providers"] and "openai" in s["providers"]
