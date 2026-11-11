"""Deterministic quality loop (plan M6). No AI, no network: the same build result always gives the same metrics.

Three parts:
- metrics(): reads a build result and its spec and returns metric values, with the metric version and the inputs they
  came from, so a result can be compared later.
- gate(): hard checks that block a result (budgets, texel density, accounting, unexplained low utilization).
- keep(): the keep/reject decision for a change. A higher overall score never hides a drop in a protected category
  (plan M6 task 7).
Geometry and image metrics (plan M6 tasks 1-4), all deterministic:
- plan_iou(): the built parts against the reference footprint, rasterised on a fixed grid.
- mask_iou(): silhouette IoU of two masks of the same size (a fixed-camera render and a reference mask).
- edge_distance(): symmetric mean distance between the edges of two rectified facade images, as a share of the diagonal.
- semantic_scores(): recall and precision of accepted observed elements against the built ones (same class, box IoU).
- facade coverage, utilization, material count and draw calls come from the build result.
"""
from __future__ import annotations

import json
import hashlib
import math

import numpy as np
from PIL import Image, ImageDraw

METRICS_VERSION = "1.1.0"
PLAN_GRID_PX = 512                    # plan IoU raster: the longer side of the joint bounds
EDGE_SIZE_PX = 256                    # rectified facades are compared at this width
EDGE_THRESHOLD = 0.15                 # gradient magnitude (0-1) above which a pixel is an edge
SEMANTIC_IOU = 0.5
UTILIZATION_FLOOR = 0.6               # PRODUCTION_BASELINE 5.5: below this the result must say why
PROTECTED_TOLERANCE = 0.0             # a protected category may not fall at all


def metrics(build: dict, spec: dict) -> dict:
    budgets = spec.get("budgets", {})
    tris = int(build.get("triangles") or 0)
    tri_max = int(budgets.get("triangles_max") or 0) or None
    byt = int(build.get("bytes") or 0)
    byt_max = int(budgets.get("bytes_max") or 0) or None
    alloc = build.get("openings_allocation") or {}
    util = {pid: a.get("target_utilization") for pid, a in alloc.items()}
    reasons = {pid: a.get("low_utilization_reason") for pid, a in alloc.items()}
    texel = [w for w in build.get("warnings", []) if "texel" in w.lower()]
    inputs = {"spec_sha256": _digest(spec), "triangles": tris, "bytes": byt, "budgets": budgets}
    return {
        "metrics_version": METRICS_VERSION,
        "inputs": inputs,
        "triangle_ratio": round(tris / tri_max, 4) if tri_max else None,
        "byte_ratio": round(byt / byt_max, 4) if byt_max else None,
        "material_count": int(build.get("materials") or 0),
        "utilization": util,
        "low_utilization_reasons": reasons,
        "texel_warnings": len(texel),
        "accounting_ok": not any("accounting" in e for e in build.get("errors", [])),
        "draw_calls": build.get("draw_calls"),
        "facade_coverage": _coverage(build.get("facade_coverage") or {}),
        "plan_iou": _plan_iou_of(spec),
    }


def _coverage(cov: dict):
    faces = sum(int(c.get("faces") or 0) for c in cov.values())
    photo = sum(int(c.get("photo_faces") or 0) for c in cov.values())
    return round(photo / faces, 4) if faces else None


def _plan_iou_of(spec: dict):
    fp = (spec.get("footprint") or {}).get("local")
    parts = [p for p in spec.get("parts") or [] if p.get("enabled", True) is not False]
    if not fp or not parts:
        return None
    from . import geom
    main = [tuple(q) for q in fp]
    try:
        polys = [geom.footprint_polygon(p["footprint"], main) for p in parts]
    except (KeyError, TypeError, ValueError):
        return None
    return plan_iou(polys, [main])


def _raster(polys: list, bounds, size: int) -> np.ndarray:
    x0, y0, x1, y1 = bounds
    scale = (size - 1) / max(x1 - x0, y1 - y0, 1e-9)
    w = max(1, int(math.ceil((x1 - x0) * scale)) + 1)
    h = max(1, int(math.ceil((y1 - y0) * scale)) + 1)
    im = Image.new("1", (w, h), 0)
    d = ImageDraw.Draw(im)
    for poly in polys:
        if len(poly) >= 3:
            d.polygon([((x - x0) * scale, (y1 - y) * scale) for x, y in poly], fill=1)
    return np.array(im, dtype=bool)


def plan_iou(built: list, reference: list, size: int = PLAN_GRID_PX) -> float:
    """IoU of the union of the built polygons and the union of the reference polygons (plan view, metres)."""
    pts = [p for poly in list(built) + list(reference) for p in poly]
    if not pts:
        return 0.0
    bounds = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
    return mask_iou(_raster(built, bounds, size), _raster(reference, bounds, size))


