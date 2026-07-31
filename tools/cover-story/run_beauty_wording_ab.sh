#!/usr/bin/env bash
set -euo pipefail

: "${COMFY_SERVER:?Set COMFY_SERVER to the tokenized ComfyUI URL}"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
label="${COVER_STORY_LABEL:-performers-v23-beauty-wording}"
output_root="${COVER_STORY_OUTPUT_DIR:-/mnt/Misc/sd/cover-story/experiments}"
exceptional_label="$label-exceptional-ab"
breathtaking_label="$label-breathtaking-ab"
exceptional_dir="$output_root/$exceptional_label"
breathtaking_dir="$output_root/$breathtaking_label"
variants=(1 20 42 58 73 97 111 128 145 172 189 214 241 281 322 341 401 431 464 500)
common=(
  --server "$COMFY_SERVER"
  --mode performers-v6
  --workflow "$script_dir/workflows/krea2-turbo-fp8.json"
  --steps 12
  --cfg 1
  --bypass-strength 1.5
  --age-wording band
  --seed 2026072700
)

for variant in "${variants[@]}"; do common+=(--variant "$variant"); done

python3 "$script_dir/experiment_headshots.py" "${common[@]}" \
  --label "$exceptional_label" --download-dir "$exceptional_dir" --suffix A "$@"
python3 "$script_dir/experiment_headshots.py" "${common[@]}" \
  --label "$exceptional_label" --download-dir "$exceptional_dir" \
  --prompt-replace "fresh-faced, approachable, and photogenic" \
    "exceptionally beautiful, radiant, and highly photogenic" \
  --suffix B "$@"

if [[ " $* " != *" --dry-run "* ]]; then
  mkdir -p "$breathtaking_dir"
  cp -p "$exceptional_dir"/*_A__*.png "$breathtaking_dir/"
fi

python3 "$script_dir/experiment_headshots.py" "${common[@]}" \
  --label "$breathtaking_label" --download-dir "$breathtaking_dir" \
  --prompt-replace "fresh-faced, approachable, and photogenic" \
    "breathtakingly beautiful, striking, and highly photogenic" \
  --suffix B "$@"
