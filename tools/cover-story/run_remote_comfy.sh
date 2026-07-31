#!/usr/bin/env bash
set -euo pipefail

runner="${1:?usage: $0 RUNNER OUTPUT_DIR [RUNNER_ARGS...]}"
output_dir="${2:?usage: $0 RUNNER OUTPUT_DIR [RUNNER_ARGS...]}"
shift 2
port="${COMFY_TUNNEL_PORT:-18189}"

ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -p 40764 \
  -L "$port:127.0.0.1:18188" root@107.206.71.138 &
tunnel=$!
trap 'kill "$tunnel" 2>/dev/null || true' EXIT

python3 -u "$runner" \
  --server "http://127.0.0.1:$port" \
  --output-dir "$output_dir" \
  "$@"
