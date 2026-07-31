#!/usr/bin/env python3
"""Generate and export reusable wide performer background plates."""

import argparse
import html
import json
import tempfile
from collections import Counter
from pathlib import Path

from PIL import Image

from comfy import prepare, run
from experiment_headshots import configure_filter_bypass, configure_guidance
from export_personas import atomic_text, sha256
from export_scene_assets import save_avif
from review_headshots import metadata


SCENES = (
    ("bedroom-scandinavian", "bedroom", "bedroom with a neatly made bed, pale wood furniture, and soft window light", "Scandinavian", ("casual", "winter")),
    ("bedroom-hotel", "bedroom", "spacious hotel bedroom with a king bed, upholstered headboard, and warm ambient lamps", "luxury contemporary", ("evening", "glamorous", "date-night")),
    ("living-modern", "living-room", "open living room with large windows, low seating, and restrained decor", "contemporary", ("casual", "smart-casual")),
    ("living-midcentury", "living-room", "comfortable lounge with sculptural seating, walnut accents, and broad windows", "mid-century modern", ("casual", "smart-casual")),
    ("kitchen-white", "kitchen", "bright kitchen with a long island, white cabinetry, and subtle stone surfaces", "minimalist", ("casual", "smart-casual")),
    ("kitchen-loft", "kitchen", "loft kitchen with a broad island, exposed brick, steel details, and tall windows", "industrial", ("casual", "smart-casual")),
    ("bathroom-marble", "bathroom", "spacious marble bathroom with a freestanding tub and diffuse daylight", "luxury", ("glamorous", "evening")),
    ("bathroom-spa", "bathroom", "calm spa bathroom with stone, pale timber, folded towels, and soft indirect light", "Japandi", ("casual", "summer")),
    ("office-home", "office", "bright home office with a long desk, restrained shelving, and a large window", "contemporary", ("professional", "smart-casual")),
    ("office-executive", "office", "executive office overlooking a skyline with a broad desk and tailored seating", "luxury contemporary", ("professional", "smart-casual")),
    ("hotel-suite", "hotel", "large hotel suite lounge with elegant seating, drapery, and an open doorway to a bedroom", "luxury", ("evening", "glamorous", "date-night")),
    ("hotel-rooftop", "hotel", "sheltered rooftop lounge overlooking the city with low seating and warm practical lights", "contemporary luxury", ("evening", "glamorous", "date-night")),
    ("cafe-coffee", "cafe", "trendy coffee shop with window seating, pale counters, and uncluttered tables", "Scandinavian", ("casual", "smart-casual")),
    ("restaurant-upscale", "cafe", "upscale restaurant with widely spaced tables, soft wall lighting, and elegant finishes", "contemporary luxury", ("evening", "glamorous", "date-night")),
    ("urban-sidewalk", "urban", "quiet downtown sidewalk with modern storefronts, broad pavement, and distant architecture", "contemporary", ("casual", "smart-casual", "winter")),
    ("urban-rooftop", "urban", "open rooftop overlooking a city skyline with a simple parapet and broad sky", "modern", ("casual", "smart-casual", "evening")),
    ("nature-forest", "nature", "wide forest trail with soft undergrowth, tall trees, and diffuse cloudy daylight", "natural", ("casual", "summer", "winter")),
    ("nature-lakeside", "nature", "calm lakeside overlook with a broad shoreline, distant trees, and open sky", "natural", ("casual", "summer", "date-night")),
    ("resort-pool", "resort", "luxury pool deck with pale stone, restrained loungers pushed to the sides, and a distant villa", "coastal luxury", ("summer", "glamorous")),
    ("resort-terrace", "resort", "private villa terrace with a distant sea view, pale masonry, and sparse side seating", "Mediterranean", ("summer", "date-night", "glamorous")),
    ("transport-airport", "transport", "quiet premium airport lounge with large windows, side seating, and distant runway views", "contemporary", ("professional", "smart-casual")),
    ("transport-yacht", "transport", "wide luxury yacht deck with side railings, an open sea horizon, and restrained seating", "coastal luxury", ("summer", "glamorous")),
    ("misc-gallery", "misc", "large art gallery with pale walls, abstract artwork placed toward the sides, and polished floors", "minimalist", ("professional", "smart-casual", "glamorous")),
    ("misc-bookstore", "misc", "independent bookstore with shelves framing the sides, a clear central aisle, and warm daylight", "warm contemporary", ("casual", "smart-casual", "winter")),
)
TIMES = (
    "morning daylight", "midday daylight", "cloudy daylight",
    "afternoon light", "soft indoor lighting", "warm practical lighting",
)
PALETTES = (
    "white and oak", "beige and cream", "charcoal and black", "warm walnut",
    "pastel accents", "earth tones", "modern monochrome",
)
CAMERAS = (
    "eye-level medium-wide framing", "eye-level centered framing",
    "eye-level slight three-quarter framing", "standing eye-level wide framing",
)
CROPS = (
    {"id": "left", "position_x": 12, "zoom": 1.00},
    {"id": "center-left", "position_x": 37, "zoom": 1.05},
    {"id": "center-right", "position_x": 63, "zoom": 1.00},
    {"id": "right", "position_x": 88, "zoom": 1.05},
)
POC_VARIANTS = (1, 3, 11, 13, 15, 18)
PROMPT_SUFFIX = (
    " Wide 3:2 landscape background plate with a continuous uninterrupted full-width composition. "
    "Distribute useful environmental detail across the width while preserving broad clear human "
    "silhouette space from head height through the lower frame. Eye-level natural perspective, "
    "soft frontal diffuse illumination, physically "
    "accurate materials, high-detail realistic architectural and location photography. Completely "
    "unoccupied and uncluttered, with text-free and logo-free surfaces, empty mirror reflections, "
    "no foreground obstruction, and no dominant object in the center."
)


