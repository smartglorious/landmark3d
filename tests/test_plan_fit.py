"""Fit of the AI massing to the measured footprint (plan_fit)."""
import copy
import math

import pytest

from landmark3d import geom, plan_fit, spec as spec_mod

# an L-shaped building: a 40 x 12 wing along x and a 12 x 30 wing along y
MAIN = [(0, 0), (40, 0), (40, 12), (12, 12), (12, 30), (0, 30)]


def parts_true():
    return [
        {"id": "wing_x", "footprint": {"rect": {"cx": 20, "cy": 6, "w": 40, "d": 12, "rot_deg": 0}}, "base_z": 0,
         "height": 10},
        {"id": "wing_y", "footprint": {"rect": {"cx": 6, "cy": 21, "w": 12, "d": 18, "rot_deg": 0}}, "base_z": 0,
         "height": 10},
        {"id": "tower", "footprint": {"rect": {"cx": 30, "cy": 6, "w": 6, "d": 6, "rot_deg": 0}}, "base_z": 10,
         "height": 15},
    ]


def shifted(dx=4.0, dy=-3.0, turn=4.0, scale=0.92):
    ps = copy.deepcopy(parts_true())
    C = (sum(x for x, _ in MAIN) / len(MAIN), sum(y for _, y in MAIN) / len(MAIN))
    for p in ps:
        if p["base_z"] == 0:
            plan_fit._scale_about(p, C[0], C[1], scale)
        plan_fit._turn_about(p, C[0], C[1], turn)
        plan_fit._move(p, dx, dy)
    return ps


def test_a_shifted_turned_and_shrunk_model_is_fitted_back():
    r = plan_fit._Raster(MAIN)
    assert r.iou(parts_true()) > 0.97
    bad = shifted()
    assert r.iou(bad) < 0.75
    fitted, rep = plan_fit.fit(bad, MAIN)
    assert rep["iou_before"] < 0.75 and rep["iou_after"] > 0.93, rep
    assert rep["global"] is not None


def test_a_volume_on_top_keeps_its_size_and_moves_with_the_model():
    fitted, _ = plan_fit.fit(shifted(), MAIN)
    tower = next(p for p in fitted if p["id"] == "tower")["footprint"]["rect"]
    assert tower["w"] == pytest.approx(6.0) and tower["d"] == pytest.approx(6.0)
    assert math.dist((tower["cx"], tower["cy"]), (30, 6)) < 2.5      # back over the x wing


def test_one_misplaced_volume_is_moved_alone():
    ps = parts_true()
    plan_fit._move(ps[1], 0, 4.0)                                    # the y wing slid 4 m north
    fitted, rep = plan_fit.fit(ps, MAIN)
    assert rep["iou_after"] > 0.95
    wy = next(p for p in fitted if p["id"] == "wing_y")["footprint"]["rect"]
    assert wy["cy"] == pytest.approx(21.0, abs=1.0)


def test_a_good_model_is_left_alone():
    fitted, rep = plan_fit.fit(parts_true(), MAIN)
    assert rep["global"] is None and rep["moved"] == []
    assert fitted == parts_true()


def test_a_step_that_adds_a_structure_error_is_not_kept():
    calls = []

    def strict(ps):                                                  # pretend every change breaks the structure
        calls.append(1)
        return [] if ps == shifted() else ["error"]
    fitted, rep = plan_fit.fit(shifted(), MAIN, strict)
    assert fitted == shifted() and rep["global"] is None and rep["moved"] == []


def test_real_structure_check_stays_clean():
    fitted, rep = plan_fit.fit(shifted(), MAIN, lambda ps: spec_mod.structure_errors(ps, MAIN))
    assert spec_mod.structure_errors(fitted, MAIN) == []


def test_point_features_move_turn_and_scale_with_their_part():
    """A finial or mast placed by plan position stays on its part when plan_fit moves, turns or scales it."""
    from landmark3d import plan_fit
    part = {"id": "tower", "footprint": {"rect": {"cx": 0, "cy": 0, "w": 6, "d": 6, "rot_deg": 0}}, "base_z": 0,
            "height": 20, "features": [{"type": "finial", "x": 1.0, "y": 0.0, "z": 20}, {"type": "disc", "z": 5}]}
    plan_fit._move(part, 5, -2)
    assert part["features"][0]["x"] == 6.0 and part["features"][0]["y"] == -2.0
    plan_fit._turn_about(part, 5, -2, 90)
    assert part["features"][0]["x"] == pytest.approx(5.0) and part["features"][0]["y"] == pytest.approx(-1.0)
    plan_fit._scale_about(part, 5, -2, 2)
    assert part["features"][0]["y"] == pytest.approx(0.0)
    plan_fit._stretch(part, 1.5, 1.0)      # along the part's own x axis (turned 90 deg): the point is on its y axis
    rect = part["footprint"]["rect"]
    assert "x" not in part["features"][1]
    assert abs(part["features"][0]["x"] - rect["cx"]) < 1e-6
