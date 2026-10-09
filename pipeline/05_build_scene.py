"""Step 4: assemble the Blender scene from scene.json + per-object GLBs.

usage: blender -b -P 05_build_scene.py -- <project_dir> [--preview]

Placement: every object has a 2D bbox and an estimated depth. The bbox centre is back-projected through
the reference camera to that depth, and the model is scaled so its silhouette matches the bbox size.
World axes: X right, Y away from camera, Z up. Floor is z = 0.
"""
import json
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scene_surfaces

PROJECT = sys.argv[sys.argv.index("--") + 1]
PREVIEW = "--preview" in sys.argv   # Workbench renders (no lighting/fog, almost no VRAM) and no .blend save
scene_cfg = json.load(open(os.path.join(PROJECT, "scene.json"), encoding="utf-8"))
IMG_W, IMG_H = scene_cfg["image_size"]
env = scene_cfg["environment"]
STAGE_R, STAGE_H = env["stage"]["radius_m"], env["stage"]["height_m"]
SURFACES = env.get("surfaces", {})   # per-surface material spec, see scene_surfaces.py

# ---------------------------------------------------------------- reset
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
col_models = bpy.data.collections.new("Models_TRELLIS")
col_env = bpy.data.collections.new("Environment")
col_props = bpy.data.collections.new("Props_Procedural")
col_light = bpy.data.collections.new("Lights_Camera")
col_scatter = bpy.data.collections.new("Scatter")
for c in (col_models, col_env, col_props, col_scatter, col_light):
    scene.collection.children.link(c)


def move_to(obj, col):
    for c in obj.users_collection:
        c.objects.unlink(obj)
    col.objects.link(obj)


def material(name, color, rough=0.6, emission=0.0, metallic=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metallic
    if emission:
        b.inputs["Emission Color"].default_value = (*color, 1)
        b.inputs["Emission Strength"].default_value = emission
    return m


# ---------------------------------------------------------------- camera model
cam_cfg = scene_cfg["camera"]
HFOV = math.radians(cam_cfg["hfov_deg"])
FOCAL = (IMG_W / 2) / math.tan(HFOV / 2)       # focal length in image pixels
CAM_POS = Vector((0, 0, cam_cfg["height_m"]))
objects_by_id = {o["id"]: o for o in scene_cfg["objects"]}
anchor = objects_by_id[env["anchor"]]


def tight_bbox(obj_cfg):
    p = os.path.join(PROJECT, "work", "objects", obj_cfg["id"], "meta.json")
    return json.load(open(p))["tight_bbox"] if os.path.exists(p) else obj_cfg["bbox"]


def solve_pitch():
    """Pitch so that the anchor's feet (bbox bottom, at its depth) land on the stage top."""
    v = tight_bbox(anchor)[3]
    y_up = -(v - IMG_H / 2) / FOCAL
    target = (STAGE_H - CAM_POS.z) / anchor["depth_m"]
    best = min((abs(y_up * math.cos(t) + math.sin(t) - target), t) for t in np.linspace(-0.6, 0.6, 4801))
    return best[1]


PITCH = solve_pitch()
CAM_RIGHT = Vector((1, 0, 0))
CAM_FWD = Vector((0, math.cos(PITCH), math.sin(PITCH)))
CAM_UP = Vector((0, -math.sin(PITCH), math.cos(PITCH)))


def unproject(u, v, depth):
    x = (u - IMG_W / 2) / FOCAL
    y = -(v - IMG_H / 2) / FOCAL
    return CAM_POS + depth * (x * CAM_RIGHT + y * CAM_UP + CAM_FWD)


def px_to_m(px, depth):
    return px / FOCAL * depth


ANCHOR_BB = tight_bbox(anchor)
ANCHOR_XY = unproject((ANCHOR_BB[0] + ANCHOR_BB[2]) / 2, ANCHOR_BB[3], anchor["depth_m"]).xy
print(f"camera pitch {math.degrees(PITCH):.2f} deg, focal {FOCAL:.0f}px, anchor xy {tuple(round(c, 2) for c in ANCHOR_XY)}")


def support_height(xy):
    return STAGE_H if (Vector(xy) - ANCHOR_XY).length < STAGE_R else 0.0


# ---------------------------------------------------------------- environment ("terrain")
def add_box(name, center, size, mat, col, rot=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center, rotation=rot)
    o = bpy.context.active_object
    o.name = name
    o.scale = size
    o.data.materials.append(mat)
    move_to(o, col)
    return o


def build_environment():
    hall = env["hall"]
    def surf(name, fallback):
        return scene_surfaces.surface_material(name, SURFACES.get(name), fallback, PROJECT)

    floor_mat = surf("floor", (0.03, 0.035, 0.05))
    bpy.ops.mesh.primitive_plane_add(size=80, location=(ANCHOR_XY.x, ANCHOR_XY.y, 0))
    o = bpy.context.active_object
    o.name = "Floor"
    o.data.materials.append(floor_mat)
    move_to(o, col_env)

    bpy.ops.mesh.primitive_cylinder_add(vertices=96, radius=STAGE_R, depth=STAGE_H, location=(ANCHOR_XY.x, ANCHOR_XY.y, STAGE_H / 2))
    o = bpy.context.active_object
    o.name = "Stage"
    o.data.materials.append(surf("stage", (0.3, 0.33, 0.42)))
    bpy.ops.object.shade_auto_smooth()
    move_to(o, col_env)

    bpy.ops.mesh.primitive_cylinder_add(vertices=128, radius=hall["radius_m"], depth=hall["height_m"], end_fill_type='NGON',
                                        location=(ANCHOR_XY.x, ANCHOR_XY.y, hall["height_m"] / 2))
    o = bpy.context.active_object
    o.name = "Hall_Wall"
    o.data.materials.append(surf("hall_wall", (0.08, 0.09, 0.14)))
    # open the camera side so the doorway view is not blocked; drop the bottom cap (floor plane is used instead)
    me = o.data
    doomed = [p.index for p in me.polygons
              if p.normal.z < -0.5 or (abs(p.normal.z) < 0.5 and p.center.y < -hall["radius_m"] * 0.75)]
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.faces[i] for i in doomed], context='FACES')
    bm.to_mesh(me)
    bm.free()
    bpy.ops.object.shade_auto_smooth()
    move_to(o, col_env)

    ring = env["balcony_ring"]
    bpy.ops.mesh.primitive_cylinder_add(vertices=128, radius=ring["radius_m"], depth=ring["band_m"], end_fill_type='NOTHING',
                                        location=(ANCHOR_XY.x, ANCHOR_XY.y, ring["z_m"]))
    o = bpy.context.active_object
    o.name = "Balcony_Ring"
    o.data.materials.append(surf("balcony", (0.6, 0.63, 0.7)))
    mod = o.modifiers.new("Solidify", 'SOLIDIFY')
    mod.thickness = 0.8
    mod.offset = 1
    bpy.ops.object.shade_auto_smooth()
    move_to(o, col_env)


