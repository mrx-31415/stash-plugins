#!/usr/bin/env python3
"""Install the approved 500 static portraits as Cover Story AVIF assets."""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageOps

from curate_personas import feature_fields, identity_fields, eye_color, write_json
from experiment_headshots import PRODUCTION_EXPANSION
from export_personas import atomic_text, crop, sha256
from export_scene_assets import save_avif
from run_static_performers import inspect_avif


SOURCE = Path("/mnt/Misc/sd/cover-story/static-performer-final-review-v1")
RUN_ID = "performers-static-final"
AGE_OVERRIDES = {25: 25, 196: 23, 266: 22}
QUALITY = 60
YUV = "420"


def persona(slot, profile):
    country, ethnicity = identity_fields(profile["identity"])
    tattoos, piercings = feature_fields(profile["feature"])
    age = AGE_OVERRIDES.get(slot, profile["age"])
    actor = f"actor-{slot:03d}"
    return {
        "id": actor,
        "name": profile["name"],
        "metadata_status": "prompt-derived",
        "source_run": RUN_ID,
        "source": f"raw/performer-{slot:03d}.png",
        "logical_variant": slot,
        "gender": "FEMALE",
        "age": age,
        "intended_age": profile["age"],
        "birthdate": f"{2026 - age}-{(slot - 1) % 12 + 1:02d}-{(slot * 7 - 1) % 28 + 1:02d}",
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
        "archive": f"performers/{actor}.avif",
    }


def runtime_manifest(personas):
    fields = (
        "id", "name", "gender", "birthdate", "country", "ethnicity",
        "eye_color", "hair_color", "height_cm", "weight_kg", "measurements",
        "tattoos", "piercings",
    )
    runtime = [{field: item[field] for field in fields} for item in personas]
    for item, source in zip(runtime, personas):
        item["image_path"] = f"/plugin/cover-story/assets/{source['archive']}"
    payload = json.dumps(runtime, ensure_ascii=False, separators=(",", ":"))
    return (
        '(function(root){"use strict";const personas=' + payload + ";"
        'if(typeof module!=="undefined"&&module.exports)module.exports=personas;'
        "if(root)root.CoverStoryPersonas=personas;"
        '})(typeof window!=="undefined"?window:null);\n'
    )


def self_test():
    assert len(PRODUCTION_EXPANSION) == 500
    profiles = [persona(slot, item[0]) for slot, item in enumerate(PRODUCTION_EXPANSION, 1)]
    assert len({item["id"] for item in profiles}) == 500
    assert len({item["name"] for item in profiles}) == 500
    assert profiles[24]["age"] == 25
    assert profiles[195]["age"] == 23
    assert profiles[265]["age"] == 22
    assert all(item["archive"].endswith(".avif") for item in profiles)
    print("static performer asset installer self-check passed")


def main():
    tool_root = Path(__file__).resolve().parent
    repo_root = tool_root.parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=SOURCE)
    parser.add_argument("--plugin-root", type=Path, default=repo_root / "plugins/cover-story")
    parser.add_argument("--start", type=int, choices=range(1, 501), default=1)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return

    source_manifest = json.loads((args.source_root / "manifest.json").read_text())
    entries = source_manifest["entries"]
    if source_manifest.get("target_count") != 500 or [entry["slot"] for entry in entries] != list(range(1, 501)):
        raise ValueError("approved source manifest must contain slots 1 through 500")

    personas = []
    installed = []
    output_root = args.plugin_root / "assets/performers"
    for entry, expansion in zip(entries, PRODUCTION_EXPANSION):
        slot = entry["slot"]
        profile = expansion[0]
        if (entry["name"], entry["slug"]) != (profile["name"], profile["slug"]):
            raise ValueError(f"profile mismatch for performer-{slot:03d}")
        source = args.source_root / "raw" / f"performer-{slot:03d}.png"
        if sha256(source) != entry["source_sha256"]:
            raise ValueError(f"approved source changed: {source}")
        destination = output_root / f"actor-{slot:03d}.avif"
        if slot >= args.start:
            with Image.open(source) as opened:
                image = crop(ImageOps.exif_transpose(opened).convert("RGB"), 0.5, 0.5, 600, 900)
            save_avif(image, destination, quality=QUALITY, alpha_quality=None, yuv=YUV)
        inspect_avif(destination, YUV)
        personas.append(persona(slot, profile))
        installed.append({
            "slot": slot,
            "source": f"raw/performer-{slot:03d}.png",
            "source_sha256": entry["source_sha256"],
            "accepted_source": entry["accepted_source"],
            "accepted_round": entry["accepted_round"],
            "asset": f"plugins/cover-story/assets/performers/actor-{slot:03d}.avif",
            "asset_sha256": sha256(destination),
            "dimensions": [600, 900],
            "bytes": destination.stat().st_size,
        })
        print(f"[{slot}/500] {destination.name}", flush=True)

    expected = {f"actor-{slot:03d}.avif" for slot in range(1, 501)}
    for stale in output_root.iterdir():
        if stale.is_file() and stale.suffix.lower() in {".avif", ".webp"} and stale.name not in expected:
            stale.unlink()

    run = {
        "version": 1,
        "id": RUN_ID,
        "source_root_hint": str(args.source_root),
        "source_manifest_sha256": sha256(args.source_root / "manifest.json"),
        "asset": {
            "format": "AVIF", "quality": QUALITY, "speed": 6, "yuv": YUV,
            "width": 600, "height": 900, "alpha": False,
        },
        "entries": installed,
    }
    catalog = {
        "version": 3,
        "status": "approved",
        "source_run": RUN_ID,
        "counts": {"personas": 500},
        "personas": personas,
    }
    write_json(tool_root / "runs" / f"{RUN_ID}.json", run)
    write_json(tool_root / "personas.json", catalog)
    atomic_text(args.plugin_root / "personas.js", runtime_manifest(personas))
    print(f"installed 500 portraits ({sum(entry['bytes'] for entry in installed) / 1024 / 1024:.1f} MiB)")


if __name__ == "__main__":
    main()
