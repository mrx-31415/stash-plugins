#!/usr/bin/env python3
"""Resumable Cover Story layered-costume production pilot."""

import argparse
import html
import json
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat

from comfy import api, run, upload_image
from export_personas import atomic_text, sha256
from export_scene_assets import composite as scene_composite
from export_scene_assets import save as save_webp
from export_scene_assets import save_avif


TOOL_ROOT = Path(__file__).resolve().parent
CATALOG_PATH = TOOL_ROOT / "layered-costume-catalog.json"
DEFAULT_ROOT = Path("/mnt/Misc/sd/cover-story/layered-costume-production-v20d")
RUN_ID = "layered-costume-production-v20d"
VERSION = 3
CANVAS = (1024, 1024)
CARD = (600, 900)
MAX_AUTOMATIC_RETRIES = 2
EDGE_CLEANUP = "corridorkey-alpha-only-v20"
BASE_SEED = 2026080100
EDIT_MODEL = "qwen_image_edit_2511_int8_convrot.safetensors"
CARRIER_MODEL = "qwen_image_2512_fp8_e4m3fn.safetensors"
TEXT_ENCODER = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE = "qwen_image_vae.safetensors"
SAM_MODEL = "sam3.1_multiplex_fp16.safetensors"
# InstantX Qwen-Image ControlNet Union. Only used when edit_graph() is given a control image.
CONTROL_NET = "qwen_image_controlnet_union_instantx.safetensors"
# SDPose, a diffusion-based whole-body keypoint estimator shipped in ComfyUI core
# (comfy_extras/nodes_sdpose.py). Loaded as a checkpoint because SDPoseKeypointExtractor wants both
# a MODEL and a VAE, and reads a heatmap_head off the diffusion model that only this file carries.
POSE_MODEL = "sdpose_wholebody_fp16.safetensors"
# SetUnionControlNetType's own option strings, not friendly names: the node validates against this
# exact list and rejects "canny". Keyed by the short name callers actually want to say.
CONTROL_TYPES = {
    "auto": "auto",
    "openpose": "openpose",
    "depth": "depth",
    "canny": "canny/lineart/anime_lineart/mlsd",
    "scribble": "hed/pidi/scribble/ted",
    "normal": "normal",
    "segment": "segment",
    "tile": "tile",
    "repaint": "repaint",
}
NEGATIVE = "低分辨率，低画质，肢体畸形，手指畸形，画面过饱和，蜡像感，人脸无细节，过度光滑，画面具有AI感。构图混乱。文字模糊，扭曲"
SETTINGS = {
    "edit": {
        "model": EDIT_MODEL, "text_encoder": TEXT_ENCODER, "vae": VAE,
        "shift": 3.1, "cfg_norm": 1.0, "steps": 40, "cfg": 4.0,
        "sampler": "euler", "scheduler": "simple", "denoise": 1.0,
        "reference_latents_method": "index_timestep_zero", "lightning": False,
        "canvas": list(CANVAS),
    },
    "carrier": {
        "model": CARRIER_MODEL, "text_encoder": TEXT_ENCODER, "vae": VAE,
        "shift": 3.1, "steps": 50, "cfg": 4.0, "sampler": "euler",
        "scheduler": "simple", "denoise": 1.0, "canvas": [1328, 1328],
        "canonical_canvas": list(CANVAS), "lightning": False,
    },
    "sam3": {"model": SAM_MODEL, "threshold": 0.5, "refine_iterations": 2, "dilation_px": 12},
    "corridorkey": {
        "gamma_space": "sRGB", "despill_strength": 1.0,
        "refiner_strength": 1.0, "auto_despeckle": "On", "despeckle_size": 400,
        "inference_size": 2048,
    },
}
MASKED_EDIT_SETTINGS = {**SETTINGS["edit"], "latent_noise_mask": True, "final_image_composite_masked": True}
PROMPTS = {
    "identity": "Keep the green woman's body, clothing, pose and background. Replace her green head, hair, neck and visible upper chest with those of the woman in image 2.",
}
GREEN_PREPROCESS = "Put the woman from image 1 in the green suit and full-body pose from image 2. Keep her face, hair, identity and skin tone. Keep the suit, gloves, shoes and background matte chroma key green."
PLATE_PROMPTS = {
    "head": ["head, hair, face, ears, neck, clavicles, shoulders and wide upper chest", "hair"],
    "body": ["person", "clothing", "upper-body clothing, shirt, jacket, tunic, dress or bodice",
             "pants", "left hand", "right hand", "left shoe", "right shoe",
             "entire head, face, ears, scalp, neck, clavicles and upper chest"],
}


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def save_png(image, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".png", delete=False) as output:
        temporary = Path(output.name)
    try:
        image.save(temporary, "PNG", compress_level=1)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def relative(path, root):
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def image_record(path, root):
    path = Path(path)
    with Image.open(path) as image:
        image.load()
        return {
            "path": relative(path, root), "sha256": sha256(path),
            "bytes": path.stat().st_size, "dimensions": list(image.size),
            "mode": image.mode,
        }


def load_catalog(path=CATALOG_PATH):
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if catalog.get("version") != VERSION:
        raise ValueError(f"unsupported layered catalog: {path}")
    required = {"performers", "poses", "themes", "settings"}
    if not required <= catalog.keys():
        raise ValueError(f"catalog missing {sorted(required - catalog.keys())}")
    if len(catalog["performers"]) != 20:
        raise ValueError("layered catalog must contain 20 performers")
    if len(catalog["poses"]) != 4 or len(catalog["themes"]) != 4:
        raise ValueError("layered catalog must contain four poses and themes")
    if len({item["id"] for item in catalog["performers"]}) != 20:
        raise ValueError("performer IDs are not unique")
    pilots = [item for item in catalog["performers"] if item.get("pilot")]
    if len(pilots) != 4 or {item["skin_tone_group"] for item in pilots} != {"light", "medium", "dark"}:
        raise ValueError("pilot must contain four performers spanning light, medium and dark tones")
    for performer in catalog["performers"]:
        source = source_path(catalog, performer["source"])
        if not source.is_file() or sha256(source) != performer["source_sha256"]:
            raise ValueError(f"missing or changed approved performer source: {source}")
    for theme in catalog["themes"]:
        for outfit in theme["outfits"]:
            if outfit["key_color"] not in rejected_key_colors(outfit["description"]):
                raise ValueError(f"{theme['id']}/{outfit['id']} key conflicts with its foreground color")
    return catalog


def source_root(catalog):
    return Path(catalog["source_root"])


def source_path(catalog, value):
    path = Path(value)
    return path if path.is_absolute() else source_root(catalog) / path


def ensure_manifest(root, catalog, scope):
    root.mkdir(parents=True, exist_ok=True)
    path = root / "manifest.json"
    digest = sha256(CATALOG_PATH)
    recipe = {"settings": SETTINGS, "negative_prompt": NEGATIVE, "scope": scope}
    if root.exists() and any(root.iterdir()) and not path.exists():
        raise ValueError(f"refusing nonempty output directory without manifest: {root}")
    if path.exists():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (
            manifest.get("version") != VERSION
            or manifest.get("catalog_sha256") != digest
            or manifest.get("recipe") != recipe
        ):
            raise ValueError(f"incompatible layered production manifest: {path}")
        return manifest
    manifest = {
        "version": VERSION, "run_id": RUN_ID,
        "catalog": CATALOG_PATH.name, "catalog_sha256": digest,
        "recipe": recipe, "status": "initialized", "attempts": {},
        "layers": {}, "composites": {}, "reviews": {}, "accepted": {}, "carrier_variants": {},
    }
    atomic_json(path, manifest)
    return manifest


def persist(root, manifest):
    atomic_json(root / "manifest.json", manifest)


def verify_nodes(server):
    for name in ("CorridorKey", "SAM3_Detect"):
        data = api(server, f"/object_info/{name}")
        if name not in data:
            raise RuntimeError(f"ComfyUI does not expose /object_info/{name}")


def seed_for(job_id, retry=0):
    digest = int(sha256_bytes(job_id.encode())[:10], 16)
    return BASE_SEED + digest % 900000 + retry


