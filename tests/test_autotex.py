import json

import numpy as np
import pytest
from PIL import Image, ImageDraw


@pytest.fixture()
def prj(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d.project import Project
    p = Project.create("Tex Hall", "X", lat=1.0, lon=2.0)
    photo = Image.new("RGB", (800, 600), (90, 140, 200))           # sky
    d = ImageDraw.Draw(photo)
    d.polygon([(200, 150), (650, 210), (650, 520), (200, 560)], fill=(220, 180, 90))   # a wall seen at an angle
    d.rectangle((40, 40, 160, 120), fill=(170, 70, 50))               # roof tiles sample
    photo.save(p.path("input", "photos", "a.jpg"))
    p.write_json("input/sources.json", {"sources": [
        {"id": "SRC-1", "kind": "photo", "path": "input/photos/a.jpg", "licence": "cc_by_sa", "author": "Someone",
         "use": "reference_and_texture"}]})
    spec = {"footprint": {"local": [[-15, -5], [15, -5], [15, 5], [-15, 5]]},
            "parts": [{"id": "hall", "footprint": "main", "base_z": 0, "height": 12, "roof": {"type": "hip"},
                       "material": {"wall": "wall", "roof": "roof", "trim": "trim"}}],
            "materials": {"wall": {"srgb": [220, 180, 90]}, "roof": {"srgb": [170, 70, 50]},
                          "trim": {"srgb": [250, 250, 250]}, "opening": {"srgb": [0, 0, 0]}},
            "photos": ["input/photos/a.jpg"]}
    p.write_json("spec/landmark_spec.json", spec)
    return p


def test_faces_and_rectify(prj):
    from landmark3d import autotex
    faces = autotex.faces_of(prj.read_json("spec/landmark_spec.json"))
    assert [f["length_m"] for f in faces] == [30.0, 30.0, 10.0, 10.0]
    south = next(f for f in faces if f["facing_deg"] == 270)
    assert south["part"] == "hall" and south["z1"] == 12
    img = Image.open(prj.root / "input/photos/a.jpg")
    flat = autotex.rectify(img, [[200 / 800, 150 / 600], [650 / 800, 210 / 600], [650 / 800, 520 / 600],
                                 [200 / 800, 560 / 600]], 300, 120)
    a = np.asarray(flat, dtype=float)
    assert flat.size == (300, 120) and abs(a[..., 0].mean() - 220) < 12 and abs(a[..., 2].mean() - 90) < 12


def test_tileable_edges_match():
    from landmark3d import autotex
    rng = np.random.default_rng(0)
    grad = np.zeros((64, 96, 3), dtype=np.uint8)
    grad[..., 0] = np.linspace(0, 255, 96)[None, :]                 # strong left-right seam if tiled raw
    grad[..., 1] = rng.integers(0, 255, (64, 96))
    t = np.asarray(autotex.make_tileable(Image.fromarray(grad)), dtype=float)
    raw_seam = abs(float(grad[:, 0, 0].mean()) - float(grad[:, -1, 0].mean()))
    new_seam = abs(t[:, 0, 0].mean() - t[:, -1, 0].mean())
    assert new_seam < raw_seam * 0.2


def test_run_writes_auto_entries_and_respects_manual(prj):
    from landmark3d import autotex, textures
    plan = {"facades": [{"face": "hall#0", "photo": 1, "occlusion": 0,
                         "corners": [[0.25, 0.25], [0.81, 0.35], [0.81, 0.87], [0.25, 0.93]]}],
            "swatches": [{"material": "roof", "photo": 1, "box": [0.05, 0.07, 0.2, 0.2], "width_m": 2.5},
                         {"material": "wall", "photo": 1, "box": [0.3, 0.5, 0.5, 0.8], "width_m": 4}]}

    class Fake:
        def chat(self, system, prompt, images=None, **k):
            from landmark3d.research import ai
            assert len(images) == 1 and str(images[0]).endswith("grid_photo1.jpg")
            return ai.AIResult(json.dumps(plan), "fake", "m", [])
    # a texture the user added by hand for "wall" must win over the automatic swatch
    buf = prj.root / "input" / "photos" / "a.jpg"
    textures.add(prj, file_bytes=buf.read_bytes(), filename="mine.jpg", mode="repeat", material="wall")
    res = autotex.run(prj, Fake())
    assert res == {"facades": 1, "swatches": 1}
    tx = textures.load(prj)["textures"]
    auto = [t for t in tx if t.get("auto")]
    assert {t["mode"] for t in auto} == {"panel", "repeat"} and all(t["source_id"] == "SRC-1" for t in auto)
    assert any(not t.get("auto") and t["material"] == "wall" for t in tx)
    assert autotex.run(prj, Fake()) == {"skipped": "up to date"}
    repeat, panels, atlas = textures.build_job(prj)
    assert "roof" in repeat and panels[0]["edge_index"] == 0 and atlas


def test_visible_faces_from_camera_direction(prj):
    from landmark3d import autotex
    spec = prj.read_json("spec/landmark_spec.json")
    faces = autotex.faces_of(spec)
    vis = autotex.visible_faces(faces, [{"photo": 1, "azimuth_deg": 300}], spec)   # camera south-east-ish... south
    names = [fid for fid, _ in vis[1]]
    south = next(f["face"] for f in faces if f["facing_deg"] == 270)
    north = next(f["face"] for f in faces if f["facing_deg"] == 90)
    assert south in names and north not in names
    assert dict(vis[1])[south] in ("faces the camera", "turned 30 deg to the left", "turned 30 deg to the right")


def test_swatch_colour_check():
    from landmark3d import autotex
    plaster = Image.new("RGB", (64, 64), (225, 190, 95))
    shutter = Image.new("RGB", (64, 64), (30, 80, 55))
    ok, d, balanced = autotex.colour_match(plaster, [230, 190, 90])
    assert ok and d < 10 and balanced.size == (64, 64)
    ok, d, _ = autotex.colour_match(shutter, [230, 190, 90])
    assert not ok and d > 70


# ----------------------------------------------------------------------------- v2: bay tiles, QA, repair, exposure
def test_tile_face_repeats_bay_on_the_grid():
    from landmark3d import autotex
    tile = Image.new("RGB", (64, 200), (200, 170, 90))
    ImageDraw.Draw(tile).rectangle((20, 40, 44, 160), fill=(20, 60, 40))       # one window in the bay
    face = autotex.tile_face(tile, 5, 200)
    a = np.asarray(face, dtype=float)
    assert face.size == (320, 200)
    col = a[100, :, 0]
    windows = [x for x in range(1, 320) if col[x] < 60 <= col[x - 1]]           # left edges of the windows
    assert len(windows) == 5 and np.allclose(np.diff(windows), 64)


def test_mirror_repair_and_harmonise():
    from landmark3d import autotex
    img = Image.new("RGB", (100, 40), (200, 180, 100))
    ImageDraw.Draw(img).rectangle((0, 0, 19, 39), fill=(20, 120, 20))            # a tree on the left fifth
    fixed = np.asarray(autotex.mirror_repair(img, [[0.0, 0.2]]), dtype=float)
    assert abs(fixed[:, :20, 1].mean() - 180) < 2                                # replaced by the clean right side
    dark = Image.new("RGB", (10, 10), (60, 60, 60))
    bright = Image.new("RGB", (10, 10), (140, 140, 140))
    mid = Image.new("RGB", (10, 10), (100, 100, 100))
    out = autotex.harmonise([dark, bright, mid])
    means = [np.asarray(o, dtype=float).mean() for o in out]
    assert max(means) - min(means) < 140 - 60                                   # closer than before


def test_run_v2_bays_unique_and_qa(prj):
    from landmark3d import autotex, textures
    spec = prj.read_json("spec/landmark_spec.json")
    spec["parts"][0]["facade"] = {"opening_type": "arch", "bay_spacing": 3.0, "level_height": 5}
    prj.write_json("spec/landmark_spec.json", spec)
    replies = [
        {"parts": [{"id": "hall", "mode": "unique", "face": "hall#0", "face_photo": 1,
                    "face_corners": [[0.25, 0.25], [0.81, 0.35], [0.81, 0.87], [0.25, 0.93]],
                    "bay": {"photo": 1, "corners": [[0.3, 0.3], [0.38, 0.31], [0.38, 0.86], [0.3, 0.87]]}}],
         "swatches": [{"material": "roof", "photo": 1, "box": [0.05, 0.07, 0.2, 0.2], "width_m": 2.5}]},
        {"textures": [{"n": 1, "verdict": "keep", "occluded": [[0.0, 0.1]]}, {"n": 2, "verdict": "keep"}]}]

    class Fake:
        def chat(self, system, prompt, images=None, **k):
            from landmark3d.research import ai
            return ai.AIResult(json.dumps(replies.pop(0)), "fake", "m", [])
    res = autotex.run_v2(prj, Fake())
    # 4 faces: the unique south face + 3 faces from the repeated bay, roof swatch, nothing dropped
    assert res["unique_faces"] == 1 and res["bay_faces"] == 3 and res["swatches"] == 1 and res["dropped_by_qa"] == 0
    tx = [t for t in textures.load(prj)["textures"] if t.get("auto")]
    north = next(t for t in tx if t.get("kind") == "bays" and t["edge_index"] == 2)
    w, h = Image.open(prj.root / north["file"]).size
    assert (w, h) == (960, 384)                      # 30 m face = 10 bays x 96 px; 12 m wall = 384 px
    assert (prj.root / "reports" / "autotex" / "qa_sheet.jpg").exists()


def test_clean_band_crops_texture_and_shrinks_face_height():
    from PIL import Image
    from landmark3d import autotex
    assert autotex.clean_band(None) is None
    assert autotex.clean_band([0, 1]) is None                 # whole height clean: no crop
    assert autotex.clean_band([0.5, 0.6]) is None             # too thin to be useful
    assert autotex.clean_band([0.55, 0.0]) == (0.0, 0.55)     # order does not matter
    img = Image.new("RGB", (200, 100), "white")
    face = {"face": "hall:0", "z0": 0.0, "z1": 10.0}
    out, f = autotex.crop_band(img, face, (0.0, 0.4))
    assert out.size == (200, 40)
    assert f["z1"] == 10.0 and f["z0"] == 6.0 and face["z0"] == 0.0   # the upper 40 % of the wall; input untouched
