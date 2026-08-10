#!/usr/bin/env python3
"""Resumable Cover Story layered-costume production pilot."""

import argparse
import html
import json
import os
import statistics
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageMath, ImageOps, ImageStat

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
# 2:3, matching the 1024x1536 source portraits and the 600x900 card, so the card crop is a scale
# rather than a decision about what to throw away. This was 1024x1024 -- what the reference document
# and the catalog's settings.canvas both said -- while every measured result ran at 832x1248, which
# registration() could not score at all.
CANVAS = (832, 1248)
CARD = (600, 900)
MAX_AUTOMATIC_RETRIES = 2
EDGE_CLEANUP = "corridorkey-alpha-only-v20"
BASE_SEED = 2026080100
# fp8mixed, not the int8_convrot variant comfy-bootstrap.json declares: that one cannot load on
# ComfyUI 0.26.2, whose QUANT_ALGOS covers only float8_e4m3fn/float8_e5m2/nvfp4, and it dies with
# KeyError: 'int8_tensorwise' at UNETLoader. Same 2511 model from the same Comfy-Org repository,
# header declaring float8_e4m3fn only. The sibling comfy-bootstrap repo still needs the same edit.
EDIT_MODEL = "qwen_image_edit_2511_fp8mixed.safetensors"
CARRIER_MODEL = "qwen_image_2512_fp8_e4m3fn.safetensors"
# Alternative carrier generator. All-in-one fp8 checkpoint, so CheckpointLoaderSimple supplies
# model, CLIP and VAE together and pod_bootstrap.sh fetches one file rather than four that must
# agree. See flux_generation_graph() for why a second carrier model is worth having at all.
FLUX_CARRIER_MODEL = "flux1-dev-fp8.safetensors"
# FLUX.2 dev, Comfy-Org repackaged split files. Three, not one: its text encoder is Mistral 3 Small
# rather than FLUX.1's T5+CLIP, so CLIPLoader needs a type this ComfyUI may not know. preflight
# probes /object_info for that enum instead of hardcoding a guess -- FLUX2_CLIP_TYPE is the fallback
# used when the probe cannot run, not an assertion that it is right.
FLUX2_MODEL = "flux2_dev_fp8mixed.safetensors"
FLUX2_TEXT_ENCODER = "mistral_3_small_flux2_fp8.safetensors"
FLUX2_VAE = "flux2-vae.safetensors"
FLUX2_CLIP_TYPE = "flux2"
# FLUX.2 Klein 9B: the distilled variant, and the one the community's masked-editing workflows use
# -- which is our case exactly, since every stage edits an existing plate rather than generating from
# scratch. Smaller and faster than dev (35.5 GB for dev's transformer alone).
#
# Its text encoder is Qwen 3 8B, not dev's Mistral 3 Small, so the CLIPLoader type differs *between
# FLUX.2 variants* -- which is why preflight probes the pod's own enum rather than trusting either
# constant. Every black-forest-labs Klein repo is gated ("gated: auto", needs a token); the mirror
# below and Comfy-Org's companions are not, so the default path needs no credentials.
FLUX2_KLEIN_MODEL = "flux-2-klein-base-9b.safetensors"
FLUX2_KLEIN_TEXT_ENCODER = "qwen_3_8b_fp8mixed.safetensors"
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
# Inverted identity transfer: the performer is image 1, the mask covers her body, and her head sits
# outside it where ImageCompositeMasked is bit-exact. Identity cannot drift because nothing
# repaints it. These are the geometry constants that transfer needs; see face_align(),
# body_repaint_mask() and silhouette_outline() for what each one is defending against.
FACE_ALIGN_TOLERANCE = 0.12
NECK_OVERLAP = 15
# How far harmonize_skin()'s mask is held back from the figure's own silhouette, in dilate()/
# erode() units (size//2 actual pixels). ImageCompositeMasked guarantees bit-exactness only
# *outside* the mask, so a mask that reached the boundary would let a body-wide edit shrink the
# silhouette within it; keeping the outermost ring original makes that impossible.
SKIN_BLEND_EDGE_GUARD = 6
OUTER_MARGIN = 25
OUTER_FEATHER = 8
OUTLINE_WIDTH = 5
# Strength, not presence, is what makes the control work, and this nearly cost the result. At 1.0
# the union ControlNet is below the threshold where it has authority over Qwen-Image-Edit: a
# control demanding feet 194 px higher was ignored outright. At 3.0 the same control was followed
# to within 1 px, and silhouette drift on a real transfer closed from 14 px to 1-2.
CONTROL_STRENGTH = 3.0
# Deterministic body construction, 2026-08-05: paste the performer's head onto the carrier's own
# body instead of asking diffusion to regenerate a body that only approximates the carrier's
# proportions, and recolor that body from the carrier's own pixels rather than an independently
# diffused skin.png, so registration holds by construction rather than by control-strength tuning
# or by chance. See head_transplant() and deterministic_skin_recolor(). HEAD_BLEND_FEATHER is
# deliberately small -- most of the head must stay bit-exact; only the join at the neck should
# blend.
HEAD_BLEND_FEATHER = 8
# Margin around the actual blended band, giving a follow-up smoothing edit some working room beyond
# the seam's own thin line without opening up so much of the shoulders/chest that it risks the
# geometry those regions are supposed to hold bit-exact.
SEAM_EDIT_MARGIN = 15
# The reference document's cross-performer pose limits, treated as a hard gate: several performers
# rendered on the same pose must land on the same silhouette to within these. See
# silhouette_spread().
SPREAD_TRANSLATION_PX = 2
SPREAD_SCALE = 0.005
NEGATIVE = "低分辨率，低画质，肢体畸形，手指畸形，画面过饱和，蜡像感，人脸无细节，过度光滑，画面具有AI感。构图混乱。文字模糊，扭曲"

# VITON person-image tone samples (PoC constants; the catalog's skin_tone_group owns these in
# production). Used by the grey-carrier/VITON path to recolor the carrier deterministically to the
# tone the exposed skin must carry, so the try-on output needs no per-tone garment generation.
TONE_RGB = {"light": (212, 176, 150), "medium": (183, 143, 110), "dark": (140, 96, 70)}
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


def image_from(path):
    with Image.open(path) as opened:
        return opened.convert("RGB").copy()


