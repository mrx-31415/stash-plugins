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
from birefnet_ab import birefnet_alpha, make_session  # noqa: E402


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
