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

# 6b. comfy-kitchen must match ComfyUI's pin, or fp8 loading silently disables: an older kitchen
#     lacks the layout classes the repo imports (e.g. 0.2.10 vs the 0.2.26 pin missing
#     TensorCoreConvRotW4A4Layout), which makes _CK_AVAILABLE False and every fp8 model load crash
#     with "'NoneType' object has no attribute 'Params'". Compare against requirements.txt rather
#     than a fixed version so the repo stays the source of truth.
COMFY_VENV=$COMFY_DIR/.venv-cu128
if [ -x "$COMFY_VENV/bin/pip" ]; then
    wanted=$(grep -i '^comfy-kitchen' "$COMFY_DIR/requirements.txt" 2>/dev/null | sed 's/.*==//' | tr -d '[:space:]')
    if [ -n "$wanted" ]; then
        have=$("$COMFY_VENV/bin/pip" show comfy-kitchen 2>/dev/null | awk '/^Version/{print $2}')
        if [ "$have" != "$wanted" ]; then
            say comfy_kitchen "$have -> installing $wanted (required by requirements.txt)"
            "$COMFY_VENV/bin/pip" install -q "comfy-kitchen==$wanted" >/dev/null 2>&1 \
                && say comfy_kitchen "installed $wanted" || fail comfy_kitchen "pip install failed"
        else
            say comfy_kitchen "$have (matches pin)"
        fi
    fi
fi

# 7. CatVTON (the VITON clothes stage). Installed only when the runner asks for it -- preflight
#    touches $VOLUME/install-catvton for --clothes-mode viton, so the one-time multi-GB download
#    does not run for the chroma recipe. The wrapper (chflame163/ComfyUI_CatVTON_Wrapper) pads to
#    its internal 768x1024 working size and restores the output to the input size and position, so
#    registration to the carrier canvas survives; the plain pzc163 node crops instead and would
#    need an inverted crop transform, so do not swap the two.
CATVTON_FLAG=$VOLUME/install-catvton
if [ -f "$CATVTON_FLAG" ]; then
    NODE_DIR=$COMFY_DIR/custom_nodes/ComfyUI_CatVTON_Wrapper
    if [ ! -d "$NODE_DIR" ]; then
        git clone --depth 1 https://github.com/chflame163/ComfyUI_CatVTON_Wrapper.git "$NODE_DIR" \
            >/dev/null 2>&1 && say catvton "wrapper cloned" || fail catvton "wrapper clone failed"
    else
        say catvton "wrapper present"
    fi
    CATVTON_MODELS=$COMFY_DIR/models/CatVTON
    if [ ! -d "$CATVTON_MODELS/stable-diffusion-inpainting/unet" ]; then
        say catvton "downloading stable-diffusion-inpainting (one-time, several GB) ..."
        "$PYTHON" - "$CATVTON_MODELS" <<'EOF' >/dev/null 2>&1 || fail catvton "sd15 inpainting download failed"
import os, sys
from huggingface_hub import snapshot_download
root = sys.argv[1]
os.makedirs(root, exist_ok=True)
# Canonical home is the stable-diffusion-v1-5 org; runwayml/stable-diffusion-inpainting is only a
# redirect stub whose files 404. The pipeline needs just the scheduler/ and unet/ subfolders
# (CatVTON has no text conditioning), but snapshot_download fetches the whole diffusers repo.
snapshot_download(repo_id="stable-diffusion-v1-5/stable-diffusion-inpainting",
                  local_dir=os.path.join(root, "stable-diffusion-inpainting"))
EOF
    else
        say catvton "sd15 inpainting present"
    fi
    # The wrapper's pipeline also loads a VAE explicitly from models/CatVTON/sd-vae-ft-mse.
    if [ ! -f "$CATVTON_MODELS/sd-vae-ft-mse/diffusion_pytorch_model.safetensors" ]; then
        say catvton "downloading sd-vae-ft-mse (stabilityai, small) ..."
        "$PYTHON" - "$CATVTON_MODELS" <<'EOF' >/dev/null 2>&1 || fail catvton "vae download failed"
