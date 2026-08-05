#!/usr/bin/env python3
"""Find and fill local tag gaps from linked Stash-box scenes."""

import json
import os
import difflib
from pathlib import Path
import re
import sys
import tempfile
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PLUGIN_ID = "tag-organizer"
PAGE_SIZE = 100
SCAN_PAGE_SIZE = 25
REMOTE_BATCH_SIZE = 25
CLEANUP_TAG_PAGE_SIZE = 25
LOCAL_BATCH_SIZE = 100
SCAN_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
FUZZY_CUTOFF = 0.72
DUPLICATE_FUZZY_CUTOFF = 0.85
DUPLICATE_SIMILARITY_FLOOR = 0.70

CONFIG_QUERY = """
query Configuration {
  configuration {
    plugins(include: [\"tag-organizer\"])
    general { stashBoxes { name endpoint api_key } }
  }
}
"""
TAGS_QUERY = "query Tags { findTags(filter: { per_page: -1 }) { tags { id name aliases } } }"
CLEANUP_TAGS_QUERY = """
query CleanupTags($filter: FindFilterType) { findTags(filter: $filter) { count tags {
  id name aliases stash_ids { endpoint stash_id }
  scene_count scene_marker_count image_count gallery_count performer_count studio_count group_count
  parents { id } children { id }
} } }
"""
CLEANUP_TAGS_BY_IDS_QUERY = """
query CleanupTagsByIds($ids: [ID!]) { findTags(ids: $ids) { tags {
  id name aliases stash_ids { endpoint stash_id }
  scene_count scene_marker_count image_count gallery_count performer_count studio_count group_count
  parents { id } children { id }
} } }
"""
TAG_EXISTS_QUERY = "query TagExists($id: ID!) { findTag(id: $id) { id } }"
SCENES_TAGS_FK_ARGS_RE = re.compile(r"\[\[(\d+)[,\s]+(\d+)\]\]")
STALE_JOB_GRACE_SECONDS = 300


TAG_SEARCH_QUERY = """
query Tags($filter: FindFilterType) {
  findTags(filter: $filter) { tags { id name aliases } }
}
"""
SCENES_QUERY = """
query Scenes($filter: FindFilterType, $scene_filter: SceneFilterType) {
  findScenes(filter: $filter, scene_filter: $scene_filter) {
    count
    scenes { id title paths { screenshot } tags { id } stash_ids { endpoint stash_id } }
  }
}
"""
SCENE_QUERY = """
query Scene($id: ID!) {
  findScene(id: $id) { id title paths { screenshot } tags { id } stash_ids { endpoint stash_id } }
}
"""
SCENES_BY_IDS_QUERY = """
query ScenesByIds($ids: [ID!]!) {
  findScenes(ids: $ids) {
    scenes { id title paths { screenshot } tags { id } stash_ids { endpoint stash_id } }
  }
}
"""
CLEANUP_SCENES_BY_TAGS_QUERY = """
query CleanupScenesByTags($filter: FindFilterType, $scene_filter: SceneFilterType) {
  findScenes(filter: $filter, scene_filter: $scene_filter) {
    count
    scenes { id tags { id } stash_ids { endpoint stash_id } }
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
TAG_UPDATE_MUTATION = """
mutation CleanupTagUpdate($input: TagUpdateInput!) { tagUpdate(input: $input) { id } }
"""
TAG_DESTROY_MUTATION = """
mutation CleanupTagDestroy($input: TagDestroyInput!) { tagDestroy(input: $input) }
"""
TAGS_MERGE_MUTATION = """
mutation CleanupTagsMerge($input: TagsMergeInput!) { tagsMerge(input: $input) { id } }
"""
TAG_CREATE_WITH_PARENT_MUTATION = """
mutation CleanupTagCreate($input: TagCreateInput!) { tagCreate(input: $input) { id } }
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


def pull_state_path(server):
    config_dir = server.get("Dir")
    if not config_dir:
        raise RuntimeError("Stash config directory is unavailable")
    return Path(config_dir) / "tag-organizer" / "pull.json"


def cleanup_state_path(server):
    config_dir = server.get("Dir")
    if not config_dir:
        raise RuntimeError("Stash config directory is unavailable")
    return Path(config_dir) / "tag-organizer" / "cleanup.json"


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


def write_pull_state(server, state):
    write_scan_state(pull_state_path(server), state)


def read_pull_state(server):
    path = pull_state_path(server)
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except FileNotFoundError:
        return None


def write_cleanup_state(server, state):
    write_scan_state(cleanup_state_path(server), state)


def read_cleanup_state(server, token=None):
    path = cleanup_state_path(server)
    try:
        with path.open(encoding="utf-8") as source:
            state = json.load(source)
            return state if token is None or state.get("cleanup_token") == token else None
    except FileNotFoundError:
        return None


def cleanup_review_state_path(server):
    config_dir = server.get("Dir")
    if not config_dir:
        raise RuntimeError("Stash config directory is unavailable")
    return Path(config_dir) / "tag-organizer" / "cleanup-review.json"


def write_cleanup_review_state(server, state):
    write_scan_state(cleanup_review_state_path(server), state)


def read_cleanup_review_state(server, token):
    path = cleanup_review_state_path(server)
    try:
        with path.open(encoding="utf-8") as source:
            state = json.load(source)
            return state if state.get("cleanup_token") == token else None
    except FileNotFoundError:
        return None


def tag_exists(local_url, local_headers, tag_id):
    """Return True if the local tag id still exists."""
    data = graphql(local_url, TAG_EXISTS_QUERY, {"id": str(tag_id)}, local_headers)
    return (data.get("findTag") or {}).get("id") is not None


def dead_tag_ids_from_error(message):
    """Extract the tag id from a scenes_tags foreign-key failure message.

    Stash reports these as e.g. ``... INSERT INTO scenes_tags ...` [[3467 253]]:
    FOREIGN KEY constraint failed`` where 253 is the tag that no longer exists.
    """
    match = SCENES_TAGS_FK_ARGS_RE.search(message or "")
    if not match:
        return set()
    return {match.group(2)}


def writer_alive(state, path, max_stale_seconds=STALE_JOB_GRACE_SECONDS):
    """Best-effort check whether the process that wrote `state` is still running.

    Relies on the recorded pid when available; falls back to how recently the
    state file was written (relevant for state files from older versions).
    """
    pid = state.get("pid")
    if pid is not None:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return False
        except OSError:
            pass  # cannot determine (e.g. permission); fall back to file age below
        else:
            return True
    try:
        return time.time() - path.stat().st_mtime <= max_stale_seconds
    except OSError:
        return True


def resolve_running_state(state, path):
    """Mark a persisted 'running' state as aborted when its writer is gone."""
    if state and state.get("status") == "running" and not writer_alive(state, path):
        resolved = dict(state)
        resolved["status"] = "aborted"
        resolved["error"] = "The job was stopped before it finished."
        return resolved
    return state


def cleanup_overview(state, token=None, duplicate_cutoff=DUPLICATE_FUZZY_CUTOFF):
    if state is None:
        return {
            "cleanup_token": token,
            "status": "waiting",
            "scanned": 0,
            "total": 0,
            "tag_count": 0,
            "duplicate_count": 0,
            "split_count": 0,
            "failure_count": 0,
            "progress_phase": "",
            "progress_detail": "",
            "error": None,
        }
    return {
        "cleanup_token": state.get("cleanup_token"),
        "status": state.get("status", "waiting"),
        "scanned": state.get("scanned", 0),
        "total": state.get("total", 0),
        "tag_count": len(state.get("tags") or []),
        "duplicate_count": len(cleanup_duplicate_groups(state, duplicate_cutoff)),
        "split_count": len(state.get("splits") or []),
        "failure_count": state.get("failure_count", len(state.get("failures") or [])),
        "progress_phase": state.get("progress_phase", ""),
        "progress_detail": state.get("progress_detail", ""),
        "error": state.get("error"),
    }


def cleanup_review(state, args, duplicate_cutoff=DUPLICATE_FUZZY_CUTOFF):
    section = args.get("section")
    if section not in {"tags", "duplicates", "splits"}:
        raise ValueError("section must be tags, duplicates, or splits")
    page = args.get("page", 1)
    per_page = args.get("per_page", 50)
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    if isinstance(per_page, bool) or not isinstance(per_page, int) or not 1 <= per_page <= 100:
        raise ValueError("per_page must be between 1 and 100")
    query = args.get("query", "")
    if not isinstance(query, str):
        raise ValueError("query must be text")
    query = query.casefold().strip()
    selected_ids = args.get("selected_ids") or []
    if not isinstance(selected_ids, list) or any(not isinstance(item, (str, int)) for item in selected_ids):
        raise ValueError("selected_ids must be a list")
    selected_ids = {str(item) for item in selected_ids}

    if section == "tags":
        filter_value = args.get("filter", "unused")
        sort = args.get("sort", "usage_desc")
        if filter_value not in {"unused", "all", "selected"}:
            raise ValueError("invalid tags filter")
        if sort not in {"usage_desc", "usage_asc", "name_asc", "name_desc"}:
            raise ValueError("invalid tags sort")
        hierarchy_usage = cleanup_hierarchy_usage(state.get("tags") or [])
        rows = [
            {
                **tag,
                "direct_usage": int(tag.get("usage") or 0),
                "usage": hierarchy_usage[str(tag.get("id"))],
            }
            for tag in state.get("tags") or []
        ]
        if filter_value == "unused":
            rows = [tag for tag in rows if not tag.get("usage")]
        elif filter_value == "selected":
            rows = [tag for tag in rows if str(tag.get("id")) in selected_ids]
        if query:
            rows = [tag for tag in rows if query in " ".join([tag.get("name", ""), *(tag.get("aliases") or [])]).casefold()]
        if sort.startswith("usage"):
            rows.sort(key=lambda tag: (int(tag.get("usage") or 0), tag.get("name", "").casefold()), reverse=sort.endswith("desc"))
        else:
            rows.sort(key=lambda tag: tag.get("name", "").casefold(), reverse=sort.endswith("desc"))
        rows = [
            {key: tag.get(key) for key in ("id", "name", "aliases", "usage", "direct_usage", "counts")}
            for tag in rows
        ]
    elif section == "duplicates":
        filter_value = args.get("filter", "all")
        sort = args.get("sort", "score_desc")
        if filter_value not in {"all", "conflicts", "selected"}:
            raise ValueError("invalid duplicates filter")
        if sort not in {"score_desc", "name_asc"}:
            raise ValueError("invalid duplicates sort")
        rows = cleanup_duplicate_groups(state, duplicate_cutoff)
        if filter_value == "conflicts":
            rows = [group for group in rows if group.get("remote_conflicts")]
        elif filter_value == "selected":
            rows = [group for group in rows if str(group.get("id")) in selected_ids]
        if query:
            rows = [group for group in rows if any(query in tag.get("name", "").casefold() for tag in group.get("tags") or [])]
        rows.sort(
            key=(lambda group: (-float(group.get("score") or 0), (group.get("tags") or [{}])[0].get("name", "").casefold()))
            if sort == "score_desc" else
            (lambda group: (group.get("tags") or [{}])[0].get("name", "").casefold())
        )
        rows = [
            {
                "id": group.get("id"),
                "score": group.get("score"),
                "conflicts": [
                    {"endpoint": endpoint, "id_count": len(stash_ids)}
                    for endpoint, stash_ids in (group.get("remote_conflicts") or {}).items()
                ],
                "tags": [
                    {
                        **{key: tag.get(key) for key in ("id", "name", "aliases", "usage")},
                        "remote_endpoints": sorted({
                            item.get("endpoint") for item in tag.get("stash_ids") or [] if item.get("endpoint")
                        }),
                        "conflict_endpoints": sorted({
                            item.get("endpoint") for item in tag.get("stash_ids") or []
                            if item.get("endpoint") in (group.get("remote_conflicts") or {})
                        }),
                    }
                    for tag in group.get("tags") or []
                ],
            }
            for group in rows
        ]
    else:
        filter_value = args.get("filter", "all")
        sort = args.get("sort", "name_asc")
        if filter_value not in {"all", "selected"}:
            raise ValueError("invalid splits filter")
        if sort not in {"name_asc", "aliases_desc", "scenes_desc"}:
            raise ValueError("invalid splits sort")
        rows = list(state.get("splits") or [])
        if filter_value == "selected":
            rows = [split for split in rows if str(split.get("tag_id")) in selected_ids]
        if query:
            rows = [
                split for split in rows
                if query in " ".join([(split.get("tag") or {}).get("name", ""), *((split.get("tag") or {}).get("aliases") or split.get("aliases") or [])]).casefold()
            ]
        if sort == "aliases_desc":
            rows.sort(key=lambda split: (-int(split.get("alias_count") or 0), (split.get("tag") or {}).get("name", "").casefold()))
        elif sort == "scenes_desc":
            rows.sort(key=lambda split: (-int(split.get("scene_count") or 0), (split.get("tag") or {}).get("name", "").casefold()))
        else:
            rows.sort(key=lambda split: (split.get("tag") or {}).get("name", "").casefold())
        rows = [
            {
                "tag_id": split.get("tag_id"),
                "name": (split.get("tag") or {}).get("name"),
                "aliases": list((split.get("tag") or {}).get("aliases") or split.get("aliases") or []),
                "alias_count": split.get("alias_count", len(split.get("aliases") or [])),
                "scene_count": split.get("scene_count", 0),
                "candidate_count": len(split.get("candidates") or []),
            }
            for split in rows
        ]

    total = len(rows)
    start = (page - 1) * per_page
    result = {"items": rows[start:start + per_page], "page": page, "per_page": per_page, "total": total}
    if section == "duplicates":
        result["similarity_cutoff"] = duplicate_cutoff
    return result


