"""Deterministic fixes for photo crops used as facade textures (autotex v2), from the Đức Bà case study (2026-11-11):

- refine_quad: the AI reads quad corners off a 10 x 10 grid, so crops slip by a storey or a bay. Each quad edge is
  moved onto the strongest image line running along it (LSD segments, same direction, overlapping the edge), and
  the corners become the intersections of the snapped edges. Edges with no clear line stay where the AI put them.
- obliqueness: how unequal opposite edges of a quad are; a strongly foreshortened crop is blurred and smeared when
  rectified, so it is rejected rather than stretched.
- centre_bay: rolls a one-bay tile so its window sits in the middle of the bay (bay phase from smarttex.metrics);
  the geometry's openings are centred in their bays, so the photo's must be too.
- auto_qa: rejects or trims textures that measurably are not wall: sky reaching in from the top (trimmed when the
  wall below is enough, otherwise dropped) and roof or gable slopes (dominant diagonal lines).
- match_part_colours: brings the faces of one part to the same colour (Lab median), since one part can be textured
  from several photos taken in different light.
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image

from . import lens, metrics

SNAP_DIST = 0.03             # an image line within this share of the diagonal of a quad edge may replace it
SNAP_ANGLE_DEG = 6.0
MAX_CORNER_MOVE = 0.05       # snapped corners may move at most this share of the diagonal
MIN_OVERLAP = 0.35           # supporting segments must cover this share of the edge
OBLIQUE_MAX = 1.8            # opposite edges differing by more than this factor: too foreshortened to rectify
SKY_TRIM_MIN_KEEP = 0.6      # a texture with sky is trimmed when at least this share of its height remains
DIAGONAL_MAX = 0.45          # share of line length at 20-70 degrees above which a texture is a roof or gable slope


def _px(corners, size):
    W, H = size
    return np.array([[float(x) * W, float(y) * H] for x, y in corners])


def _line_through(p, q):
    l = np.cross(np.r_[p, 1.0], np.r_[q, 1.0])
    return l / (np.linalg.norm(l[:2]) + 1e-12)


def _meet(l1, l2):
    p = np.cross(l1, l2)
    if abs(p[2]) < 1e-9:
        return None
    return p[:2] / p[2]


def _best_line(segs, du, p, q, tol):
    """Line (a, b, c) of the image segments best supporting edge p-q, or None."""
    v = q - p
    L = float(np.linalg.norm(v))
    if L < 1e-6 or not len(segs):
        return None
    u = v / L
    nrm = np.array([-u[1], u[0]])
    par = np.abs(du @ u) >= math.cos(math.radians(SNAP_ANGLE_DEG))
    d0 = (segs[:, :2] - p) @ nrm
    d1 = (segs[:, 2:] - p) @ nrm
    near = par & (np.abs(d0) <= tol) & (np.abs(d1) <= tol)
    if not near.any():
        return None
    t0, t1 = (segs[:, :2] - p) @ u, (segs[:, 2:] - p) @ u
    ov = np.clip(np.minimum(np.maximum(t0, t1), L) - np.maximum(np.minimum(t0, t1), 0), 0, None)
    off = (d0 + d1) / 2
    # group the candidates by offset (2 px bins of the tolerance) and take the offset with the most overlap
    idx = np.nonzero(near & (ov > 0))[0]
    if not len(idx):
        return None
    bins = np.round(off[idx] / max(tol / 15, 1.0)).astype(int)
    best_bin, best_cov = None, 0.0
    for b in np.unique(bins):
        cov = float(ov[idx][np.abs(bins - b) <= 1].sum())
        if cov > best_cov:
            best_bin, best_cov = b, cov
    if best_cov < MIN_OVERLAP * L:
        return None
    sel = idx[np.abs(bins - best_bin) <= 1]
    pts = np.concatenate([segs[sel, :2], segs[sel, 2:]])
    m = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - m)
    d = vt[0]
    return _line_through(m, m + d)


def refine_quad(img: Image.Image, corners) -> tuple[list, int]:
    """Corners [[x, y] TL, TR, BR, BL] (fractions) moved onto image lines. Returns (corners, edges snapped)."""
    W, H = img.size
    grey = np.asarray(img.convert("L"))
    diag = math.hypot(W, H)
    P = _px(corners, (W, H))
    segs = lens.segments(grey, 0.01 * diag)
    if not len(segs):
        return [list(c) for c in corners], 0
    d = segs[:, 2:] - segs[:, :2]
    du = d / (np.linalg.norm(d, axis=1, keepdims=True) + 1e-12)
    tol = SNAP_DIST * diag
    lines, snapped = [], 0
    for i in range(4):
        p, q = P[i], P[(i + 1) % 4]
        l = _best_line(segs, du, p, q, tol)
        if l is None:
            l = _line_through(p, q)
        else:
            snapped += 1
        lines.append(l)
    new = []
    for i in range(4):                     # corner i joins edge i-1 (ending at it) and edge i (starting at it)
        c = _meet(lines[i - 1], lines[i])
        if c is None or np.linalg.norm(c - P[i]) > MAX_CORNER_MOVE * diag:
            return [list(c) for c in corners], 0
        new.append(c)
    new = np.array(new)
    if not _convex(new) or not (0.6 <= _area(new) / max(_area(P), 1e-9) <= 1.6):
        return [list(c) for c in corners], 0
    return [[float(x / W), float(y / H)] for x, y in new], snapped


def _area(P):
    x, y = P[:, 0], P[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _convex(P):
    def cross(u, v):
        return float(u[0] * v[1] - u[1] * v[0])
    s = [cross(P[(i + 1) % 4] - P[i], P[(i + 2) % 4] - P[(i + 1) % 4]) for i in range(4)]
    return all(v > 0 for v in s) or all(v < 0 for v in s)


def obliqueness(corners, size) -> float:
    """Largest ratio between opposite edge lengths of the quad (1 = seen straight on)."""
    P = _px(corners, size)
    e = [np.linalg.norm(P[(i + 1) % 4] - P[i]) for i in range(4)]
    r = [max(e[0], e[2]) / max(min(e[0], e[2]), 1e-9), max(e[1], e[3]) / max(min(e[1], e[3]), 1e-9)]
    return float(max(r))


def centre_bay(tile: Image.Image) -> tuple[Image.Image, float | None]:
    """Roll a one-bay tile so its window is centred. Returns (tile, shift as a share of the width or None)."""
    a = np.asarray(tile.convert("RGB"))
    r = metrics.bay_phase(np.hstack([a, a, a]), 3)
    if r.get("offset") is None or r.get("strength", 0) < metrics.MIN_PERIOD_STRENGTH:
        return tile, None
    shift = 0.5 - r["offset"]
    px = int(round(shift * a.shape[1]))
    if px == 0:
        return tile, 0.0
    return Image.fromarray(np.roll(a, px, axis=1)), round(shift, 3)


def diagonal_share(img: Image.Image) -> float:
    grey = np.asarray(img.convert("L"))
    segs = lens.segments(grey, 0.03 * math.hypot(*grey.shape[::-1]))
    if not len(segs):
        return 0.0
    d = segs[:, 2:] - segs[:, :2]
    ln = np.linalg.norm(d, axis=1)
    ang = np.degrees(np.arctan2(np.abs(d[:, 1]), np.abs(d[:, 0])))
    diag = (ang >= 20) & (ang <= 70)
    return float(ln[diag].sum() / max(ln.sum(), 1e-9))


def sky_rows(img: Image.Image) -> float:
    """Share of the height, from the top, down to which sky reaches in most columns (0 = no sky)."""
    m = metrics.sky_mask(np.asarray(img.convert("RGB")))
    if not m.any():
        return 0.0
    H = m.shape[0]
    depth = np.array([np.nonzero(~col)[0][0] if (~col).any() else H for col in m.T])   # first non-sky row
    return float(np.percentile(depth, 90)) / H


def auto_qa(img: Image.Image) -> tuple[str, str, float]:
    """("keep" | "trim" | "drop", reason, top fraction to cut for "trim")."""
    if diagonal_share(img) > DIAGONAL_MAX:
        return "drop", "mostly diagonal lines: a roof or gable slope, not a wall", 0.0
    if metrics.sky_share(img) > metrics.THRESHOLDS["sky_share_max"]:
        top = min(1.0, sky_rows(img) + 0.02)
        if 1.0 - top >= SKY_TRIM_MIN_KEEP:
            return "trim", f"sky in the top {top:.0%}", top
        return "drop", f"sky reaches {top:.0%} down", 0.0
    return "keep", "", 0.0


SWATCH_PX = 64               # smallest sample window (px of a face texture; textures are about 32 px per metre)
SWATCH_SIZES = (128, 96, 64, 48, 32)  # larger plain samples first (less repetition); smooth stucco may
#                                      only be plain over a small area
SWATCH_COLOUR_MAX = 60.0     # sRGB distance between a sample and the material colour
STRUCTURE_MAX = 9.0          # a plain sample: std of its blurred luminance (large-scale structure) at most this
FLAT_SIGMA = 0.25            # illumination is the Gaussian blur at this share of the sample size


def structure(a: np.ndarray) -> float:
    """Large-scale structure of a sample (pilasters, cornices, shadows, roof ridges): std of the luminance blurred at
    1/12 of its size. Fine texture such as brick courses or tiles is blurred away and does not count."""
    lum = a.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    k = max(3, int(min(lum.shape) / 12) | 1)
    return float(cv2.GaussianBlur(lum, (k, k), 0).std())


def flatten(a: np.ndarray) -> np.ndarray:
    """Remove slow light falloff and blotches: divide by a heavy blur, keep the mean colour."""
    a = a.astype(np.float32)
    sig = max(2.0, FLAT_SIGMA * min(a.shape[:2]))
    low = cv2.GaussianBlur(a, (0, 0), sig)
    out = a / np.maximum(low, 1.0) * a.reshape(-1, 3).mean(axis=0)
    return out.clip(0, 255)


def periodic(a: np.ndarray) -> np.ndarray:
    """Periodic component of an image (Moisan 2011, periodic plus smooth decomposition): it tiles without seams
    and, unlike blending with a shifted copy, leaves no cross-shaped ghosting."""
    a = a.astype(np.float64)
    H, W = a.shape[:2]
    out = np.empty_like(a)
    q = np.arange(H)[:, None]
    r = np.arange(W)[None, :]
    den = 2 * np.cos(2 * np.pi * q / H) + 2 * np.cos(2 * np.pi * r / W) - 4
    den[0, 0] = 1.0
    for c in range(a.shape[2]):
        u = a[..., c]
        v = np.zeros_like(u)
        v[0, :] += u[-1, :] - u[0, :]
        v[-1, :] += u[0, :] - u[-1, :]
        v[:, 0] += u[:, -1] - u[:, 0]
        v[:, -1] += u[:, 0] - u[:, -1]
        sh = np.fft.fft2(v) / den
        sh[0, 0] = 0.0
        out[..., c] = u - np.real(np.fft.ifft2(sh))
    return out.clip(0, 255)


def seamless(img: Image.Image, size: int = 256) -> Image.Image:
    """Flattened, periodic, resized swatch tile."""
    a = periodic(flatten(np.asarray(img.convert("RGB"))))
    return Image.fromarray(a.astype(np.uint8)).resize((size, size), Image.LANCZOS)


def plain_patch(images: list, srgb, sizes=SWATCH_SIZES) -> tuple[Image.Image | None, float | None]:
    """The largest plain sample of the material colour in the images: (patch, structure) or (None, None).
    Windows are scanned at half-size steps; among those within SWATCH_COLOUR_MAX of the colour and STRUCTURE_MAX
    of plainness, the largest size wins, then the plainest."""
    target = np.asarray(srgb[:3], dtype=np.float32)
    for size in sizes:
        best, best_s = None, None
        for im in images:
            a = np.asarray(im.convert("RGB"), dtype=np.float32)
            H, W = a.shape[:2]
            if H < size or W < size:
                continue
            step = max(8, size // 2)
            for y in range(0, H - size + 1, step):
                for x in range(0, W - size + 1, step):
                    win = a[y:y + size, x:x + size]
                    if float(np.linalg.norm(win.reshape(-1, 3).mean(axis=0) - target)) > SWATCH_COLOUR_MAX:
                        continue
                    st = structure(win)
                    if st <= STRUCTURE_MAX and (best_s is None or st < best_s):
                        best, best_s = Image.fromarray(win.astype(np.uint8)), st
        if best is not None:
            return best, best_s
    return None, None


def wall_swatch(images: list, srgb) -> Image.Image | None:
    """A plain wall sample from face textures (kept for callers that only want the patch)."""
    return plain_patch(images, srgb)[0]


def roof_region(photo: Image.Image, corners, rise: float = 0.45, px_per_m_h: int = 256) -> Image.Image | None:
    """The band of the photo just above a wall crop's top edge, rectified along that edge: where the roof of that
    part shows. corners are the wall quad (fractions, TL TR BR BL); the band rises by `rise` of the quad's height."""
    W, H = photo.size
    P = np.array([[x * W, y * H] for x, y in corners], dtype=np.float32)
    tl, tr, br, bl = P
    up_l, up_r = (tl - bl) * rise, (tr - br) * rise
    quad = np.array([tl + up_l, tr + up_r, tr, tl], dtype=np.float32)
    if (quad < -1).any() or (quad[:, 0] > W + 1).any() or (quad[:, 1] > H + 1).any():
        return None
    w = int(max(32, np.linalg.norm(tr - tl)))
    h = int(max(16, np.linalg.norm(up_l)))
    M = cv2.getPerspectiveTransform(quad, np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32))
    return Image.fromarray(cv2.warpPerspective(np.asarray(photo.convert("RGB")), M, (w, h)))


