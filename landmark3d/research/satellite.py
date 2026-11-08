"""Satellite plan view for the AI: imagery around the site with the OSM outline and the spec's local grid drawn on.

Sources
  esri    Esri World Imagery tiles (reference only, attribution required; see README for the terms).
  upload  a screenshot the user uploads (north up assumed); stored as is, not georeferenced.
Google imagery is not downloaded automatically: Google's terms do not allow it.

The annotated image covers EXTENT_M (width x height, 3:2) centred on the spec origin (footprint centroid), so the
same area can be rendered from above (orthographic, same extent) and compared side by side with the model.
"""
from __future__ import annotations

import io
import math
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

from .. import geom, settings as settings_mod

ESRI_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
ESRI_ATTRIBUTION = "Imagery: Esri, Maxar, Earthstar Geographics, and the GIS User Community"
EXTENT_M = (240.0, 160.0)          # width x height in metres (3:2, same as the preview renders)
ZOOM = 19
OUT_PX = (1200, 800)


def _tile_xy(lat, lon, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def _font(size):
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def local_to_lonlat(x, y, origin):
    lat0, lon0 = origin
    lon = lon0 + math.degrees(x / (geom.R_EARTH * math.cos(math.radians(lat0))))
    lat = lat0 + math.degrees(y / geom.R_EARTH)
    return lon, lat


def fetch_esri(origin, client: httpx.Client | None = None, zoom: int = ZOOM) -> Image.Image:
    """North-up image covering EXTENT_M around origin (lat, lon), resampled to OUT_PX."""
    w_m, h_m = EXTENT_M
    lon_w, lat_s = local_to_lonlat(-w_m / 2, -h_m / 2, origin)
    lon_e, lat_n = local_to_lonlat(w_m / 2, h_m / 2, origin)
    x0, y0 = _tile_xy(lat_n, lon_w, zoom)
    x1, y1 = _tile_xy(lat_s, lon_e, zoom)
    tx0, ty0, tx1, ty1 = int(x0), int(y0), int(x1), int(y1)
    if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) > 64:
        raise ValueError("satellite area too large")
    own = client is None
    client = client or httpx.Client(timeout=30, headers={"User-Agent": settings_mod.user_agent()})
    try:
        mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                r = client.get(ESRI_URL.format(z=zoom, x=tx, y=ty))
                r.raise_for_status()
                mosaic.paste(Image.open(io.BytesIO(r.content)).convert("RGB"), ((tx - tx0) * 256, (ty - ty0) * 256))
    finally:
        if own:
            client.close()
    crop = (int((x0 - tx0) * 256), int((y0 - ty0) * 256), int((x1 - tx0) * 256), int((y1 - ty0) * 256))
    return mosaic.crop(crop).resize(OUT_PX, Image.LANCZOS)


