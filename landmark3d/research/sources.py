"""Structured, licensed sources: Nominatim, Overpass/OSM, Wikidata, Wikimedia Commons."""
from __future__ import annotations

import math
import re
import time

import httpx

from .. import geom
from .. import settings as settings_mod

OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]


def _client(timeout=60):
    return httpx.Client(timeout=timeout, headers={"User-Agent": settings_mod.user_agent()}, follow_redirects=True)


# ----------------------------------------------------------------------------- name matching
def normalize(s: str) -> str:
    import unicodedata
    s = (s or "").replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


GENERIC = {"the", "of", "and", "de", "la", "le", "du", "cua"}


def name_score(query: str, names) -> float:
    """Best token-overlap score (0..1) between the landmark name and any of the candidate names."""
    q = {t for t in normalize(query).split() if len(t) > 1 and t not in GENERIC}
    if not q:
        return 0.0
    best = 0.0
    for n in names:
        toks = {t for t in normalize(n).split() if len(t) > 1 and t not in GENERIC}
        if not toks:
            continue
        inter = len(q & toks)
        best = max(best, inter / len(q | toks) if inter else 0.0, inter / len(q) * (0.9 if inter < len(q) else 1.0) * 0.8)
    return round(best, 3)


def tag_names(tags: dict) -> list[str]:
    keys = ("name", "name:en", "name:vi", "alt_name", "official_name", "old_name", "short_name", "int_name")
    return [tags[k] for k in tags if k in keys or k.startswith("name:")]


GOOD_CATEGORIES = {"building", "amenity", "tourism", "historic", "landuse", "man_made", "leisure"}
BAD_TYPES = {"company", "bus_stop", "station", "stop_position", "platform", "street", "residential", "primary",
             "secondary", "tertiary", "administrative", "suburb", "quarter", "neighbourhood", "city"}


def geocode_ranked(name: str, location: str, aliases: list[str] | None = None) -> list[dict]:
    """Several Nominatim queries, merged and ranked by name match and object category."""
    queries = [f"{name}, {location}" if location else name, name] + [a for a in (aliases or []) if a]
    city = location.split(",")[-2].strip() if location.count(",") >= 1 else ""
    if city:
        queries.append(f"{name}, {city}")
    seen, hits = set(), []
    for q in dict.fromkeys(queries):
        try:
            for h in geocode_raw(q):
                key = (h.get("osm_type"), h.get("osm_id"))
                if key in seen:
                    continue
                seen.add(key)
                names = [h.get("name", ""), h.get("display_name", "").split(",")[0]] + \
                    list((h.get("namedetails") or {}).values())
                score = max(name_score(name, names), max((name_score(a, names) for a in aliases or []), default=0))
                if h.get("category") in GOOD_CATEGORIES:
                    score += 0.15
                if h.get("type") in BAD_TYPES or h.get("osm_type") == "node":
                    score -= 0.2
                h["match_score"] = round(score, 3)
                hits.append(h)
        except httpx.HTTPError:
            continue
    hits.sort(key=lambda h: -h["match_score"])
    return hits


# ----------------------------------------------------------------------------- Nominatim
def geocode(name: str, location: str) -> list[dict]:
    return geocode_raw(f"{name}, {location}" if location else name)


def geocode_raw(q: str) -> list[dict]:
    with _client(30) as c:
        r = c.get("https://nominatim.openstreetmap.org/search",
                  params={"q": q, "format": "jsonv2", "limit": 5, "extratags": 1, "namedetails": 1, "polygon_geojson": 0})
        r.raise_for_status()
        time.sleep(1.0)                      # Nominatim usage policy: max 1 request/second
        return r.json()


# ----------------------------------------------------------------------------- Overpass / OSM
def overpass(query: str) -> dict:
    last = None
    with _client(45) as c:
        for url in OVERPASS:
            try:
                r = c.post(url, data={"data": query})
                if r.status_code == 200:
                    return r.json()
                last = f"{url}: HTTP {r.status_code}"
            except httpx.HTTPError as exc:
                last = f"{url}: {type(exc).__name__}"
    raise RuntimeError(f"Overpass unavailable ({last})")


