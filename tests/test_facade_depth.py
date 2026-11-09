"""Architectural details with real depth, per-wall facades, and the judge gate that decides what is kept."""
import json

import pytest

from landmark3d import geom, spec

BOX = geom.rect_polygon(0, 0, 40, 20, 0)     # south wall at y = -10, facing_deg 270
HALL = {"id": "hall", "footprint": "main", "base_z": 0, "height": 15, "roof": {"type": "hip"}}


def build(*feats, budget=10 ** 9):
    m = geom.Mesh()
    return m, geom.features(BOX, 0, 15, None, list(feats), m, budget)


def faces_verts(m):
    return [m.verts[i] for f in m.faces for i in f]


@pytest.mark.parametrize("ft", [
    {"type": "column", "facing_deg": 270, "z": 0, "height": 6, "diameter": 0.8, "material": "s"},
    {"type": "balustrade", "facing_deg": 270, "z": 6, "width": 10, "material": "s"},
    {"type": "balcony", "facing_deg": 270, "z": 6, "width": 4, "depth": 1.2, "material": "s"},
    {"type": "colonnade", "facing_deg": 270, "z": 0, "width": 12, "height": 6, "depth": 1.5, "bays": 4, "material": "s"},
    {"type": "pediment", "facing_deg": 270, "z": 8, "width": 6, "height": 2, "material": "s"},
    {"type": "pediment", "facing_deg": 270, "z": 8, "width": 6, "height": 2, "style": "segmental", "material": "s"},
    {"type": "pediment", "facing_deg": 270, "z": 8, "width": 6, "height": 2, "style": "broken", "gap": 2, "material": "s"},
])
def test_depth_details_stand_out_from_their_wall(ft):
    m, used = build(ft)
    assert used > 0 and "d:s" in m.tags
    ys = [v[1] for v in faces_verts(m)]
    assert min(ys) < -10.1 and max(ys) <= -9.9                    # in front of the south wall, not through the building
    assert spec.validate_massing({"parts": [dict(HALL, features=[ft])], "materials": {"s": {"srgb": [1, 2, 3]}}}) == []


def test_a_colonnade_on_the_ground_stays_above_it():
    m, _ = build({"type": "colonnade", "facing_deg": 270, "z": 0, "width": 12, "height": 6})
    assert min(v[2] for v in faces_verts(m)) >= 0


def test_used_triangles_are_counted():
    for ft in ({"type": "column", "facing_deg": 270, "height": 6, "diameter": 0.8, "count": 5, "spacing": 3},
               {"type": "colonnade", "facing_deg": 270, "width": 12, "height": 6, "bays": 3, "pair_gap": 1.0},
               {"type": "balcony", "facing_deg": 270, "z": 6, "width": 4}):
        m, used = build(ft)
        assert used == m.triangles()


def test_column_row_and_pairs():
    single, _ = build({"type": "column", "facing_deg": 270, "height": 6, "diameter": 0.8})
    row, _ = build({"type": "column", "facing_deg": 270, "height": 6, "diameter": 0.8, "count": 4, "spacing": 3})
    pairs, _ = build({"type": "column", "facing_deg": 270, "height": 6, "diameter": 0.8, "count": 4, "spacing": 4, "pair_gap": 1.2})
    assert row.triangles() == 4 * single.triangles() and pairs.triangles() == 8 * single.triangles()


def test_a_row_is_kept_on_its_wall():
    m, _ = build({"type": "column", "facing_deg": 270, "height": 6, "diameter": 0.8, "count": 60, "spacing": 3})
    assert all(-20.6 <= v[0] <= 20.6 for v in faces_verts(m))


def test_budget_limits_the_details():
    _, used = build({"type": "column", "facing_deg": 270, "height": 6, "diameter": 0.8, "count": 60, "spacing": 0.9}, budget=200)
    assert used <= 200


def test_no_detail_without_a_wall_facing_it():
    assert build({"type": "column", "facing_deg": 45, "height": 6})[1] == 0


def test_balustrade_around_a_part_and_cresting():
    m, _ = build({"type": "balustrade", "around": True, "z": 14, "height": 1, "material": "s"})
    vs = faces_verts(m)
    assert min(v[0] for v in vs) < -19.8 and max(v[0] for v in vs) > 19.8 and min(v[1] for v in vs) < -9.8 and max(v[1] for v in vs) > 9.8
    m, _ = build({"type": "cresting", "height": 0.8, "material": "iron"})
    assert min(v[2] for v in faces_verts(m)) == pytest.approx(15.0) and "d:iron" in m.tags


def test_finial_styles_stand_where_asked():
    m, _ = build({"type": "finial", "x": 3, "y": 2, "height": 2, "material": "s"},
                 {"type": "finial", "x": 3, "y": 2, "z": 20, "height": 3, "style": "ball", "material": "s"})
    assert max(v[2] for v in faces_verts(m)) == pytest.approx(23.0)
    assert min(v[2] for v in faces_verts(m)) == pytest.approx(15.0)


