"""Group A (structure gate) and group B (shapes, curtain wall, cornice) from the Diamond Plaza audit."""
import math

import pytest

from landmark3d import geom, spec

SQUARE = [(-20, -20), (20, -20), (20, 20), (-20, 20)]
LOW = {"plan": 3, "silhouette": 3, "heights": 3, "facade": 2, "materials": 4}
HIGH = {"plan": 4, "silhouette": 5, "heights": 4, "facade": 4, "materials": 4}


def part(pid, fp, base, h, roof="flat", facade=None):
    p = {"id": pid, "footprint": fp, "base_z": base, "height": h, "roof": {"type": roof, "height": 0}}
    if facade:
        p["facade"] = facade
    return p


def errors(parts, facts=None):
    return [i for i in spec.check_structure(parts, SQUARE, facts) if i["severity"] == "error"]


def test_supported_tower_passes():
    parts = [part("main", "main", 0, 20),
             part("tower", {"rect": {"cx": 0, "cy": 0, "w": 20, "d": 10}}, 20, 40)]
    assert errors(parts) == []


def test_overhang_is_blocked_with_distance():
    # podium is 40 x 40; a 30 m wide block centred 12 m east overhangs the podium edge by 7 m
    parts = [part("main", "main", 0, 20),
             part("block", {"rect": {"cx": 12, "cy": 0, "w": 30, "d": 10}}, 20, 30)]
    errs = errors(parts)
    assert len(errs) == 1 and errs[0]["part"] == "block" and errs[0]["code"] == "unsupported"
    assert "overhanging up to" in errs[0]["message"]


def test_small_edge_overhang_is_caught():
    # 2.5 m strip off one side: small share of the area, but a visible overhang
    parts = [part("main", "main", 0, 20),
             part("block", {"rect": {"cx": 0, "cy": 0, "w": 45, "d": 30}}, 20, 10)]
    assert [e["part"] for e in errors(parts)] == ["block"]


def test_part_with_nothing_at_its_base_floats():
    parts = [part("main", "main", 0, 10),
             part("sky", {"rect": {"cx": 0, "cy": 0, "w": 10, "d": 10}}, 30, 5)]
    assert errors(parts)[0]["code"] == "unsupported"


def test_tower_through_podium_supports_upper_crown():
    parts = [part("main", "main", 0, 20),
             part("tower", {"rect": {"cx": 0, "cy": 0, "w": 16, "d": 16}}, 0, 80),
             part("crown", {"circle": {"cx": 0, "cy": 0, "r": 6}}, 80, 4)]
    assert errors(parts) == []


def test_ground_part_outside_footprint_is_only_a_warning():
    issues = spec.check_structure([part("main", "main", 0, 10),
                                   part("gate", {"rect": {"cx": 0, "cy": 22, "w": 8, "d": 6}}, 0, 9)], SQUARE)
    assert [(i["severity"], i["code"]) for i in issues] == [("warning", "outside_footprint")]


def test_height_mismatch_warning():
    issues = spec.check_structure([part("main", "main", 0, 20)], SQUARE, {"total_height_m": {"value": 90}})
    assert issues and issues[0]["code"] == "height_mismatch" and issues[0]["severity"] == "warning"


def test_rounded_shapes():
    st = geom.footprint_polygon({"stadium": {"cx": 0, "cy": 0, "w": 40, "d": 20, "rot_deg": 30}}, [])
    assert abs(geom.area(st) - (20 * 20 + math.pi * 100)) < 6
    rr = geom.footprint_polygon({"rounded_rect": {"cx": 5, "cy": 5, "w": 30, "d": 20, "r": 4}}, [])
    assert abs(geom.area(rr) - (600 - (4 - math.pi) * 16)) < 2
    assert geom.area2(geom.ensure_ccw(rr)) > 0
    assert spec.validate_massing({"parts": [part("main", "main", 0, 10),
                                            part("b", {"stadium": {"cx": 0, "cy": 0, "w": 20, "d": 10}}, 10, 30,
                                                 facade={"opening_type": "curtain_wall", "cornice": 1.0, "bands": True})],
                                  "materials": {"glass": {"srgb": [40, 90, 100], "roughness": 0.1, "metallic": 0.6}}}) == []


