"""AI connectors: Claude and ChatGPT behind one contract. No API key is ever entered into or stored by this tool.

Each provider connects in one of two ways ("via"):
  cli  - the official command-line agent, signed in through the web (browser login to your Claude or ChatGPT
         account): Claude Code (`claude auth login`) or Codex (`codex login`). The CLI also honours the
         credentials it finds in the environment (e.g. Bedrock/Vertex/ANTHROPIC_* or OPENAI_API_KEY).
  env  - the official Python SDK with credentials that already exist as environment variables on this machine
         (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN, OPENAI_API_KEY, Azure OpenAI ...).
"auto" picks cli when the CLI is installed and signed in, else env when the variables exist.

Every call returns text; structured results are extracted as JSON and validated by the caller.
"""
from __future__ import annotations

import base64
import contextlib
import contextvars
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import settings as settings_mod


class AIError(RuntimeError):
    pass


@dataclass
class AIResult:
    text: str
    provider: str
    model: str
    citations: list
    meta: dict = field(default_factory=dict)              # what the connector knows about the call (turns, tokens)


class CallLog:
    """One line per AI attempt in a project's logs/ai_calls.jsonl: step, attempt, outcome, durations, and why an answer
    was rejected. Prompts and replies are not stored, only their lengths. A logging failure never affects a run."""

    def __init__(self, path: Path, phase: str):
        self.path, self.phase = Path(path), phase
        self.entries: list[dict] = []
        self.requests = 0

    def new_request(self) -> int:
        self.requests += 1
        return self.requests

    def record(self, entry: dict) -> None:
        entry = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "phase": self.phase, **entry}
        self.entries.append(entry)
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def summary(self) -> dict:
        by_step: dict[str, dict] = {}
        for e in self.entries:
            s = by_step.setdefault(e["step"], {"calls": 0, "rejected": 0, "seconds": 0.0})
            s["calls"] += 1
            s["rejected"] += e["outcome"] != "ok"
            s["seconds"] = round(s["seconds"] + e["seconds"], 1)
        return {"requests": self.requests, "calls": len(self.entries), "retries": len(self.entries) - self.requests,
                "seconds": round(sum(e["seconds"] for e in self.entries), 1), "by_step": by_step}


_call_log: contextvars.ContextVar = contextvars.ContextVar("l3d_ai_call_log", default=None)


@contextlib.contextmanager
def logging_calls(log: CallLog):
    """Record every ask_json attempt made inside this block to `log`."""
    token = _call_log.set(log)
    try:
        yield log
    finally:
        _call_log.reset(token)


NAMES = ("claude", "openai")
LABELS = {"claude": "Claude", "openai": "ChatGPT"}
URL_RE = re.compile(r"https?://[^\s)\]>\"'`]+")
MODEL_RE = re.compile(r"^[\w.\-:/\[\]]{1,80}$")
CLI_TIMEOUT_S = 900

