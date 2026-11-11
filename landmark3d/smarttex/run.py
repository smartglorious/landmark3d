"""T2 driver: per texture-licensed photo, lens intake, AI keypoints, camera solve; writes input/cameras.json and
review overlays (reports/texture_review/, never committed)."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .. import autotex
from . import camera as cam_mod
from . import keypoints as kp
from . import lens

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "schemas" / "cameras.schema.json"
WORK_PX = 1920                    # photos are analysed and projected at most this size (long side)

PROMPT = """Landmark: {name}. Two images:
1. A photo of the building, with a yellow grid labelled 0.0-1.0 from the TOP-LEFT corner (x to the right, y down).
2. A line sketch of our simplified 3D model seen from roughly the same direction, with numbered red points on the
   corners of its walls (where a vertical wall corner meets the ground / base, or the top of the wall / cornice
   line). The sketch's viewpoint and proportions are only approximate.
For each numbered point, find the SAME building corner in the photo and give its position as grid fractions
(x, y), as precisely as you can (read the grid; positions are refined automatically afterwards, but a point on the
wrong corner spoils the result). Top points sit where the wall's top edge (cornice line, not the roof ridge or a
balustrade above it) meets the vertical corner; base points where the corner meets the ground or the part below.
Omit a point when that corner is hidden (trees, vehicles, another part), outside the photo, or you are not sure
which corner it is. confidence: 1 = clearly visible and certain, 0.5 = probably.
Reply with one JSON object: {{"points": [{{"n": <number>, "x": <0-1>, "y": <0-1>, "confidence": <0-1>}}],
"notes": "..."}}"""


def spec_digest(spec: dict) -> str:
    return autotex.spec_hash(spec)


def _views_by_path(spec: dict) -> dict:
    order = {k: p for k, p in enumerate(spec.get("photos") or [], start=1)}
    out = {}
    for v in spec.get("views") or []:
        p = order.get(v.get("photo"))
        if p:
            out[p] = v
    return out


def ask_keypoints(prj, provider, photo_img: Image.Image, sketch_img: Image.Image, labels: dict, name: str,
                  stem: str) -> dict:
    """{label: (x, y, confidence)} in photo fractions, from the AI. labels: {label: keypoint id} in the sketch."""
    from ..research import ai
    n_labels = len(labels)
    out_dir = prj.root / "reports" / "texture_review"
    out_dir.mkdir(parents=True, exist_ok=True)
    gp = out_dir / f"{stem}_grid.jpg"
    sp = out_dir / f"{stem}_sketch.png"
    autotex.grid_overlay(photo_img, "photo").save(gp, "JPEG", quality=90)
    sketch_img.save(sp)

    def check(o):
        pts = o.get("points")
        if not isinstance(pts, list):
            return ["points list required"]
        errs = []
        for p in pts:
            try:
                n, x, y = int(p["n"]), float(p["x"]), float(p["y"])
            except (KeyError, TypeError, ValueError):
                errs.append(f"bad point {p}")
                continue
            if not (1 <= n <= n_labels) or not (0 <= x <= 1 and 0 <= y <= 1):
                errs.append(f"point {n}: n in 1..{n_labels}, x and y in 0..1")
        return errs
    obj, _ = ai.ask_json(provider, "You locate building corners in photos. Reply with one JSON object only.",
                         PROMPT.format(name=name), validator=check, images=[gp, sp], retries=1)
    return {str(int(p["n"])): (float(p["x"]), float(p["y"]), float(p.get("confidence", 0.7))) for p in obj["points"]}


def solve_photo(prj, provider, spec: dict, path: str, src: dict, view: dict | None, name: str, k: int,
                ask=ask_keypoints) -> dict:
    rec = {"source_id": src.get("id"), "path": path, "photo": k}
    img = Image.open(prj.root / path)
    img.load()
    info = lens.intake(img)
    rec["lens"] = {kk: info[kk] for kk in ("k", "straight_before", "straight_after", "chains", "f_exif_px")}
    rec["projection"] = info["projection"]
    if info["projection"] == "panorama":
        rec.update(status="skipped", why="; ".join(info.get("why") or ["panorama"]))
        return rec
    work = lens.undistort_image(img, info["k"])
    scale = min(1.0, WORK_PX / max(work.size))
    if scale < 1.0:
        work = work.resize((round(work.width * scale), round(work.height * scale)), Image.LANCZOS)
    und = Path("input") / "undistorted" / f"{Path(path).stem}.jpg"
    (prj.root / und).parent.mkdir(parents=True, exist_ok=True)
    work.save(prj.root / und, "JPEG", quality=92)
    rec["undistorted"] = und.as_posix()
    if not view:
        rec.update(status="skipped", why="no matched viewpoint for this photo (spec views)")
        return rec
    f_prior = info["f_exif_px"] * scale if info["f_exif_px"] else None
    W, H = work.size
    seed = kp.seed_camera(spec, view, W, H, f_prior, part=view.get("focus") if view.get("focus") != "whole" else None)
    sk, labels = kp.sketch(spec, seed)
    if len(labels) < 4:
        rec.update(status="failed", why="fewer than 4 model corners visible from the matched viewpoint")
        return rec
    stem = f"photo{k}"
    cache = prj.root / "reports" / "texture_review" / f"{stem}_ai.json"
    key = {"spec": spec_digest(spec), "path": path, "labels": labels, "size": [W, H]}
    cached = prj.read_json(f"reports/texture_review/{stem}_ai.json") if cache.is_file() else None
    if cached and cached.get("key") == key:
        got = {lab: tuple(v) for lab, v in cached["points"].items()}
    else:
        try:
            got = ask(prj, provider, work, sk, labels, name, stem)
        except Exception as exc:                      # AI failure is reported, never replaced by a guess
            rec.update(status="failed", why=f"AI keypoints failed: {str(exc)[:200]}")
            return rec
        prj.write_json(f"reports/texture_review/{stem}_ai.json", {"key": key, "points": got})
    coarse = {labels[lab]: (x * W, y * H) for lab, (x, y, c) in got.items() if lab in labels and c >= 0.4}
    rec["ai_points"] = len(coarse)
    rec["coarse"] = {kid: [round(u, 1), round(v, 1)] for kid, (u, v) in coarse.items()}
    res = cam_mod.solve(spec, np.asarray(work.convert("L")), coarse, f_prior=f_prior, view=view)
    if res["status"] != "solved":
        rec.update(status="failed", why=res.get("why", ""))
        return rec
    c = res["camera"]
    pts = [((p["uv"][0], p["uv"][1]), (0, 255, 0) if p["source"] == "snapped" else (255, 160, 0))
           for p in res["keypoints"]] + [(tuple(uv), (255, 0, 255)) for uv in coarse.values()]
    ov = Path("reports") / "texture_review" / f"{stem}_camera.jpg"
    (prj.root / ov).parent.mkdir(parents=True, exist_ok=True)
    kp.overlay(work, spec, c, pts).save(prj.root / ov, "JPEG", quality=88)
    rec.update(status="solved", camera=c.to_json(), keypoints=res["keypoints"], overlay=ov.as_posix(), gate=res["gate"],
               metrics={kk: res.get(kk) for kk in ("rms", "rms_px", "chamfer", "chamfer_before", "support",
                                                   "ai_agreement", "inliers", "given", "inlier_share", "parts",
                                                   "mismatch", "f_sources", "vps", "start", "silhouette")})
    return rec


def run(prj, provider=None, ask=ask_keypoints, only: list | None = None) -> dict:
    """Solve cameras for the project's texture-licensed photos. Writes input/cameras.json; returns a summary."""
    from ..research import ai
    spec = prj.read_json("spec/landmark_spec.json")
    if not spec:
        return {"skipped": "no spec"}
    provider = provider if provider is not None else ai.get("vision")
    if provider is None and ask is ask_keypoints:
        return {"skipped": "no AI provider signed in"}
    photos = autotex._texture_photos(prj, spec)
    views = _views_by_path(spec)
    name = prj.state()["name"]
    recs = []
    for k, (path, src) in enumerate(photos, start=1):
        if only and k not in only:
            continue
        t = time.time()
        rec = solve_photo(prj, provider, spec, path, src, views.get(path), name, k, ask=ask)
        rec["seconds"] = round(time.time() - t, 1)
        recs.append(rec)
        prj.log("camera solve", {"photo": k, "status": rec["status"], "why": rec.get("why"),
                                 "rms": (rec.get("metrics") or {}).get("rms")})
    doc = {"version": 1, "spec_hash": spec_digest(spec), "written": time.strftime("%Y-%m-%dT%H:%M:%S"), "photos": recs}
    prj.write_json("input/cameras.json", doc)
    return summary(doc)


