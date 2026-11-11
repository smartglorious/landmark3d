"""Facade grammar extensions: arch shapes, coupled windows (lights), oculi, blank floors, buttresses, and the
generated-texture layout being the build's own layout."""
import pytest

from landmark3d import geom
from landmark3d.smarttex import genfacade as gf

WALL = [(0, 0), (24, 0), (24, 10), (0, 10)]          # the wall facing south (270 deg) is 24 m long


def lay(fac, top=10.0):
    out = geom.facade_layout(WALL, 0.0, top, fac)
    south = [w for w in out["windows"] if w["o"][1] < -0.9]
    return out, south


def test_arch_shapes_have_their_own_outline_and_height():
    for shape in geom.ARCH_SHAPES:
        o = geom.arch_outline(1.2, 3.0, 8, shape)
        assert max(z for _, z in o) == pytest.approx(3.0) and min(x for x, _ in o) == pytest.approx(-0.6)
    pointed = geom.arch_outline(1.2, 3.0, 8, "pointed")
    apex = max(pointed, key=lambda p: p[1])
    assert apex[0] == pytest.approx(0.0, abs=1e-6)                       # a pointed arch meets in the middle
    assert geom.arch_rise(1.2, "segmental") < geom.arch_rise(1.2, "round") < geom.arch_rise(1.2, "pointed")


def test_layout_carries_the_arch_shape_of_each_floor():
    _, south = lay({"opening_type": "arch", "bay_spacing": 4, "level_height": 5, "arch_shape": "pointed",
                    "floors": [{"from": 1, "to": 1, "opening_type": "arch", "arch_shape": "segmental"}]})
    assert {w["shape"] for w in south if w["lv"] == 0} == {"pointed"}
    assert {w["shape"] for w in south if w["lv"] == 1} == {"segmental"}


def test_coupled_windows_split_the_opening_into_lights_without_overlap():
    out, south = lay({"opening_type": "arch", "bay_spacing": 4, "level_height": 5, "opening_width": 1.8, "lights": 2})
    floor0 = sorted((w for w in south if w["lv"] == 0), key=lambda w: w["s"])
    assert len(floor0) == 12 and all(w["lights"] == 2 for w in floor0)            # 6 bays x 2 lights
    a, b = floor0[0], floor0[1]
    assert b["s"] - a["s"] == pytest.approx(a["w"] + max(geom.LIGHT_GAP_MIN, 0.08 * 1.8))
    assert geom.check_facade(out) == []


def test_an_oculus_sits_above_each_window_when_there_is_room():
    out, south = lay({"opening_type": "arch", "bay_spacing": 4, "level_height": 5, "opening_height": 2.4,
                      "sill": 0.6, "oculus": 0.9})
    oc = [w for w in south if w["kind"] == "oculus"]
    win = [w for w in south if w["kind"] == "arch"]
    assert len(oc) == len(win) and all(o["z0"] > w["z0"] + w["h"] for o, w in zip(
        sorted(oc, key=lambda w: (w["lv"], w["s"])), sorted(win, key=lambda w: (w["lv"], w["s"]))))
    _, south = lay({"opening_type": "arch", "bay_spacing": 4, "level_height": 3, "opening_height": 2.4,
                    "oculus": 0.9})
    assert not [w for w in south if w["kind"] == "oculus"]                       # no room: none, and an issue


def test_oculus_rows_and_blank_floors():
    _, south = lay({"opening_type": "arch", "bay_spacing": 4, "level_height": 5,
                    "floors": [{"from": 1, "to": 1, "opening_type": "oculus", "opening_width": 1.0},
                               {"from": 0, "to": 0, "openings": False}]})
    assert {w["kind"] for w in south} == {"oculus"} and all(w["w"] == w["h"] == 1.0 for w in south)


def test_buttresses_are_wider_and_deeper_than_pilasters():
    out, _ = lay({"opening_type": "arch", "bay_spacing": 4, "level_height": 5, "opening_width": 1.2,
                  "pilasters": {"style": "buttress"}})
    assert out["pilasters"] and out["pilasters"][0]["w"] == 0.9
    m = geom.Mesh()
    used = geom.pilasters(WALL, 0.0, 10.0, {"opening_type": "arch", "bay_spacing": 4, "level_height": 5,
                                            "pilasters": {"style": "buttress"}}, m, 10 ** 6)
    ys = [v[1] for v in m.verts]
    assert used and min(ys) == pytest.approx(-0.6)                               # 0.6 m in front of the wall


def test_generated_texture_layout_is_the_build_layout():
    part = {"id": "aisle", "footprint": {"rect": {"cx": 0, "cy": 0, "w": 24, "d": 10, "rot_deg": 0}}, "base_z": 0,
            "height": 10, "facade": {"opening_type": "arch", "bay_spacing": 4, "level_height": 5, "sill": 0.9,
                                     "opening_width": 1.6, "opening_height": 2.6, "arch_shape": "round",
                                     "lights": 2, "oculus": 0.8}}
    el = gf.wall_elements(part, 12.0)
    real, _ = lay(part["facade"])
    real0 = sorted((w for w in real["windows"] if w["o"][1] < -0.9 and w["lv"] == 0 and w["kind"] == "arch"),
                   key=lambda w: w["s"])
    gen0 = sorted((w for w in el["windows"] if w["z0"] < 5 and w["kind"] == "arch"), key=lambda w: w["x"])
    assert [round(w["z0"], 3) for w in gen0] == [round(w["z0"], 3) for w in real0[:len(gen0)]]
    assert gen0[0]["w"] == pytest.approx(real0[0]["w"]) and len(gen0) == 6          # 3 bays x 2 lights
    img, info = gf.layout(part, 3)
    assert "ROUND-arched" in info["words"] and "oculi" in info["words"]
