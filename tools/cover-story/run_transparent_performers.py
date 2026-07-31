#!/usr/bin/env python3
"""Generate, key and encode the 500 transparent Cover Story performers."""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from export_performer_cutouts import review_html
from export_personas import atomic_text, sha256
from experiment_headshots import PRODUCTION_EXPANSION, generation_prompt
from performer_palettes import WARDROBE_PALETTES
from review_headshots import metadata
from run_corridorkey_poc import encode_avif, output_paths, process_source


SCREEN_BACKGROUNDS = {
    "green": (
        "uniform seamless chroma-key green (#00ff00) background filling the entire frame, "
        "flat and evenly lit, with no gradient, texture, scenery, floor line, cast shadow, "
        "reflection, props, or green spill"
    ),
    "blue": (
        "uniform seamless chroma-key blue (#0000ff) background filling the entire frame, "
        "flat and evenly lit, with no gradient, texture, scenery, floor line, cast shadow, "
        "reflection, props, or blue spill"
    ),
}
GREEN_ADJACENT = {
    "green", "forest", "moss", "olive", "teal", "sage", "emerald", "jade",
    "mint", "aqua", "turquoise", "cyan",
}
BLUE_ADJACENT = {"blue", "navy", "indigo", "cobalt", "sapphire"}
LOWER_BODY_WORDS = {
    "pants", "jeans", "shorts", "skirt", "trousers", "leggings", "joggers",
    "shoes", "sneakers", "boots", "heels", "stilettos", "espadrilles",
    "sandals", "socks", "tights", "fishnets", "pumps",
}
GREEN_BACKGROUND = SCREEN_BACKGROUNDS["green"]
PASS_VERSION = 9
RAW_SUFFIX = f"chroma-v{PASS_VERSION}"


def color_words(wardrobe):
    return set(re.findall(r"[a-z]+", wardrobe.lower()))


def screen_for(wardrobe):
    colors = color_words(wardrobe)
    green = colors & GREEN_ADJACENT
    blue = colors & BLUE_ADJACENT
    if green and blue:
        raise ValueError(f"wardrobe mixes green-adjacent {sorted(green)} and blue-adjacent {sorted(blue)}")
    return "blue" if green else "green"


def production_wardrobes():
    return {style["wardrobe"] for _, _, _, style in PRODUCTION_EXPANSION}


def palette_for(wardrobe):
    try:
        return WARDROBE_PALETTES[wardrobe]
    except KeyError as exc:
        raise ValueError(f"no curated palette for production wardrobe {wardrobe!r}") from exc


def selected_variants(variants, start, stop):
    selected = variants or list(range(start, (stop or len(PRODUCTION_EXPANSION)) + 1))
    if not selected or any(number < 1 or number > len(PRODUCTION_EXPANSION) for number in selected):
        raise ValueError(f"variants must be between 1 and {len(PRODUCTION_EXPANSION)}")
    return selected


def find_raw(raw_dir, number):
    matches = sorted(raw_dir.glob(f"{number:02d}-*_{RAW_SUFFIX}_*.png"))
    if len(matches) > 1:
        raise ValueError(f"multiple raw images found for performer {number}: {matches}")
    return matches[0] if matches else None


def performer_details(number, base_seed):
    profile, _, seed_offset, style = PRODUCTION_EXPANSION[number - 1]
    performer_id = f"performer-{number:03d}"
    wardrobe = palette_for(style["wardrobe"])
    screen = screen_for(wardrobe)
    background = SCREEN_BACKGROUNDS[screen]
    return {
        "id": performer_id,
        "stem": performer_id,
        "variant": number,
        "slug": profile["slug"],
        "name": profile["name"],
        "seed": base_seed + seed_offset,
        "wardrobe": wardrobe,
        "screen": screen,
        "prompt": generation_prompt(
            profile, style, background=background,
            age_wording="band", wardrobe=wardrobe, standing=True,
        ),
    }


def wardrobe_from_prompt(prompt):
    match = re.search(r"\bwears an? (.*?), against a uniform seamless chroma-key", prompt)
    if not match:
        raise ValueError("raw performer prompt has no recognizable wardrobe")
    return match.group(1)


def generate(tool_root, server, raw_dir, label, number, base_seed, timeout, wardrobe, screen):
    command = [
        sys.executable, str(tool_root / "experiment_headshots.py"),
        "--server", server,
        "--mode", "performers-v6",
        "--label", label,
        "--workflow", str(tool_root / "workflows" / "krea2-turbo-fp8.json"),
        "--steps", "12",
        "--cfg", "1",
        "--bypass-strength", "1.5",
        "--age-wording", "band",
        "--seed", str(base_seed),
        "--background", SCREEN_BACKGROUNDS[screen],
        "--wardrobe", wardrobe,
        "--standing",
        "--suffix", RAW_SUFFIX,
        "--download-dir", str(raw_dir),
        "--timeout", str(timeout),
        "--variant", str(number),
    ]
    subprocess.run(command, check=True)


