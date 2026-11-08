import io

import pytest
from PIL import Image


@pytest.fixture()
def prj(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    from landmark3d.project import Project
    return Project.create("Sat Test", "X", lat=10.78, lon=106.70)


def test_tile_math_and_annotation():
    from landmark3d.research import satellite
    x, y = satellite._tile_xy(0.0, 0.0, 1)
    assert (x, y) == (1.0, 1.0)
    lon, lat = satellite.local_to_lonlat(100.0, 0.0, (10.0, 106.0))
    assert lat == 10.0 and 106.0009 < lon < 106.0010
    img = Image.new("RGB", satellite.OUT_PX, (60, 80, 60))
    out = satellite.annotate(img, [(-10, -10), (10, -10), (10, 10), (-10, 10)], "test attribution")
    W, H = out.size
    assert out.getpixel((W // 2 + 50, H // 2 - 50)) == (255, 230, 0)    # outline corner (10 m = 50 px)


def test_ensure_registers_reference_only_source(prj, monkeypatch):
    from landmark3d.research import satellite
    monkeypatch.setattr(satellite, "fetch_esri", lambda origin, **k: Image.new("RGB", satellite.OUT_PX, (10, 20, 30)))
    e = satellite.ensure(prj, [(-5, -5), (5, -5), (5, 5), (-5, 5)], (10.78, 106.70))
    assert e["use"] == "reference_only" and e["kind"] == "satellite" and "Esri" in e["author"]
    assert (prj.root / e["path"]).exists() and satellite.images_for_ai(prj)[0].name == "satellite_annotated.jpg"
    from landmark3d import settings
    settings.update({"satellite_provider": "none"})
    assert satellite.ensure(prj, [], (0, 0), force=True) is None


def test_upload_and_refresh_api(prj, monkeypatch):
    from fastapi.testclient import TestClient
    from landmark3d.research import satellite
    from landmark3d.server import app
    c = TestClient(app)
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (1, 2, 3)).save(buf, "JPEG")
    r = c.post(f"/api/projects/{prj.id}/satellite", files={"file": ("gmaps.jpg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 200 and r.json()["id"] == "SRC-SAT-U" and r.json()["use"] == "reference_only"
    assert c.post(f"/api/projects/{prj.id}/satellite", files={"file": ("x.jpg", b"nope", "image/jpeg")}).status_code == 400
    assert c.post(f"/api/projects/{prj.id}/satellite/refresh").status_code == 409        # no footprint yet
    assert [p.name for p in satellite.images_for_ai(prj)] == ["satellite_user.jpg"]
