"""Deterministic quality loop (plan M6). No AI, no network: the same build result always gives the same metrics.

Three parts:
- metrics(): reads a build result and its spec and returns metric values, with the metric version and the inputs they
  came from, so a result can be compared later.
- gate(): hard checks that block a result (budgets, texel density, accounting, unexplained low utilization).
- keep(): the keep/reject decision for a change. A higher overall score never hides a drop in a protected category
  (plan M6 task 7).
The photo-based metrics (plan IoU, silhouette IoU, edge distance, semantic recall) need renders and reference photos;
they plug into the same score dict under their own names when those inputs exist.
"""
from __future__ import annotations

import json
import hashlib

METRICS_VERSION = "1.0.0"
UTILIZATION_FLOOR = 0.6               # PRODUCTION_BASELINE 5.5: below this the result must say why
PROTECTED_TOLERANCE = 0.0             # a protected category may not fall at all


def metrics(build: dict, spec: dict) -> dict:
    budgets = spec.get("budgets", {})
    tris = int(build.get("triangles") or 0)
    tri_max = int(budgets.get("triangles_max") or 0) or None
    byt = int(build.get("bytes") or 0)
    byt_max = int(budgets.get("bytes_max") or 0) or None
    alloc = build.get("openings_allocation") or {}
    util = {pid: a.get("target_utilization") for pid, a in alloc.items()}
    reasons = {pid: a.get("low_utilization_reason") for pid, a in alloc.items()}
    texel = [w for w in build.get("warnings", []) if "texel" in w.lower()]
    inputs = {"spec_sha256": _digest(spec), "triangles": tris, "bytes": byt, "budgets": budgets}
    return {
        "metrics_version": METRICS_VERSION,
        "inputs": inputs,
        "triangle_ratio": round(tris / tri_max, 4) if tri_max else None,
        "byte_ratio": round(byt / byt_max, 4) if byt_max else None,
        "material_count": int(build.get("materials") or 0),
        "utilization": util,
        "low_utilization_reasons": reasons,
        "texel_warnings": len(texel),
        "accounting_ok": not any("accounting" in e for e in build.get("errors", [])),
    }


def gate(m: dict, spec: dict) -> list[str]:
    """Hard checks. An empty list means the result passes the deterministic gate."""
    problems = []
    budgets = spec.get("budgets", {})
    if m["triangle_ratio"] is not None and m["triangle_ratio"] >= 1:
        problems.append(f"triangles over the hard budget ({m['triangle_ratio']:.2f} of the limit)")
    if m["byte_ratio"] is not None and m["byte_ratio"] >= 1:
        problems.append(f"file size over the hard budget ({m['byte_ratio']:.2f} of the limit)")
    if m["material_count"] > int(budgets.get("materials_max", 10)):
        problems.append(f"material count {m['material_count']} over the limit")
    if m["texel_warnings"]:
        problems.append(f"{m['texel_warnings']} texel density warning(s)")
    if not m["accounting_ok"]:
        problems.append("accounting does not match the generated meshes")
    for pid, u in m["utilization"].items():
        if u is not None and u < UTILIZATION_FLOOR and not m["low_utilization_reasons"].get(pid):
            problems.append(f"{pid}: utilization {u:.2f} below {UTILIZATION_FLOOR} without a reason")
    return problems


def keep(before: dict, after: dict, protected: set[str]) -> tuple[bool, list[str]]:
    """Keep a change only if no protected category falls. Returns (keep, the categories that fell)."""
    regressions = []
    for name in sorted(protected):
        b, a = before.get(name), after.get(name)
        if b is None or a is None:
            regressions.append(f"{name}: missing score")
        elif a < b - PROTECTED_TOLERANCE:
            regressions.append(f"{name}: {b:.3f} -> {a:.3f}")
    return (not regressions), regressions


def score_digest(m: dict) -> str:
    """Stable digest of a metric set: repeated builds from the same inputs must give the same digest."""
    return hashlib.sha256(json.dumps(m, sort_keys=True).encode("utf-8")).hexdigest()


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()
