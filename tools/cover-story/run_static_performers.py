#!/usr/bin/env python3
"""Generate and export opaque Cover Story performer portraits."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from comfy import prepare, run
from experiment_headshots import (
    PRODUCTION_EXPANSION,
    configure_filter_bypass,
    configure_guidance,
    generation_prompt,
)
from export_performer_cutouts import review_html
from export_personas import atomic_text, crop, sha256
from export_scene_assets import save_avif
from performer_palettes import WARDROBE_PALETTES


VERSION = 1
BASE_SEED = 2026072700
PILOT_VARIANTS = (67, 14, 87, 40, 59, 48)
WORKFLOW = Path(__file__).resolve().parent / "workflows" / "krea2-turbo-fp8.json"
RECIPE = {
    "model": "krea2_turbo_fp8_scaled.safetensors",
    "clip": "qwen3vl_4b_fp8_scaled.safetensors",
    "vae": "qwen_image_vae.safetensors",
    "steps": 12,
    "cfg": 1,
    "negative_prompt": None,
    "age_wording": "band",
    "posture": "upright",
}
ASSET = {
    "format": "AVIF",
    "width": 600,
    "height": 900,
    "speed": 6,
    "alpha": False,
}
BACKGROUND_BLUR = (
    "Keep the environment strongly defocused with smooth creamy bokeh, preserving only broad "
    "shapes and color gradients without fine background texture"
)


def select(variants, start, stop):
    numbers = variants or list(range(start, (stop or len(PRODUCTION_EXPANSION)) + 1))
    if not numbers or any(number < 1 or number > len(PRODUCTION_EXPANSION) for number in numbers):
        raise ValueError(f"variants must be between 1 and {len(PRODUCTION_EXPANSION)}")
    return numbers


def details(number, background_blur=False):
    profile, _, seed_offset, style = PRODUCTION_EXPANSION[number - 1]
    wardrobe = WARDROBE_PALETTES[style["wardrobe"]]
    environment = style["background"]
    if background_blur:
        environment = f"{environment}. {BACKGROUND_BLUR}"
    return {
        "slot": number,
        "id": f"performer-{number:03d}",
        "stem": f"performer-{number:03d}",
        "name": profile["name"],
        "slug": profile["slug"],
        "seed": BASE_SEED + seed_offset,
        "crop": style["composition"],
        "wardrobe": wardrobe,
        "environment": environment,
        "prompt": generation_prompt(
            profile,
            style,
            background=environment,
            age_wording="band",
            wardrobe=wardrobe,
            standing=True,
        ),
    }


def configured_workflow(entry, label, bypass_strength=1.5):
    workflow = json.loads(WORKFLOW.read_text())
    samplers = [node for node in workflow.values() if "KSampler" in node.get("class_type", "")]
    if len(samplers) != 1:
        raise ValueError(f"expected one sampler, found {len(samplers)}")
    samplers[0]["inputs"]["steps"] = RECIPE["steps"]
    configure_guidance(workflow, RECIPE["cfg"])
    configure_filter_bypass(workflow, bypass_strength)
    return prepare(
        workflow,
        entry["prompt"],
        entry["seed"],
        f"cover-story/{label}/{entry['id']}_",
    )


def generate(server, raw, entry, label, timeout, bypass_strength):
    raw.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=raw.parent) as directory:
        result = run(
            server,
            configured_workflow(entry, label, bypass_strength),
            Path(directory),
            timeout,
        )
        if len(result["images"]) != 1 or result["images"][0]["format"] != "PNG":
            raise ValueError(f"expected one PNG from ComfyUI: {result}")
        os.replace(result["images"][0]["path"], raw)
    return result["prompt_id"]


def inspect_avif(path, yuv=None):
    result = subprocess.run(
        ["avifdec", "--info", str(path)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ).stdout
    if "Resolution     : 600x900" not in result or "Alpha          : Absent" not in result:
        raise ValueError(f"export must be opaque 600x900: {path}\n{result}")
    if yuv and f"Format         : YUV{yuv}" not in result:
        raise ValueError(f"export must use YUV{yuv}: {path}\n{result}")
    return [600, 900]


def encode(raw, avif, quality=60, yuv="420"):
    with Image.open(raw) as opened:
        source_dimensions = list(opened.size)
        image = crop(ImageOps.exif_transpose(opened).convert("RGB"), 0.5, 0.5, 600, 900)
    save_avif(image, avif, quality=quality, alpha_quality=None, yuv=yuv)
    return source_dimensions, inspect_avif(avif, yuv)


def recipe(bypass_strength, background_blur=False):
    selected = {**RECIPE, "filter_bypass": bypass_strength}
    if background_blur:
        selected["background_blur"] = BACKGROUND_BLUR
    return selected


def asset(quality, yuv):
    selected = {**ASSET, "quality": quality}
    if yuv:
        selected["yuv"] = yuv
    return selected


def load_manifest(path, expected_recipe, expected_asset):
    if not path.exists():
        return {}
    manifest = json.loads(path.read_text())
    if (
        manifest.get("version") != VERSION
        or manifest.get("base_seed") != BASE_SEED
        or manifest.get("recipe") != expected_recipe
        or manifest.get("asset") != expected_asset
    ):
        raise ValueError(f"incompatible static performer manifest: {path}")
    return {entry["slot"]: entry for entry in manifest["entries"]}


def write_manifest(output_dir, entries, selected_recipe, selected_asset):
    ordered = [entries[number] for number in sorted(entries)]
    manifest = {
        "version": VERSION,
        "target_count": len(PRODUCTION_EXPANSION),
        "base_seed": BASE_SEED,
        "recipe": selected_recipe,
        "asset": selected_asset,
        "entries": ordered,
    }
    atomic_text(output_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    html = review_html(ordered, output_dir).replace(
        "Performer cutout PoC", "Static performer portraits"
    ).replace(">cutout<", ">portrait<")
    atomic_text(output_dir / "review.html", html)


def complete(entry, yuv=None):
    raw, avif = Path(entry["raw"]), Path(entry["avif"])
    if not raw.is_file() or not avif.is_file():
        return False
    if sha256(raw) != entry.get("source_sha256") or sha256(avif) != entry.get("avif_sha256"):
        raise ValueError(f"completed files changed for {entry['id']}")
    inspect_avif(avif, yuv)
    return True


def self_test():
    assert len(PRODUCTION_EXPANSION) == 500
    assert select(None, 499, None) == [499, 500]
    assert select([1, 500], 1, None) == [1, 500]
    prompts = [details(number)["prompt"] for number in range(1, 501)]
    assert len(set(prompts)) == 500
    assert all("Standing upright." in prompt for prompt in prompts)
    assert all(
        details(number)["wardrobe"] == WARDROBE_PALETTES[
            PRODUCTION_EXPANSION[number - 1][3]["wardrobe"]
        ]
        for number in range(1, 501)
    )
    assert not any("cape" in wardrobe.lower() for wardrobe in WARDROBE_PALETTES.values())
    assert BACKGROUND_BLUR in details(1, background_blur=True)["prompt"]
    workflow = configured_workflow(details(1), "self-test")
    sampler = next(node for node in workflow.values() if node.get("class_type") == "KSampler")
    bypass = next(
        node for node in workflow.values()
        if node.get("class_type") == "LoraLoaderModelOnly"
    )
    assert (sampler["inputs"]["steps"], sampler["inputs"]["cfg"]) == (12, 1)
    assert bypass["inputs"]["strength_model"] == 1.5
    assert workflow["7"]["inputs"]["text"] == ""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        raw, avif = root / "raw.png", root / "asset.avif"
        Image.new("RGBA", (1024, 1536), (10, 20, 30, 80)).save(raw)
        assert encode(raw, avif) == ([1024, 1536], [600, 900])
    print("static performer runner self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--label", default="static-performer-production-v1")
    parser.add_argument("--variant", type=int, action="append")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--stop", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--codec-only", action="store_true")
    parser.add_argument("--bypass-strength", type=float, choices=(1.0, 1.5), default=1.5)
    parser.add_argument("--background-blur", action="store_true")
    parser.add_argument("--quality", type=int, choices=range(1, 101), default=60)
    parser.add_argument("--yuv", choices=("auto", "420"), default="420")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.output_dir:
        parser.error("--output-dir is required")
    if not args.server and not args.codec_only and not args.dry_run:
        parser.error("--server is required unless --codec-only or --dry-run is used")
    try:
        selected = select(args.variant, args.start, args.stop)
    except ValueError as exc:
        parser.error(str(exc))

    manifest_path = args.output_dir / "manifest.json"
    if args.output_dir.exists() and any(args.output_dir.iterdir()) and not manifest_path.exists():
        raise ValueError(f"refusing nonempty output directory without manifest: {args.output_dir}")
    selected_recipe = recipe(args.bypass_strength, args.background_blur)
    selected_yuv = None if args.yuv == "auto" else args.yuv
    selected_asset = asset(args.quality, selected_yuv)
    entries = load_manifest(manifest_path, selected_recipe, selected_asset)
    for position, number in enumerate(selected, 1):
        entry = details(number, args.background_blur)
        previous = entries.get(number)
        if previous:
            if any(previous.get(key) != entry[key] for key in ("seed", "prompt", "wardrobe")):
                raise ValueError(f"catalog details changed for {entry['id']}")
            entry.update(previous)
        raw = args.output_dir / "raw" / f"{entry['id']}.png"
        avif = args.output_dir / "assets" / f"{entry['id']}.avif"
        entry.update({"raw": str(raw), "avif": str(avif)})
        print(f"[{position}/{len(selected)}] {entry['id']} — {entry['name']}", flush=True)
        if args.dry_run:
            print(entry["prompt"])
            continue
        if previous and complete(entry, selected_yuv):
            print("already complete", flush=True)
            continue
        if raw.exists() and previous and sha256(raw) != previous.get("source_sha256"):
            raise ValueError(f"raw image changed for {entry['id']}")
        if not raw.exists():
            if args.codec_only:
                raise FileNotFoundError(f"missing raw image: {raw}")
            entry["prompt_id"] = generate(
                args.server, raw, entry, args.label, args.timeout, args.bypass_strength
            )
        if avif.exists():
            inspect_avif(avif, selected_yuv)
            with Image.open(raw) as opened:
                source_dimensions = list(opened.size)
        else:
            source_dimensions, export_dimensions = encode(
                raw, avif, args.quality, selected_yuv
            )
            entry["dimensions"] = export_dimensions
        entry.update({
            "source_sha256": sha256(raw),
            "source_dimensions": source_dimensions,
            "avif_sha256": sha256(avif),
            "dimensions": entry.get("dimensions", [600, 900]),
            "bytes": avif.stat().st_size,
        })
        entries[number] = entry
        write_manifest(args.output_dir, entries, selected_recipe, selected_asset)
    if not args.dry_run:
        print(f"manifest has {len(entries)}/{len(PRODUCTION_EXPANSION)} performers", flush=True)


if __name__ == "__main__":
    main()