def annotate(img: Image.Image, main_local, attribution: str) -> Image.Image:
    """Draw the site outline, the local metre grid (labelled), a north arrow, a scale bar and the attribution."""
    w_m, h_m = EXTENT_M
    W, H = img.size
    sx, sy = W / w_m, H / h_m

    def px(x, y):
        return (W / 2 + x * sx, H / 2 - y * sy)
    out = img.convert("RGB").copy()
    d = ImageDraw.Draw(out, "RGBA")
    f, fs = _font(18), _font(14)
    for gx in range(-int(w_m // 2 // 20) * 20, int(w_m // 2) + 1, 20):
        d.line([px(gx, -h_m / 2), px(gx, h_m / 2)], fill=(255, 255, 255, 70 if gx else 160), width=1)
        d.text((px(gx, 0)[0] + 3, H - 20), f"x={gx}", fill=(255, 255, 255, 220), font=fs)
    for gy in range(-int(h_m // 2 // 20) * 20, int(h_m // 2) + 1, 20):
        d.line([px(-w_m / 2, gy), px(w_m / 2, gy)], fill=(255, 255, 255, 70 if gy else 160), width=1)
        d.text((4, px(0, gy)[1] - 18), f"y={gy}", fill=(255, 255, 255, 220), font=fs)
    if main_local:
        ring = [px(x, y) for x, y in main_local]
        d.line(ring + [ring[0]], fill=(255, 230, 0, 255), width=3)
    # north arrow and scale bar
    d.polygon([(W - 40, 20), (W - 52, 52), (W - 28, 52)], fill=(255, 255, 255, 230))
    d.text((W - 46, 54), "N", fill=(255, 255, 255, 255), font=f)
    bar = 50 * sx
    d.rectangle((20, H - 50, 20 + bar, H - 42), fill=(255, 255, 255, 230))
    d.text((20, H - 72), "50 m", fill=(255, 255, 255, 255), font=f)
    d.rectangle((0, 0, W, 24), fill=(0, 0, 0, 120))
    d.text((6, 3), f"North up | yellow = site outline (OSM) | grid = spec metres (x east, y north) | {attribution}",
           fill=(255, 255, 255, 255), font=fs)
    return out


def ensure(prj, main_local, origin, force: bool = False) -> dict | None:
    """Make input/geo/satellite_annotated.jpg (Esri, when enabled) and register it as a reference-only source.
    Returns the source entry, or None when disabled / unavailable (the error is logged, never fatal)."""
    provider = settings_mod.load().get("satellite_provider", "esri")
    raw = prj.root / "input" / "geo" / "satellite.jpg"
    ann = prj.root / "input" / "geo" / "satellite_annotated.jpg"
    srcs = prj.read_json("input/sources.json", {"sources": []})
    entry = next((s for s in srcs["sources"] if s.get("id") == "SRC-SAT"), None)
    if entry and ann.exists() and not force:
        return entry
    if provider != "esri":
        return None
    try:
        img = fetch_esri(origin)
    except Exception as exc:
        prj.log("satellite imagery unavailable", {"error": str(exc)[:200]})
        return None
    raw.parent.mkdir(parents=True, exist_ok=True)
    img.save(raw, "JPEG", quality=88)
    annotate(img, main_local, ESRI_ATTRIBUTION).save(ann, "JPEG", quality=88)
    entry = {"id": "SRC-SAT", "kind": "satellite", "path": "input/geo/satellite_annotated.jpg",
             "title": f"Satellite plan view {EXTENT_M[0]:g} x {EXTENT_M[1]:g} m (Esri World Imagery)",
             "author": ESRI_ATTRIBUTION, "licence": "esri_world_imagery", "use": "reference_only",
             "origin": "esri", "url": "https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9",
             "extent_m": list(EXTENT_M), "center": {"lat": origin[0], "lon": origin[1]}}
    srcs["sources"] = [s for s in srcs["sources"] if s.get("id") != "SRC-SAT"] + [entry]
    prj.write_json("input/sources.json", srcs)
    prj.log("satellite plan view saved", {"provider": "esri", "extent_m": list(EXTENT_M)})
    return entry


def save_upload(prj, data: bytes, filename: str, note: str = "") -> dict:
    """A user-supplied satellite screenshot (north up assumed). Reference only; the user is responsible for its use."""
    ext = Path(filename or "sat.jpg").suffix.lower() or ".jpg"
    rel = f"input/geo/satellite_user{ext}"
    target = prj.root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    Image.open(target).verify()
    srcs = prj.read_json("input/sources.json", {"sources": []})
    entry = {"id": "SRC-SAT-U", "kind": "satellite", "path": rel, "title": filename or "satellite screenshot",
             "author": "uploaded by the user", "licence": "user_supplied", "use": "reference_only", "origin": "user",
             "note": note or "north up assumed; not georeferenced"}
    srcs["sources"] = [s for s in srcs["sources"] if s.get("id") != "SRC-SAT-U"] + [entry]
    prj.write_json("input/sources.json", srcs)
    prj.log("satellite screenshot uploaded", {"path": rel})
    return entry


def images_for_ai(prj) -> list[Path]:
    """Satellite images to show the AI: the annotated Esri view first, then a user screenshot."""
    out = []
    for sid in ("SRC-SAT", "SRC-SAT-U"):
        s = next((x for x in prj.read_json("input/sources.json", {"sources": []})["sources"] if x.get("id") == sid), None)
        if s and (prj.root / s["path"]).is_file():
            out.append(prj.root / s["path"])
    return out
