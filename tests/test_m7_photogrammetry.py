"""Photogrammetry adapter contract (plan M7, tasks 2-3). No COLMAP run here: the tests use hand-written files."""
import json
import math

import pytest

from landmark3d import photogrammetry


def _folder(tmp_path, **over):
    # cameras whose centres are spread along x, so the health check sees a real baseline
    cams = [{"image": f"p{i}.jpg", "R": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "t": [-float(i), 0.0, -5.0]}
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
    monkeypatch.setattr(photogrammetry, "TOOLS_DIR", photogrammetry.TOOLS_DIR / "does-not-exist")
    assert photogrammetry.status()["mode"] == "procedural"
    assert all(not v["available"] for v in photogrammetry.detect().values())


def test_with_a_tool_the_adapter_is_listed_but_the_procedural_build_stays_the_default(monkeypatch):
    monkeypatch.setattr(photogrammetry.shutil, "which", lambda exe: "C:/tools/colmap.exe" if exe == "colmap" else None)
    monkeypatch.setattr(photogrammetry, "TOOLS_DIR", photogrammetry.TOOLS_DIR / "does-not-exist")
    st = photogrammetry.status()
    assert st["mode"] == "adapter" and st["adapters"] == ["colmap"]
    assert "procedural build is still the default" in st["note"]


def test_a_complete_output_folder_passes_the_contract(tmp_path):
    assert photogrammetry.check_output(_folder(tmp_path)) == []


def test_a_folder_missing_its_parts_is_listed(tmp_path):
    folder = _folder(tmp_path, cameras=[], mesh="absent.ply")
    (folder / "points.ply").unlink()
    errs = photogrammetry.check_output(folder)
    assert any("at least" in e for e in errs) and any("points" in e for e in errs)
    assert any("mesh" in e for e in errs)
    assert photogrammetry.check_output(tmp_path / "nothing") == ["manifest.json is missing"]


def test_a_sparse_result_without_a_mesh_is_valid(tmp_path):
    folder = _folder(tmp_path, mesh=None)
    (folder / "mesh.ply").unlink()
    assert photogrammetry.check_output(folder) == []


def test_units_must_be_metres_or_arbitrary(tmp_path):
    assert any("metres or arbitrary" in e for e in photogrammetry.check_output(_folder(tmp_path, units="ft")))


def _colmap_text(tmp_path):
    """A tiny COLMAP text model: 8 images, each turned 45 degrees about z, and a point cloud. Hand-written."""
    d = tmp_path / "model"
    d.mkdir()
    rows = []
    for i in range(8):
        a = math.radians(45 * i)
        qw, qz = math.cos(a / 2), math.sin(a / 2)
        rows.append(f"{i + 1} {qw:.6f} 0 0 {qz:.6f} {i * 0.5} 0 2 {i + 1} img{i:02d}.jpg")
        rows.append("")
    (d / "images.txt").write_text("# header\n" + "\n".join(rows) + "\n", encoding="utf-8")
    pts = [(0, 0, 0), (10, 0, 0), (10, 5, 0), (0, 5, 0), (0, 0, 20), (10, 5, 20), (5, 2, 10), (5, 2, 4)]
    lines = [f"{k + 1} {x} {y} {z} 128 128 128 0.5 1 0" for k, (x, y, z) in enumerate(pts)]
    (d / "points3D.txt").write_text("# header\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return d


def test_a_colmap_text_model_becomes_a_contract_folder(tmp_path):
    model = _colmap_text(tmp_path)
    out = tmp_path / "out"
    m = photogrammetry.export_manifest(model, out)
    assert len(m["cameras"]) == 8 and m["units"] == "arbitrary"
    R = m["cameras"][2]["R"]                                    # 90 degrees about z: x maps to y
    assert R[0][0] == pytest.approx(0.0, abs=1e-6) and R[1][0] == pytest.approx(1.0, abs=1e-6)
    assert photogrammetry.check_output(out) == []
    assert (out / "points.ply").read_text(encoding="utf-8").startswith("ply")


def test_the_exported_points_align_to_a_known_height():
    pts = [(0, 0, 0), (10, 0, 0), (10, 5, 0), (0, 5, 0), (0, 0, 20), (10, 5, 20), (5, 2, 10), (5, 2, 4)]
    tr = photogrammetry.align(pts, [(0, 0), (10, 0), (10, 5), (0, 5)], known_height=40.0)
    assert tr["scale"] == pytest.approx(2.0)                    # 20 arbitrary units tall, 40 m known


def test_alignment_centres_the_model_on_the_footprint():
    pts = [(0.0, 0.0, 10.0), (10.0, 0.0, 10.0), (10.0, 5.0, 30.0), (0.0, 5.0, 30.0)]
    fp = [(100.0, 200.0), (120.0, 200.0), (120.0, 210.0), (100.0, 210.0)]
    tr = photogrammetry.align(pts, fp, known_height=10.0)
    assert tr["scale"] == pytest.approx(0.5)
    assert tr["translate"][0] == pytest.approx(110.0 - 5.0 * 0.5)
    assert tr["translate"][1] == pytest.approx(205.0 - 2.5 * 0.5)


def test_an_implausible_scale_is_refused_not_guessed():
    with pytest.raises(ValueError, match="outside"):
        photogrammetry.align([(0, 0, 0), (1, 1, 1000.0)], [(0, 0), (1, 1)], known_height=10.0)
    with pytest.raises(ValueError):
        photogrammetry.align([(0, 0, 5.0), (1, 1, 5.0)], [(0, 0), (1, 1)], known_height=10.0)


def test_a_collapsed_reconstruction_is_refused(tmp_path):
    """Cameras piled on one spot with one far away (the real failure seen on the Saigon photos): no depth, refused."""
    folder = _folder(tmp_path)
    cams = []
    for i in range(9):
        t = [-0.08, 0.04, 1.1] if i < 8 else [0.6, -0.3, -8.8]
        cams.append({"image": f"c{i}.jpg", "R": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "t": [-x for x in t]})
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    m["cameras"] = cams
    (folder / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    errs = photogrammetry.check_output(folder)
    assert any("collapsed" in e or "degenerate" in e for e in errs), errs


def test_a_spread_out_reconstruction_passes_the_health_check(tmp_path):
    model = _colmap_text(tmp_path)                       # 8 cameras on a 45-degree turn, spread on a line
    m = photogrammetry.export_manifest(model, tmp_path / "ok")
    assert photogrammetry.camera_spread(m["cameras"]) is None
