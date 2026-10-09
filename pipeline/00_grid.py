"""Step 0: enlarge + brighten the scene image and draw a coordinate grid so bboxes can be read off accurately.

usage (trellis2 env): python 00_grid.py <project_dir> [x1,y1,x2,y2 ...]
  -> work/debug/scene_grid.png (whole image, 3x) and one close-up per requested region: work/debug/zoom_<n>.png (6x)
Labels are ORIGINAL-image pixel coordinates. Without regions, the image is split into a 3x2 set of close-ups.
"""
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

PROJECT = sys.argv[1]
scene = json.load(open(os.path.join(PROJECT, "scene.json"), encoding="utf-8"))
im = Image.open(os.path.join(PROJECT, scene["image"])).convert("RGB")
W, H = im.size
out_dir = os.path.join(PROJECT, "work", "debug")
os.makedirs(out_dir, exist_ok=True)
# large images are not enlarged as much, and get a coarser grid with bigger labels so they stay readable
BIG = W > 1400
OV_SCALE, OV_STEP, ZOOM_SCALE, ZOOM_STEP, FONT = (1, 100, 2, 50, 24) if BIG else (3, 20, 6, 10, 10)


def gridded(box, scale, step):
    x1, y1, x2, y2 = box
    c = im.crop(box).resize(((x2 - x1) * scale, (y2 - y1) * scale), Image.LANCZOS)
    a = np.asarray(c).astype(np.float32) / 255
    c = Image.fromarray((np.clip(a ** scene.get("ref_gamma", 0.55) * 1.15, 0, 1) * 255).astype(np.uint8))   # lift dark scenes
    d = ImageDraw.Draw(c)
    for x in range((x1 // step + 1) * step, x2, step):
        major = x % (step * 5) == 0
        d.line([((x - x1) * scale, 0), ((x - x1) * scale, c.height)], fill=(255, 255, 0) if major else (110, 110, 110), width=1)
        d.text(((x - x1) * scale + 2, 2), str(x), fill=(255, 255, 0), font_size=FONT)
    for y in range((y1 // step + 1) * step, y2, step):
        major = y % (step * 5) == 0
        d.line([(0, (y - y1) * scale), (c.width, (y - y1) * scale)], fill=(255, 255, 0) if major else (110, 110, 110), width=1)
        d.text((2, (y - y1) * scale + 2), str(y), fill=(255, 255, 0), font_size=FONT)
    return c


gridded((0, 0, W, H), OV_SCALE, OV_STEP).save(os.path.join(out_dir, "scene_grid.png"))
regions = [tuple(int(v) for v in a.split(",")) for a in sys.argv[2:]]
if not regions:
    cw, ch = W // 3, H // 2
    regions = [(i * cw, j * ch, min(W, (i + 1) * cw + 20), min(H, (j + 1) * ch + 20)) for j in range(2) for i in range(3)]
for n, box in enumerate(regions):
    gridded(box, ZOOM_SCALE, ZOOM_STEP).save(os.path.join(out_dir, f"zoom_{n}.png"))
    print(f"zoom_{n}.png = region {box}")
print(f"image size {W}x{H}; wrote {out_dir}")
