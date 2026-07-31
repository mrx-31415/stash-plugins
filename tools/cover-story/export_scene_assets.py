#!/usr/bin/env python3
"""Validate, export and precompose Cover Story scene assets."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save(image, path, **options):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
    try:
        image.save(temporary, "WEBP", method=6, **options)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_avif(image, path, quality=70, alpha_quality=100, yuv=None):
    encoder = shutil.which("avifenc")
    if not encoder:
        raise RuntimeError("avifenc is required to export scene layers")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as directory:
        temporary = Path(directory)
        source = temporary / "source.png"
        output = temporary / "output.avif"
        image.save(source, "PNG")
        command = [encoder, "-q", str(quality), "--speed", "6"]
        if alpha_quality is not None:
            command += ["--qalpha", str(alpha_quality)]
        if yuv:
            command += ["--yuv", yuv]
        subprocess.run(command + [str(source), str(output)], check=True, stdout=subprocess.DEVNULL)
        os.replace(output, path)


def actor_image(source, height=790):
    image = Image.open(source).convert("RGBA")
    if image.getchannel("A").getextrema() != (0, 255):
        raise ValueError(f"actor source lacks full transparency: {source}")
    image = image.crop(image.getbbox())
    width = round(image.width * height / image.height)
    return image.resize((width, height), Image.Resampling.LANCZOS)


def grade(image, name):
    if not name:
        return image
    color = {"warm": (255, 218, 175, 255), "night": (170, 195, 230, 255)}[name]
    rgb = ImageChops.multiply(image.convert("RGB"), Image.new("RGB", image.size, color[:3]))
    rgb.putalpha(image.getchannel("A"))
    return rgb


def composite(background, placements, actors, actor_grade=None):
    result = background.copy().convert("RGBA")
    for actor_id, x in placements:
        actor = grade(actors[actor_id], actor_grade)
        y = result.height - actor.height + 18
        shadow = Image.new("RGBA", result.size)
        draw = ImageDraw.Draw(shadow)
        draw.ellipse((x + 25, result.height - 27, x + actor.width - 25, result.height - 5), fill=(0, 0, 0, 85))
        result.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(12)))
        result.alpha_composite(actor, (x, y))
    return result.convert("RGB")


def self_test():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "actor.png"
        test = Image.new("RGBA", (200, 300))
        ImageDraw.Draw(test).rectangle((50, 30, 149, 299), fill="white")
        test.save(source)
        actor = actor_image(source, 270)
        result = composite(Image.new("RGB", (800, 450), "navy"), [["actor", 200]], {"actor": actor})
        assert actor.height == 270 and result.size == (800, 450)
        output = root / "actor.avif"
        save_avif(actor, output)
        assert b"ftypavif" in output.read_bytes()[:32]
    print("scene exporter self-check passed")


def main():
    tool_root = Path(__file__).resolve().parent
    repo_root = tool_root.parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=tool_root / "scene-assets.json")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--plugin-root", type=Path, default=repo_root / "plugins/cover-story")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return

    manifest = json.loads(args.manifest.read_text())
    source_root = args.source_root or Path(manifest["source_root"])
    total = 0
    for theme_id, theme in manifest["themes"].items():
        theme_root = args.plugin_root / "assets" / "themes" / theme_id
        actors = {}
        for entry in theme["actors"]:
            source = source_root / entry["source"]
            if not source.is_file() or sha256(source) != entry["source_sha256"]:
                raise ValueError(f"missing or changed actor source: {source}")
            image = actor_image(source)
            destination = theme_root / "actors" / entry["output"]
            save(image, destination, lossless=True)
            avif = destination.with_suffix(".avif")
            save_avif(image, avif)
            actors[entry["id"]] = image
            total += destination.stat().st_size + avif.stat().st_size

        backgrounds = {}
        for entry in theme["backgrounds"]:
            source = source_root / entry["source"]
            if not source.is_file() or sha256(source) != entry["source_sha256"]:
                raise ValueError(f"missing or changed background source: {source}")
            with Image.open(source) as opened:
                image = ImageOps.fit(opened.convert("RGB"), (1920, 1080), Image.Resampling.LANCZOS)
            destination = theme_root / "backgrounds" / entry["output"]
            save(image, destination, quality=82)
            avif = destination.with_suffix(".avif")
            save_avif(image, avif)
            backgrounds[entry["id"]] = image.resize((1600, 900), Image.Resampling.LANCZOS)
            total += destination.stat().st_size + avif.stat().st_size

        expected_covers = set()
        for entry in theme["covers"]:
            destination = theme_root / "covers" / entry["output"]
            image = composite(backgrounds[entry["background"]], entry["actors"], actors, entry.get("grade"))
            save(image, destination, quality=82)
            expected_covers.add(destination)
            total += destination.stat().st_size
        for stale in (theme_root / "covers").glob("*.webp"):
            if stale not in expected_covers:
                stale.unlink()

    print(f"exported scene assets ({total / 1024 / 1024:.1f} MiB)")


if __name__ == "__main__":
    main()
