"""T0: accuracy metrics computed from the face textures alone (no camera needed), and the baseline report.

- sky_share: share of the texture's pixels that are sky connected to the top edge (a wall texture holds none).
- bay_phase: where the windows sit in the texture compared with the modelled bay grid. The window columns are
  found from the periodic darkness of the middle band; the error is the distance of the window centre from the bay
  centre, as a share of the bay (0 = centred, 0.5 = window on the bay line). Not measurable when the texture shows
  no clear period at the modelled bay count.
- seam_de: CIEDE2000 colour difference between the touching edge strips of two neighbouring faces of a part.
All functions are deterministic; images are PIL images or arrays.
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image

METRICS_VERSION = "t0-1.0.0"
SKY_SAT_MIN = 0.12            # blue sky: saturated enough ...
SKY_HUE = (95, 135)           # ... with an OpenCV hue (0-180) between cyan and blue
SKY_VAL_MIN = 0.45
OVERCAST_VAL_MIN = 0.45       # grey or white sky (overcast, or colour-corrected): unsaturated and smooth
OVERCAST_SAT_MAX = 0.15
SMOOTH_GRAD_MAX = 6.0         # mean gradient (0-255 scale) for a smooth region
TINTED_GRAD_MAX = 2.5         # any hue: only a very smooth region counts
SKY_MAX_DEPTH = 0.85          # a sky region ends above this share of the height; one reaching lower is plain wall
MIN_PERIOD_STRENGTH = 0.25    # share of the profile's AC energy at the modelled bay frequency
SEAM_STRIP = 0.04             # edge strip width as a share of the face width (at least 4 px)

THRESHOLDS = {"sky_share_max": 0.02, "seam_de_max": 6.0, "bay_phase_max": 0.10}


def _rgb(img) -> np.ndarray:
    if isinstance(img, Image.Image):
        return np.asarray(img.convert("RGB"))
    a = np.asarray(img)
    return a[..., :3] if a.ndim == 3 else np.repeat(a[..., None], 3, axis=2)


def sky_mask(img, max_depth: float | None = None) -> np.ndarray:
    """Boolean mask of sky pixels connected to the top edge (blue sky or smooth overcast white). max_depth: a region
    reaching lower than this share of the height is not sky (default SKY_MAX_DEPTH, for wall textures; photos pass
    1.0, since sky beside a building reaches far down)."""
    depth = SKY_MAX_DEPTH if max_depth is None else max_depth
    a = _rgb(img).astype(np.uint8)
    hsv = cv2.cvtColor(a, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1] / 255.0, hsv[..., 2] / 255.0
    grey = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY).astype(np.float32)
    grad = np.abs(cv2.Sobel(grey, cv2.CV_32F, 1, 0, ksize=3)) + np.abs(cv2.Sobel(grey, cv2.CV_32F, 0, 1, ksize=3))
    grad = cv2.blur(grad, (7, 7)) / 4.0
    blue = (h >= SKY_HUE[0]) & (h <= SKY_HUE[1]) & (s >= SKY_SAT_MIN) & (v >= SKY_VAL_MIN)
    white = (v >= OVERCAST_VAL_MIN) & (s <= OVERCAST_SAT_MAX) & (grad <= SMOOTH_GRAD_MAX)
    # textures are colour-corrected towards the wall, which can tint the sky any hue: a very smooth bright region
    # reaching the top edge is sky too (a correct wall texture ends at the cornice, which is detailed)
    tinted = (v >= SKY_VAL_MIN) & (grad <= TINTED_GRAD_MAX)
    cand = ((blue & (grad <= 3 * SMOOTH_GRAD_MAX)) | white | tinted).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=4)
    H = cand.shape[0]
    top = [k for k in set(np.unique(lab[0][cand[0] > 0]).tolist()) - {0}
           if stats[k, cv2.CC_STAT_TOP] + stats[k, cv2.CC_STAT_HEIGHT] <= depth * H]   # a plain wall reaches down
    return np.isin(lab, top) if top else np.zeros(cand.shape, bool)


def sky_share(img) -> float:
    m = sky_mask(img)
    return round(float(m.mean()), 4)


def bay_phase(img, n_bays: int, band=(0.25, 0.75)) -> dict:
    """Window placement against n_bays equal bays across the texture width.
    Returns {"error": share of a bay (0-0.5) or None, "strength": periodicity at the bay frequency, "offset": the
    window centre's position inside a bay (0-1, 0.5 = centred)}."""
    a = _rgb(img).astype(np.float32)
    H, W = a.shape[:2]
    if n_bays < 2 or W < 8 * n_bays:
        return {"error": None, "strength": 0.0, "offset": None, "reason": "too few bays or pixels"}
    lum = a[int(band[0] * H):max(int(band[0] * H) + 1, int(band[1] * H))] @ np.array([0.299, 0.587, 0.114], np.float32)
    dark = 255.0 - lum.mean(axis=0)                              # windows are darker than the wall
    dark = dark - dark.mean()
    total = float((dark ** 2).sum())
    if total <= 1e-6:
        return {"error": None, "strength": 0.0, "offset": None, "reason": "flat texture"}
    x = np.arange(W)
    z = (dark * np.exp(-2j * np.pi * n_bays * x / W)).sum()
    strength = float(2 * abs(z) ** 2 / (W * total))              # share of the AC energy in that frequency
    # dark(x) ~ A cos(2 pi n x / W + phi): maximum at x = -phi W / (2 pi n), i.e. at bay fraction -phi / (2 pi)
    offset = float((-np.angle(z) / (2 * np.pi)) % 1.0)
    if strength < MIN_PERIOD_STRENGTH:
        return {"error": None, "strength": round(strength, 3), "offset": round(offset, 3),
                "reason": "no clear window period at the modelled bay count"}
    return {"error": round(abs(offset - 0.5), 3), "strength": round(strength, 3), "offset": round(offset, 3)}


def _lab(rgb: np.ndarray) -> np.ndarray:
    a = (rgb.reshape(-1, 1, 3).astype(np.float32) / 255.0)
    return cv2.cvtColor(a, cv2.COLOR_RGB2Lab).reshape(-1, 3)


def delta_e2000(lab1, lab2) -> float:
    """CIEDE2000 difference of two CIELAB colours (Sharma, Wu and Dalal 2005)."""
    L1, a1, b1 = (float(v) for v in lab1)
    L2, a2, b2 = (float(v) for v in lab2)
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if C1p else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if C2p else 0.0
    dLp, dCp = L2 - L1, C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360) / 2
    else:
        hbp = (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dtheta = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7))
    Sl = 1 + 0.015 * (Lbp - 50) ** 2 / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dtheta)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))


