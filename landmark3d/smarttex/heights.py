"""Fit part heights from photos: one joint solve of every photo's camera and the wall heights of the parts, with
the plan fixed (fitted to the measured footprint, so it carries the metric scale).

Keypoints are wall corners: a base corner sits at the part's base_z, a top corner at base_z + height. A part that
stands on another (its base at that part's top, its centre inside it) keeps standing on it when heights change.
Unknowns: per photo rotation, position and focal; per part the log of its wall height (only parts with an observed
top corner). Residuals: reprojection errors (Huber), plus weak priors that keep heights near the AI's values and
focal lengths plausible. Points far off after a first solve are dropped and the solve is repeated.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from . import camera as cm
from . import keypoints as kp

HEIGHT_PRIOR_W = 0.6        # weight of log(h / h_ai) in pixel-equivalent units x diag/100 (weak)
HEIGHT_RANGE = (0.6, 1.7)   # fitted heights stay within this factor of the AI's
OUTLIER = 0.03              # points further than this share of the diagonal are dropped after the first solve
HUBER = 0.01
ACCEPT_RMS = 0.012          # the fit is accepted when the remaining points' RMS is below this share of the diagonal
MIN_REDUNDANCY = 8          # observed coordinates minus unknowns needed before heights are trusted


def supports(spec: dict) -> dict:
    """{part: the part it stands on} for parts whose base is at another part's top and whose centre is inside it."""
    rings = kp.part_rings(spec)
    parts = {p["id"]: p for p in spec["parts"] if p.get("enabled", True) is not False}
    out = {}
    for pid, p in parts.items():
        zb = float(p.get("base_z", 0))
        if zb <= 0.5 or pid not in rings:
            continue
        c = np.array(rings[pid][0], float).mean(axis=0)
        best = None
        for qid, (ring, qb, qt) in rings.items():
            if qid == pid or qt is None or abs(qt - zb) > 0.6:
                continue
            from .. import geom
            if geom.point_in_polygon(tuple(c), ring):
                best = qid
        if best:
            out[pid] = best
    return out


class Model:
    """Keypoint positions as a function of part heights."""

    def __init__(self, spec: dict):
        self.spec = spec
        self.rings = kp.part_rings(spec)
        self.parts = {p["id"]: p for p in spec["parts"] if p.get("enabled", True) is not False}
        self.h0 = {pid: float(p["height"]) for pid, p in self.parts.items()}
        self.base0 = {pid: float(p.get("base_z", 0)) for pid, p in self.parts.items()}
        self.on = supports(spec)

    def base(self, pid, h):
        if pid in self.on:
            q = self.on[pid]
            return self.base(q, h) + h.get(q, self.h0[q])
        return self.base0[pid]

    def xyz(self, kid, h):
        pid, i, level = kid.split("/")
        ring = self.rings[pid][0]
        x, y = ring[int(i)]
        zb = self.base(pid, h)
        return (float(x), float(y), zb + (h.get(pid, self.h0[pid]) if level == "t" else 0.0))


