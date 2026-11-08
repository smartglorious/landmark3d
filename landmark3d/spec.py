"""Generic landmark spec (any shape): parts with footprints, heights, roofs, facades, materials.

No landmark-specific modules: every building is a set of parts. A part's footprint is the main
footprint, an OSM building:part polygon, a rectangle or a circle in local metres (x east, y north,
origin = footprint centroid). Every value that did not come from the user carries provenance.
"""
from __future__ import annotations

import math

import jsonschema

from . import geom

ROOF_TYPES = ["flat", "gable", "hip", "pyramid", "dome", "shed", "none"]
OSM_ROOF = {"flat": "flat", "gabled": "gable", "hipped": "hip", "pyramidal": "pyramid", "dome": "dome",
            "onion": "dome", "skillion": "shed", "half-hipped": "hip", "mansard": "hip", "gambrel": "gable",
            "round": "dome", "saltbox": "gable", "cone": "pyramid"}

OPENING_TYPES = ["rect", "arch", "square", "storefront", "none"]
FLOOR_RULE = {
    "type": "object", "required": ["from"],
    "properties": {
        "from": {"type": "integer", "minimum": 0}, "to": {"type": "integer", "minimum": 0},
        "opening_type": {"enum": OPENING_TYPES}, "bay_spacing": {"type": "number", "exclusiveMinimum": 1},
        "opening_width": {"type": "number"}, "opening_height": {"type": "number"}, "sill": {"type": "number"},
        "pediment": {"type": "boolean"}, "awning": {"type": "boolean"}, "awning_depth": {"type": "number"}},
}