def test_curtain_wall_frames_curves_and_respects_budget():
    st = geom.footprint_polygon({"stadium": {"cx": 0, "cy": 0, "w": 40, "d": 20}}, [])
    m = geom.Mesh()
    made = geom.facade_openings(st, 0, 40, {"opening_type": "curtain_wall", "level_height": 4, "bay_spacing": 1.5}, m, 20000)
    assert made > 50 and set(m.tags) == {"trim"}
    small = geom.Mesh()
    geom.facade_openings(st, 0, 40, {"opening_type": "curtain_wall", "level_height": 4, "bay_spacing": 1.5}, small, 600)
    assert small.triangles() <= 700


def test_punched_windows_on_curved_outline():
    st = geom.footprint_polygon({"stadium": {"cx": 0, "cy": 0, "w": 40, "d": 20}}, [])
    m = geom.Mesh()
    n = geom.facade_openings(st, 0, 12, {"opening_type": "rect", "bay_spacing": 3, "opening_width": 1.4,
                                         "opening_height": 2, "level_height": 4}, m, 10000)
    # openings now also go round the rounded ends (old code skipped every edge shorter than one bay)
    assert n > 2 * int(20 / 3) * 2


def test_cornice_and_bands():
    m = geom.Mesh()
    used = geom.facade_detail(SQUARE, 0, 20, {"cornice": 1.0, "bands": True, "level_height": 4}, m, 10000)
    assert used == m.triangles() and m.triangles() > 0
    zs = sorted({round(v[2], 2) for v in m.verts})
    assert 19.0 in zs and 20.0 in zs and 4.0 in zs


