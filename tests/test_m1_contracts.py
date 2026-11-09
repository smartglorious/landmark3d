"""M1 versioned contracts and accounting (docs/DETAIL_ENGINE_V2_PLAN.md section 10, M1; section 11 test list).

Offline only. Covers: types and provenance rules, component registry, observation schema and validation, deterministic
merge, spec version 2 schema, v1 facade adapter round-trip, accounting that equals the meshes, and the disabled flag
that must leave v1 geometry untouched.
"""
import copy
import importlib
import json
import subprocess

import pytest

from landmark3d import facade_grammar, geom, observations, spec_v2
from landmark3d.detail import accounting, engine, registry
from landmark3d.detail.types import Bounds, DetailCandidate, GeneratedComponent, Provenance


# ============================================================================ types (task 1)
def quad_mesh(tag="wall", n=2):
    m = geom.Mesh()
    for i in range(n):
        x = i * 2.0
        m.f([m.v(x, 0, 0), m.v(x + 1, 0, 0), m.v(x + 1, 0, 1), m.v(x, 0, 1)], tag)
    return m


def test_generated_component_accounting_must_match_its_mesh():
    m = quad_mesh(n=2)
    assert GeneratedComponent(mesh=m, triangle_count=4).problems() == []
    assert any("does not match" in p for p in GeneratedComponent(mesh=m, triangle_count=3).problems())


def test_generated_component_rejects_bad_geometry():
    m = quad_mesh()
    m.verts[0] = (float("nan"), 0, 0)
    assert any("non-finite" in p for p in GeneratedComponent(mesh=m, triangle_count=2).problems())
    assert any("below" in p or "corner" in p for p in GeneratedComponent(mesh=quad_mesh(), triangle_count=2,
                                                                          bounds=Bounds((1, 0, 0), (0, 0, 0))).problems())


@pytest.mark.parametrize("prov, ok", [
    (Provenance("observed", 0.9, "FO-001"), True),
    (Provenance("default", 0.5), True),
    (Provenance("default", 0.6), False),                  # a default may not look more certain than 0.5
    (Provenance("observed", 0.9), False),                 # a non-default needs its source
    (Provenance("guessed", 0.9, "x"), False),             # unknown kind
    (Provenance("symmetry", 1.5, "FE-1"), False),         # confidence out of range
])
def test_provenance_rules(prov, ok):
    assert (prov.problems() == []) is ok


def test_detail_candidate_needs_a_real_upgrade():
    assert DetailCandidate("c", ["a"], 1, 2, 100).problems() == []
    assert DetailCandidate("c", ["a"], 2, 2, 100).problems() != []
    assert DetailCandidate("c", [], 0, 1, 100).problems() != []
    assert DetailCandidate("c", ["a"], 0, 1, -5).problems() != []


# ============================================================================ registry (task 2)
def test_the_shipped_library_is_valid_and_versioned():
    assert registry.validate_registry() == []
    assert registry.LIBRARY_VERSION == "1.0.0"
    ids = [c["id"] for c in registry.load_library()["components"]]
    assert len(ids) == len(set(ids)) and "opening_assembly_rect" in ids


def test_declared_generators_must_exist():
    lib = copy.deepcopy(registry.load_library())
    comp = registry.component("plinth_string_course", lib)
    comp["status"] = "implemented"
    comp["lods"] = [{"level": 0, "generator": "band_panel"}]
    assert any("not registered" in e for e in registry.validate_registry(lib, generators={}))
    assert registry.validate_registry(lib, generators={**registry.GENERATORS, "band_panel": lambda p: None}) == []


def test_registry_rejects_duplicate_ids_and_bad_parameters():
    lib = copy.deepcopy(registry.load_library())
    lib["components"].append(copy.deepcopy(lib["components"][0]))
    assert any("duplicate" in e for e in registry.validate_registry(lib))
    lib = copy.deepcopy(registry.load_library())
    lib["components"][0]["parameters"]["width_m"]["default"] = 99
    assert any("default outside" in e for e in registry.validate_registry(lib))


