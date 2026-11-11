"""Generated facade textures (smarttex.genfacade) with a fake generator: layout, checks, retry, cache, tiling."""
import importlib

import numpy as np
import pytest
from PIL import Image, ImageDraw

from landmark3d.smarttex import genfacade as gf

PART = {"id": "wing", "footprint": {"rect": {"cx": 0, "cy": 0, "w": 24, "d": 10, "rot_deg": 0}}, "base_z": 0,
        "height": 10, "material": {"wall": "stucco"},
        "facade": {"bay_spacing": 4.0, "level_height": 5.0, "opening_width": 1.6, "opening_height": 2.6, "sill": 1.0,
                   "opening_type": "arch"}}
SPEC = {"name": "Test Hall", "footprint": {"local": [[-12, -5], [12, -5], [12, 5], [-12, 5]]}, "parts": [PART],
        "materials": {"stucco": {"srgb": [220, 190, 110]}}}


def painted(lay: Image.Image, wall=(220, 190, 110), sky_rows=0) -> Image.Image:
    """A 'generated' facade that follows the layout: wall colour where the layout is grey, dark openings."""
    a = np.asarray(lay.convert("L"))
    out = np.zeros(a.shape + (3,), np.uint8)
    out[:] = wall
    rng = np.random.default_rng(0)
    out = np.clip(out + rng.normal(0, 5, out.shape), 0, 255).astype(np.uint8)
    out[a < 80] = (30, 50, 40)
    if sky_rows:
        out[:sky_rows] = (120, 165, 225)
    return Image.fromarray(out)


def test_layout_follows_the_facade_grid():
    lay, info = gf.layout(PART, 3)
    assert lay.size == (3 * 4 * gf.PX_PER_M, 10 * gf.PX_PER_M)
    assert info["floors"] == 2 and len(info["openings"]) == 6
    xs = sorted({round((o[0] + o[2]) / 2, 3) for o in info["openings"]})
    assert xs == pytest.approx([1 / 6, 0.5, 5 / 6], abs=0.01)          # one opening centred in each bay


