import io

import pytest
from PIL import Image


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    import importlib
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    return tmp_path


def _jpg(w=64, h=32, colour=(200, 150, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, "JPEG")
    return buf.getvalue()


def test_add_repeat_and_panel_then_pack(home):
    from landmark3d import textures
    from landmark3d.project import Project
    prj = Project.create("Tex Hall", "X", lat=1.0, lon=2.0)
    a = textures.add(prj, file_bytes=_jpg(), filename="wall.jpg", mode="repeat", material="wall",
                     world_size_m=2.5, tint=[1.0, 0.8, 0.4])
    b = textures.add(prj, file_bytes=_jpg(400, 300), filename="face.png", mode="panel", part="tower",
                     side="south", z0=1.0, z1=9.0, crop=[0, 0, 0.5, 1])
    assert a["id"] == "TX-01" and b["id"] == "TX-02"
    assert (prj.root / a["file"]).exists() and b["size"] == [400, 300]
    repeat, panels, atlas = textures.build_job(prj)
    assert repeat["wall"]["world_size_m"] == 2.5 and repeat["wall"]["tint"] == [1.0, 0.8, 0.4]
    assert len(panels) == 1 and panels[0]["part"] == "tower" and panels[0]["z1"] == 9.0
    u0, v0, u1, v1 = panels[0]["uv"]
    assert 0 <= u0 < u1 <= 1 and 0 <= v0 < v1 <= 1
    with Image.open(atlas) as im:
        assert im.size == (256, 512)  # 200x300 crop -> fits, power-of-two sides, not a fixed 2048 square
    assert textures.remove(prj, "TX-01") and not textures.remove(prj, "TX-99")
    assert [t["id"] for t in textures.load(prj)["textures"]] == ["TX-02"]


def test_rejects_bad_licence_and_inputs(home):
    from landmark3d import textures
    from landmark3d.project import Project
    prj = Project.create("Tex Hall", "X", lat=1.0, lon=2.0)
    with pytest.raises(ValueError):
        textures.add(prj, file_bytes=_jpg(), filename="x.jpg", licence="all_rights_reserved")
    with pytest.raises(ValueError):
        textures.add(prj, file_bytes=_jpg(), filename="x.jpg", mode="panel", side="south")  # no part
    with pytest.raises(ValueError):
        textures.add(prj, file_bytes=b"not an image", filename="x.jpg")
    prj.write_json("input/sources.json", {"sources": [
        {"id": "SRC-C01", "kind": "photo", "path": "input/photos/c.jpg", "licence": "all_rights_reserved",
         "use": "reference_only"}]})
    with pytest.raises(ValueError):
        textures.add(prj, source_id="SRC-C01")


def test_source_photo_texture_for_is_listed(home):
    from landmark3d import textures
    from landmark3d.project import Project
    prj = Project.create("Tex Hall", "X", lat=1.0, lon=2.0)
    prj.path("input", "photos", "p.jpg").write_bytes(_jpg())
    prj.write_json("input/sources.json", {"sources": [
        {"id": "SRC-W01", "kind": "photo", "path": "input/photos/p.jpg", "licence": "cc_by",
         "use": "reference_and_texture", "texture_for": "roof"}]})
    data = textures.load(prj)
    assert data["textures"][0]["material"] == "roof"
    repeat, _, _ = textures.build_job(prj)
    assert "roof" in repeat
    assert textures.remove(prj, "TX-S-SRC-W01")
    assert textures.load(prj)["textures"] == []


def test_texture_api(home):
    from fastapi.testclient import TestClient
    from landmark3d.server import app
    c = TestClient(app)
    pid = c.post("/api/projects", json={"name": "Api Tex", "location": "X", "lat": 1.0, "lon": 2.0}).json()["id"]
    r = c.post(f"/api/projects/{pid}/textures", files={"file": ("w.jpg", _jpg(), "image/jpeg")},
               data={"mode": "repeat", "material": "wall", "world_size_m": "4", "tint": "#f4c666", "licence": "owned"})
    assert r.status_code == 200, r.text
    assert r.json()["tint"] is not None and r.json()["world_size_m"] == 4.0
    r = c.post(f"/api/projects/{pid}/textures", files={"file": ("w.jpg", _jpg(), "image/jpeg")},
               data={"mode": "panel", "part": "main", "side": "south", "crop": "0.5,0,0.2,1"})
    assert r.status_code == 400
    assert c.post(f"/api/projects/{pid}/textures", data={"mode": "repeat"}).status_code == 400
    lst = c.get(f"/api/projects/{pid}/textures").json()["textures"]
    assert len(lst) == 1
    assert c.get(f"/files/{pid}/{lst[0]['file']}").status_code == 200
    assert c.post(f"/api/projects/{pid}/rebuild").status_code == 409  # A2 not approved
    assert c.delete(f"/api/projects/{pid}/textures/{lst[0]['id']}").status_code == 200
    assert c.get(f"/api/projects/{pid}/textures").json()["textures"] == []
