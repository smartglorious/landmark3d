"""Local web app (FastAPI). Run: python -m landmark3d serve  ->  http://127.0.0.1:8770"""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import doctor, pipeline, settings as settings_mod, spec as spec_mod
from .project import PHASES, Project, list_projects
from .research import ai

WEB = Path(__file__).with_name("web")
app = FastAPI(title="Landmark3D")
app.mount("/static", StaticFiles(directory=WEB), name="static")
_jobs: dict[str, threading.Thread] = {}


def _prj(pid: str) -> Project:
    p = Project(pid)
    if not p.exists():
        raise HTTPException(404, "project not found")
    return p


@app.get("/", response_class=HTMLResponse)
def index():
    return (WEB / "index.html").read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- settings / connections
@app.get("/api/settings")
def get_settings():
    s = settings_mod.load()
    s["phases"] = PHASES
    return s


@app.put("/api/settings")
def put_settings(patch: dict = Body(...)):
    allowed = {"projects_dir", "blender_path", "routing", "cross_check", "default_budgets", "contact"}
    return settings_mod.update({k: v for k, v in patch.items() if k in allowed})


def _check_name(name: str):
    if name not in ai.NAMES:
        raise HTTPException(404, "unknown provider")


@app.get("/api/providers")
def providers_status():
    """How each AI can connect (CLI web sign-in / environment variables). Variable names only, never values."""
    return {n: ai.status(n) for n in ai.NAMES}


@app.put("/api/providers/{name}")
def put_provider(name: str, body: dict = Body(...)):
    _check_name(name)
    # cli_path is deliberately not settable from the web UI (it names an executable to run); edit settings.json.
    patch = {k: body[k] for k in ("enabled", "via", "cli_model", "model", "vision_model") if k in body}
    if patch.get("via", "auto") not in ("auto", "cli", "env"):
        raise HTTPException(400, "via must be auto, cli or env")
    for k in ("cli_model", "model", "vision_model"):
        if patch.get(k) and not ai.MODEL_RE.match(patch[k]):
            raise HTTPException(400, f"invalid {k}")
    settings_mod.update({"providers": {name: patch}})
    return ai.status(name)


@app.post("/api/providers/{name}/login")
def provider_login(name: str):
    """Start the CLI's browser sign-in (Claude account / ChatGPT account) in its own window."""
    _check_name(name)
    try:
        return ai.start_login(name)
    except ai.AIError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/providers/{name}/test")
def test_provider(name: str):
    _check_name(name)
    try:
        return ai.make(name).test()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/doctor")
def get_doctor():
    return doctor.run_all()


@app.post("/api/doctor/install_validator")
def install_validator():
    return doctor.install_validator()


# ----------------------------------------------------------------------------- projects
@app.get("/api/projects")
def projects():
    return list_projects()


