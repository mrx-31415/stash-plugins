#!/usr/bin/env bash
set -euo pipefail

: "${COMFY_SERVER:?Set COMFY_SERVER to the tokenized ComfyUI URL}"

variants=(5 11 14 19 22 48 51 52 70 79 91 100 111 126 128 131 137 138 141 150 153 167 169 194)
baseline="cartoon, illustration, anime, doll-like face, plastic skin, waxy skin, exaggerated facial proportions, deep wrinkles, pronounced forehead lines, heavy under-eye bags, sagging skin, exaggerated aging"
treatment="anime, manga, cartoon, illustration, 3D render, CGI, video-game character, doll, mannequin, beauty filter, airbrushed skin, over-smoothed skin, porcelain skin, plastic skin, waxy skin, uncanny face, glassy eyes, lifeless eyes, oversized eyes, tiny nose, pointed chin, exaggerated facial proportions, deep wrinkles, pronounced forehead lines, heavy under-eye bags, sagging skin, exaggerated aging"
common=(
  --server "$COMFY_SERVER"
  --mode performers-v6
  --label performers-v17-negative-ab
  --workflow tools/cover-story/workflows/krea2-turbo-fp8.json
  --steps 12
  --cfg 1.25
  --bypass-strength 2
  --age-wording band
  --seed 2026072700
  --download-dir /mnt/Misc/sd/cover-story/experiments/performers-v17-negative-ab
)

for variant in "${variants[@]}"; do common+=(--variant "$variant"); done

python3 tools/cover-story/experiment_headshots.py "${common[@]}" --negative "$baseline" --suffix A "$@"
python3 tools/cover-story/experiment_headshots.py "${common[@]}" --negative "$treatment" --suffix B "$@"
