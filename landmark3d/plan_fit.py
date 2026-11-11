"""Fit the AI's massing to the measured footprint (deterministic, generic).

The AI reads positions and sizes of volumes off a satellite image; its plan shapes are usually right but the whole
model, or single volumes, come out shifted, turned or scaled by a few metres (2026-11-10 benchmark: plan IoU fell to
0.44-0.79 on all four dev landmarks while the earlier specs reached 0.79-0.997). The footprint outline (OSM) is
measured, so the plan is fitted to it:

1. the whole model: shift, small turn and uniform scale about the footprint centre;
2. each volume standing on the ground (largest first): shift and stretch along its own axes within small bounds;
   volumes standing on it (base_z above the ground, centre inside it) move with it.

Each step is kept only when the plan IoU (rasterised union of the volumes against the footprint) improves and the
structure check reports no new error. Heights, roofs and facades are not touched.
"""
from __future__ import annotations

import copy
import math

import numpy as np
from PIL import Image, ImageDraw

from . import geom

RASTER_PX = 256
GLOBAL_STEPS = (2.0, 2.0, 2.0, 0.04)      # initial steps: dx (m), dy (m), turn (deg), scale
GLOBAL_LIMITS = (12.0, 12.0, 12.0, 0.2)   # largest shift (m), turn (deg) and scale change for the whole model
PART_SHIFT = 0.3                           # a volume may move by this share of its size (at least 2 m) ...
PART_SHIFT_MIN = 2.0
PART_STRETCH = 0.2                         # ... and stretch its width and depth by at most 20 %
MIN_GAIN = 0.002                           # an IoU gain below this is not worth a change
GROUND = 0.5                               # base_z at or below this is on the ground


# ----------------------------------------------------------------------------------------------- geometry of parts
def _kind(fp):
    if fp == "main":
        return "main"
    return next((k for k in ("rect", "rounded_rect", "stadium", "circle", "polygon") if isinstance(fp, dict) and k in fp),
                None)


def _centre(part, main):
    poly = geom.footprint_polygon(part["footprint"], main)
    P = np.array(poly, float)
    return P.mean(axis=0)


def _points(part, fn):
    """Features placed by absolute plan position (finial, mast: x and y) move with their part."""
    for ft in part.get("features") or []:
        if isinstance(ft, dict) and ft.get("x") is not None and ft.get("y") is not None:
            x, y = fn(float(ft["x"]), float(ft["y"]))
            ft["x"], ft["y"] = round(x, 3), round(y, 3)


def _move(part, dx, dy):
    _points(part, lambda x, y: (x + dx, y + dy))
    fp = part["footprint"]
    k = _kind(fp)
    if k in ("rect", "rounded_rect", "stadium", "circle"):
        fp[k]["cx"] = round(float(fp[k]["cx"]) + dx, 3)
        fp[k]["cy"] = round(float(fp[k]["cy"]) + dy, 3)
    elif k == "polygon":
        fp["polygon"] = [[round(float(x) + dx, 3), round(float(y) + dy, 3)] for x, y in fp["polygon"]]


def _turn_about(part, cx, cy, deg):
    fp = part["footprint"]
    k = _kind(fp)
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    rot = lambda x, y: (cx + (x - cx) * ca - (y - cy) * sa, cy + (x - cx) * sa + (y - cy) * ca)
    _points(part, rot)
    if k in ("rect", "rounded_rect", "stadium", "circle"):
        x, y = rot(float(fp[k]["cx"]), float(fp[k]["cy"]))
        fp[k]["cx"], fp[k]["cy"] = round(x, 3), round(y, 3)
        if k != "circle":
            fp[k]["rot_deg"] = round((float(fp[k].get("rot_deg", 0.0)) + deg) % 360.0, 3)
    elif k == "polygon":
        fp["polygon"] = [[round(v, 3) for v in rot(float(x), float(y))] for x, y in fp["polygon"]]


