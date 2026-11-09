"""Pure-Python plan and roof geometry (no Blender). Used by the Blender engine and by tests.

Conventions: plan polygons are lists of (x, y) in metres, x east, y north, counter-clockwise.
Meshes are returned as (verts, faces, face_tags) with verts = [(x, y, z)], faces = [tuple(idx)],
face_tags = ["wall" | "roof" | "trim" | "opening" ...] per face.
"""
from __future__ import annotations

import math

R_EARTH = 6371008.8


# ----------------------------------------------------------------------------- basics
def lonlat_to_local(lon: float, lat: float, lon0: float, lat0: float) -> tuple[float, float]:
    return (R_EARTH * math.radians(lon - lon0) * math.cos(math.radians(lat0)),
            R_EARTH * math.radians(lat - lat0))


def area2(poly) -> float:
    n = len(poly)
    return sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))


def area(poly) -> float:
    return abs(area2(poly)) / 2.0


def ensure_ccw(poly):
    poly = [tuple(p) for p in poly]
    if len(poly) > 1 and poly[0] == poly[-1]:
        poly = poly[:-1]
    clean = []
    for p in poly:
        if not clean or math.dist(p, clean[-1]) > 1e-6:
            clean.append(p)
    if len(clean) > 2 and math.dist(clean[0], clean[-1]) < 1e-6:
        clean.pop()
    return clean if area2(clean) > 0 else clean[::-1]


def centroid(poly) -> tuple[float, float]:
    a = area2(poly)
    if abs(a) < 1e-9:
        return (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
    cx = cy = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        c = x0 * y1 - x1 * y0
        cx += (x0 + x1) * c
        cy += (y0 + y1) * c
    return (cx / (3 * a), cy / (3 * a))


def min_area_rect(poly):
    """Returns (u, v, (umin, umax, vmin, vmax)); u is the long axis."""
    best = None
    for i in range(0, 180):
        th = math.radians(i * 0.5)
        u = (math.cos(th), math.sin(th))
        v = (-u[1], u[0])
        us = [p[0] * u[0] + p[1] * u[1] for p in poly]
        vs = [p[0] * v[0] + p[1] * v[1] for p in poly]
        a = (max(us) - min(us)) * (max(vs) - min(vs))
        if best is None or a < best[0]:
            best = (a, u, v, (min(us), max(us), min(vs), max(vs)))
    _, u, v, ext = best
    if (ext[3] - ext[2]) > (ext[1] - ext[0]):
        u, v = v, (-v[0], -v[1])
        us = [p[0] * u[0] + p[1] * u[1] for p in poly]
        vs = [p[0] * v[0] + p[1] * v[1] for p in poly]
        ext = (min(us), max(us), min(vs), max(vs))
    return u, v, ext


def clip_halfplane(poly, nx, ny, c):
    """Keep the part of poly where nx*x + ny*y <= c."""
    out = []
    n = len(poly)
    for i in range(n):
        cur, prev = poly[i], poly[i - 1]
        dc = nx * cur[0] + ny * cur[1] - c
        dp = nx * prev[0] + ny * prev[1] - c
        if dc <= 0:
            if dp > 0:
                t = dp / (dp - dc)
                out.append((prev[0] + t * (cur[0] - prev[0]), prev[1] + t * (cur[1] - prev[1])))
            out.append(cur)
        elif dp <= 0:
            t = dp / (dp - dc)
            out.append((prev[0] + t * (cur[0] - prev[0]), prev[1] + t * (cur[1] - prev[1])))
    return out


def rect_polygon(cx, cy, w, d, rot_deg=0.0):
    a = math.radians(rot_deg)
    u = (math.cos(a), math.sin(a))
    v = (-u[1], u[0])
    pts = []
    for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        pts.append((cx + u[0] * su * w / 2 + v[0] * sv * d / 2, cy + u[1] * su * w / 2 + v[1] * sv * d / 2))
    return pts


def circle_polygon(cx, cy, r, segments=24):
    return [(cx + r * math.cos(2 * math.pi * i / segments), cy + r * math.sin(2 * math.pi * i / segments))
            for i in range(segments)]


def rounded_rect_polygon(cx, cy, w, d, rot_deg=0.0, r=0.0, corner_segments=6):
    """Rectangle with rounded corners (CCW). r >= min(w, d)/2 gives a stadium (fully rounded short ends)."""
    r = max(0.0, min(r, w / 2, d / 2))
    if r < 1e-3:
        return rect_polygon(cx, cy, w, d, rot_deg)
    a = math.radians(rot_deg)
    u = (math.cos(a), math.sin(a))
    v = (-u[1], u[0])
    hw, hd = w / 2 - r, d / 2 - r
    pts = []
    # corner centres in (u, v) order CCW: (+,-) (+,+) (-,+) (-,-); arcs start at angles -90, 0, 90, 180
    for (su, sv), start in (((1, -1), -90.0), ((1, 1), 0.0), ((-1, 1), 90.0), ((-1, -1), 180.0)):
        ccu, ccv = su * hw, sv * hd
        for k in range(corner_segments + 1):
            ang = math.radians(start + 90.0 * k / corner_segments)
            pu, pv = ccu + r * math.cos(ang), ccv + r * math.sin(ang)
            pts.append((cx + u[0] * pu + v[0] * pv, cy + u[1] * pu + v[1] * pv))
    out = []
    for p in pts:                                    # drop duplicates where straight sides have zero length
        if not out or math.dist(p, out[-1]) > 1e-4:
            out.append(p)
    if math.dist(out[0], out[-1]) < 1e-4:
        out.pop()
    return out


def footprint_polygon(fp, main_poly):
    """Polygon of a part footprint: "main", polygon, rect, rounded_rect, stadium or circle (local metres)."""
    if fp == "main":
        return list(main_poly)
    if "polygon" in fp:
        return ensure_ccw([tuple(p) for p in fp["polygon"]])
    if "rect" in fp:
        r = fp["rect"]
        return rect_polygon(r["cx"], r["cy"], r["w"], r["d"], r.get("rot_deg", 0.0))
    if "rounded_rect" in fp:
        r = fp["rounded_rect"]
        return rounded_rect_polygon(r["cx"], r["cy"], r["w"], r["d"], r.get("rot_deg", 0.0), r.get("r", 0.0))
    if "stadium" in fp:
        r = fp["stadium"]
        return rounded_rect_polygon(r["cx"], r["cy"], r["w"], r["d"], r.get("rot_deg", 0.0), min(r["w"], r["d"]) / 2, 8)
    c = fp["circle"]
    return circle_polygon(c["cx"], c["cy"], c["r"], 24)


def outward_offset(poly, d, miter_limit=3.0):
    """Mitred outward offset of a CCW polygon by d metres (small d: cornices, bands, mullions)."""
    poly = ensure_ccw(poly)
    n = len(poly)
    normals = []
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ln = max(1e-9, math.dist(a, b))
        normals.append(((b[1] - a[1]) / ln, -(b[0] - a[0]) / ln))
    out = []
    for i in range(n):
        n1, n2 = normals[i - 1], normals[i]
        m = (n1[0] + n2[0], n1[1] + n2[1])
        ml = math.hypot(*m)
        if ml < 1e-9:
            m, ml = n2, 1.0
        m = (m[0] / ml, m[1] / ml)
        k = min(miter_limit, 1.0 / max(1e-3, m[0] * n2[0] + m[1] * n2[1]))
        out.append((poly[i][0] + m[0] * d * k, poly[i][1] + m[1] * d * k))
    return out


def dist_to_polygon(pt, poly) -> float:
    best = float("inf")
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / L2))
        best = min(best, math.dist(pt, (a[0] + dx * t, a[1] + dy * t)))
    return best


