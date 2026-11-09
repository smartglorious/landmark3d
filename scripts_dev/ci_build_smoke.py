"""CI smoke build: no network, no AI. A synthetic 40 x 25 m hall -> spec (no-AI path) -> headless Blender -> GLB.

    python scripts_dev/ci_build_smoke.py [--blender PATH]

Exits non-zero when the spec has structure errors, Blender fails, or the GLB is missing / empty / not glTF.
Runs in its own LANDMARK3D_HOME (a temp folder unless the variable is set), so it never touches real settings.
"""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("LANDMARK3D_HOME", tempfile.mkdtemp(prefix="l3d_ci_"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from landmark3d import pipeline, settings  # noqa: E402
from landmark3d.engine import runner  # noqa: E402
from landmark3d.project import Project  # noqa: E402

home = Path(os.environ["LANDMARK3D_HOME"])
upd = {"projects_dir": str(home / "projects"), "contact": "https://example.org/landmark3d-ci",
       "auto_textures": False}
if "--blender" in sys.argv:
    upd["blender_path"] = sys.argv[sys.argv.index("--blender") + 1]
settings.update(upd)
s = settings.load()
for name in list(s.get("providers", {})):         # no AI in CI, even if a CLI happens to be on the runner
    s["providers"][name]["enabled"] = False
settings.update({"providers": s.get("providers", {})})

lat, lon = 10.7725, 106.6980
prj = Project.create("CI Smoke Hall", "Test City", lat=lat, lon=lon)
dy, dx = 12.5 / 111320.0, 20.0 / (111320.0 * math.cos(math.radians(lat)))
ring = [[lon - dx, lat - dy], [lon + dx, lat - dy], [lon + dx, lat + dy], [lon - dx, lat + dy], [lon - dx, lat - dy]]
prj.write_json("input/geo/footprint.geojson", {"type": "FeatureCollection", "features": [{
    "type": "Feature", "properties": {"name": "CI Smoke Hall", "l3d_match_score": 1.0},
    "geometry": {"type": "Polygon", "coordinates": [ring]}}]})
fact = lambda v: {"value": v, "confidence": "high", "source": "ci", "method": "ci_fixture"}  # noqa: E731
prj.write_json("input/research.json", {"facts": {"main_wall_height_m": fact(12.0), "roof_form": fact("hip"),
                                                 "facade_bay_spacing_m": fact(5.0), "opening_type": fact("arch")}})

spec = pipeline.build_spec(prj)
errors = [i for i in spec.get("issues", []) if i["severity"] == "error"]
print(f"spec: {len(spec['parts'])} parts, ~{spec['estimated_triangles']} triangles, {len(errors)} errors")
if errors:
    print(json.dumps(errors, indent=1))
    sys.exit(1)

res = runner.build(prj, timeout=1500)
print(json.dumps({k: res.get(k) for k in ("ok", "triangles", "bytes", "render_engine", "seconds", "errors")}, indent=1))
glb = prj.path("output", "model.glb")
log = prj.path("logs", "blender_build.log")
ok = bool(res.get("ok")) and glb.exists() and glb.stat().st_size > 1000 and glb.read_bytes()[:4] == b"glTF"
if not ok:
    if log.exists():
        print("---- blender log (tail) ----\n" + "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]))
    sys.exit(1)
renders = res.get("renders") or []
print(f"OK: {glb} ({glb.stat().st_size} bytes), {len(renders)} renders")
out = os.environ.get("L3D_CI_OUT")
if out:                                            # CI uploads the GLB + renders as an artifact
    import shutil
    Path(out).mkdir(parents=True, exist_ok=True)
    shutil.copy2(glb, Path(out) / "model.glb")
    for r in renders:
        p = prj.root / "renders" / str(r)
        if p.is_file():
            shutil.copy2(p, Path(out) / p.name)
