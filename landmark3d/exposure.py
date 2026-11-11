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


def feature_issues(spec_or_parts, main, fixable_only: bool = False) -> list[str]:
    """Features on faces that are mostly hidden by other parts, and point features outside their part.
    fixable_only: leave out parts hidden on every side (only a change of the volumes can free them)."""
    parts = spec_or_parts["parts"] if isinstance(spec_or_parts, dict) else spec_or_parts
    fx = faces(parts, main)
    main = [tuple(p) for p in main]
    out = []
    for p in parts:
        if p.get("enabled", True) is False or p["id"] not in fx:
            continue
        rows = fx[p["id"]]
        exposed = sorted((r for r in rows if r["exposed"] >= HIDDEN_MAX), key=lambda r: -r["length_m"])
        where = ", ".join(f"{r['facing_deg']:g} deg ({compass(r['facing_deg'])}, {r['length_m']:g} m)"
                          for r in exposed[:4]) or "none - the part is hidden on every side"
        items = [(ft.get("type"), ft.get("facing_deg")) for ft in p.get("features") or []
                 if ft.get("type") in FACED and ft.get("facing_deg") is not None and not ft.get("around")]
        ent = (p.get("facade") or {}).get("entrance")
        if ent and ent.get("facing_deg") is not None:
            items.append(("entrance", ent["facing_deg"]))
        seen = set()
        if fixable_only and not exposed:
            items = []
        for kind, facing in items:
            f = face_for(rows, facing)
            if f is None or f["exposed"] >= HIDDEN_MAX or (kind, f["edge_index"]) in seen:
                continue
            seen.add((kind, f["edge_index"]))
            out.append(f"part {p['id']}: the {kind} facing {float(facing):g} deg is on a wall that is "
                       f"{1 - f['exposed']:.0%} hidden by other parts; put it on an exposed wall of this part "
                       f"(exposed: {where}) or change the parts so that wall is free")
        try:
            ring = geom.footprint_polygon(p["footprint"], main)
        except Exception:
            continue
        for ft in p.get("features") or []:
            if ft.get("x") is None or ft.get("y") is None:
                continue
            pt = (float(ft["x"]), float(ft["y"]))
            if not geom.inside_with_tol(pt, ring, POINT_TOL_M):
                out.append(f"part {p['id']}: the {ft.get('type')} at x={pt[0]:g}, y={pt[1]:g} stands outside the "
                           f"part's outline; give a position on the part (or leave x, y out for its centre)")
    return out


def exposure_text(spec_or_parts, main) -> str:
    """Prompt section: per part, the faces that can be seen (direction, compass, length, share exposed)."""
    fx = faces(spec_or_parts, main)
    lines = []
    for pid, rows in fx.items():
        vis = [r for r in sorted(rows, key=lambda r: -r["length_m"]) if r["exposed"] >= HIDDEN_MAX]
        hid = [r for r in rows if r["exposed"] < HIDDEN_MAX and r["length_m"] >= 3]
        lines.append(f"- {pid}: exposed " + (", ".join(
            f"{r['facing_deg']:g} deg {compass(r['facing_deg'])} {r['length_m']:g} m ({r['exposed']:.0%})"
            for r in vis[:6]) or "none") + ("; hidden " + ", ".join(f"{r['facing_deg']:g} deg" for r in hid[:6])
                                            if hid else ""))
    return "\n".join(lines)
