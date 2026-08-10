#!/usr/bin/env python3
"""Run the carrier -> full-body skin -> head identity -> clothing PoC."""

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
import time
from urllib import error, request

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat

import subprocess

from comfy import api, endpoint, run, upload_image
import layered_costume_production as production

STAGES = ("preflight", "carrier", "envelope", "skin", "garment", "preprocess", "identity", "clothes", "extract", "composite")


TOOL_ROOT = Path(__file__).resolve().parent


DEFAULT_ROOT = Path("/tmp/cover-story-qwen2512-skin-head-clothes-poc")
DEFAULT_PERFORMER = Path("/mnt/Misc/sd/cover-story/layered-costume-production-v20d/raw/preprocess/"
                         "preprocess-actor-154-center-s2026604027.png")
DEFAULT_CORRIDORKEY_ROOT = "/workspace/CorridorKey"
# Pods are recreated often and only /workspace survives; see the script's header for what breaks.
BOOTSTRAP_SCRIPT = "pod_bootstrap.sh"
REMOTE_BOOTSTRAP = "/workspace/runpod-slim/bootstrap.sh"
POC_RUN_ID = "qwen2512-skin-head-clothes-poc"
# Instance settings live outside the repository: this file holds a Comfy token and SSH details,
# and the handover forbids storing either here. ~/.config is not a git worktree, so it cannot be
# committed by accident.
CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cover-story" / "instance.json"
CONFIG_TEMPLATE = {
    "server": "http://HOST:PORT/?token=TOKEN",
    "ssh_target": "root@HOST",
    "ssh_port": 22,
    "edit_model": production.EDIT_MODEL,
    "corridorkey_root": DEFAULT_CORRIDORKEY_ROOT,
    "performer": str(DEFAULT_PERFORMER),
    "output_dir": str(DEFAULT_ROOT),
    # Only run_serverless_edit_test.py reads these; they live here so there is one file to protect
    # rather than a second dotfile holding a second credential.
    "runpod_endpoint": "ENDPOINTID",
    "runpod_api_key": "RUNPODAPIKEY",
}
PLACEHOLDERS = ("HOST", "PORT", "TOKEN", "ENDPOINTID", "RUNPODAPIKEY")
# The RunPod proxy 404s for a window after ComfyUI restarts; long enough to cover it, short enough
# that a genuinely dead server still fails the run rather than hanging it.
SERVER_RETRIES = 5
SERVER_RETRY_WAIT = 10
CARRIER_PROMPT = (
    "Full-body centered frontal bald woman in a natural relaxed standing pose on a seamless evenly lit matte "
    "chroma-key blue background. She has a slender, feminine, statuesque hourglass figure, and ample cleavage. "
    "Her head, face, neck, clavicles, shoulders, arms, hands, torso and legs are "
    "uniformly matte chroma-key green body paint; no hair, wig, clothing, pasties, accessories, gloss, latex or "
    "reflections. Keep anatomy clear, proportions natural, feet visible, and the blue background clean and uniform."
)
CARRIER_NEGATIVE = "低分辨率，低画质，肢体畸形，手指畸形，画面过饱和，蜡像感，人脸无细节，过度光滑，画面具有AI感。构图混乱。文字模糊，扭曲, nipples"
# Grey-carrier / VITON path (see VITON_CLOTHING_POC_HANDOVER.md): the chroma screen is replaced by a
# flat grey background, so learned matting extracts cleanly (the matte is background-colour-invariant,
# measured 0.48-0.93/255 on the v3 plates) and no key colour exists to spill or fringe.
#
# The figure is a NATURAL body, not a painted/grey-suit one: the matting path has no key colour, so
# nothing needs the figure painted -- and the grey-suit prompt proved fragile (Qwen 2512 rendered it
# patchily: natural-skin head, near-black blotches, suit darker than the background, so the
# deterministic recolor amplified suit shading into splotchy skin). A swimsuit-clad natural figure
# renders reliably, gives the recolor real shading to work with, and is what the Qwen edit's
# "dress this person" use case expects. The suit region (recolored to skin tone) sits invisibly
# under the clothes plate. The suit/background contrast is load-bearing: screen_foreground("grey")
# keys on distance from a per-row border-derived background estimate, so the background must stay
# visibly darker than her skin.
GREY_CARRIER_PROMPT = (
    "Full-body centered frontal bald young woman in a natural relaxed standing pose on a seamless evenly lit "
    "flat medium grey background. She wears a plain matte light-grey fitted swimsuit covering her torso and "
    "hips. Her shaved head, face, neck, arms, hands and legs are natural bare skin; no hair, no headwear, no "
    "hair accessories, no jewellery, no gloss, no reflections. Keep anatomy clear, proportions natural, feet "
    "visible, and the medium grey background clean, uniform and a few shades darker than her skin."
)
GREY_SKIN_PROMPT = (
    "Recolor the grey-clad person to one fair, warm natural skin tone. Keep the bald head, anatomy, pose, "
    "framing, light grey background and everything else unchanged."
)
# One T2I generation per outfit; CatVTON warps it onto the person image. Flat lay keeps it
# pose-agnostic. The shoes in the catalog description are deliberately absent here: a flat lay cannot
# carry footwear, and shoe handling is an open VITON item (see the handover's risks).
GARMENT_PROMPT = (
    "Flat lay product photo of a fitted plum Victorian walking dress with a high collar, long sleeves and a "
    "full skirt, shown at full length on a plain white background, even studio lighting, crisp fabric detail."
)
# A worn garment reference carries the coverage cues a flat lay cannot: the dress on a model shows the
# edit model where sleeves end and the skirt falls, which a 2D flat garment cannot imply -- measured:
# the flat-lay reference left 79% of the blue paint in the lower body (rows 800-1200), i.e. the model
# drew the torso but not the skirt/legs. Use --garment-worn for the chroma/grey garment-ref runs.
GARMENT_WORN_PROMPT = (
    "Full-length front-view photograph of a woman wearing a fitted plum Victorian walking dress with a high "
    "collar, long sleeves, a full skirt and dark leather shoes, standing straight on a plain white background, "
    "even studio lighting. The dress covers her torso, arms and legs completely; no skin is visible below her "
    "neck."
)
# chflame163/ComfyUI_CatVTON_Wrapper: pads to its internal 768x1024 working size and restores the
# output to the input size and position (restore_padding_image), so registration to the carrier canvas
# survives -- unlike pzc163/Comfyui-CatVTON's resize_and_crop, which would need an inverted crop
# transform. Weights live under models/CatVTON/ on the pod (stable-diffusion-inpainting + attn ckpt).
CATVTON_NODE = "CatVTONWrapper"
DEFAULT_BIREFNET_MODEL = TOOL_ROOT / "models" / "BiRefNet-general-tiny.onnx"
DEFAULT_BIREFNET_PYTHON = TOOL_ROOT / ".venv-birefnet" / "bin" / "python"
# Real-ESRGAN x2, run over a plate before matting so the matte has sharper structure to decide on.
# Not a segmentation model and never reaches the output -- see birefnet_extract.upscale(). The
# dedicated *matting* BiRefNet checkpoints were tested on 2026-08-08 and rejected: holes and spill
# were unchanged, the soft-edge band grew only ~20%, and the run cost 6.5x more, because the limit
# is the plate's own soft hair edge rather than the matte. See CLAUDE_PICKUP_HANDOVER.md.
DEFAULT_UPSCALE_MODEL = TOOL_ROOT / "models" / "realesrgan-x2.onnx"
# Short by design: HANDOVER.md's generation rules forbid long anatomical checklists, and
# edit_graph()'s negative conditioning is hardcoded empty, so every "do not add X" would enter as
# a positive token instead of a suppressor. Drift is measured by drift_check(), not asserted in
# prose.
#
# Single reference, deliberately. Passing the performer as image 2 to sample her tone made the
# model abandon the carrier and reproduce the reference outright — dress, backdrop and crop —
# drifting 314px. The tone is therefore described rather than referenced. "fair, warm" matches
# this performer's measured skin (cheek 196,156,137; chest 227,190,164); a generic "medium" came
# back ~30 RGB levels too dark, which would seam at the envelope boundary on a skin-exposing
# outfit. In production this adjective comes from the catalog's skin_tone_group.
SKIN_PROMPT = (
    "Recolor the green person to one fair, warm natural skin tone. Keep the bald head, anatomy, pose, "
    "framing, blue background and everything else unchanged."
)
# Preprocessing is HANDOVER.md step 3.1: expose a compatible neckline and a clean hair outline
# before identity transfer. Unlike v20d's GREEN_PREPROCESS this targets a matte blue background,
# so the reference matches this pipeline's key colour instead of fighting it.
#
# Alignment matters because the identity edit regenerates inside its mask at denoise 1.0, where
# image 1 carries the carrier's face at the very position being painted. An unaligned reference
# cannot compete with that spatial prior: the transfer keeps the carrier's eyes and skin and takes
# only the hair.
#
# SINGLE REFERENCE, and the framing is described rather than referenced. This model does not blend
# two references in a mask-free edit — it returns one of them and the prompt decides which. Five
# two-reference attempts confirmed it: emphasising image 1 kept the performer but ignored image 2's
# pose entirely, while emphasising image 2 returned image 2 verbatim (difference 4.08-15.58 against
# ~32 for a genuine transfer), whether image 2 was the skin plate or the green carrier. Describing
# the target framing instead reaches scale ~0.9-1.0 with identity intact, which no two-reference
# phrasing achieved.
#
# Imperative "change X to Y" clauses acting on image 1, with no cross-image pronouns: "her" can
# only bind to the image being edited.
#
# The reference is bare because the identity envelope reaches y=473 — below the bust — so the
# reference has to carry skin through that region to inform it; a covering there cannot be cropped
# away without blanking the very area being painted. This is an intermediate asset only. The
# shipped composite layers the clothed-body plate over this one, so the final product is clothed.
PREPROCESS_PROMPT = (
    "Zoom out to show her whole body standing, and remove her clothing so her body is bare. "
    "Change the background to matte chroma key blue. Keep her face, hair and skin tone unchanged."
)
# Grey-path preprocess: the background must match the carrier's, not chroma blue -- the transplanted
# head carries its background's fringe into the composite, and with matting (not chroma keying) that
# fringe stays in the matte. On the grey composite background a grey fringe is invisible; a blue one
# is the halo this prompt exists to prevent. Measured: the v3-run head showed exactly that blue halo.
GREY_PREPROCESS_PROMPT = (
    "Zoom out to show her whole body standing, and remove her clothing so her body is bare. "
    "Change the background to a flat even medium grey, a few shades darker than her skin. "
    "Keep her face, hair and skin tone unchanged."
)
# Mirrors run_green_carrier_poc.py's validated HEAD_PROMPT shape. Deliberately says nothing about
# hair length: "the face and hair of the woman in image 2" already takes hers, and an earlier
# "let long hair fall behind her shoulders" pushed long hair onto performers who do not have it. The
# clothing layer covers the shoulder underlap through composite order, not through the prompt.
# The inverted transfer. Every earlier construction painted the performer's face into the carrier
# and lost to whatever face was already there, because the carrier's face is the spatial prior at
# exactly that position. Here she is image 1, the mask covers her body, and her head sits outside
# it where ImageCompositeMasked is bit-exact: identity cannot drift because nothing repaints it.
#
# Image 2 is the skin plate, not the raw carrier: same pose and silhouette but natural skin, so the
# body being painted has nothing green to copy.
IDENTITY_PROMPT = (
    "Keep image 1's head, face and hair completely unchanged. Change her body below the neck to the bare "
    "standing body, pose and proportions of image 2, with the same matte chroma key blue background."
)
# Alignment matches faces, not heads -- see production.face_align().
FACE_SAM_PROMPT = "face"
HEAD_SAM_PROMPT = "head, face, ears and hair"
PERSON_SAM_PROMPT = "the whole person"
SAM_PREFIX = "cover-story/qwen2512-skin-head-clothes"
# A small, local touch-up on compose_identity()'s output: the Gaussian-blended join at the neck and
# shoulders reads slightly soft next to the crisp skin either side of it. Says nothing about pose,
# shape or proportions -- the mask (production.blend_zone(), a narrow dilated band around the actual
# blend) is what keeps this from being able to touch either.
SKIN_BLEND_PROMPT = (
    "Blend and even out the skin tone, texture and lighting across the masked body so it matches the "
    "skin of her face and neck. Keep her pose, body shape, proportions, the background and everything "
    "outside the masked area exactly the same."
)
# Alternative skin prompts. The default above asks the model to "blend and even out" -- then we
# measure saturation *spread* and complain the result is uniform. We have been asking for the
# defect. "variation" asks for the quantity actually being scored; "tone" names a target instead of
# pointing at her face, to separate "the conditioning is too weak" from "denoise is too low".
SKIN_BLEND_PROMPTS = {
    "blend": SKIN_BLEND_PROMPT,
    "variation": (
        "Repaint the masked body as photographic skin that matches her face and neck: natural colour "
        "variation, warmer at the knees, elbows and hands, visible pores and subsurface warmth. Not "
        "airbrushed, not uniform, not plastic. Keep her pose, body shape, proportions, the background "
        "and everything outside the masked area exactly the same."
    ),
    "tone": (
        "Repaint the masked body as bare {tone} skin, photographic and naturally varied rather than "
        "smooth or airbrushed. Keep her pose, body shape, proportions, the background and everything "
        "outside the masked area exactly the same."
    ),
}
SKIN_BLEND_DENOISE = 0.4
SEAM_PROMPT = (
    "Smooth and blend the skin tone, texture and lighting across the masked area where her neck and "
    "shoulders meet, so the join between them is seamless and natural. Keep her pose, body shape, "
    "proportions and everything outside the masked area exactly the same."
)
# Canny over openpose: measured 1-2 px of silhouette drift against openpose's 2-7. Identity came
# out equivalent (12.66 against 12.51 with a 12.33 ceiling), but at n=2 those ranges overlap, so
# only the silhouette result is established.
# IDENTITY_CONTROL, IDENTITY_PROMPT and the functions below them (body_masks(), identity_control())
# are the 2026-08-04/05 diffusion-repaint construction: paint the carrier's silhouette onto the
# performer via a masked edit and a ControlNet. Superseded in the pipeline by compose_identity() --
# see its docstring -- but kept, and still used by run_phase3_probe.py and the day's
# run_ghost_foot_*.py comparison scripts, as the historical construction those measurements are
# against.
IDENTITY_CONTROL = "canny"
# The outfit enumeration is the free-form design description the handover calls for, so it stays.
# What was removed: green "hair" the bald carrier does not have, "green suit" vocabulary belonging
# to the v20d carrier, and negations that only ever reached the model as positive tokens.
# One description, used by both clothes modes. They must not drift: GARMENT_REF_PROMPT used to
# carry no description at all ("the outfit from image 2"), and the resulting plate was missing
# exactly the four features named here that the image alone did not convey -- collar, gloves, full
# skirt, shoes. That made the 2026-08-07 garment-ref run a test of "image *instead of* text"
# rather than "image *plus* text", so its negative result says nothing about garment references.
# See CLAUDE_PICKUP_HANDOVER.md, 2026-08-08.
GARMENT_DESCRIPTION = (
    "a fitted plum Victorian walking dress with a high collar, long sleeves, matching gloves, "
    "a full skirt and dark leather shoes"
)
CLOTHES_PROMPT = (
    "Keep image 1's {aperture} head, pose, framing and {screen} background unchanged. Dress the masked body in "
    "{description}. The garment may extend beyond the body silhouette for natural cloth bulk."
)
# garment-ref clothes mode: the same masked edit, but the outfit comes from a garment *image*
# (reference 2) instead of a text description. Masked edits preserve the latent outside the mask,
# which is the regime where this edit model follows a second reference (see the STATUS reference-
# collapse notes: mask-free edits return one of the two references, masked edits behave). Image 1
# still owns pose, framing and background; image 2 supplies colour, fabric and silhouette.
GARMENT_REF_PROMPT = (
    "Keep image 1's {aperture} head, pose, framing and {screen} background unchanged. Dress the masked body in "
    "the outfit from image 2 -- {description} -- matching image 2's colour, fabric, cut and silhouette. The "
    "garment covers her torso, arms and legs completely, as the outfit requires; no painted skin may remain "
    "inside the masked region. The garment may extend beyond the body silhouette for natural cloth bulk."
)
# Coverage variants. The waist gap -- the painted bodice being narrower than the carrier's torso, so
# the body shows at the sides -- is a generation failure, not a masking one: the clothes mask already
# permits 48px past the silhouette and the prompt already permits cloth bulk. These say it two ways,
# positively and as a prohibition, because it is not obvious which a diffusion model honours.
CLOTHES_COVERAGE_CLAUSES = {
    "default": "",
    "sides": " The garment covers her sides and back completely, with no gap between the bodice and "
             "her arms.",
    "bulk": " The garment is cut generously and extends past her body outline on both sides with "
            "natural cloth bulk, never narrower than her torso.",
}
GREY_GARMENT_REF_PROMPT = (
    "Keep image 1's head, pose, framing and medium grey background unchanged. Dress the masked body in the "
    "outfit from image 2 -- {description} -- matching image 2's colour, fabric, cut and silhouette. The "
    "garment covers her torso, arms and legs completely, as the outfit requires. The garment may extend "
    "beyond the body silhouette for natural cloth bulk."
)
# From layered-costume-catalog.json: victorian / outfit-01 carries "key_color": "green", and
# production.rejected_key_colors() confirms plum permits either. The PoC previously hardcoded blue
# for every stage, contradicting its own catalog; measured on the composite that cost 1451 px of
# visible skin below the neckline against 72 px on green. The garment interior keys identically
# either way -- what fails on blue is the boundary, where a plum/blue blend stays blue-dominant and
# reads as screen. Plum has almost no green channel, so the same blend against green does not.
#
# Only the clothing plate moves. Skin and identity stay on blue: SKIN_PROMPT has to distinguish
# "the green person" from "the blue background", which a single-colour carrier cannot express.
CLOTHES_KEY_COLOR = "green"
# The body paint is whichever key colour the screen is not, so the head stays a distinct aperture.
APERTURE_COLOR = {"blue": "green", "green": "blue"}
HINT_DILATION = 9
# The identity envelope stays deliberately loose, down through the shoulders and upper chest. The
# clothes envelope subtracts a different, narrower SAM region so the garment keeps the underlap it
# has to cover in the composite; see head_stop_region() for why that subtraction is clipped at the
# neck base rather than simply dilated less.
IDENTITY_SAM_PROMPT = "head, hair, face, ears, neck, clavicles, shoulders and upper chest"
# Head only -- deliberately NOT the neck. head_stop_region() cuts the forbidden zone off at the
# bottom of *this* region's bbox, so including the neck here put the cutoff at the neck base and
# made the neck unpaintable, which is why every run through 2026-08-08 produced a boat neckline
# with a flat horizontal cut and no collar could exist at any prompt or garment reference. The
# function's own docstring already said it "must stop at the neck base so the garment keeps its
# shoulders and collar" -- the prompt contradicted the intent. With the head alone the cutoff
# lands at the chin, the neck becomes paintable, and the 97px dilation still protects the skull
# sideways and upward against CLOTHES_PROMPT's forbidden bonnet.
CLOTHES_STOP_SAM_PROMPT = "head, face and ears"
IDENTITY_DILATION = 97
SUPPORT_DILATION = 97
CLOTHES_STOP_DILATION = 97


def apply_variant_dials(args):
    """Override the geometry constants from the command line.

    Every one of these is a number somebody chose once and nobody has varied since. Exposed as flags
    rather than edited in place so a sweep can try several values in one pod session, and so each
    output directory records which value produced it -- an unlabelled variant is indistinguishable
    from a regression three runs later.

    Set on the module rather than passed down because each is read at call time from module scope by
    several functions. HEAD_BLEND_FEATHER is the exception and the reason this needs saying: it is a
    *default argument* of head_transplant() and blend_zone(), bound at definition time, so setting
    the module attribute alone would move the checks (which read it at call time) without moving the
    blur they check. compose_identity() passes it explicitly for exactly that reason."""
    dials = {
        "IDENTITY_DILATION": (globals(), args.identity_dilation),
        "SUPPORT_DILATION": (globals(), args.support_dilation),
        "CLOTHES_STOP_DILATION": (globals(), args.clothes_stop_dilation),
        "NECK_OVERLAP": (vars(production), args.neck_overlap),
        "HEAD_BLEND_FEATHER": (vars(production), args.head_blend_feather),
        "SKIN_BLEND_EDGE_GUARD": (vars(production), args.skin_blend_edge_guard),
    }
    changed = {}
    for name, (namespace, value) in dials.items():
        if value is not None and value != namespace[name]:
            changed[name] = f"{namespace[name]} -> {value}"
            namespace[name] = value
    if changed:
        for name, move in changed.items():
            print(f"  dial     {name:24s} {move}", flush=True)
    return changed
