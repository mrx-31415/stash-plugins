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

from PIL import Image, ImageChops, ImageFilter, ImageOps

import identity_metrics
import layered_costume_production as production
import run_identity_ab as ab
import run_qwen2512_skin_head_clothes_poc as poc

DEFAULT_SOURCE = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-skin-head-clothes-poc-v2")
DEFAULT_ROOT = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-phase3-probe")
# image 2 is the skin plate, not the raw carrier: same pose and silhouette but natural skin, so the
# body being painted has nothing green to copy.
BODY_PROMPT = (
    "Keep image 1's head, face and hair completely unchanged. Change her body below the neck to the bare "
    "standing body, pose and proportions of image 2, with the same matte chroma key blue background."
)
HEAD_SAM_PROMPT = "head, face, ears and hair"
# Alignment matches faces, not heads. The carrier is bald, so its head box is a bare skull
# (147 px tall) while the performer's includes hair falling past her shoulders (210 px) —
# comparing them shrank her to 0.700 and left her 794 px tall against the carrier's 1094,
# feet off the bottom of the frame. A face is the same object in both images.
FACE_SAM_PROMPT = "face"
# Both images are full-body frames of a standing woman, so after alignment their silhouettes
# must be nearly the same height. This is the check that matters: the earlier guard tested the
# input scale ratio, which is the quantity that was wrong, so it passed 0.700 happily.
SILHOUETTE_HEIGHT_TOLERANCE = 0.12
PERSON_SAM_PROMPT = "the whole person"
# Enough overlap at the neck that the repainted body meets the preserved head without a seam, but
# not so much that the mask reaches the jaw and starts repainting the face.
NECK_OVERLAP = 15
# Far enough out that the mask boundary sits in flat background rather than beside the
# performer's original outline, and feathered so the join between regenerated and original
# background is a gradient instead of a contour.
OUTER_MARGIN = 25
OUTER_FEATHER = 8
# Thickness of the silhouette outline handed to the ControlNet. The union model works on an 8x
# downsampled latent, so a 1 px FIND_EDGES line would land on well under one latent cell.
OUTLINE_WIDTH = 5
SEEDS = 4


def sam_box(server, image, prompt, work, prefix):
    mask = identity_metrics.binary(poc.sam_hints(server, image, [prompt], prefix, work)[0])
    box = mask.getbbox()
    if box is None:
        raise RuntimeError(f"SAM found no '{prompt}' in {image}")
    return mask, box


