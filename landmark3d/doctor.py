"""Environment checks: Blender (headless + glTF/Draco), Blender MCP add-on and client config, Node tools, AI keys."""
from __future__ import annotations

import glob
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

from . import settings as settings_mod

NODE_DIR = settings_mod.TOOL_ROOT / "node_tools"


def find_blender() -> str | None:
    s = settings_mod.load()
    if s.get("blender_path") and Path(s["blender_path"]).exists():
        return s["blender_path"]
    cands = sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender *\blender.exe"), reverse=True)
    cands += [shutil.which("blender") or ""]
    cands += sorted(glob.glob("/Applications/Blender.app/Contents/MacOS/Blender"))
    for c in cands:
        if c and Path(c).exists():
            return c
    return None


def _run(cmd, timeout=120):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:  # pragma: no cover - environment dependent
        return -1, str(exc)


def check_blender() -> list[dict]:
    out = []
    exe = find_blender()
    if not exe:
        return [{"id": "blender", "ok": False, "detail": "Blender not found",
                 "fix": "Install Blender >= 4.2 from https://www.blender.org/download/ or set the path in Settings."}]
    code, text = _run([exe, "-b", "--factory-startup", "--version"], 60)
    ver = next((ln.strip() for ln in text.splitlines() if ln.startswith("Blender ")), "unknown")
    major_minor = ver.split()[1] if len(ver.split()) > 1 else "0.0"
    try:
        mm = tuple(int(x) for x in major_minor.split(".")[:2])
    except ValueError:
        mm = (0, 0)
    out.append({"id": "blender", "ok": code == 0 and mm >= (4, 2), "detail": f"{exe} - {ver}",
                "fix": "" if mm >= (4, 2) else "Blender 4.2 or newer is required."})
    probe = ("import bpy, addon_utils, json;"
             "addon_utils.enable('io_scene_gltf2', default_set=True);"
             "ok=hasattr(bpy.ops.export_scene,'gltf');"
             "dr=False\n"
             "try:\n"
             " from io_scene_gltf2.io.com.draco import dll_exists\n"
             " dr=bool(dll_exists())\n"
             "except Exception:\n"
             " try:\n"
             "  from io_scene_gltf2.io.com.gltf2_io_draco_compression_extension import dll_exists\n"
             "  dr=bool(dll_exists())\n"
             " except Exception:\n"
             "  dr=None\n"
             "print('PROBE', json.dumps({'gltf': ok, 'draco': dr}))")
    code, text = _run([exe, "-b", "--factory-startup", "--python-expr", probe], 120)
    line = next((ln for ln in text.splitlines() if ln.startswith("PROBE ")), None)
    info = json.loads(line[6:]) if line else {"gltf": False, "draco": None}
    out.append({"id": "blender_headless", "ok": line is not None, "detail": "headless Python run works" if line else text[-400:],
                "fix": "" if line else "Check that Blender starts without a GUI from the command line."})
    out.append({"id": "gltf_exporter", "ok": bool(info.get("gltf")), "detail": "glTF exporter available",
                "fix": "" if info.get("gltf") else "Enable the 'glTF 2.0 format' add-on."})
    out.append({"id": "draco", "ok": info.get("draco") is not False,
                "detail": "Draco library " + ("found" if info.get("draco") else "not verified"),
                "fix": "" if info.get("draco") is not False else "Use compression 'none' or install a Blender build with Draco."})
    return out


