#!/usr/bin/env python3
"""Offline A/B: BiRefNet (general-tiny ONNX, CPU) vs CorridorKey on the existing
qwen2512-skin-head-clothes-poc-v3 plates. No pod, no GPU, no ComfyUI.

Runs BiRefNet on the same source RGB plates CorridorKey was given, writes:
  - alpha maps for both keyers
  - checkerboard composites for both
  - alpha difference map (CK - BN, inverted so light = CK more opaque)
  - metrics.json: coverage, enclosed-hole counts (speckle) overall and in the
    hair / feet / waist regions -- the three known defect sites.

Hole/speckle counting: label the alpha<128 region, count components fully
enclosed by alpha>=128 (holes), plus isolated small foreground islands
(alpha>=128 components < 60 px). Fewer holes in a region = cleaner matte.
"""

import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw

ROOT = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-skin-head-clothes-poc-v3")
OUT = Path("/mnt/Misc/sd/cover-story/birefnet-ab-v1")
MODEL = Path(__file__).resolve().parent / "models" / "BiRefNet-general-tiny.onnx"

# Canvas 832x1248. Regions of interest (the known defect sites).
REGIONS = {
    "full": (0, 0, 832, 1248),
    "hair": (220, 0, 612, 320),      # head/hair band at the top
    "waist": (250, 430, 582, 660),   # bodice/hip seam zone
    "feet": (250, 1030, 582, 1248),  # shoes / ankles
    "neckline": (280, 180, 552, 400),
}

MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 3, 1, 1)
MODEL_INPUT = 1024  # tiny swin export is fixed at 1024x1024

PLATES = [
    # (plate file, CorridorKey alpha file, key screen, name)
    ("identity.png", "poc:qwen2512-identity-alpha.png", "blue", "identity"),
    ("clothes.png", "poc:qwen2512-clothes-alpha.png", "green", "clothes"),
]


def skip_existing(out, name):
    return all((out / f"{name}-{suffix}").is_file() for suffix in
               ("bn-alpha.png", "bn-checker.png", "ck-checker.png", "alpha-diff.png"))


