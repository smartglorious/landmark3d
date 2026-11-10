"""Observed grid fed into the build (plan M3, task 7). Pure functions; no AI, no Blender."""
import copy

import pytest

from landmark3d import facade_grid

MAIN = [[0.0, 0.0], [20.0, 0.0], [20.0, 10.0], [0.0, 10.0]]      # longest side 20 m


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


def test_one_confident_photo_sets_floor_height_and_bay_width_on_the_longest_side():
    parts = [_part()]
    rep = facade_grid.apply(parts, MAIN, [_obs("OBS-1")], {"parts": {}})
    assert rep[0]["applied"] is True and rep[0]["policy"] == "visible_only"
    fac = parts[0]["facade"]
    assert fac["level_height"] == pytest.approx(3.0)              # 12 m over 4 floors
    assert fac["bay_spacing"] == pytest.approx(5.0)               # 20 m over 4 bays
    grid = parts[0]["observed_grid"]
    assert grid["floors"] == 4 and grid["bays"] == 4
    assert grid["before"] == {"level_height": 3.5, "bay_spacing": 4.0}
    assert grid["side_chosen_by"] == "longest_side"


def test_no_observation_leaves_the_facade_alone_and_says_why():
    parts = [_part()]
    before = copy.deepcopy(parts)
    rep = facade_grid.apply(parts, MAIN, [], {"parts": {}})
    assert rep[0]["applied"] is False
    assert parts[0]["facade"] == before[0]["facade"]
    assert "observed_grid" not in parts[0]


def test_conflicting_confident_photos_block_the_grid():
    parts = [_part()]
    obs = [_obs("OBS-A", conf=0.9, etype="rect_window"), _obs("OBS-B", conf=0.9, etype="arched_window")]
    rep = facade_grid.apply(parts, MAIN, obs, {"parts": {}})
    assert rep[0]["applied"] is False
    assert "conflicting" in rep[0]["reason"]
    assert parts[0]["facade"]["level_height"] == 3.5             # untouched


def test_a_person_correction_applies_without_any_ai_observation():
    parts = [_part()]
    corr = {"parts": {"hall": {"floor_lines": [0, 0.5, 1], "bay_lines": [0, 0.2, 0.4, 0.6, 0.8, 1]}}}
    rep = facade_grid.apply(parts, MAIN, [], corr)
    assert rep[0]["applied"] is True and rep[0]["provenance"] == "user_corrected"
    assert parts[0]["facade"]["level_height"] == pytest.approx(6.0)
    assert parts[0]["facade"]["bay_spacing"] == pytest.approx(4.0)


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
    assert parts[0]["facade"]["level_height"] == pytest.approx(11.0 / 4)


def test_sides_report_length_and_outward_facing():
    ss = facade_grid.sides(MAIN)
    assert [s["length_m"] for s in ss] == [20.0, 10.0, 20.0, 10.0]
    # MAIN runs counter-clockwise: the edge from (0,0) to (20,0) faces south (-y, 270 degrees)
    assert ss[0]["facing_deg"] == pytest.approx(270.0)
    assert ss[1]["facing_deg"] == pytest.approx(0.0)              # the east edge faces +x


def test_a_chosen_side_sets_the_bay_width_and_is_recorded_as_the_person_s_choice():
    parts = [_part()]
    corr = {"parts": {"hall": {"side": 1}}}                      # the 10 m east side
    rep = facade_grid.apply(parts, MAIN, [_obs("OBS-1")], corr)
    assert rep[0]["applied"] is True
    assert parts[0]["facade"]["bay_spacing"] == pytest.approx(10.0 / 4)
    assert parts[0]["observed_grid"]["side_chosen_by"] == "person"
    assert parts[0]["observed_grid"]["side_index"] == 1


def test_a_side_that_is_not_an_edge_is_refused():
    parts = [_part()]
    rep = facade_grid.apply(parts, MAIN, [_obs("OBS-1")], {"parts": {"hall": {"side": 9}}})
    assert rep[0]["applied"] is False and "not an outline edge" in rep[0]["reason"]


def test_a_photo_reassigned_to_another_part_feeds_that_part():
    parts = [_part("hall"), _part("tower")]
    obs = [_obs("SRC-1/OBS-1", part="hall")]                    # the AI attached the photo to the hall
    corr = {"parts": {}, "photo_parts": {"SRC-1": "tower"}}
    rep = {r["part"]: r for r in facade_grid.apply(parts, MAIN, obs, corr)}
    assert rep["tower"]["applied"] is True
    assert rep["hall"]["applied"] is False and "no usable observation" in rep["hall"]["reason"]


def test_reapply_undoes_the_old_grid_before_applying_the_new_choice():
    spec = {"footprint": {"local": MAIN}, "parts": [_part()]}
    facade_grid.apply(spec["parts"], MAIN, [_obs("OBS-1")], {"parts": {}})
    assert spec["parts"][0]["facade"]["bay_spacing"] == pytest.approx(5.0)
    facade_grid.reapply(spec, [_obs("OBS-1")], {"parts": {"hall": {"side": 1}}})
    assert spec["parts"][0]["facade"]["bay_spacing"] == pytest.approx(2.5)
    assert spec["parts"][0]["observed_grid"]["before"] == {"level_height": 3.5, "bay_spacing": 4.0}