def dilate(mask, size):
    """Identical to MaxFilter(size) for the square structuring elements used here, but O(px * k)
    instead of O(px * k**2): a 97px dilation of an 832x1248 mask drops from ~27s to ~5s."""
    for _ in range(size // 2):
        mask = mask.filter(ImageFilter.MaxFilter(3))
    return mask


def erode(mask, size):
    """dilate()'s inverse: shrink a mask's interior instead of growing it."""
    return ImageOps.invert(dilate(ImageOps.invert(mask), size))


def screen_foreground(image, screen="blue"):
    """Raw foreground estimate; a hint input, never final alpha.

    Parameterised by screen because the clothing plate keys green while skin and identity stay on
    blue. Hardcoded blue counts a green background as *figure*.

    screen="grey" is the flat generated-background mode (grey carrier / VITON PoC): the screen is
    estimated per row as the horizontal blend of the left/right border bands (averaged 8-column
    strips, vertically blurred) -- the figure never touches the frame borders, so those strips are
    pure background. A single corner sample or a bilinear corner blend cannot fit the non-linear
    vertical gradient generated images have (measured on a real grey carrier: a bright band near the
    bottom edge misclassified the whole margin as figure at 45.7% coverage). Any pixel differing
    from that estimate by >= 20 in at least one channel is figure. Deliberately cheap and
    deterministic -- no model -- so it drives the recolor mask and silhouette checks without a
    matting pass. Assumes the prompt guarantees suit/background contrast (the grey carrier prompt
    does: light-grey bodysuit on a background a few shades darker)."""
    image = image.convert("RGB")
    if screen == "grey":
        w, h = image.size
        bands = []
        for ch in image.split():
            left_col = ch.crop((0, 0, 8, h)).resize((1, h), Image.Resampling.BOX)
            right_col = ch.crop((w - 8, 0, w, h)).resize((1, h), Image.Resampling.BOX)
            left_col = left_col.filter(ImageFilter.GaussianBlur(3))
            right_col = right_col.filter(ImageFilter.GaussianBlur(3))
            pair = Image.new("L", (2, h))
            pair.paste(left_col, (0, 0))
            pair.paste(right_col, (1, 0))
            bands.append(pair.resize((w, h), Image.Resampling.BILINEAR))
        bg = Image.merge("RGB", bands)
        diff = ImageChops.difference(image, bg)
        r, g, b = diff.split()
        maxd = ImageChops.lighter(r, ImageChops.lighter(g, b))
        return maxd.point(lambda v: 255 if v >= 20 else 0)
    red, green, blue = image.split()
    key, others = (green, (red, blue)) if screen == "green" else (blue, (red, green))
    dominance = ImageChops.subtract(key, ImageChops.lighter(*others))
    return ImageOps.invert(dominance.point(lambda value: 255 if value >= 12 else 0))


def silhouette_box(path, screen="blue"):
    return screen_foreground(image_from(path), screen).getbbox()


def face_align(performer, performer_face, carrier_face, size, background):
    """Scale and translate the performer so her face lands on the carrier's face.

    Alignment is on the *face* box, not the head box. The carrier is bald, so its head box is a bare
    skull (147 px tall) while the performer's includes hair past her shoulders (210 px); comparing
    those shrank her to 0.700 and left her 794 px tall against the carrier's 1094, feet off the
    bottom of the frame. A face is the same object in both images.

    Returns (aligned image, scale). The caller checks the silhouette height ratio -- see
    aligned_height_check() -- because that is the quantity that was wrong, and an input-scale guard
    passed 0.700 happily."""
    scale = (carrier_face[3] - carrier_face[1]) / max(1, performer_face[3] - performer_face[1])
    scaled = performer.resize((max(1, round(performer.width * scale)),
                               max(1, round(performer.height * scale))), Image.Resampling.LANCZOS)
    face_centre = (round((performer_face[0] + performer_face[2]) / 2 * scale),
                   round((performer_face[1] + performer_face[3]) / 2 * scale))
    target = ((carrier_face[0] + carrier_face[2]) // 2, (carrier_face[1] + carrier_face[3]) // 2)
    canvas = Image.new("RGB", size, background)
    canvas.paste(scaled, (target[0] - face_centre[0], target[1] - face_centre[1]))
    return canvas, scale


def aligned_height_check(aligned_box, carrier_box, tolerance=FACE_ALIGN_TOLERANCE):
    """Both images are full-body frames of a standing woman, so after alignment their silhouettes
    must be nearly the same height. This is the check that catches a mismatched anchor feature."""
    height = max(1, carrier_box[3] - carrier_box[1])
    ratio = (aligned_box[3] - aligned_box[1]) / height
    return {"name": "aligned_silhouette_height", "passed": abs(ratio - 1) <= tolerance,
            "detail": {"ratio": round(ratio, 3), "aligned_px": aligned_box[3] - aligned_box[1],
                       "carrier_px": height, "tolerance": tolerance}}


def body_repaint_mask(performer_person, performer_head, carrier_person,
                      neck_overlap=NECK_OVERLAP, outer_margin=OUTER_MARGIN,
                      outer_feather=OUTER_FEATHER):
    """The inverted transfer's two masks: everything to repaint, and the head to preserve.

    Repaint covers the carrier's silhouette *unioned with* the performer's own. Masking only the
    carrier's would leave her shoulders and arms standing outside it, untouched, because everything
    outside the mask survives exactly -- the same property that protects the head.

    Asymmetric by necessity. The OUTER boundary is pushed into flat background and feathered:
    inside the mask the background is regenerated, outside it the original survives, and the two
    blues are not identical, so a hard edge there reads as a pale contour tracing the figure. The
    INNER boundary against the preserved head stays hard, because a feathered edge would blend
    generated pixels into the head and lose outside_mask_unchanged == 0, which is the entire
    premise of this approach."""
    preserve = dilate(performer_head, neck_overlap)
    union = ImageChops.lighter(performer_person, carrier_person)
    outer = dilate(union, outer_margin).filter(ImageFilter.GaussianBlur(outer_feather))
    return ImageChops.subtract(outer, preserve), preserve


def silhouette_outline(foreground, width=OUTLINE_WIDTH):
    """Silhouette boundary as a control image, `width` px thick.

    Not ComfyUI's Canny node, which finds almost nothing here: the carrier is matte green paint on
    a matte blue screen, and those are nearly isoluminant (luma 95 against 71), so a luminance
    gradient detector returned 795 lit pixels for a whole standing figure against 18553 for this.
    The screen key already separates them on colour, and its boundary *is* the silhouette -- the
    only geometry a uniformly painted body has to offer anyway.

    OUTLINE_WIDTH is 5 rather than 1 because the union ControlNet works on an 8x downsampled
    latent, where a 1 px line lands on well under one cell."""
    return ImageChops.subtract(dilate(foreground, width),
                               ImageOps.invert(dilate(ImageOps.invert(foreground), width)))


def region_tone(image, box, mask=None):
    """Average RGB within `box`, restricted to `mask` if given (ImageStat ignores pixels where the
    mask is 0). Used to sample a small patch of skin colour, not to characterise a whole region."""
    crop = image.crop(box)
    stat = ImageStat.Stat(crop, mask=mask.crop(box)) if mask else ImageStat.Stat(crop)
    return tuple(round(value) for value in stat.mean[:3])


def inset(box, margin):
    x0, y0, x1, y1 = box
    dx, dy = round((x1 - x0) * margin), round((y1 - y0) * margin)
    return (x0 + dx, y0 + dy, x1 - dx, y1 - dy)


# Blur radius for deterministic_skin_recolor()'s local brightness reference. Large enough to average
# out broad studio lighting falloff (measured on a real carrier: the key channel reads 162 at the
# chest and 79 at the thigh, roughly half, purely from the light falling off toward the floor) while
# staying smaller than that falloff's own scale, so local shading -- a muscle's curve, a knee's
# highlight, which vary over tens of pixels rather than hundreds -- still comes through.
SKIN_TONE_LOCAL_RADIUS = 90


def deterministic_skin_recolor(image, foreground, target_rgb, paint_channel="green",
                               radius=SKIN_TONE_LOCAL_RADIUS):
    """Recolor `foreground` pixels to `target_rgb`, tinted by the image's own *local* shading --
    `paint_channel`'s brightness at that point relative to a blurred local average, not relative to
    one global figure-wide average -- so photographed highlights, shadows and muscle definition
    survive a hue change that a diffusion regeneration cannot guarantee stays registered to the
    source. Background pixels are untouched.

    Local, not global: a single figure-wide reference carries the carrier's own broad lighting
    falloff straight through to skin tone along with the local shading, and that falloff can be
    severe -- half the key-channel brightness from chest to thigh on a real carrier -- producing
    legs that read as flat and grey next to a warm torso. A blurred local reference divides out
    exactly that broad-scale falloff (the blur mixes chest-level and thigh-level brightness apart
    from each other, so each region's own local average maps back to `target_rgb`) while leaving
    fine shading, which varies within the blur radius, intact.

    `paint_channel` names the figure's own paint colour (green, for this project's carriers) --
    deliberately not called `screen`, which screen_foreground() already uses for the *opposite*
    end of the same image, the background colour being keyed out. The carrier is matte body paint
    on a real photographed figure, so its paint channel's raw value already *is* a shading map:
    brighter paint reads as a highlight, darker a shadow, for the same reason screen_foreground()
    can key on that channel's dominance at all. An earlier construction recolored via a separate
    diffusion pass (skin.png) instead; that pass carries its own independent registration drift
    against the carrier (measured up to 7px), which a downstream clothes plate built from the
    carrier's own bit-exact pixels would inherit. This has none, because it never leaves the
    carrier's own pixel grid.

    paint_channel=None selects the luminance mode for grey carriers (no paint channel exists):
    the figure's luma is the shading map. On the real blue-screen/green-paint carrier the two modes
    differ by ~12 levels mean (p95 27) inside the figure -- the green paint's R/B noise and screen
    spill leaking into luma -- which does not exist on a grey suit, where luma *is* the shading."""
    if paint_channel is None:
        channel = image.convert("L")
    else:
        channel = image.split()[{"green": 1, "blue": 2}[paint_channel]]
    local_mean = channel.filter(ImageFilter.GaussianBlur(radius))
    ratio = ImageMath.lambda_eval(
        lambda args: ImageMath.imagemath_float(args["value"])
        / ImageMath.imagemath_max(ImageMath.imagemath_float(args["mean"]), 1),
        value=channel, mean=local_mean,
    )
    bands = [
        ImageMath.lambda_eval(
            lambda args, t=target: ImageMath.imagemath_min(ImageMath.imagemath_max(args["r"] * t, 0), 255),
            r=ratio,
        ).convert("L")
        for target in target_rgb
    ]
    recolored = Image.merge("RGB", bands)
    return Image.composite(recolored, image, foreground)


def head_transplant(head_source, body, head_mask, feather=HEAD_BLEND_FEATHER):
    """Paste `head_source`'s pixels onto `body` wherever `head_mask` is set, blended over a thin band
    at the mask's own boundary so the join isn't a razor seam.

    Blurring the mask directly, rather than a separately-feathered outer ring the way
    body_repaint_mask() protects its *outer* boundary, is enough here: this mask has no second
    boundary of its own to keep hard, and blurring leaves its interior at full opacity while only
    softening near the edge -- which is exactly where a real head meets a real body anyway."""
    alpha = head_mask.filter(ImageFilter.GaussianBlur(feather))
    return Image.composite(head_source, body, alpha)


def chroma_spread(image, mask):
    """Saturation mean and spread (percent) over `mask` -- the quantitative stand-in for "does this
    read as real skin".

    Spread is the number that matters, and it is the one nothing measured before 2026-08-08.
    deterministic_skin_recolor() computes `target_rgb * ratio`, so every output pixel is a scalar
    multiple of one RGB vector: all pixels lie on a single line through the origin and saturation is
    constant *by construction*. Measured on a real plate, torso saturation spread was 2.0 against
    real skin's 9.9 -- while the *mean* was already correct (hue 28.6 against 28.4). Tone was never
    the problem; variance was, and only a spread metric can see the difference."""
    red, green, blue = image.convert("RGB").split()
    high = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    low = ImageChops.darker(ImageChops.darker(red, green), blue)
    saturation = ImageMath.lambda_eval(
        lambda args: 255.0 * ImageMath.imagemath_float(args["d"])
        / ImageMath.imagemath_max(ImageMath.imagemath_float(args["h"]), 1.0),
        d=ImageChops.subtract(high, low), h=high).convert("L")
    stat = ImageStat.Stat(saturation, mask)
    return {"saturation_mean": round(stat.mean[0] * 100 / 255, 2),
            "saturation_spread": round(stat.stddev[0] * 100 / 255, 2)}


def rebackground(image, alpha, from_rgb, to_rgb):
    """Swap one known flat backdrop for another, correcting the blend band and not just the solid
    background behind the figure.

    A boundary pixel is `C = a*F + (1-a)*B_from`, so the pixel that would have been produced against
    a different backdrop is `C + (1-a)*(B_to - B_from)`. Written that way it never needs the unmixed
    foreground `F`, which is what makes it stable: recovering `F` means dividing by `a`, and that
    amplifies noise worst exactly at the wispy hair edge this exists to fix. Where `a` is 255 the
    correction is zero, so solid figure pixels are untouched by construction.

    Why it exists: head_transplant() composites through a *geometric* mask, so whatever backdrop
    surrounds the performer's hair inside that mask is pasted onto the plate along with her. Measured
    on a real run, 17.6% of the transplant mask (6,406 px of 36,377) is her backdrop. That is
    invisible only while her backdrop and the carrier's are the same colour. Running the preprocess
    stage on grey -- which is what removes the blue contamination from her hair edge at source --
    makes it a grey halo instead. Correcting the backdrop first makes the two independent.

    Requires the whole frame to share one `from_rgb`, so an aligned canvas must be padded with the
    performer's own backdrop rather than the carrier's; padding with the destination colour would be
    corrected as though it were her backdrop and shifted by the full delta."""
    bands = []
    for index, (source, target) in enumerate(zip(from_rgb, to_rgb)):
        bands.append(ImageMath.lambda_eval(
            lambda args, delta=float(target) - float(source): ImageMath.imagemath_min(
                ImageMath.imagemath_max(
                    ImageMath.imagemath_float(args["c"])
                    + (255.0 - ImageMath.imagemath_float(args["a"])) * delta / 255.0, 0), 255),
            c=image.convert("RGB").split()[index], a=alpha.convert("L")).convert("L"))
    return Image.merge("RGB", bands)


def blend_zone(head_mask, feather=HEAD_BLEND_FEATHER, margin=SEAM_EDIT_MARGIN):
    """The band head_transplant() actually blended for this exact head_mask -- where its alpha
    (the same Gaussian blur, recomputed identically) is strictly between 0 and 255 -- dilated by
    `margin` so a follow-up edit has some working room around the seam itself, not just the seam's
    own thin line.

    A masked edit here is far lower-risk than the abandoned body-repaint construction's mask: this
    one is a narrow band with bit-exact, fully-formed content on both sides (a real head above, a
    real body below in the same reference image) for the model to reconcile, not most of a body to
    invent from a silhouette outline."""
    alpha = head_mask.filter(ImageFilter.GaussianBlur(feather))
    band = alpha.point(lambda value: 255 if 0 < value < 255 else 0)
    return dilate(band, margin)


def silhouette_spread(boxes, translation_limit=SPREAD_TRANSLATION_PX, scale_limit=SPREAD_SCALE):
    """Do several performers on the same pose land on the same silhouette?

    This is the cost the inverted transfer has to justify. The carrier exists to freeze pose across
    20 performers x 4 poses; here the pose reaches the sampler through a mask and a control image
    rather than through the image being edited, so consistency is a measured property rather than a
    structural guarantee.

    `boxes` maps a label to a silhouette bbox. The top edge is excluded deliberately and for the
    same reason it is excluded from the per-image drift check: it is hair, which legitimately
    differs between performers. Spread is peak-to-peak, not standard deviation -- with three or four
    samples the outlier *is* the result."""
    if len(boxes) < 2:
        return {"name": "silhouette_spread", "passed": True, "detail": "fewer than two performers"}
    edges = {name: [box[0], box[2], box[3]] for name, box in boxes.items()}
    spread = [max(values) - min(values) for values in zip(*edges.values())]
    # Width only. Height is measured from the top edge, which is hair, so it varies by tens of
    # pixels between performers for reasons that have nothing to do with pose -- an early version
    # of this check read 4% scale spread off three silhouettes that agreed to within 2 px.
    widths = [box[2] - box[0] for box in boxes.values()]
    scale = (max(widths) - min(widths)) / max(1, statistics.mean(widths))
    worst = max(spread)
    return {
        "name": "silhouette_spread",
        "passed": worst <= translation_limit and scale <= scale_limit,
        "detail": {"left_right_bottom_px": spread, "worst_px": worst,
                   "scale_spread": round(scale, 5), "boxes": {k: list(v) for k, v in boxes.items()},
                   "limits": {"translation_px": translation_limit, "scale": scale_limit}},
    }


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
    # Fractions of the canvas, not pixels. These were written as 1024-square coordinates and could
    # only score a 1024x1024 asset -- everything else returned "canvas mismatch", including the
    # 832x1248 the pipeline actually runs at. They are coarse patches for a difference score, not
    # anatomical landmarks, so a full-body figure filling either frame puts them in the same place.
    boxes = {"torso": (0.293, 0.410, 0.707, 0.674), "hands": (0.088, 0.420, 0.912, 0.752),
             "crotch": (0.342, 0.586, 0.658, 0.771), "feet": (0.244, 0.801, 0.756, 0.996)}
    boxes = {name: (round(box[0] * CANVAS[0]), round(box[1] * CANVAS[1]),
                    round(box[2] * CANVAS[0]), round(box[3] * CANVAS[1]))
             for name, box in boxes.items()}
    try:
        with Image.open(reference) as opened:
            ref = opened.convert("RGB")
        with Image.open(candidate) as opened:
            current = opened.convert("RGB")
    except (OSError, ValueError) as exc:
        return {"accepted": False, "reason": str(exc)}
    if ref.size != CANVAS or current.size != CANVAS:
        return {"accepted": False, "reason": f"canvas mismatch: wanted {CANVAS}, "
                                             f"reference {ref.size}, candidate {current.size}"}
    best = None
    for scale in (0.995, 0.9975, 1.0, 1.0025, 1.005):
        size = (round(CANVAS[0] * scale), round(CANVAS[1] * scale))
        scaled = current.resize(size, Image.Resampling.BICUBIC)
        canvas = Image.new("RGB", CANVAS)
        canvas.paste(scaled, ((CANVAS[0] - size[0]) // 2, (CANVAS[1] - size[1]) // 2))
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                scores = []
                for box in boxes.values():
                    shifted = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
                    if min(shifted) < 0 or shifted[2] > CANVAS[0] or shifted[3] > CANVAS[1]:
                        continue
                    scores.append(sum(ImageStat.Stat(ImageChops.difference(ref.crop(box), canvas.crop(shifted))).mean))
                score = sum(scores) if scores else 10 ** 9
                if best is None or score < best[0]:
                    best = (score, scale, dx, dy)
    _, scale, dx, dy = best
    # The search only covers +/-2 px and +/-0.5%, so `abs(dx) <= 2` was tautological: it tested the
    # loop bounds, not the images. A candidate shifted 8 px reported "accepted, translation [2, 2]"
    # -- the best offset available, sitting hard against the edge of the window, with the true
    # optimum somewhere outside it. An interior minimum is the evidence that the window contained
    # the answer; a boundary one is evidence that it did not.
    interior = abs(dx) < 2 and abs(dy) < 2 and 0.995 < scale < 1.005
    return {
        "accepted": interior,
        "translation_px": [dx, dy], "scale_change": round(scale - 1, 5),
        "limits": {"translation_px": 2, "scale_change": 0.005},
        "reason": None if interior else "best alignment sits on the search boundary; the true "
                                        "offset is outside +/-2 px or +/-0.5%",
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


def flux_generation_graph(prompt, seed, prefix, size=(1328, 1328), canonical=True,
                          guidance=3.5, steps=28):
    """Carrier generation on FLUX.1 dev instead of Qwen Image 2512.

    Why this exists: the carrier's own look survives everything downstream. Its luminance is why the
    Lab chroma transfer failed, and the odd knee creases persist through every variant including the
    diffused skin stage -- they are generated, not introduced later. Qwen-Image-Edit is characterised
    as leaning "illustration-adjacent" in photorealism where FLUX preserves photographic fidelity, so
    the plastic look this pipeline keeps fighting downstream may simply be the base model. One
    carrier from each, side by side, settles it.

    Three deliberate differences from generation_graph(), all forced by the architecture rather than
    chosen: FLUX dev is guidance-distilled, so CFG is 1.0 and steering comes from FluxGuidance;
    negative conditioning does nothing at CFG 1.0 (this pipeline's NEGATIVE is empty anyway); and it
    converges in far fewer steps than Qwen's 50.

    The all-in-one fp8 checkpoint is used so CheckpointLoaderSimple supplies model, CLIP and VAE
    together -- one file for pod_bootstrap.sh to fetch instead of a UNet, two text encoders and a VAE
    that must all agree."""
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": FLUX_CARRIER_MODEL}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["2", 0], "guidance": guidance}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
        "5": {"class_type": "EmptySD3LatentImage", "inputs": {"width": size[0], "height": size[1], "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler",
            "scheduler": "simple", "positive": ["3", 0], "negative": ["4", 0],
            "latent_image": ["5", 0], "denoise": 1.0}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": f"{prefix}-raw"}},
    }
    if canonical:
        graph.update({
            "9": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["7", 0]}},
            "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": f"{prefix}-canonical"}},
        })
    return graph


def flux2_generation_graph(prompt, seed, prefix, size=(1328, 1328), canonical=True,
                           guidance=4.0, steps=28, clip_type=FLUX2_CLIP_TYPE):
    """Carrier generation on FLUX.2 dev.

    Worth trying above FLUX.1 for a reason specific to this pipeline rather than for image quality:
    FLUX.2 supports multiple reference images natively. LAYERED_COSTUME_PRODUCTION_STATUS.md records,
    over seven generations, that a mask-free Qwen edit "does not blend two references -- it returns
    one, and the prompt decides which". Both the identity transfer and the garment-ref clothes edit
    are built *around* that constraint, and it is why every stage here is a masked edit. A model that
    genuinely accepts several references could simplify the construction rather than work around it.

    Split into three loaders because the Comfy-Org repack ships three files and the text encoder is
    Mistral 3 Small, not T5+CLIP. `clip_type` is passed in because preflight reads the real enum from
    /object_info -- support arrived in some ComfyUI version and this pod may predate it, so the type
    string is verified rather than assumed."""
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": FLUX2_MODEL, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": FLUX2_TEXT_ENCODER, "type": clip_type, "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": FLUX2_VAE}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["4", 0], "guidance": guidance}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": size[0], "height": size[1], "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler",
            "scheduler": "simple", "positive": ["5", 0], "negative": ["6", 0],
            "latent_image": ["7", 0], "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": f"{prefix}-raw"}},
    }
    if canonical:
        graph.update({
            "11": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["9", 0]}},
            "12": {"class_type": "SaveImage", "inputs": {"images": ["11", 0], "filename_prefix": f"{prefix}-canonical"}},
        })
    return graph