def birefnet_alpha(session, image):
    """Letterbox into the model's fixed 1024x1024 (scale-to-fit, pad with the plate's
    own corner/screen colour, so padding never reads as foreground), then crop the
    alpha back to the original canvas. Avoids the aspect distortion of a straight
    squash to 1024x1024."""
    w, h = image.size
    scale = MODEL_INPUT / max(w, h)
    target = (round(w * scale), round(h * scale))
    resized = image.resize(target, Image.Resampling.BICUBIC)
    pad_color = image.getpixel((2, 2))  # top-left: screen colour
    canvas = Image.new("RGB", (MODEL_INPUT, MODEL_INPUT), pad_color)
    canvas.paste(resized, ((MODEL_INPUT - target[0]) // 2, (MODEL_INPUT - target[1]) // 2))
    img = np.asarray(canvas, dtype=np.float32) / 255.0
    img = img.transpose(2, 0, 1)[None]
    img = (img - MEAN) / STD
    logits = session.run(None, {session.get_inputs()[0].name: img.astype(np.float32)})[0]
    alpha = 1.0 / (1.0 + np.exp(-logits))[0, 0]
    top = (MODEL_INPUT - target[1]) // 2
    left = (MODEL_INPUT - target[0]) // 2
    alpha = alpha[top:top + target[1], left:left + target[0]]
    alpha_img = Image.fromarray((np.clip(alpha, 0, 1) * 255).astype(np.uint8))
    alpha_img = alpha_img.resize(image.size, Image.Resampling.BILINEAR)
    return np.asarray(alpha_img, dtype=np.uint8)


def checkerboard(size, cell=32):
    image = Image.new("RGBA", size, (42, 42, 42, 255))
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(86, 86, 86, 255))
    return image


def composite_on_checker(rgba, size):
    return Image.alpha_composite(checkerboard(size), rgba)


def enclosed_holes(alpha_arr, region, fg_min=128):
    """Label alpha<fg_min components inside the region; count those fully enclosed
    by foreground (no 4-neighbor contact with the region border)."""
    x0, y0, x1, y1 = region
    sub = alpha_arr[y0:y1, x0:x1]
    fg = sub >= fg_min
    bg = ~fg
    h, w = bg.shape
    labels = np.zeros(bg.shape, dtype=np.int32)
    current = 0
    stack = []
    for i in range(h):
        for j in range(w):
            if bg[i, j] and labels[i, j] == 0:
                current += 1
                labels[i, j] = current
                stack.append((i, j))
                while stack:
                    a, b = stack.pop()
                    for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        na, nb = a + da, b + db
                        if 0 <= na < h and 0 <= nb < w and bg[na, nb] and labels[na, nb] == 0:
                            labels[na, nb] = current
                            stack.append((na, nb))
    enclosed = []
    for label in range(1, current + 1):
        ys, xs = np.where(labels == label)
        touches_border = (ys.min() == 0 or ys.max() == h - 1 or xs.min() == 0 or xs.max() == w - 1)
        if not touches_border:
            enclosed.append(int((labels == label).sum()))
    return {"holes": len(enclosed), "hole_px": int(sum(enclosed))}


def region_metrics(alpha_arr, region):
    x0, y0, x1, y1 = region
    sub = alpha_arr[y0:y1, x0:x1]
    fg = sub >= 128
    # Boundary quality: pixels in the soft mid-range (25..230) indicate feathered
    # or ragged edges; the count of tiny isolated foreground components (<= 24 px)
    # indicates speckle (e.g. the black noise around shoes).
    mid = ((sub >= 25) & (sub <= 230)).sum()
    h, w = fg.shape
    labels = np.zeros(fg.shape, dtype=np.int32)
    current = 0
    stack = []
    for i in range(h):
        for j in range(w):
            if fg[i, j] and labels[i, j] == 0:
                current += 1
                labels[i, j] = current
                stack.append((i, j))
                while stack:
                    a, b = stack.pop()
                    for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        na, nb = a + da, b + db
                        if 0 <= na < h and 0 <= nb < w and fg[na, nb] and labels[na, nb] == 0:
                            labels[na, nb] = current
                            stack.append((na, nb))
    counts = np.bincount(labels.ravel())[1:]
    specks = int((counts <= 24).sum())
    return {
        "fg_px": int(fg.sum()),
        "fg_frac": round(float(fg.mean()), 4),
        "mid_px": int(mid),
        "specks": specks,
        **enclosed_holes(alpha_arr, region),
    }


def available_providers(prefer_gpu=True):
    """CUDA first when the runtime actually has it, CPU otherwise.

    Auto-detected rather than configured, so the same script runs on this 7.4 GB CPU VM and on a
    pod GPU without a flag. The difference is not marginal: a full-plate upscale-then-matte measured
    **25m30s on this VM's CPU**, which is why --matte-upscale ships off by default. Requires
    onnxruntime-gpu on the pod; stock onnxruntime lists CPU only and this silently stays on CPU."""
    if prefer_gpu and "CUDAExecutionProvider" in ort.get_available_providers():
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def make_session(model_path=MODEL, providers=None):
    """Memory-lean session: without enable_cpu_mem_arena=False, onnxruntime's default CPU memory
    arena grows to 5GB+ per process and the 7.4GB VM OOM-kills concurrent jobs. The arena settings
    are harmless on CUDA, where allocation is handled by the GPU allocator instead."""
    so = ort.SessionOptions()
    so.enable_cpu_mem_arena = False
    so.enable_mem_pattern = False
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.intra_op_num_threads = 4
    return ort.InferenceSession(str(model_path), so, providers=providers or available_providers())


def main():
    if not MODEL.is_file():
        sys.exit(f"model not found: {MODEL}")
    if not (ROOT / PLATES[0][0]).is_file():
        sys.exit(f"plate root not found: {ROOT}")
    OUT.mkdir(parents=True, exist_ok=True)
    session = make_session()

    report = {}
    if (OUT / "metrics.json").is_file():
        report = json.loads((OUT / "metrics.json").read_text())
    for plate_name, ck_alpha_name, screen, name in PLATES:
        if skip_existing(OUT, name):
            print(f"{name}: skipped (outputs exist)", flush=True)
            continue
        try:
            plate = Image.open(ROOT / plate_name).convert("RGB")
            ck = np.asarray(Image.open(ROOT / ck_alpha_name).convert("L"), dtype=np.uint8)
            bn = birefnet_alpha(session, plate)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"{name}: FAILED {exc}", flush=True)
            continue

        Image.fromarray(ck).save(OUT / f"{name}-ck-alpha.png")
        Image.fromarray(bn).save(OUT / f"{name}-bn-alpha.png")

        diff = np.clip(ck.astype(int) - bn.astype(int) + 128, 0, 255).astype(np.uint8)
        Image.fromarray(diff).save(OUT / f"{name}-alpha-diff.png")

        src_rgba = plate.convert("RGBA")
        ck_rgba = src_rgba.copy(); ck_rgba.putalpha(Image.fromarray(ck))
        bn_rgba = src_rgba.copy(); bn_rgba.putalpha(Image.fromarray(bn))
        composite_on_checker(ck_rgba, plate.size).convert("RGB").save(OUT / f"{name}-ck-checker.png")
        composite_on_checker(bn_rgba, plate.size).convert("RGB").save(OUT / f"{name}-bn-checker.png")

        report[name] = {
            "screen": screen,
            "regions": {
                rname: {
                    "ck": region_metrics(ck, region),
                    "bn": region_metrics(bn, region),
                }
                for rname, region in REGIONS.items()
            },
            "mean_abs_alpha_diff": float(np.abs(ck.astype(int) - bn.astype(int)).mean()),
        }
        print(f"{name}: holes  CK {report[name]['regions']['full']['ck']['hole_px']:>7}px  "
              f"BN {report[name]['regions']['full']['bn']['hole_px']:>7}px  "
              f"mean|diff| {report[name]['mean_abs_alpha_diff']:.1f}")

    (OUT / "metrics.json").write_text(json.dumps(report, indent=2))
    print(f"\noutputs -> {OUT}")


if __name__ == "__main__":
    main()
