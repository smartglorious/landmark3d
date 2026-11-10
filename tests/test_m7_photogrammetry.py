"""Photogrammetry adapter contract (plan M7, tasks 2-3). No COLMAP or Meshroom is run; the tests use a fake folder."""
import json

import pytest

from landmark3d import photogrammetry


def _folder(tmp_path, **over):
    cams = [{"image": f"p{i}.jpg", "R": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "t": [0, 0, 0]}
            for i in range(photogrammetry.MIN_CAMERAS)]
    m = {"adapter": "colmap", "adapter_version": "3.9", "units": "m", "cameras": cams,
         "points": "points.ply", "mesh": "mesh.ply"}
    m.update(over)
    (tmp_path / "points.ply").write_text("ply", encoding="utf-8")
    (tmp_path / "mesh.ply").write_text("ply", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return tmp_path


def test_without_a_tool_the_procedural_workflow_is_reported(monkeypatch):
    monkeypatch.setattr(photogrammetry.shutil, "which", lambda exe: None)
    assert photogrammetry.status()["mode"] == "procedural"
    assert all(not v["available"] for v in photogrammetry.detect().values())


def test_with_a_tool_the_adapter_is_listed_but_the_procedural_build_stays_the_default(monkeypatch):
    monkeypatch.setattr(photogrammetry.shutil, "which", lambda exe: "C:/tools/colmap.exe" if exe == "colmap" else None)
    st = photogrammetry.status()
    assert st["mode"] == "adapter" and st["adapters"] == ["colmap"]
    assert "procedural build is still the default" in st["note"]


def test_a_complete_output_folder_passes_the_contract(tmp_path):
    assert photogrammetry.check_output(_folder(tmp_path)) == []


def test_a_folder_missing_its_parts_is_listed(tmp_path):
    folder = _folder(tmp_path, cameras=[])
    (folder / "mesh.ply").unlink()
    errs = photogrammetry.check_output(folder)
    assert any("at least" in e for e in errs) and any("mesh" in e for e in errs)
    assert photogrammetry.check_output(tmp_path / "nothing") == ["manifest.json is missing"]


def test_units_and_camera_shape_are_checked(tmp_path):
    folder = _folder(tmp_path, units="ft")
    assert any("metres" in e for e in photogrammetry.check_output(folder))


def test_alignment_scales_to_the_known_height_and_centres_on_the_footprint():
    pts = [(0.0, 0.0, 10.0), (10.0, 0.0, 10.0), (10.0, 5.0, 30.0), (0.0, 5.0, 30.0)]    # 20 units tall
    fp = [(100.0, 200.0), (120.0, 200.0), (120.0, 210.0), (100.0, 210.0)]
    tr = photogrammetry.align(pts, fp, known_height=10.0)
    assert tr["scale"] == pytest.approx(0.5)
    assert tr["translate"][0] == pytest.approx(110.0 - 5.0 * 0.5)
    assert tr["translate"][1] == pytest.approx(205.0 - 2.5 * 0.5)


def test_an_implausible_scale_is_refused_not_guessed():
    pts = [(0, 0, 0), (1, 1, 1000.0)]
    with pytest.raises(ValueError, match="outside"):
        photogrammetry.align(pts, [(0, 0), (1, 1)], known_height=10.0)
    with pytest.raises(ValueError):
        photogrammetry.align([(0, 0, 5.0), (1, 1, 5.0)], [(0, 0), (1, 1)], known_height=10.0)
