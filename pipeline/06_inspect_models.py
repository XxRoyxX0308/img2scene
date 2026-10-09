"""Quality check: render every TRELLIS model alone from the front (-Y) and from behind at 3/4, textured, flat lit.

usage: blender -b -P 06_inspect_models.py -- <project_dir> [object_id ...]
Writes work/inspect/<id>.png (front | back-3/4 side by side). Uses Workbench, so it needs almost no VRAM.
"""
import json
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
PROJECT, ONLY = argv[0], set(argv[1:])
scene_cfg = json.load(open(os.path.join(PROJECT, "scene.json"), encoding="utf-8"))
out_dir = os.path.join(PROJECT, "work", "inspect")
os.makedirs(out_dir, exist_ok=True)

for cfg in scene_cfg["objects"]:
    glb = os.path.join(PROJECT, "work", "objects", cfg["id"], "model.glb")
    if (ONLY and cfg["id"] not in ONLY) or not os.path.exists(glb):
        continue
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    bpy.ops.import_scene.gltf(filepath=glb)
    pts = []
    faces = 0
    for o in scene.objects:
        if o.parent is None:  # preview the model_yaw_deg correction from scene.json
            o.rotation_mode = 'XYZ'
            o.rotation_euler.z += math.radians(cfg.get("model_yaw_deg", 0.0))
    bpy.context.view_layer.update()
    for o in scene.objects:
        if o.type == 'MESH':
            pts += [o.matrix_world @ Vector(c) for c in o.bound_box]
            faces += len(o.data.polygons)
    lo = Vector(np.min(pts, axis=0).tolist())
    hi = Vector(np.max(pts, axis=0).tolist())
    center, size = (lo + hi) / 2, (hi - lo)
    print(f"INSPECT {cfg['id']}: dims x={size.x:.3f} y={size.y:.3f} z={size.z:.3f} faces={faces}")

    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light = 'FLAT'
    scene.display.shading.color_type = 'TEXTURE'
    scene.render.resolution_x = scene.render.resolution_y = 640
    world = bpy.data.worlds.new("w")
    world.color = (0.25, 0.25, 0.25)
    scene.world = world
    cam_data = bpy.data.cameras.new("c")
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = max(size) * 1.15
    cam = bpy.data.objects.new("c", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    paths = []
    for name, direction in (("front", Vector((0, -1, 0.15))), ("back34", Vector((0.7, 0.8, 0.35)))):
        cam.location = center + direction.normalized() * 5
        cam.rotation_euler = (center - cam.location).to_track_quat('-Z', 'Y').to_euler()
        scene.render.filepath = os.path.join(out_dir, f"_{cfg['id']}_{name}.png")
        bpy.ops.render.render(write_still=True)
        paths.append(scene.render.filepath)
    # stitch side by side
    imgs = [bpy.data.images.load(p) for p in paths]
    w, h = imgs[0].size
    buf = np.zeros((h, w * 2, 4), np.float32)
    for i, im in enumerate(imgs):
        px = np.empty(w * h * 4, np.float32)
        im.pixels.foreach_get(px)
        buf[:, i * w:(i + 1) * w] = px.reshape(h, w, 4)
    out = bpy.data.images.new("sheet", w * 2, h)
    out.pixels.foreach_set(buf.ravel())
    out.filepath_raw = os.path.join(out_dir, f"{cfg['id']}.png")
    out.file_format = 'PNG'
    out.save()
    for p in paths:
        os.remove(p)