import os, sys
from huggingface_hub import snapshot_download
root = sys.argv[1]
os.makedirs(root, exist_ok=True)
snapshot_download(repo_id="stabilityai/sd-vae-ft-mse",
                  local_dir=os.path.join(root, "sd-vae-ft-mse"))
EOF
    else
        say catvton "sd-vae-ft-mse present"
    fi
    # attn_ckpt_version="mix" resolves to the mix-48k-1024 subfolder (the repo has no plain
    # attn_ckpt/ dir; the ckpts are named by training set). The pipeline does
    # load_checkpoint_in_model(attn_modules, models/CatVTON/mix-48k-1024/attention).
    if [ ! -f "$CATVTON_MODELS/mix-48k-1024/attention/model.safetensors" ]; then
        say catvton "downloading CatVTON mix attn checkpoint (one-time, ~1.7 GB) ..."
        "$PYTHON" - "$CATVTON_MODELS" <<'EOF' >/dev/null 2>&1 || fail catvton "attn ckpt download failed"
import os, sys
from huggingface_hub import snapshot_download
root = sys.argv[1]
os.makedirs(root, exist_ok=True)
snapshot_download(repo_id="zhengchong/CatVTON", local_dir=root, allow_patterns=["mix-48k-1024/*"])
EOF
    else
        say catvton "mix attn checkpoint present"
    fi
    [ -d "$CATVTON_MODELS/mix-48k-1024/attention" ] && [ -d "$CATVTON_MODELS/sd-vae-ft-mse" ] \
        && say catvton "weights present" || fail catvton "weights missing"
    # The wrapper resolves its models against ComfyUI's models dir at node instantiation; a wrapper
    # installed while ComfyUI was already running needs the node list to refresh.
    if pgrep -f 'main\.py.*--cache-lru' >/dev/null 2>&1; then
        curl -fsS -X POST "http://127.0.0.1:8188/object_info" -o /dev/null 2>/dev/null \
            || say catvton "node list refresh skipped (comfyui restart required before [viton])"
    fi
fi

# 8. GPU matting venv (BiRefNet + Real-ESRGAN, both ONNX). Optional and flag-gated the same way
#    CatVTON is: preflight touches $VOLUME/install-matting when the runner is asked to matte on the
#    pod, so a CPU-only workflow never pays for the download.
#
#    Why it is worth having: the same upscale-then-matte pass measured 25m30s per plate on the CPU
#    dev VM. birefnet_ab.available_providers() picks CUDA automatically when it is present, so
#    nothing downstream needs a flag.
#
#    The gate is that CUDAExecutionProvider is *listed*, not that the wheel installed. Stock
#    onnxruntime, or an onnxruntime-gpu built against a different CUDA runtime than the pod has,
#    both install cleanly and then fall back to CPU silently -- a 25-minute no-op instead of an
#    error, which is precisely the failure worth catching in preflight rather than mid-run.
MATTE_VENV=$VOLUME/matting-venv
if [ -f "$VOLUME/install-matting" ]; then
    if [ ! -x "$MATTE_VENV/bin/python" ]; then
        say matting "creating venv at $MATTE_VENV"
        uv venv "$MATTE_VENV" >/dev/null 2>&1 || fail matting "uv venv failed"
    fi
    if [ -x "$MATTE_VENV/bin/python" ]; then
        if ! "$MATTE_VENV/bin/python" -c 'import onnxruntime, numpy, PIL' >/dev/null 2>&1; then
            say matting "installing onnxruntime-gpu, numpy, pillow (one-time) ..."
            VIRTUAL_ENV=$MATTE_VENV uv pip install -q onnxruntime-gpu numpy pillow >/dev/null 2>&1 \
                || fail matting "uv pip install failed"
        fi
        providers=$("$MATTE_VENV/bin/python" -c \
            'import onnxruntime; print(",".join(onnxruntime.get_available_providers()))' 2>/dev/null)
        case "$providers" in
            *CUDAExecutionProvider*) say matting "CUDA provider available" ;;
            "")  fail matting "onnxruntime does not import in $MATTE_VENV" ;;
            *)   fail matting "no CUDA provider (got: $providers) -- matting would silently run on CPU" ;;
        esac
    fi
