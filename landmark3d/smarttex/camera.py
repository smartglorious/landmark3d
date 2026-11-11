"""T2: solve a photo's camera against the model.

Inputs: an undistorted photo (T1), the spec, and coarse 2D positions for some named keypoints (from the AI or the
owner). Steps:
1. focal candidates: EXIF prior, vanishing-point estimate and a field-of-view sweep;
2. robust PnP (RANSAC) per candidate, then Levenberg-Marquardt over pose and focal;
3. snapping: around each keypoint's predicted position, the image line segments whose directions agree with the
   model edges meeting there are intersected; the solve repeats with a shrinking search radius;
4. edge refinement: the pose and focal are adjusted to bring the projected model edges onto the photo's edges
   (truncated chamfer distance), anchored by the keypoints;
5. residuals: RMS reprojection error, inlier share, chamfer distance, per-part residuals, and geometry_mismatch
   for a part that stays off while the rest fits.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from . import lens
from .keypoints import Camera, model, part_rings, visible

FOV_SWEEP = range(30, 101, 5)          # horizontal field of view over the long side, degrees
RANSAC_THRESHOLDS = (0.05, 0.02)       # inlier thresholds for the coarse (AI) points, share of the diagonal
SNAP_RADII = (0.04, 0.02, 0.01)        # search radius per snapping round, share of the diagonal
SNAP_ANGLE_DEG = 7.0
CHAMFER_TRUNC = 0.02                   # chamfer distance is truncated here, share of the diagonal
MISMATCH_MIN_COARSE = 0.04            # a part is a mismatch when its given points are off by more than this share
MISMATCH_FACTOR = 2.5                  # ... and this multiple of the median of all given points (AI points are coarse)
GATE = {"rms_max": 0.005, "chamfer_max": 0.01, "inliers_min": 6, "support_min": 0.35, "agreement_max": 0.03,
        "silhouette_min": 0.7}


# ----------------------------------------------------------------------------------------------- focal priors
def focal_from_fov(width: int, height: int, fov_deg: float) -> float:
    return max(width, height) / 2.0 / math.tan(math.radians(fov_deg) / 2)


def vanishing_points(segs: np.ndarray, size, iters: int = 400, seed: int = 0) -> list:
    """Up to three vanishing points (homogeneous, normalised) by sequential RANSAC on segment lines."""
    rng = np.random.default_rng(seed)
    if len(segs) < 6:
        return []
    p1 = np.c_[segs[:, :2], np.ones(len(segs))]
    p2 = np.c_[segs[:, 2:], np.ones(len(segs))]
    lines = np.cross(p1, p2)
    lines /= np.linalg.norm(lines[:, :2], axis=1, keepdims=True) + 1e-12
    mids = (segs[:, :2] + segs[:, 2:]) / 2
    dirs = segs[:, 2:] - segs[:, :2]
    lens_ = np.linalg.norm(dirs, axis=1)
    dirs = dirs / (lens_[:, None] + 1e-12)
    left = np.ones(len(segs), bool)
    vps = []
    for _ in range(3):
        idx = np.nonzero(left)[0]
        if len(idx) < 6:
            break
        best, best_score, best_in = None, 0.0, None
        for _ in range(iters):
            i, j = rng.choice(idx, 2, replace=False)
            v = np.cross(lines[i], lines[j])
            if np.linalg.norm(v) < 1e-12:
                continue
            # angle between each segment and the direction from its midpoint to v
            to_v = v[:2] - mids * v[2] if abs(v[2]) > 1e-12 else np.tile(v[:2], (len(mids), 1))
            to_v = to_v / (np.linalg.norm(to_v, axis=1, keepdims=True) + 1e-12)
            cosang = np.abs((to_v * dirs).sum(axis=1))
            inl = left & (cosang > math.cos(math.radians(1.5)))
            score = float(lens_[inl].sum())
            if score > best_score:
                best, best_score, best_in = v, score, inl
        if best is None or best_in.sum() < 5:
            break
        vps.append(best / (np.linalg.norm(best) + 1e-12))
        left &= ~best_in
    return vps


def focal_from_vps(vps: list, size) -> float | None:
    """Focal length from two finite, roughly orthogonal vanishing points: f^2 = -(v1 - c).(v2 - c)."""
    W, H = size
    c = np.array([W / 2.0, H / 2.0])
    fin = [v[:2] / v[2] for v in vps if abs(v[2]) > 1e-9]
    best = None
    for i in range(len(fin)):
        for j in range(i + 1, len(fin)):
            f2 = -float((fin[i] - c) @ (fin[j] - c))
            if f2 > 0:
                f = math.sqrt(f2)
                if 0.3 * max(W, H) <= f <= 5 * max(W, H):
                    best = f if best is None else max(best, f)
    return best


# ----------------------------------------------------------------------------------------------- rotation from VPs
VERT_TOL_DEG = 20.0                    # image segments within this of vertical vote for the vertical VP
HORIZ_TOL_DEG = 50.0                   # ... and within this of horizontal for the horizontal VPs
VIEW_TOL_DEG = 70.0                    # a rotation must look at the building from roughly the matched direction


def _seg_angles(segs: np.ndarray) -> np.ndarray:
    d = segs[:, 2:] - segs[:, :2]
    return np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 180.0


def scene_vps(segs: np.ndarray, size) -> dict:
    """{"vertical": VP or None, "horizontal": [VPs]} (homogeneous image points): the vertical VP from near-vertical
    segments, up to two horizontal VPs from the rest."""
    if not len(segs):
        return {"vertical": None, "horizontal": []}
    ang = _seg_angles(segs)
    vert = np.abs(ang - 90.0) <= VERT_TOL_DEG
    horiz = np.minimum(ang, 180.0 - ang) <= HORIZ_TOL_DEG
    v = vanishing_points(segs[vert], size)
    h = vanishing_points(segs[horiz & ~vert], size)
    return {"vertical": v[0] if v else None, "horizontal": h[:2]}


def model_directions(spec: dict) -> list:
    """Dominant horizontal wall directions of the model (degrees, mod 90), longest first; each stands for itself
    and its perpendicular."""
    hist = {}
    for ring, zb, zt in part_rings(spec).values():
        h = (zt - zb) if zt is not None else 1.0
        for i in range(len(ring)):
            a, b = ring[i], ring[(i + 1) % len(ring)]
            ln = math.dist(a, b)
            if ln < 1e-6:
                continue
            d = round(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 90.0) % 90
            hist[d] = hist.get(d, 0.0) + ln * h
    fam = []
    for d in sorted(hist, key=lambda k: -hist[k]):
        if all(min(abs(d - o) % 90, 90 - abs(d - o) % 90) > 5 for o in fam):
            fam.append(float(d))
        if len(fam) >= 2:
            break
    return fam


def rotations_from_vps(vps: dict, f: float, size, directions: list, view: dict | None = None) -> list:
    """Candidate rotation vectors (world -> camera) aligning world up with the vertical VP and a model wall direction
    with a horizontal VP. Sign and direction ambiguities are enumerated; candidates that put the image upside down
    or look at the building from the wrong side of the matched view are dropped."""
    if vps.get("vertical") is None or not vps.get("horizontal") or not directions:
        return []
    W, H = size
    Kinv = np.linalg.inv(np.array([[f, 0, W / 2.0], [0, f, H / 2.0], [0, 0, 1.0]]))

    def cdir(v):
        c = Kinv @ np.asarray(v, float)
        return c / np.linalg.norm(c)
    cz = cdir(vps["vertical"])
    if cz[1] > 0:                          # world up must point up in the image (camera y is down)
        cz = -cz
    out = []
    zw = np.array([0.0, 0.0, 1.0])
    for hv in vps["horizontal"]:
        ch0 = cdir(hv)
        ch0 = ch0 - (ch0 @ cz) * cz
        if np.linalg.norm(ch0) < 1e-6:
            continue
        ch0 /= np.linalg.norm(ch0)
        for d in directions:
            for extra in (0.0, 90.0):
                a = math.radians(d + extra)
                hw = np.array([math.cos(a), math.sin(a), 0.0])
                gw = np.cross(zw, hw)
                for s in (1.0, -1.0):
                    ch = s * ch0
                    cg = np.cross(cz, ch)
                    R = np.stack([ch, cg, cz], axis=1) @ np.stack([hw, gw, zw], axis=1).T
                    if np.linalg.det(R) < 0:
                        continue
                    look = R.T @ np.array([0.0, 0.0, 1.0])            # viewing direction in the world
                    if view is not None and "azimuth_deg" in view:
                        az = math.radians(float(view["azimuth_deg"]))
                        want = -np.array([math.cos(az), math.sin(az)])
                        lh = look[:2] / max(np.linalg.norm(look[:2]), 1e-9)
                        if float(lh @ want) < math.cos(math.radians(VIEW_TOL_DEG)):
                            continue
                    rv = cv2.Rodrigues(R)[0].ravel()
                    if all(np.linalg.norm(rv - o) > 1e-3 for o in out):
                        out.append(rv)
    return out


def translation_for(R: np.ndarray, f: float, size, X, uv) -> np.ndarray | None:
    """Least-squares camera translation with rotation and focal fixed (2 equations per point, 2+ points)."""
    W, H = size
    cx, cy = W / 2.0, H / 2.0
    X, uv = np.asarray(X, float), np.asarray(uv, float)
    if len(X) < 2:
        return None
    K = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1.0]])
    A, b = [], []
    for Xi, (u, v) in zip(X, uv):
        a = K @ (R @ Xi)
        A.append([-f, 0.0, u - cx]); b.append(a[0] - u * a[2])
        A.append([0.0, -f, v - cy]); b.append(a[1] - v * a[2])
    t, *_ = np.linalg.lstsq(np.array(A), np.array(b), rcond=None)
    return t


def vp_starts(X, uv, size, f: float, rvecs: list, threshold: float) -> list:
    """[(key, camera, inlier mask)] from VP rotations: translation by RANSAC over point pairs, then LM with the
    focal fixed. Works from 2 points, where PnP needs 4."""
    W, H = size
    X, uv = np.asarray(X, float), np.asarray(uv, float)
    n = len(X)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)] or [(0, 0)]
    found = []
    for rv in rvecs:
        R = cv2.Rodrigues(np.asarray(rv, float))[0]
        best = None
        for i, j in pairs:
            idx = [i, j] if i != j else [i]
            t = translation_for(R, f, size, X[idx], uv[idx])
            if t is None:
                continue
            cam = Camera(W, H, f, rv, t)
            if (cam.depth(X) <= 0).any():
                continue
            err = np.linalg.norm(cam.project(X) - uv, axis=1)
            mask = err <= threshold
            key = (int(mask.sum()), -float(np.median(err)))
            if best is None or key > best[0]:
                best = (key, mask)
        if best is None or best[1].sum() < 2:
            continue
        mask = best[1]
        t = translation_for(R, f, size, X[mask], uv[mask])
        cam = Camera(W, H, f, rv, t)
        if mask.sum() >= 3:
            cam = lm_refine(cam, X[mask], uv[mask], iters=15, fix_f=True)
        err = np.linalg.norm(cam.project(X) - uv, axis=1)
        mask = (err <= threshold) & (cam.depth(X) > 0)
        if mask.sum() >= 2:
            found.append(((int(mask.sum()), -float(np.sqrt((err[mask] ** 2).mean()))), cam, mask))
    found.sort(key=lambda t: t[0], reverse=True)
    return found


# ----------------------------------------------------------------------------------------------- solve
def _residuals(cam: Camera, X, uv, w):
    proj = cam.project(X)
    return ((proj - uv) * w[:, None]).ravel(), proj


def lm_refine(cam: Camera, X, uv, w=None, iters: int = 30, f_prior: float | None = None, prior_w: float = 0.0,
              extra=None, fix_f: bool = False) -> Camera:
    """Levenberg-Marquardt on (rvec, tvec, f) with Huber weights. extra(cam) -> (residuals, jacobian) adds terms.
    fix_f keeps the focal length as given."""
    X, uv = np.asarray(X, float), np.asarray(uv, float)
    w = np.ones(len(X)) if w is None else np.asarray(w, float)
    th = np.r_[np.ravel(cam.rvec), np.ravel(cam.tvec), math.log(cam.f)]
    lam = 1e-3
    huber = 0.01 * cam.diag

    def build(t):
        c = Camera(cam.width, cam.height, math.exp(t[6]), t[:3], t[3:6])
        proj, J = cv2.projectPoints(X, c.rvec, c.tvec, c.K, None)
        proj = proj.reshape(-1, 2)
        e = proj - uv
        n = np.linalg.norm(e, axis=1)
        hw = np.where(n <= huber, 1.0, np.sqrt(huber / np.maximum(n, 1e-9)))
        ww = np.repeat(w * hw, 2)
        Jp = np.c_[J[:, :6], (J[:, 6] + J[:, 7]) * c.f]           # d/d(log f) = f (d/dfx + d/dfy)
        r = e.ravel() * ww
        Jr = Jp * ww[:, None]
        if f_prior and prior_w > 0:
            r = np.r_[r, prior_w * (t[6] - math.log(f_prior))]
            Jr = np.r_[Jr, prior_w * np.eye(7)[6:7]]
        if extra is not None:
            er, eJ = extra(c)
            r, Jr = np.r_[r, er], np.r_[Jr, eJ]
        if fix_f:
            Jr = Jr.copy()
            Jr[:, 6] = 0.0
        return r, Jr

    r, J = build(th)
    cost = float(r @ r)
    for _ in range(iters):
        A = J.T @ J
        g = J.T @ r
        step = np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-9), -g)
        r2, J2 = build(th + step)
        c2 = float(r2 @ r2)
        if c2 < cost:
            th, r, J, cost = th + step, r2, J2, c2
            lam = max(lam / 3, 1e-7)
            if np.linalg.norm(step) < 1e-7:
                break
        else:
            lam *= 4
            if lam > 1e8:
                break
    return Camera(cam.width, cam.height, math.exp(th[6]), th[:3], th[3:6])


def pnp(X, uv, width: int, height: int, f_candidates, threshold: float):
    """Best (camera, inlier mask) over focal candidates by robust PnP, then LM with the focal free."""
    c = pnp_all(X, uv, width, height, f_candidates, threshold)
    return (c[0][1], c[0][2]) if c else (None, None)


def pnp_all(X, uv, width: int, height: int, f_candidates, threshold: float, fix_f: bool = False) -> list:
    """[(key, camera, inlier mask)] for every focal candidate that worked, best first (most inliers, lowest RMS).
    fix_f keeps each candidate's focal length (used to sweep the focal / distance trade-off)."""
    X, uv = np.asarray(X, float), np.asarray(uv, float)
    found = []
    for f in f_candidates:
        cam0 = Camera(width, height, f)
        try:
            ok, rvec, tvec, inl = cv2.solvePnPRansac(X, uv, cam0.K, None, reprojectionError=threshold,
                                                     iterationsCount=300, confidence=0.995, flags=cv2.SOLVEPNP_SQPNP)
        except cv2.error:
            continue
        if not ok or inl is None or len(inl) < 4:
            continue
        mask = np.zeros(len(X), bool)
        mask[inl.ravel()] = True
        cam = lm_refine(Camera(width, height, f, rvec.ravel(), tvec.ravel()), X[mask], uv[mask], iters=15, fix_f=fix_f)
        err = np.linalg.norm(cam.project(X) - uv, axis=1)
        mask = (err <= threshold) & (cam.depth(X) > 0)
        if mask.sum() < 4:
            continue
        rms = float(np.sqrt((err[mask] ** 2).mean()))
        found.append(((int(mask.sum()), -rms), cam, mask))
    found.sort(key=lambda t: t[0], reverse=True)
    return found