def test_a_planned_component_cannot_claim_lods_yet():
    lib = copy.deepcopy(registry.load_library())
    registry.component("plinth_string_course", lib)["lods"] = [{"level": 0, "generator": "x"}]
    assert any("planned" in e for e in registry.validate_registry(lib, generators={"x": None}))


def test_registry_is_bundled_with_the_package():
    from pathlib import Path
    assert registry.LIBRARY_FILE.is_file()
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert "components/*.json" in pyproject and "schemas/*.json" in pyproject


# ============================================================================ observations (task 3)
SOURCES = {"SRC-012": {"kind": "photo", "use": "reference_and_texture"},
           "SRC-099": {"kind": "photo", "use": "excluded"},
           "SRC-050": {"kind": "satellite", "use": "reference_only"}}
PARTS = {"main"}


def observation(**over):
    ob = {"id": "FO-001", "source_id": "SRC-012", "part_id": "main", "photo_path": "input/photos/p.jpg",
          "facade_run": 0,
          "quad": [[0.08, 0.18], [0.92, 0.19], [0.94, 0.90], [0.06, 0.89]],
          "camera": {"azimuth_deg": 181.0, "elevation_deg": 7.0},
          "floor_lines": [0.90, 0.67, 0.43, 0.18],
          "bay_lines": [0.08, 0.20, 0.32, 0.44, 0.56, 0.68, 0.80, 0.92],
          "elements": [{"id": "FE-001", "type": "arched_window", "bbox": [0.22, 0.45, 0.30, 0.63], "floor": 1, "bay": 1,
                        "symmetry_group": "upper_windows", "attributes": {"surround": True}, "confidence": 0.91,
                        "provenance": "observed"}],
          "confidence": 0.88, "review": {"status": "accepted", "notes": []}}
    ob.update(over)
    return ob


def doc(*obs):
    return {"version": 1, "observations": list(obs)}


def test_a_valid_observation_file_passes():
    assert observations.validate(doc(observation()), SOURCES, PARTS) == []


@pytest.mark.parametrize("change, needle", [
    ({"quad": [[0.08, 0.18], [1.2, 0.19], [0.94, 0.90], [0.06, 0.89]]}, "quad"),           # coordinate above 1
    ({"floor_lines": [0.90, 0.43, 0.67, 0.18]}, "floor_lines"),                              # not ordered
    ({"bay_lines": [0.08, 0.08, 0.32]}, "bay_lines"),                                        # repeated line
    ({"part_id": "tower"}, "unknown part"),
    ({"source_id": "SRC-404"}, "unknown source"),
    ({"source_id": "SRC-099"}, "not a usable photo"),                                        # excluded by the user
    ({"source_id": "SRC-050"}, "not a usable photo"),                                        # satellite is not a facade photo
    ({"confidence": 1.5}, "confidence"),
])
def test_invalid_observation_fields_are_rejected(change, needle):
    errors = observations.validate(doc(observation(**change)), SOURCES, PARTS)
    assert errors and any(needle in e for e in errors), errors


@pytest.mark.parametrize("bbox, needle", [
    ([0.22, 0.45, 0.22, 0.63], "positive area"),
    ([0.01, 0.45, 0.30, 0.63], "outside the facade quad"),
    ([0.22, 0.45, 0.30, 0.99], "outside the facade quad"),
])
def test_elements_must_have_area_and_sit_inside_the_facade(bbox, needle):
    ob = observation()
    ob["elements"][0]["bbox"] = bbox
    errors = observations.validate(doc(ob), SOURCES, PARTS)
    assert any(needle in e for e in errors), errors


def test_element_floor_must_exist_and_ids_must_be_unique():
    ob = observation()
    ob["elements"][0]["floor"] = 9
    assert any("not between the floor lines" in e for e in observations.validate(doc(ob), SOURCES, PARTS))
    ob = observation()
    ob["elements"].append(copy.deepcopy(ob["elements"][0]))
    assert any("duplicate element" in e for e in observations.validate(doc(ob), SOURCES, PARTS))
    assert any("duplicate observation" in e for e in observations.validate(doc(observation(), observation()), SOURCES, PARTS))


