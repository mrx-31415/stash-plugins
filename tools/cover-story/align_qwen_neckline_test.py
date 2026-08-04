#!/usr/bin/env python3
"""Register Qwen wardrobe layers to one fixed carrier and build a review plate."""

import argparse
from collections import deque
import hashlib
import html
import json
import shutil
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter


HEAD_BAND = 0.26


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def green_foreground(image):
    """Return non-green pixels; sufficient for this fixed green-screen carrier."""
    image = image.convert("RGB")
    pixels = image.load()
    mask = Image.new("L", image.size)
    output = mask.load()
    for y in range(image.height):
        for x in range(image.width):
            red, green, blue = pixels[x, y]
            output[x, y] = 0 if green > red + 24 and green > blue + 24 else 255
    return mask


def head_box(image, alpha=None):
    # ponytail: fixed head band; use pose landmarks when framing varies.
    mask = alpha.convert("L") if alpha is not None else green_foreground(image)
    band = round(mask.height * HEAD_BAND)
    box = mask.crop((0, 0, mask.width, band)).getbbox()
    if not box:
        raise ValueError("could not find a head anchor")
    return box


def head_component(image):
    mask = green_foreground(image)
    width, height = mask.size
    box = head_box(image)
    limit = round(height * 0.43)
    pixels = mask.tobytes()
    seed = None
    for y in range(box[1], min(box[3], limit)):
        for x in range(box[0], box[2]):
            if pixels[y * width + x]:
                seed = (x, y)
                break
        if seed:
            break
    if seed is None:
        raise ValueError("could not seed head component")
    visited = bytearray(width * height)
    queue = deque([seed])
    result = Image.new("L", mask.size)
    output = result.load()
    while queue:
        x, y = queue.popleft()
        index = y * width + x
        if visited[index] or y >= limit or not pixels[index]:
            continue
        visited[index] = 1
        output[x, y] = 255
        for next_x in (x - 1, x, x + 1):
            for next_y in (y - 1, y, y + 1):
                if 0 <= next_x < width and 0 <= next_y < limit:
                    next_index = next_y * width + next_x
                    if not visited[next_index] and pixels[next_index]:
                        queue.append((next_x, next_y))
    return result


def checkerboard(size, cell=32):
    image = Image.new("RGB", size, (42, 42, 42))
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(86, 86, 86))
    return image.convert("RGBA")


def paste_clipped(canvas, image, xy):
    x, y = xy
    left, top = max(0, -x), max(0, -y)
    right, bottom = min(image.width, canvas.width - x), min(image.height, canvas.height - y)
    if right > left and bottom > top:
        canvas.alpha_composite(image.crop((left, top, right, bottom)), (max(0, x), max(0, y)))


def warp(layer, source_box, target_box):
    source_width = source_box[2] - source_box[0]
    target_width = target_box[2] - target_box[0]
    scale = target_width / source_width
    resized = layer.resize((round(layer.width * scale), round(layer.height * scale)), Image.Resampling.LANCZOS)
    source_center = (source_box[0] + source_width / 2, source_box[1])
    target_center = ((target_box[0] + target_box[2]) / 2, target_box[1])
    offset = (
        round(target_center[0] - source_center[0] * scale),
        round(target_center[1] - source_center[1] * scale),
    )
    result = Image.new("RGBA", layer.size)
    paste_clipped(result, resized, offset)
    return result, scale, offset


def remove_head(layer, mask):
    alpha = ImageChops.multiply(layer.getchannel("A"), ImageChops.invert(mask))
    result = layer.copy()
    result.putalpha(alpha)
    return result


def composite(carrier, layer):
    return Image.alpha_composite(carrier.convert("RGBA"), layer).convert("RGB")