def test_spec_review_approval_blocked_by_floating_part(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d import pipeline
    from landmark3d.project import Project
    prj = Project.create("Float Test", "X", lat=1.0, lon=2.0)
    prj.write_json("spec/landmark_spec.json", {"footprint": {"local": SQUARE}, "parts": [
        part("main", "main", 0, 20), part("block", {"rect": {"cx": 12, "cy": 0, "w": 30, "d": 10}}, 20, 30)]})
    prj.set_phase("spec_review", "waiting_approval")
    with pytest.raises(pipeline.SpecIssues):
        pipeline.approve(prj, "spec_review", "approved")
    assert pipeline.approve(prj, "spec_review", "changes_requested", "fix block")["rewound_to"] == "spec"


# ----------------------------------------------------------------------------- round 2: orientation, ground floor, glass
def test_edge_directions_find_main_frontage():
    rot = geom.rect_polygon(0, 0, 60, 20, 47.5)
    dirs = geom.edge_direction_shares(rot)
    assert abs(dirs[0][0] - 47.5) < 0.6 and dirs[0][1] > 0.7
    curvy = geom.footprint_polygon({"stadium": {"cx": 0, "cy": 0, "w": 60, "d": 20, "rot_deg": 47.5}}, [])
    assert abs(geom.edge_directions(curvy)[0] - 47.5) < 1.0       # straight sides dominate, the curves do not


def test_misaligned_part_warning_and_ai_error():
    from landmark3d import pipeline
    main = geom.rect_polygon(0, 0, 60, 30, 47.5)
    good = part("b", {"rounded_rect": {"cx": 0, "cy": 0, "w": 30, "d": 12, "rot_deg": 47.5, "r": 4}}, 20, 30)
    bad = part("b", {"rounded_rect": {"cx": 0, "cy": 0, "w": 20, "d": 10, "rot_deg": 0, "r": 4}}, 20, 30)
    base = part("main", "main", 0, 20)
    assert not [i for i in spec.check_structure([base, good], main) if i["code"] == "misaligned"]
    assert [i["code"] for i in spec.check_structure([base, bad], main) if i["code"] == "misaligned"] == ["misaligned"]
    assert pipeline.orientation_errors([bad], main) and not pipeline.orientation_errors([good], main)
    assert not pipeline.orientation_errors([dict(good, footprint={"rect": {"cx": 0, "cy": 0, "w": 9, "d": 9, "rot_deg": 137.5}})], main)


def test_ground_floor_storefronts_and_awnings():
    m = geom.Mesh()
    fac = {"opening_type": "rect", "bay_spacing": 4, "opening_width": 1.4, "opening_height": 2.4, "level_height": 5,
           "ground": {"opening_type": "storefront", "awning": True}}
    geom.facade_openings(SQUARE, 0, 20, fac, m, 100000)
    assert "accent" in m.tags
    ground_open = [f for f, t in zip(m.faces, m.tags) if t == "opening" and max(m.verts[i][2] for i in f) < 5]
    assert ground_open and all(max(m.verts[i][2] for i in f) - min(m.verts[i][2] for i in f) > 3.0 for f in ground_open)


def test_glass_normalised_and_invalid_answer_kept():
    from landmark3d import pipeline
    from landmark3d.research import ai
    ms = {"parts": [{"id": "t", "facade": {"opening_type": "curtain_wall"}, "material": {"wall": "g"}}],
          "materials": {"g": {"srgb": [70, 160, 160], "roughness": 0.3, "metallic": 0.5}}}
    pipeline.normalise_glass(ms)
    assert ms["materials"]["g"]["metallic"] >= 0.85 and ms["materials"]["g"]["roughness"] <= 0.1

    class Stub(ai.Provider):
        def chat(self, system, prompt, web_search=False, images=None, max_tokens=8000):
            return ai.AIResult('{"parts": [{"id": "main", "footprint": "main", "base_z": 0, "height": 5, '
                               '"roof": {"type": "flat"}}]}', "stub", "m", [])
    obj, res = ai.ask_json(Stub("m"), "s", "p", validator=lambda o: ["still wrong"], retries=1, accept_invalid=True)
    assert obj["parts"][0]["id"] == "main" and res.validation_errors == ["still wrong"]
    with pytest.raises(ai.AIError):
        ai.ask_json(Stub("m"), "s", "p", validator=lambda o: ["still wrong"], retries=0)


# ----------------------------------------------------------------------------- round 3: A-E
def test_per_floor_facade_entrance_and_pilasters():
    fac = {"opening_type": "rect", "bay_spacing": 4.5, "opening_width": 1.6, "opening_height": 2.4, "level_height": 5,
           "floors": [{"from": 0, "to": 0, "opening_type": "arch", "opening_width": 2.4, "opening_height": 3.6},
                      {"from": 1, "to": 1, "opening_type": "square", "opening_width": 2.0},
                      {"from": 2, "to": 2, "opening_type": "arch"},
                      {"from": 3, "to": 3, "opening_type": "rect", "pediment": True}],
           "entrance": {"width": 6, "height": 8}, "pilasters": {"every": 1}, "cornice": 1.0}
    long_box = geom.rect_polygon(0, 0, 60, 20, 0)
    m = geom.Mesh()
    made = geom.facade_openings(long_box, 0, 21, fac, m, 100000)
    assert made > 40
    # floor 1 openings are square (2 x 2 m), floor 3 has pediments (triangles tagged trim)
    tris_trim = [f for f, t in zip(m.faces, m.tags) if t == "trim" and len(f) == 3]
    assert tris_trim
    heights = {round(max(m.verts[i][2] for i in f) - min(m.verts[i][2] for i in f), 2)
               for f, t in zip(m.faces, m.tags) if t == "opening"}
    assert 2.0 in heights and 8.0 in heights          # square windows and the 8 m portal
    d = geom.Mesh()
    used = geom.facade_detail(long_box, 0, 21, fac, d, 100000)
    assert used == d.triangles() and used > 30        # cornice + pilasters


def test_declared_cantilever_allowed_within_limits():
    lower = part("mid_low", {"rect": {"cx": 6, "cy": 0, "w": 24, "d": 12}}, 20, 8)
    upper = dict(part("mid_up", {"rect": {"cx": 3, "cy": 0, "w": 30, "d": 12}}, 28, 20), cantilever_of="mid_low")  # 6 m out
    base = part("podium", {"rect": {"cx": 0, "cy": 0, "w": 40, "d": 30}}, 0, 20)
    issues = spec.check_structure([base, lower, upper], SQUARE)
    assert not [i for i in issues if i["severity"] == "error"]
    assert any(i["code"] == "cantilever" for i in issues)
    # same overhang without the declaration is an error
    assert errors([base, lower, dict(upper, cantilever_of=None)])
    # too far: rests on too little
    far = dict(part("far", {"rect": {"cx": -14, "cy": 0, "w": 30, "d": 12}}, 28, 10), cantilever_of="mid_low")
    assert [i["code"] for i in errors([base, lower, far])] == ["cantilever_too_far"]
    bad = dict(upper, cantilever_of="nope")
    assert [i["code"] for i in errors([base, lower, bad])] == ["bad_cantilever"]


def test_podium_need_not_be_the_outline(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    import json
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects"), "photo_review_rounds": 0})
    from landmark3d import pipeline
    from landmark3d.project import Project
    from landmark3d.research import ai
    prj = Project.create("Podium Test", "X", lat=10.0, lon=106.0)
    half = 0.0003
    ring = [[106 - half, 10 - half], [106 + half, 10 - half], [106 + half, 10 + half], [106 - half, 10 + half], [106 - half, 10 - half]]
    prj.write_json("input/geo/footprint.geojson", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [ring]}}]})
    pipeline.submit_inputs(prj, {"main_wall_height_m": 12, "roof_form": "flat"})
    massing = {"parts": [{"id": "podium", "footprint": {"rect": {"cx": 0, "cy": -10, "w": 40, "d": 20, "rot_deg": 0}},
                          "base_z": 0, "height": 20, "roof": {"type": "flat"}}],
               "materials": {"wall": {"srgb": [200, 200, 200]}, "roof": {"srgb": [90, 90, 90]}, "trim": {"srgb": [250, 250, 250]}}}

    class Fake(ai.Provider):
        def chat(self, system, prompt, web_search=False, images=None, max_tokens=8000):
            return ai.AIResult(json.dumps(massing), "fake", "m", [])
    monkeypatch.setattr(ai, "get", lambda task="research", name=None: Fake("m"))
    sp = pipeline.build_spec(prj)
    assert [p["id"] for p in sp["parts"]] == ["podium"]


