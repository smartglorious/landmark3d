"""Generated facade textures: Codex CLI image generation (the user's signed-in account, no API key) draws a clean,
straight-on elevation of each part's facade, in the building's own style taken from the reference photos, with its
windows exactly where the model has them.

For each part:
1. a layout image is drawn from the model (bays, floors, openings, wall top and base) - the generator must follow it;
2. the reference photos that show the part are attached as style and material references;
3. the result is checked deterministically: no sky, the windows centred in the model's bays, the right number of
   window rows, the wall colour close to the material. A failed image is generated once more with the problems
   named; if it fails again the part keeps its other texture;
4. the accepted image is cached (reports/genfacade/) and tiled over the part's faces by whole bays.
Every generated texture is labelled as generated (licence "ai_generated", source "codex image generation"), never
as a photo.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import metrics

PX_PER_M = 40                   # layout drawing scale
TEX_PX_PER_M = 32               # texture scale (matches autotex)
MAX_BAYS = 3                    # bays per generated image (more bays: less repetition; fewer: more detail)
GEN_TIMEOUT_S = 420
COLOUR_MAX = 95.0               # sRGB distance between the plaster colour of the image and the wall material
PHASE_MAX = 0.18                # window centre within this share of a bay from the bay centre
GENERATOR = "codex image generation"

PROMPT = """Use the image generation tool to create ONE image, then reply with only the full path of the image file.

Create a facade texture for a 3D model of {name}: a perfectly straight-on ORTHOGRAPHIC elevation (no perspective,
no vanishing lines) of one wall of the part "{part}", exactly {bays} bay(s) wide and {floors} floor(s) high.
- The LAST attached image is the LAYOUT drawn from the 3D model: grey = wall, dark = window or door openings,
  thin lines = floor levels. Your image must have exactly the same proportions ({aspect}), and every opening must be
  exactly where the dark shapes are (same count, positions and sizes; arched tops where the layout shows arches).
- The other attached image(s) are PHOTOS of the real building: copy its architectural style, wall colour and
  material, window joinery and colour, mouldings, cornices, pilasters and ornament from them{materials}.
- Fill the whole frame edge to edge with the wall: the top edge of the image is the top of the wall (cornice), the
  bottom edge is the base of the wall. No sky, no roof, no ground, no street, no people, no vehicles, no trees, no
  lamps, no signs, no shadows from outside objects, even daylight, no text, no watermark.
