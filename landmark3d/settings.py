"""User settings (~/.landmark3d/settings.json). The tool stores no secrets: AI access comes from the CLIs' own
web sign-in or from environment variables already on the machine (see research/ai.py)."""
from __future__ import annotations

import json
import os
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[1]
APP_HOME = Path(os.environ.get("LANDMARK3D_HOME") or Path.home() / ".landmark3d")
SETTINGS_FILE = APP_HOME / "settings.json"

DEFAULT_BUDGETS = {
    "triangles_max": 75000,       # raised 50 % on 2026-11-12 (owner): more volumes from the inventory need room
    "triangles_target": 60000,
    "bytes_max": 5_000_000,
    "bytes_target": 4_500_000,
    "materials_max": 10,
    "compression": "draco",
    "texture_max_px": 2048,
}

DEFAULTS = {
    "projects_dir": str(TOOL_ROOT / "projects"),
    "blender_path": "",
    # No API key is stored by the tool. via: "auto" | "cli" (web sign-in through Claude Code / Codex) |
    # "env" (credentials already in this machine's environment variables). cli_model: model for the CLI route
    # ("" = the CLI's own default); model / vision_model: for the env (SDK) route.
    "providers": {
        "claude": {"enabled": False, "via": "auto", "cli_model": "sonnet", "cli_path": "",
                   "model": "claude-sonnet-5-5", "vision_model": "claude-sonnet-5-5"},
        "openai": {"enabled": False, "via": "auto", "cli_model": "", "cli_path": "",
                   "model": "gpt-5", "vision_model": "gpt-5"},
    },
    # task -> provider name ("auto" = first enabled provider)
    "routing": {"research": "auto", "vision": "auto", "spec": "auto"},
    "cross_check": False,
    # After the AI massing: render the model from each photo's viewpoint, show photo | render to the AI and let it
    # correct the model. 0 disables the loop.
    "photo_review_rounds": 3,
    "facade_pass": True,
    "bake_ao": False,            # M5: bake ambient occlusion per part (needs detail_engine_v2 on; Cycles, Blender)
    "ao_size": 256,
    "generative_components": False,   # M8: opt-in for generated unique components (experimental; no provider approved yet)
    "bake_normal": False,        # M5: tangent normal map from the detailed part onto a decimated copy (needs detail_engine_v2)
    "normal_size": 256,
    "normal_ratio": 0.6,         # kept share of triangles in the shipped copy; 0.3 dropped the trim (measured)
    "facade_observations": False,  # M3: the AI proposes facade observations per photo (calls the AI; off by default)
    "opening_assemblies": True,  # M2 window assemblies (frame, sill, recess) also with detail_engine_v2 off: relief under generated skins
    "detail_engine_v2": False,   # M1: contracts only; the flag must stay off until a v2 generator is released
    "facade_review_rounds": 2,  # only with facade_gate "judge": extra facade rounds driven by the judge's errors
    "facade_gate": "checks",    # "checks": one facade pass accepted on deterministic checks; "judge": earlier judge-gated rounds
    "judge": True,
    "judge_samples": 3,       # independent readings averaged into each score (one reading moves by a point or more)
    "auto_textures": True,    # facade photo panels + seamless material swatches from licence-allowed photos            # independent AI score of the final model (spec["quality"])
    "roof_plan": False,           # trace the roof volumes on the satellite view before the massing (off: the AI tracing missed the transept and a tower on Đức Bà)
    "photo_exact": False,         # Smart Texture T2-T5: photo cameras, masks and projection; the camera editor shows only when on
    "generated_textures": True,   # facade textures drawn by Codex image generation (signed-in CLI), in the building's style, windows on the model's grid; labelled as generated
    # Satellite plan view for the AI: "esri" (Esri World Imagery, reference only, attribution) or "none".
    # Google imagery is never downloaded automatically (its terms forbid it); a screenshot can be uploaded instead.
    "satellite_provider": "esri",
    "default_budgets": DEFAULT_BUDGETS,
    # Contact (website or e-mail) for the HTTP User-Agent. Wikimedia (Wikidata, Commons) rejects requests
    # without one, and OSM/Nominatim ask for it. Entered by the user in Settings; never guessed.
    "contact": "",
}


def user_agent() -> str:
    contact = load().get("contact", "").strip()
    return f"Landmark3D/0.1 ({contact + '; ' if contact else ''}local landmark modelling tool) python-httpx"


def _merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        out[k] = _merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


def load() -> dict:
    data = {}
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    return _merge(DEFAULTS, data)


def save(settings: dict) -> None:
    APP_HOME.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")


def update(patch: dict) -> dict:
    s = _merge(load(), patch)
    save(s)
    return s

