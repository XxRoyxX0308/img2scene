"""Surface materials and scattered small objects for 05_build_scene.py (imported inside Blender).

surface_material(name, spec, fallback, project)  -> material
    spec = None                      plain colour (fallback)
    spec = {"preset": "tiles", "color": [r,g,b], "scale_m": 0.6, ...}     procedural shader nodes, no files
    spec = {"polyhaven": "<asset id>", "scale_m": 2.0, "tint": [r,g,b]}   PBR maps from work/textures/<id>/
                                                                           (download first with polyhaven.py)
    All mapping is in world metres (box projection), so scale_m is the real size of one tile / one texture repeat.

scatter(specs, ...)  -> instances of a few prototype meshes dropped on the floor, never inside other objects.
"""
import glob
import math
import os
import random

import bpy
import numpy as np
from mathutils import Vector

PRESETS = ("plain", "concrete", "tiles", "painted", "metal", "gravel", "wood")


def _base(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    geo = nt.nodes.new("ShaderNodeNewGeometry")          # world-space position -> sizes are real metres
    mapping = nt.nodes.new("ShaderNodeMapping")
    nt.links.new(geo.outputs["Position"], mapping.inputs["Vector"])
    return m, nt, bsdf, mapping


def _ramp(nt, c0, c1, p0=0.3, p1=0.7):
    r = nt.nodes.new("ShaderNodeValToRGB")
    r.color_ramp.elements[0].position, r.color_ramp.elements[1].position = p0, p1
    r.color_ramp.elements[0].color, r.color_ramp.elements[1].color = (*c0, 1), (*c1, 1)
    return r


def _bump(nt, bsdf, height_socket, strength, distance=0.02):
    b = nt.nodes.new("ShaderNodeBump")
    b.inputs["Strength"].default_value = strength
    b.inputs["Distance"].default_value = distance
    nt.links.new(height_socket, b.inputs["Height"])
    nt.links.new(b.outputs["Normal"], bsdf.inputs["Normal"])


def _shade(c, f):
    return tuple(min(1.0, max(0.0, v * f)) for v in c)


def surface_material(name, spec, fallback, project):
    spec = spec or {}
    color = tuple(spec.get("color", fallback))
    scale_m = float(spec.get("scale_m", 1.0))
    rough = spec.get("roughness")
    m, nt, bsdf, mapping = _base(name)
    L = nt.links.new

    if "polyhaven" in spec:
        folder = os.path.join(project, "work", "textures", spec["polyhaven"])

        def find(*keys):
            for k in keys:
                hits = sorted(glob.glob(os.path.join(folder, f"*{k}*")))
                if hits:
                    return hits[0]
            return None

        mapping.inputs["Scale"].default_value = (1 / scale_m,) * 3

        def tex(path, non_color):
            t = nt.nodes.new("ShaderNodeTexImage")
            t.image = bpy.data.images.load(path)
            t.projection, t.projection_blend = 'BOX', 0.25
            if non_color:
                t.image.colorspace_settings.name = 'Non-Color'
            L(mapping.outputs["Vector"], t.inputs["Vector"])
            return t

        diff, nor, rgh = find("diff", "col"), find("nor_gl", "nor"), find("rough")
        if not diff:
            raise FileNotFoundError(f"no Poly Haven maps in {folder}; run polyhaven.py get {spec['polyhaven']}")
        d = tex(diff, False)
        tint = nt.nodes.new("ShaderNodeMixRGB")
        tint.blend_type = 'MULTIPLY'
        tint.inputs["Fac"].default_value = 1.0
        tint.inputs["Color2"].default_value = (*spec.get("tint", (1, 1, 1)), 1)
        L(d.outputs["Color"], tint.inputs["Color1"])
        L(tint.outputs["Color"], bsdf.inputs["Base Color"])
        if rgh:
            L(tex(rgh, True).outputs["Color"], bsdf.inputs["Roughness"])
        if nor:
            nm = nt.nodes.new("ShaderNodeNormalMap")
            nm.inputs["Strength"].default_value = spec.get("normal_strength", 1.0)
            L(tex(nor, True).outputs["Color"], nm.inputs["Color"])
            L(nm.outputs["Normal"], bsdf.inputs["Normal"])
        if rough is not None:
            bsdf.inputs["Roughness"].default_value = rough
        return m

    preset = spec.get("preset", "plain")
    if preset not in PRESETS:
        raise ValueError(f"unknown surface preset {preset!r}; choose from {PRESETS}")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = 0.6 if rough is None else rough
    bsdf.inputs["Metallic"].default_value = spec.get("metallic", 0.0)
    if preset == "plain":
        return m

    noise = nt.nodes.new("ShaderNodeTexNoise")
    L(mapping.outputs["Vector"], noise.inputs["Vector"])

    if preset == "tiles":          # scale_m = edge length of one tile
        brick = nt.nodes.new("ShaderNodeTexBrick")
        brick.offset, brick.squash = 0.0, 1.0
        mapping.inputs["Scale"].default_value = (1 / scale_m,) * 3
        brick.inputs["Scale"].default_value = 1.0
        brick.inputs["Brick Width"].default_value = 1.0
        brick.inputs["Row Height"].default_value = 1.0
        brick.inputs["Mortar Size"].default_value = spec.get("grout", 0.012)
        brick.inputs["Color1"].default_value = (*color, 1)
        brick.inputs["Color2"].default_value = (*spec.get("color2", _shade(color, 0.8)), 1)
        brick.inputs["Mortar"].default_value = (*_shade(color, 0.35), 1)
        L(mapping.outputs["Vector"], brick.inputs["Vector"])
        L(brick.outputs["Color"], bsdf.inputs["Base Color"])
        noise.inputs["Scale"].default_value = 3.0      # cloudy wear so the gloss is not uniform
        rr = _ramp(nt, (0.15,) * 3, (0.45,) * 3) if rough is None else _ramp(nt, (rough * 0.6,) * 3, (min(1, rough * 1.5),) * 3)
        L(noise.outputs["Fac"], rr.inputs["Fac"])
        L(rr.outputs["Color"], bsdf.inputs["Roughness"])
        _bump(nt, bsdf, brick.outputs["Fac"], -0.4, 0.004)
    elif preset in ("concrete", "painted"):
        mapping.inputs["Scale"].default_value = (1 / scale_m,) * 3
        noise.inputs["Scale"].default_value = 2.0
        noise.inputs["Detail"].default_value = 8.0
        amount = 0.75 if preset == "concrete" else 0.9
        cr = _ramp(nt, _shade(color, amount), _shade(color, 1.15))
        L(noise.outputs["Fac"], cr.inputs["Fac"])
        L(cr.outputs["Color"], bsdf.inputs["Base Color"])
        fine = nt.nodes.new("ShaderNodeTexNoise")
        fine.inputs["Scale"].default_value = 60.0 if preset == "concrete" else 25.0
        L(mapping.outputs["Vector"], fine.inputs["Vector"])
        _bump(nt, bsdf, fine.outputs["Fac"], 0.25 if preset == "concrete" else 0.08, 0.003)
        if rough is None:
            bsdf.inputs["Roughness"].default_value = 0.85 if preset == "concrete" else 0.55
    elif preset == "metal":
        mapping.inputs["Scale"].default_value = (1 / scale_m, 1 / scale_m, 8 / scale_m)   # streaks
        noise.inputs["Scale"].default_value = 6.0
        rr = _ramp(nt, (0.2,) * 3, (0.5,) * 3)
        L(noise.outputs["Fac"], rr.inputs["Fac"])
        L(rr.outputs["Color"], bsdf.inputs["Roughness"])
        bsdf.inputs["Metallic"].default_value = spec.get("metallic", 0.9)
    elif preset == "gravel":       # scale_m = typical stone size
        mapping.inputs["Scale"].default_value = (1 / scale_m,) * 3
        vor = nt.nodes.new("ShaderNodeTexVoronoi")
        vor.inputs["Scale"].default_value = 1.0
        L(mapping.outputs["Vector"], vor.inputs["Vector"])
        cr = _ramp(nt, _shade(color, 0.55), _shade(color, 1.25), 0.0, 1.0)
        L(vor.outputs["Color"], cr.inputs["Fac"])
        L(cr.outputs["Color"], bsdf.inputs["Base Color"])
        _bump(nt, bsdf, vor.outputs["Distance"], 0.9, scale_m * 0.3)
        if rough is None:
            bsdf.inputs["Roughness"].default_value = 0.9
    elif preset == "wood":         # scale_m = plank width
        mapping.inputs["Scale"].default_value = (1 / scale_m, 0.08 / scale_m, 1 / scale_m)
        noise.inputs["Scale"].default_value = 4.0
        noise.inputs["Detail"].default_value = 6.0
        cr = _ramp(nt, _shade(color, 0.65), _shade(color, 1.15))
        L(noise.outputs["Fac"], cr.inputs["Fac"])
        L(cr.outputs["Color"], bsdf.inputs["Base Color"])
        _bump(nt, bsdf, noise.outputs["Fac"], 0.15, 0.002)
    return m


# ---------------------------------------------------------------- scatter
def _footprints(collections, margin):
    """(centre xy, radius) of everything standing near the floor, so scattered items never intersect it."""
    out = []
    for col in collections:
        for o in col.objects:
            if o.type != 'MESH':
                continue
            pts = [o.matrix_world @ Vector(c) for c in o.bound_box]
            lo = Vector(np.min(pts, axis=0).tolist())
            hi = Vector(np.max(pts, axis=0).tolist())
            if lo.z > 0.8 or (hi.x - lo.x) > 12 or (hi.y - lo.y) > 12:   # hanging things, walls and frames don't block the floor
                continue
            out.append((Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2)), max(hi.x - lo.x, hi.y - lo.y) / 2 + margin))
    return out


