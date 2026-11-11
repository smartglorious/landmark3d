"""Run a held-out set in-process (no web server, so a server restart cannot cut a run): create -> run -> auto-approve
A1, A2 and A2.5 (benchmark projects only, note recorded) -> stop at A3, a block or a failure. Run ONCE per set.

    python reports/benchmark/run_heldout_direct.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from landmark3d import pipeline  # noqa: E402
from landmark3d.project import Project, projects_dir  # noqa: E402

src = Path(__file__).with_name("run_benchmark.py").read_text(encoding="utf-8")
HELDOUT2 = eval(src.split("HELDOUT2 = ", 1)[1].split("\n]\n", 1)[0] + "\n]")    # the list only, no client code

NOTE = "benchmark auto-approval by Claude (user asked to run the held-out benchmark)"
LIMIT_WORDS = ("hit your", "usage limit", "rate limit")
log = open(Path(__file__).with_name("benchmark_log.txt"), "a", encoding="utf-8")


def say(*a):
    msg = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(msg, flush=True)
    log.write(msg + "\n")
    log.flush()


existing = {}
for d in projects_dir().iterdir():
    f = d / "project.json"
    if f.is_file():
        existing[json.loads(f.read_text(encoding="utf-8")).get("name")] = d.name
done = {}
for lm in HELDOUT2:
    if lm["name"] in existing:
        # resume a run cut by the machine (Blender could not start, 0xC0000142): only failed phases run again
        prj = Project(existing[lm["name"]])
        if prj.state()["phases"]["build_review"]["status"] != "pending":
            say("SKIP already finished:", prj.id)
            continue
        failed = [k for k, v in prj.state()["phases"].items() if v["status"] == "failed"]
        for k in failed:
            prj.set_phase(k, "pending", "resumed after an infrastructure failure")
        say("resume", prj.id, "failed phases reset:", failed)
    else:
        prj = Project.create(lm["name"], lm["location"], None, lm["lat"], lm["lon"])
        say("project", prj.id)
    t0 = time.time()
    while True:
        out = pipeline.run_until_blocked(prj)
        st = prj.state()
        ph = st["phases"]
        sp = prj.read_json("spec/landmark_spec.json") or {}
        text = json.dumps([ph, sp.get("issues", [])], ensure_ascii=False).lower()
        if any(i.get("code") == "ai_failed" for i in sp.get("issues", [])) or any(w in text for w in LIMIT_WORDS):
            done[prj.id] = "STOPPED: AI usage limit or AI failure"
            say(prj.id, done[prj.id])
            break
        stop = out.get("stopped_at")
        if out["reason"] == "approval" and stop in ("sources_review", "spec_review", "texture_review"):
            try:
                pipeline.approve(prj, stop, "approved", NOTE)
                say(prj.id, "approved", stop)
                continue
            except Exception as exc:
                done[prj.id] = f"approval of {stop} refused: {exc}"[:400]
                say(prj.id, done[prj.id])
                break
        if stop == "build_review":
            done[prj.id] = f"A3 waiting ({round((time.time() - t0) / 60)} min): " + ph["build"]["message"]
        else:
            done[prj.id] = (f"stopped at {stop} ({out['reason']}): {ph.get(stop, {}).get('message', '')} | "
                            f"missing: {[m['id'] for m in st.get('missing', [])]}")[:600]
        say(prj.id, done[prj.id])
        break
say("DONE", json.dumps(done, ensure_ascii=False))
