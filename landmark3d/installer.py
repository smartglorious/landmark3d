"""`python -m landmark3d setup`: the part of installation that is the same on every OS.

1. Download the 3D viewer's three.js files (pinned release, MIT licence) into landmark3d/web/vendor/, so the viewer
   works without internet. The server serves them instead of the CDN when they are present.
2. Find Blender (newest installed version) and store its path in the settings.
3. Run the environment check and print it.
The OS installers (install.ps1 / install.sh) create the Python environment and install Python / Blender first.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import httpx

THREE_VERSION = "0.160.0"
THREE_FILES = [
    "build/three.module.js",
    "examples/jsm/loaders/GLTFLoader.js",
    "examples/jsm/loaders/DRACOLoader.js",
    "examples/jsm/controls/OrbitControls.js",
    "examples/jsm/environments/RoomEnvironment.js",
    "examples/jsm/utils/BufferGeometryUtils.js",
    "examples/jsm/libs/draco/draco_decoder.js",
    "examples/jsm/libs/draco/draco_decoder.wasm",
    "examples/jsm/libs/draco/draco_wasm_wrapper.js",
]
CDNS = ["https://cdn.jsdelivr.net/npm/three@{v}/{f}", "https://unpkg.com/three@{v}/{f}"]
VENDOR = Path(__file__).with_name("web") / "vendor" / "three"


def vendor_ready() -> bool:
    return all((VENDOR / f).is_file() and (VENDOR / f).stat().st_size > 0 for f in THREE_FILES)


def fetch_three(force: bool = False, log=print) -> bool:
    """Download the pinned three.js files (two CDNs tried in turn). Returns True when all files are present."""
    if vendor_ready() and not force:
        log(f"three.js {THREE_VERSION}: already present")
        return True
    manifest = {}
    with httpx.Client(timeout=60, follow_redirects=True) as c:
        for f in THREE_FILES:
            dest = VENDOR / f
            dest.parent.mkdir(parents=True, exist_ok=True)
            data, last = None, None
            for tpl in CDNS:
                try:
                    r = c.get(tpl.format(v=THREE_VERSION, f=f))
                    r.raise_for_status()
                    data = r.content
                    break
                except Exception as exc:              # try the next CDN
                    last = exc
            if data is None:
                log(f"three.js: could not download {f} ({last}); the viewer will use the CDN online")
                return False
            dest.write_bytes(data)
            manifest[f] = hashlib.sha256(data).hexdigest()
    (VENDOR / "MANIFEST.json").write_text(json.dumps({"version": THREE_VERSION, "files": manifest}, indent=2),
                                          encoding="utf-8")
    log(f"three.js {THREE_VERSION}: {len(manifest)} files saved to {VENDOR}")
    return True


def configure_blender(log=print) -> str | None:
    from . import doctor, settings as settings_mod
    exe = doctor.find_blender()
    if exe:
        settings_mod.update({"blender_path": exe})
        log(f"Blender: {exe}")
    else:
        log("Blender: NOT FOUND - install Blender 4.2 or newer (https://www.blender.org/download/), then run setup again")
    return exe


def main(argv=None) -> int:
    from . import doctor
    fetch_three(force="--force" in (argv or []))
    configure_blender()
    rep = doctor.run_all()
    for c in rep["checks"]:
        flag = "OK " if c["ok"] else ("opt" if c.get("optional") else "ERR")
        print(f"[{flag}] {c['id']}: {c['detail']}" + (f"\n      fix: {c['fix']}" if c.get("fix") else ""))
    print("READY" if rep["ok"] else "NOT READY - see the ERR lines above")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