# ----------------------------------------------------------------------------------------------- snapping
def _line(seg):
    p, q = np.r_[seg[:2], 1.0], np.r_[seg[2:], 1.0]
    l = np.cross(p, q)
    return l / (np.linalg.norm(l[:2]) + 1e-12)


def _lines(segs: np.ndarray) -> np.ndarray:
    """Homogeneous lines (a, b, c) with a^2 + b^2 = 1 through each segment."""
    p = np.c_[segs[:, :2], np.ones(len(segs))]
    q = np.c_[segs[:, 2:], np.ones(len(segs))]
    l = np.cross(p, q)
    return l / (np.linalg.norm(l[:, :2], axis=1, keepdims=True) + 1e-12)


def _point_seg_dist(p, seg):
    a, b = seg[:2], seg[2:]
    ab = b - a
    t = float(np.clip((p - a) @ ab / max(ab @ ab, 1e-12), 0, 1))
    return float(np.linalg.norm(p - (a + t * ab)))


def snap_point(segs: np.ndarray, centre, directions: list, radius: float):
    """Intersect the best image line for each model edge direction near centre. Returns (uv, n_lines) or
    (None, 0). A line qualifies when its segment passes within radius of centre and its direction is within
    SNAP_ANGLE_DEG of the edge direction; among those, the one whose line passes closest to centre wins
    (ties: longer)."""
    centre = np.asarray(centre, float)
    if not len(segs):
        return None, 0
    d = segs[:, 2:] - segs[:, :2]
    ln = np.linalg.norm(d, axis=1)
    du = d / (ln[:, None] + 1e-12)
    a = segs[:, :2]
    t = np.clip(((centre - a) * d).sum(axis=1) / np.maximum((d * d).sum(axis=1), 1e-12), 0, 1)
    near = np.linalg.norm(centre - (a + t[:, None] * d), axis=1) <= radius      # point-to-segment distance
    mids = (segs[:, :2] + segs[:, 2:]) / 2
    chosen = []
    for e in directions:
        e = np.asarray(e, float)
        e = e / (np.linalg.norm(e) + 1e-12)
        ok = near & (np.abs(du @ e) >= math.cos(math.radians(SNAP_ANGLE_DEG)))
        # the edge leaves the corner in direction e: its segment lies on that side of the corner, not behind it
        ok &= (mids - centre) @ e > -0.25 * radius
        if not ok.any():
            continue
        cand = np.nonzero(ok)[0]
        lines = _lines(segs[cand])
        perp = np.abs(lines @ np.r_[centre, 1.0])
        # building edges are long and pass close to the corner; window and moulding edges are short
        score = ln[cand] / (1.0 + perp / (0.25 * radius))
        k = int(np.argmax(score))
        chosen.append(lines[k])
    if len(chosen) >= 2:
        # least-squares intersection of all chosen lines
        A = np.array([l[:2] for l in chosen])
        b = -np.array([l[2] for l in chosen])
        if abs(np.linalg.det(A.T @ A)) < 1e-6:
            return None, 0
        p = np.linalg.solve(A.T @ A, A.T @ b)
        if np.linalg.norm(p - centre) <= radius:
            return p, len(chosen)
        return None, 0
    if len(chosen) == 1:
        l = chosen[0]
        p = centre - (l @ np.r_[centre, 1.0]) * l[:2]          # foot of the perpendicular
        return p, 1
    return None, 0


