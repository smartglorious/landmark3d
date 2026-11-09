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
