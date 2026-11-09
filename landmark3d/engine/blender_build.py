"""Runs INSIDE Blender (headless):  blender -b --factory-startup --python blender_build.py -- <job.json>

Builds any landmark spec (parts / roofs / openings / materials), exports the GLB (Draco when
requested, plain glTF otherwise), saves the .blend, renders QA views and writes result.json.
Geometry comes from landmark3d/geom.py (pure Python) so it is shared with the tests.
"""
import json
import math
import os
import sys
import time

import bpy
from mathutils import Matrix, Vector

TOOL_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, TOOL_ROOT)
from landmark3d import geom  # noqa: E402
from landmark3d.detail import accounting as detail_accounting, engine as detail_engine  # noqa: E402


def srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def clear_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.cameras, bpy.data.lights):
        for d in list(coll):
            if d.users == 0:
                coll.remove(d)


def make_material(name, srgb, roughness, texture=None, world_size=3.0, tint="material", clamp=False, metallic=0.0):
    """tint: 'material' = multiply by the material colour, None = keep the photo colours, [r,g,b] = explicit tint."""
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    col = tuple(srgb_to_linear(c) for c in srgb) + (1.0,)
    if texture and tint is None:
        col = (1.0, 1.0, 1.0, 1.0)
    elif texture and isinstance(tint, (list, tuple)):
        col = tuple(srgb_to_linear(c) for c in tint) + (1.0,)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = float(metallic or 0.0)
    m.diffuse_color = col
    if texture and os.path.exists(texture):
        img = bpy.data.images.load(texture, check_existing=True)
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.extension = "EXTEND" if clamp else "REPEAT"
        mix = nt.nodes.new("ShaderNodeMix")              # exported as baseColorTexture x baseColorFactor
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        mix.inputs["Factor"].default_value = 1.0
        a = next(s for s in mix.inputs if s.name == "A" and s.type == "RGBA")
        b = next(s for s in mix.inputs if s.name == "B" and s.type == "RGBA")
        b.default_value = col
        nt.links.new(tex.outputs["Color"], a)
        nt.links.new(next(s for s in mix.outputs if s.name == "Result" and s.type == "RGBA"), bsdf.inputs["Base Color"])
        if not clamp:
            m["l3d_world_size"] = world_size
    else:
        bsdf.inputs["Base Color"].default_value = col
    return m


def link_mesh(name, gm, mat_by_tag, mats):
    me = bpy.data.meshes.new(name)
    me.from_pydata(gm.verts, [], gm.faces)
    me.update()
    order = []
    for t in gm.tags:
        if mat_by_tag[t] not in order:
            order.append(mat_by_tag[t])
    for mname in order:
        me.materials.append(mats[mname])
    for poly, t in zip(me.polygons, gm.tags):
        poly.material_index = order.index(mat_by_tag[t])
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def planar_uvs(ob):
    me = ob.data
    if not any(m and "l3d_world_size" in m for m in me.materials):
        return
    layer = me.uv_layers.get("UVMap") or me.uv_layers.new(name="UVMap")
    Z = Vector((0, 0, 1))
    for poly in me.polygons:
        m = me.materials[poly.material_index]
        size = float(m.get("l3d_world_size", 3.0)) if m else 3.0
        n = poly.normal
        for li in poly.loop_indices:
            p = me.vertices[me.loops[li].vertex_index].co
            if abs(n.z) >= 0.995:
                uv = (p.x / size, p.y / size)
            elif abs(n.z) <= 0.3:
                t = Z.cross(Vector((n.x, n.y, 0))).normalized()
                uv = (p.dot(t) / size, p.z / size)
            else:
                r = n.cross(Z).normalized()
                d = r.cross(n).normalized()
                uv = (p.dot(r) / size, p.dot(d) / size)
            layer.data[li].uv = uv
    me.update()


def triangle_count():
    dg = bpy.context.evaluated_depsgraph_get()
    total = 0
    for ob in bpy.context.scene.objects:
        if ob.type != "MESH":
            continue
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
        me.calc_loop_triangles()
        total += len(me.loop_triangles)
        ev.to_mesh_clear()
    return total


