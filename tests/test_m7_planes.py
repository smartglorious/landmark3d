"""Plane fitting and anchors on a synthetic building with a known answer (plan M7, task 4). No COLMAP, no network."""
import random

import pytest

from landmark3d import photogrammetry as P

H = 17.0


def _box_points(n=1200, noise=0.02, seed=1):
    rnd = random.Random(seed)
    pts = []
    for _ in range(n):
        side = rnd.randrange(5)
        u, v = rnd.random(), rnd.random()
        if side == 0:
            p = (-20 + 40 * u, -10 + 20 * v, 14.0)                 # roof at 14 m
        elif side == 1:
            p = (-20 + 40 * u, -10.0, 14 * v)
        elif side == 2:
            p = (-20 + 40 * u, 10.0, 14 * v)
        elif side == 3:
            p = (-20.0, -10 + 20 * u, 14 * v)
        else:
            p = (20.0, -10 + 20 * u, 14 * v)
        pts.append(tuple(c + rnd.uniform(-noise, noise) for c in p))
    return pts


def test_ransac_finds_the_four_walls_and_the_roof_with_their_normals():
    planes = P.fit_planes(_box_points(), threshold=0.1, max_planes=6, seed=3)
    assert len(planes) == 5
    kinds = sorted(p["kind"] for p in planes)
    assert kinds == ["horizontal", "vertical", "vertical", "vertical", "vertical"]
    roof = next(p for p in planes if p["kind"] == "horizontal")
    assert abs(abs(roof["normal"][2]) - 1.0) < 0.01
    for p in planes:
        if p["kind"] == "vertical":
            assert abs(p["normal"][2]) < 0.01
    assert all(p["support"] > 100 for p in planes)


def test_the_same_seed_gives_the_same_planes():
    a = P.fit_planes(_box_points(), threshold=0.1, seed=7)
    b = P.fit_planes(_box_points(), threshold=0.1, seed=7)
    assert [p["normal"] for p in a] == [p["normal"] for p in b]


def test_anchors_are_the_four_corners_and_the_four_eave_lines():
    planes = P.fit_planes(_box_points(), threshold=0.1, seed=3)
    an = P.anchors(planes)
    assert sum(1 for a in an if a["type"] == "corner") == 4
    assert sum(1 for a in an if a["type"] == "eave") == 4
    for a in an:
        if a["type"] == "eave":
            assert a["p0"][2] == pytest.approx(14.0, abs=0.1) and a["p1"][2] == pytest.approx(14.0, abs=0.1)
        assert a["length"] > 5


def test_too_few_points_gives_no_planes():
    assert P.fit_planes([(0, 0, 0), (1, 0, 0)], threshold=0.1) == []


def test_upright_scales_to_the_known_height_and_rotates_the_vertical_to_z():
    import math
    pts = _box_points(noise=0.0)
    # tilt the building 30 degrees about x, then recover it
    c, s = math.cos(0.5), math.sin(0.5)
    tilted = [(p[0], c * p[1] - s * p[2], s * p[1] + c * p[2]) for p in pts]
    cams = []
    for k in range(10):
        a = 2 * math.pi * k / 10
        C = (60 * math.cos(a), 60 * math.sin(a), 20.0)
        R = [[0, 0, 0] for _ in range(3)]
        cams.append({"R": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "t": [-C[0], -C[1], -C[2]]})
    up, scale = P.upright(tilted, cams, known_height=14.0)
    zs = [p[2] for p in up]
    assert P._robust_extent(zs) == pytest.approx(14.0, rel=0.05)
    assert scale > 0


def test_walls_place_a_rotated_building_on_the_footprint():
    """A 40 x 20 m box, turned 25 degrees in the plane and shifted, must come back on the footprint from its walls."""
    import math as m
    pts = _box_points(noise=0.02)
    a = m.radians(25)
    c, s = m.cos(a), m.sin(a)
    turned = [(c * p[0] - s * p[1] + 30.0, s * p[0] + c * p[1] - 12.0, p[2]) for p in pts]
    cams = []
    for k in range(10):
        ang = 2 * m.pi * k / 10
        C = (60 * m.cos(ang), 60 * m.sin(ang), 20.0)
        f = [-C[0], -C[1], 8.0 - C[2]]
        fn = m.sqrt(sum(x * x for x in f))
        f = [x / fn for x in f]
        r = [f[1] * 1.0, -f[0] * 1.0, 0.0]
        rn = m.sqrt(sum(x * x for x in r))
        r = [x / rn for x in r]
        u = [r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0]]
        R = [r, [-x for x in u], f]
        t = [-sum(R[i][k2] * C[k2] for k2 in range(3)) for i in range(3)]
        cams.append({"R": R, "t": t})
    fp = [(-20.0, -10.0), (20.0, -10.0), (20.0, 10.0), (-20.0, 10.0)]
    tr = P.align_from_walls(turned, cams, fp, known_height=14.0)   # the box is 14 m tall (roof at 14)
    out = P.apply_transform(turned, tr)
    xs = [p[0] for p in out]
    ys = [p[1] for p in out]
    # centred: the midpoint between the 2nd and 98th percentiles is the building centre (the median is not, because the
    # point counts on the walls and roof are not symmetric)
    assert (P._pct(xs, 0.02) + P._pct(xs, 0.98)) / 2 == pytest.approx(0.0, abs=0.5)
    assert (P._pct(ys, 0.02) + P._pct(ys, 0.98)) / 2 == pytest.approx(0.0, abs=0.5)
    assert tr["walls"]["x"] == pytest.approx(40.0, rel=0.05)
    assert tr["walls"]["y"] == pytest.approx(20.0, rel=0.05)
