"""Guided capture: readiness checks for a set of photographs (plan M7, task 1). Pure PIL; no network, no reconstruction.

What can be checked from the pictures alone: count, size, sharpness (blur), exposure (too dark, too bright, clipped).
Overlap, coverage and viewpoint diversity need camera poses, so they are reported as `needs_reconstruction` and never
claimed as passed. The instructions are the same for every building; they never name a landmark.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

MIN_PHOTOS = 20                  # a usable structure-from-motion set is rarely built from fewer
MIN_SHORT_SIDE = 1000            # px; smaller frames lose the window grid
BLUR_VARIANCE_MIN = 60.0         # variance of the Laplacian; below this a frame is soft
DARK_MEAN, BRIGHT_MEAN = 40, 215
CLIPPED_MAX = 0.08               # share of pixels at 0 or 255 above which the exposure is wrong

INSTRUCTIONS = [
    "Đi vòng quanh công trình ở khoảng cách đều, mỗi ảnh chồng lên ảnh trước khoảng 60–80%.",
    "Chụp cả chính diện, hai góc chéo và mặt sau; mỗi mặt cần ít nhất vài ảnh từ các góc khác nhau.",
    "Giữ máy vuông góc với mặt đứng khi có thể, tránh nghiêng quá để không méo đường thẳng.",
    "Chụp khi ánh sáng đều (trời nhiều mây hoặc sáng sớm); tránh ngược sáng và tránh bóng đổ dài.",
    "Dùng tốc độ màn trập đủ nhanh để tránh rung; kiểm tra từng ảnh trên màn hình trước khi rời đi.",
]


def frame_metrics(path: Path) -> dict:
    """Size, sharpness and exposure of one frame."""
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        gray = im.convert("L")
        # the 3x3 Laplacian: its variance is high for sharp edges and low for soft frames
        lap = gray.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128))
        blur = ImageStat.Stat(lap).var[0]
        mean = ImageStat.Stat(gray).mean[0]
        hist = gray.histogram()
        total = sum(hist) or 1
        clipped = (hist[0] + hist[255]) / total
    return {"width": w, "height": h, "sharpness": round(blur, 2), "mean_luma": round(mean, 1),
            "clipped_share": round(clipped, 4)}


def assess(paths: list[Path]) -> dict:
    """Readiness report for a photo set. `ready` is True only when every check that can be made from the pictures passes;
    the pose-based checks are listed as pending, so a passing report is not a claim about coverage."""
    problems, frames = [], []
    if len(paths) < MIN_PHOTOS:
        problems.append(f"{len(paths)} photos; at least {MIN_PHOTOS} are needed for a reconstruction")
    for p in paths:
        try:
            m = frame_metrics(p)
        except OSError as exc:
            problems.append(f"{p.name}: cannot be read ({exc})")
            continue
        m["file"] = p.name
        frames.append(m)
        if min(m["width"], m["height"]) < MIN_SHORT_SIDE:
            problems.append(f"{p.name}: {m['width']}x{m['height']} px is below {MIN_SHORT_SIDE} px on the short side")
        if m["sharpness"] < BLUR_VARIANCE_MIN:
            problems.append(f"{p.name}: soft (sharpness {m['sharpness']} < {BLUR_VARIANCE_MIN})")
        if m["mean_luma"] < DARK_MEAN:
            problems.append(f"{p.name}: too dark (mean {m['mean_luma']})")
        if m["mean_luma"] > BRIGHT_MEAN:
            problems.append(f"{p.name}: too bright (mean {m['mean_luma']})")
        if m["clipped_share"] > CLIPPED_MAX:
            problems.append(f"{p.name}: {round(m['clipped_share'] * 100)}% of pixels clipped")
    return {"ready": not problems, "problems": problems, "frames": frames,
            "pending": ["overlap between neighbouring photos", "coverage of every facade",
                        "viewpoint diversity"],
            "instructions": INSTRUCTIONS}


# Pose checks, made once a reconstruction has solved the cameras (the adapter contract's manifest).
SECTORS = 12                     # 30 degree sectors around the building
COVERAGE_MIN = 0.75              # share of sectors with at least one camera
NEIGHBOUR_GAP_MAX_DEG = 30.0     # two neighbouring cameras further apart than this rarely share enough of the wall
OVERLAP_MIN = 0.8                # share of neighbour gaps under NEIGHBOUR_GAP_MAX_DEG
ELEVATION_SPREAD_MIN_DEG = 5.0   # heights or distances must vary, or the depth of the facade is poorly observed
DISTANCE_RATIO_MIN = 1.3


def assess_poses(cams: list, points: list | None = None) -> dict:
    """Overlap, coverage and viewpoint diversity from solved camera poses (R, t world-to-camera, as in the adapter
    manifest). Azimuths are measured around the point-cloud centre (or the camera centroid without points) in the
    plane normal to the cameras' up direction. Overlap is a pose proxy: the angular gap between neighbouring cameras.
    Cameras that are not level give `not_measurable`, never a pass."""
    import math

    from . import photogrammetry
    if len(cams) < 3:
        return {"status": "not_measurable", "reason": "fewer than three solved cameras", "checks": {}}
    try:
        up = photogrammetry.up_axis(cams)
    except ValueError as exc:
        return {"status": "not_measurable", "reason": str(exc), "checks": {}}
    C = photogrammetry.camera_centres(cams)
    ref = (1.0, 0.0, 0.0) if abs(up[0]) < 0.9 else (0.0, 1.0, 0.0)
    dot = sum(ref[i] * up[i] for i in range(3))
    e1 = [ref[i] - dot * up[i] for i in range(3)]
    n1 = math.sqrt(sum(x * x for x in e1))
    e1 = [x / n1 for x in e1]
    e2 = [up[1] * e1[2] - up[2] * e1[1], up[2] * e1[0] - up[0] * e1[2], up[0] * e1[1] - up[1] * e1[0]]
    centre = _look_centre(cams, C, e1, e2, up)
    if centre is None:               # parallel optical axes: fall back to the median of the points, then the cameras
        src = points if points else C
        centre = [sorted(p[i] for p in src)[len(src) // 2] for i in range(3)]
    az, elev, dist = [], [], []
    for c in C:
        v = [c[i] - centre[i] for i in range(3)]
        h = sum(v[i] * up[i] for i in range(3))
        x, y = sum(v[i] * e1[i] for i in range(3)), sum(v[i] * e2[i] for i in range(3))
        r = math.hypot(x, y)
        az.append(math.degrees(math.atan2(y, x)) % 360)
        elev.append(math.degrees(math.atan2(h, max(r, 1e-9))))
        dist.append(r)
    sectors = {int(a // (360 / SECTORS)) for a in az}
    s = sorted(az)
    gaps = [(s[(i + 1) % len(s)] - s[i]) % 360 for i in range(len(s))]
    if len(s) == 1:
        gaps = [360.0]
    coverage = len(sectors) / SECTORS
    overlap = sum(1 for g in gaps if g <= NEIGHBOUR_GAP_MAX_DEG) / len(gaps)
    e_spread = max(elev) - min(elev)
    d_ratio = max(dist) / max(min(dist), 1e-9)
    checks = {
        "coverage": {"value": round(coverage, 3), "largest_gap_deg": round(max(gaps), 1),
                     "ok": coverage >= COVERAGE_MIN},
        "overlap": {"value": round(overlap, 3), "ok": overlap >= OVERLAP_MIN},
        "viewpoint_diversity": {"elevation_spread_deg": round(e_spread, 1), "distance_ratio": round(d_ratio, 2),
                                "ok": e_spread >= ELEVATION_SPREAD_MIN_DEG or d_ratio >= DISTANCE_RATIO_MIN},
    }
    problems = []
    if not checks["coverage"]["ok"]:
        problems.append(f"cameras cover {len(sectors)} of {SECTORS} directions around the building; the largest gap "
                        f"is {max(gaps):.0f} degrees: photograph the missing sides")
    if not checks["overlap"]["ok"]:
        problems.append(f"only {overlap:.0%} of neighbouring cameras are within {NEIGHBOUR_GAP_MAX_DEG:.0f} degrees of "
                        f"each other: take photos in smaller steps")
    if not checks["viewpoint_diversity"]["ok"]:
        problems.append("every photo is taken from the same height and distance: add a few from further away or "
                        "higher up")
    return {"status": "pass" if not problems else "fail", "problems": problems, "checks": checks}


def _look_centre(cams, C, e1, e2, up):
    """The point the cameras look at: least-squares intersection of their optical axes in the horizontal plane. Photos
    of a building point at it, so this is its centre even when only one side was photographed. None when the axes are
    (nearly) parallel."""
    a11 = a12 = a22 = b1 = b2 = 0.0
    for cam, c in zip(cams, C):
        f = cam["R"][2]                                  # the optical axis is the third row of R
        fx, fy = sum(f[i] * e1[i] for i in range(3)), sum(f[i] * e2[i] for i in range(3))
        n = (fx * fx + fy * fy) ** 0.5
        if n < 1e-6:
            continue
        fx, fy = fx / n, fy / n
        px, py = sum(c[i] * e1[i] for i in range(3)), sum(c[i] * e2[i] for i in range(3))
        # projector onto the normal of the axis: I - f f^T
        m11, m12, m22 = 1 - fx * fx, -fx * fy, 1 - fy * fy
        a11 += m11
        a12 += m12
        a22 += m22
        b1 += m11 * px + m12 * py
        b2 += m12 * px + m22 * py
    det = a11 * a22 - a12 * a12
    if abs(det) < 1e-6 * max(1.0, (a11 + a22) ** 2):
        return None
    x = (a22 * b1 - a12 * b2) / det
    y = (a11 * b2 - a12 * b1) / det
    h = sum(sum(c[i] * up[i] for i in range(3)) for c in C) / len(C)
    return [x * e1[i] + y * e2[i] + h * up[i] for i in range(3)]
