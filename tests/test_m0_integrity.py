"""M0 correctness foundation (docs/DETAIL_ENGINE_V2_PLAN.md section 10, M0; docs/PRODUCTION_BASELINE.md section 4).

Each test maps to one M0 acceptance criterion. Offline only: no network, no AI, no Blender.
"""
import importlib
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from landmark3d import pipeline
from landmark3d.project import PHASES, Project
from landmark3d.research import sources


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    return tmp_path


@pytest.fixture()
def prj(home):
    p = Project.create("M0 Test", "Nowhere", lat=10.0, lon=106.0)
    return p


def approve_up_to(prj, last):
    """Mark every phase up to and including `last` as done/approved (what a real run produces)."""
    for ph in PHASES:
        prj.set_phase(ph["id"], "approved" if ph["approval"] else "done")
        if ph["id"] == last:
            break


def statuses(prj):
    return {k: v["status"] for k, v in prj.state()["phases"].items()}


@pytest.fixture()
def client(home):
    from landmark3d.server import app
    return TestClient(app)


# ============================================================================ 4.1 state machine
def test_cli_cannot_approve_a_phase_that_is_not_waiting(prj):
    from landmark3d import cli
    with pytest.raises(SystemExit) as e:
        cli.main(["approve", prj.id, "sources_review"])
    assert e.value.code == 2
    assert statuses(prj)["sources_review"] == "pending"


def test_pipeline_approve_refuses_a_phase_that_is_not_waiting(prj):
    with pytest.raises(pipeline.ApprovalError):
        pipeline.approve(prj, "sources_review", "approved")
    assert statuses(prj)["sources_review"] == "pending"
    assert prj.state()["approvals"] == []


def test_api_approve_refuses_unknown_phase_and_decision_with_422(client, prj):
    assert client.post(f"/api/projects/{prj.id}/approve", json={"phase": "nope", "decision": "approved"}).status_code == 422
    assert client.post(f"/api/projects/{prj.id}/approve", json={"phase": "spec_review", "decision": "maybe"}).status_code == 422
    assert client.post(f"/api/projects/{prj.id}/approve", json={"decision": "approved"}).status_code == 422


