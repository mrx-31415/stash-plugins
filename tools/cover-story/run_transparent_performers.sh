#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
output_dir="${COVER_STORY_OUTPUT_DIR:-/mnt/Misc/sd/cover-story/performers-transparent-v6-colorless-test}"
label="${COVER_STORY_LABEL:-performers-transparent-v6-colorless-test}"
if (($# == 0)); then
  set -- --variant 2
fi
server_args=()
if [[ " $* " != *" --codec-only "* ]]; then
  : "${COMFY_SERVER:?Set COMFY_SERVER to the tokenized ComfyUI URL}"
  server_args=(--server "$COMFY_SERVER")
fi

exec python3 "$script_dir/run_transparent_performers.py" \
  "${server_args[@]}" \
  --output-dir "$output_dir" \
  --label "$label" \
  --seed "${COVER_STORY_SEED:-2026072700}" \
  "$@"
