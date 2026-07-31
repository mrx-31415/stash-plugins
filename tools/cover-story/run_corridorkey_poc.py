#!/usr/bin/env python3
"""Refine performer chroma keys with a remote ComfyUI CorridorKey node."""

import argparse
import json
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter

from comfy import api, run, upload_image
from export_performer_cutouts import key, review_html, save_png
from export_personas import atomic_text, sha256
from export_scene_assets import save_avif


def workflow(image, hint, stem, screen="green"):
    if screen not in {"green", "blue"}:
        raise ValueError(f"unsupported screen: {screen}")
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image}},
        "2": {"class_type": "LoadImage", "inputs": {"image": hint}},
        "3": {"class_type": "ImageToMask", "inputs": {"image": ["2", 0], "channel": "red"}},
        "4": {"class_type": "CorridorKey", "inputs": {
            "image": ["1", 0], "mask": ["3", 0], "gamma_space": "sRGB",
            "despill_strength": 0.0 if screen == "blue" else 1.0, "refiner_strength": 1.0,
            "auto_despeckle": "On", "despeckle_size": 400,
        }},
        "5": {"class_type": "SaveImage", "inputs": {
            "images": ["4", 0], "filename_prefix": f"cover-story/corridorkey/{stem}_fg",
        }},
        "6": {"class_type": "MaskToImage", "inputs": {"mask": ["4", 1]}},
        "7": {"class_type": "SaveImage", "inputs": {
            "images": ["6", 0], "filename_prefix": f"cover-story/corridorkey/{stem}_matte",
        }},
        "8": {"class_type": "SaveImage", "inputs": {
            "images": ["4", 3], "filename_prefix": f"cover-story/corridorkey/{stem}_qc",
        }},
    }


def pick(result, marker):
    matches = [
        Path(image["path"]) for image in result["images"]
        if marker in Path(image["path"]).stem
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one {marker} output, found {len(matches)}")
    return matches[0]


def coarse_hint(source):
    keyed, _ = key(source)
    return keyed.getchannel("A").filter(ImageFilter.MinFilter(9)).filter(ImageFilter.GaussianBlur(4))


def complete(*paths):
    return all(path.is_file() for path in paths)


def output_paths(output_dir, stem):
    return {
        "corridorkey": output_dir / "corridorkey" / f"{stem}.png",
        "qc": output_dir / "qc" / f"{stem}.png",
        "avif": output_dir / "assets" / f"{stem}.avif",
    }


def encode_avif(rgba_path, avif_path):
    with Image.open(rgba_path) as opened:
        asset = opened.convert("RGBA").resize((600, 900), Image.Resampling.LANCZOS)
    save_avif(asset, avif_path)


def process_source(
    server, source, output_dir, timeout=1800, stem=None, queued_event=None, screen="green",
):
    stem = stem or source.stem
    paths = output_paths(output_dir, stem)
    if not complete(paths["corridorkey"], paths["qc"]):
        with Image.open(source) as opened:
            rgb = opened.convert("RGB")
            hint = coarse_hint(rgb)
            with tempfile.TemporaryDirectory(prefix="cover-story-corridorkey-") as directory:
                temporary = Path(directory)
                hint_path = temporary / f"{stem}_hint.png"
                save_png(hint, hint_path)
                remote_image = upload_image(server, source, timeout=timeout)
                remote_hint = upload_image(server, hint_path, timeout=timeout)
                result = run(
                    server, workflow(remote_image, remote_hint, stem, screen),
                    temporary / "result", timeout, queued_event=queued_event,
                )
                with Image.open(pick(result, "_fg_")) as foreground, Image.open(pick(result, "_matte_")) as matte:
                    rgba = foreground.convert("RGB")
                    rgba.putalpha(matte.convert("L"))
                with Image.open(pick(result, "_qc_")) as qc:
                    save_png(qc.convert("RGB"), paths["qc"])
        save_png(rgba, paths["corridorkey"])
    if not paths["avif"].is_file():
        encode_avif(paths["corridorkey"], paths["avif"])
    return {
        "stem": stem,
        "source": str(source),
        "source_sha256": sha256(source),
        **{name: str(path) for name, path in paths.items()},
    }


def self_test():
    graph = workflow("input.png", "hint.png", "test")
    assert graph["4"]["class_type"] == "CorridorKey"
    assert graph["4"]["inputs"]["mask"] == ["3", 0]
    assert graph["4"]["inputs"]["despill_strength"] == 1.0
    assert workflow("input.png", "hint.png", "test", "blue")["4"]["inputs"]["despill_strength"] == 0.0
    image = Image.new("RGB", (120, 180), (30, 225, 10))
    for x in range(35, 85):
        for y in range(25, 180):
            image.putpixel((x, y), (180, 60, 50))
    hint = coarse_hint(image)
    assert hint.getpixel((0, 0)) == 0 and hint.getpixel((60, 90)) > 200
    blue = Image.new("RGB", (120, 180), (10, 30, 225))
    for x in range(35, 85):
        for y in range(25, 180):
            blue.putpixel((x, y), (180, 60, 50))
    blue_hint = coarse_hint(blue)
    assert blue_hint.getpixel((0, 0)) == 0 and blue_hint.getpixel((60, 90)) > 200
    with tempfile.TemporaryDirectory() as directory:
        paths = [Path(directory) / name for name in ("rgba", "qc", "avif")]
        assert not complete(*paths)
        for path in paths:
            path.touch()
        assert complete(*paths)
    print("CorridorKey PoC runner self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server")
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not all((args.server, args.source_dir, args.output_dir)):
        parser.error("--server, --source-dir and --output-dir are required")

    node = api(args.server, "/object_info/CorridorKey")
    if "CorridorKey" not in node:
        raise RuntimeError("ComfyUI does not expose CorridorKey after restart")
    sources = sorted(args.source_dir.glob("*.png"))
    if not sources:
        raise ValueError(f"no PNG sources in {args.source_dir}")

    entries = []
    for index, source in enumerate(sources, 1):
        stem = source.stem
        paths = output_paths(args.output_dir, stem)
        if complete(*paths.values()):
            print(f"[{index}/{len(sources)}] skip {source.name}", flush=True)
        else:
            print(f"[{index}/{len(sources)}] {source.name}", flush=True)
        entries.append(process_source(args.server, source, args.output_dir, args.timeout))

    manifest = {
        "version": 1,
        "processor": "CorridorKey",
        "settings": {
            "gamma_space": "sRGB", "despill_strength": 1.0, "refiner_strength": 1.0,
            "auto_despeckle": "On", "despeckle_size": 400,
        },
        "entries": entries,
    }
    atomic_text(args.output_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    atomic_text(args.output_dir / "review.html", review_html(entries))
    print(f"exported {len(entries)} CorridorKey performers to {args.output_dir}")


if __name__ == "__main__":
    main()