def inside_with_tol(pt, poly, tol=0.0) -> bool:
    return point_in_polygon(pt, poly) or (tol > 0 and dist_to_polygon(pt, poly) <= tol)


def sample_points(poly, target=150):
    """Points covering a polygon: a grid inside it plus its vertices pulled slightly inwards."""
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    a = abs(area(poly))
    step = max(0.5, math.sqrt(max(a, 1e-6) / target))
    pts = []
    y = min(ys) + step / 2
    while y < max(ys):
        x = min(xs) + step / 2
        while x < max(xs):
            if point_in_polygon((x, y), poly):
                pts.append((x, y))
            x += step
        y += step
    # the outline, every ~1 m, 0.3 m inside: overhangs show up at the edges first
    inner, _ = inward_offset(ensure_ccw(poly), min(0.3, inradius_estimate(poly) * 0.5))
    ring = inner or poly
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        k = max(1, int(math.dist(a, b) // 1.0))
        for j in range(k):
            t = j / k
            pts.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return pts


def _line_isect(a, b):
    (p, d), (q, e) = a, b
    cr = d[0] * e[1] - d[1] * e[0]
    if abs(cr) < 1e-12:
        return q
    t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / cr
    return (p[0] + d[0] * t, p[1] + d[1] * t)


def inward_offset(poly, dist):
    """Fixed-distance inward offset of a CCW polygon with edge collapse (straight-skeleton step).

    Returns (inner_polygon, per_edge) where per_edge[j] = (start, end) of outer edge j on the inner
    polygon (start == end for a collapsed edge). Returns (None, None) if the polygon vanishes.
    """
    n = len(poly)
    lines = []
    for j in range(n):
        a, b = poly[j], poly[(j + 1) % n]
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        if ln < 1e-9:
            return None, None
        d = ((b[0] - a[0]) / ln, (b[1] - a[1]) / ln)
        nrm = (-d[1], d[0])
        lines.append(((a[0] + nrm[0] * dist, a[1] + nrm[1] * dist), d))
    active = list(range(n))
    changed = True
    while changed and len(active) > 2:
        changed = False
        m = len(active)
        for k in range(m):
            p, a, q = active[k - 1], active[k], active[(k + 1) % m]
            st = _line_isect(lines[p], lines[a])
            en = _line_isect(lines[a], lines[q])
            d = lines[a][1]
            if (en[0] - st[0]) * d[0] + (en[1] - st[1]) * d[1] <= 1e-6:
                active.pop(k)
                changed = True
                break
    if len(active) < 3:
        return None, None
    m = len(active)
    seg = {}
    for k in range(m):
        p, a, q = active[k - 1], active[k], active[(k + 1) % m]
        seg[a] = (_line_isect(lines[p], lines[a]), _line_isect(lines[a], lines[q]))
    inner = [seg[a][0] for a in active]
    if area2(inner) <= 1e-6:
        return None, None
    per_edge = []
    for j in range(n):
        if j in seg:
            per_edge.append(seg[j])
        else:
            k = 1
            while (j + k) % n not in seg:
                k += 1
            hip = seg[(j + k) % n][0]
            per_edge.append((hip, hip))
    return inner, per_edge


def point_in_polygon(pt, poly, tol=1e-6) -> bool:
    """Inside or on the boundary (within tol)."""
    x, y = pt
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / l2))
        if math.hypot(x - x1 - t * dx, y - y1 - t * dy) <= tol:
            return True
    inside = False
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def offset_is_valid(poly, inner, per_edge) -> bool:
    """An inward offset is usable only if every new point lies inside the original polygon."""
    pts = list(inner) + [p for seg in per_edge for p in seg]
    return all(point_in_polygon(p, poly, 1e-4) for p in pts)


def inradius_estimate(poly):
    """Largest inward offset that keeps the polygon (bisection on inward_offset)."""
    lo, hi = 0.0, max(0.01, math.sqrt(area(poly)))
    for _ in range(30):
        mid = (lo + hi) / 2
        inner, _ = inward_offset(poly, mid)
        if inner is None:
            hi = mid
        else:
            lo = mid
    return lo


# ----------------------------------------------------------------------------- mesh builder
class Mesh:
    def __init__(self):
        self.verts: list[tuple[float, float, float]] = []
        self.faces: list[tuple[int, ...]] = []
        self.tags: list[str] = []
        self._cache: dict = {}

    def v(self, x, y, z):
        key = (round(x, 5), round(y, 5), round(z, 5))
        idx = self._cache.get(key)
        if idx is None:
            idx = len(self.verts)
            self.verts.append((float(x), float(y), float(z)))
            self._cache[key] = idx
        return idx

    def f(self, idxs, tag):
        idxs = list(idxs)
        clean = [i for k, i in enumerate(idxs) if i != idxs[k - 1]]
        if len(set(clean)) >= 3:
            self.faces.append(tuple(clean))
            self.tags.append(tag)

    def triangles(self) -> int:
        return sum(len(f) - 2 for f in self.faces)


# ----------------------------------------------------------------------------- roofs
def roof_height_fn(poly, roof):
    """z(x, y) on the roof plane for boundary-following roofs (gable, shed); None for others."""
    t = roof.get("type", "flat")
    eave = roof["eave_z"]
    rise = roof.get("height", 0.0)
    u, v, (umin, umax, vmin, vmax) = min_area_rect(poly)
    if roof.get("ridge_axis") == "short":
        u, v = v, (-u[0], -u[1])
        us = [p[0] * u[0] + p[1] * u[1] for p in poly]
        vs = [p[0] * v[0] + p[1] * v[1] for p in poly]
        umin, umax, vmin, vmax = min(us), max(us), min(vs), max(vs)
    vc = (vmin + vmax) / 2
    half = max(1e-6, (vmax - vmin) / 2)
    if t == "gable":
        return lambda x, y: eave + rise * max(0.0, 1.0 - abs((x * v[0] + y * v[1]) - vc) / half), (v, vc)
    if t == "shed":
        return lambda x, y: eave + rise * ((x * v[0] + y * v[1]) - vmin) / (2 * half), None
    return None, None


def insert_ridge_points(poly, v, vc):
    """Insert the points where the ridge line (v . p = vc) crosses the polygon boundary."""
    out = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        out.append(a)
        da = a[0] * v[0] + a[1] * v[1] - vc
        db = b[0] * v[0] + b[1] * v[1] - vc
        if da * db < 0:
            t = da / (da - db)
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def is_tapered(taper) -> bool:
    return bool(taper) and (abs(float(taper.get("top_scale", 1.0)) - 1.0) > 1e-3 or any(taper.get("top_shift") or []))


def taper_factor(taper, t: float) -> float:
    """Share of the full taper reached at height fraction t (0 = base, 1 = top) for the profile."""
    t = max(0.0, min(1.0, t))
    prof = (taper or {}).get("profile", "linear")
    if prof == "ease_in":                 # stays wide, narrows near the top (bud / bullet)
        return t * t
    if prof == "ease_out":                # narrows fast at the bottom, then straight (skirt)
        return 1.0 - (1.0 - t) ** 2
    return t


def ring_at(poly, base_z, top_z, taper, z):
    """The outline of a (possibly tapered) part at height z: scaled about the base centroid and shifted."""
    if not is_tapered(taper):
        return list(poly)
    t = 0.0 if top_z <= base_z else (z - base_z) / (top_z - base_z)
    f = taper_factor(taper, t)
    k = 1.0 + (float(taper.get("top_scale", 1.0)) - 1.0) * f
    sx, sy = (list(taper.get("top_shift") or [0.0, 0.0]) + [0.0, 0.0])[:2]
    c = centroid(poly)
    return [(c[0] + (x - c[0]) * k + sx * f, c[1] + (y - c[1]) * k + sy * f) for x, y in poly]


