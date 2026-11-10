"""Photogrammetry adapter contract (plan M7, tasks 2-3). Optional: without COLMAP or Meshroom the standard procedural
workflow runs unchanged, and the status says so (no silent default, no fake reconstruction).

The adapter does not run the reconstruction itself. It defines what an external output folder must contain, checks it,
exports a COLMAP text model into that folder, and aligns its points to project coordinates with the footprint and a
known height. Fitting planes and component anchors (task 4) and using dense geometry as bake source (task 5) are not
implemented yet.

Output folder contract (`manifest.json` plus the files it names):
  {"adapter": "colmap" | "meshroom", "adapter_version": "...", "units": "m" | "arbitrary",
   "cameras": [{"image": "name.jpg", "R": [[..3..]x3], "t": [x, y, z]}, ...],
   "points": "points.ply", "mesh": "mesh.ply" (optional: a sparse result has points only)}
"units" is "arbitrary" when the reconstruction has no metric scale (COLMAP sparse); the scale then comes from the known
height in align(). R and t map world to camera coordinates.
"""
from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / ".tools"      # local installs, checked by SHA-256 when installed
ADAPTERS = {"colmap": "colmap", "meshroom": "meshroom_batch"}   # adapter name -> executable name
MIN_CAMERAS = 8
SCALE_RANGE = (0.2, 5.0)          # a reconstruction scaled further than this is not trusted without a person


def detect() -> dict:
    """Which adapters are installed here: a verified local install first, then PATH. Nothing is run."""
    out = {}
    for name, exe in ADAPTERS.items():
        local = TOOLS_DIR / name / "bin" / (exe + ".exe" if os.name == "nt" else exe)
        path = str(local) if local.is_file() else shutil.which(exe)
        out[name] = {"available": path is not None, "executable": exe, "path": path}
    return out


def status() -> dict:
    found = [n for n, v in detect().items() if v["available"]]
    if found:
        return {"mode": "adapter", "adapters": found,
                "note": "reconstruction output can be imported; the procedural build is still the default"}
    return {"mode": "procedural", "adapters": [],
            "note": "no photogrammetry tool found; the standard procedural workflow is used"}


def check_output(folder: Path) -> list[str]:
    """Problems with an external output folder. Empty means it matches the contract."""
    folder = Path(folder)
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        return ["manifest.json is missing"]
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"manifest.json cannot be read: {exc}"]
    errors = []
    if m.get("adapter") not in ADAPTERS:
        errors.append(f"adapter must be one of {sorted(ADAPTERS)}")
    if m.get("units") not in ("m", "arbitrary"):
        errors.append("units must be metres or arbitrary")
    cams = m.get("cameras") or []
    if len(cams) < MIN_CAMERAS:
        errors.append(f"{len(cams)} cameras; at least {MIN_CAMERAS} are needed")
    for c in cams:
        R, t = c.get("R"), c.get("t")
        if not (isinstance(R, list) and len(R) == 3 and all(isinstance(r, list) and len(r) == 3 for r in R)
                and isinstance(t, list) and len(t) == 3):
            errors.append(f"camera {c.get('image', '?')}: R must be 3x3 and t must have 3 values")
            break
    name = m.get("points")
    if not name or not (folder / name).is_file():
        errors.append(f"points file {name!r} is missing")
    if m.get("mesh") and not (folder / m["mesh"]).is_file():
        errors.append(f"mesh file {m['mesh']!r} is missing")
    return errors


def _quat_to_matrix(qw, qx, qy, qz):
    n = (qw * qw + qx * qx + qy * qy + qz * qz) ** 0.5
    w, x, y, z = qw / n, qx / n, qy / n, qz / n
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]]


