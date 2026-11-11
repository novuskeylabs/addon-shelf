#!/usr/bin/env python3
"""Turn the upscaled paintings in tools/big into the app's emblems in art/: 2048px, dark backdrop keyed out, soft round edge."""
import pathlib, os
from PIL import Image, ImageChops, ImageDraw, ImageFilter
HERE = pathlib.Path(__file__).parent; N = 2048; Image.MAX_IMAGE_PIXELS = None
mask = Image.new("L", (N, N), 0)
ImageDraw.Draw(mask).ellipse([N * .03, N * .03, N * .97, N * .97], fill=255)
mask = mask.filter(ImageFilter.GaussianBlur(N * .012))             # also removes the corner mark the model leaves
total = 0
for src in sorted((HERE / "big").glob("*.png")):
    im = Image.open(src).convert("RGB").resize((N, N), Image.LANCZOS)
    r, g, b = im.split(); mx = ImageChops.lighter(ImageChops.lighter(r, g), b)
    key = mx.point(lambda v: 0 if v < 26 else 255 if v > 64 else int(255 * (v - 26) / 38)).filter(ImageFilter.GaussianBlur(2.5))
    out = im.convert("RGBA"); out.putalpha(ImageChops.multiply(key, mask))
    dest = HERE.parent / "art" / (src.stem + ".webp"); out.save(dest, "WEBP", quality=90, method=6); total += os.path.getsize(dest)
    out.resize((288, 288), Image.LANCZOS).save(dest.with_name(src.stem + "-s.webp"), "WEBP", quality=90, method=6)   # for the header and the picker
print(len(list((HERE / "big").glob("*.png"))), "emblems,", total // 1024, "KB")
