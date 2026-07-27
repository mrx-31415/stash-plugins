#!/usr/bin/env bash
set -euo pipefail

: "${COMFY_SERVER:?Set COMFY_SERVER to the tokenized ComfyUI URL}"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
label="${COVER_STORY_LABEL:-performers-production}"
output_dir="${COVER_STORY_OUTPUT_DIR:-/mnt/Misc/sd/cover-story/experiments/$label}"

exec python3 "$script_dir/experiment_headshots.py" \
  --server "$COMFY_SERVER" \
  --mode performers-v6 \
  --label "$label" \
  --workflow "$script_dir/workflows/krea2-turbo-fp8.json" \
  --steps 12 \
  --cfg 1 \
  --bypass-strength 1.5 \
  --age-wording band \
  --seed "${COVER_STORY_SEED:-2026072700}" \
  --download-dir "$output_dir" \
  "$@"
