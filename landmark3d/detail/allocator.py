"""Complexity allocator (plan section 8; M2 task 6).

Candidates are upgrades (current LOD -> target LOD) with an incremental cost. They are chosen greedily by utility per
triangle, a candidate may require another to be chosen first, and selection stops at 90% of the target budget (plan 8.3).
The reason an allocation is low is reported with the codes of section 8.3, so a low result is explained, never hidden.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .types import DetailCandidate

STOP_FRACTION = 0.9
LOW_UTILIZATION = 0.6


@dataclass
class Choice:
    candidate: DetailCandidate
    requires: tuple = ()


def select(choices: list[Choice], base_triangles: int, target_triangles: int, hard_triangles: int | None = None) -> dict:
    budget = max(0, int(target_triangles * STOP_FRACTION) - base_triangles)
    hard = None if hard_triangles is None else hard_triangles - base_triangles
    order = sorted(choices, key=lambda ch: (-(ch.candidate.utility / max(1, ch.candidate.incremental_triangles)),
                                            ch.candidate.id))
    chosen: set[str] = set()
    selected, rejected, used = [], [], 0
    invalid = [ch for ch in order if ch.candidate.problems()]
    rejected += [{"id": ch.candidate.id, "reason": "invalid"} for ch in invalid]
    pending = [ch for ch in order if not ch.candidate.problems()]
    progressed = True
    while progressed:                                   # a candidate may wait for the one it requires
        progressed = False
        for ch in list(pending):
            c = ch.candidate
            if any(r not in chosen for r in ch.requires):
                continue
            if used + c.incremental_triangles > budget or (hard is not None and used + c.incremental_triangles > hard):
                continue
            chosen.add(c.id)
            selected.append(c.id)
            used += c.incremental_triangles
            pending.remove(ch)
            progressed = True
    for ch in pending:
        missing = [r for r in ch.requires if r not in chosen]
        reason = "dependency" if missing else "budget"
        rejected.append({"id": ch.candidate.id, "reason": reason, **({"needs": missing} if missing else {})})
    total = base_triangles + used
    util = round(total / target_triangles, 4) if target_triangles else None
    low_reason = None
    if util is not None and util < LOW_UTILIZATION:
        # Only budget or dependency limits explain a low result. A run that took every supported upgrade simply
        # has nothing more to add; a generator failure is reported by the build itself, never guessed here.
        if not choices or not rejected:
            low_reason = "no_supported_candidates"
        elif any(r["reason"] == "budget" for r in rejected):
            low_reason = "budget_limited"          # the allocator tracks triangles only; byte/material limits come later
        else:
            low_reason = "no_supported_candidates"
    return {"selected": selected, "rejected": rejected, "added_triangles": used, "total_triangles": total,
            "target_utilization": util, "low_utilization_reason": low_reason}


def utility(area_m2: float, salience: float = 1.0, confidence: float = 1.0) -> float:
    """Deterministic value of a detail: visible area, weighted by how salient and how well evidenced it is."""
    return max(0.0, area_m2) * max(0.0, salience) * max(0.0, min(1.0, confidence))
