"""Local web app (FastAPI). Run: python -m landmark3d serve  ->  http://127.0.0.1:8770"""
from __future__ import annotations

import hashlib
import json
import math
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
    html = (WEB / "index.html").read_text(encoding="utf-8")
    from . import installer
    if installer.vendor_ready():               # offline viewer: three.js downloaded by `landmark3d setup`
        cdn = f"https://unpkg.com/three@{installer.THREE_VERSION}/"
        html = html.replace(cdn + "build/three.module.js", "/static/vendor/three/build/three.module.js")
        html = html.replace(cdn + "examples/jsm/", "/static/vendor/three/examples/jsm/")
        html = html.replace("</head>", "<script>window.L3D_DRACO='/static/vendor/three/examples/jsm/libs/draco/';"
                                       "</script>\n</head>")
    return html


@app.get("/api/about")
def about():
    """Product and publisher details, from web/about.json (the one place to edit them)."""
    from ._version import __version__
    info = json.loads((WEB / "about.json").read_text(encoding="utf-8"))
    return dict(info, version=__version__)


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
    name = _text(body, "name").strip()
    if not name:
        raise HTTPException(400, "name required")
    lat, lon = _position(body)
    budgets = _budgets(body.get("budgets") or {}, dict(settings_mod.load()["default_budgets"]))
    prj = Project.create(name, _text(body, "location").strip(), budgets, lat, lon, _text(body, "notes"))
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
        "running": _running(pid),
        "research": prj.read_json("input/research.json"),
        "sources": prj.read_json("input/sources.json", {"sources": []})["sources"],
        "spec": sp,
        "spec_polys": _spec_polys(sp),
        "build": prj.read_json("reports/build_result.json"),
        "doctor": prj.read_json("reports/doctor.json"),
        "renders": renders,
        "has_glb": (prj.root / "output" / "model.glb").exists() and st["phases"]["build"]["status"] == "done",
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


def _running(pid: str) -> bool:
    return pid in _jobs and _jobs[pid].is_alive()


_start_lock = threading.Lock()          # check-and-start is one step: concurrent requests start one worker
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PHOTO_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
REQUEST_DECISIONS = ("approved", "changes_requested")


def _refuse_if_running(pid: str) -> None:
    if _running(pid):
        raise HTTPException(409, "Dự án đang chạy; chờ xong rồi mới sửa được.")


def _text(body: dict, key: str, default: str = "") -> str:
    v = body.get(key, default)
    if v is None:
        return default
    if not isinstance(v, str):
        raise HTTPException(422, f"{key} must be text")
    return v


