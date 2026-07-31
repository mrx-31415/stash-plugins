#!/usr/bin/env python3
"""Key generated green-screen performers and export transparent review assets."""

import argparse
import json
import os
import statistics
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

from export_personas import atomic_text, sha256
from export_scene_assets import save_avif


def background_color(image):
    width, height = image.size
    size = max(8, round(min(width, height) * 0.08))
    pixels = []
    for box in (
        (0, 0, size, size), (width - size, 0, width, size),
    ):
        pixels.extend(image.crop(box).get_flattened_data())
    return tuple(round(statistics.median(pixel[channel] for pixel in pixels)) for channel in range(3))


def key(image, inner=30, outer=90):
    if not 0 <= inner < outer <= 255:
        raise ValueError("key thresholds must satisfy 0 <= inner < outer <= 255")
    rgb = image.convert("RGB")
    background = background_color(rgb)
    channels = rgb.split()
    distance = Image.new("L", rgb.size)
    for channel, value in zip(channels, background):
        distance = ImageChops.lighter(
            distance, ImageChops.difference(channel, Image.new("L", rgb.size, value))
        )
    distance_alpha = distance.point(
        lambda value: max(0, min(255, round((value - inner) * 255 / (outer - inner))))
    )

    screen_channel = max(range(3), key=background.__getitem__)
    other_channels = [channel for index, channel in enumerate(channels) if index != screen_channel]
    chroma_excess = ImageChops.subtract(
        channels[screen_channel], ImageChops.lighter(*other_channels)
    )
    background_excess = max(
        1, background[screen_channel] - max(
            value for index, value in enumerate(background) if index != screen_channel
        ),
    )
    low, high = 0, max(12, round(background_excess * 0.35))
    chroma_alpha = chroma_excess.point(
        lambda value: 255 - max(0, min(255, round((value - low) * 255 / (high - low))))
    )
    # ponytail: chroma key assumes no adjacent-hue wardrobe; CorridorKey provides the final matte.
    alpha = ImageChops.darker(distance_alpha, chroma_alpha).filter(ImageFilter.GaussianBlur(0.6))
    cleaned = list(channels)
    cleaned[screen_channel] = ImageChops.subtract(cleaned[screen_channel], chroma_excess)
    result = Image.merge("RGBA", (*cleaned, alpha))
    return result, background


def metrics(image):
    alpha = image.getchannel("A")
    width, height = image.size
    opaque = sum(value >= 128 for value in alpha.get_flattened_data()) / (width * height)
    size = max(8, round(min(width, height) * 0.05))
    border = list(alpha.crop((0, 0, size, size)).get_flattened_data())
    border += list(alpha.crop((width - size, 0, width, size)).get_flattened_data())
    leakage = sum(value >= 32 for value in border) / len(border)
    return {
        "opaque_fraction": round(opaque, 4),
        "top_corner_leakage": round(leakage, 4),
        "needs_review": not 0.12 <= opaque <= 0.9 or leakage > 0.02,
    }


def save_png(image, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".png", delete=False) as output:
        temporary = Path(output.name)
    try:
        image.save(temporary, "PNG", compress_level=1)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def review_html(entries, root=None):
    cards = []
    for entry in entries:
        images = [(
            "cutout",
            os.path.relpath(entry["avif"], root) if root else f'assets/{entry["stem"]}.avif',
        )]
        if entry.get("qc"):
            relative_root = root or Path(entry["qc"]).parent.parent
            source = entry.get("source") or entry["raw"]
            images[:0] = [
                ("raw", os.path.relpath(source, relative_root)),
                ("CorridorKey QC", os.path.relpath(entry["qc"], relative_root)),
            ]
        panels = "".join(
            f'<figure><img src="{source}" alt=""><figcaption>{label}</figcaption></figure>'
            for label, source in images
        )
        cards.append(f'<article><div class="images">{panels}</div><p>{entry["stem"]}</p></article>')
    return f"""<!doctype html>
<meta charset="utf-8"><title>Performer cutout PoC</title>
<style>
body{{background:#17191e;color:#eee;font:14px system-ui;margin:24px}}
main{{display:grid;gap:24px}}article{{min-width:0}}p{{overflow-wrap:anywhere}}
.images{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px}}
figure{{margin:0;background:linear-gradient(145deg,#2e3e55,#bd8b65 55%,#222)}}
img{{width:100%;aspect-ratio:2/3;display:block;object-fit:contain}}
figcaption{{padding:6px;background:#111a}}
</style><h1>Performer cutout PoC</h1><main>{"".join(cards)}</main>"""


def self_test():
    image = Image.new("RGB", (120, 180), (30, 225, 10))
    for x in range(35, 85):
        for y in range(25, 180):
            image.putpixel((x, y), (180, 60, 50))
    result, background = key(image)
    assert background == (30, 225, 10)
    assert result.getpixel((0, 0))[3] == 0
    assert result.getpixel((60, 90))[3] == 255
    blue = Image.new("RGB", (120, 180), (10, 30, 225))
    for x in range(35, 85):
        for y in range(25, 180):
            blue.putpixel((x, y), (20, 110, 45))
    blue_result, blue_background = key(blue)
    assert blue_background == (10, 30, 225)
    assert blue_result.getpixel((0, 0))[3] == 0
    assert blue_result.getpixel((60, 90))[3] == 255
    assert not metrics(result)["needs_review"]
    assert "CorridorKey QC" in review_html([{
        "stem": "test", "source": "/tmp/poc/raw/test.png", "qc": "/tmp/poc/qc/test.png",
        "avif": "/tmp/poc/assets/test.avif",
    }])
    assert "../raw/test.png" not in review_html([{
        "stem": "test", "source": "/tmp/poc/raw/test.png", "qc": "/tmp/poc/green/qc/test.png",
        "avif": "/tmp/poc/green/assets/test.avif",
    }], Path("/tmp/poc"))
    print("performer cutout exporter self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--inner", type=int, default=30)
    parser.add_argument("--outer", type=int, default=90)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.source_dir or not args.output_dir:
        parser.error("--source-dir and --output-dir are required")

    sources = sorted(args.source_dir.glob("*.png"))
    if not sources:
        raise ValueError(f"no PNG sources in {args.source_dir}")
    entries = []
    for source in sources:
        with Image.open(source) as opened:
            keyed, background = key(opened, args.inner, args.outer)
        keyed_path = args.output_dir / "keyed" / source.name
        save_png(keyed, keyed_path)
        asset = keyed.resize((600, 900), Image.Resampling.LANCZOS)
        avif = args.output_dir / "assets" / f"{source.stem}.avif"
        save_avif(asset, avif)
        entries.append({
            "stem": source.stem,
            "source": str(source),
            "source_sha256": sha256(source),
            "background_rgb": background,
            **metrics(keyed),
            "keyed": str(keyed_path),
            "avif": str(avif),
        })
        print(f"[{len(entries)}/{len(sources)}] {source.name}")

    manifest = {
        "version": 1,
        "key": {"method": "corner-sampled chroma key", "inner": args.inner, "outer": args.outer},
        "entries": entries,
    }
    atomic_text(args.output_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    atomic_text(args.output_dir / "review.html", review_html(entries))
    print(f"exported {len(entries)} performer cutouts to {args.output_dir}")


if __name__ == "__main__":
    main()
