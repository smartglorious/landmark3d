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
MAX_CELL_SHARE = 0.1             # decimation may merge points no further apart than this share of the component size


def clean(verts: list, faces: list, eps: float = 1e-6) -> dict:
    """Merge duplicate vertices, drop degenerate, repeated and broken faces and unused vertices, and split polygons
    into triangles (fan). Returns the mesh and counts of what was removed."""
    key_of, new_verts, remap = {}, [], {}
    for i, v in enumerate(verts):
        k = tuple(round(float(c) / eps) for c in v[:3])
        if k not in key_of:
            key_of[k] = len(new_verts)
            new_verts.append([float(c) for c in v[:3]])
        remap[i] = key_of[k]
    tris, seen, bad = [], set(), 0
    for f in faces:
        if len(f) < 3 or any(not isinstance(i, int) or i not in remap for i in f):
            bad += 1
            continue
        idx = [remap[i] for i in f]
        for k in range(1, len(idx) - 1):
            t = (idx[0], idx[k], idx[k + 1])
            key = tuple(sorted(t))
            if len(set(t)) < 3 or _area(new_verts, t) <= eps * eps or key in seen:
                bad += 1
                continue
            seen.add(key)
            tris.append(list(t))
    used = sorted({i for t in tris for i in t})
    pos = {old: n for n, old in enumerate(used)}
    return {"vertices": [new_verts[i] for i in used], "faces": [[pos[i] for i in t] for t in tris],
            "removed": {"duplicate_vertices": len(verts) - len(new_verts), "bad_faces": bad,
                        "unused_vertices": len(new_verts) - len(used)}}


def _area(v, t) -> float:
    a, b, c = (v[i] for i in t)
    u = [b[k] - a[k] for k in range(3)]
    w = [c[k] - a[k] for k in range(3)]
    x = [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]]
    return 0.5 * (x[0] ** 2 + x[1] ** 2 + x[2] ** 2) ** 0.5


def decimate(verts: list, faces: list, max_triangles: int) -> dict | None:
    """Reduce the topology by vertex clustering on a grid that grows until the mesh fits `max_triangles`; each cluster
    becomes the mean of its points. None when the grid would have to be coarser than MAX_CELL_SHARE of the size."""
    lo = [min(v[k] for v in verts) for k in range(3)]
    hi = [max(v[k] for v in verts) for k in range(3)]
    size = max(hi[k] - lo[k] for k in range(3)) or 1.0
    cell = size / 256
    while cell <= MAX_CELL_SHARE * size + 1e-12:
        cluster, sums = {}, {}
        for i, v in enumerate(verts):
            key = tuple(int((v[k] - lo[k]) // cell) for k in range(3))
            cluster[i] = key
            s = sums.setdefault(key, [0.0, 0.0, 0.0, 0])
            for k in range(3):
                s[k] += v[k]
            s[3] += 1
        index = {k: n for n, k in enumerate(sums)}
        nv = [[s[0] / s[3], s[1] / s[3], s[2] / s[3]] for s in sums.values()]
        out = clean(nv, [[index[cluster[i]] for i in f] for f in faces])
        if out["faces"] and len(out["faces"]) <= max_triangles:
            out["cell_m"] = cell
            return out
        cell *= 1.5
    return None


def anchors(mesh: dict) -> dict:
    """Where the component attaches and how big it is: base centre, top centre and bounds (plan M8 task 3)."""
    v = mesh["vertices"]
    lo = [min(p[k] for p in v) for k in range(3)]
    hi = [max(p[k] for p in v) for k in range(3)]
    cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    return {"base": [cx, cy, lo[2]], "top": [cx, cy, hi[2]], "bounds": [lo, hi]}


def collides(box_a, box_b, clearance: float = 0.0) -> bool:
    """Axis-aligned boxes [[x0, y0, z0], [x1, y1, z1]] overlap, or come closer than `clearance`."""
    return all(box_a[0][k] < box_b[1][k] + clearance and box_b[0][k] < box_a[1][k] + clearance for k in range(3))


def place(adm: dict, at: list, part_box, others: list, part_triangle_allowance: int) -> dict:
    """Validate an admitted component placed with its base at `at`: inside its part's box, clear of the other
    components' boxes, and within the part's remaining triangle allowance. Returns {ok, problems, box}."""
    problems = []
    lo, hi = adm["anchors"]["bounds"]
    base = adm["anchors"]["base"]
    box = [[lo[k] - base[k] + at[k] for k in range(3)], [hi[k] - base[k] + at[k] for k in range(3)]]
    if not all(part_box[0][k] - 1e-6 <= box[0][k] and box[1][k] <= part_box[1][k] + 1e-6 for k in range(3)):
        problems.append("the component reaches outside its part")
    for n, other in enumerate(others):
        if collides(box, other):
            problems.append(f"the component overlaps component {n}")
    tris = len(adm["mesh"]["faces"])
    if tris > part_triangle_allowance:
        problems.append(f"{tris} triangles exceed the part's remaining allowance of {part_triangle_allowance}")
    return {"ok": not problems, "problems": problems, "box": box}


def review_items(spec: dict) -> list[dict]:
    """Every generated component in a spec, as review items for the A2 and A3 panels (plan M8 task 4): unseen
    geometry is shown as generated, never as observed."""
    out = []
    for p in spec.get("parts") or []:
        for c in p.get("generated_components") or []:
            rec = c.get("record") or {}
            out.append({"severity": "warning", "code": "generated_component", "part": p.get("id"),
                        "message": f"part {p.get('id')}: {c.get('slot', 'component')} is generated by "
                                   f"{rec.get('provider')} {rec.get('model')} {rec.get('version')}, not observed"})
    return out


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
    cleaned = clean(verts, faces) if verts and faces else {"vertices": [], "faces": [], "removed": {}}
    reduced_from = None
    if not cleaned["faces"]:
        problems.append("the result has no mesh")
    elif len(cleaned["faces"]) > MAX_TRIANGLES:
        reduced_from = len(cleaned["faces"])
        red = decimate(cleaned["vertices"], cleaned["faces"], MAX_TRIANGLES)
        if red is None:
            problems.append(f"{reduced_from} triangles cannot be reduced to the limit of {MAX_TRIANGLES} without "
                            f"merging points further apart than {MAX_CELL_SHARE:.0%} of the component size")
        else:
            cleaned = red
    normalized = None
    if not problems:
        normalized = normalize(cleaned["vertices"], cleaned["faces"], req["size_m"])
        if normalized["problems"]:
            problems += normalized["problems"]
    record = {"provider": provider, "model": model, "version": version,
              "licence": lic, "confidence": conf, "provenance": "generated",
              "triangles_in": len(faces), "triangles_out": len(cleaned["faces"]), "reduced_from": reduced_from,
              "cleaned": cleaned.get("removed", {}),
              "request_sha256": hashlib.sha256(json.dumps(req, sort_keys=True).encode("utf-8")).hexdigest()}
    out_mesh = normalized["mesh"] if normalized and not problems else None
    return {"admitted": not problems, "problems": problems, "record": record, "mesh": out_mesh,
            "anchors": anchors(out_mesh) if out_mesh else None}


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
