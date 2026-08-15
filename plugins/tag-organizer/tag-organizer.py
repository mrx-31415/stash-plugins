#!/usr/bin/env python3
"""Find and fill local tag gaps from linked Stash-box scenes."""

import json
import os
import difflib
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PLUGIN_ID = "tag-organizer"
PAGE_SIZE = 100
SCAN_PAGE_SIZE = 25
REMOTE_BATCH_SIZE = 200
CLEANUP_TAG_PAGE_SIZE = 25
CLEANUP_SCENE_PAGE_SIZE = 1000
LOCAL_BATCH_SIZE = 100
SCAN_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
FUZZY_CUTOFF = 0.72
DUPLICATE_FUZZY_CUTOFF = 0.85
DUPLICATE_SIMILARITY_FLOOR = 0.70
LINK_NEAR_DUPLICATE_CUTOFF = 0.85
LINK_MAX_CANDIDATES = 10

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
    scenes { id tags { id } stash_ids { endpoint stash_id } }
  }
}
"""
REMOTE_TAGS_QUERY = """
query RemoteScene($id: ID!) { findScene(id: $id) { tags { name } } }
"""
LINK_REMOTE_TAG_QUERY = """
query RemoteTag($name: String!) { findTag(name: $name) {
  id name aliases description deleted
} }
"""
LINK_SEARCH_REMOTE_QUERY = """
query RemoteTagSearch($term: String!, $limit: Int) {
  searchTag(term: $term, limit: $limit) { id }
}
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


def link_state_path(server, token):
    if not valid_scan_token(token):
        raise ValueError("invalid link scan token")
    config_dir = server.get("Dir")
    if not config_dir:
        raise RuntimeError("Stash config directory is unavailable")
    return Path(config_dir) / "tag-organizer" / f"link-{token}.json"


def write_link_state(server, state):
    write_scan_state(link_state_path(server, state.get("link_token") or ""), state)


def read_link_state(server, token):
    if not valid_scan_token(token):
        return None
    path = link_state_path(server, token)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


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


_NAME_STATS = {}
_RATIO_CACHE = {}


def name_stats(name):
    """Per-name (normalized, char mask, char multiset, length) for exact gating."""
    key = name.casefold()
    stats = _NAME_STATS.get(key)
    if stats is None:
        normalized = normalized_tag_name(name)
        mask = 0
        counts = {}
        for char in normalized:
            mask |= 1 << (ord(char) & 63)
            counts[char] = counts.get(char, 0) + 1
        stats = (normalized, mask, counts, len(normalized))
        _NAME_STATS[key] = stats
    return stats


def gated_fuzzy_similarity(left, right, cutoff):
    """difflib ratio when it can reach `cutoff`, else None.

    Exact gate: pairs whose length ratio, disjoint character mask, or shared-
    character multiset upper bound already prove the ratio is below `cutoff`
    never reach difflib, and computed ratios are memoized (SequenceMatcher's
    ratio is symmetric, so each unordered name pair is computed once).
    """
    left_norm, left_mask, left_counts, left_len = name_stats(left)
    right_norm, right_mask, right_counts, right_len = name_stats(right)
    if not left_norm or not right_norm:
        return None
    if left_norm == right_norm:
        return 1.0
    if not (left_mask & right_mask):
        return None
    if 2 * min(left_len, right_len) / (left_len + right_len) < cutoff:
        return None
    shared = 0
    for char, left_count in left_counts.items():
        right_count = right_counts.get(char)
        if right_count:
            shared += left_count if left_count < right_count else right_count
    if 2 * shared / (left_len + right_len) < cutoff:
        return None
    pair = (left_norm, right_norm) if left_norm <= right_norm else (right_norm, left_norm)
    value = _RATIO_CACHE.get(pair)
    if value is None:
        value = difflib.SequenceMatcher(None, left_norm, right_norm).ratio()
        _RATIO_CACHE[pair] = value
    return value if value >= cutoff else None