def osm_element_full(osm_type: str, osm_id: int) -> dict:
    """Fallback without Overpass: OSM API v0.6 full element."""
    with _client(60) as c:
        r = c.get(f"https://api.openstreetmap.org/api/0.6/{osm_type}/{osm_id}/full.json")
        r.raise_for_status()
        return r.json()


def footprint_from_osm_object(osm_type: str, osm_id: int) -> dict | None:
    """Footprint of a known OSM way/relation via the OSM API (no Overpass needed)."""
    if osm_type not in ("way", "relation"):
        return None
    data = osm_element_full(osm_type, osm_id)
    nodes = {e["id"]: e for e in data["elements"] if e["type"] == "node"}
    ways = {e["id"]: e for e in data["elements"] if e["type"] == "way"}
    if osm_type == "way":
        way = ways.get(osm_id)
        if not way:
            return None
        return {"osm_type": "way", "osm_id": osm_id, "tags": way.get("tags", {}), "ring_lonlat": _way_ring(way, nodes)}
    rel = next((e for e in data["elements"] if e["type"] == "relation" and e["id"] == osm_id), None)
    if not rel:
        return None
    outers = [ways[m["ref"]] for m in rel.get("members", []) if m.get("role") == "outer" and m["ref"] in ways]
    if not outers:
        return None
    biggest = max(outers, key=lambda w: len(w.get("nodes", [])))
    return {"osm_type": "relation", "osm_id": osm_id, "tags": rel.get("tags", {}),
            "ring_lonlat": _way_ring(biggest, nodes)}


def reverse_geocode(lat: float, lon: float) -> dict | None:
    with _client(30) as c:
        r = c.get("https://nominatim.openstreetmap.org/reverse",
                  params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 18, "extratags": 1})
        time.sleep(1.0)
        return r.json() if r.status_code == 200 else None


def _way_ring(way, nodes=None):
    if "geometry" in way:
        return [(p["lon"], p["lat"]) for p in way["geometry"]]
    return [(nodes[n]["lon"], nodes[n]["lat"]) for n in way["nodes"] if n in nodes]


def buildings_near(lat: float, lon: float, radius_m: int = 120) -> list[dict]:
    """Buildings and building:parts around a point, with geometry and tags."""
    q = (f"[out:json][timeout:60];("
         f"way(around:{radius_m},{lat},{lon})[building];"
         f"way(around:{radius_m},{lat},{lon})[\"building:part\"];"
         f"relation(around:{radius_m},{lat},{lon})[building];"
         f");out tags geom;")
    data = overpass(q)
    out = []
    for el in data.get("elements", []):
        if el["type"] == "way" and "geometry" in el:
            ring = _way_ring(el)
        elif el["type"] == "relation":
            outers = [m for m in el.get("members", []) if m.get("role") == "outer" and "geometry" in m]
            if not outers:
                continue
            ring = [(p["lon"], p["lat"]) for p in max(outers, key=lambda m: len(m["geometry"]))["geometry"]]
        else:
            continue
        if len(ring) < 3:
            continue
        out.append({"osm_type": el["type"], "osm_id": el["id"], "tags": el.get("tags", {}), "ring_lonlat": ring})
    return out


def pick_main_building(cands: list[dict], lat: float, lon: float, name: str, aliases=None) -> dict | None:
    """A building whose name matches the landmark; else the one containing the point; never 'the largest nearby'.

    The returned dict carries match = {"method", "score"}; a weak match is confirmed by the user at the input gate.
    """
    def local(c):
        return [geom.lonlat_to_local(x, y, lon, lat) for x, y in c["ring_lonlat"]]

    bld = [c for c in cands if "building" in c["tags"]]
    for c in bld:
        names = tag_names(c["tags"])
        c["match"] = {"method": "name", "score": max([name_score(name, names)] +
                                                     [name_score(a, names) for a in aliases or []])}
    named = [c for c in bld if c["match"]["score"] >= 0.5]
    if named:
        best = max(named, key=lambda c: (c["match"]["score"], geom.area(local(c))))
        return best
    containing = [c for c in bld if geom.point_in_polygon((0.0, 0.0), geom.ensure_ccw(local(c)))]
    if containing:
        best = min(containing, key=lambda c: geom.area(local(c)))
        best["match"] = {"method": "contains_point", "score": best["match"]["score"]}
        return best
    return None


