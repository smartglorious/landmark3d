"""M4 articulation and roof slices (plan section 7 and M4 order): plinth, brise-soleil array, chimney stack.

Each slice is one generator with a registry entry. They take a params dict and return a GeneratedComponent. None of them
writes files, calls AI, or looks at a landmark name. They are not yet placed by the Blender builder: a v2 facade or part
field must ask for them first, and that wiring is the next step (see docs/M2_M5_RUN_REPORT.md).
"""
from __future__ import annotations

import math

from .. import geom
from .types import Bounds, GeneratedComponent


def _component(mesh, anchors=None) -> GeneratedComponent:
    xs = [v[0] for v in mesh.verts] or [0.0]
    ys = [v[1] for v in mesh.verts] or [0.0]
    zs = [v[2] for v in mesh.verts] or [0.0]
    return GeneratedComponent(mesh=mesh, triangle_count=sum(len(f) - 2 for f in mesh.faces),
                              material_slots={"trim"},
                              bounds=Bounds((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))),
                              anchors=anchors or {})


def plinth_band(params: dict) -> GeneratedComponent:
    """A plinth: the ring of the base projecting around the whole outline (uses the geometry engine's band)."""
    poly = params["poly"]
    base_z = float(params.get("base_z", 0.0))
    height = max(0.1, float(params.get("height", 0.6)))
    projection = max(0.0, float(params.get("projection", 0.12)))
    if projection > 0.6:
        raise ValueError("plinth projection above 0.6 m is a different element, not a plinth")
    m = geom.Mesh()
    geom.band(poly, base_z, base_z + height, projection, m, "trim", caps=True)
    return _component(m, {"base": (base_z, height)})


def brise_soleil_array(params: dict) -> GeneratedComponent:
    """Vertical fins in front of a facade: the screen array of a modernist or colonnaded front.

    The fins stand on the outline face that points to `facing_deg`, centred on that face, `count` fins spaced `spacing`
    metres apart, each `fin_width` wide and projecting `depth`. A face too short for the array is an error, not a
    silently shortened array."""
    poly = params["poly"]
    facing = float(params.get("facing_deg", 270))
    base_z = float(params.get("base_z", 0.0))
    z0 = base_z + float(params.get("z", 0.0))
    height = max(0.2, float(params.get("height", 3.0)))
    count = max(1, int(params.get("count", 6)))
    spacing = max(0.2, float(params.get("spacing", 1.2)))
    fin_w = max(0.03, float(params.get("fin_width", 0.12)))
    depth = max(0.05, float(params.get("depth", 0.6)))
    face = geom._face_for(geom.ensure_ccw(poly), facing)
    if face is None:
        raise ValueError("no outline face points to the requested direction")
    a, _b, ln, t, o = face
    needed = (count - 1) * spacing + fin_w
    if needed > ln - 0.2:
        raise ValueError(f"array needs {needed:.2f} m but the face is {ln:.2f} m long")
    m = geom.Mesh()
    centre = (ln / 2)
    for i in range(count):
        x = centre + (i - (count - 1) / 2) * spacing
        cx, cy = a[0] + t[0] * x, a[1] + t[1] * x

        def at(u, v, zz, cx=cx, cy=cy):
            return (cx + t[0] * u + o[0] * v, cy + t[1] * u + o[1] * v, zz)
        geom._solid(m, at, -fin_w / 2, fin_w / 2, 0.0, depth, z0, z0 + height, "trim")
    return _component(m, {"first_fin": (a[0] + t[0] * (centre - (count - 1) / 2 * spacing), a[1] + t[1] * (centre - (count - 1) / 2 * spacing))})


def chimney_stack(params: dict) -> GeneratedComponent:
    """A rectangular chimney standing on the roof at (x, y), from `z0` up to `z1`, with a cap projecting at the top."""
    x, y = float(params.get("x", 0.0)), float(params.get("y", 0.0))
    w, d = max(0.3, float(params.get("width", 0.8))), max(0.3, float(params.get("depth", 0.8)))
    z0, z1 = float(params.get("z0", 0.0)), float(params.get("z1", 2.5))
    if z1 <= z0:
        raise ValueError("chimney top must be above its base")
    cap = max(0.0, float(params.get("cap", 0.12)))
    m = geom.Mesh()
    geom._solid(m, lambda u, v, zz: (x + u, y + v, zz), -w / 2, w / 2, -d / 2, d / 2, z0, z1, "trim")
    if cap > 0:
        geom._solid(m, lambda u, v, zz: (x + u, y + v, zz), -w / 2 - cap, w / 2 + cap, -d / 2 - cap, d / 2 + cap,
                    z1, z1 + 0.1, "trim")
    return _component(m, {"top": (x, y, z1)})


