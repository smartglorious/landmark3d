"""Denser correspondences from the facade grid: floor lines, bay lines and face ends of every visible wall.

Single model lines are ambiguous on a facade (every cornice and window head is a horizontal line), so lines are
matched as a comb: for each face, the whole family of horizontal lines (floor lines with their uneven spacing,
base and wall top) is shifted together across the image and the shift with the most supporting image segments wins;
the same for the vertical family (bay lines plus the two face ends). The matched lines then add residuals to the
camera refinement: points sampled along each model line must lie on the matched image line.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from .keypoints import Camera, face_visible, part_rings

ANGLE_TOL_DEG = 3.0          # image segment direction against the projected model line
LINE_TOL = 0.004             # a segment supports a line when both ends are this close (share of the diagonal)
SEARCH = (0.05, 0.02, 0.008)  # comb search half-range per round, share of the diagonal
STEP = 0.0015                # comb search step, share of the diagonal
MIN_FACE_PX = 0.08           # faces narrower than this share of the diagonal on screen are skipped
MIN_SUPPORT = 0.25           # a matched line needs supporting segments over this share of its on-screen length
SAMPLES = 6                  # points per matched line in the refinement


def face_lines(spec: dict) -> list:
    """[(part, edge index, kind "h" | "v", A, B)] 3D model lines of every wall face: floor lines from the facade's
    level height (and the base and top), bay lines from its bay spacing, and the two face ends."""
    out = []
    parts = {p["id"]: p for p in spec.get("parts") or []}
    for pid, (ring, zb, zt) in part_rings(spec).items():
        if zt is None:
            continue
        fac = (parts.get(pid) or {}).get("facade") or {}
        lh = float(fac.get("level_height") or 0)
        bay = float(fac.get("bay_spacing") or 0)
        zs = [zb, zt]
        if lh > 1.5:
            k = 1
            while zb + k * lh < zt - 0.5 * lh:
                zs.append(zb + k * lh)
                k += 1
        n = len(ring)
        for i in range(n):
            a, b = np.array(ring[i], float), np.array(ring[(i + 1) % n], float)
            L = float(np.linalg.norm(b - a))
            if L < 2.0:
                continue
            for z in sorted(set(round(z, 3) for z in zs)):
                out.append((pid, i, "h", np.r_[a, z], np.r_[b, z]))
            ss = [0.0, L]
            if bay > 1.0:
                nb = max(1, int(round(L / bay)))
                ss += [L * k / nb for k in range(1, nb)]
            for s in sorted(set(round(s, 3) for s in ss)):
                p = a + (b - a) * s / L
                out.append((pid, i, "v", np.r_[p, zb], np.r_[p, zt]))
    return out


def _seg_arrays(segs: np.ndarray):
    d = segs[:, 2:] - segs[:, :2]
    ln = np.linalg.norm(d, axis=1)
    return d / (ln[:, None] + 1e-12), ln


def _support(segs, du, ln, p, q, tol, ang_cos) -> tuple[float, int]:
    """(supporting length, best segment index) for the image line p-q: segments parallel to it, both ends within
    tol of the line and overlapping its extent."""
    v = q - p
    L = float(np.linalg.norm(v))
    if L < 1e-6:
        return 0.0, -1
    u = v / L
    nrm = np.array([-u[1], u[0]])
    par = np.abs(du @ u) >= ang_cos
    d0 = np.abs((segs[:, :2] - p) @ nrm)
    d1 = np.abs((segs[:, 2:] - p) @ nrm)
    t0 = (segs[:, :2] - p) @ u
    t1 = (segs[:, 2:] - p) @ u
    lo, hi = np.minimum(t0, t1), np.maximum(t0, t1)
    overlap = np.clip(np.minimum(hi, L) - np.maximum(lo, 0.0), 0, None)
    ok = par & (d0 <= tol) & (d1 <= tol) & (overlap > 0)
    if not ok.any():
        return 0.0, -1
    idx = np.nonzero(ok)[0]
    return float(overlap[idx].sum()), int(idx[np.argmax(overlap[idx])])


def match(spec: dict, cam: Camera, segs: np.ndarray, search: float) -> list:
    """Line matches [(A, B, image line (a, b, c))] for the visible faces, found by a comb search per face and
    line family within +-search (share of the diagonal)."""
    if not len(segs):
        return []
    diag = cam.diag
    du, ln = _seg_arrays(segs)
    ang_cos = math.cos(math.radians(ANGLE_TOL_DEG))
    tol = LINE_TOL * diag
    vis = face_visible(part_rings(spec), cam)
    lines = [l for l in face_lines(spec) if vis.get((l[0], l[1]))]
    groups = {}
    for pid, i, kind, A, B in lines:
        groups.setdefault((pid, i, kind), []).append((A, B))
    out = []
    W, H = cam.width, cam.height
    for (pid, i, kind), ls in groups.items():
        AB = np.array([[A, B] for A, B in ls])
        if (cam.depth(AB.reshape(-1, 3)) <= 0.1).any():
            continue
        P = cam.project(AB.reshape(-1, 3)).reshape(-1, 2, 2)
        span = P.reshape(-1, 2)
        if np.linalg.norm(span.max(axis=0) - span.min(axis=0)) < MIN_FACE_PX * diag:
            continue
        # shift direction: across the family (horizontal lines move vertically in the image, and vice versa)
        mean_dir = (P[:, 1] - P[:, 0]).mean(axis=0)
        mean_dir /= max(np.linalg.norm(mean_dir), 1e-9)
        shift_dir = np.array([-mean_dir[1], mean_dir[0]])
        best = (0.0, 0.0)
        for off in np.arange(-search * diag, search * diag + 1e-9, STEP * diag):
            sh = off * shift_dir
            tot = sum(_support(segs, du, ln, p + sh, q + sh, tol, ang_cos)[0] for p, q in P)
            if tot > best[0] + 1e-9 or (abs(tot - best[0]) <= 1e-9 and abs(off) < abs(best[1])):
                best = (tot, off)
        if best[0] <= 0:
            continue
        sh = best[1] * shift_dir
        for (A, B), (p, q) in zip(ls, P):
            p2, q2 = p + sh, q + sh
            inside = all(-0.02 * W <= x <= 1.02 * W and -0.02 * H <= y <= 1.02 * H for x, y in (p2, q2))
            sup, k = _support(segs, du, ln, p2, q2, tol, ang_cos)
            if k < 0 or not inside or sup < MIN_SUPPORT * float(np.linalg.norm(q2 - p2)):
                continue
            s = segs[k]
            l = np.cross(np.r_[s[:2], 1.0], np.r_[s[2:], 1.0])
            l = l / (np.linalg.norm(l[:2]) + 1e-12)
            out.append((A, B, l))
    return out


def line_term(matches: list, weight: float):
    """extra(cam) for lm_refine: signed distances of points sampled along each model line to its image line."""
    if not matches:
        return None
    t = np.linspace(0.1, 0.9, SAMPLES)[:, None]
    X = np.concatenate([A + t * (B - A) for A, B, _ in matches])
    Lh = np.repeat(np.array([l for _, _, l in matches]), SAMPLES, axis=0)

    def extra(c: Camera):
        proj, J = cv2.projectPoints(X, c.rvec, c.tvec, c.K, None)
        proj = proj.reshape(-1, 2)
        r = (Lh[:, :2] * proj).sum(axis=1) + Lh[:, 2]
        Jp = np.c_[J[:, :6], (J[:, 6] + J[:, 7]) * c.f].reshape(-1, 2, 7)
        Jr = np.einsum("nk,nkp->np", Lh[:, :2], Jp)
        return r * weight, Jr * weight
    return extra


def refine(spec: dict, cam: Camera, segs: np.ndarray, X, uv, w, lm_refine, fix_f: bool = True,
           prior=None, rounds=SEARCH) -> tuple[Camera, int]:
    """Refine the camera with facade-grid line matches (comb search, shrinking range) plus the keypoints.
    Returns (camera, number of matched lines in the last round)."""
    n = 0
    for search in rounds:
        m = match(spec, cam, segs, search)
        n = len(m)
        if n < 3:
            break
        # lines in total weigh about as much as the keypoints
        wt = math.sqrt(max(1, len(X)) / (n * SAMPLES))
        cam = lm_refine(cam, X, uv, w, iters=15, extra=line_term(m, wt), fix_f=fix_f,
                        f_prior=prior, prior_w=0.5 if prior else 0.0)
    return cam, n
