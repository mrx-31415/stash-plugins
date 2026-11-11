#!/usr/bin/env python3
"""Add plugins published by other repositories to this package source.

build_site.sh packages the plugins in this repository into SITE/main. This
script then mirrors the packages listed in sources.json from other package
indexes into each channel (SITE/main, SITE/dev, ...), so one source URL
offers every plugin:

- each listed index is fetched, and each wanted entry's archive is
  downloaded (its path resolved against the index URL, as Stash does) and
  checked against the entry's sha256;
- the archive is stored under <channel>/<id>/<version>/ and the entry's path
  rewritten to it, so a published archive never changes under the same URL;
- the archive the live source currently lists for a mirrored package is
  carried over for one more deploy, so a client holding a cached index can
  still download what it names.

Channels other than main also get this repository's own plugins.

usage: build_hub.py SITE_DIR [--config sources.json] [--live BASE_URL]
       [--changed-output FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

USER_AGENT = "stash-plugins-hub"
TIMEOUT = 120


class HubError(Exception):
    pass


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()
    except (urllib.error.URLError, OSError) as error:
        raise HubError(f"cannot fetch {url}: {error}") from error


def load_index(text: str) -> list[dict]:
    """Parse a package index, keeping every scalar a string (dates included)."""
    data = yaml.load(text, Loader=yaml.BaseLoader) or []
    if not isinstance(data, list) or not all(isinstance(e, dict) and "id" in e for e in data):
        raise HubError("not a package index (expected a list of entries with an id)")
    return data


def dump_index(entries: list[dict]) -> str:
    if not entries:
        return ""
    return yaml.safe_dump(entries, sort_keys=False, allow_unicode=True, width=10_000)


def resolve(index_url: str, path: str) -> str:
    """Where Stash downloads an entry's archive from (pkg/pkg resolvePath)."""
    parsed = urllib.parse.urlparse(path)
    if parsed.scheme and (parsed.scheme == "file" or parsed.netloc):
        return path
    return urllib.parse.urljoin(index_url, path)


def safe_part(value: str) -> str:
    part = re.sub(r"[^A-Za-z0-9._+-]", "_", value).strip(".")
    if not part:
        raise HubError(f"unusable path part {value!r}")
    return part


def mirror(source: dict, channel_dir: Path) -> list[dict]:
    """Download the wanted entries of one source index into channel_dir."""
    index_url = source["index"]
    wanted = source.get("packages")
    entries = load_index(fetch(index_url).decode("utf-8"))
    by_id = {entry["id"]: entry for entry in entries}
    if wanted is None:
        wanted = list(by_id)
    missing = [pid for pid in wanted if pid not in by_id]
    if missing:
        raise HubError(f"{index_url} has no package {', '.join(missing)}")
    mirrored = []
    for pid in wanted:
        entry = dict(by_id[pid])
        for key in ("path", "sha256", "version", "date"):
            if not entry.get(key):
                raise HubError(f"{index_url}: {pid} has no {key}")
        url = resolve(index_url, entry["path"])
        data = fetch(url)
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry["sha256"].lower():
            raise HubError(f"{url}: sha256 {digest} does not match the index ({entry['sha256']})")
        name = safe_part(Path(urllib.parse.urlparse(url).path).name)
        relative = f"{safe_part(pid)}/{safe_part(entry['version'])}/{name}"
        target = channel_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        entry["path"] = relative
        mirrored.append(entry)
    return mirrored


def carry_over(live_index_url: str, channel_dir: Path, mirrored_ids: set[str]) -> list[str]:
    """Keep the archives the live index names for mirrored packages, unlisted."""
    try:
        live = load_index(fetch(live_index_url).decode("utf-8"))
    except HubError as error:
        print(f"warning: no live index to carry over ({error})", file=sys.stderr)
        return []
    kept = []
    for entry in live:
        path = entry.get("path", "")
        if entry.get("id") not in mirrored_ids or (channel_dir / path).exists():
            continue
        # Only versioned paths this script wrote; never escape the channel.
        parts = Path(path).parts
        if len(parts) != 3 or any(part in ("", ".", "..") for part in parts) or Path(path).is_absolute():
            continue
        try:
            data = fetch(resolve(live_index_url, path))
        except HubError as error:
            print(f"warning: could not carry over {path} ({error})", file=sys.stderr)
            continue
        if hashlib.sha256(data).hexdigest() != entry.get("sha256", "").lower():
            print(f"warning: live {path} does not match its sha256; not kept", file=sys.stderr)
            continue
        target = channel_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        kept.append(path)
    return kept


