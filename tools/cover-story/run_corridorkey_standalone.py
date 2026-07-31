#!/usr/bin/env python3
"""Key server-side PNGs with the official standalone CorridorKey engine."""

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from export_performer_cutouts import background_color, key, review_html, save_png
from export_personas import atomic_text, sha256
from run_transparent_performers import performer_details


def coarse_hint(source):
    rgb = source.convert("RGB")
    alpha = key(rgb)[0].getchannel("A").point(lambda value: 0 if value < 128 else 255)
    for corner in ((0, 0), (alpha.width - 1, 0), (0, alpha.height - 1), (alpha.width - 1, alpha.height - 1)):
        if alpha.getpixel(corner) == 0:
            ImageDraw.floodfill(alpha, corner, 128)
    alpha = alpha.point(lambda value: 0 if value == 128 else 255)
    channels = rgb.split()
    background = background_color(rgb)
    distance = Image.new("L", rgb.size)
    for channel, value in zip(channels, background):
        distance = ImageChops.lighter(
            distance, ImageChops.difference(channel, Image.new("L", rgb.size, value))
        )
    screen_channel = max(range(3), key=background.__getitem__)
    other_channels = [channel for index, channel in enumerate(channels) if index != screen_channel]
    not_screen_dominant = ImageChops.subtract(
        ImageChops.lighter(*other_channels), channels[screen_channel],
    ).point(lambda value: 255 if value else 0)
    alpha = ImageChops.lighter(
        ImageChops.darker(
            alpha, distance.point(lambda value: 0 if value <= 45 else 255),
        ),
        not_screen_dominant,
    )
    return alpha.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.GaussianBlur(4))


def output_paths(output_dir, stem):
    return {
        "corridorkey": output_dir / "corridorkey" / f"{stem}.png",
        "qc": output_dir / "qc" / f"{stem}.png",
        "avif": output_dir / "assets" / f"{stem}.avif",
    }


def production_number(source):
    match = re.match(r"(\d+)-", source.name)
    if not match:
        raise ValueError(f"cannot read production variant from {source.name}")
    return int(match.group(1))


def production_screen(source):
    return performer_details(production_number(source), 0)["screen"]


def straight_rgba(result, linear_to_srgb):
    processed = result["processed"]
    alpha = np.clip(processed[..., 3:4], 0, 1)
    straight = np.divide(
        processed[..., :3], alpha,
        out=np.zeros_like(processed[..., :3]), where=alpha > 1e-6,
    )
    return np.concatenate((np.clip(linear_to_srgb(straight), 0, 1), alpha), axis=2)


def save_avif(image, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    image.resize((600, 900), Image.Resampling.LANCZOS).save(
        path, "AVIF", quality=70, alpha_quality=100, speed=6,
    )


class StandaloneKeyer:
    def __init__(self, corridorkey_root):
        sys.path.insert(0, str(corridorkey_root))
        from CorridorKeyModule.backend import create_engine
        from CorridorKeyModule.core.color_utils import linear_to_srgb

        self.create_engine = create_engine
        self.linear_to_srgb = linear_to_srgb
        self.engines = {}

    def process(self, source, output_dir, screen, stem=None, encode=True):
        stem = stem or source.stem
        paths = output_paths(output_dir, stem)
        required = [paths["corridorkey"], paths["qc"]]
        if encode:
            required.append(paths["avif"])
        if not all(path.is_file() for path in required):
            if screen not in self.engines:
                self.engines[screen] = self.create_engine(
                    backend="torch", device="cuda", img_size=2048, screen_color=screen,
                )
            engine = self.engines[screen]
            with Image.open(source) as opened:
                rgb = opened.convert("RGB")
                result = engine.process_frame(
                    np.array(rgb), np.array(coarse_hint(rgb)), input_is_linear=False,
                    despill_strength=1.0, auto_despeckle=True, despeckle_size=400,
                    refiner_scale=1.0, generate_comp=True, post_process_on_gpu=True,
                    screen_channel=2 if screen == "blue" else 1,
                )
            rgba = Image.fromarray(
                np.rint(straight_rgba(result, self.linear_to_srgb) * 255).astype(np.uint8),
                "RGBA",
            )
            qc = Image.fromarray(np.rint(np.clip(result["comp"], 0, 1) * 255).astype(np.uint8), "RGB")
            save_png(rgba, paths["corridorkey"])
            save_png(qc, paths["qc"])
            if encode:
                save_avif(rgba, paths["avif"])
        return paths


def self_test():
    processed = np.array([[[0.1, 0.2, 0.3, 0.5], [0, 0, 0, 0]]], dtype=np.float32)
    rgba = straight_rgba({"processed": processed}, lambda value: value)
    assert np.allclose(rgba[0, 0], (0.2, 0.4, 0.6, 0.5))
    assert np.allclose(rgba[0, 1], 0)
    assert production_number(Path("21-test.png")) == 21
    assert production_screen(Path("21-test.png")) == "blue"
    source = Image.new("RGB", (40, 60), (0, 0, 255))
    for x in range(10, 30):
        for y in range(10, 60):
            source.putpixel((x, y), (180, 120, 90))
    for x in range(12, 19):
        for y in range(27, 34):
            source.putpixel((x, y), (0, 0, 255))
    for x in range(23, 29):
        for y in range(27, 34):
            source.putpixel((x, y), (40, 110, 55))
    hint = coarse_hint(source)
    assert hint.getpixel((0, 0)) == 0
    assert hint.getpixel((15, 30)) < 128
    assert hint.getpixel((26, 30)) > 128
    muted = Image.new("RGB", (40, 60), (60, 100, 70))
    for x in range(10, 30):
        for y in range(10, 60):
            muted.putpixel((x, y), (180, 120, 90))
    for x in range(14, 26):
        for y in range(24, 40):
            muted.putpixel((x, y), (75, 70, 115))
    assert coarse_hint(muted).getpixel((20, 30)) > 128
    print("standalone CorridorKey runner self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corridorkey-root", type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--screen", choices=("green", "blue"))
    parser.add_argument(
        "--production-routing", action="store_true",
        help="process only production variants routed to --screen",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not all((args.corridorkey_root, args.source_dir, args.output_dir, args.screen)):
        parser.error("--corridorkey-root, --source-dir, --output-dir and --screen are required")

    sources = sorted(args.source_dir.glob("*.png"))
    if args.production_routing:
        sources = [source for source in sources if production_screen(source) == args.screen]
    if not sources:
        raise ValueError(f"no PNG sources in {args.source_dir}")
    keyer = StandaloneKeyer(args.corridorkey_root)
    entries = []
    for index, source in enumerate(sources, 1):
        number = production_number(source) if args.production_routing else None
        stem = f"performer-{number:03d}" if number else source.stem
        paths = keyer.process(source, args.output_dir, args.screen, stem)
        entries.append({
            "stem": stem, "screen": args.screen, "source": str(source),
            "source_sha256": sha256(source),
            **{name: str(path) for name, path in paths.items()},
        })
        print(f"[{index}/{len(sources)}] {source.name}", flush=True)

    atomic_text(args.output_dir / "manifest.json", json.dumps({
        "version": 1,
        "processor": "official CorridorKey standalone",
        "screen": args.screen,
        "entries": entries,
    }, indent=2) + "\n")
    atomic_text(args.output_dir / "review.html", review_html(entries))
    print(f"exported {len(entries)} {args.screen}-screen performers to {args.output_dir}")


if __name__ == "__main__":
    main()
