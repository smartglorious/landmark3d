"""Find the landmark's footprint when OpenStreetMap does not name it: every building outline around the point is
drawn, numbered, on the satellite view; the AI (with the photos and the researched description) picks the outline(s)
that are the landmark itself, or traces the footprint on the metre grid when no outline fits.

Result: {"main": {osm_type, osm_id, tags, ring_lonlat, match}, "extra": [...]} or None. A low-confidence answer is
returned with a low match score, so the input gate still asks the user to confirm it.
"""
from __future__ import annotations

import math

import httpx
from PIL import Image, ImageDraw, ImageFont

from .. import geom, settings as settings_mod
from . import ai, satellite, sources

PROMPT = """Landmark: {name} ({location}). What we know: {description}

The FIRST attached image is a satellite view (north up, {w:g} x {h:g} m, centred on the landmark's coordinates).
Every building outline OpenStreetMap has in this area is drawn on it with a number (outline colour = label
colour). The grid is labelled in metres: x east, y north, origin = image centre. The other images are photos of
the landmark.

Which numbered outline(s) are the landmark itself? Rules:
- If the landmark is one building inside a larger complex (e.g. a tower standing on a shopping podium), choose the
  landmark's own outline; add the outlines of volumes that belong to the same building (its podium, wings) only
  when the photos show them as part of it.
- If no outline matches the building you see in the satellite view and the photos, trace the landmark's
  footprint yourself as a polygon in the grid's metres (6-30 points, along the base of the walls; tall roofs lean
  in satellite views, so follow the base).
- confidence: "high" when the satellite view and photos make it clear, "medium" when probable, "low" otherwise.
Reply with one JSON object:
{{"choice": "candidates" | "traced", "ids": [n, ...], "polygon": [[x, y], ...], "confidence": "high|medium|low",
  "why": "..."}}"""

COLOURS = [(255, 230, 0), (0, 230, 255), (255, 80, 200), (120, 255, 80), (255, 140, 0), (200, 160, 255)]


def osm_buildings_bbox(lat: float, lon: float, half_w_m: float, half_h_m: float) -> list[dict]:
    """Building outlines in a box via the OSM API map call (used when Overpass is unavailable)."""
    dlat = math.degrees(half_h_m / geom.R_EARTH)
    dlon = math.degrees(half_w_m / (geom.R_EARTH * math.cos(math.radians(lat))))
    bbox = f"{lon - dlon:.6f},{lat - dlat:.6f},{lon + dlon:.6f},{lat + dlat:.6f}"
    with httpx.Client(timeout=60, headers={"User-Agent": settings_mod.user_agent()}) as c:
        r = c.get(f"https://api.openstreetmap.org/api/0.6/map.json?bbox={bbox}")
        r.raise_for_status()
        data = r.json()
    nodes = {e["id"]: e for e in data.get("elements", []) if e["type"] == "node"}
    out = []
    for e in data.get("elements", []):
        if e["type"] != "way" or not ({"building", "building:part"} & set(e.get("tags", {}))):
            continue
        ring = [(nodes[n]["lon"], nodes[n]["lat"]) for n in e.get("nodes", []) if n in nodes]
        if len(ring) >= 4:
            out.append({"osm_type": "way", "osm_id": e["id"], "tags": e.get("tags", {}), "ring_lonlat": ring})
    return out


def candidate_map(img: Image.Image, cands: list[dict], origin) -> tuple[Image.Image, dict]:
    """Draw building outlines (not parts) numbered on the satellite image; returns (image, {n: candidate})."""
    lat0, lon0 = origin
    w_m, h_m = satellite.EXTENT_M
    W, H = img.size
    sx, sy = W / w_m, H / h_m

    def px(x, y):
        return (W / 2 + x * sx, H / 2 - y * sy)
    base = satellite.annotate(img, None, satellite.ESRI_ATTRIBUTION)
    d = ImageDraw.Draw(base, "RGBA")
    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except Exception:
        font = ImageFont.load_default()
    numbered = {}
    blds = [c for c in cands if "building" in c.get("tags", {})]
    blds = [c for c in blds if len(c["ring_lonlat"]) >= 3]
    for c in blds:
        c["_local"] = geom.ensure_ccw([geom.lonlat_to_local(x, y, lon0, lat0) for x, y in c["ring_lonlat"]])
    blds = [c for c in blds if all(abs(x) < w_m / 2 + 20 and abs(y) < h_m / 2 + 20 for x, y in [geom.centroid(c["_local"])])]
    blds.sort(key=lambda c: math.hypot(*geom.centroid(c["_local"])))
    for i, c in enumerate(blds[:40], start=1):
        col = COLOURS[(i - 1) % len(COLOURS)]
        ring = [px(x, y) for x, y in c["_local"]]
        d.line(ring + [ring[0]], fill=col + (255,), width=3)
        cx, cy = px(*geom.centroid(c["_local"]))
        d.rectangle((cx - 15, cy - 14, cx + 15, cy + 14), fill=(0, 0, 0, 170))
        d.text((cx - 11 if i < 10 else cx - 13, cy - 12), str(i), fill=col + (255,), font=font)
        numbered[i] = c
    return base, numbered


