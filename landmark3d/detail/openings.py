"""Opening assemblies at LOD0, LOD1 and LOD2 (plan section 7 "Opening assemblies"; M2).

LOD0  glass panel, as the v1 facade draws it.
LOD1  the glass is set back behind a projecting surround: sill, jambs (or an arch band) and lintel, plus the recess.
LOD2  adds a central mullion for wide openings, a transom for tall rectangular ones and a keystone on arches.

Honest limit: the wall is one mesh, so the opening is not cut through it. The recess is shown by the projecting surround
and the set-back glass, not by a boolean hole. Geometry comes from geom.facade_layout, so the spacing, pilasters and
entrance constraints are the ones the v1 engine already checks.
"""
from __future__ import annotations

from .. import geom
from .types import Bounds, GeneratedComponent

FRAME_MAX = 0.25            # metres: the surround never wider than this
RECESS_MAX = 0.8            # plan: depth within wall thickness assumptions
MULLION_MIN_WIDTH = 2.4
TRANSOM_MIN_HEIGHT = 3.0
ARCH_SEGMENTS = 8           # the v1 count; kept for callers that ask for the old fixed value
# Arch tessellation scales with the arch size and the LOD (plan M2): the longest chord of the half circle stays under
# this many metres, so a small arch is not over-built and a wide one does not look faceted at close range.
ARCH_CHORD_M = {0: 0.6, 1: 0.3, 2: 0.15}
ARCH_SEGMENTS_MIN, ARCH_SEGMENTS_MAX = 4, 32

GENERATOR_FOR_LOD = {0: "opening_panel", 1: "opening_recessed", 2: "opening_surround"}


def arch_segments(width: float, lod: int) -> int:
    """Segments of the half circle of an arch `width` metres wide at `lod`: the chord stays under ARCH_CHORD_M[lod]."""
    import math
    chord = ARCH_CHORD_M.get(max(0, min(2, int(lod))))
    r = max(0.05, float(width) / 2)
    n = math.ceil(math.pi * r / chord)
    return max(ARCH_SEGMENTS_MIN, min(ARCH_SEGMENTS_MAX, n))


def _at(win):
    p, t, o = win["p"], win["t"], win["o"]
    return lambda x, y, z: (p[0] + t[0] * x + o[0] * y, p[1] + t[1] * x + o[1] * y, z)


def dimensions(win, lod: int) -> dict:
    w = max(0.2, float(win["w"]))
    h = max(0.2, float(win["h"]))
    # The surround never reaches the next pilaster or entrance: its width is capped by the layout's clear space.
    clear = max(0.0, float(win.get("frame_half", w / 2 + FRAME_MAX)) - w / 2)
    frame = min(FRAME_MAX, w * 0.2, clear) if lod >= 1 else 0.0
    recess = min(RECESS_MAX, max(0.08, w * 0.06)) if lod >= 1 else 0.0
    return {"w": w, "h": h, "frame": frame, "recess": recess, "arch": win.get("kind") == "arch"}


def emit(mesh, win, lod: int) -> int:
    """Add the assembly of one window to `mesh`. Returns the triangles added."""
    before = mesh.triangles()
    d = dimensions(win, lod)
    w, h, frame, recess, arch = d["w"], d["h"], d["frame"], d["recess"], d["arch"]
    z0 = float(win["z0"])
    at = _at(win)
    glass_y = 0.02 if lod >= 1 else 0.05
    segs = arch_segments(w, lod)
    shape = geom.arch_outline(w, h, segs) if arch else [(-w / 2, 0.0), (w / 2, 0.0), (w / 2, h), (-w / 2, h)]
    mesh.f([mesh.v(*at(x, glass_y, z0 + z)) for x, z in shape], "opening")
    if lod == 0:
        return mesh.triangles() - before
    spring = max(0.0, h - w / 2) if arch else h
    sill_z = z0 - 0.12
    geom._solid(mesh, at, -w / 2 - frame, w / 2 + frame, 0.0, recess + 0.05, sill_z, z0, "trim")
    if arch:
        outer = geom.arch_outline(w + 2 * frame, h + frame, segs)
        inner = geom.arch_outline(w, h, segs)
        plane = recess
        for i in range(len(inner) - 1):                              # the band: jambs and arc, bottom edge excluded
            a, b = outer[i], outer[i + 1]
            c, dd = inner[i + 1], inner[i]
            mesh.f([mesh.v(*at(x, plane, z0 + z)) for x, z in (a, b, c, dd)], "trim")
    else:
        geom._solid(mesh, at, -w / 2 - frame, -w / 2, 0.0, recess, z0, z0 + h, "trim")
        geom._solid(mesh, at, w / 2, w / 2 + frame, 0.0, recess, z0, z0 + h, "trim")
        geom._solid(mesh, at, -w / 2 - frame, w / 2 + frame, 0.0, recess, z0 + h, z0 + h + frame, "trim")
    if lod >= 2:
        if w >= MULLION_MIN_WIDTH and not arch:
            geom._solid(mesh, at, -0.06, 0.06, 0.0, recess, z0, z0 + h, "trim")
        if not arch and h >= TRANSOM_MIN_HEIGHT:
            zt = z0 + h * 0.6
            geom._solid(mesh, at, -w / 2, w / 2, 0.0, recess * 0.8, zt - 0.05, zt + 0.05, "trim")
        if arch:
            geom._solid(mesh, at, -0.18, 0.18, 0.0, recess + 0.06, z0 + h, z0 + h + 0.3, "trim")
        else:                                                       # a head moulding over the lintel
            geom._solid(mesh, at, -w / 2 - frame, w / 2 + frame, 0.0, recess + 0.06,
                        z0 + h + frame, z0 + h + frame + 0.06, "trim")
    return mesh.triangles() - before


