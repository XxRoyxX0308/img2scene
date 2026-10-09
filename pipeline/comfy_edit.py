"""image + text -> image with FLUX.2 klein through a running ComfyUI (official "image edit distilled" graph).

usage: python comfy_edit.py --model 4b|9b --image in.png --prompt "..." --out out.png [--seed 1] [--steps 4] [--mp 1.0] [--free]
Standard library only, so it runs under any Python. ComfyUI must be listening on --server.
"""
import argparse
import json
import os
import time
import urllib.parse
import urllib.request
import uuid

MODELS = {
    "4b": {"unet": "flux-2-klein-4b-fp8.safetensors", "clip": "qwen_3_4b.safetensors"},
    "9b": {"unet": "flux-2-klein-9b-fp8.safetensors", "clip": "qwen_3_8b_fp8mixed.safetensors"},
}
VAE = "flux2-vae.safetensors"


def build_graph(model, image_name, prompt, seed, steps, megapixels):
    m = MODELS[model]
    return {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": m["unet"], "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": m["clip"], "type": "flux2", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "load": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "scale": {"class_type": "ImageScaleToTotalPixels",
                  "inputs": {"image": ["load", 0], "upscale_method": "lanczos", "megapixels": megapixels, "resolution_steps": 1}},
        "size": {"class_type": "GetImageSize", "inputs": {"image": ["scale", 0]}},
        "latent": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": ["size", 0], "height": ["size", 1], "batch_size": 1}},
        "sigmas": {"class_type": "Flux2Scheduler", "inputs": {"steps": steps, "width": ["size", 0], "height": ["size", 1]}},
        "text": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": prompt}},
        "zero": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["text", 0]}},
        "ref": {"class_type": "VAEEncode", "inputs": {"pixels": ["scale", 0], "vae": ["vae", 0]}},
        "pos": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["text", 0], "latent": ["ref", 0]}},
        "neg": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["zero", 0], "latent": ["ref", 0]}},
        "guider": {"class_type": "CFGGuider", "inputs": {"model": ["unet", 0], "positive": ["pos", 0], "negative": ["neg", 0], "cfg": 1.0}},
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "sampler": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "sample": {"class_type": "SamplerCustomAdvanced",
                   "inputs": {"noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["sampler", 0],
                              "sigmas": ["sigmas", 0], "latent_image": ["latent", 0]}},
        "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["decode", 0], "filename_prefix": "klein_edit"}},
    }


def http_json(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req) as r:
        body = r.read()
    return json.loads(body) if body else {}


def upload_image(server, path):
    boundary = uuid.uuid4().hex
    name = f"{uuid.uuid4().hex[:8]}_{os.path.basename(path)}"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + open(path, "rb").read() + \
           (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n").encode()
    req = urllib.request.Request(f"{server}/upload/image", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["name"]


def edit(server, model, image, prompt, out, seed=1, steps=4, megapixels=1.0, timeout=1800):
    t0 = time.time()
    graph = build_graph(model, upload_image(server, image), prompt, seed, steps, megapixels)
    try:
        pid = http_json(f"{server}/prompt", {"prompt": graph, "client_id": uuid.uuid4().hex})["prompt_id"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ComfyUI rejected the workflow: {e.read().decode()[:2000]}")
    while True:
        hist = http_json(f"{server}/history/{pid}").get(pid)
        if hist and hist.get("status", {}).get("completed"):
            break
        if hist and hist.get("status", {}).get("status_str") == "error":
            raise RuntimeError(f"ComfyUI execution error: {json.dumps(hist['status'])[:2000]}")
        if time.time() - t0 > timeout:
            raise TimeoutError("ComfyUI did not finish in time")
        time.sleep(0.5)
    img = hist["outputs"]["save"]["images"][0]
    q = urllib.parse.urlencode({"filename": img["filename"], "subfolder": img["subfolder"], "type": img["type"]})
    with urllib.request.urlopen(f"{server}/view?{q}") as r, open(out, "wb") as f:
        f.write(r.read())
    return time.time() - t0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8188")
    ap.add_argument("--model", choices=MODELS, default="4b")
    ap.add_argument("--image", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--steps", type=int, default=4)
    ap.add_argument("--mp", type=float, default=1.0, help="output size in megapixels")
    ap.add_argument("--free", action="store_true", help="unload models from VRAM afterwards (do this before running TRELLIS)")
    a = ap.parse_args()
    secs = edit(a.server, a.model, a.image, a.prompt, a.out, a.seed, a.steps, a.mp)
    print(f"{a.model}: {secs:.1f}s -> {a.out}")
    if a.free:
        http_json(f"{a.server}/free", {"unload_models": True, "free_memory": True})
