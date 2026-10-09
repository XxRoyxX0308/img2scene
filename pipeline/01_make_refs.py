"""Step 1: scene image + scene.json -> one reference crop per object (input for the image-edit model).

crop (ref_bbox or bbox + pad) -> Real-ESRGAN x4 -> relight so the object is readable -> ref.png

usage (trellis2 env): python 01_make_refs.py <project_dir> [object_id ...]
"""
import json
import os
import sys

import cv2
import numpy as np
import torch
from PIL import Image

PROJECT = sys.argv[1]
ONLY = set(sys.argv[2:])
sys.path.insert(0, os.path.join(PROJECT, "work", "vendor"))

PAD = 0.25
LONG_SIDE = 1024

from spandrel import ModelLoader
upscaler = ModelLoader().load_from_file(os.path.join(PROJECT, "work", "models", "RealESRGAN_x4plus.pth")).cuda().eval()


def upscale4x(rgb):
    t = torch.from_numpy(rgb.copy()).float().div(255).permute(2, 0, 1).unsqueeze(0).cuda()
    with torch.no_grad():
        out = upscaler(t).clamp(0, 1)
    return (out[0].permute(1, 2, 0).cpu().numpy() * 255).astype(np.uint8)


scene = json.load(open(os.path.join(PROJECT, "scene.json"), encoding="utf-8"))
img = np.asarray(Image.open(os.path.join(PROJECT, scene["image"])).convert("RGB"))
H, W = img.shape[:2]
for obj in scene["objects"]:
    if ONLY and obj["id"] not in ONLY:
        continue
    out_dir = os.path.join(PROJECT, "work", "objects", obj["id"])
    os.makedirs(out_dir, exist_ok=True)
    if "ref_bbox" in obj:
        x1, y1, x2, y2 = obj["ref_bbox"]
    else:
        bx1, by1, bx2, by2 = obj["bbox"]
        pad = max(6, int(max(bx2 - bx1, by2 - by1) * PAD))
        x1, y1, x2, y2 = bx1 - pad, by1 - pad, bx2 + pad, by2 + pad
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(W, x2), min(H, y2)
    up = upscale4x(img[y1:y2, x1:x2])
    s = LONG_SIDE / max(up.shape[:2])
    up = cv2.resize(up, None, fx=s, fy=s, interpolation=cv2.INTER_LANCZOS4 if s > 1 else cv2.INTER_AREA)
    up = (np.clip((up.astype(np.float32) / 255) ** 0.62 * 1.1, 0, 1) * 255).astype(np.uint8)
    Image.fromarray(up).save(os.path.join(out_dir, "ref.png"))
    print(f"{obj['id']}: {x2 - x1}x{y2 - y1}px -> {up.shape[1]}x{up.shape[0]}")