- The left and right edges must cut through the middle of a pier between bays, so the image tiles side by side.
{feedback}"""


# ----------------------------------------------------------------------------------------------- Codex CLI
def codex_cli() -> str | None:
    from ..research import ai
    return ai.find_cli("openai")


def codex_generate(prompt: str, images: list, timeout: int = GEN_TIMEOUT_S) -> Path:
    """Run Codex exec with image generation and return the generated image (copied to a temp file).
    Raises RuntimeError when nothing was produced."""
    from ..research import ai
    cli = codex_cli()
    if not cli:
        raise RuntimeError("Codex CLI not found (needed for generated textures)")
    gen_root = Path.home() / ".codex" / "generated_images"
    before = {p for p in gen_root.rglob("*.png")} if gen_root.is_dir() else set()
    td = Path(tempfile.mkdtemp(prefix="l3d_gen_"))
    out = td / "last.txt"
    cmd = [cli, "exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "--ephemeral", "-C", str(td),
           "-o", str(out)]
    for p in images:
        cmd += ["-i", str(p)]
    cmd.append("-")
    t0 = time.time()
    try:
        r = ai._run(cmd, stdin_text=prompt, timeout=timeout, cwd=str(td))
    except Exception as exc:
        ai.record_call("codex_image", time.time() - t0, "failed", "openai", len(images), [exc])
        raise
    reply = out.read_text(encoding="utf-8").strip() if out.exists() else ""
    cands = []
    for line in reply.splitlines():
        p = Path(line.strip().strip("`\"'"))
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and p.is_file():
            cands.append(p)
    cands += [p for p in td.rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
    if gen_root.is_dir():
        cands += sorted((p for p in gen_root.rglob("*.png") if p not in before and p.stat().st_mtime >= t0 - 5),
                        key=lambda p: p.stat().st_mtime)
    ai.record_call("codex_image", time.time() - t0, "ok" if cands else "no_image", "openai", len(images))
    if not cands:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-4:]
        raise RuntimeError(f"Codex produced no image (exit {r.returncode}): {' | '.join(tail)[:300]}")
    dst = Path(tempfile.mkdtemp(prefix="l3d_genout_")) / "gen.png"
    shutil.copy(cands[-1] if not reply else cands[0], dst)
    shutil.rmtree(td, ignore_errors=True)
    return dst


# ----------------------------------------------------------------------------------------------- layout
def _floor_rules(fac: dict, k: int) -> dict:
    rule = dict(fac)
    for o in fac.get("floors") or []:
        if int(o.get("from", 0)) <= k <= int(o.get("to", 0)):
            rule.update(o)
    return rule


def layout(part: dict, n_bays: int) -> tuple[Image.Image, dict]:
    """The model's elevation of n bays of the part: wall, openings per floor (rect or arched), floor lines.
    Returns (image, {"floors", "bay_m", "height_m", "openings": [(x0, y0, x1, y1) fractions]})."""
    fac = part.get("facade") or {}
    bay = max(1.0, float(fac.get("bay_spacing", 4.0)))
    h = float(part["height"])
    lh = float(fac.get("level_height") or 0) or h
    floors = max(1, round(h / lh))
    W, H = int(round(n_bays * bay * PX_PER_M)), int(round(h * PX_PER_M))
    im = Image.new("RGB", (W, H), (180, 180, 180))
    d = ImageDraw.Draw(im)
    ops = []
    for k in range(floors):
        rule = _floor_rules(fac, k)
        if rule.get("openings") is False:
            continue
        ow = float(rule.get("opening_width", min(1.6, 0.45 * bay)))
        oh = float(rule.get("opening_height", min(2.4, 0.6 * lh)))
        sill = float(rule.get("sill", 0.9 if k else 0.3))
        z0 = k * lh + sill
        z1 = min(h - 0.3, z0 + oh)
        if z1 <= z0:
            continue
        arch = str(rule.get("opening_type", "rect")).startswith("arch")
        for b in range(n_bays):
            cx = (b + 0.5) * bay
            x0, x1 = (cx - ow / 2) * PX_PER_M, (cx + ow / 2) * PX_PER_M
            y0, y1 = H - z1 * PX_PER_M, H - z0 * PX_PER_M
            if arch:
                r = (x1 - x0) / 2
                d.rectangle((x0, y0 + r, x1, y1), fill=(40, 40, 40))
                d.pieslice((x0, y0, x1, y0 + 2 * r), 180, 360, fill=(40, 40, 40))
            else:
                d.rectangle((x0, y0, x1, y1), fill=(40, 40, 40))
            ops.append((x0 / W, y0 / H, x1 / W, y1 / H))
    for k in range(1, floors):
        y = H - k * lh * PX_PER_M
        d.line((0, y, W, y), fill=(120, 120, 120), width=max(2, PX_PER_M // 10))
    return im, {"floors": floors, "bay_m": bay, "height_m": h, "openings": ops, "n_bays": n_bays}


# ----------------------------------------------------------------------------------------------- checks
def check(img: Image.Image, info: dict, wall_srgb=None) -> list[str]:
    """Problems of a generated elevation against the model layout (empty = accepted)."""
    probs = []
    a = np.asarray(img.convert("RGB"))
    if metrics.sky_share(a) > metrics.THRESHOLDS["sky_share_max"]:
        probs.append("there is sky at the top: the image must end at the top of the wall")
    n = info["n_bays"]
    if n >= 2:
        bp = metrics.bay_phase(a, n)
        if bp.get("error") is None:
            probs.append(f"the windows do not repeat {n} times across the width")
        elif bp["error"] > PHASE_MAX:
            probs.append("the windows are not centred in their bays as in the layout")
    else:
        bp = metrics.bay_phase(np.hstack([a, a, a]), 3)
        if bp.get("error") is not None and bp["error"] > PHASE_MAX:
            probs.append("the window is not centred as in the layout")
    if wall_srgb is not None:
        lum = a.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
        plaster = a[lum >= np.percentile(lum, 55)].reshape(-1, 3).astype(np.float32)
        if plaster.size and float(np.linalg.norm(np.median(plaster, axis=0) - np.asarray(wall_srgb[:3], np.float32))) \
                > COLOUR_MAX:
            probs.append("the wall colour is far from the building's (see the photos)")
    return probs


# ----------------------------------------------------------------------------------------------- per part
def _key(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode("utf-8"))
    return h.hexdigest()[:20]


def generate_part(prj, spec: dict, part: dict, refs: list, generate=None,
                  owner_note: str = "") -> dict:
    """Generated elevation of the part (cached). Returns {"img": PIL image n bays wide, "n_bays", "problems",
    "cached", "attempts"} or {"error": ...}."""
    fac = part.get("facade") or {}
    if not fac:
        return {"error": "no facade grid"}
    from .. import autotex
    faces = autotex.part_faces(spec, part)
    if not faces:
        return {"error": "no wall faces"}
    n = max(1, min(MAX_BAYS, min(f["n_bays"] for f in faces)))
    lay, info = layout(part, n)
    out_dir = prj.root / "reports" / "genfacade"
    out_dir.mkdir(parents=True, exist_ok=True)
    lay_path = out_dir / f"{part['id']}_layout.png"
    lay.save(lay_path)
    mats = spec.get("materials") or {}
    wall = (part.get("material") or {}).get("wall")
    wall_srgb = (mats.get(wall) or {}).get("srgb")
    mat_note = f" (the wall material is '{wall}', about RGB {tuple(wall_srgb)})" if wall_srgb else ""
    ref_paths = [prj.root / r for r in refs][:2]
    base_prompt = dict(name=spec.get("name", ""), part=part["id"], bays=n, floors=info["floors"],
                       aspect=f"{lay.width} x {lay.height}, width / height = {lay.width / lay.height:.3f}",
                       materials=mat_note)
    owner = (f"- The owner reviewed an earlier version and asks: {owner_note.strip()}\n" if owner_note.strip() else "")
    key = _key(owner, json.dumps(base_prompt, sort_keys=True), lay_path.read_bytes(),
               *[p.read_bytes() for p in ref_paths if p.is_file()])
    meta_path = out_dir / f"{part['id']}.json"
    img_path = out_dir / f"{part['id']}.png"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    if meta.get("key") == key and img_path.is_file():
        return {"img": Image.open(img_path).convert("RGB"), "n_bays": n, "problems": meta.get("problems", []),
                "cached": True, "attempts": meta.get("attempts", 0), "accepted": meta.get("accepted", False)}
    feedback, problems, img, attempts = "", [], None, 0
    for attempt in range(2):
        attempts += 1
        prompt = PROMPT.format(**base_prompt, feedback=owner + feedback)
        try:
            got = (generate or codex_generate)(prompt, [p for p in ref_paths if p.is_file()] + [lay_path])
        except Exception as exc:
            return {"error": f"generation failed: {str(exc)[:200]}", "attempts": attempts}
        cand = Image.open(got).convert("RGB").resize((lay.width, lay.height), Image.LANCZOS)
        problems = check(cand, info, wall_srgb)
        img = cand
        if not problems:
            break
        feedback = "The previous attempt had these problems, fix them: " + "; ".join(problems) + "."
    img.save(img_path)
    meta = {"key": key, "problems": problems, "attempts": attempts, "accepted": not problems, "n_bays": n,
            "generator": GENERATOR, "references": refs[:2]}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"img": img, "n_bays": n, "problems": problems, "cached": False, "attempts": attempts,
            "accepted": not problems}


EDGE_BLEND = 0.04               # share of the width cross-faded at each side so the image repeats without a seam


def x_periodic(img: Image.Image, share: float = EDGE_BLEND) -> Image.Image:
    """Cross-fade the left and right edge bands so the right edge continues into the left one: copies side by side,
    and neighbouring faces of a part (each ends where the next starts), meet without a colour step."""
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    W = a.shape[1]
    n = max(1, int(W * share))
    out = a.copy()
    for i in range(n):
        w = 0.5 * (1.0 - i / n)
        L, R = a[:, i], a[:, W - 1 - i]
        out[:, i] = (1 - w) * L + w * R
        out[:, W - 1 - i] = (1 - w) * R + w * L
    return Image.fromarray(out.clip(0, 255).astype(np.uint8))


def tile(img: Image.Image, n_src: int, n_face: int, h_px: int) -> Image.Image:
    """A face of n_face bays from an n_src-bay image: whole copies side by side, cut at a bay line."""
    bay_w = max(4, round(img.width * h_px / img.height / n_src))
    src = x_periodic(img.resize((bay_w * n_src, h_px), Image.LANCZOS))
    out = Image.new("RGB", (bay_w * n_face, h_px))
    x = 0
    while x < out.width:
        out.paste(src, (x, 0))
        x += src.width
    return out


def forget(prj, part_id: str) -> None:
    """Drop the cached elevations of a part (its bay elevation and its feature faces), so the next texture run draws
    them again."""
    d = prj.root / "reports" / "genfacade"
    for name in (f"{part_id}.png", f"{part_id}.json"):
        (d / name).unlink(missing_ok=True)
    for p in list(d.glob(f"{part_id}__f*.png")) + list(d.glob(f"{part_id}__f*.json")):
        p.unlink(missing_ok=True)


# ----------------------------------------------------------------------------------------------- unique faces
# Faces with their own composition (rose window, clock, portal, pediment, loggia) get a face-specific elevation of the
# span that holds the features, drawn from the spec's features; the rest of the face keeps the part's bay elevation.
FEATURE_WEIGHT = {"disc": 3, "entrance": 3, "pediment": 2, "colonnade": 2, "balcony": 1, "column": 1, "panel": 1,
                  "balustrade": 1}
FEATURE_WORDS = {
    "disc": "a round element: rose window, oculus or clock face (take which one, and its tracery or dial, from the "
            "photos)",
    "entrance": "the entrance portal: a large door opening with its surround",
    "pediment": "a pediment or gable crown with its mouldings",
    "colonnade": "a loggia or colonnade: columns in front of a recessed wall",
    "column": "columns or pilasters",
    "balcony": "a balcony with its railing",
    "panel": "a decorative panel: relief, frieze, inscription band or sign as in the photos",
    "balustrade": "a balustrade",
}
FEATURE_FILL = (110, 130, 175)        # layout colour of features (openings stay dark, wall grey)
FACING_TOL_DEG = 30.0
MAX_FEATURE_FACES = 12                # per project: the faces with the most important features first
SEG_MAX_ASPECT = 3.0                  # width / height of a feature span image
BAND_SHARE = 0.6                      # panels / balustrades wider than this share of the face are bands, not features

UNIQUE_PROMPT = """Use the image generation tool to create ONE image, then reply with only the full path of the image file.

