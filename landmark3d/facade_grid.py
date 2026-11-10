"""Feed the observed facade grid into the build (plan M3, task 7).

For each part the compiled grammar gives a floor count and a bay count. They are written into the part's version 1
facade (`level_height` from the floor count, `bay_spacing` from the bay count), which the geometry already honours.

Only what the photos actually support is applied:
- the run's policy must be `observed` or `visible_only` (conflicts block, no evidence keeps the default);
- the run must be confident enough (grammar MIN_CONFIDENCE, applied by compile_run);
- the grid is applied to the part's longest outline side. That side is the assumption: a photo that shows another side
  would need its facade picked at A2, which is not built yet. The part is marked so the spec says so.
Every change is recorded in `part["observed_grid"]`, and parts left alone say why.
"""
from __future__ import annotations

from . import corrections as corr_mod
from . import geom
from . import observation_grammar as grammar

APPLIED_POLICIES = ("observed", "visible_only")


def _longest_side(poly) -> float:
    best = 0.0
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        best = max(best, ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5)
    return best


def apply(parts: list, main: list, observations: list, correction_doc: dict) -> list[dict]:
    """Change the parts in place. Returns one report entry per facade part, applied or not, with the reason."""
    report = []
    for part in parts:
        fac = part.get("facade")
        pid = part["id"]
        if not fac or fac.get("opening_type") == "curtain_wall" or part.get("enabled", True) is False:
            continue
        poly = geom.footprint_polygon(part["footprint"], main)
        length = _longest_side(poly)
        correction = corr_mod.for_part(correction_doc, pid)
        obs_for_part = [o for o in observations if o.get("part_id") == pid]
        try:
            run = grammar.compile_run(observations, pid, 1, length, correction=correction)
        except ValueError as exc:
            report.append({"part": pid, "applied": False, "reason": str(exc)[:200]})
            continue
        accepted = len({o.get("id") for o in obs_for_part if o.get("confidence", 0) >= grammar.MIN_CONFIDENCE})
        conflicts = run.get("conflicts") or []
        policy = grammar.policy(accepted, conflicts, run["confidence"])
        if run["provenance"] == "user_corrected":
            policy = "observed"                    # a person decided this part; the correction is evidence enough
        if policy not in APPLIED_POLICIES:
            report.append({"part": pid, "applied": False, "policy": policy, "reason": _why(policy, conflicts)})
            continue
        floors = max(z["to"] for z in run["grammar"]["floor_zones"]) + 1
        bays = run["grammar"]["bay_count"]
        cornice = float(fac.get("cornice", 0) or 0)
        level_h = max(2.0, (float(part["height"]) - cornice) / floors)
        bay_w = length / bays
        before = {"level_height": fac.get("level_height"), "bay_spacing": fac.get("bay_spacing")}
        fac["level_height"] = round(level_h, 3)
        fac["bay_spacing"] = round(bay_w, 3)
        part["observed_grid"] = {"floors": floors, "bays": bays, "side_m": round(length, 3), "policy": policy,
                                 "provenance": run["provenance"], "confidence": run["confidence"],
                                 "evidence": run["evidence"], "before": before,
                                 "assumption": "grid applied to the longest outline side"}
        report.append({"part": pid, "applied": True, "policy": policy, "floors": floors, "bays": bays,
                       "provenance": run["provenance"]})
    return report


def _why(policy: str, conflicts: list) -> str:
    if policy == "blocked":
        return "conflicting confident observations: " + "; ".join(conflicts)[:160]
    if policy == "v1_default":
        return "no accepted observation: the version 1 facade is kept"
    return policy
