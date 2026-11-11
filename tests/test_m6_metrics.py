"""M6 metrics: plan IoU, silhouette IoU, edge distance, semantic recall/precision, line match, baseline evaluation.

Synthetic images and polygons only; deterministic; no Blender, no network.
"""
import numpy as np
from PIL import Image, ImageDraw

from landmark3d import quality

SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]


def test_plan_iou_of_identical_shapes_is_one():
    assert quality.plan_iou([SQUARE], [SQUARE]) == 1.0


def test_plan_iou_of_half_overlap_is_a_third():
    shifted = [(5, 0), (15, 0), (15, 10), (5, 10)]
    assert abs(quality.plan_iou([shifted], [SQUARE]) - 1 / 3) < 0.02


def test_plan_iou_uses_the_union_of_the_parts():
    left = [(0, 0), (5, 0), (5, 10), (0, 10)]
    right = [(5, 0), (10, 0), (10, 10), (5, 10)]
    assert quality.plan_iou([left, right], [SQUARE]) > 0.99


def test_metrics_report_plan_iou_from_the_spec():
    spec = {"budgets": {"triangles_max": 1000, "bytes_max": 1000},
            "footprint": {"local": [list(p) for p in SQUARE]},
            "parts": [{"id": "a", "footprint": "main", "base_z": 0, "height": 5}]}
    m = quality.metrics({"triangles": 10, "bytes": 10}, spec)
    assert m["plan_iou"] > 0.99


def _disc(r, size=64, cx=32, cy=32):
    im = Image.new("L", (size, size), 0)
    ImageDraw.Draw(im).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    return im


def test_mask_iou_on_images_and_arrays():
    assert quality.mask_iou(_disc(10), _disc(10)) == 1.0
    assert quality.mask_iou(np.zeros((4, 4)), np.zeros((4, 4))) == 1.0
    assert 0 < quality.mask_iou(_disc(10), _disc(10, cx=40)) < 1


def test_mask_iou_refuses_different_sizes():
    try:
        quality.mask_iou(np.zeros((4, 4)), np.zeros((5, 4)))
    except ValueError:
        return
    raise AssertionError("different sizes must be refused")


def _grid(offset=0, w=200, h=120):
    im = Image.new("L", (w, h), 230)
    d = ImageDraw.Draw(im)
    for x in range(20 + offset, w, 40):
        d.rectangle([x, 20, x + 16, 90], fill=30)
    return im


def test_edge_distance_is_zero_for_the_same_image_and_grows_with_shift():
    a = _grid()
    assert quality.edge_distance(a, a) == 0.0
    small, big = quality.edge_distance(a, _grid(3)), quality.edge_distance(a, _grid(12))
    assert 0 < small < big


def test_edge_distance_of_a_blank_image_is_not_measurable():
    assert quality.edge_distance(_grid(), Image.new("L", (200, 120), 128)) is None


def test_edge_distance_is_deterministic():
    a, b = _grid(), _grid(5)
    assert quality.edge_distance(a, b) == quality.edge_distance(a, b)


def test_semantic_scores_match_one_to_one_within_the_class():
    obs = [{"class": "window", "box": [0, 0, 1, 1]}, {"class": "window", "box": [2, 0, 3, 1]},
           {"class": "door", "box": [5, 0, 6, 2]}]
    built = [{"class": "window", "box": [0, 0, 1, 1]}, {"class": "window", "box": [0.05, 0, 1.05, 1]},
             {"class": "door", "box": [9, 0, 10, 2]}]
    r = quality.semantic_scores(obs, built)
    assert r["matched"] == 1 and r["recall"] == round(1 / 3, 4) and r["precision"] == round(1 / 3, 4)
    assert r["recall_by_class"] == {"door": 0.0, "window": 0.5}


def test_semantic_scores_ignore_input_order():
    obs = [{"class": "w", "box": [0, 0, 1, 1]}, {"class": "w", "box": [1, 0, 2, 1]}]
    built = [{"class": "w", "box": [1, 0, 2, 1]}, {"class": "w", "box": [0, 0, 1, 1]}]
    assert quality.semantic_scores(obs, built)["matched"] == quality.semantic_scores(obs[::-1], built)["matched"] == 2


def test_line_match_uses_the_tolerance():
    assert quality.line_match([0.2, 0.5, 0.8], [0.21, 0.56, 0.8], 0.05) == round(2 / 3, 4)
    assert quality.line_match([], [0.5], 0.05) is None


def test_unmeasured_thresholds_are_not_measurable_never_pass():
    r = quality.evaluate({"plan_iou": 0.97})
    assert r["plan_iou"]["status"] == "pass"
    assert r["silhouette_iou_median"]["status"] == "not_measurable"
    assert all(v["status"] != "pass" for k, v in r.items() if k != "plan_iou")


def test_a_failed_baseline_metric_blocks_the_gate():
    spec = {"budgets": {"triangles_max": 1000, "bytes_max": 1000}}
    m = quality.metrics({"triangles": 10, "bytes": 10}, spec)
    m["plan_iou"] = 0.5
    assert any("plan_iou" in p for p in quality.gate(m, spec))


def test_view_summary_takes_median_and_minimum():
    s = quality.view_summary([0.9, 0.7, 0.8, None], [0.03, 0.05], main_edge=0.02)
    assert s == {"silhouette_iou_median": 0.8, "silhouette_iou_min": 0.7, "edge_distance_median": 0.04,
                 "edge_distance_main": 0.02}


def test_frame_coverage_is_the_mask_share():
    m = np.zeros((10, 10), dtype=bool)
    m[:, :8] = True
    assert quality.frame_coverage(m) == 0.8


def test_facade_coverage_and_draw_calls_come_from_the_build():
    spec = {"budgets": {"triangles_max": 1000, "bytes_max": 1000}}
    b = {"triangles": 10, "bytes": 10, "draw_calls": 7,
         "facade_coverage": {"a": {"faces": 4, "photo_faces": 1}, "b": {"faces": 4, "photo_faces": 3}}}
    m = quality.metrics(b, spec)
    assert m["draw_calls"] == 7 and m["facade_coverage"] == 0.5