# Generous: these catch a reframe or a redrawn background, not VAE round-trip noise.
DRIFT_LIMIT_PX = 16
DRIFT_LIMIT_BACKGROUND = 24.0


def redact(value):
    """Never print a Comfy token, even into a terminal scrollback."""
    return re.sub(r"(token=)[^&\s]+", r"\1REDACTED", str(value))


def load_config(path):
    if not path.is_file():
        return {}
    config = json.loads(path.read_text(encoding="utf-8"))
    unknown = sorted(set(config) - set(CONFIG_TEMPLATE))
    if unknown:
        raise RuntimeError(f"unknown keys in {path}: {', '.join(unknown)}; "
                           f"known keys are {', '.join(sorted(CONFIG_TEMPLATE))}")
    # An untouched placeholder counts as unset, so a half-filled file fails loudly rather than
    # dialling out to a literal "HOST".
    return {key: value for key, value in config.items()
            if value not in ("", None) and not any(marker in str(value) for marker in PLACEHOLDERS)}


def init_config(path):
    if path.is_file():
        raise RuntimeError(f"{path} already exists; edit it directly")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(CONFIG_TEMPLATE, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def resolver(config, config_path, sources):
    """CLI flag, then the config file, then the environment. The config wins over the environment
    deliberately, and every source is printed: a stale exported variable must not silently shadow
    a stored setting."""
    def resolve(name, cli_value, env_name=None, fallback=None):
        candidates = [(f"--{name.replace('_', '-')}", cli_value), (str(config_path), config.get(name))]
        if env_name:
            candidates.append((env_name, os.environ.get(env_name)))
        for source, value in candidates:
            if value not in (None, ""):
                sources[name] = source
                return value
        sources[name] = "built-in default"
        return fallback
    return resolve


def save_png(image, path):
    production.save_png(image, path)


def soft_free(server, drop_from_ram=False):
    """Free VRAM, keeping the weights in system RAM so the next load is a PCIe copy, not a re-read.

    ComfyUI's two /free flags are not two intensities of the same thing (main.py, the block that
    reads `q.get_flags()`):

      unload_models -> unload_all_models() -> detach() -> unpatch_model(offload_device).
                       Weights move to CPU RAM. VRAM is freed, the RAM copy survives.
      free_memory   -> e.reset(), which wipes the execution cache. That drops the last reference to
                       the ModelPatcher, so the RAM copy is collected too and the next run re-reads
                       the model from disk -- 19 GiB for the edit model.

    This used to send both, which is why alternating the edit model with SAM was so slow. The pod
    has 186 GB of RAM against ~36 GB of weights, so there is no reason to pay that.

    Note the second half of the same problem lives in ComfyUI's launch flags, not here: the default
    HierarchicalCache calls clean_unused() after every prompt and evicts node outputs absent from
    the *current* prompt, so a SAM graph still evicts the edit model's loader. --cache-lru N keeps
    them both. Fixing this end alone helps repeated runs of one graph, not alternation.
    """
    payload = b'{"unload_models":true,"free_memory":true}' if drop_from_ram \
        else b'{"unload_models":true,"free_memory":false}'
    # Retried because the pod is reached through the RunPod proxy, which answers 404 for a short
    # window after ComfyUI restarts -- the backend is listening on the pod before the proxy has
    # reconnected to it. A run that dies here has usually already spent a long time generating.
    for attempt in range(SERVER_RETRIES):
        req = request.Request(
            endpoint(server, "/free"),
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "CoverStoryComfy/1.0"},
        )
        try:
            with request.urlopen(req, timeout=60):
                return
        except (error.HTTPError, error.URLError) as failure:
            if attempt == SERVER_RETRIES - 1:
                raise
            print(f"  /free failed ({failure}); retrying in {SERVER_RETRY_WAIT}s", flush=True)
            time.sleep(SERVER_RETRY_WAIT)


# These three moved to the production module when the inverted identity transfer was promoted out
# of run_phase3_probe.py; the aliases stay because the probes and the metrics call them as poc.*.
image_from = production.image_from
dilate = production.dilate
screen_foreground = production.screen_foreground


def screen_foreground_hint(image, screen="blue", dilation=HINT_DILATION):
    """Widen and soften the raw foreground into a CorridorKey hint; not final alpha."""
    return dilate(screen_foreground(image, screen), dilation).filter(ImageFilter.GaussianBlur(2))


def sam_hints(server, source, prompts, prefix, work):
    remote = upload_image(server, source, subfolder="cover-story/qwen2512-skin-head-clothes/input")
    result_dir = Path(tempfile.mkdtemp(prefix="sam-", dir=work))
    result = run(server, production.sam_graph(remote, prompts, prefix), result_dir, 1800)
    paths = sorted(Path(item["path"]) for item in result["images"] if "-raw-hint-" in Path(item["path"]).stem)
    if len(paths) != len(prompts):
        raise RuntimeError(f"expected {len(prompts)} SAM hints, found {len(paths)}")
    return [Image.open(path).convert("L").copy() for path in paths]


def sam_mask(server, image, prompt, work, prefix):
    """One SAM region as a hard binary mask, with its bbox."""
    mask = sam_hints(server, image, [prompt], prefix, work)[0].point(lambda value: 255 if value > 127 else 0)
    box = mask.getbbox()
    if box is None:
        raise RuntimeError(f"SAM found no '{prompt}' in {image}")
    return mask, box


def aligned_performer(server, preprocessed, carrier, root, work, screen="blue",
                      reference_screen=None):
    """Put the performer on the carrier's canvas with her face on the carrier's face.

    The preprocess stage already arrives at roughly the right scale (measured 0.99 x 1.036 against
    the carrier), so this is a small correction rather than a rescue. See production.face_align()
    for why the anchor is the face box and not the head box."""
    path = root / "performer-aligned.png"
    if path.is_file():
        return path
    _, face = sam_mask(server, preprocessed, FACE_SAM_PROMPT, work, f"{SAM_PREFIX}/performer-face")
    _, target = sam_mask(server, carrier, FACE_SAM_PROMPT, work, f"{SAM_PREFIX}/carrier-face")
    with Image.open(carrier) as opened:
        size = opened.size
    # Pad with the *performer's* own backdrop, not the carrier's, so the whole aligned canvas shares
    # one background colour. compose_identity() then corrects that single colour to the carrier's
    # with production.rebackground(), which requires a uniform source backdrop -- padding with the
    # destination colour would be shifted by the full delta as though it were hers.
    aligned, scale = production.face_align(image_from(preprocessed), face, target, size,
                                           image_from(preprocessed).getpixel((8, 8)))
    save_png(aligned, path)
    check = production.aligned_height_check(production.silhouette_box(path, screen),
                                            production.silhouette_box(carrier, reference_screen or screen))
    print(f"  aligned: performer face {face} -> carrier face {target}, scale {scale:.3f}; "
          f"{check['detail']}", flush=True)
    if not check["passed"]:
        path.unlink(missing_ok=True)
        raise RuntimeError(
            f"aligned figure is {check['detail']['ratio']}x the carrier's height, outside "
            f"+/-{production.FACE_ALIGN_TOLERANCE:.0%}. Alignment matched the wrong feature: "
            f"performer face {face}, carrier face {target}, scale {scale:.3f}")
    return path


def body_masks(server, aligned, carrier, root, work):
    """Repaint and preserve masks for the diffusion-repaint construction; see
    production.body_repaint_mask(). Superseded in the pipeline by compose_identity(), which needs no
    repaint mask at all -- kept for run_phase3_probe.py and the run_ghost_foot_*.py comparison
    scripts. See LAYERED_COSTUME_PRODUCTION_STATUS.md, 2026-08-05, for why the repaint approach (a
    ghost limb from a reference/control conflict, then a chest size that turned out to vary by seed
    regardless of any fix) was abandoned rather than patched further."""
    repaint_path = root / "masks" / "identity-body-mask.png"
    preserve_path = root / "masks" / "identity-preserved-head.png"
    if repaint_path.is_file() and preserve_path.is_file():
        return repaint_path, preserve_path
    person, _ = sam_mask(server, aligned, PERSON_SAM_PROMPT, work, f"{SAM_PREFIX}/person")
    head, _ = sam_mask(server, aligned, HEAD_SAM_PROMPT, work, f"{SAM_PREFIX}/head")
    carrier_person, _ = sam_mask(server, carrier, PERSON_SAM_PROMPT, work, f"{SAM_PREFIX}/carrier-person")
    repaint, preserve = production.body_repaint_mask(person, head, carrier_person)
    save_png(repaint, repaint_path)
    save_png(preserve, preserve_path)
    print(f"  mask: repaint {sum(repaint.histogram()[1:])} px, preserved head "
          f"{sum(preserve.histogram()[1:])} px", flush=True)
    return repaint_path, preserve_path


def identity_control(server, kind, carrier, preserve, root, work, force, outline_width=None):
    """Control image for the body repaint, built from the *carrier* -- the pose being targeted.

    The preserved head is cut out of it either way. Nothing inside that region can be repainted, so
    head geometry in the control can only argue with pixels the sampler is not allowed to touch.
    For openpose that is free (draw_head/draw_face switches); for canny the bald skull outline has
    to be erased by hand, or the control asks for a scalp edge exactly where the hair is.

    outline_width overrides production.OUTLINE_WIDTH for canny, and is folded into the cache path so
    a wider test doesn't collide with the default-width file."""
    suffix = f"-w{outline_width}" if kind == "canny" and outline_width else ""
    raw = root / f"identity-control-{kind}{suffix}-raw.png"
    path = root / f"identity-control-{kind}{suffix}.png"
    if path.is_file() and not force:
        return path
    if kind == "canny":
        outline = production.silhouette_outline(screen_foreground(image_from(carrier)),
                                                 width=outline_width or production.OUTLINE_WIDTH)
        save_png(outline.convert("RGB"), raw)
    else:
        control_image(server, carrier, kind, f"{SAM_PREFIX}/control-{kind}", raw, work, force)
    image = image_from(raw)
    head = Image.open(preserve).convert("L").resize(image.size, Image.Resampling.NEAREST)
    image.paste(Image.new("RGB", image.size, (0, 0, 0)), (0, 0), dilate(head, 9))
    save_png(image, path)
    print(f"  control {kind}{suffix}: {sum(1 for pixel in image.convert('L').getdata() if pixel > 32)} "
          f"lit px after clearing the preserved head", flush=True)
    return path


def generate_carrier(server, path, work, force, prompt=CARRIER_PROMPT, negative_prompt=CARRIER_NEGATIVE,
                     family="qwen", size=(832, 1248), clip_type=production.FLUX2_CLIP_TYPE):
    """`family` selects the generator: "qwen" is the proven carrier, "flux" the photorealism
    candidate. See production.flux_generation_graph() for why a second one is worth testing --
    the carrier's look is the one thing no downstream stage can fix.

    `size` is exposed for the hair experiment: strand detail cannot be recovered by any matte
    because the source has none (measured 19.6% intermediate pixels at the hair edge), so generating
    larger and downsampling is the only route to real strands and honest antialiasing."""
    if path.is_file() and not force:
        return
    result_dir = Path(tempfile.mkdtemp(prefix="carrier-", dir=work))
    result = run(server, production.carrier_graph(
        family, prompt, production.seed_for("qwen2512:carrier:center"),
        "cover-story/qwen2512-skin-head-clothes/carrier", size=size, canonical=False,
        negative_prompt=negative_prompt, clip_type=clip_type,
    ), result_dir, 2400)
    save_png(Image.open(production.pick(result, "-raw")).convert("RGB"), path)


def control_image(server, source, kind, prefix, output, work, force):
    """Derive a ControlNet control image (SDPose skeleton or Canny edges) from `source`."""
    if output.is_file() and not force:
        return output
    graph = {"openpose": production.pose_graph, "canny": production.canny_graph}[kind]
    remote = upload_image(server, source, subfolder="cover-story/qwen2512-skin-head-clothes/input")
    result_dir = Path(tempfile.mkdtemp(prefix=f"{kind}-", dir=work))
    result = run(server, graph(remote, prefix), result_dir, 1800)
    save_png(Image.open(production.pick(result, f"-{kind}")).convert("RGB"), output)
    return output


def edit(server, source, prompt, mask, reference, seed, prefix, output, work, force,
         control=None, control_type="canny", control_strength=1.0, denoise=1.0):
    """mask=None runs a full-image, prompt-only edit (no ImageCompositeMasked paste boundary);
    output is then just the decoded result. A mask still produces a debug '-raw' sibling.

    control= a local control image path; see production.edit_graph for what it does.
    denoise< 1.0 harmonises the masked region instead of regenerating it; see edit_graph()."""
    if output.is_file() and not force:
        return
    remote_source = upload_image(server, source, subfolder="cover-story/qwen2512-skin-head-clothes/input")
    remote_mask = upload_image(server, mask, subfolder="cover-story/qwen2512-skin-head-clothes/input") if mask else None
    remote_reference = upload_image(server, reference, subfolder="cover-story/qwen2512-skin-head-clothes/input") if reference else None
    remote_control = upload_image(server, control, subfolder="cover-story/qwen2512-skin-head-clothes/input") if control else None
    result_dir = Path(tempfile.mkdtemp(prefix="edit-", dir=work))
    result = run(server, production.edit_graph(remote_source, prompt, seed, prefix, remote_reference,
                                               remote_mask, remote_control, control_type,
                                               control_strength, denoise), result_dir, 2400)
    raw = Image.open(production.pick(result, "-raw")).convert("RGB")
    if mask:
        save_png(raw, output.with_name(f"{output.stem}-raw.png"))
        save_png(Image.open(production.pick(result, "-masked")).convert("RGB"), output)
    else:
        save_png(raw, output)


def viton_graph(person_name, garment_name, mask_name, seed, prefix):
    """CatVTONWrapper graph: person + garment image + garment-region mask. The wrapper pads to its
    internal 768x1024 working size and restores the output to the input size and position, so the
    result stays registered to the carrier canvas. mask_grow=0: our envelope is already dilated."""
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": person_name}},
        "2": {"class_type": "LoadImage", "inputs": {"image": garment_name}},
        "3": {"class_type": "LoadImage", "inputs": {"image": mask_name}},
        "4": {"class_type": "ImageToMask", "inputs": {"image": ["3", 0], "channel": "red"}},
        "5": {"class_type": CATVTON_NODE, "inputs": {
            "image": ["1", 0], "mask": ["4", 0], "refer_image": ["2", 0],
            "mask_grow": 0, "mixed_precision": "fp16",
            "seed": seed, "steps": 40, "cfg": 2.5}},
        "6": {"class_type": "SaveImage", "inputs": {"images": ["5", 0],
                                                     "filename_prefix": f"{prefix}-viton"}},
    }


def generate_garment(server, path, work, force, prompt=GARMENT_PROMPT):
    """T2I flat-lay garment image (Qwen 2512, square). The outfit description drives this; CatVTON
    pads any aspect to its working size, so a square flat lay is safe for all poses."""
    if path.is_file() and not force:
        return
    result_dir = Path(tempfile.mkdtemp(prefix="garment-", dir=work))
    result = run(server, production.generation_graph(
        prompt, production.seed_for("qwen2512:garment-victorian"),
        "cover-story/qwen2512-skin-head-clothes/garment", size=(1024, 1024), canonical=False,
    ), result_dir, 2400)
    save_png(Image.open(production.pick(result, "-raw")).convert("RGB"), path)


def run_viton(server, carrier, garment, envelope, root, work, force, tone="light"):
    """VITON clothes stage (grey-carrier path). Person image is the tone-recolored carrier -- the
    deterministic recolor, not the [skin] Qwen edit -- so exposed skin carries the tone group's
    colour with exact shading and zero drift, and the body plate stays a function of
    (outfit, pose, tone) only, exactly like the chroma recipe's."""
    clothes = root / "clothes.png"
    if clothes.is_file() and not force:
        return clothes
    person = root / "viton-person.png"
    foreground = production.screen_foreground(image_from(carrier), "grey")
    target = production.TONE_RGB[tone]
    person_img = production.deterministic_skin_recolor(image_from(carrier), foreground, target,
                                                       paint_channel=None)
    save_png(person_img, person)
    remote_person = upload_image(server, person, subfolder="cover-story/qwen2512-skin-head-clothes/input")
    remote_garment = upload_image(server, garment, subfolder="cover-story/qwen2512-skin-head-clothes/input")
    remote_mask = upload_image(server, envelope["clothes_mask"],
                               subfolder="cover-story/qwen2512-skin-head-clothes/input")
    result_dir = Path(tempfile.mkdtemp(prefix="viton-", dir=work))
    result = run(server, viton_graph(remote_person, remote_garment, remote_mask,
                                     production.seed_for("qwen2512:viton"),
                                     "cover-story/qwen2512-skin-head-clothes/viton"), result_dir, 2400)
    save_png(Image.open(production.pick(result, "-viton")).convert("RGB"), clothes)
    return clothes


# Matte cache, keyed on the source image's own bytes and shared across runs. The aligned performer
# is deterministic for a given performer and pose, so without this every run re-derives an identical
# matte -- measured at ~2 min of CPU on the dev VM, on the critical path of every single run, and
# ~30 min across a 4-performer x 4-pose sweep. Lives outside the repo so it is never committed and
# survives output directories being deleted.
MATTE_CACHE = Path.home() / ".cache" / "cover-story" / "mattes"


def cached_matte(source, output, model=None, python=None, upscale=None, remote=None):
    """birefnet_extract(), but reused across runs when the source bytes are identical.

    Content-addressed rather than path-addressed: two runs write performer-aligned.png to different
    output directories, but the same performer in the same pose produces byte-identical files, so
    the hash is what makes the cache hit. The model and upscale choice are part of the key -- a
    different matting model must not silently return the previous one's alpha."""
    if output.is_file():
        return output
    key = hashlib.sha256(Path(source).read_bytes())
    key.update(str(model or DEFAULT_BIREFNET_MODEL).encode())
    key.update(str(upscale or "").encode())
    cached = MATTE_CACHE / f"{key.hexdigest()[:16]}.png"
    if not cached.is_file():
        MATTE_CACHE.mkdir(parents=True, exist_ok=True)
        if remote:
            remote_matte(source, None, cached, remote[0], remote[1],
                         model or DEFAULT_BIREFNET_MODEL, upscale)
        else:
            birefnet_extract(source, None, cached, model or DEFAULT_BIREFNET_MODEL,
                             python or DEFAULT_BIREFNET_PYTHON, False, upscale)
    else:
        print(f"  matte: cache hit {cached.name}", flush=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cached, output)
    return output


# Where the matting venv and models live on the pod's persistent volume. The venv is built by
# pod_bootstrap.sh step 8, which gates on CUDAExecutionProvider actually being listed rather than on
# the wheel installing -- stock onnxruntime installs cleanly and then silently runs on CPU.
POD_VOLUME = "/workspace/runpod-slim"
POD_MATTE_PYTHON = f"{POD_VOLUME}/matting-venv/bin/python"
POD_MATTE_DIR = f"{POD_VOLUME}/matting"


def ssh_options(ssh_port):
    """Multiplexed SSH, mirroring production.standalone_alpha(): one connection is reused across the
    several calls a single matte needs instead of paying a handshake for each."""
    return ["-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
            "-o", "ControlMaster=auto", "-o", "ControlPersist=120",
            "-o", "ControlPath=/tmp/cover-story-ssh-%C", "-p", str(ssh_port)]


