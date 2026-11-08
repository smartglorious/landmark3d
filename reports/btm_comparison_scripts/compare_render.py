"""Inside Blender: render silhouettes and shaded views of several GLBs in one frame (BTM local metres)."""
import json, math, os, sys
import bpy
from mathutils import Vector, Matrix

args = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
OUT = args["out"]
os.makedirs(OUT, exist_ok=True)
a = math.radians(120.5)
L = Vector((math.cos(a), math.sin(a), 0)); S = Vector((-L.y, L.x, 0))
C = Vector(args["centre"])          # BTM frame centre of the footprint
SPAN = 200.0


def setup(mode):
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.render.resolution_x = sc.render.resolution_y = 800
    sh = sc.display.shading
    if mode == "mask":
        sh.light = "FLAT"; sh.color_type = "SINGLE"; sh.single_color = (1, 1, 1)
        sh.show_shadows = False; sh.show_cavity = False; sh.show_object_outline = False
        sc.world.color = (0, 0, 0)
        sh.background_type = "WORLD" if hasattr(sh, "background_type") else sh.background_type
    else:
        sh.light = "STUDIO"; sh.color_type = "MATERIAL"; sh.show_shadows = True
        sc.world.color = (0.55, 0.58, 0.62)
    sc.view_settings.view_transform = "Standard"


def cam(name, loc, direction, up, ortho):
    cd = bpy.data.cameras.new(name)
    if ortho:
        cd.type = "ORTHO"; cd.ortho_scale = SPAN
    else:
        cd.lens = 35
    cd.clip_end = 3000
    o = bpy.data.objects.new(name, cd)
    bpy.context.scene.collection.objects.link(o)
    z = -direction.normalized()
    x = up.cross(z).normalized()
    y = z.cross(x)
    o.matrix_world = Matrix.Translation(loc) @ Matrix((x, y, z)).transposed().to_4x4()
    return o


VIEWS = {
    "top": (C + Vector((0, 0, 500)), Vector((0, 0, -1)), L, True),
    "south": (C - L * 500 + Vector((0, 0, 12)), L, Vector((0, 0, 1)), True),
    "north": (C + L * 500 + Vector((0, 0, 12)), -L, Vector((0, 0, 1)), True),
    "east": (C - S * 500 + Vector((0, 0, 12)), S, Vector((0, 0, 1)), True),
    "west": (C + S * 500 + Vector((0, 0, 12)), -S, Vector((0, 0, 1)), True),
    "oblique": (C - L * 160 - S * 120 + Vector((0, 0, 110)), None, Vector((0, 0, 1)), False),
}

for model in args["models"]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if bpy.context.scene.world is None:
        bpy.context.scene.world = bpy.data.worlds.new("W")
    bpy.ops.import_scene.gltf(filepath=model["glb"])
    off = Vector((model["dx"], model["dy"], 0))
    for o in list(bpy.context.scene.objects):
        if o.parent is None:
            o.location += off
    for vname, (loc, d, up, ortho) in VIEWS.items():
        if d is None:
            d = (C + Vector((0, 0, 6)) - loc)
        c = cam("cam_" + vname, loc, d, up, ortho)
        bpy.context.scene.camera = c
        for mode in (("mask", "shaded") if ortho else ("shaded",)):
            setup(mode)
            bpy.context.scene.render.filepath = os.path.join(OUT, f"{model['id']}_{vname}_{mode}.png")
            bpy.ops.render.render(write_still=True)
    tris = 0
    dg = bpy.context.evaluated_depsgraph_get()
    zmax = 0
    for o in bpy.context.scene.objects:
        if o.type == "MESH":
            me = o.evaluated_get(dg).to_mesh(); me.calc_loop_triangles(); tris += len(me.loop_triangles)
            zmax = max(zmax, max((o.matrix_world @ v.co).z for v in me.vertices))
            o.evaluated_get(dg).to_mesh_clear()
    print("CMP", model["id"], tris, round(zmax, 2))
