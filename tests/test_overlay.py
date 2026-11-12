"""Model-over-photo overlay used by the photo-review rounds."""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from landmark3d import overlay


def render_img():
    im = Image.new("RGB", (400, 300), (150, 160, 175))                 # sky
    d = ImageDraw.Draw(im)
    d.rectangle((0, 250, 400, 300), fill=(120, 120, 125))              # ground plane
    d.rectangle((150, 100, 250, 250), fill=(170, 80, 60))              # the model, 100 x 150 px
    d.point((10, 10), fill=(0, 0, 0))                                   # a speck of noise
    return im


def test_render_mask_finds_the_model_and_drops_specks():
    m = overlay.render_mask(render_img())
    ys, xs = np.where(m)
    assert (xs.min(), xs.max(), ys.min(), ys.max()) == (150, 250, 100, 250)


def test_compose_fits_the_model_height_into_the_frame():
    photo = Image.new("RGB", (800, 600), (200, 200, 200))
    out = np.asarray(overlay.compose(photo, render_img(), [0.25, 0.1, 0.75, 0.9]))
    cyan = np.where((out[..., 0] == 0) & (out[..., 1] == 255) & (out[..., 2] == 255))
    assert abs(cyan[0].min() - 60) <= 3 and abs(cyan[0].max() - 540) <= 3       # 10 % .. 90 % of 600 px
    assert abs((cyan[1].min() + cyan[1].max()) / 2 - 400) <= 3                  # centred on the frame
    with pytest.raises(ValueError):
        overlay.compose(photo, render_img(), [0.5, 0.5, 0.4, 0.9])


def test_review_sheet_has_the_overlay_panel_only_with_a_frame(tmp_path):
    from landmark3d import pipeline
    Image.new("RGB", (800, 600), (200, 200, 200)).save(tmp_path / "p.jpg")
    render_img().save(tmp_path / "r.png")
    plain = pipeline._side_by_side(tmp_path / "p.jpg", tmp_path / "r.png", tmp_path / "a.jpg", "photo1")
    framed = pipeline._side_by_side(tmp_path / "p.jpg", tmp_path / "r.png", tmp_path / "b.jpg", "photo1",
                                    [0.25, 0.1, 0.75, 0.9])
    ratio = lambda f: Image.open(f).width / Image.open(f).height
    assert ratio(framed) > ratio(plain) * 1.4                      # a third panel (sheets are capped at 1800 px)