def edge_directions(cam: Camera, kps: dict, edges: list, kid: str) -> list:
    """Image directions of the model edges meeting at keypoint kid (from a point 0.5 m along each edge)."""
    out = []
    P = np.array(kps[kid])
    for a, b, _, _ in edges:
        if kid not in (a, b):
            continue
        o = np.array(kps[b if a == kid else a])
        step = P + 0.5 * (o - P) / max(np.linalg.norm(o - P), 1e-9)
        uv = cam.project(np.stack([P, step]))
        v = uv[1] - uv[0]
        if np.linalg.norm(v) > 1e-6:
            out.append(v)
    return out


# ----------------------------------------------------------------------------------------------- edge refinement
def _edge_samples(spec, cam: Camera, step_m: float = 0.5):
    kps, _ = model(spec)
    pts, edges = visible(spec, cam, margin=0.05)
    S = []
    for a, b, _, _ in edges:
        A, B = np.array(kps[a]), np.array(kps[b])
        n = max(2, int(np.linalg.norm(B - A) / step_m))
        t = np.linspace(0.08, 0.92, n)[:, None]                 # ends excluded: corners are handled by keypoints
        S.append(A + t * (B - A))
    return np.concatenate(S) if S else np.zeros((0, 3))


def chamfer(cam: Camera, samples: np.ndarray, dt: np.ndarray) -> float | None:
    if not len(samples):
        return None
    uv = cam.project(samples)
    H, W = dt.shape
    ok = (uv[:, 0] >= 0) & (uv[:, 0] < W - 1) & (uv[:, 1] >= 0) & (uv[:, 1] < H - 1) & (cam.depth(samples) > 0)
    if not ok.any():
        return None
    tau = CHAMFER_TRUNC * cam.diag
    vals = np.minimum(dt[uv[ok, 1].astype(int), uv[ok, 0].astype(int)], tau)
    return float(vals.mean()) / cam.diag


