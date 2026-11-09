"""Component registry: the versioned library of reusable detail modules (plan section 6.3, M1 task 2).

Metadata lives in landmark3d/components/library_v1.json. Generators are Python callables registered in GENERATORS;
M1 registers none, because no vertical slice has been implemented yet. A component whose lods name an unregistered
generator is a validation error, so a library entry can never promise geometry the code does not produce.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

LIBRARY_FILE = Path(__file__).resolve().parent.parent / "components" / "library_v1.json"
GENERATORS: dict = {}                  # generator name -> callable(params) -> GeneratedComponent


@lru_cache(maxsize=1)
def load_library(path: str | None = None) -> dict:
    return json.loads(Path(path or LIBRARY_FILE).read_text(encoding="utf-8"))


def library_version(lib: dict | None = None) -> str:
    return str((lib or load_library())["library_version"])


LIBRARY_VERSION = library_version()


def component(component_id: str, lib: dict | None = None) -> dict:
    for c in (lib or load_library())["components"]:
        if c["id"] == component_id:
            return c
    raise KeyError(component_id)


def validate_registry(lib: dict | None = None, generators: dict | None = None) -> list[str]:
    lib = lib or load_library()
    generators = GENERATORS if generators is None else generators
    errors = []
    if not isinstance(lib.get("library_version"), str) or not lib["library_version"]:
        errors.append("library_version is required")
    ids = [c.get("id") for c in lib.get("components", [])]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"duplicate component id {dup}")
    for c in lib.get("components", []):
        cid = c.get("id", "?")
        if c.get("status") not in ("planned", "implemented"):
            errors.append(f"{cid}: status must be planned or implemented")
        if not c.get("semantic_type"):
            errors.append(f"{cid}: semantic_type is required")
        if not c.get("material_slots"):
            errors.append(f"{cid}: at least one material slot is required")
        if c.get("status") == "planned" and c.get("lods"):
            errors.append(f"{cid}: a planned component may not declare lods yet; use planned_lods")
        for lod in c.get("lods", []):
            if lod.get("generator") not in generators:
                errors.append(f"{cid}: generator {lod.get('generator')!r} is not registered")
        levels = [lod.get("level") for lod in c.get("lods", [])]
        if levels != sorted(levels) or len(set(levels)) != len(levels):
            errors.append(f"{cid}: lod levels must be unique and ascending")
        for name, p in c.get("parameters", {}).items():
            lo, hi, default = p.get("min"), p.get("max"), p.get("default")
            if lo is not None and hi is not None and lo > hi:
                errors.append(f"{cid}.{name}: min above max")
            if default is not None and ((lo is not None and default < lo) or (hi is not None and default > hi)):
                errors.append(f"{cid}.{name}: default outside its range")
    return errors