def _position(body: dict):
    lat, lon = body.get("lat"), body.get("lon")
    if lat is None and lon is None:
        return None, None
    if lat is None or lon is None:
        raise HTTPException(422, "lat and lon must be given together")
    out = []
    for name, v, limit in (("lat", lat, 90), ("lon", lon, 180)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > limit:
            raise HTTPException(422, f"{name} must be a number between -{limit} and {limit}")
        out.append(float(v))
    return out[0], out[1]


def _budgets(body: dict, current: dict) -> dict:
    """Validated budget changes: positive numbers, a known compression, and target not above the hard limit."""
    out = {}
    for k, v in body.items():
        if k not in settings_mod.DEFAULT_BUDGETS:
            continue
        if k == "compression":
            if v not in ("draco", "none"):
                raise HTTPException(422, "compression must be draco or none")
        elif isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise HTTPException(422, f"{k} must be a positive number")
        out[k] = v
    merged = {**current, **out}
    for target, hard in (("triangles_target", "triangles_max"), ("bytes_target", "bytes_max")):
        if merged.get(target) is not None and merged.get(hard) is not None and merged[target] > merged[hard]:
            raise HTTPException(422, f"{target} must not exceed {hard}")
    return out


@app.post("/api/projects/{pid}/run")
def run(pid: str):
    prj = _prj(pid)
    with _start_lock:
        if _running(pid):
            return {"started": False, "reason": "already running"}
        th = threading.Thread(target=_run_bg, args=(prj,), daemon=True)
        _jobs[pid] = th
        th.start()
    return {"started": True}


@app.post("/api/projects/{pid}/reopen")
def reopen(pid: str, body: dict = Body(...)):
    prj = _prj(pid)
    phase, notes = _text(body, "phase"), _text(body, "notes")
    if _running(pid):
        raise HTTPException(409, "Dự án đang chạy, chờ xong rồi mới quay lại được.")
    try:
        return pipeline.reopen(prj, phase, notes)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/projects/{pid}/restart")
def restart(pid: str, body: dict = Body(...)):
    prj = _prj(pid)
    notes, advanced, start = _text(body, "notes"), _text(body, "advanced_prompt"), _text(body, "start", "research")
    if _running(pid):
        raise HTTPException(409, "Dự án đang chạy, chờ xong rồi mới chạy lại được.")
    try:
        pipeline.restart(prj, notes, advanced, start)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return run(pid)


@app.post("/api/projects/{pid}/approve")
def approve(pid: str, body: dict = Body(...)):
    prj = _prj(pid)
    phase, decision, notes = body.get("phase"), body.get("decision"), _text(body, "notes")
    if not isinstance(phase, str) or phase not in [p["id"] for p in PHASES]:
        raise HTTPException(422, "unknown phase")
    if decision not in REQUEST_DECISIONS:
        raise HTTPException(422, "decision must be approved or changes_requested")
    try:
        return pipeline.approve(prj, phase, decision, notes)
    except pipeline.SpecIssues as exc:
        raise HTTPException(409, "Kế hoạch còn lỗi kết cấu, chưa duyệt được: " + str(exc))
    except pipeline.ApprovalError as exc:
        raise HTTPException(409, str(exc))


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
    _refuse_if_running(pid)
    ext = Path(file.filename or "photo.jpg").suffix.lower() or ".jpg"
    if ext not in PHOTO_TYPES:
        raise HTTPException(415, "photos must be jpg, png, webp or tiff")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"photo larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(400, "empty file")
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
    pipeline.invalidate_from(prj, "spec", f"photo {rel} added: the plan is made again")
    items = pipeline.missing_items(prj)
    prj.update_state(lambda s: s.update(missing=items))
    if not items and prj.state()["phases"]["completion"]["status"] == "blocked":
        prj.set_phase("completion", "pending", "user data received")
    return {"path": rel, "missing": items}


@app.put("/api/projects/{pid}/sources/{sid}")
def put_source(pid: str, sid: str, body: dict = Body(...)):
    prj = _prj(pid)
    _refuse_if_running(pid)
    srcs = prj.read_json("input/sources.json", {"sources": []})
    for s in srcs["sources"]:
        if s.get("id") == sid:
            old = {k: s.get(k) for k in ("use", "texture_for", "world_size_m", "licence")}
            if "use" in body and body["use"] not in ("reference_and_texture", "reference_only", "texture_permitted",
                                                     "excluded"):
                raise HTTPException(422, "use must be reference_and_texture, reference_only, texture_permitted or excluded")
            for k in ("use", "texture_for", "world_size_m", "licence", "note"):
                if k in body:
                    s[k] = body[k]
            from .textures import licence_allows
            if s.get("texture_for") and s.get("use") in ("reference_and_texture", "texture_permitted")                     and not licence_allows(s.get("licence") or "unknown", bool(prj.state().get("non_commercial"))):
                raise HTTPException(422, f"licence {s.get('licence')} does not allow texture use in this project")
            if s.get("texture_for") and s.get("use") == "reference_only":
                raise HTTPException(400, "licence does not allow texture use; set use=texture_permitted after "
                                         "recording permission")
            prj.write_json("input/sources.json", srcs)
            if old != {k: s.get(k) for k in old}:
                pipeline.invalidate_from(prj, "build", f"source {sid} changed: build again")
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
    pipeline.invalidate_from(prj, "build", "texture added: build again")
    return entry


@app.delete("/api/projects/{pid}/textures/{tid}")
def delete_texture(pid: str, tid: str):
    from . import textures as textures_mod
    prj = _prj(pid)
    _refuse_if_running(pid)
    if not textures_mod.remove(prj, tid):
        raise HTTPException(404, "texture not found")
    pipeline.invalidate_from(prj, "build", "texture removed: build again")
    return {"ok": True}


@app.get("/api/projects/{pid}/layouts")
def layouts(pid: str):
    """A2: the facade layouts each part's generated texture will follow (bays, floors, openings, features)."""
    from .smarttex import genfacade
    prj = _prj(pid)
    sp = prj.read_json("spec/landmark_spec.json")
    if not sp:
        raise HTTPException(409, "no spec yet")
    return {"parts": genfacade.preview_layouts(prj, sp)}


@app.get("/api/projects/{pid}/texture_review")
def texture_review(pid: str):
    """A2.5: per part, the generated elevation, its layout, problems, references and the owner's choice."""
    from . import autotex
    return autotex.review(_prj(pid))


@app.post("/api/projects/{pid}/texture_review/{part}")
def texture_choice(pid: str, part: str, body: dict = Body(...)):
    """A2.5 per-part decision: {"mode": "regenerate" | "colour" | "generate", "note": "..."}. The texture step runs
    again (only the changed part is drawn again; the others come from the cache) and stops at A2.5."""
    from . import autotex
    prj = _prj(pid)
    _refuse_if_running(pid)
    if prj.state()["phases"]["spec_review"]["status"] != "approved":
        raise HTTPException(409, "approve the plan (A2) first")
    try:
        choice = autotex.set_override(prj, part, _text(body, "mode"), _text(body, "note"))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    pipeline.invalidate_from(prj, "textures", f"texture choice for {part}: {choice.get('mode')}")
    run(pid)
    return {"ok": True, "choice": choice}


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
    _refuse_if_running(pid)
    sp = body["spec"]
    errs = spec_mod.validate_massing({"parts": sp.get("parts", []), "materials": sp.get("materials", {})})
    if errs:
        raise HTTPException(400, "; ".join(errs[:6]))
    sp["estimated_triangles"] = spec_mod.estimate_triangles(sp)
    sp["issues"] = spec_mod.check_structure(sp["parts"], [tuple(p) for p in sp["footprint"]["local"]],
                                            pipeline.merged_facts(prj)) + pipeline.review_issues(sp)
    prj.write_json("spec/landmark_spec.json", sp)
    prj.log("spec edited by the user")
    pipeline.invalidate_from(prj, "spec_review", "plan edited: approve it again")
    return {"ok": True, "estimated_triangles": sp["estimated_triangles"], "issues": sp["issues"]}


def _photo_sources(prj: Project) -> dict:
    return {s["id"]: s for s in prj.read_json("input/sources.json", {"sources": []})["sources"]}


@app.get("/api/projects/{pid}/observation_review")
def observation_review(pid: str):
    """What A2 needs for the facade step: each facade part with its outline sides, each photo with its part, the
    observations per photo, and the grid the build will use."""
    from . import corrections as corr_mod, facade_grid, observe as observe_mod
    prj = _prj(pid)
    spec = prj.read_json("spec/landmark_spec.json")
    if not spec:
        raise HTTPException(409, "no plan yet: build the plan first")
    main = [tuple(p) for p in spec["footprint"]["local"]]
    from . import geom
    corr = corr_mod.load(prj)
    parts = []
    for part in spec["parts"]:
        if not part.get("facade"):
            continue
        poly = geom.footprint_polygon(part["footprint"], main)
        parts.append({"id": part["id"], "sides": facade_grid.sides(poly), "correction": corr["parts"].get(part["id"], {}),
                      "observed_grid": part.get("observed_grid")})
    sources = _photo_sources(prj)
    obs_doc = observe_mod.load(prj) or {"observations": []}
    photos = []
    for rel in spec.get("photos", []):
        src = next((s for s in sources.values() if s.get("path") == rel and s.get("kind") == "photo"), None)
        sid = src["id"] if src else None
        mine = [o for o in obs_doc["observations"] if sid and str(o.get("id", "")).split("/")[0] == sid]
        photos.append({"path": rel, "source_id": sid,
                       "part": corr.get("photo_parts", {}).get(sid) if sid else None,
                       "side": corr.get("photo_sides", {}).get(sid) if sid else None,
                       "observations": [{"confidence": o.get("confidence"), "floors": len(o.get("floor_lines", [])) - 1,
                                         "bays": len(o.get("bay_lines", [])) - 1} for o in mine]})
    return {"ai_enabled": bool(settings_mod.load().get("facade_observations", False)),
            "parts": parts, "photos": photos, "part_ids": [p["id"] for p in spec["parts"]],
            "grid_report": spec.get("observed_grid")}


@app.put("/api/projects/{pid}/observation_corrections")
def put_observation_corrections(pid: str, body: dict = Body(...)):
    """Save what the person set at A2 (sides, floor and bay lines, photo-to-part), then redo the grid from them."""
    from . import corrections as corr_mod, facade_grid, observe as observe_mod
    prj = _prj(pid)
    _refuse_if_running(pid)
    spec = prj.read_json("spec/landmark_spec.json")
    if not spec:
        raise HTTPException(409, "no plan yet: build the plan first")
    doc = {"parts": body.get("parts", {}), "photo_parts": body.get("photo_parts", {}),
           "photo_sides": body.get("photo_sides", {})}
    part_ids = {p["id"] for p in spec["parts"]}
    errors = corr_mod.save(prj, doc, part_ids)
    if errors:
        raise HTTPException(400, "; ".join(errors[:6]))
    obs = (observe_mod.load(prj) or {"observations": []})["observations"]
    report = facade_grid.reapply(spec, obs, doc)
    prj.write_json("spec/landmark_spec.json", spec)
    prj.log("observation corrections saved")
    pipeline.invalidate_from(prj, "spec_review", "facade corrections changed: approve the plan again")
    return {"ok": True, "grid": report}


@app.post("/api/projects/{pid}/observations/run")
def run_observations(pid: str):
    """Ask the AI to read the photos of this project (A2 button). Uses the photo-to-part choices already saved."""
    from . import corrections as corr_mod, facade_grid, observe as observe_mod
    prj = _prj(pid)
    _refuse_if_running(pid)
    if not settings_mod.load().get("facade_observations", False):
        raise HTTPException(409, "turn on facade observations in Settings first")
    spec = prj.read_json("spec/landmark_spec.json")
    if not spec or not spec.get("photos"):
        raise HTTPException(409, "no photos in this plan")
    provider = ai.get("research")
    if provider is None:
        raise HTTPException(409, "no AI provider is available (sign in to the AI in Connections)")
    sources = _photo_sources(prj)
    corr = corr_mod.load(prj)
    part_for = {sid: pid_ for sid, pid_ in corr.get("photo_parts", {}).items()}
    log, errors = observe_mod.run_for_photos(prj, provider, spec["name"], spec["photos"], sources,
                                             {p["id"] for p in spec["parts"]}, part_for=part_for)
    obs = (observe_mod.load(prj) or {"observations": []})["observations"]
    report = facade_grid.reapply(spec, obs, corr)
    prj.write_json("spec/landmark_spec.json", spec)
    prj.log("facade observations run from A2")
    pipeline.invalidate_from(prj, "spec_review", "facade observations changed: approve the plan again")
    return {"log": log, "errors": errors, "grid": report}


@app.get("/api/projects/{pid}/cameras")
def cameras_list(pid: str):
    """Smart Texture T3: the texture photos with the state of their cameras."""
    from .smarttex import run as st_run
    return st_run.editor_list(_prj(pid))


@app.get("/api/projects/{pid}/cameras/{k}")
def camera_photo(pid: str, k: int):
    """Editor view of photo k: undistorted image, wireframe and corners of the solved camera, hand-placed points,
    a numbered model sketch, and the checks."""
    from .smarttex import run as st_run
    prj = _prj(pid)
    if not prj.read_json("spec/landmark_spec.json"):
        raise HTTPException(409, "no plan yet: build the plan first")
    try:
        return st_run.editor_photo(prj, k)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.put("/api/projects/{pid}/cameras/{k}")
def camera_points(pid: str, k: int, body: dict = Body(...)):
    """Save hand-placed corner points for photo k ({"points": {keypoint id: [u, v]}}, undistorted pixels) and solve
    its camera from them. An empty set clears the hand-placed points."""
    from .smarttex import run as st_run
    prj = _prj(pid)
    _refuse_if_running(pid)
    pts = body.get("points") or {}
    if not isinstance(pts, dict) or any(not isinstance(v, (list, tuple)) or len(v) != 2 for v in pts.values()):
        raise HTTPException(400, "points must be {keypoint id: [x, y]}")
    try:
        return st_run.editor_solve(prj, k, {kid: (float(u), float(v)) for kid, (u, v) in pts.items()})
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.post("/api/projects/{pid}/cameras/{k}/auto")
def camera_auto(pid: str, k: int):
    """The AI places the corner points of photo k itself (rough, then refined in zoomed crops) and the camera is
    solved from them. The points stay editable."""
    from .smarttex import run as st_run
    prj = _prj(pid)
    _refuse_if_running(pid)
    provider = ai.get("vision")
    if provider is None:
        raise HTTPException(409, "no AI provider is available (sign in to the AI in Connections)")
    try:
        return st_run.auto_place(prj, k, provider)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.put("/api/projects/{pid}/budgets")
def put_budgets(pid: str, body: dict = Body(...)):
    prj = _prj(pid)
    _refuse_if_running(pid)
    before = dict(prj.state()["budgets"])
    changes = _budgets(body, before)
    st = prj.update_state(lambda s: s["budgets"].update(changes))
    if st["budgets"] != before:
        pipeline.invalidate_from(prj, "spec_review", "budgets changed: approve the plan again")
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
