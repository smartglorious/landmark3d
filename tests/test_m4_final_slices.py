"""M4 slices closed last: mansard and gambrel roofs, string course, roof gutter and trim, bundled tubes, sign/frieze.

Pure geometry on synthetic outlines; no AI, no landmark names.
"""
import pytest

from landmark3d import geom, spec
from landmark3d.detail import articulation, registry

RECT = [(-10.0, -6.0), (10.0, -6.0), (10.0, 6.0), (-10.0, 6.0)]


def _roof(rtype, height=5.0):
    m = geom.Mesh()
    geom.build_part(RECT, 0.0, 10.0, {"type": rtype, "height": height}, m)
    return m


def _roof_z(m):
    return [v[2] for f, t in zip(m.faces, m.tags) if t == "roof" for v in (m.verts[i] for i in f)]


@pytest.mark.parametrize("rtype", ["mansard", "gambrel"])
def test_two_slope_roofs_reach_their_height_and_stay_inside_the_plan(rtype):
    m = _roof(rtype)
    zs = _roof_z(m)
    assert min(zs) == pytest.approx(10.0, abs=1e-6)
    assert max(zs) == pytest.approx(15.0, abs=0.01)             # the hip cap stops 0.1% short of its apex by design
    for f, t in zip(m.faces, m.tags):
        if t == "roof":
            for i in f:
                x, y, _ = m.verts[i]
                assert -10 - 1e-6 <= x <= 10 + 1e-6 and -6 - 1e-6 <= y <= 6 + 1e-6


def test_mansard_has_a_steep_lower_slope_and_a_shallow_top():
    m = _roof("mansard")
    zs = sorted(set(round(z, 6) for z in _roof_z(m)))
    knee = 10.0 + 5.0 * geom.MANSARD_RISE
    assert any(abs(z - knee) < 1e-6 for z in zs)               # the break line exists
    hip = _roof("hip")
    assert len(m.faces) > len([t for t in hip.tags if t == "roof"])


def test_gambrel_breaks_at_the_knee_and_its_gable_walls_follow_the_roof():
    m = _roof("gambrel")
    k, kh = geom.GAMBREL_KNEE
    knee_z = 10.0 + 5.0 * kh
    assert any(abs(z - knee_z) < 1e-6 for z in _roof_z(m))
    wall_top = max(m.verts[i][2] for f, t in zip(m.faces, m.tags) if t == "wall" for i in f)
    assert wall_top == pytest.approx(15.0, abs=1e-6)            # gable end walls reach the ridge


def test_gambrel_roof_is_closed_on_its_top():
    # the four strips cover the plan exactly once: their projected areas add up to the footprint area
    m = _roof("gambrel")
    total = sum(geom.area([(m.verts[i][0], m.verts[i][1]) for i in f]) for f, t in zip(m.faces, m.tags) if t == "roof")
    assert total == pytest.approx(geom.area(RECT), rel=1e-6)


def test_the_spec_accepts_and_osm_maps_the_new_roofs():
    assert {"mansard", "gambrel"} <= set(spec.ROOF_TYPES)
    assert spec.OSM_ROOF["mansard"] == "mansard" and spec.OSM_ROOF["gambrel"] == "gambrel"


def test_a_small_plan_falls_back_to_a_hip_instead_of_failing():
    m = geom.Mesh()
    geom.build_mansard([(0, 0), (0.3, 0), (0.3, 0.3), (0, 0.3)], 5.0, 1.0, m)
    assert m.triangles() > 0


@pytest.mark.parametrize("cid, gen", [("string_course", "string_course"), ("roof_gutter_trim", "roof_gutter"),
                                      ("bundled_tube_assembly", "bundled_tubes"), ("sign_frieze_panel", "sign_panel")])
def test_new_slices_are_registered_and_implemented(cid, gen):
    lib = registry.load_library()
    assert registry.validate_registry(lib) == []
    c = registry.component(cid, lib)
    assert c["status"] == "implemented" and [l["generator"] for l in c["lods"]] == [gen]


def test_string_course_sits_at_its_height_and_projects():
    comp = articulation.string_course({"poly": RECT, "base_z": 0.0, "z": 4.0, "height": 0.3, "projection": 0.1})
    assert comp.bounds.lo[2] == pytest.approx(4.0) and comp.bounds.hi[2] == pytest.approx(4.3)
    assert comp.bounds.hi[0] == pytest.approx(10.1, abs=1e-6)
    with pytest.raises(ValueError):
        articulation.string_course({"poly": RECT, "height": 2.0})


def test_roof_gutter_stands_in_front_of_the_eave_below_it():
    comp = articulation.roof_gutter({"poly": RECT, "base_z": 0.0, "z": 10.0})
    assert comp.bounds.hi[2] == pytest.approx(10.0)
    assert comp.bounds.hi[0] > 10.0 and comp.problems() == []
    with pytest.raises(ValueError):
        articulation.roof_gutter({"poly": RECT, "gutter_width": 1.0})


def test_bundled_tubes_step_their_heights_and_skip_empty_cells():
    comp = articulation.bundled_tubes({"x": 0, "y": 0, "cell": 5.0, "heights": [[20, 0], [40, 30]]})
    assert comp.bounds.hi[2] == pytest.approx(40.0)
    assert comp.material_slots == {"wall"} and comp.problems() == []
    one = articulation.bundled_tubes({"cell": 5.0, "heights": [[20]]})
    assert comp.triangle_count == 3 * one.triangle_count
    with pytest.raises(ValueError):
        articulation.bundled_tubes({"heights": [[0, 0]]})


def test_sign_and_frieze_fit_their_face():
    sign = articulation.sign_panel({"poly": RECT, "facing_deg": 270, "kind": "sign", "width": 6.0, "z": 3.0})
    frieze = articulation.sign_panel({"poly": RECT, "facing_deg": 270, "kind": "frieze", "margin": 1.0, "z": 8.0})
    assert sign.material_slots == {"trim", "accent"} and sign.problems() == []
    assert frieze.bounds.hi[0] - frieze.bounds.lo[0] > sign.bounds.hi[0] - sign.bounds.lo[0]
    with pytest.raises(ValueError):
        articulation.sign_panel({"poly": RECT, "facing_deg": 270, "kind": "sign", "width": 30.0})
