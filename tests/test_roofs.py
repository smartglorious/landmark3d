"""Roof extensions: overhanging eaves, upturned corners, skirt tiers (tiered halls, pagoda storeys)."""
import pytest

from landmark3d import geom, spec as spec_mod

RECT = geom.rect_polygon(0, 0, 40, 20)


def build(roof, poly=RECT, base=0.0, top=8.0):
    m = geom.Mesh()
    geom.build_part(poly, base, top, roof, m)
    return m


def zs(m):
    return [v[2] for v in m.verts]


def xy_extent(m):
    xs, ys = [v[0] for v in m.verts], [v[1] for v in m.verts]
    return max(xs) - min(xs), max(ys) - min(ys)


@pytest.mark.parametrize("rtype", ["hip", "gable", "pyramid", "flat", "shed"])
def test_overhang_projects_past_walls_and_keeps_wall_height(rtype):
    plain = build({"type": rtype, "height": 3})
    eaves = build({"type": rtype, "height": 3, "overhang": 1.5})
    w0, d0 = xy_extent(plain)
    w1, d1 = xy_extent(eaves)
    assert w1 == pytest.approx(w0 + 3.0, abs=0.05) and d1 == pytest.approx(d0 + 3.0, abs=0.05)
    assert "trim" in eaves.tags                                   # soffit / fascia under the eaves
    if rtype != "shed":                                           # a shed's high side continues its slope up
        assert max(zs(eaves)) == pytest.approx(max(zs(plain)), abs=0.05)   # the ridge stays where it was
    walls = [f for f, t in zip(eaves.faces, eaves.tags) if t == "wall"]
    assert walls and all(abs(abs(eaves.verts[i][0]) - 20) < 1e-6 or abs(abs(eaves.verts[i][1]) - 10) < 1e-6
                         for f in walls for i in f)                # walls stay on the footprint


def test_upturn_lifts_only_convex_corners():
    m = build({"type": "hip", "height": 3, "overhang": 1.0, "upturn": 0.8})
    outer = geom.outward_offset(geom.ensure_ccw(RECT), 1.0)
    roof_v = {i for f, t in zip(m.faces, m.tags) if t == "roof" for i in f}
    corner_z = [m.verts[i][2] for i in roof_v
                if any(abs(m.verts[i][0] - x) < 1e-6 and abs(m.verts[i][1] - y) < 1e-6 for x, y in outer)]
    assert len(corner_z) == 4
    plain = build({"type": "hip", "height": 3, "overhang": 1.0})
    plain_roof = {i for f, t in zip(plain.faces, plain.tags) if t == "roof" for i in f}
    plain_eave = min(plain.verts[i][2] for i in plain_roof if abs(abs(plain.verts[i][0]) - 21) < 1e-6)
    assert all(z == pytest.approx(plain_eave + 0.8, abs=1e-6) for z in corner_z)
    concave = geom.ensure_ccw([(0, 0), (20, 0), (20, 20), (10, 20), (10, 10), (0, 10)])
    lifts = geom.corner_lifts(concave, 1.0)
    assert lifts.count(1.0) == 5 and lifts.count(0.0) == 1        # the re-entrant corner stays down


def test_skirts_stack_tiers_with_clerestory():
    roof = {"type": "hip", "height": 2.5, "skirts": [{"depth": 4, "rise": 1.5, "gap": 1.2}]}
    m = build(roof)
    assert max(zs(m)) == pytest.approx(8 + 1.5 + 1.2 + 2.5, abs=0.05)
    assert geom.roof_total_height(roof) == pytest.approx(5.2)
    inner = geom.inward_offset(geom.ensure_ccw(RECT), 4)[0]
    clerestory = [f for f, t in zip(m.faces, m.tags) if t == "wall"
                  and min(m.verts[i][2] for i in f) == pytest.approx(9.5, abs=1e-6)]
    assert len(clerestory) == 4                                    # one upper wall per side of the inset ring
    for f in clerestory:
        assert all(geom.dist_to_polygon(m.verts[i][:2], inner) < 1e-6 for i in f)


def test_two_skirts_make_a_pagoda_and_bad_skirt_falls_back():
    sq = geom.rect_polygon(0, 0, 10, 10)
    roof = {"type": "pyramid", "height": 2, "upturn": 0.5, "overhang": 0.8,
            "skirts": [{"depth": 1.5, "rise": 1, "gap": 2.5}, {"depth": 1.2, "rise": 0.8, "gap": 2}]}
    m = build(roof, sq, 0, 6)
    assert max(zs(m)) == pytest.approx(6 + 1 + 2.5 + 0.8 + 2 + 2, abs=0.05)
    too_deep = build({"type": "hip", "height": 2, "skirts": [{"depth": 30, "rise": 1, "gap": 1}]}, sq, 0, 6)
    assert max(zs(too_deep)) == pytest.approx(8, abs=0.05)       # inset does not fit: a plain hip roof


def test_schema_accepts_new_roof_fields_and_total_height_counts_skirts():
    part = {"id": "hall", "footprint": "main", "base_z": 0, "height": 7,
            "roof": {"type": "hip", "height": 2.5, "overhang": 1.2, "upturn": 0.4,
                     "skirts": [{"depth": 4, "rise": 1.5, "gap": 1.2}]}}
    massing = {"parts": [part], "materials": {"wall": {"srgb": [200, 190, 170]}}}
    assert spec_mod.validate_massing(massing) == []
    issues = spec_mod.check_structure([part], RECT, {"total_height_m": {"value": 12.2}})
    assert not [i for i in issues if i["code"] == "height_mismatch"]
    bad = dict(part, roof={"type": "hip", "skirts": [{"depth": 4}]})
    assert spec_mod.validate_massing({"parts": [bad], "materials": massing["materials"]})


def test_part_may_stand_on_a_skirt_tier():
    tower = {"id": "tower", "footprint": {"rect": {"cx": 0, "cy": 0, "w": 11, "d": 8}}, "base_z": 0, "height": 9,
             "roof": {"type": "hip", "height": 1.5, "skirts": [{"depth": 1.4, "rise": 0.9, "gap": 4.0}]}}
    clock = {"id": "clock", "footprint": {"rect": {"cx": 0, "cy": 0, "w": 6, "d": 6}}, "base_z": 13.9, "height": 4,
             "roof": {"type": "hip", "height": 2}}
    errs = spec_mod.structure_errors([tower, clock], RECT)
    assert errs == []
    wide = dict(clock, footprint={"rect": {"cx": 0, "cy": 0, "w": 11, "d": 8}})   # wider than the inset tier
    assert any("clock" in e for e in spec_mod.structure_errors([tower, wide], RECT))
