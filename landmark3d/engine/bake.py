"""Ambient occlusion baking per part (plan M5, step 5). Runs inside Blender, only with the bake_ao engine setting.

Each part gets its own AO image, baked on a dedicated UV layer (AO_UV) so the photo textures on UVMap are untouched.
Each material the part uses is copied for that part, and its base colour is multiplied by the AO image. The image is
packed, so the GLB carries it. Baking needs Cycles; if it fails the reason is returned and nothing is half-applied.
"""
import bpy

AO_UV = "AO_UV"


def _multiply_node(nt):
    """A multiply node for the running Blender (its name changed between releases)."""
    for name in ("ShaderNodeMix", "ShaderNodeMixRGB"):
        try:
            node = nt.nodes.new(name)
        except RuntimeError:
            continue
        if name == "ShaderNodeMix":
            node.data_type = "RGBA"
            node.blend_type = "MULTIPLY"
            return node, node.inputs[6], node.inputs[7], node.outputs[2]
        node.blend_type = "MULTIPLY"
        return node, node.inputs[1], node.inputs[2], node.outputs[0]
    raise RuntimeError("no multiply shader node in this Blender")


def bake_part(ob, part_id: str, size: int) -> dict:
    me = ob.data
    if not me.polygons:
        return {"part": part_id, "result": "skipped: no faces"}
    for o in bpy.context.selected_objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    prev_uv = me.uv_layers.active.name if me.uv_layers.active else None
    layer = me.uv_layers.get(AO_UV) or me.uv_layers.new(name=AO_UV)
    me.uv_layers.active = layer
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=1e-4)              # weld the separate pieces: an open shell leaks occlusion rays
    bpy.ops.mesh.normals_make_consistent(inside=False)       # faces must point outwards, or occlusion rays go inside
    bpy.ops.uv.smart_project(island_margin=0.02)
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.pack_islands(margin=0.01)                    # fill the atlas: most of it was empty after smart_project
    bpy.ops.object.mode_set(mode="OBJECT")

    img = bpy.data.images.new(f"AO_{part_id}", size, size)
    img.colorspace_settings.name = "Non-Color"      # occlusion is data, not colour: no sRGB decode
    new_mats = []
    for slot in me.materials:
        if slot is None:
            new_mats.append(None)
            continue
        m = slot.copy()
        m.name = f"{slot.name}__ao_{part_id}"
        nt = m.node_tree
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            new_mats.append(m)
            continue
        mul, a_in, b_in, out = _multiply_node(nt)
        base = bsdf.inputs["Base Color"]
        if base.is_linked:
            nt.links.new(base.links[0].from_socket, a_in)
        else:
            a_in.default_value = tuple(base.default_value)
        ao = nt.nodes.new("ShaderNodeTexImage")
        ao.image = img
        uvn = nt.nodes.new("ShaderNodeUVMap")
        uvn.uv_map = AO_UV
        nt.links.new(uvn.outputs["UV"], ao.inputs["Vector"])
        nt.links.new(ao.outputs["Color"], b_in)
        nt.links.new(out, base)
        nt.nodes.active = ao                     # the bake writes into the active image node
        new_mats.append(m)
    for i, m in enumerate(new_mats):                       # replace slot by slot: clear() resets every face to slot 0
        me.materials[i] = m

    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    try:
        bpy.ops.object.bake(type="AO", target="IMAGE_TEXTURES", margin=2)
    except RuntimeError as exc:
        _restore_uv(me, prev_uv)
        return {"part": part_id, "result": f"bake failed: {str(exc)[:200]}"}
    img.pack()
    _restore_uv(me, prev_uv)
    px = img.pixels[0::4]
    # texels no island covers stay 0: average only the covered ones, or the report reads dark for a lit building
    covered = [v for v in px if v > 0.0]
    cover = len(covered) / len(px) if len(px) else 0.0
    mean = sum(covered) / len(covered) if covered else 0.0
    return {"part": part_id, "result": "baked", "size": size, "image": img.name, "mean_ao": round(mean, 3),
            "coverage": round(cover, 3), "min_ao": round(min(covered), 3) if covered else 0.0, "max_ao": round(max(px), 3)}


NORMAL_UV = "NORMAL_UV"


def _restore_uv(me, name) -> None:
    """Put the photo UV back as the active one. Textures map through the active UV on export, so a bake that leaves
    AO_UV or NORMAL_UV active would move every photo texture onto the bake's layout."""
    if name and me.uv_layers.get(name):
        me.uv_layers.active = me.uv_layers[name]


