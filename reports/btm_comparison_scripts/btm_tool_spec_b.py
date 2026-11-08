"""Pass B: put the BTM-derived generic parts into the tool's spec at A2, approve, build."""
import json
import sys
import time

import httpx

sys.path.insert(0, r"D:\MTECH\Projects\Research\AI\Modeling\landmark3d")
from landmark3d import geom  # noqa: E402

SP = r"C:\Users\Admin\AppData\Local\Temp\claude\9d70dde4a1d45629\fc53157e-4275-47ec-9c42-77771f04a04e\scratchpad"
pid = sys.argv[1]
c = httpx.Client(base_url="http://127.0.0.1:8770", timeout=120)
d = c.get(f"/api/projects/{pid}").json()
assert d["state"]["phases"]["spec_review"]["status"] == "waiting_approval", d["state"]["phases"]["spec_review"]
spec = d["spec"]
dump = json.load(open(SP + r"\btm_parts.json", encoding="utf-8"))
lat0, lon0 = dump["origin"]["lat"], dump["origin"]["lon"]
olat, olon = spec["origin"]["lat"], spec["origin"]["lon"]
import math
R = geom.R_EARTH


def conv(x, y):
    lon = lon0 + math.degrees(x / (R * math.cos(math.radians(lat0))))
    lat = lat0 + math.degrees(y / R)
    return geom.lonlat_to_local(lon, lat, olon, olat)


mat = {"wall": "wall", "roof": "roof", "trim": "trim"}
parts = []
for p in dump["parts"]:
    q = {"id": p["id"], "kind": p["kind"], "base_z": round(p["base_z"], 3), "height": round(p["height"], 3),
         "roof": p["roof"], "material": dict(mat)}
    if "polygon" in p:
        q["footprint"] = {"polygon": [[round(v, 3) for v in conv(x, y)] for x, y in p["polygon"]]}
    else:
        r = p["rect"]
        cx, cy = conv(r["cx"], r["cy"])
        q["footprint"] = {"rect": {"cx": round(cx, 3), "cy": round(cy, 3), "w": round(r["w"], 3), "d": round(r["d"], 3),
                                   "rot_deg": round(r["rot_deg"], 3)}}
    q["facade"] = {"openings": p["kind"] in ("hall", "wing"), "bay_spacing": 4.6, "opening_type": "arch",
                   "opening_width": 3.6, "opening_height": 3.4, "level_height": 9.0} if p["id"] == "hall" or p["kind"] == "wing" \
        else {"openings": False}
    if p["id"].startswith(("strip_", "cross_")):
        q["roof"] = dict(q["roof"])
    parts.append(q)
spec["parts"] = parts
spec["materials"] = {"wall": {"srgb": [244, 198, 102], "roughness": 0.78},      # tower texture yellow (r5b wall)
                     "roof": {"srgb": [216, 66, 44], "roughness": 0.58},        # r5b roof red (linear 0.68,0.055,0.025)
                     "trim": {"srgb": [247, 243, 229], "roughness": 0.7},
                     "opening": {"srgb": [28, 32, 36], "roughness": 0.9}}
spec["notes"] = ["pass B: parts converted from the manual r5b model (BTM data)"]
r = c.put(f"/api/projects/{pid}/spec", json={"spec": spec})
print("PUT", r.status_code, r.text[:300])
c.post(f"/api/projects/{pid}/approve", json={"phase": "spec_review", "decision": "approved", "notes": "pass B spec from BTM data"})
c.post(f"/api/projects/{pid}/run")
for _ in range(100):
    time.sleep(3)
    d = c.get(f"/api/projects/{pid}").json()
    if not d["running"]:
        break
print("BUILD", json.dumps(d["build"]))