def summary(doc: dict) -> dict:
    recs = doc.get("photos") or []
    solved = [r for r in recs if r["status"] == "solved"]
    passing = [r for r in solved if not r.get("gate")]
    rms = [r["metrics"]["rms"] for r in solved if r.get("metrics", {}).get("rms") is not None]
    ch = [r["metrics"]["chamfer"] for r in solved if r.get("metrics", {}).get("chamfer") is not None]
    return {"photos": len(recs), "solved": len(solved), "passing_gate": len(passing),
            "skipped": sum(1 for r in recs if r["status"] == "skipped"),
            "failed": sum(1 for r in recs if r["status"] == "failed"),
            "rms_median": round(float(np.median(rms)), 5) if rms else None,
            "chamfer_median": round(float(np.median(ch)), 5) if ch else None,
            "mismatch": sorted({p for r in solved for p in (r.get("metrics") or {}).get("mismatch") or []})}


def schema_errors(doc) -> list[str]:
    import jsonschema
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    return [f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}"
            for e in jsonschema.Draft202012Validator(schema).iter_errors(doc)]


# ----------------------------------------------------------------------------------------------- T3 editor
def _photo_list(prj, spec):
    return autotex._texture_photos(prj, spec)


def _record(prj, k: int) -> dict:
    doc = prj.read_json("input/cameras.json", {}) or {}
    return next((r for r in doc.get("photos") or [] if r.get("photo") == k), {})