def solve(spec: dict, photos: list, iters: int = 40) -> dict:
    """photos: [{"size": (W, H), "points": {kid: (u, v)}, "camera": Camera (initial), "f_prior": float | None}].
    Returns {"heights": {part: m}, "cameras": [Camera], "rms": [share of diag per photo], "used": [[kid]],
    "dropped": [[kid]], "accepted": bool}."""
    m = Model(spec)
    kps0 = kp.model(spec)[0]
    pts = [{k: v for k, v in ph["points"].items() if k in kps0} for ph in photos]
    free = sorted({k.split("/")[0] for p in pts for k in p if k.endswith("/t")} |
                  {m.on[k.split("/")[0]] for p in pts for k in p if k.split("/")[0] in m.on})
    free = [pid for pid in free if pid in m.parts]
    cams = [ph["camera"] for ph in photos]
    dropped = [[] for _ in photos]

    def unpack(x):
        hs = {pid: m.h0[pid] * math.exp(x[i]) for i, pid in enumerate(free)}
        cs = []
        o = len(free)
        for k, ph in enumerate(photos):
            W, H = ph["size"]
            t = x[o + 7 * k:o + 7 * k + 7]
            cs.append(kp.Camera(W, H, math.exp(t[6]), t[:3], t[3:6]))
        return hs, cs

    def residuals(x):
        hs, cs = unpack(x)
        out = []
        for k, (ph, c) in enumerate(zip(photos, cs)):
            diag = c.diag
            ids = list(pts[k])
            if not ids:
                continue
            X = np.array([m.xyz(kid, hs) for kid in ids])
            e = (c.project(X) - np.array([pts[k][kid] for kid in ids], float)) / diag * 100.0
            n = np.linalg.norm(e, axis=1, keepdims=True)
            hw = np.where(n <= HUBER * 100, 1.0, np.sqrt(HUBER * 100 / np.maximum(n, 1e-9)))
            out.append((e * hw).ravel())
            if ph.get("f_prior"):
                out.append(np.array([0.5 * (math.log(c.f) - math.log(ph["f_prior"]))]))
        out.append(HEIGHT_PRIOR_W * x[:len(free)])
        return np.concatenate(out)

    def pack(hs, cs):
        x = [math.log(hs.get(pid, m.h0[pid]) / m.h0[pid]) for pid in free]
        for c in cs:
            x += list(np.ravel(c.rvec)) + list(np.ravel(c.tvec)) + [math.log(c.f)]
        return np.array(x, float)

    def lm(x):
        lam = 1e-2
        r = residuals(x)
        cost = float(r @ r)
        for _ in range(iters):
            J = np.zeros((r.size, x.size))
            for j in range(x.size):                     # numerical Jacobian: the problem is small
                d = 1e-5 * max(1.0, abs(x[j]))
                xp = x.copy()
                xp[j] += d
                J[:, j] = (residuals(xp) - r) / d
            A = J.T @ J
            g = J.T @ r
            step = np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-9), -g)
            # keep heights inside their range
            xn = x + step
            lo, hi = math.log(HEIGHT_RANGE[0]), math.log(HEIGHT_RANGE[1])
            xn[:len(free)] = np.clip(xn[:len(free)], lo, hi)
            r2 = residuals(xn)
            c2 = float(r2 @ r2)
            if c2 < cost:
                x, r, cost = xn, r2, c2
                lam = max(lam / 3, 1e-7)
                if np.linalg.norm(step) < 1e-7:
                    break
            else:
                lam *= 4
                if lam > 1e8:
                    break
        return x

    x = lm(pack({}, cams))
    # drop points far off, then solve again
    hs, cs = unpack(x)
    for k, c in enumerate(cs):
        for kid in list(pts[k]):
            e = np.linalg.norm(c.project([m.xyz(kid, hs)])[0] - np.array(pts[k][kid])) / c.diag
            if e > OUTLIER and len(pts[k]) > 4:
                dropped[k].append(kid)
                del pts[k][kid]
    if any(dropped):
        x = lm(x)
    hs, cs = unpack(x)
    rms = []
    for k, c in enumerate(cs):
        ids = list(pts[k])
        if not ids:
            rms.append(None)
            continue
        e = np.linalg.norm(c.project(np.array([m.xyz(kid, hs) for kid in ids])) - np.array([pts[k][i] for i in ids]),
                           axis=1) / c.diag
        rms.append(round(float(np.sqrt((e ** 2).mean())), 5))
    # enough evidence: two photos at least, and clearly more coordinates than unknowns (7 per camera, 1 per height),
    # otherwise the heights absorb the point errors and a "fit" means nothing
    n_obs = 2 * sum(len(p) for p in pts)
    n_unknown = 7 * len(photos) + len(free)
    redundancy = n_obs - n_unknown
    ok = (len(photos) >= 2 and redundancy >= MIN_REDUNDANCY
          and all(r is not None and r <= ACCEPT_RMS for r in rms))
    why = None if ok else (f"{len(photos)} photo(s) with a camera; at least 2 are needed" if len(photos) < 2 else
                           f"{n_obs} coordinates for {n_unknown} unknowns: at least {MIN_REDUNDANCY} more needed"
                           if redundancy < MIN_REDUNDANCY else "the points do not fit one model")
    return {"why": why, "redundancy": redundancy,"heights": {pid: round(h, 2) for pid, h in hs.items()}, "cameras": cs, "rms": rms,
            "used": [list(p) for p in pts], "dropped": dropped, "accepted": ok,
            "free": free, "before": {pid: m.h0[pid] for pid in free}}


def apply(spec: dict, heights: dict) -> dict:
    """A copy of the spec with the fitted wall heights; parts standing on a changed part are lifted with it."""
    import copy
    out = copy.deepcopy(spec)
    m = Model(spec)
    parts = {p["id"]: p for p in out["parts"]}
    for pid, h in heights.items():
        parts[pid]["height"] = round(float(h), 2)
    full = {pid: float(parts[pid]["height"]) for pid in m.parts}
    for pid in m.on:
        parts[pid]["base_z"] = round(m.base(pid, full), 2)
    return out
