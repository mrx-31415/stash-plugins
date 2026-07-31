#!/usr/bin/env bash
set -euo pipefail

remote="${1:?usage: $0 REMOTE_DIR LOCAL_DIR}"
local_dir="${2:?usage: $0 REMOTE_DIR LOCAL_DIR}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$local_dir"

while :; do
  rsync -a --partial -e 'ssh -o BatchMode=yes -p 40764' \
    "root@107.206.71.138:${remote%/}/" "$local_dir/" || true
  python3 "$script_dir/encode_performer_assets.py" --output-dir "$local_dir"
  [[ ! -e "$local_dir/COMPLETE" && ! -e "$local_dir/FAILED" ]] || exit 0
  sleep 30
done