else
    say matting "skipped (touch $VOLUME/install-matting for GPU matting)"
fi

# 9. FLUX.1 dev, the alternative carrier generator. Flag-gated like CatVTON and the matting venv:
#    preflight touches $VOLUME/install-flux when --carrier-model flux is asked for, so a ~17 GB
#    download never runs for a Qwen-only session.
#
#    Why a second generator: the carrier's look survives every downstream fix. Its luminance is why
#    the Lab chroma transfer failed and its knee creases persist through every skin variant tried, so
#    the plastic result may be the base model rather than anything the pipeline does to it. One
#    all-in-one fp8 checkpoint, so ComfyUI's CheckpointLoaderSimple supplies model, CLIP and VAE
#    together instead of four files that must agree.
FLUX_CKPT=$COMFY_DIR/models/checkpoints/flux1-dev-fp8.safetensors
if [ -f "$VOLUME/install-flux1" ]; then
    if [ -s "$FLUX_CKPT" ]; then
        say flux "checkpoint present"
    else
        say flux "downloading flux1-dev-fp8 (one-time, ~17 GB) ..."
        mkdir -p "$(dirname "$FLUX_CKPT")"
        # --continue so a dropped connection resumes rather than restarting 17 GB, and a temp name
        # so an interrupted download is never mistaken for a complete checkpoint on the next run.
        if curl -fL --continue-at - -o "$FLUX_CKPT.part" \
            https://huggingface.co/Comfy-Org/flux1-dev/resolve/main/flux1-dev-fp8.safetensors \
            >/dev/null 2>&1; then
            mv "$FLUX_CKPT.part" "$FLUX_CKPT" && say flux "downloaded"
        else
            fail flux "download failed (partial kept at $FLUX_CKPT.part for resume)"
        fi
    fi
else
    say flux1 "skipped (touch $VOLUME/install-flux1 for the FLUX.1 carrier)"
fi

# 9b. FLUX.2 dev. Three split files, not one: its text encoder is Mistral 3 Small rather than
#     FLUX.1's T5+CLIP, so ComfyUI must be new enough to know that CLIPLoader type. preflight probes
#     /object_info for the enum before any of this downloads, so an incompatible runtime costs
#     seconds rather than 20 GB.
#
#     Worth the extra weight for a reason specific to this pipeline: FLUX.2 takes several reference
#     images natively, and STATUS.md's reference-collapse finding -- a mask-free Qwen edit returns
#     one of two references rather than blending them -- is the constraint every stage here is built
#     around.
if [ -f "$VOLUME/install-flux2" ]; then
    fetch() {  # url, destination; resumable, and never leaves a partial file under the real name
        [ -s "$2" ] && return 0
        mkdir -p "$(dirname "$2")"
        curl -fL --continue-at - -o "$2.part" "$1" >/dev/null 2>&1 && mv "$2.part" "$2"
    }
    base=https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files
    say flux2 "fetching dev checkpoint, Mistral text encoder and VAE (one-time, ~20 GB) ..."
    ok=yes
    fetch "$base/diffusion_models/flux2_dev_fp8mixed.safetensors" \
          "$COMFY_DIR/models/diffusion_models/flux2_dev_fp8mixed.safetensors" || ok=no
    fetch "$base/text_encoders/mistral_3_small_flux2_fp8.safetensors" \
          "$COMFY_DIR/models/text_encoders/mistral_3_small_flux2_fp8.safetensors" || ok=no
    fetch "$base/vae/flux2-vae.safetensors" "$COMFY_DIR/models/vae/flux2-vae.safetensors" || ok=no
    [ "$ok" = yes ] && say flux2 "present" || fail flux2 "one or more downloads failed (partials kept)"
else
    say flux2 "skipped (touch $VOLUME/install-flux2 for the FLUX.2 carrier)"
fi

exit $status
