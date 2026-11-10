"""Deterministic quality loop (plan M6). Pure functions over build results: no AI, no Blender, no network."""
from landmark3d import quality

SPEC = {"budgets": {"triangles_max": 10000, "bytes_max": 500000, "materials_max": 6}}


def _build(**over):
    b = {"triangles": 4000, "bytes": 100000, "materials": 3, "warnings": [], "errors": [],
         "openings_allocation": {"hall": {"target_utilization": 0.7, "low_utilization_reason": None}}}
    b.update(over)
    return b


def test_metrics_are_deterministic_and_carry_their_version_and_inputs():
    a = quality.metrics(_build(), SPEC)
    b = quality.metrics(_build(), SPEC)
    assert quality.score_digest(a) == quality.score_digest(b)
    assert a["metrics_version"] == quality.METRICS_VERSION
    assert a["inputs"]["triangles"] == 4000 and a["inputs"]["spec_sha256"]


def test_a_clean_build_passes_the_gate():
    assert quality.gate(quality.metrics(_build(), SPEC), SPEC) == []


def test_hard_budgets_block_the_result():
    m = quality.metrics(_build(triangles=12000, bytes=600000, materials=8), SPEC)
    problems = quality.gate(m, SPEC)
    assert any("triangles" in p for p in problems)
    assert any("file size" in p for p in problems)
    assert any("material count" in p for p in problems)


def test_low_utilization_passes_only_with_a_stated_reason():
    low = _build(openings_allocation={"hall": {"target_utilization": 0.41, "low_utilization_reason": None}})
    assert any("below 0.6 without a reason" in p for p in quality.gate(quality.metrics(low, SPEC), SPEC))
    explained = _build(openings_allocation={"hall": {"target_utilization": 0.41,
                                                      "low_utilization_reason": "no_supported_candidates"}})
    assert quality.gate(quality.metrics(explained, SPEC), SPEC) == []


def test_texel_warnings_and_accounting_errors_block():
    m = quality.metrics(_build(warnings=["part hall: texel density 3x the target"],
                               errors=["accounting total 5 differs from the meshes (6)"]), SPEC)
    problems = quality.gate(m, SPEC)
    assert any("texel" in p for p in problems)
    assert any("accounting" in p for p in problems)


def test_a_protected_drop_blocks_even_when_the_overall_score_rises():
    before = {"plan_iou": 0.80, "structure": 0.90, "overall": 0.70}
    after = {"plan_iou": 0.79, "structure": 0.95, "overall": 0.90}
    keep, why = quality.keep(before, after, protected={"plan_iou"})
    assert keep is False and any("plan_iou" in w for w in why)
    keep, why = quality.keep(before, after, protected={"structure"})
    assert keep is True and why == []


def test_a_missing_protected_score_is_a_regression_not_a_pass():
    keep, why = quality.keep({"plan_iou": 0.8}, {}, protected={"plan_iou"})
    assert keep is False and "missing" in why[0]