Create a facade texture for a 3D model of {name}: a perfectly straight-on ORTHOGRAPHIC elevation (no perspective,
no vanishing lines) of one particular stretch of wall, {width:g} m wide and {height:g} m high.
- The LAST attached image is the LAYOUT drawn from the 3D model: grey = wall, dark = window or door openings, thin
  lines = floor levels, blue numbered shapes = these architectural features, which must appear exactly there:
{features}
  Your image must have exactly the same proportions ({aspect}); every opening and feature must be exactly where the
  layout shows it (same count, positions and sizes).
- The other attached image(s) are PHOTOS of the real building: copy its architectural style, wall colour and
  material, window joinery, mouldings, cornices, pilasters and ornament, and the real look of each feature{materials}.
- Fill the whole frame edge to edge with the wall: the top edge of the image is the top of the wall, the bottom edge
  is the base. No sky, no roof, no ground, no people, no vehicles, no trees, no shadows from outside objects, even
  daylight, no text other than what the building really carries, no watermark.
{feedback}"""


def _ang(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def face_features(part: dict, face: dict) -> list[dict]:
    """The spec features (and the entrance) on this face, as boxes in metres: x from the face's left end (seen from
    outside), z above the part's base. Bands (wider than BAND_SHARE of the face) and features above the wall top are
    left to the bay elevation and the model."""
    L, H = float(face["length_m"]), float(part["height"])
    out = []

    def add(kind, cx, w, z0, z1):
        x0, x1 = max(0.0, cx - w / 2), min(L, cx + w / 2)
        z0, z1 = max(0.0, z0), min(H, z1)
        if x1 - x0 > 0.3 and z1 - z0 > 0.6:
            out.append({"type": kind, "box": (round(x0, 2), round(z0, 2), round(x1, 2), round(z1, 2)),
                        "weight": FEATURE_WEIGHT[kind]})
    for ft in part.get("features") or []:
        k = ft.get("type")
        if k not in FEATURE_WEIGHT or ft.get("facing_deg") is None or ft.get("around"):
            continue
        if abs(_ang(float(ft["facing_deg"]) - float(face["facing_deg"]))) > FACING_TOL_DEG:
            continue
        cx = L / 2 + float(ft.get("offset", 0) or 0)
        if k == "disc":
            d, zc = float(ft.get("diameter", 3.0)), float(ft.get("z", H / 2))
            add(k, cx, d, zc - d / 2, zc + d / 2)
        elif k == "panel":
            w, h, zc = float(ft.get("width", 4.0)), float(ft.get("height", 1.5)), float(ft.get("z", H / 2))
            if w <= BAND_SHARE * L:
                add(k, cx, w, zc - h / 2, zc + h / 2)
        elif k == "balustrade":
            w, z = float(ft.get("width", L)), float(ft.get("z", H - 1.0))
            if w <= BAND_SHARE * L:
                add(k, cx, w, z, z + float(ft.get("height", 1.0)))
        elif k == "pediment":
            w = float(ft.get("width", 4.0))
            h = float(ft.get("height", 0.3 * w))
            z = float(ft.get("z", H - h))
            add(k, cx, w, z, z + h)
        elif k == "colonnade":
            z = float(ft.get("z", 0.0))
            add(k, cx, float(ft.get("width", 6.0)), z, z + float(ft.get("height", 4.0)))
        elif k == "balcony":
            z = float(ft.get("z", 3.0))
            add(k, cx, float(ft.get("width", 4.0)), z, z + float(ft.get("rail_height", 1.0)))
        elif k == "column":
            n, sp, d = int(ft.get("count", 2) or 2), float(ft.get("spacing", 2.5)), float(ft.get("diameter", 0.6))
            z = float(ft.get("z", 0.0))
            add(k, cx, (n - 1) * sp + d, z, z + float(ft.get("height", 4.0)))
    ent = (part.get("facade") or {}).get("entrance")
    if ent and ent.get("facing_deg") is not None and \
            abs(_ang(float(ent["facing_deg"]) - float(face["facing_deg"]))) <= FACING_TOL_DEG:
        add("entrance", L / 2, float(ent.get("width", 3.0)), 0.0, float(ent.get("height", 4.0)))
    return out


def feature_span(face: dict, part: dict, feats: list) -> tuple[float, float]:
    """The stretch of the face [x0, x1] (metres, on bay lines) that holds the features, at most SEG_MAX_ASPECT times
    as wide as the wall is high (centred on the most important feature when it must be cut)."""
    L, H = float(face["length_m"]), float(part["height"])
    bay = L / max(1, int(face["n_bays"]))
    x0 = min(f["box"][0] for f in feats) - 0.5 * bay
    x1 = max(f["box"][2] for f in feats) + 0.5 * bay
    x0, x1 = max(0.0, math.floor(x0 / bay) * bay), min(L, math.ceil(x1 / bay) * bay)
    if x1 - x0 > SEG_MAX_ASPECT * H:
        top = max(feats, key=lambda f: (f["weight"], f["box"][2] - f["box"][0]))
        c = (top["box"][0] + top["box"][2]) / 2
        w = max(bay, math.floor(SEG_MAX_ASPECT * H / bay) * bay)
        x0 = min(max(0.0, round((c - w / 2) / bay) * bay), max(0.0, L - w))
        x1 = min(L, x0 + w)
    return round(x0, 3), round(x1, 3)


def segment_layout(part: dict, face: dict, span, feats: list) -> tuple[Image.Image, dict]:
    """Layout of a feature span: the part's openings on the face's bay grid (except where a feature is), floor lines,
    and the numbered features."""
    fac = part.get("facade") or {}
    L, h = float(face["length_m"]), float(part["height"])
    x0, x1 = span
    bay = L / max(1, int(face["n_bays"]))
    lh = float(fac.get("level_height") or 0) or h
    floors = max(1, round(h / lh))
    W, H = max(8, int(round((x1 - x0) * PX_PER_M))), int(round(h * PX_PER_M))
    im = Image.new("RGB", (W, H), (180, 180, 180))
    d = ImageDraw.Draw(im)
    X = lambda x: (x - x0) * PX_PER_M
    Z = lambda z: H - z * PX_PER_M
    inside = [f for f in feats if f["box"][2] > x0 and f["box"][0] < x1]
    hit = lambda a, b, c, e: any(not (c < f["box"][0] - 0.2 or a > f["box"][2] + 0.2 or e < f["box"][1] - 0.2
                                      or b > f["box"][3] + 0.2) for f in inside)
    for k in range(1, floors):
        d.line((0, Z(k * lh), W, Z(k * lh)), fill=(120, 120, 120), width=max(2, PX_PER_M // 10))
    for k in range(floors):
        rule = _floor_rules(fac, k)
        if rule.get("openings") is False:
            continue
        ow = float(rule.get("opening_width", min(1.6, 0.45 * bay)))
        oh = float(rule.get("opening_height", min(2.4, 0.6 * lh)))
        z0 = k * lh + float(rule.get("sill", 0.9 if k else 0.3))
        z1 = min(h - 0.3, z0 + oh)
        arch = str(rule.get("opening_type", "rect")).startswith("arch")
        for b in range(int(face["n_bays"])):
            cx = (b + 0.5) * bay
            if not (x0 <= cx <= x1) or z1 <= z0 or hit(cx - ow / 2, z0, cx + ow / 2, z1):
                continue
            box = (X(cx - ow / 2), Z(z1), X(cx + ow / 2), Z(z0))
            if arch:
                r = (box[2] - box[0]) / 2
                d.rectangle((box[0], box[1] + r, box[2], box[3]), fill=(40, 40, 40))
                d.pieslice((box[0], box[1], box[2], box[1] + 2 * r), 180, 360, fill=(40, 40, 40))
            else:
                d.rectangle(box, fill=(40, 40, 40))
    labels = []
    from ..autotex import _font
    font = _font(max(14, PX_PER_M // 2))
    num = {id(f): n for n, f in enumerate(sorted(inside, key=lambda f: -f["weight"]), start=1)}
    order = ["colonnade", "column", "panel", "balustrade", "balcony", "pediment", "entrance", "disc"]
    for f in sorted(inside, key=lambda f: (order.index(f["type"]), -(f["box"][2] - f["box"][0]))):
        n = num[id(f)]
        a, b, c, e = f["box"]
        box = (X(a), Z(e), X(c), Z(b))
        kind = f["type"]
        if kind == "disc":
            d.ellipse(box, fill=FEATURE_FILL, outline=(60, 70, 110), width=3)
        elif kind == "entrance":
            r = (box[2] - box[0]) / 2
            d.rectangle((box[0], box[1] + r, box[2], box[3]), fill=(40, 40, 40))
            d.pieslice((box[0], box[1], box[2], box[1] + 2 * r), 180, 360, fill=(40, 40, 40))
            d.rectangle(box, outline=FEATURE_FILL, width=4)
        elif kind == "pediment":
            d.polygon([(box[0], box[3]), ((box[0] + box[2]) / 2, box[1]), (box[2], box[3])], fill=FEATURE_FILL)
        elif kind in ("colonnade", "column"):
            d.rectangle(box, fill=(150, 150, 160))
            step = max(10, int(2.5 * PX_PER_M))
            for x in range(int(box[0]) + 4, int(box[2]), step):
                d.rectangle((x, box[1], x + max(4, PX_PER_M // 3), box[3]), fill=FEATURE_FILL)
        else:
            d.rectangle(box, fill=FEATURE_FILL)
        d.text(((box[0] + box[2]) / 2 - 6, (box[1] + box[3]) / 2 - 10), str(n), fill=(255, 255, 255), font=font)
        labels.append((n, f"  {n}. {FEATURE_WORDS[kind]}, {c - a:.1f} m wide, {e - b:.1f} m high, "
                      f"{(a + c) / 2 - x0:.1f} m from the left edge, {(b + e) / 2:.1f} m above the base"))
    return im, {"labels": [t for _, t in sorted(labels)], "width_m": round(x1 - x0, 2), "height_m": h}


def check_unique(img: Image.Image, wall_srgb=None) -> list[str]:
    probs = []
    a = np.asarray(img.convert("RGB"))
    if metrics.sky_share(a) > metrics.THRESHOLDS["sky_share_max"]:
        probs.append("there is sky at the top: the image must end at the top of the wall")
    if wall_srgb is not None:
        lum = a.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
        plaster = a[lum >= np.percentile(lum, 55)].reshape(-1, 3).astype(np.float32)
        if plaster.size and float(np.linalg.norm(np.median(plaster, axis=0) - np.asarray(wall_srgb[:3], np.float32))) \
                > COLOUR_MAX:
            probs.append("the wall colour is far from the building's (see the photos)")
    return probs


def feature_faces(spec: dict, parts: dict, done: set) -> list[tuple]:
    """[(part id, face, features)] of the faces with features, most important first, at most MAX_FEATURE_FACES."""
    from .. import autotex
    cands = []
    for pid, part in parts.items():
        for f in autotex.part_faces(spec, part):
            if (pid, f["edge_index"]) in done:
                continue
            feats = face_features(part, f)
            bay = float(f["length_m"]) / max(1, int(f["n_bays"]))
            score = sum(x["weight"] * (1.0 if x["box"][2] - x["box"][0] >= 1.2 * bay or x["type"] in ("disc", "entrance")
                                       else 0.5) for x in feats)
            if feats:
                cands.append((score, float(part["height"]), pid, f, feats))
    cands.sort(key=lambda c: (-c[0], -c[1]))
    return [(pid, f, feats) for _, _, pid, f, feats in cands[:MAX_FEATURE_FACES]]


def generate_face(prj, spec: dict, part: dict, face: dict, feats: list, refs: list, generate=None,
                  owner_note: str = "") -> dict:
    """Face-specific elevation of the feature span (cached; identical layouts with the same references share one
    image, e.g. mirrored wings). Returns {"img", "span", "accepted", "problems", ...} or {"error"}."""
    span = feature_span(face, part, feats)
    lay, info = segment_layout(part, face, span, feats)
    out_dir = prj.root / "reports" / "genfacade"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{part['id']}__f{face['edge_index']}"
    lay_path = out_dir / f"{stem}_layout.png"
    lay.save(lay_path)
    mats = spec.get("materials") or {}
    wall = (part.get("material") or {}).get("wall")
    wall_srgb = (mats.get(wall) or {}).get("srgb")
    base = dict(name=spec.get("name", ""), width=info["width_m"], height=info["height_m"],
                features="\n".join(info["labels"]),
                aspect=f"{lay.width} x {lay.height}, width / height = {lay.width / lay.height:.3f}",
                materials=f" (the wall material is '{wall}', about RGB {tuple(wall_srgb)})" if wall_srgb else "")
    owner = (f"- The owner reviewed an earlier version and asks: {owner_note.strip()}\n" if owner_note.strip() else "")
    ref_paths = [prj.root / r for r in refs][:2]
    key = _key("face", owner, json.dumps(base, sort_keys=True), lay_path.read_bytes(),
               *[p.read_bytes() for p in ref_paths if p.is_file()])
    img_path, meta_path = out_dir / f"{stem}.png", out_dir / f"{stem}.json"
    for mp in [meta_path] + sorted(out_dir.glob("*__f*.json")):      # own cache, then a twin with the same key
        if mp.is_file() and not mp.name.endswith("_layout.json"):
            meta = json.loads(mp.read_text(encoding="utf-8"))
            ip = mp.with_suffix(".png")
            if meta.get("key") == key and ip.is_file():
                if mp != meta_path:
                    shutil.copy(ip, img_path)
                    meta_path.write_text(json.dumps(dict(meta, shared_with=mp.stem), ensure_ascii=False, indent=1),
                                         encoding="utf-8")
                return {"img": Image.open(ip).convert("RGB"), "span": span, "problems": meta.get("problems", []),
                        "accepted": meta.get("accepted", False), "cached": True, "attempts": meta.get("attempts", 0)}
    feedback, problems, img, attempts = "", [], None, 0
    for _ in range(2):
        attempts += 1
        try:
            got = (generate or codex_generate)(UNIQUE_PROMPT.format(**base, feedback=owner + feedback),
                                               [p for p in ref_paths if p.is_file()] + [lay_path])
        except Exception as exc:
            return {"error": f"generation failed: {str(exc)[:200]}", "attempts": attempts, "span": span}
        img = Image.open(got).convert("RGB").resize((lay.width, lay.height), Image.LANCZOS)
        problems = check_unique(img, wall_srgb)
        if not problems:
            break
        feedback = "The previous attempt had these problems, fix them: " + "; ".join(problems) + "."
    img.save(img_path)
    meta = {"key": key, "problems": problems, "attempts": attempts, "accepted": not problems, "span": list(span),
            "features": [f["type"] for f in feats], "generator": GENERATOR, "references": refs[:2]}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"img": img, "span": span, "problems": problems, "accepted": not problems, "cached": False,
            "attempts": attempts}


def _wall_fill(img: Image.Image) -> tuple:
    a = np.asarray(img.convert("RGB")).reshape(-1, 3).astype(np.float32)
    lum = a @ np.array([0.299, 0.587, 0.114], np.float32)
    return tuple(int(v) for v in np.median(a[lum >= np.percentile(lum, 55)], axis=0))


def match_colour(img: Image.Image, ref: Image.Image) -> Image.Image:
    """Per-channel mean and spread of img in Lab moved to those of ref (two generations of one wall differ in tone)."""
    import cv2
    a = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2LAB).astype(np.float32)
    r = cv2.cvtColor(np.asarray(ref.convert("RGB")), cv2.COLOR_RGB2LAB).astype(np.float32)
    for c in range(3):
        m, s = a[..., c].mean(), a[..., c].std() + 1e-6
        a[..., c] = (a[..., c] - m) * (r[..., c].std() / s if c == 0 else 1.0) + r[..., c].mean()
    return Image.fromarray(cv2.cvtColor(a.clip(0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB))


def compose(base: Image.Image | None, seg: Image.Image, face: dict, span, h_px: int) -> Image.Image:
    """The face texture: the bay elevation (or the plain wall colour) with the feature span pasted at its place."""
    L = float(face["length_m"])
    if base is None:
        base = Image.new("RGB", (max(8, int(round(L * h_px / max(1e-6, float(face["z1"]) - float(face["z0"]))))), h_px),
                         _wall_fill(seg))
    else:
        seg = match_colour(seg, base)       # drawn separately: bring it to the tone of the part's bay elevation
    out = base.copy()
    x0, x1 = int(round(span[0] / L * out.width)), int(round(span[1] / L * out.width))
    if x1 - x0 >= 2:
        out.paste(seg.resize((x1 - x0, out.height), Image.LANCZOS), (x0, 0))
    return out


def items_for(prj, spec: dict, parts: dict, refs_for, done: set, notes: list, generate=None,
              owner_notes: dict | None = None) -> list:
    """autotex items of generated textures for the faces not in `done` (projected faces): the part's bay elevation
    tiled on every face, with a face-specific elevation of the feature span on the faces that carry features."""
    from .. import autotex
    items = []
    owner_notes = owner_notes or {}
    special = {(pid, f["edge_index"]): (f, feats) for pid, f, feats in feature_faces(spec, parts, done)}
    for pid, part in parts.items():
        faces = [f for f in autotex.part_faces(spec, part) if (pid, f["edge_index"]) not in done]
        if not faces:
            continue
        res = generate_part(prj, spec, part, refs_for(pid), generate, owner_notes.get(pid, ""))
        notes.append({"generated": pid, **{k: res.get(k) for k in ("problems", "attempts", "cached", "accepted",
                                                                    "error")}})
        ok = not res.get("error") and res.get("accepted")
        h_px = int(min(512, max(64, float(part["height"]) * TEX_PX_PER_M)))
        src = {"id": "codex-generated", "licence": "ai_generated", "author": f"{GENERATOR}, style from "
               + ", ".join(Path(r).name for r in refs_for(pid)[:2])}
        for f in faces:
            base = tile(res["img"], res["n_bays"], f["n_bays"], h_px) if ok else None
            sp = special.get((pid, f["edge_index"]))
            if sp is not None:
                fr = generate_face(prj, spec, part, f, sp[1], refs_for(pid), generate, owner_notes.get(pid, ""))
                notes.append({"generated": f"{pid}#{f['edge_index']}", "face": True,
                              "features": [x["type"] for x in sp[1]],
                              **{k: fr.get(k) for k in ("problems", "attempts", "cached", "accepted", "error")}})
                if not fr.get("error") and fr.get("accepted"):
                    items.append({"face": f, "img": compose(base, fr["img"], f, fr["span"], h_px), "src": src,
                                  "kind": "generated", "feature_span": list(fr["span"])})
                    continue
            if base is not None:
                items.append({"face": f, "img": base, "src": src, "kind": "generated"})
    return items


def preview_layouts(prj, spec: dict) -> list:
    """A2: the elevation layouts the generator will receive (one per part, plus each face with features), drawn into
    reports/layouts/. No AI."""
    from .. import autotex
    out_dir = prj.root / "reports" / "layouts"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    parts = autotex.generatable_parts(spec)
    special = {}
    for pid, f, feats in feature_faces(spec, parts, set()):
        special.setdefault(pid, []).append((f, feats))
    rows = []
    for pid, part in parts.items():
        faces = autotex.part_faces(spec, part)
        if not faces:
            continue
        n = max(1, min(MAX_BAYS, min(f["n_bays"] for f in faces)))
        lay, info = layout(part, n)
        lay.save(out_dir / f"{pid}.png")
        row = {"part": pid, "layout": f"reports/layouts/{pid}.png", "bays": n, "floors": info["floors"],
               "bay_m": info["bay_m"], "height_m": info["height_m"], "faces": []}
        for f, feats in special.get(pid, []):
            span = feature_span(f, part, feats)
            sl, si = segment_layout(part, f, span, feats)
            name = f"{pid}__f{f['edge_index']}.png"
            sl.save(out_dir / name)
            row["faces"].append({"edge_index": f["edge_index"], "layout": f"reports/layouts/{name}",
                                 "span_m": list(span), "features": si["labels"]})
        rows.append(row)
    return rows


# ----------------------------------------------------------------------------------------------- material tiles
MATERIAL_PROMPT = """Use the image generation tool to create ONE image, then reply with only the full path of the image file.

