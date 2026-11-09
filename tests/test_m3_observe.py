"""M3: the observation step, with a fake provider. Offline; no AI is called."""
import json

import pytest
from PIL import Image

from landmark3d import observe
from landmark3d.project import Project
from landmark3d.research import ai


def good_doc(source_id="SRC-1", part="main"):
    return {"version": 1, "observations": [{
        "id": "FO-1", "source_id": source_id, "part_id": part, "facade_run": 0,
        "quad": [[0.08, 0.18], [0.92, 0.19], [0.94, 0.90], [0.06, 0.89]],
        "floor_lines": [0.90, 0.67, 0.43, 0.18], "bay_lines": [0.08, 0.2, 0.32, 0.44, 0.56, 0.68, 0.8, 0.92],
        "elements": [{"id": "FE-1", "type": "arched_window", "bbox": [0.22, 0.45, 0.30, 0.63], "floor": 1, "bay": 1,
                      "confidence": 0.9, "provenance": "observed"}],
        "confidence": 0.85}]}


class Fake(ai.Provider):
    name = "fake"

    def __init__(self, replies):
        super().__init__("fake-model")
        self.replies = list(replies)
        self.prompts = []

    def chat(self, system, prompt, web_search=False, images=None, max_tokens=8000):
        self.prompts.append((prompt, images))
        return ai.AIResult(json.dumps(self.replies.pop(0)), "fake", "fake-model", [])