def build_props():
    support_mat = material("support_dark", (0.05, 0.055, 0.08), rough=0.7)
    for p in scene_cfg.get("props", []):
        u1, v1, u2, v2 = p["bbox"]
        d = p["depth_m"]
        w, h = px_to_m(u2 - u1, d), px_to_m(v2 - v1, d)
        c = unproject((u1 + u2) / 2, (v1 + v2) / 2, d)
        if p.get("grounded"):
            c.z = support_height(c.xy) + h / 2
        mat = material(p["id"], p["color"], emission=p.get("emission", 0.0))
        add_box(p["id"], c, (w, p["thick_m"], h), mat, col_props, rot=(0, math.radians(p.get("roll_deg", 0)), 0))
        if p.get("grid"):
            cols, rows = p["grid"]
            bar = material(p["id"] + "_bars", (0.02, 0.02, 0.03))
            for i in range(cols + 1):
                add_box(f"{p['id']}_v{i}", c + Vector((-w / 2 + w * i / cols, -0.08, 0)), (0.08, 0.1, h), bar, col_props)
            for j in range(rows + 1):
                add_box(f"{p['id']}_h{j}", c + Vector((0, -0.08, -h / 2 + h * j / rows)), (w, 0.1, 0.08), bar, col_props)
        if p.get("support"):
            add_support(p["id"], c, w, p["thick_m"], c.z - h / 2, support_mat)


def add_support(name, center, w, depth, top_z, mat):
    base = support_height(center.xy)
    if top_z - base > 0.05:
        add_box(f"{name}_support", (center.x, center.y, (top_z + base) / 2), (w * 1.15, depth * 1.15, top_z - base), mat, col_props)