def sha256_bytes(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def key_rgb(color):
    return (0, 255, 0) if color == "green" else (0, 0, 255)


def key_color(image, description=""):
    image = image.convert("RGB")
    pixels = list(image.get_flattened_data())
    counts = {
        name: sum(sum((a - b) ** 2 for a, b in zip(pixel, rgb)) < 70 ** 2 for pixel in pixels)
        for name, rgb in (("green", (0, 255, 0)), ("blue", (0, 0, 255)))
    }
    if counts["green"] == counts["blue"]:
        words = description.lower()
        return "green" if "blue" in words else "blue"
    return min(counts, key=counts.get)


def key_residue(image, color, alpha_threshold=16):
    rgb = image.convert("RGBA")
    values = [pixel for pixel in rgb.get_flattened_data() if pixel[3] > alpha_threshold]
    if not values:
        return 1.0
    channel = 1 if color == "green" else 2
    near = sum(pixel[channel] > 40 and
               pixel[channel] > max(pixel[index] for index in range(3) if index != channel) * 1.10 and
               pixel[channel] - max(pixel[index] for index in range(3) if index != channel) > 16
               for pixel in values)
    return near / len(values)


def screen_color(path):
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    size = 96
    pixels = []
    for box in ((0, 0, size, size), (image.width - size, 0, image.width, size),
                (0, image.height - size, size, image.height),
                (image.width - size, image.height - size, image.width, image.height)):
        pixels.extend(image.crop(box).get_flattened_data())
    scores = {
        "green": sum(g > r * 1.15 and g > b * 1.15 for r, g, b in pixels),
        "blue": sum(b > r * 1.15 and b > g * 1.15 for r, g, b in pixels),
    }
    return max(scores, key=scores.get)


def rejected_key_colors(description=""):
    words = description.lower()
    preserve_green = any(word in words for word in ("green", "olive", "teal", "emerald", "jade", "mint"))
    preserve_blue = any(word in words for word in ("blue", "navy", "indigo", "cobalt", "sapphire"))
    return tuple(color for color, preserve in (("green", preserve_green), ("blue", preserve_blue)) if not preserve)


def key_region_check(path, color, box, name, minimum=0.15):
    with Image.open(path) as opened:
        pixels = opened.convert("RGB").crop(box).get_flattened_data()
    channel = 1 if color == "green" else 2
    fraction = sum(pixel[channel] > max(pixel[index] for index in range(3) if index != channel) * 1.25
                   and pixel[channel] > 70 for pixel in pixels) / len(pixels)
    return {"name": name, "passed": fraction >= minimum,
            "detail": {"key": color, "fraction": round(fraction, 5), "minimum": minimum,
                       "method": "dominant key channel"}}


def key_mask_check(path, mask, color, name="region_is_key_color", minimum=0.98):
    with Image.open(path) as opened:
        pixels = opened.convert("RGB").get_flattened_data()
    with Image.open(mask) as opened:
        matte = opened.convert("L").get_flattened_data()
    selected = [pixel for pixel, value in zip(pixels, matte) if value > 127]
    channel = 1 if color == "green" else 2
    fraction = sum(pixel[channel] > max(pixel[index] for index in range(3) if index != channel) * 1.25
                   and pixel[channel] > 40 for pixel in selected) / len(selected) if selected else 0
    return {"name": name, "passed": fraction >= minimum,
            "detail": {"key": color, "fraction": round(fraction, 5), "minimum": minimum,
                       "method": "dominant key channel inside SAM3 region mask"}}


def semantic_checks(job, path):
    if job["kind"] == "body":
        return [
            key_region_check(path, job["key_color"], (465, 70, 560, 210), "head_aperture_is_key_color", 0.40),
            key_region_check(path, job["key_color"], (0, 0, 160, 160), "background_is_key_color", 0.60),
        ]
    return []


def check_image(path, size, mode):
    try:
        with Image.open(path) as image:
            image.load()
            return {
                "name": "image", "passed": (size is None or image.size == tuple(size)) and image.mode == mode,
                "detail": {"actual_size": list(image.size), "actual_mode": image.mode,
                            "expected_size": list(size) if size else None, "expected_mode": mode},
            }
    except (OSError, ValueError) as exc:
        return {"name": "image", "passed": False, "detail": str(exc)}


def aspect_check(path, reference):
    try:
        with Image.open(path) as candidate, Image.open(reference) as source:
            ratio = candidate.width / candidate.height
            source_ratio = source.width / source.height
            delta = abs(ratio - source_ratio)
            return {"name": "reference_aspect", "passed": delta <= 0.01,
                    "detail": {"actual": round(ratio, 5), "source": round(source_ratio, 5), "delta": round(delta, 5)}}
    except (OSError, ValueError) as exc:
        return {"name": "reference_aspect", "passed": False, "detail": str(exc)}


def carrier_checks(path):
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    def stats(box):
        values = list(image.crop(box).get_flattened_data())
        green = sum(g > r * 1.15 and g > b * 1.15 and g > 40 for r, g, b in values) / len(values)
        dark = sum(max(r, g, b) < 45 for r, g, b in values) / len(values)
        return green, dark
    head_green, _ = stats((350, 20, 674, 300))
    _, crown_dark = stats((420, 20, 604, 210))
    return [
        {"name": "carrier_head_is_green", "passed": head_green > 0.90, "detail": round(head_green, 5)},
        {"name": "carrier_no_dark_hair", "passed": crown_dark < 0.03, "detail": round(crown_dark, 5)},
    ]


def registration(reference, candidate):
    boxes = {"torso": (300, 420, 724, 690), "hands": (90, 430, 934, 770),
             "crotch": (350, 600, 674, 790), "feet": (250, 820, 774, 1020)}
    try:
        with Image.open(reference) as opened:
            ref = opened.convert("RGB")
        with Image.open(candidate) as opened:
            current = opened.convert("RGB")
    except (OSError, ValueError) as exc:
        return {"accepted": False, "reason": str(exc)}
    if ref.size != CANVAS or current.size != CANVAS:
        return {"accepted": False, "reason": "canvas mismatch"}
    best = None
    for scale in (0.995, 0.9975, 1.0, 1.0025, 1.005):
        side = round(1024 * scale)
        scaled = current.resize((side, side), Image.Resampling.BICUBIC)
        canvas = Image.new("RGB", CANVAS)
        origin = ((1024 - side) // 2, (1024 - side) // 2)
        canvas.paste(scaled, origin)
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                scores = []
                for box in boxes.values():
                    shifted = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
                    if min(shifted) < 0 or shifted[2] > 1024 or shifted[3] > 1024:
                        continue
                    scores.append(sum(ImageStat.Stat(ImageChops.difference(ref.crop(box), canvas.crop(shifted))).mean))
                score = sum(scores) if scores else 10 ** 9
                if best is None or score < best[0]:
                    best = (score, scale, dx, dy)
    _, scale, dx, dy = best
    return {
        "accepted": abs(dx) <= 2 and abs(dy) <= 2 and round(abs(scale - 1), 5) <= 0.005,
        "translation_px": [dx, dy], "scale_change": round(scale - 1, 5),
        "limits": {"translation_px": 2, "scale_change": 0.005},
        "method": "fixed-canvas brute-force comparison; no correction applied",
    }


def alpha_checks(path, kind, key=None, alpha_only=False):
    with Image.open(path) as opened:
        image = opened.convert("RGBA")
    alpha = image.getchannel("A")
    pixels = list(alpha.get_flattened_data())
    occupied = sum(value > 8 for value in pixels) / len(pixels)
    checks = [{"name": "mask_nonempty", "passed": occupied > 0.001,
               "detail": round(occupied, 5)},
              {"name": "mask_plausible", "passed": occupied < 0.98,
               "detail": round(occupied, 5)}]
    if key:
        for color in ((key,) if isinstance(key, str) else key):
            threshold = 192 if alpha_only else 16
            maximum = 0.005 if alpha_only else 0.002
            residue = key_residue(image, color, threshold)
            checks.append({"name": f"{color}_key_residue", "passed": residue < maximum,
                           "detail": {"key": color, "fraction": round(residue, 5),
                                      "alpha_threshold": threshold, "maximum": maximum}})
    if kind == "body":
        aperture = (410, 50, 614, 205)
        body = alpha.crop((180, 230, 844, 980))
        legs = alpha.crop((370, 680, 654, 830))
        aperture_fraction = sum(v > 8 for v in alpha.crop(aperture).get_flattened_data()) / (204 * 155)
        body_fraction = sum(v > 8 for v in body.get_flattened_data()) / (664 * 750)
        leg_fraction = sum(v > 8 for v in legs.get_flattened_data()) / (284 * 150)
        checks.extend([
            {"name": "head_aperture_transparent", "passed": aperture_fraction < 0.08,
             "detail": round(aperture_fraction, 5)},
            {"name": "clothed_body_complete", "passed": body_fraction > 0.25,
             "detail": round(body_fraction, 5)},
            {"name": "lower_garment_present", "passed": leg_fraction > 0.10,
             "detail": round(leg_fraction, 5)},
        ])
    if kind == "skin":
        overlap = alpha.crop((400, 220, 624, 360))
        checks.append({"name": "skin_backs_neckline", "passed": sum(v > 8 for v in overlap.get_flattened_data()) > 100,
                       "detail": "upper-chest overlap sample"})
    return checks


def generation_graph(prompt, seed, prefix, size=(1328, 1328), canonical=True, negative_prompt=NEGATIVE):
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": CARRIER_MODEL, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT_ENCODER, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.1}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt, "clip": ["2", 0]}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": size[0], "height": size[1], "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "seed": seed, "steps": 50, "cfg": 4.0, "sampler_name": "euler", "scheduler": "simple", "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0], "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": f"{prefix}-raw"}},
    }
    if canonical:
        graph.update({
            "11": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["9", 0]}},
            "12": {"class_type": "SaveImage", "inputs": {"images": ["11", 0], "filename_prefix": f"{prefix}-canonical"}},
        })
    return graph