def carrier_graph(family, prompt, seed, prefix, size=(1328, 1328), canonical=True,
                  negative_prompt=NEGATIVE, clip_type=FLUX2_CLIP_TYPE):
    """Pick the carrier generator. Same call shape either way so the carrier stage does not branch."""
    if family == "flux2":
        return flux2_generation_graph(prompt, seed, prefix, size, canonical, clip_type=clip_type)
    if family in ("flux", "flux1"):
        return flux_generation_graph(prompt, seed, prefix, size, canonical)
    return generation_graph(prompt, seed, prefix, size, canonical, negative_prompt)


def edit_graph(image1, prompt, seed, prefix, image2=None, mask=None,
               control=None, control_type="canny", control_strength=1.0, denoise=1.0):
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

    `denoise` defaults to 1.0, which is what every stage before 2026-08-08 used and what the two
    generative edits need: the clothes stage paints a garment that is not in the source at all, and
    the identity stage regenerates inside its mask entirely.

    Below 1.0 the masked region is *harmonised* rather than regenerated -- the regime a skin-tone
    blend wants, where the composite already states the target colour in pixels and the model only
    has to supply the chromatic variation a deterministic recolor cannot (measured: saturation
    spread 2.0 against real skin's 9.9, with the mean already correct at hue 28.6 vs 28.4). At 1.0
    a body-shaped mask reproduces the abandoned repaint construction, whose body proportions varied
    by seed; see LAYERED_COSTUME_PRODUCTION_STATUS.md, 2026-08-05.
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
        "13": {"class_type": "KSampler", "inputs": {"model": ["11", 0], "seed": seed, "steps": 40, "cfg": 4.0, "sampler_name": "euler", "scheduler": "simple", "positive": ["10", 0], "negative": ["9", 0], "latent_image": ["12", 0], "denoise": denoise}},
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


def flux2_edit_graph(image1, prompt, seed, prefix, image2=None, mask=None,
                     control=None, control_type="canny", control_strength=1.0, denoise=1.0,
                     clip_type=FLUX2_CLIP_TYPE, guidance=4.0, steps=28):
    """edit_graph()'s shape, on FLUX.2 Klein, so a single stage can be swapped without touching the
    rest of the pipeline.

    The reason to try it is not image quality. LAYERED_COSTUME_PRODUCTION_STATUS.md establishes over
    seven generations that a Qwen edit "does not blend two references -- it returns one, and the
    prompt decides which"; every stage here is a masked edit *because* of that. FLUX.2 takes several
    references natively through chained ReferenceLatent nodes, so the construction could be
    simplified rather than worked around.

    What matters for safety is that the two nodes this pipeline's guarantees rest on are
    model-agnostic and used identically here: SetLatentNoiseMask keeps the latent outside the mask,
    and ImageCompositeMasked pastes the decode back over the original so everything outside the mask
    is bit-exact. That is where outside_mask_unchanged: 0 and the 0/0/0 registration come from, and
    it survives the model swap -- which is the whole reason a per-stage swap is safe to attempt.

    Differences forced by the architecture, not chosen: CFG is 1.0 because FLUX.2 is
    guidance-distilled and steering comes from FluxGuidance, so the negative conditioning is inert
    (this pipeline's NEGATIVE is empty anyway); references arrive as encoded latents rather than as
    image1/image2 arguments to a text encoder; and the text encoder is Qwen 3 8B for Klein against
    Mistral 3 Small for dev, which is why `clip_type` is passed in from preflight's probe of the
    pod's own CLIPLoader enum instead of being hardcoded."""
    nodes = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": FLUX2_KLEIN_MODEL, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": FLUX2_KLEIN_TEXT_ENCODER, "type": clip_type, "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": FLUX2_VAE}},
        "4": {"class_type": "LoadImage", "inputs": {"image": image1}},
        "5": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["4", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
        "8": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["3", 0]}},
        # Reference chain: image 1 always, image 2 appended below. This is the node FLUX.2 provides
        # in place of Qwen's image1/image2 text-encoder arguments.
        "9": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["6", 0], "latent": ["8", 0]}},
        "10": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["9", 0], "guidance": guidance}},
        "11": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler",
            "scheduler": "simple", "positive": ["10", 0], "negative": ["7", 0],
            "latent_image": ["8", 0], "denoise": denoise}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": f"{prefix}-raw"}},
    }
    if image2:
        nodes.update({
            "14": {"class_type": "LoadImage", "inputs": {"image": image2}},
            "15": {"class_type": "VAEEncode", "inputs": {"pixels": ["14", 0], "vae": ["3", 0]}},
            "16": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["9", 0], "latent": ["15", 0]}},
        })
        nodes["10"]["inputs"]["conditioning"] = ["16", 0]
    if mask:
        nodes.update({
            "17": {"class_type": "LoadImage", "inputs": {"image": mask}},
            "18": {"class_type": "ImageToMask", "inputs": {"image": ["17", 0], "channel": "red"}},
            "19": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["8", 0], "mask": ["18", 0]}},
            "20": {"class_type": "ImageCompositeMasked", "inputs": {"destination": ["5", 0], "source": ["12", 0], "x": 0, "y": 0, "resize_source": False, "mask": ["18", 0]}},
            "21": {"class_type": "SaveImage", "inputs": {"images": ["20", 0], "filename_prefix": f"{prefix}-masked"}},
        })
        nodes["11"]["inputs"]["latent_image"] = ["19", 0]
    if control:
        nodes.update({
            "22": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": CONTROL_NET}},
            "23": {"class_type": "SetUnionControlNetType",
                   "inputs": {"control_net": ["22", 0], "type": CONTROL_TYPES[control_type]}},
            "24": {"class_type": "LoadImage", "inputs": {"image": control}},
            "25": {"class_type": "ControlNetApplyAdvanced",
                   "inputs": {"positive": ["10", 0], "negative": ["7", 0], "control_net": ["23", 0],
                              "image": ["24", 0], "strength": control_strength,
                              "start_percent": 0.0, "end_percent": 1.0, "vae": ["3", 0]}},
        })
        nodes["11"]["inputs"]["positive"] = ["25", 0]
        nodes["11"]["inputs"]["negative"] = ["25", 1]
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


