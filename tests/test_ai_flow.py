"""AI path with a fake provider: facts merge, massing validation, retry on invalid JSON."""
import json

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
        "materials": {"wall": {"srgb": [230, 220, 200]}, "roof": {"srgb": [90, 110, 120]}}}
    fake = FakeProvider([json.dumps(massing)])
    monkeypatch.setattr(ai, "get", lambda task="research", name=None: fake)
    sp = pipeline.build_spec(prj)
    assert [p["id"] for p in sp["parts"]] == ["main", "dome_1"]
    assert sp["materials"]["roof"]["srgb"] == [90, 110, 120]
    assert sp["provenance"]["part.dome_1"]["method"] == "ai_massing:fake"
    assert "Domed Hall" in fake.prompts[0]
