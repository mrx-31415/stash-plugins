#!/usr/bin/env python3
"""Run the 24-pair Cover Story facial-detail A/B."""

import argparse
import json
from collections import Counter
from pathlib import Path

from experiment_headshots import PRODUCTION_EXPANSION
from export_personas import atomic_text, sha256
from run_static_performers import BASE_SEED, details, generate


VERSION = 1
VARIANTS = (
    2, 3, 4, 5,
    91, 92, 93, 94,
    173, 174, 175, 176,
    213, 214, 215, 216,
    402, 403, 404, 405,
    466, 467, 468, 469,
)
RECIPE = {
    "model": "krea2_turbo_fp8_scaled.safetensors",
    "steps": 12,
    "cfg": 1,
    "filter_bypass": 1.5,
    "negative_prompt": None,
    "base_seed": BASE_SEED,
    "background_blur": True,
    "comparison": "current facial details vs ethnicity-led open-ended face",
}


def pair(number):
    current = details(number, background_blur=True)
    profile = PRODUCTION_EXPANSION[number - 1][0]
    facial_details = ", ".join(filter(None, (profile["appearance"], profile.get("face"))))
    needle = f"{facial_details}, {profile['hair']}"
    if current["prompt"].count(needle) != 1:
        raise ValueError(f"facial details are not unique in prompt {number}")
    return tuple({
        **current,
        "arm": arm,
        "id": f"performer-{number:03d}_{arm}",
        "stem": f"performer-{number:03d}_{arm}",
        "appearance_mode": mode,
        "facial_details": selected_details,
        "prompt": current["prompt"].replace(needle, replacement),
    } for arm, mode, selected_details, replacement in (
        ("A", "current", facial_details, needle),
        ("B", "open-ended", "", profile["hair"]),
    ))


def load(path):
    if not path.exists():
        return {}
    manifest = json.loads(path.read_text())
    if (
        manifest.get("version") != VERSION
        or manifest.get("variants") != list(VARIANTS)
        or manifest.get("recipe") != RECIPE
    ):
        raise ValueError(f"incompatible appearance A/B manifest: {path}")
    return {(entry["slot"], entry["arm"]): entry for entry in manifest["entries"]}


def write(output_dir, entries):
    atomic_text(output_dir / "manifest.json", json.dumps({
        "version": VERSION,
        "variants": list(VARIANTS),
        "recipe": RECIPE,
        "entries": [entries[key] for key in sorted(entries)],
    }, indent=2) + "\n")


def self_test():
    profiles = [PRODUCTION_EXPANSION[number - 1][0] for number in VARIANTS]
    assert len(VARIANTS) == len(set(VARIANTS)) == 24
    assert Counter(profile["ethnicity"] for profile in profiles) == {
        "Caucasian": 4, "Latin": 4, "Black": 4, "Asian": 4, "Mixed": 4,
        "Middle Eastern": 4,
    }
    assert Counter(profile["face_strategy"] for profile in profiles) == {
        "balanced": 8, "minimal": 8, "natural-prose": 8,
    }
    assert Counter(profile["hair_group"] for profile in profiles) == {
        "Blonde": 6, "Brunette": 6, "Black": 6, "Red": 6,
    }
    assert Counter((number - 1) % 6 for number in VARIANTS) == dict.fromkeys(range(6), 4)
    for number in VARIANTS:
        before, after = pair(number)
        assert before["seed"] == after["seed"]
        assert before["prompt"].replace(f"{before['facial_details']}, ", "") == after["prompt"]
        assert before["facial_details"]
        assert not after["facial_details"]
        assert PRODUCTION_EXPANSION[number - 1][0]["identity"] in after["prompt"]
    print("appearance A/B runner self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--label", default="appearance-open-ended-ab-v1")
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
    entries = load(manifest_path)
    raw_dir = args.output_dir / "raw"
    for position, number in enumerate(VARIANTS, 1):
        for entry in pair(number):
            key = number, entry["arm"]
            previous = entries.get(key)
            raw = raw_dir / f"{entry['id']}.png"
            entry["raw"] = str(raw)
            print(
                f"[{position}/{len(VARIANTS)} {entry['arm']}] "
                f"{entry['id']} — {entry['appearance_mode']}",
                flush=True,
            )
            if args.dry_run:
                print(entry["prompt"])
                continue
            if previous:
                if any(previous.get(field) != entry[field] for field in (
                    "seed", "prompt", "appearance_mode", "facial_details",
                )):
                    raise ValueError(f"A/B details changed for {entry['id']}")
                if raw.is_file() and sha256(raw) == previous.get("source_sha256"):
                    print("already complete", flush=True)
                    continue
                raise ValueError(f"completed raw changed or disappeared for {entry['id']}")
            if not raw.exists():
                entry["prompt_id"] = generate(
                    args.server, raw, entry, args.label, args.timeout, 1.5
                )
            entry.update({"source_sha256": sha256(raw), "bytes": raw.stat().st_size})
            entries[key] = entry
            write(args.output_dir, entries)
    if not args.dry_run:
        print(f"manifest has {len(entries)}/{len(VARIANTS) * 2} images", flush=True)


if __name__ == "__main__":
    main()
