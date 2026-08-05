#!/usr/bin/env python3
"""Does conditioning the preprocess edit on the carrier's pose close the alignment residue at the
source, instead of the repaint's ControlNet having to override it downstream?

The identity stage (production.face_align + identity_control at strength 3.0) already closes
registration to 1-2 px by stating the carrier's silhouette directly to the repaint sampler. STATUS.md
flagged the alternative when that fix landed and called it possibly cleaner, but never ran it:
condition `[preprocess]` itself on the carrier's pose, so that a face-aligned performer is already
feet-aligned and the repaint has nothing structural left to reconcile.

What the current pipeline actually measures as the conflict: aligning the performer's *face* to the
carrier's leaves her 77 px taller (carrier height 1094, aligned performer 1171) because her
height-to-face ratio is not the carrier's. That is a preprocess-stage fact, not a repaint-stage one --
by the time the repaint sees it, a scale and a translation have already been chosen and cannot satisfy
both "head on the carrier's head" and "feet on the carrier's feet" at once.

This probe conditions PREPROCESS_PROMPT's full-frame edit on an SDPose skeleton drawn from the
carrier with draw_head=False, draw_face=False -- exactly the switches production.pose_graph() already
defaults to, because the same "head stays outside the sampler's authority" reasoning applies here:
the prompt's "keep her face, hair and skin tone unchanged" governs the head, the skeleton governs
everything below it. Then it runs face_align() on the result and reads the same height-ratio check
the identity stage already computes, so the two runs are compared on the exact number that mattered.

One performer, one carrier, no downstream identity/clothes/composite -- this is a bare comparison,
not a pipeline change.
"""

import argparse
from pathlib import Path

import layered_costume_production as production
import run_qwen2512_skin_head_clothes_poc as poc

DEFAULT_ROOT = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-preprocess-pose-probe")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--carrier", type=Path, required=True)
    parser.add_argument("--baseline-preprocess", type=Path, required=True,
                        help="the existing uncontrolled preprocessed.png, for the same-metric comparison")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--control", choices=("openpose", "canny"), default="openpose")
    parser.add_argument("--control-strength", type=float, default=production.CONTROL_STRENGTH)
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

    print(f"[control] {args.control} skeleton from the carrier, head/face excluded", flush=True)
    control = poc.control_image(server, args.carrier, args.control,
                                f"cover-story/preprocess-pose-probe/control-{args.control}",
                                root / f"control-{args.control}.png", work, args.force)

    print("[preprocess] pose-conditioned", flush=True)
    controlled = root / "preprocessed-controlled.png"
    poc.edit(server, performer, poc.PREPROCESS_PROMPT, None, None,
             production.seed_for("qwen2512:preprocess"),
             "cover-story/preprocess-pose-probe/preprocess-controlled", controlled, work, args.force,
             control=control, control_type=args.control, control_strength=args.control_strength)
    poc.soft_free(server)

    print("[align] baseline (uncontrolled preprocess)", flush=True)
    baseline_root = root / "baseline"
    baseline_root.mkdir(exist_ok=True)
    baseline_aligned = poc.aligned_performer(server, args.baseline_preprocess, args.carrier,
                                             baseline_root, work)
    baseline_ratio = production.aligned_height_check(
        production.silhouette_box(baseline_aligned), production.silhouette_box(args.carrier))

    print("[align] pose-conditioned preprocess", flush=True)
    controlled_root = root / "controlled"
    controlled_root.mkdir(exist_ok=True)
    controlled_aligned = poc.aligned_performer(server, controlled, args.carrier, controlled_root, work)
    controlled_ratio = production.aligned_height_check(
        production.silhouette_box(controlled_aligned), production.silhouette_box(args.carrier))

    print(f"\nbaseline (uncontrolled preprocess)   ratio {baseline_ratio['detail']['ratio']}  "
          f"({baseline_ratio['detail']['aligned_px']} vs carrier {baseline_ratio['detail']['carrier_px']})")
    print(f"pose-conditioned preprocess          ratio {controlled_ratio['detail']['ratio']}  "
          f"({controlled_ratio['detail']['aligned_px']} vs carrier {controlled_ratio['detail']['carrier_px']})")
    print(f"\n1.000 is the target -- the identity stage's repaint ControlNet had to close "
          f"{abs(baseline_ratio['detail']['ratio'] - 1) * baseline_ratio['detail']['carrier_px']:.0f} px "
          f"of this on its own yesterday.")
    print(f"\nwrote {root}")


if __name__ == "__main__":
    main()
