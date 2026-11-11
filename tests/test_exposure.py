"""Exposure of walls (which faces can be seen) and the feature checks built on it."""
from landmark3d import exposure

MAIN = [[-30, -20], [30, -20], [30, 20], [-30, 20]]


def rect(cx, cy, w, d, rot=0):
    return {"rect": {"cx": cx, "cy": cy, "w": w, "d": d, "rot_deg": rot}}


def nave_and_transept():
    """A nave along x and a transept across it: the transept's long walls face east/west into nothing on its ends,
    but its walls facing the nave (x direction) are buried where the nave passes through."""
    nave = {"id": "nave", "footprint": rect(0, 0, 50, 12), "base_z": 0, "height": 15}
    transept = {"id": "transept", "footprint": rect(0, 0, 10, 30), "base_z": 0, "height": 15,
                "features": [{"type": "disc", "facing_deg": 0, "z": 8, "diameter": 5}]}
    return [nave, transept]


def test_faces_measure_how_much_of_each_wall_is_buried():
    fx = exposure.faces(nave_and_transept(), MAIN)
    t = {round(r["facing_deg"]) % 360: r for r in fx["transept"]}
    assert t[90]["exposed"] == 1.0 and t[270]["exposed"] == 1.0          # its ends stick out of the nave
    assert 0.3 < t[0]["exposed"] < 0.7                                   # its long side passes through the nave


def test_a_feature_on_a_buried_wall_is_reported_with_the_exposed_walls():
    parts = nave_and_transept()
    parts[0]["footprint"] = rect(0, 0, 50, 26)                             # the nave now buries most of that wall
    iss = exposure.feature_issues(parts, MAIN)
    assert len(iss) == 1 and "transept" in iss[0] and "disc" in iss[0] and "90 deg" in iss[0]
    parts[1]["features"][0]["facing_deg"] = 90                            # moved to the free end: accepted
    assert exposure.feature_issues(parts, MAIN) == []


def test_point_features_outside_their_part_are_reported():
    parts = nave_and_transept()
    parts[1]["features"] = [{"type": "finial", "x": 20.0, "y": 0.0, "z": 15}]
    assert any("finial" in m and "outside" in m for m in exposure.feature_issues(parts, MAIN))
    parts[1]["features"][0]["x"] = 1.0
    assert exposure.feature_issues(parts, MAIN) == []


def test_a_part_hidden_on_every_side_is_left_to_the_massing_when_only_fixable_issues_are_asked():
    inner = {"id": "core", "footprint": rect(0, 0, 6, 6), "base_z": 0, "height": 10,
             "features": [{"type": "disc", "facing_deg": 0, "z": 5, "diameter": 2}]}
    outer = {"id": "hall", "footprint": rect(0, 0, 40, 30), "base_z": 0, "height": 12}
    assert exposure.feature_issues([outer, inner], MAIN)
    assert exposure.feature_issues([outer, inner], MAIN, fixable_only=True) == []


def test_exposure_text_lists_exposed_walls_with_compass_names():
    text = exposure.exposure_text(nave_and_transept(), MAIN)
    assert "transept: exposed" in text and "N" in text
    assert exposure.compass(90) == "N" and exposure.compass(0) == "E" and exposure.compass(225) == "SW"


def test_facade_pass_shows_the_current_layouts_and_the_exposed_walls(tmp_path, monkeypatch):
    """F4: the facade AI sees the build's own layout of each part (last image) and the exposed-walls list."""
    import importlib
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d import pipeline
    from landmark3d.project import Project
    from landmark3d.research import ai
    prj = Project.create("Sheet Hall", "X", lat=1.0, lon=2.0)
    parts = nave_and_transept()
    for p in parts:
        p.update(roof={"type": "gable", "height": 4},
                 facade={"opening_type": "arch", "bay_spacing": 4, "level_height": 5, "arch_shape": "round"})
    seen = {}

    def fake_ask(provider, system, prompt, validator=None, images=None, **k):
        seen["prompt"], seen["images"] = prompt, list(images or [])
        return {"parts": []}, None
    monkeypatch.setattr(ai, "ask_json", fake_ask)
    pipeline.facade_pass(prj, None, {"parts": parts, "materials": {}}, [], lambda o: [], prj.state(), main=MAIN)
    assert seen["images"] and str(seen["images"][-1]).endswith("facade_layouts.jpg")
    assert "CURRENT facade layout" in seen["prompt"] and "EXPOSED WALLS" in seen["prompt"]


def test_facade_answer_is_kept_with_bad_features_dropped(tmp_path, monkeypatch):
    """One feature of an unknown type or on a buried wall no longer throws away the whole facade answer."""
    import importlib
    import json
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d import pipeline
    from landmark3d.project import Project
    from landmark3d.research import ai
    prj = Project.create("Salvage Hall", "X", lat=1.0, lon=2.0)
    parts = nave_and_transept()
    parts[0]["footprint"] = rect(0, 0, 50, 26)
    for p in parts:
        p.update(roof={"type": "gable", "height": 4}, features=[])
    answer = {"parts": [{"id": "transept", "facade": {"opening_type": "arch", "bay_spacing": 4, "level_height": 5},
                         "features": [{"type": "disc", "facing_deg": 0, "z": 8, "diameter": 4},      # buried wall
                                      {"type": "disc", "facing_deg": 90, "z": 8, "diameter": 4},     # exposed end
                                      {"type": "statue", "facing_deg": 90, "z": 1}]}]}               # unknown

    class Fake(ai.Provider):
        def chat(self, system, prompt, **k):
            return ai.AIResult(json.dumps(answer), "fake", "m", [])
    out, entry = pipeline.facade_pass(prj, Fake("m"), {"parts": parts, "materials": {}}, [], lambda o: [],
                                      prj.state(), main=MAIN)
    t = next(p for p in out["parts"] if p["id"] == "transept")
    assert [f["facing_deg"] for f in t["features"]] == [90] and t["facade"]["opening_type"] == "arch"
    assert len(entry["features_dropped"]) == 2
