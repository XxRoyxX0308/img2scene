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
ENV_TYPE = env.get("type", "round_hall")   # "round_hall": circular lobby with a stage; "ruin_hall": walls traced from the image
STAGE_R, STAGE_H = (env["stage"]["radius_m"], env["stage"]["height_m"]) if "stage" in env else (0.0, 0.0)
HALL_R = env["hall"]["radius_m"] if "hall" in env else env.get("scatter_limit_m", 30.0)
# renders default to 2x the image; large images set render_width instead
RENDER_W = scene_cfg.get("render_width", IMG_W * 2)
RENDER_H = round(RENDER_W * IMG_H / IMG_W)
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


def pixel_dir(u, v):
    return ((u - IMG_W / 2) / FOCAL) * CAM_RIGHT + (-(v - IMG_H / 2) / FOCAL) * CAM_UP + CAM_FWD


def ground_hit(u, v):
    """Where the view ray through pixel (u, v) meets the floor (z = 0)."""
    d = pixel_dir(u, v)
    return CAM_POS + (-CAM_POS.z / d.z) * d


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


WALL_PLANES = []   # (point on the wall face, normal pointing into the wall), filled by build_ruin_hall


def inside_hall(xy, margin=0.3):
    return all((Vector(xy) - a).dot(n) < -margin for a, n in WALL_PLANES)


def build_ruin_hall():
    """Rectangular hall whose walls are traced from the image: each wall is given by two pixels on its foot line,
    which are projected onto the floor. Windows and pilasters are given in pixels too and projected onto the wall."""
    def surf(name, fallback):
        return scene_surfaces.surface_material(name, SURFACES.get(name), fallback, PROJECT)

    bpy.ops.mesh.primitive_plane_add(size=120, location=(0, 20, 0))
    o = bpy.context.active_object
    o.name = "Floor"
    o.data.materials.append(surf("floor", (0.2, 0.2, 0.2)))
    move_to(o, col_env)

    wall_mat = surf("hall_wall", (0.45, 0.46, 0.45))
    trim_mat = surf("pilaster", SURFACES.get("hall_wall", {}).get("color", (0.4, 0.41, 0.4)))
    bar_mat = material("window_bars", (0.12, 0.07, 0.05), rough=0.8)
    cam_xy = CAM_POS.xy
    for w in env.get("walls", []):
        a, b = ground_hit(*w["base"][0]).xy, ground_hit(*w["base"][1]).xy
        t = (b - a).normalized()
        ext = w.get("extend_m", [0, 0])
        a, b = a - t * ext[0], b + t * ext[1]
        n = Vector((-t.y, t.x))
        if n.dot((a + b) / 2 - cam_xy) < 0:      # n points away from the camera, into the wall
            n = -n
        WALL_PLANES.append((a.copy(), n.copy()))
        h, thick, length = w["height_m"], w.get("thick_m", 0.5), (b - a).length
        ang = math.atan2(t.y, t.x)
        mid = (a + b) / 2 + n * thick / 2
        add_box(w["id"], (mid.x, mid.y, h / 2), (length, thick, h), wall_mat, col_env, rot=(0, 0, ang))
        print(f"wall {w['id']}: ({a.x:.1f},{a.y:.1f}) -> ({b.x:.1f},{b.y:.1f}) length {length:.1f}m")

        def on_wall(u, v):
            """(distance along the wall from a, height) of the point where pixel (u, v)'s ray meets the wall face."""
            d = pixel_dir(u, v)
            k = (a - cam_xy).dot(n) / d.xy.dot(n)
            q = CAM_POS + k * d
            return (q.xy - a).dot(t), q.z

        def wall_box(name, s0, s1, z0, z1, depth, mat, out=0.0):
            c = a + t * (s0 + s1) / 2 - n * (depth / 2 + out)
            return add_box(name, (c.x, c.y, (z0 + z1) / 2), (abs(s1 - s0), depth, abs(z1 - z0)), mat, col_env, rot=(0, 0, ang))

        for i, win in enumerate(w.get("windows", [])):
            (s0, z1), (s1, z0) = on_wall(win["bbox"][0], win["bbox"][1]), on_wall(win["bbox"][2], win["bbox"][3])
            s0, s1 = sorted((s0, s1))
            z0, z1 = max(0.05, min(z0, z1)), min(h - 0.05, max(z0, z1))
            glow = material(f"{w['id']}_win{i}", win.get("color", (0.75, 0.8, 0.82)), emission=win.get("emission", 2.0))
            wall_box(f"{w['id']}_win{i}", s0, s1, z0, z1, 0.04, glow)
            cols, rows = win.get("grid", [4, 5])
            for c in range(cols + 1):
                sc = s0 + (s1 - s0) * c / cols
                wall_box(f"{w['id']}_win{i}_v{c}", sc - 0.03, sc + 0.03, z0, z1, 0.06, bar_mat, out=0.04)
            for r in range(rows + 1):
                zr = z0 + (z1 - z0) * r / rows
                wall_box(f"{w['id']}_win{i}_h{r}", s0, s1, zr - 0.03, zr + 0.03, 0.06, bar_mat, out=0.04)
        for i, u in enumerate(w.get("pilasters", [])):
            sp, _ = on_wall(u, IMG_H / 2)
            pw = w.get("pilaster_w_m", 0.7)
            wall_box(f"{w['id']}_pil{i}", sp - pw / 2, sp + pw / 2, 0, h, w.get("pilaster_d_m", 0.35), trim_mat)
        for i, band in enumerate(w.get("bands", [])):      # horizontal ledges / beams along the wall, heights in metres
            wall_box(f"{w['id']}_band{i}", 0, length, band["z_m"], band["z_m"] + band.get("h_m", 0.5), band.get("d_m", 0.4),
                     trim_mat)

    roof = env.get("roof")
    if roof:
        x0, x1, y0, y1 = roof["x_m"][0], roof["x_m"][1], roof["y_m"][0], roof["y_m"][1]
        z, th = roof["z_m"], 0.3
        sx0, sx1, sy0, sy1 = roof.get("skylight_m", [0, 0, 0, 0])
        roof_mat = surf("roof", (0.18, 0.18, 0.17))
        # four slabs around the skylight opening
        for name, (ax0, ax1, ay0, ay1) in {"roof_left": (x0, sx0, y0, y1), "roof_right": (sx1, x1, y0, y1),
                                           "roof_front": (sx0, sx1, y0, sy0), "roof_back": (sx0, sx1, sy1, y1)}.items():
            if ax1 - ax0 > 0.01 and ay1 - ay0 > 0.01:
                add_box(name, ((ax0 + ax1) / 2, (ay0 + ay1) / 2, z + th / 2), (ax1 - ax0, ay1 - ay0, th), roof_mat, col_env)
        beam_mat = material("roof_beams", (0.16, 0.13, 0.11), rough=0.8)
        nb = roof.get("beams", 0)
        for i in range(nb):
            y = y0 + (y1 - y0) * (i + 0.5) / nb
            add_box(f"roof_beam{i}", ((x0 + x1) / 2, y, z - 0.35), (x1 - x0, 0.35, 0.7), beam_mat, col_env)


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


