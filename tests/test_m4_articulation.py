"""M4: articulation slices (plinth, brise-soleil array, chimney stack). Offline and synthetic."""
import math

import pytest

from landmark3d import geom
from landmark3d.detail import articulation, registry

POLY = geom.rect_polygon(0, 0, 40, 20, 0)     # south face at y = -10, facing 270


def test_each_slice_is_registered_and_the_library_is_valid():
    assert registry.validate_registry() == []
    names = {"plinth_band", "brise_soleil_array", "chimney_stack"}
    assert names <= set(registry.GENERATORS)
    comps = {c["id"]: c for c in registry.load_library()["components"]}
    assert comps["plinth_string_course"]["status"] == "implemented"
    assert comps["brise_soleil_array"]["status"] == "implemented"
    assert comps["chimney_stack"]["status"] == "implemented"


def test_plinth_rings_the_base_and_stays_at_the_base_height():
    comp = articulation.plinth_band({"poly": POLY, "base_z": 0.0, "height": 0.6, "projection": 0.12})
    assert comp.problems() == [] and comp.triangle_count > 0
    lo, hi = comp.bounds.lo, comp.bounds.hi
    assert lo[2] == pytest.approx(0.0) and hi[2] == pytest.approx(0.6)
    assert lo[0] <= -20 and hi[0] >= 20 and lo[1] <= -10 and hi[1] >= 10     # it wraps all four sides


def test_plinth_projection_is_bounded():
    with pytest.raises(ValueError):
        articulation.plinth_band({"poly": POLY, "height": 0.6, "projection": 1.5})


def test_brise_soleil_fins_are_spaced_and_stay_on_their_face():
    comp = articulation.brise_soleil_array({"poly": POLY, "facing_deg": 270, "base_z": 0, "z": 2.0, "height": 3.0,
                                            "count": 6, "spacing": 1.5, "fin_width": 0.12, "depth": 0.6})
    assert comp.problems() == []
    assert comp.triangle_count == 6 * 12                     # six boxes, twelve triangles each
    ys = [v[1] for v in comp.mesh.verts]
    # the south face's outward normal points to -y: the fins stand from the wall (y=-10) out to y=-10.6, nowhere else
    assert min(ys) >= -10.0 - 0.6 - 1e-6 and max(ys) <= -10.0 + 1e-6
    zs = [v[2] for v in comp.mesh.verts]
    assert min(zs) == pytest.approx(2.0) and max(zs) == pytest.approx(5.0)


def test_brise_soleil_refuses_an_array_that_does_not_fit():
    with pytest.raises(ValueError, match="does not fit|needs"):
        articulation.brise_soleil_array({"poly": POLY, "facing_deg": 270, "count": 30, "spacing": 1.5})


def test_brise_soleil_refuses_a_face_nobody_points_to():
    with pytest.raises(ValueError, match="no outline face"):
        articulation.brise_soleil_array({"poly": POLY, "facing_deg": 45, "count": 2})


def test_brise_soleil_count_scales_linearly():
    one = articulation.brise_soleil_array({"poly": POLY, "facing_deg": 270, "count": 1, "spacing": 1.0}).triangle_count
    four = articulation.brise_soleil_array({"poly": POLY, "facing_deg": 270, "count": 4, "spacing": 1.5}).triangle_count
    assert four == 4 * one


def test_chimney_rises_from_its_base_with_a_cap():
    comp = articulation.chimney_stack({"x": 5, "y": 2, "width": 0.8, "depth": 0.8, "z0": 10, "z1": 12.5, "cap": 0.12})
    assert comp.problems() == []
    assert comp.bounds.lo[2] == pytest.approx(10.0) and comp.bounds.hi[2] == pytest.approx(12.6)
    assert comp.bounds.lo[0] == pytest.approx(5 - 0.4 - 0.12)


def test_chimney_top_must_be_above_its_base():
    with pytest.raises(ValueError):
        articulation.chimney_stack({"z0": 5, "z1": 4})


@pytest.mark.parametrize("gen, params", [
    ("plinth_band", {"poly": POLY, "height": 0.6, "projection": 0.12}),
    ("brise_soleil_array", {"poly": POLY, "facing_deg": 270, "count": 4, "spacing": 2.0}),
    ("chimney_stack", {"x": 0, "y": 0, "z0": 0, "z1": 2}),
])
def test_every_slice_returns_finite_geometry_with_matching_accounting(gen, params):
    comp = registry.GENERATORS[gen](params)
    assert comp.problems() == []
    assert all(math.isfinite(c) for v in comp.mesh.verts for c in v)
    assert comp.material_slots <= {"trim", "roof", "wall"}
