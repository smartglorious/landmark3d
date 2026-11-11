"""AI path with a fake provider: facts merge, massing validation, retry on invalid JSON."""
import json

import pytest

from landmark3d.research import ai


class FakeProvider(ai.Provider):
    name = "fake"

    def __init__(self, replies):
        super().__init__("fake-model")
        self.replies = list(replies)
        self.prompts = []

    def chat(self, system, prompt, web_search=False, images=None, max_tokens=8000):
        self.prompts.append(prompt)
        return ai.AIResult(self.replies.pop(0), "fake", "fake-model", [{"url": "https://example.org/src"}])


def _footprint(prj, lat=10.0, lon=106.0, half=0.0003):
    ring = [[lon - half, lat - half], [lon + half, lat - half], [lon + half, lat + half], [lon - half, lat + half],
            [lon - half, lat - half]]
    prj.write_json("input/geo/footprint.geojson", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [ring]}}]})


def test_ask_json_retries_then_validates():
    from landmark3d import spec
    good = {"parts": [{"id": "main", "footprint": "main", "base_z": 0, "height": 9, "roof": {"type": "hip"}}]}
    p = FakeProvider(["not json at all", "```json\n" + json.dumps(good) + "\n```"])
    obj, res = ai.ask_json(p, "sys", "prompt", validator=spec.validate_massing)
    assert obj == good and len(p.prompts) == 2 and "not valid JSON" in p.prompts[1]


def test_call_log_records_each_attempt_and_why_it_was_rejected(tmp_path):
    good, bad = '{"ok": true}', '{"ok": false}'
    p = FakeProvider(["not json", bad, good])
    calls = ai.CallLog(tmp_path / "ai_calls.jsonl", "spec")

    def my_step():
        return ai.ask_json(p, "sys", "secret prompt text", retries=2,
                           validator=lambda o: [] if o["ok"] else ["part tower floats"])
    with ai.logging_calls(calls):
        my_step()
    lines = [json.loads(x) for x in (tmp_path / "ai_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["outcome"] for e in lines] == ["invalid_json", "rejected", "ok"]
    assert [e["attempt"] for e in lines] == [1, 2, 3] and {e["request"] for e in lines} == {1}
    assert lines[1]["errors"] == ["part tower floats"] and lines[0]["step"] == "my_step" and lines[0]["phase"] == "spec"
    assert "secret prompt text" not in (tmp_path / "ai_calls.jsonl").read_text(encoding="utf-8")
    s = calls.summary()
    assert s["requests"] == 1 and s["calls"] == 3 and s["retries"] == 2
    assert s["by_step"]["my_step"]["calls"] == 3 and s["by_step"]["my_step"]["rejected"] == 2


def test_call_log_records_a_provider_failure_and_reraises(tmp_path):
    class Broken(FakeProvider):
        def chat(self, *a, **k):
            raise ai.AIError("usage limit reached")
    calls = ai.CallLog(tmp_path / "ai_calls.jsonl", "spec")
    with ai.logging_calls(calls), pytest.raises(ai.AIError):
        ai.ask_json(Broken([]), "s", "p")
    assert calls.entries[0]["outcome"] == "error" and "usage limit" in calls.entries[0]["errors"][0]


def test_call_log_failure_never_breaks_the_call(tmp_path):
    p = FakeProvider(['{"a": 1}'])
    with ai.logging_calls(ai.CallLog(tmp_path / "missing" / "dir" / "x.jsonl", "spec")):
        assert ai.ask_json(p, "s", "p")[0] == {"a": 1}


def test_no_log_outside_a_phase(tmp_path):
    assert ai.ask_json(FakeProvider(['{"a": 1}']), "s", "p")[0] == {"a": 1}


def test_run_phase_adds_a_usage_summary_event(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    from landmark3d import pipeline, settings
    from landmark3d.project import Project
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    prj = Project.create("Log Hall", "Nowhere")

    def fake_spec(p):
        def massing():
            return ai.ask_json(FakeProvider(['{"a": 1}']), "s", "p")
        massing()
        return {"parts": [], "estimated_triangles": 0, "defaults_used": [], "issues": []}
    monkeypatch.setattr(pipeline, "build_spec", fake_spec)
    assert pipeline.run_phase(prj, "spec") == "done"
    assert (prj.root / "logs" / "ai_calls.jsonl").exists()
    usage = [e for e in prj.events(20) if e["msg"] == "ai usage"]
    assert usage and usage[0]["data"]["calls"] == 1 and "massing" in usage[0]["data"]["by_step"]


def test_ai_massing_used_in_spec(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d import pipeline
    from landmark3d.project import Project
    prj = Project.create("Domed Hall", "Somewhere", lat=10.0, lon=106.0)
    _footprint(prj)
    pipeline.submit_inputs(prj, {"main_wall_height_m": 12, "roof_form": "flat"})
    massing = {"parts": [
        {"id": "main", "kind": "hall", "footprint": "main", "base_z": 0, "height": 12, "roof": {"type": "flat", "parapet": 1}},
        {"id": "dome_1", "kind": "dome", "footprint": {"circle": {"cx": 0, "cy": 0, "r": 9}}, "base_z": 12, "height": 4,
         "roof": {"type": "dome", "height": 9}}],
        "materials": {"wall": {"srgb": [230, 220, 200]}, "roof": {"srgb": [90, 110, 120]}},
        "volumes": [{"name": "hall", "part": "main"}, {"name": "dome", "part": "dome_1"}]}
    fake = FakeProvider([json.dumps(massing)])
    monkeypatch.setattr(ai, "get", lambda task="research", name=None: fake)
    sp = pipeline.build_spec(prj)
    assert [p["id"] for p in sp["parts"]] == ["main", "dome_1"]
    assert sp["materials"]["roof"]["srgb"] == [90, 110, 120]
    assert sp["provenance"]["part.dome_1"]["method"] == "ai_massing:fake"
    assert "Domed Hall" in fake.prompts[0]