def match_part_colours(items: list) -> None:
    """In place: per part, scale each face texture's colour towards the part's median colour (Lab, gains 0.8-1.25)."""
    by_part = {}
    for it in items:
        by_part.setdefault(it["face"]["part"], []).append(it)
    for its in by_part.values():
        if len(its) < 2:
            continue
        means = [np.median(np.asarray(it["img"].convert("RGB"), dtype=np.float32).reshape(-1, 3), axis=0)
                 for it in its]
        target = np.median(np.array(means), axis=0)
        for it, m in zip(its, means):
            g = np.clip(target / np.maximum(m, 1.0), 0.8, 1.25)
            a = np.asarray(it["img"].convert("RGB"), dtype=np.float32) * g
            it["img"] = Image.fromarray(a.clip(0, 255).astype(np.uint8))


# ------------------------------------------------------------------------------------------- multi-bay tiles
MULTI_BAY_OPTIONS = ((1, 1), (0, 2), (2, 0), (0, 1), (1, 0))   # extra bays (left, right), most preferred first
BAY_MATCH_MIN = 0.5          # neighbouring bays must correlate at least this much to be the same facade
EDGE_MARGIN = 0.01           # extended crops must stay this far inside the photo (share of the size)


def extend_bays(corners, left: int, right: int, size) -> list | None:
    """The quad of `left` more bays to the left and `right` to the right of a one-bay quad, through the bay's own
    homography (exact for a flat facade under perspective). None when it leaves the photo."""
    W, H = size
    P = _px(corners, size).astype(np.float32)
    unit = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(unit, P)
    ext = np.array([[[-left, 0], [1 + right, 0], [1 + right, 1], [-left, 1]]], dtype=np.float32)
    Q = cv2.perspectiveTransform(ext, M)[0]
    m = EDGE_MARGIN
    if (Q[:, 0] < m * W).any() or (Q[:, 0] > (1 - m) * W).any() or (Q[:, 1] < m * H).any() or \
            (Q[:, 1] > (1 - m) * H).any():
        return None
    return [[float(x / W), float(y / H)] for x, y in Q]


