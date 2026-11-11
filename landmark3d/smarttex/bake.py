"""T5 (first slice): projective texturing of wall faces from solved photo cameras.

For every wall face of the model, each texel (a point on the real wall plane, 1/PX_PER_M metre apart) is projected
into every photo with a solved camera (input/cameras.json, status "solved" or "manual", passing its checks).
A photo may colour the texel when:
- the texel is in front of the camera and inside the image;
- the face is the visible surface at that pixel (face-id render of the model from the same camera, so a texel hidden
  behind another part takes nothing from that photo);
- the pixel is not sky.
Each photo is scored by viewing angle and resolution (cos(angle) x pixels per metre); the colour is a soft blend
weighted by score^SHARPNESS, so the best view dominates without hard seams. Each face gets an image and a coverage
mask; texels no photo sees are left for the caller to fill.
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image

from . import keypoints as kp
from .camera import sky_of

PX_PER_M = 32
MAX_SIDE = 1024
MIN_COS = 0.25               # faces seen more obliquely than ~75 degrees take nothing from that photo
SHARPNESS = 4.0
VIS_RADIUS = 1               # the face id must appear within this many pixels of the projected texel


def wall_faces(spec: dict, min_len: float = 2.0) -> list:
    """[{part, edge_index, a, b, zb, zt, length, normal}] for every wall face of the untapered parts."""
    out = []
    for pid, (ring, zb, zt) in kp.part_rings(spec).items():
        if zt is None:
            continue
        n = len(ring)
        for i in range(n):
            a, b = np.array(ring[i], float), np.array(ring[(i + 1) % n], float)
            L = float(np.linalg.norm(b - a))
            if L < min_len:
                continue
            d = (b - a) / L
            out.append({"part": pid, "edge_index": i, "a": a, "b": b, "zb": zb, "zt": zt, "length": L,
                        "normal": np.array([d[1], -d[0], 0.0])})
    return out


def usable_cameras(prj, spec: dict, require_pass: bool = True) -> list:
    """[(camera, rgb array, record)] for photos whose camera is solved (or hand-placed) and passes its checks."""
    doc = prj.read_json("input/cameras.json", {}) or {}
    out = []
    for rec in doc.get("photos") or []:
        if rec.get("status") not in ("solved", "manual") or not rec.get("camera") or not rec.get("undistorted"):
            continue
        if require_pass and rec.get("gate"):
            continue
        f = prj.root / rec["undistorted"]
        if not f.is_file():
            continue
        img = np.asarray(Image.open(f).convert("RGB"))
        cam = kp.Camera.from_json(rec["camera"])
        if (cam.width, cam.height) != (img.shape[1], img.shape[0]):
            continue
        from . import masks
        lab = masks.load(prj, rec)
        out.append((cam, img, dict(rec, _mask=lab) if lab is not None and lab.shape == img.shape[:2] else rec))
    return out


def _face_ids(spec, cam):
    _, ids, faces = kp.render(spec, cam)
    index = {(p, i): k + 1 for k, (p, kind, i) in enumerate(faces) if kind == "wall"}
    return ids, index


def bake(spec: dict, cameras: list, px_per_m: int = PX_PER_M) -> list:
    """Projective textures for every wall face seen by at least one camera.
    cameras: [(Camera, rgb array, record)]. Returns [{face, img (PIL), mask (bool array), coverage, sources}]."""
    if not cameras:
        return []
    prepared = []
    for cam, rgb, rec in cameras:
        ids, index = _face_ids(spec, cam)
        lab = rec.get("_mask")                      # T4 labels: anything not 0 (sky, occluder, other building)
        sky = (lab != 0) if lab is not None else sky_of(rgb)
        prepared.append({"cam": cam, "rgb": rgb.astype(np.float32), "ids": ids, "index": index, "sky": sky,
                         "rec": rec, "dil": {}, "k": 2 * VIS_RADIUS + 1})   # dil: per-face visibility, built lazily
    out = []
    for face in wall_faces(spec):
        L, h = face["length"], face["zt"] - face["zb"]
        W = int(min(MAX_SIDE, max(8, round(L * px_per_m))))
        H = int(min(MAX_SIDE, max(8, round(h * px_per_m))))
        us = (np.arange(W) + 0.5) / W
        vs = (np.arange(H) + 0.5) / H
        uu, vv = np.meshgrid(us, vs)
        xy = face["a"][None, None, :] + (face["b"] - face["a"])[None, None, :] * uu[..., None]
        z = face["zt"] - vv * h
        P = np.concatenate([xy, z[..., None]], axis=2).reshape(-1, 3)
        acc = np.zeros((P.shape[0], 3), np.float64)
        wsum = np.zeros(P.shape[0], np.float64)
        sources = []
        for pc in prepared:
            cam = pc["cam"]
            fid = pc["index"].get((face["part"], face["edge_index"]))
            if fid is None:
                continue
            C = cam.centre
            mid = P.mean(axis=0)
            view = C - mid
            cos = float(face["normal"] @ view / max(np.linalg.norm(view), 1e-9))
            if cos < MIN_COS:
                continue
            depth = cam.depth(P)
            uv = cam.project(P)
            Hc, Wc = pc["ids"].shape
            inside = (depth > 0.1) & (uv[:, 0] >= 0) & (uv[:, 0] <= Wc - 1) & (uv[:, 1] >= 0) & (uv[:, 1] <= Hc - 1)
            if not inside.any():
                continue
            if fid not in pc["dil"]:
                m = (pc["ids"] == fid).astype(np.uint8)
                pc["dil"][fid] = cv2.dilate(m, np.ones((pc["k"], pc["k"]), np.uint8)) > 0 if pc["k"] > 1 else m > 0
            xi = np.clip(np.round(uv[:, 0]).astype(int), 0, Wc - 1)
            yi = np.clip(np.round(uv[:, 1]).astype(int), 0, Hc - 1)
            ok = inside & pc["dil"][fid][yi, xi] & ~pc["sky"][yi, xi]
            if not ok.any():
                continue
            res = cam.f / np.maximum(depth, 0.1)                     # pixels per metre at each texel
            score = cos * res
            w = np.where(ok, score ** SHARPNESS, 0.0)
            mx = uv[:, 0].astype(np.float32).reshape(H, W)
            my = uv[:, 1].astype(np.float32).reshape(H, W)
            col = cv2.remap(pc["rgb"], mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).reshape(-1, 3)
            acc += col * w[:, None]
            wsum += w
            sources.append({"photo": pc["rec"].get("photo"), "source_id": pc["rec"].get("source_id"),
                            "texels": int(ok.sum())})
        mask = wsum > 0
        if not mask.any():
            continue
        img = np.zeros((P.shape[0], 3), np.float32)
        img[mask] = (acc[mask] / wsum[mask, None]).astype(np.float32)
        out.append({"face": face, "img": Image.fromarray(img.reshape(H, W, 3).clip(0, 255).astype(np.uint8)),
                    "mask": mask.reshape(H, W), "coverage": round(float(mask.mean()), 3), "sources": sources})
    return out


def fill(img: Image.Image, mask: np.ndarray, background: Image.Image | None = None) -> Image.Image:
    """Uncovered texels: the background texture (e.g. the wall swatch, tiled) where given, else inpainting."""
    a = np.asarray(img.convert("RGB")).copy()
    hole = ~mask
    if not hole.any():
        return img
    if background is not None:
        bg = np.asarray(background.convert("RGB"))
        H, W = a.shape[:2]
        reps = (H // bg.shape[0] + 1, W // bg.shape[1] + 1, 1)
        tiled = np.tile(bg, reps)[:H, :W]
        a[hole] = tiled[hole]
        return Image.fromarray(a)
    return Image.fromarray(cv2.inpaint(a, hole.astype(np.uint8) * 255, 5, cv2.INPAINT_TELEA))


PROJECT_MIN_COVERAGE = 0.7   # a face is textured by projection when photos see at least this share of it


def project_items(prj, spec: dict, notes: list | None = None, provider=None) -> list:
    """autotex items for the faces that solved cameras cover well enough: the projected texture, holes inpainted.
    Each item names the photo that gave it the most texels (licence and attribution). The T4 masks of the photos
    with usable cameras are made first (AI occluder boxes when a provider is given)."""
    if not usable_cameras(prj, spec):
        return []
    from . import masks
    made = masks.ensure(prj, provider, spec.get("name") or prj.state().get("name", ""),
                        only={c[2].get("photo") for c in usable_cameras(prj, spec)})
    if notes is not None:
        notes.append({"masks": made})
    cams = usable_cameras(prj, spec)
    sources = {s.get("id"): s for s in prj.read_json("input/sources.json", {"sources": []})["sources"]}
    items = []
    for r in bake(spec, cams):
        f = r["face"]
        rec = {"face": f"{f['part']}#{f['edge_index']}", "coverage": r["coverage"],
               "photos": [s["photo"] for s in r["sources"]]}
        if r["coverage"] < PROJECT_MIN_COVERAGE:
            rec["skipped"] = f"coverage {r['coverage']:.2f} < {PROJECT_MIN_COVERAGE}"
            if notes is not None:
                notes.append({"projected": rec})
            continue
        main = max(r["sources"], key=lambda s: s["texels"])
        img = fill(r["img"], r["mask"])
        items.append({"face": {"face": rec["face"], "part": f["part"], "edge_index": f["edge_index"],
                               "z0": f["zb"], "z1": f["zt"], "length_m": round(f["length"], 2)},
                      "img": img, "src": sources.get(main["source_id"], {"id": main["source_id"]}),
                      "kind": "projected", "coverage": r["coverage"]})
        if notes is not None:
            notes.append({"projected": rec})
    return items