# How far past a hole's own edges spill_mask()'s morphological closing reaches, in the same
# dilate()/erode() units as elsewhere in this file (size//2 actual pixels). Large enough to bridge
# the waist-crease-sized defect (measured ~3px wide) with margin, small enough that it cannot bridge
# the gap between genuinely separate parts of the figure.
SPILL_HOLE_CLOSE_RADIUS = 24


def spill_mask(image, screen, radius=SPILL_HOLE_CLOSE_RADIUS):
    if screen == "grey":
        # No chroma screen means no spill; there is nothing to close. Callers (segment_source,
        # scoped_despill) must be able to pass "grey" for the flat-background mode.
        return Image.new("L", image.size, 0)
    """Small gaps *enclosed* within the confident-foreground shape -- a hole like a shadowed waist
    crease, surrounded on all sides by solid fabric -- found by morphological closing (dilate then
    erode by the same amount) rather than by raw dominance strength.

    An earlier version tested every pixel's dominance independently of context: weak enough, and
    below a fixed ceiling, meant "despill it." That worked cleanly at the waist and feet (measured
    dominance 39-67 there against 79-97 for confident background, no overlap) but not generally: a
    genuine soft edge near hair measured the same weak dominance (69) as the real defects, because
    ordinary antialiasing at any true boundary reads as weak dominance too, spill or not. Confirmed
    on a real run: with the dominance-ceiling hint, CorridorKey assigned high alpha (213-246) to
    blue-screen pixels near her hairline that a hint built without any despill had correctly left
    transparent (alpha 0) the run before -- a regression the ceiling approach introduced by
    "fixing" a boundary that was never broken.

    Morphological closing only finds *enclosed* gaps, not ordinary edges, so it does not have this
    problem by construction: a hair boundary is open to the background, not a hole surrounded by
    fabric. Verified on real data -- closes the waist gap, leaves the hair region and all tested far
    background untouched. Does not close the feet/shoe defect, which is a much wider (30px+) notch
    open to the outside rather than an enclosed hole; that remains unsolved. See
    LAYERED_COSTUME_PRODUCTION_STATUS.md, 2026-08-06."""
    fg = screen_foreground(image, screen)
    closed = erode(dilate(fg, radius), radius)
    return ImageChops.subtract(closed, fg)


