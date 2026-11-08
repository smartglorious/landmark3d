"""AI connection routes: CLI (web sign-in) and environment variables. No real CLI or network is used."""
import json
import sys
from pathlib import Path

import pytest

FAKE_CLAUDE = r'''
import json, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
print(json.dumps({"type": "result", "is_error": False,
                  "result": "```json\n" + json.dumps({"args": args, "prompt": prompt}) + "\n```\nsee https://example.org/a",
                  "modelUsage": {"claude-sonnet-5-5": {}}}))
'''

FAKE_CODEX = r'''
import json, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
out = args[args.index("-o") + 1]
open(out, "w", encoding="utf-8").write(json.dumps({"args": args, "prompt": prompt}))
sys.stderr.write("model: gpt-fake\n")
'''


def _fake_cli(tmp_path: Path, name: str, body: str) -> str:
    script = tmp_path / f"{name}.py"
    script.write_text(body, encoding="utf-8")
    if sys.platform == "win32":
        launcher = tmp_path / f"{name}.cmd"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        launcher = tmp_path / name
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return str(launcher)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    from landmark3d import settings
    importlib.reload(settings)
    return tmp_path


def test_claude_cli_command_and_parse(home):
    from landmark3d.research import ai
    p = ai.ClaudeCLIProvider("sonnet", cli=_fake_cli(home, "claude", FAKE_CLAUDE))
    res = p.chat("SYSTEM TEXT", "find facts", web_search=True)
    obj = ai.extract_json(res.text)
    args = obj["args"]
    assert obj["prompt"] == "find facts"  # prompt goes through stdin, not the command line
    assert args[args.index("--tools") + 1] == "WebSearch,WebFetch"
    assert args[args.index("--system-prompt") + 1] == "SYSTEM TEXT"
    assert "--strict-mcp-config" in args and "--no-session-persistence" in args
    assert res.model == "claude-sonnet-5-5" and res.citations[0]["url"] == "https://example.org/a"


def test_codex_cli_command_and_parse(home):
    from landmark3d.research import ai
    p = ai.CodexCLIProvider("", cli=_fake_cli(home, "codex", FAKE_CODEX))
    res = p.chat("SYS", "massing please", web_search=False)
    obj = json.loads(res.text)
    args = obj["args"]
    assert args[:2] == ["exec", "--skip-git-repo-check"]
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert 'web_search="disabled"' in args and args[-1] == "-" and "-m" not in args
    assert "massing please" in obj["prompt"] and obj["prompt"].startswith("SYS")
    assert res.model == "gpt-fake"


def test_status_routes(home, monkeypatch):
    from landmark3d import settings
    from landmark3d.research import ai
    monkeypatch.setattr(ai, "machine_env", lambda: {"PATH": ""})
    monkeypatch.setattr(ai, "find_cli", lambda name: None)
    st = ai.status("claude")
    assert st["resolved"] is None and st["env_vars"] == []
    # Environment variable only -> env route; the value is never reported.
    monkeypatch.setattr(ai, "machine_env", lambda: {"PATH": "", "ANTHROPIC_API_KEY": "secret-value"})
    st = ai.status("claude")
    assert st["resolved"] == "env" and st["env_vars"] == ["ANTHROPIC_API_KEY"]
    assert "secret-value" not in json.dumps(st)
    # Signed-in CLI wins under auto; forcing env keeps env.
    monkeypatch.setattr(ai, "find_cli", lambda name: "/fake/claude")
    monkeypatch.setattr(ai, "cli_login_status", lambda name, cli=None: {"logged_in": True, "method": "claude.ai", "account": ""})
    assert ai.status("claude")["resolved"] == "cli"
    settings.update({"providers": {"claude": {"via": "env"}}})
    assert ai.status("claude")["resolved"] == "env"
    settings.update({"providers": {"claude": {"via": "cli"}}})
    monkeypatch.setattr(ai, "cli_login_status", lambda name, cli=None: {"logged_in": False, "method": "", "account": ""})
    monkeypatch.setattr(ai, "machine_env", lambda: {"PATH": ""})
    assert ai.status("claude")["resolved"] is None
    assert ai.available() == []


def test_provider_api_has_no_key_endpoints(home, monkeypatch):
    from fastapi.testclient import TestClient
    from landmark3d.research import ai
    from landmark3d.server import app
    monkeypatch.setattr(ai, "find_cli", lambda name: None)
    monkeypatch.setattr(ai, "machine_env", lambda: {"PATH": "", "OPENAI_API_KEY": "sk-should-not-leak"})
    c = TestClient(app)
    st = c.get("/api/providers").json()
    assert st["openai"]["resolved"] == "env" and st["claude"]["resolved"] is None
    assert "sk-should-not-leak" not in json.dumps(st)
    assert c.put("/api/providers/claude/key", json={"key": "x"}).status_code in (404, 405)
    r = c.put("/api/providers/openai", json={"enabled": True, "via": "cli", "cli_model": "gpt-5.5", "cli_path": "C:/evil.exe"})
    assert r.status_code == 200
    from landmark3d import settings
    assert settings.load()["providers"]["openai"]["cli_path"] == ""  # not settable from the web
    assert c.put("/api/providers/openai", json={"via": "magic"}).status_code == 400
    assert c.put("/api/providers/openai", json={"cli_model": "x; rm -rf /"}).status_code == 400
    assert c.post("/api/providers/openai/login").status_code == 400  # no CLI installed
