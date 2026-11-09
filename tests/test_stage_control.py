"""Going back to an approval stage, restarting with new instructions, and the About details."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def prj(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d.project import PHASES, Project
    p = Project.create("Test Hall", "Nowhere", lat=10.0, lon=106.0, notes="ghi chú cũ")
    for ph in PHASES:
        p.set_phase(ph["id"], "approved" if ph["approval"] else "done")
    return p


def status(prj):
    return {k: v["status"] for k, v in prj.state()["phases"].items()}


@pytest.fixture()
def client(monkeypatch):
    from landmark3d import pipeline
    from landmark3d.server import app
    monkeypatch.setattr(pipeline, "run_until_blocked", lambda p: {"stopped_at": None})
    return TestClient(app)


@pytest.mark.parametrize("stage,later", [("sources_review", "completion"), ("spec_review", "build"),
                                         ("build_review", "release"), ("release_review", None)])
def test_reopen_keeps_earlier_results_and_resets_later_phases(prj, stage, later):
    from landmark3d import pipeline
    pipeline.reopen(prj, stage, "xem lại")
    s = status(prj)
    assert s[stage] == "waiting_approval"
    assert s["research"] == "done"
    if later:
        assert s[later] == "pending"
    assert prj.state()["approvals"][-1]["decision"] == "reopened"


def test_reopen_a2_does_not_rerun_the_spec(prj):
    from landmark3d import pipeline
    pipeline.reopen(prj, "spec_review")
    assert status(prj)["spec"] == "done"
    assert pipeline.run_until_blocked(prj) == {"stopped_at": "spec_review", "reason": "approval"}


def test_cannot_reopen_a_stage_not_reached_or_an_automatic_phase(prj):
    from landmark3d import pipeline
    with pytest.raises(ValueError):
        pipeline.reopen(prj, "build")
    prj.set_phase("build", "pending")
    with pytest.raises(ValueError):
        pipeline.reopen(prj, "build_review")


def test_restart_replaces_instructions_and_forgets_old_change_requests(prj):
    from landmark3d import pipeline
    pipeline.approve(prj, "build_review", "changes_requested", "lỗi cũ")
    assert pipeline.change_requests(prj.state()) == ["lỗi cũ"]
    pipeline.restart(prj, " tháp cao hơn ", " dùng mái ngói ")
    st = prj.state()
    assert st["notes"] == "tháp cao hơn" and st["advanced_prompt"] == "dùng mái ngói"
    assert pipeline.change_requests(st) == []
    s = status(prj)
    assert s["doctor"] == "done" and s["research"] == s["sources_review"] == s["spec"] == "pending"
    pipeline.approve(prj, "build_review", "changes_requested", "lỗi mới")
    assert pipeline.change_requests(prj.state()) == ["lỗi mới"]


def test_restart_from_spec_keeps_research(prj):
    from landmark3d import pipeline
    pipeline.restart(prj, start="spec")
    s = status(prj)
    assert s["research"] == "done" and s["sources_review"] == "approved" and s["spec"] == "pending"
    with pytest.raises(ValueError):
        pipeline.restart(prj, start="release")


def test_instructions_are_added_to_ai_prompts():
    from landmark3d.research import agent
    assert agent.user_directive({}) == ""
    text = agent.user_directive({"notes": "ghi chú", "advanced_prompt": "nâng cao"})
    assert "ghi chú" in text and "nâng cao" in text
    assert "ghi chú" not in agent.user_directive({"notes": "ghi chú", "advanced_prompt": "nâng cao"}, notes=False)


def test_api_reopen_and_restart(prj, client):
    r = client.post(f"/api/projects/{prj.id}/reopen", json={"phase": "spec_review"})
    assert r.status_code == 200 and status(prj)["spec_review"] == "waiting_approval"
    assert client.post(f"/api/projects/{prj.id}/reopen", json={"phase": "spec"}).status_code == 409
    r = client.post(f"/api/projects/{prj.id}/restart", json={"notes": "n", "advanced_prompt": "a"})
    assert r.status_code == 200 and r.json()["started"] is True
    assert prj.state()["advanced_prompt"] == "a"
    assert client.post(f"/api/projects/{prj.id}/restart", json={"start": "build"}).status_code == 422


def test_api_refuses_while_running(prj, client, monkeypatch):
    from landmark3d import server
    monkeypatch.setattr(server, "_running", lambda pid: True)
    assert client.post(f"/api/projects/{prj.id}/reopen", json={"phase": "spec_review"}).status_code == 409
    assert client.post(f"/api/projects/{prj.id}/restart", json={}).status_code == 409


def test_about_reports_version_and_publisher(client):
    from landmark3d import __version__
    d = client.get("/api/about").json()
    assert d["version"] == __version__ and d["publisher"]["name"] == "Persona by MTech Digital"
    for name in ("persona-logo.png", "favicon.png"):
        assert client.get(f"/static/{name}").status_code == 200
