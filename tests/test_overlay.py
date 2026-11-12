"""Model-over-photo overlay used by the photo-review rounds."""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from landmark3d import overlay


def render_img():
    im = Image.new("RGB", (400, 300), (150, 160, 175))                 # sky
    d = ImageDraw.Draw(im)
    d.rectangle((0, 250, 400, 300), fill=(120, 120, 125))              # ground plane
    d.rectangle((150, 100, 250, 250), fill=(170, 80, 60))              # the model, 100 x 150 px
    d.point((10, 10), fill=(0, 0, 0))                                   # a speck of noise
    return im


def test_render_mask_finds_the_model_and_drops_specks():
    m = overlay.render_mask(render_img())
    ys, xs = np.where(m)
    assert (xs.min(), xs.max(), ys.min(), ys.max()) == (150, 250, 100, 250)


def test_compose_fits_the_model_height_into_the_frame():
    photo = Image.new("RGB", (800, 600), (200, 200, 200))
    out = np.asarray(overlay.compose(photo, render_img(), [0.25, 0.1, 0.75, 0.9]))
    cyan = np.where((out[..., 0] == 0) & (out[..., 1] == 255) & (out[..., 2] == 255))
    assert abs(cyan[0].min() - 60) <= 3 and abs(cyan[0].max() - 540) <= 3       # 10 % .. 90 % of 600 px
    assert abs((cyan[1].min() + cyan[1].max()) / 2 - 400) <= 3                  # centred on the frame
    with pytest.raises(ValueError):
        overlay.compose(photo, render_img(), [0.5, 0.5, 0.4, 0.9])


def test_review_sheet_has_the_overlay_panel_only_with_a_frame(tmp_path):
    from landmark3d import pipeline
    Image.new("RGB", (800, 600), (200, 200, 200)).save(tmp_path / "p.jpg")
    render_img().save(tmp_path / "r.png")
    plain = pipeline._side_by_side(tmp_path / "p.jpg", tmp_path / "r.png", tmp_path / "a.jpg", "photo1")
    framed = pipeline._side_by_side(tmp_path / "p.jpg", tmp_path / "r.png", tmp_path / "b.jpg", "photo1",
                                    [0.25, 0.1, 0.75, 0.9])
    ratio = lambda f: Image.open(f).width / Image.open(f).height
    assert ratio(framed) > ratio(plain) * 1.4                      # a third panel (sheets are capped at 1800 px)


