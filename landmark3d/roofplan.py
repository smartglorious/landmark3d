"""Roof plan: the building's volumes traced from the satellite plan view before the massing.

A plan view has no perspective: how many roofs the building has, where each one is and its outline can be read off
it far more reliably than from street photos (Đức Bà, 2026-11-12: the massing AI returned 7 to 14 volumes on
different runs, and overlays of oblique photos were too approximate to say which were missing). The AI traces every
roof volume as a polygon in the spec's local metres on a zoomed crop of the Esri image with a 5 m grid; the tool
checks the tracing (valid polygons, standing on the site outline, covering most of it) and the massing must then give
each traced volume a part of its own with that plan. Heights, roofs and facades still come from the photos.
"""
from __future__ import annotations

import json
import math

from PIL import Image, ImageDraw

from . import geom

MARGIN_M = 12.0              # around the site outline in the crop
CROP_PX_PER_M = 10.0         # the crop is drawn at this scale (the Esri view is about 5 px/m)
GRID_M = 5.0
MIN_AREA_M2 = 4.0
INSIDE_MIN = 0.7             # a traced volume stands mostly on the site outline
COVER_MIN = 0.6              # the traced volumes cover most of the site outline
MATCH_MIN = 0.6              # a massing part matches a traced volume when this share of the traced plan is inside it
ROOF_FORMS = ("gable", "hip", "pyramid", "dome", "flat", "shed", "spire", "mansard", "conical")


def _font(size):
    from .research.satellite import _font as f
    return f(size)


def crop(sat_raw: Image.Image, main, extent_m) -> tuple[Image.Image, dict]:
    """A zoomed crop of the raw satellite image around the site outline, with a labelled 5 m grid in local metres and
    the outline in yellow. Returns (image, {"x0", "y1", "s"}) where pixel (u, v) = ((x - x0) * s, (y1 - y) * s)."""
    W, H = sat_raw.size
    w_m, h_m = extent_m
    xs, ys = [p[0] for p in main], [p[1] for p in main]
    x0, x1 = min(xs) - MARGIN_M, max(xs) + MARGIN_M
    y0, y1 = min(ys) - MARGIN_M, max(ys) + MARGIN_M
    to_src = lambda x, y: (W / 2 + x * W / w_m, H / 2 - y * H / h_m)
    a, b = to_src(x0, y1), to_src(x1, y0)
    im = sat_raw.convert("RGB").crop((int(a[0]), int(a[1]), int(b[0]), int(b[1])))
    s = CROP_PX_PER_M
    im = im.resize((max(8, int((x1 - x0) * s)), max(8, int((y1 - y0) * s))), Image.LANCZOS)
    d = ImageDraw.Draw(im, "RGBA")
    f = _font(13)
    px = lambda x, y: ((x - x0) * s, (y1 - y) * s)
    gx = math.ceil(x0 / GRID_M) * GRID_M
    while gx <= x1:
        major = abs(gx % 10) < 1e-6
        d.line([px(gx, y0), px(gx, y1)], fill=(255, 255, 255, 110 if major else 50), width=1)
        if major:
            d.text((px(gx, y0)[0] + 2, im.height - 16), f"{gx:g}", fill=(255, 255, 0, 230), font=f)
        gx += GRID_M
    gy = math.ceil(y0 / GRID_M) * GRID_M
    while gy <= y1:
        major = abs(gy % 10) < 1e-6
        d.line([px(x0, gy), px(x1, gy)], fill=(255, 255, 255, 110 if major else 50), width=1)
        if major:
            d.text((2, px(x0, gy)[1] - 15), f"{gy:g}", fill=(255, 255, 0, 230), font=f)
        gy += GRID_M
    ring = [px(x, y) for x, y in main]
    d.line(ring + [ring[0]], fill=(255, 230, 0, 230), width=2)
    return im, {"x0": x0, "y1": y1, "s": s}


def _sample(poly, n=10):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    pts = [(min(xs) + (max(xs) - min(xs)) * (i + 0.5) / n, min(ys) + (max(ys) - min(ys)) * (j + 0.5) / n)
           for i in range(n) for j in range(n)]
    return [q for q in pts if geom.point_in_polygon(q, poly)]


def errors(obj: dict, main) -> list[str]:
    """Checks of a traced roof plan."""
    vols = obj.get("volumes")
    if not isinstance(vols, list) or len(vols) < 2:
        return ['give "volumes": at least two traced roof volumes, each {"name", "polygon": [[x, y], ...], "roof"}']
    main = [tuple(p) for p in main]
    errs, names = [], set()
    for v in vols:
        if not isinstance(v, dict) or not v.get("name"):
            errs.append(f"every volume needs a name: {str(v)[:60]}")
            continue
        if v["name"] in names:
            errs.append(f'two volumes are named "{v["name"]}"')
        names.add(v["name"])
        try:
            poly = [(float(x), float(y)) for x, y in v.get("polygon") or []]
        except (TypeError, ValueError):
            poly = []
        if len(poly) < 3 or geom.area(poly) < MIN_AREA_M2:
            errs.append(f'"{v["name"]}": the polygon needs 3+ corners and at least {MIN_AREA_M2:g} m2')
            continue
        pts = _sample(poly)
        if pts and sum(geom.inside_with_tol(q, main, 1.0) for q in pts) / len(pts) < INSIDE_MIN:
            errs.append(f'"{v["name"]}" lies mostly outside the yellow site outline: trace only this building')
        if v.get("roof") and v["roof"] not in ROOF_FORMS:
            errs.append(f'"{v["name"]}": roof must be one of {", ".join(ROOF_FORMS)}')
    if not errs:
        site = _sample(main, 20)
        polys = [[(float(x), float(y)) for x, y in v["polygon"]] for v in vols]
        cov = sum(any(geom.point_in_polygon(q, p) for p in polys) for q in site) / max(1, len(site))
        if cov < COVER_MIN:
            errs.append(f"the traced volumes cover only {cov:.0%} of the site outline: trace every roof of the "
                        f"building (low side roofs and chapels too)")
    return errs


