"""Photo textures must match the walls around them and sit cleanly with the generated detail."""
import numpy as np
import pytest
from PIL import Image

from landmark3d import autotex, geom

A, B = (0.0, 0.0), (20.0, 0.0)            # a wall along x; the outside is -y for this direction


def strip(mesh, x, z0, z1, tag="trim"):
    """A vertical strip standing on the wall."""
    y = -0.1
    mesh.f((mesh.v(x, y, z0), mesh.v(x + 1, y, z0), mesh.v(x + 1, y, z1), mesh.v(x, y, z1)), tag)


def zs(mesh):
    return [sorted({mesh.verts[i][2] for i in f}) for f in mesh.faces]


def test_strip_running_past_a_photo_keeps_only_the_part_outside_it():
    m = geom.Mesh()
    strip(m, 5, 0, 12)
    geom.clear_covered_faces(m, [(A, B, 6, 12)])
    assert zs(m) == [[0, 6]]


def test_strip_is_cut_above_and_below_a_photo_band():
    m = geom.Mesh()
    strip(m, 5, 0, 12)
    geom.clear_covered_faces(m, [(A, B, 4, 8)])
    assert zs(m) == [[0, 4], [8, 12]]


def test_detail_on_the_photo_is_dropped_and_detail_away_from_it_stays():
    m = geom.Mesh()
    strip(m, 5, 7, 11)
    strip(m, 5, 0, 5)
    geom.clear_covered_faces(m, [(A, B, 6, 12)])
    assert zs(m) == [[0, 5]]


def test_an_arch_straddling_the_photo_edge_is_removed():
    m = geom.Mesh()
    shape = geom.arch_outline(2.0, 4.0)
    m.f([m.v(5 + x, -0.05, 4.0 + z) for x, z in shape], "opening")
    geom.clear_covered_faces(m, [(A, B, 6, 12)])
    assert m.faces == []


@pytest.mark.parametrize("tag", ["wall", "roof", "d:stone"])
def test_walls_roofs_and_depth_details_are_left_alone(tag):
    m = geom.Mesh()
    strip(m, 5, 0, 12, tag)
    geom.clear_covered_faces(m, [(A, B, 6, 12)])
    assert zs(m) == [[0, 12]]


def test_detail_on_another_wall_is_left_alone():
    m = geom.Mesh()
    m.f((m.v(30, -0.1, 0), m.v(31, -0.1, 0), m.v(31, -0.1, 12), m.v(30, -0.1, 12)), "trim")
    geom.clear_covered_faces(m, [(A, B, 6, 12)])
    assert len(m.faces) == 1


FACE = {"z0": 0.0, "z1": 12.4, "level_height": 6.2}


def test_band_edge_moves_up_to_the_next_floor_line():
    t, b = autotex.snap_band((0.0, 0.79), FACE)                    # bottom edge at 2.6 m, inside the ground floor
    assert t == 0.0 and b == pytest.approx(0.5)


def test_band_already_on_a_floor_line_is_unchanged():
    assert autotex.snap_band((0.0, 0.5), FACE) == pytest.approx((0.0, 0.5))


def test_band_top_moves_down_to_a_floor_line():
    t, b = autotex.snap_band((0.25, 1.0), dict(FACE, z1=18.6))      # top edge at 13.95 m, floors every 6.2 m
    assert t == pytest.approx(1 - 12.4 / 18.6) and b == 1.0


@pytest.mark.parametrize("face", [dict(FACE, level_height=0), {"z0": 0.0, "z1": 12.4}])
def test_band_without_a_floor_height_is_unchanged(face):
    assert autotex.snap_band((0.0, 0.79), face) == (0.0, 0.79)


def test_band_too_small_after_snapping_is_unchanged():
    face = {"z0": 0.0, "z1": 10.0, "level_height": 9.0}
    assert autotex.snap_band((0.0, 0.5), face) == (0.0, 0.5)


def test_crop_follows_the_snapped_band():
    img = Image.new("RGB", (100, 124))
    out, f = autotex.crop_band(img, FACE, autotex.snap_band((0.0, 0.79), FACE))
    assert out.height == 62 and f["z0"] == pytest.approx(6.2) and f["z1"] == pytest.approx(12.4)


def facade_image():
    """A dull wall (bright 60%) with dark windows (40%)."""
    a = np.full((100, 100, 3), (120, 108, 90), dtype=np.uint8)
    a[:40] = (25, 25, 28)
    return Image.fromarray(a)


def test_wall_colour_is_matched_and_windows_stay_dark():
    out = np.asarray(autotex.match_wall_colour(facade_image(), [240, 208, 140]))
    assert out[90, 50].tolist() == pytest.approx([240, 208, 140], abs=2)
    assert out[10, 50].max() < 70


def test_correction_is_bounded():
    dark = Image.new("RGB", (10, 10), (20, 20, 20))
    assert np.asarray(autotex.match_wall_colour(dark, [240, 240, 240])).max() <= 48
    bright = Image.new("RGB", (10, 10), (250, 250, 250))
    assert np.asarray(autotex.match_wall_colour(bright, [10, 10, 10])).min() >= 199


def test_no_material_leaves_the_image_unchanged():
    img = facade_image()
    assert autotex.match_wall_colour(img, None) is img


def test_wall_colour_uses_the_parts_own_material():
    spec = {"materials": {"wall": {"srgb": [1, 1, 1]}, "stone": {"srgb": [9, 9, 9]}},
            "parts": [{"id": "a", "material": {"wall": "stone"}}, {"id": "b"}]}
    assert autotex.wall_colour(spec, "a") == [9, 9, 9]
    assert autotex.wall_colour(spec, "b") == [1, 1, 1]
    assert autotex.wall_colour({"parts": [], "materials": {}}, "x") is None
