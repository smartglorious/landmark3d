"""T0 texture metrics (docs/SMART_TEXTURE_PLAN.md): sky share, bay phase error, CIEDE2000 seams."""
import numpy as np
import pytest

from landmark3d.smarttex import metrics as m

WALL = (214, 180, 110)
SKY = (90, 150, 230)


def facade(W=400, H=120, n_bays=5, window_at=0.5, sky_rows=0, wall=WALL):
    rng = np.random.default_rng(1)                 # plaster has fine texture
    a = np.clip(np.array(wall, float) + rng.normal(0, 10, (H, W, 1)), 0, 255).astype(np.uint8)
    bay = W / n_bays
    for k in range(n_bays):
        cx = (k + window_at) * bay
        a[int(0.3 * H):int(0.7 * H), int(cx - 0.18 * bay):int(cx + 0.18 * bay)] = (40, 50, 45)
    if sky_rows:
        a[:sky_rows] = SKY
    return a


def test_sky_share_zero_on_a_clean_wall():
    assert m.sky_share(facade()) == 0.0


def test_sky_share_counts_sky_connected_to_the_top():
    a = facade(sky_rows=30)
    assert m.sky_share(a) == pytest.approx(30 / 120, abs=0.01)


def test_tinted_smooth_sky_counts_but_a_plain_wall_does_not():
    a = facade()
    a[:40] = (225, 190, 150)                        # sky tinted orange by colour correction, perfectly smooth
    assert m.sky_share(a) == pytest.approx(40 / 120, abs=0.04)      # the gradient blur loses the boundary rows
    plain = np.full((120, 300, 3), 200, np.uint8)   # a smooth plain wall touching top and bottom
    assert m.sky_share(plain) == 0.0


def test_blue_patch_inside_the_wall_is_not_sky():
    a = facade()
    a[60:80, 10:40] = SKY                       # a blue sign, not touching the top
    assert m.sky_share(a) == 0.0


def test_bay_phase_centred_windows_score_zero():
    r = m.bay_phase(facade(window_at=0.5), 5)
    assert r["error"] == pytest.approx(0.0, abs=0.02)
    assert r["strength"] > m.MIN_PERIOD_STRENGTH


def test_bay_phase_windows_on_the_bay_line_score_half():
    r = m.bay_phase(facade(window_at=0.0), 5)
    assert r["error"] == pytest.approx(0.5, abs=0.02)


def test_bay_phase_quarter_shift():
    r = m.bay_phase(facade(window_at=0.25), 5)
    assert r["error"] == pytest.approx(0.25, abs=0.03)


def test_bay_phase_wrong_bay_count_is_not_measurable():
    r = m.bay_phase(facade(n_bays=7), 5)
    assert r["error"] is None


def test_bay_phase_flat_texture_is_not_measurable():
    a = np.full((50, 200, 3), 128, np.uint8)
    assert m.bay_phase(a, 4)["error"] is None


def test_ciede2000_reference_pairs():
    # Sharma, Wu and Dalal (2005), test data pairs 1, 7 and 17
    assert m.delta_e2000((50, 2.6772, -79.7751), (50, 0, -82.7485)) == pytest.approx(2.0425, abs=1e-3)
    assert m.delta_e2000((50, 0, 0), (50, -1, 2)) == pytest.approx(2.3669, abs=1e-3)
    assert m.delta_e2000((50, 2.5, 0), (73, 25, -18)) == pytest.approx(27.1492, abs=1e-3)


def test_seam_de_same_wall_is_small_and_different_walls_large():
    a, b = facade(), facade()
    assert m.seam_de(a, b) < 1.0
    c = facade(wall=(150, 120, 70))
    assert m.seam_de(a, c) > 10.0


def test_summary_counts_failures():
    rows = [{"sky_share": 0.0, "bay_phase": {"error": 0.05}}, {"sky_share": 0.3, "bay_phase": {"error": 0.4}}]
    s = m.summarise(rows, [{"de2000": 2.0}, {"de2000": 9.0}])
    assert s["faces_over_sky_limit"] == 1 and s["faces_over_phase_limit"] == 1 and s["seams_over_limit"] == 1
