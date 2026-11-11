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
    r = ai._run(cmd, stdin_text=prompt, timeout=timeout, cwd=str(td))
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


def tile(img: Image.Image, n_src: int, n_face: int, h_px: int) -> Image.Image:
    """A face of n_face bays from an n_src-bay image: whole copies side by side, cut at a bay line."""
    bay_w = max(4, round(img.width * h_px / img.height / n_src))
    src = img.resize((bay_w * n_src, h_px), Image.LANCZOS)
    out = Image.new("RGB", (bay_w * n_face, h_px))
    x = 0
    while x < out.width:
        out.paste(src, (x, 0))
        x += src.width
    return out


def forget(prj, part_id: str) -> None:
    """Drop the cached elevation of a part, so the next texture run draws it again."""
    d = prj.root / "reports" / "genfacade"
    for name in (f"{part_id}.png", f"{part_id}.json"):
        (d / name).unlink(missing_ok=True)


def items_for(prj, spec: dict, parts: dict, refs_for, done: set, notes: list, generate=None,
              owner_notes: dict | None = None) -> list:
    """autotex items of generated textures for the faces not in `done` (projected or unique photo faces)."""
    from .. import autotex
    items = []
    for pid, part in parts.items():
        faces = [f for f in autotex.part_faces(spec, part) if (pid, f["edge_index"]) not in done]
        if not faces:
            continue
        res = generate_part(prj, spec, part, refs_for(pid), generate, (owner_notes or {}).get(pid, ""))
        note = {"generated": pid, **{k: res.get(k) for k in ("problems", "attempts", "cached", "accepted", "error")}}
        notes.append(note)
        if res.get("error") or not res.get("accepted"):
            continue
        h_px = int(min(512, max(64, float(part["height"]) * TEX_PX_PER_M)))
        src = {"id": "codex-generated", "licence": "ai_generated", "author": f"{GENERATOR}, style from "
               + ", ".join(Path(r).name for r in refs_for(pid)[:2])}
        for f in faces:
            items.append({"face": f, "img": tile(res["img"], res["n_bays"], f["n_bays"], h_px), "src": src,
                          "kind": "generated"})
    return items