def taper_heights(base_z, top_z, taper):
    segs = 1 if (taper or {}).get("profile", "linear") == "linear" and not (taper or {}).get("top_shift") else 8
    return [base_z + (top_z - base_z) * j / segs for j in range(segs + 1)]


MAX_SKIRTS = 3


def roof_skirts(roof) -> list[dict]:
    """The usable skirt tiers of a roof (bottom first), with numeric fields."""
    out = []
    for s in (roof or {}).get("skirts") or []:
        if not isinstance(s, dict):
            continue
        try:
            depth, rise = float(s.get("depth", 0)), float(s.get("rise", 0))
            gap, ov = float(s.get("gap", 0) or 0), s.get("overhang")
        except (TypeError, ValueError):
            continue
        if depth > 0.2 and rise >= 0:
            out.append({"depth": depth, "rise": rise, "gap": max(0.0, gap),
                        "overhang": None if ov is None else max(0.0, float(ov))})
    return out[:MAX_SKIRTS]


def roof_total_height(roof) -> float:
    """Height of everything above the wall top: skirt tiers (rise + clerestory gap) and the top roof."""
    return sum(s["rise"] + s["gap"] for s in roof_skirts(roof)) + float((roof or {}).get("height", 0) or 0)


def corner_lifts(ring, up: float, min_turn_deg: float = 25.0) -> list[float]:
    """Upturned eaves: lift `up` metres at the convex corners of a CCW ring (0 elsewhere)."""
    n = len(ring)
    if not up or up <= 0:
        return [0.0] * n
    out = []
    for i in range(n):
        p, a, q = ring[i - 1], ring[i], ring[(i + 1) % n]
        e1, e2 = (a[0] - p[0], a[1] - p[1]), (q[0] - a[0], q[1] - a[1])
        cross = e1[0] * e2[1] - e1[1] * e2[0]
        l1, l2 = math.hypot(*e1), math.hypot(*e2)
        turn = math.degrees(math.asin(max(-1.0, min(1.0, cross / max(1e-9, l1 * l2)))))
        out.append(float(up) if turn >= min_turn_deg else 0.0)
    return out


def _plain_walls(poly, base_z, top_z, mesh: Mesh):
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        mesh.f((mesh.v(a[0], a[1], base_z), mesh.v(b[0], b[1], base_z), mesh.v(b[0], b[1], top_z),
                mesh.v(a[0], a[1], top_z)), "wall")


def _soffit(ring, outer, z_ring, z_outer, mesh: Mesh):
    """Underside of projecting eaves: from the wall line out to the eave edge (faces point down)."""
    n = len(ring)
    for i in range(n):
        a, b, oa, ob = ring[i], ring[(i + 1) % n], outer[i], outer[(i + 1) % n]
        mesh.f((mesh.v(a[0], a[1], z_ring(i)), mesh.v(oa[0], oa[1], z_outer(i)),
                mesh.v(ob[0], ob[1], z_outer((i + 1) % n)), mesh.v(b[0], b[1], z_ring((i + 1) % n))), "trim")


def build_skirt(ring, z, skirt, mesh: Mesh, overhang=0.0, upturn=0.0):
    """One lower roof tier (a "skirt"): a sloped band from the wall line (or the eave edge when it overhangs) up to
    the ring inset by `depth`, rising `rise` m. Returns the inset ring (where the clerestory / upper walls stand),
    or None when the inset does not fit the plan."""
    depth, rise = skirt["depth"], skirt["rise"]
    inner, per_edge = inward_offset(ring, depth)
    if inner is None or not offset_is_valid(ring, inner, per_edge):
        return None
    ov = skirt["overhang"] if skirt.get("overhang") is not None else overhang
    outer = outward_offset(ring, ov) if ov > 0 else list(ring)
    drop = ov * rise / depth                          # the slope continues out to the eave edge
    lift = corner_lifts(outer, upturn)
    n = len(ring)
    zt = z + rise
    for j in range(n):
        oa, ob = outer[j], outer[(j + 1) % n]
        ia, ib = per_edge[j]
        za, zb = z - drop + lift[j], z - drop + lift[(j + 1) % n]
        if math.dist(ia, ib) < 1e-6:
            mesh.f((mesh.v(oa[0], oa[1], za), mesh.v(ob[0], ob[1], zb), mesh.v(ia[0], ia[1], zt)), "roof")
        else:
            mesh.f((mesh.v(oa[0], oa[1], za), mesh.v(ob[0], ob[1], zb), mesh.v(ib[0], ib[1], zt),
                    mesh.v(ia[0], ia[1], zt)), "roof")
    if ov > 0:
        _soffit(ring, outer, lambda i: z, lambda i: z - drop + lift[i], mesh)
    return inner