def scene(number):
    scene_id, category, description, style, compatibility = SCENES[number - 1]
    return {
        "id": f"bg-{number:03d}-{scene_id}",
        "number": number,
        "category": category,
        "scene": description,
        "style": style,
        "compatibility": compatibility,
        "time": TIMES[(number - 1) % len(TIMES)],
        "palette": PALETTES[(number * 3 - 1) % len(PALETTES)],
        "camera": CAMERAS[(number * 5 - 1) % len(CAMERAS)],
    }


def scene_prompt(entry):
    return (
        f"Photorealistic empty environmental scene: {entry['scene']}. "
        f"{entry['style']} design, {entry['time']}, {entry['palette']} color palette, "
        f"{entry['camera']}.{PROMPT_SUFFIX}"
    )


def selected_variants(variants, start, stop, poc):
    if variants and poc:
        raise ValueError("--variant and --poc are mutually exclusive")
    selected = list(POC_VARIANTS) if poc else variants or list(range(start, (stop or len(SCENES)) + 1))
    if not selected or any(number < 1 or number > len(SCENES) for number in selected):
        raise ValueError(f"variants must be between 1 and {len(SCENES)}")
    return selected


def configure_size(workflow, width=1920, height=1280):
    nodes = [node for node in workflow.values() if node.get("class_type") == "EmptyLatentImage"]
    if len(nodes) != 1:
        raise ValueError(f"expected one EmptyLatentImage, found {len(nodes)}")
    nodes[0]["inputs"].update({"width": width, "height": height, "batch_size": 1})


def configure_sampler(workflow):
    nodes = [node for node in workflow.values() if node.get("class_type") == "KSampler"]
    if len(nodes) != 1:
        raise ValueError(f"expected one KSampler, found {len(nodes)}")
    nodes[0]["inputs"].update({
        "steps": 12, "cfg": 1, "sampler_name": "er_sde",
        "scheduler": "simple", "denoise": 1,
    })


def raw_path(raw_dir, entry):
    matches = sorted(raw_dir.glob(f"{entry['number']:02d}-{entry['id']}_*.png"))
    if len(matches) > 1:
        raise ValueError(f"multiple raw images found for {entry['id']}: {matches}")
    return matches[0] if matches else None


def generate(tool_root, server, output_dir, label, entry, seed, timeout, bypass_strength):
    workflow = json.loads((tool_root / "workflows" / "krea2-turbo-fp8.json").read_text())
    configure_size(workflow)
    configure_sampler(workflow)
    if bypass_strength:
        configure_filter_bypass(workflow, bypass_strength)
    configure_guidance(workflow, 1, None)
    prepare(
        workflow, scene_prompt(entry), seed,
        f"cover-story/experiments/{label}/{entry['number']:02d}-{entry['id']}_",
    )
    result = run(server, workflow, output_dir, timeout)
    return Path(result["images"][0]["path"])


