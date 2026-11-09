"""M2: opening assemblies LOD0-LOD2, clearance from neighbours, the allocator, and the standard fixture metrics.

Offline only. The facade fixtures are synthetic (no landmark names, no photos).
"""
import pytest

from landmark3d import geom
from landmark3d.detail import allocator, openings, registry
from landmark3d.detail.allocator import Choice
from landmark3d.detail.types import DetailCandidate


def win(w=1.6, h=2.4, kind="rect", z0=3.0, frame_half=None, run=0, s=0.0, lv=0):
    return {"p": (0.0, 0.0), "t": (1.0, 0.0), "o": (0.0, -1.0), "w": w, "h": h, "kind": kind, "z0": z0,
            "run": run, "s": s, "lv": lv,
            "frame_half": frame_half if frame_half is not None else w / 2 + 0.25}


SHAPES = [win(w=1.2, h=2.2), win(w=2.8, h=3.6), win(w=1.8, h=3.4, kind="arch"), win(w=4.0, h=6.0, kind="arch")]


@pytest.mark.parametrize("w", SHAPES)
@pytest.mark.parametrize("lod", [0, 1, 2])
def test_every_assembly_is_a_valid_component(w, lod):
    comp = openings.assemble(w, lod)
    assert comp.problems() == []
    assert comp.triangle_count > 0
    assert comp.material_slots <= {"opening", "trim", "glass"} and "opening" in comp.material_slots


@pytest.mark.parametrize("w", SHAPES)
def test_detail_increases_monotonically_with_lod(w):
    counts = [openings.assemble(w, lod).triangle_count for lod in (0, 1, 2)]
    assert counts[0] < counts[1] < counts[2]


@pytest.mark.parametrize("w", SHAPES)
def test_lod1_shows_a_real_recess(w):
    d = openings.dimensions(w, 1)
    assert 0.08 <= d["recess"] <= openings.RECESS_MAX
    depth = lambda comp: max((v[0] - w["p"][0]) * w["o"][0] + (v[1] - w["p"][1]) * w["o"][1] for v in comp.mesh.verts)
    assert depth(openings.assemble(w, 1)) >= d["recess"] - 1e-9    # the surround projects out of the wall
    assert depth(openings.assemble(w, 0)) <= 0.06                  # LOD0 stays a flat panel


@pytest.mark.parametrize("w", SHAPES)
def test_frame_is_clamped_to_the_clear_opening(w):
    d = openings.dimensions(w, 2)
    assert d["frame"] <= openings.FRAME_MAX and d["frame"] <= 0.2 * d["w"] + 1e-9


def test_the_surround_stays_inside_the_layout_clearance():
    # A window packed tightly between pilasters: the surround may not reach the pilaster line.
    tight = win(w=2.0, h=3.0, frame_half=1.0 + 0.05)
    comp = openings.assemble(tight, 2)
    x_max = max(abs(v[0]) for v in comp.mesh.verts)
    assert x_max <= tight["frame_half"] + 1e-6


def test_no_overlap_with_pilasters_on_a_real_layout():
    poly = geom.rect_polygon(0, 0, 30, 20, 0)
    fac = {"opening_type": "rect", "level_height": 4.0, "bay_spacing": 3.0, "opening_width": 1.5,
           "opening_height": 2.4, "pilasters": {"every": 1, "width": 0.7, "depth": 0.3}}
    lay = geom.facade_layout(poly, 0, 12, fac)
    assert geom.check_facade(lay) == []
    for w in lay["windows"]:
        comp = openings.assemble(w, 2)
        pieces = [p for p in lay["pilasters"] if p["run"] == w["run"]]
        assert comp.bounds is not None
        along = lambda v: (v[0] - w["p"][0]) * w["t"][0] + (v[1] - w["p"][1]) * w["t"][1]
        assert max(abs(along(v)) for v in comp.mesh.verts) <= w["frame_half"] + 1e-6


def test_every_declared_generator_exists_and_the_library_is_valid():
    assert registry.validate_registry() == []
    comps = {c["id"]: c for c in registry.load_library()["components"]}
    for cid in ("opening_assembly_rect", "opening_assembly_arch"):
        assert comps[cid]["status"] == "implemented"
        assert [lod["generator"] for lod in comps[cid]["lods"]] == list(openings.GENERATOR_FOR_LOD.values())


