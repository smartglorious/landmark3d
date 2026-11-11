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


def _grid_mesh(n, height=2.0):
    """A wavy n x n sheet standing up in x-z, 2 n^2 triangles."""
    import math
    verts = [[x / n, 0.05 * math.sin(7 * x / n + 3 * z / n), height * z / n] for z in range(n + 1) for x in range(n + 1)]
    faces = []
    for z in range(n):
        for x in range(n):
            a = z * (n + 1) + x
            faces += [[a, a + 1, a + n + 2], [a, a + n + 2, a + n + 1]]
    return {"vertices": verts, "faces": faces}


def test_too_many_triangles_are_reduced_to_the_limit_and_recorded(approved):
    mesh = _grid_mesh(40)                                   # 3200 triangles
    out = generative.admit(_req(size=(1.0, 2.0)), _result(mesh=mesh), opted_in=True)
    assert out["admitted"] is True, out["problems"]
    assert len(out["mesh"]["faces"]) <= generative.MAX_TRIANGLES
    assert out["record"]["reduced_from"] == 3200 and out["record"]["triangles_out"] <= generative.MAX_TRIANGLES


def test_a_mesh_that_cannot_be_reduced_without_losing_its_shape_is_refused(approved, monkeypatch):
    monkeypatch.setattr(generative, "MAX_TRIANGLES", 4)
    out = generative.admit(_req(size=(1.0, 2.0)), _result(mesh=_grid_mesh(40)), opted_in=True)
    assert out["admitted"] is False and any("cannot be reduced" in p for p in out["problems"])


def test_cleaning_merges_duplicates_and_drops_bad_faces():
    verts = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 0], [5, 5, 5]]
    faces = [[0, 1, 2], [3, 1, 2], [0, 0, 1], [0, 1, 9], [0, 1, 2, 4]]
    out = generative.clean(verts, faces)
    assert out["removed"]["duplicate_vertices"] == 1
    assert len(out["faces"]) == 2                           # the triangle and the second fan triangle of the quad
    assert len(out["vertices"]) == 4


def test_anchors_and_collision_and_budget_on_placement(approved):
    adm = generative.admit(_req(size=(1.0, 2.0)), _result(), opted_in=True)
    a = adm["anchors"]
    assert a["base"][2] == pytest.approx(0.0) and a["top"][2] == pytest.approx(2.0)
    part = [[-10, -10, 0], [10, 10, 20]]
    ok = generative.place(adm, [0, 0, 5], part, [], 100)
    assert ok["ok"], ok["problems"]
    outside = generative.place(adm, [0, 0, 19], part, [], 100)
    assert any("outside" in p for p in outside["problems"])
    hit = generative.place(adm, [0, 0, 5], part, [ok["box"]], 100)
    assert any("overlaps" in p for p in hit["problems"])
    poor = generative.place(adm, [0, 0, 5], part, [], 1)
    assert any("allowance" in p for p in poor["problems"])


def test_generated_components_are_review_items_not_observations():
    spec = {"parts": [{"id": "hall", "generated_components": [
        {"slot": "statue", "record": {"provider": "acme", "model": "m1", "version": "2", "provenance": "generated"}}]}]}
    items = generative.review_items(spec)
    assert len(items) == 1 and items[0]["code"] == "generated_component" and "not observed" in items[0]["message"]
    from landmark3d import pipeline
    assert any(i["code"] == "generated_component" for i in pipeline.review_issues({"materials": {}, **spec}))


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
