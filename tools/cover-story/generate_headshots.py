#!/usr/bin/env python3
"""Generate deterministic Cover Story headshot candidates."""

import argparse
import json
import tempfile
from pathlib import Path

from comfy import prepare, run


CAST = {
    "actor-01": {
        "name": "Maya Vale",
        "age": 32,
        "appearance": "medium-brown skin, large expressive brown eyes, defined cheekbones, and full natural lips",
        "hair": "Dark naturally curly shoulder-length hair",
        "figure": "a shapely curvy feminine figure",
        "wardrobe": "a casual fitted ribbed knit top in muted terracotta with a tasteful crew neckline",
    },
    "actor-02": {
        "name": "Lena Hart",
        "age": 26,
        "appearance": "fair freckled skin, bright green eyes, high cheekbones, and full natural lips",
        "hair": "Long copper-red hair in loose waves",
        "figure": "a shapely athletic feminine figure",
        "wardrobe": "a casual fitted sage-green jersey top with a modest scoop neckline",
    },
    "actor-03": {
        "name": "Amara Reed",
        "age": 38,
        "appearance": "deep brown skin, dark expressive eyes, sculpted cheekbones, and full natural lips",
        "hair": "Thick black coily hair falling just past her shoulders",
        "figure": "a shapely full-figured feminine silhouette",
        "wardrobe": "a casual fitted cobalt-blue knit top with a tasteful square neckline",
    },
    "actor-04": {
        "name": "Sofia Marlow",
        "age": 45,
        "appearance": "warm olive skin, hazel eyes, elegant cheekbones, and full natural lips",
        "hair": "Glossy dark-brown hair in a softly layered shoulder-length cut",
        "figure": "a shapely curvy feminine figure",
        "wardrobe": "a casual fitted burgundy long-sleeve top with a soft boat neckline",
    },
    "actor-05": {
        "name": "Elise Arden",
        "age": 54,
        "appearance": "fair luminous skin with natural age detail, blue-gray eyes, defined cheekbones, and full natural lips",
        "hair": "Silver-blonde hair in polished shoulder-length waves",
        "figure": "a shapely elegant feminine figure",
        "wardrobe": "a casual fitted navy knit top with a modest V neckline",
    },
    "actor-06": {
        "name": "Priya West",
        "age": 29,
        "appearance": "warm brown South Asian skin, large dark eyes, refined cheekbones, and full natural lips",
        "hair": "Long glossy black hair in relaxed waves",
        "figure": "a shapely curvy feminine figure",
        "wardrobe": "a casual fitted plum-colored jersey top with a tasteful scoop neckline",
    },
    "actor-07": {
        "name": "Hana Mercer",
        "age": 41,
        "appearance": "light-medium East Asian skin, dark almond-shaped eyes, elegant cheekbones, and full natural lips",
        "hair": "Straight black hair in a sleek chin-length bob",
        "figure": "a shapely athletic feminine figure",
        "wardrobe": "a casual fitted forest-green short-sleeve knit top with a crew neckline",
    },
    "actor-08": {
        "name": "Freya Stone",
        "age": 50,
        "appearance": "pale skin with natural age detail, clear blue eyes, strong cheekbones, and full natural lips",
        "hair": "Thick platinum-blonde hair in a tousled shoulder-length cut",
        "figure": "a shapely curvy feminine figure",
        "wardrobe": "a casual fitted charcoal-gray ribbed top with a tasteful wide neckline",
    },
}

BASE_SEED = 2026072400


def prompt(actor):
    return (
        f"Relaxed cinematic screen-test portrait of {actor['name']}, a fictional "
        f"{actor['age']}-year-old adult leading actress. She is exceptionally beautiful, "
        f"glamorous, and highly photogenic, with harmonious feminine facial features, "
        f"{actor['appearance']}. {actor['hair']}. She has {actor['figure']}, shown in a "
        f"waist-up composition. She wears {actor['wardrobe']}; no blazer, button-down shirt, "
        "uniform, or businesswear. Warm confident expression, realistic skin texture, soft "
        "directional studio lighting, softly textured warm neutral background, eye-level "
        "85mm portrait lens, 2:3 cinematic editorial casting photography, fully clothed, "
        "workplace-safe, non-provocative pose, no text, logo, or watermark, and no resemblance "
        "to any real person or public figure."
    )


def approved_ids(path):
    if not path.exists():
        return set()
    return {asset["id"] for asset in json.loads(path.read_text()).get("assets", [])}


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, help="ComfyUI base URL")
    parser.add_argument("--actors", nargs="+", choices=CAST, default=list(CAST))
    parser.add_argument("--candidate", type=int, choices=range(1, 5), action="append")
    parser.add_argument("--workflow", type=Path, default=root / "workflows/krea2-text2img.json")
    parser.add_argument("--approved", type=Path, default=root / "approved.json")
    parser.add_argument("--include-approved", action="store_true")
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    candidates = args.candidate or [1, 2, 3, 4]
    skip = set() if args.include_approved else approved_ids(args.approved)
    jobs = [
        (actor_id, candidate)
        for actor_id in args.actors
        if actor_id not in skip
        for candidate in candidates
    ]
    if not jobs:
        print("nothing to generate")
        return

    workflow_text = args.workflow.read_text()
    for position, (actor_id, candidate) in enumerate(jobs, 1):
        actor = CAST[actor_id]
        seed = BASE_SEED + int(actor_id[-2:]) * 10 + candidate
        text = prompt(actor)
        print(f"[{position}/{len(jobs)}] {actor_id} — {actor['name']} — candidate {candidate} — seed {seed}")
        print(text)
        if args.dry_run:
            continue
        workflow = prepare(
            json.loads(workflow_text),
            text,
            seed,
            f"cover-story/candidates/{actor_id}/{actor_id}_",
        )
        with tempfile.TemporaryDirectory(prefix="cover-story-headshot-") as directory:
            result = run(args.server, workflow, Path(directory), args.timeout)
        remote = result["images"][0]["remote"]
        print(json.dumps({
            "prompt_id": result["prompt_id"],
            "filename": remote["filename"],
            "subfolder": remote.get("subfolder", ""),
            "type": remote.get("type", "output"),
        }))


if __name__ == "__main__":
    main()