def reuse_live(local_entries: list[dict], main_dir: Path, live_index_url: str) -> list[str]:
    """Swap freshly built archives of unchanged local plugins for the live ones.

    build_site.sh zips depend on file times, so a rebuild of the same commit
    gets a new sha256. A plugin's version names its last commit, so the same
    version means the same files: serve the live archive and keep its sha256,
    rather than replace a published archive under the same name."""
    try:
        live = {e.get("id"): e for e in load_index(fetch(live_index_url).decode("utf-8"))}
    except HubError as error:
        print(f"warning: no live index to reuse archives from ({error})", file=sys.stderr)
        return []
    reused = []
    for entry in local_entries:
        old = live.get(entry["id"])
        if not old or old.get("version") != entry["version"] or old.get("path") != entry["path"]:
            continue
        try:
            data = fetch(resolve(live_index_url, old["path"]))
        except HubError as error:
            print(f"warning: rebuilt {entry['id']} ({error})", file=sys.stderr)
            continue
        if hashlib.sha256(data).hexdigest() != old.get("sha256", "").lower():
            continue
        (main_dir / entry["path"]).write_bytes(data)
        entry["sha256"] = old["sha256"]
        entry["date"] = old.get("date", entry["date"])
        reused.append(entry["id"])
    return reused


def build(site: Path, config: dict, live_base: str | None) -> dict[str, str]:
    """Write every channel; returns {channel: index text}."""
    main_dir = site / "main"
    local_index = main_dir / "index.yml"
    local_entries = load_index(local_index.read_text("utf-8")) if local_index.is_file() else []
    if live_base and local_entries:
        reuse_live(local_entries, main_dir, f"{live_base.rstrip('/')}/main/index.yml")
    local_ids = {entry["id"] for entry in local_entries}
    written = {}
    for channel, sources in config["channels"].items():
        channel_dir = site / safe_part(channel)
        if channel_dir != main_dir:
            if channel_dir.exists():
                shutil.rmtree(channel_dir)
            channel_dir.mkdir(parents=True)
            for entry in local_entries:
                shutil.copy2(main_dir / entry["path"], channel_dir / entry["path"])
        entries = [dict(entry) for entry in local_entries]
        seen = set(local_ids)
        for source in sources:
            for entry in mirror(source, channel_dir):
                if entry["id"] in seen:
                    raise HubError(f"{channel}: package {entry['id']} is listed twice")
                seen.add(entry["id"])
                entries.append(entry)
        if live_base:
            mirrored_ids = seen - local_ids
            live_url = f"{live_base.rstrip('/')}/{channel}/index.yml"
            for path in carry_over(live_url, channel_dir, mirrored_ids):
                print(f"{channel}: kept previous {path}", file=sys.stderr)
        text = dump_index(entries)
        (channel_dir / "index.yml").write_text(text, encoding="utf-8")
        written[channel] = text
    return written


def changed_channels(written: dict[str, str], live_base: str) -> list[str]:
    changed = []
    for channel, text in written.items():
        try:
            live = load_index(fetch(f"{live_base.rstrip('/')}/{channel}/index.yml").decode("utf-8"))
        except HubError:
            changed.append(channel)
            continue
        def by_id(entries: list[dict]) -> list[dict]:
            return sorted(entries, key=lambda entry: entry["id"])

        # Entry order follows the build's directory walk; it is not a change.
        if by_id(live) != by_id(load_index(text)):
            changed.append(channel)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("site", type=Path)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("sources.json"))
    parser.add_argument("--live", help="base URL of the published source, e.g. https://<user>.github.io/<repo>")
    parser.add_argument("--changed-output", type=Path, help="append changed=true|false (GitHub step output)")
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text("utf-8"))
    try:
        written = build(args.site, config, args.live)
        changed = changed_channels(written, args.live) if args.live else list(written)
    except HubError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for channel, text in written.items():
        count = len(load_index(text))
        mark = " (changed)" if channel in changed else ""
        print(f"{channel}: {count} packages{mark}")
    if args.changed_output:
        with args.changed_output.open("a", encoding="utf-8") as output:
            output.write(f"changed={'true' if changed else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