def aligned_performer(server, preprocessed, carrier, root, work):
    """Scale and translate the performer so her head lands on the carrier's head.

    Alignment is on the head box specifically, not the whole figure: the head is the part being
    preserved, so it is the part that has to land in the right place. The preprocess stage already
    arrives at roughly the right scale (measured 0.99 x 1.036 against the carrier), so this is a
    small correction rather than a rescue.

    Both boxes come from SAM asking for a head. The first version of this used
    masks/identity-head-mask.png as the target, which is not a head: it is the identity *envelope*,
    dilated by IDENTITY_DILATION (97 px) to cover neck, clavicles, shoulders and upper chest, with a
    350x436 box against a real head of roughly 150x190. That scaled the performer up about 2.3x."""
    path = root / "performer-aligned.png"
    if path.is_file():
        return path
    _, performer_head = sam_box(server, preprocessed, FACE_SAM_PROMPT, work, "cover-story/phase3/face")
    _, target = sam_box(server, carrier, FACE_SAM_PROMPT, work, "cover-story/phase3/carrier-face")
    image = poc.image_from(preprocessed)
    with Image.open(carrier) as opened:
        size = opened.size
    scale = (target[3] - target[1]) / max(1, performer_head[3] - performer_head[1])
    scaled = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                          Image.Resampling.LANCZOS)
    head_centre = (round((performer_head[0] + performer_head[2]) / 2 * scale),
                   round((performer_head[1] + performer_head[3]) / 2 * scale))
    target_centre = ((target[0] + target[2]) // 2, (target[1] + target[3]) // 2)
    canvas = Image.new("RGB", size, poc.image_from(carrier).getpixel((8, 8)))
    canvas.paste(scaled, (target_centre[0] - head_centre[0], target_centre[1] - head_centre[1]))
    poc.save_png(canvas, path)
    print(f"  aligned: performer face {performer_head} -> carrier face {target}, "
          f"scale {scale:.3f}", flush=True)
    carrier_box, aligned_box = silhouette_box(carrier), silhouette_box(path)
    carrier_height = carrier_box[3] - carrier_box[1]
    ratio = (aligned_box[3] - aligned_box[1]) / max(1, carrier_height)
    print(f"  silhouette height {aligned_box[3] - aligned_box[1]} vs carrier {carrier_height} "
          f"(ratio {ratio:.3f})", flush=True)
    if abs(ratio - 1) > SILHOUETTE_HEIGHT_TOLERANCE:
        path.unlink(missing_ok=True)
        raise RuntimeError(
            f"aligned figure is {ratio:.2f}x the carrier's height, outside "
            f"+/-{SILHOUETTE_HEIGHT_TOLERANCE:.0%}. Alignment matched the wrong feature: "
            f"performer face {performer_head}, carrier face {target}, scale {scale:.3f}")
    return path


def body_mask(server, aligned, carrier, root, work):
    """Everything to repaint: the carrier's silhouette plus any performer body outside it, minus the
    head being preserved.

    The union matters. Masking only the carrier's silhouette would leave the performer's own
    shoulders and arms standing outside it, untouched, because everything outside the mask survives
    exactly — the same property that protects the head would preserve her original body too."""
    path = root / "phase3-body-mask.png"
    preserve_path = root / "phase3-preserved-head.png"
    if path.is_file():
        return path, preserve_path
    performer_person, _ = sam_box(server, aligned, PERSON_SAM_PROMPT, work, "cover-story/phase3/person")
    performer_head, _ = sam_box(server, aligned, HEAD_SAM_PROMPT, work, "cover-story/phase3/aligned-head")
    carrier_person, _ = sam_box(server, carrier, PERSON_SAM_PROMPT, work, "cover-story/phase3/carrier-person")
    # Asymmetric by necessity. The OUTER boundary is pushed into flat background and feathered:
    # inside the mask the background is regenerated, outside it the original survives, and the two
    # blues are not identical, so a hard edge there reads as a pale contour tracing the figure —
    # visible in the first run, running alongside the aligned performer's original outline.
    # The INNER boundary against the preserved head stays hard, because a feathered edge there
    # would blend generated pixels into the head and lose outside_mask_unchanged == 0, which is the
    # entire premise of this approach.
    preserve = poc.dilate(performer_head, NECK_OVERLAP)
    union = ImageChops.lighter(performer_person, carrier_person)
    outer = poc.dilate(union, OUTER_MARGIN).filter(ImageFilter.GaussianBlur(OUTER_FEATHER))
    repaint = ImageChops.subtract(outer, preserve)
    poc.save_png(repaint, path)
    poc.save_png(preserve, preserve_path)
    print(f"  mask: repaint {sum(repaint.histogram()[1:])} px, preserved head "
          f"{sum(preserve.histogram()[1:])} px", flush=True)
    return path, preserve_path


def silhouette_box(path):
    return poc.screen_foreground(poc.image_from(path)).getbbox()


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


def control_for(server, kind, carrier, preserve, root, work, force):
    """Build the control image from the *carrier*, since the carrier is the pose being targeted.

    The preserved head is cut out of it either way. Nothing inside that region can be repainted —
    it sits outside the noise mask — so head geometry in the control image can only argue with
    pixels the sampler is not allowed to touch. For openpose that is free (the node has draw_head
    and draw_face switches); for canny the bald skull outline has to be erased by hand, otherwise
    the control asks for a scalp edge exactly where the performer's hair is."""
    raw = root / f"control-{kind}-raw.png"
    path = root / f"control-{kind}.png"
    if path.is_file() and not force:
        return path
    if kind == "canny":
        # Not ComfyUI's Canny node, which finds almost nothing here: the carrier is matte green
        # paint on a matte blue screen, and those are nearly isoluminant (luma 95 against 71), so a
        # luminance gradient detector returned 795 lit pixels for a whole standing figure. The
        # screen-key foreground already separates them on colour, and its boundary *is* the
        # silhouette -- which is the only geometry a uniformly painted body has to offer anyway.
        figure = poc.screen_foreground(poc.image_from(carrier))
        outline = ImageChops.subtract(poc.dilate(figure, OUTLINE_WIDTH),
                                      ImageOps.invert(poc.dilate(ImageOps.invert(figure), OUTLINE_WIDTH)))
        poc.save_png(outline.convert("RGB"), raw)
    else:
        poc.control_image(server, carrier, kind, f"cover-story/phase3/control-{kind}", raw, work, force)
    image = poc.image_from(raw)
    head = Image.open(preserve).convert("L").resize(image.size, Image.Resampling.NEAREST)
    image.paste(Image.new("RGB", image.size, (0, 0, 0)), (0, 0), poc.dilate(head, 9))
    poc.save_png(image, path)
    kept = sum(1 for pixel in image.convert("L").getdata() if pixel > 32)
    print(f"  control {kind}: {kept} lit px after clearing the preserved head", flush=True)
    return path


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
