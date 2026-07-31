#!/usr/bin/env python3
"""Generate the eight-slot Cover Story feedback closeout A/B round."""

import argparse
import json
from pathlib import Path

from experiment_headshots import PRODUCTION_EXPANSION, generation_prompt, identity_seed
from export_personas import atomic_text, sha256
from performer_palettes import TIGHTNESS_DIRECTION
from run_static_feedback_ab import RECIPE, reference
from run_static_performers import BASE_SEED, details, generate


VERSION = 1
COUNTS = {31: 2, 107: 1, 196: 3, 239: 3, 266: 3, 360: 3, 430: 3, 472: 2}
WARDROBES = {
    31: (
        "fitted cool-white silk top with a delicate petrol-and-black botanical pattern and a portrait neckline",
        "fitted ivory jacquard top with a subtle crimson geometric motif and a square neckline",
    ),
    239: (
        "fitted black blazer worn open over a champagne satin sweetheart-neck bodysuit",
        "cropped oxblood moto jacket over a fitted black low scoop-neck bodysuit",
        "charcoal tuxedo blazer worn open over a deep-petrol cowl-neck bodysuit",
    ),
    472: (
        "silver chainmail-inspired halter layered over a narrow opaque fitted petrol-blue bandeau top",
        "silver chainmail-inspired halter layered over a narrow opaque fitted rust bandeau top",
    ),
}
FACE_TEMPLATES = (
    "a youthful softly heart-shaped face with fresh natural proportions and a gently tapered jaw",
    "a youthful balanced oval face with softly rounded cheeks and naturally asymmetric features",
    "a youthful refined diamond-shaped face with subtle cheek definition and a softly rounded chin",
)
HAIR = {
    266: (
        "long glossy softly waved dark hair with flowing face-framing layers",
        "long polished chestnut waves with a soft side part",
        "long sleek dark hair with gentle movement and curtain layers",
    ),
    360: (
        "a glossy shoulder-length layered bob with side-swept bangs",
        "long soft waves with a relaxed center part",
        "a sleek collarbone-length lob with flowing face-framing layers",
    ),
    430: (
        "long glossy loose waves with a soft side part",
        "a sleek shoulder-length layered lob with curtain bangs",
        "long polished curls swept over one shoulder",
    ),
}


def indexed(path):
    return {
        (entry["slot"], entry["candidate"], entry["arm"]): entry
        for entry in json.loads(path.read_text())["entries"]
    }


def sources(root):
    final = indexed(root / "static-performer-feedback-final-ab-v1" / "manifest.json")
    return final


def baseline(number, final):
    candidate = 2 if number == 107 else 3 if number in {196, 360, 430} else 1
    return final[number, candidate, "B"]


def identity_candidate(number, candidate, current):
    profile, _, _, style = PRODUCTION_EXPANSION[number - 1]
    profile = dict(profile)
    profile["face"] = FACE_TEMPLATES[candidate - 1]
    profile["makeup"] = (
        "Fresh natural makeup with sheer coverage, softly defined eyes, "
        "and lightly tinted lips."
    )
    if number in {196, 266}:
        profile["age"] = 21 + candidate
    if number in HAIR:
        profile["hair"] = HAIR[number][candidate - 1]
    wardrobe = current["wardrobe"]
    return {
        **current,
        "seed": BASE_SEED + identity_seed(
            f"{profile['slug']}:static-feedback-closeout:{candidate}"
        ),
        "prompt": generation_prompt(
            profile,
            style,
            background=current["environment"],
            age_wording="band",
            wardrobe=wardrobe,
            standing=True,
        ),
    }


def changed(number, candidate, current):
    if number in {196, 266, 360, 430}:
        return identity_candidate(number, candidate, current)
    wardrobe = f"{WARDROBES[number][candidate - 1]}, {TIGHTNESS_DIRECTION}"
    if current["prompt"].count(current["wardrobe"]) != 1:
        raise ValueError(f"wardrobe is not unique in prompt {number}")
    return {
        **current,
        "wardrobe": wardrobe,
        "prompt": current["prompt"].replace(current["wardrobe"], wardrobe),
    }


