#!/usr/bin/env python3
"""Local BiRefNet extraction for the PoC runner's birefnet extract mode.

Standalone so the runner (system python3, Pillow only) can call it as a subprocess,
mirroring how CorridorKey runs as a separate remote process:

    .venv-birefnet/bin/python birefnet_extract.py \
        --source plate.png [--aperture aperture.png] [--model BiRefNet-general-tiny.onnx] \
        --output alpha.png

Reads source RGB, runs BiRefNet (letterboxed into the model's fixed 1024x1024),
post-multiplies the aperture if given (alpha *= 1 - aperture/255 -- this is the
mechanical fix for matting models keeping the flattened head blob as foreground),
and writes a single-channel alpha PNG. No hint input: BiRefNet needs none.

--upscale runs a super-resolution model over the plate BEFORE matting and scales the
alpha back down afterwards, so the upscaled pixels only ever inform the matte and
never reach the output: RGB and the pipeline's 0/0/0 px registration are untouched.
Measured on the head crop against a Lanczos control at identical matte resolution
(so the variable is learned sharpening, not resolution): retained screen-coloured
pixels 2,323 -> 1,990 and rim spill 64.3% -> 57.2%. Tiled, because the whole plate
at 2x is 1664x2496 and this VM has 7.4 GB.
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from birefnet_ab import available_providers, birefnet_alpha, make_session  # noqa: E402


def upscale(image, model_path, tile=512, overlap=32):
    """Super-resolve `image` in overlapping tiles, averaging the seams.

    Tiled for memory, not for speed: onnxruntime's arena is already disabled (see make_session)
    and a whole 832x1248 plate at once is what pushes this VM into swap. Overlaps are averaged
    rather than feathered -- the result feeds a matting model, not the output, so a slightly
    softer seam costs nothing."""
    session = make_session(model_path)
    name = session.get_inputs()[0].name

    def run(chunk):
        array = (np.asarray(chunk, dtype=np.float32) / 255.0).transpose(2, 0, 1)[None]
        out = session.run(None, {name: array})[0][0]
        return np.clip(out.transpose(1, 2, 0), 0.0, 1.0) * 255.0

    probe_size = min(64, image.width, image.height)
    scale = round(run(image.crop((0, 0, probe_size, probe_size))).shape[0] / probe_size)
    width, height = image.size
    canvas = np.zeros((height * scale, width * scale, 3), dtype=np.float32)
    weight = np.zeros((height * scale, width * scale, 1), dtype=np.float32)
    step = max(1, tile - overlap)
    for top in range(0, height, step):
        for left in range(0, width, step):
            box = (left, top, min(left + tile, width), min(top + tile, height))
            piece = run(image.crop(box))
            y, x = box[1] * scale, box[0] * scale
            canvas[y:y + piece.shape[0], x:x + piece.shape[1]] += piece
            weight[y:y + piece.shape[0], x:x + piece.shape[1]] += 1.0
    return Image.fromarray((canvas / np.maximum(weight, 1.0)).astype(np.uint8), mode="RGB")


# Peak anon-rss measured for a single 832x1248 plate through BiRefNet-general-tiny on CPU. The
# allocation lives in the decoder's deformable-convolution ASPP block
# (/decoder/decoder_block1/dec_att/aspp_deforms.2/atrous_conv/Mul_8) and is not reduced by disabling
# the arena, by mem-pattern, or by tiling the input -- only by running somewhere with more memory.
PEAK_MEMORY_MB = 6000
# Headroom over the peak. Small, because the point is only to refuse a run that is going to fail.
MEMORY_MARGIN_MB = 500


def available_memory_mb():
    """MemAvailable, i.e. what can be had without swapping -- not MemFree, which ignores reclaimable
    page cache and would refuse runs that would actually succeed. Returns None off Linux."""
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def refuse_if_memory_short(providers):
    """Refuse a CPU run that cannot fit, instead of letting the kernel choose a victim.

    On 2026-08-08/09 this OOM-killed a 7.4 GB dev box three times: it destroyed in-flight pipeline
    runs and endangered unrelated processes belonging to the user. The peak is not tunable (see
    PEAK_MEMORY_MB), so the only correct behaviour on a small machine is to decline and say where to
    run instead. GPU runs are exempt -- the allocation lands in VRAM."""
    if any("CUDA" in provider for provider in providers):
        return
    available = available_memory_mb()
    if available is not None and available < PEAK_MEMORY_MB + MEMORY_MARGIN_MB:
        sys.exit(
            f"refusing to matte locally: {available} MB available, need about "
            f"{PEAK_MEMORY_MB + MEMORY_MARGIN_MB} MB.\n"
            "BiRefNet peaks near 6 GB per plate on CPU and has OOM-killed this machine before.\n"
            "Run this on a pod (pod_bootstrap.sh step 8 provisions the GPU matting venv), or set "
            "COVER_STORY_ALLOW_LOW_MEMORY_MATTE=1 to override at your own risk."
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--aperture", help="L-mode mask; alpha is zeroed where it is > 127")
    parser.add_argument("--model", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                        "models", "BiRefNet-general-tiny.onnx"))
    parser.add_argument("--upscale", help="super-resolution ONNX run before matting; the alpha is "
                                          "scaled back down, so output RGB is unaffected")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if not os.environ.get("COVER_STORY_ALLOW_LOW_MEMORY_MATTE"):
        refuse_if_memory_short(available_providers())
    session = make_session(args.model)
    image = Image.open(args.source).convert("RGB")
    matte_input = upscale(image, args.upscale) if args.upscale else image
    alpha = birefnet_alpha(session, matte_input)
    if matte_input.size != image.size:
        alpha = np.asarray(Image.fromarray(alpha, mode="L").resize(image.size, Image.Resampling.LANCZOS),
                           dtype=np.uint8)
    if args.aperture:
        aperture = np.asarray(Image.open(args.aperture).convert("L"), dtype=np.float32) / 255.0
        alpha = (alpha.astype(np.float32) * (1.0 - aperture)).astype(np.uint8)
    Image.fromarray(alpha, mode="L").save(args.output)
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
