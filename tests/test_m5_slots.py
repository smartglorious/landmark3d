"""M5 closed items: emissive material slot and mip-safe atlas padding with edge bleed. Offline; no Blender."""
import io

import pytest
from PIL import Image

from landmark3d import spec
from landmark3d.detail import materials


def test_emissive_is_a_valid_material_field():
    m = {"srgb": [200, 200, 200], "emissive": [255, 220, 120], "emissive_strength": 2.0}
    assert materials.validate_material("sign", m) == []


@pytest.mark.parametrize("bad", [{"emissive": [300, 0, 0]}, {"emissive": [1, 2]}, {"emissive_strength": -1},
                                 {"emissive_strength": 50}])
def test_bad_emissive_values_are_errors(bad):
    assert materials.validate_material("sign", {"srgb": [1, 2, 3], **bad})


def test_pbr_slots_name_what_each_material_fills():
    plain = materials.pbr_slots({"srgb": [1, 2, 3]})
    assert plain == {"baseColor": "factor", "metallicRoughness": "factor", "normal": None, "occlusion": None,
                     "emissive": None}
    lit = materials.pbr_slots({"srgb": [1, 2, 3], "emissive": [255, 200, 0]}, texture=True, normal=True, ao=True)
    assert lit["emissive"] == "factor" and lit["normal"] == "texture" and lit["occlusion"] == "texture"
    off = materials.pbr_slots({"srgb": [1, 2, 3], "emissive": [255, 200, 0], "emissive_strength": 0})
    assert off["emissive"] is None


def test_the_spec_schema_takes_emissive_and_refuses_bad_values():
    import jsonschema
    part = {"id": "a", "footprint": "main", "base_z": 0, "height": 5, "roof": {"type": "flat"}}
    ok = {"parts": [part], "materials": {"sign": {"srgb": [1, 2, 3], "emissive": [9, 9, 9], "emissive_strength": 2}}}
    jsonschema.validate(ok, spec.MASSING_SCHEMA)
    bad = {"parts": [part], "materials": {"sign": {"srgb": [1, 2, 3], "emissive": [999, 9, 9]}}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, spec.MASSING_SCHEMA)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    return tmp_path


def _jpg(w, h, colour):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, "JPEG", quality=100)
    return buf.getvalue()


def test_atlas_padding_survives_the_mip_levels_and_bleeds_the_edge(home):
    from landmark3d import textures
    from landmark3d.project import Project
    prj = Project.create("Atlas Hall", "X", lat=1.0, lon=2.0)
    textures.add(prj, file_bytes=_jpg(200, 120, (200, 40, 40)), filename="a.png", mode="panel", part="p",
                 side="south", z0=0.0, z1=5.0)
    textures.add(prj, file_bytes=_jpg(180, 100, (40, 40, 200)), filename="b.png", mode="panel", part="p",
                 side="north", z0=0.0, z1=5.0)
    _repeat, panels, atlas = textures.build_job(prj)
    pad = textures.ATLAS_PADDING_PX
    with Image.open(atlas) as im:
        im = im.convert("RGB")
        aw, ah = im.size
        assert materials.padding_ok(pad, aw, ah, materials.mip_levels(aw, ah))
        for p in panels:
            u0, v0, u1, v1 = p["uv"]
            x0, y0 = int(u0 * aw) - 1, int((1 - v1) * ah) - 1        # the cell's top-left pixel (uv has a 1 px inset)
            inside = im.getpixel((x0 + 3, y0 + 3))
            gutter = im.getpixel((max(0, x0 - pad // 2), y0 + 3))    # half way into the left gutter
            assert sum(abs(a - b) for a, b in zip(inside, gutter)) < 40, (inside, gutter)
