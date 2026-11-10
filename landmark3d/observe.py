"""M3: the AI step that proposes facade observations from one rectified-or-not photo.

The model answers with a JSON document in the schema of observations.py. The answer is validated, and retried with the
errors if it is not valid (ai.ask_json). Nothing is written unless the whole document validates. The step is a function
only: the pipeline does not call it yet, so it spends no quota until it is wired and approved.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import observations as obs_mod
from .research import ai

SYSTEM = "You describe building facades from photographs. Reply with one JSON object only."

PROMPT = """Building: {name}. The attached photo shows part of the building.

Describe the facade that faces the camera, for the part "{part_id}", as one observation in this JSON form:
{{"version": 1, "observations": [{{
  "id": "FO-1", "source_id": "{source_id}", "part_id": "{part_id}", "facade_run": 0,
  "quad": [[x,y],[x,y],[x,y],[x,y]],
  "floor_lines": [...], "bay_lines": [...],
  "elements": [{{"id": "FE-1", "type": "arched_window", "bbox": [x0,y0,x1,y1], "floor": 0, "bay": 0,
                 "symmetry_group": "...", "attributes": {{}}, "confidence": 0.0, "provenance": "observed"}}],
  "confidence": 0.0}}]}}

Rules:
- All coordinates are normalized photo coordinates from 0 to 1, origin at the top-left.
- quad: the four corners of the facade in the photo (top-left, top-right, bottom-right, bottom-left).
- floor_lines and bay_lines: strictly ordered lines, one per floor edge and bay edge.
- Only list elements you can see clearly. A bbox must have positive area and lie inside the quad.
- provenance is "observed" for everything you see. Never guess hidden elements.
- confidence is your certainty from 0 to 1. Keep it low when the view is oblique, the facade is partly hidden or
  the light is poor.
- Use element types such as arched_window, rect_window, storefront, entrance_portal, colonnade, balcony, pediment, clock.
"""


def build_prompt(name: str, part_id: str, source_id: str) -> str:
    return PROMPT.format(name=name, part_id=part_id, source_id=source_id)


def propose(provider, name: str, part_id: str, source_id: str, photo: Path, source_record: dict, parts: set) -> dict:
    """One validated observation document for one photo. Raises AIError when no valid answer comes back."""
    sources = {source_id: source_record}

    def check(doc):
        return obs_mod.validate(doc, sources, parts)
    doc, _res = ai.ask_json(provider, SYSTEM, build_prompt(name, part_id, source_id), validator=check,
                            images=[photo], retries=2)
    return doc


def persist(prj, doc: dict, sources: dict, parts: set) -> list[str]:
    """Write input/facade_observations.json, but only a document that validates. Returns the errors (empty = written)."""
    errors = obs_mod.validate(doc, sources, parts)
    if errors:
        return errors
    prj.write_json("input/facade_observations.json", doc)
    return []


def load(prj) -> dict | None:
    p = prj.root / "input" / "facade_observations.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def run_for_photos(prj, provider, name: str, photo_paths: list, sources: dict, parts: set, limit: int = 3,
                    part_for: dict | None = None, max_workers: int = 3):
    """Propose observations for up to `limit` photos of the project and persist the valid ones together.

    Returns (log, errors). The log has one entry per photo: accepted, or the reason it failed. A failed photo is never
    turned into an invented observation; it is reported, so the spec can carry a warning (see pipeline.build_spec)."""
    from concurrent.futures import ThreadPoolExecutor
    # One task per photo, run in parallel: the calls are independent and each waits on the provider, so the wall time
    # is roughly the slowest call instead of the sum. The number of calls and the quota used are unchanged.
    jobs = []
    for rel in photo_paths[:limit]:
        src = next((s for s in sources.values() if s.get("path") == rel and s.get("kind") == "photo"), None)
        if src is None:
            jobs.append((rel, None, None, None, "not a photo source in this project"))
            continue
        sid = next(k for k, v in sources.items() if v is src)
        # Which part a photo shows is not known yet. The observations describe the main part when there is one, else the
        # first part by name; the user corrects the assignment at A2. This is a stated assumption.
        part_id = (part_for or {}).get(sid) or ("main" if "main" in parts else sorted(parts)[0])
        if part_id not in parts:
            part_id = "main" if "main" in parts else sorted(parts)[0]
        jobs.append((rel, sid, src, part_id, None))

    def work(job):
        rel, sid, src, part_id, early = job
        if early is not None:
            return None, early
        try:
            return propose(provider, name, part_id, sid, prj.root / rel, src, parts), None
        except ai.AIError as exc:
            return None, f"failed: {exc}"[:300]

    runnable = [j for j in jobs if j[4] is None]
    workers = max(1, min(len(runnable), int(max_workers)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(work, jobs))                 # map keeps the input order, so the log stays in photo order
    log, docs = [], []
    for (rel, sid, src, part_id, early), (doc, err) in zip(jobs, results):
        if err is not None:
            log.append({"photo": rel, "source_id": sid, "result": err} if sid else {"photo": rel, "result": err})
            continue
        # Ids are scoped to their photo: two photos each answer FO-1 and FE-1, and those are different things. A
        # matching element in two photos is not assumed the same; merging across photos needs identity evidence.
        for ob in doc["observations"]:
            ob["id"] = f"{sid}/{ob['id']}"
            for el in ob["elements"]:
                el["id"] = f"{ob['id']}/{el['id']}"
        docs.extend(doc["observations"])
        log.append({"photo": rel, "source_id": sid, "result": "accepted",
                    "observations": len(doc["observations"])})
    if not docs:
        return log, []
    merged = {"version": 1, "observations": docs}
    errors = persist(prj, merged, sources, parts)
    return log, errors
