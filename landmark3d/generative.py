"""Generative unique-component adapter (plan M8). Experimental and off by default.

A provider-neutral contract: a request is an isolated crop of one unique component (an ornament, a statue, a sign); the
provider returns a mesh with its confidence and licence metadata. `admit()` decides whether that result may enter a
build. It refuses by default, because the plan forbids a generated asset in the production baseline until it passes the
held-out benchmark and a licence review. No provider is approved in this code, so nothing is admitted yet.

Every admitted component is marked `generated` (plan task 4), and its provider, model and version are recorded so the
result can be reproduced (plan task 5). Whole-building replacement is refused (plan task 2).
"""
from __future__ import annotations

import hashlib
import json

ALLOWED_SLOTS = {"ornament", "statue", "sign", "finial", "crest", "relief"}
MIN_CONFIDENCE = 0.6
MAX_TRIANGLES = 2000             # per component; a result above it is refused, not silently decimated
# Providers that have passed the held-out benchmark and the licence review. Empty: none has, so nothing is admitted.
APPROVED_PROVIDERS: dict = {}


def request(part_id: str, slot: str, crop_path: str, mask_path: str, size_m: list) -> dict:
    """A request for one unique component. The crop and mask are isolated from the rest of the photo."""
    return {"part_id": part_id, "slot": slot, "crop": crop_path, "mask": mask_path, "size_m": list(size_m),
            "purpose": "unique_component"}


def admit(req: dict, result: dict, opted_in: bool) -> dict:
    """Decide whether a provider's result may enter a build. Returns {admitted, problems, record, mesh}."""
    problems = []
    if not opted_in:
        problems.append("generated components are off: turn on the setting first (experimental)")
    if req.get("slot") not in ALLOWED_SLOTS:
        problems.append(f"slot {req.get('slot')!r} is not a unique component (allowed: {sorted(ALLOWED_SLOTS)})")
    if req.get("purpose") != "unique_component":
        problems.append("whole-building replacement is out of scope")
    provider = result.get("provider")
    model = result.get("model")
    version = result.get("version")
    if provider not in APPROVED_PROVIDERS:
        problems.append("no provider has passed the held-out benchmark and licence review")
    elif APPROVED_PROVIDERS[provider].get("model") != model or APPROVED_PROVIDERS[provider].get("version") != version:
        problems.append(f"{provider} {model} {version} is not the approved model version")
    lic = result.get("licence") or {}
    if not lic.get("name") or not lic.get("terms"):
        problems.append("licence name and terms are required for every generated asset")
    conf = result.get("confidence")
    if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        problems.append("confidence must be a number between 0 and 1")
    elif conf < MIN_CONFIDENCE:
        problems.append(f"confidence {conf:.2f} is below {MIN_CONFIDENCE}")
    mesh = result.get("mesh") or {}
    verts, faces = mesh.get("vertices") or [], mesh.get("faces") or []
    if not verts or not faces:
        problems.append("the result has no mesh")
    elif len(faces) > MAX_TRIANGLES:
        problems.append(f"{len(faces)} triangles; the limit is {MAX_TRIANGLES} (reduce the topology first)")
    normalized = None
    if not problems:
        normalized = normalize(verts, faces, req["size_m"])
        if normalized["problems"]:
            problems += normalized["problems"]
    record = {"provider": provider, "model": model, "version": version,
              "licence": lic, "confidence": conf, "provenance": "generated",
              "request_sha256": hashlib.sha256(json.dumps(req, sort_keys=True).encode("utf-8")).hexdigest()}
    return {"admitted": not problems, "problems": problems, "record": record,
            "mesh": normalized["mesh"] if normalized and not problems else None}


def normalize(verts: list, faces: list, size_m: list) -> dict:
    """Scale the component so its height matches the requested size, put its base on the origin, and check it fits the
    requested footprint. The scale is taken from height only; a wider or narrower object is a result to refuse."""
    zs = [v[2] for v in verts]
    h = max(zs) - min(zs)
    if h <= 0:
        return {"mesh": None, "problems": ["the component has no height"]}
    want_h = float(size_m[1]) if len(size_m) > 1 else float(size_m[0])
    scale = want_h / h
    out = [[round(x * scale, 6), round(y * scale, 6), round((z - min(zs)) * scale, 6)] for x, y, z in verts]
    xs = [v[0] for v in out]
    ys = [v[1] for v in out]
    problems = []
    if max(xs) - min(xs) > float(size_m[0]) * 1.1 + 1e-6 or max(ys) - min(ys) > float(size_m[-1]) * 1.1 + 1e-6:
        problems.append("the component is wider than the slot after scaling to its height")
    return {"mesh": {"vertices": out, "faces": faces}, "problems": problems}