Create a SEAMLESS, TILEABLE, square texture of the {slot} material "{material}" of {name}, seen perfectly straight on
(no perspective), covering about {size:g} m x {size:g} m of surface.
- Colour about RGB {rgb}. Copy the real material from the attached photos of the building: {what}.
- Even, flat daylight; no shadows of objects, no windows, no edges, corners, gutters or ornaments, no sky, no plants,
  no text. Opposite edges must continue seamlessly so the image repeats without visible seams.
{feedback}"""
CURTAIN_PROMPT = """Use the image generation tool to create ONE image, then reply with only the full path of the image file.

Create a SEAMLESS, TILEABLE, square texture of the glass curtain wall ("{material}") of {name}, seen perfectly straight
on (no perspective), covering {size:g} m x {size:g} m: exactly {cols} glass panels across and {rows} floor(s) high,
vertical mullions evenly spaced, a spandrel or transom band at each floor line.
- Glass colour about RGB {rgb}; copy the real curtain wall from the attached photos: mullion colour and width, glass
  tint, spandrels, and soft, even reflections of the sky and the city (no single strong object reflected).
- No frame around the image, no building edges, no sky beyond the glass, no text. The left edge must continue into the
  right edge and the top into the bottom (the grid lines cross the edges at the same places), so it repeats seamlessly.
{feedback}"""
CURTAIN_TILE_M = 6.0              # about this wide: whole mullion bays, the floors that fit
MATERIAL_WORLD_M = 3.0
MATERIAL_STRUCTURE_MAX = 18.0     # large-scale structure of a material tile (2x a plain swatch: tiles and bricks allowed)


def check_material(img: Image.Image, srgb, curtain: bool = False) -> list[str]:
    """Problems of a generated material tile. A curtain wall reflects the sky and carries its mullion grid, so the sky
    and structure tests do not apply to it and its colour may stray further."""
    from . import quads
    probs = []
    a = np.asarray(img.convert("RGB").resize((128, 128), Image.LANCZOS))
    # no sky test: a material tile has no top edge, and grey-blue metal or slate reads as sky; colour decides
    if srgb is not None and float(np.linalg.norm(np.median(a.reshape(-1, 3), axis=0).astype(np.float32)
                                                 - np.asarray(srgb[:3], np.float32))) > COLOUR_MAX * (1.4 if curtain else 1):
        probs.append(f"the colour is far from about RGB {tuple(srgb[:3])}")
    if not curtain and quads.structure(a) > MATERIAL_STRUCTURE_MAX:
        probs.append("the image has large shapes, edges or shading: it must be an even, repeating material only")
    return probs


def _stem(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(name))[:60]


def curtain_grid(part: dict) -> tuple[int, int, float]:
    """(columns, floors, tile size m) of a square curtain-wall tile on the part's mullion and floor grid."""
    fac = part.get("facade") or {}
    bay = max(0.6, float(fac.get("bay_spacing", 1.5)))
    lvl = max(2.0, float(fac.get("level_height", 3.5)))
    cols = max(2, round(CURTAIN_TILE_M / bay))
    size = cols * bay
    return cols, max(1, round(size / lvl)), round(size, 3)


