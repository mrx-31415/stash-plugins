#!/usr/bin/env python3
"""Generate the minimal shared-carrier head/wardrobe PoC."""

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
SAM_MODEL = "sam3.1_multiplex_fp16.safetensors"
SAM_PROMPT = "human head, face, hair and neck"
REMOTE_PREFIX = "cover-story/green-carrier-poc-v8"

BASE_PROMPT = (
    "Make only the green head and neck completely matte and uniformly green, with no shine or reflections. "
    "Keep the realistic bald human anatomy and everything else unchanged."
)
HEAD_PROMPT = (
    "Green mannequin is reference. Keep body and background. Replace green head with the head, neck and hair of the woman. "
    "Keep the replacement head proportional to the mannequin's body."
)
REVERSE_HEAD_PROMPT = (
    "Put the woman from image 1 in the green suit and full-body pose from image 2. Keep her face and hair."
)
OUTFITS = {
    "viking-rust": "Put the green mannequin in rust-brown Viking clothing. Leave the head green and keep the composition unchanged.",
    "viking-dark-brown": "Put the green female mannequin in dark-brown Viking clothing. Leave the head green and keep the composition unchanged.",
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def graph(source, prompt, seed, prefix, reference=None):
    nodes = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODEL, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT_ENCODER, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.1}},
        "5": {"class_type": "LoadImage", "inputs": {"image": source}},
        "6": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["5", 0]}},
        "7": {
            "class_type": "TextEncodeQwenImageEditPlus",
            "inputs": {"clip": ["2", 0], "prompt": "", "vae": ["3", 0], "image1": ["6", 0]},
        },
        "8": {
            "class_type": "TextEncodeQwenImageEditPlus",
            "inputs": {"clip": ["2", 0], "prompt": prompt, "vae": ["3", 0], "image1": ["6", 0]},
        },
        "9": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"reference_latents_method": "index_timestep_zero", "conditioning": ["7", 0]}},
        "10": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"reference_latents_method": "index_timestep_zero", "conditioning": ["8", 0]}},
        "11": {"class_type": "CFGNorm", "inputs": {"strength": 1.0, "pre_cfg": False, "model": ["4", 0]}},
        "12": {"class_type": "VAEEncode", "inputs": {"pixels": ["6", 0], "vae": ["3", 0]}},
        "13": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["11", 0], "seed": seed, "steps": 40, "cfg": 4.0,
                "sampler_name": "euler", "scheduler": "simple", "positive": ["10", 0],
                "negative": ["9", 0], "latent_image": ["12", 0], "denoise": 1.0,
            },
        },
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["3", 0]}},
        "15": {"class_type": "SaveImage", "inputs": {"images": ["14", 0], "filename_prefix": prefix}},
    }
    if reference:
        nodes["16"] = {"class_type": "LoadImage", "inputs": {"image": reference}}
        nodes["7"]["inputs"]["image2"] = ["16", 0]
        nodes["8"]["inputs"]["image2"] = ["16", 0]
    return nodes