def test_photo_review_loop_revises_then_matches(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    import json
    from PIL import Image
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d import pipeline
    from landmark3d.engine import runner
    from landmark3d.project import Project
    from landmark3d.research import ai
    prj = Project.create("Review Test", "X", lat=1.0, lon=2.0)
    photo = prj.path("input", "photos", "a.jpg")
    Image.new("RGB", (300, 200), (100, 150, 200)).save(photo)

    def fake_preview(p, spec_, views, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for name in views:
            f = out_dir / f"{name}.png"
            Image.new("RGB", (96, 64), (200, 200, 200)).save(f)
            paths.append(f)
        return paths
    monkeypatch.setattr(runner, "preview", fake_preview)
    good = {"parts": [part("podium", {"rect": {"cx": 0, "cy": 0, "w": 30, "d": 20}}, 0, 20)],
            "materials": {"wall": {"srgb": [1, 2, 3]}, "roof": {"srgb": [1, 2, 3]}, "trim": {"srgb": [1, 2, 3]}}}
    replies = [{"matches": [{"photo": 1, "label": "c", "elevation": "street", "why": "tower behind on the left"}]},
               dict(good, verdict="revise", differences=["podium too short"], views=[{"photo": 1, "azimuth_deg": 5}],
                    scores=LOW),
               {"verdict": "match", "differences": [], "scores": {**LOW, "silhouette": 3}},   # rejected: score < 4
               {"verdict": "match", "differences": [], "scores": HIGH}]
    seen_images = []

    class Fake(ai.Provider):
        def chat(self, system, prompt, web_search=False, images=None, max_tokens=8000):
            seen_images.append(list(images or []))
            return ai.AIResult(json.dumps(replies.pop(0)), "fake", "m", [])
    start = dict(good, views=[{"photo": 1, "azimuth_deg": 270, "elevation_deg": 8}])
    massing, log = pipeline.photo_review(prj, Fake("m"), start, [photo], lambda o: [], SQUARE,
                                         {"opening": {"srgb": [0, 0, 0]}}, prj.state(), 3)
    assert [e["verdict"] for e in log] == ["viewpoints matched", "revise", "match"]
    assert log[1]["differences"] == ["podium too short"] and log[1]["scores"]["facade"] == 2
    assert log[2]["scores"] == HIGH
    assert len(seen_images[0]) == 2 and str(seen_images[0][1]).endswith("sides_AH.jpg")   # photo + A-H sheet
    assert all(len(imgs) == 1 and str(imgs[0]).endswith(".jpg") for imgs in seen_images[1:])
    assert (prj.root / log[1]["images"][0]).exists() and (prj.root / log[0]["images"][0]).exists()
    # side C of a square outline (main direction 0): 0 - 90 + 2 * 45 = 0 deg; it stays fixed through revisions
    # side C of a square outline (main direction 0) is 0 deg; the review then corrected photo 1 to 5 deg
    assert log[0]["verdict"] == "viewpoints matched" and massing["views"][0]["azimuth_deg"] == 5.0


def test_best_candidate_is_kept_when_review_never_matches(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    import json
    from PIL import Image
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d import pipeline
    from landmark3d.engine import runner
    from landmark3d.project import Project
    from landmark3d.research import ai
    prj = Project.create("Best Test", "X", lat=1.0, lon=2.0)
    photo = prj.path("input", "photos", "a.jpg")
    Image.new("RGB", (300, 200), (100, 150, 200)).save(photo)

    def fake_preview(p, spec_, views, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        out = []
        for name in views:
            f = out_dir / f"{name}.png"
            Image.new("RGB", (96, 64), (200, 200, 200)).save(f)
            out.append(f)
        return out
    monkeypatch.setattr(runner, "preview", fake_preview)
    mats = {"wall": {"srgb": [1, 2, 3]}, "roof": {"srgb": [1, 2, 3]}, "trim": {"srgb": [1, 2, 3]}}
    first = {"parts": [part("podium", {"rect": {"cx": 0, "cy": 0, "w": 30, "d": 20}}, 0, 20)], "materials": mats}
    worse = {"parts": [part("podium", {"rect": {"cx": 0, "cy": 0, "w": 10, "d": 10}}, 0, 20)], "materials": mats}
    replies = [{"matches": [{"photo": 1, "label": "A"}]},
               dict(worse, verdict="revise", differences=["x"], scores=LOW),
               {"best": 1, "why": "the first draft has the right plan"}]

    class Fake(ai.Provider):
        def chat(self, system, prompt, web_search=False, images=None, max_tokens=8000):
            return ai.AIResult(json.dumps(replies.pop(0)), "fake", "m", [])
    massing, log = pipeline.photo_review(prj, Fake("m"), first, [photo], lambda o: [], SQUARE,
                                         {"opening": {"srgb": [0, 0, 0]}}, prj.state(), 1)
    assert log[-1]["step"] == "select" and log[-1]["verdict"] == "chose candidate 1 of 2"
    assert massing["parts"][0]["footprint"]["rect"]["w"] == 30


def test_ai_failure_blocks_plan_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects"), "photo_review_rounds": 0})
    from landmark3d import pipeline
    from landmark3d.project import Project
    from landmark3d.research import ai
    prj = Project.create("Quota Test", "X", lat=10.0, lon=106.0)
    half = 0.0003
    ring = [[106 - half, 10 - half], [106 + half, 10 - half], [106 + half, 10 + half], [106 - half, 10 + half], [106 - half, 10 - half]]
    prj.write_json("input/geo/footprint.geojson", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [ring]}}]})
    pipeline.submit_inputs(prj, {"main_wall_height_m": 12, "roof_form": "flat"})

    class Broke(ai.Provider):
        def chat(self, *a, **k):
            raise ai.AIError("Claude Code: You've hit your 5-hour limit")
    monkeypatch.setattr(ai, "get", lambda task="research", name=None: Broke("m"))
    sp = pipeline.build_spec(prj)
    assert sp["issues"][0]["code"] == "ai_failed"
    prj.set_phase("spec_review", "waiting_approval")
    with pytest.raises(pipeline.SpecIssues):
        pipeline.approve(prj, "spec_review", "approved")


