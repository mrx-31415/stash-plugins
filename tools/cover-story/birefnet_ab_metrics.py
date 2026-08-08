#!/usr/bin/env python3
"""Recompute region metrics + contact sheet from saved A/B alphas (no inference).

Usage: .venv-birefnet/bin/python birefnet_ab_metrics.py
Reads the alpha PNGs written by birefnet_ab.py, writes metrics.json and a
contact sheet (compare-*.png strips) for human review.
"""

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from birefnet_ab import REGIONS, region_metrics  # noqa: E402

OUT = Path("/mnt/Misc/sd/cover-story/birefnet-ab-v1")
PLATES = ["identity", "clothes"]


def strip(*images, out, gap=8):
    images = [im if isinstance(im, Image.Image) else Image.open(im) for im in images]
    h = max(im.height for im in images)
    w = sum(im.width for im in images) + gap * (len(images) - 1)
    canvas = Image.new("RGB", (w, h), (128, 128, 128))
    x = 0
    for im in images:
        canvas.paste(im.convert("RGB"), (x, 0))
        x += im.width + gap
    canvas.save(out)
    return out


def main():
    report = {}
    for name in PLATES:
        if not (OUT / f"{name}-ck-alpha.png").is_file() or not (OUT / f"{name}-bn-alpha.png").is_file():
            print(f"{name}: alphas missing, skipping")
            continue
        ck = np.asarray(Image.open(OUT / f"{name}-ck-alpha.png").convert("L"), dtype=np.uint8)
        bn = np.asarray(Image.open(OUT / f"{name}-bn-alpha.png").convert("L"), dtype=np.uint8)
        entry = {
            "regions": {rname: {"ck": region_metrics(ck, region), "bn": region_metrics(bn, region)}
                        for rname, region in REGIONS.items()},
            "mean_abs_alpha_diff": float(np.abs(ck.astype(int) - bn.astype(int)).mean()),
            "max_abs_alpha_diff": int(np.abs(ck.astype(int) - bn.astype(int)).max()),
        }
        report[name] = entry
        print(f"== {name} ==  mean|diff| {entry['mean_abs_alpha_diff']:.2f}")
        print(f"   {'region':<9} {'':>4} {'fg%':>7} {'mid_px':>8} {'specks':>7} {'holes':>7} {'hole_px':>8}")
        for rname in REGIONS:
            r = entry["regions"][rname]
            print(f"   {rname:<9} CK  {r['ck']['fg_frac']*100:6.1f} {r['ck']['mid_px']:8d} {r['ck']['specks']:7d} {r['ck']['holes']:7d} {r['ck']['hole_px']:8d}")
            print(f"   {rname:<9} BN  {r['bn']['fg_frac']*100:6.1f} {r['bn']['mid_px']:8d} {r['bn']['specks']:7d} {r['bn']['holes']:7d} {r['bn']['hole_px']:8d}")
        # contact strips for the defect regions, CK | BN
        for rname, region in REGIONS.items():
            if rname == "full":
                continue
            x0, y0, x1, y1 = region
            ck_im = Image.open(OUT / f"{name}-ck-checker.png").crop(region)
            bn_im = Image.open(OUT / f"{name}-bn-checker.png").crop(region)
            strip(ck_im, bn_im, out=OUT / f"compare-{name}-{rname}.png")
            print(f"   wrote compare-{name}-{rname}.png")
    (OUT / "metrics.json").write_text(json.dumps(report, indent=2))
    print(f"\nmetrics.json + contact sheets -> {OUT}")


if __name__ == "__main__":
    main()
