"""Feed the observed facade grid into the build (plan M3, task 7), and the A2 choices that steer it.

For each part the compiled grammar gives a floor count and a bay count. They are written into the part's version 1
facade (`level_height` from the floor count, `bay_spacing` from the bay count), which the geometry already honours.

Only what the photos support is applied:
- the run's policy must be `observed` or `visible_only` (conflicts block, no evidence keeps the default);
- the run must be confident enough (grammar MIN_CONFIDENCE, applied by compile_run);
- the person chooses the photographed side at A2 (`side`, an outline edge index). Without a choice the longest side is
  used, and the record says so.
- the person can reassign a photo to another part (`photo_parts`), since the AI does not know which part a photo shows.
Every change is recorded in `part["observed_grid"]`; parts left alone say why.
"""
from __future__ import annotations

import math

from . import corrections as corr_mod
from . import geom
from . import observation_grammar as grammar

APPLIED_POLICIES = ("observed", "visible_only")


def sides(poly) -> list[dict]:
    """Outline edges with their index, length and outward facing (degrees, same convention as the facade settings)."""
    pts = [tuple(p) for p in poly]
    area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))
    out = []
    for i, ((x0, y0), (x1, y1)) in enumerate(zip(pts, pts[1:] + pts[:1])):
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length == 0:
            continue
        nx, ny = (dy, -dx) if area > 0 else (-dy, dx)            # outward normal for either winding
        out.append({"index": i, "length_m": round(length, 3),
                    "facing_deg": round(math.degrees(math.atan2(ny, nx)) % 360, 1)})
    return out


def _side_length(poly, index) -> float | None:
    for s in sides(poly):
        if s["index"] == index:
            return s["length_m"]
    return None


def _longest(poly) -> tuple[float, int | None]:
    best = max(sides(poly), key=lambda s: s["length_m"], default=None)
    return (best["length_m"], best["index"]) if best else (0.0, None)


def apply(parts: list, main: list, observations: list, correction_doc: dict) -> list[dict]:
    """Change the parts in place. Returns one report entry per facade part, applied or not, with the reason."""
    report = []
    for part in parts:
        fac = part.get("facade")
        pid = part["id"]
        if not fac or fac.get("opening_type") == "curtain_wall" or part.get("enabled", True) is False:
            continue
        poly = geom.footprint_polygon(part["footprint"], main)
        correction = corr_mod.for_part(correction_doc, pid)
        photo_parts = (correction_doc or {}).get("photo_parts", {})
        # copies, so a reassigned photo reads as this part's observation; the stored file is not changed
        obs_for_part = [dict(o, part_id=pid) for o in observations if _part_of(o, photo_parts) == pid]
        if "side" in correction and _side_length(poly, correction["side"]) is None:
            report.append({"part": pid, "applied": False, "reason": f"side {correction['side']} is not an outline edge"})
            continue
        if "side" in correction:
            length, side_index = _side_length(poly, correction["side"]), correction["side"]
        else:
            length, side_index = _longest(poly)
        try:
            run = grammar.compile_run(obs_for_part, pid, 1, length, correction=correction)
        except ValueError as exc:
            report.append({"part": pid, "applied": False, "reason": str(exc)[:200]})
            continue
        accepted = len({o.get("id") for o in obs_for_part if o.get("confidence", 0) >= grammar.MIN_CONFIDENCE})
        conflicts = list(run.get("conflicts") or [])
        if run["provenance"] != "user_corrected":
            # the same structural check the build reports: photos that disagree on floors or bays block the grid
            conflicts += grammar.structure_conflicts(obs_for_part, pid)
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
        before = part.get("observed_grid", {}).get("before") or {"level_height": fac.get("level_height"),
                                                                "bay_spacing": fac.get("bay_spacing")}
        fac["level_height"] = round(level_h, 3)
        fac["bay_spacing"] = round(bay_w, 3)
        part["observed_grid"] = {"floors": floors, "bays": bays, "side_index": side_index, "side_m": round(length, 3),
                                 "side_chosen_by": "person" if "side" in correction else "longest_side",
                                 "policy": policy, "provenance": run["provenance"], "confidence": run["confidence"],
                                 "evidence": run["evidence"], "before": before,
                                 "photos": sorted({_source(o) for o in obs_for_part})}
        report.append({"part": pid, "applied": True, "policy": policy, "floors": floors, "bays": bays,
                       "side_index": side_index, "provenance": run["provenance"]})
    return report


def reapply(spec: dict, observations: list, correction_doc: dict) -> list[dict]:
    """Undo the last grid on every part (back to the facade it had before) and apply again with the current choices."""
    for part in spec.get("parts", []):
        grid = part.pop("observed_grid", None)
        if grid and part.get("facade"):
            part["facade"].update(grid.get("before") or {})
    return apply(spec["parts"], [tuple(p) for p in spec["footprint"]["local"]], observations, correction_doc)


def _source(obs: dict) -> str:
    return str(obs.get("id", "")).split("/")[0]


def _part_of(obs: dict, photo_parts: dict) -> str:
    return photo_parts.get(_source(obs)) or obs.get("part_id")


def _why(policy: str, conflicts: list) -> str:
    if policy == "blocked":
        return "conflicting confident observations: " + "; ".join(conflicts)[:160]
    if policy == "v1_default":
        return "no accepted observation: the version 1 facade is kept"
    return policy
