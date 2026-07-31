#!/usr/bin/env bash
set -euo pipefail

output_dir="${1:?usage: $0 OUTPUT_DIR}"
tools=/workspace/stash-plugins/tools/cover-story
key_root=/workspace/CorridorKey
mkdir -p "$output_dir"
trap 'touch "$output_dir/FAILED"' ERR

cd "$key_root"
uv run python -u "$tools/run_transparent_performers.py" \
  --server http://127.0.0.1:18188 \
  --output-dir "$output_dir" \
  --label performers-transparent-production-v9 \
  --corridorkey-root "$key_root" \
  --start 1 --stop 500

touch "$output_dir/COMPLETE"
