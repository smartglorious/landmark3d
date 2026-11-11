"""T4: masks of what is not the building's wall in a photo, so projection (T5) never paints it onto the model.

Labels (input/texture_masks/<photo stem>.png, on the undistorted image): 0 clear, 1 sky, 2 occluder (tree, person,
vehicle, pole, sign, flag, awning), 3 other building.
- sky: smarttex.metrics.sky_mask (connected to the top).
- vegetation: deterministic. Green (excess green and hue) and textured (leaves), so smooth painted green shutters
  are not taken for trees.
- other occluders: one AI look per photo gives boxes with a class; GrabCut, started from each box, cuts the object's
  own shape out of it (a box around a person is mostly wall). Answers are cached per photo.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

CLEAR, SKY, OCCLUDER, BUILDING = 0, 1, 2, 3
CLASSES = ("tree", "person", "vehicle", "pole", "sign", "flag", "awning", "other_building")
EXG_MIN = 18.0               # excess green 2G - R - B (0-255 scale) of foliage
GREEN_HUE = (30, 95)         # OpenCV hue (0-180): yellow-green to cyan-green
TEXTURE_MIN = 9.0            # local luminance std of leaves; painted shutters are smoother
MIN_BLOB = 0.0015            # vegetation blobs smaller than this share of the image are noise
BAND_ASPECT = 6.0            # a green blob this elongated ...
BAND_FILL = 0.4              # ... and this sparse in its box is an ornament band on the facade, not foliage
THICK = 0.035               # foliage is at least this share of the image's short side thick
GRABCUT_FULL = 0.85          # GrabCut keeping more than this share of its box failed: use the box itself
GRABCUT_PX = 900             # GrabCut runs on the image scaled to this long side
BOX_PAD = 0.15               # a box is grown by this share of its size before GrabCut (the AI's boxes are tight)

PROMPT = """This photo of {name} (yellow grid labelled 0.0-1.0 from the TOP-LEFT: x to the right, y down) will be
projected onto a 3D model of the building's walls. Mark everything that stands IN FRONT of the building's walls or
is not part of the building, so it is not painted onto the model: trees and foliage, people, vehicles (cars,
motorbikes, buses), poles and street lamps, signs, flags and banners, awnings and stalls, and other buildings.
Give one box per object (or per tight group), as tight as possible, in grid fractions. Do not mark parts of the
building itself (its windows, doors, columns, ornaments, balconies).
Reply with one JSON object: {{"objects": [{{"class": "{classes}", "box": [x0, y0, x1, y1]}}]}}"""


def vegetation(rgb: np.ndarray) -> np.ndarray:
    """Foliage: green, saturated enough and textured."""
    a = rgb.astype(np.float32)
    exg = 2 * a[..., 1] - a[..., 0] - a[..., 2]
    hsv = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2HSV)
    hue_ok = (hsv[..., 0] >= GREEN_HUE[0]) & (hsv[..., 0] <= GREEN_HUE[1]) & (hsv[..., 1] >= 40)
    lum = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    k = max(5, int(min(lum.shape) / 120) | 1)
    mean = cv2.blur(lum, (k, k))
    std = np.sqrt(np.maximum(cv2.blur(lum * lum, (k, k)) - mean * mean, 0))
    m = ((exg > EXG_MIN) & hue_ok & (std > TEXTURE_MIN)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    # foliage is thick; painted ornament bands are thin, and are often joined to a tree: keep only what survives an
    # opening with a large disc, then grow it back inside the original mask
    r = max(5, int(THICK * min(m.shape)) | 1)
    disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (r, r))
    core = cv2.morphologyEx(m, cv2.MORPH_OPEN, disc)
    m = (cv2.dilate(core, disc) & m).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = np.zeros(n, bool)
    for i in range(1, n):
        w, h, area = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT], stats[i, cv2.CC_STAT_AREA]
        band = max(w, h) / max(1, min(w, h)) > BAND_ASPECT and area / max(1, w * h) < BAND_FILL
        keep[i] = area >= MIN_BLOB * m.size and not band   # long thin sparse strips: painted green ornament bands
    return keep[lab]


def grabcut_box(rgb: np.ndarray, box) -> np.ndarray:
    """The object's shape inside a box (pixel x0, y0, x1, y1), as a boolean mask of the whole image."""
    H, W = rgb.shape[:2]
    s = min(1.0, GRABCUT_PX / max(W, H))
    small = cv2.resize(rgb.astype(np.uint8), (max(1, round(W * s)), max(1, round(H * s))), interpolation=cv2.INTER_AREA)
    x0, y0, x1, y1 = [v * s for v in box]
    pw, ph = (x1 - x0) * BOX_PAD, (y1 - y0) * BOX_PAD
    x0, y0 = max(0, int(x0 - pw)), max(0, int(y0 - ph))
    x1, y1 = min(small.shape[1] - 1, int(x1 + pw)), min(small.shape[0] - 1, int(y1 + ph))
    out = np.zeros((H, W), bool)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return out
    mask = np.zeros(small.shape[:2], np.uint8)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(cv2.cvtColor(small, cv2.COLOR_RGB2BGR), mask, (x0, y0, x1 - x0, y1 - y0), bgd, fgd, 4,
                    cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        m = np.zeros(small.shape[:2], bool)
        m[y0:y1, x0:x1] = True
        return cv2.resize(m.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) > 0
    fg = (mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD)
    if fg[y0:y1, x0:x1].mean() > GRABCUT_FULL:            # nothing separated (e.g. a person against paving)
        bx0, by0, bx1, by1 = [int(round(v)) for v in box]
        out[max(0, by0):max(0, by1), max(0, bx0):max(0, bx1)] = True
        return out
    return cv2.resize(fg.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) > 0


def ai_objects(prj, provider, img: Image.Image, stem: str, name: str) -> list:
    """[{class, box: [x0, y0, x1, y1] fractions}] from one AI look at the photo (cached by image and prompt)."""
    from .. import autotex
    from ..research import ai
    out_dir = prj.root / "reports" / "texture_review"
    out_dir.mkdir(parents=True, exist_ok=True)
    gp = out_dir / f"{stem}_mask_grid.jpg"
    autotex.grid_overlay(img, "photo").save(gp, "JPEG", quality=90)
    prompt = PROMPT.format(name=name, classes="|".join(CLASSES))
    key = hashlib.sha256(prompt.encode("utf-8") + gp.read_bytes()).hexdigest()[:20]
    cache = prj.read_json(f"reports/texture_review/{stem}_mask_ai.json", None)
    if cache and cache.get("key") == key:
        return cache["objects"]

    def check(o):
        objs = o.get("objects")
        if not isinstance(objs, list):
            return ["objects list required"]
        errs = []
        for x in objs:
            b = x.get("box") or []
            if x.get("class") not in CLASSES or len(b) != 4 or not (0 <= b[0] < b[2] <= 1 and 0 <= b[1] < b[3] <= 1):
                errs.append(f"bad object {x}")
        return errs
    obj, _ = ai.ask_json(provider, "You find occluders in photos of buildings. Reply with one JSON object only.",
                         prompt, validator=check, images=[gp], retries=1)
    prj.write_json(f"reports/texture_review/{stem}_mask_ai.json", {"key": key, "objects": obj["objects"]})
    return obj["objects"]


def build(rgb: np.ndarray, objects: list | None = None) -> np.ndarray:
    """Label image: sky, vegetation, then the AI's objects cut out by GrabCut (other buildings keep their box)."""
    from .camera import sky_of
    H, W = rgb.shape[:2]
    lab = np.zeros((H, W), np.uint8)
    lab[vegetation(rgb)] = OCCLUDER
    for o in objects or []:
        x0, y0, x1, y1 = [float(v) for v in o["box"]]
        box = (x0 * W, y0 * H, x1 * W, y1 * H)
        if o.get("class") == "other_building":
            lab[int(box[1]):int(box[3]), int(box[0]):int(box[2])] = BUILDING
        else:
            lab[grabcut_box(rgb, box)] = OCCLUDER
    lab[sky_of(rgb)] = SKY
    return lab


def mask_path(rec: dict) -> str:
    return (Path("input") / "texture_masks" / f"{Path(rec['undistorted']).stem}.png").as_posix()


def ensure(prj, provider=None, name: str = "", only: set | None = None) -> dict:
    """Make the label mask of every photo with an undistorted image in input/cameras.json (or only the given photo
    numbers): AI boxes when a provider is given, otherwise sky and vegetation only.
    Returns {photo: {"path", "occluder_share", "objects"}}."""
    doc = prj.read_json("input/cameras.json", {}) or {}
    out = {}
    for rec in doc.get("photos") or []:
        if not rec.get("undistorted") or not (prj.root / rec["undistorted"]).is_file():
            continue
        if only is not None and rec.get("photo") not in only:
            continue
        img = Image.open(prj.root / rec["undistorted"]).convert("RGB")
        objs = None
        if provider is not None:
            try:
                objs = ai_objects(prj, provider, img, f"photo{rec.get('photo')}", name)
            except Exception as exc:                 # masks are an aid: report and fall back to sky + vegetation
                prj.log("occluder boxes failed", {"photo": rec.get("photo"), "error": str(exc)[:200]})
        lab = build(np.asarray(img), objs)
        rel = mask_path(rec)
        (prj.root / rel).parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(lab).save(prj.root / rel)
        out[rec.get("photo")] = {"path": rel, "occluder_share": round(float((lab == OCCLUDER).mean()), 4),
                                 "objects": len(objs or [])}
    return out


def load(prj, rec: dict) -> np.ndarray | None:
    f = prj.root / mask_path(rec) if rec.get("undistorted") else None
    if f is None or not f.is_file():
        return None
    return np.asarray(Image.open(f))


def overlay(rgb: np.ndarray, lab: np.ndarray) -> Image.Image:
    """The photo with sky tinted blue, occluders red and other buildings yellow (for review)."""
    a = rgb.astype(np.float32).copy()
    for v, col in ((SKY, (60, 120, 255)), (OCCLUDER, (255, 40, 40)), (BUILDING, (255, 220, 0))):
        m = lab == v
        a[m] = 0.45 * a[m] + 0.55 * np.array(col, np.float32)
    return Image.fromarray(a.clip(0, 255).astype(np.uint8))
