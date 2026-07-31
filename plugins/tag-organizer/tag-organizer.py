#!/usr/bin/env python3
"""Find and fill local tag gaps from linked Stash-box scenes."""

import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PLUGIN_ID = "tag-organizer"
PAGE_SIZE = 100
SCAN_PAGE_SIZE = 10
SCAN_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

CONFIG_QUERY = """
query Configuration {
  configuration {
    plugins(include: [\"tag-organizer\"])
    general { stashBoxes { name endpoint api_key } }
  }
}
"""
TAGS_QUERY = "query Tags { findTags(filter: { per_page: -1 }) { tags { id name aliases } } }"
TAG_SEARCH_QUERY = """
query Tags($filter: FindFilterType) {
  findTags(filter: $filter) { tags { id name aliases } }
}
"""
SCENES_QUERY = """
query Scenes($filter: FindFilterType, $scene_filter: SceneFilterType) {
  findScenes(filter: $filter, scene_filter: $scene_filter) {
    count
    scenes { id title tags { id } stash_ids { endpoint stash_id } }
  }
}
"""
SCENE_QUERY = """
query Scene($id: ID!) {
  findScene(id: $id) { id title tags { id } stash_ids { endpoint stash_id } }
}
"""
SCENES_BY_IDS_QUERY = """
query ScenesByIds($ids: [ID!]!) {
  findScenes(scene_ids: $ids) {
    scenes { id title tags { id } stash_ids { endpoint stash_id } }
  }
}
"""
REMOTE_TAGS_QUERY = """
query RemoteScene($id: ID!) { findScene(id: $id) { tags { name } } }
"""
CREATE_TAG_MUTATION = """
mutation CreateTag($input: TagCreateInput!) { tagCreate(input: $input) { id } }
"""
UPDATE_SCENE_MUTATION = """
mutation UpdateScene($input: SceneUpdateInput!) { sceneUpdate(input: $input) { id } }
"""
BULK_UPDATE_SCENES_MUTATION = """
mutation BulkUpdateScenes($input: BulkSceneUpdateInput!) {
  bulkSceneUpdate(input: $input) { id }
}
"""


def stash_log(level, message):
    print(f"\x01{level}\x02{message}", file=sys.stderr)


def stash_progress(current, total):
    stash_log("p", current / total if total else 1)


def valid_scan_token(token):
    return isinstance(token, str) and SCAN_TOKEN_RE.fullmatch(token) is not None


def scan_state_path(server, token):
    if not valid_scan_token(token):
        raise ValueError("invalid scan token")
    config_dir = server.get("Dir")
    if not config_dir:
        raise RuntimeError("Stash config directory is unavailable")
    return Path(config_dir) / "tag-organizer" / f"scan-{token}.json"


def write_scan_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(state, output, separators=(",", ":"))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_scan_state(server, token):
    path = scan_state_path(server, token)
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except FileNotFoundError:
        return None