def duplicate_similarity_edges(tags, minimum=DUPLICATE_SIMILARITY_FLOOR, progress_callback=None):
    """Calculate compact direct-similarity edges once for runtime filtering.

    Optimized to stay exact: pairs that cannot reach the floor are skipped via
    (1) a length gate, (2) a 64-bit character-mask disjointness check, and
    (3) the exact multiset upper bound on SequenceMatcher's matched-character
    count (it can never match more characters than the two strings share, so
    ``2 * shared / (len_a + len_b) < minimum`` proves the pair is below the
    floor without running difflib). Repeated name pairs are memoized.
    """
    snapshots = sorted(
        (cleanup_tag_snapshot(tag) if "counts" not in tag else tag for tag in tags),
        key=lambda tag: (-int(tag.get("usage") or 0), tag["name"].casefold()),
    )
    normalized = [
        [
            normalized_tag_name(name)
            for name in [tag["name"], *tag.get("aliases", [])]
            if normalized_tag_name(name)
        ]
        for tag in snapshots
    ]
    masks = []
    multisets = []
    lengths = []
    for tag_names in normalized:
        tag_mask = 0
        tag_counts = []
        tag_lengths = []
        for name in tag_names:
            mask = 0
            counts = {}
            for char in name:
                mask |= 1 << (ord(char) & 63)
                counts[char] = counts.get(char, 0) + 1
            tag_mask |= mask
            tag_counts.append(counts)
            tag_lengths.append(len(name))
        masks.append(tag_mask)
        multisets.append(tag_counts)
        lengths.append(tag_lengths)
    edges = []
    ratio_cache = {}
    # ponytail: store only scores >= 0.70; lower scores are too noisy and dense to tune safely.
    progress_step = max(1, len(snapshots) // 100)
    if progress_callback:
        progress_callback(0, len(snapshots))
    for index, anchor in enumerate(snapshots):
        for candidate_index, candidate in enumerate(snapshots[index + 1:], index + 1):
            if not (masks[index] & masks[candidate_index]):
                continue
            score = 0
            for left_name, left_counts, left_len in zip(normalized[index], multisets[index], lengths[index]):
                for right_name, right_counts, right_len in zip(
                    normalized[candidate_index], multisets[candidate_index], lengths[candidate_index]
                ):
                    if 2 * min(left_len, right_len) / (left_len + right_len) < minimum:
                        continue
                    shared = 0
                    for char, left_count in left_counts.items():
                        right_count = right_counts.get(char)
                        if right_count:
                            shared += left_count if left_count < right_count else right_count
                    if 2 * shared / (left_len + right_len) < minimum:
                        continue
                    pair = (left_name, right_name) if left_name <= right_name else (right_name, left_name)
                    value = ratio_cache.get(pair)
                    if value is None:
                        value = difflib.SequenceMatcher(None, left_name, right_name).ratio()
                        ratio_cache[pair] = value
                    if value > score:
                        score = value
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
    scene_filter = {"tags": {"value": sorted(parent_ids), "modifier": "INCLUDES"}}
    try:
        # one request for the whole result set; the filter dominates the query
        # cost, so paging through it 25 scenes at a time is orders of magnitude
        # slower than fetching everything at once
        batch = graphql(
            local_url,
            CLEANUP_SCENES_BY_TAGS_QUERY,
            {"filter": {"per_page": -1}, "scene_filter": scene_filter},
            local_headers,
        )["findScenes"].get("scenes") or []
    except RuntimeError:
        # per_page -1 unsupported or the result is too large for one request:
        # fall back to paging (the query carries no count, so stop on an empty page)
        batch = []
        page = 1
        while True:
            result = graphql(
                local_url,
                CLEANUP_SCENES_BY_TAGS_QUERY,
                {
                    "filter": {"page": page, "per_page": CLEANUP_SCENE_PAGE_SIZE},
                    "scene_filter": scene_filter,
                },
                local_headers,
            )["findScenes"]
            page_batch = result.get("scenes") or []
            if not page_batch:
                break
            batch.extend(page_batch)
            page += 1
    if progress_callback:
        progress_callback(len(batch), len(batch))
    for scene in batch:
        matching_parents = set()
        for tag in scene.get("tags") or []:
            matching_parents.update(ancestors.get(str(tag["id"]), {str(tag["id"])}))
        for parent_id in matching_parents & parent_ids:
            scenes_by_parent[parent_id].append(scene)
    return scenes_by_parent


def cleanup_candidate_id(parent_id, alias, remote_name):
    return "split-{}-{}-{}".format(
        parent_id,
        normalized_tag_name(alias) or "alias",
        normalized_tag_name(remote_name) or "manual",
    )


def cleanup_scene_evidence(scenes, providers, remote_cache, progress_callback=None):
    def report_prefetch_progress(done, total):
        if progress_callback:
            # chunk counts so the tail of the prefetch stays visible instead of
            # being capped at the scene total
            progress_callback(done, total)

    prefetch_remote_tag_names(scenes, providers, remote_cache, report_prefetch_progress)
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
            score = gated_fuzzy_similarity(alias, observation["name"], FUZZY_CUTOFF)
            if score is None:
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


def prefetch_remote_tag_names(scenes, providers, cache, progress_callback=None):
    pending = {}
    for scene in scenes:
        for stash_id in scene.get("stash_ids") or []:
            endpoint = stash_id.get("endpoint")
            if endpoint not in providers:
                continue
            cache_key = (endpoint, stash_id["stash_id"])
            if cache_key not in cache:
                pending.setdefault(endpoint, {})[cache_key] = stash_id["stash_id"]

    def chunks_for(count):
        chunks = (count + REMOTE_BATCH_SIZE - 1) // REMOTE_BATCH_SIZE
        return max(0, chunks - 1) if count % REMOTE_BATCH_SIZE == 1 else chunks

    total_chunks = sum(chunks_for(len(ids)) for ids in pending.values())
    progress_lock = threading.Lock()
    done = [0]

    def fetch_endpoint(endpoint, scene_ids):
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
            with progress_lock:
                done[0] += 1
                if progress_callback:
                    progress_callback(done[0], total_chunks)

    # providers are different servers, so their prefetch streams run in
    # parallel; each provider is still hit sequentially so this does not add
    # any load per provider, it only halves the wall time
    if len(pending) > 1:
        workers = [
            threading.Thread(target=fetch_endpoint, args=(endpoint, scene_ids))
            for endpoint, scene_ids in pending.items()
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
    else:
        for endpoint, scene_ids in pending.items():
            fetch_endpoint(endpoint, scene_ids)


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
        last_persist = [0.0, -1]

        def report_cleanup_progress(scanned, total, phase, detail=""):
            state.update({
                "scanned": scanned,
                "total": total,
                "progress_phase": phase,
                "progress_detail": detail,
            })
            now = time.time()
            # progress is reported per scene for tags with tens of thousands of
            # scenes; persisting the full state file and emitting a stderr
            # progress line that often is orders of magnitude slower than the
            # work itself, so throttle both to ~2/s
            now_delta = now - last_persist[0]
            scan_delta = abs(float(scanned) - last_persist[1])
            if now_delta >= 0.5 or scan_delta >= 500:
                write_cleanup_state(server, state)
                stash_progress(scanned, total)
                last_persist[0] = now
                last_persist[1] = float(scanned)

        plan = cleanup_plan(local_url, local_headers, providers, report_cleanup_progress, duplicate_cutoff)
        # nothing consumes splits until the scan reports completed/applied, so
        # persist them in a single write at the end instead of re-serializing
        # the whole (ever-growing) state file once per split
        state.update(
            {
                "tags": plan["tags"],
                "junk": plan["junk"],
                "duplicates": plan["duplicates"],
                "duplicate_edges": plan["duplicate_edges"],
                "splits": plan["splits"],
                "scanned": len(plan["splits"]),
                "total": len(plan["splits"]),
                "failure_count": len(plan["failures"]),
                "failures": plan["failures"],
                "progress_phase": "complete",
                "progress_detail": "",
            }
        )
        state["status"] = "completed"
        write_cleanup_state(server, state)
        stash_progress(len(plan["splits"]), len(plan["splits"]))
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


# ---------------------------------------------------------------------------
# Link local tags to remote stash-box tags (batch review list)
# ---------------------------------------------------------------------------


def link_name_overlap(left, right):
    """True when the names share a meaningful whole word (e.g. "Goth Girl" and "Goth")."""
    def words(value):
        return set(re.findall(r"[a-z0-9]{3,}", str(value or "").casefold()))

    return bool(words(left) & words(right))


def link_fuzzy_match(left, right):
    """Fuzzy local-name match for link suggestions: shared whole word or a near-duplicate ratio.

    Deliberately conservative: difflib is character-based, so loose thresholds pair up
    unrelated names ("water" vs "amateur" scores 0.667). Token overlap catches
    multi-word variants ("Goth Girl" vs "Goth") and a high ratio only catches
    near-duplicates (plurals, compound splits, typos). Stash-box itself is the
    semantic oracle: candidates it knows as separate tags are dropped later.
    """
    return link_name_overlap(left, right) or gated_fuzzy_similarity(left, right, LINK_NEAR_DUPLICATE_CUTOFF) is not None


def link_resolve_remote(provider, name, cache):
    """Resolve a stash-box tag by name, cached per scan.

    Returns None when the name does not resolve to a live tag (deleted or missing);
    used both to enrich matches with remote aliases and as the semantic oracle:
    a candidate whose name resolves to a *different* stash-box tag is its own
    concept, not a variant of the remote tag.
    """
    key = name.casefold()
    if key in cache:
        return cache[key]
    remote = graphql(
        provider["endpoint"],
        LINK_REMOTE_TAG_QUERY,
        {"name": name},
        {"ApiKey": provider["api_key"]},
    ).get("findTag")
    if not remote or remote.get("deleted"):
        cache[key] = None
        return None
    record = {
        "id": str(remote["id"]),
        "name": str(remote.get("name") or name),
        "aliases": [str(alias) for alias in remote.get("aliases") or [] if str(alias).strip()],
        "description": str(remote.get("description") or "").strip(),
    }
    cache[key] = record
    return record


def link_search_remote(provider, term, cache):
    """Search stash-box tags for a term, cached per scan.

    Returns the list of matching tag ids (stash-box's own relevance ranking), or
    None when the lookup failed. Used as the semantic confirmation for fuzzy
    candidates: a candidate survives only when the remote tag appears in stash-
    box's search results for the candidate's name (stash-box associates them,
    e.g. via alias or name similarity). A failed lookup returns None so the
    caller keeps the candidate rather than dropping it on a network blip.
    """
    key = "search:" + term.casefold()
    if key in cache:
        return cache[key]
    try:
        data = graphql(
            provider["endpoint"],
            LINK_SEARCH_REMOTE_QUERY,
            {"term": term, "limit": 8},
            {"ApiKey": provider["api_key"]},
        )
        ids = [str(tag["id"]) for tag in data.get("searchTag") or [] if tag.get("id")]
        cache[key] = ids
        return ids
    except RuntimeError:
        cache[key] = None
        return None


def link_candidates(remote_name, local_tags, endpoint, remote_aliases=None):
    """Classify local tags matching a remote tag into (exact, fuzzy) candidate dicts.

    Exact covers the remote name itself and the remote tag's stash-box aliases:
    a local tag whose name or alias equals a stash-box alias is the same concept
    (e.g. local "Goth Girl" when stash-box "Goth" lists "Goth Girl" as an alias).
    """
    keys = {remote_name.casefold()}
    for alias in remote_aliases or []:
        keys.add(str(alias).casefold())
    exact, fuzzy = [], []
    for tag in local_tags:
        name = tag.get("name") or ""
        tag_keys = {name.casefold()}
        tag_keys.update(str(alias).casefold() for alias in tag.get("aliases") or [])
        if tag_keys & keys:
            exact.append(cleanup_tag_snapshot(tag))
        elif link_fuzzy_match(name, remote_name):
            fuzzy.append(cleanup_tag_snapshot(tag))

    def to_candidate(snapshot, match):
        provider_id = next(
            (
                str(item["stash_id"])
                for item in snapshot["stash_ids"]
                if item.get("endpoint") == endpoint
            ),
            None,
        )
        return {
            "id": snapshot["id"],
            "name": snapshot["name"],
            "aliases": snapshot["aliases"],
            "usage": snapshot["usage"],
            "has_stash_id": bool(snapshot["stash_ids"]),
            "provider_stash_id": provider_id,
            "match": match,
        }

    return [to_candidate(item, "exact") for item in exact], [to_candidate(item, "fuzzy") for item in fuzzy]


def link_rows_from_candidates(remote_name, scene_count, exact, fuzzy):
    """Build review rows from already-classified and oracle-verified candidate dicts."""
    if not exact and not fuzzy:
        return []
    rows = []
    if exact:
        name_exact = [
            candidate for candidate in exact if candidate["name"].casefold() == remote_name.casefold()
        ]
        if len(exact) >= 2 or fuzzy:
            survivor = sorted(
                name_exact or exact, key=lambda c: (-c["usage"], c["name"].casefold())
            )[0]
            ordered = [survivor] + [
                candidate for candidate in exact + fuzzy if candidate["id"] != survivor["id"]
            ]
            row = {
                "remote_name": remote_name,
                "scene_count": scene_count,
                "kind": "merge",
                "survivor_id": survivor["id"],
                "preselected": False,
                "candidates": ordered[:LINK_MAX_CANDIDATES],
            }
            if any(candidate["has_stash_id"] for candidate in row["candidates"]):
                row["linked_note"] = "already has a stash ID for this provider — applying verifies or replaces it"
            rows.append(row)
        else:
            survivor = exact[0]
            row = {
                "remote_name": remote_name,
                "scene_count": scene_count,
                "kind": "link",
                "survivor_id": survivor["id"],
                "preselected": not survivor["has_stash_id"],
                "candidates": exact[:LINK_MAX_CANDIDATES],
            }
            if survivor["has_stash_id"]:
                row["linked_note"] = "already has a stash ID for this provider — applying verifies or replaces it"
            rows.append(row)
    else:
        for candidate in sorted(fuzzy, key=lambda c: (-c["usage"], c["name"].casefold()))[:LINK_MAX_CANDIDATES]:
            row = {
                "remote_name": remote_name,
                "scene_count": scene_count,
                "kind": "link",
                "survivor_id": candidate["id"],
                "preselected": False,
                "candidates": [candidate],
            }
            if candidate["has_stash_id"]:
                row["linked_note"] = "already has a stash ID for this provider — applying verifies or replaces it"
            rows.append(row)
    return rows


def link_rows_for_remote(remote_name, scene_count, local_tags, endpoint, resolve=None, search=None):
    """Build review rows for one remote tag name, verifying candidates via stash-box.

    `resolve(name)` returns the cached stash-box record for a tag name (or None)
    and `search(name)` the cached search-result ids (or None on failure); together
    they are the semantic oracle. A fuzzy candidate is dropped when stash-box
    knows its name as a *different* tag, and kept for token/word variants only
    when stash-box's own search for the candidate name ranks the remote tag
    (associating them). Without a resolver (tests, unresolved provider) the
    heuristic result is used as-is.
    """
    remote = resolve(remote_name) if resolve is not None else None
    exact, fuzzy = link_candidates(remote_name, local_tags, endpoint, remote and remote["aliases"])
    kept = []
    # verify at most the most-used candidates; the row builder never shows more
    for candidate in sorted(fuzzy, key=lambda c: (-c["usage"], c["name"].casefold()))[:LINK_MAX_CANDIDATES]:
        if remote is not None and resolve is not None:
            candidate_tag = resolve(candidate["name"])
            if candidate_tag is not None and candidate_tag["id"] != remote["id"]:
                continue  # stash-box knows this name as its own separate tag
            if (
                search is not None
                # a near-duplicate spelling (plural/compound) needs no confirmation
                and gated_fuzzy_similarity(candidate["name"], remote_name, LINK_NEAR_DUPLICATE_CUTOFF) is None
            ):
                ids = search(candidate["name"])
                # the remote tag must rank among stash-box's own search results
                if ids is not None and remote["id"] not in ids:
                    continue
        kept.append(candidate)
    rows = link_rows_from_candidates(remote_name, scene_count, exact, kept)
    if remote is None:
        return rows
    result = []
    for row in rows:
        row["remote_id"] = remote["id"]
        if remote["aliases"]:
            row["remote_aliases"] = remote["aliases"]
        survivor = next(
            (candidate for candidate in row["candidates"] if candidate["id"] == row["survivor_id"]),
            None,
        )
        # plain link rows already linked to exactly this remote tag have nothing to
        # do: drop them instead of surfacing a no-op. A different stash ID on the
        # same provider is kept (it is a mislink the override can replace), and
        # merge rows stay even when the survivor is already linked, because the
        # merge of the variants is still meaningful.
        if (
            row["kind"] == "link"
            and survivor is not None
            and survivor.get("provider_stash_id") == remote["id"]
        ):
            continue
        result.append(row)
    return result


def link_scan_all(local_url, local_headers, provider, server, token):
    if not valid_scan_token(token):
        raise ValueError("link token must be 8-64 letters, numbers, underscores, or hyphens")
    endpoint = provider["endpoint"]
    state = {
        "link_token": token,
        "provider": endpoint,
        "status": "running",
        "scanned": 0,
        "total": 0,
        "failure_count": 0,
        "rows": [],
        "progress_phase": "scenes",
        "progress_detail": "",
        "error": None,
        "pid": os.getpid(),
    }
    write_link_state(server, state)
    gaps = {}
    cache = {}
    scanned = 0
    total = 0
    failures = 0
    page = 1
    try:
        local_tags = graphql(local_url, CLEANUP_TAGS_QUERY, headers=local_headers)["findTags"]["tags"]
        while True:
            result = graphql(
                local_url,
                SCENES_QUERY,
                {
                    "filter": {"page": page, "per_page": SCAN_PAGE_SIZE},
                    "scene_filter": {
                        "stash_ids_endpoint": {"endpoint": endpoint, "modifier": "NOT_NULL"}
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
                write_link_state(server, state)
                stash_progress(current, total)

            report_page_progress(0)
            rows, _, page_failures = gap_rows(
                scenes,
                {endpoint: provider},
                cache,
                report_page_progress,
            )
            failures += len(page_failures)
            merge_gap_rows(gaps, rows)
            scanned += len(scenes)
            state.update(
                {
                    "scanned": scanned,
                    "total": total,
                    "failure_count": failures,
                    "row_count": len(gaps),
                }
            )
            write_link_state(server, state)
            stash_progress(scanned, total)
            if not scenes or scanned >= total:
                break
            page += 1
        # Resolve every remote tag by name and verify fuzzy candidates against
        # stash-box's own tag vocabulary and search ranking (the semantic oracle).
        # Each lookup is cached, and progress is throttled because hundreds of
        # small lookups are far cheaper than persisting the state file after
        # each one.
        def resolve(name):
            return link_resolve_remote(provider, name, cache)

        def search(name):
            return link_search_remote(provider, name, cache)

        ordered_gaps = sorted(
            gaps.values(), key=lambda item: (-len(item["scene_ids"]), item["name"].casefold())
        )
        total_names = len(ordered_gaps)
        last_persist = [0.0]
        link_rows = []
        for processed, gap in enumerate(ordered_gaps, 1):
            link_rows.extend(
                link_rows_for_remote(
                    gap["name"],
                    len(gap["scene_ids"]),
                    local_tags,
                    endpoint,
                    resolve,
                    search,
                )
            )
            state.update(
                {
                    "scanned": processed,
                    "total": total_names,
                    "progress_phase": "verify",
                    "progress_detail": "resolving remote tags on the provider",
                }
            )
            now = time.time()
            if now - last_persist[0] >= 0.5 or processed == total_names:
                write_link_state(server, state)
                stash_progress(processed, total_names)
                last_persist[0] = now
        state.update({"rows": link_rows, "row_count": len(link_rows), "status": "completed"})
        write_link_state(server, state)
        return {
            "link_token": token,
            "status": state["status"],
            "scanned": scanned,
            "total": total,
            "failure_count": failures,
            "row_count": len(link_rows),
        }
    except Exception as error:
        state.update({"status": "failed", "error": str(error)})
        write_link_state(server, state)
        raise


def link_apply(local_url, local_headers, provider, server, args):
    if not isinstance(args, dict):
        raise ValueError("link apply arguments must be an object")
    token = args.get("link_token") or args.get("scan_token")
    if not valid_scan_token(token):
        raise ValueError("link token is required")
    selections = args.get("rows") or args.get("selections")
    if not isinstance(selections, list) or not selections:
        raise ValueError("select at least one link row to apply")
    for item in selections:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("remote_name"), str)
            or not item["remote_name"].strip()
        ):
            raise ValueError("each link selection must contain a remote tag name")
    state = read_link_state(server, token)
    if state is None or state.get("status") != "completed":
        raise ValueError("complete a link scan before applying changes")
    merge_selected = any(
        (item.get("source_ids") or item.get("sources") or []) for item in selections
    )
    if merge_selected:
        backup_url = args.get("backup_url")
        if args.get("backup_confirmed") is not True and not (
            isinstance(backup_url, str) and backup_url.strip()
        ):
            raise ValueError("database backup required before applying merges")
    rows_by_name = {
        str(row.get("remote_name")).casefold(): row for row in state.get("rows") or []
    }
    result = {
        "status": "completed",
        "linked": [],
        "already_linked": [],
        "merged": [],
        "warnings": [],
        "failures": [],
    }
    for selection in selections:
        remote_name = str(selection["remote_name"]).strip()
        row = rows_by_name.get(remote_name.casefold())
        survivor_id = str(selection.get("survivor_id") or "")
        source_ids = sorted(
            {str(tag_id) for tag_id in (selection.get("source_ids") or selection.get("sources") or [])}
        )
        override = bool(
            selection.get("override")
            or selection.get("override_remote_ids")
            or selection.get("replace_existing")
        )
        try:
            record = link_apply_one(
                local_url,
                local_headers,
                provider,
                remote_name,
                row,
                survivor_id,
                source_ids,
                override,
            )
            result["linked"].append(record)
            if record.get("already_linked"):
                result["already_linked"].append(record)
            if record.get("merged_source_ids"):
                result["merged"].append(record)
        except (CleanupStaleError, RuntimeError, ValueError) as error:
            result["failures"].append({"remote_name": remote_name, "error": str(error)})
    return result


def link_apply_one(local_url, local_headers, provider, remote_name, row, survivor_id, source_ids, override):
    endpoint = provider["endpoint"]
    if row is not None:
        candidate_ids = {str(candidate.get("id")) for candidate in row.get("candidates") or []}
        if survivor_id not in candidate_ids:
            raise ValueError(
                "the chosen local tag is not a match for “{}” — rescan before applying".format(remote_name)
            )
        if not set(source_ids) <= candidate_ids:
            raise ValueError(
                "a merge source is not a match for “{}” — rescan before applying".format(remote_name)
            )
    if not survivor_id:
        raise ValueError("choose a local tag to link “{}”".format(remote_name))

    remote = graphql(
        endpoint,
        LINK_REMOTE_TAG_QUERY,
        {"name": remote_name},
        {"ApiKey": provider["api_key"]},
    ).get("findTag")
    if not remote or remote.get("deleted"):
        raise RuntimeError("remote tag “{}” was not found on the provider".format(remote_name))
    remote_id = str(remote["id"])
    if row is not None and row.get("remote_id") and str(row.get("remote_id")) != remote_id:
        raise CleanupStaleError(
            "remote tag “{}” changed identity since the scan; rescan before applying".format(remote_name)
        )
    remote_aliases = [str(alias) for alias in remote.get("aliases") or [] if str(alias).strip()]
    remote_description = str(remote.get("description") or "").strip()

    all_ids = sorted({survivor_id, *source_ids})
    records = graphql(
        local_url, CLEANUP_TAGS_BY_IDS_QUERY, {"ids": all_ids}, local_headers
    )["findTags"]["tags"]
    current = cleanup_tags_by_id(records)
    missing = [tag_id for tag_id in all_ids if tag_id not in current]
    if missing:
        raise CleanupStaleError(
            "local tag {} was removed after the scan; rescan before applying".format(", ".join(missing))
        )
    survivor = current[survivor_id]

    merged_source_ids = []
    if source_ids:
        source_records = [current[tag_id] for tag_id in source_ids]
        conflicts = remote_id_conflicts([survivor, *source_records])
        if conflicts and not override:
            raise RuntimeError(
                "the tags carry conflicting stash IDs on "
                + ", ".join(sorted(conflicts))
                + "; override to proceed with the merge"
            )
        hierarchy = union_hierarchy([*current.values()], survivor_id, source_ids)
        merged_aliases = dedupe_tag_names(
            [
                *survivor.get("aliases", []),
                *(
                    name
                    for source_id in source_ids
                    for name in [current[source_id]["name"], *current[source_id].get("aliases", [])]
                ),
            ],
            excluded=[survivor["name"]],
        )
        merged_stash_ids = []
        seen = set()
        for tag in [survivor, *source_records]:
            for stash_id in tag.get("stash_ids") or []:
                entry_endpoint = stash_id.get("endpoint")
                entry_id = stash_id.get("stash_id")
                if entry_endpoint and entry_id and (entry_endpoint, entry_id) not in seen:
                    seen.add((entry_endpoint, entry_id))
                    merged_stash_ids.append(stash_id)
        graphql(
            local_url,
            TAGS_MERGE_MUTATION,
            {
                "input": {
                    "source": source_ids,
                    "destination": survivor_id,
                    "values": {
                        "id": survivor_id,
                        "aliases": merged_aliases,
                        "stash_ids": merged_stash_ids,
                        "parent_ids": hierarchy["parent_ids"],
                        "child_ids": hierarchy["child_ids"],
                    },
                }
            },
            local_headers,
        )
        survivor = {
            **survivor,
            "aliases": merged_aliases,
            "stash_ids": merged_stash_ids,
            "parents": hierarchy["parent_ids"],
            "children": hierarchy["child_ids"],
        }
        merged_source_ids = source_ids

    provider_entries = [
        stash_id
        for stash_id in survivor.get("stash_ids") or []
        if stash_id.get("endpoint") == endpoint
    ]
    existing = provider_entries[0] if provider_entries else None
    note = None
    if existing is not None and str(existing.get("stash_id")) == remote_id:
        stash_ids = list(survivor.get("stash_ids") or [])
        already_linked = True
    elif existing is not None:
        if not override:
            raise RuntimeError(
                "local tag “{}” is already linked to a different tag on this provider (id {}); "
                "override to replace it".format(survivor["name"], existing.get("stash_id"))
            )
        stash_ids = [
            stash_id for stash_id in survivor.get("stash_ids") or []
            if stash_id.get("endpoint") != endpoint
        ]
        stash_ids.append({"endpoint": endpoint, "stash_id": remote_id})
        already_linked = False
        note = "replaced existing stash ID"
    else:
        stash_ids = [
            *(survivor.get("stash_ids") or []),
            {"endpoint": endpoint, "stash_id": remote_id},
        ]
        already_linked = False

    aliases = dedupe_tag_names(
        [*(survivor.get("aliases") or []), *remote_aliases],
        excluded=[survivor["name"], remote_name],
    )
    update = {
        "id": survivor_id,
        "aliases": aliases,
        "stash_ids": stash_ids,
    }
    fields = ["stash_id"]
    if remote_aliases:
        fields.append("aliases")
    if remote_description:
        update["description"] = remote_description
        fields.append("description")
    graphql(local_url, TAG_UPDATE_MUTATION, {"input": update}, local_headers)
    return {
        "remote_name": remote_name,
        "remote_id": remote_id,
        "survivor_id": survivor_id,
        "survivor_name": survivor["name"],
        "merged_source_ids": merged_source_ids,
        "already_linked": already_linked,
        "fields": fields,
        "note": note,
    }


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
    if mode == "link_status":
        token = args.get("link_token") or args.get("scan_token")
        state = read_link_state(server, token)
        if state is None:
            return {
                "link_token": token,
                "status": "waiting",
                "scanned": 0,
                "total": 0,
                "failure_count": 0,
                "row_count": 0,
                "rows": [],
                "error": None,
            }
        path = link_state_path(server, token)
        resolved = resolve_running_state(state, path)
        if resolved is not state:
            write_link_state(server, resolved)
            state = resolved
        result = dict(state)
        result["row_count"] = len(state.get("rows") or [])
        if not args.get("include_rows"):
            result.pop("rows", None)
        return result
    if mode == "link_scan":
        token = args.get("link_token") or args.get("scan_token")
        provider = providers.get(args.get("provider"))
        if not provider:
            raise ValueError("select a configured metadata provider")
        return link_scan_all(local_url, local_headers, provider, server, token)
    if mode == "link_apply":
        provider = providers.get(args.get("provider"))
        if not provider:
            raise ValueError("select a configured metadata provider")
        return link_apply(local_url, local_headers, provider, server, args)
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

    if mode == "infer_scan":
        return infer_scan_all(
            local_url,
            local_headers,
            server,
            _infer_require_token(server, args),
            configured_providers(configuration),
        )
    if mode == "infer_status":
        token = _infer_resolve_token(server, args)
        if token is None:
            return infer_status(server, None, None)
        return infer_status(server, read_infer_state(server, token), token)
    if mode == "infer_review":
        token = _infer_require_token(server, args)
        return infer_review(_infer_state_for(server, token, "review"), args)
    if mode == "infer_apply":
        token = _infer_require_token(server, args)
        return infer_apply(local_url, local_headers, server, _infer_state_for(server, token, "apply"), args)
    if mode == "infer_apply_all":
        token = _infer_require_token(server, args)
        return infer_apply_all(
            local_url, local_headers, server, _infer_state_for(server, token, "apply_all")
        )
    if mode == "infer_skip":
        token = _infer_require_token(server, args)
        return infer_skip(server, _infer_state_for(server, token, "skip"), args)
    if mode == "infer_unskip":
        token = _infer_require_token(server, args)
        return infer_unskip(server, _infer_state_for(server, token, "unskip"), args)
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
            if per_page == -1:
                batch = scenes
            else:
                start = (variables["filter"]["page"] - 1) * per_page
                batch = scenes[start:start + per_page]
            return {"findScenes": {"scenes": batch}}
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
        assert sum(query == CLEANUP_SCENES_BY_TAGS_QUERY for query, _ in cleanup_calls) == 1
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
            # --- link review rows ---
            endpoint = "https://stashdb.org/graphql"
            goth = {"id": "1", "name": "Goth", "aliases": [], "stash_ids": [], "scene_count": 10, "scene_marker_count": 0, "image_count": 0, "gallery_count": 0, "performer_count": 0, "studio_count": 0, "group_count": 0, "parents": [], "children": []}
            goth_girl = {"id": "2", "name": "Goth Girl", "aliases": [], "stash_ids": [], "scene_count": 2, "scene_marker_count": 0, "image_count": 0, "gallery_count": 0, "performer_count": 0, "studio_count": 0, "group_count": 0, "parents": [], "children": []}
            goth_metal = {"id": "3", "name": "Goth Metal", "aliases": [], "stash_ids": [], "scene_count": 5, "scene_marker_count": 0, "image_count": 0, "gallery_count": 0, "performer_count": 0, "studio_count": 0, "group_count": 0, "parents": [], "children": []}
            unrelated = {"id": "4", "name": "Unrelated", "aliases": [], "stash_ids": [], "scene_count": 1, "scene_marker_count": 0, "image_count": 0, "gallery_count": 0, "performer_count": 0, "studio_count": 0, "group_count": 0, "parents": [], "children": []}
            # exact + variants -> merge row, survivor = the exact name match, never preselected
            rows = link_rows_for_remote("Goth", 42, [goth, goth_girl, goth_metal, unrelated], endpoint)
            assert len(rows) == 1
            assert rows[0]["kind"] == "merge"
            assert rows[0]["survivor_id"] == "1"
            assert rows[0]["preselected"] is False
            assert {candidate["id"] for candidate in rows[0]["candidates"]} == {"1", "2", "3"}
            assert {candidate["match"] for candidate in rows[0]["candidates"]} == {"exact", "fuzzy"}
            assert "linked_note" not in rows[0]
            # exact only -> link row, preselected
            rows = link_rows_for_remote("Goth", 3, [goth, unrelated], endpoint)
            assert rows[0]["kind"] == "link" and rows[0]["preselected"] is True and rows[0]["survivor_id"] == "1"
            # fuzzy only -> one unchecked link row per candidate, most-used first
            rows = link_rows_for_remote("Goth", 3, [goth_girl, goth_metal, unrelated], endpoint)
            assert [row["survivor_id"] for row in rows] == ["3", "2"]
            assert all(row["kind"] == "link" and row["preselected"] is False for row in rows)
            # alias-exact counts as exact and forces a merge with the name-exact tag
            alias_tag = dict(goth_girl, aliases=["Goth"])
            rows = link_rows_for_remote("Goth", 3, [goth, alias_tag], endpoint)
            assert rows[0]["kind"] == "merge" and rows[0]["survivor_id"] == "1"
            # already linked to this provider -> chip and not preselected
            linked_goth = dict(goth, stash_ids=[{"endpoint": endpoint, "stash_id": "r99"}])
            rows = link_rows_for_remote("Goth", 1, [linked_goth], endpoint)
            assert rows[0]["kind"] == "link" and rows[0]["preselected"] is False
            assert rows[0]["candidates"][0]["provider_stash_id"] == "r99"
            assert rows[0].get("linked_note")
            # no match -> no row
            assert link_rows_for_remote("Goth", 1, [unrelated], endpoint) == []
            assert link_name_overlap("Goth Girl", "Goth") is True
            assert link_name_overlap("Gotham", "Goth") is False
            # tightened fuzzy: near-duplicates and token variants only
            assert link_fuzzy_match("goth girl", "goth") is True
            assert link_fuzzy_match("goths", "goth") is True
            assert link_fuzzy_match("cream pie", "creampie") is True
            assert link_fuzzy_match("water", "amateur") is False
            assert link_fuzzy_match("gotham", "goth") is False
            # the semantic oracle: candidates stash-box knows as separate tags are dropped,
            # remote aliases turn local variants into exact matches
            resolved_goth = {
                "goth": {"id": "rg", "name": "Goth", "aliases": ["Goth Girl"], "description": ""},
                "goth metal": {"id": "rgm", "name": "Goth Metal", "aliases": [], "description": ""},
                "goth girl": None,  # only an alias of Goth, not a separate tag
            }
            rows = link_rows_for_remote(
                "Goth", 5, [goth, goth_girl, goth_metal], endpoint,
                lambda name: resolved_goth.get(name.casefold()),
            )
            assert len(rows) == 1 and rows[0]["kind"] == "merge"
            assert rows[0]["survivor_id"] == "1"
            assert {candidate["id"] for candidate in rows[0]["candidates"]} == {"1", "2"}
            assert rows[0]["remote_id"] == "rg"
            assert rows[0]["remote_aliases"] == ["Goth Girl"]
            # without the oracle the token-overlap candidate would still be listed
            rows = link_rows_for_remote("Goth", 5, [goth, goth_girl, goth_metal], endpoint)
            assert {candidate["id"] for candidate in rows[0]["candidates"]} == {"1", "2", "3"}
            # exact name match only, oracle confirms "Water" is its own stash-box tag
            resolved_amateur = {
                "amateur": {"id": "ra", "name": "Amateur", "aliases": [], "description": ""},
                "water": {"id": "rw", "name": "Water", "aliases": [], "description": ""},
            }
            amateur_tag = dict(goth, id="10", name="Amateur")
            water_tag = dict(goth, id="11", name="Water")
            rows = link_rows_for_remote(
                "Amateur", 5, [amateur_tag, water_tag], endpoint,
                lambda name: resolved_amateur.get(name.casefold()),
            )
            assert len(rows) == 1 and rows[0]["kind"] == "link"
            assert [candidate["id"] for candidate in rows[0]["candidates"]] == ["10"]
            # a local tag already linked to exactly this remote tag: no no-op row
            resolved_69 = {"69": {"id": "r69", "name": "69", "aliases": [], "description": ""}}
            linked_69 = dict(goth, id="20", name="69", stash_ids=[{"endpoint": endpoint, "stash_id": "r69"}])
            rows = link_rows_for_remote("69", 3, [linked_69], endpoint, lambda name: resolved_69.get(name.casefold()))
            assert rows == []
            # linked to a *different* stash-box tag: kept as a fixable mislink, not preselected
            mislinked_69 = dict(goth, id="21", name="69", stash_ids=[{"endpoint": endpoint, "stash_id": "r666"}])
            rows = link_rows_for_remote("69", 3, [mislinked_69], endpoint, lambda name: resolved_69.get(name.casefold()))
            assert len(rows) == 1 and rows[0]["kind"] == "link"
            assert rows[0]["preselected"] is False and rows[0].get("linked_note")
            # unresolved remote: cannot verify, so the row stays with the chip
            rows = link_rows_for_remote("69", 3, [linked_69], endpoint)
            assert len(rows) == 1 and rows[0]["preselected"] is False
            # merge rows stay even when the survivor is already linked (the merge matters)
            goth_linked = dict(goth, stash_ids=[{"endpoint": endpoint, "stash_id": "rg"}])
            resolved_g2 = {"goth": {"id": "rg", "name": "Goth", "aliases": [], "description": ""}, "goth girl": None}
            rows = link_rows_for_remote(
                "Goth", 5, [goth_linked, goth_girl], endpoint,
                lambda name: resolved_g2.get(name.casefold()),
            )
            assert len(rows) == 1 and rows[0]["kind"] == "merge"
            assert rows[0]["survivor_id"] == "1"
            # stash-box search is the semantic confirmation: a non-tag candidate
            # survives only when stash-box's own search ranks the remote tag
            resolved_g3 = {"goth": {"id": "rg", "name": "Goth", "aliases": [], "description": ""}}

            def search_g3(term):
                if term.casefold() == "goth girl":
                    return ["rg", "r-other"]  # stash-box ranks Goth for "Goth Girl"
                return []  # no association for "Goth Metal"

            rows = link_rows_for_remote(
                "Goth", 5, [goth, goth_girl, goth_metal], endpoint,
                lambda name: resolved_g3.get(name.casefold()),
                search_g3,
            )
            assert {candidate["id"] for candidate in rows[0]["candidates"]} == {"1", "2"}
            # a failed search keeps the candidate (cannot verify -> do not drop)
            rows = link_rows_for_remote(
                "Goth", 5, [goth, goth_girl], endpoint,
                lambda name: resolved_g3.get(name.casefold()),
                lambda term: None,
            )
            assert {candidate["id"] for candidate in rows[0]["candidates"]} == {"1", "2"}
            # near-duplicates are kept without needing a search confirmation
            calls = []

            def counting_search(term):
                calls.append(term)
                return []

            goths_tag = dict(goth, id="40", name="Goths")
            rows = link_rows_for_remote(
                "Goth", 5, [goth, goths_tag], endpoint,
                lambda name: resolved_g3.get(name.casefold()),
                counting_search,
            )
            assert {candidate["id"] for candidate in rows[0]["candidates"]} == {"1", "40"}
            assert calls == []
            # the user's cases: shared generic words are disproven by stash-box search
            breast_play = {"id": "bp", "name": "Breast Play", "aliases": [], "description": ""}
            breast_play_local = dict(goth, id="31", name="Breast Play")
            anal_play_local = dict(goth, id="30", name="Anal Play")

            def search_bp(term):
                return [] if term.casefold() == "anal play" else ["bp"]

            rows = link_rows_for_remote(
                "Breast Play", 5, [breast_play_local, anal_play_local], endpoint,
                lambda name: {"breast play": breast_play}.get(name.casefold()),
                search_bp,
            )
            assert len(rows) == 1
            assert [candidate["id"] for candidate in rows[0]["candidates"]] == ["31"]
            # "Age Group" under "Group Sex": no token relation that stash-box confirms
            group_sex = {"id": "gs", "name": "Group Sex", "aliases": [], "description": ""}
            group_sex_local = dict(goth, id="41", name="Group Sex")
            age_group_local = dict(goth, id="42", name="Age Group")

            def search_gs(term):
                return ["gs"] if term.casefold() == "group sex" else []

            rows = link_rows_for_remote(
                "Group Sex", 5, [group_sex_local, age_group_local], endpoint,
                lambda name: {"group sex": group_sex}.get(name.casefold()),
                search_gs,
            )
            assert len(rows) == 1
            assert [candidate["id"] for candidate in rows[0]["candidates"]] == ["41"]
            # --- link apply ---
            link_calls = []
            remote_tags = {
                "Goth": {"id": "r1", "name": "Goth", "aliases": ["Goth Girl"], "description": "A goth aesthetic", "deleted": False},
                "Vanished": {"id": "r2", "name": "Vanished", "aliases": [], "description": "", "deleted": True},
            }
            link_store = {
                "1": dict(goth),
                "2": dict(goth_girl),
                "3": dict(goth, id="3", name="Goth (linked)", stash_ids=[{"endpoint": endpoint, "stash_id": "r1"}]),
                "4": dict(goth, id="4", name="Goth (other)", stash_ids=[{"endpoint": endpoint, "stash_id": "other"}]),
                "5": dict(goth, id="5", name="Goth (dup a)", stash_ids=[{"endpoint": "https://other.example/graphql", "stash_id": "a"}]),
                "6": dict(goth, id="6", name="Goth (dup b)", stash_ids=[{"endpoint": "https://other.example/graphql", "stash_id": "b"}]),
            }

            def fake_link_graphql(url, query, variables=None, headers=None):
                link_calls.append((query, variables))
                if query == LINK_REMOTE_TAG_QUERY:
                    return {"findTag": remote_tags.get(variables["name"])}
                if query == CLEANUP_TAGS_BY_IDS_QUERY:
                    return {"findTags": {"tags": [link_store[item] for item in variables["ids"] if item in link_store]}}
                if query == TAGS_MERGE_MUTATION:
                    source_ids = variables["input"]["source"]
                    destination = variables["input"]["destination"]
                    for source_id in source_ids:
                        link_store.pop(source_id, None)
                    link_store[destination] = {
                        **link_store.get(destination, {}),
                        **{key: value for key, value in variables["input"]["values"].items() if key != "id"},
                    }
                    return {"tagsMerge": {"id": destination}}
                if query == TAG_UPDATE_MUTATION:
                    input_data = variables["input"]
                    link_store[input_data["id"]] = {**link_store.get(input_data["id"], {}), **input_data}
                    return {"tagUpdate": {"id": input_data["id"]}}
                raise AssertionError("unexpected link query: " + query)

            real_link_graphql = graphql
            graphql = fake_link_graphql
            try:
                provider = {"endpoint": endpoint, "api_key": "key"}
                row = {"candidates": [{"id": "1"}, {"id": "2"}]}
                # merge then link
                record = link_apply_one("local", {}, provider, "Goth", row, "1", ["2"], False)
                assert record["remote_id"] == "r1"
                assert record["merged_source_ids"] == ["2"]
                assert record["already_linked"] is False
                assert record["fields"] == ["stash_id", "aliases", "description"]
                assert "2" not in link_store
                assert link_store["1"]["aliases"] == ["Goth Girl"]
                assert link_store["1"]["description"] == "A goth aesthetic"
                assert link_store["1"]["stash_ids"] == [{"endpoint": endpoint, "stash_id": "r1"}]
                # already linked to the same remote tag: no duplicate entry, still pulls metadata
                record = link_apply_one("local", {}, provider, "Goth", {"candidates": [{"id": "3"}]}, "3", [], False)
                assert record["already_linked"] is True
                assert [entry for entry in link_store["3"]["stash_ids"] if entry["endpoint"] == endpoint] == [{"endpoint": endpoint, "stash_id": "r1"}]
                # linked to a different remote tag: requires override
                try:
                    link_apply_one("local", {}, provider, "Goth", {"candidates": [{"id": "4"}]}, "4", [], False)
                except RuntimeError as error:
                    assert "override" in str(error)
                else:
                    raise AssertionError("conflicting stash ID was replaced without override")
                record = link_apply_one("local", {}, provider, "Goth", {"candidates": [{"id": "4"}]}, "4", [], True)
                assert record["note"] == "replaced existing stash ID"
                assert [entry for entry in link_store["4"]["stash_ids"] if entry["endpoint"] == endpoint] == [{"endpoint": endpoint, "stash_id": "r1"}]
                # merging tags with conflicting stash IDs on another endpoint: override required
                try:
                    link_apply_one("local", {}, provider, "Goth", {"candidates": [{"id": "5"}, {"id": "6"}]}, "5", ["6"], False)
                except RuntimeError as error:
                    assert "conflicting stash IDs" in str(error)
                else:
                    raise AssertionError("merge with conflicting stash IDs succeeded without override")
                record = link_apply_one("local", {}, provider, "Goth", {"candidates": [{"id": "5"}, {"id": "6"}]}, "5", ["6"], True)
                assert record["merged_source_ids"] == ["6"]
                assert "6" not in link_store
                # vanished remote tag fails cleanly
                try:
                    link_apply_one("local", {}, provider, "Vanished", {"candidates": [{"id": "1"}]}, "1", [], False)
                except RuntimeError as error:
                    assert "not found" in str(error)
                else:
                    raise AssertionError("deleted remote tag was linked")
                # selection validation
                try:
                    link_apply_one("local", {}, provider, "Goth", {"candidates": [{"id": "2"}]}, "9", [], False)
                except ValueError as error:
                    assert "not a match" in str(error)
                else:
                    raise AssertionError("survivor outside the row candidates was accepted")
                # the remote tag changed identity since the scan -> stale
                try:
                    link_apply_one(
                        "local", {}, provider, "Goth",
                        {"candidates": [{"id": "1"}], "remote_id": "OLD"},
                        "1", [], False,
                    )
                except CleanupStaleError as error:
                    assert "changed identity" in str(error)
                else:
                    raise AssertionError("stale remote identity was accepted")
            finally:
                graphql = real_link_graphql
            # wrapper: backup gate for merges, per-row failures, missing state
            import tempfile as _tempfile
            link_dir = _tempfile.mkdtemp(prefix="tag-organizer-link-test-")
            write_link_state(
                {"Dir": link_dir},
                {
                    "link_token": "link-test",
                    "status": "completed",
                    "rows": [{"remote_name": "Goth", "candidates": [{"id": "1"}, {"id": "2"}]}],
                },
            )
            try:
                link_apply("local", {}, provider, {"Dir": link_dir}, {"link_token": "link-test", "rows": [{"remote_name": "Goth", "survivor_id": "1", "source_ids": ["2"]}]})
            except ValueError as error:
                assert "backup" in str(error)
            else:
                raise AssertionError("merge applied without a backup")
            graphql = fake_link_graphql
            link_store.clear()
            link_store.update({"1": dict(goth), "2": dict(goth_girl)})
            try:
                applied = link_apply(
                    "local",
                    {},
                    provider,
                    {"Dir": link_dir},
                    {"link_token": "link-test", "backup_confirmed": True, "rows": [{"remote_name": "Goth", "survivor_id": "1", "source_ids": ["2"]}]},
                )
                assert applied["failures"] == []
                assert applied["linked"][0]["remote_id"] == "r1"
                assert applied["merged"][0]["survivor_id"] == "1"
                assert "2" not in link_store
                failed = link_apply(
                    "local",
                    {},
                    provider,
                    {"Dir": link_dir},
                    {"link_token": "link-test", "backup_confirmed": True, "rows": [{"remote_name": "Vanished", "survivor_id": "1"}]},
                )
                assert len(failed["failures"]) == 1
                assert failed["failures"][0]["remote_name"] == "Vanished"
            finally:
                graphql = real_link_graphql
            link_state_path({"Dir": link_dir}, "link-test").unlink()
            try:
                link_apply("local", {}, provider, {"Dir": link_dir}, {"link_token": "link-test", "backup_confirmed": True, "rows": [{"remote_name": "Goth", "survivor_id": "1"}]})
            except ValueError as error:
                assert "complete a link scan" in str(error)
            else:
                raise AssertionError("link applied against a missing state file")
            try:
                link_apply("local", {}, provider, {"Dir": link_dir}, {"link_token": "link-test", "backup_confirmed": True, "rows": []})
            except ValueError as error:
                assert "select at least one" in str(error)
            else:
                raise AssertionError("empty link selection was accepted")
    finally:
        graphql = real_cleanup_graphql
    assert any(call[0] == TAG_DESTROY_MUTATION for call in cleanup_calls)

    # ── scene tag inference ──────────────────────────────────────────────────
    import tempfile as _tempfile
    infer_tags = [
        {"id": "60", "name": "Threesome", "aliases": []},
        {"id": "1309", "name": "Threesome (BGG)", "aliases": []},
        {"id": "1320", "name": "Threesome (BBG)", "aliases": []},
        {"id": "1324", "name": "Threesome (Lesbian)", "aliases": []},
        {"id": "1098", "name": "Solo", "aliases": []},
        {"id": "55", "name": "Orgy", "aliases": []},
        {"id": "54", "name": "Group Sex", "aliases": []},
        {"id": "143", "name": "Foursome", "aliases": []},
        {"id": "1132", "name": "Gangbang", "aliases": []},
        {"id": "1110", "name": "Bukkake", "aliases": []},
        {"id": "comp", "name": "Compilation", "aliases": []},
        {"id": "vint", "name": "Vintage", "aliases": []},
    ]
    infer_scenes = [
        # 3 performers (2F 1M), no group tag -> Threesome (BGG)
        {"id": "a", "title": "Alpha", "date": "2020-01-01", "details": "", "performers": [{"id": "1", "gender": "FEMALE"}, {"id": "2", "gender": "FEMALE"}, {"id": "3", "gender": "MALE"}], "tags": [{"id": "les"}], "paths": {"screenshot": "/s/a.jpg"}},
        # 3 performers (1F 2M) -> Threesome (BBG)
        {"id": "h", "title": "Eta Prime", "date": "2021-06-01", "details": "", "performers": [{"id": "1", "gender": "FEMALE"}, {"id": "2", "gender": "MALE"}, {"id": "3", "gender": "MALE"}], "tags": [], "paths": {"screenshot": None}},
        # 3 performers with unknown genders locally; stash-box knows 2F 1M -> Threesome (BGG)
        {"id": "i", "title": "Iota", "date": "2021-06-01", "details": "", "performers": [{"id": "1"}, {"id": "2"}, {"id": "3"}], "tags": [], "stash_ids": [{"endpoint": "https://stashdb.example.org/graphql", "stash_id": "si"}], "paths": {"screenshot": None}},
        # 2 performers locally, described as a threesome; stash-box lists 3 (1F 2M) -> BBG
        {"id": "b", "title": "Beta Three-way", "date": "2021-01-01", "details": "a spicy trio", "performers": [{"id": "1"}, {"id": "2"}], "tags": [], "stash_ids": [{"endpoint": "https://stashdb.example.org/graphql", "stash_id": "sb"}], "paths": {"screenshot": None}},
        # 5 performers -> Group Sex
        {"id": "c", "title": "Gamma", "date": "2022-01-01", "details": "", "performers": [{"id": str(i)} for i in range(5)], "tags": [], "paths": {"screenshot": None}},
        # 3 performers, all female (GGG) -> Threesome (Lesbian)
        {"id": "k", "title": "Kappa Girls", "date": "2020-05-01", "details": "", "performers": [{"id": "1", "gender": "FEMALE"}, {"id": "2", "gender": "FEMALE"}, {"id": "3", "gender": "FEMALE"}], "tags": [], "paths": {"screenshot": None}},
        # compilation in title -> Compilation
        {"id": "d", "title": "Best of Delta Compilation", "date": "2023-01-01", "details": "", "performers": [], "tags": [], "paths": {"screenshot": None}},
        # pre-2000 -> Vintage
        {"id": "e", "title": "Epsilon", "date": "1998-06-01", "details": "", "performers": [], "tags": [], "paths": {"screenshot": None}},
        # already tagged Threesome -> no suggestion
        {"id": "f", "title": "Zeta", "date": "2020-01-01", "details": "", "performers": [{"id": "1"}, {"id": "2"}, {"id": "3"}], "tags": [{"id": "60"}], "paths": {"screenshot": None}},
        # 2 performers, no keywords -> no suggestion
        {"id": "g", "title": "Eta", "date": "2020-01-01", "details": "", "performers": [{"id": "1"}, {"id": "2"}], "tags": [], "paths": {"screenshot": None}},
        # 1 performer, described as a threesome, but stash-box confirms a
        # twosome -> the mention is setup flavor, no suggestion
        {"id": "j", "title": "Jade's Sneaky Steam", "date": "2018-06-04", "details": "a hot threesome setup with a potential partner", "performers": [{"id": "1"}], "tags": [], "stash_ids": [{"endpoint": "https://stashdb.example.org/graphql", "stash_id": "sj"}], "paths": {"screenshot": None}},
        # Complete-looking local 3F cast, but stash-box lists 4 (3F 1M): the
        # local cast undercounts -> Group Sex from the remote count
        {"id": "m", "title": "Missing Fourth", "date": "2020-01-01", "details": "", "performers": [{"id": "1", "gender": "FEMALE"}, {"id": "2", "gender": "FEMALE"}, {"id": "3", "gender": "FEMALE"}], "tags": [], "stash_ids": [{"endpoint": "https://stashdb.example.org/graphql", "stash_id": "sm"}], "paths": {"screenshot": None}},
        # Solo: 1 local performer, remote confirms 1 -> Solo suggestion
        {"id": "n", "title": "Alone Time", "date": "2020-01-01", "details": "", "performers": [{"id": "1", "gender": "FEMALE"}], "tags": [], "stash_ids": [{"endpoint": "https://stashdb.example.org/graphql", "stash_id": "sn"}], "paths": {"screenshot": None}},
        # Not solo: 1 local performer, remote shows 2 -> no Solo suggestion
        {"id": "o", "title": "Duo Missing", "date": "2020-01-01", "details": "", "performers": [{"id": "1", "gender": "FEMALE"}], "tags": [], "stash_ids": [{"endpoint": "https://stashdb.example.org/graphql", "stash_id": "so"}], "paths": {"screenshot": None}},
        # Not solo: 1 local performer, already Solo-tagged -> no suggestion
        {"id": "p", "title": "Tagged Solo", "date": "2020-01-01", "details": "", "performers": [{"id": "1", "gender": "FEMALE"}], "tags": [{"id": "1098"}], "stash_ids": [{"endpoint": "https://stashdb.example.org/graphql", "stash_id": "sp"}], "paths": {"screenshot": None}},
        # Not solo: 1 remote performer but stash-box tags it Twosome -> rejected
        {"id": "q", "title": "Tagged Twosome", "date": "2020-01-01", "details": "", "performers": [{"id": "1", "gender": "FEMALE"}], "tags": [], "stash_ids": [{"endpoint": "https://stashdb.example.org/graphql", "stash_id": "sq"}], "paths": {"screenshot": None}},
        # Not solo: 1 performer everywhere but described with her boyfriend -> rejected
        {"id": "r", "title": "Date Night", "date": "2020-01-01", "details": "she spends the evening with her boyfriend", "performers": [{"id": "1", "gender": "FEMALE"}], "tags": [], "stash_ids": [{"endpoint": "https://stashdb.example.org/graphql", "stash_id": "sr"}], "paths": {"screenshot": None}},
        # Not solo: 1 performer everywhere but described with a blowjob -> rejected
        {"id": "s", "title": "Facial Fan", "date": "2020-01-01", "details": "she takes a messy facial from his cock", "performers": [{"id": "1", "gender": "FEMALE"}], "tags": [], "stash_ids": [{"endpoint": "https://stashdb.example.org/graphql", "stash_id": "ss"}], "paths": {"screenshot": None}},
        # Not solo: 1 performer everywhere but tagged Cowgirl -> rejected by tags
        {"id": "t", "title": "Ride Along", "date": "2020-01-01", "details": "", "performers": [{"id": "1", "gender": "FEMALE"}], "tags": [{"id": "x1", "name": "Cowgirl"}], "stash_ids": [{"endpoint": "https://stashdb.example.org/graphql", "stash_id": "st"}], "paths": {"screenshot": None}},
    ]
    real_graphql = graphql
    infer_calls = []

    remote_performers = {
        "si": [{"performer": {"gender": "FEMALE"}}, {"performer": {"gender": "FEMALE"}}, {"performer": {"gender": "MALE"}}],
        "sb": [{"performer": {"gender": "FEMALE"}}, {"performer": {"gender": "MALE"}}, {"performer": {"gender": "MALE"}}],
        "sj": [{"performer": {"gender": "FEMALE"}}, {"performer": {"gender": "MALE"}}],
        "sm": [{"performer": {"gender": "FEMALE"}}, {"performer": {"gender": "FEMALE"}}, {"performer": {"gender": "FEMALE"}}, {"performer": {"gender": "MALE"}}],
        "sn": [{"performer": {"gender": "FEMALE"}}],
        "so": [{"performer": {"gender": "FEMALE"}}, {"performer": {"gender": "MALE"}}],
        "sp": [{"performer": {"gender": "FEMALE"}}],
        "sq": [{"performer": {"gender": "FEMALE"}}],
        "sr": [{"performer": {"gender": "FEMALE"}}],
        "ss": [{"performer": {"gender": "FEMALE"}}],
        "st": [{"performer": {"gender": "FEMALE"}}],
    }
    solo_remote_tags = {
        "sn": ["Solo"],
        "sq": ["Twosome"],
    }
    infer_providers = {
        "https://stashdb.example.org/graphql": {
            "endpoint": "https://stashdb.example.org/graphql",
            "api_key": "key",
        }
    }

    def fake_infer_graphql(url, query, variables=None, headers=None):
        infer_calls.append(query)
        if query == TAGS_QUERY:
            return {"findTags": {"tags": infer_tags}}
        if query == TAG_SEARCH_QUERY:
            wanted = (variables or {}).get("filter", {}).get("q", "")
            return {"findTags": {"tags": [t for t in infer_tags if t["name"].casefold() == wanted.casefold()]}}
        if query == INFER_SCENES_QUERY:
            page = (variables or {}).get("filter", {}).get("page", 1)
            if page == 1:
                return {"findScenes": {"count": len(infer_scenes), "scenes": infer_scenes}}
            return {"findScenes": {"count": len(infer_scenes), "scenes": []}}
        if query == REMOTE_PERFORMERS_QUERY:
            return {
                "findScene": {
                    "performers": remote_performers.get(variables.get("id"), []),
                    "tags": [{"name": name} for name in solo_remote_tags.get(variables.get("id"), [])],
                }
            }
        if query.startswith("query RemotePerformers("):
            out = {}
            for name, value in (variables or {}).items():
                if name.startswith("id_"):
                    out["scene_" + name[3:]] = {
                        "performers": remote_performers.get(value, []),
                        "tags": [{"name": tag} for tag in solo_remote_tags.get(value, [])],
                    }
            return out
        if query == BULK_UPDATE_SCENES_MUTATION:
            return {"bulkSceneUpdate": [{"id": sid} for sid in variables["input"]["ids"]]}
        raise AssertionError("unexpected inference query: " + (query or "")[:60])

    graphql = fake_infer_graphql
    with _tempfile.TemporaryDirectory() as tmp:
        infer_server = {"Dir": tmp}
        result = infer_scan_all(
            "http://local", {}, infer_server, "infer-self-test-token", infer_providers
        )
        got = {(item["scene_id"], item["suggested"]) for item in result["suggestions"]}
        assert got == {
            ("a", "Threesome (BGG)"), ("h", "Threesome (BBG)"), ("i", "Threesome (BGG)"),
            ("k", "Threesome (Lesbian)"), ("m", "Group Sex"), ("n", "Solo"),
            ("b", "Threesome (BBG)"), ("c", "Group Sex"), ("d", "Compilation"), ("e", "Vintage"),
        }, got
        assert ("o", "Solo") not in got and ("p", "Solo") not in got and ("q", "Solo") not in got and ("r", "Solo") not in got and ("s", "Solo") not in got and ("t", "Solo") not in got
        reasons = {item["scene_id"]: item["reason"] for item in result["suggestions"]}
        assert "3 performers (2F 1M)" in reasons["a"]
        assert "3 performers (1F 2M)" in reasons["h"]
        assert "3 performers (3F)" in reasons["k"]
        # remote augmentation: local genders were unknown, stash-box supplied them
        assert "3 performers (2F 1M) · StashDB" in reasons["i"]
        assert "3 performers (1F 2M) · StashDB" in reasons["b"]
        state = read_infer_state(infer_server, "infer-self-test-token")
        assert state["status"] == "done"
        review = infer_review(state, {"page": 1, "per_page": 50})
        assert review["suggestion_count"] == 10
        applied = infer_apply("http://local", {}, infer_server, state, {
            "actions": [{"scene_id": "a", "tag_name": "Threesome (BGG)"}],
        })
        assert applied["applied"] == 1
        assert state["suggestions"][0]["applied"] is True
        all_result = infer_apply_all("http://local", {}, infer_server, state)
        assert all_result["applied"] == 9, all_result
        assert sum(item["applied"] for item in state["suggestions"]) == 10
        assert infer_apply_all("http://local", {}, infer_server, state)["processed"] == 0
        unskip = infer_unskip(infer_server, state, {
            "scene_id": "e", "tag_name": "Vintage",
        })
        assert unskip["skipped"] is False
        item = next(i for i in state["suggestions"] if i["scene_id"] == "e")
        assert item["skipped"] is False
    graphql = real_graphql
    print("self-check passed")


# ── Scene tag inference ───────────────────────────────────────────────────────
# Suggest missing tags from scene properties: group tags from performer count
# or description, Compilation from the title, Vintage from the release date.
# Suggestions are a review queue — applying is explicit and reversible.

INFER_STATE_NAME = "infer-review.json"
INFER_PAGE_SIZE = 100
INFER_REVIEW_PAGE_SIZE = 50
# bulkSceneUpdate costs ~0.45s per scene on the live library: 100-scene
# batches blow past the 30s GraphQL timeout, so applies use small batches.
INFER_APPLY_BATCH = 10
# Remote prefetch concurrency per provider: bounded so rate limits degrade
# gracefully (retry once, skip the batch) instead of hammering the endpoint.
REMOTE_PREFETCH_WORKERS = 4
INFER_GROUP_TAG_NAMES = (
    "Threesome", "Threesome (BGG)", "Threesome (BBG)", "Threesome (Lesbian)",
    "Orgy", "Group Sex", "Foursome", "Gangbang", "Bukkake",
)
# Threesome-family suggestions: a scene needs at most one of these.
INFER_THREESOME_SUGGESTIONS = (
    "Threesome", "Threesome (BGG)", "Threesome (BBG)", "Threesome (Lesbian)",
)
THREESOME_WORDS = re.compile(r"\b(threesome|three-way|3-way|3way|trio)\b", re.I)
COMPILATION_WORDS = re.compile(r"\b(compilation|best of)\b", re.I)

INFER_SCENES_QUERY = """
query InferScenes($filter: FindFilterType) {
  findScenes(filter: $filter) {
    count
    scenes {
      id title date details
      performers { id gender }
      tags { id name }
      stash_ids { endpoint stash_id }
      paths { screenshot }
    }
  }
}
"""

REMOTE_PERFORMERS_QUERY = """
query RemotePerformers($id: ID!) {
  findScene(id: $id) { performers { performer { gender } } tags { name } }
}
"""


def remote_performers_batch_query(scene_ids):
    variables = {}
    fields = []
    aliases = {}
    for index, (cache_key, scene_id) in enumerate(scene_ids):
        variable = f"id_{index}"
        alias = f"scene_{index}"
        variables[variable] = scene_id
        aliases[alias] = cache_key
        fields.append(f"{alias}: findScene(id: ${variable}) {{ performers {{ performer {{ gender }} }} tags {{ name }} }}")
    declarations = ", ".join(f"${name}: ID!" for name in variables)
    return f"query RemotePerformers({declarations}) {{ {' '.join(fields)} }}", variables, aliases


# ── Non-solo vocabulary ────────────────────────────────────────────────────────
# One canonical list of words/phrases that imply a partner is involved in a
# scene. Any of them in the scene's own text (title/details), its local tags,
# or its stash-box tags disqualifies a Solo suggestion — a scene tagged
# "Cowgirl" or described with a "facial" is not solo even when both cast
# records undercount the performers. The description check additionally
# matches pronouns and partner phrases, which only occur in free text.
INFER_NON_SOLO_TERMS = (
    # relationships and group makeup
    "couple", "twosome", "duo", "threesome", "orgy", "group sex", "foursome",
    "gangbang", "bukkake", "partner", "boyfriend", "girlfriend", "husband",
    "wife", "lover", "cheating", "cuckold", "affair",
    # male participants
    "man", "men", "male", "guy", "guys", "stud", "dude", "bloke",
    # male-act terms
    "blowjob", "handjob", "facial", "creampie", "cumshot", "cum in", "cum on",
    "deepthroat", "rimjob", "rimming", "cock", "dick", "big dick", "pov",
    # partner positions
    "cowgirl", "reverse cowgirl", "missionary", "doggy", "riding", "side fuck",
    "spoon", "piledriver", "anal sex", "double penetration", "tit.?fuck", "foot.?job",
)
INFER_NON_SOLO_PATTERN = r"\b(" + "|".join(
    re.escape(term) if term.isalpha() else term for term in INFER_NON_SOLO_TERMS
) + r")\b"

INFER_NON_SOLO_TEXT = re.compile(
    INFER_NON_SOLO_PATTERN
    + r"|\b(with (a|her|his) (man|guy|male|stud|dude|bloke))\b"
    + r"|\b(her (man|guy|stud|dude))\b"
    + r"|\b(his (woman|girl))\b"
    + r"|\b(he|him|his)\b"
    + r"|\b(sucks?|fucks?|rides?|services|pleases|pounded|pounding)\b",
    re.I,
)

INFER_NON_SOLO_TAG_TEXT = re.compile(INFER_NON_SOLO_PATTERN, re.I)


def prefetch_remote_performers(scenes, providers, cache, tags_cache=None):
    """Fetch performer genders (and optionally scene tags) from stash-box for
    scenes whose local cast is incomplete. cache: {(endpoint, stash_id):
    [gender, ...]}; tags_cache (optional): {(endpoint, stash_id): [tag, ...]}."""
    pending = {}
    for scene in scenes:
        for stash_id in scene.get("stash_ids") or []:
            endpoint = stash_id.get("endpoint")
            if endpoint not in providers:
                continue
            cache_key = (endpoint, stash_id["stash_id"])
            if cache_key not in cache:
                pending.setdefault(endpoint, {})[cache_key] = stash_id["stash_id"]

    def fetch_endpoint(endpoint, scene_ids):
        provider = providers[endpoint]
        items = list(scene_ids.items())
        chunks = [
            items[offset : offset + REMOTE_BATCH_SIZE]
            for offset in range(0, len(items), REMOTE_BATCH_SIZE)
        ]
        lock = threading.Lock()
        index = [0]
        failed = [False]

        def fetch(chunk):
            if len(chunk) == 1:
                # A single item skips the batch: fetch it directly.
                cache_key, stash_id = chunk[0]
                data = graphql(
                    endpoint,
                    REMOTE_PERFORMERS_QUERY,
                    {"id": stash_id},
                    {"ApiKey": provider["api_key"]},
                )
                scene = data.get("findScene") or {}
                return {
                    cache_key: {
                        "genders": [
                            (p.get("performer") or {}).get("gender")
                            for p in (scene.get("performers") or [])
                        ],
                        "tags": [t.get("name") for t in (scene.get("tags") or [])],
                    }
                }
            query, variables, aliases = remote_performers_batch_query(chunk)
            data = graphql(endpoint, query, variables, {"ApiKey": provider["api_key"]})
            result = {}
            for alias, cache_key in aliases.items():
                scene = data.get(alias) or {}
                result[cache_key] = {
                    "genders": [
                        (p.get("performer") or {}).get("gender")
                        for p in (scene.get("performers") or [])
                    ],
                    "tags": [t.get("name") for t in (scene.get("tags") or [])],
                }
            return result

        def work():
            while True:
                with lock:
                    if failed[0]:
                        return
                    i = index[0]
                    index[0] += 1
                if i >= len(chunks):
                    return
                chunk = chunks[i]
                try:
                    result = fetch(chunk)
                    with lock:
                        for key, value in result.items():
                            cache[key] = value["genders"]
                            if tags_cache is not None:
                                tags_cache[key] = value["tags"]
                except RuntimeError as error:
                    message = str(error)
                    if "403" in message or "401" in message:
                        # Auth failure: the endpoint will never succeed —
                        # stop scheduling its remaining batches.
                        with lock:
                            failed[0] = True
                        return
                    # Transient failure (429/5xx/timeout): retry once, then
                    # skip this batch and keep the endpoint alive.
                    try:
                        time.sleep(1.0)
                        result = fetch(chunk)
                        with lock:
                            for key, value in result.items():
                                cache[key] = value["genders"]
                                if tags_cache is not None:
                                    tags_cache[key] = value["tags"]
                    except RuntimeError:
                        pass

        # A genuinely bounded pool per endpoint: parallel within a provider
        # so a slow endpoint cannot stretch the scan to hours, but never more
        # than REMOTE_PREFETCH_WORKERS concurrent requests, so rate limits
        # degrade gracefully (skip + retry) instead of killing the endpoint.
        workers = [
            threading.Thread(target=work)
            for _ in range(min(REMOTE_PREFETCH_WORKERS, len(chunks)))
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

    for endpoint, scene_ids in pending.items():
        fetch_endpoint(endpoint, scene_ids)


def infer_review_state_path(server):
    config_dir = server.get("Dir")
    if not config_dir:
        raise RuntimeError("Stash config directory is unavailable")
    return Path(config_dir) / "tag-organizer" / INFER_STATE_NAME


def read_infer_state(server, token):
    path = infer_review_state_path(server)
    try:
        with path.open(encoding="utf-8") as source:
            state = json.load(source)
            return state if state.get("infer_token") == token else None
    except FileNotFoundError:
        return None


def write_infer_state(server, state):
    write_scan_state(infer_review_state_path(server), state)


def infer_latest_path(server):
    config_dir = server.get("Dir")
    if not config_dir:
        raise RuntimeError("Stash config directory is unavailable")
    return Path(config_dir) / "tag-organizer" / "infer-latest.json"


def write_infer_latest(server, token):
    write_scan_state(infer_latest_path(server), {"infer_token": token})


def read_infer_latest(server):
    path = infer_latest_path(server)
    try:
        with path.open(encoding="utf-8") as source:
            value = json.load(source)
            token = value.get("infer_token")
            return token if valid_scan_token(token) else None
    except (FileNotFoundError, ValueError):
        return None


def _infer_resolve_token(server, args):
    """The explicit token, or the latest scan's token when none is given."""
    token = args.get("infer_token")
    if token:
        if not valid_scan_token(token):
            raise ValueError("infer token must be 8-64 letters, numbers, underscores, or hyphens")
        return token
    return read_infer_latest(server)


def _infer_suggestion(scene, suggested, reason):
    return {
        "scene_id": scene["id"],
        "title": scene.get("title") or "",
        "screenshot": (scene.get("paths") or {}).get("screenshot") or "",
        "performers": len(scene.get("performers") or []),
        "suggested": suggested,
        "reason": reason,
        "applied": False,
        "skipped": False,
    }


def infer_scan_all(local_url, local_headers, server, token, providers):
    """Walk every scene and collect missing-tag suggestions (review queue).

    Local performer data is often incomplete (missing performers or genders),
    so scenes whose local cast cannot be classified are augmented from
    stash-box via their stash_ids before suggesting group tags.
    """
    state = {
        "infer_token": token,
        "status": "running",
        "phase": "walking",
        "scanned": 0,
        "total": 0,
        "suggestions": [],
        "error": None,
    }
    write_infer_state(server, state)
    write_infer_latest(server, token)
    try:
        local_tags = tag_index(
            graphql(local_url, TAGS_QUERY, headers=local_headers)["findTags"]["tags"]
        )
        group_ids = set()
        for name in INFER_GROUP_TAG_NAMES:
            group_ids |= local_tags.get(name.casefold(), set())
        compilation_ids = local_tags.get("compilation", set())
        vintage_ids = local_tags.get("vintage", set())
        solo_ids = local_tags.get("solo", set())
        masturbation_ids = local_tags.get("masturbation", set())

        all_scenes = []
        total = None
        page = 1
        while True:
            result = graphql(
                local_url,
                INFER_SCENES_QUERY,
                {"filter": {"page": page, "per_page": INFER_PAGE_SIZE}},
                local_headers,
            )["findScenes"]
            if total is None:
                total = result["count"]
            scenes = result["scenes"]
            if not scenes:
                break
            all_scenes.extend(scenes)
            state["scanned"] += len(scenes)
            state["total"] = total
            if state["scanned"] % 1000 == 0 or state["scanned"] == total:
                write_infer_state(server, state)
            stash_progress(state["scanned"], total)
            page += 1

        def threesome_described(scene):
            return THREESOME_WORDS.search(
                f"{scene.get('title') or ''}\n{scene.get('details') or ''}"
            )

        remote_gender_cache = {}
        remote_tag_cache = {}
        remote_needed = []
        for scene in all_scenes:
            performers = scene.get("performers") or []
            genders = [p.get("gender") for p in performers]
            described = threesome_described(scene)
            # Cross-check any group-candidate scene that has a stash-box link:
            # a complete-looking local cast can still undercount (a missing
            # performer changes the group verdict), so the remote cast is the
            # tiebreaker for count and unknown genders.
            if len(performers) >= 3 or len(performers) == 1 or (described and len(performers) < 3):
                if any(
                    stash_id.get("endpoint") in providers
                    for stash_id in (scene.get("stash_ids") or [])
                ):
                    remote_needed.append(scene)
        state["phase"] = "remote"
        write_infer_state(server, state)
        prefetch_remote_performers(
            remote_needed, providers, remote_gender_cache, remote_tag_cache
        )
        state["phase"] = "classifying"
        write_infer_state(server, state)

        suggestions = []
        for scene in all_scenes:
            scene_id = scene["id"]
            scene_tags = {tag["id"] for tag in (scene.get("tags") or [])}
            has_group = bool(scene_tags & group_ids)
            has_suggestion = {(item["scene_id"], item["suggested"]) for item in suggestions}
            performers = scene.get("performers") or []
            genders = [p.get("gender") for p in performers]
            remote_genders = None
            for stash_id in (scene.get("stash_ids") or []):
                key = (stash_id.get("endpoint"), stash_id.get("stash_id"))
                if key in remote_gender_cache:
                    remote_genders = remote_gender_cache[key]
                    break
            # The remote cast is authoritative when it lists at least as many
            # performers: a local cast that looks complete can still miss a
            # performer, which changes the group verdict entirely.
            if remote_genders is not None and len(remote_genders) >= len(performers):
                performer_count = len(remote_genders)
                genders = remote_genders
                source = " · StashDB"
            else:
                performer_count = len(performers)
                genders = [p.get("gender") for p in performers]
                source = ""
            if not has_group and performer_count >= 3:
                if performer_count == 3:
                    males = genders.count("MALE")
                    females = genders.count("FEMALE")
                    if males == 1 and females == 2:
                        suggested = "Threesome (BGG)"
                        composition = " (2F 1M)"
                    elif males == 2 and females == 1:
                        suggested = "Threesome (BBG)"
                        composition = " (1F 2M)"
                    elif females == 3:
                        # All-girl (GGG) trio: use the library's own
                        # lesbian-threesome tag when it exists, else plain.
                        suggested = (
                            "Threesome (Lesbian)"
                            if "threesome (lesbian)" in local_tags
                            else "Threesome"
                        )
                        composition = " (3F)"
                    else:
                        suggested = "Threesome"
                        composition = ""
                else:
                    suggested = "Group Sex"
                    composition = ""
                if (scene_id, suggested) not in has_suggestion:
                    suggestions.append(
                        _infer_suggestion(
                            scene, suggested,
                            f"{performer_count} performers{composition}{source}",
                        )
                    )
            # A description mentioning a threesome is only evidence when the
            # cast is unconfirmed: if stash-box resolved the cast and it is
            # not a group, the mention is setup flavor, not content.
            if not has_group and threesome_described(scene) and remote_genders is None:
                already = {item["suggested"] for item in suggestions if item["scene_id"] == scene_id}
                if not (already & set(INFER_THREESOME_SUGGESTIONS)):
                    suggestions.append(
                        _infer_suggestion(scene, "Threesome", "described as a threesome")
                    )
            if not (scene_tags & compilation_ids) and COMPILATION_WORDS.search(scene.get("title") or ""):
                if (scene_id, "Compilation") not in has_suggestion:
                    suggestions.append(
                        _infer_suggestion(scene, "Compilation", "compilation in the title")
                    )
            date = scene.get("date") or ""
            if not (scene_tags & vintage_ids) and date and date < "2000-01-01":
                if (scene_id, "Vintage") not in has_suggestion:
                    suggestions.append(
                        _infer_suggestion(scene, "Vintage", "released before 2000")
                    )
            # Solo: exactly one local performer, not already solo/masturbation
            # tagged, a stash-box cast confirming a single performer, and no
            # stash-box tag contradicting it (a 1-credit scene can still be a
            # couple/group scene on both sides).
            remote_tags = []
            if remote_genders is not None:
                for stash_id in (scene.get("stash_ids") or []):
                    key = (stash_id.get("endpoint"), stash_id.get("stash_id"))
                    if key in remote_tag_cache:
                        remote_tags = remote_tag_cache[key]
                        break
            remote_non_solo = any(
                INFER_NON_SOLO_TAG_TEXT.search(tag or "") for tag in remote_tags
            )
            local_tag_names = " ".join(
                (tag.get("name") or "") for tag in (scene.get("tags") or [])
            )
            if (
                len(performers) == 1
                and not (scene_tags & (solo_ids | masturbation_ids))
                and remote_genders is not None
                and len(remote_genders) == 1
                and not remote_non_solo
                and not INFER_NON_SOLO_TEXT.search(
                    f"{scene.get('title') or ''}\n{scene.get('details') or ''}"
                )
                and not INFER_NON_SOLO_TAG_TEXT.search(local_tag_names)
                and (scene_id, "Solo") not in has_suggestion
            ):
                suggestions.append(
                    _infer_suggestion(
                        scene, "Solo", f"single performer{source}"
                    )
                )
            if len(suggestions) % 500 == 0:
                state["suggestions"] = suggestions
                write_infer_state(server, state)
        state["status"] = "done"
        state["phase"] = "done"
        state["total"] = total
        state["suggestions"] = suggestions
        write_infer_state(server, state)
        stash_log(
            "i",
            f"Inference scan finished: {len(suggestions)} suggestions across {total} scenes"
            f" ({len(remote_gender_cache)} scenes augmented from stash-box)",
        )
        return state
    except Exception as error:
        state["status"] = "failed"
        state["error"] = str(error)
        write_infer_state(server, state)
        stash_log("e", f"Inference scan failed: {error}")
        raise


def _infer_require_token(server, args):
    token = _infer_resolve_token(server, args)
    if token is None:
        raise ValueError(
            "No inference scan has run yet — start one with “Scan for missing tags”."
        )
    return token


def _infer_state_for(server, token, action):
    state = read_infer_state(server, token)
    if state is None:
        raise ValueError(
            "That scan's review is no longer available — a newer scan replaced it "
            "or the state was cleaned. Run a new scan to see the latest suggestions."
        )
    return state


def infer_status(server, state, token):
    return {
        "status": state.get("status") if state is not None else "missing",
        "phase": state.get("phase") if state is not None else None,
        "infer_token": token if state is not None else None,
        "scanned": state.get("scanned", 0) if state is not None else 0,
        "total": state.get("total", 0) if state is not None else 0,
        "suggestion_count": len(state.get("suggestions", [])) if state is not None else 0,
        "error": state.get("error") if state is not None else None,
    }


def infer_review(state, args):
    suggestions = state.get("suggestions", [])
    page = max(1, int(args.get("page") or 1))
    per_page = max(1, min(200, int(args.get("per_page") or INFER_REVIEW_PAGE_SIZE)))
    start = (page - 1) * per_page
    return {
        "status": state.get("status"),
        "suggestion_count": len(suggestions),
        "pending_count": sum(
            1 for item in suggestions if not item.get("applied") and not item.get("skipped")
        ),
        "page": page,
        "per_page": per_page,
        "pages": (len(suggestions) + per_page - 1) // per_page,
        "items": suggestions[start : start + per_page],
    }


def infer_apply(local_url, local_headers, server, state, args):
    actions = args.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("actions must be a non-empty list")
    local_tags = tag_index(
        graphql(local_url, TAGS_QUERY, headers=local_headers)["findTags"]["tags"]
    )
    by_tag = {}
    for action in actions:
        scene_id = str(action.get("scene_id") or "")
        tag_name = str(action.get("tag_name") or "")
        if not scene_id or not tag_name:
            raise ValueError("each action needs scene_id and tag_name")
        by_tag.setdefault(tag_name, []).append(scene_id)
    results = []
    for tag_name, scene_ids in by_tag.items():
        matched = local_tags.get(tag_name.casefold(), set())
        if len(matched) != 1:
            results.append({
                "tag_name": tag_name,
                "resolved": False,
                "applied": 0,
                "failed": len(scene_ids),
                "error": "tag not found locally" if not matched else "tag name is ambiguous",
            })
            continue
        tag_id = next(iter(matched))
        applied = 0
        failure = None
        for offset in range(0, len(scene_ids), INFER_APPLY_BATCH):
            chunk = scene_ids[offset : offset + INFER_APPLY_BATCH]
            try:
                updated = graphql(
                    local_url,
                    BULK_UPDATE_SCENES_MUTATION,
                    {"input": {"ids": chunk, "tag_ids": {"ids": [tag_id], "mode": "ADD"}}},
                    local_headers,
                )["bulkSceneUpdate"]
                applied += len(updated or [])
            except RuntimeError as update_error:
                failure = failure or str(update_error)
        for scene_id in scene_ids:
            for item in state.get("suggestions", []):
                if item["scene_id"] == scene_id and item["suggested"] == tag_name:
                    item["applied"] = True
                    item["skipped"] = False
        results.append({
            "tag_name": tag_name,
            "resolved": True,
            "applied": applied,
            "failed": len(scene_ids) - applied,
            "error": failure,
        })
    write_infer_state(server, state)
    return {
        "processed": len(actions),
        "applied": sum(result["applied"] for result in results),
        "failed": sum(result["failed"] for result in results),
        "results": results,
    }


def infer_apply_all(local_url, local_headers, server, state):
    pending = [
        {"scene_id": item["scene_id"], "tag_name": item["suggested"]}
        for item in state.get("suggestions", [])
        if not item.get("applied") and not item.get("skipped")
    ]
    if not pending:
        return {
            "processed": 0,
            "applied": 0,
            "failed": 0,
            "results": [],
            "error": "no pending suggestions to apply",
        }
    return infer_apply(local_url, local_headers, server, state, {"actions": pending})


def infer_skip(server, state, args):
    scene_id = str(args.get("scene_id") or "")
    tag_name = str(args.get("tag_name") or "")
    if not scene_id or not tag_name:
        raise ValueError("scene_id and tag_name are required")
    for item in state.get("suggestions", []):
        if item["scene_id"] == scene_id and item["suggested"] == tag_name:
            item["skipped"] = True
            item["applied"] = False
    write_infer_state(server, state)
    return {"scene_id": scene_id, "tag_name": tag_name, "skipped": True}


def infer_unskip(server, state, args):
    scene_id = str(args.get("scene_id") or "")
    tag_name = str(args.get("tag_name") or "")
    if not scene_id or not tag_name:
        raise ValueError("scene_id and tag_name are required")
    for item in state.get("suggestions", []):
        if item["scene_id"] == scene_id and item["suggested"] == tag_name:
            item["skipped"] = False
            item["applied"] = False
    write_infer_state(server, state)
    return {"scene_id": scene_id, "tag_name": tag_name, "skipped": False}

if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        try:
            print(json.dumps({"output": run(json.load(sys.stdin))}))
        except (KeyError, RuntimeError, ValueError) as error:
            print(json.dumps({"error": str(error)}))
            raise SystemExit(1)
