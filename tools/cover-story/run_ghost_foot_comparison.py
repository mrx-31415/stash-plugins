#!/usr/bin/env python3
"""Two independent attempts at the same problem: a foot-shaped ghost survives the repaint in every
identity-stage run today (see LAYERED_COSTUME_PRODUCTION_STATUS.md, 2026-08-05). Blanking the edit's
reference image where it disagreed with the carrier did not remove it -- the model isn't drawing on
that pixel content in a denoise=1.0 masked region, so the ghost comes from somewhere else in the
generation, not from stale reference content. That theory is ruled out, not confirmed.

Two remaining, untested candidates, run here against the same carrier so they are directly
comparable to each other and to today's baseline (5 px drift, visible ghost):

A. Guide [preprocess] itself on the carrier's silhouette (canny, head excluded), so the performer's
   own proportions already track the carrier's before face-based alignment happens. Less registration
   residue at the source means less "excess" area for the repaint mask to reconcile at all -- and
   less room for the boundary defect to show up. Openpose was already tried here and made the height
   mismatch *worse* (ratio 1.072 vs uncontrolled 1.048): a skeleton states joints, not silhouette
   extent. Canny states the outline directly, which is the only geometry a uniformly-lit bare figure
   actually has to offer.

B. Widen the repaint's canny outline (OUTLINE_WIDTH, currently 5px). Motivated by an offline check:
   box-downsampling the current outline to the ControlNet's approximate working resolution (8x)
   showed values around 76-163/255 at the true boundary row, dropping to hard zero immediately past
   it -- a weak, ambiguous edge exactly where the sampler has to commit. That is a proxy for the
   model's real hint encoder, not a simulation of it, so this is a lead, not a diagnosis.
"""

import argparse
from pathlib import Path

from PIL import Image, ImageChops

import layered_costume_production as production
import run_qwen2512_skin_head_clothes_poc as poc

DEFAULT_ROOT = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-ghost-foot-comparison")
THICK_OUTLINE_WIDTH = 24


def head_blanked_canny(server, carrier, root, work, force):
    """Canny outline from the carrier with the carrier's *own* head excluded, for use at preprocess
    -- there is no "aligned performer" yet at that stage, so identity_control()'s preserved-head mask
    (built from the aligned performer) does not exist. Uses a fresh, modest SAM head box on the
    carrier directly instead of the 97px-dilated envelope, which would blank shoulders too."""
    raw = root / "preprocess-control-canny-raw.png"
    path = root / "preprocess-control-canny.png"
    if path.is_file() and not force:
        return path
    outline = production.silhouette_outline(poc.screen_foreground(poc.image_from(carrier)))
    poc.save_png(outline.convert("RGB"), raw)
    head, _ = poc.sam_mask(server, carrier, poc.HEAD_SAM_PROMPT, work, "cover-story/ghost-foot/carrier-head")
    image = poc.image_from(raw)
    image.paste(Image.new("RGB", image.size, (0, 0, 0)), (0, 0), poc.dilate(head, 15))
    poc.save_png(image, path)
    return path


def run_identity(server, aligned, carrier, skin, root, work, force, outline_width=None, label=""):
    """The pipeline's own [identity] stage, standalone: align -> mask -> control -> masked edit.
    Returns the identity.png path so the caller can crop/measure it."""
    body_mask, preserved_head, reference = poc.body_masks(server, aligned, carrier, root, work)
    control = poc.identity_control(server, poc.IDENTITY_CONTROL, carrier, preserved_head, root, work,
                                   force, outline_width=outline_width)
    identity = root / "identity.png"
    if not identity.is_file() or force:
        print(f"[identity{label}] generating", flush=True)
        poc.edit(server, reference, poc.IDENTITY_PROMPT, body_mask, skin,
                production.seed_for("qwen2512:identity-body"), f"cover-story/ghost-foot/identity{label}",
                identity, work, force, control=control, control_type=poc.IDENTITY_CONTROL,
                control_strength=production.CONTROL_STRENGTH)
    return identity


def crop_feet(path, out):
    im = Image.open(path)
    w, h = im.size
    crop = im.crop((int(w * 0.30), int(h * 0.85), int(w * 0.70), h))
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)
    poc.save_png(crop, out)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--carrier", type=Path, required=True)
    parser.add_argument("--skin", type=Path, required=True)
    parser.add_argument("--baseline-preprocess", type=Path, required=True,
                        help="existing uncontrolled preprocessed.png, used for experiment B")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    config = poc.load_config(poc.CONFIG_PATH)
    server = config["server"]
    production.RUN_ID = poc.POC_RUN_ID
    production.EDIT_MODEL = config["edit_model"]
    root, work = args.output_dir, args.output_dir / "_work"
    root.mkdir(parents=True, exist_ok=True)
    work.mkdir(exist_ok=True)
    performer = Path(config["performer"])

    # -- Experiment A: canny-guided preprocess, then a standard-width repaint. --
    a_root = root / "a-canny-preprocess"
    a_root.mkdir(exist_ok=True)
    print("[A] canny control from the carrier, carrier's own head excluded", flush=True)
    control = head_blanked_canny(server, args.carrier, a_root, work, args.force)
    print("[A] preprocess, canny-guided", flush=True)
    preprocess_a = a_root / "preprocessed-canny.png"
    poc.edit(server, performer, poc.PREPROCESS_PROMPT, None, None,
             production.seed_for("qwen2512:preprocess"), "cover-story/ghost-foot/preprocess-canny",
             preprocess_a, work, args.force, control=control, control_type="canny",
             control_strength=production.CONTROL_STRENGTH)
    poc.soft_free(server)
    print("[A] align", flush=True)
    aligned_a = poc.aligned_performer(server, preprocess_a, args.carrier, a_root, work)
    ratio_a = production.aligned_height_check(production.silhouette_box(aligned_a),
                                              production.silhouette_box(args.carrier))
    print(f"  height ratio {ratio_a['detail']}", flush=True)
    identity_a = run_identity(server, aligned_a, args.carrier, args.skin, a_root, work, args.force,
                              label="-a")
    poc.soft_free(server)

    # -- Experiment B: standard preprocess (already exists), thicker repaint outline. --
    b_root = root / "b-thick-outline"
    b_root.mkdir(exist_ok=True)
    print(f"[B] repaint with OUTLINE_WIDTH={THICK_OUTLINE_WIDTH} (default "
          f"{production.OUTLINE_WIDTH})", flush=True)
    aligned_b = poc.aligned_performer(server, args.baseline_preprocess, args.carrier, b_root, work)
    identity_b = run_identity(server, aligned_b, args.carrier, args.skin, b_root, work, args.force,
                              outline_width=THICK_OUTLINE_WIDTH, label="-b")
    poc.soft_free(server)

    # -- Compare. --
    carrier_box = production.silhouette_box(args.carrier)
    for name, path in (("A (canny preprocess)", identity_a), ("B (thick outline)", identity_b)):
        box = production.silhouette_box(path)
        drift = {"left": abs(box[0] - carrier_box[0]), "right": abs(box[2] - carrier_box[2]),
                 "bottom": abs(box[3] - carrier_box[3])}
        crop = crop_feet(path, root / f"{path.parent.name}-feet-crop.png")
        print(f"{name:<24} body drift l/r/b {list(drift.values())}  worst {max(drift.values())}  "
              f"crop -> {crop}")


if __name__ == "__main__":
    main()