def remote_matte_plan(source, aperture, output, ssh_target, ssh_port, model, upscale=None):
    """The commands remote_matte() will run, as data, so they can be asserted without a pod.

    Split out because the remote path cannot be exercised by the self-tests -- everything except the
    execution itself can be, and a malformed command discovered on the pod costs GPU time. That is
    not hypothetical: preflight() shipped with a NameError on 2026-08-08 and was only caught by an
    actual pod call."""
    options = ssh_options(ssh_port)
    remote_shell = "ssh " + " ".join(options)
    stem = Path(output).stem.replace(":", "-")
    remote = f"{POD_MATTE_DIR}/{stem}"
    push = [str(source), str(TOOL_ROOT / "birefnet_extract.py"), str(TOOL_ROOT / "birefnet_ab.py"),
            str(model)]
    if aperture:
        push.append(str(aperture))
    if upscale:
        push.append(str(upscale))
    run = [POD_MATTE_PYTHON, f"{remote}/birefnet_extract.py",
           "--source", f"{remote}/{Path(source).name}",
           "--model", f"{remote}/{Path(model).name}",
           "--output", f"{remote}/alpha.png"]
    if aperture:
        run += ["--aperture", f"{remote}/{Path(aperture).name}"]
    if upscale:
        run += ["--upscale", f"{remote}/{Path(upscale).name}"]
    return {
        "remote": remote,
        "mkdir": ["ssh", *options, ssh_target, f"mkdir -p {remote}"],
        # --ignore-existing on the model only: it is 224 MB and never changes, so re-sending it on
        # every plate would dominate the transfer.
        "push": ["rsync", "-az", "--no-owner", "--no-group", "-e", remote_shell,
                 *push, f"{ssh_target}:{remote}/"],
        "run": ["ssh", *options, ssh_target, " ".join(run)],
        "fetch": ["rsync", "-az", "--no-owner", "--no-group", "-e", remote_shell,
                  f"{ssh_target}:{remote}/alpha.png", str(output)],
    }


def remote_matte(source, aperture, output, ssh_target, ssh_port, model=DEFAULT_BIREFNET_MODEL,
                 upscale=None, force=False):
    """Matte on the pod's GPU. This is the only supported path on a small machine.

    BiRefNet peaks near 6 GB per plate on CPU; that OOM-killed the dev box three times on
    2026-08-08/09, taking in-flight runs and unrelated user processes with it. On the pod the
    allocation lands in 32 GB of VRAM and the pass takes well under a second instead of ~2 min, so
    this also takes local CPU work off the critical path *between* GPU stages."""
    if output.is_file() and not force:
        return output
    plan = remote_matte_plan(source, aperture, output, ssh_target, ssh_port, model, upscale)
    for step in ("mkdir", "push", "run"):
        result = subprocess.run(plan[step], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"remote matte {step} failed: {result.stderr.strip()[:400]}")
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(plan["fetch"], check=True)
    return output


def birefnet_extract(source, aperture, output, model=DEFAULT_BIREFNET_MODEL,
                     python=DEFAULT_BIREFNET_PYTHON, force=False, upscale=None):
    """Local matting extraction: alpha = BiRefNet(source) x (1 - aperture), run in the standalone
    onnxruntime venv (the runner itself is Pillow-only). Mirrors how CorridorKey runs as a separate
    process; unlike CorridorKey it needs no hint, no key colour, no pod, and no SSH.

    `upscale` names a super-resolution ONNX run over the plate before matting; the alpha comes back
    at the plate's own size, so nothing about the output RGB or its registration changes. See
    birefnet_extract.py's upscale() for the measurement that justifies it."""
    if output.is_file() and not force:
        return
    command = [str(python), str(TOOL_ROOT / "birefnet_extract.py"),
               "--source", str(source), "--model", str(model), "--output", str(output)]
    if aperture:
        command += ["--aperture", str(aperture)]
    if upscale:
        command += ["--upscale", str(upscale)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"birefnet_extract failed: {result.stderr.strip()}")


def viton_checks(person, clothes, envelope, carrier):
    """The VITON output must stay registered to the carrier silhouette (it is a diffusion warp,
    not a bit-exact paste) and must actually change the garment region; the head region must stay
    close to the person image since the aperture post-mask removes it in extract anyway."""
    image = image_from(clothes)
    head = image_from(envelope["head_mask"]).convert("L").point(lambda v: 255 if v > 127 else 0)
    body = ImageOps.invert(head)
    changed = production.region_tone(image, (0, 0, image.width, image.height), mask=body)
    silhouette = silhouette_checks(clothes, carrier, screen="grey")
    return [
        *silhouette,
        {"name": "viton_garment_region_changed", "passed": changed is not None, "detail": "mask region carries content"},
    ]


def build_hints(carrier, masks_dir, screen="blue"):
    """Record the carrier's own foreground estimate. This is the drift-check reference and
    provenance only — it must never be used as the hint for a generated plate; see plate_hints()."""
    masks_dir.mkdir(parents=True, exist_ok=True)
    person_hint = screen_foreground_hint(image_from(carrier), screen)
    paths = {
        "person_hint": masks_dir / "carrier-person-hint.png",
        "background_hint": masks_dir / "carrier-background-hint.png",
    }
    save_png(person_hint, paths["person_hint"])
    save_png(ImageOps.invert(person_hint), paths["background_hint"])
    return paths


# How far past the plate's own rough foreground estimate despilled_plate() reaches. Ties the scope
# to the *plate's own* boundary, not the diffusion edit's permission envelope -- see that function's
# docstring for why the envelope (dilated 97px for the edit's own working room) was the wrong scope
# and produced a large, wrong leak of its own.
# Key-channel dominance (key minus the brighter of the other two) below which a pixel is treated as
# ambiguous rather than confidently screen-coloured. Measured on a real plate: spill-contaminated
# edge pixels (a shadowed waist crease, a shoe's dark leather) sit at 39-54, with a marginal fringe up
# to 67 right at the true boundary (a continuous transition, not a clean binary); confident
# background, anywhere in the frame, sits at 79-87. 70 sits with comfortable margin on both sides.
def despilled_plate(source, screen, output, force, radius=production.SPILL_HOLE_CLOSE_RADIUS):
    """Despill `source` for extraction -- see production.scoped_despill() for the mechanism (small
    enclosed holes only, found by morphological closing) and the regression a dominance-only version
    of this caused near hair before landing on closing instead. Also applied, independently, to the
    colour layers segment_source() builds for the final composite -- this function exists only to
    prepare a cleaner *hint* for CorridorKey, not to be the sole place despill happens."""
    if output.is_file() and not force:
        return output
    save_png(production.scoped_despill(image_from(source), screen, radius), output)
    return output


def plate_hints(plate, masks_dir, name, screen="blue"):
    """Derive the CorridorKey hint from the plate being keyed, exactly as production.extract()
    does. A carrier-derived hint cannot work here: the carrier is bald and unclothed, so garment
    bulk and hair falling behind the shoulders both lie outside its silhouette at any dilation."""
    masks_dir.mkdir(parents=True, exist_ok=True)
    image = image_from(plate)
    raw_path = masks_dir / f"{name}-raw-hint.png"
    hint_path = masks_dir / f"{name}-hint.png"
    save_png(screen_foreground(image, screen), raw_path)
    save_png(screen_foreground_hint(image, screen), hint_path)
    return raw_path, hint_path


def drift_check(carrier, candidate, name, screen="blue"):
    """The mask-free recolor runs at denoise 1.0 with no latent mask, so the background is
    genuinely re-synthesized and no prompt wording can guarantee registration. Measure it before
    spending the identity and clothing edits."""
    before, after = image_from(carrier), image_from(candidate)
    if before.size != after.size:
        raise RuntimeError(f"{name}: canvas changed {before.size} -> {after.size}")
    reference = screen_foreground(before, screen)
    shift = [abs(a - b) for a, b in zip(reference.getbbox(), screen_foreground(after, screen).getbbox())]
    background = ImageOps.invert(reference).point(lambda value: 255 if value > 127 else 0)
    delta = ImageChops.multiply(ImageChops.difference(before, after).convert("L"), background)
    counted = sum(background.histogram()[1:]) or 1
    mean_delta = sum(value * count for value, count in enumerate(delta.histogram())) / counted
    result = {"silhouette_shift_px": shift, "background_mean_abs_diff": round(mean_delta, 2),
              "limits": {"silhouette_shift_px": DRIFT_LIMIT_PX, "background_mean_abs_diff": DRIFT_LIMIT_BACKGROUND}}
    if max(shift) > DRIFT_LIMIT_PX or mean_delta > DRIFT_LIMIT_BACKGROUND:
        raise RuntimeError(f"{name} drifted from the carrier: {result}; inspect {candidate}")
    return result


ENVELOPE_ACCEPTED = "accepted"
ENVELOPE_PENDING_REVIEW = "pending_review"


