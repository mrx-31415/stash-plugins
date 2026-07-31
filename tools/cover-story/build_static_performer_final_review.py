#!/usr/bin/env python3
"""Assemble the reviewed Cover Story winners into one 500-image viewer group."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from export_personas import atomic_text, sha256
from run_static_feedback_ab import reference


ROUNDS = (
    "static-performer-feedback-styling-ab-v1",
    "static-performer-feedback-identity-ab-v1",
    "static-performer-feedback-final-ab-v1",
    "static-performer-feedback-closeout-ab-v1",
)
RUNOFF = "static-performer-feedback-runoff-ab-v1"
FINALISTS = "static-performer-feedback-final-candidates-ab-v1"


def manifest(root, name):
    return json.loads((root / name / "manifest.json").read_text())["entries"]


def outcome(left, right):
    statuses = left.get("status", ""), right.get("status", "")
    if statuses == ("keep", "reject"):
        return "A"
    if statuses == ("reject", "keep"):
        return "B"
    if statuses == ("maybe", "maybe"):
        return "tie"
    if statuses == ("reject", "reject"):
        return "neither"
    raise ValueError(f"unfinished pair statuses: {statuses}")


def reviewed_pairs(root, reviews, name):
    grouped = defaultdict(dict)
    for entry in manifest(root, name):
        match = entry.get("match")
        key = entry["slot"], None if match else entry.get("candidate"), match
        path = f"{name}/raw/{Path(entry['raw']).name}"
        if path not in reviews:
            raise ValueError(f"missing review: {path}")
        grouped[key][entry["arm"]] = (entry, reviews[path])
    results = []
    for key, sides in grouped.items():
        if set(sides) != {"A", "B"}:
            raise ValueError(f"incomplete pair: {name} {key}")
        result = outcome(sides["A"][1], sides["B"][1])
        winner = sides[result][0] if result in {"A", "B"} else None
        results.append((key, result, winner))
    return results


def apply_round(selected, provenance, pending, results, name):
    by_slot = defaultdict(list)
    for (slot, _, _), result, winner in results:
        by_slot[slot].append((result, winner))
    for slot, decisions in by_slot.items():
        b_winners = {
            winner["source_sha256"]: winner
            for result, winner in decisions if result == "B"
        }
        a_winners = {
            winner["source_sha256"]: winner
            for result, winner in decisions if result == "A"
        }
        if len(b_winners) == 1:
            selected[slot] = next(iter(b_winners.values()))
            provenance[slot] = name
            pending.discard(slot)
        elif not b_winners and len(a_winners) == 1:
            selected[slot] = next(iter(a_winners.values()))
            provenance[slot] = name
            pending.discard(slot)
        else:
            pending.add(slot)


def runoff_winners(root, reviews):
    wins = defaultdict(Counter)
    for (slot, _, _), result, winner in reviewed_pairs(root, reviews, RUNOFF):
        if result not in {"A", "B"}:
            raise ValueError(f"runoff must choose one side: performer-{slot:03d}")
        wins[slot][winner["candidate"]] += 1
    return {
        slot: counts.most_common(1)[0][0]
        for slot, counts in wins.items()
        if list(counts.values()).count(max(counts.values())) == 1
    }


def self_test():
    assert outcome({"status": "keep"}, {"status": "reject"}) == "A"
    assert outcome({"status": "reject"}, {"status": "keep"}) == "B"
    assert outcome({"status": "maybe"}, {"status": "maybe"}) == "tie"
    assert outcome({"status": "reject"}, {"status": "reject"}) == "neither"
    print("static performer final review builder self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--root", type=Path, default=Path("/mnt/Misc/sd/cover-story"))
    parser.add_argument(
        "--reviews",
        type=Path,
        default=Path("/mnt/Misc/sd/cover-story/reviews.json"),
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.output_dir:
        parser.error("--output-dir is required")
    manifest_path = args.output_dir / "manifest.json"
    if args.output_dir.exists() and any(args.output_dir.iterdir()) and not manifest_path.exists():
        raise ValueError(f"refusing nonempty output directory without manifest: {args.output_dir}")

    reviews = json.loads(args.reviews.read_text())["reviews"]
    production = manifest(
        args.root, "static-performer-production-blur-q60-v3"
    )
    selected = {entry["slot"]: entry for entry in production}
    provenance = dict.fromkeys(selected, "static-performer-production-blur-q60-v3")
    pending = set()
    for name in ROUNDS:
        apply_round(
            selected, provenance, pending,
            reviewed_pairs(args.root, reviews, name), name,
        )

    ranked = runoff_winners(args.root, reviews)
    finalists_manifest = json.loads((
        args.root / FINALISTS / "manifest.json"
    ).read_text())
    finalists = {
        entry["slot"]: entry
        for entry in finalists_manifest["entries"]
        if entry["arm"] == "B"
    }
    if ranked != {int(slot): candidate
                  for slot, candidate in finalists_manifest["finalists"].items()}:
        raise ValueError(f"runoff ranking changed: {ranked}")
    for slot, entry in finalists.items():
        selected[slot] = entry
        provenance[slot] = RUNOFF
        pending.discard(slot)

    if pending or set(selected) != set(range(1, 501)):
        raise ValueError(f"unresolved final selection: {sorted(pending)}")

    entries = []
    raw_dir = args.output_dir / "raw"
    for slot in range(1, 501):
        source = selected[slot]
        source_path = Path(source["raw"])
        if sha256(source_path) != source["source_sha256"]:
            raise ValueError(f"accepted source changed for performer-{slot:03d}")
        output = raw_dir / f"performer-{slot:03d}.png"
        reference(source_path, output)
        entries.append({
            **source,
            "slot": slot,
            "id": f"performer-{slot:03d}",
            "stem": f"performer-{slot:03d}",
            "raw": str(output),
            "accepted_source": str(source_path),
            "accepted_round": provenance[slot],
            "source_sha256": sha256(output),
            "bytes": output.stat().st_size,
        })
    atomic_text(manifest_path, json.dumps({
        "version": 1,
        "target_count": 500,
        "review_rounds": list(ROUNDS) + [RUNOFF],
        "entries": entries,
    }, indent=2) + "\n")
    print(f"manifest has {len(entries)}/500 accepted performers")


if __name__ == "__main__":
    main()