def despill_source(image, screen):
    if screen == "grey":
        return image.convert("RGB")
    channels = list(image.convert("RGB").split())
    index = 1 if screen == "green" else 2
    other = ImageChops.lighter(*(channel for number, channel in enumerate(channels) if number != index))
    threshold = other.point(lambda value: min(255, round(value * 1.10)))
    reject = ImageChops.subtract(channels[index], threshold).point(lambda value: 255 if value else 0)
    channels[index] = Image.composite(other, channels[index], reject)
    return Image.merge("RGB", channels)


def scoped_despill(image, screen, radius=SPILL_HOLE_CLOSE_RADIUS):
    """despill_source(), but only within spill_mask()'s enclosed holes, leaving ordinary boundaries
    and all background -- confidently screen-coloured or not -- at their original colour."""
    mask = spill_mask(image, screen, radius)
    return Image.composite(despill_source(image, screen), image, mask)


# How far in from a matte's own boundary edge_despill() reaches. The screen-coloured band a learned
# matte keeps is a few pixels wide (measured 3-4 px on BiRefNet output); 4 covers it without
# reaching into skin or fabric that happens to lean toward the key colour.
MATTE_EDGE_WIDTH = 4
# Fraction of a layer's edge band that may be screen-dominant before the layer is called fringed.
# Measured on real plates: CorridorKey 6.4-9.3%, BiRefNet 21.8-40.2%, either one after edge_despill
# 0.0%. 15% sits in the empty gap between "a keyer's residue" and "a visible coloured halo".
FRINGE_LIMIT = 0.15