def _ensure_undistorted(prj, path: str, rec: dict) -> tuple[Image.Image, dict]:
    """The undistorted working image of a photo (made and recorded on first use) and its lens record."""
    und = rec.get("undistorted")
    if und and (prj.root / und).is_file() and rec.get("lens"):
        return Image.open(prj.root / und).convert("RGB"), rec
    img = Image.open(prj.root / path)
    img.load()
    info = lens.intake(img)
    work = lens.undistort_image(img, info["k"])
    scale = min(1.0, WORK_PX / max(work.size))
    if scale < 1.0:
        work = work.resize((round(work.width * scale), round(work.height * scale)), Image.LANCZOS)
    rel = (Path("input") / "undistorted" / f"{Path(path).stem}.jpg").as_posix()
    (prj.root / rel).parent.mkdir(parents=True, exist_ok=True)
    work.save(prj.root / rel, "JPEG", quality=92)
    rec = dict(rec, undistorted=rel, projection=info["projection"],
               lens={kk: info[kk] for kk in ("k", "straight_before", "straight_after", "chains", "f_exif_px")},
               lens_scale=scale)
    return work, rec


def _label(kid: str) -> str:
    part, i, level = kid.split("/")
    return f"{part} · góc {i} · {'đỉnh tường' if level == 't' else 'chân tường'}"


