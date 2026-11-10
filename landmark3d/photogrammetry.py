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
