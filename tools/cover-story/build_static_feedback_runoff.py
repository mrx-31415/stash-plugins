#!/usr/bin/env python3
"""Build reference-only pairwise runoffs for the final ambiguous performers."""

import argparse
import json
from pathlib import Path

from export_personas import atomic_text, sha256
from run_static_feedback_ab import reference


MATCHES = {
    196: ((1, 2), (1, 3), (2, 3)),
    239: ((1, 2),),
    266: ((1, 2), (1, 3), (2, 3)),
}
FINALISTS = {196: 2, 239: 1, 266: 1}


def self_test():
    assert sum(map(len, MATCHES.values())) == 7
    assert all(left < right for matches in MATCHES.values() for left, right in matches)
    assert FINALISTS == {196: 2, 239: 1, 266: 1}
    print("static feedback runoff builder self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(
            "/mnt/Misc/sd/cover-story/static-performer-feedback-closeout-ab-v1/manifest.json"
        ),
    )
    parser.add_argument(
        "--original-source",
        type=Path,
        default=Path(
            "/mnt/Misc/sd/cover-story/static-performer-production-blur-q60-v3/manifest.json"
        ),
    )
    parser.add_argument("--finalists", action="store_true")
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

    source = {
        (entry["slot"], entry["candidate"]): entry
        for entry in json.loads(args.source.read_text())["entries"]
        if entry["arm"] == "B"
    }
    if args.finalists:
        originals = {
            entry["slot"]: entry
            for entry in json.loads(args.original_source.read_text())["entries"]
        }
        entries = []
        raw_dir = args.output_dir / "raw"
        for slot, candidate in FINALISTS.items():
            for arm, original in (
                ("A", originals[slot]),
                ("B", source[slot, candidate]),
            ):
                output = raw_dir / f"performer-{slot:03d}-final_{arm}.png"
                reference(Path(original["raw"]), output)
                entries.append({
                    **original,
                    "id": output.stem,
                    "stem": output.stem,
                    "arm": arm,
                    "candidate": 0 if arm == "A" else candidate,
                    "match": "final",
                    "raw": str(output),
                    "source_sha256": sha256(output),
                    "bytes": output.stat().st_size,
                })
        atomic_text(manifest_path, json.dumps({
            "version": 1,
            "source": str(args.source),
            "original_source": str(args.original_source),
            "finalists": FINALISTS,
            "entries": entries,
        }, indent=2) + "\n")
        print(f"manifest has {len(entries)}/6 finalist images")
        return
    entries = []
    raw_dir = args.output_dir / "raw"
    for slot, matches in MATCHES.items():
        for left, right in matches:
            match = f"{left}v{right}"
            for arm, candidate in (("A", left), ("B", right)):
                original = source[slot, candidate]
                output = raw_dir / f"performer-{slot:03d}-{match}_{arm}.png"
                reference(Path(original["raw"]), output)
                entries.append({
                    **original,
                    "id": output.stem,
                    "stem": output.stem,
                    "arm": arm,
                    "candidate": candidate,
                    "match": match,
                    "raw": str(output),
                    "source_sha256": sha256(output),
                    "bytes": output.stat().st_size,
                })
    atomic_text(manifest_path, json.dumps({
        "version": 1,
        "source": str(args.source),
        "matches": {str(slot): [list(match) for match in matches]
                    for slot, matches in MATCHES.items()},
        "entries": entries,
    }, indent=2) + "\n")
    print(f"manifest has {len(entries)}/14 images")


if __name__ == "__main__":
    main()