def check_mcp() -> list[dict]:
    out = []
    appdata = os.environ.get("APPDATA", "")
    addon_hits = []
    for base in glob.glob(os.path.join(appdata, "Blender Foundation", "Blender", "*", "scripts", "addons")) + \
            glob.glob(os.path.join(appdata, "Blender Foundation", "Blender", "*", "extensions", "*")):
        for name in os.listdir(base):
            if "mcp" in name.lower():
                addon_hits.append(os.path.join(base, name))
    out.append({"id": "blender_mcp_addon", "ok": bool(addon_hits), "optional": True,
                "detail": addon_hits[0] if addon_hits else "Blender MCP add-on not found",
                "fix": "" if addon_hits else "Optional (live preview only): install the Blender MCP add-on, e.g. with "
                                             "Install-ClaudeCode-BlenderMCP.ps1, then enable it in Blender."})
    cfgs = [Path(appdata) / "Claude" / "claude_desktop_config.json", Path.home() / ".claude.json",
            Path.home() / ".cursor" / "mcp.json"]
    found = []
    for c in cfgs:
        if c.exists():
            try:
                txt = c.read_text(encoding="utf-8", errors="replace").lower()
                if "blender" in txt and "mcp" in txt:
                    found.append(str(c))
            except OSError:
                pass
    out.append({"id": "mcp_client_config", "ok": bool(found), "optional": True,
                "detail": ", ".join(found) if found else "no MCP client config with a Blender server found",
                "fix": "" if found else "Optional: add the Blender MCP server to your MCP client (Claude Desktop/Code)."})
    port_open = False
    try:
        with socket.create_connection(("127.0.0.1", 9876), timeout=0.5):
            port_open = True
    except OSError:
        pass
    out.append({"id": "mcp_socket", "ok": port_open, "optional": True,
                "detail": "Blender MCP socket 127.0.0.1:9876 " + ("open" if port_open else "closed (Blender not running with MCP)"),
                "fix": "" if port_open else "Optional: open Blender and start the MCP server from its panel for live preview."})
    return out


def check_node() -> list[dict]:
    node = shutil.which("node")
    out = [{"id": "node", "ok": bool(node), "optional": True, "detail": node or "Node.js not found",
            "fix": "" if node else "Optional: install Node.js LTS to enable the Khronos glTF validator."}]
    validator = NODE_DIR / "node_modules" / "gltf-validator"
    out.append({"id": "gltf_validator", "ok": validator.exists(), "optional": True,
                "detail": str(validator) if validator.exists() else "gltf-validator not installed",
                "fix": "" if validator.exists() else "Doctor action 'install_validator' (npm install gltf-validator)."})
    return out


def check_ai() -> list[dict]:
    from .research import ai
    out = []
    for name in ai.NAMES:
        st = ai.status(name)
        cli = "Claude Code" if name == "claude" else "Codex"
        if st["resolved"] == "cli":
            how = f"{cli} CLI, signed in" + (f" ({st['login_method']})" if st["login_method"] else "")
        elif st["resolved"] == "env":
            how = "environment variables " + ", ".join(st["env_vars"])
        else:
            how = f"{cli} CLI {'installed, not signed in' if st['cli_path'] else 'not installed'}; no usable variables"
        ok = bool(st["resolved"]) and st["enabled"]
        fix = ""
        if not st["resolved"]:
            fix = (f"Sign in from Connections ('{cli} web login')" if st["cli_path"] else
                   f"Install the {cli} CLI and sign in with your {st['label']} account") + \
                  ", or set its environment variables. Without AI, research uses structured sources and your input."
        elif not st["enabled"]:
            fix = "Turn it on in Connections."
        out.append({"id": f"ai_{name}", "ok": ok, "optional": True,
                    "detail": f"{st['label']}: {how}; {'on' if st['enabled'] else 'off'}", "fix": fix})
    any_ai = any(c["ok"] for c in out)
    out.append({"id": "ai_any", "ok": any_ai, "optional": True,
                "detail": "at least one AI provider connected" if any_ai else "no AI provider connected",
                "fix": "" if any_ai else "Connect Claude and/or ChatGPT in Connections for automatic web research."})
    return out


def check_contact() -> list[dict]:
    c = settings_mod.load().get("contact", "").strip()
    return [{"id": "research_contact", "ok": bool(c), "optional": True,
             "detail": f"User-Agent contact: {c}" if c else "no contact set",
             "fix": "" if c else "Set a contact (your website or e-mail) in Settings: Wikidata and Wikimedia Commons "
                                 "refuse requests without one, so photos and Wikidata facts will be skipped."}]


def run_all() -> dict:
    checks = check_blender() + check_mcp() + check_node() + check_ai() + check_contact()
    required_ok = all(c["ok"] for c in checks if not c.get("optional"))
    return {"ok": required_ok, "checks": checks}


def install_validator() -> dict:
    npm = shutil.which("npm")
    if not npm:
        return {"ok": False, "detail": "npm not found"}
    NODE_DIR.mkdir(parents=True, exist_ok=True)
    if not (NODE_DIR / "package.json").exists():
        (NODE_DIR / "package.json").write_text('{"name":"landmark3d-node-tools","private":true}', encoding="utf-8")
    code, text = _run([npm, "install", "--prefix", str(NODE_DIR), "gltf-validator@2.0.0-dev.3.9"], 600)
    return {"ok": code == 0, "detail": text[-800:]}
