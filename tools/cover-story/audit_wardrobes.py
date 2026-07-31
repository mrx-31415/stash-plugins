#!/usr/bin/env python3
"""Validate and report the Cover Story production wardrobe rotation."""

import argparse
import re
from collections import Counter
from pathlib import Path

from experiment_headshots import PRODUCTION_EXPANSION
from export_personas import atomic_text
from performer_palettes import (
    TIGHTNESS_DIRECTION,
    WARDROBE_PALETTES,
    WARDROBE_STYLE_GROUPS,
)


REPORT = Path(__file__).with_name("CURRENT_WARDROBE_ROTATION.md")
STYLE_TARGETS = {
    "sensual contemporary": 100,
    "dark glam": 75,
    "romantic goth": 60,
    "punk / grunge / alt": 60,
    "sleek alternative / industrial": 50,
    "tailored sexy": 60,
    "soft luxe / sensual casual": 50,
    "statement / editorial": 45,
}
CHANGE_SUMMARY = {"retained": 20, "rewritten": 39, "replaced": 171}
WARNING_TERMS = (
    "midi", "mini dress", "maxi", "skirt", "trousers", "shorts", "thigh slit",
    "train", "floor-length", "mermaid", "belt",
)
COLORS = (
    "black", "charcoal", "gunmetal", "steel gray", "silver", "cool white",
    "ivory", "crimson", "cherry red", "oxblood", "dark wine", "petrol blue",
    "midnight blue", "teal", "electric violet", "pale lilac", "magenta",
    "deep emerald", "dusty rose", "rust", "moss", "forest", "burgundy",
    "plum", "cream",
)
GARMENTS = (
    "top", "blouse", "camisole", "bodysuit", "blazer", "jacket", "sweater",
    "tee", "shirt", "shell", "vest", "hoodie", "bodice", "tank", "dress",
    "jumpsuit",
)
NECKLINES = (
    "off-the-shoulder", "one-shoulder", "sweetheart", "square-neck", "scoop-neck",
    "portrait neckline", "boat neckline", "cowl-neck", "halter", "wrap",
    "open collar", "asymmetric neckline", "keyhole", "cold-shoulder",
    "sheer upper", "illusion", "corset-inspired", "lace-up", "collarbone cutout",
    "sculptural shoulders",
)
FABRICS = (
    "velvet", "crushed-velvet", "burnout-velvet", "lace", "chiffon", "organza",
    "tulle", "brocade", "jacquard", "leather", "suede", "metallic-knit",
    "sequined", "chainmail-inspired", "mesh", "ribbed cotton", "silk", "satin",
    "cashmere", "fine-knit", "denim", "jersey",
)


def has(phrase, term):
    pattern = re.escape(term).replace(r"\ ", r"[- ]")
    return re.search(rf"(?<![a-z]){pattern}(?![a-z])", phrase.lower()) is not None


def frequencies(terms, keys, slots):
    return [
        (term, sum(has(WARDROBE_PALETTES[key], term) for key in keys),
         sum(slots[key] for key in keys if has(WARDROBE_PALETTES[key], term)))
        for term in terms
        if any(has(WARDROBE_PALETTES[key], term) for key in keys)
    ]


def table(title, rows):
    lines = [f"## {title}", "", "| Term | Unique outfits | Slots |",
             "| --- | ---: | ---: |"]
    lines.extend(f"| {term} | {unique} | {slots} |" for term, unique, slots in rows)
    return lines