def test_schema_refuses_unknown_provenance():
    ob = observation()
    ob["elements"][0]["provenance"] = "guessed"
    assert observations.schema_errors(doc(ob))


def test_merge_keeps_the_most_confident_observation_regardless_of_order():
    low = observation(id="FO-A", confidence=0.6)
    high = observation(id="FO-B", confidence=0.9)
    high["elements"][0] = dict(high["elements"][0], confidence=0.95)
    low["elements"][0] = dict(low["elements"][0], confidence=0.5)
    a, issues_a = observations.merge([low, high])
    b, issues_b = observations.merge([high, low])
    assert a == b and issues_a == issues_b == []
    assert a[0]["merged_from"] == ["FO-B", "FO-A"] and a[0]["confidence"] == 0.95


def test_confident_disagreement_is_reported_not_averaged():
    a = observation(id="FO-A", confidence=0.95)
    b = observation(id="FO-B", confidence=0.9)
    b["elements"][0] = dict(b["elements"][0], type="round_window", bbox=[0.5, 0.1, 0.6, 0.2])
    merged, issues = observations.merge([a, b])
    assert merged[0]["type"] == "arched_window" and merged[0]["bbox"] == [0.22, 0.45, 0.30, 0.63]
    assert issues and "review" in issues[0]


def test_weak_disagreement_is_not_a_review_item():
    a = observation(id="FO-A", confidence=0.9)
    b = observation(id="FO-B", confidence=0.4)
    b["elements"][0] = dict(b["elements"][0], type="round_window")
    _, issues = observations.merge([a, b])
    assert issues == []


# ============================================================================ spec version 2 (task 3)
def v2_spec(**over):
    spec = {"spec_version": 2, "materials": {}, "budgets": {},
            "parts": [{"id": "main", "facade_system": {
                "style_family": "classical", "style_confidence": 0.76,
                "runs": [{"run": 2,
                          "grammar": {"floor_zones": [{"kind": "ground", "from": 0, "to": 0, "module": "storefront_arch"},
                                                      {"kind": "repeat", "from": 1, "to": 2,
                                                       "module": "arched_window_classical"},
                                                      {"kind": "cornice", "module": "cornice_stepped"}],
                                      "bay_count": 7, "bay_width_m": 3.85, "symmetry": "mirror"},
                          "overrides": [{"floor": 0, "bay": 3, "module": "entrance_portal", "params": {"width_m": 6.4}}],
                          "evidence": ["FO-001"]}]}}]}
    spec.update(over)
    return spec


def test_a_valid_v2_spec_passes():
    assert spec_v2.validate(v2_spec()) == []


def test_v2_refuses_the_wrong_version_and_expanded_instances():
    assert spec_v2.validate(v2_spec(spec_version=1)) != []
    bad = v2_spec()
    bad["parts"][0]["facade_system"]["runs"][0]["grammar"]["instances"] = [{"x": 1}]
    assert spec_v2.validate(bad) != []


def test_v2_semantic_checks():
    s = v2_spec()
    s["parts"][0]["facade_system"]["runs"][0]["overrides"][0]["bay"] = 9
    assert any("outside the grammar" in e for e in spec_v2.validate(s))
    s = v2_spec()
    s["parts"][0]["facade_system"]["runs"][0]["grammar"]["floor_zones"][1]["to"] = 0
    assert any("ends below" in e for e in spec_v2.validate(s))
    s = v2_spec()
    s["parts"][0]["facade_system"]["runs"].append(copy.deepcopy(s["parts"][0]["facade_system"]["runs"][0]))
    assert any("duplicate facade run" in e for e in spec_v2.validate(s))


def test_v2_style_families_and_ranges_are_closed_sets():
    s = v2_spec()
    s["parts"][0]["facade_system"]["style_family"] = "art_deco_guess"
    assert spec_v2.validate(s) != []
    s = v2_spec()
    s["parts"][0]["facade_system"]["style_confidence"] = 2
    assert spec_v2.validate(s) != []