def draw(img: Image.Image, frame: dict, vols: list) -> Image.Image:
    """The traced volumes drawn on the crop (for A2 and the massing AI)."""
    out = img.copy()
    d = ImageDraw.Draw(out, "RGBA")
    f = _font(13)
    s, x0, y1 = frame["s"], frame["x0"], frame["y1"]
    for k, v in enumerate(vols):
        ring = [((x - x0) * s, (y1 - y) * s) for x, y in v["polygon"]]
        col = [(0, 200, 255), (255, 80, 200), (120, 255, 120), (255, 160, 0)][k % 4]
        d.polygon(ring, outline=col + (255,), fill=col + (40,))
        cx, cy = sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)
        d.text((cx - 20, cy - 7), v["name"][:18], fill=(255, 255, 255, 255), font=f)
    return out


def match_errors(parts, vols: list, main) -> list[str]:
    """Traced volumes that no massing part stands on (most of the traced plan inside the part's footprint)."""
    main = [tuple(p) for p in main]
    rings = []
    for p in parts or []:
        if p.get("enabled", True) is False or float(p.get("base_z", 0)) > 0.5:
            continue
        try:
            rings.append(geom.footprint_polygon(p["footprint"], main))
        except Exception:
            continue
    out = []
    for v in vols or []:
        poly = [(float(x), float(y)) for x, y in v["polygon"]]
        pts = _sample(poly, 8)
        best = max((sum(geom.point_in_polygon(q, r) for q in pts) / max(1, len(pts)) for r in rings), default=0.0)
        if best < MATCH_MIN:
            out.append(f'the roof-plan volume "{v["name"]}" has no ground part of its own on its traced plan: add a '
                       f'part with footprint {{"polygon": {json.dumps([[round(x, 1), round(y, 1)] for x, y in poly])}}}'
                       f' (or its rect), roof "{v.get("roof", "gable")}"')
    return out


def prompt_section(vols: list) -> str:
    return ("ROOF PLAN, traced from the satellite plan view (local metres, x east, y north). Every volume below is a "
            "ground part of its own with this plan (polygon footprint as given, or a rect of the same outline), and "
            "its roof form; take its heights, the volumes standing on top (tower stages, spires, lanterns) and the "
            "facades from the photos:\n" + "\n".join(
                f'- {v["name"]}: roof {v.get("roof", "?")}, level {v.get("level", "?")}, polygon '
                f'{json.dumps([[round(float(x), 1), round(float(y), 1)] for x, y in v["polygon"]])}' for v in vols))


def trace(prj, provider, main, photos: list, st: dict) -> tuple[list, dict]:
    """AI step: trace the roof volumes on the zoomed satellite crop. Returns (volumes, log entry); [] when there is
    no satellite image or the tracing fails (the massing then runs as before, and the entry says why)."""
    from .research import agent, ai, satellite
    entry = {"step": "roof_plan"}
    raw = prj.root / "input" / "geo" / "satellite.jpg"
    if not raw.is_file():
        entry["result"] = "no satellite image: roof plan skipped"
        return [], entry
    img, frame = crop(Image.open(raw), [tuple(p) for p in main], satellite.EXTENT_M)
    out_dir = prj.root / "reports" / "roof_plan"
    out_dir.mkdir(parents=True, exist_ok=True)
    crop_path = out_dir / "satellite_crop.jpg"
    img.save(crop_path, "JPEG", quality=90)
    prompt = agent.ROOFPLAN_PROMPT.format(name=st["name"], forms=", ".join(ROOF_FORMS),
                                          site=json.dumps([[round(x, 1), round(y, 1)] for x, y in main]))
    try:
        obj, _ = ai.ask_json(provider, agent.MASSING_SYSTEM, prompt, validator=lambda o: errors(o, main),
                             images=[crop_path] + list(photos)[:3], retries=2)
    except Exception as exc:
        entry["result"] = f"roof plan failed, massing without it: {exc}"[:300]
        return [], entry
    vols = [dict(v, polygon=[[round(float(x), 2), round(float(y), 2)] for x, y in v["polygon"]])
            for v in obj["volumes"]]
    prj.write_json("input/geo/roof_plan.json", {"volumes": vols, "notes": obj.get("notes", [])})
    drawn = out_dir / "roof_plan.jpg"
    draw(img, frame, vols).save(drawn, "JPEG", quality=90)
    entry.update({"verdict": f"{len(vols)} roof volumes traced",
                  "differences": [f'{v["name"]}: {v.get("roof", "?")}, {v.get("level", "?")}' for v in vols][:20],
                  "images": [drawn.relative_to(prj.root).as_posix()]})
    prj.log("roof plan traced", {"volumes": len(vols)})
    return vols, entry