def _distance_map(grey: np.ndarray) -> np.ndarray:
    """Distance (px) from every pixel to the nearest image edge (Canny, thresholds from the median)."""
    med = float(np.median(grey))
    edges_img = cv2.Canny(cv2.GaussianBlur(grey, (3, 3), 0), max(10, 0.66 * med), min(255, 1.33 * med + 20))
    return cv2.distanceTransform(255 - edges_img, cv2.DIST_L2, 5).astype(np.float32)


def edge_refine(cam: Camera, spec, grey: np.ndarray, X, uv, w, iters: int = 4) -> tuple[Camera, float | None, float | None]:
    """Adjust pose and focal so projected model edges meet image edges; keypoints anchor the solution.
    Returns (camera, chamfer before, chamfer after); the refinement is kept only if the chamfer drops and the
    keypoint RMS does not grow by more than half."""
    dt = _distance_map(grey)
    gx =cv2.Sobel(dt, cv2.CV_32F, 1, 0, ksize=3) / 8
    gy = cv2.Sobel(dt, cv2.CV_32F, 0, 1, ksize=3) / 8
    samples = _edge_samples(spec, cam)
    before = chamfer(cam, samples, dt)
    if before is None or len(samples) < 10:
        return cam, before, before
    tau = CHAMFER_TRUNC * cam.diag
    H, W = dt.shape
    wt = 0.5 * math.sqrt(max(1, len(X)) / len(samples))     # edge terms in total weigh about half the keypoints

    def extra(c):
        proj, J = cv2.projectPoints(samples, c.rvec, c.tvec, c.K, None)
        proj = proj.reshape(-1, 2)
        xi = np.clip(proj[:, 0], 0, W - 1).astype(int)
        yi = np.clip(proj[:, 1], 0, H - 1).astype(int)
        inside = (proj[:, 0] >= 0) & (proj[:, 0] < W - 1) & (proj[:, 1] >= 0) & (proj[:, 1] < H - 1)
        d = dt[yi, xi]
        act = inside & (d < tau)
        r = np.where(act, d, 0.0) * wt
        Jp = np.c_[J[:, :6], (J[:, 6] + J[:, 7]) * c.f].reshape(-1, 2, 7)
        g = np.stack([gx[yi, xi], gy[yi, xi]], axis=1) * act[:, None]
        return r, wt * np.einsum("nk,nkp->np", g, Jp)

    rms0 = float(np.sqrt(((cam.project(X) - uv) ** 2).sum(axis=1).mean()))
    new = cam
    for _ in range(iters):
        new = lm_refine(new, X, uv, w, iters=10, extra=extra)
    after = chamfer(new, samples, dt)
    rms1 = float(np.sqrt(((new.project(X) - uv) ** 2).sum(axis=1).mean()))
    f_ok = 0.9 * focal_from_fov(cam.width, cam.height, max(FOV_SWEEP)) <= new.f <= \
        1.1 * focal_from_fov(cam.width, cam.height, min(FOV_SWEEP))
    if after is not None and after < before and rms1 <= 1.5 * rms0 + 0.002 * cam.diag and f_ok:
        return new, before, after
    return cam, before, before


# ----------------------------------------------------------------------------------------------- full solve
TRIM_FACTOR = 3.0                      # a point is dropped when its error exceeds this multiple of the median ...
TRIM_FLOOR = 0.004                     # ... and this share of the diagonal


def _trim(cam: Camera, X, uv, diag: float) -> np.ndarray:
    err = np.linalg.norm(cam.project(X) - uv, axis=1)
    return err <= max(TRIM_FACTOR * float(np.median(err)), TRIM_FLOOR * diag)