def load_entries(path, base_seed):
    if not path.is_file():
        return {}
    manifest = json.loads(path.read_text())
    if manifest.get("version") != PASS_VERSION or manifest.get("target_count") != 500:
        raise ValueError(f"unsupported production manifest: {path}")
    if manifest.get("base_seed") != base_seed:
        raise ValueError(
            f"manifest base seed is {manifest.get('base_seed')}, not requested seed {base_seed}"
        )
    return {entry["variant"]: entry for entry in manifest["entries"]}


def write_progress(output_dir, entries, base_seed):
    ordered = [entries[number] for number in sorted(entries)]
    manifest = {
        "version": PASS_VERSION,
        "target_count": 500,
        "base_seed": base_seed,
        "generator": {
            "model": "krea2_turbo_fp8_scaled.safetensors",
            "steps": 12,
            "cfg": 1,
            "filter_bypass": 1.5,
            "backgrounds": SCREEN_BACKGROUNDS,
            "wardrobe": "fixed garment-by-garment palettes in performer_palettes.py",
        },
        "processor": {
            "name": "official CorridorKey standalone",
            "coarse_hint": "corner-connected chroma plus close screen-color pockets, 5px erosion, 4px blur",
            "gamma_space": "sRGB",
            "despill_strength": 1.0,
            "refiner_strength": 1.0,
            "auto_despeckle": "On",
            "despeckle_size": 400,
        },
        "asset": {
            "format": "AVIF", "width": 600, "height": 900,
            "quality": 70, "alpha_quality": 100,
        },
        "entries": ordered,
    }
    atomic_text(output_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    atomic_text(output_dir / "review.html", review_html(ordered, output_dir))


def self_test():
    assert selected_variants(None, 499, None) == [499, 500]
    assert selected_variants([1, 500], 1, None) == [1, 500]
    details = performer_details(1, 2026072700)
    prompts = {performer_details(number, 2026072700)["prompt"] for number in range(1, 501)}
    wardrobes = [performer_details(number, 2026072700)["wardrobe"] for number in range(1, 501)]
    used = production_wardrobes()
    assert details["id"] == "performer-001"
    assert all(prompt.count("exceptionally beautiful, radiant, and highly photogenic") == 1 for prompt in prompts)
    assert not any("fresh-faced, approachable, and photogenic" in prompt for prompt in prompts)
    assert not any("fresh well-rested features" in prompt for prompt in prompts)
    assert all("Standing upright." in prompt for prompt in prompts)
    assert not any(color_words(wardrobe) & LOWER_BODY_WORDS for wardrobe in wardrobes)
    assert len(used) == 230
    assert set(WARDROBE_PALETTES) == used
    assert len(set(wardrobes)) == 230
    assert all(screen_for(wardrobe) in SCREEN_BACKGROUNDS for wardrobe in wardrobes)
    assert {"green", "blue"} == {
        performer_details(number, 2026072700)["screen"] for number in range(1, 501)
    }
    assert not any(
        color_words(wardrobe) & GREEN_ADJACENT
        and color_words(wardrobe) & BLUE_ADJACENT
        for wardrobe in wardrobes
    )
    assert wardrobe_from_prompt(
        "she wears a fitted cardigan with jeans, against a uniform seamless chroma-key green background"
    ) == "fitted cardigan with jeans"
    with patch("subprocess.run") as run:
        generate(Path("/tools"), "http://comfy", Path("/raw"), "test", 1, 7, 30, "dress", "green")
        assert "--standing" in run.call_args.args[0]
    with tempfile.TemporaryDirectory() as directory:
        raw = Path(directory) / f"01-test_{RAW_SUFFIX}__00001_.png"
        raw.touch()
        assert find_raw(Path(directory), 1) == raw
    completed = Future()
    completed.set_result(None)
    wait_queued(Event(), completed)
    failed = Future()
    failed.set_exception(RuntimeError("queue failed"))
    try:
        wait_queued(Event(), failed)
    except RuntimeError as exc:
        assert str(exc) == "queue failed"
    else:
        raise AssertionError("pre-queue failure was ignored")
    print("transparent performer production runner self-check passed")


def wait_queued(event, future):
    while not event.wait(0.1):
        if future.done():
            future.result()
            return


def main():
    tool_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--label", default=f"performers-transparent-production-v{PASS_VERSION}")
    parser.add_argument("--variant", type=int, action="append")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--stop", type=int)
    parser.add_argument("--seed", type=int, default=2026072700)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--key-only", action="store_true")
    parser.add_argument("--codec-only", action="store_true")
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--corridorkey-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.output_dir:
        parser.error("--output-dir is required")
    if sum((args.key_only, args.codec_only, args.generate_only)) > 1:
        parser.error("--key-only, --codec-only and --generate-only are mutually exclusive")
    if not args.codec_only and not args.server:
        parser.error("--server is required unless --codec-only is used")
    try:
        selected = selected_variants(args.variant, args.start, args.stop)
    except ValueError as exc:
        parser.error(str(exc))

    raw_dir = args.output_dir / "raw"
    entries = load_entries(args.output_dir / "manifest.json", args.seed)
    pending = None
    keyer = None
    if args.corridorkey_root:
        from run_corridorkey_standalone import StandaloneKeyer
        keyer = StandaloneKeyer(args.corridorkey_root)

    def finish(number, details, paths, raw, processed=None):
        if processed:
            details.update(processed)
        if raw:
            details.update({"raw": str(raw), "raw_sha256": sha256(raw)})
        details["avif"] = str(paths["avif"])
        if paths["avif"].is_file():
            details["avif_sha256"] = sha256(paths["avif"])
        entries[number] = details
        write_progress(args.output_dir, entries, args.seed)

    with ThreadPoolExecutor(max_workers=1) as executor:
        for position, number in enumerate(selected, 1):
            details = {**entries.get(number, {}), **performer_details(number, args.seed)}
            raw = find_raw(raw_dir, number)
            if raw and number in entries:
                details.update(entries[number])
            elif raw:
                prompt = metadata(raw)[4]
                if prompt:
                    details.update({"prompt": prompt, "wardrobe": wardrobe_from_prompt(prompt)})
            paths = output_paths(
                args.output_dir / details["screen"] if keyer else args.output_dir,
                details["id"],
            )
            details.update({name: str(path) for name, path in paths.items()})
            print(f"[{position}/{len(selected)}] {details['id']} — {details['name']}", flush=True)

            if args.dry_run:
                print(details["prompt"])
                continue
            if args.codec_only:
                if not paths["corridorkey"].is_file():
                    raise FileNotFoundError(f"missing RGBA master: {paths['corridorkey']}")
                if not paths["avif"].is_file():
                    encode_avif(paths["corridorkey"], paths["avif"])
                finish(number, details, paths, raw)
                continue

            if raw is None:
                if args.key_only:
                    raise FileNotFoundError(f"missing raw image for performer {number}")
                generate(
                    tool_root, args.server, raw_dir, args.label,
                    number, args.seed, args.timeout, details["wardrobe"], details["screen"],
                )
                raw = find_raw(raw_dir, number)
                if raw is None:
                    raise RuntimeError(f"generation produced no raw image for performer {number}")

            if args.generate_only:
                print(f"kept raw on host: {raw}", flush=True)
                continue

            if keyer:
                paths = keyer.process(
                    raw, args.output_dir / details["screen"], details["screen"],
                    details["id"], encode=False,
                )
                finish(number, details, paths, raw)
                continue

            if pending:
                old_number, old_details, old_paths, old_raw, future = pending
                finish(old_number, old_details, old_paths, old_raw, future.result())
                pending = None

            previous_hash = entries.get(number, {}).get("raw_sha256")
            if previous_hash and previous_hash != sha256(raw) and any(path.is_file() for path in paths.values()):
                raise ValueError(
                    f"raw image changed for {details['id']}; move its existing RGBA, QC and AVIF "
                    "outputs aside before reprocessing"
                )

            if args.key_only:
                finish(
                    number, details, paths, raw,
                    process_source(
                        args.server, raw, args.output_dir, args.timeout,
                        details["id"], screen=details["screen"],
                    ),
                )
            else:
                queued = Event()
                future = executor.submit(
                    process_source, args.server, raw, args.output_dir,
                    args.timeout, details["id"], queued, details["screen"],
                )
                wait_queued(queued, future)
                pending = number, details, paths, raw, future

        if pending:
            number, details, paths, raw, future = pending
            finish(number, details, paths, raw, future.result())

    if not args.dry_run:
        print(f"completed {len(selected)} performer(s); manifest has {len(entries)}/500", flush=True)


if __name__ == "__main__":
    main()
