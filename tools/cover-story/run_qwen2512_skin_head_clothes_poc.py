#!/usr/bin/env python3
"""Run the carrier -> full-body skin -> head identity -> clothing PoC."""

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
import time
from urllib import error, request

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat

import subprocess

from comfy import api, endpoint, run, upload_image
import layered_costume_production as production

STAGES = ("preflight", "carrier", "envelope", "skin", "preprocess", "identity", "clothes", "extract", "composite")


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
CLOTHES_PROMPT = (
    "Keep image 1's {aperture} head, pose, framing and {screen} background unchanged. Dress the masked body in a "
    "fitted plum Victorian walking dress with a high collar, long sleeves, matching gloves, a full skirt and dark "
    "leather shoes. The garment may extend beyond the body silhouette for natural cloth bulk."
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
CLOTHES_STOP_SAM_PROMPT = "head, face, ears and neck"
IDENTITY_DILATION = 97
SUPPORT_DILATION = 97
CLOTHES_STOP_DILATION = 97
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


def aligned_performer(server, preprocessed, carrier, root, work):
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
    aligned, scale = production.face_align(image_from(preprocessed), face, target, size,
                                           image_from(carrier).getpixel((8, 8)))
    save_png(aligned, path)
    check = production.aligned_height_check(production.silhouette_box(path),
                                            production.silhouette_box(carrier))
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


def generate_carrier(server, path, work, force):
    if path.is_file() and not force:
        return
    result_dir = Path(tempfile.mkdtemp(prefix="carrier-", dir=work))
    result = run(server, production.generation_graph(
        CARRIER_PROMPT, production.seed_for("qwen2512:carrier:center"),
        "cover-story/qwen2512-skin-head-clothes/carrier", size=(832, 1248), canonical=False,
        negative_prompt=CARRIER_NEGATIVE,
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
         control=None, control_type="canny", control_strength=1.0):
    """mask=None runs a full-image, prompt-only edit (no ImageCompositeMasked paste boundary);
    output is then just the decoded result. A mask still produces a debug '-raw' sibling.

    control= a local control image path; see production.edit_graph for what it does."""
    if output.is_file() and not force:
        return
    remote_source = upload_image(server, source, subfolder="cover-story/qwen2512-skin-head-clothes/input")
    remote_mask = upload_image(server, mask, subfolder="cover-story/qwen2512-skin-head-clothes/input") if mask else None
    remote_reference = upload_image(server, reference, subfolder="cover-story/qwen2512-skin-head-clothes/input") if reference else None
    remote_control = upload_image(server, control, subfolder="cover-story/qwen2512-skin-head-clothes/input") if control else None
    result_dir = Path(tempfile.mkdtemp(prefix="edit-", dir=work))
    result = run(server, production.edit_graph(remote_source, prompt, seed, prefix, remote_reference,
                                               remote_mask, remote_control, control_type,
                                               control_strength), result_dir, 2400)
    raw = Image.open(production.pick(result, "-raw")).convert("RGB")
    if mask:
        save_png(raw, output.with_name(f"{output.stem}-raw.png"))
        save_png(Image.open(production.pick(result, "-masked")).convert("RGB"), output)
    else:
        save_png(raw, output)


def build_hints(carrier, masks_dir):
    """Record the carrier's own foreground estimate. This is the drift-check reference and
    provenance only — it must never be used as the hint for a generated plate; see plate_hints()."""
    masks_dir.mkdir(parents=True, exist_ok=True)
    person_hint = screen_foreground_hint(image_from(carrier))
    paths = {
        "person_hint": masks_dir / "carrier-person-hint.png",
        "background_hint": masks_dir / "carrier-blue-background-hint.png",
    }
    save_png(person_hint, paths["person_hint"])
    save_png(ImageOps.invert(person_hint), paths["background_hint"])
    return paths


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


def drift_check(carrier, candidate, name):
    """The mask-free recolor runs at denoise 1.0 with no latent mask, so the background is
    genuinely re-synthesized and no prompt wording can guarantee registration. Measure it before
    spending the identity and clothing edits."""
    before, after = image_from(carrier), image_from(candidate)
    if before.size != after.size:
        raise RuntimeError(f"{name}: canvas changed {before.size} -> {after.size}")
    reference = screen_foreground(before)
    shift = [abs(a - b) for a, b in zip(reference.getbbox(), screen_foreground(after).getbbox())]
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
    """The region the clothing edit may never touch. Subtracting the head must be generous
    sideways and upward — otherwise the dilated person envelope leaves a halo around the skull
    and CLOTHES_PROMPT's forbidden bonnet becomes paintable — but must stop at the neck base so
    the garment keeps its shoulders and collar. The cutoff row comes from SAM, so it stays
    pose-aware rather than a fixed center-pose constant."""
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


def envelope_checks(carrier, head_path, clothes_path):
    with Image.open(head_path) as opened:
        head_mask = opened.convert("L")
    with Image.open(clothes_path) as opened:
        clothes_mask = opened.convert("L")
    frame = head_mask.width * head_mask.height
    person = screen_foreground(image_from(carrier))
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


def preprocess_checks(preprocessed, reference):
    """Technical checks only. Whether the hair outline is clean, the earrings are gone and the
    identity survived are human calls — HANDOVER.md keeps identity and styling out of automation,
    so this stage is a review stop, not a pass/fail verdict on quality."""
    image = image_from(preprocessed)
    foreground = screen_foreground(image)
    inside = region_fractions(image, foreground)
    outside = region_fractions(image, ImageOps.invert(foreground))
    box = foreground.getbbox() or (0, 0, 1, 1)
    reference_image = image_from(reference)
    target = screen_foreground(reference_image).getbbox() or (0, 0, 1, 1)
    scale = [(box[2] - box[0]) / max(1, target[2] - target[0]), (box[3] - box[1]) / max(1, target[3] - target[1])]
    # Two-reference edits in this graph collapse onto one reference and discard the other. When
    # image 2 wins, the output is simply a copy of it and the performer is gone — an attempt that
    # did exactly that still scored 1.017 on a scale check, so alignment cannot be the gate here.
    # Difference from the reference is what actually separates a transfer from a copy: a collapsed
    # attempt measured 4.08, genuine ones 50.11 and 57.84.
    collapsed = sum(ImageStat.Stat(ImageChops.difference(
        image.resize(reference_image.size), reference_image)).mean) / 3
    return [
        {"name": "preprocess_is_not_a_copy_of_reference", "passed": collapsed > 20,
         "detail": round(collapsed, 2)},
        {"name": "preprocess_background_is_blue", "passed": outside["blue"] > 0.85, "detail": outside["blue"]},
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


def drift_check_result(carrier, candidate):
    try:
        return {"name": "registration", "passed": True, "detail": drift_check(carrier, candidate, "skin-tone")}
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
        # Uncovered body paint is the defect; the screen showing through the envelope around the
        # garment is not. Those are different colours and they swap with the key, so this has to
        # follow the screen. Hardcoded "green" read the screen itself once the key moved to green
        # and failed a good plate at 0.2628 while the real remnant was 0.0078.
        paint = APERTURE_COLOR[screen]
        residue = region_fractions(after, region)[paint]
        checks.append({"name": "no_paint_remnant_in_edit", "passed": residue < 0.05,
                       "detail": {"paint": paint, "fraction": residue}})
    return checks


def compose_identity(server, aligned, carrier, root, work):
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
    if composite.is_file() and preserve_path.is_file() and toned_body_path.is_file():
        return composite, preserve_path, toned_body_path
    head, head_box = sam_mask(server, aligned, HEAD_SAM_PROMPT, work, f"{SAM_PREFIX}/head")
    preserve = dilate(head, production.NECK_OVERLAP)
    save_png(preserve, preserve_path)
    aligned_image, carrier_image = image_from(aligned), image_from(carrier)
    performer_tone = production.region_tone(aligned_image, production.inset(head_box, 0.3))
    # screen_foreground()'s screen= names the *background* colour to key out (blue, for the
    # carrier) -- unrelated to deterministic_skin_recolor()'s paint_channel=, which names the
    # *figure's own paint* colour (green) it reads as a shading map. Same word, opposite ends of
    # the same image; this collision produced a real bug the first time through -- see
    # LAYERED_COSTUME_PRODUCTION_STATUS.md, 2026-08-05.
    body_foreground = screen_foreground(carrier_image)
    toned_body = production.deterministic_skin_recolor(carrier_image, body_foreground, performer_tone,
                                                        paint_channel="green")
    save_png(toned_body, toned_body_path)
    composed = production.head_transplant(aligned_image, toned_body, preserve)
    save_png(composed, composite)
    print(f"  compose: performer tone {performer_tone}", flush=True)
    return composite, preserve_path, toned_body_path


def silhouette_checks(candidate, carrier):
    """Does the result's silhouette still match the carrier's -- the one thing that has to hold for
    the clothes plate, built from that same carrier, to fit. Compared against carrier.png directly,
    not skin.png: an earlier revision compared against skin.png, which is itself an independently
    diffused recolor with up to 7px of its own drift from the carrier, and that residue passed
    straight through as if it were not there. Valid at any stage of the identity pipeline, since
    nothing after compose_identity() is allowed to touch the outer body silhouette (the seam mask
    sits at the neck/shoulders, nowhere near it)."""
    candidate_box, carrier_box = production.silhouette_box(candidate), production.silhouette_box(carrier)
    drift = {"left": abs(candidate_box[0] - carrier_box[0]), "right": abs(candidate_box[2] - carrier_box[2]),
             "bottom": abs(candidate_box[3] - carrier_box[3])}
    return [
        {"name": "body_matches_carrier", "passed": max(drift.values()) <= 1,
         "detail": {"body_drift_px": drift, "head_top_offset_px": candidate_box[1] - carrier_box[1]}},
        {"name": "background_still_blue", "passed": production.screen_color(candidate) == "blue",
         "detail": production.screen_color(candidate)},
    ]


def identity_composite_checks(aligned, carrier, toned_body, candidate, preserve_path):
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
        *silhouette_checks(candidate, carrier),
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


def preflight(server, ssh_target, ssh_port, performer, edit_model, corridor_root):
    """Fail on a misconfigured instance before any GPU time is spent. Without this the SSH and
    CorridorKey settings are only exercised after four Qwen generations have already run."""
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

    ssh = ["ssh", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-p", str(ssh_port), ssh_target]
    try:
        reachable = subprocess.run([*ssh, "true"], timeout=45).returncode == 0
    except (OSError, subprocess.SubprocessError) as error:
        reachable = False
        record("ssh_reachable", False, str(error))
    else:
        record("ssh_reachable", reachable, f"{ssh_target}:{ssh_port}")
    if reachable:
        record(*remote_bootstrap(ssh, ssh_port, ssh_target))
        probe = f"test -x {corridor_root}/.venv/bin/python && test -d {corridor_root}/CorridorKeyModule/checkpoints"
        record("corridorkey_installed", subprocess.run([*ssh, probe], timeout=60).returncode == 0, corridor_root)
        # standalone_alpha() stages and retrieves over rsync; a pod without it fails only after
        # every generation has already run.
        record("remote_rsync_present",
               subprocess.run([*ssh, "command -v rsync"], timeout=60).returncode == 0, "rsync on the pod")
    else:
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
    args = parser.parse_args()
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
    report(root, "preflight", preflight(server, ssh_target, int(ssh_port), performer,
                                        edit_model, corridorkey_root))
    if done("preflight"):
        return print(root)

    print("[carrier]", flush=True)
    carrier = root / "carrier.png"
    generate_carrier(server, carrier, work, args.force)
    report(root, "carrier", carrier_checks(carrier))
    if done("carrier"):
        return print(root)

    print("[envelope]", flush=True)
    # No free before SAM: ComfyUI's load_models_gpu evicts enough of the LRU model itself, and with
    # weights kept in RAM (see soft_free) the eviction it does is a PCIe copy rather than a re-read.
    # The only transition ComfyUI cannot see coming is the one into CorridorKey, below.
    hints = build_hints(carrier, root / "masks")
    bootstrap_envelope(server, carrier, root, work, args.force)
    report(root, "envelope", envelope_checks(carrier, root / "masks" / "identity-head-mask.png",
                                             root / "masks" / "clothes-body-mask.png"))
    if done("envelope"):
        return print(root)

    print("[skin]", flush=True)
    # Single reference and mask-free: see SKIN_PROMPT for why image 2 is not used here.
    skin = root / "skin-tone.png"
    edit(server, carrier, SKIN_PROMPT, None, None, production.seed_for("qwen2512:skin-tone"),
         "cover-story/qwen2512-skin-head-clothes/skin-tone", skin, work, args.force)
    report(root, "skin", skin_checks(carrier, skin))
    if done("skin"):
        return print(root)

    print("[preprocess]", flush=True)
    # performer = image 1, no second reference: see PREPROCESS_PROMPT for why. Unconditioned: an
    # earlier revision guided this on the carrier's silhouette (canny) so the performer's own body
    # proportions would track the carrier's before alignment -- worth it only while the identity
    # stage repainted a body from those proportions. Now only her *head* survives past
    # aligned_performer(); the rest of preprocessed.png is discarded, so matching its body to the
    # carrier bought nothing and cost a SAM call, a ControlNet edit, and the risk both carried.
    preprocessed = root / "preprocessed.png"
    edit(server, performer, PREPROCESS_PROMPT, None, None, production.seed_for("qwen2512:preprocess"),
         "cover-story/qwen2512-skin-head-clothes/preprocess", preprocessed, work, args.force)
    report(root, "preprocess", preprocess_checks(preprocessed, carrier))
    if done("preprocess"):
        return print(root)

    print("[identity]", flush=True)
    envelope = load_accepted_envelope(root, carrier)
    # Deterministic: her head pasted onto the carrier's own body, not a diffusion repaint. See
    # compose_identity(). Checked before spending GPU on the seam smoothing below, so a geometry
    # problem fails fast rather than after an edit call that would only inherit it.
    aligned = aligned_performer(server, preprocessed, carrier, root, work)
    composite, preserved_head, toned_body = compose_identity(server, aligned, carrier, root, work)
    report(root, "identity-composite",
           identity_composite_checks(aligned, carrier, toned_body, composite, preserved_head))
    # A small, local touch-up on the join; see smooth_seam(). masked_edit_checks() already proves
    # outside its (wider) seam mask is bit-exact vs the composite, and the composite's own check
    # above already proved it bit-exact vs aligned/toned_body out to the narrower raw blend boundary
    # -- so only the silhouette is worth re-verifying here, not head/body bit-exactness against a
    # boundary the touch-up was deliberately allowed to cross.
    identity, seam_mask = smooth_seam(server, composite, preserved_head, root, work, args.force)
    report(root, "identity", [*masked_edit_checks(composite, identity, seam_mask),
                              *silhouette_checks(identity, carrier)])
    if done("identity"):
        return print(root)

    print("[clothes]", flush=True)
    clothes = root / "clothes.png"
    # The garment keys against the outfit's own key colour, not a pipeline-wide constant.
    clothes_carrier = carrier_for_screen(carrier, CLOTHES_KEY_COLOR, root)
    edit(server, clothes_carrier,
         CLOTHES_PROMPT.format(aperture=APERTURE_COLOR[CLOTHES_KEY_COLOR], screen=CLOTHES_KEY_COLOR),
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
    report(root, "extract", [*alpha_checks(carrier, identity, identity_alpha, "identity"),
                             *alpha_checks(clothes_carrier, clothes, clothes_alpha, "clothes", clothes_aperture,
                                           CLOTHES_KEY_COLOR),
                             # A painted head surviving here would cover the skin plate underneath.
                             {"name": "clothes_aperture_keyed_away", "passed": aperture_left < 500,
                              "detail": aperture_left}])
    if done("extract"):
        return print(root)

    print("[composite]", flush=True)
    hair_hints = sam_hints(server, identity, ["hair"], "cover-story/qwen2512-skin-head-clothes/hair", work)
    hair_hint = root / "masks" / "identity-hair-sam.png"
    save_png(hair_hints[0], hair_hint)
    skin_rgba, hair_rgba = production.split_head_layers(identity, identity_alpha, hair_hints[0], "blue")
    clothes_rgba = production.segment_source(clothes, clothes_alpha, CLOTHES_KEY_COLOR)
    save_png(skin_rgba, root / "identity-skin-rgba.png")
    save_png(hair_rgba, root / "identity-hair-rgba.png")
    save_png(clothes_rgba, root / "clothes-rgba.png")
    background = Image.new("RGBA", image_from(carrier).size, (44, 48, 58, 255))
    # background, skin, clothing, hair -- production.compose()'s order, which this PoC prototypes.
    # Hair goes on top so it falls over the garment's shoulders; putting the garment last buries
    # it. No effect on this run (the hair stops above the neckline, so the swap moved 0 px), which
    # is exactly why it survived a full end-to-end review: it only shows on longer hair.
    composite = Image.alpha_composite(background, skin_rgba)
    composite = Image.alpha_composite(composite, clothes_rgba)
    composite = Image.alpha_composite(composite, hair_rgba)
    save_png(composite, root / "composite.png")
    (root / "poc.json").write_text(json.dumps({
        "version": 1, "run_id": POC_RUN_ID, "carrier_model": production.CARRIER_MODEL, "edit_model": production.EDIT_MODEL,
        "carrier_dimensions": list(image_from(carrier).size),
        # Per-layer, not per-run: the garment keys against its outfit's catalog key_color while
        # skin and identity stay on the carrier's own screen.
        "screen": {"identity": "blue", "clothes": CLOTHES_KEY_COLOR},
        "carrier_positive_prompt": CARRIER_PROMPT, "carrier_negative_prompt": CARRIER_NEGATIVE,
        "alpha_policy": "CorridorKey alpha only; RGB preserved from each source",
        "hint_policy": "blue-dominance hint derived from each plate, never from the carrier",
        "carrier_reference_hints": {name: str(path) for name, path in hints.items()},
        "hints": {"identity": [str(identity_raw_hint), str(identity_hint)],
                  "clothes": [str(clothes_raw_hint), str(clothes_hint)]},
        "envelope": {name: str(path) for name, path in envelope.items()},
        "envelope_status": json.loads((root / "masks" / "envelope-status.json").read_text(encoding="utf-8")),
        "checks": json.loads((root / "checks.json").read_text(encoding="utf-8")),
        "performer_source": str(performer), "preprocess_prompt": PREPROCESS_PROMPT,
        "sequence": ["carrier", "performer preprocess (mask-free)", "full-body skin recolor (mask-free)",
                     "drift check", "head identity transfer", "carrier-based clothing",
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
