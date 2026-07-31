#!/usr/bin/env python3
"""Run the 20-pair Cover Story wardrobe wording A/B."""

import argparse
import json
from pathlib import Path

from export_personas import atomic_text, sha256
from run_static_performers import BASE_SEED, details, generate


VERSION = 1
VARIANTS = (1, 2, 11, 17, 37, 108, 105, 142, 100, 104, 110, 116, 140, 149, 152, 154, 186, 190, 202, 181)
OLD_WARDROBES = {
    "relaxed plain cotton tee": "relaxed dusty-rose cotton tee",
    "double-breasted blazer over slim trousers": "camel double-breasted wool blazer",
    "studded blazer over a fitted camisole": "charcoal studded blazer over a dusty-rose fitted camisole",
    "luxury satin jumpsuit with a dramatic cape": "rich indigo satin jumpsuit with a silver-gray dramatic cape",
    "loose striped sweater with denim shorts and sneakers": "moss-and-cream loose striped sweater",
    "fitted blazer with a pencil skirt": "moss fitted blazer",
    "vintage graphic T-shirt with an oversized denim jacket and baggy jeans": "charcoal graphic T-shirt with muted-rust print under a stonewashed-gray oversized denim jacket",
    "tie-dye hoodie with ripped jeans and skate shoes": "muted plum-and-rust tie-dye hoodie",
    "dark ripped jeans with a fitted turtleneck and leather jacket": "burgundy fitted turtleneck under a dark-brown leather jacket",
    "relaxed crew-neck sweatshirt": "forest relaxed crew-neck sweatshirt",
    "casual draped jersey top with a modest neckline": "rich plum casual draped jersey top with a modest neckline",
    "fine-knit cardigan over a modest scoop-neck top": "sage fine-knit cardigan over a warm-cream modest scoop-neck top",
    "pencil skirt with a fitted cashmere sweater": "dusty-rose fitted cashmere sweater",
    "off-shoulder cotton dress": "dusty-rose off-shoulder cotton dress",
    "casual sleeveless button-front blouse": "moss casual sleeveless button-front blouse",
    "lightweight cropped zip hoodie over a plain camisole": "dusty-rose lightweight cropped zip hoodie over a warm-cream plain camisole",
    "fashion-forward metallic evening suit": "brushed-bronze metallic evening suit",
    "oversized rugby shirt with baggy jeans": "burgundy-and-cream oversized rugby shirt",
    "faux-fur jacket over a fitted dress": "camel faux-fur jacket over a deep-burgundy fitted dress",
}
TWEAK_VARIANTS = (105, 152, 190)
TWEAK_OLD_WARDROBES = {
    "vintage graphic T-shirt with an oversized denim jacket and baggy jeans": "charcoal fitted graphic crop tee with muted-rust print",
    "tie-dye hoodie with ripped jeans and skate shoes": "solid muted-plum fitted cropped zip hoodie",
    "oversized rugby shirt with baggy jeans": "burgundy-and-cream fitted cropped rugby top with a deep open collar",
}
DIVERSITY_VARIANTS = (
    3, 7, 8, 34, 68, 111, 41, 185, 322, 48,
    66, 115, 127, 159, 10, 71, 260, 38, 54, 5,
)
DIVERSITY_OLD_WARDROBES = {
    "fitted long-sleeve henley with denim shorts": "fitted moss long-sleeve cotton henley",
    "velvet off-the-shoulder gown with opera gloves and a statement necklace": "deep burgundy velvet off-the-shoulder gown with charcoal opera gloves and an antique-gold statement necklace",
    "one-shoulder gown with a metallic belt and elegant updo": "midnight-navy one-shoulder gown with a brushed-gold metallic belt and elegant updo",
    "chic jumpsuit with a waist belt and heels": "deep navy chic jumpsuit with a burgundy waist belt",
    "floor-length satin evening gown with a thigh-high slit, elegant stilettos, and diamond earrings": "floor-length emerald satin evening gown with a thigh-high slit and diamond earrings",
    "modern gothic-inspired fitted dress with subtle lace accents": "aubergine modern gothic-inspired fitted dress with black lace accents",
    "oversized hoodie with loose cargo pants and skate shoes": "burgundy oversized hoodie",
    "blouse with a fitted blazer": "ivory blouse with a forest fitted blazer",
    "mesh long-sleeve top layered under a graphic T-shirt with ripped jeans": "black mesh long-sleeve top layered under a charcoal graphic T-shirt with muted-rust print",
    "linen midi dress with espadrilles": "sage linen midi dress",
    "fitted tank top with linen shorts": "moss fitted tank top",
    "figure-hugging velvet dress": "deep burgundy figure-hugging velvet dress",
    "silk evening gown with a thigh slit": "rich aubergine silk evening gown with a thigh slit",
    "high-waisted leather pants with a silk blouse": "ochre silk blouse",
    "ribbed knit midi dress": "rust ribbed knit midi dress",
    "casual romper with a thin belt and sneakers": "muted olive casual romper with a tan leather thin belt",
    "simple crossover jersey top": "muted emerald simple crossover jersey top",
    "soft wrap-style jersey top": "muted indigo soft wrap-style jersey top",
    "graphic T-shirt with cargo shorts and skate shoes": "cream graphic T-shirt with muted-rust print",
    "sparkling sequined gown": "sparkling deep-plum sequined gown",
}
BRIGHTNESS_VARIANTS = (
    3, 174, 10, 27, 83, 351, 54, 117, 132, 347,
    127, 196, 405, 260, 309, 454, 115, 212, 268, 19,
    330, 23, 426, 147, 160, 403, 236, 246, 250, 310,
    320, 398, 301, 385, 89, 221, 452, 126, 335, 362,
)
BRIGHTNESS_OLD_WARDROBES = {
    "fitted long-sleeve henley with denim shorts": "fitted black off-the-shoulder cashmere sweater",
    "ribbed knit midi dress": "oversized black blazer over a champagne satin camisole",
    "graphic T-shirt with cargo shorts and skate shoes": "ivory lace-trim camisole under a loosely draped black shirt",
    "silk evening gown with a thigh slit": "matte-black structured top with an asymmetric neckline",
    "simple crossover jersey top": "structured black blazer over a sweetheart-neck top",
    "figure-hugging velvet dress": "faded charcoal off-the-shoulder band sweatshirt",
    "fitted crop top with a flowing maxi skirt": "fitted black fine-knit top with a wide boat neckline",
    "high-waisted midi skirt with a tucked-in blouse": "fitted black scoop-neck bodysuit",
    "sleeveless hoodie with cargo joggers and skate shoes": "black sleeveless bodysuit under a sharply tailored blazer",
    "faux-leather leggings with an oversized rock band shirt": "fitted black wrap top with an angular neckline",
    "hoodie over a pleated tennis skirt": "tailored camel blazer over a fitted black turtleneck",
    "ribbed scoop-neck top with cargo pants": "black tuxedo-style jacket over a satin cowl-neck shell",
    "casual henley-style knit top": "sculptural black satin top with dramatic folds",
    "jumpsuit with accessories": "fitted black henley with metal eyelet detailing",
}
BRIGHTNESS_TWEAK_VARIANTS = (
    89, 221, 438, 240, 310, 320, 127, 196, 416, 147, 403, 441,
)
BRIGHTNESS_TWEAK_OLD_WARDROBES = {
    "silk evening gown with a thigh slit": "close-fitting electric-violet structured velvet top with an asymmetric neckline",
    "sleeveless hoodie with cargo joggers and skate shoes": "fitted steel-gray cropped blazer worn open over a deep-petrol sleeveless bodysuit",
    "hoodie over a pleated tennis skirt": "fitted camel blazer worn open over a close-fitting deep-plum turtleneck",
    "casual henley-style knit top": "close-fitting electric-violet sculptural satin top with asymmetric gathered folds",
}
TIGHTNESS_VARIANTS = (
    49, 115, 127, 151, 8, 14, 68, 446, 9, 63, 153, 255,
    10, 34, 148, 382, 5, 11, 71, 239, 6, 198, 282, 294,
)
TIGHTNESS_OLD_WARDROBES = {
    "modern asymmetrical evening gown with statement earrings": "fitted magenta ribbed scoop-neck top",
    "figure-hugging velvet dress": "close-fitting deep-wine off-the-shoulder band sweatshirt",
    "silk evening gown with a thigh slit": "close-fitting dark-petrol velvet top with a sculpted asymmetric neckline",
    "wrap dress with ankle boots": "black velvet blouse with dramatic gathered sleeves",
    "one-shoulder gown with a metallic belt and elegant updo": "black silk blouse with the collar open",
    "cropped blazer with a fitted dress": "fitted dark-petrol long-sleeve jersey top with gathered ruching",
    "floor-length satin evening gown with a thigh-high slit, elegant stilettos, and diamond earrings": "fitted deep-wine velvet top with a wide portrait neckline",
    "lace cocktail dress with a delicate necklace": "fitted white architectural blouse with a sharp neckline",
    "fitted cardigan buttoned as a top with jeans": "fitted charcoal square-neck bodysuit",
    "satin slip dress": "fitted midnight-blue crushed-velvet top with a portrait neckline",
    "wrap dress with delicate accessories": "fitted dark-aubergine brocade top",
    "off-shoulder knit dress with ankle boots": "fitted black top with sleek geometric seam lines",
    "ribbed knit midi dress": "fitted deep-navy blazer worn open over a close-fitting champagne satin camisole",
    "chic jumpsuit with a waist belt and heels": "fitted black velvet sweetheart-neck top",
    "metallic evening gown": "fitted black mock-neck lace blouse",
    "silk mermaid gown with crystal jewelry": "fitted dusty-mauve wrap knit top",
    "sparkling sequined gown": "fitted black feather-trimmed evening top",
    "studded blazer over a fitted camisole": "charcoal fitted satin camisole with a low square neckline",
    "casual romper with a thin belt and sneakers": "fitted ivory blouse with the top buttons open",
    "casual ribbed scoop-neck top": "charcoal corset-seamed top under an oversized black blazer",
    "lightweight zip hoodie over a fitted tank top": "champagne satin cowl-neck camisole",
    "cocktail dress with metallic heels": "black mesh long-sleeve top over a fitted camisole",
    "knit dress with opaque tights": "fitted petrol-blue top with geometric paneling",
    "off-shoulder knit dress": "deep-navy blazer over a low square-neck ivory top",
}
TIGHTNESS_CONFIRM_VARIANTS = (
    391, 415, 481, 493, 80, 212, 296, 416, 21, 261, 273, 363,
    94, 196, 268, 394, 83, 119, 443, 473, 30, 96, 312, 498,
)
ROUND1_NEW_OVERRIDES = dict(TWEAK_OLD_WARDROBES)
RECIPE = {
    "model": "krea2_turbo_fp8_scaled.safetensors",
    "steps": 12,
    "cfg": 1,
    "filter_bypass": 1.5,
    "negative_prompt": None,
    "base_seed": BASE_SEED,
    "background_blur": True,
}


