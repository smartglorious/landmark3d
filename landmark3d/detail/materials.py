"""M5 deterministic material and texture checks (plan M5 tasks 1, 4, 7; production baseline 5.4 and 4.3).

These are the checks the exporter can run without a renderer: PBR values in range, architectural surfaces not made
metallic, texel density within 2x between neighbours, atlas padding that survives mip levels, and attribution that maps
to the pixels actually exported. Baking normals or AO and KTX2 output are not here yet.
"""
from __future__ import annotations

import math


ARCH_MAX_METALLIC = 0.1
GLASS_HINTS = ("glass", "curtain", "mirror")
TEXEL_RATIO_MAX = 2.0
ATTRIBUTION_LICENCES = ("cc_by", "cc_by_sa", "permission_granted")
TEXTURE_USES = ("reference_and_texture", "texture_permitted")


def is_glass(name: str) -> bool:
    return any(h in name.lower() for h in GLASS_HINTS)


def validate_material(name: str, m: dict) -> list[str]:
    errs = []
    rgb = m.get("srgb")
    if not isinstance(rgb, (list, tuple)) or len(rgb) != 3 or not all(isinstance(c, (int, float)) and 0 <= c <= 255 for c in rgb):
        errs.append(f"{name}: srgb must be three values in 0-255")
    r = m.get("roughness", 0.75)
    if not isinstance(r, (int, float)) or not 0.0 <= r <= 1.0:
        errs.append(f"{name}: roughness must be in [0, 1]")
    met = m.get("metallic", 0.0)
    if not isinstance(met, (int, float)) or not 0.0 <= met <= 1.0:
        errs.append(f"{name}: metallic must be in [0, 1]")
    elif not is_glass(name) and met > ARCH_MAX_METALLIC:
        errs.append(f"{name}: architectural surfaces are not metallic (metallic {met} > {ARCH_MAX_METALLIC}); "
                    f"only glass or metal trim may be")
    return errs


def validate_materials(materials: dict) -> list[str]:
    errs = []
    for name, m in sorted(materials.items()):
        errs += validate_material(name, m)
    return errs


def texel_density(width_px: float, length_m: float) -> float:
    if length_m <= 0 or width_px <= 0:
        raise ValueError("texel density needs a positive width in pixels and length in metres")
    return width_px / length_m


def density_ratio_ok(densities: list[float]) -> bool:
    """Adjacent equally visible faces differ by at most 2x (production baseline 5.4)."""
    pos = [d for d in densities if d > 0]
    if len(pos) < 2:
        return True
    return max(pos) / min(pos) <= TEXEL_RATIO_MAX


def texel_warnings(densities_by_part: dict) -> list[str]:
    """Faces of one part with texel densities more than 2x apart: a warning per part (production baseline 5.4).
    Only faces of the same part are compared, since the baseline speaks of neighbouring, equally visible faces."""
    out = []
    for part, dens in sorted(densities_by_part.items()):
        if not density_ratio_ok(dens):
            out.append(f"{part}: texel density differs more than {TEXEL_RATIO_MAX:g}x between its faces "
                       f"({min(d for d in dens if d > 0):.0f}-{max(dens):.0f} px/m)")
    return out


def padding_ok(padding_px: int, tile_w: int, tile_h: int, mip_levels: int) -> bool:
    """Atlas padding must survive the mip levels: a tile loses about one texel of padding per level."""
    if tile_w <= 0 or tile_h <= 0 or mip_levels < 0:
        return False
    needed = 2 ** min(mip_levels, 3)
    return padding_px >= needed


def mip_levels(width_px: int, height_px: int) -> int:
    return max(0, int(math.log2(max(1, min(width_px, height_px)))) - 2)


def attribution(textures: list[dict], sources: dict, project_noncommercial: bool = False) -> tuple[dict, list[str]]:
    """Map each automatic texture to the source whose pixels it contains. Returns (map, errors).

    A texture whose source is missing, not allowed to be used as texture, or whose licence needs an author but has none
    is an error. A licence that is not allowed in this project (CC BY-NC in a commercial one) is an error too."""
    from ..textures import licence_allows     # imported here: the Blender builder must not need PIL
    out, errs = {}, []
    for t in textures:
        tid = t.get("id", t.get("file", "?"))
        sid = t.get("source_id")
        if not sid:
            if t.get("auto"):
                errs.append(f"{tid}: automatic texture without a source")
            continue
        src = sources.get(sid)
        if src is None:
            errs.append(f"{tid}: source {sid} is not in the project")
            continue
        if src.get("use") not in TEXTURE_USES:
            errs.append(f"{tid}: source {sid} is not allowed as texture (use={src.get('use')})")
            continue
        lic = src.get("licence") or "unknown"
        if not licence_allows(lic, project_noncommercial):
            errs.append(f"{tid}: licence {lic} is not allowed in this project")
            continue
        if lic in ATTRIBUTION_LICENCES and not src.get("author"):
            errs.append(f"{tid}: licence {lic} needs an author for attribution")
            continue
        out[tid] = {"source_id": sid, "licence": lic, "author": src.get("author", ""),
                    "title": src.get("title") or src.get("path", "")}
    return out, errs


def architectural_warnings(spec: dict) -> list[str]:
    """Walls and roofs must not be metallic. Metal trim, bronze details and glass are allowed. Only the materials a part
    actually uses as wall or roof are checked, so a metal ornament does not trip the rule. Reported as warnings: a dev
    project with an existing finish keeps building, and the warning names what to fix."""
    mats = spec.get("materials") or {}
    out = []
    seen = set()
    for part in spec.get("parts", []):
        pm = part.get("material") or {}
        for slot in ("wall", "roof"):
            name = pm.get(slot, slot)
            if name in seen or name not in mats or is_glass(name):
                continue
            seen.add(name)
            met = mats[name].get("metallic", 0.0)
            if isinstance(met, (int, float)) and met > ARCH_MAX_METALLIC:
                out.append(f"{name} is used as {slot} of {part.get('id')} but metallic is {met} "
                           f"(architectural surfaces should stay at or below {ARCH_MAX_METALLIC})")
    return out
