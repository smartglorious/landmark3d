"""Gravity-aligned alignment (plan M7, task 3) on a synthetic building with a known answer: rotated, rescaled, shifted,
seen by cameras on a ring. No COLMAP, no network."""
import math
import random

import pytest

from landmark3d import photogrammetry as P

FOOT = [(-20.0, -10.0), (20.0, -10.0), (20.0, 10.0), (-20.0, 10.0)]     # 40 x 20 m
H = 17.0                                                                  # walls 14 m + roof 3 m


def _rot(ax, ang):
    c, s = math.cos(ang), math.sin(ang)
    x, y, z = ax
    K = [[0, -z, y], [z, 0, -x], [-y, x, 0]]
    KK = P._matmul(K, K)
    return [[(1.0 if i == j else 0.0) + s * K[i][j] + (1 - c) * KK[i][j] for j in range(3)] for i in range(3)]


def _building_points(n=500, seed=3):
    rnd = random.Random(seed)
    pts = []
    for _ in range(n):
        side = rnd.randrange(5)
        u, v = rnd.random(), rnd.random()
        if side == 0:                                   # roof
            pts.append((-20 + 40 * u, -10 + 20 * v, H))
        else:                                           # walls, four sides
            z = 14 * v
            if side == 1:
                pts.append((-20 + 40 * u, -10.0, z))
            elif side == 2:
                pts.append((-20 + 40 * u, 10.0, z))
            elif side == 3:
                pts.append((-20.0, -10 + 20 * u, z))
            else:
                pts.append((20.0, -10 + 20 * u, z))
    return pts


def _cameras(scene_to_colmap, centres_world, target=(0.0, 0.0, 8.0)):
    """Cameras looking at the target from the given world centres, expressed in the COLMAP frame."""
    s, Q, shift = scene_to_colmap
    cams = []
    for C in centres_world:
        f = [target[i] - C[i] for i in range(3)]
        fn = math.sqrt(sum(x * x for x in f))
        f = [x / fn for x in f]
        r = [f[1] * 0 - f[2] * 0, f[2] * 0 - f[0] * 0, f[0] * 0 - f[1] * 0]       # placeholder, replaced below
        up_w = (0.0, 0.0, 1.0)
        r = [f[1] * up_w[2] - f[2] * up_w[1], f[2] * up_w[0] - f[0] * up_w[2], f[0] * up_w[1] - f[1] * up_w[0]]
        rn = math.sqrt(sum(x * x for x in r))
        r = [x / rn for x in r]
        u = [r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0]]
        R_world = [r, [-x for x in u], f]                                       # world -> camera rows
        R_col = P._matmul(R_world, P._transpose(Q))                              # COLMAP-frame points
        Cc = [s * sum(Q[i][k] * C[k] for k in range(3)) + shift[i] for i in range(3)]
        t = [-sum(R_col[i][k] * Cc[k] for k in range(3)) for i in range(3)]
        cams.append({"image": f"c{len(cams)}.jpg", "R": R_col, "t": t})
    return cams


def _scene(seed=5):
    Q = P._matmul(_rot((0, 0, 1), 0.7), _rot((1, 0, 0), 1.9))                  # the reconstruction's frame is arbitrary
    s = 0.31
    shift = (4.0, -2.0, 9.0)
    world_pts = _building_points(seed=seed)
    colmap_pts = [tuple(s * sum(Q[i][k] * p[k] for k in range(3)) + shift[i] for i in range(3)) for p in world_pts]
    centres = []
    for k in range(12):                                                         # a ring, 20 degrees up
        a = 2 * math.pi * k / 12
        d = 60.0
        centres.append((d * math.cos(a), d * math.sin(a), H * 0.5 + d * math.tan(math.radians(20))))
    cams = _cameras((s, Q, shift), centres)
    return colmap_pts, cams, Q, s


def test_the_vertical_is_found_from_the_camera_ring():
    pts, cams, Q, _ = _scene()
    up = P.up_axis(cams, pts)
    expect = [Q[i][2] for i in range(3)]                                       # where the world z axis went
    assert all(abs(up[i] - expect[i]) < 1e-3 for i in range(3)), (up, expect)


def test_alignment_recovers_the_known_height_and_footprint():
    pts, cams, _, _ = _scene()
    tr = P.align_gravity(pts, cams, FOOT, known_height=H)
    out = P.apply_transform(pts, tr)
    xs, ys, zs = ([p[i] for p in out] for i in range(3))
    assert P._pct(zs, 0.98) - P._pct(zs, 0.02) == pytest.approx(H, rel=0.05)
    assert P._robust_extent(xs) == pytest.approx(40.0, rel=0.05)
    assert P._robust_extent(ys) == pytest.approx(20.0, rel=0.05)
    assert P._pct(zs, 0.02) == pytest.approx(0.0, abs=0.2)     # the base is the 2nd percentile, not the stray minimum
    assert tr["footprint_residual"] < 0.05


def test_a_plan_that_does_not_match_the_footprint_is_refused():
    pts, cams, _, _ = _scene()
    wrong = [(-60.0, -10.0), (60.0, -10.0), (60.0, 10.0), (-60.0, 10.0)]     # a 120 m plan for a 40 m building
    with pytest.raises(ValueError, match="does not match the footprint"):
        P.align_gravity(pts, cams, wrong, known_height=H)


def test_too_few_points_or_cameras_are_refused():
    with pytest.raises(ValueError):
        P.align_gravity([(0, 0, 0)], [], FOOT, known_height=H)
    with pytest.raises(ValueError):
        P.up_axis([{"R": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "t": [0, 0, 0]}], [(0, 0, 0)] * 5)