def mask_iou(a, b) -> float:
    """IoU of two boolean masks (numpy arrays or PIL images of the same size); two empty masks give 1.0."""
    a, b = _mask(a), _mask(b)
    if a.shape != b.shape:
        raise ValueError(f"masks differ in size: {a.shape} and {b.shape}")
    union = np.logical_or(a, b).sum()
    if union == 0:
        return 1.0
    return round(float(np.logical_and(a, b).sum() / union), 4)


def _mask(m) -> np.ndarray:
    if isinstance(m, Image.Image):
        if m.mode in ("RGBA", "LA"):
            return np.array(m.getchannel("A")) > 127
        return np.array(m.convert("L")) > 127
    return np.asarray(m, dtype=bool)


def edges(img: Image.Image, width: int = EDGE_SIZE_PX, threshold: float = EDGE_THRESHOLD) -> np.ndarray:
    """Edge map of an image resized to `width` (aspect kept): central-difference gradient above `threshold`."""
    g = img.convert("L")
    h = max(2, round(g.height * width / max(1, g.width)))
    a = np.asarray(g.resize((width, h), Image.BILINEAR), dtype=np.float64) / 255.0
    gx = np.zeros_like(a)
    gy = np.zeros_like(a)
    gx[:, 1:-1] = (a[:, 2:] - a[:, :-2]) / 2
    gy[1:-1, :] = (a[2:, :] - a[:-2, :]) / 2
    return np.hypot(gx, gy) > threshold / 2


def _distance_to(mask: np.ndarray) -> np.ndarray:
    """Distance of every pixel to the nearest True pixel (two-pass chamfer, steps 1 and sqrt 2)."""
    big = float(mask.shape[0] + mask.shape[1])
    d = np.where(mask, 0.0, big)
    r2 = math.sqrt(2)
    h, w = d.shape
    for y in range(h):
        row = d[y].copy()
        if y > 0:
            up = d[y - 1]
            row = np.minimum(row, up + 1)
            row[1:] = np.minimum(row[1:], up[:-1] + r2)
            row[:-1] = np.minimum(row[:-1], up[1:] + r2)
        for x in range(1, w):
            if row[x - 1] + 1 < row[x]:
                row[x] = row[x - 1] + 1
        d[y] = row
    for y in range(h - 1, -1, -1):
        row = d[y].copy()
        if y < h - 1:
            dn = d[y + 1]
            row = np.minimum(row, dn + 1)
            row[1:] = np.minimum(row[1:], dn[:-1] + r2)
            row[:-1] = np.minimum(row[:-1], dn[1:] + r2)
        for x in range(w - 2, -1, -1):
            if row[x + 1] + 1 < row[x]:
                row[x] = row[x + 1] + 1
        d[y] = row
    return d


def edge_distance(a: Image.Image, b: Image.Image, width: int = EDGE_SIZE_PX) -> float | None:
    """Symmetric mean distance between the edges of two rectified facade images, as a share of the image diagonal
    (0 means the same edges). The second image is resized to the first. None when either image has no edges."""
    if b.size != a.size:
        b = b.resize(a.size)
    ea, eb = edges(a, width), edges(b, width)
    if not ea.any() or not eb.any():
        return None
    da, db = _distance_to(ea), _distance_to(eb)
    mean = (db[ea].mean() + da[eb].mean()) / 2
    return round(float(mean / math.hypot(*ea.shape)), 5)


def _box_iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def semantic_scores(observed: list, built: list, iou: float = SEMANTIC_IOU) -> dict:
    """Recall and precision of accepted observed elements ({"class", "box": [x0, y0, x1, y1]}) against built ones.
    Greedy one-to-one matching by best IoU within the same class, in a fixed order, so the result is deterministic."""
    pairs = []
    for i, o in enumerate(observed):
        for j, b in enumerate(built):
            if o.get("class") == b.get("class"):
                v = _box_iou(o["box"], b["box"])
                if v >= iou:
                    pairs.append((-v, i, j))
    pairs.sort()
    used_o, used_b = set(), set()
    for _, i, j in pairs:
        if i not in used_o and j not in used_b:
            used_o.add(i)
            used_b.add(j)
    tp = len(used_o)
    by_class = {}
    for c in sorted({str(o.get("class")) for o in observed}):
        idx = [i for i, o in enumerate(observed) if str(o.get("class")) == c]
        by_class[c] = round(sum(1 for i in idx if i in used_o) / len(idx), 4)
    return {"recall": round(tp / len(observed), 4) if observed else None,
            "precision": round(tp / len(built), 4) if built else None,
            "matched": tp, "recall_by_class": by_class}