def photo_camera(scene, cd, bounds, cv, fill=0.88, ground_z=None):
    """Camera position and aim for a photo viewpoint: binary-search the distance so the bounding box's projection
    fills `fill` of the frame (the tighter axis). Street level (elevation <= 15 deg): camera 1.7 m above the ground,
    looking up. `bounds` may be one part's box (a close-up); the ground stays the building's."""
    (x0, y0, z0), (x1, y1, z1) = bounds
    gz = z0 if ground_z is None else ground_z
    mid = Vector(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    corners = [Vector((x, y, z)) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    az, el = math.radians(float(cv["azimuth_deg"])), float(cv.get("elevation_deg", 10))
    street = el <= 15.0
    d = Vector((math.cos(az), math.sin(az), 0.0 if street else math.sin(math.radians(el))))
    if not street:
        d = Vector((d.x * math.cos(math.radians(el)), d.y * math.cos(math.radians(el)), d.z)).normalized()
    aspect = scene.render.resolution_y / scene.render.resolution_x
    th = (cd.sensor_width / 2) / cd.lens
    tv = th * aspect
    fill = float(cv.get("fill", fill))

    def place(dist):
        loc = mid + d * dist
        if street:
            loc = Vector((loc.x, loc.y, gz + 1.7))
        return loc

    def extent(dist):
        loc = place(dist)
        f = (mid - loc).normalized()
        r = f.cross(Vector((0, 0, 1)))
        r = r.normalized() if r.length > 1e-6 else Vector((1, 0, 0))
        u = r.cross(f)
        worst = 0.0
        for p in corners:
            v = p - loc
            z = v.dot(f)
            if z <= 0.5:
                return float("inf")
            worst = max(worst, abs(v.dot(r)) / (z * th), abs(v.dot(u)) / (z * tv))
        return worst
    radius = 0.5 * (Vector((x1, y1, z1)) - Vector((x0, y0, z0))).length
    lo, hi = max(1.0, radius * 0.3), radius * 40
    for _ in range(40):
        m = (lo + hi) / 2
        if extent(m) > fill:
            lo = m
        else:
            hi = m
    return place(hi), mid


def focus_hidden(scene, cam_loc, fb, focus, max_hidden=0.5):
    """True when more than `max_hidden` of the rays from the camera to points on the focused part's box hit another
    part first."""
    (x0, y0, z0), (x1, y1, z1) = fb
    targets = [Vector(((x0 + x1) / 2, (y0 + y1) / 2, z0 + (z1 - z0) * f)) for f in (0.3, 0.55, 0.8)]
    targets += [Vector((x, y, z0 + (z1 - z0) * 0.55)) for x, y in ((x0, y0), (x1, y1), (x0, y1), (x1, y0))]
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    hidden = 0
    for t in targets:
        d = t - Vector(cam_loc)
        hit, _, _, _, ob, _ = scene.ray_cast(dg, Vector(cam_loc), d.normalized(), distance=max(0.0, d.length - 0.05))
        part = (getattr(ob, "original", None) or ob).get("l3d_part") if hit and ob is not None else None
        if part not in (None, focus):
            hidden += 1
    print("L3D_FOCUS", focus, f"{hidden}/{len(targets)} rays hidden")
    return hidden > max_hidden * len(targets)


def render_views(out_dir, bounds, views, custom=None, resolution=(1280, 800), part_bounds=None):
    scene = bpy.context.scene
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        try:
            scene.render.engine = eng
            break
        except TypeError:
            continue
    if scene.render.engine == "BLENDER_WORKBENCH":
        scene.display.shading.color_type = "TEXTURE"
    scene.render.resolution_x, scene.render.resolution_y = int(resolution[0]), int(resolution[1])
    scene.render.image_settings.file_format = "PNG"
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    scene.world.use_nodes = True
    bg = next(n for n in scene.world.node_tree.nodes if n.type == "BACKGROUND")
    # Sky-to-ground gradient so glass and metal have something to reflect (neutral, no colour cast on walls).
    nt = scene.world.node_tree
    try:
        tc = nt.nodes.new("ShaderNodeTexCoord")
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        # World "Generated" is the view direction (-1..1); Map Range puts the horizon at 0.5.
        # Dark ground | bright horizon | blue zenith: that contrast is what makes glass read as glass.
        mr = nt.nodes.new("ShaderNodeMapRange")
        mr.inputs["From Min"].default_value, mr.inputs["From Max"].default_value = -1.0, 1.0
        els = ramp.color_ramp.elements
        els[0].position, els[0].color = 0.42, (0.30, 0.31, 0.32, 1.0)
        els[1].position, els[1].color = 1.0, (0.22, 0.42, 0.85, 1.0)
        for pos, col in ((0.5, (0.86, 0.89, 0.94, 1.0)), (0.7, (0.52, 0.66, 0.90, 1.0))):
            e = els.new(pos)
            e.color = col
        nt.links.new(tc.outputs["Generated"], sep.inputs[0])
        nt.links.new(sep.outputs["Z"], mr.inputs["Value"])
        nt.links.new(mr.outputs["Result"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    except Exception:
        bg.inputs["Color"].default_value = (0.6, 0.63, 0.67, 1.0)
    bg.inputs["Strength"].default_value = 0.8
    try:
        scene.view_settings.view_transform = "AgX"
    except TypeError:
        pass
    (x0, y0, z0), (x1, y1, z1) = bounds
    c = Vector(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 3))
    span = max(x1 - x0, y1 - y0, z1 - z0, 10.0)
    gm = bpy.data.meshes.new("QA_Ground")
    g = span * 3
    gm.from_pydata([(c.x - g, c.y - g, -0.02), (c.x + g, c.y - g, -0.02), (c.x + g, c.y + g, -0.02), (c.x - g, c.y + g, -0.02)], [], [(0, 1, 2, 3)])
    ground = bpy.data.objects.new("QA_Ground", gm)
    scene.collection.objects.link(ground)
    gmat = make_material("QA_Ground", (120, 124, 128), 0.95)
    gm.materials.append(gmat)
    sun_d = bpy.data.lights.new("QA_Sun", "SUN")
    sun_d.energy = 3.5
    sun = bpy.data.objects.new("QA_Sun", sun_d)
    sun.rotation_euler = (math.radians(40), math.radians(-15), math.radians(150))
    scene.collection.objects.link(sun)
    made, tmp = [], [ground, sun]
    dirs = {"front_south": (0, -1, 0.35), "oblique_sw": (-0.8, -0.9, 0.6), "oblique_ne": (0.8, 0.9, 0.6),
            "east": (1, 0, 0.3), "west": (-1, 0, 0.3), "north": (0, 1, 0.3), "aerial": (-0.3, -0.6, 1.2)}
    for vname, v in (custom or {}).items():          # camera direction from the building, as given for a photo
        if v.get("ortho_extent"):
            continue
        az, el = math.radians(float(v.get("azimuth_deg", 270))), math.radians(float(v.get("elevation_deg", 10)))
        dirs[vname] = (math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), max(0.02, math.sin(el)))
    for name in views:
        cd = bpy.data.cameras.new("QA_" + name)
        cam = bpy.data.objects.new("QA_" + name, cd)
        scene.collection.objects.link(cam)
        tmp.append(cam)
        cv = (custom or {}).get(name) or {}
        if cv.get("ortho_extent"):
            cd.type = "ORTHO"
            cd.ortho_scale = float(cv["ortho_extent"][0])          # width; height follows the 3:2 render
            cam.location = (0.0, 0.0, z1 + 200)
            cam.rotation_euler = (0, 0, 0)
        elif name == "plan":
            cd.type = "ORTHO"
            cd.ortho_scale = span * 1.15
            cam.location = (c.x, c.y, z1 + 200)
            cam.rotation_euler = (0, 0, 0)
        elif cv.get("azimuth_deg") is not None:
            # Photo viewpoint: frame the model the way photos frame a building - it fills most of the picture,
            # and street-level shots are taken from eye height looking up.
            cd.lens = 40
            fb = (part_bounds or {}).get(cv.get("focus")) if cv.get("focus") else None
            cam.location, c_view = photo_camera(scene, cd, fb or bounds, cv, ground_z=bounds[0][2])
            if fb and focus_hidden(scene, cam.location, fb, cv["focus"]):
                # other volumes stand between the camera and the part (a raised shot over the roofs): a close-up
                # would show only them - frame the whole building instead
                cam.location, c_view = photo_camera(scene, cd, bounds, cv)
            cam.rotation_mode = "QUATERNION"
            cam.rotation_quaternion = (c_view - cam.location).to_track_quat("-Z", "Y")
        else:
            d = Vector(dirs.get(name, (0, -1, 0.4))).normalized()
            cd.lens = 40
            # Fit the whole bounding sphere (tall towers included) in the narrower (vertical) field of view.
            mid = Vector(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
            radius = 0.5 * math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2)
            vfov = 2 * math.atan((cd.sensor_width / 2) * (scene.render.resolution_y / scene.render.resolution_x) / cd.lens)
            dist = max(radius / math.sin(vfov / 2) * 1.02, span * 1.2)
            cam.location = mid + d * dist
            c_view = mid
            cam.rotation_mode = "QUATERNION"
            cam.rotation_quaternion = (c_view - cam.location).to_track_quat("-Z", "Y")
        cd.clip_end = 5000
        scene.camera = cam
        scene.render.filepath = os.path.join(out_dir, f"{name}.png")
        bpy.ops.render.render(write_still=True)
        made.append(f"{name}.png")
    for ob in tmp:
        data = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        if isinstance(data, bpy.types.Camera):
            bpy.data.cameras.remove(data)
        elif isinstance(data, bpy.types.Light):
            bpy.data.lights.remove(data)
        elif isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
    scene.camera = None
    return made, scene.render.engine


SIDE_DIR = {"north": (0.0, 1.0), "south": (0.0, -1.0), "east": (1.0, 0.0), "west": (-1.0, 0.0)}


def add_panels(job, meshes, warnings):
    """Photo panels: one quad per chosen wall face, 3 cm in front, UVs into the packed panel atlas."""
    panels = job.get("panels") or []
    if not panels or not job.get("panel_atlas"):
        return 0
    mat = make_material("L3D_panels", (255, 255, 255), 0.7, job["panel_atlas"], tint=None, clamp=True)
    by_id = {part["id"]: (part, poly) for part, poly, _ in meshes}
    verts, faces, uvs = [], [], []
    made = 0
    for pn in panels:
        if pn["part"] not in by_id:
            warnings.append(f"panel {pn['id']}: part '{pn['part']}' not found or disabled")
            continue
        part, poly = by_id[pn["part"]]
        ring = geom.ensure_ccw(poly)
        n = len(ring)
        edges = []
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            ln = math.dist(a, b)
            if ln < 0.5:
                continue
            d = ((b[0] - a[0]) / ln, (b[1] - a[1]) / ln)
            out = (d[1], -d[0])
            edges.append((a, b, ln, out))
        if pn.get("edge_index") is not None:          # an exact wall face (automatic facade textures)
            i = int(pn["edge_index"]) % n
            a_, b_ = ring[i], ring[(i + 1) % n]
            ln_ = math.dist(a_, b_)
            chosen = [(a_, b_, ln_, ((b_[1] - a_[1]) / ln_, -(b_[0] - a_[0]) / ln_))] if ln_ > 0.5 else []
        elif pn["side"] == "all":
            chosen = [e for e in edges if e[2] >= 0.5 * max(x[2] for x in edges)]
        else:
            sd = SIDE_DIR[pn["side"]]
            best = max(edges, key=lambda e: e[3][0] * sd[0] + e[3][1] * sd[1])
            chosen = [best] if best[3][0] * sd[0] + best[3][1] * sd[1] > 0.3 else []
            if not chosen:
                warnings.append(f"panel {pn['id']}: part '{pn['part']}' has no face towards {pn['side']}")
        z0 = pn["z0"] if pn.get("z0") is not None else part["base_z"]
        z1 = pn["z1"] if pn.get("z1") is not None else part["base_z"] + part["height"]
        u0, v0, u1, v1 = pn["uv"]
        for a, b, ln, out in chosen:
            o = (out[0] * 0.08, out[1] * 0.08)            # in front of the window panels (5 cm)
            base = len(verts)
            verts += [(a[0] + o[0], a[1] + o[1], z0), (b[0] + o[0], b[1] + o[1], z0),
                      (b[0] + o[0], b[1] + o[1], z1), (a[0] + o[0], a[1] + o[1], z1)]
            faces.append((base, base + 1, base + 2, base + 3))
            uvs += [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
            made += 1
    if not faces:
        return 0
    me = bpy.data.meshes.new("L3D_panels")
    me.from_pydata(verts, [], faces)
    me.update()
    layer = me.uv_layers.new(name="UVMap")
    for i, uv in enumerate(uvs):
        layer.data[i].uv = uv
    me.materials.append(mat)
    ob = bpy.data.objects.new("L3D_panels", me)
    bpy.context.scene.collection.objects.link(ob)
    return made


def export(path, compression):
    kw = dict(filepath=path, export_format="GLB", export_apply=True, export_materials="EXPORT",
              export_cameras=False, export_lights=False, export_image_format="AUTO", export_texcoords=True)
    if compression == "draco":
        try:
            bpy.ops.export_scene.gltf(**kw, export_draco_mesh_compression_enable=True,
                                      export_draco_mesh_compression_level=6, export_draco_position_quantization=14,
                                      export_draco_normal_quantization=10, export_draco_texcoord_quantization=12)
            return "draco"
        except Exception as exc:  # Draco library missing
            print("DRACO_FALLBACK", exc)
    bpy.ops.export_scene.gltf(**kw)
    return "none"


def main():
    job = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
    spec = job["spec"]
    spec = detail_engine.expand(spec, bool((job.get("engine") or {}).get("detail_engine_v2")))
    out = job["out"]
    budgets = spec["budgets"]
    errors, warnings = [], []
    t0 = time.time()
    clear_scene()
    bpy.context.scene.unit_settings.system = "METRIC"

    mats = {}
    for name, m in spec["materials"].items():
        tex = job.get("textures", {}).get(name)
        mats[name] = make_material(f"L3D_{name}", m["srgb"], m.get("roughness", 0.75),
                                   tex["file"] if tex else None, tex.get("world_size_m", 3.0) if tex else 3.0,
                                   tint=(tex.get("tint") if tex else "material"), metallic=m.get("metallic", 0.0))
    for name, tex in job.get("textures", {}).items():
        if name not in mats:                       # texture for a material the spec does not define yet
            mats[name] = make_material(f"L3D_{name}", (255, 255, 255), 0.75, tex["file"], tex.get("world_size_m", 3.0),
                                       tint=tex.get("tint"))
    if "opening" not in mats:
        mats["opening"] = make_material("L3D_opening", (28, 32, 36), 0.9)
    if "trim" not in mats:
        mats["trim"] = make_material("L3D_trim", (236, 232, 222), 0.7)

    main = [tuple(p) for p in spec["footprint"]["local"]]
    tri_budget = int(budgets.get("triangles_target", budgets["triangles_max"]))
    meshes = []
    for part in spec["parts"]:
        if part.get("enabled", True) is False:
            continue
        poly = geom.footprint_polygon(part["footprint"], main)
        gm = geom.Mesh()
        top = part["base_z"] + part["height"]
        geom.build_part(poly, part["base_z"], top, part["roof"], gm, part.get("taper"))
        meshes.append((part, poly, gm))
    base_tris = sum(g.triangles() for _, _, g in meshes)
    left = max(0, tri_budget - base_tris)
    openings_total = 0
    for part, poly, gm in meshes:
        share = int(left * (geom.area(poly) / max(1e-6, sum(geom.area(p) for _, p, _ in meshes))))
        detail = geom.facade_detail(poly, part["base_z"], part["base_z"] + part["height"], part.get("facade"), gm,
                                    share // 2, part.get("taper"))
        openings_total += geom.facade_openings(poly, part["base_z"], part["base_z"] + part["height"],
                                               part.get("facade"), gm, max(0, share - detail), part.get("taper"))
    # Details share what is left of the whole budget: a small part such as a tower is often the most ornate one.
    feature_left = max(0, tri_budget - sum(g.triangles() for _, _, g in meshes))
    for part, poly, gm in meshes:
        feature_left -= geom.features(poly, part["base_z"], part["base_z"] + part["height"], part.get("taper"),
                                      part.get("features"), gm, max(0, feature_left))
        pm = part.get("material", {})
        tag_mat = {"wall": pm.get("wall", "wall"), "roof": pm.get("roof", "roof"), "trim": pm.get("trim", "trim"),
                   "opening": "opening", "accent": pm.get("accent", pm.get("trim", "trim"))}
        for k, v in list(tag_mat.items()):
            if v not in mats:
                fallback = {"wall": "wall", "roof": "roof", "trim": "trim"}.get(k, "trim")
                tag_mat[k] = fallback if fallback in mats else next(iter(mats))
                warnings.append(f"part {part['id']}: material '{v}' unknown, used '{tag_mat[k]}'")
        covered = [pn for pn in job.get("panels") or [] if pn.get("part") == part["id"] and pn.get("edge_index") is not None]
        if covered:                                   # a real photo shows this face: drop the generated detail on it
            ring = geom.ensure_ccw(poly)
            z_lo, z_hi = part["base_z"], part["base_z"] + part["height"]
            geom.clear_covered_faces(gm, [(ring[int(pn["edge_index"]) % len(ring)],
                                           ring[(int(pn["edge_index"]) + 1) % len(ring)],
                                           pn["z0"] if pn.get("z0") is not None else z_lo,
                                           pn["z1"] if pn.get("z1") is not None else z_hi) for pn in covered])
        for t in set(gm.tags):                        # feature tags name their material directly ("m:" or "d:" + name)
            if t.startswith(("m:", "d:")):
                tag_mat[t] = t[2:] if t[2:] in mats else tag_mat["trim"]
                if t[2:] not in mats:
                    warnings.append(f"part {part['id']}: feature material '{t[2:]}' unknown, used trim")
        ob = link_mesh(f"L3D_{part['id']}", gm, tag_mat, mats)
        planar_uvs(ob)
        ob["l3d_part"] = part["id"]

    panels_made = add_panels(job, meshes, warnings)
    acc_parts = [detail_accounting.PartMesh(part["id"], gm) for part, _, gm in meshes]
    accounting = detail_accounting.summarize(acc_parts, budgets.get("triangles_target"), budgets.get("triangles_max"))
    acc_errors = detail_accounting.check(acc_parts, accounting)
    errors += acc_errors

    tris = triangle_count()
    xs = [v[0] for _, _, g in meshes for v in g.verts] or [0]
    ys = [v[1] for _, _, g in meshes for v in g.verts] or [0]
    zs = [v[2] for _, _, g in meshes for v in g.verts] or [0]
    bounds = ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))
    part_bounds = {}
    for part, _, g in meshes:                        # close-up photos frame one part
        if g.verts:
            px, py, pz = zip(*g.verts)
            part_bounds[part["id"]] = ((min(px), min(py), min(pz)), (max(px), max(py), max(pz)))
    os.makedirs(out["renders"], exist_ok=True)
    renders, engine = render_views(out["renders"], bounds, job.get("views", ["front_south", "oblique_sw", "oblique_ne", "aerial", "plan"]),
                                   job.get("custom_views"), job.get("resolution", (1280, 800)), part_bounds)
    if job.get("preview"):                           # photo-review preview: renders only, no export
        json.dump({"ok": True, "renders": renders, "triangles": triangle_count(), "bounds": bounds},
                  open(out["result"], "w", encoding="utf-8"), indent=2)
        print("L3D_PREVIEW", len(renders))
        return
    os.makedirs(os.path.dirname(out["glb"]), exist_ok=True)
    comp = export(out["glb"], budgets.get("compression", "draco"))
    size = os.path.getsize(out["glb"])
    os.makedirs(os.path.dirname(out["blend"]), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=out["blend"])
    used = sorted({m.name for ob in bpy.context.scene.objects if ob.type == "MESH" for m in ob.data.materials if m})
    if tris >= budgets["triangles_max"]:
        errors.append(f"triangles {tris} >= limit {budgets['triangles_max']}")
    if size >= budgets["bytes_max"]:
        errors.append(f"GLB {size} bytes >= limit {budgets['bytes_max']}")
    if len(used) > budgets.get("materials_max", 10):
        errors.append(f"materials {len(used)} > limit {budgets['materials_max']}")
    if budgets.get("compression") == "draco" and comp != "draco":
        warnings.append("Draco not available; exported without compression")
    result = {"ok": not errors, "errors": errors, "warnings": warnings, "triangles": tris, "bytes": size,
              "materials": len(used), "material_names": used, "openings": openings_total, "compression": comp,
              "panels": panels_made, "textured_materials": sorted(job.get("textures", {}).keys()),
              "renders": renders, "render_engine": engine, "bounds": bounds, "seconds": round(time.time() - t0, 1),
              "accounting": accounting, "engine": job.get("engine") or {}}
    json.dump(result, open(out["result"], "w", encoding="utf-8"), indent=2)
    print("L3D_RESULT", json.dumps({k: result[k] for k in ("ok", "triangles", "bytes", "materials")}))


if __name__ == "__main__":
    main()
