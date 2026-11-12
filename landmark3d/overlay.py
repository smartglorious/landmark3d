"""Overlay of a model render on its reference photo, for the photo-review AI and the owner.

The photo's building frame (left, top of the highest point, right, ground line - fractions of the photo) comes from
the viewpoint-matching AI answer. The model in the render (its pixels against the render's sky and ground) is scaled
so its height fills that frame and centred on it, then drawn over the photo half transparent with a cyan outline.
Where the photo's building sticks out of the cyan outline, a volume is missing or too small; where the outline
encloses sky or street, a volume is too large or misplaced.

Automatic alignment from the photo alone was tried and rejected (2026-11-12): overcast cloud, trees and scaffolding
in the photos made both the "not sky" mask and edge matching land the model in the wrong place.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

MASK_DIFF = 30                    # a render pixel differs this much (sum of RGB) from the sky and the ground: model
MODEL_ALPHA = 0.45


def render_mask(img: Image.Image) -> np.ndarray:
    """The model in a render: pixels unlike both the sky (top rows) and the ground plane (bottom rows)."""
    import cv2
    a = np.asarray(img.convert("RGB"), dtype=np.int16)
    H = a.shape[0]
    sky = np.median(a[: max(2, H // 40)].reshape(-1, 3), axis=0)
    ground = np.median(a[-max(2, H // 40):].reshape(-1, 3), axis=0)
    m = (np.abs(a - sky).sum(axis=2) > MASK_DIFF) & (np.abs(a - ground).sum(axis=2) > MASK_DIFF)
    m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)   # drop specks of sky gradient
    if n > 1:
        big = stats[1:, cv2.CC_STAT_AREA].max()
        m = np.isin(lab, [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= 0.02 * big]).astype(np.uint8)
    return m.astype(bool)


def valid_frame(frame) -> bool:
    try:
        x0, y0, x1, y1 = [float(v) for v in frame]
    except (TypeError, ValueError):
        return False
    return 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 and (x1 - x0) > 0.05 and (y1 - y0) > 0.05


def compose(photo: Image.Image, render: Image.Image, frame) -> Image.Image:
    """The photo with the render's model drawn over it, its height filling `frame` ([x0, y0, x1, y1] fractions of
    the photo: the building from its highest point to the ground) and centred on the frame."""
    import cv2
    photo = photo.convert("RGB")
    m = render_mask(render)
    ys, xs = np.where(m)
    if xs.size == 0 or not valid_frame(frame):
        raise ValueError("no model in the render or no valid frame")
    W, H = photo.size
    x0, y0, x1, y1 = [float(v) for v in frame]
    box = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
    model = np.asarray(render.convert("RGB"))[box[1]:box[3], box[0]:box[2]]
    mm = m[box[1]:box[3], box[0]:box[2]]
    s = (y1 - y0) * H / mm.shape[0]
    w, h = max(1, int(round(mm.shape[1] * s))), max(1, int(round(mm.shape[0] * s)))
    model = cv2.resize(model, (w, h), interpolation=cv2.INTER_AREA)
    mm = cv2.resize(mm.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)
    left = int(round((x0 + x1) / 2 * W - w / 2))
    top = int(round(y0 * H))
    out = np.asarray(photo, dtype=np.float32).copy()
    # paste with clipping at the photo's edges
    sx0, sy0 = max(0, -left), max(0, -top)
    dx0, dy0 = max(0, left), max(0, top)
    dx1, dy1 = min(W, left + w), min(H, top + h)
    if dx1 <= dx0 or dy1 <= dy0:
        raise ValueError("the model falls outside the photo")
    sub_m = mm[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]
    sub = model[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)].astype(np.float32)
    region = out[dy0:dy1, dx0:dx1]
    region[sub_m] = region[sub_m] * (1 - MODEL_ALPHA) + sub[sub_m] * MODEL_ALPHA
    full = np.zeros((H, W), np.uint8)
    full[dy0:dy1, dx0:dx1] = sub_m.astype(np.uint8) * 255
    edge = cv2.dilate((cv2.Canny(full, 50, 150) > 0).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    out[edge] = (0, 255, 255)
    return Image.fromarray(out.clip(0, 255).astype(np.uint8))
