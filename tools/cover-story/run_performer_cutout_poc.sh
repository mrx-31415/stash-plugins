#!/usr/bin/env bash
set -euo pipefail

: "${COMFY_SERVER:?Set COMFY_SERVER to the tokenized ComfyUI URL}"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
label="${COVER_STORY_LABEL:-performer-screen-poc-20260728}"
output_dir="${COVER_STORY_OUTPUT_DIR:-/mnt/Misc/sd/cover-story/$label}"
raw_dir="$output_dir/raw"
seed="${COVER_STORY_SEED:-2026072800}"
palette="tailored camel jacket over an ivory silk blouse with charcoal trousers and black heels; no green, lime, teal, turquoise, or cyan fabric or accessories"
control="tailored jacket over a silk blouse with trousers and heels; separate visible garments use naturally coordinated contrasting colors and distinct materials"

for case in palette-green palette-blue control-green control-blue; do
  screen="${case##*-}"
  wardrobe="$palette"
  [[ "$case" == control-* ]] && wardrobe="$control"
  python3 "$script_dir/experiment_headshots.py" \
    --server "$COMFY_SERVER" \
    --mode performers-v6 \
    --variant 2 \
    --label "$label" \
    --workflow "$script_dir/workflows/krea2-turbo-fp8.json" \
    --steps 12 \
    --cfg 1 \
    --bypass-strength 1.5 \
    --age-wording band \
    --seed "$seed" \
    --background "uniform seamless chroma-key $screen (#$([[ "$screen" == green ]] && echo 00ff00 || echo 0000ff)) background filling the entire frame, flat and evenly lit, with no gradient, texture, scenery, floor line, cast shadow, reflection, props, or color spill" \
    --wardrobe "$wardrobe" \
    --suffix "$case" \
    --download-dir "$raw_dir" \
    "$@"
done

exec python3 "$script_dir/run_corridorkey_poc.py" \
  --server "$COMFY_SERVER" \
  --source-dir "$raw_dir" \
  --output-dir "$output_dir"
