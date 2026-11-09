"""User textures: repeat textures bound to a material, and photo panels placed on a part face.

Stored in input/textures.json:
  {"textures": [{"id": "TX-01", "file": "input/textures/tx_01.jpg", "licence": "owned", "author": "", "source_id": null,
                 "mode": "repeat", "material": "wall", "world_size_m": 3.0, "tint": [r, g, b] | null,
                 "part": null, "side": null, "z0": null, "z1": null, "crop": [x0, y0, x1, y1] | null}]}
Panels are packed into one atlas image at build time (one material, whatever the number of panels).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from .project import Project

TEXTURE_LICENCES = {"owned", "permission_granted", "public_domain", "cc_by", "cc_by_sa", "cc_by_nc"}
SIDES = ["north", "south", "east", "west", "all"]


def load(prj: Project) -> dict:
    data = prj.read_json("input/textures.json", {"textures": []})
    # Backward compatibility: research photos assigned in A1 with "texture_for".
    for s in prj.read_json("input/sources.json", {"sources": []})["sources"]:
        if s.get("kind") == "photo" and s.get("texture_for") and not any(t.get("source_id") == s["id"] for t in data["textures"]):
            data["textures"].append({"id": f"TX-S-{s['id']}", "file": s["path"], "licence": s.get("licence"),
                                     "author": s.get("author", ""), "source_id": s["id"], "mode": "repeat",
                                     "material": s["texture_for"], "world_size_m": float(s.get("world_size_m", 3.0)),
                                     "tint": None, "from_sources": True})
    return data


def save(prj: Project, data: dict) -> None:
    old = prj.read_json("input/textures.json", {})
    prj.write_json("input/textures.json", {"textures": [t for t in data["textures"] if not t.get("from_sources")],
                                           **({"auto_for": old["auto_for"]} if old.get("auto_for") else {})})


def licence_allows(licence: str, project_noncommercial: bool = False) -> bool:
    """CC BY-NC pixels may be exported only from a project explicitly marked non-commercial (production baseline 4.3)."""
    if licence == "cc_by_nc":
        return project_noncommercial
    return licence in TEXTURE_LICENCES


def add(prj: Project, *, file_bytes: bytes | None = None, filename: str | None = None, source_id: str | None = None,
        licence: str = "owned", author: str = "", mode: str = "repeat", material: str | None = "wall",
        world_size_m: float = 3.0, tint=None, part: str | None = None, side: str | None = None,
        z0: float | None = None, z1: float | None = None, crop=None) -> dict:
    if mode not in ("repeat", "panel"):
        raise ValueError("mode must be repeat or panel")
    data = load(prj)
    n = 1 + max([int(t["id"].split("-")[1]) for t in data["textures"] if t["id"].startswith("TX-") and t["id"].split("-")[1].isdigit()] or [0])
    tid = f"TX-{n:02d}"
    if source_id:
        src = next((s for s in prj.read_json("input/sources.json", {"sources": []})["sources"] if s.get("id") == source_id), None)
        if src is None:
            raise ValueError("unknown source")
        if src.get("use") not in ("reference_and_texture", "texture_permitted"):
            raise ValueError(f"source {source_id} is reference-only ({src.get('licence')}); record permission first")
        rel, licence, author = src["path"], src.get("licence", "unknown"), src.get("author", "")
    else:
        if not file_bytes:
            raise ValueError("upload a file or choose a source photo")
        if not licence_allows(licence):
            raise ValueError(f"licence '{licence}' does not allow texture use")
        ext = Path(filename or "tex.jpg").suffix.lower() or ".jpg"
        rel = f"input/textures/{tid.lower().replace('-', '_')}{ext}"
        prj.path(*rel.split("/")).write_bytes(file_bytes)
    try:
        with Image.open(prj.root / rel) as im:
            size = list(im.size)
    except Exception as exc:
        raise ValueError(f"not a readable image: {exc}")
    if mode == "panel":
        if not part:
            raise ValueError("a panel needs a part")
        if side not in SIDES:
            raise ValueError(f"side must be one of {SIDES}")
    entry = {"id": tid, "file": rel, "size": size, "licence": licence, "author": author, "source_id": source_id,
             "mode": mode, "material": material if mode == "repeat" else None,
             "world_size_m": float(world_size_m or 3.0), "tint": tint, "part": part if mode == "panel" else None,
             "side": side if mode == "panel" else None, "z0": z0, "z1": z1, "crop": crop}
    data["textures"].append(entry)
    save(prj, data)
    prj.log("texture added", {"id": tid, "mode": mode, "material": material, "part": part, "side": side})
    return entry


def remove(prj: Project, tid: str) -> bool:
    if tid.startswith("TX-S-"):
        srcs = prj.read_json("input/sources.json", {"sources": []})
        hit = [s for s in srcs["sources"] if f"TX-S-{s['id']}" == tid and s.get("texture_for")]
        for s in hit:
            s.pop("texture_for", None)
        if hit:
            prj.write_json("input/sources.json", srcs)
        return bool(hit)
    data = load(prj)
    keep = [t for t in data["textures"] if t["id"] != tid]
    if len(keep) == len(data["textures"]):
        return False
    data["textures"] = keep
    save(prj, data)
    return True


# ----------------------------------------------------------------------------- atlas packing
def pack_panels(prj: Project, panels: list[dict], max_px: int = 2048) -> tuple[str | None, dict]:
    """Shelf-pack the panel images (with crops) into one JPEG atlas. Returns (atlas path, {id: [u0, v0, u1, v1]})."""
    if not panels:
        return None, {}
    imgs = []
    for t in panels:
        im = Image.open(prj.root / t["file"]).convert("RGB")
        if t.get("crop"):
            x0, y0, x1, y1 = t["crop"]
            w, h = im.size
            im = im.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
        imgs.append((t["id"], im))
    scale = 1.0
    for _ in range(12):
        x, y, row_h, ok = 0, 0, 0, True
        width = max_px
        cells = []
        for tid, im in sorted(imgs, key=lambda p: -p[1].size[1]):
            w = max(8, int(min(im.size[0], max_px // 2) * scale))
            h = max(8, int(im.size[1] * w / im.size[0]))
            if x + w > width:
                x, y, row_h = 0, y + row_h + 2, 0
            if y + h > max_px:
                ok = False
                break
            cells.append((tid, im, x, y, w, h))
            x += w + 2
            row_h = max(row_h, h)
        if ok:
            break
        scale *= 0.8
    # Atlas only as large as needed (power-of-two sides) to keep the GLB small.
    def pow2(n):
        p = 64
        while p < n:
            p *= 2
        return min(p, max_px)
    aw = pow2(max(x + w for _, _, x, _, w, _ in cells))
    ah = pow2(max(y + h for _, _, _, y, _, h in cells))
    atlas = Image.new("RGB", (aw, ah), (255, 255, 255))
    uv = {}
    for tid, im, x, y, w, h in cells:
        atlas.paste(im.resize((w, h), Image.LANCZOS), (x, y))
        inset = 1
        uv[tid] = [(x + inset) / aw, 1 - (y + h - inset) / ah, (x + w - inset) / aw, 1 - (y + inset) / ah]
    out = prj.path("output", "panel_atlas.jpg")
    atlas.save(out, "JPEG", quality=88, subsampling=0)
    return str(out), uv


def build_job(prj: Project, max_px: int = 2048) -> tuple[dict, list, str | None]:
    """(repeat textures by material, panels with UV rects, atlas path) for the Blender job."""
    data = load(prj)
    repeat, panels = {}, []
    for t in data["textures"]:
        if not licence_allows(t.get("licence") or "unknown") and not t.get("source_id"):
            continue
        if t["mode"] == "repeat" and t.get("material"):
            repeat[t["material"]] = {"file": str(prj.root / t["file"]), "world_size_m": t.get("world_size_m", 3.0),
                                     "tint": t.get("tint")}
        elif t["mode"] == "panel":
            panels.append(t)
    atlas, uv = pack_panels(prj, panels, max_px)
    job_panels = [{"id": t["id"], "part": t["part"], "side": t["side"], "z0": t.get("z0"), "z1": t.get("z1"),
                   "edge_index": t.get("edge_index"), "uv": uv[t["id"]]} for t in panels if t["id"] in uv]
    return repeat, job_panels, atlas