# ---------------------------------------------------------------- TRELLIS models
def import_glb(path, name, model_yaw_deg=0.0):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == 'MESH']
    bpy.ops.object.select_all(action='DESELECT')
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.parent_clear(type='CLEAR_KEEP_TRANSFORM')
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for o in new:
        if o.type != 'MESH' and o.name in bpy.data.objects:
            bpy.data.objects.remove(o)
    # models generated from an angled view are turned back so their front faces -Y before measuring
    if model_yaw_deg:
        obj.data.transform(Matrix.Rotation(math.radians(model_yaw_deg), 4, 'Z'))
    # origin -> bottom centre, so "put it on the floor" is just a location
    n = len(obj.data.vertices)
    co = np.empty(n * 3, np.float32)
    obj.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    lo, hi = co.min(0), co.max(0)
    obj.data.transform(Matrix.Translation((-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2])))
    obj.name = name
    move_to(obj, col_models)
    return obj, Vector((hi - lo).tolist())


def build_image_slab(cfg):
    """Text signs: TRELLIS garbles lettering, so the sign is built from its clean front-view image instead.
    Front face = heightfield mesh textured with the image, letters (pixels unlike the board colour) raised as
    real relief; a box behind it gives the board its thickness. Returns (object, dims), origin bottom centre,
    front facing -Y, width normalised to 1."""
    path = os.path.join(PROJECT, "work", "objects", cfg["id"], cfg.get("slab_image", "rgba.png"))
    img = bpy.data.images.load(path)
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)                       # row 0 = bottom
    ys, xs = np.where(px[:, :, 3] > 0.5)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    rgb = px[y0:y1, x0:x1, :3]
    aspect = (y1 - y0) / (x1 - x0)
    board = np.median(rgb.reshape(-1, 3), axis=0)
    mask = np.clip((np.linalg.norm(rgb - board, axis=2) - 0.16) / 0.12, 0, 1)
    edge = max(2, int(0.012 * (x1 - x0)))          # keep the rim flat so the relief meets the board cleanly
    mask[:edge] = mask[-edge:] = 0
    mask[:, :edge] = mask[:, -edge:] = 0
    for _ in range(3):                             # cheap blur -> soft bevel on the letters
        mask = (mask + np.roll(mask, 1, 0) + np.roll(mask, -1, 0) + np.roll(mask, 1, 1) + np.roll(mask, -1, 1)) / 5

    nx = 420
    nz = max(8, int(nx * aspect))
    gx, gz = np.meshgrid(np.linspace(0, 1, nx + 1), np.linspace(0, 1, nz + 1))
    sx = np.clip((gx * (x1 - x0 - 1)).astype(int), 0, x1 - x0 - 1)
    sz = np.clip((gz * (y1 - y0 - 1)).astype(int), 0, y1 - y0 - 1)
    thick, relief = cfg.get("slab_thickness", 0.06), cfg.get("slab_relief", 0.02)
    verts = np.stack([gx - 0.5, -thick / 2 - mask[sz, sx] * relief, gz * aspect], axis=-1).reshape(-1, 3)
    idx = np.arange((nx + 1) * (nz + 1)).reshape(nz + 1, nx + 1)
    faces = np.stack([idx[:-1, :-1], idx[:-1, 1:], idx[1:, 1:], idx[1:, :-1]], axis=-1).reshape(-1, 4)
    me = bpy.data.meshes.new(cfg["id"])
    me.from_pydata(verts.tolist(), [], faces.tolist())
    uv = me.uv_layers.new(name="UVMap")
    u = (x0 + gx * (x1 - x0)) / w
    v = (y0 + gz * (y1 - y0)) / h
    uvs = np.stack([u, v], axis=-1).reshape(-1, 2)
    loops = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", loops)
    uv.data.foreach_set("uv", uvs[loops].ravel())
    me.polygons.foreach_set("use_smooth", np.ones(len(me.polygons), bool))
    front = bpy.data.objects.new(cfg["id"], me)
    col_models.objects.link(front)

    mat = bpy.data.materials.new(cfg["id"] + "_face")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = img
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.55
    me.materials.append(mat)

    body = add_box(cfg["id"] + "_board", (0, 0.002, aspect / 2), (1, thick, aspect),
                   material(cfg["id"] + "_board", [float(c) for c in board], rough=0.55), col_models)
    bpy.ops.object.select_all(action='DESELECT')
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    front.select_set(True)
    bpy.context.view_layer.objects.active = front
    bpy.ops.object.join()
    front.name = cfg["id"]
    return front, Vector((1.0, thick + relief, aspect))