# Environment variables each route can use. Only names are ever reported, never values.
ENV_VARS = {
    "claude": {
        "api": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"],
        "cli_only": ["CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                     "CLAUDE_CODE_USE_FOUNDRY"],
        "extra": ["ANTHROPIC_BASE_URL"],
    },
    "openai": {
        "api": ["OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"],
        "cli_only": ["CODEX_API_KEY", "CODEX_ACCESS_TOKEN"],
        "extra": ["OPENAI_BASE_URL", "AZURE_OPENAI_ENDPOINT", "OPENAI_API_VERSION"],
    },
}


# ----------------------------------------------------------------------------- environment
def _registry_env(scope: str) -> dict:
    """User/machine variables from the Windows registry, so variables set after the server started still count."""
    if sys.platform != "win32":
        return {}
    try:
        import winreg
        if scope == "user":
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment")
        else:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
        out, i = {}, 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            out[name.upper()] = os.path.expandvars(str(value))
            i += 1
        return out
    except Exception:
        return {}


def machine_env() -> dict:
    """Process environment, completed with the Windows user/machine variables (process wins)."""
    env = {}
    env.update(_registry_env("machine"))
    env.update(_registry_env("user"))
    if sys.platform == "win32":
        env.update({k.upper(): v for k, v in os.environ.items()})
        # PATH must combine, not replace.
        paths = [os.environ.get("PATH", ""), _registry_env("user").get("PATH", ""), _registry_env("machine").get("PATH", "")]
        env["PATH"] = os.pathsep.join(p for p in paths if p)
    else:
        env.update(os.environ)
    return env


def env_present(name: str) -> list[str]:
    env = machine_env()
    groups = ENV_VARS[name]
    return [v for v in groups["api"] + groups["cli_only"] + groups["extra"] if env.get(v)]


# ----------------------------------------------------------------------------- CLI discovery
def _cli_candidates(name: str) -> list[Path]:
    home = Path.home()
    if name == "claude":
        c = [home / ".local" / "bin" / ("claude.exe" if sys.platform == "win32" else "claude"),
             home / ".claude" / "local" / ("claude.exe" if sys.platform == "win32" else "claude")]
        if sys.platform == "win32":
            c.append(Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd")
        return c
    if sys.platform == "win32":
        return [Path(os.environ.get("APPDATA", "")) / "npm" / "codex.cmd"]
    return [home / ".local" / "bin" / "codex"]


def find_cli(name: str) -> str | None:
    cfg = settings_mod.load()["providers"][name]
    if cfg.get("cli_path") and Path(cfg["cli_path"]).exists():
        return cfg["cli_path"]
    exe = "claude" if name == "claude" else "codex"
    found = shutil.which(exe, path=machine_env().get("PATH"))
    if found:
        return found
    return next((str(p) for p in _cli_candidates(name) if p.exists()), None)


def _run(cmd: list[str], stdin_text: str | None = None, timeout: int = 60, cwd: str | None = None):
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(cmd, input=stdin_text if stdin_text is not None else "", capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd, env=machine_env(), **kw)


def cli_login_status(name: str, cli: str | None = None) -> dict:
    """{'logged_in': bool, 'method': str, 'account': str} from the CLI's own status command."""
    cli = cli or find_cli(name)
    if not cli:
        return {"logged_in": False, "method": "", "account": ""}
    try:
        if name == "claude":
            r = _run([cli, "auth", "status"], timeout=30)
            d = json.loads(r.stdout or "{}")
            method = d.get("authMethod") or d.get("apiProvider") or ""
            plan = d.get("subscriptionType") or ""
            return {"logged_in": bool(d.get("loggedIn")), "method": method + (f" ({plan})" if plan else ""),
                    "account": d.get("email") or ""}
        r = _run([cli, "login", "status"], timeout=30)
        text = (r.stdout + r.stderr).strip()
        ok = r.returncode == 0 and "logged in" in text.lower() and "not logged in" not in text.lower()
        return {"logged_in": ok, "method": text.splitlines()[-1] if text else "", "account": ""}
    except Exception as exc:
        return {"logged_in": False, "method": f"status failed: {type(exc).__name__}", "account": ""}


def status(name: str) -> dict:
    """What can be used for this provider right now, without revealing any secret."""
    cfg = settings_mod.load()["providers"][name]
    cli = find_cli(name)
    login = cli_login_status(name, cli) if cli else {"logged_in": False, "method": "", "account": ""}
    env = env_present(name)
    api_env = [v for v in env if v in ENV_VARS[name]["api"]]
    cli_env = [v for v in env if v in ENV_VARS[name]["cli_only"]]
    cli_ok = bool(cli) and (login["logged_in"] or bool(api_env) or bool(cli_env))
    env_ok = bool(api_env)
    via = cfg.get("via", "auto")
    resolved = ("cli" if cli_ok else "env" if env_ok else None) if via == "auto" else (
        via if (via == "cli" and cli_ok) or (via == "env" and env_ok) else None)
    return {"name": name, "label": LABELS[name], "enabled": cfg.get("enabled", False), "via": via,
            "resolved": resolved, "cli_path": cli, "cli_logged_in": login["logged_in"],
            "login_method": login["method"], "account": login["account"], "env_vars": env,
            "cli_usable": cli_ok, "env_usable": env_ok}


def start_login(name: str) -> dict:
    """Open the CLI's browser sign-in in its own console window (the user completes it in the browser)."""
    cli = find_cli(name)
    if not cli:
        raise AIError(f"{'Claude Code' if name == 'claude' else 'Codex'} CLI is not installed")
    cmd = [cli, "auth", "login"] if name == "claude" else [cli, "login"]
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    subprocess.Popen(cmd, env=machine_env(), **kw)
    return {"started": True, "command": " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd))}


# ----------------------------------------------------------------------------- providers
def _img_b64(path: Path, max_px: int = 1280) -> tuple[str, str]:
    from PIL import Image
    import io
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return "image/jpeg", base64.b64encode(buf.getvalue()).decode("ascii")


def _urls(text: str) -> list[dict]:
    seen, out = set(), []
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(".,;")
        if u not in seen:
            seen.add(u)
            out.append({"url": u, "title": ""})
    return out


def _small_images(images, workdir: Path) -> list[Path]:
    """Copies of the images, downscaled, inside the CLI's working folder (the only folder it may read)."""
    from PIL import Image
    out = []
    for i, p in enumerate(images or []):
        dst = workdir / f"image_{i + 1:02d}.jpg"
        with Image.open(p) as im:
            im = im.convert("RGB")
            im.thumbnail((1280, 1280))
            im.save(dst, "JPEG", quality=85)
        out.append(dst)
    return out


class Provider:
    name = "base"
    via = ""

    def __init__(self, model: str, vision_model: str | None = None):
        self.model = model
        self.vision_model = vision_model or model

    def chat(self, system: str, prompt: str, web_search: bool = False, images: list[Path] | None = None,
             max_tokens: int = 8000) -> AIResult:  # pragma: no cover - interface
        raise NotImplementedError

    def test(self) -> dict:
        try:
            r = self.chat("Reply with the single word OK.", "ping", max_tokens=20)
            return {"ok": "ok" in r.text.lower(), "model": r.model or self.model, "via": self.via,
                    "reply": r.text[:60]}
        except Exception as exc:
            return {"ok": False, "model": self.model, "via": self.via, "error": f"{type(exc).__name__}: {exc}"[:400]}


class ClaudeCLIProvider(Provider):
    """Claude Code in print mode, signed in with the user's Claude account (or the environment it finds)."""
    name, via = "claude", "cli"

    def __init__(self, model, vision_model=None, cli=None):
        super().__init__(model or "sonnet", vision_model)
        self.cli = cli or find_cli("claude")
        if not self.cli:
            raise AIError("Claude Code CLI not found")
        if not MODEL_RE.match(self.model):
            raise AIError(f"invalid model name {self.model!r}")

    def chat(self, system, prompt, web_search=False, images=None, max_tokens=8000):
        with tempfile.TemporaryDirectory(prefix="l3d_claude_") as td:
            imgs = _small_images(images, Path(td))
            tools = (["WebSearch", "WebFetch"] if web_search else []) + (["Read"] if imgs else [])
            text = prompt
            if imgs:
                text += ("\n\nATTACHED IMAGES - before answering, open EVERY one of these files with the Read tool "
                         "and base your answer on what you see (an answer without looking at them is rejected): "
                         + ", ".join(p.name for p in imgs))
            cmd = [self.cli, "-p", "--output-format", "json", "--model", self.model, "--system-prompt", system,
                   "--strict-mcp-config", "--no-session-persistence", "--setting-sources", "project",
                   "--tools", ",".join(tools) if tools else "", "--max-turns", "30"]
            if tools:
                cmd += ["--allowedTools", ",".join(tools)]
            for attempt in range(3):
                r = _run(cmd, stdin_text=text, timeout=CLI_TIMEOUT_S, cwd=td)
                try:
                    d = json.loads(r.stdout)
                except json.JSONDecodeError:
                    raise AIError(f"Claude Code returned no JSON (exit {r.returncode}): {(r.stderr or r.stdout)[:300]}")
                result = d.get("result") or ""
                if d.get("is_error"):
                    # Another Claude Code process refreshing the same sign-in: transient, wait and retry.
                    if "refresh" in result.lower() and attempt < 2:
                        time.sleep(15)
                        continue
                    raise AIError(f"Claude Code: {result[:300]}")
                # The images are only seen through the Read tool: a one-turn answer means none was opened.
                if imgs and int(d.get("num_turns") or 0) < 2 and attempt < 2:
                    text += ("\n\nYour previous answer did not open the images. Open each image file with the "
                             "Read tool first, then answer.")
                    continue
                # Claude Code also uses a small model for web-search sub-steps: report the main one (most output).
                usage = d.get("modelUsage") or {}
                used = max(usage, key=lambda k: (usage[k] or {}).get("outputTokens", 0)) if usage else self.model
                tokens = d.get("usage") or {}
                meta = {"turns": d.get("num_turns"), "cli_attempts": attempt + 1,
                        "api_seconds": round((d.get("duration_api_ms") or 0) / 1000, 1),
                        "input_tokens": tokens.get("input_tokens"), "output_tokens": tokens.get("output_tokens"),
                        "cache_read_tokens": tokens.get("cache_read_input_tokens")}
                return AIResult(result, self.name, used, _urls(result), meta)
        raise AIError("Claude Code: sign-in refresh kept failing")


class CodexCLIProvider(Provider):
    """Codex exec (read-only sandbox), signed in with the user's ChatGPT account (or OPENAI_API_KEY it finds)."""
    name, via = "openai", "cli"

    def __init__(self, model, vision_model=None, cli=None):
        super().__init__(model or "", vision_model)
        self.cli = cli or find_cli("openai")
        if not self.cli:
            raise AIError("Codex CLI not found")
        if self.model and not MODEL_RE.match(self.model):
            raise AIError(f"invalid model name {self.model!r}")

    def chat(self, system, prompt, web_search=False, images=None, max_tokens=8000):
        with tempfile.TemporaryDirectory(prefix="l3d_codex_") as td:
            out = Path(td) / "last_message.txt"
            imgs = _small_images(images, Path(td))
            cmd = [self.cli, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral",
                   "-c", f'web_search="{"live" if web_search else "disabled"}"', "-o", str(out)]
            if self.model:
                cmd += ["-m", self.model]
            for p in imgs:
                cmd += ["-i", str(p)]
            cmd.append("-")  # prompt from stdin: no length limit, nothing passed through the shell
            text = f"{system}\n\nDo not run commands or edit files; answer from your knowledge" \
                   f"{' and web search' if web_search else ''}.\n\n{prompt}"
            r = _run(cmd, stdin_text=text, timeout=CLI_TIMEOUT_S, cwd=td)
            reply = out.read_text(encoding="utf-8").strip() if out.exists() else ""
            if r.returncode != 0 or not reply:
                tail = (r.stderr or r.stdout).strip().splitlines()[-6:]
                raise AIError(f"Codex exit {r.returncode}: {' | '.join(tail)[:400]}")
            m = re.search(r"^model:\s*(\S+)", r.stderr or "", re.M)
            return AIResult(reply, self.name, m.group(1) if m else (self.model or "codex default"), _urls(reply))


class ClaudeEnvProvider(Provider):
    """Anthropic SDK with ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN already set on this machine."""
    name, via = "claude", "env"

    def __init__(self, model, vision_model=None):
        super().__init__(model, vision_model)
        env = machine_env()
        if not (env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN")):
            raise AIError("No ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN in the environment")
        import anthropic
        self.client = anthropic.Anthropic(api_key=env.get("ANTHROPIC_API_KEY") or None,
                                          auth_token=env.get("ANTHROPIC_AUTH_TOKEN") or None,
                                          base_url=env.get("ANTHROPIC_BASE_URL") or None)

    def chat(self, system, prompt, web_search=False, images=None, max_tokens=8000):
        content = []
        for p in images or []:
            mt, data = _img_b64(Path(p))
            content.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}})
        content.append({"type": "text", "text": prompt})
        model = self.vision_model if images else self.model
        kwargs = {"model": model, "max_tokens": max_tokens, "system": system,
                  "messages": [{"role": "user", "content": content}]}
        if web_search:
            kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}]
        msg = self.client.messages.create(**kwargs)
        texts, cites = [], []
        for block in msg.content:
            if getattr(block, "type", "") == "text":
                texts.append(block.text)
                for c in getattr(block, "citations", None) or []:
                    url = getattr(c, "url", None)
                    if url:
                        cites.append({"url": url, "title": getattr(c, "title", "")})
        return AIResult("\n".join(texts), self.name, model, cites)