def cleanup_candidates(state, args):
    parent_id = args.get("parent_tag_id") or args.get("tag_id")
    if not isinstance(parent_id, (str, int)) or not str(parent_id):
        raise ValueError("parent tag ID is required")
    page = args.get("page", 1)
    per_page = args.get("per_page", 100)
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    if isinstance(per_page, bool) or not isinstance(per_page, int) or not 1 <= per_page <= 100:
        raise ValueError("per_page must be between 1 and 100")
    split = next(
        (item for item in state.get("splits") or [] if str(item.get("tag_id")) == str(parent_id)),
        None,
    )
    if split is None:
        raise ValueError("alias-split parent is not present in the cleanup plan")
    candidates = list(split.get("candidates") or [])
    start = (page - 1) * per_page
    items = [
        {
            key: candidate.get(key)
            for key in (
                "id", "alias", "remote_name", "child_name", "scene_count", "providers",
                "score", "exact", "existing_tag_ids", "action",
            )
        } | {"evidence_scene_ids": [str(scene_id) for scene_id in (candidate.get("scene_ids") or [])[:3]]}
        for candidate in candidates[start:start + per_page]
    ]
    tag = split.get("tag") or {}
    return {
        "parent": {
            "tag_id": str(split.get("tag_id")),
            "name": tag.get("name"),
            "aliases": list(tag.get("aliases") or split.get("aliases") or []),
            "scene_count": split.get("scene_count", 0),
            "candidate_count": len(candidates),
        },
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": len(candidates),
    }


CLEANUP_REVIEW_STATE_DEFAULTS = {
    "junk_ids": [],
    "duplicates": {},
    "splits": {},
    "section": "tags",
    "split_parent_id": "",
    "views": {
        "tags": {"page": 1, "query": "", "filter": "unused", "sort": "usage_desc"},
        "duplicates": {"page": 1, "per_page": 1, "query": "", "filter": "all", "sort": "score_desc"},
        "splits": {"page": 1, "query": "", "filter": "all", "sort": "name_asc"},
    },
}

CLEANUP_REVIEW_VIEW_OPTIONS = {
    "tags": {"filter": {"unused", "all", "selected"}, "sort": {"usage_desc", "usage_asc", "name_asc", "name_desc"}},
    "duplicates": {"filter": {"all", "conflicts", "selected"}, "sort": {"score_desc", "name_asc"}},
    "splits": {"filter": {"all", "selected"}, "sort": {"name_asc", "aliases_desc", "scenes_desc"}},
}


def cleanup_review_state_view(value):
    if not isinstance(value, dict):
        raise ValueError("view must be an object")
    page = value.get("page", 1)
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("view page must be a positive integer")
    query = value.get("query", "")
    if not isinstance(query, str):
        raise ValueError("view query must be text")
    view = {"page": page, "query": query, "filter": value.get("filter"), "sort": value.get("sort")}
    if "per_page" in value:
        per_page = value.get("per_page")
        if isinstance(per_page, bool) or not isinstance(per_page, int) or not 1 <= per_page <= 100:
            raise ValueError("view per_page must be between 1 and 100")
        view["per_page"] = per_page
    return view


def cleanup_review_state_views(value):
    if not isinstance(value, dict):
        raise ValueError("views must be an object")
    views = {}
    for section, options in CLEANUP_REVIEW_VIEW_OPTIONS.items():
        view = cleanup_review_state_view(value.get(section) or CLEANUP_REVIEW_STATE_DEFAULTS["views"][section])
        if view["filter"] not in options["filter"]:
            raise ValueError(f"invalid {section} filter")
        if view["sort"] not in options["sort"]:
            raise ValueError(f"invalid {section} sort")
        views[section] = view
    return views


def cleanup_review_state_duplicates(value):
    if not isinstance(value, dict):
        raise ValueError("duplicates must be an object")
    duplicates = {}
    for group_id, choice in value.items():
        if not isinstance(choice, dict):
            raise ValueError("duplicate choice must be an object")
        source_ids = choice.get("source_ids") or []
        if not isinstance(source_ids, list) or any(not isinstance(item, (str, int)) for item in source_ids):
            raise ValueError("duplicate source_ids must be a list")
        duplicates[str(group_id)] = {
            "target_id": str(choice.get("target_id") or ""),
            "source_ids": [str(item) for item in source_ids],
            "override_remote_ids": bool(choice.get("override_remote_ids")),
            "has_conflicts": bool(choice.get("has_conflicts")),
        }
    return duplicates


def cleanup_review_state_splits(value):
    if not isinstance(value, dict):
        raise ValueError("splits must be an object")
    splits = {}
    for tag_id, candidates in value.items():
        if not isinstance(candidates, list):
            raise ValueError("split candidates must be a list")
        items = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ValueError("split candidate must be an object")
            action = candidate.get("action") or "child-only"
            if action not in {"child-only", "parent-only", "parent-plus-child", "skip"}:
                raise ValueError("invalid split candidate action")
            candidate_id = candidate.get("candidate_id")
            if not isinstance(candidate_id, (str, int)) or not str(candidate_id):
                raise ValueError("split candidate_id is required")
            scene_count = candidate.get("scene_count", 0)
            if isinstance(scene_count, bool) or not isinstance(scene_count, int):
                raise ValueError("split candidate scene_count must be an integer")
            items.append({
                "candidate_id": str(candidate_id),
                "action": action,
                "child_name": str(candidate.get("child_name") or ""),
                "remove_alias": bool(candidate.get("remove_alias", True)),
                "scene_count": scene_count,
            })
        splits[str(tag_id)] = items
    return splits


def cleanup_review_state_payload(args):
    junk_ids = args.get("junk_ids") or []
    if not isinstance(junk_ids, list) or any(not isinstance(item, (str, int)) for item in junk_ids):
        raise ValueError("junk_ids must be a list")
    section = args.get("section", "tags")
    if section not in CLEANUP_REVIEW_VIEW_OPTIONS:
        raise ValueError("section must be tags, duplicates, or splits")
    return {
        "junk_ids": sorted({str(item) for item in junk_ids}),
        "duplicates": cleanup_review_state_duplicates(args.get("duplicates") or {}),
        "splits": cleanup_review_state_splits(args.get("splits") or {}),
        "section": section,
        "split_parent_id": str(args.get("split_parent_id") or ""),
        "views": cleanup_review_state_views(args.get("views") or CLEANUP_REVIEW_STATE_DEFAULTS["views"]),
    }


def cleanup_review_state_get(server, token):
    if not valid_scan_token(token):
        raise ValueError("cleanup token is required")
    state = read_cleanup_review_state(server, token)
    if state is None:
        return dict(CLEANUP_REVIEW_STATE_DEFAULTS, cleanup_token=token)
    return state


def cleanup_review_state_save(server, args):
    token = args.get("cleanup_token") or args.get("scan_token")
    if not valid_scan_token(token):
        raise ValueError("cleanup token is required")
    plan = read_cleanup_state(server, token)
    if plan is None or plan.get("status") not in {"completed", "applied"}:
        raise ValueError("review state can only be saved while the cleanup plan is completed")
    payload = cleanup_review_state_payload(args)
    payload["cleanup_token"] = token
    write_cleanup_review_state(server, payload)
    return {"ok": True}


def graphql(url, query, variables=None, headers=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    request = Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "Tag Organizer/1.5.0", **(headers or {})})
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


def canonical_tag_names(tags):
    return {str(tag["id"]): tag["name"] for tag in tags}


class CleanupStaleError(RuntimeError):
    """The review snapshot no longer describes the object being changed."""


def normalized_tag_name(value):
    folded = "".join(
        char for char in unicodedata.normalize("NFKD", str(value or ""))
        if not unicodedata.combining(char)
    ).casefold()
    return re.sub(r"[^\w]+", "", folded, flags=re.UNICODE)


def cleanup_tag_snapshot(tag):
    stash_ids = [
        {"endpoint": item.get("endpoint"), "stash_id": item.get("stash_id")}
        for item in tag.get("stash_ids") or []
        if item.get("endpoint") and item.get("stash_id")
    ]
    counts = {
        key: int(tag.get(key) or 0)
        for key in (
            "scene_count",
            "scene_marker_count",
            "image_count",
            "gallery_count",
            "performer_count",
            "studio_count",
            "group_count",
        )
    }
    return {
        "id": str(tag["id"]),
        "name": tag["name"],
        "aliases": list(tag.get("aliases") or []),
        "stash_ids": stash_ids,
        "counts": counts,
        "usage": sum(counts.values()),
        "parents": sorted(str(parent["id"]) for parent in tag.get("parents") or []),
        "children": sorted(str(child["id"]) for child in tag.get("children") or []),
    }


def cleanup_tag_identity(tag):
    """Fields whose change makes a review mutation unsafe."""
    def relation_ids(values):
        return sorted(
            str(value.get("id")) if isinstance(value, dict) else str(value)
            for value in values or []
        )

    return {
        "id": str(tag["id"]),
        "name": tag["name"],
        "aliases": list(tag.get("aliases") or []),
        "stash_ids": sorted(
            (item.get("endpoint"), item.get("stash_id"))
            for item in tag.get("stash_ids") or []
        ),
        "parents": relation_ids(tag.get("parents")),
        "children": relation_ids(tag.get("children")),
    }


def cleanup_tags_by_id(tags):
    return {str(tag["id"]): tag for tag in tags}


def cleanup_hierarchy_usage(tags):
    records = cleanup_tags_by_id(tags)
    totals = {}
    # ponytail: one graph walk per tag; memoize descendant sets if review becomes slow.
    for tag_id in records:
        seen = set()
        pending = [tag_id]
        while pending:
            current_id = pending.pop()
            if current_id in seen:
                continue
            seen.add(current_id)
            current = records.get(current_id) or {}
            pending.extend(
                str(child.get("id")) if isinstance(child, dict) else str(child)
                for child in current.get("children") or []
            )
        totals[tag_id] = sum(int((records.get(item) or {}).get("usage") or 0) for item in seen)
    return totals


def dedupe_tag_names(names, excluded=None):
    excluded = {str(name).casefold() for name in (excluded or [])}
    result = []
    seen = set()
    for name in names:
        if not isinstance(name, str) or not name.strip():
            continue
        key = name.casefold()
        if key in excluded or key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


def fuzzy_similarity(left, right):
    return difflib.SequenceMatcher(None, normalized_tag_name(left), normalized_tag_name(right)).ratio()