def place_model(obj, dims, bbox, depth, grounded, yaw_deg, support, name, support_mat, fit="dominant", roll_deg=0.0):
    u1, v1, u2, v2 = bbox
    w, h = px_to_m(u2 - u1, depth), px_to_m(v2 - v1, depth)
    if fit == "inside":      # whole model inside the bbox (for objects whose pose differs from the image)
        s = min(w / dims.x, h / dims.z)
    else:                    # match the dominant silhouette dimension
        s = w / dims.x if (u2 - u1) > (v2 - v1) else h / dims.z
    c = unproject((u1 + u2) / 2, (v1 + v2) / 2, depth)
    z = support_height(c.xy) if grounded else c.z - s * dims.z / 2
    obj.location = (c.x, c.y, z)
    obj.scale = (s, s, s)
    obj.rotation_euler = (0, math.radians(roll_deg), math.radians(yaw_deg))
    if support:
        add_support(name, c, s * dims.x, s * dims.y, z, support_mat)
    print(f"placed {name}: loc=({c.x:.2f},{c.y:.2f},{z:.2f}) size={s * dims.x:.2f}x{s * dims.y:.2f}x{s * dims.z:.2f}m")


def build_models():
    support_mat = material("model_support", (0.05, 0.055, 0.08), rough=0.7)
    for cfg in scene_cfg["objects"]:
        glb = os.path.join(PROJECT, "work", "objects", cfg["id"], "model.glb")
        if cfg.get("mode") == "image_slab":
            obj, dims = build_image_slab(cfg)
        elif cfg.get("status") == "rejected" or not os.path.exists(glb):
            print(f"no model for {cfg['id']}, skipped")
            continue
        else:
            obj, dims = import_glb(glb, cfg["id"], cfg.get("model_yaw_deg", 0.0))
        place_model(obj, dims, tight_bbox(cfg), cfg["depth_m"], cfg.get("grounded", False), cfg.get("yaw_deg", 0),
                    cfg.get("support", False), cfg["id"], support_mat, cfg.get("fit", "dominant"), cfg.get("roll_deg", 0.0))
        # repeated objects are linked duplicates of the same mesh
        for i, inst in enumerate(cfg.get("instances", [])):
            dup = obj.copy()
            dup.name = f"{cfg['id']}_inst{i + 1}"
            col_models.objects.link(dup)
            place_model(dup, dims, inst["bbox"], inst["depth_m"], inst.get("grounded", False), inst.get("yaw_deg", 0),
                        inst.get("support", False), dup.name, support_mat, inst.get("fit", cfg.get("fit", "dominant")),
                        inst.get("roll_deg", 0.0))


