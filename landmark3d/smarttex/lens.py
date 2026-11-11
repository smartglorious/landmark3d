"""T1: photo intake for projection. Focal prior from EXIF, lens distortion from line straightness, panorama check.

Distortion uses the one-parameter division model about the image centre, with radii normalised by the half diagonal:
    x_u = c + (x_d - c) / (1 + k * r_d^2)
k is chosen to make long edge chains straightest. Straightness is the median over chains of the RMS distance of
the chain's points from their best-fit line, divided by the image diagonal. A photo whose lines stay bent after the
best k (a stitched panorama, a fisheye or a heavily edited image) is marked "panorama" and is not projected.
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image

K_RANGE = (-0.45, 0.45)
K_STEPS = 91
MIN_CHAIN = 0.2               # an edge chain must span at least this share of the diagonal
SEG_MIN = 0.01                # LSD segments shorter than this share of the diagonal are ignored
LINK_GAP = 0.03               # largest gap between linked segments, share of the diagonal
LINK_OFFSET = 0.006           # largest sideways step between linked segments, share of the diagonal
LINK_TURN_DEG = 6.0           # largest direction change between neighbouring segments
CHAIN_TURN_DEG = 25.0         # largest direction change along a whole chain
MAX_CHAINS = 80
STRAIGHT_MAX = 0.003          # exit gate: straightness after correction, share of the diagonal
PANORAMA_ASPECT = 1.9         # width / height above this (beyond 16:9) is treated as a stitched panorama
MIN_GAIN = 0.85               # a lens correction must cut the straightness residual by at least 15 %
WORK_PX = 1600                # analysis size (long side)


def exif_focal_px(img: Image.Image) -> float | None:
    """Focal length in pixels from the 35 mm equivalent focal length (EXIF 0xA405), for the image as stored."""
    try:
        exif = img.getexif()
        sub = exif.get_ifd(0x8769) if hasattr(exif, "get_ifd") else {}
        f35 = sub.get(0xA405) or exif.get(0xA405)
    except Exception:
        return None
    try:
        f35 = float(f35)
    except (TypeError, ValueError):
        return None
    if not 8 <= f35 <= 600:
        return None
    W, H = img.size
    return f35 / 36.0 * max(W, H)         # 36 mm is the long side of the 35 mm frame


def _grey(img) -> np.ndarray:
    if isinstance(img, Image.Image):
        return np.asarray(img.convert("L"))
    a = np.asarray(img)
    return cv2.cvtColor(a, cv2.COLOR_RGB2GRAY) if a.ndim == 3 else a


def segments(grey: np.ndarray, min_len: float) -> np.ndarray:
    """LSD line segments [x1, y1, x2, y2] at least min_len pixels long."""
    lines = cv2.createLineSegmentDetector().detect(grey)[0]
    if lines is None:
        return np.zeros((0, 4))
    s = np.asarray(lines, dtype=np.float64).reshape(-1, 4)      # OpenCV 4 returns (n, 1, 4), OpenCV 5 (n, 4)
    return s[np.hypot(s[:, 2] - s[:, 0], s[:, 3] - s[:, 1]) >= min_len]


def edge_chains(grey: np.ndarray) -> list[np.ndarray]:
    """Long edge chains (arrays of x, y points): LSD segments linked end to end while the direction turns slowly.
    A radially distorted straight line is broken by LSD into short pieces; linking them recovers the whole, gently
    bent line, which is what the distortion fit needs. Sharp turns (arches, corners) stop a chain."""
    H, W = grey.shape
    diag = math.hypot(W, H)
    segs = segments(grey, SEG_MIN * diag)
    if not len(segs):
        return []
    order = np.argsort(-np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1]))
    used = np.zeros(len(segs), bool)
    gap, off = LINK_GAP * diag, LINK_OFFSET * diag
    chains = []

    def extend(chain, forward):
        a, b = (chain[-1][2:], chain[-1][:2]) if forward else (chain[0][:2], chain[0][2:])
        d0 = np.array(chain[0][2:]) - np.array(chain[0][:2])
        d0 /= np.linalg.norm(d0)
        while True:
            end, prev = np.array(a), np.array(b)
            d = end - prev
            d /= max(np.linalg.norm(d), 1e-9)
            best, best_gap = None, gap
            for j in np.nonzero(~used)[0]:
                p, q = segs[j, :2], segs[j, 2:]
                for s0, s1 in ((p, q), (q, p)):          # s0 is the end near ours
                    v = s0 - end
                    along = float(v @ d)
                    if along < -0.5 * gap or np.linalg.norm(v) > best_gap:
                        continue
                    dir_j = (s1 - s0) / max(np.linalg.norm(s1 - s0), 1e-9)
                    if dir_j @ d < math.cos(math.radians(LINK_TURN_DEG)):
                        continue
                    if abs(float(v @ np.array([-d[1], d[0]]))) > off:
                        continue
                    if abs(float(dir_j @ d0)) < math.cos(math.radians(CHAIN_TURN_DEG)):
                        continue
                    best, best_gap, best_seg = j, float(np.linalg.norm(v)), (s0, s1)
            if best is None:
                return
            used[best] = True
            s0, s1 = best_seg
            if forward:
                chain.append(np.concatenate([s0, s1]))
            else:
                chain.insert(0, np.concatenate([s1, s0]))
            a, b = s1, s0

    for i in order:
        if used[i]:
            continue
        used[i] = True
        chain = [segs[i].copy()]
        extend(chain, True)
        extend(chain, False)
        pts = np.concatenate([np.linspace(s[:2], s[2:], max(4, int(np.hypot(*(s[2:] - s[:2])) // 8))) for s in chain])
        if math.dist(pts[0], pts[-1]) >= MIN_CHAIN * diag:
            chains.append(pts)
        if len(chains) >= MAX_CHAINS:
            break
    return chains


def undistort_points(pts: np.ndarray, k: float, size) -> np.ndarray:
    W, H = size
    c = np.array([W / 2.0, H / 2.0])
    s = math.hypot(W, H) / 2.0
    d = (pts - c) / s
    r2 = (d ** 2).sum(axis=1, keepdims=True)
    return c + s * d / (1.0 + k * r2)


def _residual(seg: np.ndarray) -> float:
    m = seg.mean(axis=0)
    _, sv, _ = np.linalg.svd(seg - m, full_matrices=False)
    return float(sv[-1] / math.sqrt(len(seg)))          # RMS distance from the total-least-squares line


def _is_vertical(chain: np.ndarray) -> bool:
    d = chain[-1] - chain[0]
    return abs(d[1]) > abs(d[0]) * math.tan(math.radians(60))


def straightness(chains: list, k: float, size) -> float | None:
    """The larger of the median residuals of the near-vertical and of the other chains (each group needs 3 chains
    to count), as a share of the diagonal. Groups are kept apart because a panorama bends only its horizontals,
    and straight verticals would otherwise hide that in one median."""
    if not chains:
        return None
    diag = math.hypot(*size)
    res = [(_is_vertical(c), _residual(undistort_points(c, k, size))) for c in chains]
    groups = [[r for v, r in res if v == flag] for flag in (True, False)]
    meds = [float(np.median(g)) for g in groups if len(g) >= 3] or [float(np.median([r for _, r in res]))]
    return max(meds) / diag


def fit_division(chains: list, size) -> tuple[float, float | None, float | None]:
    """(k, straightness before, straightness after)."""
    if len(chains) < 3:
        return 0.0, straightness(chains, 0.0, size), straightness(chains, 0.0, size)
    ks = np.linspace(K_RANGE[0], K_RANGE[1], K_STEPS)
    costs = [straightness(chains, float(k), size) for k in ks]
    i = int(np.argmin(costs))
    lo, hi = ks[max(0, i - 1)], ks[min(len(ks) - 1, i + 1)]
    for _ in range(30):                                   # golden-section refinement around the best grid step
        a, b = lo + 0.382 * (hi - lo), lo + 0.618 * (hi - lo)
        if straightness(chains, a, size) < straightness(chains, b, size):
            hi = b
        else:
            lo = a
    k = float((lo + hi) / 2)
    before = straightness(chains, 0.0, size)
    after = straightness(chains, k, size)
    # a correction is kept only when it clearly straightens the lines and is not stuck at the model's limit;
    # otherwise the fit is noise and applying it would add distortion
    if after > MIN_GAIN * before or abs(k) >= 0.98 * K_RANGE[1]:
        k, after = 0.0, before
    return k, before, after


def undistort_image(img: Image.Image, k: float) -> Image.Image:
    """Resample the photo to the division model's undistorted geometry (same size, same centre)."""
    if abs(k) < 1e-6:
        return img.convert("RGB")
    a = np.asarray(img.convert("RGB"))
    H, W = a.shape[:2]
    c = np.array([W / 2.0, H / 2.0])
    s = math.hypot(W, H) / 2.0
    xs, ys = np.meshgrid(np.arange(W, dtype=np.float64), np.arange(H, dtype=np.float64))
    ux, uy = (xs - c[0]) / s, (ys - c[1]) / s
    ru = np.hypot(ux, uy)
    # inverse of r_u = r_d / (1 + k r_d^2): k r_u r_d^2 - r_d + r_u = 0
    disc = np.clip(1 - 4 * k * ru ** 2, 0, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        rd = np.where(ru > 1e-9, (1 - np.sqrt(disc)) / (2 * k * ru), 0.0)
        scale = np.where(ru > 1e-9, rd / ru, 1.0)
    mx = (c[0] + ux * scale * s).astype(np.float32)
    my = (c[1] + uy * scale * s).astype(np.float32)
    out = cv2.remap(a, mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return Image.fromarray(out)


def intake(img: Image.Image) -> dict:
    """Analyse one photo. Returns {size, f_exif_px, k, straight_before, straight_after, chains, projection, why}.
    k refers to the full-size image (the model is scale-free in normalised radius)."""
    W, H = img.size
    work = img.convert("RGB")
    if max(W, H) > WORK_PX:
        work = work.resize((round(W * WORK_PX / max(W, H)), round(H * WORK_PX / max(W, H))), Image.LANCZOS)
    chains = edge_chains(_grey(work))
    k, before, after = fit_division(chains, work.size)
    rec = {"size": [W, H], "f_exif_px": exif_focal_px(img), "k": round(k, 5),
           "straight_before": None if before is None else round(before, 5),
           "straight_after": None if after is None else round(after, 5), "chains": len(chains)}
    why = []
    if W / H > PANORAMA_ASPECT or H / W > PANORAMA_ASPECT:
        why.append(f"aspect {max(W / H, H / W):.2f} > {PANORAMA_ASPECT} (stitched panorama)")
    if after is None or len(chains) < 3:
        why.append("too few long straight edges to check the lens")
    elif after > STRAIGHT_MAX:
        why.append(f"lines stay bent after lens correction ({after:.4f} > {STRAIGHT_MAX} of the diagonal)")
    rec["projection"] = "panorama" if any("panorama" in w or "bent" in w for w in why) else "pinhole"
    if why:
        rec["why"] = why
    return rec
