#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
output_dir="${COVER_STORY_OUTPUT_DIR:-/mnt/Misc/sd/cover-story/performer-backgrounds}"
server_args=()
if [[ " $* " != *" --codec-only "* && " $* " != *" --dry-run "* ]]; then
  : "${COMFY_SERVER:?Set COMFY_SERVER to the tokenized ComfyUI URL}"
  server_args=(--server "$COMFY_SERVER")
fi

exec python3 "$script_dir/run_performer_backgrounds.py" \
  "${server_args[@]}" \
  --output-dir "$output_dir" \
  --label "${COVER_STORY_LABEL:-performer-backgrounds-v1}" \
  --seed "${COVER_STORY_SEED:-2026073000}" \
  "$@"
