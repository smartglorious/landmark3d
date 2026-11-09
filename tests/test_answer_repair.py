"""What the tool can correct in an AI answer must not cost another AI call."""
import json

from PIL import Image

from landmark3d import spec
from landmark3d.research import ai


def part(pid, rect_w, base, height, level):
    return {"id": pid, "footprint": {"rect": {"cx": 0, "cy": 0, "w": rect_w, "d": rect_w}}, "base_z": base, "height": height,
            "roof": {"type": "flat"}, "facade": {"opening_type": "arch", "level_height": level}}


def answer():
    return {"parts": [part("hall", 30, 0, 12, 4), part("deck", 8, 12, 0.9, 0.9)],
            "materials": {k: {"srgb": [1, 2, 3]} for k in ("wall", "roof", "trim")}}


def test_low_storey_is_invalid_until_repaired():
    from landmark3d import pipeline
    assert any("level_height" in e for e in spec.validate_massing(answer()))
    fixed = pipeline.repair_answer(answer())
    deck = fixed["parts"][1]["facade"]
    assert deck["level_height"] == pipeline.MIN_LEVEL_M and deck["openings"] is False
    assert spec.validate_massing(fixed) == []


def test_answers_of_the_wrong_shape_are_left_to_the_check():
    from landmark3d import pipeline
    for odd in ({"verdict": "match"}, {"parts": "none"}, {"parts": [1]}, {"parts": [{"height": "tall", "facade": {}}]}, []):
        assert pipeline.repair_answer(odd) == odd


def test_repair_costs_no_extra_call_and_is_logged(tmp_path):
    from landmark3d import pipeline

    class Fake(ai.Provider):
        calls = 0

        def chat(self, *a, **k):
            Fake.calls += 1
            return ai.AIResult(json.dumps(answer()), "fake", "m", [])
    log = ai.CallLog(tmp_path / "calls.jsonl", "spec")
    with ai.logging_calls(log):
        obj, _ = ai.ask_json(Fake("m"), "s", "p", validator=spec.validate_massing, repair=pipeline.repair_answer)
    assert Fake.calls == 1 and obj["parts"][1]["facade"]["level_height"] == pipeline.MIN_LEVEL_M
    assert log.entries[0]["outcome"] == "ok" and log.entries[0]["repaired"] is True


def test_a_clean_answer_is_not_marked_repaired(tmp_path):
    from landmark3d import pipeline

    class Fake(ai.Provider):
        def chat(self, *a, **k):
            return ai.AIResult(json.dumps({"parts": [part("hall", 30, 0, 12, 4)]}), "fake", "m", [])
    log = ai.CallLog(tmp_path / "calls.jsonl", "spec")
    with ai.logging_calls(log):
        ai.ask_json(Fake("m"), "s", "p", repair=pipeline.repair_answer)
    assert log.entries[0]["repaired"] is False


def test_massing_and_photo_review_repair_before_checking(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    from landmark3d import pipeline, settings
    from landmark3d.engine import runner
    from landmark3d.project import Project
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects"), "facade_pass": False, "judge": False,
                     "auto_textures": False, "photo_review_rounds": 1})
    prj = Project.create("Repair Test", "X", lat=10.0, lon=106.0)
    half = 0.0003
    ring = [[106 - half, 10 - half], [106 + half, 10 - half], [106 + half, 10 + half], [106 - half, 10 + half],
            [106 - half, 10 - half]]
    prj.write_json("input/geo/footprint.geojson", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [ring]}}]})
    pipeline.submit_inputs(prj, {"main_wall_height_m": 12, "roof_form": "flat"})
    Image.new("RGB", (200, 150), (100, 100, 100)).save(prj.path("input", "photos", "a.jpg"))
    prj.write_json("input/sources.json", {"sources": [{"id": "U1", "kind": "photo", "path": "input/photos/a.jpg", "origin": "user"}]})

    def fake_preview(p, spec_, views, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        res = []
        for name in views:
            Image.new("RGB", (96, 64)).save(out_dir / f"{name}.png")
            res.append(out_dir / f"{name}.png")
        return res
    monkeypatch.setattr(runner, "preview", fake_preview)
    view = {"photo": 1, "azimuth_deg": 270, "elevation_deg": 8}
    monkeypatch.setattr(pipeline, "match_viewpoints", lambda *a, **k: ([view], {"round": 0, "step": "viewpoints"}))
    monkeypatch.setattr(pipeline, "select_best", lambda p, pr, cands, *a, **k: (cands[-1], {"step": "select"}))
    reply = dict(answer(), verdict="revise", differences=["d"], views=[view], scores={k: 3 for k in pipeline.REVIEW_ASPECTS})
    calls = []

    class Fake(ai.Provider):
        def chat(self, system, prompt, **k):
            calls.append(prompt)
            return ai.AIResult(json.dumps(reply), "fake", "m", [])
    monkeypatch.setattr(ai, "get", lambda task="research", name=None: Fake("m"))
    sp = pipeline.build_spec(prj)
    assert not any("failed validation" in c for c in calls)            # no AI call was spent on the low storey
    assert any("deck" == p["id"] for p in sp["parts"])
    deck = next(p for p in sp["parts"] if p["id"] == "deck")
    assert deck["facade"]["level_height"] == pipeline.MIN_LEVEL_M
