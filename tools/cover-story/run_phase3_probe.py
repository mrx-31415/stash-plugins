#!/usr/bin/env python3
"""Invert the transfer: keep the performer's head, repaint her body into the carrier's silhouette.

Every construction tried so far paints the performer's face *into* the carrier and competes with
whatever face is already there. This does the opposite. The performer is image 1, the mask covers her
body, and her head sits *outside* it — where ImageCompositeMasked is bit-exact
(`outside_mask_unchanged: 0`, verified on both masked stages). Identity cannot drift because nothing
repaints it.

Ceiling, measured before building this: `preprocessed.png` scores 12.33 against the shipped portrait
(iris delta 11, face 24.65), better than every generated attempt — best generated is the headless
carrier at 17.46 mean. That number is this approach's ceiling, since the head is copied rather than
generated.

The cost is the thing to measure, not assume. The frozen carrier exists to hold pose constant across
20 performers x 4 poses; here the pose comes from a mask rather than from the image being edited, so
`--performers` runs more than one and reports the silhouette spread against the reference document's
+/-2 px and +/-0.5% limits.
"""

import argparse
import json
import statistics
from pathlib import Path

from PIL import Image

import identity_metrics
import layered_costume_production as production
import run_identity_ab as ab
import run_qwen2512_skin_head_clothes_poc as poc

DEFAULT_SOURCE = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-skin-head-clothes-poc-v2")
DEFAULT_ROOT = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-phase3-probe")
SEEDS = 4
# The construction this probe established now lives in the pipeline: the prompt and the three build
# steps are the PoC's identity stage, and the geometry under them is in the production module. The
# probe keeps only what is probe-specific -- multiple seeds, scoring, and the distortion diagnostic
# -- so it cannot drift away from what actually ships.
BODY_PROMPT = poc.IDENTITY_PROMPT
aligned_performer = poc.aligned_performer
body_mask = poc.body_masks
control_for = poc.identity_control
silhouette_box = production.silhouette_box


