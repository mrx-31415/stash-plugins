#!/usr/bin/env python3
"""Does a featureless carrier head stop the carrier's face from reasserting itself?

Measured problem this targets: across four seeds the current construction returns the *carrier's*
eyes rather than the performer's on 2 of 4 (3 of 4 for the full-body reference). Every lever tried
so far — better reference, alignment, a second pass, more resolution — competes with the carrier's
face without removing it, and moves the average while leaving the failure rate.

A carrier with no face has nothing to reassert. The `flatface` proxy (blurring the existing
carrier's features) reached iris b-r -16 against the baseline's -29 on one sample, which is why this
is worth a real carrier rather than another single-sample probe: this runs four seeds so the numbers
are directly comparable to the four-seed baseline and headcrop rows.

Three prompts change together, and all three matter:

* the carrier prompt, so the head is generated blank;
* the skin prompt, which says "keep the bald head" today and would happily paint a face onto a
  blank one during the recolor;
* the identity prompt, which becomes an unambiguous "change the mannequin head to the woman's" —
  there is no longer a second candidate face for "the woman" to bind to.
"""

import argparse
import json
import statistics
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

import identity_metrics
import layered_costume_production as production
import run_identity_ab as ab
import run_qwen2512_skin_head_clothes_poc as poc

DEFAULT_ROOT = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-mannequin-probe")
AB_ROOT = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-identity-ab")

# Byte-identical to CARRIER_PROMPT except for the one sentence describing the face.
#
# The first attempt said "mannequin head" and the word propagated to the whole figure: the carrier
# came back as a shop dummy with joint seams at the shoulders and wrists, no navel, and elongated
# proportions. That body is not usable — it is also the body the clothing plate drapes onto. So the
# word "mannequin" appears nowhere in any generation prompt; the face is described by what it lacks,
# and the plastic vocabulary is suppressed in the negative instead.
MANNEQUIN_CARRIER_PROMPT = (
    "Full-body centered frontal bald woman in a natural relaxed standing pose on a seamless evenly lit matte "
    "chroma-key blue background. Her face is a smooth blank surface with no eyes, nose, mouth or ears. "
    "She has a slender, feminine, statuesque hourglass figure, and ample cleavage. "
    "Her head, face, neck, clavicles, shoulders, arms, hands, torso and legs are "
    "uniformly matte chroma-key green body paint; no hair, wig, clothing, pasties, accessories, gloss, latex or "
    "reflections. Keep anatomy clear, proportions natural, feet visible, and the blue background clean and uniform."
)
# generation_graph() has a real negative input, unlike edit_graph(), and this prompt has to do two
# jobs at once. Getting it wrong twice is what cost the two previous attempts:
#
#   v1  facial features suppressed, "mannequin head" in the positive
#       -> blank head, but the word propagated: shop-dummy body with joint seams and no navel
#   v2  anti-plastic terms only, facial features dropped from the negative
#       -> realistic body, but the face came straight back
#
# Both lists are needed. "featureless body" is the one term deliberately left out of the anti-plastic
# set, because it is the only one that also argues against the blank face the positive asks for.
MANNEQUIN_CARRIER_NEGATIVE = (poc.CARRIER_NEGATIVE +
                              ", eyes, nose, mouth, ears, facial features, eyebrows, lips, "
                              "mannequin, shop dummy, doll, plastic, joint seams, ball joints, "
                              "segmented limbs, glossy skin")