def edit_graph(image1, prompt, seed, prefix, image2=None, mask=None,
               control=None, control_type="canny", control_strength=1.0):
    """control= a control image applied through the Qwen Union ControlNet.

    Additive: every existing caller keeps today's graph exactly. The point of the control path is
    that it states target geometry directly, instead of the caller pre-registering two images by
    matching bounding boxes — which failed twice, once by comparing a head box against a dilated
    envelope and once by comparing a bald skull against a head with hair.

    `control_type` is a short name from CONTROL_TYPES, not the node's literal option string —
    SetUnionControlNetType validates against its own list and rejects a bare "canny".

    `canny` is the default because it needs nothing installed and states the silhouette directly:
    an outline says where the edge of the body is, where a skeleton only says where the joints are.
    `openpose` needs POSE_MODEL on the pod and comes from pose_graph().

    Strength 1.0-1.5 is the range documented for this ControlNet against Qwen Image Edit
    (civitai 1966651), whose author also pairs it with CFG 2.5 / 20 steps; this graph keeps the
    handover's CFG 4 / 40 steps so that the control image is the only variable being changed.
    """
    nodes = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": EDIT_MODEL, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT_ENCODER, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.1}},
        "5": {"class_type": "LoadImage", "inputs": {"image": image1}},
        "6": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["5", 0]}},
        "7": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "prompt": "", "vae": ["3", 0], "image1": ["6", 0]}},
        "8": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "prompt": prompt, "vae": ["3", 0], "image1": ["6", 0]}},
        "9": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"reference_latents_method": "index_timestep_zero", "conditioning": ["7", 0]}},
        "10": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"reference_latents_method": "index_timestep_zero", "conditioning": ["8", 0]}},
        "11": {"class_type": "CFGNorm", "inputs": {"strength": 1.0, "pre_cfg": False, "model": ["4", 0]}},
        "12": {"class_type": "VAEEncode", "inputs": {"pixels": ["6", 0], "vae": ["3", 0]}},
        "13": {"class_type": "KSampler", "inputs": {"model": ["11", 0], "seed": seed, "steps": 40, "cfg": 4.0, "sampler_name": "euler", "scheduler": "simple", "positive": ["10", 0], "negative": ["9", 0], "latent_image": ["12", 0], "denoise": 1.0}},
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["3", 0]}},
        "15": {"class_type": "SaveImage", "inputs": {"images": ["14", 0], "filename_prefix": f"{prefix}-raw"}},
    }
    if image2:
        nodes["16"] = {"class_type": "LoadImage", "inputs": {"image": image2}}
        nodes["7"]["inputs"]["image2"] = ["16", 0]
        nodes["8"]["inputs"]["image2"] = ["16", 0]
    if mask:
        nodes.update({
            "17": {"class_type": "LoadImage", "inputs": {"image": mask}},
            "18": {"class_type": "ImageToMask", "inputs": {"image": ["17", 0], "channel": "red"}},
            "19": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["12", 0], "mask": ["18", 0]}},
            "20": {"class_type": "ImageCompositeMasked", "inputs": {"destination": ["6", 0], "source": ["14", 0], "x": 0, "y": 0, "resize_source": False, "mask": ["18", 0]}},
            "21": {"class_type": "SaveImage", "inputs": {"images": ["20", 0], "filename_prefix": f"{prefix}-masked"}},
        })
        nodes["13"]["inputs"]["latent_image"] = ["19", 0]
    if control:
        nodes.update({
            "22": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": CONTROL_NET}},
            "23": {"class_type": "SetUnionControlNetType",
                   "inputs": {"control_net": ["22", 0], "type": CONTROL_TYPES[control_type]}},
            "24": {"class_type": "LoadImage", "inputs": {"image": control}},
            # Both conditionings go through together: ControlNetApplyAdvanced returns the pair, and
            # applying it to the positive alone would leave the negative unguided.
            "25": {"class_type": "ControlNetApplyAdvanced",
                   "inputs": {"positive": ["10", 0], "negative": ["9", 0], "control_net": ["23", 0],
                              "image": ["24", 0], "strength": control_strength,
                              "start_percent": 0.0, "end_percent": 1.0, "vae": ["3", 0]}},
        })
        nodes["13"]["inputs"]["positive"] = ["25", 0]
        nodes["13"]["inputs"]["negative"] = ["25", 1]
    return nodes


def pose_graph(image, prefix, draw_face=False, draw_head=False, draw_feet=True):
    """An OpenPose-format skeleton drawn from SDPose keypoints, for the union ControlNet.

    Face and head default off. The control image is applied to the whole canvas, but in the inverted
    transfer the head is preserved outside the noise mask, so head keypoints could only argue with
    pixels the sampler is not allowed to change. Feet default on for the opposite reason — the
    silhouette drift being chased is at the ankles, and the node omits them by default."""
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": POSE_MODEL}},
        "2": {"class_type": "LoadImage", "inputs": {"image": image}},
        "3": {"class_type": "SDPoseKeypointExtractor",
              "inputs": {"model": ["1", 0], "vae": ["1", 2], "image": ["2", 0], "batch_size": 1}},
        "4": {"class_type": "SDPoseDrawKeypoints",
              "inputs": {"keypoints": ["3", 0], "draw_body": True, "draw_hands": True,
                         "draw_face": draw_face, "draw_feet": draw_feet, "draw_head": draw_head,
                         "stick_width": 4, "face_point_size": 3, "score_threshold": 0.3}},
        "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": f"{prefix}-pose"}},
    }


def canny_graph(image, prefix, low=0.4, high=0.8):
    """Edge map for the union ControlNet. Loads no model, so it is cheap enough to run inline."""
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image}},
        "2": {"class_type": "Canny", "inputs": {"image": ["1", 0], "low_threshold": low, "high_threshold": high}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0], "filename_prefix": f"{prefix}-canny"}},
    }


def sam_graph(image, prompts, prefix):
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": SAM_MODEL}},
        "2": {"class_type": "LoadImage", "inputs": {"image": image}},
    }
    for index, prompt in enumerate(prompts):
        text, detect, convert, save = (str(3 + index * 4 + offset) for offset in range(4))
        graph[text] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}}
        graph[detect] = {"class_type": "SAM3_Detect", "inputs": {"threshold": 0.5, "refine_iterations": 2, "individual_masks": False, "model": ["1", 0], "image": ["2", 0], "conditioning": [text, 0]}}
        graph[convert] = {"class_type": "MaskToImage", "inputs": {"mask": [detect, 0]}}
        graph[save] = {"class_type": "SaveImage", "inputs": {"images": [convert, 0], "filename_prefix": f"{prefix}-raw-hint-{index}"}}
    return graph