def test_photo_triage_drops_interiors_and_historic(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    import json
    from PIL import Image
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d import pipeline
    from landmark3d.project import Project
    from landmark3d.research import ai
    prj = Project.create("Triage Test", "X", lat=1.0, lon=2.0)
    files = []
    for i in range(5):
        f = prj.path("input", "photos", f"c{i}.jpg")
        Image.new("RGB", (120, 80), (40 * i, 100, 100)).save(f)
        files.append(f)
    prj.write_json("input/sources.json", {"sources": [{"id": "U1", "kind": "photo", "path": "input/photos/c4.jpg",
                                                      "origin": "user"}]})
    reply = {"photos": [{"n": 1, "kind": "exterior", "current": True, "score": 3},
                        {"n": 2, "kind": "interior", "current": True, "score": 1},
                        {"n": 3, "kind": "exterior", "current": False, "score": 3},
                        {"n": 4, "kind": "aerial", "current": True, "score": 2},
                        {"n": 5, "kind": "detail", "current": True, "score": 1}]}

    class Fake(ai.Provider):
        def chat(self, *a, **k):
            return ai.AIResult(json.dumps(reply), "fake", "m", [])
    kept, log = pipeline.triage_photos(prj, Fake("m"), files, prj.state())
    assert [f.name for f in kept] == ["c4.jpg", "c0.jpg", "c3.jpg"]   # user photo first, then by score
    assert [e["kept"] for e in log] == [True, False, False, True, True]
    assert (prj.root / "reports" / "photo_review" / "triage.jpg").exists()


# ----------------------------------------------------------------------------- round 4: taper and decks
def test_taper_geometry_and_profiles():
    base = geom.footprint_polygon({"stadium": {"cx": 0, "cy": 0, "w": 40, "d": 30}}, [])
    taper = {"top_scale": 0.4, "profile": "ease_in", "top_shift": [3, 0]}
    top = geom.ring_at(base, 0, 200, taper, 200)
    assert abs(geom.area(top) / geom.area(base) - 0.16) < 0.01
    assert abs(geom.centroid(top)[0] - (geom.centroid(base)[0] + 3)) < 1e-6
    mid_bud = geom.ring_at(base, 0, 200, taper, 100)
    mid_lin = geom.ring_at(base, 0, 200, dict(taper, profile="linear"), 100)
    assert geom.area(mid_bud) > geom.area(mid_lin)            # ease_in stays wider half way up
    m = geom.Mesh()
    geom.build_part(base, 0, 200, {"type": "shed", "height": 12}, m, taper)
    assert max(v[2] for v in m.verts) == pytest.approx(212.0) and m.triangles() > 8 * len(base)
    cw = geom.Mesh()
    geom.facade_openings(base, 0, 200, {"opening_type": "curtain_wall", "level_height": 4, "bay_spacing": 2}, cw,
                         50000, taper)
    top_mullion_x = max(v[0] for v in cw.verts if v[2] > 199)
    assert top_mullion_x < 0.5 * 20 + 3 + 0.5                  # the frame follows the taper to the narrow top


def test_tower_on_tapered_part_and_deck_cantilever():
    tower = part("tower", {"rect": {"cx": 0, "cy": 0, "w": 30, "d": 30}}, 0, 100)
    tower["taper"] = {"top_scale": 0.5}
    crown_ok = part("crown", {"rect": {"cx": 0, "cy": 0, "w": 14, "d": 14}}, 100, 10)
    crown_too_big = part("crown", {"rect": {"cx": 0, "cy": 0, "w": 28, "d": 28}}, 100, 10)
    assert errors([tower, crown_ok]) == []
    assert [e["code"] for e in errors([tower, crown_too_big])] == ["unsupported"]   # top is only 15 m wide
    # a 22 m helipad deck fixed to the side at 60 m (tower about 22.5 m wide there): allowed as a thin deck
    deck = dict(part("helipad", {"circle": {"cx": 18, "cy": 0, "r": 11}}, 60, 1.5), cantilever_of="tower")
    assert errors([tower, deck]) == []
    thick = dict(deck, height=12)                               # the same plan as a 12 m volume is too far out
    assert [e["code"] for e in errors([tower, thick])] == ["cantilever_too_far"]


# ----------------------------------------------------------------------------- round 5: features, facade pass, judge
def test_feature_primitives():
    box = geom.rect_polygon(0, 0, 40, 20, 0)
    m = geom.Mesh()
    used = geom.features(box, 0, 20, None, [
        {"type": "disc", "facing_deg": 270, "z": 15, "diameter": 3, "material": "clock"},   # faces south? no: west
        {"type": "panel", "facing_deg": -90, "z": 18, "width": 12, "height": 1.2, "material": "sign"},
        {"type": "mast", "x": 0, "y": 0, "height": 8, "material": "steel"}], m)
    assert used == m.triangles() and {"m:clock", "m:sign", "m:steel"} <= set(m.tags)
    sign = [m.verts[i] for f, t in zip(m.faces, m.tags) if t == "m:sign" for i in f]
    assert all(v[1] < -9.9 for v in sign)                     # on the south face (y = -10), facing south
    assert max(v[2] for v in m.verts) == pytest.approx(28.0)  # mast on the roof


def test_facade_pass_keeps_geometry_and_judge_scores(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    import json
    from PIL import Image
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d import pipeline
    from landmark3d.engine import runner
    from landmark3d.project import Project
    from landmark3d.research import ai
    prj = Project.create("Facade Test", "X", lat=1.0, lon=2.0)
    photo = prj.path("input", "photos", "a.jpg")
    Image.new("RGB", (200, 150), (120, 120, 90)).save(photo)
    mats = {"wall": {"srgb": [1, 2, 3]}, "roof": {"srgb": [1, 2, 3]}, "trim": {"srgb": [1, 2, 3]}}
    massing = {"parts": [part("hall", {"rect": {"cx": 0, "cy": 0, "w": 30, "d": 20}}, 0, 15)], "materials": mats,
               "views": [{"photo": 1, "azimuth_deg": 270, "elevation_deg": 8}]}
    reply = {"parts": [{"id": "hall", "footprint": "IGNORED", "height": 99,
                        "facade": {"opening_type": "arch", "level_height": 5, "bay_spacing": 4},
                        "features": [{"type": "disc", "facing_deg": 270, "z": 12, "diameter": 3, "material": "clock"}]}],
             "materials": {"clock": {"srgb": [240, 240, 230]}}, "notes": ["clock over the portal"]}

    class Fake(ai.Provider):
        def chat(self, *a, **k):
            return ai.AIResult(json.dumps(reply), "fake", "m", [])
    check = lambda o: pipeline.material_errors(o) + spec.validate_massing({"parts": o["parts"], "materials": o["materials"]})
    out, entry = pipeline.facade_pass(prj, Fake("m"), massing, [photo], check, prj.state())
    hall = out["parts"][0]
    assert hall["height"] == 15 and hall["footprint"]["rect"]["w"] == 30          # geometry untouched
    assert hall["facade"]["opening_type"] == "arch" and hall["features"][0]["material"] == "clock"
    assert "clock" in out["materials"] and entry["verdict"].endswith("1 detail features")

    def fake_preview(p, spec_, views, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        res = []
        for name in views:
            f = out_dir / f"{name}.png"
            Image.new("RGB", (96, 64), (200, 200, 200)).save(f)
            res.append(f)
        return res
    monkeypatch.setattr(runner, "preview", fake_preview)
    reply.clear()
    reply.update({"scores": {"plan": 4, "silhouette": 4, "heights": 3, "facade": 3, "materials": 4}, "overall": 7,
                  "errors": ["spire too short"]})
    q = pipeline.judge(prj, Fake("m"), out, [photo], [], SQUARE, mats, prj.state())
    assert q["overall"] == 7 and q["scores"]["heights"] == 3 and q["images"]


def test_features_stay_on_their_facade_and_material_budget():
    from landmark3d import pipeline
    box = geom.rect_polygon(0, 0, 10, 8, 0)
    m = geom.Mesh()
    geom.features(box, 0, 10, None, [{"type": "panel", "facing_deg": 270, "z": 5, "width": 30, "height": 1,
                                      "offset": 20, "material": "s"},
                                     {"type": "disc", "facing_deg": 45, "z": 5, "diameter": 2, "material": "s"}], m)
    xs = [m.verts[i][0] for f in m.faces for i in f]
    assert min(xs) >= -5 and max(xs) <= 5           # clamped to the 10 m south facade
    ys = {round(m.verts[i][1], 1) for f in m.faces for i in f}
    assert all(y <= -3.9 for y in ys)                # the 45 deg disc found no facade facing it: skipped
    mats = {k: {"srgb": [1, 1, 1]} for k in ("wall", "roof", "trim", "a", "b")}
    massing = {"parts": [dict(part("p", "main", 0, 5), material={"wall": "a", "roof": "b", "trim": "trim"},
                              features=[{"type": "mast", "material": "wall"}, {"type": "mast", "material": "roof"}])],
               "materials": mats}
    assert any("limit is 5" in e for e in pipeline.material_errors(massing, 5))
    assert not pipeline.material_errors(massing, 10)


def test_facade_pass_discarded_when_judge_score_drops(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects"), "photo_review_rounds": 0})
    from landmark3d import pipeline
    from landmark3d.project import Project
    from landmark3d.research import ai
    from PIL import Image
    import json
    prj = Project.create("Keep Test", "X", lat=10.0, lon=106.0)
    half = 0.0003
    ring = [[106 - half, 10 - half], [106 + half, 10 - half], [106 + half, 10 + half], [106 - half, 10 + half], [106 - half, 10 - half]]
    prj.write_json("input/geo/footprint.geojson", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [ring]}}]})
    pipeline.submit_inputs(prj, {"main_wall_height_m": 12, "roof_form": "flat"})
    Image.new("RGB", (200, 150), (100, 100, 100)).save(prj.path("input", "photos", "a.jpg"))
    prj.write_json("input/sources.json", {"sources": [{"id": "U1", "kind": "photo", "path": "input/photos/a.jpg", "origin": "user"}]})
    mats = {"wall": {"srgb": [1, 2, 3]}, "roof": {"srgb": [1, 2, 3]}, "trim": {"srgb": [1, 2, 3]}}
    massing = {"parts": [dict(part("hall", {"rect": {"cx": 0, "cy": 0, "w": 30, "d": 20}}, 0, 12),
                              facade={"opening_type": "arch", "level_height": 4})], "materials": mats}
    worse = {"parts": [{"id": "hall", "facade": {"opening_type": "rect", "level_height": 4}}], "materials": {}}
    calls = []
    monkeypatch.setattr(pipeline, "judge", lambda *a, **k: calls.append(1) or {"overall": 7 if len(calls) == 1 else 5,
                                                                            "scores": {}})

    class Fake(ai.Provider):
        def chat(self, system, prompt, **k):
            return ai.AIResult(json.dumps(worse if "FACADES" in prompt else massing), "fake", "m", [])
    monkeypatch.setattr(ai, "get", lambda task="research", name=None: Fake("m"))
    sp = pipeline.build_spec(prj)
    assert sp["parts"][0]["facade"]["opening_type"] == "arch"           # the worse facade pass was discarded
    assert sp["quality"]["overall"] == 7 and len(calls) == 4            # a discarded round does not end the pass
    assert "discarded" in sp["photo_review"][-1]["result"]
