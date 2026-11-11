#!/usr/bin/env python3
"""Upscale the full-size paintings in tools/raw 4x with a local ESRGAN model through ComfyUI (port 8199); results go to tools/big."""
import json, sys, time, urllib.request, urllib.parse, pathlib
HOST = "127.0.0.1:8199"; HERE = pathlib.Path(__file__).parent; OUT = HERE / "big"; OUT.mkdir(exist_ok=True)
def api(path, data=None):
    req = urllib.request.Request(f"http://{HOST}{path}", data=json.dumps(data).encode() if data is not None else None, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r: return json.loads(r.read() or b"null")
for src in sorted((HERE / "raw").glob("*.png")):
    t = time.time()
    g = {"1": {"class_type": "LoadImage", "inputs": {"image": "shelf_" + src.name}},
         "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4x-UltraSharp.pth"}},
         "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
         "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": "shelfbig_" + src.stem}}}
    pid = api("/prompt", {"prompt": g})["prompt_id"]
    while True:
        h = api("/history/" + pid)
        if pid in h and h[pid].get("outputs"): break
        if pid in h and h[pid].get("status", {}).get("status_str") == "error": raise SystemExit(f"{src.stem}: {json.dumps(h[pid]['status'])[:300]}")
        time.sleep(1)
    img = h[pid]["outputs"]["4"]["images"][0]
    (OUT / src.name).write_bytes(urllib.request.urlopen(f"http://{HOST}/view?" + urllib.parse.urlencode(img), timeout=300).read())
    print(f"{src.stem}: {time.time()-t:.0f}s", flush=True)
