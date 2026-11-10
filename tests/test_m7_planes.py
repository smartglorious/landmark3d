"""Plane fitting and anchors on a synthetic building with a known answer (plan M7, task 4). No COLMAP, no network."""
import random

import pytest

from landmark3d import photogrammetry as P


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