def _prototypes(kind, rng, colors):
    """A few shared meshes per kind; every scattered item is a linked duplicate of one of them."""
    protos = []
    for i in range(5):
        if kind == "rocks":
            bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=0.5)
            o = bpy.context.active_object
            for v in o.data.vertices:
                v.co *= 1 + rng.uniform(-0.28, 0.28)
                v.co.z *= 0.62
            bpy.ops.object.shade_flat()
        elif kind == "blocks":
            bpy.ops.mesh.primitive_cube_add(size=1)
            o = bpy.context.active_object
            o.scale = (1, rng.choice((1, 1, 2)), rng.choice((1, 1, 0.5)))
            bpy.ops.object.transform_apply(scale=True)
            bev = o.modifiers.new("Bevel", 'BEVEL')
            bev.width, bev.segments = 0.06, 2
        elif kind == "papers":
            bpy.ops.mesh.primitive_plane_add(size=1)
            o = bpy.context.active_object
            o.scale = (1, 1.41, 1)
            bpy.ops.object.transform_apply(scale=True)
        else:
            raise ValueError(f"unknown scatter kind {kind!r}; choose rocks, blocks or papers")
        c = colors[i % len(colors)]
        mat = bpy.data.materials.new(f"scatter_{kind}_{i}")
        mat.use_nodes = True
        b = mat.node_tree.nodes["Principled BSDF"]
        b.inputs["Base Color"].default_value = (*c, 1)
        b.inputs["Roughness"].default_value = 0.85 if kind == "rocks" else 0.5
        o.data.materials.append(mat)
        protos.append(o)
    return protos