def test_the_allocator_prefers_cheap_useful_upgrades_and_respects_dependencies():
    a1 = Choice(DetailCandidate("a.lod0_to_lod1", ["a"], 0, 1, 100, utility=50.0), ())
    a2 = Choice(DetailCandidate("a.lod1_to_lod2", ["a"], 1, 2, 100, utility=500.0), ("a.lod0_to_lod1",))
    res = allocator.select([a2, a1], base_triangles=100, target_triangles=1000)
    assert res["selected"] == ["a.lod0_to_lod1", "a.lod1_to_lod2"]
    orphan = Choice(DetailCandidate("b.lod1_to_lod2", ["b"], 1, 2, 10, utility=9e9), ("b.lod0_to_lod1",))
    res = allocator.select([orphan], base_triangles=100, target_triangles=1000)
    assert res["selected"] == [] and res["rejected"][0]["reason"] == "dependency"


def test_the_allocator_stops_at_ninety_percent_and_reports_why():
    cands = [Choice(DetailCandidate(f"c{i}", [str(i)], 0, 1, 100, utility=1.0), ()) for i in range(20)]
    res = allocator.select(cands, base_triangles=0, target_triangles=1000)
    assert res["added_triangles"] <= 900 and res["target_utilization"] <= 0.9
    assert any(r["reason"] == "budget" for r in res["rejected"])
    empty = allocator.select([], base_triangles=100, target_triangles=1000)
    assert empty["low_utilization_reason"] == "no_supported_candidates"


def test_supported_upgrades_reach_the_sixty_percent_band():
    # Enough budget for every supported upgrade: the allocator must take them and reach the 60% band.
    windows = [win(w=1.6, h=2.4, s=float(i), lv=0) for i in range(30)]
    need = sum(openings.tri_cost(w, 2) - openings.tri_cost(w, 0) for w in windows)
    plan = openings.plan(windows, budget=int(need / 0.9) + 100)
    assert all(v == 2 for v in plan["lods"].values())
    assert plan["allocation"]["added_triangles"] == need
    assert plan["allocation"]["target_utilization"] >= 0.6
    assert plan["allocation"]["low_utilization_reason"] is None


def test_a_larger_budget_never_lowers_a_window():
    windows = [win(w=1.6 + 0.2 * i, h=2.4, s=float(i)) for i in range(6)]
    small = openings.plan(windows, budget=200)["lods"]
    large = openings.plan(windows, budget=50_000)["lods"]
    assert all(large[k] >= small[k] for k in small)


def test_the_builder_accounts_every_triangle_it_adds():
    windows = [win(w=1.6, h=2.4, s=float(i)) for i in range(4)]
    build, stats = openings.builder_for(windows, budget=10_000)
    m = geom.Mesh()
    added = sum(build(m, w) for w in windows)
    assert added == m.triangles() == sum(len(f) - 2 for f in m.faces)
    assert sum(stats["lod_counts"].values()) == len(windows)


def test_the_v1_facade_path_is_unchanged_without_a_builder():
    poly = geom.rect_polygon(0, 0, 30, 20, 0)
    fac = {"opening_type": "rect", "level_height": 4.0, "bay_spacing": 3.0, "opening_width": 1.5, "opening_height": 2.4}
    m1, m2 = geom.Mesh(), geom.Mesh()
    geom.facade_openings(poly, 0, 12, fac, m1, 10 ** 6)
    geom.facade_openings(poly, 0, 12, fac, m2, 10 ** 6, window_builder=None)
    assert m1.verts == m2.verts and m1.faces == m2.faces


def test_previews_used_by_the_judge_carry_the_engine_flag(tmp_path, monkeypatch):
    """The judge and the photo review look at preview renders. A preview must build the same engine as the model,
    otherwise a flag-on benchmark would score version 1 geometry."""
    import json
    import subprocess
    from pathlib import Path
    from landmark3d import doctor, settings
    from landmark3d.engine import runner
    from landmark3d.project import Project
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects"), "detail_engine_v2": True})
    prj = Project.create("Preview Flag", "X")
    seen = {}

    def fake_run(cmd, **k):
        job = json.loads(Path(cmd[-1]).read_text(encoding="utf-8"))
        seen["engine"] = job.get("engine")
        return subprocess.CompletedProcess(cmd, 1, "", "")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(doctor, "find_blender", lambda: "blender")
    runner.preview(prj, {"parts": [], "materials": {}, "budgets": {}, "footprint": {"local": []}}, {}, tmp_path / "out")
    assert seen["engine"] == {"detail_engine_v2": True, "library_version": "1.0.0", "bake_ao": False, "ao_size": 256}
