#!/usr/bin/env bash
set -euo pipefail

: "${COMFY_SERVER:?Set COMFY_SERVER to the tokenized ComfyUI URL}"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$script_dir/run_corridorkey_poc.py" \
  --server "$COMFY_SERVER" \
  --source-dir /mnt/Misc/sd/cover-story/experiments/performers-cutout-poc \
  --output-dir /mnt/Misc/sd/cover-story/performer-corridorkey-poc \
  "$@"
