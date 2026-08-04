#!/usr/bin/env python3
"""Run the official CorridorKey engine on a small screenshot batch."""

import argparse
import hashlib
import json
import statistics
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def background_color(image):
    size = max(8, round(min(image.size) * 0.08))
    pixels = []
    for box in ((0, 0, size, size), (image.width - size, 0, image.width, size)):
        pixels.extend(image.crop(box).get_flattened_data())
    return tuple(round(statistics.median(pixel[channel] for pixel in pixels)) for channel in range(3))


def coarse_hint(image):
    """Build a conservative foreground hint; CorridorKey supplies the final matte."""
    rgb = image.convert("RGB")
    background = background_color(rgb)
    channels = rgb.split()
    screen_channel = max(range(3), key=background.__getitem__)
    other_channels = [channel for index, channel in enumerate(channels) if index != screen_channel]
    # The screenshot has a deliberately uneven green screen.  A corner-distance
    # hint mistakes that gradient for foreground; use chroma dominance instead.
    other = ImageChops.lighter(*other_channels)
    other = ImageChops.add(other, Image.new("L", rgb.size, 24))
    hint = ImageChops.subtract(other, channels[screen_channel]).point(
        lambda value: 255 if value else 0
    )
    return hint.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.GaussianBlur(4))


def checkerboard(size, cell=32):
    image = Image.new("RGB", size, (42, 42, 42))
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(86, 86, 86))
    return image.convert("RGBA")


def straight_rgba(result, linear_to_srgb):
    processed = result["processed"]
    alpha = np.clip(processed[..., 3:4], 0, 1)
    straight = np.divide(
        processed[..., :3], alpha,
        out=np.zeros_like(processed[..., :3]), where=alpha > 1e-6,
    )
    return np.concatenate((np.clip(linear_to_srgb(straight), 0, 1), alpha), axis=2)


def save(image, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", compress_level=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--hint-dir", type=Path)
    parser.add_argument("--screen", choices=("green", "blue"), default="green")
    args = parser.parse_args()

    from CorridorKeyModule.backend import create_engine
    from CorridorKeyModule.core.color_utils import linear_to_srgb

    sources = sorted(args.source_dir.glob("*.png"))
    if not sources:
        raise ValueError(f"no PNG sources in {args.source_dir}")
    engine = create_engine(
        backend="torch", device="cuda", img_size=2048, screen_color=args.screen,
    )
    entries = []
    for source in sources:
        with Image.open(source) as opened:
            rgb = opened.convert("RGB")
            hint_path = args.hint_dir / source.name if args.hint_dir else None
            if hint_path and not hint_path.is_file():
                raise ValueError(f"missing hint: {hint_path}")
            with Image.open(hint_path) if hint_path else coarse_hint(rgb) as opened_hint:
                hint = opened_hint.convert("L")
            if hint.size != rgb.size:
                hint = hint.resize(rgb.size, Image.Resampling.NEAREST)
            result = engine.process_frame(
                np.array(rgb), np.array(hint), input_is_linear=False,
                despill_strength=1.0, auto_despeckle=True, despeckle_size=400,
                refiner_scale=1.0, generate_comp=True, post_process_on_gpu=True,
                screen_channel=2 if args.screen == "blue" else 1,
            )
        rgba = Image.fromarray(
            np.rint(straight_rgba(result, linear_to_srgb) * 255).astype(np.uint8), "RGBA",
        )
        matte = Image.fromarray(
            np.rint(np.clip(result["processed"][..., 3], 0, 1) * 255).astype(np.uint8), "L",
        )
        qc = Image.fromarray(np.rint(np.clip(result["comp"], 0, 1) * 255).astype(np.uint8), "RGB")
        preview = Image.alpha_composite(checkerboard(rgba.size), rgba)
        save(rgba, args.output_dir / "rgba" / source.name)
        save(matte, args.output_dir / "matte" / source.name)
        save(qc, args.output_dir / "qc" / source.name)
        save(preview.convert("RGB"), args.output_dir / "preview" / source.name)
        save(hint, args.output_dir / "hint" / source.name)
        entries.append({
            "source": str(source), "source_sha256": sha256(source),
            "width": rgba.width, "height": rgba.height,
            "rgba": str(args.output_dir / "rgba" / source.name),
            "matte": str(args.output_dir / "matte" / source.name),
            "qc": str(args.output_dir / "qc" / source.name),
            "preview": str(args.output_dir / "preview" / source.name),
            "hint": str(args.output_dir / "hint" / source.name),
        })
        print(source.name, flush=True)
    manifest = {
        "version": 1, "processor": "official CorridorKey standalone",
        "screen": args.screen, "img_size": 2048,
        "hint_source": str(args.hint_dir) if args.hint_dir else "automatic chroma dominance",
        "entries": entries,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