def solve(spec: dict, grey: np.ndarray, coarse: dict, f_prior: float | None = None, view: dict | None = None,
          use_vp: bool = True, use_grid: bool = True, grey_rgb: np.ndarray | None = None,
          use_search: bool = True) -> dict:
    """Camera for an undistorted greyscale photo from coarse keypoint positions {id: (u, v)} (pixels).
    Starts come from PnP (4+ points) and from vanishing-point rotations (2+ points; view = the matched viewpoint,
    used to discard rotations seen from the wrong side).
    Returns {"camera": Camera | None, "status", "keypoints": [...], "rms_px", "rms", "inlier_share", "chamfer",
    "chamfer_before", "parts": {part: rms share}, "mismatch": [part], "f_sources": {...}, "start": "pnp" | "vp"}."""
    H, W = grey.shape
    diag = math.hypot(W, H)
    kps, edges_all = model(spec)
    ids = [k for k in coarse if k in kps]
    out = {"camera": None, "status": "failed", "keypoints": [], "f_sources": {}}
    min_pts = 2 if use_vp else 4
    if len(ids) < min_pts:
        out["why"] = f"{len(ids)} usable keypoints, at least {min_pts} are needed"
        return out
    segs = lens.segments(grey, 0.008 * diag)
    vps = scene_vps(segs, (W, H)) if use_vp else {"vertical": None, "horizontal": []}
    vp_list = ([vps["vertical"]] if vps["vertical"] is not None else []) + list(vps["horizontal"])
    vp_f = focal_from_vps(vp_list, (W, H)) if use_vp else focal_from_vps(vanishing_points(segs, (W, H)), (W, H))
    dirs = model_directions(spec)
    out["f_sources"] = {"exif": f_prior, "vanishing_points": vp_f}
    out["vps"] = {"vertical": vps["vertical"] is not None, "horizontal": len(vps["horizontal"])}
    cands = [f for f in (f_prior, vp_f) if f] + [focal_from_fov(W, H, a) for a in FOV_SWEEP]
    X = np.array([kps[k] for k in ids])
    uv = np.array([coarse[k] for k in ids], float)
    prior = f_prior or vp_f
    # Focal length and distance trade off when the points are few and noisy, and a free focal drifts with wrong
    # snaps. So the snapping rounds run once per focal candidate with the focal held fixed; the camera whose
    # projection makes the most model corners land on image corners wins (an independent check of the whole model
    # against the photo). The focal is freed only at the end.
    dt = _distance_map(grey)
    sky = sky_of(grey_rgb if grey_rgb is not None else grey)

    def rate(c):
        """(selection score, corner support): corners, edge distance and, when the photo shows sky, the outline."""
        sup = _support(spec, c, kps, segs, diag)
        ch = chamfer(c, _edge_samples(spec, c), dt)
        s = sup - CHAMFER_WEIGHT * (ch if ch is not None else CHAMFER_TRUNC)
        sil = silhouette(spec, c, sky)
        if sil is not None:
            s += SIL_WEIGHT * sil["score"]
        return s, sup
    tried = []
    # two inlier thresholds: the loose one tolerates coarse AI points, the tight one keeps a wrongly modelled part
    # (whose points are off by a few per cent) from bending the camera
    for f, thr in ((f, t) for f in cands for t in RANSAC_THRESHOLDS):
        starts = []
        if len(ids) >= 4:
            found = pnp_all(X, uv, W, H, [f], thr * diag, fix_f=True)
            if found:
                starts.append(("pnp", found[0]))
        rvecs = rotations_from_vps(vps, f, (W, H), dirs, view) if use_vp else []
        for item in vp_starts(X, uv, (W, H), f, rvecs, thr * diag)[:2]:
            starts.append(("vp", item))
        for kind, (_, cam0, mask0) in starts:
            cam1, obs1 = _snap_rounds(spec, cam0,
                                      {k: (tuple(coarse[k]), "coarse", 0.5) for k, m in zip(ids, mask0) if m},
                                      kps, segs, None, diag, fix_f=True)
            tried.append((*rate(cam1), cam1, obs1, mask0, kind))
    if not tried:
        out["why"] = "no camera explains the given keypoints"
        return out
    tried.sort(key=lambda t: t[0], reverse=True)
    if use_search:
        # With the rotation fixed, search position and scale around the best starts by how well the model's outline
        # and corners agree with the photo: coarse AI points only need to put the camera in the right area.
        extra = []
        for _, _, c0, ob, mk, kind in _distinct_f(tried, SEARCH_STARTS):
            c1 = translation_search(spec, c0, segs, sky, kps)
            c1, ob1 = _snap_rounds(spec, c1, {k: v for k, v in ob.items() if v[1] == "coarse"} or ob, kps, segs,
                                   None, diag, fix_f=True, radii=SNAP_RADII[1:])
            extra.append((*rate(c1), c1, ob1, mk, kind + "+search"))
        tried = sorted(tried + extra, key=lambda t: t[0], reverse=True)
    if use_grid:
        # facade-grid lines (floors, bays, face ends) as extra correspondences for the best few starts; the refined
        # and unrefined versions compete under the same score, so the grid is used only where it helps
        from . import grid
        extra = []
        for score, sup, c0, ob, mk, kind in tried[:GRID_STARTS]:
            k_ids = list(ob)
            Xg = np.array([kps[k] for k in k_ids])
            Ug = np.array([ob[k][0] for k in k_ids])
            Wg = np.array([ob[k][2] for k in k_ids])
            c1, n_lines = grid.refine(spec, c0, segs, Xg, Ug, Wg, lm_refine)
            if n_lines < 3:
                continue
            c1, ob1 = _snap_rounds(spec, c1, ob, kps, segs, None, diag, fix_f=True, radii=SNAP_RADII[-1:])
            extra.append((*rate(c1), c1, ob1, mk, kind + "+grid"))
        tried = sorted(tried + extra, key=lambda t: t[0], reverse=True)
    out["_tried"] = [(t[0], t[1], t[2], t[5]) for t in tried]          # diagnostics (not written to JSON)
    _, support, cam, obs, mask, kind = tried[0]
    out["start"] = kind
    free, obs_free = _snap_rounds(spec, cam, obs, kps, segs, prior, diag, radii=SNAP_RADII[-1:])
    s_free = _support(spec, free, kps, segs, diag)
    f_lo, f_hi = focal_from_fov(W, H, max(FOV_SWEEP)), focal_from_fov(W, H, min(FOV_SWEEP))
    if s_free >= support - 0.02 and 0.9 * f_lo <= free.f <= 1.1 * f_hi:
        cam, obs, support = free, obs_free, s_free
    out["support"] = round(support, 3)
    k_ids = list(obs)
    Xs = np.array([kps[k] for k in k_ids])
    Us = np.array([obs[k][0] for k in k_ids])
    Ws = np.array([obs[k][2] for k in k_ids])
    cam, ch0, ch1 = edge_refine(cam, spec, grey, Xs, Us, Ws)
    out["silhouette"] = silhouette(spec, cam, sky)
    return _finish(out, spec, cam, obs, kps, coarse, ids, mask, ch0, ch1, diag)


STARTS = 4                             # camera starts tried by the snapping rounds
GRID_STARTS = 3                        # best starts refined with facade-grid lines
CHAMFER_WEIGHT = 25.0                  # selection score = corner support - weight x edge distance (diagonal share)
SUPPORT_RADIUS = 0.006                 # a corner supports a camera when it snaps within this share of the diagonal


