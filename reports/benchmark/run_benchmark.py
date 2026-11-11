"""Drive the 3-landmark benchmark through the local API: research -> A1 -> spec (+photo review) -> A2 -> build -> A3.
A1/A2 are auto-approved for these benchmark projects only (note recorded); A3 is left for the user."""
import json
import sys
import time

import httpx

B = "http://127.0.0.1:8770"
LANDMARKS = [
    {"name": "Bưu điện Trung tâm Sài Gòn", "location": "2 Công xã Paris, TP. Hồ Chí Minh, Việt Nam", "lat": 10.77995, "lon": 106.69990},
    {"name": "Nhà thờ Đức Bà Sài Gòn", "location": "01 Công xã Paris, TP. Hồ Chí Minh, Việt Nam", "lat": 10.77980, "lon": 106.69900},
    {"name": "Bitexco Financial Tower", "location": "2 Hải Triều, TP. Hồ Chí Minh, Việt Nam", "lat": 10.77160, "lon": 106.70440},
    {"name": "Ủy Ban Nhân Dân Thành Phố Hồ Chí Minh", "location": "86 Lê Thánh Tôn, Quận 1, TP. Hồ Chí Minh, Việt Nam",
     "lat": 10.7766558, "lon": 106.7008526},
]
# Held-out set: never used while developing the tool. Run ONCE with --heldout; no manual inputs, no tool changes.
HELDOUT = [
    {"name": "Dinh Độc Lập", "location": "135 Nam Kỳ Khởi Nghĩa, TP. Hồ Chí Minh, Việt Nam", "lat": 10.77700, "lon": 106.69530},
    {"name": "Nhà hát Thành phố Hồ Chí Minh", "location": "7 Công trường Lam Sơn, TP. Hồ Chí Minh, Việt Nam", "lat": 10.77650, "lon": 106.70310},
    {"name": "Landmark 81", "location": "720A Điện Biên Phủ, Bình Thạnh, TP. Hồ Chí Minh, Việt Nam", "lat": 10.79490, "lon": 106.72180},
    {"name": "Chợ Bình Tây", "location": "57A Tháp Mười, Quận 6, TP. Hồ Chí Minh, Việt Nam", "lat": 10.74970, "lon": 106.65100},
]
if "--heldout" in sys.argv:
    LANDMARKS = HELDOUT
NOTE = "benchmark auto-approval by Claude (user asked to run the benchmark)"
c = httpx.Client(base_url=B, timeout=60)
log = open("reports/benchmark/benchmark_log.txt", "a", encoding="utf-8")


def say(*a):
    msg = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(msg, flush=True)
    log.write(msg + "\n")
    log.flush()


RESET = {"Dinh Độc Lập": "research", "Nhà hát Thành phố Hồ Chí Minh": "research", "Landmark 81": "research",
         "Chợ Bình Tây": "research", "Bưu điện Trung tâm Sài Gòn": "spec", "Nhà thờ Đức Bà Sài Gòn": "spec", "Bitexco Financial Tower": "spec",
         "Ủy Ban Nhân Dân Thành Phố Hồ Chí Minh": "research"}
LIMIT_WORDS = ("hit your", "usage limit", "rate limit")
existing = {p["name"]: p["id"] for p in c.get("/api/projects").json()}
done = {}
ONLY = [a for a in sys.argv[1:] if not a.startswith("--")]
for lm in [l for l in LANDMARKS if not ONLY or any(o in l["name"] for o in ONLY)]:   # one project at a time: less load, and a quota stop affects only one
    pid = existing.get(lm["name"]) or c.post("/api/projects", json=lm).json()["id"]
    if "--reset" in sys.argv:
        c.post(f"/api/projects/{pid}/reset/{RESET[lm['name']]}")
    say("project", pid)
    c.post(f"/api/projects/{pid}/run")
    t0 = time.time()
    while time.time() - t0 < 3600:
        time.sleep(15)
        d = c.get(f"/api/projects/{pid}").json()
        if d["running"]:
            continue
        ph = d["state"]["phases"]
        # only fresh signals: phase messages, and the ai_failed error of a spec built in this run
        fresh_spec = ph["spec"]["status"] == "done"
        ai_failed = fresh_spec and any(i.get("code") == "ai_failed" for i in (d.get("spec") or {}).get("issues", []))
        spec_now = d.get("spec") or {}
        text = json.dumps([ph] + ([spec_now.get("issues", []), spec_now.get("photo_review", []),
                                   spec_now.get("quality")] if fresh_spec else []), ensure_ascii=False).lower()
        if ai_failed or any(w in text for w in LIMIT_WORDS):
            done[pid] = "STOPPED: AI usage limit reached"
            say(pid, done[pid])
            say("DONE", json.dumps(done, ensure_ascii=False))
            sys.exit(2)
        if ph["build_review"]["status"] == "waiting_approval":
            done[pid] = "A3 waiting: " + ph["build"]["message"]
            say(pid, done[pid])
            break
        blocked = [k for k, v in ph.items() if v["status"] in ("blocked", "failed", "error")]
        if blocked:
            done[pid] = (f"stopped at {blocked[0]}: {ph[blocked[0]]['message']} | "
                         f"missing: {[m['id'] for m in d['state'].get('missing', [])]}")
            say(pid, done[pid])
            break
        waiting = [k for k, v in ph.items() if v["status"] == "waiting_approval"]
        if waiting and waiting[0] in ("sources_review", "spec_review"):
            r = c.post(f"/api/projects/{pid}/approve", json={"phase": waiting[0], "decision": "approved", "notes": NOTE})
            if r.status_code != 200:
                done[pid] = f"approval of {waiting[0]} refused: {r.text[:300]}"
                say(pid, done[pid])
                break
            say(pid, "approved", waiting[0])
        c.post(f"/api/projects/{pid}/run")
say("DONE", json.dumps(done, ensure_ascii=False))
