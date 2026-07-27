#!/usr/bin/env bash
set -euo pipefail

: "${COMFY_SERVER:?Set COMFY_SERVER to the tokenized ComfyUI URL}"

variants=(5 19 22 52 70 111 126 128 141 150 167 194)
negative="cartoon, illustration, anime, doll-like face, plastic skin, waxy skin, exaggerated facial proportions, deep wrinkles, pronounced forehead lines, heavy under-eye bags, sagging skin, exaggerated aging"
common=(
  --server "$COMFY_SERVER"
  --mode performers-v6
  --label performers-v19-bypass-midpoint-ab
  --workflow tools/cover-story/workflows/krea2-turbo-fp8.json
  --steps 12
  --cfg 1.25
  --negative "$negative"
  --age-wording band
  --seed 2026072700
  --download-dir /mnt/Misc/sd/cover-story/experiments/performers-v19-bypass-midpoint-ab
)

for variant in "${variants[@]}"; do common+=(--variant "$variant"); done

python3 tools/cover-story/experiment_headshots.py "${common[@]}" --bypass-strength 1 --suffix A "$@"
python3 tools/cover-story/experiment_headshots.py "${common[@]}" --bypass-strength 1.5 --suffix B "$@"
