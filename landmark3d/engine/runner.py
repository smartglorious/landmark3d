"""Launch the headless Blender build for a project and collect the result."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .. import doctor
from ..project import Project

SCRIPT = Path(__file__).with_name("blender_build.py")


def texture_jobs(prj: Project) -> dict:
    """Photos the user assigned to a material (sources.json: texture_for), only if the licence allows it."""
    out = {}
    srcs = prj.read_json("input/sources.json", {"sources": []})
    changed = False
    for s in srcs["sources"]:
        mat = s.get("texture_for")
        if not mat or s.get("kind") != "photo":
            continue
        if s.get("use") not in ("reference_and_texture", "texture_permitted"):
            continue
        out[mat] = {"file": str(prj.root / s["path"]), "world_size_m": float(s.get("world_size_m", 3.0))}
        if not s.get("used_as_texture"):
            s["used_as_texture"] = True
            changed = True
    if changed:
        prj.write_json("input/sources.json", srcs)
    return out


def photo_views(spec: dict) -> list[dict]:
    """Camera directions of the reference photos (from the AI massing / photo review), for like-for-like renders."""
    out, seen = [], set()
    for v in spec.get("views") or []:
        try:
            n = int(v.get("photo"))
            az, el = float(v["azimuth_deg"]), float(v.get("elevation_deg", 10))
        except (TypeError, ValueError, KeyError):
            continue
        if n not in seen and len(out) < 6:
            seen.add(n)
            ent = {"photo": n, "azimuth_deg": az, "elevation_deg": el}
            if v.get("focus") and any(p.get("id") == v["focus"] for p in spec.get("parts") or []):
                ent["focus"] = v["focus"]
            out.append(ent)
    return out


def _run_blender(cmd: list[str], timeout: int) -> tuple[int, str]:
    """Run Blender headless. Returns (exit code, log); the log always says how Blender ended when it failed."""
    p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    log = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        log += f"\n[landmark3d] Blender exited with code {_exit_code(p.returncode)}\n"
    return p.returncode, log


def _exit_code(code: int) -> str:
    return f"{code} (0x{code & 0xffffffff:08X})"


def preview(prj: Project, spec: dict, views: dict, out_dir: Path, timeout: int = 600) -> list[Path]:
    """Quick renders of a candidate spec from given camera directions (photo review). Returns the PNG paths.
    Only renders made by this call are returned: a failed run returns [], never the previous run's images."""
    exe = doctor.find_blender()
    if not exe:
        return []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in views:
        (out_dir / f"{name}.png").unlink(missing_ok=True)
    (out_dir / "preview_result.json").unlink(missing_ok=True)
    job = {"spec": spec, "textures": {}, "panels": [], "panel_atlas": None, "preview": True,
           "views": list(views), "custom_views": views, "resolution": [960, 640],
           "out": {"glb": str(out_dir / "preview.glb"), "blend": str(out_dir / "preview.blend"),
                   "renders": str(out_dir), "result": str(out_dir / "preview_result.json")}}
    job_path = out_dir / "preview_job.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")
    cmd = [exe, "-b", "--factory-startup", "--python", str(SCRIPT), "--", str(job_path)]
    try:
        code, log = _run_blender(cmd, timeout)
    except subprocess.TimeoutExpired:
        prj.log("blender preview failed", {"dir": out_dir.name, "error": f"timed out after {timeout} s"})
        return []
    (out_dir / "preview.log").write_text(log, encoding="utf-8")
    if code != 0:
        prj.log("blender preview failed", {"dir": out_dir.name, "exit_code": _exit_code(code), "log": log[-300:]})
        return []
    return [out_dir / f"{name}.png" for name in views if (out_dir / f"{name}.png").exists()]


def build(prj: Project, timeout: int = 1800) -> dict:
    exe = doctor.find_blender()
    if not exe:
        return {"ok": False, "errors": ["Blender not found (run the environment check)"]}
    spec = prj.read_json("spec/landmark_spec.json")
    if not spec:
        return {"ok": False, "errors": ["no spec"]}
    from .. import textures as textures_mod
    repeat, panels, atlas = textures_mod.build_job(prj, int(spec["budgets"].get("texture_max_px", 2048)))
    texture_jobs(prj)                                      # marks research photos used as textures (attribution)
    job = {
        "spec": spec,
        "textures": repeat,
        "panels": panels,
        "panel_atlas": atlas,
        "views": ["front_south", "oblique_sw", "oblique_ne", "east", "west", "north", "aerial", "plan"]
                 + [f"photo{int(v['photo'])}" for v in photo_views(spec)],
        "custom_views": {f"photo{int(v['photo'])}": v for v in photo_views(spec)},
        "out": {"glb": str(prj.path("output", "model.glb")), "blend": str(prj.path("blender", "model.blend")),
                "renders": str(prj.root / "renders"), "result": str(prj.path("reports", "build_result.json"))},
    }
    job_path = prj.write_json("logs/build_job.json", job)
    cmd = [exe, "-b", "--factory-startup", "--python", str(SCRIPT), "--", str(job_path)]
    prj.path("reports", "build_result.json").unlink(missing_ok=True)     # a failed build must not report the last one
    try:
        code, log = _run_blender(cmd, timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "errors": [f"Blender build timed out after {timeout} s"]}
    prj.path("logs", "blender_build.log").write_text(log, encoding="utf-8")
    res = prj.read_json("reports/build_result.json")
    tail = "\n".join(log.splitlines()[-25:])[-1500:]
    if code != 0:
        return {"ok": False, "errors": [f"Blender exited with code {_exit_code(code)}", *(res or {}).get("errors", []), tail]}
    if not res:
        return {"ok": False, "errors": ["Blender did not produce a result", tail]}
    return res