def export_manifest(model_dir: Path, out_dir: Path, adapter: str = "colmap", version: str = "") -> dict:
    """Read a COLMAP model exported as text (images.txt, points3D.txt) and write the contract folder: manifest.json and
    points.ply. COLMAP sparse output has no metric scale, so units are 'arbitrary'. Returns the manifest."""
    model_dir, out_dir = Path(model_dir), Path(out_dir)
    cams, pts = [], []
    # Each image is a pose line followed by its 2D point line, which is empty when the image has no points. Empty lines
    # are kept on purpose: dropping them shifts every pose after the first empty one.
    lines = [ln for ln in (model_dir / "images.txt").read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    for i in range(0, len(lines), 2):
        f = lines[i].split()
        qw, qx, qy, qz, tx, ty, tz = (float(v) for v in f[1:8])
        cams.append({"image": f[9], "R": _quat_to_matrix(qw, qx, qy, qz), "t": [tx, ty, tz]})
    for ln in (model_dir / "points3D.txt").read_text(encoding="utf-8").splitlines():
        if ln and not ln.startswith("#"):
            f = ln.split()
            pts.append((float(f[1]), float(f[2]), float(f[3])))
    out_dir.mkdir(parents=True, exist_ok=True)
    header = ["ply", "format ascii 1.0", f"element vertex {len(pts)}", "property float x",
              "property float y", "property float z", "end_header"]
    body = header + [f"{x} {y} {z}" for x, y, z in pts]
    (out_dir / "points.ply").write_text("\n".join(body) + "\n", encoding="utf-8")
    manifest = {"adapter": adapter, "adapter_version": version, "units": "arbitrary", "cameras": cams,
                "points": "points.ply"}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def align(points: list, footprint_xy: list, known_height: float) -> dict:
    """Transform that puts the reconstruction on project coordinates: scale from the known height, then the centre of
    its footprint on the centre of the plan footprint. Raises ValueError when the scale is implausible."""
    if not points or known_height <= 0:
        raise ValueError("need points and a known height above zero")
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    h = max(zs) - min(zs)
    if h <= 0:
        raise ValueError("the reconstruction has no vertical extent")
    scale = known_height / h
    lo, hi = SCALE_RANGE
    if not lo <= scale <= hi:
        raise ValueError(f"scale {scale:.2f} is outside {lo}-{hi}; the known height or the reconstruction is wrong")
    fx = [p[0] for p in footprint_xy]
    fy = [p[1] for p in footprint_xy]
    src = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    dst = ((min(fx) + max(fx)) / 2, (min(fy) + max(fy)) / 2)
    return {"scale": round(scale, 6),
            "translate": [round(dst[0] - src[0] * scale, 6), round(dst[1] - src[1] * scale, 6),
                          round(-min(zs) * scale, 6)]}


# ----------------------------------------------------------------------------- gravity-aligned alignment (task 3)
def _matmul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(len(B))) for j in range(len(B[0]))] for i in range(len(A))]


def _transpose(A):
    return [list(r) for r in zip(*A)]


def _eigh3(A, sweeps: int = 60):
    """Eigen-decomposition of a symmetric 3x3 matrix by Jacobi rotations. Returns (values, vectors as columns)."""
    a = [list(r) for r in A]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(sweeps):
        p, q, big = 0, 1, 0.0
        for i in range(3):
            for j in range(i + 1, 3):
                if abs(a[i][j]) > big:
                    p, q, big = i, j, abs(a[i][j])
        if big < 1e-12:
            break
        phi = 0.5 * math.atan2(2 * a[p][q], a[q][q] - a[p][p])
        c, s = math.cos(phi), math.sin(phi)
        J = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
        J[p][p], J[q][q], J[p][q], J[q][p] = c, c, s, -s
        a = _matmul(_transpose(J), _matmul(a, J))
        v = _matmul(v, J)
    return [a[i][i] for i in range(3)], v


def _rotation_from_to(u, w):
    """Rotation matrix taking unit vector u onto unit vector w (Rodrigues)."""
    c = u[0] * w[0] + u[1] * w[1] + u[2] * w[2]
    ax = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
    s = math.sqrt(sum(x * x for x in ax))
    if s < 1e-12:
        if c > 0:
            return [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]
        return [[1.0, 0, 0], [0, -1.0, 0], [0, 0, -1.0]]     # opposite vectors: a half turn about x
    k = [x / s for x in ax]
    K = [[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]]
    KK = _matmul(K, K)
    return [[(1.0 if i == j else 0.0) + s * K[i][j] + (1 - c) * KK[i][j] for j in range(3)] for i in range(3)]


def camera_centres(cams: list) -> list:
    """World-space camera centres: C = -R^T t for each camera (R, t map world to camera)."""
    out = []
    for c in cams:
        Rt = _transpose(c["R"])
        t = c["t"]
        out.append(tuple(-sum(Rt[i][k] * t[k] for k in range(3)) for i in range(3)))
    return out


def up_axis(cams: list, points: list) -> tuple:
    """Vertical direction of a reconstruction, from where the cameras stand: photos taken around a building lie on a
    horizontal ring, so the smallest-variance direction of the camera centres is the vertical. The sign points up from
    the building (cameras above the points)."""
    C = camera_centres(cams)
    if len(C) < 3:
        raise ValueError("need at least three cameras to find the vertical")
    mean = [sum(c[i] for c in C) / len(C) for i in range(3)]
    cov = [[sum((c[i] - mean[i]) * (c[j] - mean[j]) for c in C) / len(C) for j in range(3)] for i in range(3)]
    vals, vecs = _eigh3(cov)
    k = min(range(3), key=lambda i: vals[i])
    n = [vecs[r][k] for r in range(3)]
    norm = math.sqrt(sum(x * x for x in n))
    n = [x / norm for x in n]
    pm = [sum(p[i] for p in points) / len(points) for i in range(3)]
    if sum((mean[i] - pm[i]) * n[i] for i in range(3)) < 0:
        n = [-x for x in n]
    return tuple(n)


