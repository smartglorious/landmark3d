import json, math, sys
sys.path.insert(0, r"D:\MTECH\Projects\Research\AI\Modeling\landmark3d")
from landmark3d import geom

LAT0, LON0 = 10.7725367792, 106.6979747542
SP = r"C:\Users\Admin\AppData\Local\Temp\claude\9d70dde4a1d45629\fc53157e-4275-47ec-9c42-77771f04a04e\scratchpad"
P = r"D:\MTECH\Projects\Research\AI\Modeling\landmark3d\projects"
models = [{"id": "r5b", "glb": r"D:\MTECH\Projects\Research\AI\Modeling\BenThanhMarket3D\output\BenThanhMarket.glb", "dx": 0, "dy": 0}]
for mid, pid in (("toolA", "cho-ben-thanh"), ("toolB", "cho-ben-thanh-2")):
    sp = json.load(open(rf"{P}\{pid}\spec\landmark_spec.json", encoding="utf-8"))
    dx, dy = geom.lonlat_to_local(sp["origin"]["lon"], sp["origin"]["lat"], LON0, LAT0)
    models.append({"id": mid, "glb": rf"{P}\{pid}\output\model.glb", "dx": dx, "dy": dy})
    centre = (dx, dy, 0)
json.dump({"out": SP + r"\cmp", "centre": centre, "models": models}, open(SP + r"\cmp_job.json", "w"), indent=1)
print(models)
