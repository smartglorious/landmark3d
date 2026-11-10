"""User corrections to facade observations (plan M3, tasks 6-7). No AI, no network."""
import pytest

from landmark3d import corrections
from landmark3d import observation_grammar as grammar


class _Store:
    """Stands in for Project's JSON helpers, so the tests need no project folder."""

    def __init__(self):
        self.files = {}

    def read_json(self, rel, default=None):
        return self.files.get(rel, default)

    def write_json(self, rel, data):
        self.files[rel] = data


def _obs(part="hall", conf=0.9, floor_lines=(0.0, 0.5, 1.0), bay_lines=(0.0, 0.25, 0.5, 0.75, 1.0)):
    els = [{"id": f"E{f}{b}", "type": "rect_window", "bbox": [0.1 * b, 0.1 * f, 0.1 * b + 0.05, 0.1 * f + 0.05],
            "floor": f, "bay": b, "confidence": conf} for f in range(len(floor_lines) - 1) for b in range(len(bay_lines) - 1)]
    return {"id": f"OBS-{part}-{conf}", "part_id": part, "confidence": conf, "floor_lines": list(floor_lines),
            "bay_lines": list(bay_lines), "elements": els}


def test_corrections_must_name_a_real_part_and_ascending_lines():
    parts = {"hall"}
    assert corrections.validate({"parts": {"hall": {"floor_lines": [0, 0.4, 1], "bay_lines": [0, 0.5, 1]}}}, parts) == []
    errs = corrections.validate({"parts": {"tower": {"bay_lines": [0, 1]}}}, parts)
    assert any("no such part" in e for e in errs)
    errs = corrections.validate({"parts": {"hall": {"floor_lines": [0, 0.6, 0.3]}}}, parts)
    assert any("ascending" in e for e in errs)
    errs = corrections.validate({"parts": {"hall": {"bay_lines": [0, 1.4]}}}, parts)
    assert any("[0, 1]" in e for e in errs)
    errs = corrections.validate({"parts": {"hall": {"floor_types": {"x": "rect_window"}}}}, parts)
    assert any("not a floor index" in e for e in errs)


def test_a_correction_without_observations_compiles_a_run_marked_user_corrected():
    corr = {"floor_lines": [0, 0.5, 1], "bay_lines": [0, 0.2, 0.4, 0.6, 0.8, 1]}
    run = grammar.compile_run([], "hall", 1, 30.0, correction=corr)
    assert run["provenance"] == "user_corrected"
    assert run["grammar"]["bay_count"] == 5
    assert run["confidence"] == 1.0
    assert "USER-CORRECTION" in run["evidence"]


def test_the_correction_wins_over_the_ai_lines_and_floor_types():
    obs = [_obs(conf=0.9)]
    plain = grammar.compile_run(obs, "hall", 1, 30.0)
    assert plain["provenance"] == "observed" and plain["grammar"]["bay_count"] == 4
    corr = {"bay_lines": [0, 0.5, 1], "floor_types": {"1": "arched_window"}}
    run = grammar.compile_run(obs, "hall", 1, 30.0, correction=corr)
    assert run["provenance"] == "user_corrected"
    assert run["grammar"]["bay_count"] == 2
    zones = {z["from"]: z["module"] for z in run["grammar"]["floor_zones"]}
    assert zones[1] == "arched_window"


def test_no_observation_and_no_correction_still_refuses():
    with pytest.raises(ValueError):
        grammar.compile_run([], "hall", 1, 30.0)


def test_save_keeps_only_valid_corrections_and_load_returns_them():
    prj = _Store()
    good = {"parts": {"hall": {"floor_lines": [0, 0.5, 1]}}}
    assert corrections.save(prj, good, {"hall"}) == []
    assert corrections.load(prj) == good
    bad = {"parts": {"hall": {"floor_lines": [0, 0.9, 0.2]}}}
    assert corrections.save(prj, bad, {"hall"}) != []
    assert corrections.load(prj) == good                 # a refused save does not overwrite the stored correction


def test_corrections_are_read_per_part_and_absent_parts_have_none():
    doc = {"parts": {"hall": {"floor_lines": [0, 1]}}}
    assert corrections.for_part(doc, "hall") == {"floor_lines": [0, 1]}
    assert corrections.for_part(doc, "tower") == {}
    assert corrections.load(_Store()) == {"parts": {}}