def portrait_crop(image, crop, size=(600, 900)):
    zoom = crop["zoom"]
    visible_height = image.height / zoom
    visible_width = visible_height * size[0] / size[1]
    left = (image.width - visible_width) * crop["position_x"] / 100
    top = (image.height - visible_height) / 2
    return image.crop((
        round(left), round(top), round(left + visible_width), round(top + visible_height),
    )).resize(size, Image.Resampling.LANCZOS)


def portrait_crop_bytes(image):
    with tempfile.TemporaryDirectory(prefix="cover-story-background-crops-") as directory:
        root = Path(directory)
        outputs = []
        for crop in CROPS:
            output = root / f"{crop['id']}.avif"
            save_avif(portrait_crop(image, crop), output)
            outputs.append(output)
        return sum(output.stat().st_size for output in outputs)


def review_html(entries, performer_dir):
    performers = sorted(performer_dir.glob("performer-*.avif"))[:8] if performer_dir else []
    cards = []
    for background_index, entry in enumerate(entries):
        for crop_index, crop in enumerate(entry["crops"]):
            actor = performers[(background_index + crop_index) % len(performers)].resolve().as_uri() if performers else ""
            actor_tag = f'<img class="actor" src="{actor}" alt="">' if actor else ""
            cards.append(
                f'<article><div class="frame"><img class="background" src="assets/{entry["id"]}.avif" '
                f'style="--x:{crop["position_x"]}%;--zoom:{crop["zoom"]}" alt="">{actor_tag}</div>'
                f'<p>{html.escape(entry["id"])} · {crop["id"]} · {crop["zoom"]:.2f}×</p></article>'
            )
    return f"""<!doctype html>
<meta charset="utf-8"><title>Performer background review</title>
<style>
body{{background:#17191e;color:#eee;font:14px system-ui;margin:24px}}
main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:18px}}
article{{min-width:0}}p{{overflow-wrap:anywhere}}.frame{{aspect-ratio:2/3;background:#252a33;
overflow:hidden;position:relative}}img{{display:block}}.background{{height:100%;width:100%;
object-fit:cover;object-position:var(--x) 50%;
filter:blur(5px);transform:scale(calc(var(--zoom) + .04))}}.actor{{bottom:0;
height:100%;left:0;object-fit:contain;position:absolute;width:100%;
filter:drop-shadow(0 8px 9px #0008)}}
</style><h1>Performer background review</h1><main>{''.join(cards)}</main>"""


def load_entries(path, base_seed):
    if not path.is_file():
        return {}
    manifest = json.loads(path.read_text())
    if manifest.get("version") != 1 or manifest.get("base_seed") != base_seed:
        raise ValueError(f"incompatible background manifest: {path}")
    return {entry["number"]: entry for entry in manifest["entries"]}