def _strip(img, side: str) -> np.ndarray:
    a = _rgb(img)
    w = max(4, int(round(SEAM_STRIP * a.shape[1])))
    s = a[:, -w:] if side == "right" else a[:, :w]
    keep = ~sky_mask(img)[:, -w:] if side == "right" else ~sky_mask(img)[:, :w]
    px = s[keep] if keep.any() else s.reshape(-1, 3)
    return px.reshape(-1, 3)


def seam_de(left_face, right_face) -> float:
    """CIEDE2000 between the median colour of the right edge of one face and the left edge of the next one."""
    l1 = np.median(_lab(_strip(left_face, "right")), axis=0)
    l2 = np.median(_lab(_strip(right_face, "left")), axis=0)
    return round(delta_e2000(l1, l2), 2)


def texture_report(prj, spec: dict | None = None) -> dict:
    """T0 metrics for the project's current automatic face textures (input/textures.json, mode "panel").
    Written to reports/texture_quality.json under "t0"; returns the report."""
    from .. import autotex
    spec = spec or prj.read_json("spec/landmark_spec.json") or {}
    entries = [t for t in prj.read_json("input/textures.json", {"textures": []})["textures"] if t.get("mode") == "panel"]
    parts = {p["id"]: p for p in spec.get("parts") or []}
    faces = {}
    for pid, p in parts.items():
        try:
            for f in autotex.part_faces(spec, p):
                faces[(pid, f["edge_index"])] = f
        except (KeyError, TypeError, ValueError):
            continue
    rows, imgs = [], {}
    for e in entries:
        f = prj.root / e["file"]
        if not f.is_file():
            continue
        img = Image.open(f).convert("RGB")
        imgs[(e.get("part"), e.get("edge_index"))] = img
        face = faces.get((e.get("part"), e.get("edge_index")))
        bp = bay_phase(img, face["n_bays"]) if face else {"error": None, "reason": "face not in the current spec"}
        rows.append({"id": e["id"], "part": e.get("part"), "edge_index": e.get("edge_index"), "kind": e.get("kind"),
                     "sky_share": sky_share(img), "bay_phase": bp})
    seams = []
    for (pid, i), img in imgs.items():
        ring_n = _ring_len(spec, parts[pid]) if pid in parts and i is not None else 0
        nxt = imgs.get((pid, (i + 1) % ring_n)) if ring_n else None
        if nxt is not None:
            seams.append({"part": pid, "faces": [i, (i + 1) % ring_n], "de2000": seam_de(img, nxt)})
    report = {"version": METRICS_VERSION, "faces": rows, "seams": seams, "summary": summarise(rows, seams)}
    q = prj.read_json("reports/texture_quality.json", {}) or {}
    q["t0"] = report
    prj.write_json("reports/texture_quality.json", q)
    return report


def _ring_len(spec: dict, part: dict) -> int:
    from .. import geom
    try:
        return len(geom.footprint_polygon(part["footprint"], [tuple(p) for p in spec["footprint"]["local"]]))
    except (KeyError, TypeError, ValueError):
        return 0


def summarise(rows: list, seams: list) -> dict:
    sky = [r["sky_share"] for r in rows]
    ph = [r["bay_phase"]["error"] for r in rows if (r.get("bay_phase") or {}).get("error") is not None]
    de = [s["de2000"] for s in seams]
    med = lambda v: round(float(np.median(v)), 4) if v else None
    return {
        "faces": len(rows),
        "sky_share_mean": round(float(np.mean(sky)), 4) if sky else None,
        "faces_over_sky_limit": sum(1 for v in sky if v > THRESHOLDS["sky_share_max"]),
        "bay_phase_median": med(ph), "bay_phase_measured": len(ph),
        "faces_over_phase_limit": sum(1 for v in ph if v > THRESHOLDS["bay_phase_max"]),
        "seam_de_median": med(de), "seams": len(de),
        "seams_over_limit": sum(1 for v in de if v > THRESHOLDS["seam_de_max"]),
        "thresholds": THRESHOLDS,
    }
