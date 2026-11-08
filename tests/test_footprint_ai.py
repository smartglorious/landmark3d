import json

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
    from landmark3d.research import satellite
    monkeypatch.setattr(satellite, "fetch_esri", lambda origin, **k: Image.new("RGB", satellite.OUT_PX, (50, 60, 50)))
    return Project.create("Tower Test", "X", lat=10.0, lon=106.0)


def square(lon, lat, half):
    return [(lon - half, lat - half), (lon + half, lat - half), (lon + half, lat + half), (lon - half, lat + half),
            (lon - half, lat - half)]


def cands():
    # a big mall (podium) around the point and a small tower inside it, plus a neighbour
    return [{"osm_type": "way", "osm_id": 1, "tags": {"building": "retail", "name": "Mall"}, "ring_lonlat": square(106.0, 10.0, 0.0004)},
            {"osm_type": "way", "osm_id": 2, "tags": {"building": "yes"}, "ring_lonlat": square(106.0001, 10.0001, 0.0001)},
            {"osm_type": "way", "osm_id": 3, "tags": {"building": "yes"}, "ring_lonlat": square(106.0009, 10.0, 0.0001)}]


class Fake:
    def __init__(self, reply):
        self.reply = reply
        self.images = None

    def chat(self, system, prompt, images=None, **k):
        from landmark3d.research import ai
        self.images = images
        return ai.AIResult(json.dumps(self.reply), "fake", "m", [])


def test_candidate_map_numbers_nearest_first(prj):
    from landmark3d.research import footprint_ai, satellite
    img, numbered = footprint_ai.candidate_map(Image.new("RGB", satellite.OUT_PX), cands(), (10.0, 106.0))
    assert numbered[1]["osm_id"] in (1, 2) and len(numbered) == 3 and img.size == satellite.OUT_PX


def test_ai_picks_the_tower_inside_the_mall(prj):
    from landmark3d.research import footprint_ai
    from landmark3d.research.footprint_ai import candidate_map
    from landmark3d.research import satellite
    _, numbered = candidate_map(Image.new("RGB", satellite.OUT_PX), cands(), (10.0, 106.0))
    tower_n = next(n for n, c in numbered.items() if c["osm_id"] == 2)
    fake = Fake({"choice": "candidates", "ids": [tower_n], "confidence": "high", "why": "the tower, not the mall"})
    out = footprint_ai.resolve(prj, fake, "Tower", "X", 10.0, 106.0, cands(), [], "a 400 m tower on a mall")
    assert out["main"]["osm_id"] == 2 and out["main"]["match"]["score"] == 1.0 and out["extra"] == []
    assert str(fake.images[0]).endswith("footprint_candidates.jpg")


def test_ai_traces_when_osm_has_nothing(prj, monkeypatch):
    from landmark3d.research import footprint_ai
    monkeypatch.setattr(footprint_ai, "osm_buildings_bbox", lambda *a, **k: [])
    poly = [[-20, -15], [20, -15], [20, 15], [-20, 15]]
    out = footprint_ai.resolve(prj, Fake({"choice": "traced", "polygon": poly, "confidence": "medium"}),
                               "Market", "X", 10.0, 106.0, [], [])
    assert out["main"]["osm_type"] == "ai_traced" and out["main"]["match"]["score"] == 0.7
    lons = [p[0] for p in out["main"]["ring_lonlat"]]
    assert 106.0001 < max(lons) < 106.0003                     # 20 m east of the point


def test_low_confidence_is_returned_low(prj):
    from landmark3d.research import footprint_ai
    out = footprint_ai.resolve(prj, Fake({"choice": "candidates", "ids": [1], "confidence": "low"}),
                               "X", "X", 10.0, 106.0, cands(), [])
    assert out["confidence"] == "low" and out["main"]["match"]["score"] == 0.3