# SKIN_PROMPT says "keep the bald head", which on a blank head is an invitation to invent a face.
MANNEQUIN_SKIN_PROMPT = (
    "Recolor the green person to one fair, warm natural skin tone. Keep her face a smooth blank surface with no "
    "eyes, nose, mouth or ears, and keep the anatomy, pose, framing, blue background and everything else "
    "unchanged."
)
# "Change X to Y" acting on image 1, as the preprocess work established. Image 1's head is blank, so
# "the woman in image 2" has only one possible referent — which is the disambiguation this probe is
# testing. edit_graph()'s negative conditioning is hardcoded empty, so "mannequin" here would enter
# as a positive token and risk the same plasticising; "blank featureless head" says the same thing.
MANNEQUIN_IDENTITY_PROMPT = (
    "Keep image 1's pose, body, framing and blue background unchanged. Change the blank featureless head to the "
    "head of the woman in image 2, with her face and her hair. Keep her head proportional to image 1's body."
)
# No head at all. A blank head still supplies skull shape, size, jaw width and tilt, and face
# geometry is the one signal that has not improved across any variant (~37 for the blank head
# against 29 for the head-crop reference). The blank head may be removing the wrong eyes while still
# forcing the wrong geometry. With the head erased the model has only the reference to derive
# geometry from — but the neck is deliberately left intact, so placement and scale keep an anchor
# and "onto her neck" has something to refer to.
HEADLESS_ERASE_SAM_PROMPT = "head, face, ears and hair"
HEADLESS_IDENTITY_PROMPT = (
    "Keep image 1's pose, body, framing and blue background unchanged. Add the head of the woman in image 2 "
    "on top of her bare neck, with her face and her hair. Keep her head proportional to image 1's body."
)
SEEDS = 4


def head_mask(server, carrier, root, work):
    """The identity envelope, straight from SAM rather than through the PoC's reviewed-envelope
    gate: this is a probe, and the gate exists to protect production assets."""
    path = root / "mannequin-head-mask.png"
    if path.is_file():
        return path
    person, head = (identity_metrics.binary(mask) for mask in poc.sam_hints(
        server, carrier, [poc.IDENTITY_SAM_PROMPT, poc.CLOTHES_STOP_SAM_PROMPT],
        "cover-story/mannequin/masks", work))
    poc.save_png(poc.dilate(head, poc.IDENTITY_DILATION), path)
    return path


