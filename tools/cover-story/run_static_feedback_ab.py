#!/usr/bin/env python3
"""Generate resumable v3 feedback A/B replacements without changing accepted assets."""

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

from experiment_headshots import (
    DISTINCT_PROFILES,
    PRODUCTION_EXPANSION,
    PRODUCTION_PROFILE_OVERRIDES,
    generation_prompt,
    identity_seed,
    production_profile,
)
from export_personas import atomic_text, sha256
from performer_palettes import TIGHTNESS_DIRECTION
from run_static_performers import BASE_SEED, details, generate
from static_feedback_specs import (
    COMPOSITION_OVERRIDES,
    FACE_MAYBES,
    FACE_REJECTS,
    HAIR_OVERRIDES,
    KEEP_CHANGES,
    MAKEUP_OVERRIDES,
    POSE_OVERRIDES,
    TOO_OLD,
    UNKNOWN_REJECTS,
    WARDROBE_OVERRIDES,
)


VERSION = 1
SOURCE_LABEL = "static-performer-production-blur-q60-v3"
RECIPE = {
    "model": "krea2_turbo_fp8_scaled.safetensors",
    "steps": 12,
    "cfg": 1,
    "filter_bypass": 1.5,
    "negative_prompt": None,
    "base_seed": BASE_SEED,
    "background_blur": True,
    "comparison": "accepted v3 source vs curated feedback replacement",
}
IDENTITY_TARGETS = FACE_REJECTS | FACE_MAYBES | UNKNOWN_REJECTS


def source_reviews(path):
    reviews = json.loads(path.read_text())["reviews"]
    pattern = re.compile(
        rf"{re.escape(SOURCE_LABEL)}/assets/performer-(\d{{3}})\.avif"
    )
    selected = {
        int(match.group(1)): review
        for image, review in reviews.items()
        if (match := pattern.fullmatch(image))
    }
    if len(selected) != 500:
        raise ValueError(f"expected 500 v3 reviews, found {len(selected)}")
    return selected


def target_slots(reviews):
    targets = {
        number for number, review in reviews.items()
        if review["status"] in {"maybe", "reject"}
    } | KEEP_CHANGES
    covered = (
        IDENTITY_TARGETS
        | set(WARDROBE_OVERRIDES)
        | set(HAIR_OVERRIDES)
        | set(MAKEUP_OVERRIDES)
        | set(POSE_OVERRIDES)
        | set(COMPOSITION_OVERRIDES)
    )
    if targets != covered:
        raise ValueError(
            f"feedback coverage mismatch; missing={sorted(targets - covered)}, "
            f"extra={sorted(covered - targets)}"
        )
    if len(targets) != 139:
        raise ValueError(f"expected 139 feedback targets, found {len(targets)}")
    return targets


def actions(number):
    selected = []
    for name, group in (
        ("identity", IDENTITY_TARGETS),
        ("wardrobe", WARDROBE_OVERRIDES),
        ("hair", HAIR_OVERRIDES),
        ("makeup", MAKEUP_OVERRIDES),
        ("pose", POSE_OVERRIDES),
        ("composition", COMPOSITION_OVERRIDES),
    ):
        if number in group:
            selected.append(name)
    return selected or ["freeze"]


def feedback_snapshot(reviews):
    return [
        {
            "slot": number,
            "status": reviews[number]["status"],
            "notes": reviews[number].get("notes", ""),
            "actions": actions(number),
            "targeted": actions(number) != ["freeze"],
        }
        for number in range(1, 501)
    ]


def feedback_digest(snapshot):
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def candidate_count(number):
    if number in FACE_REJECTS or number in UNKNOWN_REJECTS:
        return 3
    if number in FACE_MAYBES:
        return 2
    return 1