def screen_dominant(image, screen, margin=18):
    """Pixels whose key channel beats both others by `margin` -- i.e. that still read as screen."""
    red, green, blue = image.convert("RGB").split()
    key = green if screen == "green" else blue
    others = ImageChops.lighter(red, blue) if screen == "green" else ImageChops.lighter(red, green)
    return ImageChops.subtract(key, others).point(lambda value: 255 if value > margin else 0)


def matte_edge(alpha, width=MATTE_EDGE_WIDTH):
    """The band just inside a matte's boundary, plus every partially-transparent pixel.

    This is exactly where a learned matting model keeps screen-coloured pixels: it segments by
    *shape* and has no notion of chroma, so spill at the boundary reads as subject. A chroma keyer
    never had this problem -- screen colour means transparent by definition -- which is why despill
    was unnecessary while CorridorKey produced the alpha and became necessary the moment BiRefNet
    did. Measured on one plate: BiRefNet's matte is a strict superset of CorridorKey's (1 px goes
    the other way) and the 6,632 px it adds include 2,663 screen-dominant ones CorridorKey excluded
    outright. See LAYERED_COSTUME_PRODUCTION_STATUS.md, 2026-08-08."""
    solid = alpha.point(lambda value: 255 if value > 8 else 0)
    rim = ImageChops.subtract(solid, erode(solid, 2 * width))
    partial = alpha.point(lambda value: 255 if 8 < value < 248 else 0)
    return ImageChops.lighter(rim, partial)


def edge_despill(image, alpha, screen, width=MATTE_EDGE_WIDTH):
    """despill_source() over matte_edge() only, so matte interiors keep their original colour.

    Deliberately not the whole plate: an unconditional despill also rewrites pixels whose alpha was
    already decided correctly, which is what segment_source() used to do and why CorridorKey's own
    despilled output looked worse after reconstruction. A grey screen has no hue to pull, so this
    is a no-op there."""
    if screen == "grey":
        return image
    return Image.composite(despill_source(image, screen), image, matte_edge(alpha, width))


def chroma_gate(alpha, image, screen):
    """Hybrid matte: a learned model's shape, a chroma keyer's edge precision.

    Zeroes alpha wherever the source pixel is still screen-dominant. The two extractors fail in
    opposite directions -- BiRefNet leaves holes-free shapes with a contaminated rim, CorridorKey
    leaves a clean rim with holes -- so intersecting them beats either alone. Measured on v3's
    clothes plate: interior holes 3,730 against CorridorKey's 4,426, rim spill 0.0% against
    CorridorKey's 8.9% and BiRefNet's 60.8%."""
    if screen == "grey":
        return alpha
    return ImageChops.multiply(alpha, screen_foreground(image, screen))


def fringe_fraction(rgba, screen, width=3):
    """Share of a composited layer's edge band that still reads as screen colour. The cheap visual
    proxy for "is there a coloured halo", which no mechanical check caught before 2026-08-08."""
    if screen == "grey":
        return 0.0
    alpha = rgba.getchannel("A")
    solid = alpha.point(lambda value: 255 if value > 8 else 0)
    rim = ImageChops.subtract(solid, erode(solid, 2 * width))
    total = sum(rim.histogram()[1:])
    if not total:
        return 0.0
    return sum(ImageChops.multiply(rim, screen_dominant(rgba, screen)).histogram()[1:]) / total


def segment_source(source, alpha, screen):
    with Image.open(source) as opened:
        image = scoped_despill(opened.convert("RGB"), screen)
    image = edge_despill(image, alpha, screen).convert("RGBA")
    image.putalpha(alpha)
    return image


