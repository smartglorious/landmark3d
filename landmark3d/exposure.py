"""Which walls of the model can be seen: the share of each wall face that is not inside a neighbouring volume.

Features (rose windows, clocks, portals, pediments, loggias) are placed by the AI with a compass direction
(`facing_deg`). Nothing told it which faces are buried against other parts, so features ended up on walls nobody can
see (a transept rose window facing into the nave), and the visible ends stayed plain. This module measures exposure
deterministically and turns it into:
- checks (`feature_issues`): a feature on a mostly hidden face, a point feature (finial, mast) outside its part;
- a prompt section (`exposure_text`) listing every part's exposed faces, so the AI places features on them.
No AI here.
"""
from __future__ import annotations

import math

from . import geom

HIDDEN_MAX = 0.5          # a feature's face must be at least this share exposed
FACING_TOL_DEG = 30.0
SAMPLE_OUT_M = 0.4        # samples sit this far in front of the wall
POINT_TOL_M = 0.75        # a point feature may stand this far outside its part's outline (eaves, corner pinnacles)
FACED = ("disc", "panel", "pediment", "colonnade", "column", "balcony", "balustrade")
COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def compass(deg: float) -> str:
    """Compass name of a facing in the local frame (0 = east, counter-clockwise, as facing_deg; 90 = north)."""
    bearing = (90.0 - float(deg)) % 360.0
    return COMPASS[int((bearing + 22.5) // 45) % 8]


def _ang(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def _top(p: dict) -> float:
    return float(p.get("base_z", 0)) + float(p["height"]) + 0.6 * geom.roof_total_height(p.get("roof"))


def faces(spec_or_parts, main) -> dict:
    """{part id: [{"edge_index", "facing_deg", "length_m", "exposed"}]} for every wall face of every enabled part.
    exposed = share of samples (along the face at 25 %, 50 % and 75 % of the wall height) that are not inside another
    enabled part spanning that height."""
    parts = spec_or_parts["parts"] if isinstance(spec_or_parts, dict) else spec_or_parts
    main = [tuple(p) for p in main]
    live = []
    for p in parts:
        if p.get("enabled", True) is False:
            continue
        try:
            live.append((p, geom.ensure_ccw(geom.footprint_polygon(p["footprint"], main))))
        except Exception:
            continue
    out = {}
    for p, ring in live:
        z0, h = float(p.get("base_z", 0)), float(p["height"])
        rows = []
        n = len(ring)
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            L = math.dist(a, b)
            if L < 1.0:
                continue
            nx, ny = (b[1] - a[1]) / L, -(b[0] - a[0]) / L
            k = max(4, min(24, int(L / 1.5)))
            hits = tot = 0
            for frac in (0.25, 0.5, 0.75):
                z = z0 + frac * h
                others = [r for q, r in live if q is not p and float(q.get("base_z", 0)) - 0.1 <= z <= _top(q)]
                for j in range(k):
                    t = (j + 0.5) / k
                    pt = (a[0] + (b[0] - a[0]) * t + nx * SAMPLE_OUT_M, a[1] + (b[1] - a[1]) * t + ny * SAMPLE_OUT_M)
                    tot += 1
                    hits += any(geom.point_in_polygon(pt, r) for r in others)
            rows.append({"edge_index": i, "facing_deg": round(math.degrees(math.atan2(ny, nx)) % 360, 1),
                         "length_m": round(L, 2), "exposed": round(1 - hits / max(1, tot), 2)})
        out[p["id"]] = rows
    return out


def face_for(rows: list, facing: float):
    """The face a feature with this facing sits on: the longest face within FACING_TOL_DEG, else None."""
    cands = [r for r in rows if abs(_ang(r["facing_deg"] - float(facing))) <= FACING_TOL_DEG]
    return max(cands, key=lambda r: r["length_m"]) if cands else None


def _live(parts, main):
    out = []
    for p in parts:
        if p.get("enabled", True) is False:
            continue
        try:
            out.append((p, geom.ensure_ccw(geom.footprint_polygon(p["footprint"], main))))
        except Exception:
            continue
    return out


def _edge(ring, facing):
    """(a, b, L, outward normal) of the longest edge facing within FACING_TOL_DEG of `facing`, else None."""
    best = None
    for i in range(len(ring)):
        a, b = ring[i], ring[(i + 1) % len(ring)]
        L = math.dist(a, b)
        if L < 1.0:
            continue
        n = ((b[1] - a[1]) / L, -(b[0] - a[0]) / L)
        if abs(_ang(math.degrees(math.atan2(n[1], n[0])) - float(facing))) <= FACING_TOL_DEG and \
                (best is None or L > best[2]):
            best = (a, b, L, n)
    return best


def feature_box(ft, L: float, h: float):
    """(x0, x1, z0, z1) of a facing feature on its wall: x from the wall's left end seen from outside (the wall's
    middle + offset), z above the part's base (disc and panel z is their centre, the others their bottom)."""
    kind = ft.get("type")
    off = float(ft.get("offset", 0) or 0)
    if kind == "disc":
        w = hh = float(ft.get("diameter", 3.0))
        z0 = float(ft.get("z", h / 2)) - hh / 2
    elif kind == "panel":
        w, hh = float(ft.get("width", 4.0)), float(ft.get("height", 1.5))
        z0 = float(ft.get("z", h / 2)) - hh / 2
    elif kind == "column":
        n, sp, d = int(ft.get("count", 1) or 1), float(ft.get("spacing", 2.5)), float(ft.get("diameter", 0.6))
        w, hh, z0 = (n - 1) * sp + d, float(ft.get("height", 4.0)), float(ft.get("z", 0.0))
    elif kind == "entrance":
        w, hh, z0 = float(ft.get("width", 3.0)), float(ft.get("height", 4.0)), 0.0
    else:
        w = float(ft.get("width", min(L, 4.0)))
        hh = float(ft.get("height", ft.get("rail_height", 1.5)) or 1.5)
        z0 = float(ft.get("z", 0.0))
    xc = L / 2 + off
    w = min(max(0.5, w), L)
    return xc - w / 2, xc + w / 2, max(0.0, z0), max(0.5, min(h, z0 + hh))


def feature_exposed(p, ring, ft, live):
    """Share of the feature's own patch of wall (just in front of it) that is not inside another part; None when the
    part has no wall facing that way."""
    e = _edge(ring, ft.get("facing_deg", 270))
    if e is None:
        return None
    a, b, L, n = e
    base, h = float(p.get("base_z", 0)), float(p["height"])
    x0, x1, z0, z1 = feature_box(ft, L, h)
    d = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
    hits = tot = 0
    for zf in (0.2, 0.5, 0.8):
        z = base + z0 + (z1 - z0) * zf
        others = [r for q, r in live if q is not p and float(q.get("base_z", 0)) - 0.1 <= z <= _top(q)]
        for j in range(7):
            x = x0 + (x1 - x0) * (j + 0.5) / 7
            pt = (a[0] + d[0] * x + n[0] * SAMPLE_OUT_M, a[1] + d[1] * x + n[1] * SAMPLE_OUT_M)
            tot += 1
            hits += any(geom.point_in_polygon(pt, r) for r in others)
    return 1 - hits / max(1, tot)


def _facing_items(p):
    items = [ft for ft in p.get("features") or []
             if ft.get("type") in FACED and ft.get("facing_deg") is not None and not ft.get("around")]
    ent = (p.get("facade") or {}).get("entrance")
    if ent and ent.get("facing_deg") is not None:
        items.append(dict(ent, type="entrance"))
    return items


def free_stretches(p, ring, live) -> str:
    """Where each wall of the part is free at mid-height, e.g. '319 deg SE: offset -4.0..+4.0 m (8 m free of 25 m)',
    for walls with at least 2 m free, longest first. Offsets run along the wall from its middle, as features use."""
    base, h = float(p.get("base_z", 0)), float(p["height"])
    z = base + 0.5 * h
    others = [r for q, r in live if q is not p and float(q.get("base_z", 0)) - 0.1 <= z <= _top(q)]
    found = []
    for i in range(len(ring)):
        a, b = ring[i], ring[(i + 1) % len(ring)]
        L = math.dist(a, b)
        if L < 2.0:
            continue
        n = ((b[1] - a[1]) / L, -(b[0] - a[0]) / L)
        d = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
        k = max(8, min(60, int(L / 0.5)))
        free = [not any(geom.point_in_polygon((a[0] + d[0] * L * (j + 0.5) / k + n[0] * SAMPLE_OUT_M,
                                               a[1] + d[1] * L * (j + 0.5) / k + n[1] * SAMPLE_OUT_M), r)
                        for r in others) for j in range(k)]
        runs, cur, start = [], 0, 0
        for j, f in enumerate(free + [False]):
            if f:
                if cur == 0:
                    start = j
                cur += 1
            elif cur:
                runs.append((start * L / k, (start + cur) * L / k))
                cur = 0
        runs = [(x0, x1) for x0, x1 in runs if x1 - x0 >= 2.0]
        if runs:
            fac = math.degrees(math.atan2(n[1], n[0])) % 360
            free_m = sum(x1 - x0 for x0, x1 in runs)
            found.append((free_m, f"{fac:.0f} deg {compass(fac)}: offset " + ", ".join(
                f"{x0 - L / 2:+.1f}..{x1 - L / 2:+.1f}" for x0, x1 in runs[:3]) + f" m ({free_m:.0f} m free of {L:.0f} m)"))
    return "; ".join(t for _, t in sorted(found, key=lambda t: -t[0])[:5])


def feature_issues(spec_or_parts, main, fixable_only: bool = False) -> list[str]:
    """Facing features whose own patch of wall is mostly buried in another part (measured where the feature is, not
    over the whole wall: a rose window between two towers is free even when the towers cover most of that wall), and
    point features outside their part. fixable_only: leave out parts hidden on every side."""
    parts = spec_or_parts["parts"] if isinstance(spec_or_parts, dict) else spec_or_parts
    main = [tuple(p) for p in main]
    live = _live(parts, main)
    out = []
    for p, ring in live:
        where = free_stretches(p, ring, live)
        if fixable_only and not where:
            continue
        for ft in _facing_items(p):
            ex = feature_exposed(p, ring, ft, live)
            if ex is None or ex >= HIDDEN_MAX:
                continue
            out.append(f"part {p['id']}: the {ft['type']} facing {float(ft['facing_deg']):g} deg (offset "
                       f"{float(ft.get('offset', 0) or 0):g} m) is {1 - ex:.0%} buried in other parts; put it where "
                       f"that part's wall is free ({where or 'nowhere - the part is hidden on every side'}) or "
                       f"change the parts so the wall is free")
        for ft in p.get("features") or []:
            if ft.get("x") is None or ft.get("y") is None:
                continue
            pt = (float(ft["x"]), float(ft["y"]))
            if not geom.inside_with_tol(pt, ring, POINT_TOL_M):
                out.append(f"part {p['id']}: the {ft.get('type')} at x={pt[0]:g}, y={pt[1]:g} stands outside the "
                           f"part's outline; give a position on the part (or leave x, y out for its centre)")
    return out


def exposure_text(spec_or_parts, main) -> str:
    """Prompt section: per part, the stretches of each wall that are free (not buried in another part) at mid-height,
    as offsets from the wall's middle, the way features are placed."""
    parts = spec_or_parts["parts"] if isinstance(spec_or_parts, dict) else spec_or_parts
    main = [tuple(p) for p in main]
    live = _live(parts, main)
    return "\n".join(f"- {p['id']}: " + (free_stretches(p, ring, live) or "no free wall (hidden on every side)")
                     for p, ring in live)


def drop_hidden_features(spec_or_parts, main) -> tuple[list, list[str]]:
    """Copy of the parts without the facing features whose own patch of wall is mostly buried (when the part has free
    wall elsewhere, which the facade step was shown), and without point features outside their part.
    Returns (parts, notes). The tool fixes these itself instead of rejecting a whole facade answer."""
    parts = spec_or_parts["parts"] if isinstance(spec_or_parts, dict) else spec_or_parts
    main_t = [tuple(p) for p in main]
    live = _live(parts, main_t)
    rings = {id(p): r for p, r in live}
    out, notes = [], []
    for p in parts:
        ring = rings.get(id(p))
        if ring is None or not p.get("features") or not free_stretches(p, ring, live):
            out.append(p)
            continue
        keep = []
        for ft in p["features"]:
            if ft.get("type") in FACED and ft.get("facing_deg") is not None and not ft.get("around"):
                ex = feature_exposed(p, ring, ft, live)
                if ex is not None and ex < HIDDEN_MAX:
                    notes.append(f"part {p['id']}: dropped the {ft['type']} facing {float(ft['facing_deg']):g} deg "
                                 f"({1 - ex:.0%} buried)")
                    continue
            if ft.get("x") is not None and ft.get("y") is not None and \
                    not geom.inside_with_tol((float(ft["x"]), float(ft["y"])), ring, POINT_TOL_M):
                notes.append(f"part {p['id']}: dropped the {ft.get('type')} outside the part")
                continue
            keep.append(ft)
        out.append(dict(p, features=keep))
    return out, notes