def test_stepped_cornice_projects_further_at_each_tier():
    plain, stepped = geom.Mesh(), geom.Mesh()
    geom.facade_detail(BOX, 0, 15, {"cornice": 1.0}, plain, 10 ** 6)
    geom.facade_detail(BOX, 0, 15, {"cornice": 1.0, "cornice_tiers": 3}, stepped, 10 ** 6)
    assert stepped.triangles() == 3 * plain.triangles()
    top_y = max(stepped.verts[i][1] for f in stepped.faces for i in f if stepped.verts[i][2] > 14.9)
    low_y = max(stepped.verts[i][1] for f in stepped.faces for i in f if stepped.verts[i][2] < 14.1)
    assert top_y > low_y


FACADE = {"opening_type": "arch", "level_height": 5, "bay_spacing": 5, "opening_width": 2, "opening_height": 3}


def windows_of(lay, facing):
    """Windows of the wall whose outward normal is `facing`."""
    return [w for w in lay["windows"] if w["o"][0] * facing[0] + w["o"][1] * facing[1] > 0.9]


def test_a_side_overrides_only_the_walls_it_faces():
    fac = dict(FACADE, sides=[{"facing_deg": 270, "bay_spacing": 2.5, "opening_width": 1.0, "opening_type": "rect"}])
    lay = geom.facade_layout(BOX, 0, 15, fac)
    south, north = windows_of(lay, (0, -1)), windows_of(lay, (0, 1))
    assert {w["kind"] for w in south} == {"rect"} and {w["kind"] for w in north} == {"arch"}
    assert len(south) == 2 * len(north)
    assert geom.check_facade(lay) == []


def test_segments_give_stretches_of_a_wall_their_own_bays():
    fac = dict(FACADE, sides=[{"facing_deg": 270, "segments": [
        {"length": 10, "bay_spacing": 10, "opening_width": 5, "opening_height": 3.5},
        {"length": 20, "bay_spacing": 2.5, "opening_width": 1.2},
        {"length": 10, "bay_spacing": 10, "opening_width": 5, "opening_height": 3.5}]}])
    lay = geom.facade_layout(BOX, 0, 15, fac)
    south = [w for w in windows_of(lay, (0, -1)) if w["lv"] == 0]
    assert sorted({round(w["w"], 1) for w in south}) == [1.2, 5.0]
    wide = [w["p"][0] for w in south if w["w"] > 3]
    tight = [w["p"][0] for w in south if w["w"] < 3]
    assert len(wide) == 2 and len(tight) == 8 and all(-10 < x < 10 for x in tight) and all(abs(x) > 10 for x in wide)
    assert geom.check_facade(lay) == []


def test_segments_share_a_pilaster_where_they_meet():
    pil = {"every": 1, "width": 0.6}
    fac = dict(FACADE, pilasters=pil, sides=[{"facing_deg": 270, "segments": [
        {"length": 20, "bay_spacing": 5, "pilasters": pil}, {"length": 20, "bay_spacing": 4, "pilasters": pil}]}])
    pts = [(round(p["pt"][0][0], 2), round(p["pt"][0][1], 2)) for p in geom.facade_layout(BOX, 0, 15, fac)["pilasters"]]
    assert len(pts) == len(set(pts))


def test_schema_accepts_sides_and_aliases_reach_them():
    from landmark3d import pipeline
    fac = dict(FACADE, cornice_tiers=3, sides=[{"facing_deg": 270, "opening_type": "horizontal_louver", "segments": [
        {"length": 10, "opening_type": "arched_window"}, {"length": 10, "floors": [{"from": 0, "opening_type": "louvre"}]}]}])
    part = dict(HALL, facade=fac)
    out = pipeline.sanitize_facades({"parts": [part]})["parts"][0]["facade"]["sides"][0]
    assert out["opening_type"] == "rect" and out["segments"][0]["opening_type"] == "arch"
    assert out["segments"][1]["floors"][0]["opening_type"] == "rect"
    assert fac["sides"][0]["opening_type"] == "horizontal_louver"                # input untouched
    clean = pipeline.sanitize_facades({"parts": [part]})
    assert spec.validate_massing({"parts": clean["parts"], "materials": {"wall": {"srgb": [1, 2, 3]}}}) == []


# ----------------------------------------------------------------------------- the judge gate
def test_not_worse_rule():
    from landmark3d.pipeline import not_worse
    q = lambda o, f: {"overall": o, "scores": {"facade": f}}
    assert not_worse(q(6, 3), q(6.33, 2))
    assert not_worse(q(6, 3), q(6, 3))
    assert not not_worse(q(6, 3), q(6, 2.67))
    assert not not_worse(q(6, 3), q(5.67, 4))
    assert not not_worse(q(6, 3), None)


