"""Volume inventory: every volume the AI lists keeps its own part through the spec phase."""
from landmark3d import inventory


def parts(*ids):
    return [{"id": i} for i in ids]


def test_first_answer_must_list_volumes_each_on_its_own_part():
    assert inventory.first_errors({"parts": parts("nave")})                      # no inventory
    ok = {"parts": parts("nave", "aisle_n", "tower"), "volumes": [
        {"name": "nave", "part": "nave"}, {"name": "north aisle", "part": "aisle_n"}, {"name": "tower", "part": "tower"}]}
    assert inventory.first_errors(ok) == []
    shared = dict(ok, volumes=ok["volumes"] + [{"name": "north chapels", "part": "aisle_n"}])
    assert any("share part" in e for e in inventory.first_errors(shared))     # a chapel is not the aisle
    ghost = dict(ok, volumes=ok["volumes"] + [{"name": "apse", "part": "apse"}])
    assert any('"apse"' in e for e in inventory.first_errors(ghost))


def test_later_answers_may_add_volumes_but_not_drop_one():
    inv = [{"name": "nave", "part": "nave"}, {"name": "apse", "part": "apse"}]
    assert inventory.coverage_errors(parts("nave", "apse", "porch"), inv) == []
    errs = inventory.coverage_errors(parts("nave"), inv)
    assert errs and "apse" in errs[0]
    grown = inventory.merged(inv, {"parts": parts("nave", "apse", "porch"),
                                   "volumes": [{"name": "porch", "part": "porch"}, {"name": "nave", "part": "nave"}]})
    assert [v["name"] for v in grown] == ["nave", "apse", "porch"]


def test_a_new_part_repeating_an_existing_one_is_a_duplicate():
    main = [[-30, -30], [30, -30], [30, 30], [-30, 30]]
    tower = {"id": "tower", "footprint": {"rect": {"cx": 0, "cy": 0, "w": 8, "d": 8}}, "base_z": 0, "height": 40}
    body = {"id": "tower_body", "footprint": {"rect": {"cx": 0.5, "cy": 0, "w": 7, "d": 7}}, "base_z": 0, "height": 34}
    chapel = {"id": "chapel", "footprint": {"rect": {"cx": 12, "cy": 0, "w": 6, "d": 6}}, "base_z": 0, "height": 8}
    errs = inventory.duplicate_errors([tower, body, chapel], {"tower"}, main)
    assert len(errs) == 1 and "tower_body" in errs[0]
    belfry = dict(body, id="belfry", base_z=40, height=6)                       # on top of it: a new volume
    assert inventory.duplicate_errors([tower, belfry], {"tower"}, main) == []
