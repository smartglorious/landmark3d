"""Inside Blender on the r5b .blend: dump the manual model's main volumes as generic parts (world XY, BTM frame)."""
import json, math, sys
import bpy
from mathutils import Vector
sys.path.insert(0, r"D:\MTECH\Projects\Research\AI\Modeling\landmark3d")
from landmark3d import geom

OUT = sys.argv[sys.argv.index("--") + 1]
a = math.radians(120.5)
L = Vector((math.cos(a), math.sin(a), 0)); S = Vector((-L.y, L.x, 0))
sc = bpy.context.scene
roof = json.loads(sc["btm_roof_meta"])
north = json.loads(sc["btm_north_wing"])


def world(o):
    return [o.matrix_world @ v.co for v in o.data.vertices]


def ls_rect(name, vs, z0, z1, roof_spec, kind):
    ls = [v.dot(L) for v in vs]; ss = [v.dot(S) for v in vs]
    l0, l1, s0, s1 = min(ls), max(ls), min(ss), max(ss)
    return rect_part(name, l0, l1, s0, s1, z0, z1, roof_spec, kind)


def rect_part(name, l0, l1, s0, s1, z0, z1, roof_spec, kind):
    lc, scc = (l0 + l1) / 2, (s0 + s1) / 2
    c = L * lc + S * scc
    return {"id": name, "kind": kind, "rect": {"cx": c.x, "cy": c.y, "w": l1 - l0, "d": s1 - s0,
                                               "rot_deg": math.degrees(math.atan2(L.y, L.x))},
            "base_z": z0, "height": max(0.3, z1 - z0), "roof": roof_spec}


parts = []
wall = bpy.data.objects["Wall_Perimeter"]
ring = [(p.x, p.y) for p in world(wall) if abs(p.z) < 1e-6]
hall = geom.clip_halfplane(geom.ensure_ccw(ring), L.x, L.y, north["hall_wall_l"])
parts.append({"id": "hall", "kind": "hall", "polygon": hall, "base_z": 0.0, "height": 8.7,
              "roof": {"type": "flat", "parapet": 0.0}})
# cross halls from the ridge lines
rid = {r["name"]: r for r in roof["ridges"]}
wn, we = roof["cross"]["ns_half_width_m"], roof["cross"]["ew_half_width_m"]
ns0, ns1 = Vector(rid["NS"]["p0"]), Vector(rid["NS"]["p1"])
parts.append(rect_part("cross_ns", ns0.dot(L), ns1.dot(L), ns0.dot(S) - wn, ns0.dot(S) + wn, 8.7, 9.0,
                       {"type": "gable", "height": 3.7, "ridge_axis": "long"}, "hall"))
e0, e1 = Vector(rid["EW_a"]["p0"]), Vector(rid["EW_b"]["p1"])
parts.append(rect_part("cross_ew", e0.dot(L) - we, e0.dot(L) + we, min(e0.dot(S), e1.dot(S)), max(e0.dot(S), e1.dot(S)),
                       8.7, 9.0, {"type": "gable", "height": 3.2, "ridge_axis": "long"}, "hall"))
for st in roof["strips"]:
    parts.append(rect_part(st["name"].replace("Roof_Strip_", "strip_"), st["l0"], st["l1"], st["s0"], st["s1"], 8.7,
                           st["eave"], {"type": "gable", "height": st["ridge"] - st["eave"], "ridge_axis": "long"}, "strip"))
tower = bpy.data.objects["REAL_TowerBody"]
parts.append(ls_rect("tower", world(tower), 7.0, 19.4, {"type": "pyramid", "height": 1.2}, "tower"))
for g, h in (("Gate_South", 10.0), ("Gate_North", 8.1), ("Gate_East", 9.4), ("Gate_West", 8.1)):
    o = bpy.data.objects.get(g)
    if o:
        parts.append(ls_rect(g.lower(), world(o), 0.0, h, {"type": "flat", "parapet": 0.0}, "gate"))
body = bpy.data.objects.get("REAL_SouthPortalBody")
if body:
    parts.append(ls_rect("portal_body", world(body), 7.0, 10.0, {"type": "flat"}, "portal"))
for nm in north["blocks"]:
    o = bpy.data.objects[nm]
    vs = [o.matrix_world @ v.co for v in o.data.vertices]
    k = len(vs) // 2
    poly = [(v.x, v.y) for v in vs[:k]]
    parts.append({"id": nm.lower(), "kind": "wing", "polygon": poly, "base_z": 0.0, "height": 5.0,
                  "roof": {"type": "shed", "height": 1.4}})
vault = bpy.data.objects["NorthWing_PassageVault"]
parts.append(ls_rect("passage", world(vault), 0.0, 4.6, {"type": "gable", "height": 1.0, "ridge_axis": "long"}, "passage"))
json.dump({"parts": parts, "origin": {"lat": 10.7725367792, "lon": 106.6979747542}}, open(OUT, "w"), indent=1)
print("DUMP", len(parts))