def build_part(poly, base_z, eave_z, roof, mesh: Mesh, taper=None):
    """Walls + roof of one part. roof = {type, height, ridge_axis?, overhang?, parapet?, upturn?, skirts?}.
    taper = {top_scale, profile linear|ease_in|ease_out, top_shift [dx, dy]}: the walls narrow (or widen) upwards
    and the roof sits on the top outline.
    overhang: eaves project this far past the walls (with a soffit underneath).
    upturn: the eave corners curl up by this much (East Asian roofs).
    skirts: lower roof tiers, bottom first: [{depth, rise, gap, overhang?}] - each slopes in by `depth` while rising
    `rise`, then a clerestory / upper wall of `gap` m stands on the inset ring; the roof `type` crowns the last one
    (tiered market halls, pagoda storeys, basilica aisles)."""
    poly = ensure_ccw(poly)
    roof = dict(roof or {"type": "flat"})
    skirts = roof_skirts(roof)
    if skirts and not is_tapered(taper):
        s = skirts[0]
        inner = build_skirt(poly, eave_z, s, mesh, float(roof.get("overhang", 0) or 0), float(roof.get("upturn", 0) or 0))
        if inner is not None:
            _plain_walls(poly, base_z, eave_z, mesh)
            rest = dict(roof, skirts=[dict(k) for k in skirts[1:]])
            build_part(inner, eave_z + s["rise"], eave_z + s["rise"] + s["gap"], rest, mesh)
            return
        roof["skirts"] = []                       # does not fit: plain roof
    if is_tapered(taper):
        zs = taper_heights(base_z, eave_z, taper)
        rings = [ring_at(poly, base_z, eave_z, taper, z) for z in zs]
        n = len(poly)
        for j in range(len(zs) - 1):
            r0, r1 = rings[j], rings[j + 1]
            for i in range(n):
                a0, b0, a1, b1 = r0[i], r0[(i + 1) % n], r1[i], r1[(i + 1) % n]
                mesh.f((mesh.v(a0[0], a0[1], zs[j]), mesh.v(b0[0], b0[1], zs[j]), mesh.v(b1[0], b1[1], zs[j + 1]),
                        mesh.v(a1[0], a1[1], zs[j + 1])), "wall")
        build_part(rings[-1], eave_z, eave_z, roof, mesh)           # zero-height walls are dropped
        return
    rtype = roof.get("type", "flat")
    ov, up = max(0.0, float(roof.get("overhang", 0) or 0)), max(0.0, float(roof.get("upturn", 0) or 0))
    if (ov > 0.05 or up > 0.05) and rtype in ("hip", "pyramid", "flat", "gable", "shed"):
        if _eaves_roof(poly, base_z, eave_z, roof, mesh, ov, up):
            return
    roof = dict(roof, eave_z=eave_z)
    zfn, ridge = roof_height_fn(poly, roof)
    ring = poly
    if rtype == "gable" and ridge is not None:
        ring = insert_ridge_points(poly, ridge[0], ridge[1])
    n = len(ring)
    # walls (top follows the roof plane for gable/shed)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        za = zfn(*a) if zfn else eave_z
        zb = zfn(*b) if zfn else eave_z
        mesh.f((mesh.v(a[0], a[1], base_z), mesh.v(b[0], b[1], base_z), mesh.v(b[0], b[1], zb),
                mesh.v(a[0], a[1], za)), "wall")
    if rtype in ("gable", "shed") and zfn:
        if rtype == "gable":
            v, vc = ridge
            halves = [clip_halfplane(ring, v[0], v[1], vc), clip_halfplane(ring, -v[0], -v[1], -vc)]
        else:
            halves = [ring]
        for h in halves:
            if len(h) >= 3:
                mesh.f([mesh.v(x, y, zfn(x, y)) for x, y in h], "roof")
        return
    if rtype == "flat" or rtype == "none":
        mesh.f([mesh.v(x, y, eave_z) for x, y in poly], "roof")
        para = roof.get("parapet", 0.0)
        if para > 0:
            thick = min(0.3, inradius_estimate(poly) * 0.5)
            inner, per_edge = inward_offset(poly, thick)
            if inner:
                zt = eave_z + para
                for i in range(n):
                    a, b = poly[i], poly[(i + 1) % n]
                    ia, ib = per_edge[i]
                    mesh.f((mesh.v(a[0], a[1], eave_z), mesh.v(b[0], b[1], eave_z),
                            mesh.v(b[0], b[1], zt), mesh.v(a[0], a[1], zt)), "wall")          # outer face
                    mesh.f((mesh.v(a[0], a[1], zt), mesh.v(b[0], b[1], zt),
                            mesh.v(ib[0], ib[1], zt), mesh.v(ia[0], ia[1], zt)), "trim")       # coping
                    if math.dist(ia, ib) > 1e-6:
                        mesh.f((mesh.v(ib[0], ib[1], eave_z), mesh.v(ia[0], ia[1], eave_z),
                                mesh.v(ia[0], ia[1], zt), mesh.v(ib[0], ib[1], zt)), "wall")   # inner face
        return
    if rtype == "pyramid":
        c = centroid(poly)
        apex = mesh.v(c[0], c[1], eave_z + roof.get("height", 3.0))
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            mesh.f((mesh.v(a[0], a[1], eave_z), mesh.v(b[0], b[1], eave_z), apex), "roof")
        return
    if rtype == "dome":
        c = centroid(poly)
        r = sum(math.dist(c, p) for p in poly) / n
        h = roof.get("height", r)
        segs, rings = max(12, min(32, n)), 6
        prev = [mesh.v(x, y, eave_z) for x, y in circle_polygon(c[0], c[1], r, segs)]
        mesh.f([mesh.v(x, y, eave_z) for x, y in poly], "trim")      # drum top under the dome
        for k in range(1, rings + 1):
            phi = (math.pi / 2) * k / rings
            rr = r * math.cos(phi)
            zz = eave_z + h * math.sin(phi)
            if k == rings:
                top = mesh.v(c[0], c[1], eave_z + h)
                for i in range(segs):
                    mesh.f((prev[i], prev[(i + 1) % segs], top), "roof")
            else:
                cur = [mesh.v(x, y, zz) for x, y in circle_polygon(c[0], c[1], rr, segs)]
                for i in range(segs):
                    mesh.f((prev[i], prev[(i + 1) % segs], cur[(i + 1) % segs], cur[i]), "roof")
                prev = cur
        return
    # hip (default for unknown): straight-skeleton approximation by stepped inward offsets
    build_hip(poly, eave_z, roof.get("height"), mesh, roof.get("pitch_deg"))


def _plane_fn(poly, roof, eave_z, rise):
    """Unclamped roof plane for gable / shed (continues past the walls out to the eave edge)."""
    u, v, (umin, umax, vmin, vmax) = min_area_rect(poly)
    if roof.get("ridge_axis") == "short":
        u, v = v, (-u[0], -u[1])
        vs = [p[0] * v[0] + p[1] * v[1] for p in poly]
        vmin, vmax = min(vs), max(vs)
    vc, half = (vmin + vmax) / 2, max(1e-6, (vmax - vmin) / 2)
    if roof.get("type") == "gable":
        return (lambda x, y: eave_z + rise * (1.0 - abs((x * v[0] + y * v[1]) - vc) / half)), (v, vc)
    return (lambda x, y: eave_z + rise * ((x * v[0] + y * v[1]) - vmin) / (2 * half)), None


def _eaves_roof(poly, base_z, eave_z, roof, mesh: Mesh, ov: float, up: float) -> bool:
    """Roof whose eaves project `ov` m past the walls (soffit underneath) and whose corners curl up by `up` m."""
    rtype = roof.get("type", "flat")
    h = float(roof.get("height", 0) or 0)
    outer = outward_offset(poly, ov) if ov > 0 else list(poly)
    n = len(poly)
    if rtype in ("gable", "shed"):
        zfn, ridge = _plane_fn(poly, roof, eave_z, h)
        ring, oring = poly, outer
        if ridge is not None:
            ring, oring = insert_ridge_points(poly, *ridge), insert_ridge_points(outer, *ridge)
            if len(ring) != len(oring):
                ring, oring = poly, outer
        for i in range(len(ring)):
            a, b = ring[i], ring[(i + 1) % len(ring)]
            mesh.f((mesh.v(a[0], a[1], base_z), mesh.v(b[0], b[1], base_z), mesh.v(b[0], b[1], zfn(*b)),
                    mesh.v(a[0], a[1], zfn(*a))), "wall")
        if ridge is not None:
            v, vc = ridge
            halves = [clip_halfplane(oring, v[0], v[1], vc), clip_halfplane(oring, -v[0], -v[1], -vc)]
        else:
            halves = [oring]
        for hp in halves:
            if len(hp) >= 3:
                mesh.f([mesh.v(x, y, zfn(x, y)) for x, y in hp], "roof")
        if ov > 0:
            _soffit(ring, oring, lambda i: zfn(*ring[i]), lambda i: zfn(*oring[i]) - 0.05, mesh)
        return True
    _plain_walls(poly, base_z, eave_z, mesh)
    lift = corner_lifts(outer, up)
    if rtype == "flat":
        fz = eave_z - 0.25
        mesh.f([mesh.v(x, y, eave_z) for x, y in outer], "roof")
        for i in range(n):                                  # fascia
            a, b = outer[i], outer[(i + 1) % n]
            mesh.f((mesh.v(a[0], a[1], fz), mesh.v(b[0], b[1], fz), mesh.v(b[0], b[1], eave_z),
                    mesh.v(a[0], a[1], eave_z)), "trim")
        if ov > 0:
            _soffit(poly, outer, lambda i: fz, lambda i: fz, mesh)
        return True
    if rtype == "pyramid":
        c = centroid(poly)
        r = sum(math.dist(c, p) for p in poly) / n
        h = h or 3.0
        drop = ov * h / max(0.1, r)
        apex = mesh.v(c[0], c[1], eave_z + h)
        for i in range(n):
            a, b = outer[i], outer[(i + 1) % n]
            mesh.f((mesh.v(a[0], a[1], eave_z - drop + lift[i]), mesh.v(b[0], b[1], eave_z - drop + lift[(i + 1) % n]),
                    apex), "roof")
    else:
        r_in = inradius_estimate(poly)
        if r_in <= 0.05:
            return False
        if not h:
            h = r_in * math.tan(math.radians(roof.get("pitch_deg") or 25.0))
        drop = ov * h / r_in
        build_hip(outer, eave_z - drop, h + drop, mesh, lift=lift)
    if ov > 0:
        _soffit(poly, outer, lambda i: eave_z, lambda i: eave_z - drop + lift[i] - 0.05, mesh)
    return True


