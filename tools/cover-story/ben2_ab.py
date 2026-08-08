#!/usr/bin/env python3
"""BEN2 (PramaLLC) vs CorridorKey A/B on the same plates as birefnet_ab.py.

Runs BEN2_Base.onnx locally (onnxruntime, CPU), letterboxed to the model's
1024x1024 input, min-max scaled alpha back to the original canvas. Writes
*-ben2-alpha.png, *-ben2-checker.png and reports region metrics into
ben2-metrics.json. Run birefnet_ab_metrics.py after to get the full table.
"""
import json
import os
import sys
import time

import numpy as np
import onnxruntime as ort
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from birefnet_ab import REGIONS, region_metrics, composite_on_checker, make_session  # noqa: E402

ROOT = "/mnt/Misc/sd/cover-story/cover-story-qwen2512-skin-head-clothes-poc-v3"
OUT = "/mnt/Misc/sd/cover-story/birefnet-ab-v1"
MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "BEN2_Base.onnx")
INPUT = 1024
PLATES = [
    ("identity.png", "poc:qwen2512-identity-alpha.png", "identity"),
    ("clothes.png", "poc:qwen2512-clothes-alpha.png", "clothes"),
]


def ben2_alpha(session, image):
    """Letterbox to 1024x1024 (scale-to-fit, pad with corner colour), run,
    interpolate logits back to original size, min-max normalize to 0..255."""
    w, h = image.size
    scale = INPUT / max(w, h)
    target = (round(w * scale), round(h * scale))
    resized = image.resize(target, Image.Resampling.BICUBIC)
    pad = image.getpixel((2, 2))
    canvas = Image.new("RGB", (INPUT, INPUT), pad)
    canvas.paste(resized, ((INPUT - target[0]) // 2, (INPUT - target[1]) // 2))
    arr = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1) / 255.0
    logits = np.squeeze(session.run(None, {session.get_inputs()[0].name: arr[None]})[0]).astype(np.float32)
    # (1024,1024) fp16 output -> fp32
    top = (INPUT - target[1]) // 2
    left = (INPUT - target[0]) // 2
    logits = logits[top:top + target[1], left:left + target[0]]
    lo, hi = logits.min(), logits.max()
    alpha = (logits - lo) / (hi - lo) * 255.0
    return np.asarray(Image.fromarray(alpha.astype(np.uint8)).resize(image.size, Image.Resampling.BILINEAR), np.uint8)


def main():
    sess = make_session(MODEL)
    report = {}
    for plate_name, ck_name, name in PLATES:
        t0 = time.time()
        plate = Image.open(os.path.join(ROOT, plate_name)).convert("RGB")
        alpha = ben2_alpha(sess, plate)
        print(f"{name}: BEN2 done in {time.time()-t0:.0f}s", flush=True)
        Image.fromarray(alpha).save(os.path.join(OUT, f"{name}-ben2-alpha.png"))
        rgba = plate.convert("RGBA"); rgba.putalpha(Image.fromarray(alpha))
        composite_on_checker(rgba, plate.size).convert("RGB").save(os.path.join(OUT, f"{name}-ben2-checker.png"))
        report[name] = {"regions": {rname: region_metrics(alpha, region) for rname, region in REGIONS.items()}}
    (os.path.join(OUT, "ben2-metrics.json")) and None
    with open(os.path.join(OUT, "ben2-metrics.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    print("done ->", OUT)


if __name__ == "__main__":
    main()