def parts_inside(cands: list[dict], main: dict, lat: float, lon: float) -> list[dict]:
    """building:part ways whose centroid lies inside the main footprint."""
    poly = [geom.lonlat_to_local(x, y, lon, lat) for x, y in main["ring_lonlat"]]

    def inside(pt):
        c = False
        n = len(poly)
        for i in range(n):
            (x1, y1), (x2, y2) = poly[i], poly[i - 1]
            if (y1 > pt[1]) != (y2 > pt[1]) and pt[0] < (x2 - x1) * (pt[1] - y1) / (y2 - y1) + x1:
                c = not c
        return c
    out = []
    for c in cands:
        if "building:part" not in c["tags"]:
            continue
        loc = [geom.lonlat_to_local(x, y, lon, lat) for x, y in c["ring_lonlat"]]
        if len(loc) >= 3 and inside(geom.centroid(geom.ensure_ccw(loc))):
            out.append(c)
    return out


def parse_height(v) -> float | None:
    if v is None:
        return None
    m = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(v))
    if not m:
        return None
    x = float(m.group(0).replace(",", "."))
    if "ft" in str(v) or "'" in str(v):
        x *= 0.3048
    return x


# ----------------------------------------------------------------------------- Wikidata
def wikidata_entity(name: str, lat: float | None, lon: float | None, qid: str | None = None) -> dict | None:
    with _client(30) as c:
        if not qid:
            found = []
            for lang in ("en", "vi", "fr", "es", "de"):
                r = c.get("https://www.wikidata.org/w/api.php", params={"action": "wbsearchentities", "search": name,
                                                                        "language": lang, "format": "json", "limit": 7})
                r.raise_for_status()
                found += [h["id"] for h in r.json().get("search", [])]
                if found:
                    break
            if not found:
                return None
            ids = "|".join(dict.fromkeys(found))
        else:
            ids = qid
        r = c.get("https://www.wikidata.org/w/api.php", params={"action": "wbgetentities", "ids": ids, "format": "json",
                                                                "props": "claims|labels|descriptions|sitelinks|aliases"})
        r.raise_for_status()
        ents = list(r.json().get("entities", {}).values())
    best, best_d, best_key = None, None, None
    for e in ents:
        coord = _claim(e, "P625")
        d = 1e9
        if coord and lat is not None:
            d = math.dist((coord["latitude"], coord["longitude"]), (lat, lon)) * 111000
        labels = [v.get("value", "") for v in e.get("labels", {}).values()]
        labels += [a.get("value", "") for v in e.get("aliases", {}).values() for a in v]
        key = (d, -name_score(name, labels), 0 if coord else 1)
        if best is None or key < best_key:
            best, best_d, best_key = e, d, key
    if best is None or (lat is not None and best_d > 2000):
        return None
    return _summarise_entity(best, best_d)


def _claim(e, prop):
    cl = e.get("claims", {}).get(prop)
    if not cl:
        return None
    try:
        return cl[0]["mainsnak"]["datavalue"]["value"]
    except (KeyError, IndexError):
        return None


def _quantity_m(v):
    if not v:
        return None
    amt = float(v["amount"])
    unit = v.get("unit", "")
    if unit.endswith("Q3710"):   # foot
        amt *= 0.3048
    return amt


