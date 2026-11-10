"""User corrections to facade observations (plan M3, tasks 6-7).

A person fixes floor lines, bay lines and floor types on the A2 panel. The corrections live in
`input/observation_corrections.json`, so they survive rebuilds and are never overwritten by a new AI observation. A
correction is a decision by a person, so it wins over the AI for that part; the run is marked `user_corrected`.
"""
from __future__ import annotations

from .observations import finite_unit

FILE = "input/observation_corrections.json"


def _ascending(values) -> bool:
    return all(a < b for a, b in zip(values, values[1:]))


def validate(doc, parts: set) -> list[str]:
    errors = []
    if not isinstance(doc, dict) or not isinstance(doc.get("parts"), dict):
        return ["corrections need a 'parts' object"]
    for pid, c in doc["parts"].items():
        if pid not in parts:
            errors.append(f"{pid}: no such part in the spec")
            continue
        for key in ("floor_lines", "bay_lines"):
            if key in c:
                vals = c[key]
                if not isinstance(vals, list) or len(vals) < 2:
                    errors.append(f"{pid}.{key}: needs at least two lines")
                elif not all(finite_unit(v) for v in vals):
                    errors.append(f"{pid}.{key}: lines must be finite values in [0, 1]")
                elif not _ascending(vals):
                    errors.append(f"{pid}.{key}: lines must be strictly ascending")
        for floor, t in (c.get("floor_types") or {}).items():
            if not str(floor).isdigit():
                errors.append(f"{pid}.floor_types: floor {floor!r} is not a floor index")
            if not isinstance(t, str) or not 1 <= len(t) <= 40:
                errors.append(f"{pid}.floor_types: element type must be a short name")
    return errors


def load(prj) -> dict:
    doc = prj.read_json(FILE)
    return doc if isinstance(doc, dict) and isinstance(doc.get("parts"), dict) else {"parts": {}}


def save(prj, doc: dict, parts: set) -> list[str]:
    errors = validate(doc, parts)
    if not errors:
        prj.write_json(FILE, doc)
    return errors


def for_part(doc: dict, part_id: str) -> dict:
    return (doc or {}).get("parts", {}).get(part_id, {})