def assemble(win, lod: int) -> GeneratedComponent:
    """One window as a standalone component (used by the tests and the registry)."""
    m = geom.Mesh()
    emit(m, win, lod)
    xs = [v[0] for v in m.verts] or [0.0]
    ys = [v[1] for v in m.verts] or [0.0]
    zs = [v[2] for v in m.verts] or [0.0]
    return GeneratedComponent(mesh=m, triangle_count=sum(len(f) - 2 for f in m.faces),
                              material_slots={"opening", "trim"},
                              bounds=Bounds((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))),
                              anchors={"center": (win["p"][0], win["p"][1], float(win["z0"]))})


def window_key(win) -> tuple:
    return (win.get("run"), round(float(win["s"]), 3), win.get("lv"))


def tri_cost(win, lod: int) -> int:
    m = geom.Mesh()
    return emit(m, win, lod)


def plan(windows: list, budget: int) -> dict:
    """LOD per window, chosen by the allocator under `budget` triangles. Every window starts at LOD0 (always built)."""
    from . import allocator
    from .allocator import Choice, select, utility
    from .types import DetailCandidate
    base = {window_key(w): tri_cost(w, 0) for w in windows}
    choices, target_of = [], {}
    for n, w in enumerate(windows):
        k = window_key(w)
        area = float(w["w"]) * float(w["h"])
        c1 = tri_cost(w, 1) - base[k]
        c2 = tri_cost(w, 2) - tri_cost(w, 1)
        id1, id2 = f"w{n}.lod0_to_lod1", f"w{n}.lod1_to_lod2"
        target_of[id1], target_of[id2] = (k, 1), (k, 2)
        choices.append(Choice(DetailCandidate(id1, [f"w{n}"], 0, 1, c1, utility=utility(area)), ()))
        choices.append(Choice(DetailCandidate(id2, [f"w{n}"], 1, 2, c2, utility=utility(area, salience=0.5)), (id1,)))
    base_total = sum(base.values())
    # select() keeps STOP_FRACTION of its target for the building, so the target is sized to leave exactly `budget`
    result = select(choices, base_total, round((budget + base_total) / allocator.STOP_FRACTION))
    lods = {window_key(w): 0 for w in windows}
    for cid in result["selected"]:
        k, target = target_of[cid]
        lods[k] = max(lods[k], target)
    return {"lods": lods, "allocation": result}


def builder_for(windows: list, budget: int):
    """A window builder for geom.facade_openings: builds each window at its planned LOD and keeps the accounting."""
    plan_out = plan(windows, budget)
    stats = {"lod_counts": {0: 0, 1: 0, 2: 0}, "allocation": plan_out["allocation"]}

    def build(mesh, win) -> int:
        lod = plan_out["lods"].get(window_key(win), 0)
        stats["lod_counts"][lod] += 1
        return emit(mesh, win, lod)
    return build, stats


def _register() -> None:
    from . import registry
    registry.GENERATORS.update({
        "opening_panel": lambda win: assemble(win, 0),
        "opening_recessed": lambda win: assemble(win, 1),
        "opening_surround": lambda win: assemble(win, 2),
    })


_register()
