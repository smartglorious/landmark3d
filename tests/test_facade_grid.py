"""Facade logic: one bay grid per facade; pilasters on bay lines, windows in bay centres, entrance central."""
import math

from landmark3d import geom

# the wing facade the AI wrote for Bưu điện (ground floor rhythm 3.6 m, upper 2.8 m, pilasters every 2.8 m):
# with independent rhythms the ground-floor windows landed on the pilasters
WING = {"opening_type": "arch", "level_height": 6.0, "bay_spacing": 2.8, "opening_width": 1.6,
        "opening_height": 3.4, "floors": [
            {"from": 0, "to": 0, "opening_type": "arch", "bay_spacing": 3.6, "opening_width": 1.8,
             "opening_height": 3.4, "pediment": True},
            {"from": 1, "to": 1, "opening_type": "arch", "bay_spacing": 2.8, "opening_width": 1.6,
             "opening_height": 3.6}],
        "pilasters": {"every": 1, "width": 0.45, "depth": 0.25}, "cornice": 0.8, "bands": True}


def wing_poly():
    return geom.rect_polygon(0, 0, 22.75, 15.5, 137)


def test_runs_split_at_corners_and_keep_curves_whole():
    assert len(geom.facade_runs(wing_poly())) == 4
    stadium = geom.footprint_polygon({"stadium": {"cx": 0, "cy": 0, "w": 60, "d": 20}}, [])
    runs = geom.facade_runs(stadium)
    assert len(runs) == 1 and runs[0]["closed"]


def test_wing_layout_is_consistent():
    lay = geom.facade_layout(wing_poly(), 0, 13, WING)
    assert geom.check_facade(lay) == []
    # every window sits in a bay centre, clear of the pilasters on both sides
    for w in lay["windows"]:
        b = lay["runs"][w["run"]]["b"]
        assert abs((w["s"] / b) % 1 - 0.5) < 1e-6
        assert w["frame_half"] <= (b - 0.45) / 2
    # both floors share the grid: the same bay centres on floor 0 and floor 1 of each facade
    for r in range(4):
        f0 = sorted(round(w["s"], 3) for w in lay["windows"] if w["run"] == r and w["lv"] == 0)
        f1 = sorted(round(w["s"], 3) for w in lay["windows"] if w["run"] == r and w["lv"] == 1)
        assert f0 == f1 and f0
    # nothing crosses the cornice or the next string course
    for w in lay["windows"]:
        assert w["z0"] + w["h"] + (0.55 if w["pediment"] else 0) <= (w["lv"] + 1) * 6.0 - 0.3 + 1e-6
        assert w["z0"] + w["h"] <= 13 - 0.8


def test_symmetry_and_entrance_in_the_middle():
    fac = dict(WING, entrance={"width": 5, "height": 8, "facing_deg": 270}, floors=[])
    box = geom.rect_polygon(0, 0, 31, 12, 0)                  # south facade 31 m long
    lay = geom.facade_layout(box, 0, 13, fac)
    e = lay["entrance"]
    run_L = lay["runs"][e["run"]]["L"]
    assert abs(e["s"] - run_L / 2) < 1e-6                      # centred on its facade
    assert geom.check_facade(lay) == []
    south = [w["s"] for w in lay["windows"] if w["run"] == e["run"] and w["lv"] == 1]
    assert sorted(round(run_L - s, 3) for s in south) == sorted(round(s, 3) for s in south)   # mirror-symmetric
    ground = [w for w in lay["windows"] if w["run"] == e["run"] and w["lv"] == 0]
    assert all(abs(w["s"] - e["s"]) >= e["w"] / 2 + w["frame_half"] for w in ground)


def test_too_wide_window_is_narrowed_and_reported():
    fac = dict(WING, opening_width=3.5, floors=[])
    lay = geom.facade_layout(wing_poly(), 0, 13, fac)
    assert any("narrowed" in m for m in lay["issues"]) and geom.check_facade(lay) == []


def test_checker_catches_a_window_on_a_pilaster():
    lay = geom.facade_layout(wing_poly(), 0, 13, WING)
    bad = dict(lay, windows=lay["windows"] + [dict(lay["windows"][0], s=lay["pilasters"][1]["s"])])
    assert any("overlaps a pilaster" in m for m in geom.check_facade(bad))


def test_auto_narrowed_windows_are_notes_not_errors():
    """A window wider than its bay is narrowed by the layout engine (the geometry is right): it must not make the
    AI facade pass fail - it is reported as a note."""
    from landmark3d import pipeline
    part = {"id": "wing", "footprint": {"rect": {"cx": 0, "cy": 0, "w": 22.75, "d": 15.5, "rot_deg": 137}},
            "base_z": 0, "height": 13, "roof": {"type": "hip"}, "facade": dict(WING, opening_width=3.5, floors=[])}
    notes = []
    issues = pipeline.facade_issues({"parts": [part]}, [(-20, -20), (20, -20), (20, 20), (-20, 20)], notes)
    assert issues == []
    assert notes and notes[0].startswith("part wing:") and "narrowed" in notes[0]


def test_unused_materials_are_pruned():
    from landmark3d import pipeline
    m = {"parts": [{"id": "a", "material": {"wall": "stucco"}, "features": [{"type": "disc", "material": "clock"}]}],
         "materials": {"stucco": {}, "roof": {}, "trim": {}, "clock": {}, "dark_opening": {}, "opening": {}}}
    out = pipeline.prune_materials(m)
    assert set(out["materials"]) == {"stucco", "roof", "trim", "clock", "opening"}
    assert pipeline.material_errors(out) == []


def test_thin_parts_get_valid_facades():
    from landmark3d import pipeline, spec as spec_mod
    deck = {"id": "deck", "footprint": "main", "base_z": 0, "height": 0.6, "roof": {"type": "flat"},
            "facade": {"level_height": 0.6, "opening_type": "rect"}}
    out = pipeline.sanitize_facades({"parts": [deck]})
    f = out["parts"][0]["facade"]
    assert f["level_height"] == pipeline.MIN_LEVEL_M and f["openings"] is False
    assert spec_mod.validate_massing({"parts": out["parts"], "materials": {"wall": {"srgb": [1, 2, 3]}}}) == []
    assert deck["facade"]["level_height"] == 0.6                    # input untouched
