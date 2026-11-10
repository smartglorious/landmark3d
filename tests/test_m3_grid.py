"""Observed grids, one per photographed side (plan M3, task 7). Pure functions; no AI, no Blender."""
import copy

import pytest

from landmark3d import facade_grid

MAIN = [[0.0, 0.0], [20.0, 0.0], [20.0, 10.0], [0.0, 10.0]]      # CCW: side 0 = 20 m facing 270, side 1 = 10 m facing 0


def _part(pid="hall", height=12.0, **fac):
    facade = {"opening_type": "rect", "level_height": 3.5, "bay_spacing": 4.0, "cornice": 0.0}
    facade.update(fac)
    return {"id": pid, "footprint": "main", "base_z": 0.0, "height": height, "facade": facade}


def _obs(oid, part="hall", conf=0.9, floor_lines=(0, 0.25, 0.5, 0.75, 1.0), bay_lines=(0, 0.25, 0.5, 0.75, 1.0),
         etype="rect_window"):
    els = [{"id": f"E{f}{b}", "type": etype, "bbox": [0.1 * b, 0.1 * f, 0.1 * b + 0.05, 0.1 * f + 0.05],
            "floor": f, "bay": b, "confidence": conf} for f in range(len(floor_lines) - 1) for b in range(len(bay_lines) - 1)]
    return {"id": oid, "part_id": part, "confidence": conf, "floor_lines": list(floor_lines),
            "bay_lines": list(bay_lines), "elements": els}


def _entry(parts, side):
    return next(s for s in parts[0]["observed_grid"]["sides"] if s["side"] == side)


def test_sides_report_length_and_outward_facing():
    ss = facade_grid.sides(MAIN)
    assert [s["length_m"] for s in ss] == [20.0, 10.0, 20.0, 10.0]
    assert ss[0]["facing_deg"] == pytest.approx(270.0)           # south edge
    assert ss[1]["facing_deg"] == pytest.approx(0.0)             # east edge


def test_one_photo_sets_that_walls_grid_and_leaves_the_base_facade_alone():
    parts = [_part()]
    rep = facade_grid.apply(parts, MAIN, [_obs("OBS-1")], {"parts": {}})
    assert rep[0]["applied"] is True
    sides = parts[0]["facade"]["sides"]
    assert len(sides) == 1 and sides[0]["facing_deg"] == pytest.approx(270.0)
    assert sides[0]["level_height"] == pytest.approx(3.0)        # 12 m over 4 floors
    assert sides[0]["bay_spacing"] == pytest.approx(5.0)         # 20 m over 4 bays
    assert parts[0]["facade"]["level_height"] == 3.5             # the base is not changed for walls nobody photographed
    e = _entry(parts, 0)
    assert e["floors"] == 4 and e["bays"] == 4 and e["side_chosen_by"] == "default"


def test_two_photographed_sides_get_two_grids_each_from_its_own_photo():
    parts = [_part()]
    obs = [_obs("SRC-A/OBS-1", conf=0.9, floor_lines=(0, 0.5, 1)),                           # 2 floors, front
           _obs("SRC-B/OBS-1", conf=0.9, floor_lines=(0, 0.2, 0.4, 0.6, 0.8, 1.0))]          # 5 floors, east
    corr = {"parts": {"hall": {"grids": [{"side": 0}, {"side": 1}]}},
            "photo_sides": {"SRC-A": 0, "SRC-B": 1}}
    rep = facade_grid.apply(parts, MAIN, obs, {"parts": corr["parts"], "photo_sides": corr["photo_sides"]})
    assert rep[0]["applied"] is True
    sides = {round(s["facing_deg"]): s for s in parts[0]["facade"]["sides"]}
    assert sides[270]["level_height"] == pytest.approx(6.0)      # 12 / 2
    assert sides[0]["level_height"] == pytest.approx(12 / 5)     # 12 / 5
    assert _entry(parts, 0)["photos"] == ["SRC-A"] and _entry(parts, 1)["photos"] == ["SRC-B"]


def test_a_photo_with_no_side_choice_goes_to_the_default_side():
    parts = [_part()]
    corr = {"parts": {"hall": {"side": 1}}}                       # the person chose the east side for the part
    facade_grid.apply(parts, MAIN, [_obs("SRC-A/OBS-1")], corr)
    assert _entry(parts, 1)["applied"] is True and _entry(parts, 1)["side_chosen_by"] == "person"
    assert parts[0]["facade"]["sides"][0]["bay_spacing"] == pytest.approx(10 / 4)