def pair(number, old_wardrobes=OLD_WARDROBES, new_overrides=ROUND1_NEW_OVERRIDES):
    current = details(number, background_blur=True)
    from experiment_headshots import PRODUCTION_EXPANSION

    wardrobe_key = PRODUCTION_EXPANSION[number - 1][3]["wardrobe"]
    old = old_wardrobes[wardrobe_key]
    latest = current["wardrobe"]
    new = new_overrides.get(wardrobe_key, latest)
    if current["prompt"].count(latest) != 1:
        raise ValueError(f"current wardrobe is not unique in prompt {number}")
    current["prompt"] = current["prompt"].replace(latest, new)
    current["wardrobe"] = new
    return tuple({
        **current,
        "arm": arm,
        "id": f"performer-{number:03d}_{arm}",
        "stem": f"performer-{number:03d}_{arm}",
        "wardrobe": wardrobe,
        "prompt": current["prompt"].replace(current["wardrobe"], wardrobe),
    } for arm, wardrobe in (("A", old), ("B", current["wardrobe"])))


def load(path, variants, recipe):
    if not path.exists():
        return {}
    manifest = json.loads(path.read_text())
    if (
        manifest.get("version") != VERSION
        or manifest.get("variants") != list(variants)
        or manifest.get("recipe") != recipe
    ):
        raise ValueError(f"incompatible wardrobe A/B manifest: {path}")
    return {(entry["slot"], entry["arm"]): entry for entry in manifest["entries"]}


