"""M2 exit-gate items closed after the first run: arch tessellation by size and LOD, and unknown materials blocking.

Offline only; synthetic fixtures.
"""
import math

import pytest

from landmark3d import pipeline
from landmark3d.detail import openings


def arch(w, h=None):
    h = h if h is not None else w * 1.6
    return {"p": (0.0, 0.0), "t": (1.0, 0.0), "o": (0.0, -1.0), "w": w, "h": h, "kind": "arch", "z0": 3.0,
            "run": 0, "s": 0.0, "lv": 0, "frame_half": w / 2 + 0.25}


@pytest.mark.parametrize("lod", [0, 1, 2])
def test_wider_arches_get_more_segments(lod):
    counts = [openings.arch_segments(w, lod) for w in (0.6, 1.2, 2.4, 4.8)]
    assert counts == sorted(counts) and counts[0] < counts[-1]


@pytest.mark.parametrize("w", [0.6, 1.6, 3.0, 6.0])
def test_higher_lod_never_has_fewer_segments(w):
    s = [openings.arch_segments(w, lod) for lod in (0, 1, 2)]
    assert s[0] <= s[1] <= s[2]


@pytest.mark.parametrize("w", [0.8, 1.6, 3.0])
@pytest.mark.parametrize("lod", [0, 1, 2])
def test_the_chord_stays_under_the_lod_limit_unless_capped(w, lod):
    n = openings.arch_segments(w, lod)
    chord = 2 * (w / 2) * math.sin(math.pi / (2 * n))
    assert chord <= openings.ARCH_CHORD_M[lod] + 1e-9 or n == openings.ARCH_SEGMENTS_MAX


def test_a_small_arch_costs_fewer_triangles_than_a_wide_one():
    small = openings.assemble(arch(0.8), 2).triangle_count
    wide = openings.assemble(arch(4.0), 2).triangle_count
    assert small < wide


def test_segment_count_is_bounded():
    assert openings.arch_segments(0.01, 0) == openings.ARCH_SEGMENTS_MIN
    assert openings.arch_segments(100.0, 2) == openings.ARCH_SEGMENTS_MAX


def _spec(part_material):
    return {"materials": {"stone": {"srgb": [200, 190, 170]}},
            "parts": [{"id": "a", "material": part_material, "features": []}]}


def test_an_unknown_material_is_a_blocking_issue():
    issues = pipeline.unknown_material_issues(_spec({"wall": "stone", "roof": "slate"}))
    assert [i["code"] for i in issues] == ["unknown_material"]
    assert issues[0]["severity"] == "error" and "slate" in issues[0]["message"]


def test_defined_and_default_materials_pass():
    assert pipeline.unknown_material_issues(_spec({"wall": "stone", "roof": "roof"})) == []


def test_an_unknown_feature_material_is_caught_too():
    sp = _spec({"wall": "stone"})
    sp["parts"][0]["features"] = [{"type": "spire", "material": "bronze"}]
    assert [i["code"] for i in pipeline.unknown_material_issues(sp)] == ["unknown_material"]
