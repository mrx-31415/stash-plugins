#!/usr/bin/env python3
"""Generate the standalone reusable-wardrobe layer PoC."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from comfy import run, upload_image


VERSION = 1
WIDTH, HEIGHT = 1024, 1536
MODEL_SETTINGS = {
    "unet": "qwen_image_edit_2509_fp8_e4m3fn.safetensors",
    "text_encoder": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
    "vae": "qwen_image_vae.safetensors",
    "sampling_shift": 3.1,
    "steps": 20,
    "cfg": 1.0,
    "sampler": "euler",
    "scheduler": "simple",
    "denoise": 1.0,
    "canvas": [WIDTH, HEIGHT],
}

OUTFITS = {
    "outfit-01": {
        "name": "navy wool and rust leather",
        "description": (
            "deep navy fitted wool tunic with a modest neckline and a visible "
            "rust-brown leather belt, charcoal tailored wool trousers, and plain "
            "brown leather ankle boots"
        ),
    },
    "outfit-02": {
        "name": "forest quilted wool and cream linen",
        "description": (
            "deep forest-green fitted quilted wool overshirt over a warm-cream "
            "linen blouse, dark-brown tailored trousers, and plain brown leather "
            "ankle boots"
        ),
    },
    "outfit-03": {
        "name": "oxblood wrap wool and bronze trim",
        "description": (
            "oxblood fitted wrap-front wool tunic with restrained muted-bronze "
            "trim, slate-gray tailored wool trousers, and plain brown leather "
            "ankle boots"
        ),
    },
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as output:
        json.dump(value, output, indent=2)
        output.write("\n")
        temporary = Path(output.name)
    os.replace(temporary, path)


def save_png(image, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".png", delete=False) as output:
        temporary = Path(output.name)
    try:
        image.save(temporary, "PNG", compress_level=1)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def ensure_png(source, destination):
    if destination.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".avif":
        subprocess.run(["avifdec", str(source), str(destination)], check=True, stdout=subprocess.DEVNULL)
    else:
        with Image.open(source) as opened:
            save_png(opened.convert("RGB"), destination)


def upload_cached(server, path, cache):
    key = str(path.resolve())
    if key not in cache:
        cache[key] = upload_image(
            server,
            path,
            subfolder="cover-story/wardrobe-layer-poc-v1/input",
            timeout=300,
        )
    return cache[key]


def output_from_run(server, graph, work_dir, marker, timeout):
    work_dir.parent.mkdir(parents=True, exist_ok=True)
    result_dir = Path(tempfile.mkdtemp(prefix=f"{work_dir.name}-", dir=work_dir.parent))
    result = run(server, graph, result_dir, timeout)
    matches = [Path(item["path"]) for item in result["images"] if marker in Path(item["path"]).stem]
    if len(matches) != 1:
        raise ValueError(f"expected one {marker} output, found {len(matches)}")
    return matches[0]


def qwen_graph(source, prompt, seed, remote_source, remote_pose=None, prefix="output"):
    nodes = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": MODEL_SETTINGS["unet"], "weight_dtype": "default"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": MODEL_SETTINGS["text_encoder"],
                "type": "qwen_image",
                "device": "default",
            },
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": MODEL_SETTINGS["vae"]}},
        "4": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"model": ["1", 0], "shift": MODEL_SETTINGS["sampling_shift"]},
        },
        "5": {"class_type": "LoadImage", "inputs": {"image": remote_source}},
        "8": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["2", 0]},
        },
        "9": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": WIDTH, "height": HEIGHT, "batch_size": 1},
        },
        "10": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["4", 0],
                "seed": seed,
                "steps": MODEL_SETTINGS["steps"],
                "cfg": MODEL_SETTINGS["cfg"],
                "sampler_name": MODEL_SETTINGS["sampler"],
                "scheduler": MODEL_SETTINGS["scheduler"],
                "positive": ["7", 0],
                "negative": ["8", 0],
                "latent_image": ["9", 0],
                "denoise": MODEL_SETTINGS["denoise"],
            },
        },
        "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["3", 0]}},
        "12": {
            "class_type": "SaveImage",
            "inputs": {"images": ["11", 0], "filename_prefix": prefix},
        },
    }
    if remote_pose:
        nodes["6"] = {"class_type": "LoadImage", "inputs": {"image": remote_pose}}
        nodes["7"] = {
            "class_type": "TextEncodeQwenImageEditPlus",
            "inputs": {
                "clip": ["2", 0],
                "prompt": prompt,
                "vae": ["3", 0],
                "image1": ["5", 0],
                "image2": ["6", 0],
            },
        }
    else:
        nodes["7"] = {
            "class_type": "TextEncodeQwenImageEdit",
            "inputs": {"clip": ["2", 0], "prompt": prompt, "vae": ["3", 0], "image": ["5", 0]},
        }
    return nodes


def pose_graph(remote_source, prefix):
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": remote_source}},
        "2": {
            "class_type": "OpenposePreprocessor",
            "inputs": {
                "image": ["1", 0],
                "detect_hand": "enable",
                "detect_body": "enable",
                "detect_face": "enable",
                "resolution": 1024,
                "scale_stick_for_xinsr_cn": "disable",
            },
        },
        "3": {
            "class_type": "DensePosePreprocessor",
            "inputs": {
                "image": ["1", 0],
                "model": "densepose_r50_fpn_dl.torchscript",
                "cmap": "Viridis (MagicAnimate)",
                "resolution": 1024,
            },
        },
        "4": {
            "class_type": "SaveImage",
            "inputs": {"images": ["2", 0], "filename_prefix": f"{prefix}-openpose"},
        },
        "5": {
            "class_type": "SaveImage",
            "inputs": {"images": ["3", 0], "filename_prefix": f"{prefix}-densepose"},
        },
    }


def mask_graph(remote_source, prefix):
    def detect(conditioning):
        return {
            "class_type": "SAM3_Detect",
            "inputs": {
                "threshold": 0.5,
                "refine_iterations": 2,
                "individual_masks": False,
                "model": ["1", 0],
                "image": ["2", 0],
                "conditioning": [conditioning, 0],
            },
        }

    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sam3.1_multiplex_fp16.safetensors"},
        },
        "2": {"class_type": "LoadImage", "inputs": {"image": remote_source}},
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "person", "clip": ["1", 1]},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "top garment or tunic", "clip": ["1", 1]},
        },
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "trousers or pants", "clip": ["1", 1]},
        },
        "6": detect("3"),
        "7": detect("4"),
        "8": detect("5"),
        "9": {"class_type": "MaskToImage", "inputs": {"mask": ["6", 0]}},
        "10": {"class_type": "MaskToImage", "inputs": {"mask": ["7", 0]}},
        "11": {"class_type": "MaskToImage", "inputs": {"mask": ["8", 0]}},
        "12": {
            "class_type": "SaveImage",
            "inputs": {"images": ["9", 0], "filename_prefix": f"{prefix}-person-mask"},
        },
        "13": {
            "class_type": "SaveImage",
            "inputs": {"images": ["10", 0], "filename_prefix": f"{prefix}-top-mask"},
        },
        "14": {
            "class_type": "SaveImage",
            "inputs": {"images": ["11", 0], "filename_prefix": f"{prefix}-bottom-mask"},
        },
    }


def flip_if_needed(image, mirrored):
    return image.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if mirrored else image


def run_pose_references(server, output, sources, cache, work, force=False):
    references = {}
    for pose, source in sources.items():
        stem = f"pose-{pose}"
        openpose = output / "references" / f"{stem}-openpose.png"
        densepose = output / "references" / f"{stem}-densepose.png"
        if force or not openpose.is_file() or not densepose.is_file():
            remote = upload_cached(server, source, cache)
            result_dir = Path(tempfile.mkdtemp(prefix=f"pose-{pose}-", dir=work))
            result = run(server, pose_graph(remote, f"cover-story/wardrobe-layer-poc-v1/{stem}"), result_dir, 1800)
            for item in result["images"]:
                name = Path(item["path"]).name
                if "openpose" in name:
                    shutil.copyfile(item["path"], openpose)
                elif "densepose" in name:
                    shutil.copyfile(item["path"], densepose)
        mirrored_openpose = output / "references" / f"{stem}-openpose-mirrored.png"
        mirrored_densepose = output / "references" / f"{stem}-densepose-mirrored.png"
        if pose == "right":
            with Image.open(openpose) as image:
                save_png(flip_if_needed(image.convert("RGB"), True), mirrored_openpose)
            with Image.open(densepose) as image:
                save_png(flip_if_needed(image.convert("RGB"), True), mirrored_densepose)
        references[pose] = {
            "source": source,
            "openpose": openpose,
            "densepose": densepose,
            "openpose_mirrored": mirrored_openpose if pose == "right" else openpose,
            "densepose_mirrored": mirrored_densepose if pose == "right" else densepose,
        }
    return references


def generate_qwen_asset(server, output, work, cache, source, pose, prompt, seed, slug, mirrored, force=False):
    if output.is_file() and not force:
        return
    remote_source = upload_cached(server, source, cache)
    remote_pose = upload_cached(server, pose, cache) if pose else None
    result_dir = work / f"qwen-{slug}"
    raw = output_from_run(
        server,
        qwen_graph(
            source,
            prompt,
            seed,
            remote_source,
            remote_pose,
            f"cover-story/wardrobe-layer-poc-v1/{slug}",
        ),
        result_dir,
        slug,
        1800,
    )
    with Image.open(raw) as image:
        save_png(flip_if_needed(image.convert("RGB"), mirrored), output)


def mask_fraction(mask):
    return sum(value > 16 for value in mask.getdata()) / (mask.width * mask.height)


def cleanup_mask(mask, person, kind):
    mask = mask.convert("L").point(lambda value: 255 if value > 16 else 0)
    person = person.convert("L").point(lambda value: 255 if value > 16 else 0)
    mask = ImageChops.multiply(mask, person).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(0.6))
    clipped = Image.new("L", mask.size)
    draw = ImageDraw.Draw(clipped)
    if kind == "top":
        draw.rectangle((0, round(mask.height * 0.20), mask.width, round(mask.height * 0.70)), fill=255)
    else:
        draw.rectangle((0, round(mask.height * 0.42), mask.width, round(mask.height * 0.88)), fill=255)
    mask = ImageChops.multiply(mask, clipped)
    if not mask.getbbox() or mask_fraction(mask) < 0.002:
        # ponytail: fixed y-band fallback; replace with hand-drawn masks if SAM3 misses a garment.
        mask = ImageChops.multiply(person, clipped)
    return mask


def run_masks(server, output, work, cache, source, slug, mirrored, force=False):
    person_path = output / "layers" / f"{slug}-person.png"
    top_path = output / "layers" / f"{slug}-top.png"
    bottom_path = output / "layers" / f"{slug}-bottom.png"
    if not force and person_path.is_file() and top_path.is_file() and bottom_path.is_file():
        return
    remote = upload_cached(server, source, cache)
    result_dir = Path(tempfile.mkdtemp(prefix=f"mask-{slug}-", dir=work))
    result = run(server, mask_graph(remote, f"cover-story/wardrobe-layer-poc-v1/{slug}"), result_dir, 1800)
    found = {}
    for item in result["images"]:
        name = Path(item["path"]).name
        for kind in ("person", "top", "bottom"):
            if f"{kind}-mask" in name:
                found[kind] = Path(item["path"])
    if set(found) != {"person", "top", "bottom"}:
        raise ValueError(f"missing SAM3 masks for {slug}: {sorted(found)}")
    with Image.open(found["person"]) as person_image, Image.open(found["top"]) as top_image, Image.open(found["bottom"]) as bottom_image, Image.open(source) as source_image:
        person = person_image.convert("L").point(lambda value: 255 if value > 16 else 0)
        top = cleanup_mask(top_image, person, "top")
        bottom = cleanup_mask(bottom_image, person, "bottom")
        if mirrored:
            person = person.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            top = top.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            bottom = bottom.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        source_rgb = flip_if_needed(source_image.convert("RGB"), mirrored)
        top_layer = source_rgb.convert("RGBA")
        bottom_layer = source_rgb.convert("RGBA")
        save_png(person, person_path)
        top_layer.putalpha(top)
        bottom_layer.putalpha(bottom)
        save_png(top_layer, top_path)
        save_png(bottom_layer, bottom_path)


def bbox(path):
    with Image.open(path) as image:
        return image.getchannel("A").getbbox() if "A" in image.getbands() else None


def mask_bbox(path):
    with Image.open(path) as image:
        return image.convert("L").getbbox()


def aligned_layer(layer_path, source_person_bbox, target_person_bbox):
    with Image.open(layer_path) as opened:
        layer = opened.convert("RGBA")
    if not source_person_bbox or not target_person_bbox:
        return layer
    source = layer.crop(source_person_bbox)
    target_width = max(1, target_person_bbox[2] - target_person_bbox[0])
    target_height = max(1, target_person_bbox[3] - target_person_bbox[1])
    source = source.resize((target_width, target_height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", layer.size)
    canvas.alpha_composite(source, target_person_bbox[:2])
    return canvas


def composite_assets(output, base_paths, layer_paths):
    composite_paths = []
    for actor, base in base_paths.items():
        pose = "front" if "-front" in actor else "right"
        target_bbox = bbox(base)
        for outfit in OUTFITS:
            slug = f"{actor}-{outfit}"
            destination = output / "composites" / f"{slug}.png"
            with Image.open(base) as opened:
                result = opened.convert("RGBA")
            for kind in ("bottom", "top"):
                layer_key = f"{pose}-{outfit}-{kind}"
                layer, source_bbox = layer_paths[layer_key]
                result.alpha_composite(aligned_layer(layer, source_bbox, target_bbox))
            save_png(result, destination)
            composite_paths.append(destination)
    return composite_paths


def checkerboard(size, cell=24):
    image = Image.new("RGB", size, (235, 235, 235))
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(198, 198, 198))
    return image


def contact_sheet(paths, destination, columns=4, alpha=False, labels=None):
    tile = (256, 384)
    label_height = 26
    rows = (len(paths) + columns - 1) // columns
    canvas = Image.new("RGB", (columns * tile[0], rows * (tile[1] + label_height)), (28, 30, 34))
    draw = ImageDraw.Draw(canvas)
    for index, path in enumerate(paths):
        x = (index % columns) * tile[0]
        y = (index // columns) * (tile[1] + label_height)
        background = checkerboard(tile) if alpha else Image.new("RGB", tile, (225, 225, 225))
        with Image.open(path) as opened:
            image = opened.convert("RGBA") if alpha else opened.convert("RGB")
            image.thumbnail(tile, Image.Resampling.LANCZOS)
            px = x + (tile[0] - image.width) // 2
            py = y + (tile[1] - image.height) // 2
            if alpha:
                background.paste(image, (px - x, py - y), image)
            else:
                background.paste(image, (px - x, py - y))
        canvas.paste(background, (x, y))
        draw.rectangle((x, y + tile[1], x + tile[0], y + tile[1] + label_height), fill=(18, 19, 22))
        draw.text((x + 5, y + tile[1] + 5), path.stem[:34], fill=(235, 235, 235))
    save_png(canvas, destination)


def viking_preview(output, composite):
    background_path = Path("plugins/cover-story/assets/themes/viking/backgrounds/warm-right-01.webp")
    with Image.open(background_path) as opened:
        background = ImageOps.fit(opened.convert("RGB"), (900, 600), Image.Resampling.LANCZOS)
    with Image.open(composite) as opened:
        actor = opened.convert("RGBA")
    actor.thumbnail((330, 540), Image.Resampling.LANCZOS)
    x = (background.width - actor.width) // 2
    y = background.height - actor.height - 10
    shadow = Image.new("RGBA", background.size)
    draw = ImageDraw.Draw(shadow)
    draw.ellipse((x + 25, background.height - 30, x + actor.width - 25, background.height - 4), fill=(0, 0, 0, 95))
    background = Image.alpha_composite(background.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(12)))
    background.alpha_composite(actor, (x, y))
    save_png(background.convert("RGB"), output / "qa" / "viking-background-preview.png")


def asset_record(root, path, **extra):
    with Image.open(path) as image:
        record = {
            "path": str(path.relative_to(root)),
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
            "dimensions": list(image.size),
            "mode": image.mode,
        }
    record.update(extra)
    return record


def self_test():
    assert len(OUTFITS) == 3
    assert WIDTH == 1024 and HEIGHT == 1536
    mask = Image.new("L", (100, 100), 255)
    person = Image.new("L", (100, 100), 255)
    assert cleanup_mask(mask, person, "top").getbbox() == (0, 20, 100, 71)
    assert cleanup_mask(mask, person, "bottom").getbbox() == (0, 42, 100, 89)
    print("wardrobe-layer PoC self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=os.environ.get("COMFY_SERVER"))
    parser.add_argument("--output-dir", type=Path, default=Path("/mnt/Misc/sd/cover-story/wardrobe-layer-poc-v1"))
    parser.add_argument("--actor-001", type=Path, default=Path("plugins/cover-story/assets/performers/actor-001.avif"))
    parser.add_argument("--actor-095", type=Path, default=Path("plugins/cover-story/assets/performers/actor-095.avif"))
    parser.add_argument("--right-pose-source", type=Path, default=Path("/mnt/Misc/sd/cover-story/scenes/viking/actor-004-right-source.png"))
    parser.add_argument("--force", action="store_true", help="regenerate existing generated assets")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.server:
        parser.error("--server is required")
    root = args.output_dir
    root.mkdir(parents=True, exist_ok=True)
    work = root / "_work"
    work.mkdir(parents=True, exist_ok=True)
    cache = {}

    references = root / "references"
    actor_sources = {}
    for actor, source in (("actor-001", args.actor_001), ("actor-095", args.actor_095)):
        destination = references / f"{actor}.png"
        ensure_png(source, destination)
        actor_sources[actor] = destination
    pose_sources = {"front": actor_sources["actor-001"], "right": args.right_pose_source}
    pose_refs = run_pose_references(args.server, root, pose_sources, cache, work, args.force)

    base_prompts = {
        "front": "Keep the same fictional person, face, hair, skin tone, facial features, and identity. Create a full-body relaxed front-facing standing studio portrait: square shoulders, both arms resting naturally, both legs and shoes visible, direct calm gaze. Follow the full-body front pose guide when provided. Use a plain neutral charcoal-gray fitted long-sleeve tunic, fitted dark trousers, and simple brown ankle boots as a neutral base wardrobe. Clean light-gray studio background, realistic adult proportions, workplace-safe, no armor, no cape, no props, preserve natural hands and skin texture.",
        "right": "Image 1 is the identity reference. Image 2 is the OpenPose guide. Keep the same fictional person, face, hair, skin tone, facial features, and identity from image 1. Follow the right-facing three-quarter relaxed standing pose from image 2, with full body and both shoes visible. Use a plain neutral charcoal-gray fitted long-sleeve tunic, fitted dark trousers, and simple brown ankle boots as a neutral base wardrobe. Clean light-gray studio background, realistic adult proportions, workplace-safe, no armor, no cape, no props, preserve natural hands and skin texture.",
    }
    base_paths = {}
    generation = []
    full_front_refs = None
    for actor_index, actor in enumerate(("actor-001", "actor-095")):
        for pose_index, pose in enumerate(("front", "right")):
            mirrored = pose == "right"
            output = root / "bases" / f"{actor}-{pose}{'-mirrored' if mirrored else ''}.png"
            if actor == "actor-095" and pose == "front":
                full_front_refs = run_pose_references(
                    args.server,
                    root,
                    {"front-full": base_paths["actor-001-front"]},
                    cache,
                    work,
                    args.force,
                )["front-full"]
            pose_image = (
                full_front_refs["openpose"]
                if actor == "actor-095" and pose == "front"
                else pose_refs[pose]["openpose"] if pose == "right" else None
            )
            prompt = base_prompts[pose]
            generate_qwen_asset(
                args.server,
                output,
                work,
                cache,
                actor_sources[actor],
                pose_image,
                prompt,
                2026073100 + actor_index * 10 + pose_index,
                f"base-{actor}-{pose}",
                mirrored,
                args.force,
            )
            base_paths[f"{actor}-{pose}"] = output
            generation.append({"kind": "base", "actor": actor, "pose": pose, "mirrored": mirrored, "path": output, "prompt": prompt, "seed": 2026073100 + actor_index * 10 + pose_index})

    wardrobe_paths = {}
    wardrobe_generation = []
    canonical_source = base_paths["actor-001-front"]
    for outfit_index, (outfit, details) in enumerate(OUTFITS.items(), 1):
        for pose_index, pose in enumerate(("front", "right")):
            mirrored = pose == "right"
            prompt = (
                f"Keep the full-body relaxed {('front-facing' if pose == 'front' else 'right-facing three-quarter')} pose and a generic neutral mannequin-like person. "
                f"Change only the clothing to this exact workplace-safe Viking-inspired outfit: {details['description']}. "
                "The garment silhouette must be fitted and tailored, with a clear separate top and separate trousers, no dress, no skirt, no cape, no armor, no weapons, no modern logos. Keep the head, hair, face, hands, skin, shoes, lighting, framing, and clean light-gray studio background stable. Show realistic wool, linen, leather, and restrained trim textures."
            )
            output = root / "wardrobes" / f"{outfit}-{pose}{'-mirrored' if mirrored else ''}.png"
            pose_image = full_front_refs["openpose"] if pose == "front" else pose_refs[pose]["openpose"]
            generate_qwen_asset(
                args.server,
                output,
                work,
                cache,
                canonical_source,
                pose_image,
                prompt,
                2026073200 + outfit_index * 10 + pose_index,
                f"wardrobe-{outfit}-{pose}",
                mirrored,
                args.force,
            )
            wardrobe_paths[f"{pose}-{outfit}"] = output
            wardrobe_generation.append({"kind": "wardrobe", "outfit": outfit, "outfit_name": details["name"], "pose": pose, "mirrored": mirrored, "path": output, "prompt": prompt, "seed": 2026073200 + outfit_index * 10 + pose_index})

    for actor_pose, base in base_paths.items():
        run_masks(args.server, root, work, cache, base, f"base-{actor_pose}", False, args.force)
        person_mask = root / "layers" / f"base-{actor_pose}-person.png"
        with Image.open(base) as opened, Image.open(person_mask) as mask:
            base_rgba = opened.convert("RGBA")
            base_rgba.putalpha(mask.convert("L"))
            save_png(base_rgba, base)

    layer_paths = {}
    for key, source in wardrobe_paths.items():
        pose, outfit = key.split("-", 1)
        slug = f"{outfit}-{pose}{'-mirrored' if pose == 'right' else ''}"
        run_masks(args.server, root, work, cache, source, slug, False, args.force)
        layer_paths[f"{pose}-{outfit}-top"] = (root / "layers" / f"{slug}-top.png", None)
        layer_paths[f"{pose}-{outfit}-bottom"] = (root / "layers" / f"{slug}-bottom.png", None)

    # Align each layer using a SAM3 person bbox from its canonical render and its target base.
    for actor_pose, base in base_paths.items():
        pose = actor_pose.rsplit("-", 1)[-1]
        target_bbox = bbox(base)
        if not target_bbox:
            raise ValueError(f"base has no alpha subject after segmentation: {base}")
        for outfit in OUTFITS:
            person_mask = root / "layers" / f"{outfit}-{pose}{'-mirrored' if pose == 'right' else ''}-person.png"
            source_bbox = mask_bbox(person_mask)
            if not source_bbox:
                raise ValueError(f"canonical render has no person mask: {person_mask}")
            layer_slug = f"{outfit}-{pose}{'-mirrored' if pose == 'right' else ''}"
            layer_paths[f"{pose}-{outfit}-top"] = (root / "layers" / f"{layer_slug}-top.png", source_bbox)
            layer_paths[f"{pose}-{outfit}-bottom"] = (root / "layers" / f"{layer_slug}-bottom.png", source_bbox)

    composite_paths = composite_assets(root, base_paths, layer_paths)
    qa_dir = root / "qa"
    contact_sheet(list(base_paths.values()), qa_dir / "bases.png", columns=4, alpha=True)
    contact_sheet(list(wardrobe_paths.values()), qa_dir / "wardrobes.png", columns=3)
    contact_sheet([root / "layers" / f"{outfit}-{pose}{'-mirrored' if pose == 'right' else ''}-{kind}.png" for outfit in OUTFITS for pose in ("front", "right") for kind in ("top", "bottom")], qa_dir / "transparent-layers.png", columns=4, alpha=True)
    contact_sheet(composite_paths, qa_dir / "transparent-composites.png", columns=4, alpha=True)
    viking_preview(root, root / "composites" / "actor-001-front-outfit-01.png")

    assets = {
        "references": [asset_record(root, p) for p in sorted(references.glob("*.png"))],
        "bases": [asset_record(root, p, actor=entry["actor"], pose=entry["pose"], mirrored=entry["mirrored"], prompt=entry["prompt"], seed=entry["seed"], model_settings=MODEL_SETTINGS) for entry in generation for p in [entry["path"]]],
        "wardrobes": [asset_record(root, p, outfit=entry["outfit"], outfit_name=entry["outfit_name"], pose=entry["pose"], mirrored=entry["mirrored"], prompt=entry["prompt"], seed=entry["seed"], model_settings=MODEL_SETTINGS) for entry in wardrobe_generation for p in [entry["path"]]],
        "layers": [asset_record(root, p, kind=p.stem.rsplit("-", 1)[-1]) for p in sorted((root / "layers").glob("outfit-*.png")) if p.stem.endswith(("-top", "-bottom"))],
        "composites": [asset_record(root, p) for p in sorted((root / "composites").glob("*.png"))],
        "qa": [asset_record(root, p) for p in sorted(qa_dir.glob("*.png"))],
    }
    manifest = {
        "version": VERSION,
        "status": "generated_pending_human_visual_review",
        "server": "COMFY_SERVER",
        "model_settings": MODEL_SETTINGS,
        "pose_control": {
            "front": {"openpose": str(pose_refs["front"]["openpose"].relative_to(root)), "densepose": str(pose_refs["front"]["densepose"].relative_to(root)), "source": str(pose_sources["front"]), "full_body_guide": {"openpose": str(full_front_refs["openpose"].relative_to(root)), "densepose": str(full_front_refs["densepose"].relative_to(root)), "source": str(full_front_refs["source"])}},
            "right_three_quarter": {"openpose": str(pose_refs["right"]["openpose"].relative_to(root)), "densepose": str(pose_refs["right"]["densepose"].relative_to(root)), "mirrored_openpose": str(pose_refs["right"]["openpose_mirrored"].relative_to(root)), "mirrored_densepose": str(pose_refs["right"]["densepose_mirrored"].relative_to(root)), "source": str(pose_sources["right"])},
        },
        "outfits": OUTFITS,
        "masking": {"detector": "SAM3_Detect", "checkpoint": "sam3.1_multiplex_fp16.safetensors", "threshold": 0.5, "refine_iterations": 2, "manual_cleanup": "SAM3 mask intersected with person mask, soft edge, top/bottom y-band cleanup; shoes excluded from bottom layer"},
        "assets": assets,
        "acceptance": {"human_review_required": True, "checks": ["no obvious mask seams", "faces, skin, and shoes stable", "only intentional small underlayer visible", "no directional mirroring artifacts"]},
    }
    atomic_json(root / "manifest.json", manifest)
    (root / "README.md").write_text(
        "# Wardrobe-layer PoC v1\n\n"
        "Standalone asset bundle generated from catalog actors `actor-001` and `actor-095`.\n\n"
        "The bundle is pending human visual review at Stash card size. It contains Qwen Image Edit actor bases, canonical outfit renders, SAM3-cleaned top/bottom alpha layers, 12 layer composites, pose maps, QA contact sheets, and one existing Viking-background preview.\n\n"
        "The repository plugin and shipped assets were not modified. The Comfy token is intentionally absent; rerun with `COMFY_SERVER` supplied to the runner.\n",
        encoding="utf-8",
    )
    print(f"generated wardrobe-layer PoC at {root}")


if __name__ == "__main__":
    main()