def _register() -> None:
    from . import registry
    registry.GENERATORS.update({"plinth_band": plinth_band, "brise_soleil_array": brise_soleil_array,
                                "chimney_stack": chimney_stack})


_register()


# ----------------------------------------------------------------------------- M4 slices 2 onward
def _face_frame(poly, facing_deg, offset=0.0):
    """Origin, along-face unit vector, outward unit vector and length of the outline face that faces facing_deg,
    with offset metres from its centre along the face."""
    face = geom._face_for(geom.ensure_ccw(poly), facing_deg)
    if face is None:
        raise ValueError("no outline face points to the requested direction")
    a, _b, ln, t, o = face
    cx, cy = a[0] + t[0] * (ln / 2 + offset), a[1] + t[1] * (ln / 2 + offset)
    return (cx, cy), t, o, ln


def _frame_at(origin, t, o):
    cx, cy = origin
    return lambda u, v, zz: (cx + t[0] * u + o[0] * v, cy + t[1] * u + o[1] * v, zz)


def pilaster_profile(params):
    """A pilaster: a shaft standing from the wall, with a base and a capital slightly wider than the shaft."""
    origin, t, o, ln = _face_frame(params["poly"], float(params.get("facing_deg", 270)), float(params.get("offset", 0.0)))
    base_z = float(params.get("base_z", 0.0)) + float(params.get("z", 0.0))
    height = max(0.5, float(params.get("height", 6.0)))
    width = max(0.2, float(params.get("width", 0.7)))
    depth = max(0.05, float(params.get("depth", 0.3)))
    if width > ln:
        raise ValueError("pilaster is wider than the face it stands on")
    at = _frame_at(origin, t, o)
    m = geom.Mesh()
    plinth, cap = min(0.4, height * 0.08), min(0.35, height * 0.07)
    geom._solid(m, at, -width * 0.6, width * 0.6, 0.0, depth * 1.2, base_z, base_z + plinth, "trim", bottom=False)
    geom._solid(m, at, -width / 2, width / 2, 0.0, depth, base_z + plinth, base_z + height - cap, "trim", bottom=False)
    geom._solid(m, at, -width * 0.65, width * 0.65, 0.0, depth * 1.2, base_z + height - cap, base_z + height, "trim")
    return _component(m, {"top": (origin[0], origin[1], base_z + height)})


def balustrade_run(params):
    """A balustrade along one face: a top rail, a bottom rail and evenly spaced posts."""
    origin, t, o, ln = _face_frame(params["poly"], float(params.get("facing_deg", 270)), float(params.get("offset", 0.0)))
    z = float(params.get("base_z", 0.0)) + float(params.get("z", 0.0))
    width = min(max(0.5, float(params.get("width", 6.0))), ln - 0.2)
    height = max(0.6, float(params.get("height", 1.0)))
    spacing = max(0.2, float(params.get("spacing", 0.55)))
    post = 0.07
    proj = float(params.get("projection", 0.15))
    at = _frame_at(origin, t, o)
    m = geom.Mesh()
    geom._solid(m, at, -width / 2, width / 2, 0.0, proj, z + height - 2 * post, z + height, "trim")
    geom._solid(m, at, -width / 2, width / 2, 0.0, proj, z, z + 2 * post, "trim", bottom=False)
    n = max(1, int(round(width / spacing)))
    for i in range(n + 1):
        x = -width / 2 + width * i / n
        geom._solid(m, at, x - post, x + post, 0.0, proj, z + 2 * post, z + height - 2 * post, "trim", bottom=False)
    return _component(m, {"ends": ((origin[0] - t[0] * width / 2, origin[1] - t[1] * width / 2),
                                   (origin[0] + t[0] * width / 2, origin[1] + t[1] * width / 2))})


