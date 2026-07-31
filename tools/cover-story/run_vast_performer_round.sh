#!/usr/bin/env bash
set -euo pipefail

name="${1:?usage: $0 RUN_NAME}"
[[ "$name" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "invalid run name" >&2; exit 2; }
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
remote_dir="/workspace/cover-story/$name"
local_dir="/mnt/Misc/sd/cover-story/$name"

ssh -o BatchMode=yes -p 40764 root@107.206.71.138 \
  "test ! -e '$remote_dir' && /workspace/stash-plugins/tools/cover-story/run_vast_performer_production.sh '$remote_dir'" &
producer=$!
trap 'kill "$producer" 2>/dev/null || true' EXIT
"$script_dir/mirror_vast_performer_run.sh" "$remote_dir" "$local_dir"
wait "$producer"
trap - EXIT