def bay_match(strip: Image.Image, n: int) -> float:
    """Lowest correlation between neighbouring bays of an n-bay strip (grey, each bay normalised). A strip that
    runs off its wall, into a neighbouring block or a different facade, scores low."""
    if n < 2:
        return 1.0
    g = np.asarray(strip.convert("L"), dtype=np.float32)
    w = g.shape[1] // n
    bays = [g[:, k * w:(k + 1) * w] for k in range(n)]
    out = []
    for a, b in zip(bays, bays[1:]):
        a = a - a.mean()
        b = b - b.mean()
        d = float(np.linalg.norm(a) * np.linalg.norm(b))
        out.append(float((a * b).sum() / d) if d > 0 else 0.0)
    return min(out)


def centre_bays(tile: Image.Image, n: int = 1) -> tuple[Image.Image, float | None]:
    """Roll an n-bay tile so its windows sit mid-bay (n = 1 is centre_bay)."""
    if n <= 1:
        return centre_bay(tile)
    a = np.asarray(tile.convert("RGB"))
    r = metrics.bay_phase(a, n)
    if r.get("offset") is None or r.get("strength", 0) < metrics.MIN_PERIOD_STRENGTH:
        return tile, None
    shift = 0.5 - r["offset"]
    px = int(round(shift * a.shape[1] / n))
    return (Image.fromarray(np.roll(a, px, axis=1)), round(shift, 3)) if px else (tile, 0.0)


