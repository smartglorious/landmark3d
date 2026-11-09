"""KTX2 embedding (plan M5, step 6). Uses a fake encoder, so the tests need neither toktx nor the network."""
import io
import json
import struct
from pathlib import Path

import pytest
from PIL import Image

from landmark3d.engine import ktx2


def _png(color):
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buf, "PNG")
    return buf.getvalue()


def _glb(images):
    """A GLB with one texture per image; the first texture is also the normal map of a material."""
    binc = b""
    views, imgs, texs = [], [], []
    for i, data in enumerate(images):
        while len(binc) % 4:
            binc += b"\x00"
        views.append({"buffer": 0, "byteOffset": len(binc), "byteLength": len(data)})
        binc += data
        imgs.append({"bufferView": i, "mimeType": "image/png"})
        texs.append({"source": i})
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": []}], "nodes": [],
           "buffers": [{"byteLength": len(binc)}], "bufferViews": views, "images": imgs, "textures": texs,
           "materials": [{"normalTexture": {"index": 0}, "pbrMetallicRoughness": {"baseColorTexture": {"index": 1}}}]}
    return ktx2.write_glb(doc, binc)


def _fake_encode(data, mime, normal):
    return b"KTX2" + (b"N" if normal else b"C") + data[:8]


def test_each_texture_gets_a_ktx2_copy_and_keeps_the_fallback():
    glb = _glb([_png((255, 0, 0)), _png((0, 255, 0))])
    out, report = ktx2.embed(glb, _fake_encode)
    doc, binc = ktx2.read_glb(out)
    assert report["encoded"] == 2 and report["skipped"] == []
    assert len(doc["images"]) == 4                                   # two fallbacks + two KTX2
    assert [im["mimeType"] for im in doc["images"][2:]] == ["image/ktx2", "image/ktx2"]
    assert doc["textures"][0]["source"] == 0                         # the fallback stays the texture's source
    assert doc["textures"][0]["extensions"]["KHR_texture_basisu"]["source"] == 2
    assert doc["textures"][1]["extensions"]["KHR_texture_basisu"]["source"] == 3
    assert "KHR_texture_basisu" in doc["extensionsUsed"]
    assert "KHR_texture_basisu" not in doc.get("extensionsRequired", [])


def test_normal_maps_are_encoded_as_normal_and_colour_as_colour():
    out, _ = ktx2.embed(_glb([_png((1, 2, 3)), _png((4, 5, 6))]), _fake_encode)
    doc, binc = ktx2.read_glb(out)
    kinds = []
    for im in doc["images"][2:]:
        bv = doc["bufferViews"][im["bufferView"]]
        kinds.append(binc[bv["byteOffset"]:bv["byteOffset"] + bv["byteLength"]][4:5])
    assert kinds == [b"N", b"C"]


def test_the_binary_chunk_stays_consistent():
    out, _ = ktx2.embed(_glb([_png((9, 9, 9))]), _fake_encode)
    doc, binc = ktx2.read_glb(out)
    assert doc["buffers"][0]["byteLength"] <= len(binc) < doc["buffers"][0]["byteLength"] + 4   # chunk may be padded
    for bv in doc["bufferViews"]:
        assert bv["byteOffset"] + bv["byteLength"] <= len(binc)
        assert bv["byteOffset"] % 4 == 0
    total = struct.unpack_from("<I", out, 8)[0]
    assert total == len(out)


def test_a_failed_encode_keeps_that_image_as_png_only():
    def flaky(data, mime, normal):
        if normal:
            raise RuntimeError("toktx failed (1)")
        return _fake_encode(data, mime, normal)
    out, report = ktx2.embed(_glb([_png((1, 1, 1)), _png((2, 2, 2))]), flaky)
    doc, _ = ktx2.read_glb(out)
    assert report["encoded"] == 1
    assert report["skipped"][0]["image"] == 0 and "toktx" in report["skipped"][0]["reason"]
    assert "extensions" not in doc["textures"][0]                    # the normal map keeps its PNG only
    assert doc["textures"][1]["extensions"]["KHR_texture_basisu"]["source"] == 2   # only one KTX2 image was added