def test_concurrent_run_requests_start_exactly_one_worker(client, prj, monkeypatch):
    from landmark3d import server
    started = []

    def slow_run(p):
        started.append(time.time())
        time.sleep(0.3)
        return {"stopped_at": None}
    monkeypatch.setattr(pipeline, "run_until_blocked", slow_run)
    results = []
    barrier = threading.Barrier(6)

    def hit():
        barrier.wait()
        results.append(client.post(f"/api/projects/{prj.id}/run").json())
    threads = [threading.Thread(target=hit) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    time.sleep(0.5)
    assert sum(1 for r in results if r.get("started")) == 1
    assert len(started) == 1
    assert server._jobs == {} or all(not t.is_alive() for t in server._jobs.values())


def test_a_run_in_progress_blocks_a_second_runner_in_another_process(prj):
    """The per-project run lock is taken by the runner itself, so the CLI and the server cannot both work on a project."""
    with prj.run_lock():
        result = pipeline.run_until_blocked(prj)
    assert result == {"stopped_at": None, "reason": "busy"}
    assert statuses(prj)["setup"] == "pending"


def test_editing_the_spec_after_a3_invalidates_the_build_and_later_approvals(client, prj):
    approve_up_to(prj, "build_review")
    part = {"id": "hall", "footprint": "main", "base_z": 0, "height": 9, "roof": {"type": "flat"}}
    body = {"spec": {"parts": [part], "materials": {}, "footprint": {"local": [[0, 0], [10, 0], [10, 8], [0, 8]]},
                     "budgets": {}}}
    assert client.put(f"/api/projects/{prj.id}/spec", json=body).status_code == 200
    s = statuses(prj)
    assert s["spec_review"] == "pending"
    for later in ("build", "build_review", "release", "release_review"):
        assert s[later] == "pending", later
    assert prj.state()["phases"]["spec_review"]["message"]


def test_changing_budgets_invalidates_the_plan_approval_and_build(client, prj):
    approve_up_to(prj, "build_review")
    assert client.put(f"/api/projects/{prj.id}/budgets", json={"triangles_max": 60000, "triangles_target": 50000}).status_code == 200
    s = statuses(prj)
    assert s["spec_review"] == "pending" and s["build_review"] == "pending"


def test_revoking_texture_permission_invalidates_the_build(client, prj):
    approve_up_to(prj, "build_review")
    prj.write_json("input/sources.json", {"sources": [{"id": "SRC-1", "kind": "photo", "path": "input/photos/a.jpg",
                                                      "use": "reference_and_texture", "texture_for": "wall", "origin": "user"}]})
    # revoking in one request: the texture role is removed with the permission
    r = client.put(f"/api/projects/{prj.id}/sources/SRC-1", json={"use": "reference_only", "texture_for": ""})
    assert r.status_code == 200
    s = statuses(prj)
    assert s["build"] == "pending" and s["build_review"] == "pending" and s["release_review"] == "pending"
    assert s["spec_review"] == "approved"


def test_an_invalidated_build_is_not_shown_as_current(client, prj):
    approve_up_to(prj, "build_review")
    (prj.root / "output").mkdir(parents=True, exist_ok=True)
    (prj.root / "output" / "model.glb").write_bytes(b"glTF")
    assert client.get(f"/api/projects/{prj.id}").json()["has_glb"] is True
    pipeline.invalidate_from(prj, "spec_review", "spec edited")
    assert client.get(f"/api/projects/{prj.id}").json()["has_glb"] is False


def test_invalidation_keeps_the_history_of_approvals(prj):
    approve_up_to(prj, "build_review")
    before = len(prj.state()["approvals"])
    pipeline.invalidate_from(prj, "build", "texture changed")
    assert len(prj.state()["approvals"]) == before


# ============================================================================ 4.2 artifact identity
def test_build_manifest_hashes_every_effective_input(prj):
    prj.write_json("spec/landmark_spec.json", {"parts": [{"id": "a"}], "materials": {}, "budgets": {"triangles_max": 1}})
    prj.write_json("input/sources.json", {"sources": []})
    prj.write_json("input/textures.json", {"textures": [], "auto_for": "x"})
    ident = pipeline.build_identity(prj)
    for key in ("state", "spec", "sources", "textures", "component_library", "allocator", "blender_script"):
        assert key in ident and len(ident[key]) >= 8, key


def test_identity_changes_when_any_input_changes(prj):
    prj.write_json("spec/landmark_spec.json", {"parts": [], "materials": {}, "budgets": {}})
    prj.write_json("input/sources.json", {"sources": []})
    before = pipeline.build_identity(prj)
    prj.write_json("spec/landmark_spec.json", {"parts": [{"id": "x"}], "materials": {}, "budgets": {}})
    assert pipeline.build_identity(prj)["spec"] != before["spec"]


def test_release_refuses_a_stale_build_manifest(prj):
    prj.write_json("spec/landmark_spec.json", {"parts": [], "materials": {}, "budgets": {}})
    prj.write_json("input/sources.json", {"sources": []})
    pipeline.write_build_manifest(prj)
    prj.write_json("spec/landmark_spec.json", {"parts": [{"id": "edited"}], "materials": {}, "budgets": {}})
    with pytest.raises(pipeline.ReleaseBlocked, match="stale"):
        pipeline.check_release_identity(prj)


def test_release_accepts_a_matching_build_manifest(prj):
    prj.write_json("spec/landmark_spec.json", {"parts": [], "materials": {}, "budgets": {}})
    prj.write_json("input/sources.json", {"sources": []})
    pipeline.write_build_manifest(prj)
    pipeline.check_release_identity(prj)                    # no exception


def test_release_refuses_without_a_build_manifest(prj):
    with pytest.raises(pipeline.ReleaseBlocked, match="manifest"):
        pipeline.check_release_identity(prj)


# ============================================================================ 4.3 licence and provenance
def test_auto_texture_cache_key_changes_when_licence_or_use_changes(prj):
    from landmark3d import autotex
    spec = {"parts": [{"id": "a"}], "materials": {}}
    src = {"sources": [{"id": "S1", "kind": "photo", "path": "input/photos/a.jpg", "use": "reference_and_texture",
                        "licence": "cc_by"}]}
    prj.path("input", "photos", "a.jpg").write_bytes(b"jpg")
    prj.write_json("input/sources.json", src)
    k1 = autotex.texture_key(prj, spec)
    src["sources"][0]["use"] = "reference_only"
    prj.write_json("input/sources.json", src)
    k2 = autotex.texture_key(prj, spec)
    src["sources"][0].update(use="reference_and_texture", licence="cc0")
    prj.write_json("input/sources.json", src)
    k3 = autotex.texture_key(prj, spec)
    assert len({k1, k2, k3}) == 3


def test_auto_texture_cache_key_changes_when_the_photo_file_changes(prj):
    from landmark3d import autotex
    spec = {"parts": [{"id": "a"}], "materials": {}}
    prj.path("input", "photos", "a.jpg").write_bytes(b"one")
    prj.write_json("input/sources.json", {"sources": [{"id": "S1", "kind": "photo", "path": "input/photos/a.jpg",
                                                      "use": "reference_and_texture", "licence": "owned"}]})
    k1 = autotex.texture_key(prj, spec)
    prj.path("input", "photos", "a.jpg").write_bytes(b"two")
    assert autotex.texture_key(prj, spec) != k1


def test_revoked_photo_is_not_used_for_texture_jobs(prj):
    from landmark3d.engine import runner
    prj.path("input", "photos", "a.jpg").write_bytes(b"jpg")
    prj.write_json("input/sources.json", {"sources": [{"id": "S1", "kind": "photo", "path": "input/photos/a.jpg",
                                                      "use": "reference_only", "texture_for": "wall"}]})
    assert runner.texture_jobs(prj) == {}


# ============================================================================ 4.4 / task 6 OSM relations
def test_split_outer_ways_form_one_closed_footprint():
    # One building whose outer ring is split into three ways, given out of order and one reversed.
    w1 = [(0, 0), (4, 0), (4, 2)]
    w2 = [(4, 2), (4, 4), (0, 4)]
    w3 = [(0, 4), (0, 0)]
    ring = sources.assemble_outer_ring([w2, w3, w1])
    assert ring is not None
    assert ring[0] == ring[-1]
    assert sorted(set(ring[:-1])) == sorted({(0, 0), (4, 0), (4, 2), (4, 4), (0, 4)})


def test_relation_with_two_outers_keeps_the_whole_footprint_not_only_the_biggest_way(monkeypatch):
    nodes = {1: (0, 0), 2: (4, 0), 3: (4, 4), 4: (0, 4), 5: (10, 10), 6: (12, 10), 7: (12, 12)}
    ways = {100: [1, 2, 3], 101: [3, 4, 1], 200: [5, 6, 7]}
    data = {"elements": [
        {"type": "relation", "id": 9, "tags": {"building": "yes"},
         "members": [{"type": "way", "ref": 100, "role": "outer"}, {"type": "way", "ref": 101, "role": "outer"},
                     {"type": "way", "ref": 200, "role": "outer"}]},
        *[{"type": "way", "id": wid, "nodes": ns} for wid, ns in ways.items()],
        *[{"type": "node", "id": nid, "lon": x, "lat": y} for nid, (x, y) in nodes.items()],
    ]}
    monkeypatch.setattr(sources, "osm_element_full", lambda t, i: data)
    out = sources.footprint_from_osm_object("relation", 9)
    assert out is not None and out["ring_lonlat"][0] == out["ring_lonlat"][-1]
    assert (4, 4) in out["ring_lonlat"] and (0, 4) in out["ring_lonlat"]


def test_outer_ways_that_do_not_close_are_not_guessed():
    assert sources.assemble_outer_ring([[(0, 0), (4, 0)], [(9, 9), (8, 8)]]) is None


# ============================================================================ task 7 input validation
def test_create_project_rejects_a_partial_or_out_of_range_position(client):
    assert client.post("/api/projects", json={"name": "A", "lat": 10.0}).status_code == 422
    assert client.post("/api/projects", json={"name": "A", "lat": 91, "lon": 10}).status_code == 422
    assert client.post("/api/projects", json={"name": "A", "lat": 10, "lon": 181}).status_code == 422
    assert client.post("/api/projects", json={"name": "A", "lat": "north", "lon": 10}).status_code == 422
    assert client.post("/api/projects", json={"name": "A", "lat": 10.7, "lon": 106.7}).status_code == 200


def test_budgets_reject_invalid_values(client, prj):
    bad = [{"triangles_max": -1}, {"triangles_max": "many"}, {"compression": "zip"},
           {"triangles_target": 90000, "triangles_max": 50000}, {"bytes_max": 0}]
    for body in bad:
        assert client.put(f"/api/projects/{prj.id}/budgets", json=body).status_code == 422, body
    assert client.put(f"/api/projects/{prj.id}/budgets", json={"compression": "draco"}).status_code == 200


def test_photo_upload_rejects_unsupported_type_and_oversize(client, prj, monkeypatch):
    from landmark3d import server
    r = client.post(f"/api/projects/{prj.id}/photos", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 415
    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 10)
    r = client.post(f"/api/projects/{prj.id}/photos", files={"file": ("a.jpg", b"x" * 100, "image/jpeg")})
    assert r.status_code == 413
    assert not (prj.root / "input" / "photos").exists() or not list((prj.root / "input" / "photos").glob("user_*"))


def test_phase_request_bodies_are_validated(client, prj):
    assert client.post(f"/api/projects/{prj.id}/reopen", json={"phase": 5}).status_code == 422
    assert client.post(f"/api/projects/{prj.id}/restart", json={"notes": ["not", "text"]}).status_code == 422


def test_another_process_holding_the_run_lock_blocks_the_runner(prj):
    """The CLI and the web server are separate processes: the OS-level file lock must hold across them."""
    import subprocess
    import sys
    import os
    code = ("import sys, time\n"
            "from landmark3d.project import Project\n"
            f"p = Project({prj.id!r})\n"
            "with p.run_lock() as got:\n"
            "    print('held' if got else 'busy', flush=True)\n"
            "    time.sleep(3)\n")
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                             cwd=os.getcwd(), env=os.environ.copy())
    try:
        assert child.stdout.readline().strip() == "held"
        assert pipeline.run_until_blocked(prj) == {"stopped_at": None, "reason": "busy"}
        assert statuses(prj)["setup"] == "pending"
    finally:
        child.wait(timeout=30)
    with prj.run_lock(blocking=False) as got:                       # released when the other process ends
        assert got