# ============================================================================ v1 adapter (task 4)
POLY = geom.rect_polygon(0, 0, 30, 20, 0)
FACADES = [
    {"opening_type": "arch", "level_height": 6.2, "bay_spacing": 6.5, "opening_width": 3.0, "opening_height": 8.5,
     "pediment": False, "pilasters": {"every": 1, "width": 1.0, "depth": 0.4}, "cornice": 0.8, "bands": True,
     "floors": [{"from": 0, "to": 0, "opening_type": "arch", "bay_spacing": 6.5, "opening_width": 3.4, "opening_height": 8.5},
                {"from": 1, "to": 1, "opening_type": "arch", "pediment": True}],
     "ground": {"opening_type": "storefront", "bay_spacing": 6.5}},
    {"opening_type": "curtain_wall", "level_height": 3.8, "bay_spacing": 1.5},
    {"opening_type": "rect", "entrance": {"width": 4.0, "height": 9.0, "facing_deg": 318.9}},
    None,
]


@pytest.mark.parametrize("facade", FACADES)
def test_v1_facade_round_trips_exactly(facade):
    grammar = facade_grammar.from_v1(facade)
    assert facade_grammar.to_v1(grammar) == (facade or {})
    assert facade_grammar.check(grammar) == []


@pytest.mark.parametrize("facade", FACADES[:3])
def test_the_adapter_does_not_change_the_facade_layout(facade):
    before = geom.facade_layout(POLY, 0, 30, facade or {})
    after = geom.facade_layout(POLY, 0, 30, facade_grammar.to_v1(facade_grammar.from_v1(facade)))
    assert before == after


def test_the_adapter_copies_its_input():
    fac = copy.deepcopy(FACADES[0])
    grammar = facade_grammar.from_v1(fac)
    grammar["v1_facade"]["opening_type"] = "mutated"
    assert fac["opening_type"] == "arch"


def test_derived_floor_zones_describe_the_facade():
    zones = facade_grammar.from_v1(FACADES[0])["floor_zones"]
    assert {"kind": "ground", "from": 0, "to": 0, "module": "storefront"} in zones
    assert any(z["kind"] == "repeat" and z["from"] == 1 for z in zones)


def test_only_v1_grammars_convert_back():
    with pytest.raises(ValueError):
        facade_grammar.to_v1({"grammar_version": 2, "source": "v2", "v1_facade": {}})


# ============================================================================ accounting (task 5)
def test_accounting_equals_the_mesh_triangles_exactly():
    parts = [accounting.PartMesh("main", quad_mesh("wall", n=3)),
             accounting.PartMesh("main", quad_mesh("opening", n=2)),
             accounting.PartMesh("tower", quad_mesh("m:clock", n=1))]
    acc = accounting.summarize(parts, target_triangles=40, hard_triangles=50)
    assert acc["total_triangles"] == sum(accounting.triangles_of(p.mesh) for p in parts) == 12
    assert accounting.check(parts, acc) == []
    assert acc["base_triangles"] == 6 and acc["detail_triangles"] == 6
    assert acc["by_category"] == {"facade": 4, "feature": 2, "massing": 6}
    assert acc["by_part"] == {"main": 10, "tower": 2}
    assert acc["target_utilization"] == 0.3 and acc["hard_utilization"] == 0.24


def test_accounting_catches_a_mismatch():
    parts = [accounting.PartMesh("main", quad_mesh("wall", n=2))]
    acc = accounting.summarize(parts)
    acc["total_triangles"] = 99
    assert any("differs from the meshes" in e for e in accounting.check(parts, acc))


def test_accounting_reports_textures_and_material_slots():
    acc = accounting.summarize([], textures=[{"width": 512, "height": 256, "material": "wall"},
                                             {"width": 64, "height": 64, "material": "roof"}])
    assert acc["texture_pixels"] == 512 * 256 + 64 * 64
    assert acc["texture_material_slots"] == ["roof", "wall"]
    assert acc["shared_mesh_estimate"] == 0


