import math

from landmark3d import geom


SQUARE = [(0, 0), (20, 0), (20, 12), (0, 12)]
L_SHAPE = [(0, 0), (30, 0), (30, 10), (12, 10), (12, 25), (0, 25)]
CHAMFER = [(0, 2), (2, 0), (40, 0), (42, 2), (42, 28), (40, 30), (2, 30), (0, 28)]


def _roof_z(mesh):
    return [mesh.verts[i][2] for f, t in zip(mesh.faces, mesh.tags) if t == "roof" for i in f]


def test_ensure_ccw_and_area():
    cw = SQUARE[::-1]
    assert geom.area2(geom.ensure_ccw(cw)) > 0
    assert math.isclose(geom.area(SQUARE), 240.0)


def test_inward_offset_collapses_chamfers():
    inner, per_edge = geom.inward_offset(CHAMFER, 5.0)
    assert inner is not None and len(per_edge) == len(CHAMFER)
    assert geom.area(inner) < geom.area(CHAMFER)


def test_inradius():
    assert 5.5 < geom.inradius_estimate(SQUARE) < 6.01


def test_roof_types_build_and_heights():
    for rtype in ("flat", "gable", "hip", "pyramid", "dome", "shed"):
        for poly in (SQUARE, CHAMFER, L_SHAPE):
            m = geom.Mesh()
            geom.build_part(poly, 0.0, 10.0, {"type": rtype, "height": 4.0, "parapet": 0.8}, m)
            assert m.faces, (rtype, poly)
            zs = _roof_z(m)
            assert min(zs) >= 10.0 - 1e-6, rtype
            assert max(zs) <= 14.0 + 1e-6, rtype


U_SHAPE = [(0, 0), (40, 0), (40, 30), (28, 30), (28, 10), (12, 10), (12, 30), (0, 30)]


def test_roofs_stay_inside_concave_footprints():
    for poly in (L_SHAPE, U_SHAPE, CHAMFER):
        for rtype in ("hip", "gable", "shed", "pyramid", "flat"):
            m = geom.Mesh()
            geom.build_part(poly, 0.0, 8.0, {"type": rtype, "height": 3.0, "parapet": 0.8}, m)
            if rtype == "pyramid":
                continue                     # apex at the centroid may fall outside a concave plan by design
            for x, y, _ in m.verts:
                assert geom.point_in_polygon((x, y), geom.ensure_ccw(poly), 1e-3), (rtype, poly, (x, y))


def test_hip_reaches_height_on_rectangle():
    m = geom.Mesh()
    geom.build_part(SQUARE, 0.0, 8.0, {"type": "hip", "height": 3.0}, m)
    assert max(_roof_z(m)) > 10.5


def test_gable_walls_follow_roof():
    m = geom.Mesh()
    geom.build_part(SQUARE, 0.0, 8.0, {"type": "gable", "height": 3.0}, m)
    wall_top = max(m.verts[i][2] for f, t in zip(m.faces, m.tags) if t == "wall" for i in f)
    assert math.isclose(wall_top, 11.0, abs_tol=1e-6)


def test_openings_respect_budget():
    m = geom.Mesh()
    made = geom.facade_openings(SQUARE, 0.0, 12.0, {"bay_spacing": 3.0, "opening_type": "arch"}, m, budget_left=20)
    assert made > 0 and m.triangles() <= 20 + 8