def headless_plate(server, skin, root, work):
    """Erase the head from the skin plate, leaving the neck.

    Painted rather than generated, deliberately. At denoise 1.0 with SetLatentNoiseMask the latent
    inside the mask is replaced by noise, so image 1's content there survives only as conditioning —
    it does not need to be photographic, it needs to not contain a face. Painting keeps the body
    bit-identical and costs no GPU, where a generated head removal would risk moving the body.
    """
    path = root / "skin-headless.png"
    if path.is_file():
        return path
    erase = identity_metrics.binary(poc.sam_hints(
        server, skin, [HEADLESS_ERASE_SAM_PROMPT], "cover-story/mannequin/erase", work)[0])
    image = poc.image_from(skin)
    # Sample the real screen colour rather than assuming one; the recolor stage may have shifted it.
    screen = image.getpixel((8, 8))
    # Grow past the hairline so no rim of skull survives to condition on, then feather so the cut
    # does not read as a hard silhouette edge.
    grown = poc.dilate(erase, 13).filter(ImageFilter.GaussianBlur(4))
    poc.save_png(Image.composite(Image.new("RGB", image.size, screen), image, grown), path)
    poc.save_png(grown, root / "headless-erase-mask.png")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--seeds", type=int, default=SEEDS)
    parser.add_argument("--headless", action="store_true",
                        help="erase the head entirely instead of leaving it blank")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    config = poc.load_config(poc.CONFIG_PATH)
    server = config["server"]
    production.RUN_ID = poc.POC_RUN_ID
    production.EDIT_MODEL = config["edit_model"]
    root, work = args.output_dir, args.output_dir / "_work"
    root.mkdir(parents=True, exist_ok=True)
    work.mkdir(exist_ok=True)

    reference_portrait = Path(config["performer"])
    headcrop = AB_ROOT / "reference-headcrop.png"
    if not headcrop.is_file():
        raise SystemExit(f"missing {headcrop}; run run_identity_ab.py first")

    carrier = root / "carrier-mannequin.png"
    if not carrier.is_file() or args.force:
        print("[carrier] generating the mannequin carrier", flush=True)
        result_dir = Path(work / "carrier")
        result_dir.mkdir(exist_ok=True)
        graph = production.generation_graph(
            MANNEQUIN_CARRIER_PROMPT, production.seed_for("qwen2512:carrier:mannequin"),
            "cover-story/mannequin/carrier", size=(832, 1248), canonical=False,
            negative_prompt=MANNEQUIN_CARRIER_NEGATIVE)
        from comfy import run as comfy_run
        poc.save_png(Image.open(production.pick(comfy_run(server, graph, result_dir, 2400), "-raw")
                                ).convert("RGB"), carrier)
    checks = poc.carrier_checks(carrier)
    for check in checks:
        print(f"  {'PASS' if check['passed'] else 'FAIL'} {check['name']}: {check['detail']}")
    poc.soft_free(server)

    mask = head_mask(server, carrier, root, work)
    skin = root / "skin-mannequin.png"
    if not skin.is_file() or args.force:
        print("[skin] recoloring, keeping the head blank", flush=True)
        poc.edit(server, carrier, MANNEQUIN_SKIN_PROMPT, None, None,
                 production.seed_for("qwen2512:skin-mannequin"),
                 "cover-story/mannequin/skin", skin, work, args.force)
    for check in poc.skin_checks(carrier, skin):
        print(f"  {'PASS' if check['passed'] else 'FAIL'} {check['name']}: {check['detail']}")
    poc.soft_free(server)

    if args.headless:
        skin = headless_plate(server, skin, root, work)
        identity_prompt, tag = HEADLESS_IDENTITY_PROMPT, "headless"
    else:
        identity_prompt, tag = MANNEQUIN_IDENTITY_PROMPT, "mannequin"
    print(f"\n[identity] {args.seeds} seeds, {tag} prompt, head-crop reference", flush=True)
    reference = identity_metrics.signals(server, reference_portrait, work, root, "reference")
    rows = []
    # Generate every seed before scoring any: the edits share EDIT_MODEL, while identity_metrics
    # runs SAM. Alternating them makes ComfyUI reload 19 GiB per seed.
    generated = []
    for index in range(args.seeds):
        name = f"{tag}-seed{index + 1}"
        output = root / f"identity-{name}.png"
        if not output.is_file() or args.force:
            poc.edit(server, skin, identity_prompt, mask, headcrop,
                     production.seed_for(ab.SEED_LABEL, retry=index),
                     f"cover-story/mannequin/{name}", output, work, args.force)
        generated.append((name, output))
    poc.soft_free(server)

    for name, output in generated:
        record = identity_metrics.signals(server, output, work, root, f"cand-{name}")
        comparison = identity_metrics.compare(reference, record)
        rows.append({"name": name, "path": str(output), "canonical": record["canonical"],
                     "score": comparison["score"], "comparison": comparison, "signals": record})
        print(f"  {name:<18} score {comparison['score']:<8} iris b-r "
              f"{comparison['candidate_iris_warmth']:<5} face {comparison['face_difference']}", flush=True)

    warmth = [row["comparison"]["candidate_iris_warmth"] for row in rows]
    scores = [row["score"] for row in rows]
    # The carrier reads about -29; at or below -25 the transfer kept the carrier's eyes.
    kept_carrier = sum(1 for value in warmth if value <= -25)
    summary = {
        "score_mean": round(statistics.mean(scores), 2),
        "score_range": [min(scores), max(scores)],
        "iris_warmth_mean": round(statistics.mean(warmth), 1),
        "kept_carrier_eyes": f"{kept_carrier}/{len(warmth)}",
        "compare_to": {"baseline": "23.01 mean, -26.8 iris, 3/4 kept carrier",
                       "headcrop": "20.75 mean, -20.5 iris, 2/4 kept carrier"},
    }
    print(f"\nmannequin: score mean {summary['score_mean']} range {summary['score_range']}, "
          f"iris mean {summary['iris_warmth_mean']}, kept carrier eyes {summary['kept_carrier_eyes']}")
    print(f"  baseline  {summary['compare_to']['baseline']}")
    print(f"  headcrop  {summary['compare_to']['headcrop']}")
    (root / f"{tag}-probe.json").write_text(
        json.dumps({"reference": reference, "attempts": rows, "summary": summary,
                    "prompts": {"carrier": MANNEQUIN_CARRIER_PROMPT, "skin": MANNEQUIN_SKIN_PROMPT,
                                "identity": identity_prompt}}, indent=2) + "\n",
        encoding="utf-8")
    print(f"\nwrote {ab.contact_sheet(root, reference, rows)}")


if __name__ == "__main__":
    main()