def place_model(obj, dims, bbox, depth, grounded, yaw_deg, support, name, support_mat, fit="dominant", roll_deg=0.0,
                pitch_deg=0.0, lift_m=0.0):
    u1, v1, u2, v2 = bbox
    w, h = px_to_m(u2 - u1, depth), px_to_m(v2 - v1, depth)
    if fit == "inside":      # whole model inside the bbox (for objects whose pose differs from the image)
        s = min(w / dims.x, h / dims.z)
    else:                    # match the dominant silhouette dimension
        s = w / dims.x if (u2 - u1) > (v2 - v1) else h / dims.z
    c = unproject((u1 + u2) / 2, (v1 + v2) / 2, depth)
    z = support_height(c.xy) + lift_m if grounded else c.z - s * dims.z / 2
    obj.location = (c.x, c.y, z)
    obj.scale = (s, s, s)
    obj.rotation_mode = 'XYZ'   # glTF imports come in as quaternion-rotated objects
    obj.rotation_euler = (math.radians(pitch_deg), math.radians(roll_deg), math.radians(yaw_deg))
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
                    cfg.get("support", False), cfg["id"], support_mat, cfg.get("fit", "dominant"), cfg.get("roll_deg", 0.0),
                    cfg.get("pitch_deg", 0.0), cfg.get("lift_m", 0.0))
        # repeated objects are linked duplicates of the same mesh
        for i, inst in enumerate(cfg.get("instances", [])):
            dup = obj.copy()
            dup.name = f"{cfg['id']}_inst{i + 1}"
            col_models.objects.link(dup)
            place_model(dup, dims, inst["bbox"], inst["depth_m"], inst.get("grounded", False), inst.get("yaw_deg", 0),
                        inst.get("support", False), dup.name, support_mat, inst.get("fit", cfg.get("fit", "dominant")),
                        inst.get("roll_deg", 0.0), inst.get("pitch_deg", 0.0), inst.get("lift_m", 0.0))


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
    if ENV_TYPE == "ruin_hall":
        ov = env.get("overview", {})
        over.location = ov.get("location", (ANCHOR_XY.x + 6.0, ANCHOR_XY.y - 5.0, 5.5))
        look_at(over, ov.get("target", (ANCHOR_XY.x + 3.0, ANCHOR_XY.y + 5.0, 1.0)))
        build_daylight()
        return cam, over

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