def _distinct(cands: list) -> list:
    """Up to STARTS candidates whose focal lengths differ by at least 8 % (near-duplicates add nothing)."""
    out = []
    for c in cands:
        if all(abs(math.log(c[1].f / o[1].f)) > 0.08 for o in out):
            out.append(c)
        if len(out) >= STARTS:
            break
    return out


SIL_TOL = 0.008                        # sky boundary within this share of the diagonal of the model outline counts
SIL_WEIGHT = 1.0                       # weight of the silhouette score in the camera choice


SEARCH_STARTS = 4                      # best starts (distinct focal lengths) refined by the position search
SEARCH_SHIFT = 0.08                    # search half-range of the image shift, share of the diagonal
SEARCH_SCALES = (0.8, 1.25)            # search range of the image scale
SEARCH_PX = 480                        # long side of the low-resolution renders used by the search


def _distinct_f(tried: list, n: int) -> list:
    out = []
    for t in tried:
        if all(abs(math.log(t[2].f / o[2].f)) > 0.05 for o in out):
            out.append(t)
        if len(out) >= n:
            break
    return out


def _moved(cam: Camera, dx: float, dy: float, s: float, depth: float) -> Camera:
    """The camera whose image of the model is shifted by (dx, dy) px and scaled by s about the image centre,
    rotation and focal unchanged (exact for points at the given depth, close for the rest)."""
    t = np.asarray(cam.tvec, float).copy()
    z_new = depth / s
    t[2] += z_new - depth
    t[0] += dx * z_new / cam.f
    t[1] += dy * z_new / cam.f
    return Camera(cam.width, cam.height, cam.f, np.asarray(cam.rvec, float), t)


def translation_search(spec, cam: Camera, segs, sky, kps) -> Camera:
    """Position and scale search with rotation and focal fixed: maximises silhouette agreement with the sky plus
    corner support, coarse grid then fine grid. Returns the best camera (the input when nothing is better)."""
    from .keypoints import render
    sc = SEARCH_PX / max(cam.width, cam.height)
    small_sky = cv2.resize(sky.astype(np.uint8), (max(1, round(cam.width * sc)), max(1, round(cam.height * sc))),
                           interpolation=cv2.INTER_NEAREST).astype(bool) if sky is not None else None
    pts = np.array(list(kps.values()))
    depth = float(np.median(cam.depth(pts)))
    if depth <= 0:
        return cam
    diag = cam.diag
    have_sky = small_sky is not None and small_sky.mean() > 0.01

    def score(c):
        s = _support(spec, c, kps, segs, diag)
        if have_sky:
            small = Camera(max(1, round(c.width * sc)), max(1, round(c.height * sc)), c.f * sc, c.rvec, c.tvec)
            sil = silhouette(spec, small, small_sky)
            if sil is not None:
                s += SIL_WEIGHT * sil["score"]
        return s

    best_c, best_s = cam, score(cam)
    centre = (0.0, 0.0, 1.0)
    for half, step, scales in ((SEARCH_SHIFT, 0.016, np.geomspace(*SEARCH_SCALES, 7)),
                               (0.016, 0.004, np.geomspace(0.95, 1.05, 5))):
        cx, cy, cs = centre
        offs = np.arange(-half, half + 1e-9, step) * diag
        for s in scales * cs:
            for dx in offs + cx:
                for dy in offs + cy:
                    c = _moved(cam, dx, dy, s, depth)
                    if (c.depth(pts) <= 0.1).any():
                        continue
                    v = score(c)
                    if v > best_s:
                        best_s, best_c, centre = v, c, (dx, dy, s)
    return best_c


def sky_of(grey_or_rgb) -> np.ndarray:
    from .metrics import sky_mask
    a = np.asarray(grey_or_rgb)
    if a.ndim == 2:
        a = np.repeat(a[..., None], 3, axis=2)
    return sky_mask(a, max_depth=1.0)