PART_SCHEMA = {
    "type": "object",
    "required": ["id", "footprint", "base_z", "height", "roof"],
    "properties": {
        "id": {"type": "string"},
        "kind": {"type": "string"},
        "footprint": {"oneOf": [
            {"const": "main"},
            {"type": "object", "required": ["polygon"], "properties": {"polygon": {"type": "array", "minItems": 3}}},
            {"type": "object", "required": ["rect"], "properties": {"rect": {
                "type": "object", "required": ["cx", "cy", "w", "d"],
                "properties": {"cx": {"type": "number"}, "cy": {"type": "number"}, "w": {"type": "number", "exclusiveMinimum": 0},
                               "d": {"type": "number", "exclusiveMinimum": 0}, "rot_deg": {"type": "number"}}}}},
            {"type": "object", "required": ["circle"], "properties": {"circle": {
                "type": "object", "required": ["cx", "cy", "r"],
                "properties": {"cx": {"type": "number"}, "cy": {"type": "number"}, "r": {"type": "number", "exclusiveMinimum": 0}}}}},
            {"type": "object", "required": ["rounded_rect"], "properties": {"rounded_rect": {
                "type": "object", "required": ["cx", "cy", "w", "d", "r"],
                "properties": {"cx": {"type": "number"}, "cy": {"type": "number"}, "w": {"type": "number", "exclusiveMinimum": 0},
                               "d": {"type": "number", "exclusiveMinimum": 0}, "rot_deg": {"type": "number"},
                               "r": {"type": "number", "minimum": 0}}}}},
            {"type": "object", "required": ["stadium"], "properties": {"stadium": {
                "type": "object", "required": ["cx", "cy", "w", "d"],
                "properties": {"cx": {"type": "number"}, "cy": {"type": "number"}, "w": {"type": "number", "exclusiveMinimum": 0},
                               "d": {"type": "number", "exclusiveMinimum": 0}, "rot_deg": {"type": "number"}}}}},
        ]},
        "base_z": {"type": "number", "minimum": 0},
        "height": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000},
        "roof": {"type": "object", "required": ["type"], "properties": {
            "type": {"enum": ROOF_TYPES}, "height": {"type": "number", "minimum": 0},
            "ridge_axis": {"enum": ["long", "short"]}, "parapet": {"type": "number", "minimum": 0}}},
        "cantilever_of": {"type": "string"},
        "features": {"type": "array", "maxItems": 40, "items": {
            "type": "object", "required": ["type"],
            "properties": {"type": {"enum": ["disc", "panel", "mast"]}, "facing_deg": {"type": "number"},
                           "z": {"type": "number"}, "offset": {"type": "number"},
                           "diameter": {"type": "number", "exclusiveMinimum": 0, "maximum": 40},
                           "width": {"type": "number", "exclusiveMinimum": 0, "maximum": 200},
                           "height": {"type": "number", "exclusiveMinimum": 0, "maximum": 300},
                           "depth": {"type": "number", "minimum": 0, "maximum": 3},
                           "x": {"type": "number"}, "y": {"type": "number"},
                           "radius": {"type": "number", "exclusiveMinimum": 0, "maximum": 3},
                           "material": {"type": "string"}}}},
        "taper": {"type": "object", "properties": {
            "top_scale": {"type": "number", "exclusiveMinimum": 0.02, "maximum": 3},
            "profile": {"enum": ["linear", "ease_in", "ease_out"]},
            "top_shift": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}}},
        "facade": {"type": "object", "properties": {
            "openings": {"type": "boolean"}, "bay_spacing": {"type": "number", "exclusiveMinimum": 0.5},
            "opening_type": {"enum": OPENING_TYPES + ["curtain_wall"]}, "opening_width": {"type": "number"},
            "sill": {"type": "number", "minimum": 0}, "pediment": {"type": "boolean"}, "awning": {"type": "boolean"},
            "floors": {"type": "array", "items": FLOOR_RULE},
            "entrance": {"type": "object", "properties": {
                "width": {"type": "number", "exclusiveMinimum": 1}, "height": {"type": "number", "exclusiveMinimum": 1.5},
                "facing_deg": {"type": "number"}}},
            "pilasters": {"oneOf": [{"type": "boolean"}, {"type": "object", "properties": {
                "every": {"type": "integer", "minimum": 1}, "width": {"type": "number", "exclusiveMinimum": 0},
                "depth": {"type": "number", "exclusiveMinimum": 0}}}]},
            "opening_height": {"type": "number"}, "level_height": {"type": "number", "exclusiveMinimum": 1.5},
            "cornice": {"type": "number", "minimum": 0, "maximum": 4},
            "bands": {"type": "boolean"},
            "ground": {"type": "object", "properties": {
                "opening_type": {"enum": ["storefront", "rect", "arch"]},
                "bay_spacing": {"type": "number", "exclusiveMinimum": 1},
                "opening_width": {"type": "number"}, "opening_height": {"type": "number"},
                "awning": {"type": "boolean"}, "awning_depth": {"type": "number", "minimum": 0.2, "maximum": 3}}}}},
        "material": {"type": "object", "properties": {"wall": {"type": "string"}, "roof": {"type": "string"},
                                                      "trim": {"type": "string"}, "accent": {"type": "string"}}},
        "note": {"type": "string"},
    },
}

MASSING_SCHEMA = {
    "type": "object",
    "required": ["parts"],
    "properties": {
        "parts": {"type": "array", "minItems": 1, "items": PART_SCHEMA},
        "materials": {"type": "object", "additionalProperties": {
            "type": "object", "required": ["srgb"], "properties": {
                "srgb": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 255}, "minItems": 3, "maxItems": 3},
                "roughness": {"type": "number"}, "metallic": {"type": "number", "minimum": 0, "maximum": 1}}}},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}

FACTS_SCHEMA = {
    "type": "object",
    "required": ["values", "sources"],
    "properties": {
        "values": {"type": "object", "additionalProperties": {
            "type": "object", "required": ["value", "confidence"],
            "properties": {"value": {}, "unit": {"type": "string"}, "confidence": {"enum": ["high", "medium", "low"]},
                           "source": {"type": "string"}}}},
        "description": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "object", "required": ["url"],
                                                "properties": {"url": {"type": "string"}, "title": {"type": "string"}}}},
    },
}

DEFAULT_MATERIALS = {
    "wall": {"srgb": [214, 204, 186], "roughness": 0.8},
    "roof": {"srgb": [150, 70, 55], "roughness": 0.65},
    "trim": {"srgb": [236, 232, 222], "roughness": 0.7},
    "opening": {"srgb": [28, 32, 36], "roughness": 0.9},
}


def validate_massing(obj) -> list[str]:
    v = jsonschema.Draft202012Validator(MASSING_SCHEMA)
    return [f"{'/'.join(str(p) for p in e.path)}: {e.message}"[:200] for e in v.iter_errors(obj)]


