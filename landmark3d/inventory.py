"""Volume inventory: the list of building volumes the AI sees in the photos, and the check that every one keeps its
own part through the whole spec phase.

Without it the massing AI could return a handful of boxes for a building of twenty volumes (Đức Bà: 5 parts for a
nave, aisles, side chapels with their own gables, transept arms, sacristies, an apse with radiating chapels and two
towers), and the photo-review and select steps could drop volumes silently. With fewer volumes there is nowhere to
put their details, whatever the facade and texture steps do.

The first massing answer must list "volumes": [{"name", "part", "seen_in"}] - every volume with its own roof or its
own wall plane in any photo - each on a part of its own. Later answers (review rounds, candidates) must keep a part for
every listed volume; they may add volumes, never drop one. No AI here.
"""
from __future__ import annotations

MIN_VOLUMES = 1


def _ids(parts) -> set:
    return {p.get("id") for p in parts or [] if isinstance(p, dict)}


def first_errors(obj: dict) -> list[str]:
    """Errors of the inventory in the first massing answer."""
    vols = obj.get("volumes")
    if not isinstance(vols, list) or len(vols) < MIN_VOLUMES:
        return ['give "volumes": [{"name", "part", "seen_in": [photo numbers]}, ...] listing EVERY volume of the '
                'building you can see in the photos (each wing, aisle, chapel, transept arm, annex, porch, apse, '
                'tower, belfry, spire, dome or pavilion with its own roof or wall plane), each with its own part']
    errs, used = [], {}
    ids = _ids(obj.get("parts"))
    for v in vols:
        if not isinstance(v, dict) or not v.get("name") or not v.get("part"):
            errs.append(f'every volume needs "name" and "part": {str(v)[:80]}')
            continue
        if v["part"] not in ids:
            errs.append(f'volume "{v["name"]}" names part "{v["part"]}", which is not in "parts": add that part')
        elif v["part"] in used:
            errs.append(f'volumes "{used[v["part"]]}" and "{v["name"]}" share part "{v["part"]}": each volume needs a '
                        f'part of its own (a lower aisle is not the nave, a chapel is not the aisle)')
        else:
            used[v["part"]] = v["name"]
    return errs


def coverage_errors(parts, inventory: list) -> list[str]:
    """Volumes of the inventory whose part is gone from a later answer."""
    ids = _ids(parts)
    missing = [v for v in inventory or [] if isinstance(v, dict) and v.get("part") not in ids]
    if not missing:
        return []
    return ["these volumes from your inventory lost their part - keep a part for each (same id): "
            + "; ".join(f'"{v["name"]}" (part {v["part"]})' for v in missing[:12])]


def merged(inventory: list, obj: dict) -> list:
    """The inventory plus volumes a later answer adds (by name); never fewer."""
    out = list(inventory or [])
    names = {v.get("name") for v in out if isinstance(v, dict)}
    ids = _ids(obj.get("parts"))
    for v in obj.get("volumes") or []:
        if isinstance(v, dict) and v.get("name") and v.get("name") not in names and v.get("part") in ids:
            out.append({k: v.get(k) for k in ("name", "part", "seen_in")})
            names.add(v["name"])
    return out


def text(inventory: list) -> str:
    return "; ".join(f'{v.get("name")} = {v.get("part")}' for v in inventory or [] if isinstance(v, dict))
