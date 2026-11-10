"""Feed the observed facade grids into the build (plan M3, task 7), one grid per photographed side.

A part's outline has several sides, and each photographed side has its own floors and bays. For each observed side the
compiled grammar gives a floor count and a bay count. They are written as an entry in the part's facade `sides` list:
`facing_deg` picks the wall, and `level_height` / `bay_spacing` set its grid (geom honours both per wall). Walls that
no photo shows keep the version 1 facade, so nothing is invented for them.

Only what the photos support is applied:
- each side's policy must be `observed` or `visible_only` (conflicts block that side, no evidence keeps the default);
- the run must be confident enough (grammar MIN_CONFIDENCE, applied by compile_run);
- a photo is on the side the person chose (`photo_sides`); a photo with no choice goes to the default side (the side the
  person chose for the part, else the longest side), and the record says so;
- a photo can be moved to another part (`photo_parts`), since the AI does not know which part a photo shows.
Every change is recorded in `part["observed_grid"]` with the facade as it was before, so a recompute starts clean.
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


def apply(parts: list, main: list, observations: list, correction_doc: dict) -> list[dict]:
    """Change the parts in place. Returns one report entry per facade part: the sides, applied or not, and why."""
    report = []
    for part in parts:
        fac = part.get("facade")
        pid = part["id"]
        if not fac or fac.get("opening_type") == "curtain_wall" or part.get("enabled", True) is False:
            continue
        _restore(part)                                     # start from the facade the part had before any grid
        before = _snapshot(part)
        poly = geom.footprint_polygon(part["footprint"], main)
        by_index = {s["index"]: s for s in sides(poly)}
        doc = correction_doc or {}
        correction = corr_mod.for_part(doc, pid)
        photo_parts = doc.get("photo_parts", {})
        photo_sides = doc.get("photo_sides", {})
        # copies, so a reassigned photo reads as this part's observation; the stored file is not changed
        obs_part = [dict(o, part_id=pid) for o in observations if _part_of(o, photo_parts) == pid]
        longest = max(by_index.values(), key=lambda s: s["length_m"], default=None)
        person_side = correction.get("side")
        default_side = person_side if person_side is not None else (longest["index"] if longest else None)
        grids = {g["side"]: g for g in correction.get("grids", []) if "side" in g}
        groups: dict = {}
        for o in obs_part:
            groups.setdefault(photo_sides.get(_source(o), default_side), []).append(o)
        wanted = set(groups) | set(grids)
        if (correction.get("floor_lines") or correction.get("bay_lines")) and default_side is not None:
            wanted.add(default_side)                  # a whole-part correction is a grid for the default side
        entries = []
        for s in sorted(x for x in wanted if x is not None):
            if s not in by_index:
                entries.append({"side": s, "applied": False, "reason": f"side {s} is not an outline edge of {pid}"})
                continue
            override = grids.get(s) or (correction if s == default_side else {})
            chosen = "person" if (s in grids or s == person_side) else "default"
            entries.append(_side_grid(pid, by_index[s], groups.get(s, []), override, fac, part, chosen))
        applied = [e for e in entries if e.get("applied")]
        if applied:
            fac["sides"] = list(fac.get("sides") or []) + [
                {"facing_deg": e["facing_deg"], "level_height": e["level_height"], "bay_spacing": e["bay_spacing"]}
                for e in applied]
            part["observed_grid"] = {"before": before, "sides": entries}
        report.append({"part": pid, "applied": bool(applied), "sides": entries})
    return report


def reapply(spec: dict, observations: list, correction_doc: dict) -> list[dict]:
    """Recompute every part's grids from the current observations and choices."""
    return apply(spec["parts"], [tuple(p) for p in spec["footprint"]["local"]], observations, correction_doc)


def _side_grid(pid, side, obs_s, override, fac, part, chosen) -> dict:
    base = {"side": side["index"], "facing_deg": side["facing_deg"], "length_m": side["length_m"],
            "side_chosen_by": chosen, "photos": sorted({_source(o) for o in obs_s})}
    if not obs_s and not (override.get("floor_lines") and override.get("bay_lines")):
        return dict(base, applied=False, reason="no photo is on this side")
    try:
        run = grammar.compile_run(obs_s, pid, 1, side["length_m"], correction=override or None)
    except ValueError as exc:
        return dict(base, applied=False, reason=str(exc)[:200])
    conflicts = list(run.get("conflicts") or [])
    if run["provenance"] != "user_corrected":
        # the same structural check the build reports: photos that disagree on floors or bays block this side
        conflicts += grammar.structure_conflicts(obs_s, pid)
    accepted = len({o.get("id") for o in obs_s if o.get("confidence", 0) >= grammar.MIN_CONFIDENCE})
    policy = grammar.policy(accepted, conflicts, run["confidence"])
    if run["provenance"] == "user_corrected":
        policy = "observed"                                  # a person decided this side; the correction is evidence
    if policy not in APPLIED_POLICIES:
        return dict(base, applied=False, policy=policy, reason=_why(policy, conflicts))
    floors = max(z["to"] for z in run["grammar"]["floor_zones"]) + 1
    bays = run["grammar"]["bay_count"]
    cornice = float(fac.get("cornice", 0) or 0)
    level_h = max(2.0, (float(part["height"]) - cornice) / floors)
    return dict(base, applied=True, policy=policy, floors=floors, bays=bays,
                level_height=round(level_h, 3), bay_spacing=round(side["length_m"] / bays, 3),
                provenance=run["provenance"], confidence=run["confidence"], evidence=run["evidence"])


def _restore(part: dict) -> None:
    grid = part.pop("observed_grid", None)
    fac = part.get("facade")
    if not grid or not fac:
        return
    before = grid.get("before", {})
    for key in ("level_height", "bay_spacing"):
        if before.get(key) is None:
            fac.pop(key, None)
        else:
            fac[key] = before[key]
    if before.get("sides") is None:
        fac.pop("sides", None)
    else:
        fac["sides"] = before["sides"]


def _snapshot(part: dict) -> dict:
    fac = part["facade"]
    sides_before = fac.get("sides")
    return {"level_height": fac.get("level_height"), "bay_spacing": fac.get("bay_spacing"),
            "sides": None if sides_before is None else [dict(x) for x in sides_before]}


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