# ============================================================================ 4.3 CC BY-NC is blocked unless the project is non-commercial
def test_cc_by_nc_photos_are_not_exported_by_default(prj):
    from landmark3d import textures
    from landmark3d.engine import runner
    from landmark3d.textures import licence_allows
    assert licence_allows("cc_by_nc") is False
    prj.path("input", "photos", "a.jpg").write_bytes(b"jpg")
    prj.write_json("input/sources.json", {"sources": [{"id": "S1", "kind": "photo", "path": "input/photos/a.jpg",
                                                      "use": "reference_and_texture", "licence": "cc_by_nc",
                                                      "texture_for": "wall"}]})
    assert runner.texture_jobs(prj) == {}
    prj.update_state(lambda s: s.update(non_commercial=True))
    assert "wall" in runner.texture_jobs(prj)


def test_cc_by_nc_cannot_be_set_for_texture_use_by_the_api(client, prj):
    prj.write_json("input/sources.json", {"sources": [{"id": "S1", "kind": "photo", "path": "input/photos/a.jpg",
                                                      "use": "reference_only", "licence": "cc_by_nc"}]})
    r = client.put(f"/api/projects/{prj.id}/sources/S1", json={"use": "reference_and_texture", "texture_for": "wall"})
    assert r.status_code == 422


def test_source_use_must_be_a_known_value(client, prj):
    prj.write_json("input/sources.json", {"sources": [{"id": "S1", "kind": "photo", "path": "x.jpg", "use": "reference_only",
                                                      "licence": "owned"}]})
    assert client.put(f"/api/projects/{prj.id}/sources/S1", json={"use": "everything"}).status_code == 422
    assert client.put(f"/api/projects/{prj.id}/sources/S1", json={"use": "reference_only"}).status_code == 200
