#!/usr/bin/env python3
"""Generate two headgear-free, fixed-neckline wardrobe variants."""

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from PIL import Image

from comfy import run, upload_image


WIDTH, HEIGHT = 832, 1248
MODEL = "qwen_image_edit_2511_int8_convrot.safetensors"
TEXT_ENCODER = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE = "qwen_image_vae.safetensors"
VARIANTS = (
    ("viking-crew-rust", "deep rust-brown fitted wool tunic with subtle dark leather trim"),
    ("viking-crew-forest", "deep forest-green fitted wool tunic with subtle muted-bronze trim"),
)
PROMPT = (
    "Change only the clothing in image 1. Keep the exact original camera, canvas, framing, "
    "full-body relaxed front pose, body proportions, head position, face, hair, neck, hands, "
    "green gloves, lighting, and green-screen background fixed and pixel-aligned. "
    "Bare head and natural hair: absolutely no helmet, hat, horns, crown, headgear, or hair ornament. "
    "Use {description}, with separate fitted dark tailored trousers and plain brown leather boots. "
    "Use one modest, closed crew neckline ending at the base of the neck; keep this exact neckline "
    "shape and height identical across variants, with no V-neck, low neckline, scarf, fur collar, "
    "cape, armor, jewelry, weapon, prop, logo, or extra layer. Do not change the head, face, neck, "
    "hands, pose, framing, or background."
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def graph(remote_source, prompt, seed, prefix):
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODEL, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT_ENCODER, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.1}},
        "5": {"class_type": "LoadImage", "inputs": {"image": remote_source}},
        "6": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "prompt": prompt, "vae": ["3", 0], "image1": ["5", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
        "8": {"class_type": "EmptyLatentImage", "inputs": {"width": WIDTH, "height": HEIGHT, "batch_size": 1}},
        "9": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["4", 0], "seed": seed, "steps": 20, "cfg": 1.0,
                "sampler_name": "euler", "scheduler": "simple", "positive": ["6", 0],
                "negative": ["7", 0], "latent_image": ["8", 0], "denoise": 1.0,
            },
        },
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": prefix}},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=os.environ.get("COMFY_SERVER"), required=False)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not args.server:
        parser.error("--server is required")
    if not args.source.is_file():
        parser.error(f"missing source: {args.source}")

    generated = args.output_dir / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    remote_source = upload_image(args.server, args.source, subfolder="cover-story/wardrobe-neckline-test-v1/input")
    entries = []
    for index, (slug, description) in enumerate(VARIANTS):
        output = generated / f"{slug}.png"
        prompt = PROMPT.format(description=description)
        seed = 2026073100 + index
        if args.force or not output.is_file():
            result_dir = Path(tempfile.mkdtemp(prefix=f"qwen-{slug}-", dir=args.output_dir))
            result = run(args.server, graph(remote_source, prompt, seed, f"cover-story/wardrobe-neckline-test-v1/{slug}"), result_dir, 1800)
            matches = [Path(item["path"]) for item in result["images"] if slug in Path(item["path"]).stem]
            if len(matches) != 1:
                raise RuntimeError(f"expected one output for {slug}, found {len(matches)}")
            with Image.open(matches[0]) as image:
                image.convert("RGB").save(output, "PNG", compress_level=1)
        with Image.open(output) as image:
            entries.append({
                "slug": slug, "path": str(output), "sha256": sha256(output),
                "dimensions": list(image.size), "seed": seed, "prompt": prompt,
            })
        print(output, flush=True)
    (args.output_dir / "generation-manifest.json").write_text(json.dumps({
        "version": 1, "source": str(args.source), "source_sha256": sha256(args.source),
        "model": MODEL, "conditioning_node": "TextEncodeQwenImageEditPlus",
        "text_encoder": TEXT_ENCODER, "vae": VAE,
        "steps": 20, "cfg": 1.0, "sampler": "euler", "scheduler": "simple",
        "canvas": [WIDTH, HEIGHT], "entries": entries,
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