def build_hip(poly, eave_z, height, mesh: Mesh, pitch_deg=None, lift=None):
    r_in = inradius_estimate(poly)
    lift = lift or [0.0] * len(poly)
    if r_in <= 0.05:
        mesh.f([mesh.v(x, y, eave_z) for x, y in poly], "roof")
        return
    if height is None:
        height = r_in * math.tan(math.radians(pitch_deg or 25.0))
    slope = height / r_in
    steps = 4
    ring = poly
    d_done = 0.0
    for k in range(1, steps + 1):
        d = r_in * k / steps * (0.999 if k == steps else 1.0)
        inner, per_edge = inward_offset(poly, d)
        if inner is None or not offset_is_valid(poly, inner, per_edge):
            break                     # concave plans: stop before the offset leaves the footprint; flat cap below
        # map edges of the previous ring: use the offset from the ORIGINAL polygon (planar faces per edge)
        prev_inner, prev_edges = (poly, None) if k == 1 else inward_offset(poly, d_done)
        z0, z1 = eave_z + slope * d_done, eave_z + slope * d
        if prev_edges is None:
            outer_segs = [(poly[j], poly[(j + 1) % len(poly)]) for j in range(len(poly))]
        else:
            outer_segs = prev_edges
        for j in range(len(poly)):
            (oa, ob), (ia, ib) = outer_segs[j], per_edge[j]
            if math.dist(oa, ob) < 1e-6:
                continue
            za, zb = (z0 + lift[j], z0 + lift[(j + 1) % len(poly)]) if k == 1 else (z0, z0)
            if math.dist(ia, ib) < 1e-6:
                mesh.f((mesh.v(oa[0], oa[1], za), mesh.v(ob[0], ob[1], zb), mesh.v(ia[0], ia[1], z1)), "roof")
            else:
                mesh.f((mesh.v(oa[0], oa[1], za), mesh.v(ob[0], ob[1], zb), mesh.v(ib[0], ib[1], z1),
                        mesh.v(ia[0], ia[1], z1)), "roof")
        ring = inner
        d_done = d
    mesh.f([mesh.v(x, y, eave_z + slope * d_done) for x, y in ring], "roof")


# ----------------------------------------------------------------------------- facade openings
def arch_outline(width, height, segments=8):
    spring = max(0.0, height - width / 2)
    r = width / 2
    pts = [(-r, 0.0), (-r, spring)]
    for i in range(1, segments):
        a = math.pi - math.pi * i / segments
        pts.append((r * math.cos(a), spring + r * math.sin(a)))
    pts += [(r, spring), (r, 0.0)]
    return pts