class OpenAIEnvProvider(Provider):
    """OpenAI SDK with OPENAI_API_KEY (or Azure OpenAI variables) already set on this machine."""
    name, via = "openai", "env"

    def __init__(self, model, vision_model=None):
        super().__init__(model, vision_model)
        env = machine_env()
        import openai
        if env.get("OPENAI_API_KEY"):
            self.client = openai.OpenAI(api_key=env["OPENAI_API_KEY"], base_url=env.get("OPENAI_BASE_URL") or None)
        elif env.get("AZURE_OPENAI_API_KEY") and env.get("AZURE_OPENAI_ENDPOINT"):
            self.client = openai.AzureOpenAI(api_key=env["AZURE_OPENAI_API_KEY"],
                                             azure_endpoint=env["AZURE_OPENAI_ENDPOINT"],
                                             api_version=env.get("OPENAI_API_VERSION") or "2025-04-01-preview")
        else:
            raise AIError("No OPENAI_API_KEY (or AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT) in the environment")

    def chat(self, system, prompt, web_search=False, images=None, max_tokens=8000):
        content = [{"type": "input_text", "text": prompt}]
        for p in images or []:
            mt, data = _img_b64(Path(p))
            content.append({"type": "input_image", "image_url": f"data:{mt};base64,{data}"})
        model = self.vision_model if images else self.model
        kwargs = {"model": model, "instructions": system, "input": [{"role": "user", "content": content}],
                  "max_output_tokens": max_tokens}
        if web_search:
            kwargs["tools"] = [{"type": "web_search"}]
        resp = self.client.responses.create(**kwargs)
        cites = []
        for item in getattr(resp, "output", []) or []:
            for part in getattr(item, "content", None) or []:
                for ann in getattr(part, "annotations", None) or []:
                    url = getattr(ann, "url", None)
                    if url:
                        cites.append({"url": url, "title": getattr(ann, "title", "")})
        return AIResult(resp.output_text or "", self.name, model, cites)