def test_no_photo_and_no_choice_leaves_the_facade_alone():
    parts = [_part()]
    before = copy.deepcopy(parts[0]["facade"])
    rep = facade_grid.apply(parts, MAIN, [], {"parts": {}})
    assert rep[0]["applied"] is False and rep[0]["sides"] == []
    assert parts[0]["facade"] == before
    assert "observed_grid" not in parts[0]


def test_conflicting_photos_on_one_side_block_only_that_side():
    parts = [_part()]
    obs = [_obs("SRC-A/OBS-1", conf=0.9, etype="rect_window"), _obs("SRC-B/OBS-1", conf=0.9, etype="arched_window"),
           _obs("SRC-C/OBS-1", conf=0.9, floor_lines=(0, 0.5, 1), etype="rect_window")]
    corr = {"parts": {}, "photo_sides": {"SRC-A": 0, "SRC-B": 0, "SRC-C": 1}}
    rep = facade_grid.apply(parts, MAIN, obs, corr)
    assert _entry(parts, 0)["applied"] is False
    assert "conflicting" in _entry(parts, 0)["reason"]
    assert _entry(parts, 1)["applied"] is True                   # the other side is not blocked by it


def test_a_person_correction_applies_without_any_ai_observation():
    parts = [_part()]
    corr = {"parts": {"hall": {"floor_lines": [0, 0.5, 1], "bay_lines": [0, 0.2, 0.4, 0.6, 0.8, 1]}}}
    rep = facade_grid.apply(parts, MAIN, [], corr)
    assert rep[0]["applied"] is True
    e = _entry(parts, 0)
    assert e["provenance"] == "user_corrected" and e["policy"] == "observed"
    assert parts[0]["facade"]["sides"][0]["level_height"] == pytest.approx(6.0)
    assert parts[0]["facade"]["sides"][0]["bay_spacing"] == pytest.approx(4.0)


def test_a_side_that_is_not_an_edge_is_refused():
    parts = [_part()]
    rep = facade_grid.apply(parts, MAIN, [_obs("OBS-1")], {"parts": {"hall": {"side": 9}}})
    assert rep[0]["applied"] is False
    assert any("not an outline edge" in s["reason"] for s in rep[0]["sides"])


def test_a_photo_moved_to_another_part_feeds_that_part():
    parts = [_part("hall"), _part("tower")]
    obs = [_obs("SRC-1/OBS-1", part="hall")]
    corr = {"parts": {}, "photo_parts": {"SRC-1": "tower"}}
    rep = {r["part"]: r for r in facade_grid.apply(parts, MAIN, obs, corr)}
    assert rep["tower"]["applied"] is True
    assert rep["hall"]["applied"] is False


def test_curtain_wall_and_disabled_parts_are_not_touched():
    curtain = _part("glass", opening_type="curtain_wall")
    off = _part("off")
    off["enabled"] = False
    parts = [curtain, off]
    rep = facade_grid.apply(parts, MAIN, [_obs("OBS-1")], {"parts": {}})
    assert rep == []
    assert "observed_grid" not in curtain and "observed_grid" not in off


def test_the_cornice_is_taken_off_the_height_before_the_floor_count():
    parts = [_part(height=12.0, cornice=1.0)]
    facade_grid.apply(parts, MAIN, [_obs("OBS-1")], {"parts": {}})
    assert parts[0]["facade"]["sides"][0]["level_height"] == pytest.approx(11.0 / 4)


def test_reapply_replaces_the_old_grids_instead_of_adding_to_them():
    spec = {"footprint": {"local": MAIN}, "parts": [_part()]}
    obs = [_obs("OBS-1")]
    facade_grid.reapply(spec, obs, {"parts": {}})
    assert len(spec["parts"][0]["facade"]["sides"]) == 1
    facade_grid.reapply(spec, obs, {"parts": {"hall": {"side": 1}}})
    sides = spec["parts"][0]["facade"]["sides"]
    assert len(sides) == 1 and sides[0]["facing_deg"] == pytest.approx(0.0)
    assert spec["parts"][0]["facade"]["level_height"] == 3.5     # restored from the snapshot


def test_reapply_after_the_observations_are_gone_removes_the_grids():
    spec = {"footprint": {"local": MAIN}, "parts": [_part()]}
    facade_grid.reapply(spec, [_obs("OBS-1")], {"parts": {}})
    facade_grid.reapply(spec, [], {"parts": {}})
    assert "sides" not in spec["parts"][0]["facade"] and "observed_grid" not in spec["parts"][0]
