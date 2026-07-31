#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

python3 "$script_dir/encode_performer_assets.py" "$@"
python3 "$script_dir/export_scene_assets.py"