def test_azimuth_refinement_turns_a_view_to_the_picked_overlay(tmp_path, monkeypatch):
    """The 8 matched sides are 45 deg apart; the refinement offers +-15 / +-30 deg overlays and applies the pick."""
    import importlib
    import json
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d import pipeline
    from landmark3d.engine import runner
    from landmark3d.project import Project
    from landmark3d.research import ai
    prj = Project.create("Turn Hall", "X", lat=1.0, lon=2.0)
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (800, 600), (200, 200, 200)).save(photo)

    def fake_preview(p, spec, views, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        out = []
        for name in views:
            render_img().save(out_dir / f"{name}.png")
            out.append(out_dir / f"{name}.png")
        return out
    monkeypatch.setattr(runner, "preview", fake_preview)

    class Fake(ai.Provider):
        def chat(self, system, prompt, **k):
            return ai.AIResult(json.dumps({"picks": [{"photo": 1, "best": 4}]}), "fake", "m", [])
    views = [{"photo": 1, "azimuth_deg": 90.0, "elevation_deg": 8.0, "frame": [0.1, 0.1, 0.9, 0.9]},
             {"photo": 2, "azimuth_deg": 180.0, "elevation_deg": 8.0}]                 # no frame: unchanged
    entry = {}
    out = pipeline.refine_azimuths(prj, Fake("m"), {}, [photo, photo], views, tmp_path, entry)
    assert out[0]["azimuth_deg"] == 105.0 and out[1]["azimuth_deg"] == 180.0
    assert entry["azimuth_refine"] == {"1": 15.0}


def test_volume_audit_adds_a_new_part_for_each_missing_volume(tmp_path, monkeypatch):
    import importlib
    import json
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d import pipeline
    from landmark3d.engine import runner
    from landmark3d.project import Project
    from landmark3d.research import ai
    prj = Project.create("Audit Hall", "X", lat=1.0, lon=2.0)
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (800, 600), (200, 200, 200)).save(photo)

    def fake_preview(p, spec, views, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        out = []
        for name in views:
            render_img().save(out_dir / f"{name}.png")
            out.append(out_dir / f"{name}.png")
        return out
    monkeypatch.setattr(runner, "preview", fake_preview)
    hall = {"id": "hall", "footprint": {"rect": {"cx": 0, "cy": 0, "w": 20, "d": 10}}, "base_z": 0, "height": 10,
            "roof": {"type": "gable", "height": 4}}
    chapel = {"id": "chapel", "footprint": {"rect": {"cx": 0, "cy": 7, "w": 6, "d": 4}}, "base_z": 0, "height": 5,
              "roof": {"type": "hip", "height": 2}}
    massing = {"parts": [hall], "materials": {}, "volumes": [{"name": "hall", "part": "hall"}],
               "views": [{"photo": 1, "azimuth_deg": 270, "elevation_deg": 8}]}
    replies = [{"missing": [{"name": "side chapel", "seen_in": [1], "where": "left, low"}]},
               {"parts": [hall], "volumes": [{"name": "side chapel", "part": "hall"}]},          # reuses a part: no
               {"parts": [hall, chapel], "materials": {}, "volumes": [{"name": "hall", "part": "hall"},
                                                                       {"name": "side chapel", "part": "chapel"}]}]
    seen = []

    class Fake(ai.Provider):
        def chat(self, system, prompt, **k):
            seen.append(prompt)
            return ai.AIResult(json.dumps(replies.pop(0)), "fake", "m", [])
    out, entry = pipeline.volume_audit(prj, Fake("m"), massing, [photo], {1: [0.2, 0.1, 0.8, 0.9]}, lambda o: [],
                                       [[-30, -30], [30, -30], [30, 30], [-30, 30]], {}, prj.state(),
                                       prj.root / "reports" / "photo_review")
    assert [p["id"] for p in out["parts"]] == ["hall", "chapel"] and "side chapel" in seen[1]
    assert any("NEW part" in p for p in seen[2:]) and entry["verdict"] == "volumes added"
    assert {v["name"] for v in out["volumes"]} == {"hall", "side chapel"}


def test_volume_audit_keeps_good_additions_and_drops_one_off_the_site(tmp_path, monkeypatch):
    import importlib
    import json
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d import pipeline
    from landmark3d.engine import runner
    from landmark3d.project import Project
    from landmark3d.research import ai
    prj = Project.create("Salvage Audit", "X", lat=1.0, lon=2.0)
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (800, 600), (200, 200, 200)).save(photo)

    def fake_preview(p, spec, views, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        render_img().save(out_dir / "photo1.png")
        return [out_dir / "photo1.png"]
    monkeypatch.setattr(runner, "preview", fake_preview)
    hall = {"id": "hall", "footprint": {"rect": {"cx": 0, "cy": 0, "w": 20, "d": 10}}, "base_z": 0, "height": 10,
            "roof": {"type": "gable", "height": 4}}
    chapel = {"id": "chapel", "footprint": {"rect": {"cx": 0, "cy": 7, "w": 6, "d": 4}}, "base_z": 0, "height": 5,
              "roof": {"type": "hip", "height": 2}}
    far = {"id": "far_tower", "footprint": {"rect": {"cx": 60, "cy": 0, "w": 8, "d": 8}}, "base_z": 0, "height": 40,
           "roof": {"type": "flat"}}
    replies = [{"missing": [{"name": "side chapel"}, {"name": "third tower"}]},
               {"parts": [hall, chapel, far], "materials": {},
                "volumes": [{"name": "side chapel", "part": "chapel"}, {"name": "third tower", "part": "far_tower"}]}]

    class Fake(ai.Provider):
        def chat(self, system, prompt, **k):
            return ai.AIResult(json.dumps(replies.pop(0)), "fake", "m", [])
    massing = {"parts": [hall], "materials": {}, "volumes": [{"name": "hall", "part": "hall"}],
               "views": [{"photo": 1, "azimuth_deg": 270, "elevation_deg": 8}]}
    out, entry = pipeline.volume_audit(prj, Fake("m"), massing, [photo], {1: [0.2, 0.1, 0.8, 0.9]}, lambda o: [],
                                       [[-30, -30], [30, -30], [30, 30], [-30, 30]], {}, prj.state(),
                                       prj.root / "reports" / "photo_review")
    assert [p["id"] for p in out["parts"]] == ["hall", "chapel"] and entry["dropped"]
