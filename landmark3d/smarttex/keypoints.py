"""Named 3D keypoints and edges of a spec, a pinhole camera type, projection with back-face visibility, and the
labelled wireframe sketch shown to the AI next to a photo.

Frame: the spec's local metres, x east, y north, z up. Keypoint ids are "<part>/<vertex>/b" (base) and
"<part>/<vertex>/t" (top of the wall), vertex = index in the part's CCW footprint ring.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .. import geom


@dataclass
class Camera:
    """Pinhole camera for an undistorted image: focal f (px), principal point at the image centre, pose as an OpenCV
    rotation vector and translation (world -> camera; camera x right, y down, z forward)."""
    width: int
    height: int
    f: float
    rvec: np.ndarray = field(default_factory=lambda: np.zeros(3))
    tvec: np.ndarray = field(default_factory=lambda: np.zeros(3))

    @property
    def K(self) -> np.ndarray:
        return np.array([[self.f, 0, self.width / 2.0], [0, self.f, self.height / 2.0], [0, 0, 1.0]])

    @property
    def diag(self) -> float:
        return math.hypot(self.width, self.height)

    @property
    def R(self) -> np.ndarray:
        return cv2.Rodrigues(np.asarray(self.rvec, float))[0]

    @property
    def centre(self) -> np.ndarray:
        return -self.R.T @ np.asarray(self.tvec, float).reshape(3)

    def project(self, xyz) -> np.ndarray:
        pts = np.asarray(xyz, float).reshape(-1, 3)
        uv, _ = cv2.projectPoints(pts, np.asarray(self.rvec, float), np.asarray(self.tvec, float), self.K, None)
        return uv.reshape(-1, 2)

    def depth(self, xyz) -> np.ndarray:
        pts = np.asarray(xyz, float).reshape(-1, 3)
        return (pts @ self.R.T + np.asarray(self.tvec, float).reshape(1, 3))[:, 2]

    def to_json(self) -> dict:
        return {"width": self.width, "height": self.height, "f": round(float(self.f), 3),
                "rvec": [round(float(v), 6) for v in np.ravel(self.rvec)],
                "tvec": [round(float(v), 5) for v in np.ravel(self.tvec)],
                "centre": [round(float(v), 3) for v in self.centre]}

    @classmethod
    def from_json(cls, d: dict) -> "Camera":
        return cls(int(d["width"]), int(d["height"]), float(d["f"]), np.array(d["rvec"], float),
                   np.array(d["tvec"], float))


def look_at(eye, target, up=(0.0, 0.0, 1.0)):
    """(rvec, tvec) of a camera at eye looking at target, with world z up appearing up in the image."""
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    z = target - eye
    z /= np.linalg.norm(z)
    x = np.cross(z, np.asarray(up, float))
    if np.linalg.norm(x) < 1e-9:
        x = np.array([1.0, 0, 0])
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.stack([x, y, z])                       # rows: camera axes in world coordinates
    return cv2.Rodrigues(R)[0].ravel(), -R @ eye


def part_rings(spec: dict) -> dict:
    """{part id: (CCW ring [(x, y)], z base, z top)} for enabled parts; tapered parts keep only their base ring."""
    main = [tuple(p) for p in spec["footprint"]["local"]]
    out = {}
    for p in spec.get("parts") or []:
        if p.get("enabled", True) is False:
            continue
        try:
            ring = geom.ensure_ccw(geom.footprint_polygon(p["footprint"], main))
        except (KeyError, TypeError, ValueError):
            continue
        zb = float(p.get("base_z", 0.0))
        zt = None if geom.is_tapered(p.get("taper")) else zb + float(p["height"])
        out[p["id"]] = (ring, zb, zt)
    return out


def model(spec: dict) -> tuple[dict, list]:
    """(keypoints {id: (x, y, z)}, edges [(id_a, id_b, part, face indices it borders)]). Vertical edges border the
    two faces meeting at the vertex; ring edges border their face."""
    kps, edges = {}, []
    for pid, (ring, zb, zt) in part_rings(spec).items():
        n = len(ring)
        for i, (x, y) in enumerate(ring):
            kps[f"{pid}/{i}/b"] = (float(x), float(y), zb)
            if zt is not None:
                kps[f"{pid}/{i}/t"] = (float(x), float(y), zt)
        for i in range(n):
            j = (i + 1) % n
            edges.append((f"{pid}/{i}/b", f"{pid}/{j}/b", pid, (i,)))
            if zt is not None:
                edges.append((f"{pid}/{i}/t", f"{pid}/{j}/t", pid, (i,)))
                edges.append((f"{pid}/{i}/b", f"{pid}/{i}/t", pid, ((i - 1) % n, i)))
    return kps, edges


def face_visible(spec_rings: dict, cam: Camera) -> dict:
    """{(part, face index): True when the wall faces the camera} (back-face test; ignores occlusion by other parts)."""
    c = cam.centre
    out = {}
    for pid, (ring, zb, zt) in spec_rings.items():
        n = len(ring)
        for i in range(n):
            a, b = np.array(ring[i]), np.array(ring[(i + 1) % n])
            d = b - a
            ln = np.linalg.norm(d)
            if ln < 1e-6:
                out[(pid, i)] = False
                continue
            nrm = np.array([d[1], -d[0]]) / ln               # outward normal of a CCW ring
            out[(pid, i)] = float(nrm @ (c[:2] - (a + b) / 2)) > 0
    return out


def visible(spec: dict, cam: Camera, margin: float = 0.0) -> tuple[dict, list]:
    """Keypoints and edges that face the camera, are in front of it and inside the image (with margin share)."""
    kps, edges = model(spec)
    rings = part_rings(spec)
    fv = face_visible(rings, cam)
    vis_e = [e for e in edges if any(fv.get((e[2], f)) for f in e[3])]
    ids = sorted({k for e in vis_e for k in e[:2]})
    if not ids:
        return {}, []
    xyz = np.array([kps[k] for k in ids])
    uv, z = cam.project(xyz), cam.depth(xyz)
    W, H = cam.width, cam.height
    mx, my = margin * W, margin * H
    ok = {k: (float(u), float(v)) for k, (u, v), d in zip(ids, uv, z)
          if d > 0.1 and -mx <= u <= W + mx and -my <= v <= H + my}
    return ok, [e for e in vis_e if e[0] in ok and e[1] in ok]


def seed_camera(spec: dict, view: dict, width: int, height: int, f: float | None = None,
                part: str | None = None) -> Camera:
    """Camera from a matched viewpoint (azimuth from the building, elevation) that frames the model (or one part):
    the coarse start for the keypoint sketch, not a solution."""
    rings = part_rings(spec)
    sel = {part: rings[part]} if part in rings else rings
    pts = [(x, y, z) for ring, zb, zt in sel.values() for (x, y) in ring for z in (zb, zt if zt is not None else zb)]
    P = np.array(pts)
    lo, hi = P.min(axis=0), P.max(axis=0)
    target = (lo + hi) / 2
    radius = float(np.linalg.norm(hi - lo)) / 2
    f = f or 0.9 * max(width, height)                       # about 58 degrees across the long side
    fov = 2 * math.atan(min(width, height) / 2 / f)
    dist = max(radius / math.tan(fov / 2) * 1.1, radius * 1.5)
    az, el = math.radians(float(view.get("azimuth_deg", 270))), math.radians(float(view.get("elevation_deg", 8)))
    eye = target + dist * np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
    eye[2] = max(eye[2], lo[2] + 1.6)                       # at least eye height
    rvec, tvec = look_at(eye, target)
    return Camera(width, height, f, rvec, tvec)


def _font(size):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


SUN = np.array([0.55, -0.6, 0.58])


def render(spec: dict, cam: Camera):
    """Painter's-order render of the model: (shaded RGB array, face-id array, faces). faces[i] = (part, kind, edge
    index or None); id 0 is background, face i has id i + 1. Walls are shaded by orientation; roofs are flat caps."""
    rings = part_rings(spec)
    polys = []
    for pid, (ring, zb, zt) in rings.items():
        top = zt if zt is not None else zb + 1.0
        n = len(ring)
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            quad = np.array([[a[0], a[1], zb], [b[0], b[1], zb], [b[0], b[1], top], [a[0], a[1], top]])
            d = np.array(b) - np.array(a)
            ln = max(1e-9, float(np.linalg.norm(d)))
            nrm = np.array([d[1] / ln, -d[0] / ln, 0.0])
            polys.append((quad, (pid, "wall", i), 0.7 + 0.3 * max(0.0, float(nrm @ SUN))))
        polys.append((np.array([[x, y, top] for x, y in ring]), (pid, "roof", None), 0.95))
    order = sorted(range(len(polys)), key=lambda i: -float(cam.depth(polys[i][0]).mean()))
    rgb = np.full((cam.height, cam.width, 3), 255, np.uint8)
    ids = np.zeros((cam.height, cam.width), np.int32)
    faces = [p[1] for p in polys]
    for i in order:
        P, (pid, kind, _), shade = polys[i]
        if (cam.depth(P) <= 0.1).any():
            continue
        uv = np.round(cam.project(P)).astype(np.int32)
        base = np.array((200, 170, 120)) if kind == "wall" else np.array((150, 160, 175))
        cv2.fillPoly(rgb, [uv], tuple(int(c) for c in (base * shade).clip(0, 255)))
        cv2.polylines(rgb, [uv], True, (40, 40, 40), max(1, cam.width // 600), cv2.LINE_AA)
        cv2.fillPoly(ids, [uv], i + 1)
    return rgb, ids, faces


def unoccluded(spec: dict, cam: Camera, pts: dict, ids: np.ndarray, faces: list, r: int = 4) -> dict:
    """The keypoints whose neighbourhood in the face-id render shows one of their own part's faces."""
    H, W = ids.shape
    out = {}
    for kid, (u, v) in pts.items():
        part = kid.split("/")[0]
        x0, x1 = int(max(0, u - r)), int(min(W, u + r + 1))
        y0, y1 = int(max(0, v - r)), int(min(H, v + r + 1))
        if x1 <= x0 or y1 <= y0:
            continue
        win = np.unique(ids[y0:y1, x0:x1])
        if any(i > 0 and faces[i - 1][0] == part for i in win):
            out[kid] = (u, v)
    return out