def save(image, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", compress_level=1)


def write_review(root, entries):
    cards = []
    for entry in entries:
        slug = html.escape(entry["slug"])
        cards.append(f"""
        <article class="card">
          <h2>{slug}</h2>
          <p class="muted">head scale {entry['scale']:.3f}; offset {entry['offset'][0]}, {entry['offset'][1]} px</p>
          <div class="grid">
            <figure><img src="../generated/{slug}.png"><figcaption>Qwen output</figcaption></figure>
            <figure><img src="../keyed/preview/{slug}.png"><figcaption>CorridorKey</figcaption></figure>
            <figure><img src="composites/{slug}-raw.png"><figcaption>raw layer over carrier</figcaption></figure>
            <figure><img src="composites/{slug}-aligned.png"><figcaption>registered layer over carrier</figcaption></figure>
            <figure><img src="layers/{slug}-raw.png"><figcaption>raw wardrobe alpha</figcaption></figure>
            <figure><img src="layers/{slug}-aligned.png"><figcaption>registered wardrobe alpha</figcaption></figure>
          </div>
        </article>
        """)
    source = """
<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Qwen neckline and registration test</title>
<style>
:root{color-scheme:dark;--bg:#111318;--panel:#1b1e25;--line:#343946;--muted:#9ca3b1}
*{box-sizing:border-box}body{margin:0;padding:28px;background:var(--bg);color:#f3f5f7;font:14px/1.45 system-ui,sans-serif}
main{max-width:1500px;margin:0 auto}h1{font-size:24px}h2{font-size:16px;margin:0 0 4px}.muted{color:var(--muted)}
.note{padding:12px 14px;border-left:3px solid #8dd6ff;background:#171a20;color:var(--muted)}
.card{margin-top:20px;padding:16px;border:1px solid var(--line);border-radius:12px;background:var(--panel)}
.grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin-top:12px}figure{margin:0}img{display:block;width:100%;aspect-ratio:2/3;object-fit:contain;background:#30343d;border-radius:6px}figcaption{padding-top:5px;color:var(--muted);font-size:12px}
@media(max-width:1100px){.grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:650px){body{padding:16px}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
<main><h1>Qwen neckline and registration test</h1>
<p class="muted">Two native Qwen Plus edits from one fixed green carrier. Bare head, no headgear, one fixed modest crew neckline.</p>
<p class="note">Raw keeps Qwen's placement. Registered fits the layer to the carrier's head anchor before compositing. This is a PoC review plate, not runtime code.</p>
""" + "\n".join(cards) + "</main>\n"
    (root / "review.html").write_text(source)


def self_test():
    carrier = Image.new("RGB", (100, 150), (0, 180, 0))
    ImageDraw.Draw(carrier).ellipse((42, 15, 57, 35), fill=(200, 120, 80))
    layer = Image.new("RGBA", carrier.size)
    ImageDraw.Draw(layer).rectangle((35, 40, 65, 130), fill=(180, 40, 40, 255))
    source = head_box(carrier)
    warped, scale, _ = warp(layer, (40, 10, 60, 35), source)
    assert scale == 0.8
    assert warped.getchannel("A").getbbox()
    print("Qwen neckline alignment self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--carrier", type=Path)
    parser.add_argument("--rgba-dir", type=Path)
    parser.add_argument("--keyed-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    for path in (args.carrier, args.rgba_dir, args.keyed_dir, args.output_dir):
        if path is None:
            parser.error("--carrier, --rgba-dir, --keyed-dir, and --output-dir are required")
    root = args.output_dir
    root.mkdir(parents=True, exist_ok=True)
    carrier_path = root / "source" / "carrier.png"
    carrier_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.carrier, carrier_path)
    with Image.open(args.carrier) as opened:
        carrier = opened.convert("RGB")
    target_head = head_box(carrier)
    target_head_mask = head_component(carrier).filter(ImageFilter.MaxFilter(15))
    entries = []
    for rgba_path in sorted(args.rgba_dir.glob("*.png")):
        slug = rgba_path.stem
        keyed_preview = args.keyed_dir / "preview" / rgba_path.name
        with Image.open(rgba_path) as opened:
            layer = opened.convert("RGBA")
        with Image.open(args.rgba_dir.parent.parent / "generated" / rgba_path.name) as opened:
            generated = opened.convert("RGB")
        source_head = head_box(generated)
        raw_layer = layer
        aligned, scale, offset = warp(layer, source_head, target_head)
        aligned = remove_head(aligned, target_head_mask)
        raw_path = root / "layers" / f"{slug}-raw.png"
        aligned_path = root / "layers" / f"{slug}-aligned.png"
        save(raw_layer, raw_path)
        save(aligned, aligned_path)
        save(composite(carrier, raw_layer), root / "composites" / f"{slug}-raw.png")
        save(composite(carrier, aligned), root / "composites" / f"{slug}-aligned.png")
        save(Image.alpha_composite(checkerboard(layer.size), raw_layer).convert("RGB"), root / "layers" / f"{slug}-raw-preview.png")
        save(Image.alpha_composite(checkerboard(layer.size), aligned).convert("RGB"), root / "layers" / f"{slug}-aligned-preview.png")
        entries.append({
            "slug": slug, "source": str(rgba_path), "source_sha256": sha256(rgba_path),
            "matte": str(args.keyed_dir / "matte" / rgba_path.name),
            "keyed_preview": str(keyed_preview), "dimensions": list(layer.size),
            "source_head_box": list(source_head), "target_head_box": list(target_head),
            "scale": scale, "offset": list(offset),
            "raw_layer": str(raw_path.relative_to(root)),
            "aligned_layer": str(aligned_path.relative_to(root)),
        })
    manifest = {"version": 1, "carrier": str(carrier_path.relative_to(root)), "head_band": HEAD_BAND, "entries": entries}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    write_review(root, entries)


if __name__ == "__main__":
    main()