# ------------------------------------------------------------------------------------------- real bay period
PERIOD_MIN_STRENGTH = 0.3    # autocorrelation at the found period, relative to lag 0
REAL_BAY_RATIO = (0.6, 2.5)   # real bays per AI bay accepted; outside, the period is ornament, not windows
WIDE_OPTIONS = ((2, 2), (1, 2), (2, 1), (1, 1), (0, 2), (2, 0), (0, 1), (1, 0))


def window_period(strip: Image.Image) -> tuple[float | None, float]:
    """(period in px, strength) of the window columns across a rectified strip: the first strong peak of the
    autocorrelation of the column darkness in the middle band. (None, 0) when there is no clear period."""
    a = np.asarray(strip.convert("L"), dtype=np.float32)
    H, W = a.shape
    prof = 255.0 - a[int(0.2 * H):int(0.8 * H)].mean(axis=0)
    k = max(3, W // 40)
    prof = prof - np.convolve(prof, np.ones(k * 4) / (k * 4), mode="same")    # remove slow trends
    prof -= prof.mean()
    ac = np.correlate(prof, prof, mode="full")[W - 1:]
    if ac[0] <= 0:
        return None, 0.0
    ac = ac / ac[0]
    lo, hi = max(4, int(0.06 * W)), int(0.6 * W)
    best, best_v = None, 0.0
    for lag in range(lo + 1, hi - 1):
        if ac[lag] > ac[lag - 1] and ac[lag] >= ac[lag + 1] and ac[lag] > best_v:
            best, best_v = lag, float(ac[lag])
            if best_v >= PERIOD_MIN_STRENGTH:
                break                                  # first strong peak = the fundamental period
    if best is None or best_v < PERIOD_MIN_STRENGTH:
        return None, best_v
    return float(best), best_v


def real_bay_tile(strip: Image.Image, period: float) -> Image.Image:
    """One window period cut from the middle of the strip, window centred."""
    a = np.asarray(strip.convert("RGB"))
    H, W = a.shape[:2]
    dark = 255.0 - a[int(0.2 * H):int(0.8 * H)].mean(axis=(0, 2))
    p = period
    phases = np.arange(int(p))
    score = [dark[np.arange(int(c), W, p).astype(int)].sum() for c in phases]
    c = int(phases[int(np.argmax(score))])                      # a window centre
    k = round((W / 2 - c) / p)
    x0 = int(round(c + k * p - p / 2))
    x0 = int(np.clip(x0, 0, W - int(p)))
    return Image.fromarray(a[:, x0:x0 + int(round(p))])


def best_bay_tile(photo: Image.Image, corners, bay_px: int, h_px: int, rectify) -> tuple[Image.Image | None, dict]:
    """Rectify a wide strip around the AI's bay quad, find the real window period and cut exactly one real bay.
    Returns (tile or None, info)."""
    info = {}
    for l, r in WIDE_OPTIONS:
        q = extend_bays(corners, l, r, photo.size)
        if q is None:
            continue
        n = 1 + l + r
        strip = rectify(photo, q, bay_px * n, h_px)
        p, s = window_period(strip)
        info = {"strip_bays": n, "period_px": None if p is None else round(p, 1), "strength": round(s, 2)}
        if p is None:
            continue
        ratio = bay_px / p
        info["real_bays_per_ai_bay"] = round(ratio, 2)
        if not REAL_BAY_RATIO[0] <= ratio <= REAL_BAY_RATIO[1]:
            info["rejected"] = "period too far from the AI's bay (ornament or tracery, not windows)"
            return None, info
        tile = real_bay_tile(strip, p)
        return tile.resize((bay_px, h_px), Image.LANCZOS), info
    return None, info