def choose_labels(spec: dict, pts: dict, cam: Camera, max_n: int = 14, min_gap: float = 0.06,
                  prefer_top: bool = False) -> list:
    """Well separated corners worth asking about: tall and large parts first, at least min_gap of the model's
    on-screen size apart, so labels never crowd. prefer_top puts the tops of walls first: they stand against the sky
    and are rarely hidden, while bases are often behind fences, cars, steps or trees."""
    rings = part_rings(spec)
    size = {pid: (zt - zb if zt is not None else 1.0) * sum(math.dist(r[i], r[(i + 1) % len(r)]) for i in range(len(r)))
            for pid, (r, zb, zt) in rings.items()}
    if not pts:
        return []
    P = np.array(list(pts.values()))
    span = float(np.linalg.norm(P.max(axis=0) - P.min(axis=0))) or 1.0
    order = sorted(pts, key=lambda k: ((0 if k.endswith("/t") else 1) if prefer_top else 0,
                                       -size.get(k.split("/")[0], 0), k))
    picked = []
    for k in order:
        if all(math.dist(pts[k], pts[o]) >= min_gap * span for o in picked):
            picked.append(k)
        if len(picked) >= max_n:
            break
    return picked


def sketch(spec: dict, cam: Camera, labels: dict | None = None, max_side: int = 1200,
           prefer_top: bool = False) -> tuple[Image.Image, dict]:
    """Shaded render of the model from cam, cropped to the model and enlarged, with numbered red points on visible,
    well separated wall corners. Returns (image, {label: keypoint id}); the numbers are what the AI refers to."""
    rgb, ids, faces = render(spec, cam)
    pts, _ = visible(spec, cam)
    pts = unoccluded(spec, cam, pts, ids, faces)
    labels = labels if labels is not None else {str(k + 1): kid for k, kid in
                                                enumerate(choose_labels(spec, pts, cam, prefer_top=prefer_top))}
    ys, xs = np.nonzero(ids)
    if len(xs):
        pad = 0.08 * max(xs.max() - xs.min(), ys.max() - ys.min())
        x0, x1 = int(max(0, xs.min() - pad)), int(min(cam.width, xs.max() + pad))
        y0, y1 = int(max(0, ys.min() - pad)), int(min(cam.height, ys.max() + pad))
    else:
        x0, y0, x1, y1 = 0, 0, cam.width, cam.height
    s = max_side / max(x1 - x0, y1 - y0)
    im = Image.fromarray(rgb[y0:y1, x0:x1]).resize((max(1, int((x1 - x0) * s)), max(1, int((y1 - y0) * s))),
                                                   Image.LANCZOS)
    d = ImageDraw.Draw(im)
    f = _font(26)
    for lab, kid in labels.items():
        if kid not in pts:
            continue
        x, y = (pts[kid][0] - x0) * s, (pts[kid][1] - y0) * s
        d.ellipse((x - 7, y - 7, x + 7, y + 7), fill=(230, 20, 20), outline="white", width=2)
        ty = y - 34 if kid.endswith("/t") else y + 8
        d.text((x + 8, ty), lab, fill=(200, 0, 0), font=f, stroke_width=3, stroke_fill="white")
    return im, labels