def _scale_about(part, cx, cy, s):
    _points(part, lambda x, y: (cx + (x - cx) * s, cy + (y - cy) * s))
    fp = part["footprint"]
    k = _kind(fp)
    if k in ("rect", "rounded_rect", "stadium", "circle"):
        fp[k]["cx"] = round(cx + (float(fp[k]["cx"]) - cx) * s, 3)
        fp[k]["cy"] = round(cy + (float(fp[k]["cy"]) - cy) * s, 3)
        for key in ("w", "d", "r"):
            if key in fp[k]:
                fp[k][key] = round(float(fp[k][key]) * s, 3)
    elif k == "polygon":
        fp["polygon"] = [[round(cx + (float(x) - cx) * s, 3), round(cy + (float(y) - cy) * s, 3)]
                         for x, y in fp["polygon"]]


def _stretch(part, sw, sd):
    """Stretch a volume along its own axes (rect-like: width and depth; circle: radius; polygon: about its centre
    along x/y of its main direction)."""
    fp = part["footprint"]
    k = _kind(fp)
    if k in ("rect", "rounded_rect", "stadium"):
        cx, cy = float(fp[k]["cx"]), float(fp[k]["cy"])
        a = math.radians(float(fp[k].get("rot_deg", 0.0)))
        u, v = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))

        def st(x, y):
            du, dv = (x - cx) * u[0] + (y - cy) * u[1], (x - cx) * v[0] + (y - cy) * v[1]
            return cx + du * sw * u[0] + dv * sd * v[0], cy + du * sw * u[1] + dv * sd * v[1]
        _points(part, st)
        fp[k]["w"] = round(float(fp[k]["w"]) * sw, 3)
        fp[k]["d"] = round(float(fp[k]["d"]) * sd, 3)
        if "r" in fp[k]:
            fp[k]["r"] = round(min(float(fp[k]["r"]), fp[k]["w"] / 2, fp[k]["d"] / 2), 3)
    elif k == "circle":
        fp[k]["r"] = round(float(fp[k]["r"]) * math.sqrt(sw * sd), 3)
    elif k == "polygon":
        P = np.array(fp["polygon"], float)
        c = P.mean(axis=0)
        fp["polygon"] = [[round(c[0] + (x - c[0]) * sw, 3), round(c[1] + (y - c[1]) * sd, 3)] for x, y in P]


def _size(part, main):
    P = np.array(geom.footprint_polygon(part["footprint"], main), float)
    return float(np.linalg.norm(P.max(axis=0) - P.min(axis=0)))


# ----------------------------------------------------------------------------------------------- objective
class _Raster:
    """Plan IoU of a set of parts against the footprint on a fixed grid (the frame is set once from the footprint
    with a margin, so moved parts are measured consistently)."""

    def __init__(self, main, margin: float = 0.35):
        P = np.array(main, float)
        lo, hi = P.min(axis=0), P.max(axis=0)
        pad = (hi - lo) * margin
        self.lo, self.hi = lo - pad, hi + pad
        self.s = RASTER_PX / float(max(self.hi - self.lo))
        self.main = main
        self.F = self._fill([main])

    def _fill(self, polys):
        im = Image.new("1", (RASTER_PX + 2, RASTER_PX + 2))
        d = ImageDraw.Draw(im)
        for poly in polys:
            if len(poly) >= 3:
                d.polygon([((x - self.lo[0]) * self.s, (self.hi[1] - y) * self.s) for x, y in poly], fill=1)
        return np.array(im, bool)

    def iou(self, parts) -> float:
        polys = []
        for p in parts:
            if p.get("enabled", True) is False:
                continue
            try:
                polys.append(geom.footprint_polygon(p["footprint"], self.main))
            except (KeyError, TypeError, ValueError):
                continue
        Pm = self._fill(polys)
        u = (self.F | Pm).sum()
        return float((self.F & Pm).sum() / u) if u else 0.0


# ----------------------------------------------------------------------------------------------- search
def _pattern_search(f, x0, steps, limits, min_step_share=0.06):
    """Maximise f over a box around x0 by coordinate pattern search with halving steps."""
    x = np.array(x0, float)
    best = f(x)
    step = np.array(steps, float)
    floor = step * min_step_share
    while (step > floor).any():
        improved = False
        for i in range(len(x)):
            for sgn in (1, -1):
                y = x.copy()
                y[i] += sgn * step[i]
                if abs(y[i] - x0[i]) > limits[i]:
                    continue
                v = f(y)
                if v > best + 1e-9:
                    x, best, improved = y, v, True
        if not improved:
            step = step / 2
    return x, best