def dormer_window(params):
    """A dormer on a mansard roof: a small box with a hipped cap, standing at (x, y) from z0."""
    x, y = float(params.get("x", 0.0)), float(params.get("y", 0.0))
    w, d = max(0.8, float(params.get("width", 2.0))), max(0.8, float(params.get("depth", 2.0)))
    z0 = float(params.get("z0", 0.0))
    h = max(0.8, float(params.get("height", 1.8)))
    rise = max(0.3, float(params.get("roof_rise", 1.2)))
    at = lambda u, v, zz: (x + u, y + v, zz)
    m = geom.Mesh()
    geom._solid(m, at, -w / 2, w / 2, -d / 2, d / 2, z0, z0 + h, "trim", bottom=False)
    apex = (x, y, z0 + h + rise)
    corners = [(-w / 2 - 0.1, -d / 2 - 0.1), (w / 2 + 0.1, -d / 2 - 0.1), (w / 2 + 0.1, d / 2 + 0.1), (-w / 2 - 0.1, d / 2 + 0.1)]
    base = [(x + u, y + v, z0 + h) for u, v in corners]
    for i in range(4):
        m.f([m.v(*base[i]), m.v(*base[(i + 1) % 4]), m.v(*apex)], "roof")
    return _component(m, {"apex": apex})