def mask_graph(source, prefix):
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": SAM_MODEL}},
        "2": {"class_type": "LoadImage", "inputs": {"image": source}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": SAM_PROMPT, "clip": ["1", 1]}},
        "4": {
            "class_type": "SAM3_Detect",
            "inputs": {
                "threshold": 0.5, "refine_iterations": 2, "individual_masks": False,
                "model": ["1", 0], "image": ["2", 0], "conditioning": ["3", 0],
            },
        },
        "5": {"class_type": "MaskToImage", "inputs": {"mask": ["4", 0]}},
        "6": {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": prefix}},
    }


def generate(server, source, reference, prompt, seed, slug, output, work, cache, force):
    if output.is_file() and not force:
        return
    source_remote = cache.setdefault(str(source.resolve()), upload_image(
        server, source, subfolder=f"{REMOTE_PREFIX}/input", timeout=300,
    ))
    reference_remote = None
    if reference:
        reference_remote = cache.setdefault(str(reference.resolve()), upload_image(
            server, reference, subfolder=f"{REMOTE_PREFIX}/input", timeout=300,
        ))
    result_dir = Path(tempfile.mkdtemp(prefix=f"qwen-{slug}-", dir=work))
    result = run(server, graph(source_remote, prompt, seed, f"{REMOTE_PREFIX}/{slug}", reference_remote), result_dir, 1800)
    matches = [Path(item["path"]) for item in result["images"] if slug in Path(item["path"]).stem]
    if len(matches) != 1:
        raise RuntimeError(f"expected one output for {slug}, found {len(matches)}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(matches[0]) as image:
        image.convert("RGB").save(output, "PNG", compress_level=1)


def generate_mask(server, source, output, work, cache, force):
    if output.is_file() and not force:
        return
    remote = cache.setdefault(str(source.resolve()), upload_image(
        server, source, subfolder=f"{REMOTE_PREFIX}/input", timeout=300,
    ))
    result_dir = Path(tempfile.mkdtemp(prefix=f"sam3-{source.stem}-", dir=work))
    result = run(server, mask_graph(remote, f"{REMOTE_PREFIX}/sam3/{source.stem}"), result_dir, 1800)
    matches = [Path(item["path"]) for item in result["images"] if source.stem in Path(item["path"]).stem]
    if len(matches) != 1:
        raise RuntimeError(f"expected one SAM3 mask for {source.stem}, found {len(matches)}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(matches[0]) as image:
        image.convert("L").save(output, "PNG", compress_level=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=os.environ.get("COMFY_SERVER"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--performer-430", type=Path, required=True)
    parser.add_argument("--performer-266", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--base-only", action="store_true")
    parser.add_argument("--heads-only", action="store_true")
    parser.add_argument("--reverse-head", action="store_true")
    parser.add_argument("--masks-only", action="store_true")
    parser.add_argument("--actor", choices=("430", "266"))
    args = parser.parse_args()
    if not args.server:
        parser.error("--server is required")
    for path in (args.source, args.performer_430, args.performer_266):
        if not path.is_file():
            parser.error(f"missing input: {path}")

    root = args.output_dir
    work = root / "_work"
    work.mkdir(parents=True, exist_ok=True)
    generated = root / "generated"
    cache = {}
    entries = []

    if args.masks_only:
        for actor in (args.actor,) if args.actor else ("430", "266"):
            source = generated / f"head-{actor}.png"
            if not source.is_file():
                parser.error(f"missing head render: {source}")
            output = root / "sam3" / source.name
            generate_mask(args.server, source, output, work, cache, args.force)
            with Image.open(output) as image:
                entries.append({
                    "actor": actor, "source": str(source), "source_sha256": sha256(source),
                    "path": str(output), "sha256": sha256(output), "dimensions": list(image.size),
                })
            print(output, flush=True)
        (root / "sam3-manifest.json").write_text(json.dumps({
            "version": 1, "detector": "SAM3_Detect", "checkpoint": SAM_MODEL,
            "prompt": SAM_PROMPT, "threshold": 0.5, "refine_iterations": 2, "entries": entries,
        }, indent=2) + "\n")
        return

    jobs = [("base-carrier", args.source, None, BASE_PROMPT, 2026073101)]
    jobs.extend((f"head-{actor}", root / "generated" / "base-carrier.png", portrait, HEAD_PROMPT, seed)
                for actor, portrait, seed in (("430", args.performer_430, 2026073110), ("266", args.performer_266, 2026073111)))
    jobs.extend((slug, root / "generated" / "base-carrier.png", None, prompt, seed)
                for (slug, prompt), seed in zip(OUTFITS.items(), (2026073120, 2026073121)))
    if args.reverse_head:
        if not args.actor:
            parser.error("--reverse-head requires --actor")
        portrait, seed = ((args.performer_430, 2026073110) if args.actor == "430" else (args.performer_266, 2026073111))
        jobs = [(f"reverse-head-{args.actor}", portrait, root / "generated" / "base-carrier.png",
                 REVERSE_HEAD_PROMPT, seed)]
    elif args.base_only:
        jobs = jobs[:1]
    elif args.heads_only:
        jobs = jobs[1:3]
        if args.actor:
            jobs = [job for job in jobs if job[0] == f"head-{args.actor}"]

    for slug, source, reference, prompt, seed in jobs:
        output = generated / f"{slug}.png"
        generate(args.server, source, reference, prompt, seed, slug, output, work, cache, args.force)
        with Image.open(output) as image:
            entries.append({
                "slug": slug, "path": str(output), "sha256": sha256(output),
                "dimensions": list(image.size), "seed": seed, "prompt": prompt,
                "source": str(source), "source_sha256": sha256(source),
                "reference": str(reference) if reference else None,
            })
        print(output, flush=True)
    (root / "generation-manifest.json").write_text(json.dumps({
        "version": 1, "model": MODEL, "conditioning_node": "TextEncodeQwenImageEditPlus",
        "text_encoder": TEXT_ENCODER, "vae": VAE, "steps": 40, "cfg": 4.0,
        "sampler": "euler", "scheduler": "simple", "canvas": [WIDTH, HEIGHT],
        "reference_latents_method": "index_timestep_zero",
        "carrier": str(args.source), "carrier_sha256": sha256(args.source), "entries": entries,
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