def report():
    slots = Counter(style["wardrobe"] for _, _, _, style in PRODUCTION_EXPANSION)
    keys = list(dict.fromkeys(style["wardrobe"] for _, _, _, style in PRODUCTION_EXPANSION))
    assert len(PRODUCTION_EXPANSION) == 500
    assert len(keys) == len(WARDROBE_PALETTES) == len(WARDROBE_STYLE_GROUPS) == 230
    assert set(keys) == set(WARDROBE_PALETTES) == set(WARDROBE_STYLE_GROUPS)
    assert len(set(WARDROBE_PALETTES.values())) == 230
    assert set(WARDROBE_STYLE_GROUPS.values()) == set(STYLE_TARGETS)
    assert sum(CHANGE_SUMMARY.values()) == 230
    assert not any(re.search(r"\b(?:cape|hat|beanie|baseball cap)\b", phrase, re.I)
                   for phrase in WARDROBE_PALETTES.values())
    assert all(TIGHTNESS_DIRECTION in phrase for phrase in WARDROBE_PALETTES.values())
    assert not any(re.search(r"\b(?:loose|oversized|boxy)\b", phrase, re.I)
                   for phrase in WARDROBE_PALETTES.values())

    style_counts = Counter()
    style_unique = Counter(WARDROBE_STYLE_GROUPS.values())
    for key, count in slots.items():
        style_counts[WARDROBE_STYLE_GROUPS[key]] += count
    assert style_counts == Counter(STYLE_TARGETS)

    warnings = [
        (key, phrase, [term for term in WARNING_TERMS if has(phrase, term)])
        for key, phrase in WARDROBE_PALETTES.items()
        if any(has(phrase, term) for term in WARNING_TERMS)
    ]
    lines = [
        "# Current Cover Story wardrobe rotation",
        "",
        "Generated from `PRODUCTION_EXPANSION`, `WARDROBE_PALETTES`, and "
        "`WARDROBE_STYLE_GROUPS`.",
        "",
        "- Performer slots: 500",
        "- Unique curated outfits: 230",
        f"- Previous diversification baseline: {CHANGE_SUMMARY['retained']} retained, "
        f"{CHANGE_SUMMARY['rewritten']} rewritten, {CHANGE_SUMMARY['replaced']} replaced",
        f"- Silhouette direction: `{TIGHTNESS_DIRECTION}` applied to all 230 outfits",
        f"- Lower-body warnings: {len(warnings)}",
        "",
        "The current pass restores visible fitted-dress silhouettes while keeping every "
        "description useful in a waist-up crop.",
        "",
        "## Style distribution",
        "",
        "| Style group | Unique outfits | Slots | Share |",
        "| --- | ---: | ---: | ---: |",
    ]
    for group, target in STYLE_TARGETS.items():
        lines.append(
            f"| {group} | {style_unique[group]} | {style_counts[group]} | "
            f"{style_counts[group] / 5:.1f}% |"
        )
    for title, terms in (
        ("Major color frequency", COLORS),
        ("Garment-family frequency", GARMENTS),
        ("Neckline and construction frequency", NECKLINES),
        ("Fabric and texture frequency", FABRICS),
    ):
        rows = sorted(frequencies(terms, keys, slots), key=lambda row: (-row[2], row[0]))
        lines.extend(("", *table(title, rows)))
    lines.extend(("", "## Lower-body warnings", ""))
    if warnings:
        lines.extend(
            f"- `{key}`: {phrase} — {', '.join(terms)}"
            for key, phrase, terms in warnings
        )
    else:
        lines.append("- None.")
    lines.extend((
        "",
        "## Curated rotation",
        "",
        "| # | Slots | Style group | Curated prompt phrase |",
        "| ---: | ---: | --- | --- |",
    ))
    lines.extend(
        f"| {number} | {slots[key]} | {WARDROBE_STYLE_GROUPS[key]} | "
        f"{WARDROBE_PALETTES[key]} |"
        for number, key in enumerate(keys, 1)
    )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--write", action="store_true", help="regenerate the Markdown report")
    action.add_argument("--check", action="store_true", help="verify the report is current")
    args = parser.parse_args()
    text = report()
    if args.write:
        atomic_text(REPORT, text)
    elif args.check:
        if not REPORT.is_file() or REPORT.read_text() != text:
            raise SystemExit(f"{REPORT} is stale; run {Path(__file__).name} --write")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
