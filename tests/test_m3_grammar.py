"""M3: facade grammar compiled from observations, the fallback policy, and spec version 2 validity.

Offline and synthetic: the observations describe an invented symmetric facade (three floors, six bays), not a landmark.
"""
import copy

import pytest

from landmark3d import observation_grammar as og
from landmark3d import observations as obs_mod
from landmark3d import spec_v2

BAY_LINES = [i / 6 for i in range(7)]
FLOOR_LINES = [0.9, 0.6, 0.3, 0.05]
SOURCES = {"SRC-1": {"kind": "photo", "use": "reference_only"}}


def elem(eid, floor, bay, typ="arched_window", conf=0.9, bbox=None):
    x0 = bay / 6 + 0.05
    return {"id": eid, "type": typ, "bbox": bbox or [x0, FLOOR_LINES[floor + 1] + 0.05, x0 + 0.1, FLOOR_LINES[floor] - 0.05],
            "floor": floor, "bay": bay, "confidence": conf, "provenance": "observed"}


def fixture(conf=0.9, obs_id="FO-1", extra=()):
    elements = [elem(f"E{f}{b}", f, b) for f in (1, 2) for b in range(6)]
    elements += [elem("E00", 0, 0, typ="storefront")] + [elem(f"G{b}", 0, b, typ="storefront") for b in range(1, 6)]
    elements += [elem("EP", 0, 2, typ="entrance_portal", bbox=[0.30, 0.6, 0.45, 0.85])]
    elements += list(extra)
    return {"id": obs_id, "source_id": "SRC-1", "part_id": "main", "quad": [[0, 0], [1, 0], [1, 1], [0, 1]],
            "floor_lines": FLOOR_LINES, "bay_lines": BAY_LINES, "elements": elements,
            "confidence": conf, "review": {"status": "accepted", "notes": []}}


def test_a_symmetric_facade_compiles_to_a_grammar_with_mirror_symmetry():
    run = og.compile_run([fixture()], "main", run_no=0, facade_length_m=36.0)
    g = run["grammar"]
    assert g["bay_count"] == 6 and g["bay_width_m"] == 6.0 and g["symmetry"] == "mirror"
    assert {"kind": "ground", "from": 0, "to": 0, "module": "storefront"} in g["floor_zones"]
    assert any(z["kind"] == "repeat" and z["from"] == 1 and z["to"] == 2 for z in g["floor_zones"])
    assert run["evidence"] == ["FO-1"] and run["provenance"] == "observed"


def test_the_entrance_is_an_override_with_its_measured_width():
    run = og.compile_run([fixture()], "main", run_no=0, facade_length_m=36.0)
    assert len(run["overrides"]) == 1
    o = run["overrides"][0]
    assert (o["floor"], o["bay"], o["module"]) == (0, 2, "entrance_portal")
    assert o["params"]["width_m"] == pytest.approx(0.15 * 36.0, abs=0.01)


def test_an_asymmetric_facade_is_not_called_mirrored():
    elements = [elem(f"A{b}", 1, b) for b in range(6)] + [elem(f"B{b}", 2, b) for b in range(6) if b < 2]
    ob = fixture()
    ob["elements"] = elements
    run = og.compile_run([ob], "main", run_no=0, facade_length_m=36.0)
    assert run["grammar"]["symmetry"] == "none"


def test_only_confident_observations_are_used():
    weak = fixture(conf=0.5, obs_id="FO-WEAK")
    with pytest.raises(ValueError, match="no usable observation"):
        og.compile_run([weak], "main", 0, 36.0)
    run = og.compile_run([weak, fixture(conf=0.9, obs_id="FO-OK")], "main", 0, 36.0)
    assert run["evidence"] == ["FO-OK"]


def test_rejected_observations_are_never_used():
    ob = fixture()
    ob["review"]["status"] = "rejected"
    with pytest.raises(ValueError):
        og.compile_run([ob], "main", 0, 36.0)


def test_another_parts_observations_are_ignored():
    ob = fixture()
    ob["part_id"] = "tower"
    with pytest.raises(ValueError):
        og.compile_run([ob], "main", 0, 36.0)


def test_compiling_is_deterministic_regardless_of_input_order():
    a, b = fixture(obs_id="FO-A", conf=0.9), fixture(obs_id="FO-B", conf=0.8)
    assert og.compile_run([a, b], "main", 0, 36.0) == og.compile_run([b, a], "main", 0, 36.0)


def test_hidden_facades_reuse_the_grammar_but_never_claim_observation():
    run = og.compile_run([fixture(conf=0.95)], "main", 0, 36.0)
    hidden = og.mark_hidden(run)
    assert hidden["provenance"] == "style_inferred"
    assert hidden["confidence"] <= og.HIDDEN_CONFIDENCE_CAP
    assert hidden["grammar"] == run["grammar"]
    assert run["provenance"] == "observed"                      # the source run is untouched


@pytest.mark.parametrize("photos, conflicts, conf, expected", [
    (0, [], None, "v1_default"),
    (0, [], 0.9, "v1_default"),
    (1, [], 0.9, "visible_only"),
    (3, [], 0.9, "observed"),
    (3, ["FO-1 and FO-2 disagree"], 0.9, "blocked"),
])
def test_fallback_policy(photos, conflicts, conf, expected):
    assert og.policy(photos, conflicts, conf) == expected


def test_a_compiled_facade_system_is_a_valid_spec_version_2_part():
    run = og.compile_run([fixture()], "main", 0, 36.0)
    hidden = og.mark_hidden(run)
    hidden["run"] = 1                                   # a second facade has its own run number
    fs = og.facade_system("classical", 0.8, [run, hidden])
    spec = {"spec_version": 2, "materials": {}, "budgets": {}, "parts": [{"id": "main", "facade_system": fs}]}
    assert spec_v2.validate(spec) == []
    same_number = copy.deepcopy(spec)
    same_number["parts"][0]["facade_system"]["runs"][1]["run"] = 0
    assert any("duplicate facade run" in e for e in spec_v2.validate(same_number))


def test_the_observation_schema_accepts_the_fixture():
    doc = {"version": 1, "observations": [fixture()]}
    sources = {"SRC-1": {"kind": "photo", "use": "reference_only"}}
    assert obs_mod.validate(doc, sources, {"main"}) == []
