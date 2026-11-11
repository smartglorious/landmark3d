"""The texture step as its own phase with an approval (A2.5): generation first, photo crops only as the fallback,
per-part owner choices, the build without AI, and old projects getting the new phases."""
import importlib
import json

import numpy as np
import pytest
from PIL import Image


@pytest.fixture()
def prj(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d.project import Project
    p = Project.create("Phase Hall", "X", lat=1.0, lon=2.0)
    Image.new("RGB", (400, 300), (220, 190, 110)).save(p.path("input", "photos", "a.jpg"))
    p.write_json("input/sources.json", {"sources": [
        {"id": "SRC-1", "kind": "photo", "path": "input/photos/a.jpg", "licence": "cc_by_sa", "author": "A",
         "use": "reference_and_texture"}]})
    fac = {"bay_spacing": 4.0, "level_height": 5.0, "opening_width": 1.6, "opening_height": 2.6, "sill": 1.0,
           "opening_type": "rect"}
    spec = {"name": "Phase Hall", "footprint": {"local": [[-12, -5], [12, -5], [12, 5], [-12, 5]]},
            "parts": [{"id": "hall", "footprint": "main", "base_z": 0, "height": 10, "roof": {"type": "flat"},
                       "material": {"wall": "stucco", "roof": "roof"}, "facade": fac},
                      {"id": "glass", "footprint": {"rect": {"cx": 0, "cy": 0, "w": 6, "d": 6, "rot_deg": 0}},
                       "base_z": 10, "height": 8, "roof": {"type": "flat"}, "material": {"wall": "glass"},
                       "facade": {"opening_type": "curtain_wall", "bay_spacing": 1.5}}],
            "materials": {"stucco": {"srgb": [220, 190, 110]}, "roof": {"srgb": [120, 60, 40]},
                          "glass": {"srgb": [90, 120, 140]}, "opening": {"srgb": [0, 0, 0]}},
            "photos": ["input/photos/a.jpg"]}
    p.write_json("spec/landmark_spec.json", spec)
    return p


def _facade_calls(calls):
    return [c for c in calls if "facade texture" in c]


def _painter(calls):
    def fake(prompt, images):
        calls.append(prompt)
        if "TILEABLE" in prompt:                       # a material tile: noise around the colour asked for
            import re
            rgb = [int(v) for v in re.search(r"RGB \((\d+), (\d+), (\d+)\)", prompt).groups()]
            a = np.clip(np.array(rgb, np.float32) + np.random.default_rng(len(calls)).normal(0, 6, (256, 256, 3)),
                        0, 255).astype(np.uint8)
            path = images[0].parent / f"tile_{len(calls)}.png"
            Image.fromarray(a).save(path)
            return path
        lay = np.asarray(Image.open(images[-1]).convert("L"))
        out = np.zeros(lay.shape + (3,), np.uint8)
        out[:] = (220, 190, 110)
        out = np.clip(out + np.random.default_rng(len(calls)).normal(0, 5, out.shape), 0, 255).astype(np.uint8)
        out[lay < 80] = (30, 50, 40)
        path = images[-1].parent / f"fake_{len(calls)}.png"
        Image.fromarray(out).save(path)
        return path
    return fake


@pytest.fixture()
def codex(monkeypatch):
    from landmark3d.smarttex import genfacade
    calls = []
    monkeypatch.setattr(genfacade, "codex_cli", lambda: "codex")
    monkeypatch.setattr(genfacade, "codex_generate", _painter(calls))
    return calls


class CountingAI:
    """A vision provider that must not be asked anything (generation covers the faces)."""
    def __init__(self):
        self.asked = 0

    def chat(self, *a, **k):
        self.asked += 1
        raise AssertionError("the photo-crop AI was called although generation covered the faces")


def test_phases_have_texture_step_and_a25_between_a2_and_build():
    from landmark3d.project import PHASE_IDS, PHASES
    i = PHASE_IDS.index
    assert i("spec_review") < i("textures") < i("texture_review") < i("build")
    assert next(p for p in PHASES if p["id"] == "texture_review")["approval"] is True


def test_old_projects_get_the_new_phases(prj):
    st = json.loads(prj.state_file.read_text(encoding="utf-8"))
    for k in ("textures", "texture_review"):
        st["phases"].pop(k)
    st["phases"]["build"]["status"] = "done"                  # built before the update: nothing to redo
    prj.state_file.write_text(json.dumps(st), encoding="utf-8")
    ph = prj.state()["phases"]
    assert ph["textures"]["status"] == "done" and ph["texture_review"]["status"] == "approved"
    st["phases"]["build"]["status"] = "pending"
    for k in ("textures", "texture_review"):
        st["phases"].pop(k, None)
    prj.state_file.write_text(json.dumps(st), encoding="utf-8")
    assert prj.state()["phases"]["textures"]["status"] == "pending"


def test_generation_first_no_crop_ai_when_every_face_is_generated(prj, codex):
    from landmark3d import autotex
    spec = prj.read_json("spec/landmark_spec.json")
    spec["parts"] = spec["parts"][:1]                         # only the generatable hall
    prj.write_json("spec/landmark_spec.json", spec)
    ai = CountingAI()
    res = autotex.run_v2(prj, ai)
    assert ai.asked == 0 and res["generated_faces"] == 4 and not res["crop_fallback"] and res["bare_faces"] == 0
    assert len(_facade_calls(codex)) == 1                     # one elevation for the part, tiled on its 4 faces


def test_bare_faces_are_reported_and_the_crop_fallback_needs_an_ai(prj, codex):
    from landmark3d import autotex
    res = autotex.run_v2(prj, None)                          # the glass part cannot be generated, no AI for crops
    assert res["generated_faces"] == 4 and res["bare_faces"] == 4 and not res["crop_fallback"]
    assert "4 faces without image texture" in autotex.summary_line(res)


def test_owner_choices_colour_and_regenerate_with_note(prj, codex):
    from landmark3d import autotex
    autotex.run_v2(prj, None)
    assert len(_facade_calls(codex)) == 1
    autotex.set_override(prj, "hall", "colour")
    res = autotex.run_v2(prj, None)                          # the choice changes the cache key
    assert res["generated_faces"] == 0 and res["colour_parts"] == ["hall"] and len(_facade_calls(codex)) == 1
    autotex.set_override(prj, "hall", "regenerate", "green shutters")
    res = autotex.run_v2(prj, None)
    assert res["generated_faces"] == 4 and len(_facade_calls(codex)) == 2 and "green shutters" in _facade_calls(codex)[-1]
    rv = autotex.review(prj)
    hall = next(r for r in rv["parts"] if r["part"] == "hall")
    assert hall["generated"] and hall["layout"] and hall["kinds"] == ["generated"] and hall["choice"]["round"] == 1
    assert next(r for r in rv["parts"] if r["part"] == "glass")["generatable"] is False
    with pytest.raises(ValueError):
        autotex.set_override(prj, "nope", "colour")


def test_failed_generation_is_reported_not_hidden(prj, monkeypatch):
    from landmark3d import autotex
    from landmark3d.smarttex import genfacade
    monkeypatch.setattr(genfacade, "codex_cli", lambda: "codex")

    def broken(prompt, images):
        raise RuntimeError("quota exceeded")
    monkeypatch.setattr(genfacade, "codex_generate", broken)
    res = autotex.run_v2(prj, None)
    assert res["generation_failed"] and res["generation_failed"][0]["part"] == "hall"
    assert "GENERATION FAILED for hall" in autotex.summary_line(res)


def test_textures_phase_then_a25_then_build_without_ai(prj, codex, monkeypatch):
    from landmark3d import pipeline
    from landmark3d.research import ai
    for ph in ("setup", "doctor", "research", "sources_review", "completion", "spec", "spec_review"):
        prj.set_phase(ph, "approved" if ph.endswith("review") else "done")
    monkeypatch.setattr(ai, "get", lambda role: None)
    assert pipeline.run_phase(prj, "textures") == "done"
    assert "4 generated faces" in prj.state()["phases"]["textures"]["message"]
    built = []
    monkeypatch.setattr(pipeline.engine_runner, "build", lambda p: built.append(1) or {"ok": False, "errors": ["x"]})
    from landmark3d import autotex
    monkeypatch.setattr(autotex, "run_v2", lambda *a: (_ for _ in ()).throw(AssertionError("build ran textures")))
    out = pipeline.run_until_blocked(prj)
    assert out == {"stopped_at": "texture_review", "reason": "approval"} and not built
    pipeline.approve(prj, "texture_review", "approved")
    pipeline.run_until_blocked(prj)
    assert built == [1]


def test_generated_material_tiles_for_roof_and_wall_not_glass(prj, codex):
    """P8: roof and wall materials get a generated seamless tile, labelled generated; glass gets none."""
    from landmark3d import autotex, textures
    res = autotex.run_v2(prj, None)
    assert res["generated_tiles"] == ["glass", "roof", "stucco"]      # glass only as the curtain-wall module (P9)
    assert all("curtain wall" in c for c in codex if '"glass"' in c)
    sw = {t["material"]: t for t in textures.load(prj)["textures"] if t.get("mode") == "repeat"}
    assert sw["roof"]["licence"] == "ai_generated" and sw["roof"]["from"] == "generated tile"
    n = len(codex)
    autotex.set_override(prj, "hall", "regenerate")          # another run: the tiles come from the cache
    autotex.run_v2(prj, None)
    assert len([c for c in codex[n:] if "TILEABLE" in c]) == 0


def test_curtain_wall_glass_gets_a_generated_mullion_module(prj, codex):
    """P9: the glass of a curtain-wall part gets a seamless module of its mullion and floor grid as a material tile."""
    from landmark3d import autotex, textures
    from landmark3d.smarttex import genfacade
    res = autotex.run_v2(prj, None)
    assert "glass" in res["generated_tiles"]
    prompt = next(c for c in codex if "curtain wall" in c)
    cols, rows, size = genfacade.curtain_grid(prj.read_json("spec/landmark_spec.json")["parts"][1])
    assert f"exactly {cols} glass panels across" in prompt and (cols, rows) == (4, 2) and size == 6.0
    sw = {t["material"]: t for t in textures.load(prj)["textures"] if t.get("mode") == "repeat"}
    assert sw["glass"]["world_size_m"] == 6.0 and sw["glass"]["licence"] == "ai_generated"


def test_codex_image_calls_are_counted_in_ai_usage(tmp_path, monkeypatch):
    """P11: a real Codex image call (CLI faked) is recorded in the phase's AI call log."""
    import subprocess
    from landmark3d.research import ai
    from landmark3d.smarttex import genfacade
    monkeypatch.setattr(genfacade, "codex_cli", lambda: "codex")
    monkeypatch.setattr(genfacade.Path, "home", staticmethod(lambda: tmp_path))

    def fake_run(cmd, stdin_text=None, timeout=60, cwd=None):
        Image.new("RGB", (8, 8)).save(f"{cwd}/out.png")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(ai, "_run", fake_run)
    log = ai.CallLog(tmp_path / "calls.jsonl", "textures")
    with ai.logging_calls(log):
        assert genfacade.codex_generate("draw", []).is_file()
    s = log.summary()
    assert s["by_step"]["codex_image"]["calls"] == 1 and s["by_step"]["codex_image"]["rejected"] == 0