def _slots(edges, per, bay, w):
    """Opening centres every `bay` metres along the outline (curves included), skipping sharp corners."""
    count = int((per - 0.5) // bay)
    if count < 1:
        return []
    sp = per / count
    out = []
    for c in range(count):
        s = (c + 0.5) * sp
        _, d0, _ = _point_at(edges, max(0.0, s - w / 2))
        _, d1, _ = _point_at(edges, min(per, s + w / 2))
        if d0[0] * d1[0] + d0[1] * d1[1] < 0.9:
            continue
        p, t, out_n = _point_at(edges, s)
        out.append((s, p, t, out_n))
    return out


def _entrance_spot(edges, per, ent):
    """Arc-length position of the main entrance: middle of the edge facing `facing_deg`, else of the longest edge."""
    if ent.get("facing_deg") is not None:
        f = math.radians(float(ent["facing_deg"]))
        want = (math.cos(f), math.sin(f))

        def score(e):
            a, b, ln, _ = e
            n = ((b[1] - a[1]) / ln, -(b[0] - a[0]) / ln)
            return (n[0] * want[0] + n[1] * want[1]) * min(ln, 30.0)
        a, b, ln, s0 = max(edges, key=score)
    else:
        a, b, ln, s0 = max(edges, key=lambda e: e[2])
    return s0 + ln / 2


def facade_openings(poly, base_z, top_z, facade, mesh: Mesh, budget_left: int, taper=None):
    """Openings per floor (recessed-looking dark panels 5 cm in front of the wall).

    facade keys: opening_type rect|arch|square|storefront|curtain_wall, bay_spacing, opening_width, opening_height,
    level_height, sill, pediment, awning; floors: [{from, to, <same keys>}] overrides per floor (0 = ground);
    ground: shorthand for floor 0; entrance: {width, height, facing_deg?} - a large arched portal."""
    if not facade:
        return 0
    if facade.get("opening_type") == "curtain_wall":
        return curtain_wall(poly, base_z, top_z, facade, mesh, budget_left, taper)
    if not facade.get("openings", True):
        return 0
    if is_tapered(taper):                              # punched windows on a tapered wall: one ring per floor
        level_h = float(facade.get("level_height", 3.5))
        made = 0
        lv = 0
        while base_z + (lv + 1) * level_h <= top_z + 1e-6:
            z0 = base_z + lv * level_h
            ring = ring_at(poly, base_z, top_z, taper, z0 + level_h / 2)
            made += facade_openings(ring, z0, z0 + level_h + 0.6, dict(facade, floors=[
                dict(r, **{"from": 0, "to": 0}) for r in (facade.get("floors") or [])
                if int(r.get("from", 0)) <= lv <= int(r.get("to", r.get("from", 0)))], entrance=None,
                ground=facade.get("ground") if lv == 0 else None), mesh, max(0, budget_left - made * 2))
            lv += 1
        return made
    lay = facade_layout(poly, base_z, top_z, facade)
    tris = 0
    made = 0
    ent = lay["entrance"]
    if ent:
        p, t, out = ent["p"], ent["t"], ent["o"]
        for (ww, hh, off, tag) in ((ent["w"] + 1.0, ent["h"] + 0.6, 0.06, "trim"), (ent["w"], ent["h"], 0.09, "opening")):
            shape = arch_outline(ww, hh, 12)
            cx, cy = p[0] + out[0] * off, p[1] + out[1] * off
            mesh.f([mesh.v(cx + t[0] * x, cy + t[1] * x, base_z + 0.02 + z) for x, z in shape], tag)
            tris += len(shape) - 2
        made += 1
    for win in lay["windows"]:
        if tris >= budget_left:
            break
        p, t, out, w, h, z0 = win["p"], win["t"], win["o"], win["w"], win["h"], win["z0"]
        shape = arch_outline(w, h) if win["kind"] == "arch" else [(-w / 2, 0), (w / 2, 0), (w / 2, h), (-w / 2, h)]
        cx, cy = p[0] + out[0] * 0.05, p[1] + out[1] * 0.05
        mesh.f([mesh.v(cx + t[0] * x, cy + t[1] * x, z0 + z) for x, z in shape], "opening")
        tris += len(shape) - 2
        hw = win["frame_half"]                           # pediment / awning never wider than the bay allows
        if win["pediment"]:
            zb, ph = z0 + h + 0.12, 0.4
            mesh.f((mesh.v(cx - t[0] * hw + out[0] * 0.02, cy - t[1] * hw + out[1] * 0.02, zb),
                    mesh.v(cx + t[0] * hw + out[0] * 0.02, cy + t[1] * hw + out[1] * 0.02, zb),
                    mesh.v(cx + out[0] * 0.02, cy + out[1] * 0.02, zb + ph)), "trim")
            tris += 1
        if win["awning"]:
            depth = win["awning_depth"]
            za, zl = z0 + h + 0.45, z0 + h + 0.05
            a0 = (cx - t[0] * hw, cy - t[1] * hw)
            a1 = (cx + t[0] * hw, cy + t[1] * hw)
            mesh.f((mesh.v(a0[0] + out[0] * depth, a0[1] + out[1] * depth, zl),
                    mesh.v(a1[0] + out[0] * depth, a1[1] + out[1] * depth, zl),
                    mesh.v(a1[0], a1[1], za), mesh.v(a0[0], a0[1], za)), "accent")
            tris += 2
        made += 1
    return made


# ----------------------------------------------------------------------------- facade layout (one grid per facade)
RUN_TURN_DEG = 20.0          # an outline turning more than this starts a new facade
PILASTER_GAP = 0.15          # clear distance between a window frame and a pilaster


def facade_runs(poly, max_turn_deg: float = RUN_TURN_DEG):
    """Split an outline into facades: maximal runs of edges with no sharp turn (a curved front is one facade).
    Each run: {"edges": [(a, b, ln, s0)], "L": length, "closed": bool}."""
    poly = ensure_ccw(poly)
    n = len(poly)
    E = []
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ln = math.dist(a, b)
        if ln > 1e-6:
            E.append((a, b, ln, ((b[0] - a[0]) / ln, (b[1] - a[1]) / ln)))
    if not E:
        return []

    def turn(i):
        d0, d1 = E[i - 1][3], E[i][3]
        return math.degrees(math.acos(max(-1.0, min(1.0, d0[0] * d1[0] + d0[1] * d1[1]))))
    corners = [i for i in range(len(E)) if turn(i) > max_turn_deg]
    if not corners:
        order, closed = [list(range(len(E)))], True
    else:
        order, closed = [], False
        for k, c in enumerate(corners):
            nxt = corners[(k + 1) % len(corners)]
            idx, i = [], c
            while True:
                idx.append(i)
                i = (i + 1) % len(E)
                if i == nxt:
                    break
            order.append(idx)
    runs = []
    for idx in order:
        edges, s0 = [], 0.0
        for i in idx:
            a, b, ln, _ = E[i]
            edges.append((a, b, ln, s0))
            s0 += ln
        runs.append({"edges": edges, "L": s0, "closed": closed})
    return runs


def facade_layout(poly, base_z, top_z, facade):
    """Where every punched-facade element goes. One bay grid per facade run, symmetric about the run's centre;
    pilasters on bay boundaries, windows at bay centres (narrower than a bay minus a pilaster), the entrance in
    the central bay(s), heights clear of string courses and the cornice. Returns elements plus any issues."""
    level_h = float(facade.get("level_height", 3.5))
    bay = max(1.0, float(facade.get("bay_spacing", 4.0)))
    cor = float(facade.get("cornice", 0) or 0)
    pl = facade.get("pilasters")
    pl = (pl if isinstance(pl, dict) else {}) if pl else None
    pil_w = float(pl.get("width", 0.7)) if pl else 0.0
    pil_every = max(1, int(pl.get("every", 1))) if pl else 1
    bands = bool(facade.get("bands"))
    levels = max(1, int((top_z - base_z - cor + 0.3) // level_h))
    rules = [dict(r) for r in (facade.get("floors") or [])]
    if facade.get("ground") and not any(int(r.get("from", 0)) <= 0 <= int(r.get("to", r.get("from", 0))) for r in rules):
        rules.append(dict(facade["ground"], **{"from": 0, "to": 0}))
    out = {"runs": [], "windows": [], "pilasters": [], "entrance": None, "issues": [], "bay": bay}
    runs = [r for r in facade_runs(poly) if r["L"] >= 0.7 * bay]
    if not runs:
        return out
    ent = facade.get("entrance")
    ent_run = None
    if ent:
        if ent.get("facing_deg") is not None:
            f = math.radians(float(ent["facing_deg"]))
            want = (math.cos(f), math.sin(f))

            def facing_score(r):
                p_, t_, o_ = _point_at(r["edges"], r["L"] / 2)
                return (o_[0] * want[0] + o_[1] * want[1]) * min(r["L"], 30.0)
            ent_run = max(range(len(runs)), key=lambda k: facing_score(runs[k]))
        else:
            ent_run = max(range(len(runs)), key=lambda k: runs[k]["L"])
    for ri, run in enumerate(runs):
        n = max(1, int(round(run["L"] / bay)))
        b = run["L"] / n
        out["runs"].append({"L": run["L"], "n": n, "b": b})
        # entrance: the central bay(s) of its run
        ent_bays = set()
        ent_top = base_z
        if ent and ri == ent_run:
            need = float(ent.get("width", 6.0)) + pil_w + 2 * PILASTER_GAP
            k = min(n, max(1, math.ceil(need / b)))
            if (n - k) % 2:
                k = k + 1 if k < n else k - 1
            k = max(1, k)
            first = (n - k) // 2
            ent_bays = set(range(first, first + k))
            span = k * b
            w = min(float(ent.get("width", 6.0)), span - pil_w - 2 * PILASTER_GAP)
            h = min(float(ent.get("height", level_h * 1.6)), top_z - base_z - cor - 0.8)
            if w >= 1.0 and h >= 2.0:
                p_, t_, o_ = _point_at(run["edges"], (first + k / 2) * b)
                ent_top = base_z + h + 0.6
                out["entrance"] = {"run": ri, "s": (first + k / 2) * b, "w": w, "h": h, "p": p_, "t": t_, "o": o_,
                                   "z_top": ent_top, "bays": sorted(ent_bays)}
            else:
                ent_bays = set()
        # pilasters on bay boundaries (corners included), none inside the entrance span
        if pl:
            bounds = range(n) if run["closed"] else range(n + 1)
            for i in bounds:
                if i % pil_every:
                    continue
                if any(j in ent_bays for j in (i - 1, i)) and (i - 1 in ent_bays and i in ent_bays):
                    continue
                s_ = i * b
                out["pilasters"].append({"run": ri, "s": s_, "w": pil_w, "z0": base_z, "z1": top_z - cor,
                                         "pt": _point_at(run["edges"], min(max(s_, 1e-6), run["L"] - 1e-6))})
        # windows at bay centres
        clear = b - pil_w - 2 * PILASTER_GAP
        for lv in range(levels):
            rule = next((r for r in rules if int(r.get("from", 0)) <= lv <= int(r.get("to", r.get("from", 0)))), None)
            kind = (rule or {}).get("opening_type") or facade.get("opening_type", "rect")
            if kind == "none":
                continue
            src = rule if rule and rule.get("opening_type") else facade
            every = max(1, int(round(float(src.get("bay_spacing", bay)) / bay))) if src.get("bay_spacing") else 1
            if kind == "storefront":
                w = float(src.get("opening_width", b * 0.8))
                h = float(src.get("opening_height", level_h * 0.8))
                sill = float(src.get("sill", 0.05))
            else:
                w = float(src.get("opening_width", b * 0.45))
                h = float(src.get("opening_height", w if kind == "square" else min(2.4, level_h * 0.62)))
                sill = float(src.get("sill", 0.15 if lv == 0 else 0.9))
            if kind == "square":
                h = w
            if w > clear:
                out["issues"].append(f"floor {lv}: opening width {w:g} m > clear bay {clear:.2f} m - narrowed")
                w = clear
            z_floor = base_z + lv * level_h
            ceiling = min(z_floor + level_h - (0.3 if bands else 0.15), top_z - cor - 0.2)
            pediment, awning = bool(src.get("pediment")), bool(src.get("awning"))
            head = 0.55 if pediment else (0.5 if awning else 0.0)
            z0 = z_floor + sill
            if z0 + h + head > ceiling:
                h = ceiling - head - z0
            if w < 0.4 or h < 0.6:
                continue
            centre = (n - 1) / 2
            for i in range(n):
                if every > 1 and round(abs(i - centre)) % every:
                    continue
                if i in ent_bays and z0 < ent_top:
                    continue
                s_ = (i + 0.5) * b
                p_, t_, o_ = _point_at(run["edges"], s_)
                # a window must sit on one straight piece of a curved facade (no wrapping round a sharp bend)
                _, d0, _ = _point_at(run["edges"], max(0.0, s_ - w / 2))
                _, d1, _ = _point_at(run["edges"], min(run["L"], s_ + w / 2))
                if d0[0] * d1[0] + d0[1] * d1[1] < 0.9:
                    continue
                out["windows"].append({"run": ri, "s": s_, "w": w, "h": h, "z0": z0, "kind": kind, "lv": lv,
                                       "p": p_, "t": t_, "o": o_, "pediment": pediment, "awning": awning,
                                       "awning_depth": float(src.get("awning_depth", 1.0)),
                                       "frame_half": min(w / 2 + 0.25, (b - pil_w) / 2 - 0.05)})
    return out


def check_facade(lay) -> list[str]:
    """Deterministic consistency check of a layout: no window overlaps a pilaster, the entrance or another window,
    and nothing runs past its facade. Returns the list of violations (empty = consistent)."""
    errs = []
    eps = 1e-6
    for w in lay["windows"]:
        a0, a1 = w["s"] - w["frame_half"], w["s"] + w["frame_half"]
        for pl in lay["pilasters"]:
            if pl["run"] == w["run"] and a0 < pl["s"] + pl["w"] / 2 - eps and pl["s"] - pl["w"] / 2 + eps < a1:
                errs.append(f"window at s={w['s']:.1f} floor {w['lv']} overlaps a pilaster at s={pl['s']:.1f}")
        e = lay["entrance"]
        if e and e["run"] == w["run"] and abs(w["s"] - e["s"]) < e["w"] / 2 + 0.5 + w["frame_half"]                 and w["z0"] < e["z_top"]:
            errs.append(f"window at s={w['s']:.1f} floor {w['lv']} overlaps the entrance")
    by_slot = {}
    for w in lay["windows"]:
        by_slot.setdefault((w["run"], w["lv"]), []).append(w)
    for ws in by_slot.values():
        ws.sort(key=lambda x: x["s"])
        for a, b in zip(ws, ws[1:]):
            if a["s"] + a["frame_half"] > b["s"] - b["frame_half"] + eps:
                errs.append(f"windows at s={a['s']:.1f} and s={b['s']:.1f} (floor {a['lv']}) overlap")
    return errs


def pilasters(poly, base_z, top_z, facade, mesh: Mesh, budget_left: int):
    """Flat pilasters (shallow boxes) on the bay boundaries of the facade layout (shared with the windows)."""
    if not facade.get("pilasters"):
        return 0
    pl = facade["pilasters"] if isinstance(facade["pilasters"], dict) else {}
    dep = float(pl.get("depth", 0.22))
    cor = float(facade.get("cornice", 0) or 0)
    lay = facade_layout(poly, base_z, top_z + cor, facade)      # top_z here is already below the cornice
    used = 0
    for p_ in lay["pilasters"]:
        if used + 6 > budget_left:
            break
        p, t, out = p_["pt"]
        w = p_["w"] / 2
        a = (p[0] - t[0] * w, p[1] - t[1] * w)
        b = (p[0] + t[0] * w, p[1] + t[1] * w)
        ao = (a[0] + out[0] * dep, a[1] + out[1] * dep)
        bo = (b[0] + out[0] * dep, b[1] + out[1] * dep)
        for q0, q1 in ((ao, bo), (a, ao), (bo, b)):
            mesh.f((mesh.v(q0[0], q0[1], base_z), mesh.v(q1[0], q1[1], base_z), mesh.v(q1[0], q1[1], top_z),
                    mesh.v(q0[0], q0[1], top_z)), "trim")
        used += 6
    return used


# ----------------------------------------------------------------------------- facade detail (any outline, curves too)
def _perimeter(poly):
    poly = ensure_ccw(poly)
    n = len(poly)
    edges, s = [], 0.0
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ln = math.dist(a, b)
        if ln > 1e-6:
            edges.append((a, b, ln, s))
            s += ln
    return edges, s


def _point_at(edges, s):
    for a, b, ln, s0 in edges:
        if s0 <= s <= s0 + ln + 1e-9:
            t = (s - s0) / ln
            d = ((b[0] - a[0]) / ln, (b[1] - a[1]) / ln)
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), d, (d[1], -d[0])
    a, b, ln, _ = edges[-1]
    d = ((b[0] - a[0]) / ln, (b[1] - a[1]) / ln)
    return b, d, (d[1], -d[0])


def band(poly, z0, z1, out, mesh: Mesh, tag="trim", caps=True):
    """A horizontal band (cornice, string course, spandrel) projecting `out` metres around the outline."""
    poly = ensure_ccw(poly)
    ring = outward_offset(poly, out)
    n = len(poly)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        pa, pb = poly[i], poly[(i + 1) % n]
        mesh.f((mesh.v(a[0], a[1], z0), mesh.v(b[0], b[1], z0), mesh.v(b[0], b[1], z1), mesh.v(a[0], a[1], z1)), tag)
        if caps and out > 0.04:
            mesh.f((mesh.v(pa[0], pa[1], z1), mesh.v(a[0], a[1], z1), mesh.v(b[0], b[1], z1), mesh.v(pb[0], pb[1], z1)), tag)
            mesh.f((mesh.v(pb[0], pb[1], z0), mesh.v(b[0], b[1], z0), mesh.v(a[0], a[1], z0), mesh.v(pa[0], pa[1], z0)), tag)
    return 2 * n * (3 if caps and out > 0.04 else 1)


def curtain_wall(poly, base_z, top_z, facade, mesh: Mesh, budget_left: int, taper=None):
    """Glass curtain wall: the wall itself is the glass (wall material); this adds the frame -
    a spandrel band at every floor and vertical mullions every bay along the outline (curves included)."""
    level_h = float(facade.get("level_height", 3.8))
    bay = max(0.8, float(facade.get("bay_spacing", 1.5)))
    edges, per = _perimeter(poly)
    if not edges:
        return 0
    levels = max(1, int((top_z - base_z) // level_h))
    n_edges = len(edges)
    band_cost = 2 * n_edges
    mullions = int(per // bay)
    step_l = 1
    while (levels // step_l) * band_cost + mullions * 2 > budget_left and step_l < levels:
        step_l += 1                                    # thin out floors before dropping the frame
    while (levels // step_l) * band_cost + mullions * 2 > budget_left and mullions > 8:
        mullions //= 2
    made = 0
    for lv in range(step_l, levels + 1, step_l):
        z = base_z + lv * level_h
        if z > top_z - 0.2:
            break
        band(ring_at(poly, base_z, top_z, taper, z), z - 0.45, z + 0.05, 0.06, mesh, "trim", caps=False)
        made += 1
    if mullions:
        sp = per / mullions
        w = 0.09
        zs = taper_heights(base_z, top_z, taper) if is_tapered(taper) else [base_z, top_z]
        c0 = centroid(poly)
        for k in range(mullions):
            p, d, o = _point_at(edges, k * sp)
            pts = []
            for z in zs:                                   # the same point of the outline, followed up the taper
                if is_tapered(taper):
                    f = taper_factor(taper, (z - base_z) / max(1e-6, top_z - base_z))
                    kk = 1.0 + (float(taper.get("top_scale", 1.0)) - 1.0) * f
                    sx, sy = (list(taper.get("top_shift") or [0.0, 0.0]) + [0.0, 0.0])[:2]
                    q = (c0[0] + (p[0] - c0[0]) * kk + sx * f, c0[1] + (p[1] - c0[1]) * kk + sy * f)
                else:
                    q = p
                cq = (q[0] + o[0] * 0.07, q[1] + o[1] * 0.07)
                pts.append(((cq[0] - d[0] * w, cq[1] - d[1] * w), (cq[0] + d[0] * w, cq[1] + d[1] * w), z))
            for (a0, b0, z0), (a1, b1, z1) in zip(pts, pts[1:]):
                mesh.f((mesh.v(a0[0], a0[1], z0), mesh.v(b0[0], b0[1], z0), mesh.v(b1[0], b1[1], z1),
                        mesh.v(a1[0], a1[1], z1)), "trim")
            made += 1
    return made


def facade_detail(poly, base_z, top_z, facade, mesh: Mesh, budget_left: int, taper=None):
    """Cornice at the wall top and string courses per floor (classical / masonry facades)."""
    if not facade:
        return 0
    used = 0
    level_h = float(facade.get("level_height", 3.5))
    cor = float(facade.get("cornice", 0) or 0)
    if cor > 0:
        used += band(ring_at(poly, base_z, top_z, taper, top_z), top_z - cor, top_z, min(0.6, 0.25 + cor * 0.25),
                     mesh, "trim")
    if facade.get("pilasters") and facade.get("opening_type") != "curtain_wall" and not is_tapered(taper):
        used += pilasters(poly, base_z, top_z - cor, facade, mesh, max(0, budget_left - used))
    if facade.get("bands") and facade.get("opening_type") != "curtain_wall":
        levels = int((top_z - base_z - cor) // level_h)
        for lv in range(1, levels):
            if used >= budget_left:
                break
            z = base_z + lv * level_h
            used += band(ring_at(poly, base_z, top_z, taper, z), z - 0.25, z, 0.12, mesh, "trim")
    return used


def edge_directions(poly, min_share=0.12, tol_deg=4.0):
    """Dominant edge directions of an outline, in degrees from east modulo 90 (length-weighted).
    Curved runs (many short edges turning) spread over many angles and do not form a peak."""
    poly = ensure_ccw(poly)
    n = len(poly)
    hist = [0.0] * 90
    total = 0.0
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ln = math.dist(a, b)
        if ln < 1e-6:
            continue
        ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 90.0
        hist[int(round(ang)) % 90] += ln
        total += ln
    if total <= 0:
        return []
    k = int(tol_deg)
    smooth = [sum(hist[(i + j) % 90] for j in range(-k, k + 1)) for i in range(90)]
    peaks = []
    for i in sorted(range(90), key=lambda j: -smooth[j]):
        if smooth[i] < min_share * total:
            break
        if all(min(abs(i - p), 90 - abs(i - p)) > 2 * tol_deg for p in peaks):
            peaks.append(i)
    out = []
    for p in peaks:                                   # refine: length-weighted mean angle near the peak
        num = den = 0.0
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            ln = math.dist(a, b)
            if ln < 1e-6:
                continue
            ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 90.0
            d = (ang - p + 45) % 90 - 45
            if abs(d) <= tol_deg + 0.5:
                num += d * ln
                den += ln
        out.append(round((p + (num / den if den else 0.0)) % 90.0, 1))
    return out


def edge_direction_shares(poly, tol_deg=4.0):
    """[(direction_deg, share_of_outline)] for the dominant directions, main frontage first."""
    poly = ensure_ccw(poly)
    n = len(poly)
    total = sum(math.dist(poly[i], poly[(i + 1) % n]) for i in range(n)) or 1.0
    res = []
    for d in edge_directions(poly, tol_deg=tol_deg):
        ln = 0.0
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            if math.dist(a, b) > 1e-6 and angle_gap(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])), d) <= tol_deg + 0.5:
                ln += math.dist(a, b)
        res.append((d, round(ln / total, 2)))
    return sorted(res, key=lambda t: -t[1])


def angle_gap(a_deg, b_deg) -> float:
    """Smallest difference between two directions modulo 90 degrees (rectangles repeat every 90)."""
    d = abs((a_deg - b_deg) % 90.0)
    return min(d, 90.0 - d)



# ----------------------------------------------------------------------------- feature primitives (any landmark)
def _face_for(ring, facing_deg):
    """The outline edge whose outward normal best faces `facing_deg` (weighted towards longer edges)."""
    ring = ensure_ccw(ring)
    f = math.radians(float(facing_deg))
    want = (math.cos(f), math.sin(f))
    best, best_score = None, -1e9
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        ln = math.dist(a, b)
        if ln < 0.3:
            continue
        t = ((b[0] - a[0]) / ln, (b[1] - a[1]) / ln)
        o = (t[1], -t[0])
        dot = o[0] * want[0] + o[1] * want[1]
        if dot < 0.75:                        # more than ~41 degrees off: not this facade
            continue
        score = dot * min(ln, 25.0) ** 0.5
        if score > best_score:
            best, best_score = (a, b, ln, t, o), score
    return best


def _box(mesh, center, t, o, w, h, depth, z_mid, tag, shape=None):
    """A shallow plate on a facade: front face `depth` out, plus side faces back to the wall."""
    pts = shape or [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    front = [(center[0] + t[0] * u + o[0] * depth, center[1] + t[1] * u + o[1] * depth, z_mid + v) for u, v in pts]
    back = [(center[0] + t[0] * u + o[0] * 0.02, center[1] + t[1] * u + o[1] * 0.02, z_mid + v) for u, v in pts]
    mesh.f([mesh.v(*p) for p in front], tag)
    k = len(pts)
    for i in range(k):
        j = (i + 1) % k
        mesh.f((mesh.v(*back[i]), mesh.v(*back[j]), mesh.v(*front[j]), mesh.v(*front[i])), tag)
    return (k - 2) + 2 * k


def features(poly, base_z, top_z, taper, feats, mesh, budget_left: int = 10 ** 9) -> int:
    """Generic detail primitives on a part (all positions relative to the part):
      disc  {facing_deg, z, diameter, offset?, depth?, material}   clock faces, rose / round windows
      panel {facing_deg, z, width, height, offset?, depth?, material}   signs, friezes, reliefs, balconies
      mast  {x, y, height, radius?, material}   flagpoles, antennas, crosses (stands on the part's top)
    z = height of the centre above the part's base; offset = metres along the facade from its middle."""
    used = 0
    for ft in feats or []:
        if used >= budget_left:
            break
        kind = ft.get("type")
        tag = "m:" + str(ft.get("material", "trim"))
        if kind in ("disc", "panel"):
            z = base_z + float(ft.get("z", (top_z - base_z) / 2))
            ring = ring_at(poly, base_z, top_z, taper, z)
            face = _face_for(ring, ft.get("facing_deg", 270))
            if face is None:
                continue
            a, b, ln, t, o = face
            half = (float(ft.get("diameter", 3.0)) if kind == "disc" else float(ft.get("width", 4.0))) / 2
            half = min(half, ln / 2 - 0.1)            # never wider than the facade it sits on
            if half <= 0.1:
                continue
            off = max(-(ln / 2 - half), min(ln / 2 - half, float(ft.get("offset", 0.0))))
            center = (a[0] + t[0] * (ln / 2 + off), a[1] + t[1] * (ln / 2 + off))
            depth = float(ft.get("depth", 0.15))
            if kind == "disc":
                r = half
                shape = [(r * math.cos(2 * math.pi * i / 20), r * math.sin(2 * math.pi * i / 20)) for i in range(20)]
                used += _box(mesh, center, t, o, 0, 0, depth, z, tag, shape)
            else:
                used += _box(mesh, center, t, o, 2 * half, float(ft.get("height", 1.5)), depth, z, tag)
        elif kind == "mast":
            x, y = float(ft.get("x", centroid(poly)[0])), float(ft.get("y", centroid(poly)[1]))
            h = float(ft.get("height", 5.0))
            r = float(ft.get("radius", 0.15))
            ring = circle_polygon(x, y, r, 8)
            for i in range(8):
                p0, p1 = ring[i], ring[(i + 1) % 8]
                mesh.f((mesh.v(p0[0], p0[1], top_z), mesh.v(p1[0], p1[1], top_z), mesh.v(p1[0], p1[1], top_z + h),
                        mesh.v(p0[0], p0[1], top_z + h)), tag)
            used += 16
    return used