def graphql(url, query, variables=None, headers=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    request = Request(url, data=body, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(str(error)) from error
    if result.get("errors"):
        raise RuntimeError(result["errors"][0]["message"])
    return result["data"]


def stash_url(server):
    host = server.get("Host", "localhost")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return f"{server.get('Scheme', 'http')}://{host}:{server.get('Port', 9999)}/graphql"


def stash_headers(server):
    headers = {}
    cookie = server.get("SessionCookie") or {}
    if cookie.get("Name") and cookie.get("Value"):
        headers["Cookie"] = f"{cookie['Name']}={cookie['Value']}"
    if server.get("ApiKey"):
        headers["ApiKey"] = server["ApiKey"]
    return headers


def tag_index(tags):
    index = {}
    for tag in tags:
        for name in [tag["name"], *(tag.get("aliases") or [])]:
            index.setdefault(name.casefold(), set()).add(tag["id"])
    return index


def find_local_tags(local_url, local_headers, names):
    found = {}
    for name in {name.casefold(): name for name in names if name.strip()}.values():
        matches = tag_index(
            graphql(
                local_url,
                TAG_SEARCH_QUERY,
                {"filter": {"q": name, "per_page": -1}},
                local_headers,
            )["findTags"]["tags"]
        )
        if name.casefold() in matches:
            for key, ids in matches.items():
                found.setdefault(key, set()).update(ids)
    return found


def merge_tag_index(target, source):
    for key, ids in source.items():
        target.setdefault(key, set()).update(ids)


def merge_tag_ids(existing_ids, remote_names, local_tags):
    additions = set()
    for name in remote_names:
        matched_ids = local_tags.get(name.casefold(), set())
        if len(matched_ids) == 1:
            additions.update(matched_ids)
    return set(existing_ids) | additions


def remote_tag_names(scene, providers, cache):
    names = []
    failures = []
    for stash_id in scene.get("stash_ids") or []:
        endpoint = stash_id.get("endpoint")
        provider = providers.get(endpoint)
        if not provider:
            continue
        cache_key = (endpoint, stash_id["stash_id"])
        try:
            if cache_key not in cache:
                data = graphql(
                    endpoint,
                    REMOTE_TAGS_QUERY,
                    {"id": stash_id["stash_id"]},
                    {"ApiKey": provider["api_key"]},
                )
                cache[cache_key] = [tag["name"] for tag in (data["findScene"] or {}).get("tags", [])]
        except RuntimeError as error:
            failures.append({"provider": endpoint, "error": str(error)})
            continue
        names.extend(cache[cache_key])
    return names, failures


def configured_providers(configuration):
    return {
        provider["endpoint"]: provider
        for provider in configuration["general"].get("stashBoxes", [])
        if provider.get("endpoint") and provider.get("api_key")
    }


def gap_rows(scenes, providers, cache):
    gaps = {}
    failures = []
    scanned = 0
    for scene in scenes:
        if not any(stash_id.get("endpoint") in providers for stash_id in scene.get("stash_ids") or []):
            continue
        scanned += 1
        names, scene_failures = remote_tag_names(scene, providers, cache)
        failures.extend({"scene_id": scene["id"], **failure} for failure in scene_failures)
        for name in {name.casefold(): name for name in names}.values():
            key = name.casefold()
            gap = gaps.setdefault(key, {"name": name, "scene_ids": set()})
            gap["scene_ids"].add(scene["id"])
    rows = [
        {"name": gap["name"], "scene_count": len(gap["scene_ids"]), "scene_ids": sorted(gap["scene_ids"])}
        for gap in gaps.values()
    ]
    rows.sort(key=lambda row: (-row["scene_count"], row["name"].casefold()))
    return rows, scanned, failures


def merge_gap_rows(gaps, rows):
    for row in rows:
        key = row["name"].casefold()
        gap = gaps.setdefault(key, {"name": row["name"], "scene_ids": set()})
        gap["scene_ids"].update(row["scene_ids"])


def finalized_gap_rows(gaps, local_tags):
    rows = [
        {
            "name": gap["name"],
            "scene_count": len(gap["scene_ids"]),
            "scene_ids": sorted(gap["scene_ids"]),
            "is_local": gap["name"].casefold() in local_tags,
        }
        for gap in gaps.values()
    ]
    rows.sort(key=lambda row: (-row["scene_count"], row["name"].casefold()))
    return rows


def scan_gaps(local_url, local_headers, provider, page):
    result = graphql(
        local_url,
        SCENES_QUERY,
        {
            "filter": {"page": page, "per_page": SCAN_PAGE_SIZE},
            "scene_filter": {
                "stash_ids_endpoint": {
                    "endpoint": provider["endpoint"],
                    "modifier": "NOT_NULL",
                }
            },
        },
        local_headers,
    )["findScenes"]
    rows, _, failures = gap_rows(
        result["scenes"],
        {provider["endpoint"]: provider},
        {},
    )
    for failure in failures:
        stash_log("e", f"Failed scene {failure['scene_id']} via {failure['provider']}: {failure['error']}")
    return {
        "scanned": len(result["scenes"]),
        "total": result["count"],
        "gaps": rows,
        "failure_count": len(failures),
    }


def scan_all(local_url, local_headers, provider, server, token):
    state_path = scan_state_path(server, token)
    state = {
        "scan_token": token,
        "provider": provider["endpoint"],
        "status": "running",
        "scanned": 0,
        "total": 0,
        "failure_count": 0,
        "rows": [],
        "error": None,
    }
    write_scan_state(state_path, state)
    gaps = {}
    cache = {}
    scanned = 0
    total = 0
    failures = 0
    page = 1
    try:
        local_tags = tag_index(graphql(local_url, TAGS_QUERY, headers=local_headers)["findTags"]["tags"])
        while True:
            result = graphql(
                local_url,
                SCENES_QUERY,
                {
                    "filter": {"page": page, "per_page": SCAN_PAGE_SIZE},
                    "scene_filter": {
                        "stash_ids_endpoint": {
                            "endpoint": provider["endpoint"],
                            "modifier": "NOT_NULL",
                        }
                    },
                },
                local_headers,
            )["findScenes"]
            scenes = result["scenes"]
            total = result["count"]
            rows, _, page_failures = gap_rows(
                scenes,
                {provider["endpoint"]: provider},
                cache,
            )
            for failure in page_failures:
                stash_log("e", f"Failed scene {failure['scene_id']} via {failure['provider']}: {failure['error']}")
            merge_gap_rows(gaps, rows)
            scanned += len(scenes)
            failures += len(page_failures)
            state.update(
                {
                    "scanned": scanned,
                    "total": total,
                    "failure_count": failures,
                    "rows": finalized_gap_rows(gaps, local_tags),
                }
            )
            write_scan_state(state_path, state)
            stash_progress(scanned, total)
            if not scenes or scanned >= total:
                break
            page += 1
        state["status"] = "completed"
        write_scan_state(state_path, state)
        return {
            "scan_token": token,
            "status": state["status"],
            "scanned": scanned,
            "total": total,
            "failure_count": failures,
            "row_count": len(state["rows"]),
        }
    except Exception as error:
        state.update({"status": "failed", "error": str(error)})
        write_scan_state(state_path, state)
        raise


def add_gap(local_url, local_headers, provider, name, scene_ids, local_tags):
    requested_ids = sorted({str(scene_id) for scene_id in scene_ids})
    if not name.strip() or not requested_ids:
        raise ValueError("tag name and scene IDs are required")

    scenes = graphql(
        local_url,
        SCENES_BY_IDS_QUERY,
        {"ids": requested_ids},
        local_headers,
    )["findScenes"]["scenes"]
    verified_ids = []
    failures = []
    cache = {}
    for scene in scenes:
        if not any(stash_id.get("endpoint") == provider["endpoint"] for stash_id in scene.get("stash_ids") or []):
            continue
        names, scene_failures = remote_tag_names(scene, {provider["endpoint"]: provider}, cache)
        failures.extend(scene_failures)
        if any(remote_name.casefold() == name.casefold() for remote_name in names):
            verified_ids.append(scene["id"])
    if not verified_ids:
        raise RuntimeError("the selected tag could not be verified on any linked scene")

    if name.casefold() not in local_tags:
        merge_tag_index(local_tags, find_local_tags(local_url, local_headers, [name]))
    matched_ids = local_tags.get(name.casefold(), set())
    if len(matched_ids) > 1:
        raise RuntimeError("the tag name now matches multiple local tags")
    created = not matched_ids
    if created:
        tag_id = graphql(
            local_url,
            CREATE_TAG_MUTATION,
            {"input": {"name": name}},
            local_headers,
        )["tagCreate"]["id"]
    else:
        tag_id = next(iter(matched_ids))

    error = None
    try:
        updated = graphql(
            local_url,
            BULK_UPDATE_SCENES_MUTATION,
            {"input": {"ids": verified_ids, "tag_ids": {"ids": [tag_id], "mode": "ADD"}}},
            local_headers,
        )["bulkSceneUpdate"]
        applied = len(updated or [])
    except RuntimeError as update_error:
        applied = 0
        error = str(update_error)
    return {
        "created": created,
        "applied": applied,
        "failed": len(requested_ids) - applied,
        "failure_count": len(failures),
        "error": error,
    }


def run_operation(args, configuration, local_url, local_headers, server):
    providers = configured_providers(configuration)
    mode = args.get("mode")
    if mode == "scan_status":
        token = args.get("scan_token")
        state = read_scan_state(server, token)
        if state is None:
            return {"scan_token": token, "status": "waiting", "scanned": 0, "total": 0, "row_count": 0, "rows": []}
        result = dict(state)
        result["row_count"] = len(state.get("rows") or [])
        if not args.get("include_rows"):
            result.pop("rows", None)
        return result
    if mode == "providers":
        local_tags = tag_index(graphql(local_url, TAGS_QUERY, headers=local_headers)["findTags"]["tags"])
        return {
            "providers": sorted(
                [{"name": provider.get("name") or endpoint, "endpoint": endpoint} for endpoint, provider in providers.items()],
                key=lambda provider: provider["name"].casefold(),
            ),
            "local_tag_names": sorted(local_tags),
        }
    if mode == "matches":
        names = args.get("names")
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise ValueError("tag names must be a list of strings")
        return {"local_tag_names": sorted(find_local_tags(local_url, local_headers, names))}

    provider = providers.get(args.get("provider"))
    if not provider:
        raise ValueError("select a configured metadata provider")
    if mode == "gaps":
        page = args.get("page", 1)
        if not isinstance(page, int) or page < 1:
            raise ValueError("page must be a positive integer")
        return scan_gaps(local_url, local_headers, provider, page)
    if mode == "scan":
        token = args.get("scan_token")
        if not valid_scan_token(token):
            raise ValueError("scan token must be 8-64 letters, numbers, underscores, or hyphens")
        return scan_all(local_url, local_headers, provider, server, token)
    if mode == "add":
        local_tags = tag_index(graphql(local_url, TAGS_QUERY, headers=local_headers)["findTags"]["tags"])
        return add_gap(
            local_url,
            local_headers,
            provider,
            args.get("name", ""),
            args.get("scene_ids") or [],
            local_tags,
        )
    raise ValueError("unknown operation")


def sync_scene(scene, local_url, local_headers, providers, local_tags, cache, checked_names):
    existing_ids = {tag["id"] for tag in scene.get("tags") or []}
    names, failures = remote_tag_names(scene, providers, cache)
    unchecked = {
        name.casefold(): name
        for name in names
        if name.casefold() not in local_tags and name.casefold() not in checked_names
    }
    checked_names.update(unchecked)
    merge_tag_index(local_tags, find_local_tags(local_url, local_headers, unchecked.values()))
    merged_ids = merge_tag_ids(existing_ids, names, local_tags)
    unknown_names = {name for name in names if len(local_tags.get(name.casefold(), set())) != 1}
    if merged_ids == existing_ids:
        return 0, failures, unknown_names
    graphql(local_url, UPDATE_SCENE_MUTATION, {"input": {"id": scene["id"], "tag_ids": sorted(merged_ids)}}, local_headers)
    return len(merged_ids - existing_ids), failures, unknown_names


def hook_target(payload, settings):
    context = payload.get("args", {}).get("hookContext")
    if not context:
        return "all"
    if context.get("type") == "Tag.Create.Post":
        return "all" if settings.get("syncOnTagCreate", False) else None
    if context.get("type") == "Scene.Update.Post":
        fields = context.get("inputFields") or []
        return context["id"] if settings.get("syncOnStashIdChange", False) and "stash_ids" in fields else None
    return None


def run(payload):
    server = payload["server_connection"]
    local_url = stash_url(server)
    local_headers = stash_headers(server)
    configuration = graphql(local_url, CONFIG_QUERY, headers=local_headers)["configuration"]
    args = payload.get("args") or {}
    if args.get("mode"):
        return run_operation(args, configuration, local_url, local_headers, server)

    target = hook_target(payload, configuration.get("plugins", {}).get(PLUGIN_ID, {}))
    if target is None:
        return {"skipped": True, "reason": "automatic sync is disabled or not applicable"}

    providers = configured_providers(configuration)
    local_tags = tag_index(graphql(local_url, TAGS_QUERY, headers=local_headers)["findTags"]["tags"])
    checked_names = set(local_tags)
    summary = {"scanned": 0, "changed": 0, "tags_added": 0, "unknown_remote_tags": [], "failures": []}
    cache = {}
    total = 0

    def process(scene):
        summary["scanned"] += 1
        progress = f"[{summary['scanned']}/{total}]"
        try:
            added, failures, unknown_names = sync_scene(
                scene, local_url, local_headers, providers, local_tags, cache, checked_names
            )
            summary["failures"].extend({"scene_id": scene["id"], **failure} for failure in failures)
            summary["unknown_remote_tags"] = sorted(set(summary["unknown_remote_tags"]) | unknown_names)
            if added:
                summary["changed"] += 1
                summary["tags_added"] += added
                stash_log("i", f"{progress} Updated scene {scene.get('title') or '(untitled)'} (ID {scene['id']}): added {added} tag(s)")
            else:
                stash_log("d", f"{progress} Checked scene {scene.get('title') or '(untitled)'} (ID {scene['id']}): no new matching tags")
        except RuntimeError as error:
            summary["failures"].append({"scene_id": scene["id"], "error": str(error)})
            stash_log("e", f"{progress} Failed scene {scene.get('title') or '(untitled)'} (ID {scene['id']}): {error}")
        stash_progress(summary["scanned"], total)

    if target == "all":
        page = 1
        while True:
            result = graphql(local_url, SCENES_QUERY, {"filter": {"page": page, "per_page": PAGE_SIZE}}, local_headers)["findScenes"]
            total = result["count"]
            stash_progress(summary["scanned"], total)
            for scene in result["scenes"]:
                process(scene)
            if page * PAGE_SIZE >= result["count"]:
                break
            page += 1
    else:
        total = 1
        scene = graphql(local_url, SCENE_QUERY, {"id": target}, local_headers)["findScene"]
        if scene:
            process(scene)
        else:
            stash_progress(1, 1)
    return summary


def self_test():
    global graphql

    index = tag_index([
        {"id": "1", "name": "Anal", "aliases": ["A"]},
        {"id": "2", "name": "BDSM", "aliases": []},
        {"id": "3", "name": "Ambiguous one", "aliases": ["shared"]},
        {"id": "4", "name": "Ambiguous two", "aliases": ["shared"]},
    ])
    assert merge_tag_ids({"9"}, ["anal", "A", "unknown", "shared"], index) == {"1", "9"}
    rows, scanned, failures = gap_rows(
        [
            {"id": "2", "stash_ids": [{"endpoint": "remote", "stash_id": "b"}]},
            {"id": "1", "stash_ids": [{"endpoint": "remote", "stash_id": "a"}]},
            {"id": "3", "stash_ids": [{"endpoint": "other", "stash_id": "c"}]},
        ],
        {"remote": {"endpoint": "remote", "api_key": "key"}},
        {
            ("remote", "a"): ["New", "new", "Anal", "Another"],
            ("remote", "b"): ["NEW"],
        },
    )
    assert rows == [
        {"name": "NEW", "scene_count": 2, "scene_ids": ["1", "2"]},
        {"name": "Anal", "scene_count": 1, "scene_ids": ["1"]},
        {"name": "Another", "scene_count": 1, "scene_ids": ["1"]},
    ]
    assert scanned == 2 and failures == []
    real_graphql = graphql
    calls = []

    def fake_graphql(url, query, variables=None, headers=None):
        calls.append((query, variables))
        if query == TAGS_QUERY:
            return {
                "findTags": {
                    "tags": [
                        {"id": "1", "name": "Anal", "aliases": ["A"]},
                        {"id": "12", "name": "Oral", "aliases": ["Blowjob"]},
                    ]
                }
            }
        if query == SCENES_QUERY:
            return {
                "findScenes": {
                    "count": 1,
                    "scenes": [{"id": "1", "stash_ids": [{"endpoint": "remote", "stash_id": "a"}]}],
                }
            }
        if query == SCENES_BY_IDS_QUERY:
            return {
                "findScenes": {
                    "scenes": [
                        {"id": "1", "stash_ids": [{"endpoint": "remote", "stash_id": "a"}]},
                        {"id": "2", "stash_ids": [{"endpoint": "other", "stash_id": "b"}]},
                    ]
                }
            }
        if query == REMOTE_TAGS_QUERY:
            return {"findScene": {"tags": [{"name": "New"}, {"name": "Anal"}, {"name": "Blowjob"}]}}
        if query == TAG_SEARCH_QUERY:
            if variables["filter"]["q"].casefold() == "blowjob":
                return {"findTags": {"tags": [{"id": "11", "name": "Oral", "aliases": ["Blowjob"]}]}}
            return {"findTags": {"tags": []}}
        if query == CREATE_TAG_MUTATION:
            return {"tagCreate": {"id": "10"}}
        if query == BULK_UPDATE_SCENES_MUTATION:
            return {"bulkSceneUpdate": [{"id": "1"}]}
        raise AssertionError(query)

    graphql = fake_graphql
    try:
        page_result = scan_gaps(
            "local",
            {},
            {"endpoint": "remote", "api_key": "key"},
            2,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            scan_result = scan_all(
                "local",
                {},
                {"endpoint": "remote", "api_key": "key"},
                {"Dir": state_dir},
                "scan-token",
            )
            scan_state = read_scan_state({"Dir": state_dir}, "scan-token")
        added = add_gap(
            "local",
            {},
            {"endpoint": "remote", "api_key": "key"},
            "New",
            ["1", "2"],
            index,
        )
        matched = find_local_tags("local", {}, ["Blowjob", "Missing"])
    finally:
        graphql = real_graphql
    assert page_result == {
        "scanned": 1,
        "total": 1,
        "gaps": [
            {"name": "Anal", "scene_count": 1, "scene_ids": ["1"]},
            {"name": "Blowjob", "scene_count": 1, "scene_ids": ["1"]},
            {"name": "New", "scene_count": 1, "scene_ids": ["1"]},
        ],
        "failure_count": 0,
    }
    assert scan_result == {
        "scan_token": "scan-token",
        "status": "completed",
        "scanned": 1,
        "total": 1,
        "failure_count": 0,
        "row_count": 3,
    }
    assert scan_state["status"] == "completed"
    assert scan_state["rows"] == [
        {"name": "Anal", "scene_count": 1, "scene_ids": ["1"], "is_local": True},
        {"name": "Blowjob", "scene_count": 1, "scene_ids": ["1"], "is_local": True},
        {"name": "New", "scene_count": 1, "scene_ids": ["1"], "is_local": False},
    ]
    assert calls[0][1] == {
        "filter": {"page": 2, "per_page": SCAN_PAGE_SIZE},
        "scene_filter": {
            "stash_ids_endpoint": {
                "endpoint": "remote",
                "modifier": "NOT_NULL",
            }
        },
    }
    assert added == {"created": True, "applied": 1, "failed": 1, "failure_count": 0, "error": None}
    assert matched == {"oral": {"11"}, "blowjob": {"11"}}
    bulk_call = next(call for call in calls if call[0] == BULK_UPDATE_SCENES_MUTATION)
    assert bulk_call[1] == {"input": {"ids": ["1"], "tag_ids": {"ids": ["10"], "mode": "ADD"}}}
    assert hook_target({"args": {}}, {}) == "all"
    assert hook_target({"args": {"hookContext": {"type": "Tag.Create.Post"}}}, {}) is None
    assert hook_target({"args": {"hookContext": {"type": "Scene.Update.Post", "id": "5", "inputFields": ["stash_ids"]}}}, {"syncOnStashIdChange": True}) == "5"
    print("self-check passed")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        try:
            print(json.dumps({"output": run(json.load(sys.stdin))}))
        except (KeyError, RuntimeError, ValueError) as error:
            print(json.dumps({"error": str(error)}))
            raise SystemExit(1)