@pytest.fixture()
def prj(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    p = Project.create("Observe Test", "X")
    photo = p.path("input", "photos", "a.jpg")
    Image.new("RGB", (200, 150), (90, 90, 90)).save(photo)
    return p


SOURCE = {"kind": "photo", "use": "reference_only", "path": "input/photos/a.jpg"}


def test_a_valid_answer_is_accepted_on_the_first_try(prj):
    p = Fake([good_doc()])
    doc = observe.propose(p, "Test Building", "main", "SRC-1", prj.root / "input/photos/a.jpg", SOURCE, {"main"})
    assert doc["observations"][0]["id"] == "FO-1"
    assert len(p.prompts) == 1
    prompt, images = p.prompts[0]
    assert "SRC-1" in prompt and "main" in prompt and images


def test_an_invalid_answer_is_retried_with_its_errors_then_accepted(prj):
    bad = good_doc()
    bad["observations"][0]["floor_lines"] = [0.1, 0.5, 0.3]        # not ordered
    p = Fake([bad, good_doc()])
    doc = observe.propose(p, "Test Building", "main", "SRC-1", prj.root / "input/photos/a.jpg", SOURCE, {"main"})
    assert doc["observations"][0]["id"] == "FO-1"
    assert len(p.prompts) == 2 and "failed validation" in p.prompts[1][0]


def test_no_valid_answer_raises_and_writes_nothing(prj):
    bad = good_doc()
    bad["observations"][0]["part_id"] = "tower"                     # unknown part, every time
    p = Fake([bad, bad, bad])
    with pytest.raises(ai.AIError):
        observe.propose(p, "Test Building", "main", "SRC-1", prj.root / "input/photos/a.jpg", SOURCE, {"main"})
    assert observe.load(prj) is None


def test_persist_writes_only_a_validated_document(prj):
    sources = {"SRC-1": SOURCE}
    assert observe.persist(prj, good_doc(), sources, {"main"}) == []
    assert observe.load(prj)["observations"][0]["id"] == "FO-1"
    errors = observe.persist(prj, good_doc(source_id="SRC-9"), sources, {"main"})
    assert errors and observe.load(prj)["observations"][0]["source_id"] == "SRC-1"   # previous file kept


def test_the_prompt_names_no_fixed_landmark():
    text = observe.build_prompt("Some Building", "main", "SRC-1")
    assert "Some Building" in text
    for word in ("Chợ", "UBND", "Bitexco", "Diamond", "Landmark 81"):
        assert word not in observe.PROMPT


def test_a_failed_photo_is_logged_and_the_valid_ones_are_kept(prj):
    from landmark3d.research import ai as ai_mod
    Image.new("RGB", (40, 40)).save(prj.path("input", "photos", "b.jpg"))
    sources = {"SRC-1": {"kind": "photo", "path": "input/photos/a.jpg", "use": "reference_only"},
               "SRC-2": {"kind": "photo", "path": "input/photos/b.jpg", "use": "reference_only"}}

    class Mixed(Fake):
        def chat(self, system, prompt, web_search=False, images=None, max_tokens=8000):
            if "SRC-2" in prompt:
                raise ai_mod.AIError("usage limit reached")
            return super().chat(system, prompt, web_search, images, max_tokens)
    p = Mixed([good_doc("SRC-1")] * 3)
    log, errors = observe.run_for_photos(prj, p, "Test Building", ["input/photos/a.jpg", "input/photos/b.jpg"],
                                         sources, {"main"})
    assert errors == []
    assert [e["result"] for e in log][0] == "accepted"
    assert log[1]["result"].startswith("failed") and "usage limit" in log[1]["result"]
    assert len(observe.load(prj)["observations"]) == 1


def test_a_path_that_is_not_a_photo_source_is_refused(prj):
    log, errors = observe.run_for_photos(prj, Fake([]), "Test Building", ["input/photos/none.jpg"],
                                         {"SRC-1": {"kind": "satellite", "path": "input/photos/none.jpg"}}, {"main"})
    assert log[0]["result"].startswith("not a photo source") and errors == []


def test_the_observation_setting_is_off_by_default():
    from landmark3d import settings
    assert settings.DEFAULTS["facade_observations"] is False


def test_texture_container_is_recorded_honestly():
    from landmark3d.engine import runner
    assert runner.texture_container() in ("ktx2", "jpeg")


def test_observation_ids_stay_unique_across_photos(prj):
    Image.new("RGB", (40, 40)).save(prj.path("input", "photos", "b.jpg"))
    sources = {"SRC-1": {"kind": "photo", "path": "input/photos/a.jpg", "use": "reference_only"},
               "SRC-2": {"kind": "photo", "path": "input/photos/b.jpg", "use": "reference_only"}}
    p = Fake([good_doc("SRC-1"), good_doc("SRC-2")])          # both answer FO-1 and FE-1
    log, errors = observe.run_for_photos(prj, p, "Test Building", ["input/photos/a.jpg", "input/photos/b.jpg"],
                                         sources, {"main"})
    assert errors == []
    doc = observe.load(prj)
    ids = [ob["id"] for ob in doc["observations"]]
    assert ids == ["SRC-1/FO-1", "SRC-2/FO-1"]
    assert len({el["id"] for ob in doc["observations"] for el in ob["elements"]}) == 2


def test_confident_observations_that_disagree_on_floors_are_a_conflict():
    from landmark3d import observation_grammar as og2
    a = good_doc("SRC-1")["observations"][0]
    b = good_doc("SRC-2")["observations"][0]
    b["id"] = "SRC-2/FO-1"
    b["floor_lines"] = [0.9, 0.6, 0.3, 0.1, 0.05]          # three floors, not two
    b["confidence"] = 0.9
    a["confidence"] = 0.9
    issues = og2.structure_conflicts([a, b], "main")
    assert issues and "floors" in issues[0]
    assert og2.policy(2, issues, 0.9) == "blocked"
    assert og2.structure_conflicts([a], "main") == []


def test_low_confidence_disagreement_is_not_a_conflict():
    from landmark3d import observation_grammar as og2
    a = good_doc("SRC-1")["observations"][0]
    b = good_doc("SRC-2")["observations"][0]
    b["floor_lines"] = [0.9, 0.6, 0.3, 0.1, 0.05]
    b["confidence"] = 0.75
    a["confidence"] = 0.9
    assert og2.structure_conflicts([a, b], "main") == []
