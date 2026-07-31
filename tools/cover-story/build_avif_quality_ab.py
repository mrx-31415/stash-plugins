#!/usr/bin/env python3
"""Build q70-versus-q60/q50/q40 YUV420 AVIF review pairs."""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageOps

from export_personas import atomic_text, crop, sha256
from export_scene_assets import save_avif
from run_static_feedback_ab import reference
from run_static_performers import inspect_avif


SLOTS = (1, 50, 81, 196, 239, 300)
QUALITIES = (60, 50, 40)


def self_test():
    assert len(SLOTS) == 6
    assert len([(slot, quality) for slot in SLOTS for quality in QUALITIES]) == 18
    assert len({(slot - 1) % 6 for slot in SLOTS}) == 6
    print("AVIF quality A/B builder self-check passed")


def encode(image, path, quality):
    save_avif(image, path, quality=quality, alpha_quality=None, yuv="420")
    inspect_avif(path, "420")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path("/mnt/Misc/sd/cover-story/static-performer-final-review-v1"),
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.output_dir:
        parser.error("--output-dir is required")
    manifest_path = args.output_dir / "manifest.json"
    if args.output_dir.exists() and any(args.output_dir.iterdir()) and not manifest_path.exists():
        raise ValueError(f"refusing nonempty output directory without manifest: {args.output_dir}")

    approved = {
        entry["slot"]: entry
        for entry in json.loads((args.source_root / "manifest.json").read_text())["entries"]
    }
    entries = []
    assets = args.output_dir / "assets"
    for slot in SLOTS:
        source = args.source_root / "raw" / f"performer-{slot:03d}.png"
        if sha256(source) != approved[slot]["source_sha256"]:
            raise ValueError(f"approved source changed: {source}")
        with Image.open(source) as opened:
            image = crop(ImageOps.exif_transpose(opened).convert("RGB"), 0.5, 0.5, 600, 900)
        baseline = None
        for quality in QUALITIES:
            stem = f"performer-{slot:03d}-q70-vs-q{quality}"
            for arm, selected_quality in (("A", 70), ("B", quality)):
                output = assets / f"{stem}_{arm}.avif"
                if arm == "A" and baseline:
                    reference(baseline, output)
                else:
                    encode(image, output, selected_quality)
                    if arm == "A":
                        baseline = output
                entries.append({
                    "slot": slot,
                    "id": output.stem,
                    "stem": output.stem,
                    "arm": arm,
                    "quality": selected_quality,
                    "comparison_quality": quality,
                    "yuv": "420",
                    "source": str(source),
                    "source_sha256": approved[slot]["source_sha256"],
                    "asset": str(output),
                    "asset_sha256": sha256(output),
                    "dimensions": [600, 900],
                    "bytes": output.stat().st_size,
                })
    atomic_text(manifest_path, json.dumps({
        "version": 1,
        "slots": list(SLOTS),
        "qualities": [70, *QUALITIES],
        "asset": {"format": "AVIF", "speed": 6, "yuv": "420", "alpha": False},
        "entries": entries,
    }, indent=2) + "\n")
    print(f"manifest has {len(entries)}/36 images in 18 pairs")


if __name__ == "__main__":
    main()
