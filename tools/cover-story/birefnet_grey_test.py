#!/usr/bin/env python3
"""Grey-background test (memory-lean, reuses saved original alphas).

Loads the saved BiRefNet alphas (identity-bn-alpha.png / clothes-bn-alpha.png),
fills the confident background with flat mid-grey, re-runs BiRefNet once per
plate, and compares. If alpha barely changes, matting quality is
screen-color-invariant and a grey background only removes the blue fringe.

Only 2 inferences (was 4) — the original pass-1 alphas are already on disk.
"""
import os
import sys
import time

import numpy as np
import onnxruntime as ort
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from birefnet_ab import birefnet_alpha, composite_on_checker, make_session  # noqa: E402

OUT = "/mnt/Misc/sd/cover-story/birefnet-ab-v1"
ROOT = "/mnt/Misc/sd/cover-story/cover-story-qwen2512-skin-head-clothes-poc-v3"

for name, plate_file in [("identity", "identity.png"), ("clothes", "clothes.png")]:
    t0 = time.time()
    plate = Image.open(os.path.join(ROOT, plate_file)).convert("RGB")
    bn_orig = np.asarray(Image.open(os.path.join(OUT, f"{name}-bn-alpha.png")).convert("L"), dtype=np.uint8)
    print(f"{name}: loaded saved alpha ({time.time()-t0:.0f}s)", flush=True)
    arr = np.asarray(plate).copy()
    arr[bn_orig < 25] = 118
    plate_grey = Image.fromarray(arr)
    sess = make_session()
    bn_grey = birefnet_alpha(sess, plate_grey)
    print(f"{name}: grey pass done ({time.time()-t0:.0f}s)", flush=True)
    diff = np.abs(bn_orig.astype(int) - bn_grey.astype(int)).astype(np.uint8)
    print(f"{name}: grey-vs-orig alpha mean|diff| {diff.mean():.2f}/255, changed>32 {100*(diff>32).mean():.1f}%", flush=True)
    for label, alpha, src in [("orig", bn_orig, plate), ("grey", bn_grey, plate_grey)]:
        rgba = src.convert("RGBA")
        rgba.putalpha(Image.fromarray(alpha))
        composite_on_checker(rgba, src.size).convert("RGB").save(os.path.join(OUT, f"{name}-bn-{label}-checker.png"))
        if label == "grey":
            Image.fromarray(alpha).save(os.path.join(OUT, f"{name}-bn-grey-alpha.png"))
    Image.fromarray(diff).save(os.path.join(OUT, f"{name}-bn-grey-vs-orig-diff.png"))
    print(f"{name}: saved", flush=True)
print("GREY TEST DONE", flush=True)
