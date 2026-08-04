#!/usr/bin/env python3
"""Compare identity-transfer constructions against the performer's shipped portrait.

The composite has to look like the same person as
`plugins/cover-story/assets/performers/actor-NNN.avif`. It currently does not: identity_metrics.py
scores the accepted plate 20.45 against the reference, where an unrelated performer scores 26.37.

Every attempt here is one masked edit differing from the baseline in exactly one way, written to its
own directory, scored by identity_metrics.py and laid out side by side for review. Nothing
overwrites the accepted run.

Attempts:

  baseline    the accepted plate; no GPU, reused for comparison
  headcrop    image 2 is a bare head crop of the preprocessed performer rather than her whole body.
              LAYERED_COSTUME_PIPELINE_REFERENCE.md's own probe log says a full-body reference
              "pulled the reference performer's proportions into the edit and was rejected", and
              that the corrected pass used "a head-only performer crop as the identity reference".
  headcrop-aligned
              the same crop, but pasted at the carrier's head position on a matching canvas, so the
              reference's face sits where the mask is about to be painted. Alignment previously
              moved the preprocess stage from scale 2.5 to 1.0, so it is worth separating from the
              crop itself.
  twostage    image 1 is the baseline result instead of the skin plate, so the carrier's face is no
              longer the spatial prior on the second pass. Reference unchanged, to isolate the pass.
  altmodel    baseline with qwen_image_edit_fp8_e4m3fn, to establish whether the failure belongs to
              the model or to the construction.
"""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

import identity_metrics
import layered_costume_production as production
import run_qwen2512_skin_head_clothes_poc as poc

DEFAULT_SOURCE = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-skin-head-clothes-poc-v2")
DEFAULT_ROOT = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-identity-ab")
ALT_EDIT_MODEL = "qwen_image_edit_fp8_e4m3fn.safetensors"
# edit_graph() runs FluxKontextImageScale, which resizes to whichever of these has the nearest
# aspect ratio (ComfyUI comfy_extras/nodes_flux.py). Picking any other size silently distorts:
# a 928x1120 crop (aspect 0.829) was stretched to 944x1104 (0.855), a 3% error, before this table
# was used to choose the crop instead. 832x1248 is in here, which is why the full-size stages see
# the node as a no-op.
PREFERRED_KONTEXT_RESOLUTIONS = [
    (672, 1568), (688, 1504), (720, 1456), (752, 1392), (800, 1328), (832, 1248),
    (880, 1184), (944, 1104), (1024, 1024), (1104, 944), (1184, 880), (1248, 832),
    (1328, 800), (1392, 752), (1456, 720), (1504, 688), (1568, 672),
]
SEED_LABEL = "qwen2512:identity-head"


def head_crop(server, preprocessed, work, out_dir):
    """Tight crop of the performer's head and hair, with a little breathing room."""
    face, hair = (identity_metrics.binary(mask) for mask in
                  poc.sam_hints(server, preprocessed, ["face", "hair"], "cover-story/identity-ab/headcrop", work))
    face_box, hair_box = face.getbbox(), hair.getbbox()
    if face_box is None:
        raise RuntimeError(f"SAM found no face in {preprocessed}")
    box = face_box if hair_box is None else (min(face_box[0], hair_box[0]), min(face_box[1], hair_box[1]),
                                             max(face_box[2], hair_box[2]), max(face_box[3], hair_box[3]))
    image = poc.image_from(preprocessed)
    pad = round(0.08 * max(box[2] - box[0], box[3] - box[1]))
    box = (max(0, box[0] - pad), max(0, box[1] - pad),
           min(image.width, box[2] + pad), min(image.height, box[3] + pad))
    crop = image.crop(box)
    poc.save_png(crop, out_dir / "reference-headcrop.png")
    return out_dir / "reference-headcrop.png", box, face_box


