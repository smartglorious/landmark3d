"""Facade observations: schema, validation against the project, and deterministic merge (plan section 6.1, M1 task 3).

The schema checks shape. Validation checks what the schema cannot know: that the source and part exist, that the source
is an exterior photo that may be used, that lines are ordered and elements sit inside their facade. Merge never averages
conflicting observations; it keeps the more confident one and reports a conflict between two confident ones for review.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

SCHEMA_FILE = Path(__file__).resolve().parent / "schemas" / "facade_observations.schema.json"
CONFLICT_CONFIDENCE = 0.8               # two observations this sure that disagree need a person, not an average


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def schema_errors(doc) -> list[str]:
    import jsonschema
    v = jsonschema.Draft202012Validator(schema())
    return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"[:240] for e in v.iter_errors(doc)]


def _strictly_ordered(values) -> bool:
    pairs = list(zip(values, values[1:]))
    return all(a < b for a, b in pairs) or all(a > b for a, b in pairs)


def _iou(a, b) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def validate(doc, sources: dict, parts: set) -> list[str]:
    """Errors that make an observation file unusable. sources: id -> source record; parts: known part ids."""
    errors = schema_errors(doc)
    if errors:
        return errors
    seen_ids = set()
    for ob in doc["observations"]:
        oid = ob["id"]
        if oid in seen_ids:
            errors.append(f"{oid}: duplicate observation id")
        seen_ids.add(oid)
        src = sources.get(ob["source_id"])
        if src is None:
            errors.append(f"{oid}: unknown source {ob['source_id']}")
        elif src.get("kind") != "photo" or src.get("use") == "excluded":
            errors.append(f"{oid}: source {ob['source_id']} is not a usable photo")
        if ob["part_id"] not in parts:
            errors.append(f"{oid}: unknown part {ob['part_id']}")
        if not _strictly_ordered(ob["floor_lines"]):
            errors.append(f"{oid}: floor_lines must be strictly ordered")
        if not _strictly_ordered(ob["bay_lines"]):
            errors.append(f"{oid}: bay_lines must be strictly ordered")
        xs = [p[0] for p in ob["quad"]]
        ys = [p[1] for p in ob["quad"]]
        qx0, qx1, qy0, qy1 = min(xs), max(xs), min(ys), max(ys)
        floors = len(ob["floor_lines"]) - 1
        ids = set()
        for el in ob["elements"]:
            if el["id"] in ids:
                errors.append(f"{oid}: duplicate element id {el['id']}")
            ids.add(el["id"])
            x0, y0, x1, y1 = el["bbox"]
            if not (x1 > x0 and y1 > y0):
                errors.append(f"{oid}/{el['id']}: bbox must have positive area")
            if x0 < qx0 - 1e-9 or x1 > qx1 + 1e-9 or y0 < qy0 - 1e-9 or y1 > qy1 + 1e-9:
                errors.append(f"{oid}/{el['id']}: bbox lies outside the facade quad")
            if el["floor"] >= max(1, floors):
                errors.append(f"{oid}/{el['id']}: floor {el['floor']} is not between the floor lines")
    return errors


def merge(observations: list[dict]) -> tuple[list[dict], list[str]]:
    """Merge observations of the same element. The most confident wins; two confident, disagreeing observations are
    reported for review and neither is averaged in. Output order is deterministic."""
    by_id: dict[str, list[tuple[dict, dict]]] = {}
    for ob in observations:
        for el in ob.get("elements", []):
            by_id.setdefault(el["id"], []).append((ob, el))
    merged, issues = [], []
    for eid in sorted(by_id):
        ranked = sorted(by_id[eid], key=lambda pair: (-pair[0]["confidence"], -pair[1]["confidence"], pair[0]["id"]))
        best_ob, best = ranked[0]
        for other_ob, other in ranked[1:]:
            disagree = other["type"] != best["type"] or _iou(best["bbox"], other["bbox"]) < 0.5
            if disagree and min(best_ob["confidence"], other_ob["confidence"]) >= CONFLICT_CONFIDENCE:
                issues.append(f"{eid}: confident observations {best_ob['id']} and {other_ob['id']} disagree - review")
        merged.append(dict(best, merged_from=[ob["id"] for ob, _ in ranked]))
    return merged, issues


def conflicts_block(issues: list[str]) -> bool:
    """A conflict makes the affected run need review before it is used."""
    return bool(issues)


def finite_unit(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1
