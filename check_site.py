#!/usr/bin/env python3
"""Check a built plugin source before it is published.

    check_site.py SITE [--config sources.json]

Every channel in sources.json must have SITE/<channel>/index.yml listing each
package sources.json names plus the in-repo plugins, each archive present
under the channel with the sha256 its entry names, and every entry carrying
the fields Stash needs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import build_hub

REQUIRED = ("id", "name", "version", "date", "path", "sha256")


def local_plugins() -> set[str]:
    """The in-repo plugin ids: build_site.sh packages every plugins/**/<id>.yml."""
    return {path.stem for path in Path(__file__).with_name("plugins").rglob("*.yml")}


def check(site: Path, config: dict, local: set[str]) -> list[str]:
    problems = []
    for channel, sources in config["channels"].items():
        index = site / channel / "index.yml"
        if not index.is_file():
            problems.append(f"{channel}: no index.yml")
            continue
        entries = build_hub.load_index(index.read_text("utf-8"))
        ids = [entry.get("id") for entry in entries]
        wanted = set(local) | {p for source in sources for p in source.get("packages", [])}
        if missing := wanted - set(ids):
            problems.append(f"{channel}: missing {', '.join(sorted(missing))}")
        if len(ids) != len(set(ids)):
            problems.append(f"{channel}: duplicate ids")
        for entry in entries:
            name = f"{channel}/{entry.get('id')}"
            if absent := [key for key in REQUIRED if not entry.get(key)]:
                problems.append(f"{name}: no {', '.join(absent)}")
                continue
            archive = (site / channel / entry["path"]).resolve()
            if not archive.is_relative_to((site / channel).resolve()) or not archive.is_file():
                problems.append(f"{name}: archive {entry['path']} is not in the site")
            elif hashlib.sha256(archive.read_bytes()).hexdigest() != entry["sha256"]:
                problems.append(f"{name}: {entry['path']} does not match its sha256")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("site", type=Path)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("sources.json"))
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text("utf-8"))
    problems = check(args.site, config, local_plugins())
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if not problems:
        print(f"ok: {', '.join(config['channels'])}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
