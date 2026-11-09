"""M5: deterministic material, texel density, atlas padding and attribution checks. Offline; no renderer, no photos."""
import pytest

from landmark3d.detail import materials as mat


def test_architectural_surfaces_are_not_metallic():
    assert mat.validate_material("wall", {"srgb": [230, 205, 150], "roughness": 0.8, "metallic": 0.0}) == []
    errs = mat.validate_material("wall", {"srgb": [230, 205, 150], "metallic": 0.9})
    assert any("not metallic" in e for e in errs)


def test_glass_and_curtain_wall_may_be_reflective():
    assert mat.validate_material("curtain_glass", {"srgb": [20, 60, 80], "roughness": 0.05, "metallic": 0.9}) == []


@pytest.mark.parametrize("m, needle", [
    ({"srgb": [300, 0, 0]}, "srgb"),
    ({"srgb": [1, 2, 3], "roughness": 1.5}, "roughness"),
    ({"srgb": [1, 2, 3], "metallic": -0.1}, "metallic"),
    ({"srgb": [1, 2]}, "srgb"),
])
def test_out_of_range_values_are_errors(m, needle):
    errs = mat.validate_material("tile", m)
    assert errs and any(needle in e for e in errs)


def test_validate_materials_collects_all_problems_in_order():
    errs = mat.validate_materials({"wall": {"srgb": [1, 2, 3], "metallic": 0.5}, "roof": {"srgb": [1, 2, 3]}})
    assert len(errs) == 1 and errs[0].startswith("wall")


def test_texel_density_is_pixels_per_metre():
    assert mat.texel_density(512, 4.0) == 128.0
    with pytest.raises(ValueError):
        mat.texel_density(512, 0)


def test_neighbouring_faces_stay_within_two_times_density():
    assert mat.density_ratio_ok([128.0, 200.0, 150.0])
    assert not mat.density_ratio_ok([128.0, 300.0])
    assert mat.density_ratio_ok([128.0])                      # one face has no neighbour to compare with


def test_atlas_padding_must_survive_the_mip_levels():
    assert mat.mip_levels(1024, 512) == 7
    assert mat.padding_ok(8, 512, 512, mat.mip_levels(512, 512))
    assert not mat.padding_ok(2, 512, 512, mat.mip_levels(512, 512))
    assert not mat.padding_ok(8, 0, 512, 3)


def test_each_automatic_texture_maps_to_its_source_and_author():
    sources = {"S1": {"use": "reference_and_texture", "licence": "cc_by", "author": "A. Photographer",
                      "title": "Front", "path": "input/photos/a.jpg"}}
    textures = [{"id": "TX-AF-01", "auto": True, "source_id": "S1"}]
    mapping, errs = mat.attribution(textures, sources)
    assert errs == [] and mapping["TX-AF-01"]["author"] == "A. Photographer" and mapping["TX-AF-01"]["licence"] == "cc_by"


def test_attribution_errors_are_reported_not_dropped():
    sources = {
        "NOAUTH": {"use": "reference_and_texture", "licence": "cc_by"},
        "REFONLY": {"use": "reference_only", "licence": "owned", "author": "x"},
        "NC": {"use": "reference_and_texture", "licence": "cc_by_nc", "author": "y"},
    }
    textures = [{"id": "T1", "auto": True, "source_id": "NOAUTH"}, {"id": "T2", "auto": True, "source_id": "REFONLY"},
                {"id": "T3", "auto": True, "source_id": "NC"}, {"id": "T4", "auto": True, "source_id": "GONE"},
                {"id": "T5", "auto": True}]
    mapping, errs = mat.attribution(textures, sources)
    assert mapping == {}
    assert len(errs) == 5
    assert any("needs an author" in e for e in errs) and any("not allowed as texture" in e for e in errs)
    assert any("cc_by_nc" in e for e in errs) and any("not in the project" in e for e in errs)


def test_non_commercial_projects_may_use_nc_pixels():
    sources = {"NC": {"use": "reference_and_texture", "licence": "cc_by_nc", "author": "y"}}
    mapping, errs = mat.attribution([{"id": "T", "auto": True, "source_id": "NC"}], sources, project_noncommercial=True)
    assert errs == [] and "T" in mapping
