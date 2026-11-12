"""Roof plan traced on the satellite view: crop, checks, and matching the massing parts to it."""
from PIL import Image

from landmark3d import roofplan

MAIN = [(-20, -10), (20, -10), (20, 10), (-20, 10)]
NAVE = {"name": "nave", "polygon": [[-20, -5], [20, -5], [20, 5], [-20, 5]], "roof": "gable", "level": "main"}
AISLE_N = {"name": "north aisle", "polygon": [[-20, 5], [20, 5], [20, 10], [-20, 10]], "roof": "shed", "level": "low"}
AISLE_S = {"name": "south aisle", "polygon": [[-20, -10], [20, -10], [20, -5], [-20, -5]], "roof": "shed"}


def test_crop_has_the_grid_frame_of_the_site():
    sat = Image.new("RGB", (1200, 800), (90, 90, 90))
    img, frame = roofplan.crop(sat, MAIN, (240.0, 160.0))
    assert img.size == (int((40 + 24) * roofplan.CROP_PX_PER_M), int((20 + 24) * roofplan.CROP_PX_PER_M))
    assert frame["x0"] == -32 and frame["y1"] == 22


def test_checks_name_the_problems_of_a_tracing():
    assert roofplan.errors({"volumes": [NAVE, AISLE_N, AISLE_S]}, MAIN) == []
    assert any("cover only" in e for e in roofplan.errors({"volumes": [AISLE_N, AISLE_S]}, MAIN))
    off = dict(AISLE_N, name="neighbour", polygon=[[40, 0], [60, 0], [60, 10], [40, 10]])
    assert any("outside" in e for e in roofplan.errors({"volumes": [NAVE, AISLE_N, AISLE_S, off]}, MAIN))
    assert roofplan.errors({"volumes": [NAVE]}, MAIN)                         # one volume is not a plan


def test_every_traced_volume_needs_a_ground_part_on_its_plan():
    parts = [{"id": "nave", "footprint": {"polygon": NAVE["polygon"]}, "base_z": 0, "height": 20},
             {"id": "aisles", "footprint": {"rect": {"cx": 0, "cy": 7.5, "w": 40, "d": 5}}, "base_z": 0, "height": 8},
             {"id": "spire", "footprint": {"polygon": AISLE_S["polygon"]}, "base_z": 20, "height": 5}]   # not ground
    errs = roofplan.match_errors(parts, [NAVE, AISLE_N, AISLE_S], MAIN)
    assert len(errs) == 1 and "south aisle" in errs[0] and "polygon" in errs[0]
    assert "ROOF PLAN" in roofplan.prompt_section([NAVE])