def _summarise_entity(e, dist):
    coord = _claim(e, "P625") or {}
    labels = e.get("labels", {})
    aliases = [labels[k]["value"] for k in ("en", "vi", "fr") if k in labels]
    aliases += [a["value"] for k in ("en", "vi") for a in e.get("aliases", {}).get(k, [])]
    out = {
        "lat": coord.get("latitude"), "lon": coord.get("longitude"),
        "aliases": list(dict.fromkeys(aliases))[:8],
        "qid": e["id"],
        "label": e.get("labels", {}).get("en", {}).get("value") or next(iter(e.get("labels", {}).values()), {}).get("value"),
        "description": e.get("descriptions", {}).get("en", {}).get("value"),
        "distance_m": round(dist, 1) if dist and dist < 1e8 else None,
        "height_m": _quantity_m(_claim(e, "P2048")),
        "area_m2": _quantity_m(_claim(e, "P2046")),
        "floors": _quantity_m(_claim(e, "P1101")),
        "image": _claim(e, "P18"),
        "commons_category": _claim(e, "P373"),
        "url": f"https://www.wikidata.org/wiki/{e['id']}",
        "wikipedia": {k: v.get("url") or v.get("title") for k, v in e.get("sitelinks", {}).items() if k in ("enwiki", "viwiki")},
    }
    return out


# ----------------------------------------------------------------------------- Wikimedia Commons
LICENCE_MAP = [
    (r"^cc0|public domain|^pd", "public_domain"),
    (r"cc[- ]by[- ]sa", "cc_by_sa"),
    (r"cc[- ]by[- ]nc", "cc_by_nc"),
    (r"cc[- ]by", "cc_by"),
]


def classify_licence(short_name: str) -> str:
    s = (short_name or "").lower()
    for pat, val in LICENCE_MAP:
        if re.search(pat, s):
            return val
    return "unknown"


def commons_images(lat: float, lon: float, category: str | None = None, radius_m: int = 250, limit: int = 40) -> list[dict]:
    titles = []
    with _client(40) as c:
        if category:
            r = c.get("https://commons.wikimedia.org/w/api.php",
                      params={"action": "query", "list": "categorymembers", "cmtitle": f"Category:{category}",
                              "cmtype": "file", "cmlimit": limit, "format": "json"})
            if r.status_code == 200:
                titles += [m["title"] for m in r.json().get("query", {}).get("categorymembers", [])]
        r = c.get("https://commons.wikimedia.org/w/api.php",
                  params={"action": "query", "list": "geosearch", "gscoord": f"{lat}|{lon}", "gsradius": radius_m,
                          "gsnamespace": 6, "gslimit": limit, "format": "json"})
        if r.status_code == 200:
            titles += [g["title"] for g in r.json().get("query", {}).get("geosearch", [])]
        titles = list(dict.fromkeys(titles))[:limit]
        out = []
        for i in range(0, len(titles), 20):
            r = c.get("https://commons.wikimedia.org/w/api.php",
                      params={"action": "query", "titles": "|".join(titles[i:i + 20]), "prop": "imageinfo",
                              "iiprop": "url|size|extmetadata|mime", "iiurlwidth": 1600, "format": "json"})
            if r.status_code != 200:
                continue
            for page in r.json().get("query", {}).get("pages", {}).values():
                info = (page.get("imageinfo") or [{}])[0]
                if not info.get("mime", "").startswith("image/"):
                    continue
                md = info.get("extmetadata", {})
                lic_short = md.get("LicenseShortName", {}).get("value", "")
                out.append({
                    "title": page.get("title"),
                    "url": info.get("thumburl") or info.get("url"),
                    "page": info.get("descriptionurl"),
                    "width": info.get("width"), "height": info.get("height"),
                    "licence_short": lic_short,
                    "licence": classify_licence(lic_short),
                    "author": re.sub(r"<[^>]+>", "", md.get("Artist", {}).get("value", ""))[:200],
                    "description": re.sub(r"<[^>]+>", "", md.get("ImageDescription", {}).get("value", ""))[:300],
                })
    return out


def download(url: str, dest) -> int:
    with _client(120) as c:
        r = c.get(url)
        r.raise_for_status()
        dest.write_bytes(r.content)
        return len(r.content)
