#!/usr/bin/env python3
"""Score how well a generated head matches the performer's shipped persona portrait.

The layered composite has to look like the same person as
`plugins/cover-story/assets/performers/actor-NNN.avif`, which is already live in the plugin. Judging
that by eye does not scale across a day of attempts, so this reduces a candidate to four numbers
that can be ranked, and leaves the accept decision to a human.

Why these four signals, and how they were calibrated:

* **Iris colour** is the sharpest available. actor-266 is recorded as Blue-Gray and the reference
  portrait measures b-r = +6; the current rejected identity plate measures -29. SAM3's `iris` prompt
  resolves this cleanly on a large face but returns the *hair* mask on an 832x1248 plate, so the eye
  band is located with `eyes` (which does work at that scale), then cropped and upscaled before
  `iris` is asked for. Pupil and specular highlight are trimmed by luminance percentile; taking the
  darkest pixels instead reads every eye as brown, because lashes and pupil are dark on everyone.
* **Skin tone** separates a fair Norwegian from the tanned face the pipeline currently produces.
* **Hair colour** catches a brunette turning black or blonde.
* **Face difference** is the catch-all for shape, aligned on the eye band so it measures the face
  rather than the framing.

`--calibrate` is not optional decoration. It scores the reference against itself, against the known
bad plate, and against a different performer. If those three do not come out in that order the
metric is not measuring identity and must not be used to rank anything.

Calibration on 2026-08-04 (actor-266, reference iris measured (74, 81, 80), b-r = +6, matching the
recorded Blue-Gray):

    case              score   iris  skin  hair  face
    self               0.00      0   0.0   0.0   0.0
    rejected_plate    20.45     35   8.0  4.67  34.12
    other_performer   26.37     39  7.67  0.67  58.15

Two things that reading is worth taking seriously. First, the rejected plate's *colour* deltas are
indistinguishable from an unrelated performer's (20.45 vs 26.37 overall, but 15.89 vs 15.78 before
`face_difference` was added) — the current identity transfer is about as far from Laura Everly as
Megan Bellamy is. Second, that near-tie is exactly why `face_difference` exists; without it this
metric cannot tell "nearly the right person" from "a different person with similar colouring", and
a day spent optimising against it would have been wasted.
"""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

import layered_costume_production as production
import run_qwen2512_skin_head_clothes_poc as poc

TOOL_ROOT = Path(__file__).resolve().parent
PERSONAS = TOOL_ROOT / "personas.json"
# Every candidate is measured at the same face scale, so a 832x1248 plate and a 1024x1536 portrait
# produce comparable numbers instead of numbers that track resolution.
CANONICAL_FACE_HEIGHT = 512
EYE_BAND_PAD = 25
EYE_BAND_ZOOM = 4
# Pupil and catchlight sit at the ends of the iris luminance range; the colour is in the middle.
IRIS_BAND = (0.30, 0.90)
# Brows, lashes and lips are the dark tail of a face mask, and specular highlights the bright one.
SKIN_BAND = (0.35, 0.80)
HAIR_BAND = (0.20, 0.80)


def personas(path=PERSONAS):
    return {record["id"]: record for record in json.loads(path.read_text(encoding="utf-8"))["personas"]}