def line_match(observed: list, built: list, tolerance: float) -> float | None:
    """Share of observed floor or bay lines (fractions 0-1 of the facade) that have a built line within `tolerance`
    (PRODUCTION_BASELINE 5.3 uses 5% of the facade height or width). None without observed lines."""
    if not observed:
        return None
    hit = sum(1 for o in observed if any(abs(float(o) - float(b)) <= tolerance for b in built))
    return round(hit / len(observed), 4)


def frame_coverage(mask) -> float:
    """Share of the frame covered by the building in a comparison render mask (baseline 5.2: 70-95%)."""
    m = _mask(mask)
    return round(float(m.mean()), 4) if m.size else 0.0


# PRODUCTION_BASELINE section 5 thresholds for tier F0/F1: (metric, comparison, value). Medians and minimums are taken
# over the reference views by evaluate(); a metric with no evidence is `not_measurable`, never a pass.
THRESHOLDS_F1 = [
    ("plan_iou", ">=", 0.95),
    ("silhouette_iou_median", ">=", 0.78),
    ("silhouette_iou_min", ">=", 0.62),
    ("edge_distance_median", "<=", 0.045),
    ("edge_distance_main", "<=", 0.035),
    ("floor_line_match", ">=", 0.80),
    ("bay_line_match", ">=", 0.75),
    ("component_recall", ">=", 0.75),
    ("component_precision", ">=", 0.85),
]


def evaluate(values: dict, thresholds=THRESHOLDS_F1) -> dict:
    """Pass, fail or not_measurable for every baseline threshold. `values` holds the metric values by name."""
    out = {}
    for name, op, limit in thresholds:
        v = values.get(name)
        if v is None:
            out[name] = {"status": "not_measurable", "limit": f"{op} {limit}"}
            continue
        ok = v >= limit if op == ">=" else v <= limit
        out[name] = {"status": "pass" if ok else "fail", "value": v, "limit": f"{op} {limit}"}
    return out


def view_summary(silhouettes: list, edge_distances: list, main_edge: float | None = None) -> dict:
    """Median and minimum silhouette IoU and the median edge distance over the reference views."""
    def med(v):
        v = sorted(x for x in v if x is not None)
        if not v:
            return None
        n = len(v)
        return round(v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2, 5)
    sil = [x for x in silhouettes if x is not None]
    return {"silhouette_iou_median": med(sil), "silhouette_iou_min": min(sil) if sil else None,
            "edge_distance_median": med(edge_distances), "edge_distance_main": main_edge}


def gate(m: dict, spec: dict) -> list[str]:
    """Hard checks. An empty list means the result passes the deterministic gate."""
    problems = []
    budgets = spec.get("budgets", {})
    if m["triangle_ratio"] is not None and m["triangle_ratio"] >= 1:
        problems.append(f"triangles over the hard budget ({m['triangle_ratio']:.2f} of the limit)")
    if m["byte_ratio"] is not None and m["byte_ratio"] >= 1:
        problems.append(f"file size over the hard budget ({m['byte_ratio']:.2f} of the limit)")
    if m["material_count"] > int(budgets.get("materials_max", 10)):
        problems.append(f"material count {m['material_count']} over the limit")
    if m["texel_warnings"]:
        problems.append(f"{m['texel_warnings']} texel density warning(s)")
    if not m["accounting_ok"]:
        problems.append("accounting does not match the generated meshes")
    for pid, u in m["utilization"].items():
        if u is not None and u < UTILIZATION_FLOOR and not m["low_utilization_reasons"].get(pid):
            problems.append(f"{pid}: utilization {u:.2f} below {UTILIZATION_FLOOR} without a reason")
    for name, r in evaluate(m).items():
        if r["status"] == "fail":
            problems.append(f"{name} {r['value']} fails the baseline ({r['limit']})")
    return problems


def keep(before: dict, after: dict, protected: set[str]) -> tuple[bool, list[str]]:
    """Keep a change only if no protected category falls. Returns (keep, the categories that fell)."""
    regressions = []
    for name in sorted(protected):
        b, a = before.get(name), after.get(name)
        if b is None or a is None:
            regressions.append(f"{name}: missing score")
        elif a < b - PROTECTED_TOLERANCE:
            regressions.append(f"{name}: {b:.3f} -> {a:.3f}")
    return (not regressions), regressions


def score_digest(m: dict) -> str:
    """Stable digest of a metric set: repeated builds from the same inputs must give the same digest."""
    return hashlib.sha256(json.dumps(m, sort_keys=True).encode("utf-8")).hexdigest()


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()