def head_crop_aligned(crop_path, face_box, head_mask, out_dir):
    """The same crop, scaled and positioned so the performer's head lands where the mask is.

    Takes the crop rather than recomputing it: an identical SAM graph is served from ComfyUI's
    cache and comes back with no images at all, which is what broke the first run of this script."""
    target = identity_metrics.binary(Image.open(head_mask)).getbbox()
    if target is None:
        raise RuntimeError(f"{head_mask} is empty")
    crop = poc.image_from(crop_path)
    # Scale on the face box, not the padded crop: the face is what has to line up.
    scale = (target[3] - target[1]) / max(1, face_box[3] - face_box[1])
    scaled = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
                         Image.Resampling.LANCZOS)
    with Image.open(head_mask) as opened:
        size = opened.size
    # Matte blue, matching the plate this reference is describing.
    canvas = Image.new("RGB", size, (5, 83, 168))
    centre = ((target[0] + target[2]) // 2, (target[1] + target[3]) // 2)
    canvas.paste(scaled, (centre[0] - scaled.width // 2, centre[1] - scaled.height // 2))
    poc.save_png(canvas, out_dir / "reference-headcrop-aligned.png")
    return out_dir / "reference-headcrop-aligned.png"


def flattened_face(server, skin_plate, work, out_dir):
    """Blur the carrier's facial features away, leaving a mannequin-like head.

    The skin plate that serves as image 1 carries a *fully realized* face — a specific tanned,
    dark-eyed woman — sitting at exactly the pixels the mask is about to repaint, and the baseline
    result looks like her rather than like the performer. This is the cheap test of whether removing
    that competing identity helps: no carrier regeneration, one generation. Head shape, position,
    scale and skin tone all survive, so the model keeps every anchor it needs except the face."""
    face = identity_metrics.binary(
        poc.sam_hints(server, skin_plate, ["face"], "cover-story/identity-ab/flatface", work)[0])
    box = face.getbbox()
    if box is None:
        raise RuntimeError(f"SAM found no face in {skin_plate}")
    image = poc.image_from(skin_plate)
    # Radius scaled to the face so this behaves the same on any carrier size.
    radius = max(8, round(0.09 * max(box[2] - box[0], box[3] - box[1])))
    blurred = image.filter(ImageFilter.GaussianBlur(radius))
    # Feather the mask so the flattened region blends into the surrounding head rather than
    # leaving a hard disc the edit would have to reconcile.
    soft = face.filter(ImageFilter.GaussianBlur(radius // 2))
    flattened = Image.composite(blurred, image, soft)
    path = out_dir / "skin-tone-flatface.png"
    poc.save_png(flattened, path)
    return path


def kontext_crop(box, image_size):
    """Grow a crop box to the nearest Kontext-native aspect, and give the size to scale it to.

    Matching the aspect exactly makes FluxKontextImageScale a no-op, so the head is upscaled once by
    lanczos rather than upscaled and then resampled again into a different shape."""
    width, height = box[2] - box[0], box[3] - box[1]
    _, target_w, target_h = min((abs(width / height - w / h), w, h)
                                for w, h in PREFERRED_KONTEXT_RESOLUTIONS)
    aspect = target_w / target_h
    # Grow the short side rather than shrink the long one: the head must stay fully inside.
    if width / height < aspect:
        width = round(height * aspect)
    else:
        height = round(width / aspect)
    centre = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
    left, top = centre[0] - width // 2, centre[1] - height // 2
    # Slide back inside the canvas before clamping, so a head near an edge keeps its full box.
    left = max(0, min(left, image_size[0] - width))
    top = max(0, min(top, image_size[1] - height))
    right, bottom = min(image_size[0], left + width), min(image_size[1], top + height)
    return (left, top, right, bottom), (target_w, target_h)


def hires_head_edit(server, skin_plate, head_mask, reference, seed, root, work, force):
    """Run the identity edit on an upscaled crop of just the head, then paste it back.

    Measured motivation: the plate's face is already the same pixel size as the reference portrait
    (350x436 against 354x432) yet carries a third of the edge energy (1.56 against 4.71). More
    canvas would not change that — the head is 350x436 either way. What changes it is making the
    face the *subject* of the edit instead of 14.7% of a full-body composition, at something near
    the edit model's native ~1 MP working resolution."""
    output = root / "identity-hires-head.png"
    if output.is_file() and not force:
        return output
    plate = poc.image_from(skin_plate)
    with Image.open(head_mask) as opened:
        mask = identity_metrics.binary(opened)
    box = mask.getbbox()
    pad_x, pad_y = round((box[2] - box[0]) * 0.12), round((box[3] - box[1]) * 0.12)
    padded = (max(0, box[0] - pad_x), max(0, box[1] - pad_y),
              min(plate.width, box[2] + pad_x), min(plate.height, box[3] + pad_y))
    crop_box, size = kontext_crop(padded, plate.size)
    crop = plate.crop(crop_box)
    crop_mask = mask.crop(crop_box)
    poc.save_png(crop.resize(size, Image.Resampling.LANCZOS), root / "hires-head-source.png")
    poc.save_png(crop_mask.resize(size, Image.Resampling.LANCZOS), root / "hires-head-mask.png")
    print(f"  crop {crop_box} {crop.size} aspect {crop.width / crop.height:.3f} -> {size} "
          f"aspect {size[0] / size[1]:.3f} ({size[0] * size[1] / 1e6:.2f} MP)", flush=True)
    edited = root / "hires-head-edited.png"
    poc.edit(server, root / "hires-head-source.png", poc.IDENTITY_PROMPT, root / "hires-head-mask.png",
             reference, seed, "cover-story/identity-ab/hires-head", edited, work, force)
    result = poc.image_from(edited).resize(crop.size, Image.Resampling.LANCZOS)
    # Paste through the original mask so nothing outside it moves, exactly as the full-size stage
    # relies on ImageCompositeMasked doing.
    merged = plate.copy()
    merged.paste(result, crop_box[:2], crop_mask.filter(ImageFilter.GaussianBlur(1)))
    poc.save_png(merged, output)
    return output


def attempts(server, source, root, work, force):
    """Build each attempt's inputs. Returns name -> (image1, reference, edit_model)."""
    preprocessed = source / "preprocessed.png"
    head_mask = source / "masks" / "identity-head-mask.png"
    crop_path, _, face_box = head_crop(server, preprocessed, work, root)
    aligned_path = head_crop_aligned(crop_path, face_box, head_mask, root)
    return {
        "headcrop": (source / "skin-tone.png", crop_path, production.EDIT_MODEL),
        # Both new attempts reuse the head crop, which won Phase 2 on its own.
        "flatface": (flattened_face(server, source / "skin-tone.png", work, root), crop_path,
                     production.EDIT_MODEL),
        "headcrop-aligned": (source / "skin-tone.png", aligned_path, production.EDIT_MODEL),
        "twostage": (source / "identity.png", preprocessed, production.EDIT_MODEL),
        "altmodel": (source / "skin-tone.png", preprocessed, ALT_EDIT_MODEL),
    }


def contact_sheet(root, reference, rows):
    """Reference beside every attempt at face-crop scale, because you make the accept call."""
    cells = []
    for row in sorted(rows, key=lambda item: item["score"] if item["score"] is not None else 1e9):
        comparison = row["comparison"]
        cells.append(f"""
    <figure>
      <img src="{Path(row['canonical']).name}" alt="{row['name']}">
      <figcaption>
        <strong>{row['name']}</strong>
        <span class="score">score {row['score']}</span>
        <span>iris b-r {comparison['candidate_iris_warmth']} (want {comparison['reference_iris_warmth']})</span>
        <span>skin {comparison['skin_rgb_delta']} &middot; hair {comparison['hair_rgb_delta']} &middot; face {comparison['face_difference']}</span>
      </figcaption>
    </figure>""")
    html = f"""<!doctype html><meta charset="utf-8"><title>Identity A/B</title>
<style>
 body {{ background:#14161c; color:#e8e8ea; font:14px/1.5 system-ui,sans-serif; margin:24px }}
 h1 {{ font-size:18px; font-weight:600 }}
 p.note {{ color:#9aa0aa; max-width:70ch }}
 .grid {{ display:flex; flex-wrap:wrap; gap:18px; align-items:flex-start }}
 figure {{ margin:0; background:#1c1f27; border-radius:8px; padding:10px; width:280px }}
 img {{ width:100%; border-radius:4px; display:block }}
 figcaption {{ display:flex; flex-direction:column; gap:2px; margin-top:8px; font-size:12px; color:#9aa0aa }}
 figcaption strong {{ color:#e8e8ea; font-size:13px }}
 .score {{ color:#7fd1a4 }}
 .ref {{ outline:2px solid #7fd1a4 }}
</style>
<h1>Identity transfer &mdash; actor-266, Laura Everly</h1>
<p class="note">Persona records Blue-Gray eyes and Brunette hair. Lower score is closer to the
reference portrait. Calibration: reference against itself 0.00, the previously accepted plate 20.45,
an unrelated performer 26.37 &mdash; so anything at or above 20 is not this person.</p>
<div class="grid">
    <figure class="ref">
      <img src="{Path(reference['canonical']).name}" alt="reference">
      <figcaption><strong>reference portrait</strong><span class="score">score 0.00</span>
      <span>iris b-r {reference['iris_rgb'][2] - reference['iris_rgb'][0] if reference['iris_rgb'] else '?'}</span></figcaption>
    </figure>{''.join(cells)}
</div>
"""
    (root / "review.html").write_text(html, encoding="utf-8")
    return root / "review.html"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="accepted run to draw inputs from")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--actor", default="actor-266")
    parser.add_argument("--only", action="append", default=[], help="run only these attempt names")
    parser.add_argument("--variance", type=int, default=0, metavar="N",
                        help="re-run one construction at N extra seeds and report the spread, to "
                             "establish whether attempt-to-attempt differences are signal or "
                             "generation noise")
    parser.add_argument("--variance-of", default="headcrop",
                        help="which construction --variance repeats (default: headcrop)")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    config = poc.load_config(poc.CONFIG_PATH)
    server = config["server"]
    production.RUN_ID = poc.POC_RUN_ID
    production.EDIT_MODEL = config["edit_model"]
    root, work = args.output_dir, args.output_dir / "_work"
    root.mkdir(parents=True, exist_ok=True)
    work.mkdir(exist_ok=True)
    people = identity_metrics.personas()
    persona = people.get(args.actor, {})

    reference_portrait = Path(config["performer"])
    print(f"reference {reference_portrait}")
    reference = identity_metrics.signals(server, reference_portrait, work, root, "reference")
    print(f"  {identity_metrics.describe(persona, reference)}\n")

    plan = attempts(server, args.source, root, work, args.force)
    plan = {name: value for name, value in plan.items() if not args.only or name in args.only}
    head_mask = args.source / "masks" / "identity-head-mask.png"

    rows = []
    # The accepted plate is scored too, so every attempt is read against a known point.
    candidates = {"baseline": args.source / "identity.png"}
    for name, (image1, ref_image, model) in plan.items():
        output = root / f"identity-{name}.png"
        if not output.is_file() or args.force:
            print(f"[{name}] image1={Path(image1).name} ref={Path(ref_image).name} model={model}", flush=True)
            production.EDIT_MODEL = model
            poc.edit(server, image1, poc.IDENTITY_PROMPT, head_mask, ref_image,
                     production.seed_for(SEED_LABEL),
                     f"cover-story/identity-ab/{name}", output, work, args.force)
            poc.soft_free(server)
        candidates[name] = output
    production.EDIT_MODEL = config["edit_model"]

    # Handled separately: it edits an upscaled crop and pastes the result back, so it does not fit
    # the uniform "one full-size masked edit" shape the loop above assumes.
    if not args.only or "hires-head" in args.only:
        print("[hires-head] editing an upscaled head crop", flush=True)
        candidates["hires-head"] = hires_head_edit(
            server, args.source / "skin-tone.png", head_mask, root / "reference-headcrop.png",
            production.seed_for(SEED_LABEL), root, work, args.force)
        poc.soft_free(server)

    # Same construction, different seeds. Without this number an A/B table of single samples is
    # unreadable: a re-run of hires-head with a 16 px wider crop moved face_difference from 26.83 to
    # 34.34, which is larger than the gap between several of the attempts above.
    if args.variance:
        # The baseline is not in `plan` (it is the accepted run, reused rather than regenerated), so
        # spell out its construction here: whole preprocessed body as the reference.
        constructions = dict(plan, baseline=(args.source / "skin-tone.png",
                                             args.source / "preprocessed.png", config["edit_model"]))
        if args.variance_of not in constructions:
            raise SystemExit(f"--variance-of {args.variance_of}: choose one of {sorted(constructions)}")
        image1, ref_image, model = constructions[args.variance_of]
        production.EDIT_MODEL = model
        for index in range(args.variance):
            name = f"{args.variance_of}-seed{index + 2}"
            output = root / f"identity-{name}.png"
            if not output.is_file() or args.force:
                print(f"[{name}] variance probe", flush=True)
                poc.edit(server, image1, poc.IDENTITY_PROMPT, head_mask, ref_image,
                         production.seed_for(SEED_LABEL, retry=index + 1),
                         f"cover-story/identity-ab/{name}", output, work, args.force)
                poc.soft_free(server)
            candidates[name] = output
        production.EDIT_MODEL = config["edit_model"]

    # Pick up every attempt already on disk, not just the ones this invocation generated. Without
    # this, a run with --variance-of baseline silently drops the headcrop seeds from the results
    # file and the contact sheet, and a comparison across constructions reads n=1.
    for existing in sorted(root.glob("identity-*.png")):
        # edit() writes a "-raw" debug sibling for every masked edit: the pre-composite output,
        # not a plate. Scoring those doubles the table with near-duplicates.
        if existing.stem.endswith("-raw"):
            continue
        candidates.setdefault(existing.stem.removeprefix("identity-"), existing)

    for name, path in candidates.items():
        if not Path(path).is_file():
            print(f"  {name}: missing {path}")
            continue
        record = identity_metrics.signals(server, path, work, root, f"cand-{name}")
        comparison = identity_metrics.compare(reference, record)
        rows.append({"name": name, "path": str(path), "canonical": record["canonical"],
                     "score": comparison["score"], "comparison": comparison, "signals": record})
        print(f"{name:<18} score {comparison['score']:<8} iris b-r {comparison['candidate_iris_warmth']:<5} "
              f"skin {comparison['skin_rgb_delta']:<6} hair {comparison['hair_rgb_delta']:<6} "
              f"face {comparison['face_difference']}")

    (root / "identity-ab.json").write_text(
        json.dumps({"actor": args.actor, "persona": persona, "reference": reference, "attempts": rows},
                   indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {contact_sheet(root, reference, rows)}")


if __name__ == "__main__":
    main()
