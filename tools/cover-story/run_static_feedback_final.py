#!/usr/bin/env python3
"""Generate the final focused Cover Story feedback follow-up A/B round."""

import argparse
import json
from pathlib import Path

from experiment_headshots import PRODUCTION_EXPANSION, generation_prompt, identity_seed
from export_personas import atomic_text, sha256
from performer_palettes import TIGHTNESS_DIRECTION
from run_static_feedback_ab import RECIPE, reference
from run_static_performers import BASE_SEED, details, generate
from static_feedback_specs import WARDROBE_OVERRIDES


VERSION = 1
FACE_SLOTS = {24, 25, 40, 48, 107, 196, 360, 368, 383, 430}
STYLE_SLOTS = {31, 74, 81, 92, 225, 239, 266, 272, 284, 380, 472}
HAIR_SLOTS = {411}
RUNOFFS = {471: (1, 2), 476: (1, 3)}
SLOTS = FACE_SLOTS | STYLE_SLOTS | HAIR_SLOTS | set(RUNOFFS)

WARDROBES = {
    31: "fitted cool-white silk top with a fine black-and-petrol Art Deco pattern and a portrait neckline",
    74: "fitted deep-petrol jersey top with a sculpted cowl neckline",
    81: "silver chainmail-inspired halter layered over a narrow opaque fitted ivory bandeau top",
    92: "silver chainmail-inspired halter layered over a narrow opaque fitted black bandeau top",
    225: "cropped oxblood suede vest over a fitted black scoop-neck bodysuit",
    239: "fitted black blazer worn open over a low scoop-neck deep-emerald satin bodysuit",
    266: "fitted deep-petrol satin one-shoulder bodice with sculpted draping",
    272: "silver chainmail-inspired halter layered over a narrow opaque fitted petrol-blue bandeau top",
    284: "silver chainmail-inspired halter layered over a narrow opaque fitted ivory bandeau top",
    472: "silver chainmail-inspired halter layered over a narrow opaque fitted champagne bandeau top",
}
HAIR = {
    266: "long softly waved dark hair with polished face-framing layers",
    411: "long glossy softly waved hair with flowing face-framing layers",
}
COMPOSITIONS = {
    380: "Chest-up composition framed just below the bust, with no lower torso or legs visible",
}
FACE_TEMPLATES = (
    "a softly heart-shaped face with naturally asymmetric features and a gently tapered jaw",
    "a balanced oval face with softly defined cheeks and a naturally expressive smile",
    "a refined oblong face with subtle cheek definition and a softly rounded chin",
)
SELECTED_STYLING_B = {48, 74, 92, 225, 239, 266, 272, 284, 472}


def manifests(root):
    def indexed(name):
        entries = json.loads((root / name / "manifest.json").read_text())["entries"]
        return {
            (entry["slot"], entry["candidate"], entry["arm"]): entry
            for entry in entries
        }
    v3 = json.loads((
        root / "static-performer-production-blur-q60-v3" / "manifest.json"
    ).read_text())["entries"]
    return (
        {entry["slot"]: entry for entry in v3},
        indexed("static-performer-feedback-styling-ab-v1"),
        indexed("static-performer-feedback-identity-ab-v1"),
    )


def chosen(number, v3, styling, identity):
    if number in SELECTED_STYLING_B:
        return styling[number, 1, "B"]
    if number == 411:
        return identity[number, 2, "B"]
    return v3[number]


def face_candidate(number, candidate, baseline):
    current, _, _, style = PRODUCTION_EXPANSION[number - 1]
    profile = dict(current)
    profile["face"] = FACE_TEMPLATES[candidate - 1]
    profile["makeup"] = (
        "Fresh natural makeup with sheer coverage, softly defined eyes, "
        "and lightly tinted lips."
    )
    if number == 25:
        profile["age"] = 22 + candidate
    if number == 40:
        profile["appearance"] = profile["appearance"].replace("freckled", "clear")
        if "freckle" in profile["feature"].lower():
            profile["feature"] = "a subtle beauty mark near her cheek"
    wardrobe = baseline["wardrobe"]
    if number in {48, 383}:
        wardrobe = f"{WARDROBE_OVERRIDES[number]}, {TIGHTNESS_DIRECTION}"
    return {
        **baseline,
        "seed": BASE_SEED + identity_seed(
            f"{profile['slug']}:static-feedback-final:{candidate}"
        ),
        "wardrobe": wardrobe,
        "prompt": generation_prompt(
            profile,
            style,
            background=baseline["environment"],
            age_wording="band",
            wardrobe=wardrobe,
            standing=True,
        ),
    }


