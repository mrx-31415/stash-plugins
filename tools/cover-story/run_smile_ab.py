#!/usr/bin/env python3
"""Run the 20-pair Cover Story warm-smile wording A/B."""

import argparse
import json
from pathlib import Path

from experiment_headshots import PRODUCTION_EXPANSION
from export_personas import atomic_text, sha256
from run_static_performers import BASE_SEED, details, generate


VERSION = 1
VARIANTS = (15, 16, 36, 14, 43, 65, 82, 9, 71, 7, 18, 143, 68, 26, 60, 58, 62, 1, 47, 87)
WARM_POSES = {
    "Square to camera with relaxed shoulders and a calm direct gaze":
        "Square to camera with relaxed shoulders and a warm, genuine, unforced smile that reaches her eyes",
    "Shoulders gently angled while her eyes look directly into the camera":
        "Shoulders gently angled, looking directly into the camera with a warm, genuine, unforced smile that reaches her eyes",
    "Chin slightly lowered, face fully visible, with a confident direct gaze":
        "Chin slightly lowered and face fully visible, with direct eye contact and a warm, genuine, unforced smile that reaches her eyes",
    "One shoulder slightly lowered while her face and eyes remain directed at the camera":
        "One shoulder slightly lowered while her face and eyes remain directed at the camera, with a warm, genuine, unforced smile that reaches her eyes",
    "Subtle three-quarter angle with relaxed shoulders, her face and eyes fully toward the camera":
        "Subtle three-quarter angle with relaxed shoulders, her face and eyes fully toward the camera, with a warm, genuine, unforced smile that reaches her eyes",
    "Shoulders turned slightly left while her face returns to a confident direct gaze":
        "Shoulders turned slightly left while her face returns to the camera with a warm, genuine, unforced smile that reaches her eyes",
    "Shoulders turned slightly right while her face returns to a warm direct gaze":
        "Shoulders turned slightly right while her face returns to the camera with a warm, genuine, unforced smile that reaches her eyes",
    "Chin gently raised with poised direct eye contact and relaxed shoulders":
        "Chin gently raised with direct eye contact and relaxed shoulders, wearing a warm, genuine, unforced smile that reaches her eyes",
    "A slight forward lean with an engaged expression and eyes directly toward the camera":
        "A slight forward lean with eyes directly toward the camera and a warm, genuine, unforced smile that reaches her eyes",
    "Standing tall with an assured neutral expression and direct eye contact":
        "Standing tall with direct eye contact and a warm, genuine, unforced smile that reaches her eyes",
    "One shoulder slightly forward with a calm, self-possessed direct gaze":
        "One shoulder slightly forward with direct eye contact and a warm, genuine, unforced smile that reaches her eyes",
    "Relaxed posture with chin slightly angled and eyes returning fully to the camera":
        "Relaxed posture with chin slightly angled and eyes returning fully to the camera, with a warm, genuine, unforced smile that reaches her eyes",
    "Shoulders gently lowered with a serene direct gaze and softly parted lips":
        "Shoulders gently lowered with direct eye contact and a warm, genuine, unforced smile that reaches her eyes",
    "Subtle contrapposto stance with her face fully visible and a friendly direct gaze":
        "Subtle contrapposto stance with her face fully visible, direct eye contact, and a warm, genuine, unforced smile that reaches her eyes",
    "Head held level with an intense but relaxed direct gaze":
        "Head held level with direct eye contact and a warm, genuine, unforced smile that reaches her eyes",
    "A slight sideways lean with her face and eyes fully toward the camera":
        "A slight sideways lean with her face and eyes fully toward the camera, with a warm, genuine, unforced smile that reaches her eyes",
    "Square to camera with one eyebrow subtly raised and a confident direct gaze":
        "Square to camera with one eyebrow subtly raised and a warm, genuine, unforced smile that reaches her eyes",
    "Shoulders angled toward the light while maintaining direct eye contact":
        "Shoulders angled toward the light while maintaining direct eye contact and a warm, genuine, unforced smile that reaches her eyes",
}
RECIPE = {
    "model": "krea2_turbo_fp8_scaled.safetensors",
    "steps": 12,
    "cfg": 1,
    "filter_bypass": 1.5,
    "negative_prompt": None,
    "base_seed": BASE_SEED,
    "background_blur": True,
}


def pair(number):
    current = details(number, background_blur=True)
    catalog_pose = PRODUCTION_EXPANSION[number - 1][3]["pose"]
    pose = next(
        (before for before, after in WARM_POSES.items() if after == catalog_pose),
        catalog_pose,
    )
    warm_pose = WARM_POSES[pose]
    if current["prompt"].count(catalog_pose) != 1:
        raise ValueError(f"pose is not unique in prompt {number}")
    current["prompt"] = current["prompt"].replace(catalog_pose, pose)
    return tuple({
        **current,
        "arm": arm,
        "id": f"performer-{number:03d}_{arm}",
        "stem": f"performer-{number:03d}_{arm}",
        "pose": selected_pose,
        "prompt": current["prompt"].replace(pose, selected_pose),
    } for arm, selected_pose in (("A", pose), ("B", warm_pose)))


def load(path):
    if not path.exists():
        return {}
    manifest = json.loads(path.read_text())
    if (
        manifest.get("version") != VERSION
        or manifest.get("variants") != list(VARIANTS)
        or manifest.get("recipe") != RECIPE
    ):
        raise ValueError(f"incompatible smile A/B manifest: {path}")
    return {(entry["slot"], entry["arm"]): entry for entry in manifest["entries"]}


def write(output_dir, entries):
    atomic_text(output_dir / "manifest.json", json.dumps({
        "version": VERSION,
        "variants": list(VARIANTS),
        "recipe": RECIPE,
        "entries": [entries[key] for key in sorted(entries)],
    }, indent=2) + "\n")


def self_test():
    assert len(VARIANTS) == len(set(VARIANTS)) == 20
    assert len(WARM_POSES) == 18
    assert {
        PRODUCTION_EXPANSION[number - 1][3]["composition"] for number in VARIANTS
    } == {
        "Tight head-and-shoulders composition with the tops of her shoulders visible",
        "Head-and-upper-torso composition with modest space around her",
        "Chest-up composition framed just below the bust",
        "Mid-torso composition with some surrounding environment visible",
        "Waist-up composition with balanced headroom",
        "Slightly wider waist-up environmental composition",
    }
    for number in VARIANTS:
        before, after = pair(number)
        assert before["seed"] == after["seed"]
        assert before["prompt"].replace(before["pose"], after["pose"]) == after["prompt"]
        assert "warm, genuine, unforced smile that reaches her eyes" in after["pose"]
    print("smile A/B runner self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--label", default="smile-warmth-ab-v1")
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
            print(f"[{position}/20 {entry['arm']}] {entry['id']} — {entry['pose']}", flush=True)
            if args.dry_run:
                print(entry["prompt"])
                continue
            if previous:
                if any(previous.get(field) != entry[field] for field in ("seed", "prompt", "pose")):
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
        print(f"manifest has {len(entries)}/40 images", flush=True)


if __name__ == "__main__":
    main()