def envelope_overlay(carrier, head_mask, clothes_mask):
    """Tint the head/clothes edit-permission regions over the carrier. The carrier must stay
    visible through the tint: where each envelope falls against real anatomy is the only thing
    the review gate can actually judge. Image.paste() would replace the base outright."""
    base = image_from(carrier).convert("RGBA")
    for mask, (red, green, blue, opacity) in ((head_mask, (255, 60, 60, 130)), (clothes_mask, (60, 200, 255, 110))):
        tint = Image.new("RGBA", base.size, (red, green, blue, 0))
        tint.putalpha(mask.convert("L").point(lambda value: value * opacity // 255))
        base = Image.alpha_composite(base, tint)
    return base.convert("RGB")


def head_stop_region(stop):
    """The region the clothing edit may never touch. Deliberately anisotropic: generous sideways and
    upward — otherwise the dilated person envelope leaves a halo around the skull and
    CLOTHES_PROMPT's forbidden bonnet becomes paintable — but cut off at the chin so the neck stays
    paintable and a collar can exist. The cutoff row comes from SAM, so it stays pose-aware rather
    than a fixed center-pose constant.

    This is one half of the edit-mask / transparency-aperture split. The other half needs no code:
    the aperture is *colour*-derived (key_aperture() finds the carrier's paint on the finished
    plate), so it follows whatever the model actually painted. Paint a collar over the neck and that
    region is garment, hence opaque; leave it unpainted and it stays carrier paint, hence keyed
    transparent so the skin layer shows through. Deep necklines and high collars both become
    reachable from the same mask, which is what the catalog asks for."""
    box = stop.getbbox()
    if box is None:
        raise RuntimeError(f"SAM returned an empty '{CLOTHES_STOP_SAM_PROMPT}' region")
    below_neck = Image.new("L", stop.size, 0)
    below_neck.paste(255, (0, box[3], stop.size[0], stop.size[1]))
    return ImageChops.subtract(dilate(stop, CLOTHES_STOP_DILATION), below_neck)


def envelope_masks(person, head, stop):
    """Build both edit-permission envelopes. They must overlap through the shoulder junction:
    HANDOVER.md forbids butting two masks together, and CLOTHES_PROMPT asks for shoulders and a
    collar that a strict complement of the identity envelope would forbid painting."""
    return (dilate(head, IDENTITY_DILATION),
            ImageChops.subtract(dilate(person, SUPPORT_DILATION), head_stop_region(stop)))


def bootstrap_envelope(server, carrier, root, work, force):
    """Generate the SAM-derived identity/clothes edit-permission envelope once per carrier and mark
    it pending review. Nothing downstream may consume it directly; see load_accepted_envelope()."""
    masks = root / "masks"
    masks.mkdir(parents=True, exist_ok=True)
    head_path = masks / "identity-head-mask.png"
    clothes_path = masks / "clothes-body-mask.png"
    status_path = masks / "envelope-status.json"
    if force or not head_path.is_file() or not clothes_path.is_file():
        person, head, stop = sam_hints(
            server, carrier, ["person", IDENTITY_SAM_PROMPT, CLOTHES_STOP_SAM_PROMPT],
            "cover-story/qwen2512-skin-head-clothes/carrier-masks", work,
        )
        head_mask, clothes_mask = envelope_masks(person, head, stop)
        save_png(head_mask, head_path)
        save_png(clothes_mask, clothes_path)
        save_png(envelope_overlay(carrier, head_mask, clothes_mask), masks / "envelope-review.png")
        status_path.write_text(json.dumps({
            "status": ENVELOPE_PENDING_REVIEW,
            "source_sha256": production.sha256(carrier),
            "identity": {"sam3_prompt": IDENTITY_SAM_PROMPT, "dilation_px": IDENTITY_DILATION,
                         "sha256": production.sha256(head_path)},
            "clothes": {"sam3_prompt": "person", "dilation_px": SUPPORT_DILATION,
                        "subtract_sam3_prompt": CLOTHES_STOP_SAM_PROMPT,
                        "subtract_dilation_px": CLOTHES_STOP_DILATION,
                        "sha256": production.sha256(clothes_path)},
        }, indent=2) + "\n", encoding="utf-8")
    return status_path


def load_accepted_envelope(root, carrier):
    masks = root / "masks"
    status_path = masks / "envelope-status.json"
    if not status_path.is_file():
        raise RuntimeError(f"no envelope generated yet under {masks}; run bootstrap_envelope() first")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if status.get("status") != ENVELOPE_ACCEPTED:
        raise RuntimeError(
            f"envelope pending review — inspect {masks / 'envelope-review.png'}, then set "
            f'"status": "{ENVELOPE_ACCEPTED}" in {status_path} before continuing'
        )
    # An acceptance is only meaningful for the exact images that were reviewed.
    current = production.sha256(carrier)
    if status.get("source_sha256") != current:
        raise RuntimeError(
            f"envelope was accepted against a different carrier ({status.get('source_sha256')} "
            f"!= {current}); rerun with --force to rebuild and review it"
        )
    paths = {"head_mask": masks / "identity-head-mask.png", "clothes_mask": masks / "clothes-body-mask.png"}
    for name, key in (("head_mask", "identity"), ("clothes_mask", "clothes")):
        recorded = status.get(key, {}).get("sha256")
        if recorded and production.sha256(paths[name]) != recorded:
            raise RuntimeError(f"{paths[name]} changed since review; rerun with --force to rebuild and review it")
    return paths


def region_fractions(image, region):
    """Green-dominant and blue-dominant share of a region, plus the region's own coverage."""
    values = [pixel for pixel, inside in zip(image.convert("RGB").get_flattened_data(),
                                             region.get_flattened_data()) if inside > 127]
    if not values:
        return {"coverage": 0.0, "green": 0.0, "blue": 0.0}
    green = sum(g > r * 1.15 and g > b * 1.15 and g > 40 for r, g, b in values)
    blue = sum(b > r * 1.15 and b > g * 1.15 and b > 40 for r, g, b in values)
    return {"coverage": round(len(values) / (image.width * image.height), 4),
            "green": round(green / len(values), 4), "blue": round(blue / len(values), 4)}


def crown_darkness(image, foreground):
    """Fraction of dark pixels in the top slice of the figure — the carrier and skin plate must
    both stay bald, and dark hair is the failure this catches."""
    box = foreground.getbbox()
    if box is None:
        return 1.0
    crown = image.convert("RGB").crop((box[0], box[1], box[2], box[1] + max(1, (box[3] - box[1]) // 8)))
    values = list(crown.get_flattened_data())
    return round(sum(max(pixel) < 60 for pixel in values) / len(values), 4)


def carrier_checks(carrier):
    image = image_from(carrier)
    foreground = screen_foreground(image)
    box = foreground.getbbox()
    inside = region_fractions(image, foreground)
    outside = region_fractions(image, ImageOps.invert(foreground))
    feet_margin = image.height - box[3] if box else image.height
    return [
        {"name": "figure_coverage_plausible", "passed": 0.08 < inside["coverage"] < 0.45, "detail": inside["coverage"]},
        {"name": "figure_is_green", "passed": inside["green"] > 0.85, "detail": inside["green"]},
        {"name": "background_is_blue", "passed": outside["blue"] > 0.90, "detail": outside["blue"]},
        {"name": "feet_visible", "passed": feet_margin < image.height * 0.06, "detail": feet_margin},
        {"name": "carrier_is_bald", "passed": crown_darkness(image, foreground) < 0.03,
         "detail": crown_darkness(image, foreground)},
    ]


def envelope_checks(carrier, head_path, clothes_path, screen="blue"):
    with Image.open(head_path) as opened:
        head_mask = opened.convert("L")
    with Image.open(clothes_path) as opened:
        clothes_mask = opened.convert("L")
    frame = head_mask.width * head_mask.height
    person = screen_foreground(image_from(carrier), screen)
    head_area = sum(head_mask.point(lambda v: 255 if v > 127 else 0).histogram()[1:]) / frame
    clothes_area = sum(clothes_mask.point(lambda v: 255 if v > 127 else 0).histogram()[1:]) / frame
    overlap = sum(ImageChops.multiply(head_mask, clothes_mask).histogram()[1:])
    beyond = sum(ImageChops.subtract(clothes_mask, person).histogram()[1:])
    skull = ImageChops.subtract(head_mask, ImageChops.lighter(person, clothes_mask))
    return [
        {"name": "identity_envelope_plausible", "passed": 0.02 < head_area < 0.40, "detail": round(head_area, 4)},
        {"name": "clothes_envelope_plausible", "passed": 0.05 < clothes_area < 0.70, "detail": round(clothes_area, 4)},
        {"name": "envelopes_overlap", "passed": overlap > 0, "detail": overlap},
        {"name": "clothes_clears_silhouette", "passed": beyond > 0, "detail": beyond},
        {"name": "skull_halo_protected", "passed": skull.getbbox() is not None, "detail": "identity-only region exists"},
    ]


def preprocess_checks(preprocessed, reference, screen="blue", reference_screen=None):
    """Technical checks only. Whether the hair outline is clean, the earrings are gone and the
    identity survived are human calls — HANDOVER.md keeps identity and styling out of automation,
    so this stage is a review stop, not a pass/fail verdict on quality."""
    # `screen` keys the preprocessed plate, `reference_screen` the carrier. They differ the moment
    # the preprocess renders on grey while the carrier stays chroma; sharing one screen was fine
    # only while both backdrops were blue.
    reference_screen = reference_screen or screen
    image = image_from(preprocessed)
    foreground = screen_foreground(image, screen)
    inside = region_fractions(image, foreground)
    outside = region_fractions(image, ImageOps.invert(foreground))
    box = foreground.getbbox() or (0, 0, 1, 1)
    reference_image = image_from(reference)
    target = screen_foreground(reference_image, reference_screen).getbbox() or (0, 0, 1, 1)
    scale = [(box[2] - box[0]) / max(1, target[2] - target[0]), (box[3] - box[1]) / max(1, target[3] - target[1])]
    # Two-reference edits in this graph collapse onto one reference and discard the other. When
    # image 2 wins, the output is simply a copy of it and the performer is gone — an attempt that
    # did exactly that still scored 1.017 on a scale check, so alignment cannot be the gate here.
    # Difference from the reference is what actually separates a transfer from a copy: a collapsed
    # attempt measured 4.08, genuine ones 50.11 and 57.84.
    collapsed = sum(ImageStat.Stat(ImageChops.difference(
        image.resize(reference_image.size), reference_image)).mean) / 3
    if screen == "grey":
        background_check = {"name": "preprocess_background_is_grey",
                            "passed": max(outside["green"], outside["blue"]) < 0.25, "detail": outside}
    else:
        background_check = {"name": "preprocess_background_is_blue", "passed": outside["blue"] > 0.85,
                            "detail": outside["blue"]}
    return [
        {"name": "preprocess_is_not_a_copy_of_reference", "passed": collapsed > 20,
         "detail": round(collapsed, 2)},
        background_check,
        {"name": "preprocess_figure_coverage", "passed": 0.05 < inside["coverage"] < 0.70,
         "detail": inside["coverage"]},
        # She must arrive as natural skin; green here means the carrier's body paint bled into her.
        {"name": "preprocess_figure_not_green", "passed": inside["green"] < 0.05, "detail": inside["green"]},
        # Informational, not a gate: a scale mismatch means the identity edit must rescale the
        # face itself, which is worth knowing but is not a defect the way an identity loss is.
        {"name": "preprocess_scale_vs_carrier", "passed": True, "detail": [round(s, 3) for s in scale]},
        {"name": "preprocess_canvas", "passed": True, "detail": list(image.size)},
    ]


def skin_checks(carrier, skin):
    image = image_from(skin)
    foreground = screen_foreground(image)
    inside = region_fractions(image, foreground)
    outside = region_fractions(image, ImageOps.invert(foreground))
    dark = crown_darkness(image, foreground)
    return [
        {"name": "green_body_paint_removed", "passed": inside["green"] < 0.05, "detail": inside["green"]},
        {"name": "background_still_blue", "passed": outside["blue"] > 0.90, "detail": outside["blue"]},
        {"name": "still_bald", "passed": dark < 0.05, "detail": dark},
        drift_check_result(carrier, skin),
    ]


def grey_carrier_checks(carrier):
    """PIL-only checks for the flat-grey carrier: figure present via corner-distance, background grey
    (no channel dominance, unlike the chroma carriers), feet visible. The bald check needs SAM, which
    is color-independent and covered by the envelope stage anyway, so it is not repeated here."""
    image = image_from(carrier)
    foreground = screen_foreground(image, "grey")
    box = foreground.getbbox()
    inside = region_fractions(image, foreground)
    outside = region_fractions(image, ImageOps.invert(foreground))
    feet_margin = image.height - box[3] if box else image.height
    return [
        {"name": "figure_coverage_plausible", "passed": 0.08 < inside["coverage"] < 0.45, "detail": inside["coverage"]},
        {"name": "figure_not_chroma", "passed": inside["green"] < 0.5 and inside["blue"] < 0.5, "detail": inside},
        {"name": "background_is_grey", "passed": max(outside["green"], outside["blue"]) < 0.25, "detail": outside},
        {"name": "feet_visible", "passed": feet_margin < image.height * 0.06, "detail": feet_margin},
    ]


def grey_skin_checks(carrier, skin):
    """Grey-carrier equivalent of skin_checks: the suit has been recolored to natural skin (no
    chroma paint to measure), the background stayed grey, the head stayed bald, registration held."""
    image = image_from(skin)
    foreground = screen_foreground(image, "grey")
    inside = region_fractions(image, foreground)
    outside = region_fractions(image, ImageOps.invert(foreground))
    dark = crown_darkness(image, foreground)
    return [
        {"name": "suit_recolored_to_skin", "passed": inside["green"] < 0.5 and inside["blue"] < 0.5, "detail": inside},
        {"name": "background_still_grey", "passed": max(outside["green"], outside["blue"]) < 0.25, "detail": outside},
        {"name": "still_bald", "passed": dark < 0.05, "detail": dark},
        drift_check_result(carrier, skin, "grey"),
    ]


def drift_check_result(carrier, candidate, screen="blue"):
    try:
        return {"name": "registration", "passed": True, "detail": drift_check(carrier, candidate, "skin-tone", screen)}
    except RuntimeError as error:
        return {"name": "registration", "passed": False, "detail": str(error)}


def masked_edit_checks(reference, candidate, mask, screen=None):
    """ImageCompositeMasked leaves everything outside the mask bit-identical, so an exact
    comparison is available for free. Inside must have changed, or the edit was a no-op."""
    outside = production.outside_mask_changed(reference, candidate, mask)
    with Image.open(mask) as opened:
        region = opened.convert("L").point(lambda value: 255 if value > 127 else 0)
    before, after = image_from(reference), image_from(candidate)
    changed = ImageChops.multiply(ImageChops.difference(before, after).convert("L")
                                  .point(lambda value: 255 if value > 8 else 0), region)
    inside_changed = sum(changed.histogram()[1:])
    checks = [outside, {"name": "inside_mask_changed", "passed": inside_changed > 0, "detail": inside_changed}]
    if screen:
        if screen == "grey":
            # No body paint exists on a grey carrier; there is nothing to measure.
            return checks
        # Uncovered body paint is the defect; the screen showing through the envelope around the
        # garment is not. Those are different colours and they swap with the key, so this has to
        # follow the screen. Hardcoded "green" read the screen itself once the key moved to green
        # and failed a good plate at 0.2628 while the real remnant was 0.0078.
        paint = APERTURE_COLOR[screen]
        residue = region_fractions(after, region)[paint]
        checks.append({"name": "no_paint_remnant_in_edit", "passed": residue < 0.05,
                       "detail": {"paint": paint, "fraction": residue}})
    return checks


def sample_face_tone(image):
    """Median RGB of skin-toned pixels in the face region (top third, skin-hue heuristic).
    A box-mean on the SAM head box landed on hair/shadows at (147,114,93) against the true face
    tone (196,158,126) measured on the same image -- the face is small relative to the hair, so a
    mean over an imperfect box is dragged down. The median over hue-filtered pixels is robust to
    both."""
    image = image.convert("RGB")
    r, g, b = image.split()
    skin = ImageChops.multiply(
        ImageChops.multiply(
            ImageChops.subtract(r, g).point(lambda v: 255 if v > 0 else 0),
            ImageChops.subtract(g, b).point(lambda v: 255 if v > 0 else 0),
        ),
        ImageChops.multiply(
            r.point(lambda v: 255 if v > 80 else 0),
            ImageChops.subtract(r, b).point(lambda v: 255 if v > 20 else 0),
        ),
    )
    box = (0, 0, image.width, max(1, image.height // 3))
    stats = ImageStat.Stat(image.crop(box), mask=skin.crop(box))
    return tuple(round(value) for value in stats.median)


def compose_identity(server, aligned, carrier, root, work, screen="blue",
                     birefnet_model=DEFAULT_BIREFNET_MODEL, birefnet_python=DEFAULT_BIREFNET_PYTHON,
                     matte_remote=None):
    """Build the identity plate by pasting the performer's own head onto the carrier's own body,
    instead of asking diffusion to regenerate a body that only approximates the carrier's
    proportions.

    Registration by construction, not by control-strength tuning: below the head-blend band, every
    pixel of the output is derived directly from carrier.png's own pixel grid (see
    production.deterministic_skin_recolor()), so its silhouette can only differ from the carrier's
    by measurement noise, not by seed and not by a second diffusion pass's own drift. An earlier
    revision recolored via skin.png, a separately-diffused variant, and that pass's own registration
    error (measured up to 7px against the carrier) passed straight through to identity.png -- exactly
    the residue a downstream clothes plate, built from the carrier's own bit-exact pixels, cannot
    absorb. The 2026-08-04/05 repaint construction before that closed drift to 1-3px across several
    fixes but never reached 0, and a fixed body proportion (chest size, thigh gap) turned out to vary
    by seed regardless. See LAYERED_COSTUME_PRODUCTION_STATUS.md, 2026-08-05.

    No GPU cost beyond the two SAM calls aligned_performer() already spends and one more for the
    head mask here -- the compositing and recolor are pure PIL. Writes identity-composite.png, not
    identity.png: smooth_seam() runs on top of this and its own output is what downstream stages
    read as the identity plate."""
    composite = root / "identity-composite.png"
    preserve_path = root / "masks" / "identity-preserved-head.png"
    toned_body_path = root / "identity-toned-body.png"
    # The plate the transplant actually pastes from, which is the rebackgrounded aligned image and
    # no longer performer-aligned.png itself. identity_composite_checks() asserts bit-exactness
    # against it, so returning the wrong one measures the head against a plate it was never built
    # from -- the same class of mistake as checking identity against skin.png on 2026-08-05.
    transplant_source = root / "performer-aligned-rebackgrounded.png"
    if (composite.is_file() and preserve_path.is_file() and toned_body_path.is_file()
            and transplant_source.is_file()):
        return composite, preserve_path, toned_body_path, transplant_source
    head, head_box = sam_mask(server, aligned, HEAD_SAM_PROMPT, work, f"{SAM_PREFIX}/head")
    preserve = dilate(head, production.NECK_OVERLAP)
    save_png(preserve, preserve_path)
    carrier_image = image_from(carrier)
    # The transplant mask is geometric, so the performer's own backdrop rides along inside it
    # (measured: 17.6% of the mask, 6,406 px of 36,377). Correct that backdrop to the carrier's
    # before compositing, using her matte to get the hair's blend band right rather than only the
    # solid background. Invisible while both backdrops are the same colour, which is why this went
    # unnoticed; required the moment the preprocess stage renders her on grey to keep blue spill
    # out of her hair edge. Local matting, no pod. See production.rebackground().
    aligned_alpha_path = root / "masks" / "performer-aligned-alpha.png"
    cached_matte(aligned, aligned_alpha_path, birefnet_model, birefnet_python, remote=matte_remote)
    aligned_image = production.rebackground(image_from(aligned),
                                            image_from(aligned_alpha_path).convert("L"),
                                            image_from(aligned).getpixel((8, 8)),
                                            carrier_image.getpixel((8, 8)))
    save_png(aligned_image, transplant_source)
    performer_tone = sample_face_tone(aligned_image)
    # screen_foreground()'s screen= names the *background* colour to key out (blue, for the
    # carrier) -- unrelated to deterministic_skin_recolor()'s paint_channel=, which names the
    # *figure's own paint* colour (green) it reads as a shading map. Same word, opposite ends of
    # the same image; this collision produced a real bug the first time through -- see
    # LAYERED_COSTUME_PRODUCTION_STATUS.md, 2026-08-05.
    body_foreground = screen_foreground(carrier_image, screen)
    toned_body = production.deterministic_skin_recolor(carrier_image, body_foreground, performer_tone,
                                                        paint_channel="green")
    save_png(toned_body, toned_body_path)
    # Feather passed explicitly: it is a *default argument* of head_transplant(), bound at
    # definition time, so --head-blend-feather would otherwise move the checks that read it at
    # call time without moving the blur they are checking.
    composed = production.head_transplant(aligned_image, toned_body, preserve,
                                          production.HEAD_BLEND_FEATHER)
    save_png(composed, composite)
    print(f"  compose: performer tone {performer_tone}", flush=True)
    return composite, preserve_path, toned_body_path, transplant_source


def silhouette_checks(candidate, carrier, screen="blue"):
    """Does the result's silhouette still match the carrier's -- the one thing that has to hold for
    the clothes plate, built from that same carrier, to fit. Compared against carrier.png directly,
    not skin.png: an earlier revision compared against skin.png, which is itself an independently
    diffused recolor with up to 7px of its own drift from the carrier, and that residue passed
    straight through as if it were not there. Valid at any stage of the identity pipeline, since
    nothing after compose_identity() is allowed to touch the outer body silhouette (the seam mask
    sits at the neck/shoulders, nowhere near it)."""
    candidate_box, carrier_box = (production.silhouette_box(candidate, screen),
                                  production.silhouette_box(carrier, screen))
    drift = {"left": abs(candidate_box[0] - carrier_box[0]), "right": abs(candidate_box[2] - carrier_box[2]),
             "bottom": abs(candidate_box[3] - carrier_box[3])}
    if screen == "grey":
        outside = region_fractions(image_from(candidate),
                                   ImageOps.invert(production.screen_foreground(image_from(candidate), "grey")))
        background_check = {"name": "background_still_grey", "passed": max(outside["green"], outside["blue"]) < 0.25,
                            "detail": outside}
    else:
        background_check = {"name": "background_still_blue", "passed": production.screen_color(candidate) == "blue",
                            "detail": production.screen_color(candidate)}
    return [
        {"name": "body_matches_carrier", "passed": max(drift.values()) <= 1,
         "detail": {"body_drift_px": drift, "head_top_offset_px": candidate_box[1] - carrier_box[1]}},
        background_check,
    ]


def identity_composite_checks(aligned, carrier, toned_body, candidate, preserve_path, screen="blue"):
    """Did the head survive untouched, did the body outside the blend band survive untouched against
    the *toned* body it was actually composited from, plus silhouette_checks(). Valid only for
    compose_identity()'s direct output: it asserts bit-exactness right up to the raw blend boundary,
    which smooth_seam() is deliberately allowed to cross by SEAM_EDIT_MARGIN for working room -- see
    masked_edit_checks() for the check that's actually valid after that stage.

    core/outside are derived from the *real* alpha head_transplant() used (a Gaussian blur of
    `preserve`), not an eroded/dilated approximation of it. The first version used a fixed erosion
    margin as a proxy for "far enough from the boundary that blur has not reached it" and that proxy
    failed on this mask: her hair's outline is concave enough (individual strands, not a smooth
    oval) that a uniform erosion distance from the *outer* boundary does not bound the distance from
    every *nearby* boundary, and roughly 30% of the supposed "core" on a real run had already been
    blurred. Checking the alpha directly has no such assumption to get wrong."""
    with Image.open(preserve_path) as opened:
        preserve = opened.convert("L")
    alpha = preserve.filter(ImageFilter.GaussianBlur(production.HEAD_BLEND_FEATHER))
    core, outside = alpha.point(lambda v: 255 if v >= 255 else 0), alpha.point(lambda v: 255 if v <= 0 else 0)
    aligned_image, toned_body_image, after = image_from(aligned), image_from(toned_body), image_from(candidate)

    def region_changed(before, region):
        return sum(ImageChops.multiply(ImageChops.difference(before, after).convert("L")
                                       .point(lambda value: 255 if value else 0), region).histogram()[1:])

    head_changed = region_changed(aligned_image, core)
    body_changed = region_changed(toned_body_image, outside)
    return [
        {"name": "head_region_bit_exact", "passed": head_changed == 0, "detail": head_changed},
        {"name": "body_region_bit_exact", "passed": body_changed == 0, "detail": body_changed},
        *silhouette_checks(candidate, carrier, screen),
    ]


def describe_tone(rgb):
    """A words-not-numbers skin tone, for prompts that name a target instead of pointing at her face.

    The catalog works in tone *groups* (light/medium/dark) rather than RGB because that is what a
    text encoder can act on; this maps a sampled face tone onto the same vocabulary so a prompt
    variant and the catalog stay describable in the same terms."""
    value = max(rgb)
    if value > 200:
        return "fair"
    if value > 150:
        return "light warm"
    if value > 100:
        return "medium"
    return "deep"


def control_image(server, carrier, control_type, distort, root, work):
    """A ControlNet input derived from the carrier, so an edit can be told the target geometry.

    `distort` scales it vertically before use, and exists to make a null interpretable. On
    2026-08-04 three control runs at strength 1.0 showed no effect and "the ControlNet is inert" was
    nearly written up as fact -- but those controls described what the model would have drawn anyway,
    so a working mechanism and a dead one produce identical output. A deliberately distorted control
    that the output *follows* is the cheap proof the path is live; one that it ignores means the
    control is doing nothing, whatever the strength says."""
    if control_type == "none":
        return None
    path = root / "masks" / f"control-{control_type}{'' if distort == 1.0 else f'-d{distort:g}'}.png"
    if not path.is_file():
        graph = (production.pose_graph(upload_image(server, carrier, subfolder=SAM_PREFIX),
                                       f"{SAM_PREFIX}/pose")
                 if control_type == "openpose" else
                 production.canny_graph(upload_image(server, carrier, subfolder=SAM_PREFIX),
                                        f"{SAM_PREFIX}/canny"))
        result_dir = Path(tempfile.mkdtemp(prefix=f"{control_type}-", dir=work))
        image = Image.open(production.pick(run(server, graph, result_dir, 600), "")).convert("RGB")
        if distort != 1.0:
            squashed = image.resize((image.width, max(1, round(image.height * distort))),
                                    Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", image.size, (0, 0, 0))
            canvas.paste(squashed, (0, 0))
            image = canvas
        save_png(image, path)
    return path


def harmonize_skin(server, source, preserve_path, carrier, root, work, force, screen="blue",
                   denoise=SKIN_BLEND_DENOISE, prompt=SKIN_BLEND_PROMPT, tone=None,
                   control=None, control_type="canny", control_strength=3.0):
    """Composite first, then let a masked edit harmonise it -- the same mechanism smooth_seam()
    already uses on the neck band, applied to the whole body.

    Why this shape rather than a prompt naming a skin tone: deterministic_skin_recolor() already
    puts the *mean* exactly right (measured hue 28.6 against real skin's 28.4) and cannot produce
    variance (saturation spread 2.0 against 9.9), because `target_rgb * ratio` confines every pixel
    to one line through colour space. So the composite states the target in pixels and the edit only
    has to supply the variation that is missing. A prompt naming a tone is strictly worse: it cannot
    express *this* performer's colouring -- SKIN_PROMPT's "one fair, warm natural skin tone" is the
    same words for everyone -- whereas her transplanted head is right there in frame to match.

    Two things keep this from being the abandoned repaint:

    - `denoise` below 1.0 harmonises instead of regenerating. At 1.0 a body-shaped mask reproduces
      the 2026-08-05 construction whose chest size and thigh gap varied by seed.
    - The mask is eroded off the silhouette by SKIN_BLEND_EDGE_GUARD, so the outermost ring of the
      figure keeps its original pixels and the silhouette cannot move at all. ImageCompositeMasked
      only guarantees bit-exactness *outside* the mask; a mask that included the boundary would let
      the model shrink the body within it."""
    mask_path = root / "masks" / "identity-skin-blend-mask.png"
    output = root / "identity-harmonized.png"
    with Image.open(preserve_path) as opened:
        preserve = opened.convert("L")
    body = ImageChops.subtract(
        production.erode(screen_foreground(image_from(carrier), screen), production.SKIN_BLEND_EDGE_GUARD),
        dilate(preserve, production.NECK_OVERLAP))
    save_png(body, mask_path)
    edit(server, source, prompt.format(tone=tone) if "{tone}" in prompt else prompt,
         mask_path, None, production.seed_for("qwen2512:identity-skin-blend"),
         f"{SAM_PREFIX}/identity-skin-blend", output, work, force,
         control=control, control_type=control_type, control_strength=control_strength,
         denoise=denoise)
    return output, mask_path


# Saturation spread, measured by production.chroma_spread() over the body mask, on real plates:
# the deterministic recolor sits at 4.37 and the performer's own bare body at 10.93. The floor is
# placed between them, nearer the flat end, so the gate demands real progress without requiring a
# carrier to fully match a photograph.
#
# These numbers are specific to *this* measurement -- PIL saturation over the whole body mask. An
# earlier version of this gate used 4.0, carried over from a numpy HSV measurement taken over a
# torso *crop* where flat read 2.0 and real skin 9.9. On this scale 4.0 sits *below* the flat
# baseline, so the gate passed before the stage had run: it could not fail. Re-derive the threshold
# whenever the measurement changes; do not port it across.
SKIN_SPREAD_FLOOR = 7.0


def skin_blend_checks(before, after, mask, carrier, screen="blue"):
    """Harmonisation must add chromatic variation without moving the body.

    Spread is the whole point: the *mean* tone was already correct before this stage, so a check on
    mean would pass on exactly the flat plate this exists to fix."""
    region = image_from(mask).convert("L").point(lambda value: 255 if value > 127 else 0)
    was = production.chroma_spread(image_from(before), region)
    now = production.chroma_spread(image_from(after), region)
    gained = now["saturation_spread"] - was["saturation_spread"]
    return [
        *masked_edit_checks(before, after, mask),
        *silhouette_checks(after, carrier, screen),
        {"name": "skin_gained_chromatic_variation",
         # Both an absolute floor and a real move from where this run started: an already-varied
         # input must not coast through on the floor alone, and a flat one must not pass by
         # wobbling a fraction of a point.
         "passed": now["saturation_spread"] >= SKIN_SPREAD_FLOOR and gained >= 1.0,
         "detail": {"before": was, "after": now, "gained": round(gained, 2),
                    "floor": SKIN_SPREAD_FLOOR, "real_skin_reference": 10.93}},
    ]


def smooth_seam(server, composite, preserve_path, root, work, force):
    """A local, masked touch-up on compose_identity()'s output, smoothing the neck/shoulder join
    without touching anything else. See SEAM_PROMPT and production.blend_zone()."""
    mask_path = root / "masks" / "identity-seam-mask.png"
    identity = root / "identity.png"
    with Image.open(preserve_path) as opened:
        preserve = opened.convert("L")
    mask = production.blend_zone(preserve)
    save_png(mask, mask_path)
    edit(server, composite, SEAM_PROMPT, mask_path, None,
         production.seed_for("qwen2512:identity-seam"), f"{SAM_PREFIX}/identity-seam", identity,
         work, force)
    return identity, mask_path


def alpha_checks(carrier, source, alpha, name, aperture=None, screen="blue"):
    """The hint regression this run is meant to prove out shows up here: alpha that stops at the
    carrier silhouette means garment bulk or hair was clipped."""
    coverage = sum(alpha.point(lambda value: 255 if value > 8 else 0).histogram()[1:]) / (alpha.width * alpha.height)
    # Both the carrier and the plate are on the layer's own screen: the clothing layer compares a
    # green plate against the green carrier variant, the identity layer blue against blue.
    carrier_person = screen_foreground(image_from(carrier), screen)
    plate_person = screen_foreground(image_from(source), screen)
    if aperture is not None:
        # The head/neck opening is a deliberate hole in a clothed-body plate, not a defect.
        plate_person = ImageChops.subtract(plate_person, aperture)
    beyond_carrier = sum(ImageChops.subtract(ImageChops.multiply(alpha, plate_person), carrier_person).histogram()[1:])
    filled = ImageOps.invert(alpha.point(lambda value: 255 if value > 8 else 0))
    holes = sum(ImageChops.multiply(filled, plate_person).histogram()[1:])
    return [
        {"name": f"{name}_coverage_plausible", "passed": 0.05 < coverage < 0.70, "detail": round(coverage, 4)},
        {"name": f"{name}_extends_past_carrier", "passed": beyond_carrier > 0, "detail": beyond_carrier},
        {"name": f"{name}_no_interior_holes", "passed": holes < 0.02 * alpha.width * alpha.height, "detail": holes},
    ]


def report(root, stage, checks):
    """Print one line per check, persist to checks.json, and refuse to continue on failure."""
    path = root / "checks.json"
    stored = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    stored[stage] = checks
    production.atomic_json(path, stored)
    width = max(len(check["name"]) for check in checks)
    for check in checks:
        print(f"  {'PASS' if check['passed'] else 'FAIL'}  {check['name']:<{width}}  {check['detail']}", flush=True)
    failed = [check["name"] for check in checks if not check["passed"]]
    if failed:
        raise RuntimeError(f"{stage} checks failed: {', '.join(failed)} (see {path})")
    print(f"  {stage}: all {len(checks)} checks passed", flush=True)


def remote_bootstrap(ssh, ssh_port, ssh_target):
    """Copy pod_bootstrap.sh up and run it, so a migrated pod repairs itself before the checks
    below judge it. Shipping the repo's copy every time rather than trusting the one on the volume
    keeps a single source of truth and heals a pod whose /workspace copy is stale.

    Returns a record() triple. This is the whole answer to "how does CorridorKey survive a new
    pod": preflight runs it, it is idempotent, and it exits non-zero if the venv still cannot
    import torch."""
    script = Path(__file__).with_name(BOOTSTRAP_SCRIPT)
    if not script.is_file():
        return "pod_bootstrap", False, f"missing {script}"
    try:
        copy = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                               "-P", str(ssh_port), str(script), f"{ssh_target}:{REMOTE_BOOTSTRAP}"],
                              timeout=120, capture_output=True, text=True)
        if copy.returncode != 0:
            return "pod_bootstrap", False, copy.stderr.strip() or "scp failed"
        # Generous: a fresh pod installs uv and rsync and imports torch off a network volume.
        result = subprocess.run([*ssh, f"sh {REMOTE_BOOTSTRAP}"], timeout=900,
                                capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as error:                 # noqa: BLE001
        return "pod_bootstrap", False, str(error)
    lines = [line for line in (result.stdout + result.stderr).splitlines() if line.strip()]
    return "pod_bootstrap", result.returncode == 0, lines


def preflight(server, ssh_target, ssh_port, performer, edit_model, corridor_root,
              clothes_mode="qwen", extract_mode="corridorkey", matte_remotely=False,
              carrier_family="qwen"):
    """Fail on a misconfigured instance before any GPU time is spent. Without this the SSH and
    CorridorKey settings are only exercised after four Qwen generations have already run.

    Mode-aware: the grey/VITON path needs the CatVTON node and has no use for CorridorKey or
    rsync (matting is local), so those checks are skipped there instead of failing a healthy pod."""
    checks = []

    def record(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    record("performer_reference_present", performer.is_file(), str(performer))
    try:
        # Short timeout: a wrong address should fail fast, not stall the preflight.
        record("sam3_detect_exposed", "SAM3_Detect" in api(server, "/object_info/SAM3_Detect", timeout=15),
               "SAM3_Detect")
    except Exception as error:                                      # noqa: BLE001 - report, don't crash
        record("sam3_detect_exposed", False, str(error))
    try:
        info = api(server, "/object_info/UNETLoader", timeout=15)
        available = info["UNETLoader"]["input"]["required"]["unet_name"][0]
        record("edit_model_present", edit_model in available,
               {"wanted": edit_model, "available": available})
    except Exception as error:                                      # noqa: BLE001
        record("edit_model_present", False, str(error))
    if carrier_family == "flux2":
        # Probe rather than assume. FLUX.2 needs a CLIPLoader type this ComfyUI may not know: its
        # text encoder is Mistral 3 Small, not FLUX.1's T5+CLIP, and support arrived in *some*
        # ComfyUI version -- this pod runs 0.30.2 and may predate it. Reading the enum also hands us
        # the real type string instead of a guess. Seconds, and it happens before a ~20 GB download
        # into an incompatible runtime.
        try:
            info = api(server, "/object_info/CLIPLoader", timeout=15)
            types = list(info["CLIPLoader"]["input"]["required"]["type"][0])
            match = next((t for t in types if "flux2" in t.lower() or "mistral" in t.lower()), None)
            record("flux2_clip_type_supported", match is not None,
                   {"found": match, "available": types})
        except Exception as error:                                  # noqa: BLE001
            record("flux2_clip_type_supported", False, str(error))
        try:
            info = api(server, "/object_info/UNETLoader", timeout=15)
            available = info["UNETLoader"]["input"]["required"]["unet_name"][0]
            record("flux2_model_present", production.FLUX2_MODEL in available,
                   {"wanted": production.FLUX2_MODEL, "available": available})
        except Exception as error:                                  # noqa: BLE001
            record("flux2_model_present", False, str(error))
    if clothes_mode == "viton":
        try:
            node = api(server, f"/object_info/{CATVTON_NODE}", timeout=15)
            present = CATVTON_NODE in node and "image" in node[CATVTON_NODE]["input"]["required"]
            record("catvton_node_exposed", present, CATVTON_NODE)
        except Exception as error:                                  # noqa: BLE001
            record("catvton_node_exposed", False, str(error))

    ssh = ["ssh", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-p", str(ssh_port), ssh_target]
    try:
        reachable = subprocess.run([*ssh, "true"], timeout=45).returncode == 0
    except (OSError, subprocess.SubprocessError) as error:
        reachable = False
        record("ssh_reachable", False, str(error))
    else:
        record("ssh_reachable", reachable, f"{ssh_target}:{ssh_port}")
    if reachable:
        if clothes_mode == "viton":
            # The bootstrap installs CatVTON only when asked: touch the flag so a one-time
            # multi-GB download does not run for the chroma recipe's preflight.
            subprocess.run([*ssh, "mkdir -p /workspace/runpod-slim && touch /workspace/runpod-slim/install-catvton"],
                           timeout=30, check=False)
        if carrier_family in ("flux1", "flux2"):
            # ~17 GB, so it must not download for a Qwen session. Touched before remote_bootstrap()
            # so step 9 fetches it inside the same preflight that reports on it.
            subprocess.run([*ssh, "mkdir -p /workspace/runpod-slim && touch /workspace/runpod-slim/install-{family}".format(family=carrier_family)],
                           timeout=30, check=False)
        if matte_remotely:
            # Bootstrap step 8 builds the GPU matting venv only when this flag exists, and step 8
            # is what makes remote_matte() possible at all -- so the flag must be touched whenever
            # matting will run on the pod, not merely when --matte-upscale is set. Touched before
            # remote_bootstrap() below so the venv is built in the same preflight that checks it.
            subprocess.run([*ssh, "mkdir -p /workspace/runpod-slim && touch /workspace/runpod-slim/install-matting"],
                           timeout=30, check=False)
        record(*remote_bootstrap(ssh, ssh_port, ssh_target))
        if extract_mode == "corridorkey":
            probe = f"test -x {corridor_root}/.venv/bin/python && test -d {corridor_root}/CorridorKeyModule/checkpoints"
            record("corridorkey_installed", subprocess.run([*ssh, probe], timeout=60).returncode == 0, corridor_root)
            # standalone_alpha() stages and retrieves over rsync; a pod without it fails only after
            # every generation has already run.
            record("remote_rsync_present",
                   subprocess.run([*ssh, "command -v rsync"], timeout=60).returncode == 0, "rsync on the pod")
    elif extract_mode == "corridorkey":
        record("corridorkey_installed", False, "skipped: ssh unreachable")
        record("remote_rsync_present", False, "skipped: ssh unreachable")
    return checks


def key_aperture(image, screen):
    """The clothes plate keeps a painted head/neck aperture in whichever key colour the screen is
    not, so CorridorKey would keep it as opaque foreground — painting a coloured head over the skin
    plate in the composite. Flattening it to the key colour first
    (production.normalized_key_input's aperture argument) makes CorridorKey drop it. The aperture is
    simply the plate's paint-dominant pixels, which is more precise than a SAM mask."""
    red, green, blue = image.convert("RGB").split()
    paint, others = (blue, (red, green)) if screen == "green" else (green, (red, blue))
    dominance = ImageChops.subtract(paint, ImageChops.lighter(*others))
    region = dominance.point(lambda value: 255 if value >= 12 else 0)
    # Close pin-holes, then take back the dilation so the garment edge is not eaten.
    return dilate(region, 9).filter(ImageFilter.MinFilter(5))


def carrier_for_screen(carrier, screen, root):
    """Return a carrier whose screen is `screen`, swapping G/B when it is not already.

    Derived, never generated a second time. production.carrier_variant() uses this same
    swap-green-blue-channels-v1 processor, and the reason is correctness rather than cost: the skin
    and identity plates come from the blue carrier and the composite stacks all three layers by
    exact pixel coordinates. A separately generated green carrier would be a different pose, and
    nothing downstream would line up. The swap is its own inverse and bit-identical in geometry."""
    if production.screen_color(carrier) == screen:
        return carrier
    variant = root / f"carrier-{screen}.png"
    if not variant.is_file():
        with Image.open(carrier) as opened:
            red, green, blue = opened.convert("RGB").split()
        save_png(Image.merge("RGB", (red, blue, green)), variant)
    if production.screen_color(variant) != screen:
        raise RuntimeError(f"{variant} did not come out {screen}; carrier is not a clean two-colour plate")
    return variant


def extract(server, source, raw_hint, hint, screen, layer_id, ssh_target, ssh_port, root, aperture=None):
    alpha = production.standalone_alpha(source, raw_hint, hint, screen, layer_id, ssh_target, ssh_port, aperture)
    save_png(alpha, root / f"{layer_id}-alpha.png")
    return alpha


def self_test():
    probe = Image.new("RGB", (32, 32), (20, 80, 220))
    ImageDraw.Draw(probe).rectangle((12, 12, 19, 19), fill=(20, 140, 40))
    hint = screen_foreground_hint(probe)
    assert hint.getpixel((16, 16)) > hint.getpixel((2, 2))

    masked = production.edit_graph("carrier.png", SKIN_PROMPT, 1, "test", mask="mask.png")
    assert masked["13"]["inputs"]["latent_image"] == ["19", 0]
    assert masked["20"]["inputs"]["mask"] == ["18", 0]
    assert masked["21"]["inputs"]["filename_prefix"] == "test-masked"

    # mask=None must skip the ImageCompositeMasked paste boundary entirely, not just an unused input.
    maskfree = production.edit_graph("carrier.png", SKIN_PROMPT, 1, "test")
    assert maskfree["13"]["inputs"]["latent_image"] == ["12", 0]
    assert not any(key in maskfree for key in ("17", "18", "19", "20", "21"))
    assert maskfree["15"]["inputs"]["filename_prefix"] == "test-raw"

    # The bug this revision fixed lives in edit(), not edit_graph(): a mask-free run publishes no
    # "-masked" output, so selecting one unconditionally raises. Also guards "-raw" from matching
    # a "-masked" filename.
    maskfree_result = {"images": [{"path": "/tmp/skin-tone-raw_00001_.png"}]}
    assert production.pick(maskfree_result, "-raw").name == "skin-tone-raw_00001_.png"
    try:
        production.pick(maskfree_result, "-masked")
    except ValueError:
        pass
    else:
        raise AssertionError("a mask-free edit must not offer a -masked output")
    masked_result = {"images": [{"path": "/tmp/identity-raw_00001_.png"}, {"path": "/tmp/identity-masked_00001_.png"}]}
    assert production.pick(masked_result, "-raw").name == "identity-raw_00001_.png"
    assert production.pick(masked_result, "-masked").name == "identity-masked_00001_.png"

    # dilate() must be a drop-in for MaxFilter, not an approximation of it.
    probe_mask = Image.new("L", (64, 64), 0)
    ImageDraw.Draw(probe_mask).rectangle((28, 28, 35, 35), fill=255)
    assert list(dilate(probe_mask, 15).get_flattened_data()) == \
        list(probe_mask.filter(ImageFilter.MaxFilter(15)).get_flattened_data())

    # The hint must come from the plate being keyed. A carrier-derived hint misses garment bulk at
    # any dilation, because the carrier is unclothed.
    carrier_probe = Image.new("RGB", (200, 300), (20, 80, 220))
    ImageDraw.Draw(carrier_probe).rectangle((85, 40, 115, 260), fill=(30, 200, 60))
    clothed_probe = carrier_probe.copy()
    ImageDraw.Draw(clothed_probe).polygon([(100, 150), (40, 260), (160, 260)], fill=(142, 69, 133))
    assert screen_foreground_hint(carrier_probe).getpixel((55, 250)) < 8
    assert screen_foreground_hint(clothed_probe).getpixel((55, 250)) > 200

    # Calls the real envelope formula. The envelopes must overlap through the shoulder junction and
    # the clothes envelope must reach past the body silhouette; a butt joint leaves bare shoulders
    # exactly where CLOTHES_PROMPT asks for a collar.
    person_region = Image.new("L", (400, 600), 0)
    ImageDraw.Draw(person_region).rectangle((150, 40, 250, 560), fill=255)
    broad_head = Image.new("L", (400, 600), 0)
    ImageDraw.Draw(broad_head).rectangle((150, 40, 250, 200), fill=255)
    narrow_head = Image.new("L", (400, 600), 0)
    ImageDraw.Draw(narrow_head).rectangle((165, 40, 235, 130), fill=255)
    head_mask, clothes_mask = envelope_masks(person_region, broad_head, narrow_head)
    assert ImageChops.multiply(clothes_mask, head_mask).getbbox() is not None, "envelopes must not butt together"
    assert head_mask.getpixel((200, 190)) and clothes_mask.getpixel((200, 190)), "shoulder junction must be in both"
    # The neck, just below the stop region's chin line, must be paintable or no collar can exist --
    # the defect that survived every prompt and garment-reference change through 2026-08-08.
    assert clothes_mask.getpixel((200, 140)), "the neck must be paintable so a collar can exist"
    assert clothes_mask.getpixel((110, 400)) and not person_region.getpixel((110, 400)), "garment bulk must clear the body"
    # The dilated person envelope leaves a halo around the skull; the clothing edit must not own it,
    # or CLOTHES_PROMPT's forbidden bonnet becomes paintable.
    assert not clothes_mask.getpixel((200, 20)), "skull halo must stay outside the clothes envelope"
    assert not clothes_mask.getpixel((130, 80)), "the sides of the head must stay outside it too"

    with tempfile.TemporaryDirectory(prefix="poc-self-test-") as directory:
        root = Path(directory)
        carrier_path = root / "carrier.png"
        save_png(carrier_probe, carrier_path)

        # The review overlay is the gate's only artifact; the carrier has to survive the tint.
        overlay = envelope_overlay(carrier_path, broad_head.resize(carrier_probe.size),
                                   narrow_head.resize(carrier_probe.size))
        # Both samples sit inside the identity envelope, either side of the carrier's body edge.
        assert overlay.getpixel((78, 50)) != overlay.getpixel((100, 50)), \
            "envelope-review.png must show the carrier through the tint"
        assert overlay.getpixel((100, 50)) != carrier_probe.getpixel((100, 50)), \
            "the envelope region must actually be tinted"

        # An acceptance only covers the exact carrier that was reviewed.
        masks = root / "masks"
        save_png(broad_head, masks / "identity-head-mask.png")
        save_png(narrow_head, masks / "clothes-body-mask.png")
        (masks / "envelope-status.json").write_text(json.dumps({
            "status": ENVELOPE_ACCEPTED, "source_sha256": "stale",
        }) + "\n", encoding="utf-8")
        try:
            load_accepted_envelope(root, carrier_path)
        except RuntimeError as error:
            assert "different carrier" in str(error)
        else:
            raise AssertionError("an envelope accepted against another carrier must be refused")

        # despilled_plate(): an enclosed weak-dominance hole surrounded by solid figure gets
        # corrected; background -- even right beside the figure's true edge, which a
        # dominance-strength-only version of this once wrongly flagged near hair -- does not.
        plate = Image.new("RGB", (100, 100), (0, 200, 0))
        draw = ImageDraw.Draw(plate)
        draw.rectangle((20, 20, 79, 79), fill=(30, 25, 28))
        draw.rectangle((45, 45, 54, 54), fill=(20, 80, 50))
        plate_path = root / "spill-plate.png"
        save_png(plate, plate_path)
        despilled_out = despilled_plate(plate_path, "green", root / "spill-out.png", False, radius=24)
        result = image_from(despilled_out)
        assert result.getpixel((49, 49)) == (20, 50, 50), "the enclosed hole's colour must be corrected"
        assert result.getpixel((15, 50)) == (0, 200, 0), \
            "background beside the figure's true edge must be untouched"
        before = despilled_out.read_bytes()
        despilled_plate(plate_path, "green", despilled_out, False, radius=24)
        assert despilled_out.read_bytes() == before, "an existing output must not be recomputed"

        # Config resolution: placeholders count as unset, typos are refused, and the config must
        # beat a stale exported variable rather than be silently shadowed by it.
        config_path = root / "instance.json"
        init_config(config_path)
        assert config_path.stat().st_mode & 0o777 == 0o600, "a file holding a token must not be readable"
        assert "server" not in load_config(config_path), "an untouched placeholder must count as unset"
        assert load_config(config_path)["edit_model"] == production.EDIT_MODEL
        config_path.write_text(json.dumps({**CONFIG_TEMPLATE, "typo": 1}), encoding="utf-8")
        try:
            load_config(config_path)
        except RuntimeError as error:
            assert "typo" in str(error)
        else:
            raise AssertionError("an unknown config key must be refused")
        config_path.write_text(json.dumps({"server": "http://from-config"}), encoding="utf-8")
        sources = {}
        resolve = resolver(load_config(config_path), config_path, sources)
        os.environ["COVER_STORY_SELF_TEST_SERVER"] = "http://from-env"
        assert resolve("server", None, "COVER_STORY_SELF_TEST_SERVER") == "http://from-config"
        assert resolve("server", "http://from-flag", "COVER_STORY_SELF_TEST_SERVER") == "http://from-flag"
        assert sources["server"] == "--server"
        assert resolve("ssh_target", None, "COVER_STORY_SELF_TEST_SERVER") == "http://from-env"
        del os.environ["COVER_STORY_SELF_TEST_SERVER"]
        assert redact("http://h:1/?token=abc123") == "http://h:1/?token=REDACTED"

        # Stage checks must actually separate a good plate from each failure they name.
        good_carrier = Image.new("RGB", (400, 600), (18, 60, 210))
        ImageDraw.Draw(good_carrier).rectangle((150, 40, 250, 580), fill=(30, 200, 60))
        save_png(good_carrier, root / "good-carrier.png")
        assert all(check["passed"] for check in carrier_checks(root / "good-carrier.png"))

        cropped = good_carrier.copy()
        ImageDraw.Draw(cropped).rectangle((150, 450, 250, 580), fill=(18, 60, 210))  # feet cut off
        save_png(cropped, root / "cropped.png")
        assert not next(c for c in carrier_checks(root / "cropped.png") if c["name"] == "feet_visible")["passed"]

        haired = good_carrier.copy()
        ImageDraw.Draw(haired).rectangle((150, 40, 250, 95), fill=(12, 10, 14))  # dark crown
        save_png(haired, root / "haired.png")
        assert not next(c for c in carrier_checks(root / "haired.png") if c["name"] == "carrier_is_bald")["passed"]

        # A preprocessed reference must arrive as natural skin on blue, not carrier-green.
        good_pre = Image.new("RGB", (400, 600), (18, 60, 210))
        ImageDraw.Draw(good_pre).rectangle((150, 40, 250, 580), fill=(208, 168, 140))
        save_png(good_pre, root / "preprocessed.png")
        assert all(c["passed"] for c in preprocess_checks(root / "preprocessed.png", root / "good-carrier.png"))
        assert not next(c for c in preprocess_checks(root / "good-carrier.png", root / "good-carrier.png")
                        if c["name"] == "preprocess_figure_not_green")["passed"], \
            "a green-painted figure must not pass as a preprocessed reference"
        # A two-reference edit that collapses onto image 2 returns a copy of it; that must fail
        # even though every geometric property of the copy is perfect.
        assert not next(c for c in preprocess_checks(root / "preprocessed.png", root / "preprocessed.png")
                        if c["name"] == "preprocess_is_not_a_copy_of_reference")["passed"], \
            "a copy of the reference must not pass as a preprocessed result"

        recolored = good_carrier.copy()
        ImageDraw.Draw(recolored).rectangle((150, 40, 250, 580), fill=(208, 168, 140))
        save_png(recolored, root / "skin.png")
        assert all(check["passed"] for check in skin_checks(root / "good-carrier.png", root / "skin.png"))
        # A recolor that left the green paint behind must not pass.
        assert not next(c for c in skin_checks(root / "good-carrier.png", root / "good-carrier.png")
                        if c["name"] == "green_body_paint_removed")["passed"]

        # outside_mask_changed is exact, so a no-op edit and an out-of-mask edit are both caught.
        band = Image.new("L", (400, 600), 0)
        ImageDraw.Draw(band).rectangle((140, 300, 260, 500), fill=255)
        save_png(band, root / "band.png")
        edited = good_carrier.copy()
        ImageDraw.Draw(edited).rectangle((150, 320, 250, 480), fill=(120, 40, 110))
        save_png(edited, root / "edited.png")
        assert all(c["passed"] for c in masked_edit_checks(root / "good-carrier.png", root / "edited.png", root / "band.png"))
        assert not next(c for c in masked_edit_checks(root / "good-carrier.png", root / "good-carrier.png", root / "band.png")
                        if c["name"] == "inside_mask_changed")["passed"]
        spilled = edited.copy()
        ImageDraw.Draw(spilled).rectangle((150, 100, 250, 200), fill=(120, 40, 110))  # outside the band
        save_png(spilled, root / "spilled.png")
        assert not next(c for c in masked_edit_checks(root / "good-carrier.png", root / "spilled.png", root / "band.png")
                        if c["name"] == "outside_mask_unchanged")["passed"]

        # The clipping regression this run exists to prove out: alpha stopping at the carrier edge.
        plate = good_carrier.copy()
        ImageDraw.Draw(plate).polygon([(200, 300), (100, 580), (300, 580)], fill=(142, 69, 133))
        save_png(plate, root / "plate.png")
        full = screen_foreground(plate)
        clipped_alpha = ImageChops.multiply(full, screen_foreground(good_carrier))
        assert all(c["passed"] for c in alpha_checks(root / "good-carrier.png", root / "plate.png", full, "clothes"))
        assert not next(c for c in alpha_checks(root / "good-carrier.png", root / "plate.png", clipped_alpha, "clothes")
                        if c["name"] == "clothes_extends_past_carrier")["passed"]

        # The carrier variant is a G/B swap, so the screen and the body paint must trade places
        # and the geometry must not move at all -- the composite stacks layers by exact pixel.
        assert production.screen_color(root / "good-carrier.png") == "blue"
        green_carrier = carrier_for_screen(root / "good-carrier.png", "green", root)
        assert green_carrier.name == "carrier-green.png"
        assert production.screen_color(green_carrier) == "green"
        before, after = image_from(root / "good-carrier.png"), image_from(green_carrier)
        assert before.size == after.size
        red, green, blue = before.split()
        assert list(after.split()[1].get_flattened_data()) == list(blue.get_flattened_data())
        assert list(after.split()[2].get_flattened_data()) == list(green.get_flattened_data())
        # Swapping twice is the identity, which is what makes one generation serve both keys.
        assert carrier_for_screen(green_carrier, "green", root) == green_carrier
        # The aperture follows the screen: it must find the paint, not the background.
        assert key_aperture(after, "green").getbbox() == key_aperture(before, "blue").getbbox()

        # screen_foreground must follow the screen too. Reading blue dominance on a green plate
        # classifies the green background as *figure*: it inflated clothes_no_interior_holes to
        # 778670 and handed CorridorKey a hint covering the whole canvas, silently defeating the
        # plate-derived hint this revision exists for.
        green_plate = image_from(green_carrier)
        ImageDraw.Draw(green_plate).rectangle((150, 40, 250, 580), fill=(142, 69, 133))
        save_png(green_plate, root / "green-plate.png")
        canvas = green_plate.width * green_plate.height
        assert sum(screen_foreground(green_plate, "green").histogram()[1:]) < 0.35 * canvas
        assert sum(screen_foreground(green_plate, "blue").histogram()[1:]) > 0.9 * canvas
        # And the check built on it must agree: holes are near zero with the right screen and
        # near the whole canvas with the wrong one.
        plate_alpha = screen_foreground(green_plate, "green")
        right = next(c for c in alpha_checks(green_carrier, root / "green-plate.png", plate_alpha,
                                             "clothes", screen="green") if "holes" in c["name"])
        wrong = next(c for c in alpha_checks(green_carrier, root / "green-plate.png", plate_alpha,
                                             "clothes") if "holes" in c["name"])
        assert right["passed"] and not wrong["passed"], (right, wrong)

        # no_paint_remnant_in_edit has to follow the screen too. Build a plate that is a good edit
        # on either key -- garment over the body, screen everywhere else -- and assert it passes
        # both ways round. Reading the fixed colour instead of the paint fails the green case.
        for screen, painted in (("blue", root / "good-carrier.png"), ("green", green_carrier)):
            dressed = image_from(painted)
            ImageDraw.Draw(dressed).rectangle((150, 40, 250, 580), fill=(142, 69, 133))
            save_png(dressed, root / f"dressed-{screen}.png")
            remnant = next(c for c in masked_edit_checks(painted, root / f"dressed-{screen}.png",
                                                         root / "band.png", screen=screen)
                           if c["name"] == "no_paint_remnant_in_edit")
            assert remnant["passed"] and remnant["detail"]["paint"] == APERTURE_COLOR[screen], remnant
        # And it must still catch paint the garment failed to cover: the bare green carrier still
        # has its blue body paint fully exposed, which is the defect this check exists for.
        assert not next(c for c in masked_edit_checks(green_carrier, green_carrier,
                                                      root / "band.png", screen="green")
                        if c["name"] == "no_paint_remnant_in_edit")["passed"]

    # --- grey-carrier / VITON path (VITON_CLOTHING_POC_HANDOVER.md) ---

    # screen_foreground("grey") keys on distance from the corner colour, and only there.
    grey_carrier_img = Image.new("RGB", (200, 300), (118, 118, 120))
    ImageDraw.Draw(grey_carrier_img).rectangle((60, 30, 140, 290), fill=(205, 205, 208))
    grey_fg = production.screen_foreground(grey_carrier_img, "grey")
    assert grey_fg.getbbox() == (60, 30, 141, 291), grey_fg.getbbox()  # rectangle() is inclusive
    assert grey_fg.getpixel((10, 10)) == 0 and grey_fg.getpixel((100, 100)) == 255
    # The chroma screens must be unaffected by the grey branch.
    blue_probe = Image.new("RGB", (32, 32), (20, 80, 220))
    ImageDraw.Draw(blue_probe).rectangle((12, 12, 19, 19), fill=(20, 140, 40))
    assert production.screen_foreground(blue_probe).getpixel((16, 16)) == 255

    # luminance recolor (paint_channel=None): figure gets target tone scaled by its own luma,
    # background untouched.
    recolor = production.deterministic_skin_recolor(grey_carrier_img, grey_fg, (212, 176, 150),
                                                    paint_channel=None)
    assert recolor.getpixel((10, 10)) == grey_carrier_img.getpixel((10, 10))  # bg untouched
    r, g, b = recolor.getpixel((100, 150))
    assert r > 150 and r > g > b, (r, g, b)  # skin-toned, red-dominant (saturates on the toy canvas)

    # despill must be a no-op for grey screens (no chroma spill exists to remove).
    assert production.scoped_despill(grey_carrier_img, "grey").tobytes() == \
        grey_carrier_img.convert("RGB").tobytes()

    # grey_carrier_checks on a synthetic grey carrier.
    save_png(grey_carrier_img, root / "grey-carrier.png")
    grey_checks = {c["name"]: c["passed"] for c in grey_carrier_checks(root / "grey-carrier.png")}
    assert grey_checks["figure_coverage_plausible"] and grey_checks["feet_visible"] and \
        grey_checks["background_is_grey"] and grey_checks["figure_not_chroma"], grey_checks

    # viton_graph: exact node classes and wiring for the installed CatVTON wrapper.
    graph = viton_graph("person.png", "garment.png", "mask.png", 7, "test")
    assert graph["1"]["class_type"] == "LoadImage" and graph["1"]["inputs"]["image"] == "person.png"
    assert graph["2"]["inputs"]["image"] == "garment.png"
    assert graph["3"]["inputs"]["image"] == "mask.png"
    assert graph["4"]["class_type"] == "ImageToMask" and graph["4"]["inputs"]["channel"] == "red"
    assert graph["5"]["class_type"] == CATVTON_NODE
    assert graph["5"]["inputs"]["image"] == ["1", 0]
    assert graph["5"]["inputs"]["mask"] == ["4", 0]
    assert graph["5"]["inputs"]["refer_image"] == ["2", 0]
    assert graph["5"]["inputs"]["mask_grow"] == 0 and graph["5"]["inputs"]["seed"] == 7
    assert graph["6"]["inputs"]["filename_prefix"] == "test-viton"

    # birefnet extraction wiring: the standalone script and model must exist at the defaults, so a
    # fresh pod run fails at configuration time, not after the GPU stages.
    assert DEFAULT_BIREFNET_MODEL.is_file(), DEFAULT_BIREFNET_MODEL
    assert DEFAULT_BIREFNET_PYTHON.is_file(), DEFAULT_BIREFNET_PYTHON
    assert (TOOL_ROOT / "birefnet_extract.py").is_file()

    # --- garment-ref clothes mode ---

    # Grey-mode masked_edit_checks must not touch APERTURE_COLOR (no paint on a grey carrier).
    dressed = image_from(root / "grey-carrier.png").copy()
    ImageDraw.Draw(dressed).rectangle((70, 40, 130, 200), fill=(142, 69, 133))
    save_png(dressed, root / "grey-dressed.png")
    grey_mask = Image.new("L", grey_carrier_img.size, 0)
    ImageDraw.Draw(grey_mask).rectangle((60, 30, 140, 210), fill=255)
    save_png(grey_mask, root / "grey-mask.png")
    grey_edit_checks = {c["name"]: c["passed"] for c in
                        masked_edit_checks(root / "grey-carrier.png", root / "grey-dressed.png",
                                           root / "grey-mask.png", screen="grey")}
    assert grey_edit_checks["inside_mask_changed"]
    assert grey_edit_checks["outside_mask_unchanged"]
    assert "no_paint_remnant_in_edit" not in grey_edit_checks  # grey has no paint to measure
    # The chroma path still measures paint residue against the key colour.
    assert APERTURE_COLOR["green"] == "blue" and APERTURE_COLOR["blue"] == "green"
    assert "{aperture}" in GARMENT_REF_PROMPT and "image 2" in GARMENT_REF_PROMPT
    assert "image 2" in GREY_GARMENT_REF_PROMPT and "{aperture}" not in GREY_GARMENT_REF_PROMPT
    # Every clothes prompt must name the outfit as well as reference it. Dropping the description
    # from the garment-ref prompt is the confound that made the 2026-08-07 run untestable, and the
    # four features it lost are exactly the four named here.
    for template in (CLOTHES_PROMPT, GARMENT_REF_PROMPT, GREY_GARMENT_REF_PROMPT):
        assert "{description}" in template, "a clothes prompt must carry the outfit description"
    for feature in ("high collar", "gloves", "full skirt", "shoes"):
        assert feature in GARMENT_DESCRIPTION, f"{feature} must survive into the description"
    rendered = GARMENT_REF_PROMPT.format(aperture="blue", screen="green",
                                         description=GARMENT_DESCRIPTION)
    assert "high collar" in rendered and "image 2" in rendered, \
        "the garment-ref prompt must carry both the image reference and the text description"

    # Variant dials. apply_variant_dials() writes module attributes, so the test restores them.
    class _Dials:
        identity_dilation = support_dilation = clothes_stop_dilation = None
        neck_overlap = 40
        head_blend_feather = 12
        skin_blend_edge_guard = None
    before = (production.NECK_OVERLAP, production.HEAD_BLEND_FEATHER)
    try:
        moved = apply_variant_dials(_Dials())
        assert production.NECK_OVERLAP == 40 and production.HEAD_BLEND_FEATHER == 12
        assert "NECK_OVERLAP" in moved and "IDENTITY_DILATION" not in moved, \
            "only dials actually given a value may move"
        # The trap this exists for: head_transplant()/blend_zone() bind HEAD_BLEND_FEATHER as a
        # *default argument* at definition time, so moving the module attribute alone changes the
        # checks that read it at call time without changing the blur they check. compose_identity()
        # must therefore pass it explicitly.
        head = Image.new("L", (40, 40))
        ImageDraw.Draw(head).rectangle((10, 10, 29, 29), fill=255)
        wide = production.head_transplant(Image.new("RGB", (40, 40), (255, 0, 0)),
                                          Image.new("RGB", (40, 40), (0, 0, 255)), head,
                                          production.HEAD_BLEND_FEATHER)
        narrow = production.head_transplant(Image.new("RGB", (40, 40), (255, 0, 0)),
                                            Image.new("RGB", (40, 40), (0, 0, 255)), head, 1)
        assert wide.getpixel((10, 20)) != narrow.getpixel((10, 20)), \
            "feather must actually reach head_transplant, not just the module attribute"
    finally:
        production.NECK_OVERLAP, production.HEAD_BLEND_FEATHER = before

    # Prompt variants must keep the placeholders their call sites format.
    for name, template in SKIN_BLEND_PROMPTS.items():
        rendered = template.format(tone="light warm") if "{tone}" in template else template
        assert "{" not in rendered, f"skin prompt {name} has an unfilled placeholder"
        assert "outside the masked area" in rendered, f"skin prompt {name} must protect the mask"
    assert "variation" in SKIN_BLEND_PROMPTS["variation"], "the variation prompt must ask for variation"
    assert "even out" in SKIN_BLEND_PROMPTS["blend"], "the default still asks for uniformity, on purpose"
    for name, clause in CLOTHES_COVERAGE_CLAUSES.items():
        rendered = GARMENT_REF_PROMPT.format(aperture="blue", screen="green",
                                             description=GARMENT_DESCRIPTION) + clause
        assert "{" not in rendered, f"coverage clause {name} broke the template"
    assert CLOTHES_COVERAGE_CLAUSES["default"] == "", "the default must change nothing"
    assert describe_tone((235, 220, 210)) == "fair" and describe_tone((80, 60, 50)) == "deep"

    # carrier_graph(): the two generators are architecturally different, not a checkpoint swap.
    # FLUX dev is guidance-distilled, so CFG must be 1.0 and steering comes from FluxGuidance --
    # running it at Qwen's CFG 4 would produce a burnt image and look like the model was at fault.
    # FLUX.2: three loaders because the repack ships three files, and its text encoder is Mistral 3
    # Small rather than T5+CLIP -- so the CLIPLoader type is whatever preflight read from the pod's
    # own enum, never a hardcoded guess.
    flux2 = production.carrier_graph("flux2", "a woman", 7, "pre", (832, 1248), clip_type="probed")
    assert next(n for n in flux2.values()
                if n["class_type"] == "CLIPLoader")["inputs"]["type"] == "probed", \
        "the probed CLIPLoader type must reach the graph"
    assert next(n for n in flux2.values()
                if n["class_type"] == "UNETLoader")["inputs"]["unet_name"] == production.FLUX2_MODEL
    assert next(n for n in flux2.values() if n["class_type"] == "KSampler")["inputs"]["cfg"] == 1.0
    assert {"UNETLoader", "CLIPLoader", "VAELoader"} <= {n["class_type"] for n in flux2.values()}, \
        "FLUX.2 loads model, text encoder and VAE separately"

    flux = production.carrier_graph("flux1", "a woman", 7, "pre", (832, 1248))
    qwen = production.carrier_graph("qwen", "a woman", 7, "pre", (832, 1248))
    flux_sampler = next(n for n in flux.values() if n["class_type"] == "KSampler")
    qwen_sampler = next(n for n in qwen.values() if n["class_type"] == "KSampler")
    assert flux_sampler["inputs"]["cfg"] == 1.0, "FLUX dev is guidance-distilled; CFG must be 1.0"
    assert qwen_sampler["inputs"]["cfg"] == 4.0, "the Qwen path must be untouched"
    assert any(n["class_type"] == "FluxGuidance" for n in flux.values()), "FLUX needs FluxGuidance"
    assert any(n["class_type"] == "CheckpointLoaderSimple" for n in flux.values()), \
        "the all-in-one checkpoint supplies model, CLIP and VAE from one file"
    assert next(n for n in flux.values() if n["class_type"] == "EmptySD3LatentImage")["inputs"]["width"] == 832
    for graph in (flux, qwen):
        assert sum(n["class_type"] == "SaveImage" for n in graph.values()) >= 1, "output must be saved"

    # remote_matte_plan(): the remote path cannot be exercised without a pod, but everything except
    # the execution can be, and a malformed command found on the pod costs GPU time -- preflight()
    # shipped a NameError on 2026-08-08 that only a real pod call caught.
    plan = remote_matte_plan(root / "plate.png", root / "ap.png", root / "out:alpha.png",
                             "root@example", 2222, root / "model.onnx")
    assert plan["mkdir"][:2] == ["ssh", "-q"] and plan["mkdir"][-1].startswith("mkdir -p ")
    assert "-p" in plan["mkdir"] and "2222" in plan["mkdir"], "the port must reach ssh"
    assert "ControlMaster=auto" in plan["mkdir"], "one connection must be reused across the steps"
    pushed = [Path(a).name for a in plan["push"] if a.endswith((".png", ".onnx", ".py"))]
    for required in ("plate.png", "ap.png", "model.onnx", "birefnet_extract.py", "birefnet_ab.py"):
        assert required in pushed, f"{required} must be sent to the pod"
    remote_cmd = plan["run"][-1]
    assert remote_cmd.startswith(POD_MATTE_PYTHON), "must use the pod's matting venv, not system python"
    assert "--aperture" in remote_cmd, "an aperture must be passed through"
    assert plan["remote"] in remote_cmd and "/alpha.png" in remote_cmd
    # The colon in a "poc:qwen2512-..." output name would split an scp-style path; it must not
    # survive into the remote directory.
    assert ":" not in plan["remote"].split("/")[-1], "remote directory names must not contain a colon"
    assert plan["fetch"][-1].endswith("out:alpha.png"), "the alpha comes back to the requested path"
    no_ap = remote_matte_plan(root / "p.png", None, root / "o.png", "root@example", 22,
                              root / "m.onnx")
    assert "--aperture" not in no_ap["run"][-1], "no aperture means no flag"

    # cached_matte(): content-addressed, so an identical plate written to a different output
    # directory hits the cache, and a different model does not.
    matte_src = root / "matte-src.png"
    save_png(Image.new("RGB", (16, 16), (10, 120, 30)), matte_src)
    calls = []
    real_extract = globals()["birefnet_extract"]
    globals()["birefnet_extract"] = lambda src, ap, out, *a, **k: (
        calls.append(out), save_png(Image.new("L", (16, 16), 200), out))
    try:
        cache_home = MATTE_CACHE
        globals()["MATTE_CACHE"] = root / "matte-cache"
        first, second = root / "a" / "m.png", root / "b" / "m.png"
        cached_matte(matte_src, first)
        cached_matte(matte_src, second)
        assert len(calls) == 1, "identical source bytes in a new directory must reuse the cache"
        assert second.is_file(), "the cached matte must be copied into the run directory"
        assert image_from(second).convert("L").getpixel((0, 0)) == 200, "and must be the cached alpha"
        cached_matte(matte_src, root / "c" / "m.png", model="other-model.onnx")
        assert len(calls) == 2, "a different matting model must not return the previous alpha"
    finally:
        globals()["birefnet_extract"] = real_extract
        globals()["MATTE_CACHE"] = cache_home

    # --grey-preprocess: the preprocessed plate and the carrier no longer share a backdrop, so the
    # checks must key each with its own screen. Sharing one -- the bug class that cost the 08-07
    # session several GPU cycles -- makes the carrier's blue read as *figure* under a grey key.
    grey_plate = Image.new("RGB", (120, 160), (128, 128, 128))
    ImageDraw.Draw(grey_plate).rectangle((40, 30, 79, 159), fill=(210, 170, 140))
    blue_ref = Image.new("RGB", (120, 160), (0, 80, 200))
    ImageDraw.Draw(blue_ref).rectangle((42, 28, 77, 159), fill=(0, 200, 0))
    grey_path, ref_path = root / "grey-pre.png", root / "blue-ref.png"
    save_png(grey_plate, grey_path)
    save_png(blue_ref, ref_path)
    mixed = {c["name"]: c for c in preprocess_checks(grey_path, ref_path, "grey", "blue")}
    assert mixed["preprocess_background_is_grey"]["passed"], "the grey plate must key as grey"
    assert mixed["preprocess_scale_vs_carrier"]["passed"], \
        f"a grey plate must scale against a blue carrier: {mixed['preprocess_scale_vs_carrier']['detail']}"
    # The negative control runs the other way round. screen="grey" estimates the backdrop from the
    # border bands, so it copes with a blue frame too; what does *not* cope is keying the grey plate
    # as blue -- grey is not blue-dominant, so the whole frame reads as figure. That is the failure
    # this threading exists to prevent, and it must be visible.
    wrong_screen = {c["name"]: c for c in preprocess_checks(grey_path, ref_path, "blue", "blue")}
    assert not wrong_screen["preprocess_figure_coverage"]["passed"], \
        f"a grey plate keyed as blue must fail coverage: {wrong_screen['preprocess_figure_coverage']['detail']}"

    # Skin harmonisation. The mask must cover the body, spare the head, and -- the property that
    # keeps this from becoming the abandoned repaint -- stop short of the silhouette, since
    # ImageCompositeMasked only guarantees bit-exactness outside the mask.
    blend_carrier = Image.new("RGB", (200, 300), (0, 0, 255))
    ImageDraw.Draw(blend_carrier).rectangle((60, 40, 139, 279), fill=(0, 200, 0))
    blend_head = Image.new("L", (200, 300))
    ImageDraw.Draw(blend_head).rectangle((80, 40, 119, 89), fill=255)
    body_mask = ImageChops.subtract(
        production.erode(screen_foreground(blend_carrier), production.SKIN_BLEND_EDGE_GUARD),
        dilate(blend_head, production.NECK_OVERLAP))
    assert body_mask.getpixel((100, 200)) == 255, "the body must be editable"
    assert body_mask.getpixel((100, 60)) == 0, "the transplanted head must be protected"
    assert body_mask.getpixel((61, 200)) == 0, "the mask must stop short of the silhouette edge"
    assert body_mask.getpixel((100, 20)) == 0, "the background must never be editable"
    # denoise must actually reach the sampler, or "harmonise" silently becomes "regenerate".
    graph = production.edit_graph("a.png", "p", 1, "pre", denoise=0.4)
    sampler = next(n for n in graph.values() if n["class_type"] == "KSampler")
    assert sampler["inputs"]["denoise"] == 0.4, "denoise must reach the KSampler"
    assert next(n for n in production.edit_graph("a.png", "p", 1, "pre").values()
                if n["class_type"] == "KSampler")["inputs"]["denoise"] == 1.0, \
        "every existing caller must keep denoise 1.0"

    print("qwen2512 skin/head/clothes POC self-test passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH,
                        help=f"instance settings file (default: {CONFIG_PATH})")
    parser.add_argument("--init-config", action="store_true", help="write a config template and exit")
    # No instance address may live in this repository, and nothing may be silently defaulted to a
    # recycled host: every connection setting comes from --config, a flag, or the environment.
    parser.add_argument("--server")
    parser.add_argument("--ssh-target")
    parser.add_argument("--ssh-port", type=int)
    parser.add_argument("--edit-model")
    parser.add_argument("--corridorkey-root")
    parser.add_argument("--performer", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--stop-after", choices=STAGES, help="run up to this stage, then stop for review")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    # VITON / matting path (see VITON_CLOTHING_POC_HANDOVER.md): grey carrier, local BiRefNet
    # extraction, CatVTON clothes. --grey-carrier implies viton clothes mode; viton clothes mode
    # implies birefnet extraction.
    parser.add_argument("--grey-carrier", action="store_true",
                        help="flat-grey carrier instead of the chroma screen (implies --clothes-mode viton)")
    parser.add_argument("--clothes-mode", choices=("qwen", "garment-ref", "viton"), default="qwen",
                        help="qwen = prompt-only masked edit (current recipe); garment-ref = masked edit "
                             "conditioned on a T2I garment image (modern, no SD1.5); viton = CatVTON try-on "
                             "(geometry probe)")
    parser.add_argument("--extract-mode", choices=("corridorkey", "birefnet"), default=None,
                        help="corridorkey = pod-side chroma keying; birefnet = local BiRefNet matting "
                             "(default: birefnet when grey/viton, else corridorkey)")
    parser.add_argument("--birefnet-model", type=Path, default=DEFAULT_BIREFNET_MODEL)
    parser.add_argument("--birefnet-python", type=Path, default=DEFAULT_BIREFNET_PYTHON)
    # Bulk review mode: the operator is not at the gates. Auto-accepts the envelope (the only hard
    # human gate; the review page marks it pending), runs every stage in one command, and the
    # poc_review.py generator renders a review.html on the shared NFS drive for later review.
    parser.add_argument("--auto-accept-envelope", action="store_true",
                        help="flip envelope-status.json to accepted without a human (bulk mode; "
                             "review.html marks it pending)")
    parser.add_argument("--garment-worn", action="store_true",
                        help="generate the garment reference worn on a model instead of a flat lay "
                             "(carries coverage cues; see GARMENT_WORN_PROMPT)")
    parser.add_argument("--matte-upscale", action="store_true",
                        help="super-resolve each plate before matting (birefnet mode only). The "
                             "alpha is scaled back down, so output RGB and registration are "
                             "unchanged; measured to cut retained spill ~14%% against a "
                             "same-resolution control. Off by default: proven on a head crop, not "
                             "yet on a whole plate.")
    parser.add_argument("--upscale-model", type=Path, default=DEFAULT_UPSCALE_MODEL)
    # --- variant dials: geometry ---------------------------------------------------------
    parser.add_argument("--neck-overlap", type=int,
                        help="how far the head transplant reaches past the head (default 15). Larger "
                             "brings more of her REAL neck and chest across, so less of the visible "
                             "body is the flat deterministic recolor. Needs her body aligned to the "
                             "carrier's to be worth much -- see --preprocess-control.")
    parser.add_argument("--identity-dilation", type=int, help="identity edit envelope (default 97)")
    parser.add_argument("--support-dilation", type=int,
                        help="clothes edit envelope (default 97). How far past the body silhouette "
                             "the garment may be painted.")
    parser.add_argument("--clothes-stop-dilation", type=int,
                        help="forbidden head zone (default 97). Smaller lets a collar rise closer to "
                             "the jaw; too small and the skull halo becomes paintable.")
    parser.add_argument("--head-blend-feather", type=int, help="neck/shoulder seam softness (default 8)")
    parser.add_argument("--skin-blend-edge-guard", type=int,
                        help="how far harmonize_skin()'s mask is held off the silhouette (default 6)")
    # --- variant dials: prompts ----------------------------------------------------------
    parser.add_argument("--skin-blend-prompt", choices=sorted(SKIN_BLEND_PROMPTS), default="blend",
                        help="'blend' asks the model to even the skin out, which is the uniformity we "
                             "then score against; 'variation' asks for the chromatic spread actually "
                             "being measured; 'tone' names a target instead of pointing at her face.")
    parser.add_argument("--clothes-coverage", choices=sorted(CLOTHES_COVERAGE_CLAUSES), default="default",
                        help="extra clause aimed at the waist gap")
    # --- variant dials: controls ---------------------------------------------------------
    parser.add_argument("--preprocess-control", choices=("none", "canny", "openpose"), default="none",
                        help="guide the preprocess on the carrier's geometry so her body tracks the "
                             "carrier's proportions. Removed once because only her head survived the "
                             "transplant; --neck-overlap is what makes it worth having again.")
    parser.add_argument("--clothes-control", choices=("none", "canny", "openpose"), default="none",
                        help="state the body outline to the clothes edit; aimed at the waist gap")
    parser.add_argument("--harmonize-control", choices=("none", "canny", "openpose"), default="none",
                        help="lock geometry during the skin harmonisation")
    parser.add_argument("--control-strength", type=float, default=3.0,
                        help="ControlNet strength for the above (default 3.0). NOT 1.0: three runs at "
                             "1.0 on 2026-08-04 showed no effect and nearly produced the conclusion "
                             "'the ControlNet is inert' -- the controls described what the model would "
                             "draw anyway, so a working mechanism and a dead one give the same null.")
    parser.add_argument("--control-distort", type=float, default=1.0,
                        help="scale the control image vertically before use. A run at, say, 0.8 that "
                             "the output follows is the only cheap proof the control is live at all.")
    parser.add_argument("--harmonize-source", choices=("identity", "carrier"), default="identity",
                        help="what the skin harmonisation starts from. 'carrier' is the green-painted "
                             "body, so the model must generate skin rather than keep the plausible "
                             "flat tone it is handed -- the regime the [skin] stage already reaches "
                             "spread 8.3 in.")
    parser.add_argument("--carrier-model", choices=("qwen", "flux1", "flux2"), default="qwen",
                        help="generator for the carrier. 'flux2' is the strongest candidate -- it takes multiple "
                             "references natively, which is the constraint every stage here is built "
                             "around (see the reference-collapse finding in STATUS.md), and its look "
                             "may fix the plastic result no downstream stage can. 'flux1' is the "
                             "low-risk fallback that works on the current ComfyUI. Both need a large "
                             "one-time download, which preflight triggers.")
    parser.add_argument("--carrier-size", type=str, default="832x1248",
                        help="carrier canvas WxH (default 832x1248, Kontext-native). Larger then "
                             "downsampled is the only way to get real hair strands, since no matte "
                             "can recover detail the source never had.")
    parser.add_argument("--matte-local", action="store_true",
                        help="matte on this machine instead of the pod. BiRefNet peaks near 6 GB "
                             "per plate on CPU and has OOM-killed a 7.4 GB dev box; "
                             "birefnet_extract.py refuses unless the memory is there.")
    parser.add_argument("--grey-preprocess", action="store_true",
                        help="render the performer preprocess on a flat grey backdrop instead of "
                             "chroma blue. The transplanted head carries its own backdrop into the "
                             "plate, so a neutral one keeps blue spill out of her hair edge at "
                             "source. Independent of --grey-carrier, which is shelved; implied by "
                             "it. Off by default: not yet run on a pod.")
    parser.add_argument("--harmonize-skin", action="store_true",
                        help="after the seam smoothing, run a body-masked harmonisation pass so the "
                             "skin gains the chromatic variation deterministic_skin_recolor() cannot "
                             "produce (measured saturation spread 2.0 against real skin's 9.9). Off "
                             "by default: not yet run on a pod.")
    parser.add_argument("--skin-blend-denoise", type=float, default=SKIN_BLEND_DENOISE,
                        help=f"denoise for --harmonize-skin (default {SKIN_BLEND_DENOISE}). At 1.0 a "
                             "body-shaped mask regenerates the body instead of harmonising it.")
    args = parser.parse_args()
    if not 0.0 < args.skin_blend_denoise <= 1.0:
        parser.error("--skin-blend-denoise must be in (0, 1]")
    if args.control_strength <= 0:
        parser.error("--control-strength must be positive")
    try:
        carrier_size = tuple(int(part) for part in args.carrier_size.lower().split("x"))
        if len(carrier_size) != 2 or min(carrier_size) < 256:
            raise ValueError
    except ValueError:
        parser.error("--carrier-size must look like 832x1248")
    apply_variant_dials(args)
    if args.self_test:
        self_test()
        return
    if args.init_config:
        print(f"wrote {init_config(args.config)} (mode 600) — fill it in, then rerun")
        return

    sources = {}
    resolve = resolver(load_config(args.config), args.config, sources)
    server = resolve("server", args.server, "COMFY_SERVER")
    ssh_target = resolve("ssh_target", args.ssh_target, "COVER_STORY_SSH_TARGET")
    ssh_port = resolve("ssh_port", args.ssh_port, "COVER_STORY_SSH_PORT")
    edit_model = resolve("edit_model", args.edit_model, "COVER_STORY_EDIT_MODEL", production.EDIT_MODEL)
    corridorkey_root = resolve("corridorkey_root", args.corridorkey_root,
                               "COVER_STORY_CORRIDORKEY_ROOT", DEFAULT_CORRIDORKEY_ROOT)
    performer = Path(resolve("performer", args.performer, None, DEFAULT_PERFORMER))
    root = Path(resolve("output_dir", args.output_dir, "COVER_STORY_POC_ROOT", DEFAULT_ROOT))

    missing = [name for name, value in (("server", server), ("ssh_target", ssh_target), ("ssh_port", ssh_port))
               if not value]
    if missing:
        parser.error(f"missing required settings: {', '.join(missing)}. Run --init-config to create "
                     f"{args.config}, or pass them as flags/environment variables")
    # Mode resolution: the grey carrier exists for the matting paths; VITON clothes needs matting
    # extraction (no key colour exists to drive CorridorKey). Anything else is a rejected combo.
    grey_carrier = args.grey_carrier
    clothes_mode = args.clothes_mode
    if grey_carrier and clothes_mode == "qwen":
        parser.error("--grey-carrier requires --clothes-mode garment-ref or viton (the grey carrier "
                     "exists for the matting paths; the chroma recipe is unchanged)")
    extract_mode = args.extract_mode or ("birefnet" if (grey_carrier or clothes_mode == "viton") else "corridorkey")
    # Matting runs on the pod by default. BiRefNet peaks near 6 GB per plate on CPU, which
    # OOM-killed the dev box three times on 2026-08-08/09, destroying in-flight runs and
    # endangering unrelated user processes. --matte-local forces the old behaviour, and
    # birefnet_extract.py still refuses if the memory is not actually there.
    matte_remote = None if args.matte_local else (ssh_target, int(ssh_port))
    if clothes_mode == "viton" and extract_mode != "birefnet":
        parser.error("--clothes-mode viton requires --extract-mode birefnet (no key colour to key)")
    screen = "grey" if grey_carrier else "blue"
    if args.clothes_mode != "qwen" or extract_mode != "corridorkey" or grey_carrier:
        print(f"  recipe   {'grey-carrier' if grey_carrier else 'chroma-carrier'} / "
              f"clothes={clothes_mode} / extract={extract_mode}", flush=True)
    for name, value in (("server", server), ("ssh_target", ssh_target), ("ssh_port", ssh_port),
                        ("edit_model", edit_model), ("corridorkey_root", corridorkey_root),
                        ("performer", performer), ("output_dir", root)):
        print(f"  {name:<17} {redact(value):<52} <- {sources[name]}", flush=True)

    def done(stage):
        """True when the run should stop here."""
        return args.stop_after == stage

    work = root / "_work"
    root.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    # Keep CorridorKey's resumable remote cache outside the v20d namespace.
    production.RUN_ID = POC_RUN_ID
    production.EDIT_MODEL = edit_model
    # standalone_alpha() reads this from the environment; the config file is the source of truth.
    os.environ["COVER_STORY_CORRIDORKEY_ROOT"] = corridorkey_root

    print("[preflight]", flush=True)
    # preflight reads the real CLIPLoader enum from the pod; the constant is only a fallback for
    # when the probe could not run, never an assertion that it is the right string.
    flux2_clip_type = production.FLUX2_CLIP_TYPE
    report(root, "preflight", preflight(server, ssh_target, int(ssh_port), performer,
                                        edit_model, corridorkey_root, clothes_mode, extract_mode,
                                        bool(matte_remote) and extract_mode == "birefnet",
                                        args.carrier_model))
    if done("preflight"):
        return print(root)

    print("[carrier]", flush=True)
    carrier = root / "carrier.png"
    generate_carrier(server, carrier, work, args.force,
                     prompt=GREY_CARRIER_PROMPT if grey_carrier else CARRIER_PROMPT, family=args.carrier_model, size=carrier_size,
                     clip_type=flux2_clip_type)
    report(root, "carrier", grey_carrier_checks(carrier) if grey_carrier else carrier_checks(carrier))
    if done("carrier"):
        return print(root)

    print("[envelope]", flush=True)
    # No free before SAM: ComfyUI's load_models_gpu evicts enough of the LRU model itself, and with
    # weights kept in RAM (see soft_free) the eviction it does is a PCIe copy rather than a re-read.
    # The only transition ComfyUI cannot see coming is the one into CorridorKey, below.
    hints = build_hints(carrier, root / "masks", screen)
    envelope_status = bootstrap_envelope(server, carrier, root, work, args.force)
    if args.auto_accept_envelope and json.loads(envelope_status.read_text(encoding="utf-8")).get("status") != ENVELOPE_ACCEPTED:
        status = json.loads(envelope_status.read_text(encoding="utf-8"))
        status.update({"status": ENVELOPE_ACCEPTED, "auto_accepted": True})
        envelope_status.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
        print("  envelope AUTO-ACCEPTED (bulk mode); review masks/envelope-review.png on the review page",
              flush=True)
    report(root, "envelope", envelope_checks(carrier, root / "masks" / "identity-head-mask.png",
                                             root / "masks" / "clothes-body-mask.png", screen))
    if done("envelope"):
        return print(root)

    print("[skin]", flush=True)
    # Single reference and mask-free: see SKIN_PROMPT for why image 2 is not used here.
    if clothes_mode != "qwen":
        # Vestigial for both alternative clothes paths: the VITON person image is the deterministic
        # tone recolor (run_viton), and garment-ref edits the carrier directly. Nothing downstream
        # reads skin-tone.png, so skipping keeps the run honest.
        print(f"  skipped in {clothes_mode} mode (nothing downstream reads skin-tone.png)", flush=True)
        if done("skin"):
            return print(root)
    else:
        skin = root / "skin-tone.png"
        edit(server, carrier, GREY_SKIN_PROMPT if grey_carrier else SKIN_PROMPT, None, None,
             production.seed_for("qwen2512:skin-tone"),
             "cover-story/qwen2512-skin-head-clothes/skin-tone", skin, work, args.force)
        report(root, "skin", grey_skin_checks(carrier, skin) if grey_carrier else skin_checks(carrier, skin))
        if done("skin"):
            return print(root)

    print("[garment]", flush=True)
    if clothes_mode != "qwen":
        garment = root / "garment.png"
        generate_garment(server, garment, work, args.force, prompt=GARMENT_WORN_PROMPT if args.garment_worn else GARMENT_PROMPT)
        if done("garment"):
            return print(root)
    else:
        print("  skipped in qwen mode (outfit described in CLOTHES_PROMPT)", flush=True)
        if done("garment"):
            return print(root)

    print("[preprocess]", flush=True)
    # performer = image 1, no second reference: see PREPROCESS_PROMPT for why. Unconditioned: an
    # earlier revision guided this on the carrier's silhouette (canny) so the performer's own body
    # proportions would track the carrier's before alignment -- worth it only while the identity
    # stage repainted a body from those proportions. Now only her *head* survives past
    # aligned_performer(); the rest of preprocessed.png is discarded, so matching its body to the
    # carrier bought nothing and cost a SAM call, a ControlNet edit, and the risk both carried.
    preprocessed = root / "preprocessed.png"
    # --grey-preprocess is independent of the shelved --grey-carrier: the backdrop behind the
    # *performer* is what fringes her transplanted hair, and it is the only part of the grey
    # experiment worth keeping. compose_identity() corrects that backdrop to the carrier's with
    # production.rebackground() before the transplant, so the two can differ safely.
    grey_preprocess = args.grey_preprocess or grey_carrier
    preprocess_screen = "grey" if grey_preprocess else screen
    # --preprocess-control makes her body track the carrier's proportions before alignment. Dropped
    # once because only her head survived the transplant; --neck-overlap is what makes it pay again,
    # since a larger transplant needs her chest to actually sit where the carrier's does.
    preprocess_control = control_image(server, carrier, args.preprocess_control,
                                       args.control_distort, root, work)
    edit(server, performer, GREY_PREPROCESS_PROMPT if grey_preprocess else PREPROCESS_PROMPT, None, None,
         production.seed_for("qwen2512:preprocess"),
         "cover-story/qwen2512-skin-head-clothes/preprocess", preprocessed, work, args.force,
         control=preprocess_control, control_type=args.preprocess_control,
         control_strength=args.control_strength)
    report(root, "preprocess", preprocess_checks(preprocessed, carrier, preprocess_screen, screen))
    if done("preprocess"):
        return print(root)

    print("[identity]", flush=True)
    envelope = load_accepted_envelope(root, carrier)
    # Deterministic: her head pasted onto the carrier's own body, not a diffusion repaint. See
    # compose_identity(). Checked before spending GPU on the seam smoothing below, so a geometry
    # problem fails fast rather than after an edit call that would only inherit it.
    aligned = aligned_performer(server, preprocessed, carrier, root, work, preprocess_screen, screen)
    composite, preserved_head, toned_body, transplant_source = compose_identity(server, aligned, carrier, root, work, screen,
                                                          args.birefnet_model, args.birefnet_python,
                                                          matte_remote)
    report(root, "identity-composite",
           identity_composite_checks(transplant_source, carrier, toned_body, composite, preserved_head, screen))
    # A small, local touch-up on the join; see smooth_seam(). masked_edit_checks() already proves
    # outside its (wider) seam mask is bit-exact vs the composite, and the composite's own check
    # above already proved it bit-exact vs aligned/toned_body out to the narrower raw blend boundary
    # -- so only the silhouette is worth re-verifying here, not head/body bit-exactness against a
    # boundary the touch-up was deliberately allowed to cross.
    identity, seam_mask = smooth_seam(server, composite, preserved_head, root, work, args.force)
    identity_checks = [*masked_edit_checks(composite, identity, seam_mask),
                       *silhouette_checks(identity, carrier, screen)]
    if args.harmonize_skin:
        # Composite-then-harmonise: the deterministic recolor states the target tone in pixels and
        # this supplies the chromatic variation it cannot. Runs after the seam smoothing so the
        # neck join is already resolved and the body has one continuous tone to match against.
        # --harmonize-source carrier hands the model the green-painted body instead of the already
        # plausible toned one, so it has to generate skin rather than keep what it is given. That is
        # the regime the mask-free [skin] stage reaches saturation spread 8.3 in.
        blend_source = carrier if args.harmonize_source == "carrier" else identity
        blend_control = None
        if args.harmonize_control != "none":
            blend_control = control_image(server, carrier, args.harmonize_control,
                                          args.control_distort, root, work)
        harmonized, blend_mask = harmonize_skin(
            server, blend_source, preserved_head, carrier, root, work, args.force, screen,
            args.skin_blend_denoise, SKIN_BLEND_PROMPTS[args.skin_blend_prompt],
            tone=describe_tone(sample_face_tone(image_from(aligned))),
            control=blend_control, control_type=args.harmonize_control,
            control_strength=args.control_strength)
        identity_checks += skin_blend_checks(identity, harmonized, blend_mask, carrier, screen)
        identity = harmonized
    report(root, "identity", identity_checks)
    if done("identity"):
        return print(root)

    print("[clothes]", flush=True)
    if clothes_mode == "viton":
        # VITON on the tone-recolored carrier; the envelope's clothes mask is the garment region.
        envelope = load_accepted_envelope(root, carrier)
        clothes = run_viton(server, carrier, root / "garment.png", envelope, root, work, args.force)
        report(root, "clothes", viton_checks(root / "viton-person.png", clothes, envelope, carrier))
        if done("clothes"):
            return print(root)
    elif clothes_mode == "garment-ref":
        # Masked Qwen edit conditioned on the T2I garment image (reference 2). Masked edits preserve
        # the latent outside the mask, the regime where this model follows a second reference. In
        # grey mode the carrier is edited directly; on chroma the key-color variant is used and the
        # plate is checked against it.
        envelope = load_accepted_envelope(root, carrier)
        clothes = root / "clothes.png"
        if grey_carrier:
            # Image 1 is the *toned body* (identity-toned-body.png), not the raw carrier: the
            # garment edit preserves everything outside the mask, so exposed arms/neck/hands come
            # out at the performer's tone instead of the carrier's own skin -- the mismatch the
            # chroma recipe handled with tone variants. Its head is the recolored bald carrier
            # head, which the aperture post-mask removes at extract.
            edit(server, root / "identity-toned-body.png",
                 GREY_GARMENT_REF_PROMPT.format(description=GARMENT_DESCRIPTION),
                 envelope["clothes_mask"], root / "garment.png",
                 production.seed_for("qwen2512:clothes-victorian"),
                 "cover-story/qwen2512-skin-head-clothes/clothes", clothes, work, args.force)
            report(root, "clothes", [*masked_edit_checks(root / "identity-toned-body.png", clothes,
                                                         envelope["clothes_mask"], screen="grey")])
        else:
            clothes_carrier = carrier_for_screen(carrier, CLOTHES_KEY_COLOR, root)
            clothes_control = control_image(server, carrier, args.clothes_control,
                                            args.control_distort, root, work)
            edit(server, clothes_carrier,
                 GARMENT_REF_PROMPT.format(aperture=APERTURE_COLOR[CLOTHES_KEY_COLOR], screen=CLOTHES_KEY_COLOR,
                                           description=GARMENT_DESCRIPTION)
                 + CLOTHES_COVERAGE_CLAUSES[args.clothes_coverage],
                 envelope["clothes_mask"], root / "garment.png",
                 production.seed_for("qwen2512:clothes-victorian"),
                 "cover-story/qwen2512-skin-head-clothes/clothes", clothes, work, args.force,
                 control=clothes_control, control_type=args.clothes_control,
                 control_strength=args.control_strength)
            report(root, "clothes", [
                *masked_edit_checks(clothes_carrier, clothes, envelope["clothes_mask"], screen=CLOTHES_KEY_COLOR),
                {"name": "clothes_plate_screen", "passed": production.screen_color(clothes) == CLOTHES_KEY_COLOR,
                 "detail": {"wanted": CLOTHES_KEY_COLOR, "found": production.screen_color(clothes)}},
            ])
        if done("clothes"):
            return print(root)
    else:
        clothes = root / "clothes.png"
        # The garment keys against the outfit's own key colour, not a pipeline-wide constant.
        clothes_carrier = carrier_for_screen(carrier, CLOTHES_KEY_COLOR, root)
        envelope = load_accepted_envelope(root, carrier)
        edit(server, clothes_carrier,
             CLOTHES_PROMPT.format(aperture=APERTURE_COLOR[CLOTHES_KEY_COLOR], screen=CLOTHES_KEY_COLOR,
                                   description=GARMENT_DESCRIPTION)
             + CLOTHES_COVERAGE_CLAUSES[args.clothes_coverage],
             envelope["clothes_mask"], None,
             production.seed_for("qwen2512:clothes-victorian"), "cover-story/qwen2512-skin-head-clothes/clothes", clothes, work, args.force)
        # The one free that has to be explicit: [extract] runs CorridorKey as a separate process on the
        # same GPU, and ComfyUI has no way to know it needs to give the VRAM back.
        soft_free(server)
        report(root, "clothes", [
            *masked_edit_checks(clothes_carrier, clothes, envelope["clothes_mask"], screen=CLOTHES_KEY_COLOR),
            # A plate left over from a run at a different key colour would compose silently wrong;
            # every stage is resumable by file existence, so the artifact has to declare its own screen.
            {"name": "clothes_plate_screen", "passed": production.screen_color(clothes) == CLOTHES_KEY_COLOR,
             "detail": {"wanted": CLOTHES_KEY_COLOR, "found": production.screen_color(clothes)}},
        ])
        if done("clothes"):
            return print(root)

    print("[extract]", flush=True)
    # Reverted to plain, undespilled plates for now: despilled_plate() closed the waist/feet holes
    # but every scoping tried for it (edit envelope, dilated rough foreground, dominance ceiling,
    # morphological hole-closing) also mis-fired somewhere else -- a teal halo, a jagged hair edge,
    # a wrongly-filled underarm gap -- because each was diagnosed against the composite rather than
    # against each plate's own extraction in isolation. Going back to a known baseline to characterize
    # each plate's native keying quality on its own before trying another scoped fix. See
    # LAYERED_COSTUME_PRODUCTION_STATUS.md, 2026-08-06.
    clothes_screen = screen if grey_carrier else CLOTHES_KEY_COLOR
    if extract_mode == "birefnet":
        # Local learned matting: no hints, no key colour, no pod, no SSH.
        # The clothes aperture is the *painted head* region, key_aperture() -- exactly as the
        # corridorkey path derives it. Using the identity head envelope instead was a bug: that
        # mask is dilated 97px and reaches below the bust (y~473), so the post-multiply punched a
        # transparent hole through the dress bodice and the composite showed bare skin where the
        # garment should be (measured 15.7% of the garment region). The grey path has no paint to
        # derive the aperture from, so it uses the SAM head-stop mask saved by bootstrap_envelope.
        matte_upscale = args.upscale_model if args.matte_upscale else None
        identity_alpha_path = root / "poc:qwen2512-identity-alpha.png"
        if matte_remote:
            remote_matte(identity, None, identity_alpha_path, *matte_remote,
                         args.birefnet_model, matte_upscale, args.force)
        else:
            birefnet_extract(identity, None, identity_alpha_path, args.birefnet_model,
                             args.birefnet_python, args.force, matte_upscale)
        if grey_carrier:
            envelope = load_accepted_envelope(root, carrier)
            clothes_aperture_path = envelope["head_stop"]
        else:
            clothes_aperture_path = root / "masks" / f"clothes-{APERTURE_COLOR[CLOTHES_KEY_COLOR]}-aperture.png"
            key_aperture(image_from(clothes), CLOTHES_KEY_COLOR).save(clothes_aperture_path)
        clothes_aperture = image_from(clothes_aperture_path).convert("L")
        clothes_alpha_path = root / "poc:qwen2512-clothes-alpha.png"
        if matte_remote:
            remote_matte(clothes, clothes_aperture_path, clothes_alpha_path, *matte_remote,
                         args.birefnet_model, matte_upscale, args.force)
        else:
            birefnet_extract(clothes, clothes_aperture_path, clothes_alpha_path,
                             args.birefnet_model, args.birefnet_python, args.force, matte_upscale)
        # Hybrid matte: BiRefNet decides shape, chroma decides whether a boundary pixel is subject
        # at all. BiRefNet has no colour semantics, so on a chroma plate its matte swallows the
        # spill band whole -- see production.chroma_gate(). Idempotent, so a resumed run that reads
        # an already-gated alpha off disk gates to the same result.
        identity_alpha = production.chroma_gate(image_from(identity_alpha_path).convert("L"),
                                                image_from(identity), screen)
        clothes_alpha = production.chroma_gate(image_from(clothes_alpha_path).convert("L"),
                                               image_from(clothes), clothes_screen)
        save_png(identity_alpha, identity_alpha_path)
        save_png(clothes_alpha, clothes_alpha_path)
        aperture_left = 0  # post-multiplied, so the aperture is transparent by construction
    else:
        identity_raw_hint, identity_hint = plate_hints(identity, root / "masks", "identity")
        clothes_raw_hint, clothes_hint = plate_hints(clothes, root / "masks", "clothes", CLOTHES_KEY_COLOR)
        identity_alpha = extract(server, identity, identity_raw_hint, identity_hint, "blue",
                                 "poc:qwen2512-identity", ssh_target, int(ssh_port), root)
        # The clothes plate's painted aperture must be keyed away; the identity plate has no paint.
        clothes_aperture = key_aperture(image_from(clothes), CLOTHES_KEY_COLOR)
        save_png(clothes_aperture, root / "masks" / f"clothes-{APERTURE_COLOR[CLOTHES_KEY_COLOR]}-aperture.png")
        clothes_alpha = extract(server, clothes, clothes_raw_hint, clothes_hint, CLOTHES_KEY_COLOR,
                                "poc:qwen2512-clothes", ssh_target, int(ssh_port), root, clothes_aperture)
        aperture_left = sum(ImageChops.multiply(clothes_alpha, clothes_aperture).histogram()[128:])
    report(root, "extract", [*alpha_checks(carrier, identity, identity_alpha, "identity", screen=screen),
                             *alpha_checks(clothes_carrier if extract_mode == "corridorkey" else carrier,
                                           clothes, clothes_alpha, "clothes", clothes_aperture,
                                           clothes_screen),
                             # A painted head surviving here would cover the skin plate underneath.
                             {"name": "clothes_aperture_keyed_away", "passed": aperture_left < 500,
                              "detail": aperture_left}])
    if done("extract"):
        return print(root)

    print("[composite]", flush=True)
    hair_hints = sam_hints(server, identity, ["hair"], "cover-story/qwen2512-skin-head-clothes/hair", work)
    hair_hint = root / "masks" / "identity-hair-sam.png"
    save_png(hair_hints[0], hair_hint)
    # The skin plate is bounded to the identity envelope, which is IDENTITY_SAM_PROMPT ("head, hair,
    # face, ears, neck, clavicles, shoulders and upper chest") dilated for working room -- exactly
    # the face/neck/upper-chest plate the pipeline reference specifies, and a *fixed anatomical*
    # region, so one skin plate still serves every outfit. Unbounded, this layer was the whole nude
    # body with the garment as its only cover: the source of the waist slivers, the toes beside the
    # shoes, and a nude torso shipping as an asset.
    skin_bound = image_from(envelope["head_mask"]).convert("L")
    skin_rgba, hair_rgba = production.split_head_layers(identity, identity_alpha, hair_hints[0],
                                                        screen, skin_bound)
    clothes_rgba = production.segment_source(clothes, clothes_alpha, clothes_screen)
    save_png(skin_rgba, root / "identity-skin-rgba.png")
    save_png(hair_rgba, root / "identity-hair-rgba.png")
    save_png(clothes_rgba, root / "clothes-rgba.png")
    background = Image.new("RGBA", image_from(carrier).size,
                           (90, 92, 95, 255) if grey_carrier else (44, 48, 58, 255))
    # background, skin, clothing, hair -- production.compose()'s order, which this PoC prototypes.
    # Hair goes on top so it falls over the garment's shoulders; putting the garment last buries
    # it. No effect on this run (the hair stops above the neckline, so the swap moved 0 px), which
    # is exactly why it survived a full end-to-end review: it only shows on longer hair.
    composite = Image.alpha_composite(background, skin_rgba)
    composite = Image.alpha_composite(composite, clothes_rgba)
    composite = Image.alpha_composite(composite, hair_rgba)
    save_png(composite, root / "composite.png")
    # The gate that would have caught the aperture bug: skin visible where the garment should be.
    # The birefnet extract path once used the identity head envelope as the clothes aperture; that
    # mask reaches below the bust, so the post-multiply punched a hole through the dress bodice and
    # the composite showed 15.7% of its garment region as bare skin. Coverage is the cheapest
    # visual proxy that separates "mechanically fine" from "visibly broken".
    garment_region = image_from(envelope["clothes_mask"]).convert("L").point(lambda v: 255 if v > 127 else 0)
    comp_r, comp_g, comp_b = composite.convert("RGB").split()
    skin_mask = ImageChops.multiply(ImageChops.multiply(
        ImageChops.subtract(comp_r, comp_g).point(lambda v: 255 if v > 0 else 0),
        ImageChops.subtract(comp_g, comp_b).point(lambda v: 255 if v > 0 else 0)),
        ImageChops.multiply(comp_r.point(lambda v: 255 if v > 80 else 0),
                            ImageChops.subtract(comp_r, comp_b).point(lambda v: 255 if v > 20 else 0)))
    skin_in_garment = sum(ImageChops.multiply(skin_mask, garment_region).histogram()[1:])
    garment_area = max(1, garment_region.width * garment_region.height)
    region_fraction = skin_in_garment / max(1, sum(garment_region.histogram()[1:]))
    # The second visual proxy, added 2026-08-08: a coloured halo around the figure. Every
    # mechanical check passed on a composite with a pronounced blue hair halo and a green dress
    # fringe, because nothing measured edge *colour*. Per layer, not on the composite, since the
    # composite's own opaque background would swamp the edge band.
    # Bounding the skin plate to the aperture creates one new failure mode: a body region that the
    # garment does not cover now has nothing behind it and shows background. Measured against the
    # union of the three layers' alphas rather than by comparing colours, so it is exact. Production
    # answers this by painting exposed skin into the clothed-body plate ("Exposed natural skin stays
    # {tone}", generated once per tone_groups entry); the PoC's clothes prompts do not yet say that,
    # which is fine for a fully covering outfit and will not be for a sleeveless one. This gate is
    # what will catch that the first time it matters.
    covered = ImageChops.lighter(
        ImageChops.lighter(skin_rgba.getchannel("A"), clothes_rgba.getchannel("A")),
        hair_rgba.getchannel("A")).point(lambda value: 255 if value > 8 else 0)
    figure = screen_foreground(image_from(carrier), screen)
    gap_px = sum(ImageChops.subtract(figure, covered).histogram()[1:])
    gap_fraction = gap_px / max(1, sum(figure.histogram()[1:]))

    fringes = [("identity-skin", skin_rgba, screen), ("identity-hair", hair_rgba, screen),
               ("clothes", clothes_rgba, clothes_screen)]
    report(root, "composite", [
        {"name": "no_skin_in_garment_region", "passed": region_fraction < 0.05,
         "detail": round(region_fraction, 4)},
        {"name": "figure_fully_covered_by_layers", "passed": gap_fraction < 0.02,
         "detail": {"gap_px": gap_px, "fraction": round(gap_fraction, 4), "limit": 0.02}},
        *[{"name": f"no_screen_fringe_{name}",
           "passed": production.fringe_fraction(layer, layer_screen) < production.FRINGE_LIMIT,
           "detail": {"screen": layer_screen,
                      "fraction": round(production.fringe_fraction(layer, layer_screen), 4),
                      "limit": production.FRINGE_LIMIT}}
          for name, layer, layer_screen in fringes],
    ])
    (root / "poc.json").write_text(json.dumps({
        "version": 1, "run_id": POC_RUN_ID, "carrier_model": production.CARRIER_MODEL, "edit_model": production.EDIT_MODEL,
        "carrier_dimensions": list(image_from(carrier).size),
        "recipe": {"carrier": "grey" if grey_carrier else "chroma", "clothes": clothes_mode, "extract": extract_mode},
        # Which dials produced this directory. An unlabelled variant is indistinguishable from
        # a regression a few runs later, and these are exactly the knobs a sweep turns.
        "variants": {
            "neck_overlap": production.NECK_OVERLAP,
            "identity_dilation": IDENTITY_DILATION,
            "support_dilation": SUPPORT_DILATION,
            "clothes_stop_dilation": CLOTHES_STOP_DILATION,
            "head_blend_feather": production.HEAD_BLEND_FEATHER,
            "skin_blend_edge_guard": production.SKIN_BLEND_EDGE_GUARD,
            "skin_blend_prompt": args.skin_blend_prompt,
            "skin_blend_denoise": args.skin_blend_denoise,
            "harmonize": bool(args.harmonize_skin),
            "harmonize_source": args.harmonize_source,
            "clothes_coverage": args.clothes_coverage,
            "preprocess_control": args.preprocess_control,
            "clothes_control": args.clothes_control,
            "harmonize_control": args.harmonize_control,
            "control_strength": args.control_strength,
            "control_distort": args.control_distort,
            "grey_preprocess": bool(args.grey_preprocess),
        },
        # Per-layer, not per-run: the garment keys against its outfit's catalog key_color while
        # skin and identity stay on the carrier's own screen.
        "screen": {"identity": screen, "clothes": clothes_screen},
        "carrier_positive_prompt": GREY_CARRIER_PROMPT if grey_carrier else CARRIER_PROMPT,
        "carrier_negative_prompt": CARRIER_NEGATIVE,
        "alpha_policy": ("BiRefNet alpha (local matting); RGB preserved from each source" if extract_mode == "birefnet"
                          else "CorridorKey alpha only; RGB preserved from each source"),
        "hint_policy": "none (matting needs no hint)" if extract_mode == "birefnet"
                        else "blue-dominance hint derived from each plate, never from the carrier",
        "carrier_reference_hints": {name: str(path) for name, path in hints.items()},
        "envelope": {name: str(path) for name, path in envelope.items()},
        "envelope_status": json.loads((root / "masks" / "envelope-status.json").read_text(encoding="utf-8")),
        "checks": json.loads((root / "checks.json").read_text(encoding="utf-8")),
        "performer_source": str(performer), "preprocess_prompt": PREPROCESS_PROMPT,
        "sequence": ["carrier", "performer preprocess (mask-free)", "head identity transfer",
                      ("garment T2I + CatVTON try-on" if clothes_mode == "viton" else "carrier-based clothing"),
                      "alpha extraction", "composite"],
        "outputs": [p.name for p in sorted(root.glob("*.png"))],
    }, indent=2) + "\n", encoding="utf-8")
    print(root)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as failure:
        # Stage gates and the envelope review gate are expected stops, not crashes.
        raise SystemExit(f"\n{failure}")