@app.post("/api/projects")
def create_project(body: dict = Body(...)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    prj = Project.create(name, (body.get("location") or "").strip(), body.get("budgets"),
                         body.get("lat"), body.get("lon"), body.get("notes", ""))
    return {"id": prj.id}


@app.get("/api/projects/{pid}")
def project(pid: str):
    prj = _prj(pid)
    st = prj.state()
    sp = prj.read_json("spec/landmark_spec.json")
    renders = sorted(p.name for p in (prj.root / "renders").glob("*.png"))
    return {
        "state": st,
        "phases": PHASES,
        "running": pid in _jobs and _jobs[pid].is_alive(),
        "research": prj.read_json("input/research.json"),
        "sources": prj.read_json("input/sources.json", {"sources": []})["sources"],
        "spec": sp,
        "spec_polys": _spec_polys(sp),
        "build": prj.read_json("reports/build_result.json"),
        "doctor": prj.read_json("reports/doctor.json"),
        "renders": renders,
        "has_glb": (prj.root / "output" / "model.glb").exists(),
        "events": prj.events(60),
    }


def _spec_polys(sp) -> dict:
    """Plan polygons of every part (rounded shapes included) for the A2 plan drawing."""
    if not sp:
        return {}
    from . import geom
    main = [tuple(p) for p in sp["footprint"]["local"]]
    out = {}
    for p in sp.get("parts", []):
        try:
            out[p["id"]] = [[round(x, 2), round(y, 2)] for x, y in geom.footprint_polygon(p["footprint"], main)]
        except Exception:
            pass
    return out


def _run_bg(prj: Project):
    try:
        pipeline.run_until_blocked(prj)
    finally:
        _jobs.pop(prj.id, None)


@app.post("/api/projects/{pid}/run")
def run(pid: str):
    prj = _prj(pid)
    if pid in _jobs and _jobs[pid].is_alive():
        return {"started": False, "reason": "already running"}
    th = threading.Thread(target=_run_bg, args=(prj,), daemon=True)
    _jobs[pid] = th
    th.start()
    return {"started": True}


@app.post("/api/projects/{pid}/approve")
def approve(pid: str, body: dict = Body(...)):
    prj = _prj(pid)
    if prj.state()["phases"].get(body["phase"], {}).get("status") != "waiting_approval":
        raise HTTPException(409, "this phase is not waiting for approval")
    try:
        return pipeline.approve(prj, body["phase"], body["decision"], body.get("notes", ""))
    except pipeline.SpecIssues as exc:
        raise HTTPException(409, "Kế hoạch còn lỗi kết cấu, chưa duyệt được: " + str(exc))


@app.post("/api/projects/{pid}/inputs")
def inputs(pid: str, body: dict = Body(...)):
    try:
        return {"missing": pipeline.submit_inputs(_prj(pid), body.get("values", {}))}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/projects/{pid}/photos")
async def upload_photo(pid: str, file: UploadFile = File(...), licence: str = Form("owned"),
                       author: str = Form(""), note: str = Form("")):
    prj = _prj(pid)
    srcs = prj.read_json("input/sources.json", {"sources": []})
    n = sum(1 for s in srcs["sources"] if s.get("origin") == "user") + 1
    ext = Path(file.filename or "photo.jpg").suffix.lower() or ".jpg"
    data = file.file.read()
    digest = hashlib.sha256(data).hexdigest()
    for s in srcs["sources"]:
        f = prj.root / s.get("path", "")
        if s.get("kind") == "photo" and f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest() == digest:
            return {"path": s["path"], "duplicate_of": s["id"], "missing": pipeline.missing_items(prj)}
    rel = f"input/photos/user_{n:02d}{ext}"
    prj.path(*rel.split("/")).write_bytes(data)
    texture_ok = licence in ("owned", "permission_granted", "public_domain", "cc_by", "cc_by_sa")
    srcs["sources"].append({"id": f"SRC-U{n:02d}", "kind": "photo", "path": rel, "title": file.filename,
                            "author": author, "licence": licence, "note": note, "origin": "user",
                            "use": "reference_and_texture" if texture_ok else "reference_only"})
    prj.write_json("input/sources.json", srcs)
    items = pipeline.missing_items(prj)
    prj.update_state(lambda s: s.update(missing=items))
    if not items and prj.state()["phases"]["completion"]["status"] == "blocked":
        prj.set_phase("completion", "pending", "user data received")
    return {"path": rel, "missing": items}


@app.put("/api/projects/{pid}/sources/{sid}")
def put_source(pid: str, sid: str, body: dict = Body(...)):
    prj = _prj(pid)
    srcs = prj.read_json("input/sources.json", {"sources": []})
    for s in srcs["sources"]:
        if s.get("id") == sid:
            for k in ("use", "texture_for", "world_size_m", "licence", "note"):
                if k in body:
                    s[k] = body[k]
            if s.get("texture_for") and s.get("use") == "reference_only":
                raise HTTPException(400, "licence does not allow texture use; set use=texture_permitted after "
                                         "recording permission")
            prj.write_json("input/sources.json", srcs)
            return s
    raise HTTPException(404, "source not found")


@app.post("/api/projects/{pid}/satellite")
async def upload_satellite(pid: str, file: UploadFile = File(...), note: str = Form("")):
    """A satellite screenshot the user supplies (north up). Reference only."""
    from .research import satellite
    prj = _prj(pid)
    try:
        return satellite.save_upload(prj, await file.read(), file.filename or "satellite.jpg", note)
    except Exception as exc:
        raise HTTPException(400, f"not a readable image: {exc}")


@app.post("/api/projects/{pid}/satellite/refresh")
def refresh_satellite(pid: str):
    """Download the Esri World Imagery plan view again (with the outline and grid drawn on)."""
    from .research import agent, satellite
    prj = _prj(pid)
    main, origin = agent.load_footprint(prj)
    if main is None:
        raise HTTPException(409, "no footprint yet")
    entry = satellite.ensure(prj, main, origin, force=True)
    if not entry:
        raise HTTPException(502, "satellite imagery unavailable (provider disabled or download failed - see the log)")
    return entry


@app.get("/api/projects/{pid}/textures")
def get_textures(pid: str):
    from . import textures as textures_mod
    return textures_mod.load(_prj(pid))


@app.post("/api/projects/{pid}/textures")
async def add_texture(pid: str, file: UploadFile | None = File(None), source_id: str = Form(""),
                      licence: str = Form("owned"), author: str = Form(""), mode: str = Form("repeat"),
                      material: str = Form("wall"), world_size_m: float = Form(3.0), tint: str = Form(""),
                      part: str = Form(""), side: str = Form("south"), z0: str = Form(""), z1: str = Form(""),
                      crop: str = Form("")):
    from . import textures as textures_mod
    prj = _prj(pid)
    tint_v = None
    if tint.strip():
        from .pipeline import parse_colour
        tint_v = parse_colour(tint)
        if tint_v is None:
            raise HTTPException(400, "tint must be #rrggbb")
    crop_v = None
    if crop.strip():
        try:
            crop_v = [float(x) for x in crop.replace(";", ",").split(",")]
            assert len(crop_v) == 4 and 0 <= crop_v[0] < crop_v[2] <= 1 and 0 <= crop_v[1] < crop_v[3] <= 1
        except Exception:
            raise HTTPException(400, "crop must be x0,y0,x1,y1 as fractions 0..1 (top-left origin)")
    try:
        data = await file.read() if file is not None else None
        entry = textures_mod.add(prj, file_bytes=data, filename=file.filename if file else None,
                                 source_id=source_id or None, licence=licence, author=author, mode=mode,
                                 material=material or None, world_size_m=world_size_m, tint=tint_v,
                                 part=part or None, side=side or None, z0=float(z0) if z0 else None,
                                 z1=float(z1) if z1 else None, crop=crop_v)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return entry


@app.delete("/api/projects/{pid}/textures/{tid}")
def delete_texture(pid: str, tid: str):
    from . import textures as textures_mod
    if not textures_mod.remove(_prj(pid), tid):
        raise HTTPException(404, "texture not found")
    return {"ok": True}


@app.post("/api/projects/{pid}/rebuild")
def rebuild(pid: str):
    """Re-run the build (and the phases after it) with the current spec and textures."""
    prj = _prj(pid)
    st = prj.state()
    if st["phases"]["spec_review"]["status"] != "approved":
        raise HTTPException(409, "approve the plan (A2) first")
    ids = [p["id"] for p in PHASES]
    for p in ids[ids.index("build"):]:
        prj.set_phase(p, "pending", "rebuild requested")
    return run(pid)


@app.put("/api/projects/{pid}/spec")
def put_spec(pid: str, body: dict = Body(...)):
    prj = _prj(pid)
    sp = body["spec"]
    errs = spec_mod.validate_massing({"parts": sp.get("parts", []), "materials": sp.get("materials", {})})
    if errs:
        raise HTTPException(400, "; ".join(errs[:6]))
    sp["estimated_triangles"] = spec_mod.estimate_triangles(sp)
    sp["issues"] = spec_mod.check_structure(sp["parts"], [tuple(p) for p in sp["footprint"]["local"]],
                                            pipeline.merged_facts(prj))
    prj.write_json("spec/landmark_spec.json", sp)
    prj.log("spec edited by the user")
    return {"ok": True, "estimated_triangles": sp["estimated_triangles"], "issues": sp["issues"]}


@app.put("/api/projects/{pid}/budgets")
def put_budgets(pid: str, body: dict = Body(...)):
    prj = _prj(pid)
    keys = set(settings_mod.DEFAULT_BUDGETS)
    st = prj.update_state(lambda s: s["budgets"].update({k: v for k, v in body.items() if k in keys}))
    sp = prj.read_json("spec/landmark_spec.json")
    if sp:
        sp["budgets"] = st["budgets"]
        prj.write_json("spec/landmark_spec.json", sp)
    return st["budgets"]


@app.post("/api/projects/{pid}/reset/{phase}")
def reset(pid: str, phase: str):
    prj = _prj(pid)
    ids = [p["id"] for p in PHASES]
    if phase not in ids:
        raise HTTPException(404, "unknown phase")
    for p in ids[ids.index(phase):]:
        prj.set_phase(p, "pending", "reset by user")
    return {"ok": True}


@app.get("/files/{pid}/{path:path}")
def files(pid: str, path: str):
    prj = _prj(pid)
    target = (prj.root / path).resolve()
    if prj.root.resolve() not in target.parents or not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(target)


def serve(host: str = "127.0.0.1", port: int = 8770):
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")