def generate_material(prj, spec: dict, material: str, slot: str, refs: list, generate=None,
                      part: dict | None = None) -> dict:
    """A seamless tile of one material (cached in reports/genfacade/mat_<material>.png). Returns {"img" (seamless,
    512 px), "world_size_m", "accepted", "problems"} or {"error"}."""
    from . import quads
    srgb = ((spec.get("materials") or {}).get(material) or {}).get("srgb")
    what = ("roof covering: tile or sheet shape, size and courses, joints" if slot == "roof"
            else "wall surface: stucco, brick, stone or concrete grain, joints and weathering")
    size, prompt_t = MATERIAL_WORLD_M, MATERIAL_PROMPT
    extra = {}
    if slot == "curtain":                    # glass curtain wall (P9): one square module of the mullion grid
        cols, rows, size = curtain_grid(part or {})
        prompt_t, extra = CURTAIN_PROMPT, {"cols": cols, "rows": rows}
    base = dict(name=spec.get("name", ""), slot=slot, material=material, size=size,
                rgb=tuple(srgb[:3]) if srgb else "as in the photos", what=what, **extra)
    ref_paths = [prj.root / r for r in refs][:2]
    out_dir = prj.root / "reports" / "genfacade"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = "mat_" + _stem(material)
    key = _key("material", json.dumps(base, sort_keys=True), *[p.read_bytes() for p in ref_paths if p.is_file()])
    img_path, meta_path = out_dir / f"{stem}.png", out_dir / f"{stem}.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    if meta.get("key") == key and img_path.is_file():
        img = Image.open(img_path).convert("RGB")
        return {"img": quads.seamless(img, 512), "world_size_m": size, "cached": True,
                "accepted": meta.get("accepted", False), "problems": meta.get("problems", [])}
    feedback, problems, img, attempts = "", [], None, 0
    for _ in range(2):
        attempts += 1
        try:
            got = (generate or codex_generate)(prompt_t.format(**base, feedback=feedback),
                                               [p for p in ref_paths if p.is_file()])
        except Exception as exc:
            return {"error": f"generation failed: {str(exc)[:200]}", "attempts": attempts}
        img = Image.open(got).convert("RGB")
        problems = check_material(img, srgb, curtain=slot == "curtain")
        if not problems:
            break
        feedback = "The previous attempt had these problems, fix them: " + "; ".join(problems) + "."
    img.save(img_path)
    meta_path.write_text(json.dumps({"key": key, "problems": problems, "attempts": attempts,
                                     "accepted": not problems, "slot": slot, "generator": GENERATOR,
                                     "references": refs[:2]}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"img": quads.seamless(img, 512), "world_size_m": size, "cached": False,
            "accepted": not problems, "problems": problems, "attempts": attempts}


def is_glass(name: str, mat: dict) -> bool:
    return "glass" in name.lower() or float((mat or {}).get("metallic", 0) or 0) > 0.5


def material_tiles(prj, spec: dict, refs_for, skip: set, notes: list, generate=None) -> dict:
    """{material: (tile, world_size_m, src)} generated for the roof and wall materials of the enabled parts (glass,
    metal and openings excluded, and the materials in skip, e.g. the owner's own textures)."""
    mats = spec.get("materials") or {}
    want = {}
    for p in spec.get("parts", []):
        if p.get("enabled", True) is False:
            continue
        curtain = (p.get("facade") or {}).get("opening_type") == "curtain_wall"
        for slot in ("roof", "wall"):
            m = (p.get("material") or {}).get(slot)
            if not m or m not in mats or m in skip or m == "opening":
                continue
            if slot == "wall" and curtain and is_glass(m, mats[m]):
                want[m] = ("curtain", p)                       # glass of a curtain wall: the mullion module
            elif not is_glass(m, mats[m]):
                want.setdefault(m, (slot, p))
    out = {}
    for m, (slot, part) in sorted(want.items()):
        refs = refs_for(part["id"])
        res = generate_material(prj, spec, m, slot, refs, generate, part=part)
        notes.append({"generated": f"material {m}", "material": True,
                      **{k: res.get(k) for k in ("problems", "attempts", "cached", "accepted", "error")}})
        if res.get("error") or not res.get("accepted"):
            continue
        out[m] = (res["img"], res["world_size_m"], {"id": "codex-generated", "licence": "ai_generated",
                                                     "author": f"{GENERATOR}, style from "
                                                     + ", ".join(Path(r).name for r in refs[:2])})
    return out
