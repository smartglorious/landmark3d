"""Generative unique-component adapter (plan M8). The gates are the point: nothing is admitted by default."""
import pytest

from landmark3d import generative


def _req(slot="ornament", purpose="unique_component", size=(1.0, 2.0)):
    return generative.request("hall", slot, "crop.png", "mask.png", list(size)) | {"purpose": purpose}


def _result(**over):
    verts = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 4.0]]
    r = {"provider": "acme", "model": "m1", "version": "2", "confidence": 0.9,
         "licence": {"name": "CC-BY-4.0", "terms": "attribution required", "url": "https://example.invalid"},
         "mesh": {"vertices": verts, "faces": [[0, 1, 2], [0, 1, 3]]}}
    r.update(over)
    return r


@pytest.fixture()
def approved(monkeypatch):
    monkeypatch.setitem(generative.APPROVED_PROVIDERS, "acme", {"model": "m1", "version": "2"})


def test_nothing_is_admitted_by_default(approved):
    out = generative.admit(_req(), _result(), opted_in=False)
    assert out["admitted"] is False and any("off" in p for p in out["problems"])


def test_nothing_is_admitted_while_no_provider_is_approved():
    out = generative.admit(_req(), _result(), opted_in=True)
    assert out["admitted"] is False
    assert any("held-out benchmark" in p for p in out["problems"])


def test_a_whole_building_is_out_of_scope(approved):
    out = generative.admit(_req(slot="building", purpose="whole_building"), _result(), opted_in=True)
    assert out["admitted"] is False and any("out of scope" in p for p in out["problems"])


def test_a_slot_that_is_not_unique_is_refused(approved):
    out = generative.admit(_req(slot="wall"), _result(), opted_in=True)
    assert out["admitted"] is False and any("not a unique component" in p for p in out["problems"])


def test_licence_and_confidence_are_required(approved):
    no_lic = generative.admit(_req(), _result(licence={}), opted_in=True)
    low = generative.admit(_req(), _result(confidence=0.3), opted_in=True)
    assert any("licence" in p for p in no_lic["problems"])
    assert any("below" in p for p in low["problems"])


def test_an_unapproved_model_version_is_refused(approved):
    out = generative.admit(_req(), _result(version="3"), opted_in=True)
    assert out["admitted"] is False and any("not the approved model version" in p for p in out["problems"])


def test_too_many_triangles_are_refused_not_decimated(approved):
    faces = [[0, 1, 2]] * (generative.MAX_TRIANGLES + 1)
    out = generative.admit(_req(), _result(mesh={"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 1]], "faces": faces}),
                           opted_in=True)
    assert out["admitted"] is False and any("limit" in p for p in out["problems"])


def test_an_approved_result_is_scaled_to_its_height_and_marked_generated(approved):
    out = generative.admit(_req(size=(1.0, 2.0)), _result(), opted_in=True)
    assert out["admitted"] is True, out["problems"]
    zs = [v[2] for v in out["mesh"]["vertices"]]
    assert min(zs) == pytest.approx(0.0) and max(zs) == pytest.approx(2.0)
    assert out["record"]["provenance"] == "generated"
    assert out["record"]["provider"] == "acme" and out["record"]["version"] == "2"
    assert len(out["record"]["request_sha256"]) == 64


def test_a_result_wider_than_its_slot_is_refused(approved):
    wide = _result(mesh={"vertices": [[0, 0, 0], [10, 0, 0], [0, 10, 1.0]], "faces": [[0, 1, 2]]})
    out = generative.admit(_req(size=(1.0, 2.0)), wide, opted_in=True)
    assert out["admitted"] is False and any("wider" in p for p in out["problems"])