def silhouette(spec, cam: Camera, sky: np.ndarray) -> dict | None:
    """Agreement of the model outline with the sky: "on_sky" = share of model pixels that are sky (should be ~0),
    "edge" = share of the sky boundary above the model (within its columns) that lies within SIL_TOL of the
    model's outline. Unlike corners and facade lines this does not repeat along a facade, so a camera shifted by a
    bay or a floor scores lower. None when the photo shows too little sky to tell."""
    from .keypoints import render
    if sky.mean() < 0.01:
        return None
    _, ids, _ = render(spec, cam)
    model = ids > 0
    if model.sum() < 100:
        return {"on_sky": 1.0, "edge": 0.0, "score": -1.0}
    on_sky = float((model & sky).sum() / model.sum())
    sky_u8 = sky.astype(np.uint8)
    bound = (cv2.dilate(sky_u8, np.ones((3, 3), np.uint8)) > sky_u8)      # non-sky pixels touching sky
    cols = np.nonzero(model.any(axis=0))[0]
    if not len(cols):
        return None
    bound[:, :cols.min()] = False
    bound[:, cols.max() + 1:] = False
    if bound.sum() < 20:
        return None
    outline = cv2.morphologyEx(model.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    dist = cv2.distanceTransform((~outline).astype(np.uint8), cv2.DIST_L2, 5)
    edge = float((dist[bound] <= SIL_TOL * cam.diag).mean())
    return {"on_sky": round(on_sky, 4), "edge": round(edge, 4), "score": round(edge - on_sky, 4)}


def _support(spec, cam: Camera, kps, segs, diag) -> float:
    """Share of the visible model corners whose two (or more) edges meet in the image within SUPPORT_RADIUS."""
    pts, vis_edges = visible(spec, cam)
    if not pts:
        return 0.0
    hits = 0
    for kid, centre in pts.items():
        p, n = snap_point(segs, centre, edge_directions(cam, kps, vis_edges, kid), SUPPORT_RADIUS * diag)
        hits += int(p is not None and n >= 2)
    return hits / len(pts)


def _obs_rms(cam: Camera, obs: dict, kps) -> float:
    if not obs:
        return float("inf")
    X = np.array([kps[k] for k in obs])
    U = np.array([v[0] for v in obs.values()])
    return float(np.sqrt(((cam.project(X) - U) ** 2).sum(axis=1).mean()))


def _snap_rounds(spec, cam: Camera, obs: dict, kps, segs, prior, diag, fix_f: bool = False, radii=SNAP_RADII):
    """Snapping rounds with a shrinking radius around the predicted position of every visible keypoint (not only
    the given ones); each round re-solves with the snapped points. Returns (camera, observations used)."""
    W, H = cam.width, cam.height
    for r in radii:
        pts, vis_edges = visible(spec, cam)
        new = {}
        for kid, centre in pts.items():
            dirs = edge_directions(cam, kps, vis_edges, kid)
            p, n = snap_point(segs, centre, dirs, r * diag)
            if p is not None and n >= 2:
                new[kid] = (tuple(p), "snapped", 1.0)
            elif p is not None and n == 1 and kid in obs:
                new[kid] = (tuple(p), "snapped_1", 0.6)
        merged = dict(obs)
        merged.update(new)
        k_ids = list(merged)
        Xs = np.array([kps[k] for k in k_ids])
        Us = np.array([merged[k][0] for k in k_ids])
        Ws = np.array([merged[k][2] for k in k_ids])
        thr = max(2.5 * r, 0.01) * diag
        pw = 0.5 if prior else 0.0
        # continue from the current camera (it is already close; a fresh PnP could throw away a good VP rotation),
        # and fall back to PnP when that keeps fewer points
        m_cur = np.linalg.norm(cam.project(Xs) - Us, axis=1) <= thr
        c2, m2 = cam, m_cur
        if m_cur.sum() >= 2:
            c2 = lm_refine(cam, Xs[m_cur], Us[m_cur], Ws[m_cur], f_prior=prior, prior_w=pw, fix_f=fix_f)
            m2 = np.linalg.norm(c2.project(Xs) - Us, axis=1) <= thr
        if len(Xs) >= 4:
            found = pnp_all(Xs, Us, W, H, [cam.f], thr, fix_f=fix_f)
            if found and found[0][2].sum() > m2.sum():
                _, c2, m2 = found[0]
        if m2.sum() < 2:
            break
        c2 = lm_refine(c2, Xs[m2], Us[m2], Ws[m2], f_prior=prior, prior_w=pw, fix_f=fix_f)
        m2 = m2 & _trim(c2, Xs, Us, diag)
        if m2.sum() >= 3:
            c2 = lm_refine(c2, Xs[m2], Us[m2], Ws[m2], f_prior=prior, prior_w=pw, fix_f=fix_f)
        cam = c2
        obs = {k: merged[k] for k, m in zip(k_ids, m2) if m}
    return cam, obs


def _finish(out, spec, cam, obs, kps, coarse, ids, mask, ch0, ch1, diag) -> dict:
    k_ids = list(obs)
    Xs = np.array([kps[k] for k in k_ids])
    Us = np.array([obs[k][0] for k in k_ids])
    err = np.linalg.norm(cam.project(Xs) - Us, axis=1)
    rms_px = float(np.sqrt((err ** 2).mean()))
    per_part = {}
    for k, e in zip(k_ids, err):
        per_part.setdefault(k.split("/")[0], []).append(e)
    parts = {p: round(float(np.sqrt(np.mean(np.square(v)))) / diag, 5) for p, v in per_part.items()}
    overall = rms_px / diag
    mismatch = mismatches(cam, kps, coarse, diag)
    given_err = np.linalg.norm(cam.project(np.array([kps[k] for k in ids])) - np.array([coarse[k] for k in ids], float),
                               axis=1)
    out["ai_agreement"] = round(float(np.median(given_err)) / diag, 5)
    out.update({
        "camera": cam, "status": "solved",
        "keypoints": [{"id": k, "xyz": [round(float(c), 3) for c in kps[k]], "uv": [round(float(c), 1) for c in obs[k][0]],
                       "source": obs[k][1], "weight": obs[k][2], "error_px": round(float(e), 2)}
                      for k, e in zip(k_ids, err)],
        "inliers": len(k_ids), "given": len(ids), "inlier_share": round(float(mask.mean()), 3),
        "rms_px": round(rms_px, 2), "rms": round(overall, 5),
        "chamfer_before": None if ch0 is None else round(ch0, 5), "chamfer": None if ch1 is None else round(ch1, 5),
        "parts": parts, "mismatch": mismatch,
    })
    out["gate"] = gate(out)
    return out


def mismatches(cam: Camera, kps: dict, given: dict, diag: float) -> list[str]:
    """Parts whose given keypoints the solved camera cannot explain while the others fit: the median distance of
    the part's given points from their projections exceeds MISMATCH_MIN_COARSE of the diagonal and MISMATCH_FACTOR
    times the median over all given points. Points rejected by RANSAC count, which is the point: a wrong part is
    rejected, not fitted."""
    ids = [k for k in given if k in kps]
    if len(ids) < 6:
        return []
    err = np.linalg.norm(cam.project(np.array([kps[k] for k in ids])) - np.array([given[k] for k in ids], float),
                         axis=1)
    overall = float(np.median(err))
    by = {}
    for k, e in zip(ids, err):                       # grouped by part and level: a wrong height moves only the top
        part, _, level = k.split("/")
        by.setdefault((part, level), []).append(e)
    return sorted({p for (p, _), v in by.items() if len(v) >= 2 and len(v) < len(ids)
                   and float(np.median(v)) > max(MISMATCH_MIN_COARSE * diag, MISMATCH_FACTOR * overall)})


def gate(res: dict) -> list[str]:
    """Problems against the T2 thresholds (empty = passes). Unmeasured values are reported, never passed."""
    p = []
    if res.get("status") not in ("solved", "manual"):
        return [f"camera not solved: {res.get('why', '')}"]
    # rms and chamfer are measured against what the solve itself picked, and an ornate facade has edges everywhere,
    # so they cannot prove a camera right on their own. Two independent checks: the share of visible model corners
    # that land on image corners, and agreement with the points the AI (or owner) gave.
    if res.get("support") is None or res["support"] < GATE["support_min"]:
        p.append(f"only {res.get('support')} of the visible model corners land on image corners "
                 f"(at least {GATE['support_min']})")
    if res.get("ai_agreement") is None or res["ai_agreement"] > GATE["agreement_max"]:
        p.append(f"the given points are {res.get('ai_agreement')} of the diagonal from the solved projection "
                 f"(at most {GATE['agreement_max']})")
    # corners and lines repeat along a facade (a camera off by one bay still lands corners on corners); the outline
    # against the sky does not, so it is checked whenever the photo shows enough sky
    sil = res.get("silhouette")
    if sil is not None and sil.get("score", -1) < GATE["silhouette_min"]:
        p.append(f"the model outline does not follow the skyline (score {sil.get('score')} < {GATE['silhouette_min']})")
    if res.get("inliers", 0) < GATE["inliers_min"]:
        p.append(f"{res.get('inliers')} keypoints fit, at least {GATE['inliers_min']} are needed")
    if res.get("rms") is None or res["rms"] > GATE["rms_max"]:
        p.append(f"reprojection RMS {res.get('rms')} > {GATE['rms_max']} of the diagonal")
    if res.get("chamfer") is None:
        p.append("edge distance not measurable")
    elif res["chamfer"] > GATE["chamfer_max"]:
        p.append(f"edge distance {res['chamfer']} > {GATE['chamfer_max']} of the diagonal")
    for part in res.get("mismatch") or []:
        p.append(f"geometry_mismatch: part {part} does not fit the photo while the rest does")
    return p


# ----------------------------------------------------------------------------------------------- manual (T3)
MANUAL_INLIER = 0.012                  # a hand-placed point further than this share of the diagonal is flagged


def _fit_points(X, uv, W, H, cands, vps, dirs, view, prior, ids):
    """Best camera for trusted points (PnP over focal candidates for 4+, VP rotation for fewer), with errors."""
    diag = math.hypot(W, H)
    found = []
    if len(ids) >= 4:
        found += pnp_all(X, uv, W, H, cands, MANUAL_INLIER * 4 * diag)
    for f in cands:
        found += vp_starts(X, uv, (W, H), f, rotations_from_vps(vps, f, (W, H), dirs, view),
                           MANUAL_INLIER * 4 * diag)[:1]
    best = None
    for _, c0, mask in found:
        c = c0
        if mask.sum() >= 3:
            c = lm_refine(c0, X[mask], uv[mask], f_prior=prior, prior_w=0.2 if prior else 0.0, fix_f=mask.sum() < 4)
        err = np.linalg.norm(c.project(X) - uv, axis=1)
        key = (int((err <= MANUAL_INLIER * diag).sum()), -float(np.sqrt((err ** 2).mean())))
        if best is None or key > best[0]:
            best = (key, c, err)
    return (best[1], best[2]) if best else (None, None)


def solve_manual(spec: dict, grey: np.ndarray, points: dict, f_prior: float | None = None, view: dict | None = None,
                 drop_outliers: bool = False) -> dict:
    """Camera from points the owner placed by hand {keypoint id: (u, v)}. They are trusted as given: no snapping.
    4+ points: PnP over the focal candidates, then LM with the focal free. 2-3 points: rotation from the
    vanishing points, position from the points. The same checks as the automatic solve are reported."""
    H, W = grey.shape
    diag = math.hypot(W, H)
    kps, _ = model(spec)
    ids = [k for k in points if k in kps]
    out = {"camera": None, "status": "failed", "keypoints": [], "f_sources": {}}
    if len(ids) < 2:
        out["why"] = f"{len(ids)} points placed: place at least 2 (4 or more without clear building lines)"
        return out
    X = np.array([kps[k] for k in ids])
    uv = np.array([points[k] for k in ids], float)
    segs = lens.segments(grey, 0.008 * diag)
    vps = scene_vps(segs, (W, H))
    vp_list = ([vps["vertical"]] if vps["vertical"] is not None else []) + list(vps["horizontal"])
    vp_f = focal_from_vps(vp_list, (W, H))
    out["f_sources"] = {"exif": f_prior, "vanishing_points": vp_f}
    cands = [f for f in (f_prior, vp_f) if f] + [focal_from_fov(W, H, a) for a in FOV_SWEEP]
    dirs = model_directions(spec)
    prior = f_prior or vp_f
    cam, err = _fit_points(X, uv, W, H, cands, vps, dirs, view, prior, ids)
    if cam is None:
        out["why"] = "no camera explains these points: check that each point is on its corner"
        return out
    dropped = []
    # AI-placed points: a wrong one bends the camera so that a right one looks worst; find it by leaving each
    # point out in turn and keeping the removal that explains the rest best (hand-placed points are only named)
    while drop_outliers and err.max() > MANUAL_INLIER * diag and len(ids) > 4:
        trials = []
        for i in range(len(ids)):
            keep = [j for j in range(len(ids)) if j != i]
            c_i, e_i = _fit_points(X[keep], uv[keep], W, H, cands, vps, dirs, view, prior, [ids[j] for j in keep])
            if c_i is not None:
                trials.append((float(np.sqrt((e_i ** 2).mean())), i, c_i, e_i))
        if not trials:
            break
        rms_i, i, c_i, e_i = min(trials, key=lambda t: t[0])
        if rms_i > 0.6 * float(np.sqrt((err ** 2).mean())):
            break                                   # no single point explains the misfit
        dropped.append(ids[i])
        keep = [j for j in range(len(ids)) if j != i]
        X, uv, ids = X[keep], uv[keep], [ids[j] for j in keep]
        cam, err = c_i, e_i
    out["dropped"] = dropped
    f_lo, f_hi = focal_from_fov(W, H, max(FOV_SWEEP)), focal_from_fov(W, H, min(FOV_SWEEP))
    if not 0.8 * f_lo <= cam.f <= 1.25 * f_hi:
        out["why"] = f"the points give an implausible lens (focal {cam.f:.0f} px): check the points"
        return out
    sky = sky_of(grey)
    rms_px = float(np.sqrt((err ** 2).mean()))
    out.update({
        "camera": cam, "status": "manual", "start": "manual",
        "keypoints": [{"id": k, "xyz": [round(float(c), 3) for c in kps[k]], "uv": [round(float(v), 1) for v in points[k]],
                       "source": "manual", "weight": 1.0, "error_px": round(float(e), 2)} for k, e in zip(ids, err)],
        "inliers": int((err <= MANUAL_INLIER * diag).sum()), "given": len(ids), "inlier_share": 1.0,
        "rms_px": round(rms_px, 2), "rms": round(rms_px / diag, 5), "ai_agreement": round(float(np.median(err)) / diag, 5),
        "support": round(_support(spec, cam, kps, segs, diag), 3), "silhouette": silhouette(spec, cam, sky),
        "chamfer": chamfer(cam, _edge_samples(spec, cam), _distance_map(grey)), "chamfer_before": None,
        "parts": {}, "mismatch": mismatches(cam, kps, points, diag) if len(ids) >= 6 else [],
        "off_points": [k for k, e in zip(ids, err) if e > MANUAL_INLIER * diag],
    })
    if out["chamfer"] is not None:
        out["chamfer"] = round(out["chamfer"], 5)
    # hand-placed points are few by nature: 4 that fit stand in for the automatic solve's 6
    g = gate(out)
    if out["inliers"] >= 4:
        g = [x for x in g if "keypoints fit" not in x]
    out["gate"] = g
    return out
