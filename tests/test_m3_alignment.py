"""M3 exit gate: share of observed floor and bay lines the built grid reproduces (baseline 5.3). Synthetic data."""
from landmark3d import facade_grid


def _side(floors=3, bays=4, level_height=4.0, photos=("photo-1",)):
    return {"side": 0, "applied": True, "floors": floors, "bays": bays, "level_height": level_height,
            "photos": list(photos)}


def _obs(floor_lines, bay_lines, conf=0.9, oid="photo-1/FO-1"):
    return {"id": oid, "confidence": conf, "floor_lines": floor_lines, "bay_lines": bay_lines}


def _spec(side, height=12.0):
    return {"parts": [{"id": "hall", "height": height, "observed_grid": {"sides": [side]}}]}


def test_an_even_grid_matches_fully():
    # photo y from top 0.1 to bottom 0.7: four floor lines, three floors of equal height; five bay lines
    obs = _obs([0.1, 0.3, 0.5, 0.7], [0.2, 0.35, 0.5, 0.65, 0.8])
    r = facade_grid.alignment(_spec(_side()), [obs])
    assert r["floor_line_match"] == 1.0 and r["bay_line_match"] == 1.0


def test_a_tall_ground_floor_is_not_reproduced_by_an_even_grid():
    # the ground floor takes half the facade: the middle line sits at 0.5 of the height, not at 1/3
    obs = _obs([0.1, 0.25, 0.4, 0.7], [0.2, 0.35, 0.5, 0.65, 0.8])
    r = facade_grid.alignment(_spec(_side()), [obs])
    assert r["floor_line_match"] < 0.8 and r["bay_line_match"] == 1.0


def test_low_confidence_and_other_sides_photos_are_not_counted():
    weak = _obs([0.1, 0.3, 0.5, 0.7], [0.2, 0.8], conf=0.5)
    other = _obs([0.1, 0.3, 0.5, 0.7], [0.2, 0.8], oid="photo-9/FO-1")
    r = facade_grid.alignment(_spec(_side()), [weak, other])
    assert r["floor_line_match"] is None and r["bay_line_match"] is None and r["observations"] == []


def test_unapplied_sides_are_not_measured():
    side = _side()
    side["applied"] = False
    r = facade_grid.alignment(_spec(side), [_obs([0.1, 0.7], [0.2, 0.8])])
    assert r["floor_line_match"] is None


def test_built_lines_include_both_ends():
    f, b = facade_grid.built_lines(_side(floors=3, bays=2, level_height=4.0), 12.0)
    assert f[0] == 0.0 and f[-1] == 1.0 and b == [0.0, 0.5, 1.0]
