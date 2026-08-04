#!/bin/sh
# Restore the parts of a RunPod pod that do not survive being recreated.
#
# /workspace is a network volume and persists; / and /root are an ephemeral overlay. CorridorKey's
# 9.4 GB venv lives on the volume and survives intact, but uv baked an interpreter path into it:
#
#   .venv/bin/python -> /root/.local/share/uv/python/cpython-3.13-linux-x86_64-gnu/bin/python3.13
#
# That target is on the overlay, so the venv arrives dangling on every new pod. Rebuilding it costs
# a 9.4 GB reinstall. Keeping uv's data directory on the volume and re-pointing the XDG path at it
# costs a symlink, and leaves every path already recorded inside the venv valid.
#
# Idempotent: preflight runs this on every start and it is a no-op once a pod is healthy. Exits
# non-zero if CorridorKey still cannot import torch, which is the failure that actually matters.
set -eu

VOLUME=/workspace/runpod-slim
UV_HOME=$VOLUME/uv
CORRIDORKEY=$VOLUME/CorridorKey
PYTHON=$CORRIDORKEY/.venv/bin/python
export PATH=$VOLUME/bin:$PATH

status=0
say() { printf '%-22s %s\n' "$1" "$2"; }
fail() { say "$1" "FAIL: $2"; status=1; }

# 1. uv's data directory. Everything uv installs -- interpreters, the wheel cache -- goes under
#    $XDG_DATA_HOME/uv, i.e. /root/.local/share/uv. Pointing that at the volume is what makes the
#    interpreter survive; it must happen before any uv command runs.
mkdir -p "$UV_HOME" /root/.local/share
if [ -d /root/.local/share/uv ] && [ ! -L /root/.local/share/uv ]; then
    fail uv_data_dir "/root/.local/share/uv is a real directory; move it to $UV_HOME and rerun"
else
    ln -sfn "$UV_HOME" /root/.local/share/uv
    say uv_data_dir "-> $UV_HOME"
fi

# 2. uv itself, installed onto the volume so the next pod inherits it.
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$VOLUME/bin" sh >/dev/null 2>&1 \
        || fail uv "installer failed"
fi
command -v uv >/dev/null 2>&1 && say uv "$(uv --version 2>/dev/null) at $(command -v uv)"

# 3. The interpreter the venv expects. uv names its install directories with the patch version
#    (cpython-3.13.5-...) while older venvs recorded a version-less alias, so link the alias at
#    whatever 3.13 is actually present rather than assuming the names match.
wanted=$(readlink "$PYTHON" 2>/dev/null || true)
if [ -z "$wanted" ]; then
    fail interpreter "$PYTHON is not a symlink; is CorridorKey installed?"
elif [ -x "$wanted" ]; then
    say interpreter "already resolves"
else
    uv python install 3.13 >/dev/null 2>&1 || true
    if [ ! -x "$wanted" ]; then
        expected=${wanted%/bin/*}
        actual=$(ls -d "$UV_HOME"/python/cpython-3.13*-linux-x86_64-gnu 2>/dev/null | head -1)
        if [ -n "$actual" ] && [ "$actual" != "$expected" ]; then
            ln -sfn "$actual" "$expected"
            say interpreter "aliased $(basename "$expected") -> $(basename "$actual")"
        fi
    fi
    [ -x "$wanted" ] && say interpreter "restored" || fail interpreter "still missing: $wanted"
fi

# 4. rsync. An apt package on the overlay, so it genuinely has to be reinstalled per pod --
#    standalone_alpha() stages and retrieves through it, and without it extraction fails only
#    after every generation has already run.
if ! command -v rsync >/dev/null 2>&1; then
    (apt-get update -qq && apt-get install -y -qq rsync) >/dev/null 2>&1 || fail rsync "apt-get failed"
fi
command -v rsync >/dev/null 2>&1 && say rsync "$(command -v rsync)"

# 5. The real gate. A resolving symlink proves nothing if the venv's compiled extensions do not
#    load against the interpreter that got installed.
if [ -x "$PYTHON" ] && "$PYTHON" -c 'import torch' >/dev/null 2>&1; then
    say corridorkey "imports torch"
else
    fail corridorkey "cannot import torch"
fi
[ -d "$CORRIDORKEY/CorridorKeyModule/checkpoints" ] \
    && say checkpoints "present" || fail checkpoints "missing"

# 6. ComfyUI's node-output cache. The default HierarchicalCache calls clean_unused() after every
#    prompt and drops any node output not present in the *current* prompt, so alternating an edit
#    graph with a SAM graph evicts each model's loader output in turn -- and evicting the loader
#    drops the last reference to the ModelPatcher, so the weights leave system RAM and the next run
#    re-reads them from disk. --cache-lru keeps N node results across prompts instead. This pod has
#    186 GB of RAM against roughly 36 GB of weights, so the trade is free.
#
#    The tidier fix is CUSTOM_ARGS="--cache-lru 20" on the RunPod template, which /start.sh appends
#    to its own args; that survives recreation without this block having to restart anything. This
#    exists because the template is not always ours to edit, and it is a no-op once the flag is set.
COMFY_DIR=$VOLUME/ComfyUI
if pgrep -f 'main\.py.*--cache-lru' >/dev/null 2>&1; then
    say comfy_cache "--cache-lru already set"
elif ! pid=$(pgrep -f "$COMFY_DIR.*main\.py" || pgrep -f 'python main\.py'); then
    say comfy_cache "ComfyUI not running; skipped"
else
    args=$(tr '\0' ' ' < "/proc/$pid/cmdline" | sed 's/^[^ ]* *main\.py *//')
    kill "$pid" 2>/dev/null || true
    # ComfyUI is a child of /start.sh, whose `wait` returns into a message and `sleep infinity`
    # rather than a restart, so it has to be relaunched here.
    i=0; while [ $i -lt 30 ] && kill -0 "$pid" 2>/dev/null; do sleep 1; i=$((i + 1)); done
    (cd "$COMFY_DIR" && PATH="$COMFY_DIR/.venv-cu128/bin:$PATH" VIRTUAL_ENV="$COMFY_DIR/.venv-cu128" \
        nohup python main.py $args --cache-lru 20 >/workspace/comfyui.log 2>&1 &)
    i=0
    while [ $i -lt 120 ]; do
        curl -fsS -o /dev/null "http://127.0.0.1:8188/system_stats" 2>/dev/null && break
        sleep 2; i=$((i + 1))
    done
    pgrep -f 'main\.py.*--cache-lru' >/dev/null 2>&1 \
        && say comfy_cache "restarted with --cache-lru 20" \
        || fail comfy_cache "restart failed; see /workspace/comfyui.log"
fi

exit $status
