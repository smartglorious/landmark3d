"""Version 1 facade -> normalized facade grammar, without changing the output (plan M1 task 4).

The grammar carries the complete v1 facade, so converting back gives exactly the input and the geometry engine keeps
reading the same values. The derived floor zones describe the same facade in the vocabulary of version 2 for later
milestones; nothing reads them yet.
"""
from __future__ import annotations

import copy

GRAMMAR_VERSION = 1


def _zone_end(rule: dict):
    return int(rule.get("to", rule.get("from", 0)))


def from_v1(facade: dict | None) -> dict:
    fac = copy.deepcopy(facade or {})
    zones = []
    if fac.get("ground"):
        zones.append({"kind": "ground", "from": 0, "to": 0, "module": str(fac["ground"].get("opening_type", "storefront"))})
    for rule in fac.get("floors") or []:
        if isinstance(rule, dict) and "from" in rule:
            zones.append({"kind": "repeat", "from": int(rule["from"]), "to": _zone_end(rule),
                          "module": str(rule.get("opening_type", fac.get("opening_type", "rect")))})
    if fac.get("opening_type") and fac.get("opening_type") != "curtain_wall":
        zones.append({"kind": "repeat", "from": 0, "to": None, "module": str(fac["opening_type"])})
    if fac.get("cornice"):
        zones.append({"kind": "cornice", "module": "cornice_stepped"})
    return {
        "grammar_version": GRAMMAR_VERSION,
        "source": "v1",
        "v1_facade": fac,
        "floor_zones": zones,
        "level_height_m": fac.get("level_height"),
        "bay_spacing_m": fac.get("bay_spacing"),
    }


def to_v1(grammar: dict) -> dict:
    if grammar.get("grammar_version") != GRAMMAR_VERSION or grammar.get("source") != "v1":
        raise ValueError("only grammars made from a version 1 facade can be converted back")
    return copy.deepcopy(grammar["v1_facade"])


def check(grammar: dict) -> list[str]:
    errs = []
    if grammar.get("grammar_version") != GRAMMAR_VERSION:
        errs.append("unknown grammar version")
    if not isinstance(grammar.get("v1_facade"), dict):
        errs.append("grammar must keep the v1 facade")
    for z in grammar.get("floor_zones", []):
        if z.get("kind") not in ("ground", "repeat", "cornice", "attic", "band"):
            errs.append(f"unknown zone kind {z.get('kind')!r}")
        if z.get("from") is not None and z.get("to") is not None and z["to"] < z["from"]:
            errs.append("a zone ends below where it starts")
    return errs
