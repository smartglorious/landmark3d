"""Project folder, persisted state machine, event log."""
from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
import unicodedata
from pathlib import Path

from . import settings as settings_mod

_run_locks: dict[str, threading.Lock] = {}
_run_locks_guard = threading.Lock()


def _try_file_lock(path: Path):
    """An OS-level lock on a file, held by one process at a time. The OS releases it when the process ends, so a
    crash cannot leave a project locked for good. Returns the open file when locked, else None."""
    fh = open(path, "a+")
    try:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        fh.close()
        return None


def _release_file_lock(fh) -> None:
    try:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()

# Phase order. approval=True phases stop the run until the user decides.
PHASES = [
    {"id": "setup", "title": "Project set-up", "approval": False},
    {"id": "doctor", "title": "Environment check (Blender, MCP, tools)", "approval": False},
    {"id": "research", "title": "Research (structured sources first, then AI)", "approval": False},
    {"id": "sources_review", "title": "A1 - Sources and licences", "approval": True},
    {"id": "completion", "title": "Input gate (missing data from the user)", "approval": False},
    {"id": "spec", "title": "Massing spec (parts, heights, roofs, facades, materials)", "approval": False},
    {"id": "spec_review", "title": "A2 - Plan and massing", "approval": True},
    {"id": "textures", "title": "Facade textures (generated elevations, photo fallback, swatches)", "approval": False},
    {"id": "texture_review", "title": "A2.5 - Texture review", "approval": True},
    {"id": "build","title": "Blender build, export, QA, renders", "approval": False},
    {"id": "build_review", "title": "A3 - Model review", "approval": True},
    {"id": "release", "title": "Release files (report, checksums)", "approval": False},
    {"id": "release_review", "title": "A6 - Release approval", "approval": True},
]
PHASE_IDS = [p["id"] for p in PHASES]

# status: pending | running | done | failed | blocked (needs user data) | waiting_approval | approved | changes_requested

_locks: dict[str, threading.RLock] = {}


def _with_all_phases(st: dict) -> dict:
    """Projects made before a phase existed get it: already passed (done / approved) when a later phase has run,
    else pending. Example: "textures" and "texture_review" (A2.5) were added between A2 and the build."""
    ph = st.setdefault("phases", {})
    for k, p in enumerate(PHASES):
        if p["id"] in ph:
            continue
        later = any(ph.get(q["id"], {}).get("status", "pending") != "pending" for q in PHASES[k + 1:])
        ph[p["id"]] = {"status": ("approved" if p["approval"] else "done") if later else "pending", "updated": None,
                       "message": "added by an update of the tool" if later else ""}
    return st


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "landmark"


def projects_dir() -> Path:
    p = Path(settings_mod.load()["projects_dir"])
    p.mkdir(parents=True, exist_ok=True)
    return p


class Project:
    def __init__(self, pid: str):
        self.id = pid
        self.root = projects_dir() / pid
        self.state_file = self.root / "project.json"

    # ------------------------------------------------------------------ layout
    @property
    def input(self) -> Path:
        return self.root / "input"

    def path(self, *parts) -> Path:
        p = self.root.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # ------------------------------------------------------------------ create / load
    @classmethod
    def create(cls, name: str, location: str, budgets: dict | None = None, lat: float | None = None,
               lon: float | None = None, notes: str = "") -> "Project":
        base = slugify(name)
        pid, k = base, 2
        while (projects_dir() / pid).exists():
            pid, k = f"{base}-{k}", k + 1
        prj = cls(pid)
        for sub in ("input/photos", "input/geo", "input/measurements", "input/observations", "input/textures",
                    "spec", "output", "renders", "reports", "logs", "blender"):
            (prj.root / sub).mkdir(parents=True, exist_ok=True)
        s = settings_mod.load()
        state = {
            "id": pid,
            "name": name,
            "location": location,
            "lat": lat,
            "lon": lon,
            "notes": notes,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "budgets": dict(s["default_budgets"], **(budgets or {})),
            "texture_policy": "reference_and_texture_if_licence_allows",
            "phases": {p["id"]: {"status": "pending", "updated": None, "message": ""} for p in PHASES},
            "approvals": [],
            "missing": [],
            "user_inputs": {},
        }
        prj.save_state(state)
        prj.log("project created", {"name": name, "location": location})
        return prj

    def exists(self) -> bool:
        return self.state_file.exists()

    def state(self) -> dict:
        with self.lock():
            for attempt in range(20):
                try:
                    return _with_all_phases(json.loads(self.state_file.read_text(encoding="utf-8")))
                except (PermissionError, json.JSONDecodeError):
                    # Windows: the file can be briefly locked while another process replaces it
                    time.sleep(0.05 * (attempt + 1))
            return _with_all_phases(json.loads(self.state_file.read_text(encoding="utf-8")))

    def save_state(self, state: dict) -> None:
        with self.lock():
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
            for attempt in range(20):
                try:
                    tmp.replace(self.state_file)
                    return
                except PermissionError:
                    time.sleep(0.05 * (attempt + 1))
            tmp.replace(self.state_file)

    def lock(self) -> threading.RLock:
        return _locks.setdefault(self.id, threading.RLock())

    @contextlib.contextmanager
    def run_lock(self, blocking: bool = True):
        """Exclusive right to run the pipeline on this project: one thread in this process and one process overall
        (the web app, the CLI). Yields True when held. Non-blocking callers get False at once when it is taken."""
        with _run_locks_guard:
            tl = _run_locks.setdefault(self.id, threading.Lock())
        got, fh = False, None
        while True:
            if tl.acquire(blocking=False):
                fh = _try_file_lock(self.path("logs", "run.lock"))
                if fh is not None:
                    got = True
                    break
                tl.release()
            if not blocking:
                break
            time.sleep(0.05)
        try:
            yield got
        finally:
            if got:
                _release_file_lock(fh)
                tl.release()

    def update_state(self, fn) -> dict:
        with self.lock():
            st = self.state()
            fn(st)
            self.save_state(st)
            return st

    def set_phase(self, phase: str, status: str, message: str = "") -> None:
        def f(st):
            st["phases"][phase] = {"status": status, "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                   "message": message}
        self.update_state(f)
        self.log(f"phase {phase}: {status}", {"message": message} if message else None)

    # ------------------------------------------------------------------ json helpers
    def read_json(self, rel: str, default=None):
        p = self.root / rel
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))

    def write_json(self, rel: str, data) -> Path:
        p = self.path(rel)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return p

    # ------------------------------------------------------------------ log
    def log(self, message: str, data=None) -> None:
        line = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "msg": message}
        if data is not None:
            line["data"] = data
        with open(self.path("logs", "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    def events(self, limit: int = 200) -> list:
        p = self.root / "logs" / "events.jsonl"
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(x) for x in lines if x.strip()]


def list_projects() -> list[dict]:
    out = []
    for d in sorted(projects_dir().iterdir()):
        if (d / "project.json").exists():
            st = json.loads((d / "project.json").read_text(encoding="utf-8"))
            current = next((p for p in PHASE_IDS if st["phases"][p]["status"] not in ("done", "approved")), None)
            out.append({"id": st["id"], "name": st["name"], "location": st["location"], "current": current,
                        "status": st["phases"][current]["status"] if current else "complete"})
    return out
