#!/usr/bin/env python3
"""Run the 24-pair Cover Story bust-size wording A/B."""

import argparse
import json
from collections import Counter
from pathlib import Path

from experiment_headshots import PRODUCTION_EXPANSION
from export_personas import atomic_text, sha256
from performer_palettes import WARDROBE_STYLE_GROUPS
from run_static_performers import BASE_SEED, details, generate


VERSION = 1
VARIANTS = (
    463, 187, 218, 249, 418, 401, 474, 432,
    409, 230, 278, 285, 489, 178, 461, 378,
    211, 464, 171, 304, 490, 95, 419, 228,
)
ORIGINAL_BUILDS = {
    0: ("a lean, athletic build with a small bust", "Small", "Small→Medium"),
    6: ("a lean, athletic build with a modest bust", "Small", "Small→Medium"),
    2: ("a toned, athletic build with a medium bust", "Medium", "Medium→Large"),
    8: ("a fit, balanced athletic build with a medium bust", "Medium", "Medium→Large"),
    12: ("a toned, athletic build with a medium-full bust", "Medium", "Medium→Large"),
    15: ("a compact athletic build with a medium bust", "Medium", "Medium→Large"),
    9: ("a strong, curvy-athletic build with a large bust", "Large", "Large→Very Large"),
    17: ("a toned, statuesque build with a large bust", "Large", "Large→Very Large"),
}
RECIPE = {
    "model": "krea2_turbo_fp8_scaled.safetensors",
    "steps": 12,
    "cfg": 1,
    "filter_bypass": 1.5,
    "negative_prompt": None,
    "base_seed": BASE_SEED,
    "background_blur": True,
    "comparison": "previous bust tier vs increased bust tier",
}


def build_variant(number):
    index = number - 1
    return (index * 9 + (index // 10) * 7) % 20


def pair(number):
    current = details(number, background_blur=True)
    profile = PRODUCTION_EXPANSION[number - 1][0]
    old_build, old_group, transition = ORIGINAL_BUILDS[build_variant(number)]
    new_build = profile["build"]
    if current["prompt"].count(new_build) != 1:
        raise ValueError(f"build is not unique in prompt {number}")
    return tuple({
        **current,
        "arm": arm,
        "id": f"performer-{number:03d}_{arm}",
        "stem": f"performer-{number:03d}_{arm}",
        "build": build,
        "bust_group": bust_group,
        "transition": transition,
        "prompt": current["prompt"].replace(new_build, build),
    } for arm, build, bust_group in (
        ("A", old_build, old_group),
        ("B", new_build, profile["bust_group"]),
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
        raise ValueError(f"incompatible bust A/B manifest: {path}")
    return {(entry["slot"], entry["arm"]): entry for entry in manifest["entries"]}


def write(output_dir, entries):
    atomic_text(output_dir / "manifest.json", json.dumps({
        "version": VERSION,
        "variants": list(VARIANTS),
        "recipe": RECIPE,
        "entries": [entries[key] for key in sorted(entries)],
    }, indent=2) + "\n")


def self_test():
    assert len(VARIANTS) == len(set(VARIANTS)) == 24
    assert Counter(pair(number)[0]["transition"] for number in VARIANTS) == {
        "Small→Medium": 8, "Medium→Large": 8, "Large→Very Large": 8,
    }
    assert Counter((number - 1) % 6 for number in VARIANTS) == dict.fromkeys(range(6), 4)
    assert {
        WARDROBE_STYLE_GROUPS[PRODUCTION_EXPANSION[number - 1][3]["wardrobe"]]
        for number in VARIANTS
    } == set(WARDROBE_STYLE_GROUPS.values())
    assert {
        PRODUCTION_EXPANSION[number - 1][0]["ethnicity"] for number in VARIANTS
    } == {"Caucasian", "Latin", "Black", "Asian", "Mixed", "Middle Eastern"}
    for number in VARIANTS:
        before, after = pair(number)
        assert before["seed"] == after["seed"]
        assert before["prompt"].replace(before["build"], after["build"]) == after["prompt"]
        assert before["build"] != after["build"]
    print("bust A/B runner self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--label", default="bust-size-final-ab-v1")
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
                f"{entry['id']} — {entry['transition']}: {entry['build']}",
                flush=True,
            )
            if args.dry_run:
                print(entry["prompt"])
                continue
            if previous:
                if any(previous.get(field) != entry[field] for field in (
                    "seed", "prompt", "build", "bust_group", "transition",
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
