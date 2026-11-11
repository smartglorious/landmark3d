"""M7 task 1: overlap, coverage and viewpoint diversity from solved camera poses. Synthetic camera rings only."""
import math

from landmark3d import capture


def _cam(c, target=(0.0, 0.0, 0.0)):
    f = [target[i] - c[i] for i in range(3)]
    n = math.sqrt(sum(x * x for x in f))
    f = [x / n for x in f]
    up = (0.0, 0.0, 1.0)
    r = [f[1] * up[2] - f[2] * up[1], f[2] * up[0] - f[0] * up[2], f[0] * up[1] - f[1] * up[0]]
    n = math.sqrt(sum(x * x for x in r))
    r = [x / n for x in r]
    d = [f[1] * r[2] - f[2] * r[1], f[2] * r[0] - f[0] * r[2], f[0] * r[1] - f[1] * r[0]]
    R = [r, d, f]
    t = [-sum(R[i][k] * c[k] for k in range(3)) for i in range(3)]
    return {"image": "x.jpg", "R": R, "t": t}


def ring(n, start=0.0, span=360.0, radius=50.0, heights=(2.0,), radii=None):
    cams = []
    for k in range(n):
        a = math.radians(start + span * k / n)
        rr = (radii[k % len(radii)] if radii else radius)
        cams.append(_cam((rr * math.cos(a), rr * math.sin(a), heights[k % len(heights)])))
    return cams


def test_a_full_ring_at_two_heights_passes():
    r = capture.assess_poses(ring(32, heights=(2.0, 12.0)))
    assert r["status"] == "pass", r
    assert r["checks"]["coverage"]["value"] == 1.0


def test_one_side_only_fails_coverage_and_names_the_gap():
    r = capture.assess_poses(ring(10, span=90.0, heights=(2.0, 12.0)))
    assert r["status"] == "fail" and not r["checks"]["coverage"]["ok"]
    assert r["checks"]["coverage"]["largest_gap_deg"] > 250


def test_sparse_steps_fail_overlap():
    r = capture.assess_poses(ring(6, heights=(2.0, 12.0)))
    assert not r["checks"]["overlap"]["ok"]


def test_same_height_and_distance_fails_diversity():
    r = capture.assess_poses(ring(32, heights=(1.6,)))
    assert not r["checks"]["viewpoint_diversity"]["ok"]


def test_distance_variety_also_counts_as_diversity():
    r = capture.assess_poses(ring(32, heights=(1.6,), radii=(40.0, 70.0)))
    assert r["checks"]["viewpoint_diversity"]["ok"]


def test_too_few_cameras_is_not_measurable():
    assert capture.assess_poses(ring(2))["status"] == "not_measurable"


def test_cameras_that_are_not_level_are_not_measurable():
    cams = ring(16)
    for c in cams[::2]:                     # turn every other camera upside down
        c["R"] = [[-v for v in c["R"][0]], [-v for v in c["R"][1]], c["R"][2]]
    assert capture.assess_poses(cams)["status"] == "not_measurable"


def test_wall_placement_names_a_folded_reconstruction():
    from landmark3d import photogrammetry as P
    # cameras on a quarter circle only, as when four alike facades were registered as one
    cams = ring(16, span=90.0, heights=(2.0, 10.0))
    pts = [(x * 0.5, -10.0, z * 0.5) for x in range(-40, 40) for z in range(0, 28)]
    fp = [(-20.0, -10.0), (20.0, -10.0), (20.0, 10.0), (-20.0, 10.0)]
    try:
        P.align_from_walls(pts, cams, fp, known_height=14.0)
    except ValueError as exc:
        assert "folded" in str(exc) and "directions" in str(exc)
        return
    raise AssertionError("a one-sided camera set must be refused with the reason")
