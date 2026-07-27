#!/usr/bin/env python3
"""Build the final Cover Story portrait manifest and persona catalog."""

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from experiment_headshots import EYE_COLORS, PRODUCTION_PROFILES
from review_headshots import metadata


GROUPS = ("performers-v16-production", "performers-v21-reject-topup")
FILE_PATTERN = re.compile(r"(?P<number>\d+)-.+__\d+_\.png$")
COUNTRIES = {
    "Argentine": "Argentina",
    "Austrian": "Austria",
    "Brazilian": "Brazil",
    "British": "United Kingdom",
    "Chilean": "Chile",
    "Chinese": "China",
    "Colombian": "Colombia",
    "Croatian": "Croatia",
    "Czech": "Czechia",
    "Danish": "Denmark",
    "Dutch": "Netherlands",
    "Egyptian": "Egypt",
    "Finnish": "Finland",
    "French": "France",
    "German": "Germany",
    "Ghanaian": "Ghana",
    "Greek": "Greece",
    "Icelandic": "Iceland",
    "Iranian": "Iran",
    "Irish": "Ireland",
    "Italian": "Italy",
    "Jamaican": "Jamaica",
    "Japanese": "Japan",
    "Korean": "South Korea",
    "Lebanese": "Lebanon",
    "Mexican": "Mexico",
    "Nigerian": "Nigeria",
    "Norwegian": "Norway",
    "Polish": "Poland",
    "Portuguese": "Portugal",
    "Scottish": "United Kingdom",
    "South African": "South Africa",
    "Spanish": "Spain",
    "Swedish": "Sweden",
    "Swiss": "Switzerland",
    "Syrian": "Syria",
    "Ukrainian": "Ukraine",
    "Vietnamese": "Vietnam",
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as output:
        json.dump(data, output, indent=2, ensure_ascii=False)
        output.write("\n")
        temporary = Path(output.name)
    os.replace(temporary, path)


def identity_fields(identity):
    nationality, separator, heritage = identity.partition(" actress of ")
    if not separator or nationality not in COUNTRIES or not heritage.endswith(" heritage"):
        raise ValueError(f"unrecognized identity: {identity}")
    return COUNTRIES[nationality], heritage.removesuffix(" heritage")


def eye_color(profile):
    colors = sorted({color for values in EYE_COLORS.values() for color in values}, key=len, reverse=True)
    match = next((color for color in colors if f"{color} eyes" in profile["appearance"]), None)
    if not match:
        raise ValueError(f"cannot resolve eye color from {profile['appearance']}")
    return match.title()


def feature_fields(feature):
    tattoos = feature if "tattoo" in feature else ""
    piercings = feature if any(word in feature for word in ("piercing", "stud", "ring", "ear cuff")) else ""
    return tattoos, piercings


def source_entry(path, group, review, profile):
    width, height, seed, steps, prompt = metadata(path)
    return {
        "logical_variant": int(FILE_PATTERN.fullmatch(path.name)["number"]),
        "source": f"{group}/{path.name}",
        "source_sha256": sha256(path),
        "width": width,
        "height": height,
        "bytes": path.stat().st_size,
        "seed": seed,
        "sampler_steps": steps,
        "prompt": prompt,
        "review_status": review["status"],
        "apparent_age": review.get("apparent_age"),
        "profile_slug": profile["slug"],
    }


def build(experiment_root, reviews_path, tool_root):
    reviews = json.loads(reviews_path.read_text()).get("reviews", {})
    grouped = defaultdict(list)
    for group in GROUPS:
        for path in sorted((experiment_root / group).glob("*.png")):
            match = FILE_PATTERN.fullmatch(path.name)
            if not match:
                raise ValueError(f"unrecognized performer filename: {path.name}")
            number = int(match["number"])
            if not 1 <= number <= len(PRODUCTION_PROFILES):
                raise ValueError(f"invalid logical variant: {number}")
            profile = PRODUCTION_PROFILES[number - 1]
            if not path.name.startswith(f"{number:02d}-{profile['slug']}-c01-"):
                raise ValueError(f"filename/profile mismatch: {path.name}")
            review = reviews.get(f"{group}/{path.name}", {})
            if review.get("status") not in {"keep", "maybe", "reject"}:
                raise ValueError(f"{group}/{path.name} is not rated")
            grouped[group, number].append(source_entry(path, group, review, profile))

    candidates = {}
    duplicate_count = 0
    for key, entries in grouped.items():
        hashes = {entry["source_sha256"] for entry in entries}
        statuses = {entry["review_status"] for entry in entries}
        if len(entries) > 1:
            if len(hashes) != 1 or len(statuses) != 1:
                raise ValueError(f"conflicting duplicate renders for {key}")
            duplicate_count += len(entries) - 1
        candidates[key] = entries[0]

    baseline = {number: candidates[GROUPS[0], number] for number in range(1, 501)}
    topups = {
        number: entry for (group, number), entry in candidates.items()
        if group == GROUPS[1]
    }
    if len(baseline) != 500 or len(topups) != 62 or duplicate_count != 62:
        raise ValueError(
            f"unexpected sources: baseline={len(baseline)}, topups={len(topups)}, "
            f"duplicates={duplicate_count}"
        )
    if any(baseline[number]["review_status"] != "reject" for number in topups):
        raise ValueError("top-up source is not an original reject")

    selected = []
    for number in range(1, 501):
        topup = topups.get(number)
        chosen = topup if topup and topup["review_status"] == "keep" else baseline[number]
        if chosen["review_status"] == "keep":
            selected.append(chosen)

    if len(selected) != 439:
        raise ValueError(f"expected 439 selected portraits, found {len(selected)}")

    personas = []
    for persona_number, entry in enumerate(selected, 1):
        profile = PRODUCTION_PROFILES[entry["logical_variant"] - 1]
        country, ethnicity = identity_fields(profile["identity"])
        tattoos, piercings = feature_fields(profile["feature"])
        age = entry["apparent_age"] or profile["age"]
        if not isinstance(age, int) or not 18 <= age <= 80:
            raise ValueError(f"invalid apparent age for {entry['source']}: {age}")
        persona_id = f"actor-{persona_number:03d}"
        entry["persona_id"] = persona_id
        personas.append({
            "id": persona_id,
            "name": profile["name"],
            "metadata_status": "prompt-derived",
            "source_run": entry["source"].split("/", 1)[0],
            "source": entry["source"],
            "logical_variant": entry["logical_variant"],
            "gender": "FEMALE",
            "age": age,
            "intended_age": profile["age"],
            "birthdate": f"{2026 - age}-{(persona_number - 1) % 12 + 1:02d}-{(persona_number * 7 - 1) % 28 + 1:02d}",
            "country": country,
            "ethnicity": ethnicity,
            "eye_color": eye_color(profile),
            "hair_color": profile["hair_group"],
            "build": profile["build"],
            "height_cm": None,
            "weight_kg": None,
            "measurements": None,
            "tattoos": tattoos,
            "piercings": piercings,
            "crop": {"focal_x": 0.5, "focal_y": 0.5, "width": 600, "height": 900},
            "archive": f"performers/{persona_id}.webp",
        })

    repo_root = tool_root.parents[1]
    tracked_tools = (
        tool_root / "experiment_headshots.py",
        tool_root / "run_production.sh",
        tool_root / "workflows/krea2-turbo-fp8.json",
    )
    run = {
        "version": 2,
        "id": "performers-production",
        "source_root_hint": str(experiment_root),
        "source_groups": list(GROUPS),
        "selection": {
            "logical_variants": 500,
            "duplicate_renders_ignored": duplicate_count,
            "baseline": dict(Counter(entry["review_status"] for entry in baseline.values())),
            "topups": dict(Counter(entry["review_status"] for entry in topups.values())),
            "selected": len(selected),
            "selected_by_group": dict(Counter(entry["source"].split("/", 1)[0] for entry in selected)),
        },
        "recommended_recipe": {
            "mode": "performers-v6",
            "workflow": "tools/cover-story/workflows/krea2-turbo-fp8.json",
            "steps": 12,
            "cfg": 1,
            "filter_bypass_strength": 1.5,
            "base_seed": 2026072700,
            "negative_prompt": "",
        },
        "tool_hashes": {
            path.relative_to(repo_root).as_posix(): sha256(path)
            for path in tracked_tools
        },
        "entries": selected,
    }
    catalog = {
        "version": 2,
        "status": "approved",
        "source_run": run["id"],
        "counts": {"personas": len(personas)},
        "personas": personas,
    }
    if len({persona["name"] for persona in personas}) != len(personas):
        raise ValueError("persona names are not unique")
    return run, catalog


assert identity_fields("British actress of Nigerian heritage") == ("United Kingdom", "Nigerian")
assert feature_fields("a fine-line shoulder tattoo") == ("a fine-line shoulder tattoo", "")


def main():
    tool_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-root", type=Path, default=Path("/mnt/Misc/sd/cover-story/experiments"))
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    reviews = args.reviews or args.experiment_root / "reviews.json"
    run, catalog = build(args.experiment_root, reviews, tool_root)
    print(json.dumps({"selection": run["selection"], "personas": catalog["counts"]["personas"]}))
    if args.write:
        run_path = tool_root / "runs" / f"{run['id']}.json"
        write_json(run_path, run)
        write_json(tool_root / "personas.json", catalog)
        print(f"wrote {run_path}")
        print(f"wrote {tool_root / 'personas.json'}")


if __name__ == "__main__":
    main()