def resolve(prj, provider, name: str, location: str, lat: float, lon: float, cands: list[dict], photos: list,
            description: str = "") -> dict | None:
    if provider is None:
        return None
    origin = (lat, lon)
    try:
        img = satellite.fetch_esri(origin)
    except Exception as exc:
        prj.log("footprint AI: no satellite view", {"error": str(exc)[:200]})
        return None
    if not cands:
        try:
            cands = osm_buildings_bbox(lat, lon, satellite.EXTENT_M[0] / 2, satellite.EXTENT_M[1] / 2)
        except Exception as exc:
            prj.log("footprint AI: OSM map call failed", {"error": str(exc)[:200]})
            cands = []
    sheet, numbered = candidate_map(img, cands, origin)
    path = prj.path("input", "geo", "footprint_candidates.jpg")
    sheet.save(path, "JPEG", quality=88)
    w_m, h_m = satellite.EXTENT_M
    prompt = PROMPT.format(name=name, location=location, description=(description or "-")[:1200], w=w_m, h=h_m)

    def check(o):
        if o.get("choice") not in ("candidates", "traced"):
            return ['choice must be "candidates" or "traced"']
        if o["choice"] == "candidates":
            ids = o.get("ids") or []
            if not ids or any(i not in numbered for i in ids):
                return [f"ids must be numbers shown on the map (1..{len(numbered)})"]
        else:
            poly = o.get("polygon") or []
            if len(poly) < 4 or not all(isinstance(p, list) and len(p) == 2 for p in poly):
                return ["polygon needs at least 4 [x, y] points"]
            if abs(geom.area([tuple(p) for p in poly])) < 50:
                return ["traced polygon is too small"]
        return [] if o.get("confidence") in ("high", "medium", "low") else ["confidence is required"]
    try:
        obj, res = ai.ask_json(provider, "You identify buildings on satellite imagery. Reply with one JSON object only.",
                               prompt, validator=check, images=[path] + list(photos)[:3], retries=1)
    except Exception as exc:
        prj.log("footprint AI failed", {"error": str(exc)[:200]})
        return None
    score = {"high": 1.0, "medium": 0.7, "low": 0.3}[obj["confidence"]]
    if obj["choice"] == "candidates":
        chosen = [numbered[i] for i in obj["ids"]]
        main = max(chosen, key=lambda c: abs(geom.area(c["_local"])))
        main = {k: v for k, v in main.items() if not k.startswith("_")}
        main["match"] = {"method": f"ai_satellite_pick:{res.provider}", "score": score}
        extra = [{k: v for k, v in c.items() if not k.startswith("_")} for c in chosen if c is not main and
                 c.get("osm_id") != main["osm_id"]]
    else:
        ring = [satellite.local_to_lonlat(float(x), float(y), origin) for x, y in obj["polygon"]]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        main = {"osm_type": "ai_traced", "osm_id": 0, "tags": {"building": "yes", "name": name},
                "ring_lonlat": ring, "match": {"method": f"ai_satellite_trace:{res.provider}", "score": score}}
        extra = []
    main["why"] = str(obj.get("why", ""))[:300]
    prj.log("footprint found by AI", {"choice": obj["choice"], "confidence": obj["confidence"],
                                      "ids": obj.get("ids"), "why": main["why"][:160]})
    return {"main": main, "extra": extra, "confidence": obj["confidence"], "candidates": len(numbered)}