def replacement_profile(number, candidate):
    current = PRODUCTION_EXPANSION[number - 1][0]
    if number not in IDENTITY_TARGETS:
        profile = dict(current)
    else:
        alternate = (number - 1 + candidate * 137) % len(DISTINCT_PROFILES)
        while alternate + 1 in PRODUCTION_PROFILE_OVERRIDES:
            alternate = (alternate + 1) % len(DISTINCT_PROFILES)
        profile = production_profile(DISTINCT_PROFILES[number - 1], alternate)
        for field in ("name", "slug", "identity", "build", "hair", "feature"):
            profile[field] = current[field]
        profile["age"] = 23 + candidate if number in TOO_OLD else current["age"]
        profile["makeup"] = (
            "Fresh natural makeup with sheer coverage, softly defined eyes, "
            "and lightly tinted lips."
        )
    if number in HAIR_OVERRIDES:
        profile["hair"] = HAIR_OVERRIDES[number]
    if number in MAKEUP_OVERRIDES:
        profile["makeup"] = MAKEUP_OVERRIDES[number]
    if number in {40, 369}:
        profile["appearance"] = profile["appearance"].replace("freckled", "clear")
        if "freckle" in profile["feature"].lower():
            profile["feature"] = "a subtle beauty mark near her cheek"
    return profile


def replacement(number, candidate):
    baseline = details(number, background_blur=True)
    profile, _, _, original_style = PRODUCTION_EXPANSION[number - 1]
    profile = replacement_profile(number, candidate)
    style = dict(original_style)
    style["pose"] = POSE_OVERRIDES.get(number, style["pose"])
    style["composition"] = COMPOSITION_OVERRIDES.get(
        number, style["composition"]
    )
    wardrobe = baseline["wardrobe"]
    if number in WARDROBE_OVERRIDES:
        wardrobe = f"{WARDROBE_OVERRIDES[number]}, {TIGHTNESS_DIRECTION}"
    seed = baseline["seed"]
    if number in IDENTITY_TARGETS:
        seed = BASE_SEED + identity_seed(
            f"{profile['slug']}:static-feedback-v1:{candidate}"
        )
    return {
        **baseline,
        "seed": seed,
        "wardrobe": wardrobe,
        "crop": style["composition"],
        "prompt": generation_prompt(
            profile,
            style,
            background=baseline["environment"],
            age_wording="band",
            wardrobe=wardrobe,
            standing=True,
        ),
    }


def pair(number, candidate):
    original = details(number, background_blur=True)
    changed = replacement(number, candidate)
    pair_id = f"performer-{number:03d}-c{candidate}"
    return (
        {
            **original,
            "arm": "A",
            "candidate": candidate,
            "id": f"{pair_id}_A",
            "stem": f"{pair_id}_A",
        },
        {
            **changed,
            "arm": "B",
            "candidate": candidate,
            "id": f"{pair_id}_B",
            "stem": f"{pair_id}_B",
        },
    )


def select(targets, category):
    identity = targets & IDENTITY_TARGETS
    return sorted(identity if category == "identity" else targets - identity)


def load(path, variants, digest, category):
    if not path.exists():
        return {}
    manifest = json.loads(path.read_text())
    if (
        manifest.get("version") != VERSION
        or manifest.get("variants") != variants
        or manifest.get("feedback_sha256") != digest
        or manifest.get("category") != category
        or manifest.get("recipe") != RECIPE
    ):
        raise ValueError(f"incompatible static feedback manifest: {path}")
    return {
        (entry["slot"], entry["candidate"], entry["arm"]): entry
        for entry in manifest["entries"]
    }


def write(output_dir, entries, variants, digest, category):
    atomic_text(output_dir / "manifest.json", json.dumps({
        "version": VERSION,
        "source": SOURCE_LABEL,
        "category": category,
        "variants": variants,
        "feedback_sha256": digest,
        "recipe": RECIPE,
        "entries": [entries[key] for key in sorted(entries)],
    }, indent=2) + "\n")


