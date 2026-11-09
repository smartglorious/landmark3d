"""Place M4 articulation generators into a part's mesh (plan M4). Only with the detail engine flag on.

A part asks for articulation with a list in its spec:
  "articulation": [{"generator": "plinth_band", "params": {"height": 0.6, "projection": 0.12}}, ...]
The part's outline, base height and height are passed to the generator, so the caller only gives the design values.
With the flag off the list is reported and not built, so nothing is dropped silently.
"""
from __future__ import annotations

from . import registry


def _copy_into(mesh, component_mesh) -> int:
    before = mesh.triangles()
    for face, tag in zip(component_mesh.faces, component_mesh.tags):
        mesh.f([mesh.v(*component_mesh.verts[i]) for i in face], tag)
    return mesh.triangles() - before


def place_articulation(mesh, poly, part: dict, enabled: bool, budget: int) -> dict:
    """Build the part's articulation into `mesh`. Returns {"placed": [...], "skipped": [...], "triangles": n}."""
    items = part.get("articulation") or []
    out = {"placed": [], "skipped": [], "triangles": 0}
    if not items:
        return out
    if not enabled:
        out["skipped"] = [f"{part.get('id')}:{it.get('generator')} (needs detail_engine_v2)" for it in items]
        return out
    base_z = float(part.get("base_z", 0.0))
    left = budget
    for it in items:
        name = it.get("generator")
        label = f"{part.get('id')}:{name}"
        gen = registry.GENERATORS.get(name)
        if gen is None:
            raise ValueError(f"{label} is not a registered generator")
        params = dict(it.get("params") or {}, poly=poly, base_z=base_z)   # the generator's own defaults apply otherwise
        comp = gen(params)
        if comp.triangle_count > left:
            out["skipped"].append(f"{label} (budget)")
            continue
        used = _copy_into(mesh, comp.mesh)
        left -= used
        out["triangles"] += used
        out["placed"].append(label)
    return out
