"""Automatic textures from the reference photos (only photos whose licence allows texture use).

1. Facade textures: for each large flat wall face of the final spec, the AI picks the photo that shows it best and
   gives the four corners of that wall in the photo (photos carry a labelled 0-1 grid so it can read positions).
   The quad is rectified (perspective) to a straight-on image and placed on that face as a photo panel.
2. Material swatches: per material, the AI picks a clean, unoccluded sample region (roof tiles, brick, plaster ...)
   and its real width in metres; the crop is made seamless and repeated over the material on every other face.

Entries are written to input/textures.json with "auto": true and replaced on each run; textures the user added by
hand win (same material, or same part + facade). Attribution comes from the photo's source entry.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import geom, textures as textures_mod
from .project import Project

MIN_FACE_M = 6.0
MAX_SWATCH_COLOUR_DIST = 70.0      # sRGB distance between a swatch's mean colour and its material colour
VISIBLE_DEG = 75.0                 # a face is visible when it turns at most this far away from the camera
MAX_FACES = 14
PX_PER_M = 40


def _font(size):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


def faces_of(spec: dict) -> list[dict]:
    """Large flat wall faces of the enabled, untapered parts (index into the CCW outline)."""
    main = [tuple(p) for p in spec["footprint"]["local"]]
    out = []
    for p in spec["parts"]:
        if p.get("enabled", True) is False or geom.is_tapered(p.get("taper")) or float(p["height"]) < 3:
            continue
        try:
            ring = geom.ensure_ccw(geom.footprint_polygon(p["footprint"], main))
        except Exception:
            continue
        n = len(ring)
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            ln = math.dist(a, b)
            if ln < MIN_FACE_M:
                continue
            nx, ny = (b[1] - a[1]) / ln, -(b[0] - a[0]) / ln
            facing = math.degrees(math.atan2(ny, nx)) % 360
            out.append({"face": f"{p['id']}#{i}", "part": p["id"], "edge_index": i, "facing_deg": round(facing),
                        "length_m": round(ln, 1), "height_m": round(float(p["height"]), 1),
                        "z0": float(p["base_z"]), "z1": float(p["base_z"]) + float(p["height"]),
                        "wall_material": (p.get("material") or {}).get("wall", "wall")})
    out.sort(key=lambda f: -f["length_m"] * f["height_m"])
    return out[:MAX_FACES]


def _ang(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def visible_faces(faces: list[dict], views: list[dict], spec: dict) -> dict:
    """{photo n: [(face, relation)]} from the camera directions found by viewpoint matching. relation says how the
    wall is turned as seen from the camera; the list is ordered left to right in the picture."""
    main = [tuple(p) for p in spec["footprint"]["local"]]
    parts = {p["id"]: p for p in spec["parts"]}
    out = {}
    for v in views or []:
        try:
            n, az = int(v["photo"]), float(v["azimuth_deg"])
        except (KeyError, TypeError, ValueError):
            continue
        cam = (math.cos(math.radians(az)), math.sin(math.radians(az)))
        right = (cam[1], -cam[0])                 # camera looks along -cam; its right-hand side
        seen = []
        for f in faces:
            rel = _ang(f["facing_deg"] - az)
            if abs(rel) > VISIBLE_DEG:
                continue
            ring = geom.ensure_ccw(geom.footprint_polygon(parts[f["part"]]["footprint"], main))
            a, b = ring[f["edge_index"]], ring[(f["edge_index"] + 1) % len(ring)]
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            how = ("faces the camera" if abs(rel) < 25 else
                   f"turned {abs(rel):.0f} deg to the {'right' if rel > 0 else 'left'}")
            seen.append((mid[0] * right[0] + mid[1] * right[1], f["face"], how))
        out[n] = [(fid, how) for _, fid, how in sorted(seen)]
    return out


def grid_overlay(img: Image.Image, label: str) -> Image.Image:
    """The photo with a labelled 10 x 10 grid (fractions 0-1, origin top-left) so positions can be read off."""
    im = img.convert("RGB").copy()
    im.thumbnail((1400, 1400))
    W, H = im.size
    d = ImageDraw.Draw(im, "RGBA")
    f = _font(max(12, W // 70))
    for k in range(11):
        x, y = W * k / 10, H * k / 10
        d.line([(x, 0), (x, H)], fill=(255, 255, 0, 110), width=1)
        d.line([(0, y), (W, y)], fill=(255, 255, 0, 110), width=1)
        d.text((min(x + 2, W - 30), 2), f"{k / 10:.1f}", fill=(255, 255, 0, 255), font=f)
        d.text((2, min(y + 2, H - 18)), f"{k / 10:.1f}", fill=(255, 255, 0, 255), font=f)
    d.rectangle((W - 140, H - 34, W, H), fill=(0, 0, 0, 160))
    d.text((W - 132, H - 30), label, fill=(255, 255, 255, 255), font=_font(22))
    return im


def rectify(img: Image.Image, corners, out_w: int, out_h: int) -> Image.Image:
    """Perspective-rectify the quad corners [[x, y] TL, TR, BR, BL] (fractions 0-1) to an out_w x out_h image."""
    W, H = img.size
    (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = [(float(x) * W, float(y) * H) for x, y in corners]
    # PIL QUAD data: upper-left, lower-left, lower-right, upper-right of the source quad
    return img.convert("RGB").transform((out_w, out_h), Image.QUAD, (tlx, tly, blx, bly, brx, bry, trx, try_),
                                        resample=Image.BICUBIC)


def make_tileable(img: Image.Image) -> Image.Image:
    """Seamless tile: blend the crop with its half-offset copy using a feathered cross-shaped mask."""
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    h, w = a.shape[:2]
    b = np.roll(np.roll(a, h // 2, axis=0), w // 2, axis=1)
    yy = np.abs(np.linspace(-1, 1, h))[:, None]
    xx = np.abs(np.linspace(-1, 1, w))[None, :]
    m = np.clip(np.maximum(xx, yy) * 1.6 - 0.6, 0, 1)[..., None]      # 1 near the edges, 0 in the middle
    out = a * (1 - m) + b * m
    return Image.fromarray(out.clip(0, 255).astype(np.uint8))


def _texture_photos(prj: Project, spec: dict) -> list[tuple[str, dict]]:
    srcs = prj.read_json("input/sources.json", {"sources": []})["sources"]
    by_path = {s.get("path"): s for s in srcs}
    order = spec.get("photos") or [s["path"] for s in srcs if s.get("kind") == "photo"]
    out = []
    for p in order:
        s = by_path.get(p)
        if s and s.get("use") in ("reference_and_texture", "texture_permitted") and (prj.root / p).is_file():
            out.append((p, s))
    return out[:6]


PROMPT = """Landmark: {name}. We will texture a 3D model with crops of the attached photos (photo 1 .. photo {n};
each has a yellow grid labelled 0.0-1.0 from the TOP-LEFT corner: x to the right, y downwards).