def editor_photo(prj, k: int) -> dict:
    """Everything the editor shows for photo k: the undistorted image, the solved camera's wireframe and corners
    (if any), the hand-placed points, a numbered model sketch to identify corners, and the check results."""
    spec = prj.read_json("spec/landmark_spec.json")
    photos = _photo_list(prj, spec)
    if not 1 <= k <= len(photos):
        raise ValueError(f"photo {k} not found (1..{len(photos)})")
    path, src = photos[k - 1]
    rec = _record(prj, k) or {"photo": k, "path": path, "source_id": src.get("id"), "status": "skipped"}
    img, rec = _ensure_undistorted(prj, path, rec)
    W, H = img.size
    view = _views_by_path(spec).get(path)
    cam = kp.Camera.from_json(rec["camera"]) if rec.get("camera") else None
    if cam is not None and (cam.width, cam.height) != (W, H):
        cam = None
    out = {"photo": k, "path": path, "image": rec["undistorted"], "size": [W, H], "status": rec.get("status"),
           "why": rec.get("why"), "projection": rec.get("projection"), "gate": rec.get("gate"),
           "metrics": rec.get("metrics"), "manual": rec.get("manual") or {},
           "labels": {kid: _label(kid) for kid in kp.model(spec)[0]}}
    if cam is not None:
        pts, edges = kp.visible(spec, cam, margin=0.3)
        rgb, ids, faces = kp.render(spec, cam)
        shown = kp.unoccluded(spec, cam, pts, ids, faces)
        out["edges"] = [[*map(lambda v: round(float(v), 1), pts[a]), *map(lambda v: round(float(v), 1), pts[b])]
                        for a, b, _, _ in edges]
        out["corners"] = {kid: [round(float(u), 1), round(float(v), 1)] for kid, (u, v) in shown.items()}
    seed = cam or kp.seed_camera(spec, view or {"azimuth_deg": 270, "elevation_deg": 8}, W, H,
                                 part=(view or {}).get("focus") if (view or {}).get("focus") != "whole" else None)
    sk, labels = kp.sketch(spec, seed)
    rel = f"reports/texture_review/editor_photo{k}_sketch.png"
    (prj.root / rel).parent.mkdir(parents=True, exist_ok=True)
    sk.save(prj.root / rel)
    out["sketch"] = rel
    out["sketch_labels"] = labels
    return out


def editor_solve(prj, k: int, points: dict, drop_outliers: bool = False) -> dict:
    """Solve photo k's camera from hand-placed points {keypoint id: [u, v]} (undistorted image pixels), store them
    with source "manual" in input/cameras.json, and return the editor view. No points: the manual entry is cleared."""
    spec = prj.read_json("spec/landmark_spec.json")
    photos = _photo_list(prj, spec)
    if not 1 <= k <= len(photos):
        raise ValueError(f"photo {k} not found (1..{len(photos)})")
    path, src = photos[k - 1]
    doc = prj.read_json("input/cameras.json", {}) or {}
    doc.setdefault("version", 1)
    doc["spec_hash"] = spec_digest(spec)
    recs = [r for r in doc.get("photos") or [] if r.get("photo") != k]
    rec = _record(prj, k) or {"photo": k, "path": path, "source_id": src.get("id"), "status": "skipped"}
    img, rec = _ensure_undistorted(prj, path, rec)
    kps = kp.model(spec)[0]
    pts = {kid: (float(u), float(v)) for kid, (u, v) in (points or {}).items() if kid in kps}
    rec["manual"] = {kid: [round(u, 1), round(v, 1)] for kid, (u, v) in pts.items()}
    if not pts:
        rec.pop("manual", None)
    else:
        lens_rec = rec.get("lens") or {}
        f_prior = lens_rec.get("f_exif_px") * rec.get("lens_scale", 1.0) if lens_rec.get("f_exif_px") else None
        res = cam_mod.solve_manual(spec, np.asarray(img.convert("L")), pts, f_prior=f_prior,
                                   view=_views_by_path(spec).get(path), drop_outliers=drop_outliers)
        for kid in res.get("dropped") or []:               # dropped AI points leave the editable set too
            rec["manual"].pop(kid, None)
        if res["status"] == "manual":
            rec.pop("why", None)
            rec.update(status="manual", camera=res["camera"].to_json(), keypoints=res["keypoints"], gate=res["gate"],
                       metrics={kk: res.get(kk) for kk in ("rms", "rms_px", "chamfer", "support",
                                                                      "silhouette", "ai_agreement", "inliers", "given",
                                                                      "mismatch", "f_sources", "off_points", "start")})
        else:
            rec.update(status="failed", why=res.get("why"), gate=[f"camera not solved: {res.get('why')}"])
            rec.pop("camera", None)
    recs.append(rec)
    doc["photos"] = sorted(recs, key=lambda r: r.get("photo", 0))
    prj.write_json("input/cameras.json", doc)
    prj.log("camera edited by hand", {"photo": k, "points": len(pts), "status": rec.get("status")})
    return editor_photo(prj, k)


def editor_list(prj) -> dict:
    spec = prj.read_json("spec/landmark_spec.json")
    if not spec:
        return {"photos": []}
    out = []
    for k, (path, src) in enumerate(_photo_list(prj, spec), start=1):
        rec = _record(prj, k)
        out.append({"photo": k, "path": path, "status": rec.get("status") or "none",
                    "passes": bool(rec.get("camera")) and not rec.get("gate"),
                    "manual_points": len(rec.get("manual") or {})})
    return {"photos": out}