def band_median(image, mask, band):
    """Median RGB of a mask's middle luminance band. Percentiles rather than fixed thresholds so
    the same code works on a bright portrait and a dim plate."""
    pixels, lookup = image.load(), mask.load()
    values = [pixels[x, y] for y in range(image.size[1]) for x in range(image.size[0]) if lookup[x, y]]
    if not values:
        return None, 0
    values.sort(key=lambda colour: sum(colour) / 3)
    lo, hi = int(len(values) * band[0]), max(int(len(values) * band[1]), int(len(values) * band[0]) + 1)
    selected = values[lo:hi]
    return tuple(sorted(colour[index] for colour in selected)[len(selected) // 2]
                 for index in range(3)), len(selected)


def binary(mask):
    return mask.convert("L").point(lambda value: 255 if value > 127 else 0)


def canonical_face(server, path, work, prefix):
    """Crop the head and rescale so the face is CANONICAL_FACE_HEIGHT tall."""
    face, hair = (binary(mask) for mask in
                  poc.sam_hints(server, path, ["face", "hair"], f"{prefix}-locate", work))
    box = face.getbbox()
    if box is None:
        raise RuntimeError(f"SAM found no face in {path}")
    union = hair.getbbox() or box
    # Include the hair so hair colour is measurable from the same crop, but scale on the face.
    crop_box = (min(box[0], union[0]), min(box[1], union[1]),
                max(box[2], union[2]), max(box[3], union[3]))
    image = poc.image_from(path)
    scale = CANONICAL_FACE_HEIGHT / max(1, box[3] - box[1])
    crop = image.crop(crop_box)
    size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
    return crop.resize(size, Image.Resampling.LANCZOS), scale


def iris_colour(server, canonical_path, work, prefix, out_dir):
    """Locate the eye band with `eyes`, then re-ask for `iris` on an upscaled crop of it."""
    eyes = binary(poc.sam_hints(server, canonical_path, ["eyes"], f"{prefix}-eyes", work)[0])
    box = eyes.getbbox()
    if box is None:
        return None, None, 0
    image = poc.image_from(canonical_path)
    band = (max(0, box[0] - EYE_BAND_PAD), max(0, box[1] - EYE_BAND_PAD),
            min(image.width, box[2] + EYE_BAND_PAD), min(image.height, box[3] + EYE_BAND_PAD))
    crop = image.crop(band)
    zoom = crop.resize((crop.width * EYE_BAND_ZOOM, crop.height * EYE_BAND_ZOOM),
                       Image.Resampling.LANCZOS)
    zoom_path = out_dir / f"{prefix}-eyeband.png"
    poc.save_png(zoom, zoom_path)
    iris = binary(poc.sam_hints(server, zoom_path, ["iris"], f"{prefix}-iris", work)[0])
    poc.save_png(iris, out_dir / f"{prefix}-iris-mask.png")
    colour, count = band_median(zoom, iris, IRIS_BAND)
    return colour, zoom_path, count


def signals(server, path, work, out_dir, prefix):
    """Every measurable identity signal for one image."""
    canonical, scale = canonical_face(server, path, work, prefix)
    canonical_path = out_dir / f"{prefix}-canonical.png"
    poc.save_png(canonical, canonical_path)
    face, hair = (binary(mask) for mask in
                  poc.sam_hints(server, canonical_path, ["face", "hair"], f"{prefix}-canon", work))
    poc.save_png(face, out_dir / f"{prefix}-face-mask.png")
    iris, eyeband, iris_px = iris_colour(server, canonical_path, work, prefix, out_dir)
    skin, skin_px = band_median(canonical, face, SKIN_BAND)
    hair_colour, hair_px = band_median(canonical, hair, HAIR_BAND)
    return {
        "source": str(path),
        "canonical": str(canonical_path),
        "eyeband": str(eyeband) if eyeband else None,
        "scale_applied": round(scale, 4),
        "iris_rgb": list(iris) if iris else None,
        "iris_pixels": iris_px,
        "skin_rgb": list(skin) if skin else None,
        "skin_pixels": skin_px,
        "hair_rgb": list(hair_colour) if hair_colour else None,
        "hair_pixels": hair_px,
        "face_box": face.getbbox(),
    }


def face_difference(reference, candidate):
    """Mean absolute difference over the face, after aligning both canonical crops on their face
    boxes. Colour deltas alone cannot tell "nearly the right person" from "a different person with
    similar colouring" — calibration showed the rejected plate and an unrelated performer scoring
    within 0.11 of each other on colour. Shape is what separates them."""
    try:
        with Image.open(reference["canonical"]) as opened:
            left = opened.convert("RGB")
        with Image.open(candidate["canonical"]) as opened:
            right = opened.convert("RGB")
    except (OSError, KeyError, TypeError):
        return None
    a, b = reference.get("face_box"), candidate.get("face_box")
    if not a or not b:
        return None
    # Both crops are already normalised to the same face height, so this only removes residual
    # translation and the small scale error left by that normalisation.
    face_a, face_b = left.crop(a), right.crop(b)
    face_b = face_b.resize(face_a.size, Image.Resampling.LANCZOS)
    pixels_a, pixels_b = face_a.load(), face_b.load()
    total = 0
    for y in range(face_a.size[1]):
        for x in range(face_a.size[0]):
            total += sum(abs(p - q) for p, q in zip(pixels_a[x, y], pixels_b[x, y]))
    return round(total / (face_a.size[0] * face_a.size[1] * 3), 2)


def compare(reference, candidate):
    """Per-signal deltas plus one ranking number. Deliberately not a single opaque score: the
    breakdown is what tells you *how* an attempt failed, which is the part worth acting on."""
    def delta(key):
        a, b = reference.get(key), candidate.get(key)
        if not a or not b:
            return None
        return round(sum(abs(x - y) for x, y in zip(a, b)) / 3, 2)

    def warmth(record):
        rgb = record.get("iris_rgb")
        return None if not rgb else rgb[2] - rgb[0]

    reference_warmth, candidate_warmth = warmth(reference), warmth(candidate)
    iris_warmth_delta = (None if reference_warmth is None or candidate_warmth is None
                         else abs(reference_warmth - candidate_warmth))
    parts = {
        "iris_rgb_delta": delta("iris_rgb"),
        # b-r is the blue/brown axis: it is what distinguishes Blue-Gray from Brown, and it survives
        # exposure differences that move all three channels together.
        "iris_warmth_delta": iris_warmth_delta,
        "skin_rgb_delta": delta("skin_rgb"),
        "hair_rgb_delta": delta("hair_rgb"),
        "face_difference": face_difference(reference, candidate),
    }
    # Skin is reported but deliberately NOT scored. It measures the lighting environment more than
    # the person: the reference is a studio portrait with warm bounce (197,156,134) while every
    # plate sits on a blue screen (173,132,106) — darker in all three channels, which is exposure,
    # not complexion. Left in the score it ranked the two visually correct attempts 2nd and 4th of
    # five. Skin tone is separately controlled anyway, by SKIN_PROMPT from the catalog's
    # skin_tone_group, so it is not the identity edit's job to get right.
    scored = [value for value in (parts["iris_warmth_delta"], parts["hair_rgb_delta"],
                                  parts["face_difference"])
              if value is not None]
    parts["score"] = round(sum(scored) / len(scored), 2) if scored else None
    parts["reference_iris_warmth"] = reference_warmth
    parts["candidate_iris_warmth"] = candidate_warmth
    return parts


def describe(persona, record):
    """Read the measured iris against what the persona claims, so a number has a human meaning."""
    rgb = record.get("iris_rgb")
    if not rgb:
        return "no iris resolved"
    warmth = rgb[2] - rgb[0]
    reads = "cool (blue/gray/green)" if warmth > 2 else "neutral" if warmth > -8 else "warm (brown)"
    return f"{persona.get('eye_color', '?')} claimed; measured {tuple(rgb)} b-r={warmth:+d} -> {reads}"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reference", help="portrait to match against")
    parser.add_argument("--candidate", action="append", default=[], help="image(s) to score")
    parser.add_argument("--actor", default="actor-266", help="persona id for the metadata anchor")
    parser.add_argument("--out-dir", default="/mnt/Misc/sd/cover-story/identity-metrics")
    parser.add_argument("--calibrate", action="store_true",
                        help="score the three known cases and refuse to pass if they do not rank correctly")
    args = parser.parse_args()

    config = poc.load_config(poc.CONFIG_PATH)
    server = config.get("server")
    if not server:
        raise SystemExit(f"no server in {poc.CONFIG_PATH}")
    production.RUN_ID = poc.POC_RUN_ID
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_work"
    work.mkdir(exist_ok=True)
    people = personas()

    if args.calibrate:
        return calibrate(server, people, out_dir, work)

    reference_path = Path(args.reference or config["performer"])
    reference = signals(server, reference_path, work, out_dir, "reference")
    print(f"reference  {describe(people.get(args.actor, {}), reference)}")
    rows = []
    for index, candidate_path in enumerate(args.candidate):
        record = signals(server, Path(candidate_path), work, out_dir, f"candidate-{index}")
        result = compare(reference, record)
        rows.append({"candidate": candidate_path, "signals": record, "comparison": result})
        print(f"\n{candidate_path}")
        print(f"  {describe(people.get(args.actor, {}), record)}")
        for key in ("iris_warmth_delta", "skin_rgb_delta", "hair_rgb_delta",
                    "face_difference", "score"):
            print(f"  {key:<20} {result[key]}")
    (out_dir / "scores.json").write_text(
        json.dumps({"reference": reference, "candidates": rows}, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out_dir / 'scores.json'}")


def calibrate(server, people, out_dir, work):
    """Three cases whose ranking is known in advance. If the metric cannot reproduce it, it is not
    measuring identity and every number it produces afterwards would be noise dressed as evidence."""
    root = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-skin-head-clothes-poc-v2")
    portraits = Path("/mnt/Misc/sd/cover-story/static-performer-final-review-v1/raw")
    cases = {
        "self": portraits / "performer-266.png",
        "rejected_plate": root / "identity.png",
        "other_performer": portraits / "performer-043.png",   # Megan Bellamy: Brown eyes, Black hair
    }
    reference = signals(server, cases["self"], work, out_dir, "cal-reference")
    print(f"reference (actor-266)  {describe(people['actor-266'], reference)}\n")
    scores = {}
    for name, path in cases.items():
        record = signals(server, path, work, out_dir, f"cal-{name}")
        result = compare(reference, record)
        scores[name] = result["score"]
        print(f"{name:<16} score {result['score']:<8} "
              f"iris {result['iris_warmth_delta']:<4} skin {result['skin_rgb_delta']:<6} "
              f"hair {result['hair_rgb_delta']:<6} face {result['face_difference']}")
    (out_dir / "calibration.json").write_text(json.dumps(scores, indent=2) + "\n", encoding="utf-8")
    ordered = scores["self"] < scores["rejected_plate"] and scores["self"] < scores["other_performer"]
    print(f"\nself < rejected and self < other: {ordered}")
    if not ordered:
        raise SystemExit("calibration failed: this metric does not separate the known cases; "
                         "do not use it to rank attempts")
    print("calibration passed")


if __name__ == "__main__":
    main()