def _apply_global(parts, main, x):
    dx, dy, turn, scale = x
    C = np.array(main, float).mean(axis=0)
    out = copy.deepcopy(parts)
    for p in out:
        if p.get("footprint") == "main":
            continue
        if float(p.get("base_z", 0)) > GROUND:
            # a volume standing on another keeps its size (a tower does not grow with its podium): its centre
            # follows the scaling, its outline does not
            c = _centre(p, main)
            _move(p, (c[0] - C[0]) * scale, (c[1] - C[1]) * scale)
        else:
            _scale_about(p, C[0], C[1], 1.0 + scale)
        _turn_about(p, C[0], C[1], turn)
        _move(p, dx, dy)
    return out


def fit(parts: list, main, structure_errors=None) -> tuple[list, dict]:
    """Fitted copy of the parts and a report {iou_before, iou_after, global, moved}. structure_errors(parts) ->
    list of messages; a step that adds an error is not kept."""
    main = [tuple(p) for p in main]
    r = _Raster(main)
    errs = (lambda ps: len(structure_errors(ps))) if structure_errors else (lambda ps: 0)
    before = r.iou(parts)
    base_errs = errs(parts)
    report = {"iou_before": round(before, 4), "global": None, "moved": []}
    cur, cur_iou = copy.deepcopy(parts), before

    # 1. the whole model
    x, v = _pattern_search(lambda x: r.iou(_apply_global(parts, main, x)), [0, 0, 0, 0], GLOBAL_STEPS, GLOBAL_LIMITS)
    if v > cur_iou + MIN_GAIN:
        cand = _apply_global(parts, main, x)
        if errs(cand) <= base_errs:
            cur, cur_iou = cand, v
            report["global"] = {"dx": round(float(x[0]), 2), "dy": round(float(x[1]), 2),
                                "turn_deg": round(float(x[2]), 2), "scale": round(1 + float(x[3]), 3)}

    # 2. each ground volume, largest first; volumes standing on it follow
    ground = [i for i, p in enumerate(cur) if float(p.get("base_z", 0)) <= GROUND and p.get("enabled", True) is not False
              and p.get("footprint") != "main"]
    ground.sort(key=lambda i: -_size(cur[i], main))
    for i in ground:
        part = cur[i]
        try:
            ring = geom.footprint_polygon(part["footprint"], main)
        except (KeyError, TypeError, ValueError):
            continue
        riders = [j for j, q in enumerate(cur) if j != i and float(q.get("base_z", 0)) > GROUND
                  and q.get("enabled", True) is not False and geom.point_in_polygon(tuple(_centre(q, main)), ring)]
        lim = max(PART_SHIFT_MIN, PART_SHIFT * _size(part, main))

        def trial(x, i=i, riders=riders):
            t = copy.deepcopy(cur)
            _stretch(t[i], 1 + x[2], 1 + x[3])
            _move(t[i], x[0], x[1])
            for j in riders:
                _move(t[j], x[0], x[1])
            return t
        x, v = _pattern_search(lambda x: r.iou(trial(x)), [0, 0, 0, 0], (1.0, 1.0, 0.05, 0.05),
                               (lim, lim, PART_STRETCH, PART_STRETCH))
        if v > cur_iou + MIN_GAIN:
            cand = trial(x)
            if errs(cand) <= base_errs:
                cur, cur_iou = cand, r.iou(cand)
                report["moved"].append({"part": part.get("id"), "dx": round(float(x[0]), 2),
                                        "dy": round(float(x[1]), 2),
                                        "stretch": [round(1 + float(x[2]), 3), round(1 + float(x[3]), 3)],
                                        "with": [cur[j].get("id") for j in riders]})
    report["iou_after"] = round(cur_iou, 4)
    return cur, report