def choose(pts: dict, cam: Camera, max_n: int = 24) -> list:
    """Keypoints worth asking about: spread over the image, top corners and base corners alike."""
    ids = list(pts)
    if len(ids) <= max_n:
        return ids
    P = np.array([pts[k] for k in ids])
    picked = [int(np.argmin(P[:, 1]))]
    dmin = np.linalg.norm(P - P[picked[0]], axis=1)
    while len(picked) < max_n:                               # farthest-point sampling
        j = int(np.argmax(dmin))
        picked.append(j)
        dmin = np.minimum(dmin, np.linalg.norm(P - P[j], axis=1))
    return [ids[j] for j in picked]


def overlay(photo: Image.Image, spec: dict, cam: Camera, points: list | None = None) -> Image.Image:
    """The photo with the model's visible edges (cyan) and, optionally, keypoints: [(uv, colour)]."""
    im = photo.convert("RGB").copy()
    d = ImageDraw.Draw(im)
    pts, edges = visible(spec, cam, margin=0.5)
    w = max(2, int(cam.diag / 800))
    for a, b, _, _ in edges:
        d.line([pts[a], pts[b]], fill=(0, 255, 255), width=w)
    for (u, v), col in points or []:
        r = 2 * w + 2
        d.ellipse((u - r, v - r, u + r, v + r), outline=col, width=w)
    return im