def validate_facts(obj) -> list[str]:
    v = jsonschema.Draft202012Validator(FACTS_SCHEMA)
    return [f"{'/'.join(str(p) for p in e.path)}: {e.message}"[:200] for e in v.iter_errors(obj)]


def part_polygon(part, main_poly):
    return geom.footprint_polygon(part["footprint"], main_poly)


def srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def estimate_triangles(spec) -> int:
    main = [tuple(p) for p in spec["footprint"]["local"]]
    total = 0
    for part in spec["parts"]:
        m = geom.Mesh()
        poly = part_polygon(part, main)
        geom.build_part(poly, part["base_z"], part["base_z"] + part["height"], part["roof"], m, part.get("taper"))
        total += m.triangles()
    return total


def footprint_dims(poly):
    u, v, (umin, umax, vmin, vmax) = geom.min_area_rect(poly)
    return umax - umin, vmax - vmin, math.degrees(math.atan2(u[1], u[0]))


# ----------------------------------------------------------------------------- structural sanity (gate before A2)
SUPPORT_TOLERANCE_M = 0.5      # plan tolerance when testing "is there something below"
FOOTPRINT_TOLERANCE_M = 1.0    # ground parts may touch / slightly exceed the OSM outline
MAX_UNSUPPORTED = 0.05         # share of a part's plan allowed without support (rounding, mitres)
MAX_OVERHANG_M = 1.0           # beyond the tolerance, no point may hang further than this from its support
MAX_CANTILEVER_M = 9.0         # a declared cantilever (cantilever_of) may project this far from its support
MIN_CANTILEVER_SUPPORT = 0.4   # ... but at least this share of its plan must rest on what is below
# Thin decks (helipads, viewing platforms, canopies: height <= DECK_MAX_HEIGHT_M) are built as structural slabs and
# may project much further, as long as they are declared cantilevers and touch their host.
DECK_MAX_HEIGHT_M = 4.0
DECK_MAX_CANTILEVER_M = 25.0
DECK_MIN_SUPPORT = 0.08


def _top(part) -> float:
    return float(part["base_z"]) + float(part["height"])