def test_no_images_leaves_the_file_untouched():
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "buffers": [{"byteLength": 0}]}
    glb = ktx2.write_glb(doc, b"")
    out, report = ktx2.embed(glb, _fake_encode)
    assert out is glb and report["reason"] == "no images"


def test_not_a_glb_is_refused():
    with pytest.raises(ValueError):
        ktx2.read_glb(b"not a glb at all")


def test_embed_file_rewrites_the_file_in_place(tmp_path, monkeypatch):
    path = tmp_path / "model.glb"
    path.write_bytes(_glb([_png((7, 7, 7))]))
    monkeypatch.setattr(ktx2, "toktx_encoder", lambda exe: _fake_encode)
    info = ktx2.embed_file(path, "toktx")
    assert info["container"] == "ktx2" and info["encoded"] == 1
    assert info["bytes_after"] == path.stat().st_size
    doc, _ = ktx2.read_glb(path.read_bytes())
    assert doc["textures"][0]["extensions"]["KHR_texture_basisu"]["source"] == 1


def test_embed_file_reports_a_file_it_cannot_read(tmp_path, monkeypatch):
    path = tmp_path / "bad.glb"
    path.write_bytes(b"garbage")
    monkeypatch.setattr(ktx2, "toktx_encoder", lambda exe: _fake_encode)
    info = ktx2.embed_file(path, "toktx")
    assert info["container"] == "fallback" and "could not be read" in info["reason"]
    assert path.read_bytes() == b"garbage"


def test_a_ktx2_build_embeds_the_copies_and_records_them(tmp_path, monkeypatch):
    """The build job asks for KTX2 -> runner embeds the copies after Blender and records the report in the result."""
    import importlib
    import subprocess
    from landmark3d import doctor, settings, textures
    from landmark3d.engine import runner
    from landmark3d.project import Project
    monkeypatch.setenv("LANDMARK3D_HOME", str(tmp_path / "home"))
    importlib.reload(settings)
    settings.update({"projects_dir": str(tmp_path / "projects")})
    prj = Project.create("Ktx Build", "X")
    prj.write_json("spec/landmark_spec.json", {"parts": [], "materials": {}, "footprint": {"local": [[0, 0], [1, 0], [1, 1]]},
                                               "budgets": {"triangles_max": 1000, "triangles_target": 800,
                                                           "bytes_max": 10 ** 6, "bytes_target": 9 * 10 ** 5,
                                                           "texture_max_px": 512}})
    prj.write_json("input/sources.json", {"sources": []})
    monkeypatch.setattr(textures, "build_job", lambda *a: ({}, [], None))
    monkeypatch.setattr(doctor, "find_blender", lambda: "blender")
    monkeypatch.setattr(runner, "toktx_path", lambda: "toktx")
    monkeypatch.setattr(runner, "texture_container", lambda: "ktx2")
    monkeypatch.setattr(ktx2, "toktx_encoder", lambda exe: _fake_encode)

    def fake_blender(cmd, **k):
        job = json.loads(Path(cmd[-1]).read_text(encoding="utf-8"))
        Path(job["out"]["glb"]).parent.mkdir(parents=True, exist_ok=True)
        Path(job["out"]["glb"]).write_bytes(_glb([_png((5, 5, 5))]))
        Path(job["out"]["result"]).write_text(json.dumps({"ok": True, "errors": [], "warnings": [], "bytes": 1}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(runner.subprocess, "run", fake_blender)
    res = runner.build(prj)
    assert res["ktx2"]["container"] == "ktx2" and res["ktx2"]["encoded"] == 1
    assert res["bytes"] == (prj.root / "output" / "model.glb").stat().st_size
    doc, _ = ktx2.read_glb((prj.root / "output" / "model.glb").read_bytes())
    assert "KHR_texture_basisu" in doc["extensionsUsed"]
    saved = json.loads((prj.root / "reports" / "build_result.json").read_text(encoding="utf-8"))
    assert saved["ktx2"]["container"] == "ktx2"
