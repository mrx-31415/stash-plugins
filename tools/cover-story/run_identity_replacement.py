#!/usr/bin/env python3
"""Generate four deterministic replacement identities for performer 095."""

import argparse
import json
from pathlib import Path

from experiment_headshots import (
    PRODUCTION_EXPANSION,
    PRODUCTION_PROFILE_OVERRIDES,
    generation_prompt,
    identity_seed,
)
from export_personas import atomic_text, sha256
from run_static_performers import BASE_SEED, details, generate


VERSION = 2
SLOT = 95
CANDIDATES = 4
PROFILE_OVERRIDE = PRODUCTION_PROFILE_OVERRIDES[SLOT]
RECIPE = {
    "model": "krea2_turbo_fp8_scaled.safetensors",
    "steps": 12,
    "cfg": 1,
    "filter_bypass": 1.5,
    "negative_prompt": None,
    "base_seed": BASE_SEED,
    "background_blur": True,
    "replacement": "performer-095 identity bundle v2",
}


def candidate(number):
    entry = details(SLOT, background_blur=True)
    profile, _, _, style = PRODUCTION_EXPANSION[SLOT - 1]
    entry["prompt"] = generation_prompt(
        profile,
        style,
        background=entry["environment"],
        age_wording="band",
        wardrobe=entry["wardrobe"],
        standing=True,
    )
    return {
        **entry,
        "id": f"performer-{SLOT:03d}_R{number}",
        "stem": f"performer-{SLOT:03d}_R{number}",
        "candidate": number,
        "seed": BASE_SEED + identity_seed(
            f"{profile['slug']}:identity-replacement-v2:{number}"
        ),
    }


def load(path):
    if not path.exists():
        return {}
    manifest = json.loads(path.read_text())
    if (
        manifest.get("version") != VERSION
        or manifest.get("slot") != SLOT
        or manifest.get("recipe") != RECIPE
    ):
        raise ValueError(f"incompatible identity replacement manifest: {path}")
    return {entry["candidate"]: entry for entry in manifest["entries"]}


def write(output_dir, entries):
    atomic_text(output_dir / "manifest.json", json.dumps({
        "version": VERSION,
        "slot": SLOT,
        "recipe": RECIPE,
        "entries": [entries[number] for number in sorted(entries)],
    }, indent=2) + "\n")


def self_test():
    entries = [candidate(number) for number in range(1, CANDIDATES + 1)]
    assert len({entry["seed"] for entry in entries}) == CANDIDATES
    assert len({entry["prompt"] for entry in entries}) == 1
    assert all(entry["slot"] == SLOT for entry in entries)
    assert all("mid twenties" in entry["prompt"] for entry in entries)
    assert all("early thirties" not in entry["prompt"] for entry in entries)
    assert all(PROFILE_OVERRIDE["hair"] in entry["prompt"] for entry in entries)
    assert all("fitted charcoal off-the-shoulder velvet sweater" in entry["wardrobe"]
               for entry in entries)
    assert details(SLOT, background_blur=True)["seed"] == entries[3]["seed"]
    print("identity replacement runner self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--label", default="performer-095-identity-replacement-v2")
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
    for number in range(1, CANDIDATES + 1):
        entry = candidate(number)
        previous = entries.get(number)
        raw = raw_dir / f"{entry['id']}.png"
        entry["raw"] = str(raw)
        print(f"[{number}/{CANDIDATES}] {entry['id']} — seed {entry['seed']}", flush=True)
        if args.dry_run:
            print(entry["prompt"])
            continue
        if previous:
            if any(previous.get(field) != entry[field] for field in ("seed", "prompt")):
                raise ValueError(f"replacement details changed for {entry['id']}")
            if raw.is_file() and sha256(raw) == previous.get("source_sha256"):
                print("already complete", flush=True)
                continue
            raise ValueError(f"completed raw changed or disappeared for {entry['id']}")
        if not raw.exists():
            entry["prompt_id"] = generate(
                args.server, raw, entry, args.label, args.timeout, 1.5
            )
        entry.update({"source_sha256": sha256(raw), "bytes": raw.stat().st_size})
        entries[number] = entry
        write(args.output_dir, entries)
    if not args.dry_run:
        print(f"manifest has {len(entries)}/{CANDIDATES} replacements", flush=True)


if __name__ == "__main__":
    main()
