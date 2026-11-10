"""Photogrammetry adapter contract (plan M7, tasks 2-3). Optional: without COLMAP or Meshroom the standard procedural
workflow runs unchanged, and the status says so (no silent default, no fake reconstruction).

The adapter does not run the reconstruction itself here. It defines what an external output folder must contain, checks
it, and aligns its points to project coordinates with the footprint and a known height. Fitting planes and component
anchors (task 4) and using dense geometry as bake source (task 5) are not implemented yet.

Output folder contract (`manifest.json` plus the files it names):
  {"adapter": "colmap" | "meshroom", "adapter_version": "...", "units": "m",
   "cameras": [{"image": "name.jpg", "R": [[..3..]x3], "t": [x, y, z]}, ...],
   "points": "points.ply", "mesh": "mesh.ply"}
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

ADAPTERS = {"colmap": "colmap", "meshroom": "meshroom_batch"}      # adapter name -> executable looked for on PATH
MIN_CAMERAS = 8
SCALE_RANGE = (0.2, 5.0)          # a reconstruction scaled further than this is not trusted without a person


def detect() -> dict:
    """Which adapters have their executable on this machine. Nothing is run."""
    out = {}
    for name, exe in ADAPTERS.items():
        path = shutil.which(exe)
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
    if m.get("units") != "m":
        errors.append("units must be metres")
    cams = m.get("cameras") or []
    if len(cams) < MIN_CAMERAS:
        errors.append(f"{len(cams)} cameras; at least {MIN_CAMERAS} are needed")
    for c in cams:
        R, t = c.get("R"), c.get("t")
        if not (isinstance(R, list) and len(R) == 3 and all(isinstance(r, list) and len(r) == 3 for r in R)
                and isinstance(t, list) and len(t) == 3):
            errors.append(f"camera {c.get('image', '?')}: R must be 3x3 and t must have 3 values")
            break
    for key in ("points", "mesh"):
        name = m.get(key)
        if not name or not (folder / name).is_file():
            errors.append(f"{key} file {name!r} is missing")
    return errors


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
    return {"scale": round(scale, 6), "translate": [round(dst[0] - src[0] * scale, 6), round(dst[1] - src[1] * scale, 6),
                                                    round(-min(zs) * scale, 6)]}
