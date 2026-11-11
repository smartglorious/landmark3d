"""Compare the flag-off baseline (copied before the run) with the flag-on rerun: judge scores, build size, quality
report, structure issues. Reads project folders only; prints a table."""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(sys.argv[1])
BASE = ROOT / "_baseline_flag_off_2026-11-10"
IDS = ["uy-ban-nhan-dan-thanh-pho-ho-chi-minh", "buu-ien-trung-tam-sai-gon", "nha-tho-uc-ba-sai-gon",
       "bitexco-financial-tower"]


def load(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


for pid in IDS:
    rows = {}
    for label, root in (("off", BASE / pid), ("on", ROOT / pid)):
        sp = load(root / "spec" / "landmark_spec.json") or {}
        br = load(root / "reports" / "build_result.json") or {}
        q = sp.get("quality") or {}
        qr = load(root / "reports" / "quality.json") or {}
        rows[label] = {
            "overall": q.get("overall"), "scores": q.get("scores"),
            "parts": len(sp.get("parts", [])), "tris": br.get("triangles"), "bytes": br.get("bytes"),
            "materials": br.get("materials"), "lod": br.get("openings_lod"),
            "articulation": (br.get("articulation") or {}).get("placed"),
            "errors": [i["code"] for i in sp.get("issues", []) if i.get("severity") == "error"],
            "gate": qr.get("gate"), "plan_iou": (qr.get("metrics") or {}).get("plan_iou"),
            "engine": (br.get("engine") or {}).get("detail_engine_v2"),
        }
    print("=" * 100)
    print(pid)
    for k in rows["off"]:
        print(f"  {k:13s} off={rows['off'][k]!s:45.45s} on={rows['on'][k]!s}")