# ----------------------------------------------------------------------------------------------- AI auto-place
REFINE_CROP = 0.10           # zoom window around each rough point, share of the photo's long side
REFINE_PX = 360              # each zoom window is shown at this size

REFINE_PROMPT = """Landmark: {name}. The contact sheet shows {n} zoomed crops of one photo of the building. Crop k
is centred (red cross) on a ROUGH guess of one building corner, described here:
{items}
Each crop has a faint grid labelled 0.0-1.0 from its TOP-LEFT (x to the right, y down). For each crop give the
EXACT position of that corner inside the crop: where the wall's vertical corner edge meets the top of the wall
(cornice line) for a "top" corner, or meets the ground or the part below for a "base" corner. Use the actual edges
in the image, not the red cross. If that corner is not inside the crop or is hidden, give found: false.
Reply with one JSON object: {{"points": [{{"n": 1, "found": true, "x": 0.5, "y": 0.5}}]}}"""


def _refine_sheet(img: Image.Image, pts: list) -> Image.Image:
    from PIL import ImageDraw
    W, H = img.size
    half = REFINE_CROP * max(W, H) / 2
    cells = []
    for n, (u, v) in enumerate(pts, start=1):
        crop = img.crop((int(u - half), int(v - half), int(u + half), int(v + half))).resize((REFINE_PX, REFINE_PX),
                                                                                             Image.LANCZOS)
        d = ImageDraw.Draw(crop, "RGBA")
        for t in range(1, 10):
            x = REFINE_PX * t / 10
            d.line([(x, 0), (x, REFINE_PX)], fill=(255, 255, 0, 70))
            d.line([(0, x), (REFINE_PX, x)], fill=(255, 255, 0, 70))
            d.text((x + 1, 1), f"{t / 10:.1f}", fill=(255, 255, 0, 220))
            d.text((1, x + 1), f"{t / 10:.1f}", fill=(255, 255, 0, 220))
        c = REFINE_PX / 2
        d.line([(c - 10, c), (c + 10, c)], fill=(255, 0, 0, 255), width=2)
        d.line([(c, c - 10), (c, c + 10)], fill=(255, 0, 0, 255), width=2)
        cell = Image.new("RGB", (REFINE_PX, REFINE_PX + 26), "white")
        cell.paste(crop, (0, 26))
        ImageDraw.Draw(cell).text((4, 4), f"crop {n}", fill="black", font=autotex._font(18))
        cells.append(cell)
    cols = 4
    rows = (len(cells) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (REFINE_PX + 8), rows * (REFINE_PX + 34)), "white")
    for i, c in enumerate(cells):
        sheet.paste(c, ((i % cols) * (REFINE_PX + 8), (i // cols) * (REFINE_PX + 34)))
    return sheet


def ask_refine(prj, provider, img: Image.Image, coarse: dict, name: str, stem: str) -> dict:
    """{keypoint id: (u, v)} refined by one AI look at zoomed crops around the rough points (cached)."""
    import hashlib
    from ..research import ai
    ids = list(coarse)
    pts = [coarse[k] for k in ids]
    sheet = _refine_sheet(img, pts)
    path = prj.root / "reports" / "texture_review" / f"{stem}_refine_sheet.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, "JPEG", quality=90)
    items = "\n".join(f"- crop {n}: {_label(k)} ({'top of the wall' if k.endswith('/t') else 'base of the wall'})"
                      for n, k in enumerate(ids, start=1))
    prompt = REFINE_PROMPT.format(name=name, n=len(ids), items=items)
    key = hashlib.sha256(prompt.encode("utf-8") + path.read_bytes()).hexdigest()[:20]
    cached = prj.read_json(f"reports/texture_review/{stem}_refine_ai.json", None)
    if cached and cached.get("key") == key:
        obj = cached["answer"]
    else:
        def check(o):
            p = o.get("points")
            if not isinstance(p, list):
                return ["points list required"]
            bad = []
            for x in p:
                try:
                    ok = 1 <= int(x.get("n", 0)) <= len(ids)
                    if x.get("found"):
                        ok = ok and 0 <= float(x["x"]) <= 1 and 0 <= float(x["y"]) <= 1
                except (KeyError, TypeError, ValueError):
                    ok = False
                if not ok:
                    bad.append(f"bad point {x}")
            return bad
        obj, _ = ai.ask_json(provider, "You locate building corners precisely in zoomed photo crops. Reply with one "
                                       "JSON object only.", prompt, validator=check, images=[path], retries=1)
        prj.write_json(f"reports/texture_review/{stem}_refine_ai.json", {"key": key, "answer": obj})
    W, H = img.size
    half = REFINE_CROP * max(W, H) / 2
    out = {}
    for x in obj.get("points", []):
        if not x.get("found"):
            continue
        n = int(x["n"])
        u, v = pts[n - 1]
        out[ids[n - 1]] = (u - half + float(x["x"]) * 2 * half, v - half + float(x["y"]) * 2 * half)
    return out


def auto_place(prj, k: int, provider, ask=None, refine=None) -> dict:
    """AI places the corner points of photo k by itself: rough points on the whole photo, then each refined in a
    zoomed crop, then the camera is solved from them as if hand-placed. Points the solved camera cannot explain
    are dropped and the camera is solved again. The points stay editable in the editor (stored as manual, by AI)."""
    ask = ask or ask_keypoints
    refine = refine or ask_refine
    spec = prj.read_json("spec/landmark_spec.json")
    photos = _photo_list(prj, spec)
    if not 1 <= k <= len(photos):
        raise ValueError(f"photo {k} not found (1..{len(photos)})")
    path, src = photos[k - 1]
    rec = _record(prj, k) or {"photo": k, "path": path, "source_id": src.get("id"), "status": "skipped"}
    img, rec = _ensure_undistorted(prj, path, rec)
    if rec.get("projection") == "panorama":
        raise ValueError("this photo is a stitched panorama: it cannot be used for projection")
    W, H = img.size
    view = _views_by_path(spec).get(path)
    # only a camera that passed its checks may seed the sketch: a wrong one draws the model from the wrong side
    cam = kp.Camera.from_json(rec["camera"]) if rec.get("camera") and not rec.get("gate") else None
    if cam is not None and (cam.width, cam.height) != (W, H):
        cam = None
    if view is None and cam is None:
        raise ValueError("no viewpoint is known for this photo: place 2 points by hand first")
    seed = cam or kp.seed_camera(spec, view, W, H, part=view.get("focus") if view.get("focus") != "whole" else None)
    sk, labels = kp.sketch(spec, seed, prefer_top=True)
    stem = f"photo{k}"
    key = {"spec": spec_digest(spec), "path": path, "labels": labels, "size": [W, H]}
    cached = prj.read_json(f"reports/texture_review/{stem}_ai.json", None)
    name = spec.get("name") or prj.state().get("name", "")
    if cached and cached.get("key") == key:
        got = {lab: tuple(v) for lab, v in cached["points"].items()}
    else:
        got = ask(prj, provider, img, sk, labels, name, stem)
        prj.write_json(f"reports/texture_review/{stem}_ai.json", {"key": key, "points": got})
    coarse = {labels[lab]: (x * W, y * H) for lab, (x, y, c) in got.items() if lab in labels and c >= 0.4}
    if len(coarse) < 2:
        raise ValueError(f"the AI found only {len(coarse)} corner(s) in this photo")
    fine = refine(prj, provider, img, coarse, name, stem)
    pts = {kid: fine[kid] for kid in coarse if kid in fine}
    if len(pts) < 2:
        pts = dict(coarse)
    out = editor_solve(prj, k, pts, drop_outliers=True)
    dropped = sorted(set(pts) - set(out.get("manual") or {}))
    doc = prj.read_json("input/cameras.json", {}) or {}
    for r in doc.get("photos") or []:
        if r.get("photo") == k:
            r["points_by"] = "ai"
            r["ai_rough"] = {kid: [round(u, 1), round(v, 1)] for kid, (u, v) in coarse.items()}
    prj.write_json("input/cameras.json", doc)
    out["points_by"] = "ai"
    out["dropped"] = dropped
    out["ai_points"] = {"rough": len(coarse), "refined": len(fine)}
    return out
