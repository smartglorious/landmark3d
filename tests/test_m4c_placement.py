"""M4 placement into a part, and the material usage warnings. Offline."""
import pytest

from landmark3d import geom
from landmark3d.detail import materials as mat
from landmark3d.detail import placement

POLY = geom.rect_polygon(0, 0, 40, 20, 0)
PART = {"id": "hall", "base_z": 0.0, "height": 12.0}


def with_items(*items):
    return dict(PART, articulation=list(items))


def test_without_the_flag_articulation_is_reported_not_built():
    m = geom.Mesh()
    out = placement.place_articulation(m, POLY, with_items({"generator": "plinth_band", "params": {}}), False, 10 ** 6)
    assert m.faces == [] and out["placed"] == [] and out["skipped"][0].startswith("hall:plinth_band")


def test_with_the_flag_the_plinth_is_built_on_the_base():
    m = geom.Mesh()
    out = placement.place_articulation(m, POLY, with_items({"generator": "plinth_band", "params": {"height": 0.6}}),
                                       True, 10 ** 6)
    assert out["placed"] == ["hall:plinth_band"] and out["triangles"] == m.triangles() > 0
    zs = [v[2] for v in m.verts]
    assert min(zs) == pytest.approx(0.0) and max(zs) == pytest.approx(0.6)


def test_the_part_height_is_not_passed_to_the_generator():
    # A plinth with the generator's own default must not inherit the 12 m height of the part.
    m = geom.Mesh()
    placement.place_articulation(m, POLY, with_items({"generator": "plinth_band", "params": {}}), True, 10 ** 6)
    assert max(v[2] for v in m.verts) < 2.0


def test_budget_skips_what_does_not_fit():
    m = geom.Mesh()
    out = placement.place_articulation(m, POLY, with_items({"generator": "plinth_band", "params": {}}), True, 5)
    assert out["placed"] == [] and out["skipped"] == ["hall:plinth_band (budget)"]
    assert m.faces == []


def test_unknown_generator_is_an_error_never_a_silent_skip():
    with pytest.raises(ValueError, match="not a registered generator"):
        placement.place_articulation(geom.Mesh(), POLY, with_items({"generator": "no_such_thing", "params": {}}), True, 10 ** 6)


def test_a_part_with_no_articulation_is_untouched():
    m = geom.Mesh()
    out = placement.place_articulation(m, POLY, dict(PART), True, 10 ** 6)
    assert out == {"placed": [], "skipped": [], "triangles": 0} and m.faces == []


def test_metal_ornament_is_not_a_wall_warning():
    spec = {"materials": {"gold_finial": {"srgb": [200, 170, 80], "metallic": 0.9},
                          "wall": {"srgb": [230, 205, 150], "metallic": 0.0}},
            "parts": [{"id": "hall", "material": {"wall": "wall", "trim": "gold_finial"}}]}
    assert mat.architectural_warnings(spec) == []


def test_a_metallic_wall_is_flagged_once_and_names_the_part():
    spec = {"materials": {"gate_green": {"srgb": [45, 58, 48], "metallic": 0.2}},
            "parts": [{"id": "gate", "material": {"wall": "gate_green"}},
                      {"id": "gate2", "material": {"wall": "gate_green"}}]}
    w = mat.architectural_warnings(spec)
    assert len(w) == 1 and "gate_green" in w[0] and "gate" in w[0]


def test_glass_is_exempt_even_when_used_as_a_wall():
    spec = {"materials": {"curtain_glass": {"srgb": [20, 60, 80], "metallic": 0.9}},
            "parts": [{"id": "tower", "material": {"wall": "curtain_glass"}}]}
    assert mat.architectural_warnings(spec) == []