def test_every_face_gets_exactly_one_category():
    for tag in ("wall", "roof", "opening", "trim", "accent", "m:stone", "d:stone", "mystery"):
        assert accounting.category_of(tag) in ("massing", "facade", "feature", "other")


# ============================================================================ flag and v1 preservation (task 6)
def test_the_flag_is_off_by_default():
    from landmark3d import settings
    assert settings.DEFAULTS["detail_engine_v2"] is False


def test_flag_off_or_no_v2_components_leave_the_spec_as_it_is():
    v1 = {"parts": [{"id": "main", "footprint": "main", "base_z": 0, "height": 9, "roof": {"type": "flat"},
                     "facade": FACADES[0]}]}
    assert engine.expand(v1, enabled=False) is v1
    assert engine.expand(v1, enabled=True) is v1                  # on, but nothing v2 to generate


def test_enabling_the_flag_with_v2_components_fails_loudly():
    with pytest.raises(engine.DetailEngineError, match="no v2 generator"):
        engine.expand(v2_spec(), enabled=True)
    assert engine.expand(v2_spec(), enabled=False) is not None   # flag off: ignored, v1 path


def test_enabling_the_flag_keeps_v1_geometry_identical():
    spec = {"parts": [{"id": "main", "footprint": "main", "base_z": 0, "height": 15, "roof": {"type": "hip"},
                       "facade": FACADES[0], "features": [{"type": "disc", "facing_deg": 270, "z": 12, "diameter": 3,
                                                           "material": "clock"}]}]}

    def signature(s):
        m = geom.Mesh()
        poly = geom.rect_polygon(0, 0, 30, 20, 0)
        geom.build_part(poly, 0, 15, s["parts"][0]["roof"], m)
        geom.facade_detail(poly, 0, 15, s["parts"][0]["facade"], m, 10 ** 6)
        geom.facade_openings(poly, 0, 15, s["parts"][0]["facade"], m, 10 ** 6)
        geom.features(poly, 0, 15, None, s["parts"][0]["features"], m, 10 ** 6)
        return m.verts, m.faces, m.tags
    assert signature(engine.expand(spec, enabled=True)) == signature(spec) == signature(engine.expand(spec, enabled=False))


def test_engine_settings_are_persisted_in_the_build_job(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    from landmark3d import doctor, settings, textures
    from landmark3d.engine import runner
    from landmark3d.project import Project
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    prj = Project.create("Engine Job", "X")
    prj.write_json("spec/landmark_spec.json", {"parts": [], "materials": {}, "footprint": {"local": [[0, 0], [1, 0], [1, 1]]},
                                               "budgets": {"triangles_max": 1000, "triangles_target": 800,
                                                           "bytes_max": 10 ** 6, "bytes_target": 9 * 10 ** 5,
                                                           "texture_max_px": 512}})
    prj.write_json("input/sources.json", {"sources": []})
    monkeypatch.setattr(textures, "build_job", lambda *a: ({}, [], None))
    monkeypatch.setattr(doctor, "find_blender", lambda: "blender")
    monkeypatch.setattr(runner.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, "", ""))
    runner.build(prj)
    job = json.loads((prj.root / "logs" / "build_job.json").read_text(encoding="utf-8"))
    assert job["engine"] == {"detail_engine_v2": False, "library_version": registry.LIBRARY_VERSION}


def test_the_engine_setting_is_part_of_the_build_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    from landmark3d import pipeline, settings
    from landmark3d.project import Project
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    prj = Project.create("Identity", "X")
    prj.write_json("spec/landmark_spec.json", {"parts": [], "materials": {}, "budgets": {}})
    prj.write_json("input/sources.json", {"sources": []})
    off = pipeline.build_identity(prj)
    settings.update({"detail_engine_v2": True})
    assert pipeline.build_identity(prj)["detail_engine"] != off["detail_engine"]
    settings.update({"detail_engine_v2": False})
    assert pipeline.COMPONENT_LIBRARY_VERSION == registry.LIBRARY_VERSION