def build_daylight():
    """Overcast daylight for the ruin hall: bright sky world, a soft sun through the roof opening, light haze."""
    lc = scene_cfg["lights"]
    sky = lc.get("sky", {})
    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (*sky.get("color", (0.8, 0.86, 0.9)), 1)
    bg.inputs[1].default_value = sky.get("strength", 1.0)
    scene.world = world

    sun_cfg = lc.get("sun")
    if sun_cfg:
        sun = bpy.data.lights.new("Sun", 'SUN')
        sun.energy, sun.color = sun_cfg.get("strength", 3.0), sun_cfg.get("color", (1.0, 0.97, 0.9))
        sun.angle = math.radians(sun_cfg.get("soft_deg", 8))
        so = bpy.data.objects.new("Sun", sun)
        el, az = math.radians(sun_cfg.get("elevation_deg", 60)), math.radians(sun_cfg.get("azimuth_deg", 200))
        so.location = (0, 10, 30)
        # azimuth is the compass direction the light comes from (0 = from +Y, 90 = from +X)
        look_at(so, Vector(so.location) - Vector((math.cos(el) * math.sin(az), math.cos(el) * math.cos(az), math.sin(el))))
        col_light.objects.link(so)
    for i, a in enumerate(lc.get("area", [])):   # extra soft lights (window spill, fill), positions in world metres
        al = bpy.data.lights.new(f"Area_{i}", 'AREA')
        al.energy, al.color, al.size = a["energy_w"], a.get("color", (1, 1, 1)), a.get("size_m", 4)
        ao = bpy.data.objects.new(f"Area_{i}", al)
        ao.location = a["location"]
        look_at(ao, a["target"])
        col_light.objects.link(ao)

    if lc.get("fog_density", 0) > 0:
        roof = env.get("roof", {"x_m": [-12, 12], "y_m": [0, 30], "z_m": 12})
        x0, x1, y0, y1 = *roof["x_m"], *roof["y_m"]
        fog = add_box("Fog_Volume", ((x0 + x1) / 2, (y0 + y1) / 2, roof["z_m"] / 2), (x1 - x0, y1 - y0, roof["z_m"]), None, col_env)
        fm = bpy.data.materials.new("fog")
        fm.use_nodes = True
        nt = fm.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        vol = nt.nodes.new("ShaderNodeVolumeScatter")
        vol.inputs["Density"].default_value = lc["fog_density"]
        vol.inputs["Anisotropy"].default_value = 0.3
        vol.inputs["Color"].default_value = (0.9, 0.93, 0.95, 1)
        nt.links.new(vol.outputs[0], out.inputs["Volume"])
        fog.data.materials.clear()
        fog.data.materials.append(fm)
        fog.display_type = 'WIRE'


def setup_render():
    if PREVIEW:
        scene.render.engine = 'BLENDER_WORKBENCH'
        scene.display.shading.light = 'STUDIO'
        scene.display.shading.color_type = 'TEXTURE'
        scene.render.resolution_x, scene.render.resolution_y = RENDER_W, RENDER_H
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
    scene.render.resolution_x, scene.render.resolution_y = RENDER_W, RENDER_H
    scene.render.film_transparent = False
    for attr, val in (("use_raytracing", True), ("volumetric_tile_size", '4'), ("volumetric_samples", 96), ("taa_render_samples", 48)):
        try:
            setattr(scene.eevee, attr, val)
        except Exception:
            pass
    try:
        scene.view_settings.look = scene_cfg["lights"].get("look", 'AgX - Medium High Contrast')
        scene.view_settings.exposure = scene_cfg["lights"].get("exposure", 0.0)
    except Exception:
        pass


def render(cam, name):
    scene.camera = cam
    scene.render.filepath = os.path.join(PROJECT, "work", "renders", name)
    bpy.ops.render.render(write_still=True)


build_ruin_hall() if ENV_TYPE == "ruin_hall" else build_environment()
build_props()
build_models()
# small clutter goes in last so it can avoid everything that is already standing on the floor
scene_surfaces.scatter(scene_cfg.get("scatter"), col_scatter, ANCHOR_XY, support_height,
                       (col_models, col_props), HALL_R, allowed=inside_hall if WALL_PLANES else None)
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