@pytest.fixture()
def prj(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d.project import Project
    return Project.create("Gate Test", "X", lat=1.0, lon=2.0)


class Fake:
    name = "fake"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def chat(self, system, prompt, **k):
        from landmark3d.research import ai
        self.calls.append((prompt, k.get("images")))
        return ai.AIResult(json.dumps(self.replies.pop(0)), "fake", "m", [])


def reading(overall, facade, errors=()):
    return {"scores": {"plan": 4, "silhouette": 3, "heights": 3, "facade": facade, "materials": 4}, "overall": overall,
            "errors": list(errors)}


def test_judge_averages_independent_readings(prj, monkeypatch):
    from PIL import Image
    from landmark3d import pipeline, settings
    from landmark3d.engine import runner

    def fake_preview(p, spec_, views, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        res = []
        for name in views:
            Image.new("RGB", (96, 64)).save(out_dir / f"{name}.png")
            res.append(out_dir / f"{name}.png")
        return res
    monkeypatch.setattr(runner, "preview", fake_preview)
    photo = prj.path("input", "photos", "a.jpg")
    Image.new("RGB", (200, 150)).save(photo)
    massing = {"parts": [HALL], "materials": {}, "views": [{"photo": 1, "azimuth_deg": 270, "elevation_deg": 8}]}
    square = [(-20, -10), (20, -10), (20, 10), (-20, 10)]
    q = pipeline.judge(prj, Fake([reading(6, 3, ["a"]), reading(5, 2, ["b"]), reading(7, 4, ["a"])]), massing, [photo], [],
                       square, {}, prj.state(), out_name="judge_f1")
    assert q["overall"] == 6 and q["scores"]["facade"] == 3 and q["readings"] == [6, 5, 7] and q["errors"] == ["a", "b"]
    assert q["images"][0].startswith("reports/judge_f1/")
    settings.update({"judge_samples": 1})
    q = pipeline.judge(prj, Fake([reading(6, 3)]), massing, [photo], [], square, {}, prj.state())
    assert q["readings"] == [6]


MASSING = {"parts": [dict(HALL, id="a", facade={"bands": False}), dict(HALL, id="b", facade={"bands": False}),
                     dict(HALL, id="c")],
           "materials": {"wall": {"srgb": [1, 2, 3]}, "roof": {"srgb": [1, 2, 3]}}}


def refined_model():
    feat = [{"type": "column", "facing_deg": 270, "height": 5, "material": "stone"}]
    return {"parts": [dict(MASSING["parts"][0], features=feat), dict(MASSING["parts"][1], features=feat), MASSING["parts"][2]],
            "materials": {**MASSING["materials"], "stone": {"srgb": [9, 9, 9]}}}


def sheets(prj):
    p = prj.path("reports", "x.jpg")
    p.write_bytes(b"x")
    return {"images": ["reports/x.jpg"]}


def test_keep_better_parts_changes_only_the_chosen_parts(prj):
    from landmark3d import pipeline
    q = sheets(prj)
    p = Fake([{"keep": ["a"], "why": "a is closer"}])
    mixed, kept = pipeline.keep_better_parts(prj, p, MASSING, refined_model(), q, q, prj.state())
    assert kept == ["a"]
    assert mixed["parts"][0]["features"] and not mixed["parts"][1].get("features")
    assert "stone" in mixed["materials"]
    assert len(p.calls[0][1]) == 2                                 # before and after sheets


@pytest.mark.parametrize("reply", [{"keep": []}, {"keep": ["a", "b"]}])
def test_keep_better_parts_needs_a_real_subset(prj, reply):
    from landmark3d import pipeline
    q = sheets(prj)
    assert pipeline.keep_better_parts(prj, Fake([reply]), MASSING, refined_model(), q, q, prj.state()) == (None, [])


def test_keep_better_parts_survives_a_bad_answer(prj):
    from landmark3d import pipeline
    q = sheets(prj)
    p = Fake([{"keep": ["ghost"]}, {"keep": ["ghost"]}])
    assert pipeline.keep_better_parts(prj, p, MASSING, refined_model(), q, q, prj.state()) == (None, [])


def test_judge_prompt_rewards_detail_and_facade_prompt_offers_depth():
    from landmark3d.research import agent
    assert "low-detail" not in agent.JUDGE_PROMPT and "balustrades" in agent.JUDGE_PROMPT
    assert all(k in agent.FACADE_PROMPT for k in ("colonnade", "balcony", "pediment", "segments", "at most 30 per part"))
    agent.FACADE_PROMPT.format(name="n", location="l", massing="{}")           # every brace is escaped
