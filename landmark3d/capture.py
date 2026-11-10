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
