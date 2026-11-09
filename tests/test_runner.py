"""A Blender run that fails must never be reported through the previous run's files."""
import json
import subprocess
from pathlib import Path

import pytest

from landmark3d import doctor
from landmark3d.engine import runner


class FakeProject:
    def __init__(self, root: Path):
        self.root = root
        self.events = []

    def state(self):
        return {}

    def path(self, *parts):
        p = self.root.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def read_json(self, rel, default=None):
        p = self.root / rel
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    def write_json(self, rel, obj):
        p = self.path(*rel.split("/"))
        p.write_text(json.dumps(obj), encoding="utf-8")
        return p

    def log(self, message, data=None):
        self.events.append((message, data))


def blender_exits(monkeypatch, code, side_effect=None):
    def fake_run(cmd, **kw):
        assert kw["stdin"] is subprocess.DEVNULL
        if side_effect:
            side_effect()
        return subprocess.CompletedProcess(cmd, code, stdout="", stderr="")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(doctor, "find_blender", lambda: "blender")


@pytest.fixture
def prj(tmp_path, monkeypatch):
    from landmark3d import textures
    monkeypatch.setattr(textures, "build_job", lambda *a: ({}, [], None))
    p = FakeProject(tmp_path)
    p.write_json("spec/landmark_spec.json", {"budgets": {}})
    p.write_json("input/sources.json", {"sources": []})
    return p


def test_failed_preview_does_not_return_old_renders(prj, tmp_path, monkeypatch):
    out = tmp_path / "round2"
    out.mkdir()
    old = out / "photo1.png"
    old.write_bytes(b"old")
    blender_exits(monkeypatch, 3221225794)
    assert runner.preview(prj, {}, {"photo1": {}}, out) == []
    assert not old.exists()
    assert "0xC0000142" in (out / "preview.log").read_text(encoding="utf-8")
    assert prj.events[0][0] == "blender preview failed"


def test_preview_without_new_output_returns_nothing(prj, tmp_path, monkeypatch):
    out = tmp_path / "judge"
    out.mkdir()
    (out / "photo1.png").write_bytes(b"old")
    blender_exits(monkeypatch, 0)
    assert runner.preview(prj, {}, {"photo1": {}}, out) == []


def test_preview_returns_fresh_renders(prj, tmp_path, monkeypatch):
    out = tmp_path / "round1"
    blender_exits(monkeypatch, 0, side_effect=lambda: (out / "photo1.png").write_bytes(b"new"))
    assert runner.preview(prj, {}, {"photo1": {}}, out) == [out / "photo1.png"]


def test_failed_build_does_not_report_the_last_build(prj, monkeypatch):
    prj.write_json("reports/build_result.json", {"ok": True, "triangles": 3534, "bytes": 166916, "renders": []})
    blender_exits(monkeypatch, 0)
    res = runner.build(prj)
    assert res["ok"] is False and res["errors"][0] == "Blender did not produce a result"


def test_build_reports_blender_exit_code(prj, monkeypatch):
    prj.write_json("reports/build_result.json", {"ok": True, "triangles": 3534})
    blender_exits(monkeypatch, 3221225794)
    res = runner.build(prj)
    assert res["ok"] is False and "0xC0000142" in res["errors"][0]
    assert "0xC0000142" in prj.path("logs", "blender_build.log").read_text(encoding="utf-8")


def test_build_returns_fresh_result(prj, monkeypatch):
    blender_exits(monkeypatch, 0, side_effect=lambda: prj.write_json("reports/build_result.json", {"ok": True, "triangles": 9}))
    assert runner.build(prj) == {"ok": True, "triangles": 9}