def pair(number, candidate, final):
    if number == 107:
        left, right = final[number, 2, "B"], final[number, 3, "B"]
    else:
        left = baseline(number, final)
        right = changed(number, candidate, left)
    stem = f"performer-{number:03d}-c{candidate}"
    return tuple({
        **entry,
        "slot": number,
        "candidate": candidate,
        "arm": arm,
        "id": f"{stem}_{arm}",
        "stem": f"{stem}_{arm}",
    } for arm, entry in (("A", left), ("B", right)))


def schedule():
    return [
        (number, candidate)
        for number in sorted(COUNTS)
        for candidate in range(1, COUNTS[number] + 1)
    ]


def load(path):
    if not path.exists():
        return {}
    manifest = json.loads(path.read_text())
    if (
        manifest.get("version") != VERSION
        or manifest.get("schedule") != [list(item) for item in schedule()]
        or manifest.get("recipe") != RECIPE
    ):
        raise ValueError(f"incompatible closeout manifest: {path}")
    return {
        (entry["slot"], entry["candidate"], entry["arm"]): entry
        for entry in manifest["entries"]
    }


def write(output_dir, entries):
    atomic_text(output_dir / "manifest.json", json.dumps({
        "version": VERSION,
        "source": "static-performer-feedback-final-ab-v1",
        "schedule": [list(item) for item in schedule()],
        "recipe": RECIPE,
        "entries": [entries[key] for key in sorted(entries)],
    }, indent=2) + "\n")


def self_test():
    assert len(COUNTS) == 8
    assert len(schedule()) == 20
    assert COUNTS[107] == 1
    assert all("bandeau" in phrase for phrase in WARDROBES[472])
    print("static feedback closeout runner self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--root", type=Path, default=Path("/mnt/Misc/sd/cover-story"))
    parser.add_argument("--label", default="static-performer-feedback-closeout-ab-v1")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.output_dir:
        parser.error("--output-dir is required")
    if not args.server and not args.dry_run:
        parser.error("--server is required unless --dry-run is used")
    manifest_path = args.output_dir / "manifest.json"
    if args.output_dir.exists() and any(args.output_dir.iterdir()) and not manifest_path.exists():
        raise ValueError(f"refusing nonempty output directory without manifest: {args.output_dir}")

    final = sources(args.root)
    entries = load(manifest_path)
    raw_dir = args.output_dir / "raw"
    items = schedule()
    for position, (number, candidate) in enumerate(items, 1):
        pair_entries = pair(number, candidate, final)
        for entry in pair_entries:
            key = number, candidate, entry["arm"]
            source_raw = Path(entry["raw"])
            raw = raw_dir / f"{entry['id']}.png"
            entry["raw"] = str(raw)
            print(f"[{position}/{len(items)} {entry['arm']}] {entry['id']}", flush=True)
            if args.dry_run:
                print(entry["prompt"])
                continue
            previous = entries.get(key)
            if previous:
                if any(previous.get(field) != entry[field] for field in (
                    "seed", "prompt", "wardrobe",
                )):
                    raise ValueError(f"A/B details changed for {entry['id']}")
                if raw.is_file() and sha256(raw) == previous.get("source_sha256"):
                    print("already complete", flush=True)
                    continue
                raise ValueError(f"completed raw changed or disappeared for {entry['id']}")
            if entry["arm"] == "A" or number == 107:
                reference(source_raw, raw)
            else:
                entry["prompt_id"] = generate(
                    args.server, raw, entry, args.label, args.timeout, 1.5
                )
            entry.update({"source_sha256": sha256(raw), "bytes": raw.stat().st_size})
            entries[key] = entry
            write(args.output_dir, entries)
    if not args.dry_run:
        print(f"manifest has {len(entries)}/{len(items) * 2} images", flush=True)


if __name__ == "__main__":
    main()
