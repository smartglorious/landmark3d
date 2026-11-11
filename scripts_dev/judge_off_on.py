"""Same-spec A/B: judge each project's current spec with detail_engine_v2 off, then on. Only the flag differs.
The flag is overridden in-process (settings file untouched). Writes reports/judge_flag_off|on/ and a JSON summary."""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from landmark3d import settings as settings_mod, pipeline
from landmark3d.research import ai
from landmark3d.project import Project
from landmark3d.research import satellite

IDS = sys.argv[1:] or ["uy-ban-nhan-dan-thanh-pho-ho-chi-minh", "buu-ien-trung-tam-sai-gon", "nha-tho-uc-ba-sai-gon",
                       "bitexco-financial-tower"]
_orig = settings_mod.load
FLAG = {"v": False}
settings_mod.load = lambda *a, **k: {**_orig(*a, **k), "detail_engine_v2": FLAG["v"]}

out = {}
provider = ai.get("spec")
for pid in IDS:
    prj = Project(pid)
    sp = prj.read_json("spec/landmark_spec.json")
    photos = [prj.root / p for p in sp.get("photos") or []]
    sat = satellite.images_for_ai(prj)
    main = [tuple(q) for q in sp["footprint"]["local"]]
    st = prj.state()
    massing = {"parts": sp["parts"], "materials": sp.get("materials") or {}, "views": sp.get("views") or []}
    out[pid] = {}
    for flag in (False, True):
        FLAG["v"] = flag
        q = pipeline.judge(prj, provider, massing, photos, sat, main, {}, st,
                           out_name=f"judge_flag_{'on' if flag else 'off'}")
        out[pid]["on" if flag else "off"] = {k: q.get(k) for k in ("overall", "readings", "scores", "result")}
        print(pid, "on" if flag else "off", json.dumps(out[pid]["on" if flag else "off"]), flush=True)
        if "result" in q and q.get("overall") is None:
            print("STOP:", q["result"], flush=True)
            Path(__file__).with_name("judge_off_on.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
            sys.exit(2)
Path(__file__).with_name("judge_off_on.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
print("DONE")