def refined(number, baseline):
    entry = dict(baseline)
    prompt = entry["prompt"]
    if number in WARDROBES:
        wardrobe = f"{WARDROBES[number]}, {TIGHTNESS_DIRECTION}"
        if prompt.count(entry["wardrobe"]) != 1:
            raise ValueError(f"wardrobe is not unique in prompt {number}")
        prompt = prompt.replace(entry["wardrobe"], wardrobe)
        entry["wardrobe"] = wardrobe
    if number in HAIR:
        old_hair = PRODUCTION_EXPANSION[number - 1][0]["hair"]
        if prompt.count(old_hair) != 1:
            raise ValueError(f"hair is not unique in prompt {number}")
        prompt = prompt.replace(old_hair, HAIR[number])
    if number in COMPOSITIONS:
        if prompt.count(entry["crop"]) != 1:
            raise ValueError(f"composition is not unique in prompt {number}")
        prompt = prompt.replace(entry["crop"], COMPOSITIONS[number])
        entry["crop"] = COMPOSITIONS[number]
    entry["prompt"] = prompt
    return entry


def source_pair(number, candidate, v3, styling, identity):
    if number in RUNOFFS:
        left, right = RUNOFFS[number]
        return identity[number, left, "B"], identity[number, right, "B"]
    baseline = chosen(number, v3, styling, identity)
    changed = (
        face_candidate(number, candidate, baseline)
        if number in FACE_SLOTS else refined(number, baseline)
    )
    return baseline, changed


def pair(number, candidate, v3, styling, identity):
    left, right = source_pair(number, candidate, v3, styling, identity)
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
        for number in sorted(SLOTS)
        for candidate in range(1, 4 if number in FACE_SLOTS else 2)
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
        raise ValueError(f"incompatible final feedback manifest: {path}")
    return {
        (entry["slot"], entry["candidate"], entry["arm"]): entry
        for entry in manifest["entries"]
    }


def write(output_dir, entries):
    atomic_text(output_dir / "manifest.json", json.dumps({
        "version": VERSION,
        "source": "static-performer-production-blur-q60-v3",
        "schedule": [list(item) for item in schedule()],
        "recipe": RECIPE,
        "entries": [entries[key] for key in sorted(entries)],
    }, indent=2) + "\n")


def self_test():
    assert len(SLOTS) == 24
    assert len(schedule()) == 44
    assert len(FACE_SLOTS) == 10
    assert len(STYLE_SLOTS) == 11
    assert len(RUNOFFS) == 2
    assert all("bandeau" in WARDROBES[number] for number in (81, 92, 272, 284, 472))
    print("static feedback final runner self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--root", type=Path, default=Path("/mnt/Misc/sd/cover-story"))
    parser.add_argument("--label", default="static-performer-feedback-final-ab-v1")
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

    v3, styling, identity = manifests(args.root)
    entries = load(manifest_path)
    raw_dir = args.output_dir / "raw"
    items = schedule()
    for position, (number, candidate) in enumerate(items, 1):
        for entry in pair(number, candidate, v3, styling, identity):
            key = number, candidate, entry["arm"]
            raw = raw_dir / f"{entry['id']}.png"
            entry["raw"] = str(raw)
            print(
                f"[{position}/{len(items)} {entry['arm']}] {entry['id']}",
                flush=True,
            )
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
            if entry["arm"] == "A" or number in RUNOFFS:
                reference(Path(source_pair(
                    number, candidate, v3, styling, identity
                )[0 if entry["arm"] == "A" else 1]["raw"]), raw)
            else:
                entry["prompt_id"] = generate(
                    args.server, raw, entry, args.label, args.timeout, 1.5
                )
            entry.update({
                "source_sha256": sha256(raw),
                "bytes": raw.stat().st_size,
            })
            entries[key] = entry
            write(args.output_dir, entries)
    if not args.dry_run:
        print(f"manifest has {len(entries)}/{len(items) * 2} images", flush=True)


if __name__ == "__main__":
    main()