def distorted_control(path, factor, root):
    """Squash the control image vertically about the figure's top, keeping the head where it is.

    Diagnostic only. The shoulders stay put and the feet rise, which is exactly the axis the real
    registration error lies on, so a ControlNet with any authority has an obvious way to show it."""
    image = poc.image_from(path)
    box = image.convert("L").point(lambda value: 255 if value > 32 else 0).getbbox()
    top = box[1] if box else 0
    height = max(1, round((image.height - top) * factor))
    canvas = Image.new("RGB", image.size, (0, 0, 0))
    canvas.paste(image.crop((0, top, image.width, image.height))
                 .resize((image.width, height), Image.Resampling.LANCZOS), (0, top))
    out = root / f"{path.stem}-distort{factor:g}.png"
    poc.save_png(canvas, out)
    print(f"  control distorted x{factor:g} about y={top}: bbox {box} -> "
          f"{canvas.convert('L').point(lambda v: 255 if v > 32 else 0).getbbox()}", flush=True)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--seeds", type=int, default=SEEDS)
    parser.add_argument("--control", choices=("none", "openpose", "canny"), default="none",
                        help="state the target geometry through the union ControlNet instead of "
                             "leaving it to the mask alone")
    parser.add_argument("--control-strength", type=float, default=1.0)
    parser.add_argument("--control-distort", type=float, default=1.0,
                        help="Diagnostic. Scale the control image vertically about the figure's "
                             "top by this factor before use. A control that agrees with what the "
                             "model would draw anyway cannot tell a working ControlNet from an "
                             "inert one -- both produce no change. A deliberately wrong control "
                             "can: if conditioning has any effect, the body must follow it.")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    label = "phase3" if args.control == "none" else f"phase3-{args.control}"
    if args.control != "none" and args.control_strength != 1.0:
        label += f"-s{args.control_strength:g}"
    if args.control != "none" and args.control_distort != 1.0:
        label += f"-d{args.control_distort:g}"
    label = label.replace(".", "_")

    config = poc.load_config(poc.CONFIG_PATH)
    server = config["server"]
    production.RUN_ID = poc.POC_RUN_ID
    production.EDIT_MODEL = config["edit_model"]
    root, work = args.output_dir, args.output_dir / "_work"
    root.mkdir(parents=True, exist_ok=True)
    work.mkdir(exist_ok=True)

    carrier = args.source / "carrier.png"
    skin = args.source / "skin-tone.png"
    preprocessed = args.source / "preprocessed.png"

    print("[align] placing the performer's head on the carrier's", flush=True)
    aligned = aligned_performer(server, preprocessed, carrier, root, work)
    print("[mask] building the repaint region", flush=True)
    mask, preserve = body_mask(server, aligned, carrier, root, work)
    control = None
    if args.control != "none":
        print(f"[control] extracting {args.control} from the carrier", flush=True)
        control = control_for(server, args.control, carrier, preserve, root, work, args.force)
        if args.control_distort != 1.0:
            control = distorted_control(control, args.control_distort, root)
    poc.soft_free(server)

    reference = identity_metrics.signals(server, Path(config["performer"]), work, root, "reference")
    print(f"reference  {identity_metrics.describe({}, reference)}")
    ceiling = identity_metrics.compare(
        reference, identity_metrics.signals(server, preprocessed, work, root, "ceiling"))
    print(f"ceiling (preprocessed, head copied verbatim)  score {ceiling['score']}\n")

    carrier_box = silhouette_box(carrier)
    rows = []
    # Two passes, deliberately. Generating and scoring alternately makes ComfyUI swap between the
    # 19 GiB edit model and SAM on every seed — the same mistake STATUS.md already records for the
    # PoC's edit stages. All the edits share EDIT_MODEL, so they run back to back with no free;
    # then one free, then all the SAM scoring with SAM resident.
    generated = []
    for index in range(args.seeds):
        name = f"{label}-seed{index + 1}"
        output = root / f"identity-{name}.png"
        if not output.is_file() or args.force:
            print(f"[{name}] generating", flush=True)
            # Same seed sequence as the uncontrolled run, so the control image is the only variable.
            poc.edit(server, aligned, BODY_PROMPT, mask, skin,
                     production.seed_for(ab.SEED_LABEL, retry=index),
                     f"cover-story/phase3/{name}", output, work, args.force,
                     control=control, control_type=args.control,
                     control_strength=args.control_strength)
        generated.append((name, output))
    poc.soft_free(server)

    for name, output in generated:
        # The whole premise is that the head is never repainted. Check it rather than trust it.
        untouched = production.outside_mask_changed(aligned, output, mask)
        record = identity_metrics.signals(server, output, work, root, f"cand-{name}")
        comparison = identity_metrics.compare(reference, record)
        box = silhouette_box(output)
        drift = [abs(a - b) for a, b in zip(carrier_box, box)]
        # The `top` component is not drift and can never be zero: the carrier is bald and the
        # performer's hair falls past her shoulders, so their silhouettes legitimately start at
        # different heights. Scoring it made a run whose real worst error was 2 px report 11, which
        # is the sort of floor that hides a fix. Left/right/bottom are the registration error.
        body_drift = [drift[0], drift[2], drift[3]]
        rows.append({"name": name, "path": str(output), "canonical": record["canonical"],
                     "score": comparison["score"], "comparison": comparison, "signals": record,
                     "head_untouched": untouched, "silhouette_drift_px": drift,
                     "body_drift_px": {"left": drift[0], "right": drift[2], "bottom": drift[3]},
                     "head_top_offset_px": drift[1]})
        print(f"  {name:<16} score {comparison['score']:<8} iris b-r "
              f"{comparison['candidate_iris_warmth']:<5} face {comparison['face_difference']:<7} "
              f"{untouched['name']}={untouched['detail']}  body drift l/r/b {body_drift} "
              f"(head top {drift[1]})", flush=True)

    scores = [row["score"] for row in rows]
    warmth = [row["comparison"]["candidate_iris_warmth"] for row in rows]
    worst_drift = max(max(row["body_drift_px"].values()) for row in rows)
    summary = {
        "score_mean": round(statistics.mean(scores), 2),
        "score_range": [min(scores), max(scores)],
        "iris_warmth_mean": round(statistics.mean(warmth), 1),
        "ceiling": ceiling["score"],
        "worst_body_drift_px": worst_drift,
        "heads_bit_exact": f"{sum(1 for r in rows if r['head_untouched']['passed'])}/{len(rows)}",
        "control": args.control,
        "control_strength": args.control_strength if args.control != "none" else None,
        "compare_to": {"phase3 (no control)": "12.71 mean, -6.0 iris, worst body drift 14 px",
                       "phase3 canny s3.0": "12.66 mean, -6.0 iris, worst body drift 2 px",
                       "headless": "17.46 mean, -11.0 iris, 0/4 kept carrier",
                       "headcrop": "20.75 mean, -20.5 iris, 2/4 kept carrier",
                       "baseline": "23.01 mean, -26.8 iris, 3/4 kept carrier"},
    }
    print(f"\n{label}: score mean {summary['score_mean']} range {summary['score_range']}, "
          f"iris mean {summary['iris_warmth_mean']}, heads bit-exact {summary['heads_bit_exact']}")
    print(f"  ceiling {summary['ceiling']} (preprocessed)")
    print(f"  worst body drift {worst_drift} px, excluding the head (doc allows 2)")
    for key, value in summary["compare_to"].items():
        print(f"  {key:<9} {value}")
    (root / f"{label}-probe.json").write_text(
        json.dumps({"reference": reference, "attempts": rows, "summary": summary,
                    "prompt": BODY_PROMPT}, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {ab.contact_sheet(root, reference, rows)}")


if __name__ == "__main__":
    main()