def write_progress(output_dir, entries, base_seed, performer_dir, target_count=len(SCENES)):
    ordered = [entries[number] for number in sorted(entries)]
    manifest = {
        "version": 1,
        "target_count": target_count,
        "base_seed": base_seed,
        "source": {"width": 1920, "height": 1280},
        "asset": {"format": "AVIF", "quality": 70},
        "entries": ordered,
        "totals": {
            "raw_bytes": sum(Path(entry["raw"]).stat().st_size for entry in ordered),
            "avif_bytes": sum(Path(entry["avif"]).stat().st_size for entry in ordered),
            "equivalent_portrait_crop_bytes": sum(entry["equivalent_portrait_crop_bytes"] for entry in ordered),
        },
    }
    manifest["totals"]["wide_savings_bytes"] = (
        manifest["totals"]["equivalent_portrait_crop_bytes"] - manifest["totals"]["avif_bytes"]
    )
    atomic_text(output_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    atomic_text(output_dir / "review.html", review_html(ordered, performer_dir))


def self_test():
    assert len(SCENES) == 24
    assert Counter(entry[1] for entry in SCENES) == {
        "bedroom": 2, "living-room": 2, "kitchen": 2, "bathroom": 2,
        "office": 2, "hotel": 2, "cafe": 2, "urban": 2, "nature": 2,
        "resort": 2, "transport": 2, "misc": 2,
    }
    assert selected_variants(None, 1, None, True) == list(POC_VARIANTS)
    assert len({scene(number)["id"] for number in range(1, 25)}) == 24
    assert all("continuous uninterrupted full-width composition" in scene_prompt(scene(number)) for number in range(1, 25))
    assert not any("portrait crops" in scene_prompt(scene(number)) for number in range(1, 25))
    workflow = {
        "1": {"class_type": "EmptyLatentImage", "inputs": {}},
        "2": {"class_type": "KSampler", "inputs": {}},
    }
    configure_size(workflow)
    configure_sampler(workflow)
    assert workflow["1"]["inputs"] == {"width": 1920, "height": 1280, "batch_size": 1}
    assert workflow["2"]["inputs"]["steps"] == 12
    assert portrait_crop(Image.new("RGB", (1920, 1280)), CROPS[0]).size == (600, 900)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fake = root / "01-bg-001-bedroom-scandinavian__00001_.png"
        fake.touch()
        assert raw_path(root, scene(1)) == fake
    print("performer background runner self-check passed")


def main():
    tool_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--performer-dir", type=Path, default=Path(
        "/mnt/Misc/sd/cover-story/performers-transparent-production-v6/assets"
    ))
    parser.add_argument("--label", default="performer-backgrounds-v1")
    parser.add_argument("--scene-plan", type=Path)
    parser.add_argument("--variant", type=int, action="append")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--stop", type=int)
    parser.add_argument("--poc", action="store_true")
    parser.add_argument("--seed", type=int, default=2026073000)
    parser.add_argument("--bypass-strength", type=float, default=1.5)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--codec-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.output_dir:
        parser.error("--output-dir is required")
    if args.bypass_strength < 0:
        parser.error("--bypass-strength cannot be negative")
    if not args.codec_only and not args.dry_run and not args.server:
        parser.error("--server is required unless --codec-only or --dry-run is used")
    if args.scene_plan:
        if args.variant or args.poc or args.start != 1 or args.stop:
            parser.error("--scene-plan cannot be combined with variant selection")
        planned = json.loads(args.scene_plan.read_text())
        if not planned or any(entry.get("number") != number for number, entry in enumerate(planned, 1)):
            parser.error("--scene-plan entries must be numbered consecutively from 1")
        selected = list(range(1, len(planned) + 1))
    else:
        planned = None
        try:
            selected = selected_variants(args.variant, args.start, args.stop, args.poc)
        except ValueError as exc:
            parser.error(str(exc))

    raw_dir = args.output_dir / "raw"
    entries = load_entries(args.output_dir / "manifest.json", args.seed)
    for position, number in enumerate(selected, 1):
        entry = dict(planned[number - 1]) if planned else scene(number)
        entry["prompt"] = scene_prompt(entry)
        entry["seed"] = entry.get("seed", args.seed + number * 104729)
        entry["crops"] = CROPS
        raw = raw_path(raw_dir, entry)
        print(f"[{position}/{len(selected)}] {entry['id']} — seed {entry['seed']}", flush=True)
        if args.dry_run:
            print(entry["prompt"])
            continue
        if raw is None:
            if args.codec_only:
                raise FileNotFoundError(f"missing raw image for {entry['id']}")
            raw_dir.mkdir(parents=True, exist_ok=True)
            raw = generate(
                tool_root, args.server, raw_dir, args.label,
                entry, entry["seed"], args.timeout, args.bypass_strength,
            )
        entry["prompt"] = metadata(raw)[4] or entry["prompt"]
        raw_hash = sha256(raw)
        previous = entries.get(number, {})
        asset = args.output_dir / "assets" / f"{entry['id']}.avif"
        if (
            previous.get("raw_sha256")
            and previous["raw_sha256"] != raw_hash
            and asset.is_file()
        ):
            raise ValueError(
                f"raw image changed for {entry['id']}; move its existing AVIF aside before re-exporting"
            )
        with Image.open(raw) as opened:
            if opened.size != (1920, 1280):
                raise ValueError(f"{raw} is {opened.size}, expected 1920x1280")
            if not asset.is_file():
                save_avif(opened.convert("RGB"), asset)
            crop_bytes = previous.get("equivalent_portrait_crop_bytes") or portrait_crop_bytes(
                opened.convert("RGB")
            )
        entry.update({
            "raw": str(raw), "raw_sha256": raw_hash,
            "avif": str(asset), "avif_sha256": sha256(asset),
            "equivalent_portrait_crop_bytes": crop_bytes,
        })
        entries[number] = entry
        write_progress(args.output_dir, entries, args.seed, args.performer_dir, len(selected))

    if not args.dry_run:
        print(f"completed {len(selected)} background(s); manifest has {len(entries)}/{len(selected)}", flush=True)


if __name__ == "__main__":
    main()