def prepare_edit_mask(server, job, paths, root, cache):
    if job["kind"] == "preprocess":
        return None, None
    if job["kind"] == "identity":
        source, prompts, dilations = paths[0], ["head, face, ears and neck", "face, ears, neck, clavicles and upper chest"], [60, 18]
    elif job["kind"] == "body":
        source, prompts, dilations = paths[0], ["person", "head, face, ears and neck"], [24, 8]
    else:
        return None, None
    settings = {"sam3_prompt": prompts, "dilation_px": dilations,
                "source_sha256": sha256(source)}
    if job["kind"] == "body":
        settings["operation"] = "dilated-person-minus-head-neck"
    else:
        settings["invert"] = False
    path = root / "masks" / "edits" / f"{job['id'].replace(':', '-')}.png"
    if path.is_file():
        return path, settings
    remote = cache.setdefault(str(source.resolve()), upload_image(server, source, subfolder="cover-story/layered/input"))
    work = root / "_work"
    work.mkdir(parents=True, exist_ok=True)
    result = run(server, sam_graph(remote, prompts, f"cover-story/{RUN_ID}/mask-{job['id'].replace(':', '-')}"),
                 Path(tempfile.mkdtemp(prefix="edit-mask-", dir=work)), 1800)
    hints = sorted(Path(item["path"]) for item in result["images"] if "-raw-hint-" in Path(item["path"]).stem)
    if len(hints) != len(prompts):
        raise ValueError(f"expected {len(prompts)} edit-mask hints, found {len(hints)}")
    regions = []
    for hint, dilation in zip(hints, dilations):
        with Image.open(hint) as opened:
            region = opened.convert("L")
        if dilation > 24:
            small = region.resize((CANVAS[0] // 4, CANVAS[1] // 4), Image.Resampling.BILINEAR)
            region = small.filter(ImageFilter.MaxFilter(dilation // 2 + 1)).resize(CANVAS, Image.Resampling.BILINEAR)
        else:
            region = region.filter(ImageFilter.MaxFilter(dilation * 2 + 1))
        regions.append(region)
    mask = ImageChops.subtract(regions[0], regions[1]) if job["kind"] == "body" else ImageChops.lighter(*regions)
    save_png(mask, path)
    return path, settings


def outside_mask_changed(reference, candidate, mask):
    with Image.open(reference) as opened:
        before = opened.convert("RGB")
    with Image.open(candidate) as opened:
        after = opened.convert("RGB")
    with Image.open(mask) as opened:
        edit = opened.convert("L")
    changed = ImageChops.difference(before, after).convert("L").point(lambda value: 255 if value else 0)
    outside = edit.point(lambda value: 255 if value == 0 else 0)
    count = sum(ImageChops.multiply(changed, outside).histogram()[1:])
    return {"name": "outside_mask_unchanged", "passed": count == 0, "detail": {"changed_pixels": count}}


def pick(result, marker):
    matches = [Path(item["path"]) for item in result["images"] if marker in Path(item["path"]).stem]
    if len(matches) != 1:
        raise ValueError(f"expected one {marker} output, found {len(matches)}")
    return matches[0]


CORRIDORKEY_PROCESSOR = "standalone-alpha-only-normalized-key-input-v2"


def normalized_key_input(image, screen, aperture=None):
    """Make shaded matte-screen pixels unambiguous without changing output RGB."""
    image = image.convert("RGB")
    channels = list(image.split())
    index = 1 if screen == "green" else 2
    other = ImageChops.lighter(*(channel for number, channel in enumerate(channels) if number != index))
    threshold = other.point(lambda value: min(255, round(value * 1.10)))
    matte = ImageChops.subtract(channels[index], threshold).point(lambda value: 255 if value else 0)
    color = (0, 255, 0) if screen == "green" else (0, 0, 255)
    image = Image.composite(Image.new("RGB", image.size, color), image, matte)
    return Image.composite(Image.new("RGB", image.size, color), image, aperture) if aperture else image


def standalone_alpha(source, raw_hint, hint, screen, layer_id, ssh_target, ssh_port, aperture=None):
    aperture_hash = sha256_bytes(aperture.convert("L").tobytes()) if aperture else "none"
    signature = sha256_bytes(f"{sha256(source)}:{sha256(raw_hint)}:{sha256(hint)}:{screen}:{aperture_hash}:{CORRIDORKEY_PROCESSOR}".encode())[:16]
    remote = f"/workspace/{RUN_ID}/{layer_id.replace(':', '-')}-{signature}"
    with tempfile.TemporaryDirectory(prefix="corridorkey-") as directory:
        staging = Path(directory)
        for name in ("source", "hint", "output"):
            (staging / name).mkdir()
        with Image.open(source) as opened:
            save_png(normalized_key_input(opened, screen, aperture), staging / "source" / "input.png")
        save_png(Image.open(hint).convert("L"), staging / "hint" / "input.png")
        options = ["-q", "-o", "ControlMaster=auto", "-o", "ControlPersist=120",
                   "-o", "ControlPath=/tmp/cover-story-ssh-%C"]
        known_hosts = os.environ.get("COVER_STORY_SSH_KNOWN_HOSTS")
        if known_hosts:
            options.extend(["-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={known_hosts}"])
        ssh = ["ssh", *options, "-p", str(ssh_port), ssh_target]
        remote_shell = f"ssh {' '.join(options)} -p {ssh_port}"
        corridor_root = os.environ.get("COVER_STORY_CORRIDORKEY_ROOT", "/workspace/CorridorKey")
        ready = subprocess.run([*ssh, f"test -f {remote}/output/rgba/input.png"]).returncode == 0
        if not ready:
            subprocess.run([*ssh, f"mkdir -p {remote}/source {remote}/hint {remote}/output"], check=True)
            subprocess.run([
                "rsync", "-az", "--no-owner", "--no-group", "-e", remote_shell,
                str(staging / "source"), str(staging / "hint"),
                f"{ssh_target}:{remote}/",
            ], check=True)
            script = TOOL_ROOT / "run_corridorkey_screenshot_test.py"
            subprocess.run([
                "rsync", "-az", "--no-owner", "--no-group", "-e", remote_shell, str(script),
                f"{ssh_target}:{remote}/run.py",
            ], check=True)
            subprocess.run([*ssh,
                f"cd {corridor_root} && .venv/bin/python {remote}/run.py "
                f"--source-dir {remote}/source --hint-dir {remote}/hint "
                f"--output-dir {remote}/output --screen {screen}"
            ], check=True)
        subprocess.run([
            "rsync", "-az", "--no-owner", "--no-group", "-e", remote_shell,
            f"{ssh_target}:{remote}/output/", str(staging / "output") + "/",
        ], check=True)
        with Image.open(staging / "output" / "rgba" / "input.png") as opened:
            return opened.convert("RGBA").getchannel("A")


def despill_source(image, screen):
    channels = list(image.convert("RGB").split())
    index = 1 if screen == "green" else 2
    other = ImageChops.lighter(*(channel for number, channel in enumerate(channels) if number != index))
    threshold = other.point(lambda value: min(255, round(value * 1.10)))
    reject = ImageChops.subtract(channels[index], threshold).point(lambda value: 255 if value else 0)
    channels[index] = Image.composite(other, channels[index], reject)
    return Image.merge("RGB", channels)


def segment_source(source, alpha, screen):
    with Image.open(source) as opened:
        image = despill_source(opened, screen).convert("RGBA")
    image.putalpha(alpha)
    return image


def split_head_layers(source, alpha, hair_hint, screen):
    """Keep CorridorKey's alpha; SAM only decides clothing order."""
    hair = hair_hint.convert("L").point(lambda value: 255 if value > 127 else 0)
    return (
        segment_source(source, ImageChops.multiply(alpha, ImageOps.invert(hair)), screen),
        segment_source(source, ImageChops.multiply(alpha, hair), screen),
    )


def jobs(catalog, scope):
    poses = catalog["poses"]
    performers = [p for p in catalog["performers"] if scope == "full" or p.get("pilot")]
    themes = catalog["themes"]
    result = []
    for pose in poses:
        result.append({"id": f"carrier:{pose['id']}", "kind": "carrier", "pose": pose["id"], "prompt": pose["carrier_prompt"], "negative_prompt": NEGATIVE, "model": CARRIER_MODEL, "settings": SETTINGS["carrier"], "references": []})
    for performer in performers:
        source = performer["source"]
        for pose in poses:
            carrier = {"job": f"carrier:{pose['id']}"}
            preprocess_id = f"preprocess:{performer['id']}:{pose['id']}"
            result.append({"id": preprocess_id, "kind": "preprocess", "performer": performer["id"], "pose": pose["id"], "prompt": f"{performer['preprocess_prompt']} {GREEN_PREPROCESS}", "negative_prompt": NEGATIVE, "model": EDIT_MODEL, "settings": SETTINGS["edit"], "references": [{"path": source, "source": True}, carrier]})
            identity = {"id": f"identity:{performer['id']}:{pose['id']}", "kind": "identity", "performer": performer["id"], "pose": pose["id"], "prompt": PROMPTS["identity"], "negative_prompt": NEGATIVE, "model": EDIT_MODEL, "settings": MASKED_EDIT_SETTINGS, "references": [carrier, {"job": preprocess_id}]}
            result.append(identity)
    outfits = [(theme, theme["outfits"][0] if scope != "full" else outfit) for theme in themes for outfit in (theme["outfits"] if scope == "full" else [theme["outfits"][0]])]
    for theme, outfit in outfits:
        tones = outfit["tone_groups"] if outfit.get("exposed_skin", True) else ["all"]
        for pose in poses:
            for tone in tones:
                common = {"theme": theme["id"], "outfit": outfit["id"], "pose": pose["id"], "tone": tone,
                          "negative_prompt": NEGATIVE, "model": EDIT_MODEL, "settings": MASKED_EDIT_SETTINGS}
                color = outfit["key_color"]
                result.append({**common, "id": f"body:{theme['id']}:{outfit['id']}:{pose['id']}:{tone}",
                               "kind": "body", "key_color": color,
                               "prompt": outfit["prompt"].replace("Keep the pose and body outline.", "Keep the pose and camera framing; the garment may extend into the surrounding chroma screen where its silhouette requires.").replace("The {key_name} head, neck, upper-chest aperture and background are locked and must remain unchanged.", "Keep the {key_name} head, neck and background chroma keyed, but rebuild the garment neckline and collar; no green-screen suit or collar may remain.").format(description=outfit["description"], key="chroma-key blue" if color == "blue" else "chroma-key green", key_name=f"{color} chroma-key", tone=tone),
                               "references": [{"job": f"carrier:{pose['id']}", "variant": color}]})
    for theme in themes:
        backgrounds = theme["backgrounds"] if scope == "full" else [b for b in theme["backgrounds"] if b.get("pilot")]
        for background in backgrounds:
            result.append({"id": f"background:{theme['id']}:{background['id']}", "kind": "background", "theme": theme["id"], "background": background["id"], "prompt": background["prompt"], "negative_prompt": NEGATIVE, "model": CARRIER_MODEL, "settings": {**SETTINGS["carrier"], "canvas": [1920, 1080]}, "references": []})
    return result


def carrier_variant(reference, manifest, root):
    source = output_for(manifest, reference["job"], require_accepted=True)
    source_path = root / source["path"]
    color = reference["variant"]
    if color == "green":
        return source_path, source
    signature = {"source": source, "processor": "swap-green-blue-channels-v1", "color": color}
    variants = manifest.setdefault("carrier_variants", {})
    variant_id = f"{reference['job']}:{color}"
    existing = variants.get(variant_id)
    if existing:
        path = root / existing["output"]["path"]
        if existing.get("signature") != signature or not path.is_file() or sha256(path) != existing["output"]["sha256"]:
            raise ValueError(f"changed carrier variant: {variant_id}")
        return path, existing["output"]
    with Image.open(source_path) as opened:
        red, green, blue = opened.convert("RGB").split()
    path = root / "raw" / "carrier_variants" / f"{reference['job'].split(':', 1)[1]}-{color}.png"
    save_png(Image.merge("RGB", (red, blue, green)), path)
    variants[variant_id] = {"signature": signature, "output": image_record(path, root)}
    persist(root, manifest)
    return path, variants[variant_id]["output"]


def materialize_refs(job, manifest, catalog, root):
    records, paths = [], []
    for reference in job["references"]:
        record = None
        if reference.get("source"):
            path = source_path(catalog, reference["path"])
        elif reference.get("variant"):
            path, record = carrier_variant(reference, manifest, root)
        else:
            path = Path(root) / output_for(
                manifest, reference["job"], require_accepted=reference["job"].startswith("carrier:")
            )["path"]
        if not path.is_file():
            raise FileNotFoundError(path)
        records.append(record or image_record(path, root))
        paths.append(path)
    return records, paths


def output_for(manifest, job_id, require_accepted=False):
    attempts = manifest.get("attempts", {}).get(job_id, [])
    accepted = manifest.get("accepted", {}).get(job_id)
    if accepted and "attempt" in accepted:
        index = accepted["attempt"]
        if 0 <= index < len(attempts) and attempts[index].get("output"):
            return attempts[index]["output"]
    if require_accepted:
        raise ValueError(f"{job_id} must pass human review before downstream generation")
    for attempt in reversed(attempts):
        if attempt.get("state") == "generated_pending_review" and attempt.get("output"):
            return attempt["output"]
    raise ValueError(f"no automatically passing output for {job_id}")


def generation_rechecks(job, paths, path, edit_mask=None):
    return semantic_checks(job, path)


def run_generation(server, job, root, manifest, catalog, cache, retry_rejected=False):
    refs, paths = materialize_refs(job, manifest, catalog, root)
    edit_mask, edit_mask_settings = prepare_edit_mask(server, job, paths, root, cache)
    signature = {key: job[key] for key in ("kind", "prompt", "negative_prompt", "model", "settings")}
    if job.get("key_color"):
        signature["key_color"] = job["key_color"]
    signature["reference_order"] = refs
    if edit_mask:
        signature["edit_mask"] = image_record(edit_mask, root)
        signature["edit_mask_settings"] = edit_mask_settings
    attempts = manifest.setdefault("attempts", {}).setdefault(job["id"], [])
    for index, previous in enumerate(attempts):
        expected_signature = {**signature, "seed": seed_for(job["id"], index)}
        if previous.get("signature") != expected_signature:
            raise ValueError(f"immutable provenance changed for {job['id']}")
        if previous.get("state") == "running":
            previous.update({"state": "interrupted", "error": "runner restarted before completion"})
            persist(root, manifest)
        if previous.get("state") == "rejected_automatic" and previous.get("output"):
            path = root / previous["output"]["path"]
            rechecked = generation_rechecks(job, paths, path, edit_mask)
            unchanged = [check for check in previous.get("checks", []) if check["name"] not in {item["name"] for item in rechecked}]
            if rechecked and all(check["passed"] for check in unchanged + rechecked):
                previous.update({"state": "generated_pending_review", "checks": unchanged + rechecked})
                persist(root, manifest)
                return previous
        if previous.get("state") == "rejected_human" and not retry_rejected:
            raise ValueError(f"{job['id']} was human-rejected; rerun with --retry-rejected")
        if previous.get("state") == "generated_pending_review":
            output = previous["output"]
            if all(Path(root, item["path"]).is_file() and sha256(Path(root, item["path"])) == item["sha256"] for item in previous.get("outputs", [])):
                rechecked = generation_rechecks(job, paths, root / output["path"], edit_mask)
                unchanged = [check for check in previous.get("checks", []) if check["name"] not in {item["name"] for item in rechecked}]
                if rechecked and not all(check["passed"] for check in unchanged + rechecked):
                    previous.update({"state": "rejected_automatic", "checks": unchanged + rechecked})
                    persist(root, manifest)
                    continue
                if rechecked and previous.get("checks") != unchanged + rechecked:
                    previous["checks"] = unchanged + rechecked
                    persist(root, manifest)
                return previous
            raise ValueError(f"completed output changed or disappeared for {job['id']}")
    resume = len(attempts) - 1 if attempts and attempts[-1].get("state") == "interrupted" else None
    if resume is None and len(attempts) >= MAX_AUTOMATIC_RETRIES + 1:
        raise RuntimeError(f"automatic retry limit reached for {job['id']}")
    index = resume if resume is not None else len(attempts)
    seed = seed_for(job["id"], index)
    attempt = {"signature": {**signature, "seed": seed}, "state": "running", "retry_index": index, "outputs": [], "checks": []}
    if resume is None:
        attempts.append(attempt)
    else:
        attempts[index] = attempt
    persist(root, manifest)
    try:
        work = root / "_work"
        work.mkdir(parents=True, exist_ok=True)
        prefix = f"cover-story/{RUN_ID}/{job['id'].replace(':', '-')}-s{seed}"
        if job["kind"] == "carrier":
            result = run(server, generation_graph(job["prompt"], seed, prefix), Path(tempfile.mkdtemp(prefix="carrier-", dir=work)), 1800)
            raw = root / "raw" / "carriers" / f"{job['pose']}-s{seed}-1328.png"
            output = root / "raw" / "carriers" / f"{job['pose']}-s{seed}.png"
            save_png(Image.open(pick(result, "raw" )).convert("RGB"), raw)
            save_png(Image.open(pick(result, "canonical")).convert("RGB"), output)
            artifacts = [image_record(raw, root), image_record(output, root)]
            result_path = output
            expected = CANVAS
        elif job["kind"] == "background":
            result = run(server, generation_graph(job["prompt"], seed, prefix, (1920, 1080), False), Path(tempfile.mkdtemp(prefix="background-", dir=work)), 1800)
            result_path = root / "raw" / "backgrounds" / f"{job['theme']}-{job['background']}-s{seed}.png"
            save_png(Image.open(pick(result, "raw")).convert("RGB"), result_path)
            artifacts, expected = [image_record(result_path, root)], (1920, 1080)
        else:
            image1 = paths[0]
            image2 = paths[1] if len(paths) > 1 else None
            remote_mask = upload_image(server, edit_mask, subfolder="cover-story/layered/input") if edit_mask else None
            result = run(server, edit_graph(upload_image(server, image1, subfolder="cover-story/layered/input"), job["prompt"], seed, prefix, upload_image(server, image2, subfolder="cover-story/layered/input") if image2 else None, remote_mask), Path(tempfile.mkdtemp(prefix="edit-", dir=work)), 1800)
            result_path = root / "raw" / job["kind"] / f"{job['id'].replace(':', '-')}-s{seed}.png"
            if edit_mask:
                raw_path = result_path.with_name(f"{result_path.stem}-raw.png")
                save_png(Image.open(pick(result, "-raw")).convert("RGB"), raw_path)
                save_png(Image.open(pick(result, "-masked")).convert("RGB"), result_path)
                artifacts = [image_record(raw_path, root), image_record(result_path, root)]
            else:
                save_png(Image.open(pick(result, job["id"].replace(":", "-"))).convert("RGB"), result_path)
                artifacts = [image_record(result_path, root)]
            expected = CANVAS
        checks = [check_image(result_path, None if job["kind"] == "preprocess" else expected, "RGB")]
        if job["kind"] == "preprocess":
            checks.append(aspect_check(result_path, paths[0]))
        if job["kind"] == "carrier":
            checks.extend(carrier_checks(result_path))
        if job["kind"] in {"identity", "body"}:
            detail = registration(paths[0], result_path)
            checks.append({"name": "registration", "passed": detail["accepted"], "detail": detail})
        if edit_mask:
            checks.append(outside_mask_changed(paths[0], result_path, edit_mask))
        checks.extend(semantic_checks(job, result_path))
        attempt.update({"state": "generated_pending_review" if all(item["passed"] for item in checks) else "rejected_automatic", "outputs": artifacts, "output": artifacts[-1], "checks": checks})
        persist(root, manifest)
    except BaseException as exc:
        attempt.update({"state": "interrupted", "error": f"{type(exc).__name__}: {exc}", "outputs": []})
        persist(root, manifest)
        raise
    if attempt["state"] == "rejected_automatic":
        if index >= MAX_AUTOMATIC_RETRIES:
            raise RuntimeError(f"automatic checks failed for {job['id']} after {index + 1} seeds")
        return run_generation(server, job, root, manifest, catalog, cache, retry_rejected)
    return attempt


def matches(job, pose=None, performer=None, theme=None, tone=None, kind=None):
    return (not kind or job["kind"] == kind) and (not pose or job.get("pose") == pose) and \
        (not performer or not job.get("performer") or job["performer"] == performer) and \
        (not theme or not job.get("theme") or job["theme"] == theme) and \
        (not tone or not job.get("tone") or job["tone"] == tone)


def generate(server, root, catalog, scope, stage, pose=None, performer=None, theme=None, tone=None,
             kind=None, retry_rejected=False):
    verify_nodes(server)
    manifest = ensure_manifest(root, catalog, scope)
    cache = {}
    selected = [job for job in jobs(catalog, scope) if (job["kind"] == "carrier") == (stage == "carriers")]
    selected = [job for job in selected if matches(job, pose, performer, theme, tone, kind)]
    if stage == "pilot":
        required_poses = {job["pose"] for job in selected if job.get("pose")}
        missing = [f"carrier:{pose_id}" for pose_id in sorted(required_poses) if f"carrier:{pose_id}" not in manifest["accepted"]]
        if missing:
            raise ValueError(f"accept all frozen carriers before the pilot: {', '.join(missing)}")
    for number, job in enumerate(selected, 1):
        print(f"[{number}/{len(selected)}] {job['id']}", flush=True)
        job["prompt"] = job["prompt"] or PROMPTS[job["kind"]].format(key="#0000ff")
        run_generation(server, job, root, manifest, catalog, cache, retry_rejected)
    manifest["status"] = "carriers_pending_visual_review" if stage == "carriers" else "generated_pending_extraction"
    persist(root, manifest)
    if stage == "carriers":
        review(root, manifest)


def extract(server, root, catalog, scope, ssh_target, ssh_port,
            pose=None, performer=None, theme=None, tone=None, kind=None):
    verify_nodes(server)
    manifest = ensure_manifest(root, catalog, scope)
    cache = {}
    selected = [job for job in jobs(catalog, scope)
                if job["kind"] in {"identity", "body"} and matches(job, pose, performer, theme, tone, kind)]

    def complete(layer_id):
        layer = manifest["layers"].get(layer_id)
        path = root / layer["output"]["path"] if layer else None
        return layer and layer.get("state") == "pending" and all(check["passed"] for check in layer["checks"]) \
            and layer.get("settings", {}).get("corridorkey", {}).get("processor") == CORRIDORKEY_PROCESSOR \
            and path.is_file() and sha256(path) == layer["output"]["sha256"]

    def write_layer(layer_id, layer_kind, rgba, source, screen, reject_colors, prompts, operation,
                    raw_hint_path, hint_path, extra=None):
        destination = root / "layers" / layer_kind / f"{layer_id.replace(':', '-')}.png"
        matte_path = root / "mattes" / layer_kind / destination.name
        qc_path = root / "qc" / layer_kind / destination.name
        save_png(rgba, destination)
        save_png(rgba.getchannel("A"), matte_path)
        save_png(Image.alpha_composite(checkerboard(CANVAS), rgba).convert("RGB"), qc_path)
        checks = [check_image(destination, CANVAS, "RGBA"), *alpha_checks(destination, layer_kind, reject_colors, alpha_only=True)]
        state = "pending" if all(check["passed"] for check in checks) else "rejected_automatic"
        manifest["layers"][layer_id] = {
            "id": layer_id, "kind": layer_kind, "state": state, "key_color": screen,
            "source": source, "source_sha256": source["sha256"], "sam3_prompt": prompts,
            "sam3_operation": operation, "settings": {"sam3": SETTINGS["sam3"],
                "class_gate": {"enabled": False}, "rejected_key_colors": list(reject_colors),
                "edge_cleanup": EDGE_CLEANUP, "corridorkey": {**SETTINGS["corridorkey"],
                    "screen_color": screen, "processor": CORRIDORKEY_PROCESSOR,
                    "checkpoint": "CorridorKeyBlue_1.0.safetensors" if screen == "blue" else "CorridorKey_v1.0.safetensors"}},
            "hint": image_record(hint_path, root), "raw_hint": image_record(raw_hint_path, root),
            "output": image_record(destination, root), "matte": image_record(matte_path, root),
            "qc": image_record(qc_path, root), "checks": checks, **(extra or {}),
        }
        persist(root, manifest)
        return state

    for number, job in enumerate(selected, 1):
        print(f"[{number}/{len(selected)}] {job['id']}", flush=True)
        source = output_for(manifest, job["id"])
        source_path_local = root / source["path"]
        if sha256(source_path_local) != source["sha256"]:
            raise ValueError(f"source output changed: {source_path_local}")
        if job["kind"] == "identity":
            skin_id = f"skin:{job['performer']}:{job['pose']}"
            hair_id = f"hair:{job['performer']}:{job['pose']}"
            if complete(skin_id) and complete(hair_id):
                continue
            screen, reject_colors, prompts = "green", ("green",), PLATE_PROMPTS["head"]
            base_name = f"head-{job['performer']}-{job['pose']}.png"
            raw_hint_path = root / "hints" / "raw" / base_name
            hint_path = root / "hints" / "dilated" / base_name
            hair_hint_path = root / "hints" / "hair-order" / base_name
            remote = cache.setdefault(str(source_path_local.resolve()), upload_image(server, source_path_local, subfolder="cover-story/layered/input"))
            prefix = f"cover-story/layered-extract/head-{job['performer']}-{job['pose']}-green"
            (root / "_work").mkdir(parents=True, exist_ok=True)
            result = run(server, sam_graph(remote, prompts, prefix), Path(tempfile.mkdtemp(prefix="extract-", dir=root / "_work")), 1800)
            hints = sorted(Path(item["path"]) for item in result["images"] if "-raw-hint-" in Path(item["path"]).stem)
            if len(hints) != 2:
                raise ValueError(f"expected head and hair SAM hints, found {len(hints)}")
            with Image.open(hints[0]) as opened:
                raw_hint = opened.convert("L")
            with Image.open(hints[1]) as opened:
                hair_hint = opened.convert("L")
            save_png(raw_hint, raw_hint_path)
            save_png(raw_hint.filter(ImageFilter.MaxFilter(SETTINGS["sam3"]["dilation_px"] * 2 + 1)), hint_path)
            save_png(hair_hint, hair_hint_path)
            alpha = standalone_alpha(source_path_local, raw_hint_path, hint_path, screen, skin_id, ssh_target, ssh_port)
            skin, hair = split_head_layers(source_path_local, alpha, hair_hint, screen)
            extra = {"hair_order_hint": image_record(hair_hint_path, root), "alpha_source": "CorridorKey alpha applied to original identity RGB", "rgb_cleanup": "source-rgb chroma despill"}
            skin_state = write_layer(skin_id, "skin", skin, source, screen, reject_colors, prompts, "head-minus-hair-order", raw_hint_path, hint_path, extra)
            hair_state = write_layer(hair_id, "hair", hair, source, screen, reject_colors, prompts, "hair-order", raw_hint_path, hint_path, extra)
            if "rejected_automatic" in (skin_state, hair_state):
                raise RuntimeError(f"automatic extraction checks failed for {job['id']}")
            continue

        layer_id = f"body:{job['theme']}:{job['outfit']}:{job['pose']}:{job['tone']}"
        if complete(layer_id):
            continue
        theme_entry = next(item for item in catalog["themes"] if item["id"] == job["theme"])
        outfit = next(item for item in theme_entry["outfits"] if item["id"] == job["outfit"])
        screen, reject_colors, prompts = job["key_color"], rejected_key_colors(outfit["description"]), PLATE_PROMPTS["body"]
        base_name = f"{layer_id.replace(':', '-')}.png"
        raw_hint_path = root / "hints" / "raw" / base_name
        hint_path = root / "hints" / "dilated" / base_name
        remote = cache.setdefault(str(source_path_local.resolve()), upload_image(server, source_path_local, subfolder="cover-story/layered/input"))
        prefix = f"cover-story/layered-extract/{layer_id.replace(':', '-')}-{screen}"
        (root / "_work").mkdir(parents=True, exist_ok=True)
        result = run(server, sam_graph(remote, prompts, prefix), Path(tempfile.mkdtemp(prefix="extract-", dir=root / "_work")), 1800)
        hints = sorted(Path(item["path"]) for item in result["images"] if "-raw-hint-" in Path(item["path"]).stem)
        if len(hints) != len(prompts):
            raise ValueError(f"expected {len(prompts)} SAM hints, found {len(hints)}")
        raw_hint = Image.new("L", CANVAS)
        for path in hints[:-1]:
            with Image.open(path) as opened:
                raw_hint = ImageChops.lighter(raw_hint, opened.convert("L"))
        with Image.open(hints[-1]) as opened:
            aperture = opened.convert("L").filter(ImageFilter.MaxFilter(7))
        raw_hint = ImageChops.subtract(raw_hint, aperture)
        save_png(raw_hint, raw_hint_path)
        save_png(raw_hint.filter(ImageFilter.MaxFilter(SETTINGS["sam3"]["dilation_px"] * 2 + 1)), hint_path)
        alpha = standalone_alpha(source_path_local, raw_hint_path, hint_path, screen, layer_id, ssh_target, ssh_port, aperture)
        state = write_layer(layer_id, "body", segment_source(source_path_local, alpha, screen), source, screen, reject_colors,
                            prompts, "union-minus-head-neck-upper-chest", raw_hint_path, hint_path)
        if state == "rejected_automatic":
            raise RuntimeError(f"automatic extraction checks failed for {layer_id}")
    manifest["status"] = "extracted_pending_composition"
    persist(root, manifest)


def checkerboard(size, cell=32):
    image = Image.new("RGBA", size, (42, 42, 42, 255))
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(86, 86, 86, 255))
    return image


def compose(catalog, root, scope, pose_filter=None, performer_id=None, theme_id=None, tone_filter=None):
    manifest = ensure_manifest(root, catalog, scope)
    performers = [p for p in catalog["performers"] if scope == "full" or p.get("pilot")]
    performers = [p for p in performers if not performer_id or p["id"] == performer_id]
    poses = [p for p in catalog["poses"] if not pose_filter or p["id"] == pose_filter]
    for performer in performers:
        for theme in [t for t in catalog["themes"] if not theme_id or t["id"] == theme_id]:
            outfits = theme["outfits"] if scope == "full" else theme["outfits"][:1]
            background = next(b for b in theme["backgrounds"] if b.get("pilot"))
            bg_record = output_for(manifest, f"background:{theme['id']}:{background['id']}")
            bg_path = root / bg_record["path"]
            for outfit in outfits:
              for pose in poses:
                identity = f"{performer['id']}:{pose['id']}"
                tone = performer["skin_tone_group"] if outfit.get("exposed_skin", True) else "all"
                if tone_filter and tone != tone_filter:
                    continue
                layer_ids = {
                    "skin": f"skin:{performer['id']}:{pose['id']}",
                    "body": f"body:{theme['id']}:{outfit['id']}:{pose['id']}:{tone}",
                    "hair": f"hair:{performer['id']}:{pose['id']}",
                }
                if any(layer_id not in manifest["layers"] or
                       not all(check["passed"] for check in manifest["layers"][layer_id]["checks"])
                       for layer_id in layer_ids.values()):
                    raise ValueError(f"missing extracted layers for {identity} / {theme['id']}")
                output_dir = root / "composites" / f"{performer['id']}-{theme['id']}-{outfit['id']}-{pose['id']}"
                composite_id = f"{performer['id']}:{theme['id']}:{outfit['id']}:{pose['id']}"
                full = output_dir / "full.png"
                card = output_dir / "card.png"
                existing = manifest["composites"].get(composite_id)
                current_layers = {kind: manifest["layers"][layer_id]["output"] for kind, layer_id in layer_ids.items()}
                if existing and existing.get("layers") == current_layers and existing.get("background") == bg_record:
                    if all(Path(root, existing[name]["path"]).is_file() and sha256(Path(root, existing[name]["path"])) == existing[name]["sha256"] for name in ("full", "card")):
                        continue
                    raise ValueError(f"completed composite changed or disappeared: {composite_id}")
                with Image.open(bg_path) as opened:
                    background_image = ImageOps.fit(opened.convert("RGBA"), CANVAS, Image.Resampling.LANCZOS)
                layers = {}
                for kind, layer_id in layer_ids.items():
                    with Image.open(root / manifest["layers"][layer_id]["output"]["path"]) as opened:
                        layers[kind] = opened.convert("RGBA")
                foreground = Image.new("RGBA", CANVAS)
                for kind in ("skin", "body", "hair"):
                    foreground.alpha_composite(layers[kind])
                result = background_image.copy()
                result.alpha_composite(foreground)
                save_png(result.convert("RGB"), full)
                save_png(ImageOps.fit(result.convert("RGB"), CARD, Image.Resampling.LANCZOS), card)
                closeups = {}
                for name, box in {"face-hair": (220, 20, 804, 520), "neckline": (270, 180, 754, 610), "hands-shoes": (90, 430, 934, 1024)}.items():
                    path = output_dir / f"{name}.png"
                    save_png(result.crop(box).convert("RGB"), path)
                    closeups[name] = image_record(path, root)
                previews = {}
                for kind, layer in layers.items():
                    path = output_dir / f"{kind}-checker.png"
                    save_png(Image.alpha_composite(checkerboard(CANVAS), layer), path)
                    previews[kind] = image_record(path, root)
                checks = [check_image(full, CANVAS, "RGB"), check_image(card, CARD, "RGB"), {"name": "foreground_key_residue", "passed": all(key_residue(layers[kind], color) < 0.002 for kind, layer_id in layer_ids.items() for color in manifest["layers"][layer_id]["settings"]["rejected_key_colors"]), "detail": "checked rejected chroma colors in opaque layer pixels"}]
                job_ids = {
                    "background": f"background:{theme['id']}:{background['id']}",
                    "skin": f"identity:{performer['id']}:{pose['id']}",
                    "body": f"body:{theme['id']}:{outfit['id']}:{pose['id']}:{tone}",
                    "hair": f"identity:{performer['id']}:{pose['id']}",
                }
                provenance = {}
                for kind, job_id in job_ids.items():
                    attempt = next(attempt for attempt in reversed(manifest["attempts"][job_id]) if attempt.get("state") == "generated_pending_review")
                    provenance[kind] = {"prompt": attempt["signature"]["prompt"], "seed": attempt["signature"]["seed"], "reference_order": attempt["signature"]["reference_order"], "key_color": manifest["layers"].get(layer_ids.get(kind), {}).get("key_color")}
                manifest["composites"][composite_id] = {"id": composite_id, "performer": performer["id"], "theme": theme["id"], "outfit": outfit["id"], "pose": pose["id"], "tone": tone, "order": ["background", "skin", "clothing-body", "hair"], "background": image_record(bg_path, root), "layers": current_layers, "full": image_record(full, root), "card": image_record(card, root), "closeups": closeups, "previews": previews, "checks": checks, "provenance": provenance, "state": "pending"}
                persist(root, manifest)
    manifest["status"] = "composed_pending_visual_review"
    persist(root, manifest)


def review_html(root, manifest):
    def src(record):
        return html.escape(record["path"])
    carrier_articles = []
    for job_id in sorted(job_id for job_id in manifest.get("attempts", {}) if job_id.startswith("carrier:")):
        accepted = manifest.get("accepted", {}).get(job_id, {}).get("attempt")
        for index, attempt in enumerate(manifest["attempts"][job_id]):
            if not attempt.get("output"):
                continue
            state = "accepted" if accepted == index else attempt.get("state", "pending")
            images = "".join(
                f'<figure><img src="{src(record)}" alt="carrier output"><figcaption>{html.escape(Path(record["path"]).name)}</figcaption></figure>'
                for record in attempt.get("outputs", [attempt["output"]])
            )
            checks = "".join(
                f'<li class="{"pass" if check["passed"] else "fail"}">{html.escape(check["name"])}: {"pass" if check["passed"] else "fail"}</li>'
                for check in attempt.get("checks", [])
            )
            provenance = html.escape(json.dumps(attempt.get("signature", {}), indent=2, ensure_ascii=False))
            note = html.escape(attempt.get("review", {}).get("note", ""))
            carrier_articles.append(f'<article><h2>{html.escape(job_id)} · attempt {index} · {html.escape(state)}</h2><div class="row">{images}</div><ul>{checks}</ul><details><summary>Prompt, seed, settings and hashes</summary><pre>{provenance}</pre></details><p class="note">Reviewer note: {note}</p></article>')
    generation_articles = []
    for job_id in sorted(job_id for job_id in manifest.get("attempts", {}) if not job_id.startswith("carrier:")):
        accepted = manifest.get("accepted", {}).get(job_id, {}).get("attempt")
        for index, attempt in enumerate(manifest["attempts"][job_id]):
            if not attempt.get("output"):
                continue
            state = "accepted" if accepted == index else attempt.get("state", "pending")
            record = attempt["output"]
            checks = "".join(f'<li class="{"pass" if check["passed"] else "fail"}">{html.escape(check["name"])}: {"pass" if check["passed"] else "fail"}</li>' for check in attempt.get("checks", []))
            provenance = html.escape(json.dumps(attempt.get("signature", {}), indent=2, ensure_ascii=False))
            generation_articles.append(f'<article><h2>{html.escape(job_id)} · attempt {index} · {html.escape(state)}</h2><div class="row"><figure><img src="{src(record)}" alt="generated output"><figcaption>full output</figcaption></figure><figure><div class="closeup"><img src="{src(record)}" alt="face and hair closeup"></div><figcaption>face / hair closeup</figcaption></figure></div><ul>{checks}</ul><details><summary>Prompt, seed, settings and hashes</summary><pre>{provenance}</pre></details></article>')
    articles = []
    for composite_id, item in manifest.get("composites", {}).items():
        note = manifest.get("reviews", {}).get(composite_id, {}).get("note", "")
        state = manifest.get("reviews", {}).get(composite_id, {}).get("state", item.get("state", "pending"))
        toggles = "".join(f'<label><input type="checkbox" checked data-id="{html.escape(composite_id)}" data-toggle="{kind}"> {label}</label>' for kind, label in (("background", "background"), ("skin", "skin"), ("body", "body / clothing"), ("hair", "hair")))
        stage = f'<div class="stage" data-composite="{html.escape(composite_id)}"><img class="background" src="{src(item["background"])}" alt="background"><img class="skin" src="{src(item["layers"]["skin"])}" alt="skin"><img class="body" src="{src(item["layers"]["body"])}" alt="clothed body"><img class="hair" src="{src(item["layers"]["hair"])}" alt="hair"></div>'
        panels = "".join(f'<figure><img src="{src(item["previews"][kind])}" alt="{kind} checkerboard"><figcaption>{kind} checkerboard</figcaption></figure>' for kind in ("skin", "body", "hair"))
        closeups = "".join(f'<figure><img src="{src(item["closeups"][name])}" alt="{name}"><figcaption>{name}</figcaption></figure>' for name in ("face-hair", "neckline", "hands-shoes"))
        checks = "".join(f'<li class="{"pass" if check["passed"] else "fail"}">{html.escape(check["name"])}: {"pass" if check["passed"] else "fail"}</li>' for check in item["checks"])
        provenance = html.escape(json.dumps(item.get("provenance", {}), indent=2, ensure_ascii=False))
        articles.append(f'<article><h2>{html.escape(composite_id)} · {html.escape(state)}</h2><div class="toggles">{toggles}</div>{stage}<div class="row"><img src="{src(item["full"])}" alt="full composite"><img src="{src(item["card"])}" alt="600 by 900 card crop"></div><h3>Raw alpha layers</h3><div class="row">{panels}</div><h3>Closeups</h3><div class="row">{closeups}</div><ul>{checks}</ul><details><summary>Prompt, seed, key color and reference order</summary><pre>{provenance}</pre></details><p class="note">Reviewer note: {html.escape(note or "")}</p></article>')
    source = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Cover Story layered costume review</title><style>:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;padding:24px;background:#111318;color:#eee;font:14px/1.45 system-ui,sans-serif}main{max-width:1500px;margin:auto}article{margin:24px 0;padding:16px;background:#1b1e25;border:1px solid #343946;border-radius:12px}h1,h2,h3,p{margin:0 0 10px}.muted,.note,figcaption{color:#aab1bf}.stage{position:relative;max-width:560px;aspect-ratio:1;background:#363941;background-image:linear-gradient(45deg,#4b4f59 25%,transparent 25%),linear-gradient(-45deg,#4b4f59 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#4b4f59 75%),linear-gradient(-45deg,transparent 75%,#4b4f59 75%);background-size:28px 28px;background-position:0 0,0 14px,14px -14px,-14px 0}.stage img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}.stage .skin{z-index:1}.stage .body{z-index:2}.stage .hair{z-index:3}.toggles{display:flex;gap:14px;flex-wrap:wrap;margin:8px 0 12px}.row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:10px 0}.row>img,.row figure img{display:block;width:100%;max-height:520px;object-fit:contain;background:#292d36}.row figure{margin:0}.closeup{height:520px;overflow:hidden;background:#292d36}.closeup img{width:100%;height:100%;object-fit:cover;object-position:center top;transform:scale(1.8);transform-origin:center top}.pass{color:#8ee28e}.fail{color:#ff8d8d}.note{padding:8px;background:#171a20}@media(max-width:800px){body{padding:12px}.row{grid-template-columns:1fr}.stage{max-width:none}}</style><main><h1>Cover Story layered-costume production pilot</h1><p class="muted">Static review. Toggle background, skin, clothed body, and hair. Apply human decisions with a JSON file passed to the review phase; rejected entries require a note.</p><h1>Canonical carriers</h1>""" + "".join(carrier_articles) + "<h1>Generated plates</h1>" + "".join(generation_articles) + "<h1>Layered composites</h1>" + "".join(articles) + """</main><script>document.querySelectorAll('[data-toggle]').forEach(x=>x.onchange=()=>document.querySelectorAll('[data-composite="'+x.dataset.id+'"] .'+x.dataset.toggle).forEach(y=>y.hidden=!x.checked))</script>"""
    return source


def review(root, manifest, reviews_path=None):
    if reviews_path and reviews_path.is_file():
        reviews = json.loads(reviews_path.read_text(encoding="utf-8"))
        for job_id, decision in reviews.get("attempts", {}).items():
            attempts = manifest.get("attempts", {}).get(job_id, [])
            index = decision.get("attempt")
            if not isinstance(index, int) or index < 0 or index >= len(attempts):
                raise ValueError(f"unknown attempt review: {job_id} / {index}")
            state = decision.get("state", "pending")
            if state not in {"pending", "accepted", "rejected"}:
                raise ValueError(f"invalid attempt review state for {job_id}")
            if state == "rejected" and not decision.get("note", "").strip():
                raise ValueError(f"rejection requires a note: {job_id}")
            if state == "rejected":
                attempts[index].update({"state": "rejected_human", "review": {"state": state, "note": decision["note"]}})
                manifest["accepted"].pop(job_id, None)
            elif state == "accepted":
                attempt = attempts[index]
                if attempt.get("state") != "generated_pending_review":
                    raise ValueError(f"only automatic-passing attempts can be accepted: {job_id}")
                manifest["accepted"][job_id] = {"attempt": index, "output": attempt["output"]}
        for composite_id, decision in reviews.get("composites", {}).items():
            if composite_id not in manifest["composites"]:
                raise ValueError(f"unknown composite review: {composite_id}")
            state = decision.get("state", "pending")
            if state not in {"pending", "accepted", "rejected"}:
                raise ValueError(f"invalid review state for {composite_id}")
            if state == "rejected" and not decision.get("note", "").strip():
                raise ValueError(f"rejection requires a note: {composite_id}")
            manifest["reviews"][composite_id] = {"state": state, "note": decision.get("note", "")}
            manifest["composites"][composite_id]["state"] = state
            if state == "accepted":
                item = manifest["composites"][composite_id]
                manifest["accepted"][composite_id] = {"type": "composite", "full": item["full"], "card": item["card"]}
            else:
                manifest["accepted"].pop(composite_id, None)
    atomic_text(root / "review.html", review_html(root, manifest))
    persist(root, manifest)
    print(root / "review.html")


def export(catalog, root, scope):
    manifest = ensure_manifest(root, catalog, scope)
    accepted = [item for item in manifest.get("composites", {}).values() if item.get("state") == "accepted"]
    if not accepted:
        raise ValueError("export requires at least one human-accepted composite")
    output_root = root / "export"
    exports = []
    for item in accepted:
        actor = Image.new("RGBA", CANVAS)
        for kind in ("skin", "body", "hair"):
            path = root / item["layers"][kind]["path"]
            with Image.open(path) as opened:
                actor.alpha_composite(opened.convert("RGBA"))
            destination = output_root / "layers" / kind / f"{item['id'].replace(':', '-')}.webp"
            with Image.open(path) as opened:
                layer = opened.convert("RGBA")
            save_webp(layer, destination, lossless=True)
            save_avif(layer, destination.with_suffix(".avif"), quality=70, alpha_quality=100)
        background_path = root / item["background"]["path"]
        with Image.open(background_path) as opened:
            background = opened.convert("RGB")
        fallback = scene_composite(background, [(item["id"], background.width // 2)], {item["id"]: actor})
        fallback_path = output_root / "fallbacks" / f"{item['id'].replace(':', '-')}.webp"
        save_webp(fallback, fallback_path, quality=82)
        exports.append({"id": item["id"], "fallback": image_record(fallback_path, root)})
    atomic_json(output_root / "manifest.json", {"version": VERSION, "source_manifest": sha256(root / "manifest.json"), "exports": exports})
    print(f"exported {len(exports)} accepted layered composites to {output_root}")


def self_test():
    assert MAX_AUTOMATIC_RETRIES == 2
    assert seed_for("carrier:center", 0) != seed_for("carrier:center", 1)
    assert len({seed_for("carrier:center", n) for n in range(3)}) == 3
    assert key_color(Image.new("RGB", (4, 4), (0, 255, 0)), "blue outfit") == "blue"
    assert key_color(Image.new("RGB", (4, 4), (0, 0, 255)), "green outfit") == "green"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        first = root / "first.png"
        second = root / "second.png"
        image = Image.new("RGB", CANVAS, "black")
        ImageDraw.Draw(image).rectangle((300, 420, 724, 900), fill="white")
        image.save(first)
        image.save(second)
        result = registration(first, second)
        assert result["accepted"] and result["translation_px"] == [0, 0]
        carrier = root / "carrier.png"
        Image.new("RGB", CANVAS, (0, 255, 0)).save(carrier)
        assert screen_color(carrier) == "green"
        assert all(check["passed"] for check in carrier_checks(carrier))
        carrier_record = image_record(carrier, root)
        manifest = {"attempts": {"carrier:center": [{"state": "generated_pending_review", "output": carrier_record}]},
                    "accepted": {"carrier:center": {"attempt": 0}}, "carrier_variants": {}}
        blue_carrier, _ = carrier_variant({"job": "carrier:center", "variant": "blue"}, manifest, root)
        assert screen_color(blue_carrier) == "blue"
        rgba = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
        ImageDraw.Draw(rgba).rectangle((400, 300, 620, 900), fill=(180, 100, 80, 255))
        layer = root / "layer.png"
        rgba.save(layer)
        assert check_image(layer, CANVAS, "RGBA")["passed"]
        assert alpha_checks(layer, "hair")
        blue = root / "blue.png"
        Image.new("RGB", CANVAS, "blue").save(blue)
        assert screen_color(blue) == "blue"
        assert key_region_check(blue, "blue", (420, 70, 604, 245), "face")["passed"]
        assert not key_region_check(first, "blue", (420, 70, 604, 245), "face")["passed"]
        hair_hint = Image.new("L", CANVAS)
        ImageDraw.Draw(hair_hint).rectangle((400, 300, 510, 900), fill=255)
        head_alpha = Image.new("L", CANVAS)
        ImageDraw.Draw(head_alpha).rectangle((400, 300, 620, 900), fill=180)
        skin, hair = split_head_layers(carrier, head_alpha, hair_hint, "green")
        rebuilt = Image.alpha_composite(skin, hair)
        assert rebuilt.getchannel("A").tobytes() == head_alpha.tobytes()
        assert normalized_key_input(Image.new("RGB", (1, 1), (20, 90, 30)), "green").getpixel((0, 0)) == (0, 255, 0)
        assert normalized_key_input(Image.new("RGB", (1, 1), (200, 10, 10)), "green", Image.new("L", (1, 1), 255)).getpixel((0, 0)) == (0, 255, 0)
        assert despill_source(Image.new("RGB", (1, 1), (20, 90, 30)), "green").getpixel((0, 0))[1] == 30
    graph = edit_graph("carrier.png", PROMPTS["identity"], 1, "test", "performer.png", "mask.png")
    assert graph["7"]["inputs"]["image1"] == ["6", 0]
    assert graph["8"]["inputs"]["image2"] == ["16", 0]
    assert graph["13"]["inputs"]["latent_image"] == ["19", 0]
    assert graph["20"]["inputs"]["mask"] == ["18", 0]
    assert sam_graph("source.png", PLATE_PROMPTS["body"], "test")["4"]["class_type"] == "SAM3_Detect"
    catalog = load_catalog()
    assert len([item for item in catalog["performers"] if item.get("pilot")]) == 4
    pilot_jobs = jobs(catalog, "pilot")
    assert len([job for job in pilot_jobs if matches(job, theme="viking", kind="background")]) == 2
    body_jobs = [job for job in pilot_jobs if job["kind"] == "body"]
    assert len(body_jobs) == 48 and not any(job["kind"] in {"body_source", "body_background"} for job in pilot_jobs)
    assert next(job for job in body_jobs if job["theme"] == "viking")["references"][0]["variant"] == "blue"
    assert next(job for job in body_jobs if job["theme"] == "victorian")["references"][0]["variant"] == "green"
    assert rejected_key_colors("rust-brown wool") == ("green", "blue")
    assert rejected_key_colors("navy uniform") == ("green",)
    preprocess = [job for job in pilot_jobs if job["kind"] == "preprocess"]
    assert len(preprocess) == 16 and preprocess[0]["references"][0].get("source")
    assert preprocess[0]["references"][1]["job"].startswith("carrier:")
    assert "Canonical carriers" in review_html(Path("."), {"attempts": {}, "composites": {}})
    assert "Generated plates" in review_html(Path("."), {"attempts": {}, "composites": {}})
    print("layered costume production self-test passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("generate", "extract", "compose", "review", "export", "self-test"))
    parser.add_argument("--server", default=os.environ.get("COMFY_SERVER"))
    parser.add_argument("--ssh-target", default=os.environ.get("COVER_STORY_SSH_TARGET"))
    parser.add_argument("--ssh-port", type=int, default=os.environ.get("COVER_STORY_SSH_PORT"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--scope", choices=("pilot", "full"), default="pilot")
    parser.add_argument("--stage", choices=("carriers", "pilot"), default="carriers",
                        help="generate/review frozen carriers first; pilot requires accepted carriers")
    parser.add_argument("--pose", choices=("left", "right", "center", "pointing"),
                        help="smoke-test or resume one pose only")
    parser.add_argument("--performer", help="smoke-test or resume one performer only")
    parser.add_argument("--theme", help="smoke-test or resume one theme only")
    parser.add_argument("--tone", choices=("light", "medium", "dark", "all"),
                        help="smoke-test or resume one clothing tone only")
    parser.add_argument("--kind", help="generate one exact catalog job kind")
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--retry-rejected", action="store_true", help="generate the next seed for human-rejected attempts")
    args = parser.parse_args()
    if args.phase == "self-test":
        self_test()
        return
    catalog = load_catalog(args.catalog)
    if args.phase in {"generate", "extract"} and not args.server:
        parser.error("--server or COMFY_SERVER is required")
    if args.phase == "extract" and not (args.ssh_target and args.ssh_port):
        parser.error("extract requires --ssh-target/--ssh-port or COVER_STORY_SSH_TARGET/COVER_STORY_SSH_PORT")
    if args.phase == "generate":
        generate(args.server, args.output_dir, catalog, args.scope, args.stage, args.pose,
                 args.performer, args.theme, args.tone, args.kind, args.retry_rejected)
    elif args.phase == "extract":
        extract(args.server, args.output_dir, catalog, args.scope, args.ssh_target,
                args.ssh_port, args.pose, args.performer, args.theme, args.tone, args.kind)
    elif args.phase == "compose":
        compose(catalog, args.output_dir, args.scope, args.pose,
                args.performer, args.theme, args.tone)
    elif args.phase == "review":
        review(args.output_dir, ensure_manifest(args.output_dir, catalog, args.scope), args.reviews)
    elif args.phase == "export":
        export(catalog, args.output_dir, args.scope)


if __name__ == "__main__":
    main()
