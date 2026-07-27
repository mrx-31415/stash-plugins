#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export COVER_STORY_LABEL=performers-v16-production
export COVER_STORY_OUTPUT_DIR=/mnt/Misc/sd/cover-story/experiments/performers-v16-production

exec "$script_dir/run_production.sh" --start 291 "$@"