def _apply(M, p):
    return tuple(sum(M[i][k] * p[k] for k in range(3)) for i in range(3))


def _pct(vals: list, q: float) -> float:
    v = sorted(vals)
    k = (len(v) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _robust_extent(vals: list) -> float:
    """Extent between the 2nd and 98th percentiles: stray points far from the building do not stretch it."""
    return _pct(vals, 0.98) - _pct(vals, 0.02)


FOOTPRINT_TOLERANCE = 0.35        # the reconstruction's plan must match the footprint within 35 % (both extents together)


def align_gravity(points: list, cams: list, footprint_xy: list, known_height: float) -> dict:
    """Full alignment of a reconstruction whose frame is arbitrary. Vertical: from the camera ring. Scale: the robust
    vertical extent is set to the known height (COLMAP units are arbitrary, so the scale itself is not a check). Yaw: the
    footprint's longest side, with the quarter turn that fits the two extents best. The result must then match the
    footprint: a plan that is off by more than FOOTPRINT_TOLERANCE is refused. Base on z = 0, centre on the footprint."""
    if known_height <= 0 or len(points) < 4:
        raise ValueError("need points and a known height above zero")
    n = up_axis(cams, points)
    M = _rotation_from_to(n, (0.0, 0.0, 1.0))
    rp = [_apply(M, p) for p in points]
    h = _robust_extent([p[2] for p in rp])
    if h <= 0:
        raise ValueError("the reconstruction has no vertical extent")
    scale = known_height / h
    sp = [(p[0] * scale, p[1] * scale, p[2] * scale) for p in rp]
    mx = sum(p[0] for p in sp) / len(sp)
    my = sum(p[1] for p in sp) / len(sp)
    sxx = sum((p[0] - mx) ** 2 for p in sp)
    syy = sum((p[1] - my) ** 2 for p in sp)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in sp)
    theta_p = 0.5 * math.atan2(2 * sxy, sxx - syy)
    fx = [p[0] for p in footprint_xy]
    fy = [p[1] for p in footprint_xy]
    fw, fd = max(fx) - min(fx), max(fy) - min(fy)
    edges = [(footprint_xy[(i + 1) % len(footprint_xy)][0] - footprint_xy[i][0],
              footprint_xy[(i + 1) % len(footprint_xy)][1] - footprint_xy[i][1]) for i in range(len(footprint_xy))]
    long_edge = max(range(len(edges)), key=lambda i: edges[i][0] ** 2 + edges[i][1] ** 2)
    e0 = footprint_xy[long_edge]
    e1 = footprint_xy[(long_edge + 1) % len(footprint_xy)]
    theta_f = math.atan2(e1[1] - e0[1], e1[0] - e0[0])
    best = None
    for k in range(4):
        ang = theta_f - theta_p + k * math.pi / 2
        c, s = math.cos(ang), math.sin(ang)
        xs = [c * p[0] - s * p[1] for p in sp]
        ys = [s * p[0] + c * p[1] for p in sp]
        err = abs(_robust_extent(xs) - fw) + abs(_robust_extent(ys) - fd)
        if best is None or err < best[0]:
            best = (err, ang, xs, ys)
    err, ang, xs, ys = best
    residual = err / (fw + fd)
    if residual > FOOTPRINT_TOLERANCE:
        raise ValueError(f"the reconstructed plan does not match the footprint (off by {residual:.0%}); "
                         f"check the photos and the known height")
    R2 = [[math.cos(ang), -math.sin(ang), 0.0], [math.sin(ang), math.cos(ang), 0.0], [0.0, 0.0, 1.0]]
    rot = _matmul(R2, _matmul([[scale, 0, 0], [0, scale, 0], [0, 0, scale]], M))
    tp = [_apply(rot, p) for p in points]
    cx = (_pct([p[0] for p in tp], 0.02) + _pct([p[0] for p in tp], 0.98)) / 2
    cy = (_pct([p[1] for p in tp], 0.02) + _pct([p[1] for p in tp], 0.98)) / 2
    fcx, fcy = (min(fx) + max(fx)) / 2, (min(fy) + max(fy)) / 2
    tz = -_pct([p[2] for p in tp], 0.02)
    return {"matrix": [[round(v, 9) for v in row] for row in rot],
            "translate": [round(fcx - cx, 6), round(fcy - cy, 6), round(tz, 6)],
            "scale": round(scale, 6), "up": [round(v, 6) for v in n], "footprint_residual": round(residual, 4)}


def apply_transform(points: list, tr: dict) -> list:
    M = tr["matrix"]
    t = tr["translate"]
    return [tuple(_apply(M, p)[i] + t[i] for i in range(3)) for p in points]
