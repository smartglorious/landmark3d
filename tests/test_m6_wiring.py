"""M6 wiring: a finished build writes the deterministic quality report; with the detail engine on, its gate blocks the
build approval. Offline: a temporary project, no Blender, no AI."""
import importlib

import pytest

from landmark3d import pipeline
from landmark3d.project import Project

SPEC = {"budgets": {"triangles_max": 1000, "bytes_max": 100000, "materials_max": 6},
        "footprint": {"local": [[0, 0], [10, 0], [10, 10], [0, 10]]},
        "materials": {}, "parts": [{"id": "a", "footprint": "main", "base_z": 0, "height": 6}]}


@pytest.fixture()
def prj(tmp_path, monkeypatch):
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    from landmark3d import settings
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    p = Project.create("M6 Test", "Nowhere", lat=10.0, lon=106.0)
    p.write_json("spec/landmark_spec.json", SPEC)
    return p


def test_the_quality_report_is_written_with_metrics_gate_and_baseline(prj):
    rep = pipeline.write_quality(prj, {"triangles": 100, "bytes": 1000, "materials": 2, "draw_calls": 3})
    saved = prj.read_json("reports/quality.json")
    assert saved["digest"] == rep["digest"] and saved["gate"] == []
    assert saved["baseline"]["plan_iou"]["status"] == "pass"
    assert saved["baseline"]["silhouette_iou_median"]["status"] == "not_measurable"


def test_the_gate_blocks_the_build_approval_only_with_the_engine_on(prj):
    from landmark3d import settings
    pipeline.write_quality(prj, {"triangles": 5000, "bytes": 1000, "materials": 2})      # over the hard budget
    settings.update({"detail_engine_v2": False})
    assert pipeline.quality_blocking_issues(prj) == []
    settings.update({"detail_engine_v2": True})
    issues = pipeline.quality_blocking_issues(prj)
    assert issues and issues[0]["code"] == "quality_gate" and "triangles" in issues[0]["message"]
