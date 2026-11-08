import json
import numpy as np
from PIL import Image, ImageDraw

D = r"C:\Users\Admin\AppData\Local\Temp\claude\9d70dde4a1d45629\fc53157e-4275-47ec-9c42-77771f04a04e\scratchpad\cmp"
views = ["top", "south", "north", "east", "west"]


def mask(m, v):
    a = np.asarray(Image.open(f"{D}\\{m}_{v}_mask.png").convert("L")).astype(float)
    return a > 128


res = {}
for m in ("toolA", "toolB"):
    res[m] = {}
    for v in views:
        r, t = mask("r5b", v), mask(m, v)
        inter, union = (r & t).sum(), (r | t).sum()
        res[m][v] = round(float(inter / union), 3) if union else None
    res[m]["mean"] = round(float(np.mean([res[m][v] for v in views])), 3)
print(json.dumps(res, indent=1))
json.dump(res, open(D + r"\iou.json", "w"), indent=1)

# contact sheet: rows = models, cols = oblique, top, south
cols = [("oblique", "shaded"), ("top", "shaded"), ("south", "shaded")]
W = 400
sheet = Image.new("RGB", (W * 3 + 140, W * 3 + 30), (255, 255, 255))
dr = ImageDraw.Draw(sheet)
for j, (v, _) in enumerate(cols):
    dr.text((140 + j * W + 8, 8), {"oblique": "oblique SW", "top": "plan (ortho)", "south": "south elevation (ortho)"}[v], fill=(0, 0, 0))
for i, m in enumerate(("r5b", "toolA", "toolB")):
    label = {"r5b": "r5b (manual)", "toolA": "tool A (auto)", "toolB": "tool B (BTM spec)"}[m]
    dr.text((8, 30 + i * W + W // 2), label, fill=(0, 0, 0))
    for j, (v, mode) in enumerate(cols):
        im = Image.open(f"{D}\\{m}_{v}_{mode}.png").convert("RGB").resize((W, W))
        sheet.paste(im, (140 + j * W, 30 + i * W))
sheet.save(D + r"\comparison_sheet.png")
