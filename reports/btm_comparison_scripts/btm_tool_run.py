"""Drive the running Landmark3D server through the Ben Thanh inputs (pass A: automatic)."""
import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8770"
RAW = Path(r"D:\MTECH\Projects\Research\AI\Modeling\BenThanhMarket3D\data\images\raw")
c = httpx.Client(base_url=BASE, timeout=120)


def wait(pid, limit=400):
    for _ in range(limit // 3):
        d = c.get(f"/api/projects/{pid}").json()
        if not d["running"]:
            return d
        time.sleep(3)
    raise SystemExit("timeout")


def stop_at(d):
    for p in d["phases"]:
        st = d["state"]["phases"][p["id"]]["status"]
        if st not in ("done", "approved"):
            return p["id"], st
    return None, "complete"


name = sys.argv[1] if len(sys.argv) > 1 else "Chợ Bến Thành"
c.put("/api/settings", json={"contact": "https://example.org/landmark3d-local-test"})
pid = c.post("/api/projects", json={"name": name, "location": "Quận 1, Thành phố Hồ Chí Minh, Việt Nam"}).json()["id"]
print("PROJECT", pid)
uploaded = False
stop_phase = sys.argv[2] if len(sys.argv) > 2 else None
for step in range(15):
    c.post(f"/api/projects/{pid}/run")
    d = wait(pid)
    ph, st = stop_at(d)
    print("STOP", ph, st, d["state"]["phases"].get(ph, {}).get("message", "") if ph else "")
    if ph is None or ph == stop_phase:
        break
    if st == "waiting_approval":
        c.post(f"/api/projects/{pid}/approve", json={"phase": ph, "decision": "approved", "notes": "BTM test"})
    elif st == "blocked":
        missing = d["state"]["missing"]
        print("MISSING", [m["id"] for m in missing])
        if not uploaded:
            for f in sorted(RAW.iterdir()):
                lic = "cc_by_sa" if "cc-by-sa" in f.name else ("public_domain" if "cc0" in f.name else "owned")
                with open(f, "rb") as fh:
                    c.post(f"/api/projects/{pid}/photos", files={"file": (f.name, fh, "image/png" if f.suffix == ".png" else "image/jpeg")},
                           data={"licence": lic, "author": "BTM data pack"})
            uploaded = True
        vals = {}
        for m in missing:
            if m["id"] == "main_wall_height_m":
                vals[m["id"]] = 7.0                      # dimensions.json wall_height (MEASURED_SECONDARY)
            elif m["id"] == "roof_form":
                vals[m["id"]] = "hip"
        r = c.post(f"/api/projects/{pid}/inputs", json={"values": vals}).json()
        print("LEFT", [m["id"] for m in r["missing"]])
    else:
        print("FAILED", d["state"]["phases"][ph])
        break
d = c.get(f"/api/projects/{pid}").json()
print("RESEARCH", json.dumps(d["research"]["report"]["steps"], ensure_ascii=False))
print("FACTS", json.dumps({k: v["value"] for k, v in d["research"]["facts"].items()}, ensure_ascii=False))
print("BUILD", json.dumps(d["build"]))
