#!/usr/bin/env python3
"""Self-checks for build_hub.py against file:// fixture sources (no network)."""

import hashlib
import tempfile
from pathlib import Path

import build_hub


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_source(folder: Path, entries: list[tuple[str, str, bytes]], path_style: str = "relative") -> str:
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    for pid, version, data in entries:
        (folder / f"{pid}.zip").write_bytes(data)
        path = f"{pid}.zip" if path_style == "relative" else (folder / f"{pid}.zip").as_uri()
        lines += [
            f"- id: {pid}",
            f"  name: {pid.title()}",
            f"  version: {version}",
            "  date: 2026-11-09 20:08:26",
            f"  path: {path}",
            f"  sha256: {sha(data)}",
            "  metadata:",
            '    description: "Has: a colon"',
        ]
    (folder / "index.yml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return (folder / "index.yml").as_uri()


def local_site(site: Path) -> None:
    main = site / "main"
    main.mkdir(parents=True)
    (main / "local.zip").write_bytes(b"local")
    (main / "index.yml").write_text(
        "- id: local\n  name: Local\n  version: 1.0.0-abc\n  date: 2026-07-15 04:52:22\n"
        f"  path: local.zip\n  sha256: {sha(b'local')}\n\n",
        encoding="utf-8",
    )


def test_resolve_matches_stash():
    base = "https://example.com/foo/packages.yaml"
    assert build_hub.resolve(base, "bar") == "https://example.com/foo/bar"
    assert build_hub.resolve(base, "/bar") == "https://example.com/bar"
    assert build_hub.resolve(base, "http://example.org/bar") == "http://example.org/bar"


def test_mirrors_into_versioned_paths_and_dev_gets_local_plugins():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        url = write_source(
            tmp / "src", [("alpha", "0.58.1", b"A"), ("beta", "1.0.1+dev.2.f00", b"B"), ("skip", "1", b"S")]
        )
        absolute = write_source(tmp / "abs", [("gamma", "2.0.0", b"G")], path_style="absolute")
        site = tmp / "site"
        local_site(site)
        config = {"channels": {
            "main": [{"index": url, "packages": ["alpha", "beta"]}, {"index": absolute}],
            "dev": [{"index": url, "packages": ["alpha"]}],
        }}
        written = build_hub.build(site, config, None)
        main = build_hub.load_index(written["main"])
        assert [e["id"] for e in main] == ["local", "alpha", "beta", "gamma"]
        alpha = main[1]
        assert alpha["path"] == "alpha/0.58.1/alpha.zip"
        assert (site / "main" / alpha["path"]).read_bytes() == b"A"
        assert main[2]["path"] == "beta/1.0.1+dev.2.f00/beta.zip"
        assert alpha["date"] == "2026-11-09 20:08:26"  # kept as a string
        assert alpha["metadata"]["description"] == "Has: a colon"
        dev = build_hub.load_index(written["dev"])
        assert [e["id"] for e in dev] == ["local", "alpha"]
        assert (site / "dev" / "local.zip").read_bytes() == b"local"
        # Written index files parse back to what build() returned.
        assert build_hub.load_index((site / "main" / "index.yml").read_text("utf-8")) == main


def test_rejects_checksum_mismatch_missing_and_duplicate_packages():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        url = write_source(tmp / "src", [("alpha", "1", b"A")])
        (tmp / "src" / "alpha.zip").write_bytes(b"tampered")
        for config, message in (
            ({"channels": {"main": [{"index": url}]}}, "does not match"),
            ({"channels": {"main": [{"index": url, "packages": ["nope"]}]}}, "has no package nope"),
        ):
            site = tmp / f"site-{message[:4]}"
            local_site(site)
            try:
                build_hub.build(site, config, None)
            except build_hub.HubError as error:
                assert message in str(error), error
            else:
                raise AssertionError(f"expected HubError: {message}")
        clash = write_source(tmp / "clash", [("local", "9", b"L")])
        site = tmp / "site-clash"
        local_site(site)
        try:
            build_hub.build(site, {"channels": {"main": [{"index": clash}]}}, None)
        except build_hub.HubError as error:
            assert "listed twice" in str(error)
        else:
            raise AssertionError("expected duplicate id error")


def test_carries_over_the_previous_live_archive_only():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        live = tmp / "live" / "main"
        (live / "alpha" / "0.1.0").mkdir(parents=True)
        (live / "alpha" / "0.1.0" / "alpha.zip").write_bytes(b"old")
        (live / "local.zip").write_bytes(b"local")
        (live / "index.yml").write_text(
            "- id: alpha\n  name: Alpha\n  version: 0.1.0\n  date: 2026-01-01 00:00:00\n"
            f"  path: alpha/0.1.0/alpha.zip\n  sha256: {sha(b'old')}\n"
            "- id: alpha\n  name: Alpha\n  version: 0.0.9\n  date: 2026-01-01 00:00:00\n"
            f"  path: ../../escape/x/a.zip\n  sha256: {sha(b'x')}\n",
            encoding="utf-8",
        )
        url = write_source(tmp / "src", [("alpha", "0.2.0", b"new")])
        site = tmp / "site"
        local_site(site)
        written = build_hub.build(site, {"channels": {"main": [{"index": url}]}}, (tmp / "live").as_uri())
        assert (site / "main" / "alpha" / "0.1.0" / "alpha.zip").read_bytes() == b"old"
        assert (site / "main" / "alpha" / "0.2.0" / "alpha.zip").read_bytes() == b"new"
        assert "0.1.0" not in written["main"]  # kept on disk, not listed
        assert not (site / "escape.zip").exists()
        assert build_hub.changed_channels(written, (tmp / "live").as_uri()) == ["main"]
        (live / "index.yml").write_text(written["main"], encoding="utf-8")
        assert build_hub.changed_channels(written, (tmp / "live").as_uri()) == []
        reordered = build_hub.dump_index(list(reversed(build_hub.load_index(written["main"]))))
        (live / "index.yml").write_text(reordered, encoding="utf-8")
        assert build_hub.changed_channels(written, (tmp / "live").as_uri()) == []


def test_unchanged_local_plugin_keeps_the_live_archive():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        live = tmp / "live" / "main"
        live.mkdir(parents=True)
        (live / "local.zip").write_bytes(b"published")
        (live / "index.yml").write_text(
            "- id: local\n  name: Local\n  version: 1.0.0-abc\n  date: 2026-07-15 04:52:22\n"
            f"  path: local.zip\n  sha256: {sha(b'published')}\n",
            encoding="utf-8",
        )
        site = tmp / "site"
        local_site(site)  # same version, rebuilt bytes b"local"
        written = build_hub.build(site, {"channels": {"main": [], "dev": []}}, (tmp / "live").as_uri())
        assert (site / "main" / "local.zip").read_bytes() == b"published"
        assert (site / "dev" / "local.zip").read_bytes() == b"published"
        assert build_hub.load_index(written["main"])[0]["sha256"] == sha(b"published")
        # A new commit (new version) is published as built.
        site2 = tmp / "site2"
        local_site(site2)
        index = site2 / "main" / "index.yml"
        index.write_text(index.read_text("utf-8").replace("1.0.0-abc", "1.0.0-def"), encoding="utf-8")
        written = build_hub.build(site2, {"channels": {"main": []}}, (tmp / "live").as_uri())
        assert build_hub.load_index(written["main"])[0]["sha256"] == sha(b"local")


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
