"""Build accounting (plan M1 task 5; docs/PRODUCTION_BASELINE.md 9.3).

Every number here is computed from the mesh faces that are about to be exported, so the report cannot drift from the
geometry it describes. The category of a face comes from its material tag, as the Blender builder already tags them.
"""
from __future__ import annotations

from dataclasses import dataclass

MASSING_TAGS = ("wall", "roof")
FACADE_TAGS = ("opening", "trim", "accent")
FEATURE_PREFIXES = ("m:", "d:")          # feature material ("m:") and depth details ("d:")


def category_of(tag: str) -> str:
    if tag in MASSING_TAGS:
        return "massing"
    if tag in FACADE_TAGS:
        return "facade"
    if tag.startswith(FEATURE_PREFIXES):
        return "feature"
    return "other"


def triangles_of(mesh) -> int:
    return sum(len(f) - 2 for f in mesh.faces)


@dataclass
class PartMesh:
    part_id: str
    mesh: object                         # geom.Mesh
    lod: int = 0                         # LOD candidates arrive with M2; until then everything is LOD 0


def summarize(parts: list, target_triangles: int | None = None, hard_triangles: int | None = None,
              textures: list | None = None) -> dict:
    """Totals by category and LOD, and utilisation of the budgets. The totals equal the sum over faces."""
    by_category: dict[str, int] = {}
    by_lod: dict[int, int] = {}
    by_part: dict[str, int] = {}
    total = 0
    for p in parts:
        n_part = 0
        for face, tag in zip(p.mesh.faces, p.mesh.tags):
            t = len(face) - 2
            cat = category_of(tag)
            by_category[cat] = by_category.get(cat, 0) + t
            by_lod[p.lod] = by_lod.get(p.lod, 0) + t
            n_part += t
        by_part[p.part_id] = by_part.get(p.part_id, 0) + n_part
        total += n_part
    base = by_category.get("massing", 0)
    detail = total - base
    tex = textures or []
    pixels = sum(int(t.get("width", 0)) * int(t.get("height", 0)) for t in tex)
    slots = sorted({str(t.get("material")) for t in tex if t.get("material")})
    return {
        "total_triangles": total,
        "base_triangles": base,
        "detail_triangles": detail,
        "by_category": dict(sorted(by_category.items())),
        "by_lod": {str(k): v for k, v in sorted(by_lod.items())},
        "by_part": dict(sorted(by_part.items())),
        "target_utilization": round(total / target_triangles, 4) if target_triangles else None,
        "hard_utilization": round(total / hard_triangles, 4) if hard_triangles else None,
        "shared_mesh_estimate": 0,       # sharing of repeated modules arrives with M2
        "texture_pixels": pixels,
        "texture_material_slots": slots,
    }


def check(parts: list, accounted: dict) -> list[str]:
    """The accounting must agree with the meshes: any mismatch is a build error, never a silent warning."""
    errs = []
    actual = sum(triangles_of(p.mesh) for p in parts)
    if accounted.get("total_triangles") != actual:
        errs.append(f"accounting total {accounted.get('total_triangles')} differs from the meshes ({actual})")
    if accounted.get("base_triangles", 0) + accounted.get("detail_triangles", 0) != accounted.get("total_triangles"):
        errs.append("base and detail triangles do not add up to the total")
    if sum(accounted.get("by_category", {}).values()) != accounted.get("total_triangles"):
        errs.append("category totals do not add up to the total")
    if sum(accounted.get("by_lod", {}).values()) != accounted.get("total_triangles"):
        errs.append("LOD totals do not add up to the total")
    return errs
