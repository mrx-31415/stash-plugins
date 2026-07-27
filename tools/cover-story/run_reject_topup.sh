#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export COVER_STORY_LABEL=performers-v21-reject-topup
export COVER_STORY_OUTPUT_DIR=/mnt/Misc/sd/cover-story/experiments/performers-v21-reject-topup

variants=(
  5 8 19 22 29 33 37 38 40 42 49 54 56 66 69 70 78 81 88 111 117 118
  123 126 128 135 139 140 141 144 148 149 150 165 167 172 177 189 191 192
  194 198 199 201 206 208 223 227 238 241 243 255 260 261 263 267 273 281
  285 286 288 289
)
args=()
for variant in "${variants[@]}"; do args+=(--variant "$variant"); done

exec "$script_dir/run_production.sh" "${args[@]}" "$@"