def check_structure(parts, main_poly, facts: dict | None = None) -> list[dict]:
    """Physical sanity of a massing: every part stands on the ground inside the footprint or on another part.

    Returns issues [{severity: error|warning, part, code, message}]. Errors block the plan approval (A2) and are
    fed back to the AI massing step so it can correct itself.
    """
    main = [tuple(p) for p in main_poly]
    live = [p for p in parts if p.get("enabled", True) is not False]
    polys = {}
    issues = []
    for p in live:
        try:
            polys[p["id"]] = geom.footprint_polygon(p["footprint"], main)
        except Exception as exc:
            issues.append({"severity": "error", "part": p.get("id"), "code": "bad_footprint",
                           "message": f"part {p.get('id')}: footprint cannot be built ({exc})"})
    for p in live:
        pid = p["id"]
        if pid not in polys or p["footprint"] == "main":
            continue
        pts = geom.sample_points(polys[pid])
        if not pts:
            continue
        z = float(p["base_z"])
        def gap(pt, supports):
            return min((0.0 if geom.point_in_polygon(pt, sp_) else geom.dist_to_polygon(pt, sp_)) for sp_ in supports)                 if supports else float("inf")

        if z <= 0.3:
            gaps = [gap(q, [main]) for q in pts]
            out = [g for g in gaps if g > FOOTPRINT_TOLERANCE_M]
            worst = max(gaps)
            if len(out) / len(pts) > MAX_UNSUPPORTED or worst > FOOTPRINT_TOLERANCE_M + MAX_OVERHANG_M:
                # On the ground, so physically supported: a warning (porches and gates often project past the
                # OSM outline), not a blocking error.
                issues.append({"severity": "warning", "part": pid, "code": "outside_footprint",
                               "message": f"part {pid}: {len(out) / len(pts):.0%} of its plan is outside the building "
                                          f"footprint at ground level (up to {worst:.1f} m out). Fine for a porch or "
                                          f"gate; otherwise move or shrink it."})
            continue
        below = [q for q in live if q["id"] != pid and q["id"] in polys
                 and float(q["base_z"]) <= z + 0.3 and _top(q) >= z - 0.5]
        # a tapered support offers its outline at this height, not its base outline
        sup = [geom.ring_at(polys[q["id"]], float(q["base_z"]), _top(q), q.get("taper"), min(z, _top(q)))
               for q in below]
        gaps = [gap(pt, sup) for pt in pts]
        loose = [g for g in gaps if g > SUPPORT_TOLERANCE_M]
        worst = max(gaps)
        host = p.get("cantilever_of")
        if host:
            host_part = next((q for q in live if q["id"] == host), None)
            if host_part is None or host_part not in below:
                issues.append({"severity": "error", "part": pid, "code": "bad_cantilever",
                               "message": f"part {pid}: cantilever_of '{host}' must be a part directly below it "
                                          f"(top at z={z:g} m)"})
                continue
            supported = 1 - len(loose) / len(pts)
            deck = float(p["height"]) <= DECK_MAX_HEIGHT_M
            min_sup = DECK_MIN_SUPPORT if deck else MIN_CANTILEVER_SUPPORT
            max_m = DECK_MAX_CANTILEVER_M if deck else MAX_CANTILEVER_M
            if supported < min_sup or worst > max_m:
                issues.append({"severity": "error", "part": pid, "code": "cantilever_too_far",
                               "message": f"part {pid}: cantilever rests on only {supported:.0%} of its plan and "
                                          f"projects up to {worst:.1f} m (limits{' for a thin deck' if deck else ''}: "
                                          f"{min_sup:.0%} resting, {max_m:g} m projection)"})
            elif loose:
                issues.append({"severity": "warning", "part": pid, "code": "cantilever",
                               "message": f"part {pid}: declared cantilever over {host}, projecting up to "
                                          f"{worst:.1f} m"})
            continue
        if len(loose) / len(pts) > MAX_UNSUPPORTED or worst > SUPPORT_TOLERANCE_M + MAX_OVERHANG_M:
            names = ", ".join(q["id"] for q in below) or "nothing"
            far = "nothing below it" if worst == float("inf") else f"overhanging up to {worst:.1f} m"
            issues.append({"severity": "error", "part": pid, "code": "unsupported",
                           "message": f"part {pid}: {len(loose) / len(pts):.0%} of its plan floats at z={z:g} m "
                                      f"({far}; parts reaching that height: {names}). Place it fully on top of a "
                                      f"lower part, lower its base_z to the ground, or reshape it."})
    # orientation: rectangular parts follow one of the footprint's edge directions
    dirs = geom.edge_directions(main)
    for p in live:
        fp = p["footprint"]
        key = next((k for k in ("rect", "rounded_rect", "stadium") if isinstance(fp, dict) and k in fp), None)
        if not key or not dirs:
            continue
        rot = float(fp[key].get("rot_deg", 0.0))
        gap = min(geom.angle_gap(rot, d) for d in dirs)
        if gap > 8.0:
            dir_txt = ", ".join(f"{d:g}" for d in dirs)
            issues.append({"severity": "warning", "part": p["id"], "code": "misaligned",
                           "message": f"part {p['id']}: rot_deg {rot:g} does not follow the footprint edges "
                                      f"({dir_txt} deg, or +90); off by {gap:.0f} deg"})
    # height against the researched total height (warning only: facts can be wrong)
    total = None
    if facts:
        v = (facts.get("total_height_m") or {}).get("value") if isinstance(facts.get("total_height_m"), dict) else facts.get("total_height_m")
        try:
            total = float(v) if v is not None else None
        except (TypeError, ValueError):
            total = None
    if total and live:
        top = max(_top(p) + float(p["roof"].get("height", 0) or 0) for p in live)
        if abs(top - total) / total > 0.15:
            issues.append({"severity": "warning", "part": None, "code": "height_mismatch",
                           "message": f"model top {top:.1f} m differs from the researched total height {total:g} m "
                                      f"by {abs(top - total) / total:.0%}"})
    return issues


def structure_errors(parts, main_poly, facts=None) -> list[str]:
    return [i["message"] for i in check_structure(parts, main_poly, facts) if i["severity"] == "error"]
