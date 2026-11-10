"""M4 remaining planned slices: stepped cornice, column row, entrance portal, curtain-wall bay. Pure geometry, no AI."""
import copy

import pytest

from landmark3d.detail import articulation, registry

POLY = [(-10.0, -5.0), (10.0, -5.0), (10.0, 5.0), (-10.0, 5.0)]


def test_all_four_are_registered_and_the_library_declares_them_implemented():
    lib = registry.load_library()
    assert registry.validate_registry(lib) == []
    for cid, gen in [("stepped_cornice", "stepped_cornice"), ("colonnade_recessed", "column_row"),
                     ("entrance_portal", "entrance_portal"), ("curtain_wall_bay", "curtain_wall_bay")]:
        c = registry.component(cid, lib)
        assert c["status"] == "implemented"
        assert [l["generator"] for l in c["lods"]] == [gen]
        assert gen in registry.GENERATORS


def test_stepped_cornice_projects_most_at_the_bottom_and_stacks_upward():
    comp = articulation.stepped_cornice({"poly": POLY, "base_z": 0.0, "z": 12.0, "projection": 0.6, "tiers": 3,
                                         "tier_height": 0.3})
    assert comp.triangle_count > 0
    b_min, b_max = comp.bounds.lo, comp.bounds.hi
    assert b_min[2] == pytest.approx(12.0)
    assert b_max[2] == pytest.approx(12.0 + 3 * 0.3)
    assert b_max[0] - b_min[0] == pytest.approx(20.0 + 2 * 0.6, abs=1e-6)     # the lowest tier projects the full 0.6 m


def test_stepped_cornice_refuses_out_of_range_inputs():
    for bad in ({"tiers": 0}, {"tiers": 5}, {"projection": 2.0}, {"tier_height": 0.05}):
        with pytest.raises(ValueError):
            articulation.stepped_cornice({"poly": POLY, "z": 10.0, **bad})


def test_column_row_makes_one_shaft_per_column_and_refuses_a_face_too_short():
    comp = articulation.column_row({"poly": POLY, "facing_deg": 270, "count": 4, "column_diameter": 0.6,
                                    "spacing": 2.5, "height": 6.0, "z": 0.0})
    assert comp.triangle_count >= 4 * 8 * 2                       # 4 columns, 8 sides, two triangles per side at least
    assert comp.bounds.hi[2] - comp.bounds.lo[2] == pytest.approx(6.0)
    with pytest.raises(ValueError):
        articulation.column_row({"poly": POLY, "facing_deg": 270, "count": 9, "spacing": 3.0})


def test_entrance_portal_has_a_recessed_opening_slot_and_a_surround():
    comp = articulation.entrance_portal({"poly": POLY, "facing_deg": 270, "width": 4.0, "height": 5.0,
                                         "frame": 0.4, "projection": 0.3, "recess": 0.5, "z": 0.0})
    assert "opening" in comp.material_slots and "trim" in comp.material_slots
    assert comp.bounds.hi[2] == pytest.approx(5.4)                # the head sits one frame above the opening
    with pytest.raises(ValueError):
        articulation.entrance_portal({"poly": POLY, "facing_deg": 270, "width": 19.5, "frame": 0.4})   # 20.3 m > 19.8 m


def test_curtain_wall_bay_reports_glass_as_the_opening_slot():
    comp = articulation.curtain_wall_bay({"poly": POLY, "facing_deg": 270, "bay": 1.5, "height": 3.6, "z": 0.0})
    assert comp.material_slots == {"trim", "opening"}
    assert comp.bounds.hi[2] - comp.bounds.lo[2] == pytest.approx(3.6)
    assert comp.triangle_count > 0


def test_planned_lods_are_kept_for_what_is_still_missing():
    lib = copy.deepcopy(registry.load_library())
    assert registry.component("colonnade_recessed", lib)["planned_lods"] == ["loggia_recess"]
