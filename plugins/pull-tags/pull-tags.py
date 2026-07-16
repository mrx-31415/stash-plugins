#!/usr/bin/env python3
"""Add local tags that match tags returned by linked Stash-box scenes."""

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PLUGIN_ID = "pull-tags"
PAGE_SIZE = 100

CONFIG_QUERY = """
query Configuration {
  configuration {
    plugins(include: [\"pull-tags\"])
    general { stashBoxes { endpoint api_key } }
  }
}
"""
TAGS_QUERY = "query Tags { findTags { tags { id name aliases } } }"
SCENES_QUERY = """
query Scenes($filter: FindFilterType) {
  findScenes(filter: $filter) {
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
REMOTE_TAGS_QUERY = """
query RemoteScene($id: ID!) { findScene(id: $id) { tags { name } } }
"""
UPDATE_SCENE_MUTATION = """
mutation UpdateScene($input: SceneUpdateInput!) { sceneUpdate(input: $input) { id } }
"""


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


def sync_scene(scene, local_url, local_headers, providers, local_tags, cache):
    existing_ids = {tag["id"] for tag in scene.get("tags") or []}
    names, failures = remote_tag_names(scene, providers, cache)
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
    target = hook_target(payload, configuration.get("plugins", {}).get(PLUGIN_ID, {}))
    if target is None:
        return {"skipped": True, "reason": "automatic sync is disabled or not applicable"}

    providers = {
        provider["endpoint"]: provider
        for provider in configuration["general"].get("stashBoxes", [])
        if provider.get("endpoint") and provider.get("api_key")
    }
    local_tags = tag_index(graphql(local_url, TAGS_QUERY, headers=local_headers)["findTags"]["tags"])
    summary = {"scanned": 0, "changed": 0, "tags_added": 0, "unknown_remote_tags": [], "failures": []}
    cache = {}
    total = 0

    def process(scene):
        summary["scanned"] += 1
        progress = f"[{summary['scanned']}/{total}]"
        try:
            added, failures, unknown_names = sync_scene(scene, local_url, local_headers, providers, local_tags, cache)
            summary["failures"].extend({"scene_id": scene["id"], **failure} for failure in failures)
            summary["unknown_remote_tags"] = sorted(set(summary["unknown_remote_tags"]) | unknown_names)
            if added:
                summary["changed"] += 1
                summary["tags_added"] += added
                print(f"{progress} Updated scene {scene.get('title') or '(untitled)'} (ID {scene['id']}): added {added} tag(s)", file=sys.stderr)
            else:
                print(f"{progress} Checked scene {scene.get('title') or '(untitled)'} (ID {scene['id']}): no new matching tags", file=sys.stderr)
        except RuntimeError as error:
            summary["failures"].append({"scene_id": scene["id"], "error": str(error)})
            print(f"{progress} Failed scene {scene.get('title') or '(untitled)'} (ID {scene['id']}): {error}", file=sys.stderr)

    if target == "all":
        page = 1
        while True:
            result = graphql(local_url, SCENES_QUERY, {"filter": {"page": page, "per_page": PAGE_SIZE}}, local_headers)["findScenes"]
            total = result["count"]
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
    return summary


def self_test():
    index = tag_index([
        {"id": "1", "name": "Anal", "aliases": ["A"]},
        {"id": "2", "name": "BDSM", "aliases": []},
        {"id": "3", "name": "Ambiguous one", "aliases": ["shared"]},
        {"id": "4", "name": "Ambiguous two", "aliases": ["shared"]},
    ])
    assert merge_tag_ids({"9"}, ["anal", "A", "unknown", "shared"], index) == {"1", "9"}
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