# ---------------------------------------------------------------- camera, lights, fog
def look_at(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat('-Z', 'Y').to_euler()


def build_camera_and_lights():
    cam_data = bpy.data.cameras.new("RefCamera")
    cam_data.sensor_fit = 'HORIZONTAL'
    cam_data.angle_x = HFOV
    cam_data.clip_end = 200
    cam = bpy.data.objects.new("RefCamera", cam_data)
    cam.location = CAM_POS
    cam.rotation_euler = (math.pi / 2 + PITCH, 0, 0)
    col_light.objects.link(cam)
    scene.camera = cam
    # the reference image as a camera background, for manual fine-tuning in the viewport
    try:
        cam_data.show_background_images = True
        bg = cam_data.background_images.new()
        bg.image = bpy.data.images.load(os.path.join(PROJECT, scene_cfg["image"]))
        bg.alpha = 0.5
    except Exception as e:
        print("background image not set:", e)

    over_data = bpy.data.cameras.new("OverviewCamera")
    over_data.lens = 20
    over = bpy.data.objects.new("OverviewCamera", over_data)
    over.location = (ANCHOR_XY.x - 4.8, ANCHOR_XY.y - 7.0, 2.6)
    look_at(over, (ANCHOR_XY.x + 0.6, ANCHOR_XY.y + 1.0, 2.6))
    col_light.objects.link(over)

    target = Vector((ANCHOR_XY.x, ANCHOR_XY.y, 3.0))
    lc = scene_cfg["lights"]
    k = lc["key_spot"]
    key = bpy.data.lights.new("Key_Spot", 'SPOT')
    key.energy, key.color = k["energy_w"], k["color"]
    key.spot_size, key.spot_blend = math.radians(k["cone_deg"]), 0.5
    key.shadow_soft_size = 0.3
    ko = bpy.data.objects.new("Key_Spot", key)
    ko.location = target + Vector(k["offset_from_anchor"]) - Vector((0, 0, 3.0))
    look_at(ko, target)
    col_light.objects.link(ko)

    fill = bpy.data.lights.new("Fill_Front", 'AREA')
    fill.energy, fill.color, fill.size = 450, (0.45, 0.55, 1.0), 6
    fo = bpy.data.objects.new("Fill_Front", fill)
    fo.location = (ANCHOR_XY.x - 3, ANCHOR_XY.y - 7, 4)
    look_at(fo, target)
    col_light.objects.link(fo)

    sign = bpy.data.objects.get("sign")
    if sign:
        sl = bpy.data.lights.new("Sign_Light", 'AREA')
        sl.energy, sl.color, sl.size = 120, (1.0, 0.93, 0.75), 3
        so = bpy.data.objects.new("Sign_Light", sl)
        so.location = sign.location + Vector((0, -3.0, 0.5))
        look_at(so, sign.location + Vector((0, 0, 0.6)))
        col_light.objects.link(so)

    # each stage-light model also gets a real (weak) spot so its beam shows in the fog
    for o in list(col_models.objects):
        if o.name.startswith("spotlight"):
            sp = bpy.data.lights.new(o.name + "_beam", 'SPOT')
            sp.energy, sp.color = 2500, (1.0, 0.95, 0.85)
            sp.spot_size, sp.spot_blend = math.radians(38), 0.6
            so = bpy.data.objects.new(o.name + "_beam", sp)
            so.location = o.location + Vector((0, -0.25, 0.1))
            look_at(so, so.location + Vector((0, -0.8, -1.0)))
            col_light.objects.link(so)

    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.008, 0.012, 0.03, 1)
    scene.world = world

    # fog volume for the light shafts
    hall = env["hall"]
    fog = add_box("Fog_Volume", (ANCHOR_XY.x, ANCHOR_XY.y - 2, hall["height_m"] / 2),
                  (hall["radius_m"] * 2.2, hall["radius_m"] * 2.6, hall["height_m"]), None, col_env)
    fm = bpy.data.materials.new("fog")
    fm.use_nodes = True
    nt = fm.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    vol = nt.nodes.new("ShaderNodeVolumeScatter")
    vol.inputs["Density"].default_value = lc["fog_density"]
    vol.inputs["Anisotropy"].default_value = 0.4
    vol.inputs["Color"].default_value = (0.75, 0.82, 1.0, 1)
    nt.links.new(vol.outputs[0], out.inputs["Volume"])
    fog.data.materials.clear()
    fog.data.materials.append(fm)
    fog.display_type = 'WIRE'
    return cam, over


def setup_render():
    if PREVIEW:
        scene.render.engine = 'BLENDER_WORKBENCH'
        scene.display.shading.light = 'STUDIO'
        scene.display.shading.color_type = 'TEXTURE'
        scene.render.resolution_x, scene.render.resolution_y = IMG_W * 2, IMG_H * 2
        fog = bpy.data.objects.get("Fog_Volume")
        if fog:
            fog.hide_render = True
        return
    for engine in ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    scene.render.resolution_x, scene.render.resolution_y = IMG_W * 2, IMG_H * 2
    scene.render.film_transparent = False
    for attr, val in (("use_raytracing", True), ("volumetric_tile_size", '4'), ("volumetric_samples", 96), ("taa_render_samples", 48)):
        try:
            setattr(scene.eevee, attr, val)
        except Exception:
            pass
    try:
        scene.view_settings.look = 'AgX - Medium High Contrast'
    except Exception:
        pass


def render(cam, name):
    scene.camera = cam
    scene.render.filepath = os.path.join(PROJECT, "work", "renders", name)
    bpy.ops.render.render(write_still=True)


build_environment()
build_props()
build_models()
# small clutter goes in last so it can avoid everything that is already standing on the floor
scene_surfaces.scatter(scene_cfg.get("scatter"), col_scatter, ANCHOR_XY, support_height,
                       (col_models, col_props), env["hall"]["radius_m"])
ref_cam, over_cam = build_camera_and_lights()
setup_render()
os.makedirs(os.path.join(PROJECT, "work", "renders"), exist_ok=True)
prefix = "preview_" if PREVIEW else "view_"
render(ref_cam, prefix + "ref.png")
render(over_cam, prefix + "overview.png")
scene.camera = ref_cam
if not PREVIEW:
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(PROJECT, "scene.blend"))
    print("saved scene.blend")
