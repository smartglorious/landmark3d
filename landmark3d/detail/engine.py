"""Flag-gated entry point of Detail Engine v2 (plan M1 task 6).

With the flag off, or on with no v2 components in the spec, the spec passes through unchanged, so v1 geometry is
preserved exactly. A spec that asks for v2 components while no generator exists fails loudly. A silent fallback to v1
would hide a missing feature from the reviewer, which the production baseline forbids (9.1).
"""
from __future__ import annotations

from . import registry


class DetailEngineError(RuntimeError):
    pass


def engine_settings(flag: bool, bake_ao: bool = False, ao_size: int = 256, bake_normal: bool = False,
                    normal_size: int = 256, normal_ratio: float = 0.6) -> dict:
    """What a build job records about the engine (plan: persist it in the spec/build job). Baking is part of the
    detail engine: it only runs with the flag on."""
    return {"detail_engine_v2": bool(flag), "library_version": registry.LIBRARY_VERSION,
            "bake_ao": bool(flag and bake_ao), "ao_size": int(ao_size),
            "bake_normal": bool(flag and bake_normal), "normal_size": int(normal_size),
            "normal_ratio": float(normal_ratio)}


def v2_requests(spec: dict) -> list:
    """Facade-system runs and component uses that a v2 build would have to generate."""
    out = []
    for p in spec.get("parts", []):
        fs = p.get("facade_system") or {}
        for run in fs.get("runs", []):
            out.append((p.get("id"), run.get("run")))
    return out


def expand(spec: dict, enabled: bool) -> dict:
    if not enabled:
        return spec
    requests = v2_requests(spec)
    if not requests:
        return spec
    raise DetailEngineError(
        f"detail_engine_v2 is on and {len(requests)} facade run(s) ask for v2 components, but no v2 generator is "
        f"implemented yet (library {registry.LIBRARY_VERSION}). Turn the flag off, or wait for the milestone that adds it.")