def duplicate_similarity_edges(tags, minimum=DUPLICATE_SIMILARITY_FLOOR, progress_callback=None):
    """Calculate compact direct-similarity edges once for runtime filtering."""
    snapshots = sorted(
        (cleanup_tag_snapshot(tag) if "counts" not in tag else tag for tag in tags),
        key=lambda tag: (-int(tag.get("usage") or 0), tag["name"].casefold()),
    )
    normalized_names = [
        [normalized_tag_name(name) for name in [tag["name"], *tag.get("aliases", [])] if normalized_tag_name(name)]
        for tag in snapshots
    ]
    edges = []
    # ponytail: store only scores >= 0.70; lower scores are too noisy and dense to tune safely.
    progress_step = max(1, len(snapshots) // 100)
    if progress_callback:
        progress_callback(0, len(snapshots))
    for index, anchor in enumerate(snapshots):
        for candidate_index, candidate in enumerate(snapshots[index + 1:], index + 1):
            score = 0
            for left_name in normalized_names[index]:
                for right_name in normalized_names[candidate_index]:
                    if 2 * min(len(left_name), len(right_name)) / (len(left_name) + len(right_name)) < minimum:
                        continue
                    score = max(score, difflib.SequenceMatcher(None, left_name, right_name).ratio())
                    if score == 1:
                        break
                if score == 1:
                    break
            if score >= minimum:
                edges.append([anchor["id"], candidate["id"], round(score * 1000)])
        if progress_callback and ((index + 1) % progress_step == 0 or index + 1 == len(snapshots)):
            progress_callback(index + 1, len(snapshots))
    return edges


def duplicate_groups_from_edges(tags, edges, cutoff=DUPLICATE_FUZZY_CUTOFF):
    """Build non-overlapping groups whose members directly match one anchor."""
    snapshots = sorted(tags, key=lambda tag: (-int(tag.get("usage") or 0), tag["name"].casefold()))
    scores = {
        (str(left), str(right)): float(score) / 1000 if float(score) > 1 else float(score)
        for left, right, score in edges
        if (float(score) / 1000 if float(score) > 1 else float(score)) >= cutoff
    }
    assigned = set()
    rows = []
    for index, anchor in enumerate(snapshots):
        if anchor["id"] in assigned:
            continue
        matches = [
            candidate for candidate in snapshots[index + 1:]
            if candidate["id"] not in assigned and (anchor["id"], candidate["id"]) in scores
        ]
        if matches:
            assigned.update(tag["id"] for tag in matches)
            group = [anchor, *matches]
            tag_ids = sorted(tag["id"] for tag in group)
            rows.append(
                {
                    "id": "duplicate-" + "-".join(tag_ids),
                    "score": max(scores[(anchor["id"], tag["id"])] for tag in matches),
                    "tag_ids": tag_ids,
                    "tags": group,
                    "remote_conflicts": remote_id_conflicts(group),
                }
            )
    return sorted(rows, key=lambda row: (-row["score"], row["tags"][0]["name"].casefold()))


def fuzzy_duplicate_groups(tags, cutoff=DUPLICATE_FUZZY_CUTOFF, progress_callback=None):
    snapshots = [cleanup_tag_snapshot(tag) if "counts" not in tag else tag for tag in tags]
    return duplicate_groups_from_edges(
        snapshots,
        duplicate_similarity_edges(snapshots, cutoff, progress_callback),
        cutoff,
    )


def cleanup_duplicate_groups(state, cutoff):
    if "duplicate_edges" not in state:
        return list(state.get("duplicates") or [])
    return duplicate_groups_from_edges(state.get("tags") or [], state.get("duplicate_edges") or [], cutoff)


def remote_id_conflicts(tags):
    by_endpoint = {}
    for tag in tags:
        for stash_id in tag.get("stash_ids") or []:
            by_endpoint.setdefault(stash_id.get("endpoint"), set()).add(stash_id.get("stash_id"))
    return {
        endpoint: sorted(stash_ids)
        for endpoint, stash_ids in by_endpoint.items()
        if endpoint and len(stash_ids) > 1
    }


def union_hierarchy(tags, target_id, source_ids):
    """Collapse source nodes, union their edges, and reject any resulting cycle."""
    source_ids = {str(tag_id) for tag_id in source_ids}
    target_id = str(target_id)
    group_ids = source_ids | {target_id}
    records = cleanup_tags_by_id(tags)
    if target_id not in records or not source_ids <= records.keys():
        raise CleanupStaleError("duplicate selection is not present in the scan snapshot")
    parents = {}
    children = {}
    def relation_ids(values):
        return [str(value.get("id")) if isinstance(value, dict) else str(value) for value in values or []]

    for tag_id, tag in records.items():
        node = target_id if tag_id in group_ids else tag_id
        parents.setdefault(node, set())
        children.setdefault(node, set())
        for parent_id in relation_ids(tag.get("parents")):
            parent_node = target_id if parent_id in group_ids else parent_id
            if parent_node != node:
                parents[node].add(parent_node)
                children.setdefault(parent_node, set()).add(node)
        for child_id in relation_ids(tag.get("children")):
            child_node = target_id if child_id in group_ids else child_id
            if child_node != node:
                children[node].add(child_node)
                parents.setdefault(child_node, set()).add(node)

    for source_id in source_ids:
        parents.pop(source_id, None)
        children.pop(source_id, None)
    for values in (parents, children):
        for node, links in values.items():
            links.difference_update(source_ids)
            if target_id in links and node == target_id:
                links.remove(target_id)

    visiting = set()
    visited = set()

    def visit(node):
        if node in visiting:
            raise ValueError("tag hierarchy would contain a cycle")
        if node in visited:
            return
        visiting.add(node)
        for child_id in children.get(node, set()):
            visit(child_id)
        visiting.remove(node)
        visited.add(node)

    for node in children:
        visit(node)
    return {
        "parent_ids": sorted(parents.get(target_id, set())),
        "child_ids": sorted(children.get(target_id, set())),
    }


def cleanup_scenes_for_tags(local_url, local_headers, parent_ids, ancestors, progress_callback=None):
    parent_ids = {str(tag_id) for tag_id in parent_ids}
    scenes_by_parent = {tag_id: [] for tag_id in parent_ids}
    page = 1
    total = 0
    while True:
        result = graphql(
            local_url,
            CLEANUP_SCENES_BY_TAGS_QUERY,
            {
                "filter": {"page": page, "per_page": SCAN_PAGE_SIZE},
                "scene_filter": {"tags": {"value": sorted(parent_ids), "modifier": "INCLUDES"}},
            },
            local_headers,
        )["findScenes"]
        batch = result.get("scenes") or []
        total = result.get("count") or 0
        if progress_callback:
            progress_callback(min((page - 1) * SCAN_PAGE_SIZE + len(batch), total), total)
        for scene in batch:
            matching_parents = set()
            for tag in scene.get("tags") or []:
                matching_parents.update(ancestors.get(str(tag["id"]), {str(tag["id"])}))
            for parent_id in matching_parents & parent_ids:
                scenes_by_parent[parent_id].append(scene)
        if not batch or page * SCAN_PAGE_SIZE >= total:
            break
        page += 1
    return scenes_by_parent


def cleanup_candidate_id(parent_id, alias, remote_name):
    return "split-{}-{}-{}".format(
        parent_id,
        normalized_tag_name(alias) or "alias",
        normalized_tag_name(remote_name) or "manual",
    )


def cleanup_scene_evidence(scenes, providers, remote_cache, progress_callback=None):
    prefetch_remote_tag_names(scenes, providers, remote_cache)
    evidence = {}
    failures = []
    for index, scene in enumerate(scenes, 1):
        scene_id = str(scene["id"])
        names, scene_failures = remote_tag_names(scene, providers, remote_cache)
        evidence[scene_id] = names
        failures.extend({"scene_id": scene_id, **failure} for failure in scene_failures)
        if progress_callback:
            progress_callback(index, len(scenes))
    return evidence, failures


def cleanup_split_candidates(
    parent,
    scenes,
    providers,
    local_index,
    remote_cache,
    progress_callback=None,
    scene_evidence=None,
):
    observations = {}
    failures = []
    if scene_evidence is None:
        prefetch_remote_tag_names(scenes, providers, remote_cache)
    for index, scene in enumerate(scenes, 1):
        if scene_evidence is None:
            names, scene_failures = remote_tag_names(scene, providers, remote_cache)
            failures.extend({"scene_id": scene["id"], **failure} for failure in scene_failures)
        else:
            names = scene_evidence.get(str(scene["id"]), [])
        for name in {name.casefold(): name for name in names if name.strip()}.values():
            key = normalized_tag_name(name)
            observation = observations.setdefault(
                key,
                {"name": name, "scene_ids": set(), "providers": set()},
            )
            observation["scene_ids"].add(str(scene["id"]))
            observation["providers"].update(
                stash_id.get("endpoint")
                for stash_id in scene.get("stash_ids") or []
                if stash_id.get("endpoint") in providers
            )
        if progress_callback:
            progress_callback(index, len(scenes))
    candidates = []
    aliases = list(parent.get("aliases") or [])
    for alias in aliases:
        exact = []
        fuzzy = []
        alias_key = normalized_tag_name(alias)
        for observation in observations.values():
            score = fuzzy_similarity(alias, observation["name"])
            if score < FUZZY_CUTOFF:
                continue
            item = {
                "id": cleanup_candidate_id(parent["id"], alias, observation["name"]),
                "alias": alias,
                "remote_name": observation["name"],
                "child_name": alias if score == 1 else observation["name"],
                "scene_ids": sorted(observation["scene_ids"]),
                "scene_count": len(observation["scene_ids"]),
                "providers": sorted(observation["providers"]),
                "score": round(score, 3),
                "exact": score == 1,
                "existing_tag_ids": sorted(
                    tag_id for tag_id in local_index.get(normalized_tag_name(observation["name"]), set())
                    if tag_id != parent["id"]
                ),
                "action": "child-only",
            }
            (exact if score == 1 else fuzzy).append(item)
        if not exact and not fuzzy:
            candidates.append(
                {
                    "id": cleanup_candidate_id(parent["id"], alias, ""),
                    "alias": alias,
                    "remote_name": "",
                    "child_name": alias,
                    "scene_ids": [],
                    "scene_count": 0,
                    "providers": [],
                    "score": 0,
                    "exact": False,
                    "existing_tag_ids": sorted(
                        tag_id for tag_id in local_index.get(alias_key, set()) if tag_id != parent["id"]
                    ),
                    "action": "child-only",
                }
            )
        candidates.extend(sorted(exact, key=lambda item: (-item["scene_count"], item["remote_name"].casefold())))
        candidates.extend(sorted(fuzzy, key=lambda item: (-item["score"], -item["scene_count"], item["remote_name"].casefold())))
    return sorted(
        candidates,
        key=lambda item: (
            -int(item["exact"]),
            -item["score"],
            -item["scene_count"],
            (item.get("alias") or "").casefold(),
            (item.get("remote_name") or "").casefold(),
        ),
    ), failures


def cleanup_plan(local_url, local_headers, providers, progress_callback=None, duplicate_cutoff=DUPLICATE_FUZZY_CUTOFF):
    records = []
    page = 1
    total = 0
    while True:
        result = graphql(
            local_url,
            CLEANUP_TAGS_QUERY,
            {"filter": {"page": page, "per_page": CLEANUP_TAG_PAGE_SIZE}},
            local_headers,
        )["findTags"]
        batch = result["tags"]
        records.extend(batch)
        total = result["count"]
        if progress_callback:
            progress_callback(len(records), total, "tags")
        if not batch or len(records) >= total:
            break
        page += 1
    tags = [cleanup_tag_snapshot(tag) for tag in records]
    local_tags = [tag for tag in records]
    local_index = {}
    for tag in local_tags:
        for name in [tag["name"], *tag.get("aliases", [])]:
            local_index.setdefault(normalized_tag_name(name), set()).add(str(tag["id"]))
    def report_duplicate_progress(scanned, duplicate_total):
        if progress_callback:
            progress_callback(scanned, duplicate_total, "duplicates")

    duplicate_edges = duplicate_similarity_edges(tags, progress_callback=report_duplicate_progress)
    duplicates = duplicate_groups_from_edges(tags, duplicate_edges, duplicate_cutoff)
    splits = []
    failures = []
    remote_cache = {}
    parents = sorted(
        (tag for tag in tags if tag["aliases"]),
        key=lambda tag: (-len(tag["aliases"]), tag["name"].casefold()),
    )
    tags_by_id = {str(tag["id"]): tag for tag in tags}
    ancestors = {tag_id: {tag_id} for tag_id in tags_by_id}
    for tag in tags:
        node = str(tag["id"])
        pending = [str(parent_id) for parent_id in tag.get("parents") or []]
        while pending:
            parent_id = pending.pop()
            if parent_id in ancestors[node]:
                continue
            ancestors[node].add(parent_id)
            pending.extend(
                str(candidate)
                for candidate in tags_by_id.get(parent_id, {}).get("parents", [])
            )
    if progress_callback:
        progress_callback(0, len(parents), "plan")
    scenes_by_parent = {}
    if parents:
        def report_scene_loading(loaded, scene_total):
            if progress_callback:
                progress_callback(loaded, scene_total, "plan", "Loading unique scenes {}/{}".format(loaded, scene_total))

        scenes_by_parent = cleanup_scenes_for_tags(
            local_url,
            local_headers,
            [parent["id"] for parent in parents],
            ancestors,
            report_scene_loading,
        )
    unique_scenes = {}
    for scenes in scenes_by_parent.values():
        for scene in scenes:
            unique_scenes.setdefault(str(scene["id"]), scene)
    scene_evidence = {}
    if unique_scenes:
        def report_remote_loading(loaded, scene_total):
            if progress_callback:
                progress_callback(loaded, scene_total, "plan", "Loading remote tags {}/{}".format(loaded, scene_total))

        scene_evidence, evidence_failures = cleanup_scene_evidence(
            list(unique_scenes.values()),
            providers,
            remote_cache,
            report_remote_loading,
        )
        failures.extend(evidence_failures)
    for index, parent in enumerate(parents, 1):
        scenes = scenes_by_parent.get(str(parent["id"]), [])

        def report_parent_progress(processed, scene_total):
            if not progress_callback:
                return
            if scene_total:
                progress_callback(
                    index - 1 + 0.5 + 0.5 * processed / scene_total,
                    len(parents),
                    "plan",
                    "{} ({}/{} scenes)".format(parent["name"], processed, scene_total),
                )
            else:
                progress_callback(index, len(parents), "plan", "{} (no scenes)".format(parent["name"]))

        candidates, split_failures = cleanup_split_candidates(
            parent,
            scenes,
            providers,
            local_index,
            remote_cache,
            report_parent_progress,
            scene_evidence,
        )
        if not scenes and progress_callback:
            progress_callback(index, len(parents), "plan", "{} (no scenes)".format(parent["name"]))
        failures.extend(split_failures)
        splits.append(
            {
                "tag": parent,
                "tag_id": parent["id"],
                "aliases": parent["aliases"],
                "alias_count": len(parent["aliases"]),
                "scene_count": len(scenes),
                "candidates": candidates,
            }
        )
    return {
        "tags": sorted(tags, key=lambda tag: (-tag["usage"], tag["name"].casefold())),
        "junk": sorted(tags, key=lambda tag: (-tag["usage"], tag["name"].casefold())),
        "duplicates": duplicates,
        "duplicate_edges": duplicate_edges,
        "splits": splits,
        "failures": failures,
    }


def find_local_tags(local_url, local_headers, names, canonical_names=None):
    found = {}
    for name in {name.casefold(): name for name in names if name.strip()}.values():
        tags = graphql(
            local_url,
            TAG_SEARCH_QUERY,
            {"filter": {"q": name, "per_page": -1}},
            local_headers,
        )["findTags"]["tags"]
        if canonical_names is not None:
            canonical_names.update(canonical_tag_names(tags))
        matches = tag_index(tags)
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


def remote_tags_batch_query(scene_ids):
    variables = {}
    fields = []
    aliases = {}
    for index, (cache_key, scene_id) in enumerate(scene_ids):
        variable = f"id_{index}"
        alias = f"scene_{index}"
        variables[variable] = scene_id
        aliases[alias] = cache_key
        fields.append(f"{alias}: findScene(id: ${variable}) {{ tags {{ name }} }}")
    declarations = ", ".join(f"${name}: ID!" for name in variables)
    return f"query RemoteScenes({declarations}) {{ {' '.join(fields)} }}", variables, aliases


def remote_tag_names_for_id(endpoint, stash_id, provider, cache):
    cache_key = (endpoint, stash_id)
    if cache_key in cache:
        return cache[cache_key], []
    try:
        data = graphql(
            endpoint,
            REMOTE_TAGS_QUERY,
            {"id": stash_id},
            {"ApiKey": provider["api_key"]},
        )
        cache[cache_key] = [tag["name"] for tag in (data["findScene"] or {}).get("tags", [])]
        return cache[cache_key], []
    except RuntimeError as error:
        return [], [{"provider": endpoint, "error": str(error)}]


def prefetch_remote_tag_names(scenes, providers, cache):
    pending = {}
    for scene in scenes:
        for stash_id in scene.get("stash_ids") or []:
            endpoint = stash_id.get("endpoint")
            if endpoint not in providers:
                continue
            cache_key = (endpoint, stash_id["stash_id"])
            if cache_key not in cache:
                pending.setdefault(endpoint, {})[cache_key] = stash_id["stash_id"]

    for endpoint, scene_ids in pending.items():
        provider = providers[endpoint]
        items = list(scene_ids.items())
        for offset in range(0, len(items), REMOTE_BATCH_SIZE):
            chunk = items[offset:offset + REMOTE_BATCH_SIZE]
            if len(chunk) == 1:
                continue
            query, variables, aliases = remote_tags_batch_query(chunk)
            try:
                data = graphql(endpoint, query, variables, {"ApiKey": provider["api_key"]})
            except RuntimeError:
                continue
            for alias, cache_key in aliases.items():
                scene = data.get(alias) or {}
                cache[cache_key] = [tag["name"] for tag in scene.get("tags", [])]


def remote_tag_names(scene, providers, cache):
    names = []
    failures = []
    for stash_id in scene.get("stash_ids") or []:
        endpoint = stash_id.get("endpoint")
        provider = providers.get(endpoint)
        if not provider:
            continue
        scene_names, scene_failures = remote_tag_names_for_id(
            endpoint,
            stash_id["stash_id"],
            provider,
            cache,
        )
        failures.extend(scene_failures)
        names.extend(scene_names)
    return names, failures


def configured_providers(configuration):
    return {
        provider["endpoint"]: provider
        for provider in configuration["general"].get("stashBoxes", [])
        if provider.get("endpoint") and provider.get("api_key")
    }


def cleanup_duplicate_cutoff(configuration):
    value = (configuration.get("plugins", {}).get(PLUGIN_ID) or {}).get("duplicateSimilarityCutoff")
    if value in (None, ""):
        return DUPLICATE_FUZZY_CUTOFF
    try:
        cutoff = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("duplicate similarity cutoff must be a number") from error
    if not DUPLICATE_SIMILARITY_FLOOR <= cutoff <= 1:
        raise ValueError("duplicate similarity cutoff must be between 0.7 and 1")
    return cutoff


def gap_rows(scenes, providers, cache, progress_callback=None):
    gaps = {}
    failures = []
    scanned = 0
    prefetch_remote_tag_names(scenes, providers, cache)
    for processed, scene in enumerate(scenes, 1):
        if not any(stash_id.get("endpoint") in providers for stash_id in scene.get("stash_ids") or []):
            if progress_callback:
                progress_callback(processed)
            continue
        scanned += 1
        names, scene_failures = remote_tag_names(scene, providers, cache)
        failures.extend({"scene_id": scene["id"], **failure} for failure in scene_failures)
        for name in {name.casefold(): name for name in names}.values():
            key = name.casefold()
            gap = gaps.setdefault(key, {"name": name, "scene_ids": set()})
            gap["scene_ids"].add(scene["id"])
        if progress_callback:
            progress_callback(processed)
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
        "pid": os.getpid(),
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

            page_start = scanned

            def report_page_progress(processed):
                current = min(page_start + processed, total)
                state.update({"scanned": current, "total": total})
                write_scan_state(state_path, state)
                stash_progress(current, total)

            report_page_progress(0)
            rows, _, page_failures = gap_rows(
                scenes,
                {provider["endpoint"]: provider},
                cache,
                report_page_progress,
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
    verified_ids = [
        scene["id"]
        for scene in scenes
        if any(stash_id.get("endpoint") == provider["endpoint"] for stash_id in scene.get("stash_ids") or [])
    ]
    if not verified_ids:
        raise RuntimeError("none of the selected scenes are linked to the configured provider")

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

    applied = 0
    error = None
    for offset in range(0, len(verified_ids), LOCAL_BATCH_SIZE):
        chunk = verified_ids[offset:offset + LOCAL_BATCH_SIZE]
        try:
            updated = graphql(
                local_url,
                BULK_UPDATE_SCENES_MUTATION,
                {"input": {"ids": chunk, "tag_ids": {"ids": [tag_id], "mode": "ADD"}}},
                local_headers,
            )["bulkSceneUpdate"]
            applied += len(updated or [])
        except RuntimeError as update_error:
            error = error or str(update_error)
    return {
        "created": created,
        "applied": applied,
        "failed": len(requested_ids) - applied,
        "failure_count": 0,
        "error": error,
    }


def validate_add_items(items):
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list")
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("scene_ids"), list):
            raise ValueError("each item must contain a tag name and scene ID list")


def add_many(local_url, local_headers, provider, items, local_tags):
    validate_add_items(items)
    results = []
    for item in items:
        name = item["name"]
        scene_ids = item["scene_ids"]
        try:
            result = add_gap(local_url, local_headers, provider, name, scene_ids, local_tags)
            results.append({"name": name, "resolved": True, **result})
        except (RuntimeError, ValueError) as error:
            results.append({
                "name": name,
                "resolved": False,
                "created": False,
                "applied": 0,
                "failed": len({str(scene_id) for scene_id in scene_ids}),
                "failure_count": 0,
                "error": str(error),
            })
    return {
        "processed": len(results),
        "resolved": sum(result["resolved"] for result in results),
        "created": sum(result["created"] for result in results),
        "applied": sum(result["applied"] for result in results),
        "failed": sum(result["failed"] for result in results),
        "results": results,
    }


def pull_failure_text(failures):
    return "; ".join(
        (f"{failure['provider']}: " if failure.get("provider") else "") + failure["error"]
        for failure in failures
    )


def pull_all(local_url, local_headers, providers, server):
    state = {
        "status": "running",
        "scanned": 0,
        "total": 0,
        "changed": 0,
        "tags_added": 0,
        "failure_count": 0,
        "rows": [],
        "error": None,
        "pid": os.getpid(),
    }
    write_pull_state(server, state)
    summary = {"scanned": 0, "changed": 0, "tags_added": 0, "unknown_remote_tags": [], "failures": []}
    cache = {}
    checked_names = set()
    dead_ids = set()
    total = 0

    try:
        local_tag_records = graphql(local_url, TAGS_QUERY, headers=local_headers)["findTags"]["tags"]
        local_tags = tag_index(local_tag_records)
        canonical_names = canonical_tag_names(local_tag_records)
        checked_names.update(local_tags)

        def process(scene):
            summary["scanned"] += 1
            progress = f"[{summary['scanned']}/{total}]"
            failures = []
            added_names = []
            try:
                added_names, failures, unknown_names = sync_scene(
                    scene,
                    local_url,
                    local_headers,
                    providers,
                    local_tags,
                    cache,
                    checked_names,
                    canonical_names,
                    dead_ids,
                )
                summary["failures"].extend({"scene_id": scene["id"], **failure} for failure in failures)
                summary["unknown_remote_tags"] = sorted(set(summary["unknown_remote_tags"]) | unknown_names)
                if added_names:
                    summary["changed"] += 1
                    summary["tags_added"] += len(added_names)
                    stash_log(
                        "i",
                        f"{progress} Updated scene {scene.get('title') or '(untitled)'} (ID {scene['id']}): added {len(added_names)} tag(s)",
                    )
                else:
                    stash_log("d", f"{progress} Checked scene {scene.get('title') or '(untitled)'} (ID {scene['id']}): no new matching tags")
            except RuntimeError as error:
                failures = [{"error": str(error)}]
                summary["failures"].append({"scene_id": scene["id"], "error": str(error)})
                stash_log("e", f"{progress} Failed scene {scene.get('title') or '(untitled)'} (ID {scene['id']}): {error}")

            if added_names or failures:
                row = {
                    "scene_id": scene["id"],
                    "title": scene.get("title") or "(untitled)",
                    "screenshot": (scene.get("paths") or {}).get("screenshot"),
                    "added_tags": added_names,
                }
                if failures:
                    row["error"] = pull_failure_text(failures)
                state["rows"].append(row)
            state.update(
                {
                    "scanned": summary["scanned"],
                    "changed": summary["changed"],
                    "tags_added": summary["tags_added"],
                    "failure_count": len(summary["failures"]),
                }
            )
            write_pull_state(server, state)
            stash_progress(summary["scanned"], total)

        page = 1
        while True:
            result = graphql(
                local_url,
                SCENES_QUERY,
                {"filter": {"page": page, "per_page": PAGE_SIZE}},
                local_headers,
            )["findScenes"]
            scenes = result["scenes"]
            total = result["count"]
            state["total"] = total
            write_pull_state(server, state)
            stash_progress(summary["scanned"], total)
            prefetch_remote_tag_names(scenes, providers, cache)
            for scene in scenes:
                process(scene)
            if not scenes or summary["scanned"] >= total:
                break
            page += 1
        state["status"] = "completed"
        write_pull_state(server, state)
        return {
            **summary,
            "status": state["status"],
            "failure_count": state["failure_count"],
            "row_count": len(state["rows"]),
        }
    except Exception as error:
        state.update({"status": "failed", "error": str(error)})
        write_pull_state(server, state)
        raise


def cleanup_scan_all(local_url, local_headers, providers, server, token, duplicate_cutoff=DUPLICATE_FUZZY_CUTOFF):
    if not valid_scan_token(token):
        raise ValueError("cleanup token must be 8-64 letters, numbers, underscores, or hyphens")
    state = {
        "cleanup_token": token,
        "status": "running",
        "scanned": 0,
        "total": 0,
        "failure_count": 0,
        "tags": [],
        "junk": [],
        "duplicates": [],
        "duplicate_edges": [],
        "splits": [],
        "failures": [],
        "apply_result": None,
        "progress_phase": "tags",
        "progress_detail": "",
        "duplicate_similarity_cutoff": duplicate_cutoff,
        "error": None,
        "pid": os.getpid(),
    }
    write_cleanup_state(server, state)
    try:
        def report_cleanup_progress(scanned, total, phase, detail=""):
            state.update({
                "scanned": scanned,
                "total": total,
                "progress_phase": phase,
                "progress_detail": detail,
            })
            write_cleanup_state(server, state)
            stash_progress(scanned, total)

        plan = cleanup_plan(local_url, local_headers, providers, report_cleanup_progress, duplicate_cutoff)
        state.update(
            {
                "tags": plan["tags"],
                "junk": plan["junk"],
                "duplicates": plan["duplicates"],
                "duplicate_edges": plan["duplicate_edges"],
                "total": len(plan["splits"]),
                "failure_count": len(plan["failures"]),
                "failures": plan["failures"],
                "progress_phase": "plan",
                "progress_detail": "",
            }
        )
        write_cleanup_state(server, state)
        for index, split in enumerate(plan["splits"], 1):
            state["splits"].append(split)
            state["scanned"] = index
            write_cleanup_state(server, state)
            stash_progress(index, len(plan["splits"]))
        state["status"] = "completed"
        state["progress_phase"] = "complete"
        state["progress_detail"] = ""
        write_cleanup_state(server, state)
        return {
            "cleanup_token": token,
            "status": state["status"],
            "scanned": state["scanned"],
            "total": state["total"],
            "tag_count": len(state["tags"]),
            "duplicate_count": len(state["duplicates"]),
            "split_count": len(state["splits"]),
            "failure_count": state["failure_count"],
        }
    except Exception as error:
        state.update({"status": "failed", "error": str(error)})
        write_cleanup_state(server, state)
        raise


def cleanup_revalidate(local_url, local_headers, expected, tag_ids):
    ids = sorted({str(tag_id) for tag_id in tag_ids})
    if not ids:
        return {}
    records = graphql(local_url, CLEANUP_TAGS_BY_IDS_QUERY, {"ids": ids}, local_headers)["findTags"]["tags"]
    current = cleanup_tags_by_id(records)
    for tag_id in ids:
        record = current.get(tag_id)
        snapshot = expected.get(tag_id)
        if record is None or snapshot is None:
            raise CleanupStaleError("tag {} changed or was removed after the scan".format(tag_id))
        if cleanup_tag_identity(record) != cleanup_tag_identity(snapshot):
            raise CleanupStaleError("tag {} changed after the scan; rescan before applying".format(tag_id))
    return current


def cleanup_update_expected_link(expected, parent_id, child_id):
    parent_id, child_id = str(parent_id), str(child_id)
    if parent_id in expected:
        expected[parent_id]["children"] = sorted(set(expected[parent_id].get("children", [])) | {child_id})
    if child_id in expected:
        expected[child_id]["parents"] = sorted(set(expected[child_id].get("parents", [])) | {parent_id})


def cleanup_update_expected_aliases(expected, tag_id, aliases):
    if str(tag_id) in expected:
        expected[str(tag_id)]["aliases"] = list(aliases)


def cleanup_would_cycle(expected, parent_id, child_id):
    records = []
    for tag in expected.values():
        record = dict(tag)
        record["parents"] = [{"id": item} for item in tag.get("parents", [])]
        record["children"] = [{"id": item} for item in tag.get("children", [])]
        records.append(record)
    parent_id, child_id = str(parent_id), str(child_id)
    for tag in records:
        if tag["id"] == parent_id:
            tag["children"] = [{"id": child_id}, *tag["children"]]
        if tag["id"] == child_id:
            tag["parents"] = [{"id": parent_id}, *tag["parents"]]
    union_hierarchy(records, parent_id, [])


def cleanup_resolve_child(local_url, local_headers, expected, parent_id, child_name, cache):
    key = normalized_tag_name(child_name)
    if not key:
        raise ValueError("child tag name is required")
    cache_key = (str(parent_id), key)
    if cache_key in cache:
        return cache[cache_key]
    matches = []
    for tag in expected.values():
        if normalized_tag_name(tag["name"]) == key or any(
            normalized_tag_name(alias) == key for alias in tag.get("aliases", [])
        ):
            matches.append(tag)
    if len(matches) > 1:
        raise ValueError("child name matches multiple local tags")
    if matches:
        child = matches[0]
        child_id = child["id"]
        if child_id == str(parent_id):
            raise ValueError("a split child cannot be its own parent")
        if str(parent_id) not in child.get("parents", []):
            cleanup_would_cycle(expected, parent_id, child_id)
            cleanup_revalidate(local_url, local_headers, expected, [parent_id, child_id])
            parent_ids = sorted(set(child.get("parents", [])) | {str(parent_id)})
            graphql(
                local_url,
                TAG_UPDATE_MUTATION,
                {"input": {"id": child_id, "parent_ids": parent_ids}},
                local_headers,
            )
            cleanup_update_expected_link(expected, parent_id, child_id)
        cache[cache_key] = child_id
        return child_id

    cleanup_revalidate(local_url, local_headers, expected, [parent_id])
    child_id = graphql(
        local_url,
        TAG_CREATE_WITH_PARENT_MUTATION,
        {"input": {"name": child_name.strip(), "parent_ids": [str(parent_id)]}},
        local_headers,
    )["tagCreate"]["id"]
    expected[str(child_id)] = {
        "id": str(child_id),
        "name": child_name.strip(),
        "aliases": [],
        "stash_ids": [],
        "parents": [str(parent_id)],
        "children": [],
    }
    cleanup_update_expected_link(expected, parent_id, child_id)
    cache[cache_key] = str(child_id)
    return str(child_id)


def cleanup_merge_one(local_url, local_headers, expected, group, selection, result, group_id=""):
    target_id = str(selection.get("target_id") or "")
    source_ids = sorted({str(tag_id) for tag_id in (selection.get("source_ids") or selection.get("sources") or [])})
    group_ids = {str(tag_id) for tag_id in group.get("tag_ids") or []}
    if not target_id or not source_ids or target_id not in group_ids or not set(source_ids) <= group_ids:
        result["failures"].append({"kind": "merge", "group_id": group_id, "error": "select one target and at least one source from the suggested group"})
        return
    if target_id in source_ids or len(set(source_ids)) != len(source_ids):
        result["failures"].append({"kind": "merge", "group_id": group_id, "error": "the target cannot also be a source"})
        return
    all_ids = [target_id, *source_ids]
    tags = [expected[tag_id] for tag_id in all_ids if tag_id in expected]
    if len(tags) != len(all_ids):
        result["failures"].append({"kind": "merge", "group_id": group_id, "error": "duplicate selection is not present in the scan snapshot"})
        return
    conflicts = remote_id_conflicts(tags)
    override = bool(selection.get("override_remote_ids") or selection.get("remote_override"))
    if conflicts and not override:
        result["warnings"].append(
            {"kind": "remote-conflict", "group_id": group_id, "tag_id": target_id, "endpoints": conflicts, "requires_override": True}
        )
        return
    try:
        hierarchy = union_hierarchy(list(expected.values()), target_id, source_ids)
        cleanup_revalidate(local_url, local_headers, expected, all_ids)
        target = expected[target_id]
        aliases = dedupe_tag_names(
            [*target.get("aliases", []), *(name for tag in tags[1:] for name in [tag["name"], *tag.get("aliases", [])])],
            excluded=[target["name"]],
        )
        if conflicts and override:
            stash_ids = []
            seen_endpoints = set()
            for tag in tags:
                for stash_id in tag.get("stash_ids", []):
                    endpoint = stash_id.get("endpoint")
                    if endpoint not in seen_endpoints:
                        seen_endpoints.add(endpoint)
                        stash_ids.append(stash_id)
            result["warnings"].append(
                {"kind": "remote-conflict", "group_id": group_id, "tag_id": target_id, "endpoints": conflicts, "overridden": True}
            )
        else:
            stash_ids = []
            seen_stash_ids = set()
            for tag in tags:
                for stash_id in tag.get("stash_ids", []):
                    key = (stash_id.get("endpoint"), stash_id.get("stash_id"))
                    if key not in seen_stash_ids:
                        seen_stash_ids.add(key)
                        stash_ids.append(stash_id)
        graphql(
            local_url,
            TAGS_MERGE_MUTATION,
            {
                "input": {
                    "source": source_ids,
                    "destination": target_id,
                    "values": {
                        "id": target_id,
                        "aliases": aliases,
                        "stash_ids": stash_ids,
                        "parent_ids": hierarchy["parent_ids"],
                        "child_ids": hierarchy["child_ids"],
                    },
                }
            },
            local_headers,
        )
        expected[target_id].update(
            {
                "aliases": aliases,
                "stash_ids": stash_ids,
                "parents": hierarchy["parent_ids"],
                "children": hierarchy["child_ids"],
            }
        )
        for source_id in source_ids:
            expected.pop(source_id, None)
        result["merged"].append({"group_id": group_id, "target_id": target_id, "source_ids": source_ids})
    except (CleanupStaleError, RuntimeError, ValueError) as error:
        result["failures"].append({"kind": "merge", "group_id": group_id, "tag_id": target_id, "error": str(error)})


def cleanup_apply_splits(local_url, local_headers, expected, split_plan, selections, result):
    if selections is not None and not isinstance(selections, list):
        raise ValueError("splits must be a list")
    split_by_id = {str(split["tag_id"]): split for split in split_plan}
    child_cache = {}
    for selection in selections or []:
        parent_id = str(selection.get("tag_id") or selection.get("parent_id") or "")
        split = split_by_id.get(parent_id)
        if not split or parent_id not in expected:
            result["failures"].append({"kind": "split", "tag_id": parent_id, "error": "split selection is not present in the scan snapshot"})
            continue
        candidates = {str(candidate["id"]): candidate for candidate in split.get("candidates") or []}
        chosen = []
        for item in selection.get("candidates") or selection.get("items") or []:
            candidate = candidates.get(str(item.get("candidate_id") or item.get("id") or ""))
            if not candidate:
                result["failures"].append({"kind": "split", "tag_id": parent_id, "error": "unknown split candidate"})
                continue
            action = str(item.get("action") or candidate.get("action") or "child-only").replace("_", "-")
            if action == "parent-plus-child":
                action = "parent-plus-child"
            if action not in {"child-only", "parent-only", "parent-plus-child", "skip"}:
                result["failures"].append({"kind": "split", "tag_id": parent_id, "error": "invalid split action"})
                continue
            chosen.append(
                {
                    "candidate": candidate,
                    "action": action,
                    "child_name": (item.get("child_name") or candidate.get("child_name") or "").strip(),
                    "remove_alias": item.get("remove_alias", action != "skip"),
                }
            )
        if not chosen:
            continue
        try:
            child_ids = {}
            for item in chosen:
                if item["action"] in {"child-only", "parent-plus-child"}:
                    child_ids[id(item)] = cleanup_resolve_child(
                        local_url,
                        local_headers,
                        expected,
                        parent_id,
                        item["child_name"],
                        child_cache,
                    )

            scenes = {}
            for item in chosen:
                candidate = item["candidate"]
                for scene_id in candidate.get("scene_ids") or []:
                    scenes.setdefault(str(scene_id), []).append(item)
            for scene_id, scene_items in scenes.items():
                scene = graphql(local_url, SCENE_QUERY, {"id": scene_id}, local_headers)["findScene"]
                if not scene:
                    raise CleanupStaleError("scene {} changed or was removed after the scan".format(scene_id))
                current_ids = {str(tag["id"]) for tag in scene.get("tags") or []}
                next_ids = set(current_ids)
                keep_parent = False
                changed_children = set()
                for item in scene_items:
                    action = item["action"]
                    if action == "child-only":
                        changed_children.add(child_ids[id(item)])
                    elif action == "parent-only":
                        keep_parent = True
                    elif action == "parent-plus-child":
                        keep_parent = True
                        changed_children.add(child_ids[id(item)])
                if changed_children:
                    next_ids.update(changed_children)
                if not keep_parent and any(item["action"] == "child-only" for item in scene_items):
                    next_ids.discard(parent_id)
                if next_ids == current_ids:
                    continue
                tag_ids_to_check = [parent_id, *changed_children]
                cleanup_revalidate(local_url, local_headers, expected, tag_ids_to_check)
                graphql(
                    local_url,
                    UPDATE_SCENE_MUTATION,
                    {"input": {"id": scene_id, "tag_ids": sorted(next_ids)}},
                    local_headers,
                )
                result["scene_updates"].append({"scene_id": scene_id, "tag_ids": sorted(next_ids)})

            aliases_to_remove = {
                item["candidate"].get("alias")
                for item in chosen
                if item["remove_alias"] and item["candidate"].get("alias")
            }
            if aliases_to_remove:
                current = cleanup_revalidate(local_url, local_headers, expected, [parent_id])[parent_id]
                remaining = [
                    alias for alias in current.get("aliases") or []
                    if alias.casefold() not in {value.casefold() for value in aliases_to_remove}
                ]
                if remaining != current.get("aliases"):
                    graphql(
                        local_url,
                        TAG_UPDATE_MUTATION,
                        {"input": {"id": parent_id, "aliases": remaining}},
                        local_headers,
                    )
                    cleanup_update_expected_aliases(expected, parent_id, remaining)
                    result["aliases_removed"].append({"tag_id": parent_id, "aliases": sorted(aliases_to_remove)})
            result["applied_splits"].append({
                "tag_id": parent_id,
                "candidate_ids": [str(item["candidate"]["id"]) for item in chosen],
            })
        except (CleanupStaleError, RuntimeError, ValueError) as error:
            result["failures"].append({"kind": "split", "tag_id": parent_id, "error": str(error)})


def cleanup_apply(local_url, local_headers, server, args, duplicate_cutoff=DUPLICATE_FUZZY_CUTOFF):
    if not isinstance(args, dict):
        raise ValueError("cleanup apply arguments must be an object")
    token = args.get("cleanup_token") or args.get("scan_token")
    if not valid_scan_token(token):
        raise ValueError("cleanup token is required")
    backup_url = args.get("backup_url")
    if args.get("backup_confirmed") is not True and not (isinstance(backup_url, str) and backup_url.strip()):
        raise ValueError("database backup required before cleanup apply")
    for key in ("junk_ids", "duplicates", "splits"):
        if args.get(key) is not None and not isinstance(args.get(key), list):
            raise ValueError("{} must be a list".format(key))
    for key in ("duplicates", "splits"):
        if any(not isinstance(item, dict) for item in (args.get(key) or [])):
            raise ValueError("{} entries must be objects".format(key))
    state = read_cleanup_state(server, token)
    if state is None or state.get("status") not in {"completed", "applied"}:
        raise ValueError("complete a cleanup scan before applying changes")
    expected = cleanup_tags_by_id(state.get("tags") or [])
    result = {
        "status": "completed",
        "deleted": [],
        "merged": [],
        "scene_updates": [],
        "aliases_removed": [],
        "applied_splits": [],
        "warnings": [],
        "failures": [],
    }
    for tag_id in sorted({str(item) for item in args.get("junk_ids") or []}):
        try:
            cleanup_revalidate(local_url, local_headers, expected, [tag_id])
            response = graphql(local_url, TAG_DESTROY_MUTATION, {"input": {"id": tag_id}}, local_headers)
            if response.get("tagDestroy") is False:
                raise RuntimeError("tagDestroy returned false")
            result["deleted"].append(tag_id)
            expected.pop(tag_id, None)
        except (CleanupStaleError, RuntimeError, ValueError) as error:
            result["failures"].append({"kind": "delete", "tag_id": tag_id, "error": str(error)})

    duplicate_groups = {
        str(group["id"]): group for group in cleanup_duplicate_groups(state, duplicate_cutoff)
    }
    for selection in args.get("duplicates") or []:
        submitted_group_id = str(selection.get("group_id") or selection.get("duplicate_id") or "")
        group = duplicate_groups.get(submitted_group_id)
        if group is None:
            selected_ids = {str(item) for item in selection.get("source_ids") or selection.get("sources") or []}
            target_id = str(selection.get("target_id") or "")
            group = next(
                (item for item in duplicate_groups.values() if target_id in item.get("tag_ids", []) and selected_ids <= set(item.get("tag_ids", []))),
                None,
            )
        if group is None:
            result["failures"].append({"kind": "merge", "group_id": submitted_group_id, "error": "unknown duplicate group"})
            continue
        cleanup_merge_one(local_url, local_headers, expected, group, selection, result, submitted_group_id)

    cleanup_apply_splits(
        local_url,
        local_headers,
        expected,
        state.get("splits") or [],
        args.get("splits"),
        result,
    )

    removed_ids = set(result["deleted"]) | {
        source_id for merge in result["merged"] for source_id in merge["source_ids"]
    }
    if removed_ids:
        for tag in expected.values():
            tag["parents"] = [tag_id for tag_id in tag.get("parents") or [] if tag_id not in removed_ids]
            tag["children"] = [tag_id for tag_id in tag.get("children") or [] if tag_id not in removed_ids]
    state["tags"] = list(expected.values())
    if result["applied_splits"]:
        applied_by_parent = {
            str(item["tag_id"]): {str(candidate_id) for candidate_id in item["candidate_ids"]}
            for item in result["applied_splits"]
        }
        for split in state.get("splits") or []:
            applied_ids = applied_by_parent.get(str(split.get("tag_id")))
            if not applied_ids:
                continue
            split["candidates"] = [
                candidate for candidate in split.get("candidates") or []
                if str(candidate.get("id")) not in applied_ids
            ]
            parent = expected.get(str(split.get("tag_id")))
            if parent is not None:
                split["aliases"] = parent.get("aliases", [])
                split["alias_count"] = len(parent.get("aliases", []))
    state["status"] = "applied"
    state["apply_result"] = result
    state["error"] = None
    write_cleanup_state(server, state)
    return result


def run_operation(args, configuration, local_url, local_headers, server):
    providers = configured_providers(configuration)
    duplicate_cutoff = cleanup_duplicate_cutoff(configuration)
    mode = args.get("mode") or args.get("operation")
    if mode == "scan_status":
        token = args.get("scan_token")
        state = read_scan_state(server, token)
        if state is None:
            return {"scan_token": token, "status": "waiting", "scanned": 0, "total": 0, "row_count": 0, "rows": []}
        path = scan_state_path(server, token)
        resolved = resolve_running_state(state, path)
        if resolved is not state:
            write_scan_state(path, resolved)
            state = resolved
        result = dict(state)
        result["row_count"] = len(state.get("rows") or [])
        if not args.get("include_rows"):
            result.pop("rows", None)
        return result
    if mode == "pull_status":
        state = read_pull_state(server)
        if state is None:
            return {
                "status": "waiting",
                "scanned": 0,
                "total": 0,
                "changed": 0,
                "tags_added": 0,
                "failure_count": 0,
                "row_count": 0,
                "rows": [],
                "error": None,
            }
        path = pull_state_path(server)
        resolved = resolve_running_state(state, path)
        if resolved is not state:
            write_pull_state(server, resolved)
            state = resolved
        result = dict(state)
        result["row_count"] = len(state.get("rows") or [])
        return result
    if mode == "pull":
        return pull_all(local_url, local_headers, providers, server)
    if mode == "cleanup_status":
        token = args.get("cleanup_token") or args.get("scan_token")
        state = read_cleanup_state(server, token)
        path = cleanup_state_path(server)
        resolved = resolve_running_state(state, path) if state is not None else state
        if resolved is not state:
            write_cleanup_state(server, resolved)
            state = resolved
        return cleanup_overview(state, token, duplicate_cutoff)
    if mode == "cleanup_review":
        token = args.get("cleanup_token") or args.get("scan_token")
        state = read_cleanup_state(server, token)
        if state is None:
            raise ValueError("complete a cleanup scan before loading review rows")
        return cleanup_review(state, args, duplicate_cutoff)
    if mode == "cleanup_candidates":
        token = args.get("cleanup_token") or args.get("scan_token")
        if not valid_scan_token(token):
            raise ValueError("cleanup token is required")
        state = read_cleanup_state(server, token)
        if state is None:
            raise ValueError("complete a cleanup scan before loading split candidates")
        return cleanup_candidates(state, args)
    if mode == "cleanup_review_state_get":
        token = args.get("cleanup_token") or args.get("scan_token")
        return cleanup_review_state_get(server, token)
    if mode == "cleanup_review_state_save":
        return cleanup_review_state_save(server, args)
    if mode == "cleanup_scan":
        token = args.get("cleanup_token") or args.get("scan_token")
        return cleanup_scan_all(
            local_url,
            local_headers,
            providers,
            server,
            token,
            duplicate_cutoff,
        )
    if mode == "cleanup_apply":
        return cleanup_apply(local_url, local_headers, server, args, duplicate_cutoff)
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
    if mode == "add_many":
        items = args.get("items")
        validate_add_items(items)
        local_tags = tag_index(graphql(local_url, TAGS_QUERY, headers=local_headers)["findTags"]["tags"])
        return add_many(local_url, local_headers, provider, items, local_tags)
    raise ValueError("unknown operation")


def sync_scene(scene, local_url, local_headers, providers, local_tags, cache, checked_names, canonical_names=None, dead_ids=None):
    if canonical_names is None:
        canonical_names = {}
    if dead_ids is None:
        dead_ids = set()
    existing_ids = {tag["id"] for tag in scene.get("tags") or []}
    names, failures = remote_tag_names(scene, providers, cache)
    unchecked = {
        name.casefold(): name
        for name in names
        if name.casefold() not in local_tags and name.casefold() not in checked_names
    }
    checked_names.update(unchecked)
    merge_tag_index(local_tags, find_local_tags(local_url, local_headers, unchecked.values(), canonical_names))
    merged_ids = merge_tag_ids(existing_ids, names, local_tags)
    unknown_names = {name for name in names if len(local_tags.get(name.casefold(), set())) != 1}
    additions = merged_ids - existing_ids
    additions = {tag_id for tag_id in additions if str(tag_id) not in dead_ids}
    if not additions:
        return [], failures, unknown_names

    def apply_update(tag_ids):
        graphql(local_url, UPDATE_SCENE_MUTATION, {"input": {"id": scene["id"], "tag_ids": sorted(tag_ids)}}, local_headers)

    try:
        apply_update(existing_ids | additions)
    except RuntimeError as error:
        # A tag matched by name may have been deleted or merged while the pull
        # was running (e.g. by the cleanup review or another client), leaving a
        # stale id in the name index. Confirm the reported id is really gone,
        # prune it from the shared index, and retry once without it.
        reported = dead_tag_ids_from_error(str(error))
        dead = {tag_id for tag_id in reported if not tag_exists(local_url, local_headers, tag_id)}
        if not dead:
            raise
        for tag_id in dead:
            dead_ids.add(str(tag_id))
            for names_for_id in local_tags.values():
                names_for_id.discard(tag_id)
        current_ids = {tag_id for tag_id in existing_ids if str(tag_id) not in dead_ids}
        additions = {tag_id for tag_id in merged_ids - existing_ids if str(tag_id) not in dead_ids}
        if current_ids == existing_ids and not additions:
            return [], failures, unknown_names
        apply_update(current_ids | additions)
        existing_ids = current_ids
        merged_ids = current_ids | additions
    added_names = sorted(
        (canonical_names[str(tag_id)] for tag_id in merged_ids - existing_ids if str(tag_id) in canonical_names),
        key=str.casefold,
    )
    return added_names, failures, unknown_names


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
    if target == "all":
        return pull_all(local_url, local_headers, providers, server)
    local_tag_records = graphql(local_url, TAGS_QUERY, headers=local_headers)["findTags"]["tags"]
    local_tags = tag_index(local_tag_records)
    canonical_names = canonical_tag_names(local_tag_records)
    checked_names = set(local_tags)
    summary = {"scanned": 0, "changed": 0, "tags_added": 0, "unknown_remote_tags": [], "failures": []}
    cache = {}
    total = 0

    def process(scene):
        summary["scanned"] += 1
        progress = f"[{summary['scanned']}/{total}]"
        try:
            added_names, failures, unknown_names = sync_scene(
                scene, local_url, local_headers, providers, local_tags, cache, checked_names, canonical_names
            )
            summary["failures"].extend({"scene_id": scene["id"], **failure} for failure in failures)
            summary["unknown_remote_tags"] = sorted(set(summary["unknown_remote_tags"]) | unknown_names)
            if added_names:
                summary["changed"] += 1
                summary["tags_added"] += len(added_names)
                stash_log("i", f"{progress} Updated scene {scene.get('title') or '(untitled)'} (ID {scene['id']}): added {len(added_names)} tag(s)")
            else:
                stash_log("d", f"{progress} Checked scene {scene.get('title') or '(untitled)'} (ID {scene['id']}): no new matching tags")
        except RuntimeError as error:
            summary["failures"].append({"scene_id": scene["id"], "error": str(error)})
            stash_log("e", f"{progress} Failed scene {scene.get('title') or '(untitled)'} (ID {scene['id']}): {error}")
        stash_progress(summary["scanned"], total)

    total = 1
    scene = graphql(local_url, SCENE_QUERY, {"id": target}, local_headers)["findScene"]
    if scene:
        process(scene)
    else:
        stash_progress(1, 1)
    return summary


def self_test():
    global graphql, CLEANUP_TAG_PAGE_SIZE, LOCAL_BATCH_SIZE, SCAN_PAGE_SIZE, write_pull_state

    assert "findScenes(ids: $ids)" in SCENES_BY_IDS_QUERY
    index = tag_index([
        {"id": "1", "name": "Anal", "aliases": ["A"]},
        {"id": "2", "name": "BDSM", "aliases": []},
        {"id": "3", "name": "Ambiguous one", "aliases": ["shared"]},
        {"id": "4", "name": "Ambiguous two", "aliases": ["shared"]},
    ])
    assert merge_tag_ids({"9"}, ["anal", "A", "unknown", "shared"], index) == {"1", "9"}
    progress_marks = []
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
        progress_marks.append,
    )
    assert rows == [
        {"name": "NEW", "scene_count": 2, "scene_ids": ["1", "2"]},
        {"name": "Anal", "scene_count": 1, "scene_ids": ["1"]},
        {"name": "Another", "scene_count": 1, "scene_ids": ["1"]},
    ]
    assert scanned == 2 and failures == []
    assert progress_marks == [1, 2, 3]
    real_graphql = graphql
    calls = []
    remote_urls = []

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
            if variables["filter"]["per_page"] == PAGE_SIZE:
                scenes = [
                    {
                        "id": "1",
                        "title": "Changed",
                        "paths": {"screenshot": "/changed.jpg"},
                        "tags": [],
                        "stash_ids": [{"endpoint": "remote", "stash_id": "a"}],
                    },
                    {
                        "id": "2",
                        "title": "Failed",
                        "paths": {},
                        "tags": [],
                        "stash_ids": [{"endpoint": "other", "stash_id": "b"}],
                    },
                ]
                return {"findScenes": {"count": 2, "scenes": scenes}}
            return {
                "findScenes": {
                    "count": 1,
                    "scenes": [{"id": "1", "stash_ids": [{"endpoint": "remote", "stash_id": "a"}]}],
                }
            }
        if query == SCENES_BY_IDS_QUERY:
            scenes = [
                {"id": "1", "stash_ids": [{"endpoint": "remote", "stash_id": "a"}]},
                {"id": "2", "stash_ids": [{"endpoint": "remote", "stash_id": "a"}]},
            ]
            return {
                "findScenes": {
                    "scenes": [scene for scene in scenes if scene["id"] in variables["ids"]]
                }
            }
        if query.startswith("query RemoteScenes("):
            return {
                f"scene_{index}": {"tags": [{"name": f"Batch{index}"}]}
                for index in range(len(variables))
            }
        if query == REMOTE_TAGS_QUERY:
            remote_urls.append(url)
            return {"findScene": {"tags": [{"name": "New"}, {"name": "Anal"}, {"name": "Blowjob"}]}}
        if query == TAG_SEARCH_QUERY:
            if variables["filter"]["q"].casefold() == "blowjob":
                return {"findTags": {"tags": [{"id": "11", "name": "Oral", "aliases": ["Blowjob"]}]}}
            return {"findTags": {"tags": []}}
        if query == CREATE_TAG_MUTATION:
            return {"tagCreate": {"id": "10"}}
        if query == BULK_UPDATE_SCENES_MUTATION:
            return {"bulkSceneUpdate": [{"id": "1"}]}
        if query == UPDATE_SCENE_MUTATION:
            if variables["input"]["id"] == "2":
                raise RuntimeError("update failed")
            return {"sceneUpdate": {"id": variables["input"]["id"]}}
        raise AssertionError(query)

    graphql = fake_graphql
    try:
        page_result = scan_gaps(
            "local",
            {},
            {"endpoint": "remote", "api_key": "key"},
            2,
        )
        batch_rows, batch_scanned, batch_failures = gap_rows(
            [
                {"id": "1", "stash_ids": [{"endpoint": "remote", "stash_id": "a"}]},
                {"id": "2", "stash_ids": [{"endpoint": "remote", "stash_id": "b"}]},
            ],
            {"remote": {"endpoint": "remote", "api_key": "key"}},
            {},
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
        original_local_batch_size = LOCAL_BATCH_SIZE
        LOCAL_BATCH_SIZE = 1
        try:
            added = add_gap(
                "local",
                {},
                {"endpoint": "remote", "api_key": "key"},
                "New",
                ["1", "2"],
                index,
            )
        finally:
            LOCAL_BATCH_SIZE = original_local_batch_size
        batched = add_many(
            "local",
            {},
            {"endpoint": "remote", "api_key": "key"},
            [
                {"name": "New", "scene_ids": ["1"]},
                {"name": "Broken", "scene_ids": ["999"]},
            ],
            index,
        )
        matched = find_local_tags("local", {}, ["Blowjob", "Missing"])
        pull_writes = []
        real_write_pull_state = write_pull_state

        def capture_pull_state(server, state):
            pull_writes.append(json.loads(json.dumps(state)))
            real_write_pull_state(server, state)

        write_pull_state = capture_pull_state
        try:
            with tempfile.TemporaryDirectory() as pull_dir:
                pull_result = pull_all(
                    "local",
                    {},
                    {
                        "remote": {"endpoint": "remote", "api_key": "key"},
                        "other": {"endpoint": "other", "api_key": "other-key"},
                    },
                    {"Dir": pull_dir},
                )
                pull_state = read_pull_state({"Dir": pull_dir})
                pull_status_result = run_operation(
                    {"mode": "pull_status"},
                    {"general": {"stashBoxes": []}},
                    "local",
                    {},
                    {"Dir": pull_dir},
                )
        finally:
            write_pull_state = real_write_pull_state
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
    assert batch_rows == [
        {"name": "Batch0", "scene_count": 1, "scene_ids": ["1"]},
        {"name": "Batch1", "scene_count": 1, "scene_ids": ["2"]},
    ]
    assert batch_scanned == 2 and batch_failures == []
    assert sum(call[0].startswith("query RemoteScenes(") for call in calls) == 1
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
    assert added == {"created": True, "applied": 2, "failed": 0, "failure_count": 0, "error": None}
    assert batched == {
        "processed": 2,
        "resolved": 1,
        "created": 1,
        "applied": 1,
        "failed": 1,
        "results": [
            {"name": "New", "resolved": True, "created": True, "applied": 1, "failed": 0, "failure_count": 0, "error": None},
            {"name": "Broken", "resolved": False, "created": False, "applied": 0, "failed": 1, "failure_count": 0, "error": "none of the selected scenes are linked to the configured provider"},
        ],
    }
    assert matched == {"oral": {"11"}, "blowjob": {"11"}}
    assert pull_result == {
        "scanned": 2,
        "changed": 1,
        "tags_added": 2,
        "unknown_remote_tags": ["New"],
        "failures": [{"scene_id": "2", "error": "update failed"}],
        "status": "completed",
        "failure_count": 1,
        "row_count": 2,
    }
    assert pull_state == {
        "status": "completed",
        "scanned": 2,
        "total": 2,
        "changed": 1,
        "tags_added": 2,
        "failure_count": 1,
        "rows": [
            {
                "scene_id": "1",
                "title": "Changed",
                "screenshot": "/changed.jpg",
                "added_tags": ["Anal", "Oral"],
            },
            {
                "scene_id": "2",
                "title": "Failed",
                "screenshot": None,
                "added_tags": [],
                "error": "update failed",
            },
        ],
        "error": None,
        "pid": os.getpid(),
    }
    assert any(state["status"] == "running" and len(state["rows"]) == 1 for state in pull_writes)
    assert "other" in remote_urls
    assert pull_status_result == {**pull_state, "row_count": 2}

    # stale tag ids learned from scenes_tags foreign-key failures are pruned so
    # the pull recovers instead of failing every affected scene
    assert dead_tag_ids_from_error(
        "error executing `INSERT INTO scenes_tags (scene_id, tag_id) VALUES (?, ?) "
        "ON CONFLICT (scene_id, tag_id) DO NOTHING` [[3467 253]]: FOREIGN KEY constraint failed"
    ) == {"253"}
    assert dead_tag_ids_from_error("update failed") == set()
    stale_calls = []

    def stale_graphql(url, query, variables=None, headers=None):
        stale_calls.append(query)
        if query == TAGS_QUERY:
            return {"findTags": {"tags": [
                {"id": "1", "name": "Anal", "aliases": []},
                {"id": "12", "name": "Oral", "aliases": []},
            ]}}
        if query == SCENES_QUERY:
            return {"findScenes": {"count": 2, "scenes": [
                {"id": "1", "title": "A", "paths": {}, "tags": [], "stash_ids": [{"endpoint": "remote", "stash_id": "a"}]},
                {"id": "2", "title": "B", "paths": {}, "tags": [], "stash_ids": [{"endpoint": "remote", "stash_id": "b"}]},
            ]}}
        if query == REMOTE_TAGS_QUERY:
            return {"findScene": {"tags": [{"name": "Anal"}, {"name": "Oral"}]}}
        if query.startswith("query RemoteScenes("):
            return {
                f"scene_{index}": {"tags": [{"name": "Anal"}, {"name": "Oral"}]}
                for index in range(len(variables))
            }
        if query == TAG_SEARCH_QUERY:
            return {"findTags": {"tags": []}}
        if query == TAG_EXISTS_QUERY:
            return {"findTag": {"id": "1"} if variables["id"] == "1" else None}
        if query == UPDATE_SCENE_MUTATION:
            if variables["input"]["tag_ids"] == ["1", "12"]:
                raise RuntimeError(
                    "error executing `INSERT INTO scenes_tags (scene_id, tag_id) VALUES (?, ?) "
                    "ON CONFLICT (scene_id, tag_id) DO NOTHING` [[1 12]]: FOREIGN KEY constraint failed"
                )
            return {"sceneUpdate": {"id": variables["input"]["id"]}}
        raise AssertionError(query)

    real_stale_graphql = graphql
    graphql = stale_graphql
    try:
        with tempfile.TemporaryDirectory() as stale_dir:
            stale_result = pull_all("local", {}, {"remote": {"endpoint": "remote", "api_key": "key"}}, {"Dir": stale_dir})
    finally:
        graphql = real_stale_graphql
    assert stale_result["changed"] == 2
    assert stale_result["tags_added"] == 2
    assert stale_result["failure_count"] == 0
    assert stale_result["unknown_remote_tags"] == ["Oral"]
    assert stale_calls.count(TAG_EXISTS_QUERY) == 1
    assert stale_calls.count(UPDATE_SCENE_MUTATION) == 3  # failed + retried for scene 1, direct success for scene 2

    # orphaned 'running' state (writer process gone) resolves to aborted
    with tempfile.TemporaryDirectory() as orphan_dir:
        orphan_state = {"status": "running", "scanned": 1, "total": 2, "pid": 99999999}
        write_pull_state({"Dir": orphan_dir}, orphan_state)
        orphan_result = run_operation(
            {"mode": "pull_status"},
            {"general": {"stashBoxes": []}},
            "local",
            {},
            {"Dir": orphan_dir},
        )
        assert orphan_result["status"] == "aborted"
        assert orphan_result["error"] == "The job was stopped before it finished."
        assert read_pull_state({"Dir": orphan_dir})["status"] == "aborted"
        # a live writer (this test process) is still reported as running
        live_state = {"status": "running", "pid": os.getpid()}
        live_path = Path(orphan_dir) / "tag-organizer" / "pull.json"
        write_pull_state({"Dir": orphan_dir}, live_state)
        assert writer_alive(live_state, live_path) is True
        # state without a pid falls back to file age
        assert writer_alive({"status": "running"}, live_path) is True
        assert resolve_running_state(live_state, live_path)["status"] == "running"
    bulk_calls = [call for call in calls if call[0] == BULK_UPDATE_SCENES_MUTATION]
    assert [call[1]["input"]["ids"] for call in bulk_calls] == [["1"], ["2"], ["1"]]
    assert hook_target({"args": {}}, {}) == "all"
    assert hook_target({"args": {"hookContext": {"type": "Tag.Create.Post"}}}, {}) is None
    assert hook_target({"args": {"hookContext": {"type": "Scene.Update.Post", "id": "5", "inputFields": ["stash_ids"]}}}, {"syncOnStashIdChange": True}) == "5"
    assert cleanup_duplicate_cutoff({"plugins": {PLUGIN_ID: {}}}) == 0.85
    assert cleanup_duplicate_cutoff({"plugins": {PLUGIN_ID: {"duplicateSimilarityCutoff": 0.87}}}) == 0.87
    try:
        cleanup_duplicate_cutoff({"plugins": {PLUGIN_ID: {"duplicateSimilarityCutoff": 0.4}}})
    except ValueError:
        pass
    else:
        raise AssertionError("invalid duplicate similarity cutoff was accepted")
    assert normalized_tag_name("Crème brûlée") == "cremebrulee"
    fuzzy_tags = [
        cleanup_tag_snapshot({"id": "1", "name": "BDSM", "aliases": [], "stash_ids": [], "parents": [], "children": [], "scene_count": 4}),
        cleanup_tag_snapshot({"id": "2", "name": "bdsm ", "aliases": [], "stash_ids": [], "parents": [], "children": [], "scene_count": 1}),
        cleanup_tag_snapshot({"id": "3", "name": "Oral", "aliases": [], "stash_ids": [], "parents": [], "children": [], "scene_count": 1}),
    ]
    assert fuzzy_duplicate_groups(fuzzy_tags)[0]["tag_ids"] == ["1", "2"]
    drift_groups = fuzzy_duplicate_groups([
        {"id": "a", "name": "aaaa", "usage": 3, "aliases": [], "counts": {}},
        {"id": "b", "name": "aaab", "usage": 2, "aliases": [], "counts": {}},
        {"id": "c", "name": "aabb", "usage": 1, "aliases": [], "counts": {}},
    ], cutoff=0.72)
    assert [tag["id"] for tag in drift_groups[0]["tags"]] == ["a", "b"]
    assert all(tag["id"] != "c" for group in drift_groups for tag in group["tags"])
    runtime_tags = [
        {"id": "a", "name": "aaaa", "usage": 3, "aliases": [], "counts": {}},
        {"id": "b", "name": "aaab", "usage": 2, "aliases": [], "counts": {}},
    ]
    runtime_state = {
        "tags": runtime_tags,
        "duplicate_edges": duplicate_similarity_edges(runtime_tags),
    }
    assert len(cleanup_duplicate_groups(runtime_state, 0.72)) == 1
    assert cleanup_duplicate_groups(runtime_state, 0.8) == []
    assert cleanup_review(runtime_state, {"section": "duplicates"}, 0.72)["similarity_cutoff"] == 0.72
    hierarchy_usage = cleanup_hierarchy_usage([
        {"id": "parent", "usage": 0, "children": ["child"]},
        {"id": "child", "usage": 3, "children": []},
    ])
    assert hierarchy_usage == {"parent": 3, "child": 3}
    hierarchy_review = cleanup_review({"tags": [
        {"id": "parent", "name": "Exclude", "usage": 0, "children": ["child"]},
        {"id": "child", "name": "Used child", "usage": 3, "children": []},
        {"id": "unused", "name": "Unused", "usage": 0, "children": []},
    ]}, {"section": "tags"})
    assert [item["id"] for item in hierarchy_review["items"]] == ["unused"]
    assert remote_id_conflicts([
        {"stash_ids": [{"endpoint": "remote", "stash_id": "a"}]},
        {"stash_ids": [{"endpoint": "remote", "stash_id": "b"}]},
    ]) == {"remote": ["a", "b"]}
    hierarchy_tags = [
        {"id": "p", "parents": [], "children": ["a"]},
        {"id": "a", "parents": ["p"], "children": []},
        {"id": "b", "parents": [], "children": ["c"]},
        {"id": "c", "parents": ["b"], "children": []},
    ]
    assert union_hierarchy(hierarchy_tags, "a", ["b"]) == {"parent_ids": ["p"], "child_ids": ["c"]}
    cyclic_tags = [
        {"id": "p", "parents": [], "children": ["a"]},
        {"id": "a", "parents": ["p"], "children": ["p"]},
    ]
    try:
        union_hierarchy(cyclic_tags, "a", [])
    except ValueError:
        pass
    else:
        raise AssertionError("hierarchy cycle was accepted")

    cleanup_calls = []
    real_cleanup_graphql = graphql

    def cleanup_record(tag_id, name, aliases=None, parents=None, children=None):
        return {
            "id": str(tag_id),
            "name": name,
            "aliases": aliases or [],
            "stash_ids": [],
            "scene_count": 1,
            "scene_marker_count": 0,
            "image_count": 0,
            "gallery_count": 0,
            "performer_count": 0,
            "studio_count": 0,
            "group_count": 0,
            "parents": [{"id": item} for item in (parents or [])],
            "children": [{"id": item} for item in (children or [])],
        }

    cleanup_store = {
        "1": cleanup_record("1", "Parent", ["Alias"]),
        "2": cleanup_record("2", "Child"),
        "3": cleanup_record("3", "Marker primary"),
        "4": cleanup_record("4", "Parent 2", ["Alias 2"]),
    }
    cleanup_scene = {"id": "scene", "tags": [{"id": "1"}], "stash_ids": []}

    def fake_cleanup_graphql(url, query, variables=None, headers=None):
        cleanup_calls.append((query, variables))
        if query == CLEANUP_TAGS_QUERY:
            records = list(cleanup_store.values())
            per_page = variables["filter"]["per_page"]
            start = (variables["filter"]["page"] - 1) * per_page
            return {"findTags": {"count": len(records), "tags": records[start:start + per_page]}}
        if query == CLEANUP_TAGS_BY_IDS_QUERY:
            return {"findTags": {"tags": [cleanup_store[tag_id] for tag_id in variables["ids"] if tag_id in cleanup_store]}}
        if query == CLEANUP_SCENES_BY_TAGS_QUERY:
            scenes = [
                {"id": "scene-1", "tags": [{"id": "1"}], "stash_ids": []},
                {"id": "scene-2", "tags": [{"id": "1"}, {"id": "4"}], "stash_ids": []},
            ]
            per_page = variables["filter"]["per_page"]
            start = (variables["filter"]["page"] - 1) * per_page
            return {"findScenes": {"count": len(scenes), "scenes": scenes[start:start + per_page]}}
        if query == TAG_DESTROY_MUTATION:
            tag_id = variables["input"]["id"]
            if tag_id == "3":
                raise RuntimeError("marker is primary for a scene marker")
            cleanup_store.pop(tag_id, None)
            return {"tagDestroy": True}
        if query == TAG_UPDATE_MUTATION:
            values = variables["input"]
            tag = cleanup_store[values["id"]]
            if "parent_ids" in values:
                tag["parents"] = [{"id": item} for item in values["parent_ids"]]
                for parent_id in values["parent_ids"]:
                    cleanup_store[parent_id]["children"] = [{"id": values["id"]}]
            if "aliases" in values:
                tag["aliases"] = values["aliases"]
            tag["children"] = [{"id": "2"}] if tag["id"] == "1" and tag["aliases"] == [] else tag["children"]
            return {"tagUpdate": {"id": values["id"]}}
        if query == SCENE_QUERY:
            return {"findScene": cleanup_scene}
        if query == UPDATE_SCENE_MUTATION:
            cleanup_scene["tags"] = [{"id": tag_id} for tag_id in variables["input"]["tag_ids"]]
            return {"sceneUpdate": {"id": cleanup_scene["id"]}}
        if query == TAGS_MERGE_MUTATION:
            values = variables["input"]["values"]
            destination_id = variables["input"]["destination"]
            for source_id in variables["input"]["source"]:
                cleanup_store.pop(source_id, None)
            tag = cleanup_store[destination_id]
            tag["aliases"] = values.get("aliases", tag["aliases"])
            tag["stash_ids"] = values.get("stash_ids", tag["stash_ids"])
            tag["parents"] = [{"id": item} for item in values.get("parent_ids") or []]
            tag["children"] = [{"id": item} for item in values.get("child_ids") or []]
            return {"tagsMerge": {"id": destination_id}}
        raise AssertionError(query)

    graphql = fake_cleanup_graphql
    try:
        original_cleanup_tag_page_size = CLEANUP_TAG_PAGE_SIZE
        original_scan_page_size = SCAN_PAGE_SIZE
        CLEANUP_TAG_PAGE_SIZE = 1
        SCAN_PAGE_SIZE = 1
        cleanup_progress = []
        try:
            cleanup_plan_result = cleanup_plan(
                "local",
                {},
                {},
                lambda scanned, total, phase, detail="": cleanup_progress.append((scanned, total, phase, detail)),
            )
        finally:
            CLEANUP_TAG_PAGE_SIZE = original_cleanup_tag_page_size
            SCAN_PAGE_SIZE = original_scan_page_size
        assert len(cleanup_plan_result["tags"]) == 4
        assert "duplicate_edges" in cleanup_plan_result
        assert cleanup_progress[:4] == [(1, 4, "tags", ""), (2, 4, "tags", ""), (3, 4, "tags", ""), (4, 4, "tags", "")]
        assert cleanup_progress[-5:] == [
            (1, 2, "plan", "Loading remote tags 1/2"),
            (2, 2, "plan", "Loading remote tags 2/2"),
            (0.75, 2, "plan", "Parent (1/2 scenes)"),
            (1.0, 2, "plan", "Parent (2/2 scenes)"),
            (2.0, 2, "plan", "Parent 2 (1/1 scenes)"),
        ]
        assert sum(query == CLEANUP_SCENES_BY_TAGS_QUERY for query, _ in cleanup_calls) == 2
        with tempfile.TemporaryDirectory() as cleanup_dir:
            cleanup_snapshot_tags = [cleanup_tag_snapshot(tag) for tag in cleanup_store.values()]
            cleanup_snapshot_tags[2]["usage"] = 0
            cleanup_state = {
                "cleanup_token": "cleanup-test",
                "status": "completed",
                "tags": cleanup_snapshot_tags,
                "duplicates": [{
                    "id": "duplicate-1-2",
                    "score": 0.9,
                    "tag_ids": ["1", "2"],
                    "tags": cleanup_snapshot_tags[:2],
                    "remote_conflicts": {"remote": ["a", "b"]},
                }],
                "splits": [{
                    "tag_id": "1",
                    "tag": cleanup_snapshot_tags[0],
                    "aliases": ["Alias"],
                    "alias_count": 1,
                    "scene_count": 1,
                    "candidates": [{
                        "id": "candidate",
                        "alias": "Alias",
                        "child_name": "Child",
                        "scene_ids": ["scene"],
                        "action": "child-only",
                    }],
                }],
            }
            write_cleanup_state({"Dir": cleanup_dir}, cleanup_state)
            cleanup_status = run_operation(
                {"mode": "cleanup_status", "cleanup_token": "cleanup-test"},
                {"general": {"stashBoxes": []}},
                "local",
                {},
                {"Dir": cleanup_dir},
            )
            recovered_status = run_operation(
                {"mode": "cleanup_status"},
                {"general": {"stashBoxes": []}},
                "local",
                {},
                {"Dir": cleanup_dir},
            )
            assert cleanup_status == recovered_status
            assert cleanup_status["cleanup_token"] == "cleanup-test"
            assert cleanup_status["tag_count"] == 4 and cleanup_status["duplicate_count"] == 1 and cleanup_status["split_count"] == 1
            assert not {"tags", "duplicates", "splits", "failures", "apply_result"} & cleanup_status.keys()
            tag_review = cleanup_review(cleanup_state, {"section": "tags", "page": 1, "per_page": 1})
            assert tag_review == {
                "items": [{
                    **{key: cleanup_snapshot_tags[2].get(key) for key in ("id", "name", "aliases", "usage", "counts")},
                    "direct_usage": 0,
                }],
                "page": 1,
                "per_page": 1,
                "total": 1,
            }
            selected_review = cleanup_review(cleanup_state, {
                "section": "tags", "filter": "selected", "selected_ids": ["2"], "query": "child", "sort": "name_asc"
            })
            assert selected_review["total"] == 1 and selected_review["items"][0]["id"] == "2"
            duplicate_review = cleanup_review(cleanup_state, {"section": "duplicates", "filter": "conflicts"})
            assert duplicate_review["items"][0]["conflicts"] == [{"endpoint": "remote", "id_count": 2}]
            assert set(duplicate_review["items"][0]["tags"][0]) == {
                "id", "name", "aliases", "usage", "remote_endpoints", "conflict_endpoints",
            }
            split_review = run_operation(
                {"mode": "cleanup_review", "cleanup_token": "cleanup-test", "section": "splits", "page": 1, "per_page": 50},
                {"general": {"stashBoxes": []}},
                "local",
                {},
                {"Dir": cleanup_dir},
            )
            assert split_review["items"] == [{
                "tag_id": "1", "name": "Parent", "aliases": ["Alias"], "alias_count": 1, "scene_count": 1, "candidate_count": 1,
            }]
            assert "candidates" not in split_review["items"][0] and "scene_ids" not in json.dumps(split_review)
            candidate_review = run_operation(
                {"mode": "cleanup_candidates", "cleanup_token": "cleanup-test", "parent_tag_id": "1", "page": 1, "per_page": 100},
                {"general": {"stashBoxes": []}},
                "local",
                {},
                {"Dir": cleanup_dir},
            )
            assert candidate_review["parent"] == {
                "tag_id": "1", "name": "Parent", "aliases": ["Alias"], "scene_count": 1, "candidate_count": 1,
            }
            assert candidate_review["items"][0]["evidence_scene_ids"] == ["scene"]
            assert "scene_ids" not in candidate_review["items"][0]
            capped_evidence = cleanup_candidates({"splits": [{
                "tag_id": "1", "tag": {"name": "Parent", "aliases": ["Alias"]},
                "candidates": [{"id": "many", "scene_ids": ["1", "2", "3", "4"], "scene_count": 4}],
            }]}, {"parent_tag_id": "1"})
            assert capped_evidence["items"][0]["evidence_scene_ids"] == ["1", "2", "3"]
            assert capped_evidence["items"][0]["scene_count"] == 4
            try:
                cleanup_candidates(cleanup_state, {"parent_tag_id": "1", "per_page": 101})
            except ValueError:
                pass
            else:
                raise AssertionError("unbounded cleanup candidate page was accepted")
            try:
                cleanup_review(cleanup_state, {"section": "tags", "per_page": 101})
            except ValueError:
                pass
            else:
                raise AssertionError("unbounded cleanup review page was accepted")
            bounded_review = cleanup_review(
                {"tags": [{"id": str(index), "name": "Tag {}".format(index), "usage": 0} for index in range(150)]},
                {"section": "tags", "filter": "all", "per_page": 100},
            )
            assert len(bounded_review["items"]) == 100 and bounded_review["total"] == 150
            assert read_cleanup_state({"Dir": cleanup_dir})["splits"][0]["candidates"][0]["scene_ids"] == ["scene"]
            default_review_state = run_operation(
                {"mode": "cleanup_review_state_get", "cleanup_token": "cleanup-test"},
                {"general": {"stashBoxes": []}}, "local", {}, {"Dir": cleanup_dir},
            )
            assert default_review_state["junk_ids"] == [] and default_review_state["section"] == "tags"
            saved_review_state = run_operation(
                {
                    "mode": "cleanup_review_state_save",
                    "cleanup_token": "cleanup-test",
                    "junk_ids": ["3", 3],
                    "duplicates": {"duplicate-1-2": {"target_id": "1", "source_ids": ["2"], "override_remote_ids": True, "has_conflicts": True}},
                    "splits": {"1": [{"candidate_id": "candidate", "action": "child-only", "child_name": "Child", "remove_alias": True, "scene_count": 2}]},
                    "section": "splits",
                    "split_parent_id": "1",
                    "views": {
                        "tags": {"page": 1, "query": "", "filter": "unused", "sort": "usage_desc"},
                        "duplicates": {"page": 1, "per_page": 1, "query": "", "filter": "all", "sort": "score_desc"},
                        "splits": {"page": 2, "query": "child", "filter": "all", "sort": "name_asc"},
                    },
                },
                {"general": {"stashBoxes": []}}, "local", {}, {"Dir": cleanup_dir},
            )
            assert saved_review_state == {"ok": True}
            reloaded_review_state = cleanup_review_state_get({"Dir": cleanup_dir}, "cleanup-test")
            assert reloaded_review_state["junk_ids"] == ["3"]
            assert reloaded_review_state["duplicates"]["duplicate-1-2"]["source_ids"] == ["2"]
            assert reloaded_review_state["duplicates"]["duplicate-1-2"]["has_conflicts"] is True
            assert reloaded_review_state["splits"]["1"][0]["scene_count"] == 2
            assert reloaded_review_state["split_parent_id"] == "1"
            assert reloaded_review_state["views"]["splits"]["page"] == 2
            assert reloaded_review_state["views"]["duplicates"]["per_page"] == 1
            assert "per_page" not in reloaded_review_state["views"]["tags"]
            assert read_cleanup_review_state({"Dir": cleanup_dir}, "other-token") is None
            try:
                cleanup_review_state_save({"Dir": cleanup_dir}, {"cleanup_token": "cleanup-test", "junk_ids": "not-a-list"})
            except ValueError:
                pass
            else:
                raise AssertionError("invalid junk_ids shape was accepted")
            try:
                cleanup_review_state_save({"Dir": cleanup_dir}, {
                    "cleanup_token": "cleanup-test",
                    "splits": {"1": [{"candidate_id": "candidate", "action": "not-a-real-action"}]},
                })
            except ValueError:
                pass
            else:
                raise AssertionError("invalid split action was accepted")
            try:
                cleanup_review_state_save({"Dir": cleanup_dir}, {"cleanup_token": "missing-token-000000"})
            except ValueError:
                pass
            else:
                raise AssertionError("review state was saved without a completed cleanup plan")
            try:
                cleanup_apply("local", {}, {"Dir": cleanup_dir}, {"cleanup_token": "cleanup-test"})
            except ValueError as error:
                assert str(error) == "database backup required before cleanup apply"
            else:
                raise AssertionError("cleanup applied without a backup")
            applied = cleanup_apply(
                "local",
                {},
                {"Dir": cleanup_dir},
                {
                    "cleanup_token": "cleanup-test",
                    "backup_confirmed": True,
                    "junk_ids": ["3"],
                    "splits": [{"tag_id": "1", "candidates": [{"candidate_id": "candidate", "action": "child-only"}] }],
                },
            )
            assert applied["deleted"] == []
            assert applied["failures"][0]["kind"] == "delete"
            assert applied["applied_splits"] == [{"tag_id": "1", "candidate_ids": ["candidate"]}]
            assert "3" in cleanup_store
            assert cleanup_store["2"]["parents"] == [{"id": "1"}]
            assert cleanup_scene["tags"] == [{"id": "2"}]
            assert cleanup_store["1"]["aliases"] == []
            state_after_first_apply = read_cleanup_state({"Dir": cleanup_dir}, "cleanup-test")
            assert state_after_first_apply["status"] == "applied"
            assert {tag["id"] for tag in state_after_first_apply["tags"]} == {"1", "2", "3", "4"}
            assert next(tag for tag in state_after_first_apply["tags"] if tag["id"] == "1")["aliases"] == []
            assert state_after_first_apply["splits"][0]["candidates"] == []
            assert state_after_first_apply["splits"][0]["aliases"] == []
            # Applying once must not force a rescan: review state can still be saved and a
            # second round can merge/delete/split further tags against the same plan.
            resaved_review_state = cleanup_review_state_save(
                {"Dir": cleanup_dir}, {"cleanup_token": "cleanup-test", "junk_ids": []}
            )
            assert resaved_review_state == {"ok": True}
            second_apply = cleanup_apply(
                "local",
                {},
                {"Dir": cleanup_dir},
                {
                    "cleanup_token": "cleanup-test",
                    "backup_confirmed": True,
                    "duplicates": [{"group_id": "duplicate-1-2", "target_id": "1", "source_ids": ["2"]}],
                },
            )
            assert second_apply["failures"] == []
            assert second_apply["merged"] == [{"group_id": "duplicate-1-2", "target_id": "1", "source_ids": ["2"]}]
            state_after_second_apply = read_cleanup_state({"Dir": cleanup_dir}, "cleanup-test")
            assert {tag["id"] for tag in state_after_second_apply["tags"]} == {"1", "3", "4"}
            assert "2" not in cleanup_store
    finally:
        graphql = real_cleanup_graphql
    assert any(call[0] == TAG_DESTROY_MUTATION for call in cleanup_calls)
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