def _decimate(ob, ratio: float) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    mod = ob.modifiers.new("l3d_decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    bpy.ops.object.modifier_apply(modifier=mod.name)


def bake_normal_part(high, part_id: str, size: int, ratio: float, extrude: float) -> tuple:
    """Bake a tangent-space normal map from the detailed part (high) onto a decimated copy (low).

    The low copy is what ships; the detail survives as the normal map. Returns (low_object, report). On failure the
    high object is returned unchanged and the report says why, so nothing is half-applied."""
    high_tris = len(high.data.polygons)
    low = high.copy()
    low.data = high.data.copy()
    low.name = f"{high.name}_low"
    bpy.context.collection.objects.link(low)
    try:
        _decimate(low, ratio)
        me = low.data
        prev_uv = me.uv_layers.active.name if me.uv_layers.active else None
        layer = me.uv_layers.get(NORMAL_UV) or me.uv_layers.new(name=NORMAL_UV)
        me.uv_layers.active = layer
        bpy.ops.object.select_all(action="DESELECT")
        low.select_set(True)
        bpy.context.view_layer.objects.active = low
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.normals_make_consistent(inside=False)
        bpy.ops.uv.smart_project(island_margin=0.02)
        bpy.ops.uv.select_all(action="SELECT")
        bpy.ops.uv.pack_islands(margin=0.01)
        bpy.ops.object.mode_set(mode="OBJECT")

        img = bpy.data.images.new(f"N_{part_id}", size, size)
        img.colorspace_settings.name = "Non-Color"          # normals are data: no sRGB decode
        new_mats = []
        for slot in me.materials:
            if slot is None:
                new_mats.append(None)
                continue
            m = slot.copy()
            m.name = f"{slot.name}__nrm_{part_id}"
            nt = m.node_tree
            bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
            if bsdf is None:
                new_mats.append(m)
                continue
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = img
            nmap = nt.nodes.new("ShaderNodeNormalMap")
            nmap.space = "TANGENT"
            nmap.uv_map = NORMAL_UV
            nt.links.new(tex.outputs["Color"], nmap.inputs["Color"])
            nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
            nt.nodes.active = tex                            # the bake writes into the active image node
            new_mats.append(m)
        for i, m in enumerate(new_mats):
            me.materials[i] = m

        bpy.ops.object.select_all(action="DESELECT")
        high.select_set(True)
        low.select_set(True)
        bpy.context.view_layer.objects.active = low          # the active object receives the bake
        bpy.ops.object.bake(type="NORMAL", target="IMAGE_TEXTURES", use_selected_to_active=True,
                            normal_space="TANGENT", cage_extrusion=extrude, margin=2)
        img.pack()
        _restore_uv(me, prev_uv)
    except RuntimeError as exc:
        bpy.data.objects.remove(low, do_unlink=True)
        return high, {"part": part_id, "result": f"normal bake failed: {str(exc)[:200]}"}
    px = img.pixels
    blue = px[2::4]
    covered = [v for v in blue if v > 0.0]
    report = {"part": part_id, "result": "baked", "size": size, "image": img.name, "ratio": ratio,
              "high_triangles": high_tris, "low_triangles": len(low.data.polygons),
              "blue_mean": round(sum(covered) / len(covered), 3) if covered else 0.0}
    high.name = f"{high.name}_high"
    bpy.data.objects.remove(high, do_unlink=True)            # the detailed copy is only the bake source
    low.name = low.name.replace("_low", "")
    return low, report


def bake_normals(part_objects: list, size: int, ratio: float, extrude: float) -> tuple:
    """Normal-bake every part. Returns (new part objects, report). Never raises."""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 32
    report = {"size": size, "ratio": ratio, "parts": []}
    out = []
    for part_id, ob in part_objects:
        if not ob.data.polygons:
            out.append((part_id, ob))
            report["parts"].append({"part": part_id, "result": "skipped: no faces"})
            continue
        low, rep = bake_normal_part(ob, part_id, size, ratio, extrude)
        out.append((part_id, low))
        report["parts"].append(rep)
    report["baked"] = sum(1 for p in report["parts"] if p["result"] == "baked")
    return out, report


def bake_parts(part_objects: list, size: int) -> dict:
    """Bake every (part_id, object) pair. Returns a report; never raises, so the build reports the reason instead."""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 32
    report = {"size": size, "parts": []}
    for part_id, ob in part_objects:
        try:
            report["parts"].append(bake_part(ob, part_id, size))
        except (RuntimeError, ValueError, AttributeError) as exc:
            report["parts"].append({"part": part_id, "result": f"failed: {str(exc)[:200]}"})
    report["baked"] = sum(1 for p in report["parts"] if p["result"] == "baked")
    return report