CLASSES = {("claude", "cli"): ClaudeCLIProvider, ("claude", "env"): ClaudeEnvProvider,
           ("openai", "cli"): CodexCLIProvider, ("openai", "env"): OpenAIEnvProvider}


def _build(name: str, st: dict | None = None) -> Provider:
    st = st or status(name)
    if not st["resolved"]:
        want = st["via"]
        raise AIError(f"{LABELS[name]} is not connected"
                      + (f" via {want}" if want != "auto" else "")
                      + ": sign in with the CLI (web login) or set its environment variables")
    cfg = settings_mod.load()["providers"][name]
    if st["resolved"] == "cli":
        return CLASSES[(name, "cli")](cfg.get("cli_model", ""), cfg.get("cli_model") or None, cli=st["cli_path"])
    return CLASSES[(name, "env")](cfg["model"], cfg.get("vision_model"))


def available() -> list[str]:
    s = settings_mod.load()
    return [n for n in NAMES if s["providers"][n].get("enabled") and status(n)["resolved"]]


def get(task: str = "research", name: str | None = None) -> Provider | None:
    """Provider for a task: explicit name, routing setting, or the first enabled provider."""
    s = settings_mod.load()
    avail = available()
    if not avail:
        return None
    choice = name or s["routing"].get(task, "auto")
    if choice == "auto" or choice not in avail:
        choice = avail[0]
    return _build(choice)


