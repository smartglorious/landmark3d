"""M4 slices 2 onward: pilaster, balustrade, dormer, cupola, ridge cap, lattice mast, rose window. Offline."""
import math

import pytest

from landmark3d import geom
from landmark3d.detail import articulation, registry

POLY = geom.rect_polygon(0, 0, 40, 20, 0)     # south face at y = -10, outward normal (0, -1)


def ys(c):
    return [v[1] for v in c.mesh.verts]


def test_all_new_slices_are_registered_and_valid():
    assert registry.validate_registry() == []
    for name in ("pilaster_profile", "balustrade_run", "dormer_window", "cupola_lantern", "ridge_cap", "lattice_mast",
                 "rose_window"):
        assert name in registry.GENERATORS
    comps = {c["id"]: c for c in registry.load_library()["components"]}
    for cid in ("pilaster_profile", "balustrade_run", "mansard_dormer", "clock_rose_window", "cupola_lantern",
                "ridge_cap", "lattice_mast"):
        assert comps[cid]["status"] == "implemented", cid


def test_pilaster_stands_on_its_face_and_has_a_capital():
    c = articulation.pilaster_profile({"poly": POLY, "facing_deg": 270, "base_z": 0, "height": 6.0, "width": 0.7, "depth": 0.3})
    assert c.problems() == [] and c.triangle_count > 0
    assert min(ys(c)) >= -10.0 - 0.36 - 1e-6 and max(ys(c)) <= -10.0 + 1e-6 + 0.36   # from the wall, out at most 1.2 * depth
    assert c.bounds.hi[2] == pytest.approx(6.0)


def test_pilaster_refuses_a_face_too_short_for_it():
    with pytest.raises(ValueError):
        articulation.pilaster_profile({"poly": geom.rect_polygon(0, 0, 0.5, 20, 0), "facing_deg": 270, "width": 2.0})


def test_balustrade_has_rails_and_posts_along_the_face():
    c = articulation.balustrade_run({"poly": POLY, "facing_deg": 270, "width": 6.0, "height": 1.0, "spacing": 0.5, "projection": 0.15})
    assert c.problems() == []
    xs = [v[0] for v in c.mesh.verts]
    assert max(xs) - min(xs) == pytest.approx(6.0, abs=0.2)
    assert c.bounds.hi[2] == pytest.approx(1.0)


def test_dormer_has_a_box_and_a_hipped_cap():
    c = articulation.dormer_window({"x": 0, "y": 0, "z0": 12, "width": 2.0, "depth": 2.0, "height": 1.8, "roof_rise": 1.2})
    assert c.problems() == []
    assert c.bounds.hi[2] == pytest.approx(12 + 1.8 + 1.2)
    assert "roof" in c.material_slots or any(True for _ in c.mesh.tags)


def test_cupola_rises_to_a_point():
    c = articulation.cupola_lantern({"x": 0, "y": 0, "z0": 20, "z1": 24, "radius": 1.5})
    assert c.problems() == []
    assert c.bounds.hi[2] > 24 and c.anchors["apex"][2] == pytest.approx(c.bounds.hi[2])


def test_ridge_cap_runs_between_its_ends():
    c = articulation.ridge_cap({"x0": 0, "y0": 0, "x1": 10, "y1": 0, "z": 15, "width": 0.4, "height": 0.25})
    assert c.problems() == []
    xs = [v[0] for v in c.mesh.verts]
    assert min(xs) == pytest.approx(0.0, abs=1e-6) and max(xs) == pytest.approx(10.0, abs=1e-6)
    with pytest.raises(ValueError):
        articulation.ridge_cap({"x0": 0, "y0": 0, "x1": 0.2, "y1": 0, "z": 1})


def test_lattice_mast_has_legs_and_braces_and_stays_within_its_height():
    c = articulation.lattice_mast({"x": 0, "y": 0, "z0": 0, "z1": 12, "width": 2.0, "brace_every": 3.0})
    assert c.problems() == []
    assert c.bounds.hi[2] == pytest.approx(12.0) and c.bounds.lo[2] == pytest.approx(0.0)
    taller = articulation.lattice_mast({"x": 0, "y": 0, "z0": 0, "z1": 24, "width": 2.0, "brace_every": 3.0})
    assert taller.triangle_count > c.triangle_count                   # more height, more bracing


def test_rose_window_sits_in_its_face_with_a_ring():
    c = articulation.rose_window({"poly": POLY, "facing_deg": 270, "z": 8.0, "radius": 1.2, "ring": 0.2, "projection": 0.15})
    assert c.problems() == []
    assert min(ys(c)) >= -10.0 - 0.15 - 1e-6 and max(ys(c)) <= -10.0 + 1e-6 + 0.15
    assert c.bounds.lo[2] == pytest.approx(8.0 - 1.4, abs=0.05)
    with pytest.raises(ValueError):
        articulation.rose_window({"poly": geom.rect_polygon(0, 0, 2, 40, 0), "facing_deg": 270, "z": 8, "radius": 1.2})


@pytest.mark.parametrize("gen, params", [
    ("pilaster_profile", {"poly": POLY, "facing_deg": 270, "height": 6}),
    ("balustrade_run", {"poly": POLY, "facing_deg": 270, "width": 4}),
    ("dormer_window", {"x": 0, "y": 0, "z0": 1}),
    ("cupola_lantern", {"x": 0, "y": 0, "z0": 0, "z1": 3}),
    ("ridge_cap", {"x0": 0, "y0": 0, "x1": 5, "y1": 5, "z": 3}),
    ("lattice_mast", {"x": 0, "y": 0, "z0": 0, "z1": 9}),
    ("rose_window", {"poly": POLY, "facing_deg": 270, "z": 6, "radius": 1.0}),
])
def test_every_new_slice_is_finite_and_accounted(gen, params):
    c = registry.GENERATORS[gen](params)
    assert c.problems() == []
    assert all(math.isfinite(v) for vert in c.mesh.verts for v in vert)
    assert c.triangle_count == sum(len(f) - 2 for f in c.mesh.faces) > 0


def test_finial_spike_and_ball_rise_from_their_base():
    spike = articulation.finial_pinnacle({"x": 0, "y": 0, "z": 20, "height": 3.0})
    assert spike.problems() == [] and spike.bounds.lo[2] == pytest.approx(20.0)
    assert spike.bounds.hi[2] == pytest.approx(23.0)
    ball = articulation.finial_pinnacle({"x": 0, "y": 0, "z": 20, "height": 3.0, "style": "ball"})
    assert ball.problems() == [] and ball.triangle_count > spike.triangle_count
    assert ball.bounds.hi[2] == pytest.approx(23.0)
