"""Side-by-side of the reference image and the RefCamera render -> work/renders/compare.png

usage (trellis2 env): python 07_compare.py <project_dir>
"""
import json
import os
import sys

from PIL import Image, ImageDraw

PROJECT = sys.argv[1]
scene = json.load(open(os.path.join(PROJECT, "scene.json"), encoding="utf-8"))
render = Image.open(os.path.join(PROJECT, "work", "renders", "view_ref.png")).convert("RGB")
ref = Image.open(os.path.join(PROJECT, scene["image"])).convert("RGB").resize(render.size, Image.LANCZOS)
gap = 12
out = Image.new("RGB", (render.width * 2 + gap, render.height), (0, 0, 0))
out.paste(ref, (0, 0))
out.paste(render, (render.width + gap, 0))
d = ImageDraw.Draw(out)
d.text((10, 8), f"reference ({scene['image']})", fill=(255, 255, 0), font_size=28)
d.text((render.width + gap + 10, 8), "Blender render (RefCamera)", fill=(255, 255, 0), font_size=28)
path = os.path.join(PROJECT, "work", "renders", "compare.png")
out.save(path)
print(path, out.size)