1) FACADES. Wall faces of the model:
{faces}
{visible}
Cover as many of the listed faces as the photos allow (wings and side blocks too, not only the main front);
give ONE photo per face - the clearest, most straight-on one. For each face that a photo shows clearly and fairly
straight-on (not at a grazing angle), give the photo and
the four corners of THAT WALL in the photo as grid fractions, in order top-left, top-right, bottom-right,
bottom-left (top = the wall's cornice / eave line, bottom = where it meets the ground or the part below; left /
right as seen from outside). Use the whole wall, not a window. Skip faces you cannot see or that are mostly hidden
by trees or vehicles (occlusion 0 = clear, 1 = some, 2 = heavy -> skip).
2) SWATCHES. Materials of the model: {materials}
For the main surface materials (walls, roofs; not small details), give a rectangle (x0, y0, x1, y1 fractions) of
a clean, evenly lit, UNIFORM sample: only that surface - no window, door, ornament, edge, shadow line, sky or
foliage inside the rectangle - seen as straight-on as possible, at least about 1.5 m wide in reality, and the
real width of that rectangle in metres (estimate from storeys ~3.5-5 m, windows ~1-1.5 m, tiles ...). The
rectangle's average colour must be the material's colour given above (it is checked; wrong crops are dropped).
Reply with one JSON object:
{{"facades": [{{"face": "<id>", "photo": n, "corners": [[x,y],[x,y],[x,y],[x,y]], "occlusion": 0|1}}],
  "swatches": [{{"material": "<name>", "photo": n, "box": [x0, y0, x1, y1], "width_m": w}}]}}"""


def plan(prj: Project, provider, spec: dict):
    photos = _texture_photos(prj, spec)
    if not photos:
        return None, [], []
    out_dir = prj.root / "reports" / "autotex"
    out_dir.mkdir(parents=True, exist_ok=True)
    gridded = []
    for k, (p, _) in enumerate(photos, start=1):
        g = grid_overlay(Image.open(prj.root / p), f"photo {k}")
        path = out_dir / f"grid_photo{k}.jpg"
        g.save(path, "JPEG", quality=88)
        gridded.append(path)
    faces = faces_of(spec)
    uses = {}
    for part in spec["parts"]:
        for slot, m in (part.get("material") or {}).items():
            uses.setdefault(m, set()).add(slot)
    mats = {m: {"srgb": v.get("srgb"), "used_as": sorted(uses.get(m, []))} for m, v in spec["materials"].items()
            if m != "opening" and m in uses}
    face_txt = "\n".join(f"- {f['face']}: part {f['part']}, {f['length_m']} m wide, {f['height_m']} m high"
                         for f in faces) or "(none)"
    # photo n here = position in `photos`; the spec's views count photos in spec["photos"] order
    order = {pth: k for k, pth in enumerate(spec.get("photos") or [], start=1)}
    views = []
    for k, (pth, _) in enumerate(photos, start=1):
        for v in spec.get("views") or []:
            if order.get(pth) == v.get("photo"):
                views.append(dict(v, photo=k))
    vis = visible_faces(faces, views, spec)
    vis_txt = ("Which faces each photo shows (from the camera direction of each photo; left to right in the picture):\n" +
               "\n".join(f"- photo {n}: " + ("; ".join(f"{fid} ({how})" for fid, how in lst) or "none of these faces")
                         for n, lst in sorted(vis.items()))) if vis else ""
    prompt = PROMPT.format(name=prj.state()["name"], n=len(photos), faces=face_txt, visible=vis_txt,
                           materials=json.dumps(mats, ensure_ascii=False))
    face_ids = {f["face"] for f in faces}

    def check(o):
        errs = []
        for f in o.get("facades", []):
            if f.get("face") not in face_ids:
                errs.append(f"unknown face {f.get('face')}")
            elif vis and int(f.get("photo", 0)) in vis and f["face"] not in {fid for fid, _ in vis[int(f["photo"])]}:
                errs.append(f"face {f['face']} is not visible in photo {f['photo']} (it points away from that camera)")
            elif not (1 <= int(f.get("photo", 0)) <= len(photos)) or len(f.get("corners") or []) != 4:
                errs.append(f"face {f.get('face')}: photo 1..{len(photos)} and 4 corners required")
        for s in o.get("swatches", []):
            b = s.get("box") or []
            if s.get("material") not in mats or len(b) != 4 or not (1 <= int(s.get("photo", 0)) <= len(photos)):
                errs.append(f"bad swatch {s.get('material')}")
            elif not (b[0] < b[2] and b[1] < b[3]) or float(s.get("width_m", 0)) <= 0:
                errs.append(f"swatch {s.get('material')}: box x0<x1, y0<y1 and width_m > 0 required")
        return errs
    from .research import ai
    obj, _ = ai.ask_json(provider, "You map photos onto a 3D building model. Reply with one JSON object only.",
                         prompt, validator=check, images=gridded, retries=2)
    return obj, photos, faces


def _mean_rgb(img: Image.Image):
    a = np.asarray(img.convert("RGB"), dtype=np.float32).reshape(-1, 3)
    # the plaster colour as the eye reads it: sRGB mean of the middle 80 % by brightness (drops specks and shadows)
    lum = a.mean(axis=1)
    lo, hi = np.percentile(lum, [10, 90])
    a = a[(lum >= lo) & (lum <= hi)] if len(a) > 50 else a
    return a.mean(axis=0)


def colour_match(img: Image.Image, srgb) -> tuple[bool, float, Image.Image]:
    """Is the crop the right material (mean colour close to the material colour)? Returns (ok, distance, crop
    gently balanced towards the material colour)."""
    m = _mean_rgb(img)
    target = np.array(srgb[:3], dtype=np.float32)
    dist = float(np.linalg.norm(m - target))
    gain = np.clip(target / np.maximum(m, 1.0), 0.75, 1.33)
    a = np.asarray(img.convert("RGB"), dtype=np.float32) * gain
    return dist <= MAX_SWATCH_COLOUR_DIST, round(dist, 1), Image.fromarray(a.clip(0, 255).astype(np.uint8))


def apply(prj: Project, spec: dict, obj: dict, photos, faces, spec_hash: str) -> dict:
    """Write rectified facade panels and seamless swatches as auto texture entries."""
    data = textures_mod.load(prj)
    manual = [t for t in data["textures"] if not t.get("auto") and not t.get("from_sources")]
    manual_mats = {t.get("material") for t in manual if t.get("mode") == "repeat"}
    manual_faces = {(t.get("part"), t.get("side")) for t in manual if t.get("mode") == "panel"}
    by_face = {f["face"]: f for f in faces}
    tex_dir = prj.root / "input" / "textures"
    tex_dir.mkdir(parents=True, exist_ok=True)
    for old in tex_dir.glob("auto_*.jpg"):
        old.unlink()
    entries, made_faces, made_swatches, rejected = [], 0, 0, []
    best = {}
    for f in obj.get("facades", []):                   # one photo per face: the least occluded, first given
        if int(f.get("occlusion", 0)) < 2 and (f["face"] not in best or
                                               int(f.get("occlusion", 0)) < int(best[f["face"]].get("occlusion", 0))):
            best[f["face"]] = f
    for k, f in enumerate(best.values(), start=1):
        if int(f.get("occlusion", 0)) >= 2:
            continue
        face = by_face[f["face"]]
        if (face["part"], f"face{face['edge_index']}") in manual_faces:
            continue
        path, src = photos[int(f["photo"]) - 1]
        w = int(min(1024, max(64, face["length_m"] * PX_PER_M)))
        h = int(min(1024, max(64, face["height_m"] * PX_PER_M)))
        try:
            img = rectify(Image.open(prj.root / path), f["corners"], w, h)
        except Exception:
            continue
        rel = f"input/textures/auto_face_{k:02d}.jpg"
        img.save(prj.root / rel, "JPEG", quality=88)
        entries.append({"id": f"TX-AF-{k:02d}", "auto": True, "file": rel, "size": [w, h], "mode": "panel",
                        "part": face["part"], "side": f"face{face['edge_index']}", "edge_index": face["edge_index"],
                        "z0": face["z0"], "z1": face["z1"], "licence": src.get("licence"), "author": src.get("author", ""),
                        "source_id": src.get("id"), "material": None, "tint": None, "crop": None})
        made_faces += 1
    for k, s in enumerate(obj.get("swatches", []), start=1):
        if s["material"] in manual_mats:
            continue
        path, src = photos[int(s["photo"]) - 1]
        im = Image.open(prj.root / path).convert("RGB")
        W, H = im.size
        x0, y0, x1, y1 = [float(v) for v in s["box"]]
        crop = im.crop((int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)))
        if crop.width < 24 or crop.height < 24:
            continue
        srgb = (spec["materials"].get(s["material"]) or {}).get("srgb")
        if srgb:
            ok, dist, crop = colour_match(crop, srgb)
            if not ok:
                rejected.append(f"swatch {s['material']}: colour off by {dist} - not that material")
                continue
        side = min(512, max(crop.width, crop.height))
        tile = make_tileable(crop.resize((side, max(24, int(side * crop.height / crop.width)))))
        rel = f"input/textures/auto_swatch_{k:02d}.jpg"
        tile.save(prj.root / rel, "JPEG", quality=90)
        entries.append({"id": f"TX-AS-{k:02d}", "auto": True, "file": rel, "size": list(tile.size), "mode": "repeat",
                        "material": s["material"], "world_size_m": round(float(s["width_m"]), 2), "tint": None,
                        "licence": src.get("licence"), "author": src.get("author", ""), "source_id": src.get("id"),
                        "part": None, "side": None, "z0": None, "z1": None, "crop": None})
        made_swatches += 1
    data["textures"] = manual + entries
    data["auto_for"] = spec_hash
    prj.write_json("input/textures.json", {"textures": data["textures"], "auto_for": spec_hash})
    return {"facades": made_faces, "swatches": made_swatches, **({"rejected": rejected} if rejected else {})}


def spec_hash(spec: dict) -> str:
    key = json.dumps({"parts": spec.get("parts"), "materials": spec.get("materials"), "photos": spec.get("photos")},
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def run(prj: Project, provider) -> dict:
    """Auto textures for the current spec (skipped when already made for this exact spec)."""
    spec = prj.read_json("spec/landmark_spec.json")
    if not spec:
        return {"skipped": "no spec"}
    h = spec_hash(spec)
    if prj.read_json("input/textures.json", {}).get("auto_for") == h:
        return {"skipped": "up to date"}
    obj, photos, faces = plan(prj, provider, spec)
    if obj is None:
        return {"skipped": "no photo whose licence allows texture use"}
    res = apply(prj, spec, obj, photos, faces, h)
    prj.log("auto textures", res)
    return res


# =============================================================================== v2: bay tiles, repair, QA, exposure
BAY_PX_PER_M = 32
TEX_MAX_W = 1024


def part_faces(spec: dict, part: dict) -> list[dict]:
    """Every wall face of a part that is at least ~one bay long (all sides, not only those seen in photos)."""
    main = [tuple(p) for p in spec["footprint"]["local"]]
    fac = part.get("facade") or {}
    bay = max(1.0, float(fac.get("bay_spacing", 4.0)))
    ring = geom.ensure_ccw(geom.footprint_polygon(part["footprint"], main))
    out = []
    for i in range(len(ring)):
        a, b = ring[i], ring[(i + 1) % len(ring)]
        ln = math.dist(a, b)
        if ln < max(2.0, 0.7 * bay):
            continue
        nx, ny = (b[1] - a[1]) / ln, -(b[0] - a[0]) / ln
        out.append({"face": f"{part['id']}#{i}", "part": part["id"], "edge_index": i,
                    "facing_deg": round(math.degrees(math.atan2(ny, nx)) % 360), "length_m": round(ln, 2),
                    "n_bays": max(1, int(round(ln / bay))), "z0": float(part["base_z"]),
                    "z1": float(part["base_z"]) + float(part["height"])})
    return out


def tile_face(tile: Image.Image, n_bays: int, height_px: int) -> Image.Image:
    """A face texture made of the same clean bay repeated n times (the geometry uses the same bay grid)."""
    t = tile.resize((max(8, int(tile.width * height_px / tile.height)), height_px))
    w = t.width * n_bays
    scale = min(1.0, TEX_MAX_W / w)
    t = t.resize((max(4, int(t.width * scale)), max(8, int(t.height * scale))))
    out = Image.new("RGB", (t.width * n_bays, t.height))
    for i in range(n_bays):
        out.paste(t, (i * t.width, 0))
    return out


def mirror_repair(img: Image.Image, spans) -> Image.Image:
    """Replace occluded horizontal spans [(x0, x1) fractions] with the mirrored symmetric part of the facade."""
    a = np.asarray(img.convert("RGB")).copy()
    W = a.shape[1]
    for x0, x1 in spans or []:
        x0, x1 = max(0.0, float(x0)), min(1.0, float(x1))
        if x1 <= x0:
            continue
        m0, m1 = 1.0 - x1, 1.0 - x0
        if m0 < x1 and m1 > x0:                      # the span covers the centre: nothing clean to mirror
            continue
        src = a[:, int(m0 * W):max(int(m0 * W) + 1, int(m1 * W))][:, ::-1]
        dst_w = max(1, int(x1 * W) - int(x0 * W))
        src = np.asarray(Image.fromarray(src).resize((dst_w, a.shape[0])))
        a[:, int(x0 * W):int(x0 * W) + dst_w] = src
    return Image.fromarray(a)


def harmonise(images: list) -> list:
    """Bring all photo textures to the same brightness (median of their means), so neighbouring faces match."""
    if len(images) < 2:
        return images
    means = [float(np.asarray(im.convert("L"), dtype=np.float32).mean()) for im in images]
    target = float(np.median(means))
    out = []
    for im, m in zip(images, means):
        g = float(np.clip(target / max(m, 1.0), 0.8, 1.25))
        a = np.asarray(im.convert("RGB"), dtype=np.float32) * g
        out.append(Image.fromarray(a.clip(0, 255).astype(np.uint8)))
    return out


PROMPT_V2 = """Landmark: {name}. We texture a 3D model with crops of the attached photos (photo 1 .. photo {n}; each has
a yellow grid labelled 0.0-1.0 from the TOP-LEFT: x to the right, y downwards).

