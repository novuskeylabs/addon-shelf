#!/usr/bin/env python3
"""Paint theme emblems with the local Z-Image Turbo model through ComfyUI's API. usage: paint.py id [id...] | all"""
import json, sys, time, urllib.request, urllib.parse, random, pathlib
HOST = "127.0.0.1:8199"; OUT = pathlib.Path(sys.argv[0]).parent / "raw"            # full-size paintings; cut-outs for the app go in ../art
STYLE = ("Hand-painted fantasy game emblem, {motif}. {palette}. Ornate crafted metal and enamel, rich painterly brushwork, dramatic rim lighting, "
         "single centered emblem filling the frame, symmetrical, on a plain very dark vignette background. Original design. No text, no letters, no numbers, no watermark, no border.")
ART = {
 "alliance": ("a noble heraldic shield bearing a roaring golden lion head, crossed longswords behind it, laurel wreath", "Royal blue enamel and polished gold"),
 "horde": ("a savage war emblem of black iron spikes, curved tusks and jagged crossed axes around a battered round shield", "Crimson red, charred black iron and bone"),
 "warrior": ("two crossed heavy greatswords over a dented steel shield", "Tan leather, worn steel and bronze"),
 "paladin": ("a holy warhammer wreathed in radiant light with small wings and a halo", "Rose pink, warm gold and white light"),
 "hunter": ("a recurve longbow with a nocked arrow, a wolf paw print and feathers", "Leaf green, wood brown and brass"),
 "rogue": ("two crossed curved daggers behind a dark hooded mask", "Pale yellow highlights on black leather and dark steel"),
 "priest": ("a radiant winged staff with a glowing sun disc and soft rays", "White, silver and soft gold light"),
 "shaman": ("a carved wooden totem crackling with lightning, wind and water swirling around it", "Deep blue, storm cyan and carved wood"),
 "mage": ("an arcane crystal staff head surrounded by floating runes-free geometric star patterns and frost shards", "Ice blue, cyan glow and silver"),
 "warlock": ("a horned skull chalice burning with fel fire and a floating soul shard", "Violet purple and eerie green flame"),
 "druid": ("a great bear paw print inside a ring of oak leaves, antlers and a crescent moon", "Burnt orange, forest green and bark brown"),
 "human": ("a stone castle keep with banners and a golden crown above it", "Royal blue banners, grey stone and gold"),
 "dwarf": ("a blacksmith anvil with a rune-free heavy hammer, glowing forge embers and braided knotwork", "Forge orange glow on dark iron and bronze"),
 "nightelf": ("a silver crescent moon cradling an ancient glowing tree, with owl feathers", "Deep violet, moonlit silver and teal"),
 "gnome": ("interlocking brass gears, a wrench and round goggles with sparks", "Brass, teal and copper"),
 "orc": ("a crude double-headed war axe on a tattered banner with wolf fangs", "Rust red, dark iron and worn leather"),
 "undead": ("a cracked skull with glowing eye sockets rising from green alchemical mist, broken crown", "Sickly green glow, bone and purple-grey"),
 "tauren": ("great bull horns over a carved totem pole decorated with feathers and turquoise beads", "Earth brown, turquoise and feather red"),
 "skyborne": ("a small floating sky island crowned with an elegant elven spire, wrapped in swirling ribbons of wind, with spread feathered wings and drifting clouds", "Sky blue, white, silver and pale gold"),
 "troll": ("a carved wooden tribal mask with long tusks and bright feathers, crossed spears", "Jungle teal, orange and bone"),
}
def api(path, data=None):
    req = urllib.request.Request(f"http://{HOST}{path}", data=json.dumps(data).encode() if data is not None else None, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r: return json.loads(r.read() or b"null")
def graph(prompt, seed, name):
    return {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "z_image_turbo_bf16.safetensors", "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
            "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
            "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
            "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0], "seed": seed,
                                                       "steps": 9, "cfg": 1, "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
            "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "shelf_" + name}}}
ids = list(ART) if sys.argv[1:] == ["all"] else sys.argv[1:]
OUT.mkdir(exist_ok=True)
for i in ids:
    motif, pal = ART[i]; t = time.time()
    pid = api("/prompt", {"prompt": graph(STYLE.format(motif=motif, palette=pal), 7000 + list(ART).index(i), i)})["prompt_id"]
    while True:
        h = api("/history/" + pid)
        if pid in h and h[pid].get("outputs"): break
        if pid in h and h[pid].get("status", {}).get("status_str") == "error": raise SystemExit(f"{i}: {json.dumps(h[pid]['status'])[:400]}")
        time.sleep(1)
    img = h[pid]["outputs"]["10"]["images"][0]
    data = urllib.request.urlopen(f"http://{HOST}/view?" + urllib.parse.urlencode(img), timeout=120).read()
    (OUT / f"{i}.png").write_bytes(data); print(f"{i}: {len(data)//1024} KB in {time.time()-t:.0f}s", flush=True)