def test_checks_accept_a_faithful_image_and_name_the_problems_of_others():
    lay, info = gf.layout(PART, 3)
    assert gf.check(painted(lay), info, [220, 190, 110]) == []
    p = gf.check(painted(lay, sky_rows=60), info, [220, 190, 110])
    assert any("sky" in x for x in p)
    p = gf.check(painted(lay, wall=(90, 90, 160)), info, [220, 190, 110])
    assert any("colour" in x for x in p)
    shifted = Image.fromarray(np.roll(np.asarray(painted(lay)), lay.width // 6, axis=1))
    assert any("centred" in x or "repeat" in x for x in gf.check(shifted, info, [220, 190, 110]))


@pytest.fixture()
def prj(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d.project import Project
    p = Project.create("Gen Hall", "X", lat=1.0, lon=2.0)
    Image.new("RGB", (400, 300), (220, 190, 110)).save(p.path("input", "photos", "a.jpg"))
    return p


def test_generate_retries_once_with_the_problems_then_caches(prj, tmp_path):
    calls = []
    lay, _ = gf.layout(PART, 3)

    def fake(prompt, images):
        calls.append(prompt)
        img = painted(Image.open(images[-1]), sky_rows=80 if len(calls) == 1 else 0)   # follows the layout given
        path = tmp_path / f"g{len(calls)}.png"
        img.save(path)
        return path
    res = gf.generate_part(prj, SPEC, PART, ["input/photos/a.jpg"], fake)
    assert res["accepted"] and res["attempts"] == 2 and len(calls) == 2
    assert "sky" in calls[1]                                              # the retry names the problem
    again = gf.generate_part(prj, SPEC, PART, ["input/photos/a.jpg"], fake)
    assert again["cached"] and len(calls) == 2


def test_items_tile_whole_bays_and_are_labelled_generated(prj, tmp_path):
    lay, _ = gf.layout(PART, 3)

    def fake(prompt, images):
        path = tmp_path / "g.png"
        painted(Image.open(images[-1])).save(path)             # the last image is the layout
        return path
    notes = []
    items = gf.items_for(prj, SPEC, {"wing": PART}, lambda pid: ["input/photos/a.jpg"], set(), notes, fake)
    assert items and all(it["kind"] == "generated" and it["src"]["licence"] == "ai_generated" for it in items)
    long_face = max(items, key=lambda it: it["face"]["length_m"])
    assert long_face["face"]["n_bays"] == 6
    w = long_face["img"].width
    from landmark3d.smarttex import metrics
    assert metrics.bay_phase(np.asarray(long_face["img"]), 6)["error"] < 0.1   # windows still on the bay grid


def test_faces_already_textured_are_left_alone(prj, tmp_path):
    lay, _ = gf.layout(PART, 3)

    def fake(prompt, images):
        path = tmp_path / "g.png"
        painted(lay).save(path)
        return path
    from landmark3d import autotex
    faces = autotex.part_faces(SPEC, PART)
    done = {("wing", f["edge_index"]) for f in faces}
    assert gf.items_for(prj, SPEC, {"wing": PART}, lambda pid: ["input/photos/a.jpg"], done, [], fake) == []


def test_generated_textures_skin_the_real_mesh_and_photo_panels_do_not(prj):
    """Generated facades follow the model's bay grid, so the build puts them on the real wall mesh ("skin"); photo
    panels stay overlay quads."""
    from landmark3d import textures as textures_mod
    for name in ("g.jpg", "p.jpg"):
        Image.new("RGB", (64, 32), (200, 180, 120)).save(prj.path("input", "textures", name))
    prj.write_json("input/textures.json", {"textures": [
        {"id": "TX-AF-01", "auto": True, "file": "input/textures/g.jpg", "mode": "panel", "part": "wing",
         "side": "face0", "edge_index": 0, "z0": 0, "z1": 10, "licence": "ai_generated", "kind": "generated",
         "generated": True, "source_id": "codex-generated"},
        {"id": "TX-AF-02", "auto": True, "file": "input/textures/p.jpg", "mode": "panel", "part": "wing",
         "side": "face1", "edge_index": 1, "z0": 0, "z1": 10, "licence": "cc_by_sa", "kind": "bays",
         "source_id": "SRC-1"}]})
    _, panels, atlas = textures_mod.build_job(prj)
    by = {p["id"]: p for p in panels}
    assert atlas and by["TX-AF-01"].get("skin") is True and "skin" not in by["TX-AF-02"]


# ------------------------------------------------------------------ P4: faces with their own composition
def _feature_part():
    from landmark3d import autotex
    faces = autotex.part_faces(SPEC, PART)
    long_faces = sorted([f for f in faces if f["length_m"] > 20], key=lambda f: f["edge_index"])
    a, b = long_faces[0], long_faces[1]
    part = dict(PART, features=[
        {"type": "disc", "facing_deg": a["facing_deg"], "z": 6.0, "diameter": 3.0},
        {"type": "disc", "facing_deg": b["facing_deg"], "z": 6.0, "diameter": 3.0},
        {"type": "panel", "facing_deg": a["facing_deg"], "z": 9.0, "width": 22.0, "height": 1.0},   # a band
        {"type": "balustrade", "around": True, "z": 9.5}])
    return part, a, b


def test_face_features_take_the_features_on_that_face_and_skip_bands():
    part, a, b = _feature_part()
    feats = gf.face_features(part, a)
    assert [f["type"] for f in feats] == ["disc"]
    x0, z0, x1, z1 = feats[0]["box"]
    assert (x0 + x1) / 2 == pytest.approx(12.0) and (z0, z1) == (4.5, 7.5)
    side = next(f for f in __import__("landmark3d.autotex", fromlist=["x"]).part_faces(SPEC, part)
                if f["length_m"] < 20)
    assert gf.face_features(part, side) == []


def test_feature_span_is_on_bay_lines_and_not_too_wide():
    part, a, _ = _feature_part()
    x0, x1 = gf.feature_span(a, part, gf.face_features(part, a))
    bay = a["length_m"] / a["n_bays"]
    assert x0 < 10.5 and x1 > 13.5
    assert x0 / bay == pytest.approx(round(x0 / bay)) and x1 / bay == pytest.approx(round(x1 / bay))
    wide = [{"type": "disc", "box": (0.0, 1.0, 24.0, 3.0), "weight": 3}]
    low = dict(part, height=4)
    x0, x1 = gf.feature_span(a, low, wide)
    assert x1 - x0 <= gf.SEG_MAX_ASPECT * 4 + 1e-6


def test_feature_faces_get_a_face_elevation_and_mirrored_twins_share_one(prj, tmp_path):
    part, a, b = _feature_part()
    calls = []

    def fake(prompt, images):
        calls.append(prompt)
        path = tmp_path / f"g{len(calls)}.png"
        painted(Image.open(images[-1])).save(path)
        return path
    notes = []
    items = gf.items_for(prj, SPEC, {"wing": part}, lambda pid: ["input/photos/a.jpg"], set(), notes, fake)
    by_edge = {it["face"]["edge_index"]: it for it in items}
    assert by_edge[a["edge_index"]].get("feature_span") and by_edge[b["edge_index"]].get("feature_span")
    assert len(calls) == 2                                   # the bay elevation + one shared feature elevation
    assert "round element" in calls[1] and "1." in calls[1]
    assert all(it["kind"] == "generated" for it in items) and len(items) == 4
    gf.forget(prj, "wing")
    assert not list((prj.root / "reports" / "genfacade").glob("wing__f*.png"))


def test_a2_preview_layouts_list_parts_and_feature_faces(prj):
    part, a, _ = _feature_part()
    spec = dict(SPEC, parts=[part])
    rows = gf.preview_layouts(prj, spec)
    assert rows[0]["part"] == "wing" and (prj.root / rows[0]["layout"]).is_file()
    assert {f["edge_index"] for f in rows[0]["faces"]} >= {a["edge_index"]}
    assert all((prj.root / f["layout"]).is_file() for f in rows[0]["faces"])


def test_opening_assemblies_are_on_by_default_without_the_v2_flag(prj):
    from landmark3d.detail import engine
    assert engine.engine_settings(False)["opening_assemblies"] is True
    assert engine.engine_settings(False, opening_assemblies=False)["opening_assemblies"] is False
    assert engine.engine_settings(True, opening_assemblies=False)["opening_assemblies"] is True


def test_generated_skins_get_one_atlas_per_part_at_full_width(prj):
    """P7: each part's generated faces go to their own atlas, a long face keeps its width (no half-atlas cap)."""
    from landmark3d import textures as textures_mod
    Image.new("RGB", (1800, 320), (200, 180, 120)).save(prj.path("input", "textures", "long.jpg"))
    Image.new("RGB", (400, 320), (200, 180, 120)).save(prj.path("input", "textures", "b.jpg"))
    gen = {"auto": True, "mode": "panel", "z0": 0, "z1": 10, "licence": "ai_generated", "kind": "generated",
           "generated": True, "source_id": "codex-generated"}
    prj.write_json("input/textures.json", {"textures": [
        dict(gen, id="TX-1", file="input/textures/long.jpg", part="wing", side="face0", edge_index=0),
        dict(gen, id="TX-2", file="input/textures/b.jpg", part="tower", side="face0", edge_index=0)]})
    _, panels, atlas = textures_mod.build_job(prj)
    by = {p["id"]: p for p in panels}
    assert atlas is None                                     # no photo panels: no shared atlas
    assert by["TX-1"]["atlas"] != by["TX-2"]["atlas"] and "wing" in by["TX-1"]["atlas"]
    with Image.open(by["TX-1"]["atlas"]) as im:
        u0, _, u1, _ = by["TX-1"]["uv"]
        assert (u1 - u0) * im.width > 1700