def cupola_lantern(params):
    """An octagonal lantern on a roof: a shaft with a pyramidal cap rising to a point."""
    x, y = float(params.get("x", 0.0)), float(params.get("y", 0.0))
    z0, z1 = float(params.get("z0", 0.0)), float(params.get("z1", 4.0))
    if z1 <= z0:
        raise ValueError("lantern top must be above its base")
    r = max(0.3, float(params.get("radius", 1.5)))
    n = 8
    ring = [(x + r * math.cos(2 * math.pi * i / n), y + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
    top_r = r * 0.8
    rim = [(x + top_r * math.cos(2 * math.pi * i / n), y + top_r * math.sin(2 * math.pi * i / n), z1) for i in range(n)]
    cap_base = z1 + max(0.5, r * 0.8)
    m = geom.Mesh()
    for i in range(n):
        j = (i + 1) % n
        m.f([m.v(ring[i][0], ring[i][1], z0), m.v(ring[j][0], ring[j][1], z0), m.v(*rim[j]), m.v(*rim[i])], "trim")
        m.f([m.v(*rim[i]), m.v(*rim[j]), m.v(x, y, cap_base)], "roof")
    return _component(m, {"apex": (x, y, cap_base)})


def ridge_cap(params):
    """A ridge cap along a straight roof ridge from (x0, y0) to (x1, y1) at height z."""
    x0, y0 = float(params["x0"]), float(params["y0"])
    x1, y1 = float(params["x1"]), float(params["y1"])
    z = float(params.get("z", 10.0))
    w, h = max(0.1, float(params.get("width", 0.4))), max(0.05, float(params.get("height", 0.25)))
    length = math.hypot(x1 - x0, y1 - y0)
    if length < 0.5:
        raise ValueError("ridge is too short to cap")
    t = ((x1 - x0) / length, (y1 - y0) / length)
    o = (-t[1], t[0])

    def at(s, u, zz):
        return (x0 + t[0] * s + o[0] * u, y0 + t[1] * s + o[1] * u, zz)
    m = geom.Mesh()
    geom._solid(m, at, 0.0, length, -w / 2, w / 2, z, z + h, "trim")
    return _component(m, {"ends": ((x0, y0), (x1, y1))})


def lattice_mast(params):
    """A four-legged lattice mast: corner legs and a horizontal brace every brace_every metres."""
    x, y = float(params.get("x", 0.0)), float(params.get("y", 0.0))
    z0, z1 = float(params.get("z0", 0.0)), float(params.get("z1", 30.0))
    if z1 <= z0:
        raise ValueError("mast top must be above its base")
    side = max(0.5, float(params.get("width", 2.0))) / 2
    leg = max(0.05, float(params.get("leg", 0.12))) / 2
    every = max(1.0, float(params.get("brace_every", 3.0)))
    m = geom.Mesh()
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        cx, cy = x + sx * side, y + sy * side
        geom._solid(m, lambda u, v, zz, cx=cx, cy=cy: (cx + u, cy + v, zz), -leg, leg, -leg, leg, z0, z1, "trim")
    z = z0 + every
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    while z < z1 - 0.5:
        for k in range(4):
            ax, ay = corners[k]
            bx, by = corners[(k + 1) % 4]
            ax, ay, bx, by = x + ax * side, y + ay * side, x + bx * side, y + by * side
            length = math.hypot(bx - ax, by - ay)
            ux, uy = (bx - ax) / length, (by - ay) / length
            ox, oy = -uy, ux

            def brace(s, v, zz, ax=ax, ay=ay, ux=ux, uy=uy, ox=ox, oy=oy):
                return (ax + ux * s + ox * v, ay + uy * s + oy * v, zz)
            geom._solid(m, brace, 0.0, length, -leg, leg, z - leg, z + leg, "trim")
        z += every
    return _component(m, {"top": (x, y, z1)})


def rose_window(params):
    """A round window on one face: a glazed disc with a projecting ring around it."""
    origin, t, o, ln = _face_frame(params["poly"], float(params.get("facing_deg", 270)), float(params.get("offset", 0.0)))
    z = float(params.get("base_z", 0.0)) + float(params["z"])
    r = max(0.3, float(params.get("radius", 1.2)))
    ring_w = max(0.05, float(params.get("ring", 0.2)))
    proj = max(0.02, float(params.get("projection", 0.15)))
    if 2 * (r + ring_w) > ln:
        raise ValueError("rose window does not fit on the face")
    at = _frame_at(origin, t, o)
    n = 24
    pts_in = [(r * math.cos(2 * math.pi * i / n), z + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
    pts_out = [((r + ring_w) * math.cos(2 * math.pi * i / n), z + (r + ring_w) * math.sin(2 * math.pi * i / n)) for i in range(n)]
    m = geom.Mesh()
    m.f([m.v(*at(u, 0.02, zz)) for u, zz in pts_in], "opening")
    for i in range(n):
        j = (i + 1) % n
        quad = [pts_in[i], pts_in[j], pts_out[j], pts_out[i]]
        m.f([m.v(*at(u, proj, zz)) for u, zz in quad], "trim")
    return _component(m, {"centre": (origin[0], origin[1], z)})


def _register_more():
    from . import registry
    registry.GENERATORS.update({"pilaster_profile": pilaster_profile, "balustrade_run": balustrade_run,
                                "dormer_window": dormer_window, "cupola_lantern": cupola_lantern,
                                "ridge_cap": ridge_cap, "lattice_mast": lattice_mast, "rose_window": rose_window})


_register_more()


def finial_pinnacle(params):
    """A finial on top of a part: a tapered spike, or a ball on a short neck when style is 'ball'."""
    x, y = float(params.get("x", 0.0)), float(params.get("y", 0.0))
    z = float(params.get("z", 0.0))
    h = max(0.2, float(params.get("height", 3.0)))
    r = max(0.05, float(params.get("radius", 0.25)))
    m = geom.Mesh()
    n = 8
    if params.get("style") == "ball":
        ball_z = z + h * 0.72
        ring = [(x + r * math.cos(2 * math.pi * i / n), y + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
        for i in range(n):
            j = (i + 1) % n
            m.f([m.v(*ring[i], z), m.v(*ring[j], z), m.v(*ring[j], ball_z), m.v(*ring[i], ball_z)], "trim")
        top = (x, y, z + h)
        widest = [(x + 1.2 * r * math.cos(2 * math.pi * i / n), y + 1.2 * r * math.sin(2 * math.pi * i / n), ball_z + 0.6 * r) for i in range(n)]
        for i in range(n):
            j = (i + 1) % n
            m.f([m.v(*widest[i]), m.v(*widest[j]), m.v(*top)], "trim")
            m.f([m.v(x, y, ball_z), m.v(*widest[j]), m.v(*widest[i])], "trim")
    else:
        base = [(x + r * math.cos(2 * math.pi * i / n), y + r * math.sin(2 * math.pi * i / n), z) for i in range(n)]
        apex = (x, y, z + h)
        for i in range(n):
            j = (i + 1) % n
            m.f([m.v(*base[i]), m.v(*base[j]), m.v(*apex)], "trim")
    return _component(m, {"tip": (x, y, z + h)})


def _register_finial():
    from . import registry
    registry.GENERATORS["finial_pinnacle"] = finial_pinnacle


_register_finial()


# ----------------------------------------------------------------------------- M4 remaining planned slices
def stepped_cornice(params):
    """A stepped cornice: stacked bands under the roof line, the lowest projecting most and each tier above it less.

    Tiers stand from z (the roof line) upward, each `tier_height` tall. Tier k projects proj * (tiers - k) / tiers, so a
    two-tier cornice is a wide block with a narrower one on top, as in classical work."""
    poly = params["poly"]
    z = float(params.get("base_z", 0.0)) + float(params.get("z", 0.0))
    proj = float(params.get("projection", 0.5))
    tiers = int(params.get("tiers", 2))
    tier_h = float(params.get("tier_height", 0.25))
    if not 1 <= tiers <= 4:
        raise ValueError("a stepped cornice has 1 to 4 tiers")
    if not 0.1 <= proj <= 1.5:
        raise ValueError("cornice projection must be 0.1 to 1.5 m")
    if not 0.12 <= tier_h <= 0.6:
        raise ValueError("cornice tier height must be 0.12 to 0.6 m")
    m = geom.Mesh()
    for k in range(tiers):
        p = proj * (tiers - k) / tiers
        geom.band(poly, z + k * tier_h, z + (k + 1) * tier_h, p, m, "trim", caps=(k == tiers - 1))
    return _component(m, {"top": (z + tiers * tier_h)})


def column_row(params):
    """A row of round columns on one face, `count` columns evenly spaced, each an 8-sided shaft from base_z to top."""
    origin, t, o, ln = _face_frame(params["poly"], float(params.get("facing_deg", 270)), float(params.get("offset", 0.0)))
    z0 = float(params.get("base_z", 0.0)) + float(params.get("z", 0.0))
    height = max(0.5, float(params.get("height", 5.0)))
    diam = max(0.2, float(params.get("column_diameter", 0.7)))
    count = max(2, int(params.get("count", 4)))
    spacing = max(diam * 1.2, float(params.get("spacing", 2.0)))
    proj = max(0.05, float(params.get("projection", 0.3)))
    span = (count - 1) * spacing + diam
    if span > ln - 0.2:
        raise ValueError(f"column row needs {span:.2f} m but the face is {ln:.2f} m long")
    at = _frame_at(origin, t, o)
    m = geom.Mesh()
    n = 8
    r = diam / 2
    for i in range(count):
        cu = -span / 2 + diam / 2 + i * spacing
        ring0 = [(cu + r * math.cos(2 * math.pi * k / n), proj + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
        for k in range(n):
            j = (k + 1) % n
            quad = [(ring0[k][0], ring0[k][1], z0), (ring0[j][0], ring0[j][1], z0),
                    (ring0[j][0], ring0[j][1], z0 + height), (ring0[k][0], ring0[k][1], z0 + height)]
            m.f([m.v(*at(u, v, zz)) for u, v, zz in quad], "trim")
        top = [m.v(*at(u, v, z0 + height)) for u, v in ring0]
        centre = m.v(*at(cu, proj, z0 + height))
        for k in range(n):
            m.f([top[k], top[(k + 1) % n], centre], "trim")
    return _component(m, {"first": (origin[0] - t[0] * span / 2, origin[1] - t[1] * span / 2)})


def entrance_portal(params):
    """An entrance portal: a projecting surround around a recessed door opening on one face."""
    origin, t, o, ln = _face_frame(params["poly"], float(params.get("facing_deg", 270)), float(params.get("offset", 0.0)))
    z0 = float(params.get("base_z", 0.0)) + float(params.get("z", 0.0))
    w = max(1.0, float(params.get("width", 6.0)))
    h = max(2.0, float(params.get("height", 6.0)))
    frame = max(0.1, float(params.get("frame", 0.4)))
    proj = max(0.05, float(params.get("projection", 0.3)))
    recess = max(0.05, float(params.get("recess", 0.5)))
    if w + 2 * frame > ln - 0.2:
        raise ValueError("portal does not fit on the face")
    at = _frame_at(origin, t, o)
    m = geom.Mesh()
    hw = w / 2
    geom._solid(m, at, -hw - frame, -hw, 0.0, proj, z0, z0 + h + frame, "trim")            # left jamb
    geom._solid(m, at, hw, hw + frame, 0.0, proj, z0, z0 + h + frame, "trim")              # right jamb
    geom._solid(m, at, -hw - frame, hw + frame, 0.0, proj, z0 + h, z0 + h + frame, "trim")  # head
    geom._solid(m, at, -hw, hw, 0.0, recess, z0, z0 + h, "opening", bottom=False)          # the recessed door
    comp = _component(m, {"door": (origin[0], origin[1], z0)})
    comp.material_slots = {"trim", "opening"}
    return comp


def curtain_wall_bay(params):
    """One bay of curtain wall on a face: a glazed panel framed by mullions and transoms, glass as the opening slot."""
    origin, t, o, ln = _face_frame(params["poly"], float(params.get("facing_deg", 270)), float(params.get("offset", 0.0)))
    z0 = float(params.get("base_z", 0.0)) + float(params.get("z", 0.0))
    bay = min(max(0.8, float(params.get("bay", 1.5))), ln - 0.2)
    height = max(1.5, float(params.get("height", 3.6)))
    mull = max(0.03, float(params.get("mullion", 0.08)))
    proj = max(0.03, float(params.get("projection", 0.1)))
    at = _frame_at(origin, t, o)
    m = geom.Mesh()
    hw = bay / 2
    geom._solid(m, at, -hw, -hw + mull, 0.0, proj, z0, z0 + height, "trim")               # mullions
    geom._solid(m, at, hw - mull, hw, 0.0, proj, z0, z0 + height, "trim")
    geom._solid(m, at, -hw, hw, 0.0, proj, z0 + height - mull, z0 + height, "trim")        # head transom
    geom._solid(m, at, -hw, hw, 0.0, proj, z0, z0 + mull, "trim")                          # sill transom
    geom._solid(m, at, -hw + mull, hw - mull, proj * 0.4, proj * 0.4, z0 + mull, z0 + height - mull, "opening", bottom=False)
    comp = _component(m, {"centre": (origin[0], origin[1], z0 + height / 2)})
    comp.material_slots = {"trim", "opening"}                                                 # glass is the opening slot
    return comp


def _register_remaining():
    from . import registry
    registry.GENERATORS.update({"stepped_cornice": stepped_cornice, "column_row": column_row,
                                "entrance_portal": entrance_portal, "curtain_wall_bay": curtain_wall_bay})


_register_remaining()


# ----------------------------------------------------------------------------- M4: the slices still missing
def string_course(params):
    """A string course: a thin band projecting around the whole outline at height z above the part base."""
    poly = params["poly"]
    z = float(params.get("base_z", 0.0)) + float(params.get("z", 4.0))
    height = float(params.get("height", 0.25))
    projection = float(params.get("projection", 0.08))
    if not 0.05 <= height <= 0.8:
        raise ValueError("string course height must be between 0.05 and 0.8 m")
    if not 0.02 <= projection <= 0.4:
        raise ValueError("string course projection must be between 0.02 and 0.4 m")
    m = geom.Mesh()
    geom.band(poly, z, z + height, projection, m, "trim", caps=True)
    return _component(m, {"z": z})


def roof_gutter(params):
    """Roof trim at the eave: a fascia board around the outline and a gutter channel in front of it (an open U:
    bottom, front lip and back face)."""
    poly = geom.ensure_ccw(params["poly"])
    z = float(params.get("base_z", 0.0)) + float(params.get("z", 10.0))
    fascia = float(params.get("fascia_height", 0.25))
    width = float(params.get("gutter_width", 0.15))
    if not 0.05 <= fascia <= 0.6:
        raise ValueError("fascia height must be between 0.05 and 0.6 m")
    if not 0.05 <= width <= 0.4:
        raise ValueError("gutter width must be between 0.05 and 0.4 m")
    m = geom.Mesh()
    geom.band(poly, z - fascia, z, 0.03, m, "trim", caps=True)              # fascia board
    inner = geom.outward_offset(poly, 0.03)
    outer = geom.outward_offset(poly, 0.03 + width)
    zb, zt = z - fascia * 0.8, z - fascia * 0.1
    n = len(poly)
    for i in range(n):
        ia, ib, oa, ob = inner[i], inner[(i + 1) % n], outer[i], outer[(i + 1) % n]
        m.f((m.v(*ia, zb), m.v(*ib, zb), m.v(*ob, zb), m.v(*oa, zb)), "trim")    # bottom
        m.f((m.v(*oa, zb), m.v(*ob, zb), m.v(*ob, zt), m.v(*oa, zt)), "trim")    # front lip
    return _component(m, {"eave_z": z})


def bundled_tubes(params):
    """A bundled-tube tower crown: square tubes on a grid, each stopping at its own height (setback silhouette).
    `heights` lists the tube heights row by row; 0 leaves a cell empty."""
    x, y = float(params.get("x", 0.0)), float(params.get("y", 0.0))
    z0 = float(params.get("base_z", 0.0)) + float(params.get("z", 0.0))
    cell = float(params.get("cell", 6.0))
    heights = params.get("heights") or [[30, 40, 30], [40, 50, 40], [30, 40, 30]]
    if not 1.0 <= cell <= 40.0:
        raise ValueError("tube size must be between 1 and 40 m")
    rows, cols = len(heights), max(len(r) for r in heights)
    if rows * cols > 36:
        raise ValueError("at most 36 tubes")
    m = geom.Mesh()
    placed = 0
    for r, row in enumerate(heights):
        for c, h in enumerate(row):
            h = float(h)
            if h <= 0:
                continue
            cx = x + (c - (cols - 1) / 2) * cell
            cy = y + ((rows - 1) / 2 - r) * cell
            geom._solid(m, lambda u, v, zz, cx=cx, cy=cy: (cx + u, cy + v, zz), -cell / 2, cell / 2, -cell / 2,
                        cell / 2, z0, z0 + h, "wall", bottom=False)
            placed += 1
    if not placed:
        raise ValueError("no tube has a height")
    comp = _component(m, {"top": (x, y, z0 + max(float(h) for row in heights for h in row))})
    comp.material_slots = {"wall"}
    return comp


def sign_panel(params):
    """A sign or frieze panel on one face: a flat panel standing `depth` off the wall with a frame around it.
    `kind` "frieze" spans the face less `margin` at each end; "sign" uses `width`."""
    origin, t, o, ln = _face_frame(params["poly"], float(params.get("facing_deg", 270)), float(params.get("offset", 0.0)))
    z = float(params.get("base_z", 0.0)) + float(params.get("z", 4.0))
    kind = params.get("kind", "sign")
    height = float(params.get("height", 1.2))
    depth = float(params.get("depth", 0.12))
    frame = float(params.get("frame", 0.08))
    width = ln - 2 * float(params.get("margin", 0.5)) if kind == "frieze" else float(params.get("width", 4.0))
    if kind not in ("sign", "frieze"):
        raise ValueError("kind must be sign or frieze")
    if width <= 0.3 or width > ln:
        raise ValueError(f"panel width {width:.2f} m does not fit the {ln:.2f} m face")
    if not 0.2 <= height <= 6.0 or not 0.02 <= depth <= 0.6:
        raise ValueError("panel height must be 0.2-6 m and depth 0.02-0.6 m")
    at = _frame_at(origin, t, o)
    m = geom.Mesh()
    geom._solid(m, at, -width / 2, width / 2, 0.0, depth, z, z + height, "accent")
    fd = depth + 0.03
    geom._solid(m, at, -width / 2 - frame, width / 2 + frame, 0.0, fd, z - frame, z, "trim", bottom=True)
    geom._solid(m, at, -width / 2 - frame, width / 2 + frame, 0.0, fd, z + height, z + height + frame, "trim")
    geom._solid(m, at, -width / 2 - frame, -width / 2, 0.0, fd, z, z + height, "trim", bottom=False)
    geom._solid(m, at, width / 2, width / 2 + frame, 0.0, fd, z, z + height, "trim", bottom=False)
    comp = _component(m, {"center": (origin[0], origin[1], z + height / 2)})
    comp.material_slots = {"trim", "accent"}
    return comp


def _register_last():
    from . import registry
    registry.GENERATORS.update({"string_course": string_course, "roof_gutter": roof_gutter,
                                "bundled_tubes": bundled_tubes, "sign_panel": sign_panel})


_register_last()