def scatter(specs, collection, anchor_xy, support_height, blockers, hall_radius):
    """specs: list of {"id", "kind", "count", "size_m": [min, max], "radius_m", "inner_radius_m",
    "colors": [[r,g,b], ...], "margin_m", "seed"}. Returns the number of items placed."""
    total = 0
    for spec in specs or []:
        rng = random.Random(spec.get("seed", 1))
        kind = spec["kind"]
        colors = spec.get("colors") or {"rocks": [(0.25, 0.25, 0.27), (0.35, 0.33, 0.3)],
                                        "blocks": [(0.7, 0.1, 0.08), (0.8, 0.65, 0.1), (0.15, 0.3, 0.75), (0.2, 0.55, 0.25)],
                                        "papers": [(0.85, 0.85, 0.8)]}[kind]
        protos = _prototypes(kind, rng, [tuple(c) for c in colors])
        blocked = _footprints(blockers, spec.get("margin_m", 0.15))
        r_out = min(spec.get("radius_m", 8.0), hall_radius - 0.5)
        r_in = spec.get("inner_radius_m", 0.0)
        s_lo, s_hi = spec.get("size_m", [0.05, 0.2])
        placed, tries = 0, 0
        while placed < spec["count"] and tries < spec["count"] * 40:
            tries += 1
            ang, rad = rng.uniform(0, math.tau), math.sqrt(rng.uniform((r_in / r_out) ** 2, 1)) * r_out
            xy = Vector((anchor_xy.x + rad * math.cos(ang), anchor_xy.y + rad * math.sin(ang)))
            size = rng.uniform(s_lo, s_hi) if rng.random() < 0.85 else rng.uniform(s_hi, s_hi * 1.8)
            if any((xy - c).length < r + size / 2 for c, r in blocked):
                continue
            proto = rng.choice(protos)
            inst = bpy.data.objects.new(f"{spec['id']}_{placed:03d}", proto.data)
            for mod in proto.modifiers:
                m2 = inst.modifiers.new(mod.name, mod.type)
                m2.width, m2.segments = mod.width, mod.segments
            z = support_height(xy)
            if kind == "papers":
                inst.location = (xy.x, xy.y, z + 0.003 + placed * 1e-5)
                inst.rotation_euler = (0, 0, rng.uniform(0, math.tau))
            else:
                half_h = size * (0.31 if kind == "rocks" else 0.5 * proto.dimensions.z)
                inst.location = (xy.x, xy.y, z + half_h)
                tilt = 0.15 if kind == "rocks" else 0.0
                inst.rotation_euler = (rng.uniform(-tilt, tilt), rng.uniform(-tilt, tilt), rng.uniform(0, math.tau))
            inst.scale = (size, size, size)
            collection.objects.link(inst)
            placed += 1
        for p in protos:                      # prototypes only lend their mesh data
            bpy.data.objects.remove(p)
        print(f"scatter {spec['id']}: {placed}/{spec['count']} {kind} placed ({tries} tries)")
        total += placed
    return total
