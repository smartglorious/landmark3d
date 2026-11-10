"""M3: compile merged facade observations into a facade grammar run (plan section 6.2 and M3 tasks 4 and fallbacks).

Pure and deterministic: observations in, grammar out. Nothing here calls AI or writes project files. The AI step that
produces observations is not wired yet (see docs/M2_M5_RUN_REPORT.md); this module is what that step will feed.
"""
from __future__ import annotations

import copy

from . import observations as obs_mod

MIN_CONFIDENCE = 0.7                    # plan 5.3: only observations this sure take part in the gates
CONFLICT_CONFIDENCE = 0.8              # confident enough that a disagreement is a conflict, not noise
HIDDEN_CONFIDENCE_CAP = 0.6             # reused grammar on hidden facades is never shown as confidently observed
PORTAL_TYPES = ("entrance_portal", "portal", "entrance")
GROUND_TYPES = ("storefront", "storefront_arch", "ground_arch")


def usable(observations: list[dict], part_id: str) -> list[dict]:
    return [o for o in observations
            if o.get("part_id") == part_id and o.get("confidence", 0) >= MIN_CONFIDENCE
            and (o.get("review") or {}).get("status") != "rejected"]


def _dominant(types: list[tuple[str, float]]) -> str:
    score: dict[str, float] = {}
    for t, c in types:
        score[t] = score.get(t, 0.0) + c
    return sorted(score, key=lambda t: (-score[t], t))[0]


def _zones(by_floor: dict[int, str], floors: int) -> list[dict]:
    zones: list[dict] = []
    for f in range(floors):
        t = by_floor.get(f, "rect")
        kind = "ground" if f == 0 and t in GROUND_TYPES else "repeat"
        if zones and zones[-1]["module"] == t and zones[-1]["kind"] == kind and zones[-1]["to"] == f - 1:
            zones[-1]["to"] = f
        else:
            zones.append({"kind": kind, "from": f, "to": f, "module": t})
    return zones


def compile_run(observations: list[dict], part_id: str, run_no: int, facade_length_m: float,
                correction: dict | None = None) -> dict:
    """One facade run from the usable observations of one part, with the person's correction (if any) applied.

    A correction (floor_lines, bay_lines, floor_types) is a decision by a person and wins over the AI for this part.
    Without any usable observation, a correction with both floor and bay lines is enough to compile a run."""
    corr = correction or {}
    obs = usable(observations, part_id)
    if not obs and not (corr.get("floor_lines") and corr.get("bay_lines")):
        raise ValueError(f"no usable observation for part {part_id}")
    if obs:
        merged, conflicts = obs_mod.merge(obs)
        best = max(obs, key=lambda o: (o["confidence"], o["id"]))
        floor_lines, bay_lines = best["floor_lines"], best["bay_lines"]
    else:
        merged, conflicts = [], []
        floor_lines, bay_lines = [], []
    floor_lines = corr.get("floor_lines") or floor_lines
    bay_lines = corr.get("bay_lines") or bay_lines
    bays = len(bay_lines) - 1
    floors = len(floor_lines) - 1
    if bays < 1 or floors < 1 or facade_length_m <= 0:
        raise ValueError("an observation needs at least one bay, one floor and a facade length")
    typed: dict[int, list[tuple[str, float]]] = {}
    for el in merged:
        typed.setdefault(el["floor"], []).append((el["type"], el["confidence"]))
    by_floor = {f: _dominant(v) for f, v in typed.items()}
    for f, t in (corr.get("floor_types") or {}).items():
        by_floor[int(f)] = t                                  # a person chose this floor's element type
    # mirror symmetry: the repeated elements sit at the same floors and mirrored bay indexes
    cells = {(el["floor"], el["bay"]) for el in merged if el["type"] not in PORTAL_TYPES}
    mirrored = {(f, bays - 1 - b) for f, b in cells}
    symmetry = "mirror" if cells and len(cells & mirrored) / len(cells) >= 0.8 else "none"
    overrides = []
    for el in merged:
        if el["type"] in PORTAL_TYPES:
            x0, _, x1, _ = el["bbox"]
            overrides.append({"floor": el["floor"], "bay": el["bay"], "module": "entrance_portal",
                              "params": {"width_m": round((x1 - x0) * facade_length_m, 2)}})
    confidence = round(sum(o["confidence"] for o in obs) / len(obs), 4) if obs else 1.0
    evidence = sorted({o["id"] for o in obs})
    if corr:
        evidence.append("USER-CORRECTION")
    return {
        "run": run_no,
        "grammar": {"floor_zones": _zones(by_floor, floors), "bay_count": bays,
                    "bay_width_m": round(facade_length_m / bays, 4), "symmetry": symmetry},
        "overrides": overrides,
        "evidence": evidence,
        "provenance": "user_corrected" if corr else "observed",
        "confidence": confidence,
        "conflicts": conflicts,
    }


def mark_hidden(run: dict) -> dict:
    """Reuse a confirmed grammar on a facade no photo shows. It is never labelled observed (plan invariant 5)."""
    out = copy.deepcopy(run)
    out["provenance"] = "style_inferred"
    out["confidence"] = round(min(out.get("confidence", 0.0), HIDDEN_CONFIDENCE_CAP), 4)
    return out


def policy(accepted_photos: int, conflicts: list, run_confidence: float | None) -> str:
    """What a run may be built from (plan M3 fallbacks)."""
    if conflicts:
        return "blocked"                 # conflicting confident photos: a person decides at A2
    if accepted_photos == 0 or run_confidence is None:
        return "v1_default"              # no evidence: keep the version 1 facade and label it default
    if accepted_photos == 1:
        return "visible_only"            # one photo: only the visible facade, hidden ones are style_inferred
    return "observed"


def facade_system(style_family: str, style_confidence: float, runs: list[dict]) -> dict:
    """The facade_system object of spec version 2 (strips the bookkeeping that is not part of the contract)."""
    keep = ("run", "grammar", "overrides", "evidence")
    return {"style_family": style_family, "style_confidence": style_confidence,
            "runs": [{k: r[k] for k in keep} for r in runs]}


def structure_conflicts(observations: list[dict], part_id: str) -> list[str]:
    """Confident observations of one part that disagree on how many floors or bays the facade has. Merging matches
    elements by id, and ids are per photo, so this is the check that catches photos which read the same facade
    differently (production baseline: conflicting confident evidence blocks the run)."""
    obs = [o for o in usable(observations, part_id) if o.get("confidence", 0) >= CONFLICT_CONFIDENCE]
    if len(obs) < 2:
        return []
    floors = {len(o["floor_lines"]) - 1 for o in obs}
    bays = {len(o["bay_lines"]) - 1 for o in obs}
    out = []
    if len(floors) > 1:
        out.append(f"{part_id}: confident observations disagree on the number of floors {sorted(floors)}")
    if len(bays) > 1:
        out.append(f"{part_id}: confident observations disagree on the number of bays {sorted(bays)}")
    return out
