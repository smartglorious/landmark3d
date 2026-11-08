"""Benchmark comparison sheets: each reference photo next to the build's same-angle render, and the satellite plan
view next to the final model seen from above (same extent and scale). Usage: python compare_sheets.py <project-id>..."""
import hashlib
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from landmark3d.engine import runner
from landmark3d.project import Project

OUT = Path(__file__).resolve().parent


def photos_in_order(prj):
    out, seen = [], set()
    for s in prj.read_json("input/sources.json", {"sources": []})["sources"]:
        f = prj.root / s.get("path", "")
        if s.get("kind") != "photo" or s.get("use") == "excluded" or not f.is_file():
            continue
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        if h not in seen:
            seen.add(h)
            out.append(f)
    return out[:6]


def row(a_path, b_path, h=420):
    a = Image.open(a_path).convert("RGB")
    b = Image.open(b_path).convert("RGB")
    a = a.resize((max(1, int(a.width * h / a.height)), h))
    b = b.resize((max(1, int(b.width * h / b.height)), h))
    r = Image.new("RGB", (a.width + b.width + 10, h), "white")
    r.paste(a, (0, 0))
    r.paste(b, (a.width + 10, 0))
    return r


for pid in sys.argv[1:]:
    prj = Project(pid)
    sp = prj.read_json("spec/landmark_spec.json")
    photos = [prj.root / x for x in sp["photos"]] if sp.get("photos") else photos_in_order(prj)
    rows = []
    for v in (sp.get("views") or [])[:3]:
        n = int(v["photo"])
        rp = prj.root / "renders" / f"photo{n}.png"
        if rp.exists() and n <= len(photos):
            rows.append(row(photos[n - 1], rp))
    sat = prj.root / "input" / "geo" / "satellite_annotated.jpg"
    if sat.exists():
        r = runner.preview(prj, sp, {"satplan": {"ortho_extent": [240, 160]}}, prj.root / "reports" / "final_satplan")
        if r:
            rows.append(row(sat, r[0]))
    if not rows:
        print(pid, "nothing to compare")
        continue
    W = max(r.width for r in rows)
    sheet = Image.new("RGB", (W, sum(r.height + 10 for r in rows) + 40), "white")
    ImageDraw.Draw(sheet).text((10, 12), f"{sp['name']}: reference (left) | model, same viewpoint (right)", fill="black")
    y = 40
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height + 10
    sheet.thumbnail((1800, 3000))
    path = OUT / f"{pid}.jpg"
    sheet.save(path, "JPEG", quality=85)
    print(pid, path, len(rows), "rows")