def write(output_dir, entries, variants, recipe):
    ordered = [entries[key] for key in sorted(entries)]
    atomic_text(output_dir / "manifest.json", json.dumps({
        "version": VERSION,
        "variants": list(variants),
        "recipe": recipe,
        "entries": ordered,
    }, indent=2) + "\n")


def self_test():
    assert len(VARIANTS) == len(set(VARIANTS)) == 20
    assert len(OLD_WARDROBES) == 19
    assert {entry["arm"] for number in VARIANTS for entry in pair(number)} == {"A", "B"}
    for number in VARIANTS:
        before, after = pair(number)
        assert before["seed"] == after["seed"]
        assert before["prompt"].replace(before["wardrobe"], after["wardrobe"]) == after["prompt"]
        assert before["wardrobe"] != after["wardrobe"]
        assert "cape" not in after["wardrobe"].lower()
    assert len(TWEAK_VARIANTS) == len(TWEAK_OLD_WARDROBES) == 3
    for number in TWEAK_VARIANTS:
        before, after = pair(number, TWEAK_OLD_WARDROBES, {})
        assert before["seed"] == after["seed"]
        assert "crop" in before["wardrobe"] and "crop" not in after["wardrobe"]
    assert len(DIVERSITY_VARIANTS) == len(set(DIVERSITY_VARIANTS)) == 20
    assert len(DIVERSITY_OLD_WARDROBES) == 20
    from experiment_headshots import PRODUCTION_EXPANSION
    from performer_palettes import WARDROBE_STYLE_GROUPS
    diversity_keys = {
        PRODUCTION_EXPANSION[number - 1][3]["wardrobe"]
        for number in DIVERSITY_VARIANTS
    }
    assert diversity_keys == set(DIVERSITY_OLD_WARDROBES)
    assert {(number - 1) % 6 for number in DIVERSITY_VARIANTS} == set(range(6))
    assert {
        WARDROBE_STYLE_GROUPS[key] for key in diversity_keys
    } == set(WARDROBE_STYLE_GROUPS.values())
    for number in DIVERSITY_VARIANTS:
        before, after = pair(number, DIVERSITY_OLD_WARDROBES, {})
        assert before["seed"] == after["seed"]
        assert before["prompt"].replace(before["wardrobe"], after["wardrobe"]) == after["prompt"]
    assert len(BRIGHTNESS_VARIANTS) == len(set(BRIGHTNESS_VARIANTS)) == 40
    assert len(BRIGHTNESS_OLD_WARDROBES) == 14
    brightness_keys = {
        PRODUCTION_EXPANSION[number - 1][3]["wardrobe"]
        for number in BRIGHTNESS_VARIANTS
    }
    assert brightness_keys == set(BRIGHTNESS_OLD_WARDROBES)
    crop_counts = {
        crop: sum((number - 1) % 6 == crop for number in BRIGHTNESS_VARIANTS)
        for crop in range(6)
    }
    assert sorted(crop_counts.values()) == [6, 6, 7, 7, 7, 7]
    for number in BRIGHTNESS_VARIANTS:
        before, after = pair(number, BRIGHTNESS_OLD_WARDROBES, {})
        assert before["seed"] == after["seed"]
        assert before["prompt"].replace(before["wardrobe"], after["wardrobe"]) == after["prompt"]
    assert len(BRIGHTNESS_TWEAK_VARIANTS) == len(set(BRIGHTNESS_TWEAK_VARIANTS)) == 12
    assert len(BRIGHTNESS_TWEAK_OLD_WARDROBES) == 4
    tweak_keys = {
        PRODUCTION_EXPANSION[number - 1][3]["wardrobe"]
        for number in BRIGHTNESS_TWEAK_VARIANTS
    }
    assert tweak_keys == set(BRIGHTNESS_TWEAK_OLD_WARDROBES)
    assert {
        crop: sum((number - 1) % 6 == crop for number in BRIGHTNESS_TWEAK_VARIANTS)
        for crop in range(6)
    } == {crop: 2 for crop in range(6)}
    for number in BRIGHTNESS_TWEAK_VARIANTS:
        before, after = pair(number, BRIGHTNESS_TWEAK_OLD_WARDROBES, {})
        assert before["seed"] == after["seed"]
        assert before["prompt"].replace(before["wardrobe"], after["wardrobe"]) == after["prompt"]
    assert len(TIGHTNESS_VARIANTS) == len(set(TIGHTNESS_VARIANTS)) == 24
    assert len(TIGHTNESS_OLD_WARDROBES) == 24
    tightness_keys = {
        PRODUCTION_EXPANSION[number - 1][3]["wardrobe"]
        for number in TIGHTNESS_VARIANTS
    }
    assert tightness_keys == set(TIGHTNESS_OLD_WARDROBES)
    assert {
        crop: sum((number - 1) % 6 == crop for number in TIGHTNESS_VARIANTS)
        for crop in range(6)
    } == {crop: 4 for crop in range(6)}
    assert {
        WARDROBE_STYLE_GROUPS[key] for key in tightness_keys
    } == set(WARDROBE_STYLE_GROUPS.values())
    from performer_palettes import TIGHTNESS_DIRECTION
    for number in TIGHTNESS_VARIANTS:
        before, after = pair(number, TIGHTNESS_OLD_WARDROBES, {})
        assert before["seed"] == after["seed"]
        assert before["prompt"].replace(before["wardrobe"], after["wardrobe"]) == after["prompt"]
        assert TIGHTNESS_DIRECTION in after["wardrobe"]
        assert not any(term in after["wardrobe"].lower() for term in ("loose", "oversized", "boxy"))
    assert len(TIGHTNESS_CONFIRM_VARIANTS) == len(set(TIGHTNESS_CONFIRM_VARIANTS)) == 24
    assert not set(TIGHTNESS_CONFIRM_VARIANTS) & set(TIGHTNESS_VARIANTS)
    confirm_keys = {
        PRODUCTION_EXPANSION[number - 1][3]["wardrobe"]
        for number in TIGHTNESS_CONFIRM_VARIANTS
    }
    assert confirm_keys <= set(TIGHTNESS_OLD_WARDROBES)
    assert {
        crop: sum((number - 1) % 6 == crop for number in TIGHTNESS_CONFIRM_VARIANTS)
        for crop in range(6)
    } == {crop: 4 for crop in range(6)}
    assert {
        WARDROBE_STYLE_GROUPS[key] for key in confirm_keys
    } == set(WARDROBE_STYLE_GROUPS.values())
    for number in TIGHTNESS_CONFIRM_VARIANTS:
        before, after = pair(number, TIGHTNESS_OLD_WARDROBES, {})
        assert before["seed"] == after["seed"]
        assert before["prompt"].replace(before["wardrobe"], after["wardrobe"]) == after["prompt"]
    print("wardrobe A/B runner self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--label", default="wardrobe-sensual-ab-v1")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    passes = parser.add_mutually_exclusive_group()
    passes.add_argument("--tweak-pass", action="store_true")
    passes.add_argument("--diversity-pass", action="store_true")
    passes.add_argument("--brightness-pass", action="store_true")
    passes.add_argument("--brightness-tweak-pass", action="store_true")
    passes.add_argument("--tightness-pass", action="store_true")
    passes.add_argument("--tightness-confirm-pass", action="store_true")
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
    if args.tightness_confirm_pass:
        variants = TIGHTNESS_CONFIRM_VARIANTS
        old_wardrobes = TIGHTNESS_OLD_WARDROBES
        new_overrides = {}
        recipe = {**RECIPE, "tightness_confirm_pass": 2}
    elif args.tightness_pass:
        variants = TIGHTNESS_VARIANTS
        old_wardrobes = TIGHTNESS_OLD_WARDROBES
        new_overrides = {}
        recipe = {**RECIPE, "tightness_pass": 1}
    elif args.brightness_tweak_pass:
        variants = BRIGHTNESS_TWEAK_VARIANTS
        old_wardrobes = BRIGHTNESS_TWEAK_OLD_WARDROBES
        new_overrides = {}
        recipe = {**RECIPE, "brightness_tweak_pass": 3}
    elif args.brightness_pass:
        variants = BRIGHTNESS_VARIANTS
        old_wardrobes = BRIGHTNESS_OLD_WARDROBES
        new_overrides = {}
        recipe = {**RECIPE, "brightness_pass": 2}
    elif args.diversity_pass:
        variants = DIVERSITY_VARIANTS
        old_wardrobes = DIVERSITY_OLD_WARDROBES
        new_overrides = {}
        recipe = {**RECIPE, "diversity_pass": 1}
    elif args.tweak_pass:
        variants = TWEAK_VARIANTS
        old_wardrobes = TWEAK_OLD_WARDROBES
        new_overrides = {}
        recipe = {**RECIPE, "tweak_pass": 2}
    else:
        variants = VARIANTS
        old_wardrobes = OLD_WARDROBES
        new_overrides = ROUND1_NEW_OVERRIDES
        recipe = RECIPE
    entries = load(manifest_path, variants, recipe)
    raw_dir = args.output_dir / "raw"
    for position, number in enumerate(variants, 1):
        for entry in pair(number, old_wardrobes, new_overrides):
            key = number, entry["arm"]
            previous = entries.get(key)
            raw = raw_dir / f"{entry['id']}.png"
            entry["raw"] = str(raw)
            print(f"[{position}/{len(variants)} {entry['arm']}] {entry['id']} — {entry['wardrobe']}", flush=True)
            if args.dry_run:
                print(entry["prompt"])
                continue
            if previous:
                if any(previous.get(field) != entry[field] for field in ("seed", "prompt", "wardrobe")):
                    raise ValueError(f"A/B details changed for {entry['id']}")
                if raw.is_file() and sha256(raw) == previous.get("source_sha256"):
                    print("already complete", flush=True)
                    continue
                raise ValueError(f"completed raw changed or disappeared for {entry['id']}")
            if not raw.exists():
                entry["prompt_id"] = generate(
                    args.server, raw, entry, args.label, args.timeout, 1.5
                )
            entry.update({
                "source_sha256": sha256(raw),
                "bytes": raw.stat().st_size,
            })
            entries[key] = entry
            write(args.output_dir, entries, variants, recipe)
    if not args.dry_run:
        print(f"manifest has {len(entries)}/{len(variants) * 2} images", flush=True)


if __name__ == "__main__":
    main()