Parts of the model (bay = the facade's repeating unit in metres; faces listed with the photos that show them):
{parts}

For EACH part choose one mode:
- "bays": the facade is a repetition of the same bay (wings, aisles, long blocks - most parts). Give ONE bay: the
  clearest, most straight-on, UNOBSTRUCTED bay (no trees, people, vehicles, poles or signs in front) in any photo:
  the four corners of that bay column, from one bay line / pilaster to the next, from the bottom of the part's wall
  up to its cornice / top of wall, in order top-left, top-right, bottom-right, bottom-left. The tool repeats it on
  every face of the part along the model's bay grid.
- "unique": only for a part whose main face is a one-off composition (an entrance pavilion, a gable front). Give that
  face and the four corners of the WHOLE face in a photo where it is straight-on and clear. Its other faces use the
  "bay" you also give, if any.
- "none": not visible clearly enough anywhere (it keeps its plain material).
Also give swatches for roof materials (a clean uniform rectangle of the roofing and its real width in metres):
{materials}
Reply with one JSON object:
{{"parts": [{{"id": "...", "mode": "bays|unique|none", "bay": {{"photo": n, "corners": [[x,y],[x,y],[x,y],[x,y]]}},
             "face": "<face id>", "face_photo": n, "face_corners": [[x,y],[x,y],[x,y],[x,y]]}}],
  "swatches": [{{"material": "<name>", "photo": n, "box": [x0, y0, x1, y1], "width_m": w}}]}}"""

QA_PROMPT = """These are the facade textures just made for {name} (numbered 1 .. {n} on the contact sheet), each
should look like a straight-on, clean elevation of one wall of the building. For each texture decide:
- "keep": a clean elevation of this building's wall;
- "drop": wrong region (sky, ground, another building, mostly roof), badly distorted, or mostly hidden.
For kept textures, list horizontal spans hidden by trees, people, vehicles or poles as fractions of the width
(e.g. [[0.0, 0.2]]); they will be repaired from the mirrored symmetric part.
When only a horizontal BAND of the wall is clean (typically the upper wall above shop stalls, crowds, parked
vehicles, signboards or awnings), do not drop it: keep it and give that band as "clean_rows": [top, bottom]
fractions of the height (0 = top edge, e.g. [0.0, 0.55]). Only that band is used; the rest of the wall keeps its
modelled openings. Omit clean_rows when the whole height is clean.
Reply with one JSON object: {{"textures": [{{"n": 1, "verdict": "keep|drop", "occluded": [[x0, x1]],
"clean_rows": [top, bottom], "why": "..."}}]}}"""


def plan_v2(prj: Project, provider, spec: dict):
    from .research import ai
    photos = _texture_photos(prj, spec)
    if not photos:
        return None, [], {}
    out_dir = prj.root / "reports" / "autotex"
    out_dir.mkdir(parents=True, exist_ok=True)
    gridded = []
    for k, (p, _) in enumerate(photos, start=1):
        path = out_dir / f"grid_photo{k}.jpg"
        grid_overlay(Image.open(prj.root / p), f"photo {k}").save(path, "JPEG", quality=88)
        gridded.append(path)
    order = {pth: k for k, pth in enumerate(spec.get("photos") or [], start=1)}
    views = [dict(v, photo=k) for k, (pth, _) in enumerate(photos, start=1) for v in spec.get("views") or []
             if order.get(pth) == v.get("photo")]
    parts = {p["id"]: p for p in spec["parts"] if p.get("enabled", True) is not False
             and not geom.is_tapered(p.get("taper")) and (p.get("facade") or {}).get("opening_type") != "curtain_wall"
             and float(p["height"]) >= 3}
    faces = {pid: part_faces(spec, p) for pid, p in parts.items()}
    vis = visible_faces([f for fs in faces.values() for f in fs], views, spec)
    seen_in = {}
    for n, lst in vis.items():
        for fid, how in lst:
            seen_in.setdefault(fid, []).append(f"photo {n} ({how})")
    lines = []
    for pid, p in parts.items():
        fac = p.get("facade") or {}
        lines.append(f"- {pid}: {float(p['height']):g} m high, bay {float(fac.get('bay_spacing', 4)):g} m; faces: " +
                     "; ".join(f"{f['face']} {f['length_m']:g} m" + (f" [{', '.join(seen_in[f['face']])}]"
                                                                       if f['face'] in seen_in else " [not in photos]")
                               for f in faces[pid]))
    roofs = {}
    for p in parts.values():
        m = (p.get("material") or {}).get("roof")
        if m and m in spec["materials"]:
            roofs[m] = spec["materials"][m].get("srgb")
    prompt = PROMPT_V2.format(name=prj.state()["name"], n=len(photos), parts="\n".join(lines),
                              materials=json.dumps(roofs))

    def check(o):
        errs = []
        for q in o.get("parts", []):
            if q.get("id") not in parts:
                errs.append(f"unknown part {q.get('id')}")
                continue
            if q.get("mode") in ("bays", "unique") and q.get("bay"):
                b = q["bay"]
                if not (1 <= int(b.get("photo", 0)) <= len(photos)) or len(b.get("corners") or []) != 4:
                    errs.append(f"{q['id']}: bay needs photo 1..{len(photos)} and 4 corners")
            if q.get("mode") == "unique":
                if q.get("face") not in {f["face"] for f in faces[q["id"]]}:
                    errs.append(f"{q['id']}: unknown face {q.get('face')}")
                elif not (1 <= int(q.get("face_photo", 0)) <= len(photos)) or len(q.get("face_corners") or []) != 4:
                    errs.append(f"{q['id']}: face_photo and 4 face_corners required")
                elif views and q["face"] not in {fid for fid, _ in vis.get(int(q["face_photo"]), [])}:
                    errs.append(f"{q['id']}: face {q['face']} is not visible in photo {q['face_photo']}")
        for s in o.get("swatches", []):
            b = s.get("box") or []
            if len(b) != 4 or not (b[0] < b[2] and b[1] < b[3]) or not (1 <= int(s.get("photo", 0)) <= len(photos)):
                errs.append(f"bad swatch {s.get('material')}")
        return errs
    obj, _ = ai.ask_json(provider, "You texture 3D building models from photos. Reply with one JSON object only.",
                         prompt, validator=check, images=gridded, retries=2)
    return obj, photos, {"parts": parts, "faces": faces}


def build_v2(prj: Project, spec: dict, obj: dict, photos, ctx) -> list:
    """Make the face textures: bay tiles repeated on every face of a part, plus rectified unique faces."""
    items = []
    for q in obj.get("parts", []):
        pid, mode = q["id"], q.get("mode")
        if mode not in ("bays", "unique"):
            continue
        part = ctx["parts"][pid]
        fac = part.get("facade") or {}
        bay = max(1.0, float(fac.get("bay_spacing", 4.0)))
        h_px = int(min(512, max(64, float(part["height"]) * BAY_PX_PER_M)))
        tile, tsrc = None, None
        if q.get("bay"):
            path, tsrc = photos[int(q["bay"]["photo"]) - 1]
            tile = rectify(Image.open(prj.root / path), q["bay"]["corners"], int(max(16, bay * BAY_PX_PER_M)), h_px)
        unique_face = q.get("face") if mode == "unique" else None
        for f in ctx["faces"][pid]:
            if f["face"] == unique_face:
                path, src = photos[int(q["face_photo"]) - 1]
                w = int(min(TEX_MAX_W, max(64, f["length_m"] * BAY_PX_PER_M)))
                img = rectify(Image.open(prj.root / path), q["face_corners"], w, h_px)
                items.append({"face": f, "img": img, "src": src, "kind": "unique"})
            elif tile is not None:
                items.append({"face": f, "img": tile_face(tile, f["n_bays"], h_px), "src": tsrc, "kind": "bays"})
    return items


def qa_v2(prj: Project, provider, items: list) -> list:
    """One AI look at all face textures: drop bad ones, mark occluded spans (repaired by mirroring)."""
    from .research import ai
    if not items:
        return items
    reps, key_of = [], {}
    for it in items:                       # one representative per distinct image (bay tiles repeat on many faces)
        key = (it["face"]["part"], "bays") if it["kind"] == "bays" else it["face"]["face"]
        if key not in key_of:
            key_of[key] = len(reps)
            reps.append(it)
        it["_rep"] = key_of[key]
    cells = []
    for k, it in enumerate(reps, start=1):
        im = it["img"].copy()
        im.thumbnail((480, 240))
        cell = Image.new("RGB", (480, 270), "white")
        cell.paste(im, ((480 - im.width) // 2, 28))
        ImageDraw.Draw(cell).text((6, 4), f"{k}  ({it['face']['part']})", fill="black", font=_font(18))
        cells.append(cell)
    cols = 3
    sheet = Image.new("RGB", (480 * cols, 270 * ((len(cells) + cols - 1) // cols)), "white")
    for i, c in enumerate(cells):
        sheet.paste(c, ((i % cols) * 480, (i // cols) * 270))
    path = prj.root / "reports" / "autotex" / "qa_sheet.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, "JPEG", quality=88)

    def check(o):
        ts = o.get("textures")
        if not isinstance(ts, list):
            return ["textures list required"]
        return [f"bad entry {t}" for t in ts if not (1 <= int(t.get("n", 0)) <= len(reps))
                or t.get("verdict") not in ("keep", "drop")]
    try:
        obj, _ = ai.ask_json(provider, "You check textures for a 3D building model. Reply with one JSON object only.",
                             QA_PROMPT.format(name=prj.state()["name"], n=len(reps)), validator=check,
                             images=[path], retries=1)
    except Exception as exc:
        prj.log("texture QA failed - all textures kept", {"error": str(exc)[:200]})
        return items
    verdict = {int(t["n"]) - 1: t for t in obj["textures"]}
    kept = []
    for it in items:
        v = verdict.get(it["_rep"], {"verdict": "keep"})
        if v["verdict"] == "drop":
            continue
        if v.get("occluded") and it["kind"] == "unique":
            it["img"] = mirror_repair(it["img"], v["occluded"])
        band = clean_band(v.get("clean_rows"))
        if band is not None:
            it["img"], it["face"] = crop_band(it["img"], it["face"], band)
        kept.append(it)
    return kept


MIN_BAND = 0.2


def clean_band(rows):
    """[top, bottom] height fractions of the clean part of a texture, or None (whole height / unusable)."""
    try:
        t, b = sorted(float(x) for x in rows)
    except (TypeError, ValueError):
        return None
    t, b = max(0.0, t), min(1.0, b)
    if b - t < MIN_BAND or (t <= 0.02 and b >= 0.98):
        return None
    return t, b


def crop_band(img, face, band):
    """Keep the clean band of a face texture; the face's z range shrinks to match (top = 0 is the wall top)."""
    t, b = band
    h = img.height
    out = img.crop((0, int(round(t * h)), img.width, max(int(round(t * h)) + 1, int(round(b * h)))))
    z0, z1 = float(face["z0"]), float(face["z1"])
    f = dict(face, z1=round(z1 - t * (z1 - z0), 3), z0=round(z1 - b * (z1 - z0), 3))
    return out, f


def apply_v2(prj: Project, spec: dict, items: list, plan_obj: dict, photos, h: str) -> dict:
    data = textures_mod.load(prj)
    manual = [t for t in data["textures"] if not t.get("auto") and not t.get("from_sources")]
    manual_mats = {t.get("material") for t in manual if t.get("mode") == "repeat"}
    tex_dir = prj.root / "input" / "textures"
    tex_dir.mkdir(parents=True, exist_ok=True)
    for old in tex_dir.glob("auto_*.jpg"):
        old.unlink()
    imgs = harmonise([it["img"] for it in items])
    entries, rejected = [], []
    for k, (it, img) in enumerate(zip(items, imgs), start=1):
        f, src = it["face"], it["src"]
        rel = f"input/textures/auto_face_{k:02d}.jpg"
        img.save(prj.root / rel, "JPEG", quality=88)
        entries.append({"id": f"TX-AF-{k:02d}", "auto": True, "file": rel, "size": list(img.size), "mode": "panel",
                        "part": f["part"], "side": f"face{f['edge_index']}", "edge_index": f["edge_index"],
                        "z0": f["z0"], "z1": f["z1"], "licence": src.get("licence"), "author": src.get("author", ""),
                        "source_id": src.get("id"), "material": None, "tint": None, "crop": None, "kind": it["kind"]})
    for k, s in enumerate(plan_obj.get("swatches", []), start=1):
        if s.get("material") in manual_mats or s.get("material") not in spec["materials"]:
            continue
        path, src = photos[int(s["photo"]) - 1]
        im = Image.open(prj.root / path).convert("RGB")
        W, H = im.size
        x0, y0, x1, y1 = [float(v) for v in s["box"]]
        crop = im.crop((int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)))
        if crop.width < 24 or crop.height < 24:
            continue
        ok, dist, crop = colour_match(crop, spec["materials"][s["material"]]["srgb"])
        if not ok:
            rejected.append(f"swatch {s['material']}: colour off by {dist} - not that material")
            continue
        side = min(512, max(crop.width, crop.height))
        tile = make_tileable(crop.resize((side, max(24, int(side * crop.height / crop.width)))))
        rel = f"input/textures/auto_swatch_{k:02d}.jpg"
        tile.save(prj.root / rel, "JPEG", quality=90)
        entries.append({"id": f"TX-AS-{k:02d}", "auto": True, "file": rel, "size": list(tile.size), "mode": "repeat",
                        "material": s["material"], "world_size_m": round(float(s.get("width_m", 3)), 2), "tint": None,
                        "licence": src.get("licence"), "author": src.get("author", ""), "source_id": src.get("id"),
                        "part": None, "side": None, "z0": None, "z1": None, "crop": None})
    prj.write_json("input/textures.json", {"textures": manual + entries, "auto_for": h})
    res = {"bay_faces": sum(1 for it in items if it["kind"] == "bays"),
           "unique_faces": sum(1 for it in items if it["kind"] == "unique"),
           "swatches": sum(1 for e in entries if e["mode"] == "repeat")}
    if rejected:
        res["rejected"] = rejected
    return res


def run_v2(prj: Project, provider) -> dict:
    spec = prj.read_json("spec/landmark_spec.json")
    if not spec:
        return {"skipped": "no spec"}
    h = spec_hash(spec) + "-v2"
    if prj.read_json("input/textures.json", {}).get("auto_for") == h:
        return {"skipped": "up to date"}
    obj, photos, ctx = plan_v2(prj, provider, spec)
    if obj is None:
        return {"skipped": "no photo whose licence allows texture use"}
    items = build_v2(prj, spec, obj, photos, ctx)
    n_before = len(items)
    items = qa_v2(prj, provider, items)
    res = apply_v2(prj, spec, items, obj, photos, h)
    res["dropped_by_qa"] = n_before - len(items)
    prj.log("auto textures", res)
    return res
