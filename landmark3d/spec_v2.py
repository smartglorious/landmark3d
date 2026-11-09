"""Spec version 2 validation (plan section 6.2, M1 task 3). Version 1 specs stay valid and are read by the adapter in
facade_grammar.py. Nothing in the pipeline writes version 2 yet; that waits for the migration tests (plan invariant 6)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SCHEMA_FILE = Path(__file__).resolve().parent / "schemas" / "landmark_spec_v2.schema.json"


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def validate(spec) -> list[str]:
    import jsonschema
    v = jsonschema.Draft202012Validator(schema())
    errors = [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"[:240] for e in v.iter_errors(spec)]
    if errors:
        return errors
    for part in spec["parts"]:
        fs = part.get("facade_system")
        if not fs:
            continue
        runs = [r["run"] for r in fs["runs"]]
        if len(set(runs)) != len(runs):
            errors.append(f"{part['id']}: duplicate facade run numbers")
        for r in fs["runs"]:
            zones = r["grammar"]["floor_zones"]
            for z in zones:
                lo, hi = z.get("from"), z.get("to", z.get("from"))
                if lo is not None and hi is not None and hi < lo:
                    errors.append(f"{part['id']}/run {r['run']}: a floor zone ends below where it starts")
            for o in r.get("overrides", []):
                if o["bay"] >= r["grammar"]["bay_count"]:
                    errors.append(f"{part['id']}/run {r['run']}: override bay {o['bay']} is outside the grammar")
    return errors
