#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

python3 "$script_dir/curate_personas.py" --write "$@"
python3 "$script_dir/export_personas.py"