def make(name: str) -> Provider:
    return _build(name)


def extract_json(text: str):
    """First JSON object in a reply (fenced ```json``` block or bare braces)."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = m.group(1) if m else None
    if raw is None:
        start = text.find("{")
        if start < 0:
            raise AIError("no JSON object in the reply")
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    raw = text[start:i + 1]
                    break
    if raw is None:
        raise AIError("unterminated JSON object in the reply")
    return json.loads(raw)


def ask_json(provider: Provider, system: str, prompt: str, validator=None, web_search=False, images=None, retries=1,
             accept_invalid=False):
    """Ask for JSON; validate with validator(obj) -> list[str] errors; retry with the errors.

    accept_invalid: after the last retry, return the last well-formed object anyway (schema-valid only), with the
    remaining errors in res.validation_errors, so the caller can show them instead of discarding the answer."""
    last_err = None
    last_ok = None
    p = prompt
    log = _call_log.get()
    step = sys._getframe(1).f_code.co_name                   # the pipeline step asking: massing, judge, facade_pass...
    request = log.new_request() if log else 0

    def note(attempt, outcome, started, res=None, check_started=None, errors=()):
        if log:
            now = time.perf_counter()
            log.record({"step": step, "request": request, "attempt": attempt, "max_attempts": retries + 1,
                        "outcome": outcome, "seconds": round((check_started or now) - started, 1),
                        "check_seconds": round(now - check_started, 2) if check_started else 0,
                        "provider": getattr(res, "provider", provider.name), "model": getattr(res, "model", None),
                        "images": len(images or []), "prompt_chars": len(p),
                        "reply_chars": len(res.text) if res else 0, "errors": [str(e)[:200] for e in list(errors)[:8]],
                        **(getattr(res, "meta", None) or {})})

    for attempt in range(1, retries + 2):
        started = time.perf_counter()
        try:
            res = provider.chat(system, p, web_search=web_search, images=images)
        except Exception as exc:
            note(attempt, "error", started, errors=[exc])
            raise
        try:
            obj = extract_json(res.text)
        except (AIError, json.JSONDecodeError) as exc:
            last_err = str(exc)
            note(attempt, "invalid_json", started, res, errors=[exc])
            p = prompt + f"\n\nYour previous reply was not valid JSON ({last_err}). Reply with one JSON object only."
            continue
        check_started = time.perf_counter()
        errs = validator(obj) if validator else []
        note(attempt, "rejected" if errs else "ok", started, res, check_started, errs)
        if not errs:
            res.validation_errors = []
            return obj, res
        last_ok = (obj, res, errs)
        last_err = "; ".join(errs[:8])
        p = prompt + f"\n\nYour previous JSON failed validation: {last_err}. Fix it and reply with one JSON object only."
    if accept_invalid and last_ok is not None:
        from ..spec import validate_massing
        obj, res, errs = last_ok
        well_formed = "parts" in obj and not validate_massing(obj)
        if well_formed:
            res.validation_errors = errs
            return obj, res
    raise AIError(f"AI output invalid after retries: {last_err}")