def split_head_layers(source, alpha, hair_hint, screen, skin_bound=None):
    """Keep the extractor's alpha; SAM only decides clothing order.

    `skin_bound` limits the *skin* layer to an anatomical region — the face/neck/upper-chest plate
    LAYERED_COSTUME_PIPELINE_REFERENCE.md specifies, which also states plainly that "no full-body
    performer skin plate is produced". Without it this returns the whole figure minus hair, i.e. a
    nude body that the garment layer is then solely responsible for covering: that is what produced
    the waist slivers and the toes showing beside the shoes, and it ships a nude torso as an asset.

    The bound must be a *fixed anatomical region*, never derived from a garment's alpha. One skin
    plate is reused across every outfit, so a garment-derived crop would have to be regenerated per
    outfit and the layering would stop paying for itself. Exposed skin further down the body belongs
    to the clothed-body plate instead, which the catalog already arranges by generating it once per
    `tone_groups` entry with "Exposed natural skin stays {tone}" in the outfit prompt.

    Hair is deliberately *not* bounded: it falls past the shoulders and clipping it to the same
    region would cut it off."""
    hair = hair_hint.convert("L").point(lambda value: 255 if value > 127 else 0)
    skin_alpha = ImageChops.multiply(alpha, ImageOps.invert(hair))
    if skin_bound is not None:
        skin_alpha = ImageChops.multiply(
            skin_alpha, skin_bound.convert("L").point(lambda value: 255 if value > 127 else 0))
    return (
        segment_source(source, skin_alpha, screen),
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
        # And it must actually reject: the search is +/-2 px, so an 8 px shift is out of reach.
        shifted = Image.new("RGB", CANVAS, "black")
        ImageDraw.Draw(shifted).rectangle((308, 428, 732, 908), fill="white")
        third = root / "third.png"
        shifted.save(third)
        assert not registration(first, third)["accepted"], registration(first, third)
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
        # spill_mask()/scoped_despill(): an enclosed weak-dominance hole surrounded by solid figure
        # must be found and corrected; background must not be touched even immediately beside the
        # figure's true edge -- the property a dominance-only test got wrong (see spill_mask()'s
        # docstring) and morphological closing gets right by construction, since a hole has to be
        # enclosed and an ordinary edge is not.
        spill_plate = Image.new("RGB", (100, 100), (0, 200, 0))
        spill_draw = ImageDraw.Draw(spill_plate)
        spill_draw.rectangle((20, 20, 79, 79), fill=(30, 25, 28))
        spill_draw.rectangle((45, 45, 54, 54), fill=(20, 80, 50))
        mask = spill_mask(spill_plate, "green", radius=24)
        assert mask.getpixel((49, 49)) == 255, "an enclosed weak-dominance hole must be found"
        assert mask.getpixel((10, 10)) == 0, "background far from the figure must not be flagged"
        assert mask.getpixel((15, 50)) == 0, "background right beside the figure's edge must not be flagged"
        despilled = scoped_despill(spill_plate, "green", radius=24)
        assert despilled.getpixel((49, 49)) == (20, 50, 50), "the hole's colour must be corrected"
        assert despilled.getpixel((10, 10)) == (0, 200, 0), "background must keep its original colour"
        # matte_edge()/edge_despill(): the band inside a matte's boundary is where a learned matte
        # keeps screen-coloured pixels, so despill must reach it and must leave the interior alone.
        edge_plate = Image.new("RGB", (60, 60), (0, 200, 0))
        edge_draw = ImageDraw.Draw(edge_plate)
        edge_draw.rectangle((20, 20, 39, 39), fill=(40, 30, 35))
        edge_draw.rectangle((20, 20, 39, 21), fill=(20, 120, 40))  # spill along the top edge
        edge_alpha = Image.new("L", (60, 60))
        ImageDraw.Draw(edge_alpha).rectangle((20, 20, 39, 39), fill=255)
        band = matte_edge(edge_alpha, width=3)
        assert band.getpixel((30, 21)) == 255, "the rim just inside the matte must be in the band"
        assert band.getpixel((30, 30)) == 0, "the matte interior must not be"
        assert band.getpixel((5, 5)) == 0, "pixels outside the matte must not be"
        fixed = edge_despill(edge_plate, edge_alpha, "green", width=3)
        assert fixed.getpixel((30, 21))[1] <= 40, "spill on the rim must be pulled down"
        assert fixed.getpixel((30, 30)) == (40, 30, 35), "the interior must keep its colour"
        assert edge_despill(edge_plate, edge_alpha, "grey", width=3).getpixel((30, 21)) == (20, 120, 40), \
            "a grey screen has no hue to pull, so despill must be a no-op"
        # A partially transparent pixel is a subject/screen blend by definition, so it belongs to
        # the band wherever it sits -- including deep inside the matte.
        soft = edge_alpha.copy()
        ImageDraw.Draw(soft).rectangle((29, 29, 30, 30), fill=128)
        assert matte_edge(soft, width=3).getpixel((30, 30)) == 255
        # chroma_gate(): a learned matte's shape, a keyer's refusal to call screen colour subject.
        opaque = Image.new("L", (4, 4), 255)
        assert chroma_gate(opaque, Image.new("RGB", (4, 4), (0, 200, 0)), "green").getpixel((0, 0)) == 0
        assert chroma_gate(opaque, Image.new("RGB", (4, 4), (200, 40, 40)), "green").getpixel((0, 0)) == 255
        assert chroma_gate(opaque, Image.new("RGB", (4, 4), (0, 200, 0)), "grey").getpixel((0, 0)) == 255
        # fringe_fraction(): a haloed layer must score far above the limit, a clean one at zero.
        haloed = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
        ImageDraw.Draw(haloed).rectangle((20, 20, 39, 39), fill=(20, 200, 60, 255))
        ImageDraw.Draw(haloed).rectangle((24, 24, 35, 35), fill=(180, 60, 60, 255))
        assert fringe_fraction(haloed, "green") > FRINGE_LIMIT, "a screen-coloured rim must fail"
        clean = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
        ImageDraw.Draw(clean).rectangle((20, 20, 39, 39), fill=(180, 60, 60, 255))
        assert fringe_fraction(clean, "green") == 0.0, "a clean rim must score zero"
        assert fringe_fraction(haloed, "grey") == 0.0, "grey has no fringe to measure"
        # rebackground(): solid figure untouched, solid backdrop fully replaced, and -- the point --
        # a half-covered boundary pixel corrected by exactly half the backdrop delta.
        grey_bg, blue_bg = (128, 128, 128), (30, 60, 200)
        blend = Image.new("RGB", (3, 1))
        blend.putpixel((0, 0), (200, 150, 120))                 # solid figure, alpha 255
        blend.putpixel((1, 0), (164, 139, 130))                 # half figure / half grey, alpha 128
        blend.putpixel((2, 0), grey_bg)                         # solid backdrop, alpha 0
        blend_alpha = Image.new("L", (3, 1))
        blend_alpha.putpixel((0, 0), 255)
        blend_alpha.putpixel((1, 0), 128)
        blend_alpha.putpixel((2, 0), 0)
        swapped = rebackground(blend, blend_alpha, grey_bg, blue_bg)
        assert swapped.getpixel((0, 0)) == (200, 150, 120), "an opaque pixel must not move"
        assert swapped.getpixel((2, 0)) == blue_bg, "a fully transparent pixel becomes the new backdrop"
        half = swapped.getpixel((1, 0))
        blended = (164, 139, 130)
        for channel, (was, target) in enumerate(zip(grey_bg, blue_bg)):
            expected = blended[channel] + (255 - 128) * (target - was) / 255.0
            assert abs(half[channel] - expected) <= 1, \
                f"blend band channel {channel}: {half[channel]} vs {expected:.1f}"
        assert rebackground(blend, blend_alpha, grey_bg, grey_bg).getpixel((1, 0)) == (164, 139, 130), \
            "swapping a backdrop for itself must be a no-op"
        # split_head_layers(): the skin plate is an anatomical crop, the hair plate is not. A figure
        # running the height of the frame, hair at the top, and a bound covering only its upper part.
        body_image = Image.new("RGB", (60, 200), (0, 0, 255))
        ImageDraw.Draw(body_image).rectangle((20, 10, 39, 199), fill=(200, 150, 120))
        body = root / "split-body.png"          # segment_source() reads a path, not an Image
        save_png(body_image, body)
        body_alpha = Image.new("L", (60, 200))
        ImageDraw.Draw(body_alpha).rectangle((20, 10, 39, 199), fill=255)
        hair_hint = Image.new("L", (60, 200))
        ImageDraw.Draw(hair_hint).rectangle((20, 10, 39, 39), fill=255)
        bound = Image.new("L", (60, 200))
        ImageDraw.Draw(bound).rectangle((0, 0, 59, 79), fill=255)          # head/neck/upper chest
        skin_layer, hair_layer = split_head_layers(body, body_alpha, hair_hint, "blue", bound)
        assert skin_layer.getchannel("A").getpixel((30, 60)) == 255, "skin inside the bound is kept"
        assert skin_layer.getchannel("A").getpixel((30, 150)) == 0, \
            "no skin below the bound -- that is the nude torso this exists to stop shipping"
        assert skin_layer.getchannel("A").getpixel((30, 20)) == 0, "hair is not part of the skin plate"
        assert hair_layer.getchannel("A").getpixel((30, 20)) == 255, "hair is kept"
        # Long hair past the shoulders must survive: bounding it would crop it at the chest.
        long_hair = hair_hint.copy()
        ImageDraw.Draw(long_hair).rectangle((20, 10, 39, 119), fill=255)
        _, long_layer = split_head_layers(body, body_alpha, long_hair, "blue", bound)
        assert long_layer.getchannel("A").getpixel((30, 110)) == 255, \
            "hair below the bound must not be clipped"
        unbounded, _ = split_head_layers(body, body_alpha, hair_hint, "blue")
        assert unbounded.getchannel("A").getpixel((30, 150)) == 255, \
            "without a bound the old whole-body behaviour is unchanged"
        # chroma_spread(): must separate a one-dimensional recolor from real chromatic variation.
        # The flat case is exactly what deterministic_skin_recolor() produces -- one hue scaled by
        # brightness -- so a metric that cannot tell these two apart cannot gate skin naturalness.
        everywhere = Image.new("L", (40, 40), 255)
        flat = Image.new("RGB", (40, 40))
        varied = Image.new("RGB", (40, 40))
        for y in range(40):
            for x in range(40):
                scale = 0.4 + 0.6 * (x / 39)          # same hue, brightness ramp -> constant saturation
                flat.putpixel((x, y), tuple(round(c * scale) for c in (200, 150, 100)))
                # Blue swept 20..190 against a fixed red, so saturation itself ranges 0.90 down to
                # 0.25 -- the kind of spread real skin has and a scalar recolor cannot produce.
                varied.putpixel((x, y), (200, 150, 20 + round(170 * x / 39)))
        assert chroma_spread(flat, everywhere)["saturation_spread"] < 1.0, \
            "a scalar-multiple recolor has no saturation variation to find"
        assert chroma_spread(varied, everywhere)["saturation_spread"] > 5.0, \
            "genuine chromatic variation must register"
        assert 40 < chroma_spread(flat, everywhere)["saturation_mean"] < 60, \
            "(200,150,100) is 50% saturated at every brightness"
    # Inverted identity transfer geometry.
    assert screen_foreground(Image.new("RGB", (4, 4), (0, 0, 255))).getbbox() is None
    assert screen_foreground(Image.new("RGB", (4, 4), (0, 255, 0))).getbbox() == (0, 0, 4, 4)
    # A performer whose face is half the carrier's must scale 2x, and land centred on it.
    performer = Image.new("RGB", (100, 200), "black")
    ImageDraw.Draw(performer).rectangle((40, 20, 60, 40), fill="white")
    aligned, scale = face_align(performer, (40, 20, 60, 40), (150, 100, 190, 140), (300, 400), "black")
    assert abs(scale - 2.0) < 1e-9, scale
    face = aligned.convert("L").point(lambda value: 255 if value > 127 else 0).getbbox()
    assert abs((face[0] + face[2]) // 2 - 170) <= 1 and abs((face[1] + face[3]) // 2 - 120) <= 1, face
    assert aligned_height_check((0, 0, 10, 100), (0, 0, 10, 100))["passed"]
    assert not aligned_height_check((0, 0, 10, 70), (0, 0, 10, 100))["passed"]
    # Cross-performer pose gate. Top edges differ by 40 px (hair) and must be ignored; a 3 px
    # bottom spread must fail, because the limit is 2.
    tight = {"a": (200, 100, 600, 1100), "b": (201, 140, 601, 1101), "c": (200, 120, 599, 1099)}
    assert silhouette_spread(tight)["passed"], silhouette_spread(tight)
    loose = dict(tight, d=(200, 120, 600, 1103))
    assert not silhouette_spread(loose)["passed"]
    assert silhouette_spread({"only": (0, 0, 1, 1)})["passed"]
    # Deterministic body construction: tone sampling, the additive shift, and the head transplant.
    swatch = Image.new("RGB", (40, 40), (200, 150, 100))
    assert region_tone(swatch, (0, 0, 40, 40)) == (200, 150, 100)
    half_mask = Image.new("L", (40, 40))
    ImageDraw.Draw(half_mask).rectangle((0, 0, 19, 39), fill=255)
    two_tone = Image.new("RGB", (40, 40), (0, 0, 0))
    ImageDraw.Draw(two_tone).rectangle((20, 0, 39, 39), fill=(100, 100, 100))
    assert region_tone(two_tone, (0, 0, 40, 40), mask=half_mask) == (0, 0, 0)
    box = inset((0, 0, 40, 40), 0.25)
    assert box == (10, 10, 30, 30)
    # Two broad, uniform regions at very different brightness (green 180 vs 90, roughly the 2x
    # chest-vs-thigh falloff measured on a real carrier) plus a small local highlight within one of
    # them. With a radius modest relative to each region's own width, a point deep inside either
    # region has a local average close to that region's own value, so both broad regions must map to
    # the *same* target colour -- the falloff is normalized away, which is the whole point. The small
    # highlight, narrower than the blur radius, must still stand out from its own surroundings --
    # local shading is not just flattened along with everything else.
    paint = Image.new("RGB", (200, 100), (5, 20, 90))
    draw = ImageDraw.Draw(paint)
    draw.rectangle((0, 0, 99, 99), fill=(5, 180, 5))
    draw.rectangle((100, 0, 199, 99), fill=(5, 90, 5))
    draw.rectangle((25, 45, 34, 54), fill=(5, 220, 5))
    fg = Image.new("L", (200, 100), 255)
    ImageDraw.Draw(fg).rectangle((0, 0, 9, 9), fill=0)
    recolored = deterministic_skin_recolor(paint, fg, (200, 150, 100), radius=15)
    bright_region = recolored.getpixel((30, 10))
    dim_region = recolored.getpixel((170, 90))
    highlight = recolored.getpixel((29, 49))
    assert bright_region == dim_region == (200, 150, 100), \
        f"broad regional falloff must be normalized away, got {bright_region} vs {dim_region}"
    assert highlight[0] > bright_region[0], "a local highlight must still read brighter than its surroundings"
    assert recolored.getpixel((5, 5)) == paint.getpixel((5, 5)), "background must be untouched"
    head_source = Image.new("RGB", (60, 60), (255, 0, 0))
    body = Image.new("RGB", (60, 60), (0, 0, 255))
    head_mask = Image.new("L", (60, 60))
    ImageDraw.Draw(head_mask).ellipse((10, 10, 50, 50), fill=255)
    grafted = head_transplant(head_source, body, head_mask, feather=4)
    assert grafted.getpixel((30, 30)) == (255, 0, 0), "head interior must be bit-exact"
    assert grafted.getpixel((0, 0)) == (0, 0, 255), "far outside the head must be bit-exact body"
    edge = grafted.getpixel((10, 30))
    assert edge != (255, 0, 0) and edge != (0, 0, 255), "the boundary itself must actually blend"
    solid = Image.new("L", (60, 60))
    ImageDraw.Draw(solid).rectangle((20, 20, 39, 39), fill=255)
    eroded = erode(solid, 8)
    assert eroded.getpixel((29, 29)) == 255 and eroded.getpixel((21, 21)) == 0
    band = blend_zone(head_mask, feather=4, margin=0)
    assert band.getpixel((30, 30)) == 0, "deep interior of the mask is not part of the blend band"
    assert band.getpixel((0, 0)) == 0, "far outside the mask is not part of the blend band either"
    assert band.getpixel((10, 30)) == 255, "the boundary itself, where alpha is fractional, must be"
    widened = blend_zone(head_mask, feather=4, margin=6)
    assert sum(widened.histogram()[1:]) > sum(band.histogram()[1:]), "margin must actually widen the band"
    # Repaint must cover carrier-only body, exclude the preserved head, and preserve must overhang
    # the head by the neck overlap so the join has something to blend into.
    person = Image.new("L", (200, 300))
    ImageDraw.Draw(person).rectangle((80, 40, 120, 260), fill=255)
    head = Image.new("L", (200, 300))
    ImageDraw.Draw(head).rectangle((85, 40, 115, 90), fill=255)
    carrier_person = Image.new("L", (200, 300))
    ImageDraw.Draw(carrier_person).rectangle((60, 40, 140, 260), fill=255)
    repaint, preserve = body_repaint_mask(person, head, carrier_person)
    assert repaint.getpixel((70, 200)) > 200, "carrier-only body must be repainted"
    assert repaint.getpixel((100, 60)) == 0, "preserved head must be outside the repaint mask"
    assert preserve.getpixel((100, 60)) == 255
    assert preserve.getpixel((100, 90 + NECK_OVERLAP // 2)) == 255, "neck overlap missing"
    ring = silhouette_outline(carrier_person)
    assert ring.getpixel((100, 150)) == 0 and ring.getpixel((60, 150)) == 255
    assert sum(ring.histogram()[1:]) < sum(carrier_person.histogram()[1:])
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
