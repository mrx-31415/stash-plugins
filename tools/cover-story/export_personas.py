#!/usr/bin/env python3
"""Export curated Cover Story personas as plugin WebP assets and a browser manifest."""

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def crop(image, focal_x, focal_y, width, height):
    target_ratio = width / height
    source_ratio = image.width / image.height
    if source_ratio > target_ratio:
        crop_width, crop_height = round(image.height * target_ratio), image.height
    else:
        crop_width, crop_height = image.width, round(image.width / target_ratio)
    center_x, center_y = focal_x * image.width, focal_y * image.height
    left = min(max(round(center_x - crop_width / 2), 0), image.width - crop_width)
    top = min(max(round(center_y - crop_height / 2), 0), image.height - crop_height)
    return image.crop((left, top, left + crop_width, top + crop_height)).resize(
        (width, height), Image.Resampling.LANCZOS
    )


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as output:
        output.write(text)
        temporary = Path(output.name)
    os.replace(temporary, path)


def main():
    assert crop(Image.new("RGB", (1200, 1600)), 0.5, 0.5, 600, 900).size == (600, 900)
    tool_root = Path(__file__).resolve().parent
    repo_root = tool_root.parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=tool_root / "personas.json")
    parser.add_argument("--source-root", type=Path, default=Path("/mnt/Misc/sd/cover-story/experiments"))
    parser.add_argument("--plugin-root", type=Path, default=repo_root / "plugins/cover-story")
    parser.add_argument("--quality", type=int, default=75)
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text())
    personas = catalog["personas"]
    if len({persona["id"] for persona in personas}) != len(personas):
        raise ValueError("persona IDs are not unique")
    run = json.loads((tool_root / "runs" / f"{catalog['source_run']}.json").read_text())
    source_hashes = {entry["source"]: entry["source_sha256"] for entry in run["entries"]}
    output_root = args.plugin_root / "assets"
    runtime = []
    expected = {output_root / persona["archive"] for persona in personas}
    total_bytes = 0
    for persona in personas:
        source = args.source_root / persona["source"]
        if not source.is_file() or sha256(source) != source_hashes.get(persona["source"]):
            raise ValueError(f"missing or changed source image: {source}")
        destination = output_root / persona["archive"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        settings = persona["crop"]
        with Image.open(source) as opened:
            image = crop(
                ImageOps.exif_transpose(opened).convert("RGB"),
                settings["focal_x"], settings["focal_y"], settings["width"], settings["height"],
            )
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
                temporary = Path(output.name)
            try:
                image.save(temporary, "WEBP", quality=args.quality, method=6)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        total_bytes += destination.stat().st_size
        runtime.append({
            key: persona[key]
            for key in (
                "id", "name", "gender", "birthdate", "country", "ethnicity",
                "eye_color", "hair_color", "height_cm", "weight_kg",
                "measurements", "tattoos", "piercings",
            )
        })
        runtime[-1]["image_path"] = f"/plugin/cover-story/assets/{persona['archive']}"

    performer_root = output_root / "performers"
    for stale in performer_root.glob("*.webp"):
        if stale not in expected:
            stale.unlink()

    payload = json.dumps(runtime, ensure_ascii=False, separators=(",", ":"))
    manifest = (
        '(function(root){"use strict";const personas=' + payload + ";"
        'if(typeof module!=="undefined"&&module.exports)module.exports=personas;'
        "if(root)root.CoverStoryPersonas=personas;"
        '})(typeof window!=="undefined"?window:null);\n'
    )
    atomic_text(args.plugin_root / "personas.js", manifest)
    print(f"exported {len(runtime)} portraits ({total_bytes / 1024 / 1024:.1f} MiB)")
if __name__ == "__main__":
    main()