def reference(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def self_test():
    covered = (
        IDENTITY_TARGETS
        | set(WARDROBE_OVERRIDES)
        | set(HAIR_OVERRIDES)
        | set(MAKEUP_OVERRIDES)
        | set(POSE_OVERRIDES)
        | set(COMPOSITION_OVERRIDES)
    )
    assert len(covered) == 139
    assert len(KEEP_CHANGES) == 6
    assert len(FACE_REJECTS) == 47
    assert len(FACE_MAYBES) == 6
    assert sum(candidate_count(number) for number in covered) == 241
    assert len(select(covered, "identity")) == 54
    assert len(select(covered, "styling")) == 85
    for number in (13, 81, 165, 254, 285, 447, 482):
        assert "bandeau" in replacement(number, 1)["wardrobe"]
    for number in covered:
        original, changed = pair(number, 1)
        assert original["slot"] == changed["slot"] == number
        assert original["arm"] == "A" and changed["arm"] == "B"
        assert original["prompt"] != changed["prompt"] or original["seed"] != changed["seed"]
    print("static feedback A/B runner self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--source-dir", type=Path, default=Path(
        f"/mnt/Misc/sd/cover-story/{SOURCE_LABEL}"
    ))
    parser.add_argument("--reviews", type=Path, default=Path(
        "/mnt/Misc/sd/cover-story/reviews.json"
    ))
    parser.add_argument("--label", default="static-feedback-ab-v1")
    parser.add_argument("--category", choices=("identity", "styling"))
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.category:
        parser.error("--category is required")
    if not args.output_dir:
        parser.error("--output-dir is required")
    if not args.server and not args.dry_run:
        parser.error("--server is required unless --dry-run is used")

    reviews = source_reviews(args.reviews)
    targets = target_slots(reviews)
    snapshot = feedback_snapshot(reviews)
    digest = feedback_digest(snapshot)
    variants = select(targets, args.category)
    manifest_path = args.output_dir / "manifest.json"
    if args.output_dir.exists() and any(args.output_dir.iterdir()) and not manifest_path.exists():
        raise ValueError(f"refusing nonempty output directory without manifest: {args.output_dir}")
    entries = load(manifest_path, variants, digest, args.category)
    atomic_text(args.output_dir / "feedback.json", json.dumps({
        "version": 1,
        "source": SOURCE_LABEL,
        "feedback_sha256": digest,
        "summary": {"keep": 367, "maybe": 83, "reject": 50, "targeted": 139},
        "entries": snapshot,
    }, indent=2) + "\n")
    raw_dir = args.output_dir / "raw"
    source_entries = json.loads(
        (args.source_dir / "manifest.json").read_text()
    )["entries"]
    total = sum(candidate_count(number) for number in variants)
    position = 0
    for number in variants:
        source_entry = source_entries[number - 1]
        source_raw = Path(source_entry["raw"])
        if sha256(source_raw) != source_entry["source_sha256"]:
            raise ValueError(f"v3 source changed for performer-{number:03d}")
        for candidate in range(1, candidate_count(number) + 1):
            position += 1
            for entry in pair(number, candidate):
                key = number, candidate, entry["arm"]
                raw = raw_dir / f"{entry['id']}.png"
                entry["raw"] = str(raw)
                entry["source_status"] = reviews[number]["status"]
                entry["source_notes"] = reviews[number].get("notes", "")
                entry["actions"] = actions(number)
                print(
                    f"[{position}/{total} {entry['arm']}] {entry['id']} — "
                    f"{', '.join(entry['actions'])}",
                    flush=True,
                )
                if args.dry_run:
                    print(entry["prompt"])
                    continue
                previous = entries.get(key)
                if previous:
                    if any(previous.get(field) != entry[field] for field in (
                        "seed", "prompt", "wardrobe", "source_notes",
                    )):
                        raise ValueError(f"A/B details changed for {entry['id']}")
                    if raw.is_file() and sha256(raw) == previous.get("source_sha256"):
                        print("already complete", flush=True)
                        continue
                    raise ValueError(f"completed raw changed or disappeared for {entry['id']}")
                if entry["arm"] == "A":
                    reference(source_raw, raw)
                else:
                    entry["prompt_id"] = generate(
                        args.server, raw, entry, args.label, args.timeout, 1.5
                    )
                entry.update({
                    "source_sha256": sha256(raw),
                    "bytes": raw.stat().st_size,
                })
                entries[key] = entry
                write(args.output_dir, entries, variants, digest, args.category)
    if not args.dry_run:
        print(f"manifest has {len(entries)}/{total * 2} images", flush=True)


if __name__ == "__main__":
    main()
