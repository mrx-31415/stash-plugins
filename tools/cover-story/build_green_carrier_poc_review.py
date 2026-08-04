#!/usr/bin/env python3
"""Build transparent and green-carrier composites for the shared-carrier PoC."""

import argparse
import hashlib
import html
import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw


ACTORS = ("430", "266")
OUTFITS = ("viking-rust", "viking-dark-brown")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checkerboard(size, cell=32):
    image = Image.new("RGB", size, (42, 42, 42))
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(86, 86, 86))
    return image.convert("RGBA")


def save(image, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", compress_level=1)


def composite(background, *layers):
    result = background.convert("RGBA")
    for layer in layers:
        result.alpha_composite(layer)
    return result.convert("RGB")


def write_review(root):
    cards = []
    for actor in ACTORS:
        for outfit in OUTFITS:
            slug = f"performer-{actor}-{outfit}"
            cards.append(f"""
            <article class="card">
              <h2>Performer {actor} · {outfit}</h2>
              <div class="pair">
                <figure><img src="composites/{slug}.png"><figcaption>green-background stack</figcaption></figure>
                <figure><img src="transparent/{slug}.png"><figcaption>transparent stack</figcaption></figure>
              </div>
            </article>
            """)
    branches = []
    for slug, label in (
        ("base-carrier", "body carrier"),
        ("head-carrier", "green head carrier"),
        ("head-430", "head lift 430"),
        ("head-266", "head lift 266"),
        ("viking-rust", "rust wardrobe"),
        ("viking-dark-brown", "dark-brown wardrobe"),
        ("sam3-head-430", "SAM3 head hint 430"),
        ("sam3-head-266", "SAM3 head hint 266"),
    ):
        branches.append(f'<figure><img src="generated/{slug}.png"><figcaption>{label}</figcaption></figure>')
    source = """
<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Green-carrier wardrobe PoC</title>
<style>
:root{color-scheme:dark;--bg:#111318;--panel:#1b1e25;--line:#343946;--muted:#9ca3b1}
*{box-sizing:border-box}body{margin:0;padding:28px;background:var(--bg);color:#f3f5f7;font:14px/1.45 system-ui,sans-serif}
main{max-width:1500px;margin:0 auto}h1{font-size:24px}h2{font-size:16px;margin:0 0 5px}.muted{color:var(--muted)}
.note{padding:12px 14px;border-left:3px solid #8dd6ff;background:#171a20;color:var(--muted)}
.branches{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:16px}.card{margin-top:20px;padding:16px;border:1px solid var(--line);border-radius:12px;background:var(--panel)}
.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.pair img,.branches img{display:block;width:100%;aspect-ratio:2/3;object-fit:contain;background:#30343d;border-radius:6px}figure{margin:0}figcaption{padding-top:5px;color:var(--muted);font-size:12px}
@media(max-width:1000px){.branches{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:650px){body{padding:16px}.branches{grid-template-columns:repeat(2,minmax(0,1fr))}.pair{grid-template-columns:1fr}}
</style>
<main><h1>Green-carrier wardrobe PoC</h1>
<p class="muted">Geometry-matched carriers: matte human green head for identity extraction, natural extremities for body/wardrobe; no registration or scaling.</p>
<p class="note">Raw Qwen outputs. SAM3 head/hair/neck masks are supplied directly to CorridorKey as foreground hints; no fixed crop is applied.</p>
<section class="branches">""" + "\n".join(branches) + """</section>
<h2 style="margin-top:28px">Four composites</h2>
""" + "\n".join(cards) + "</main>\n"
    (root / "review.html").write_text(source)


def self_test():
    background = Image.new("RGB", (10, 10), (0, 180, 0))
    wardrobe = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    head = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    ImageDraw.Draw(wardrobe).rectangle((2, 2, 7, 7), fill=(180, 40, 40, 255))
    ImageDraw.Draw(head).rectangle((4, 1, 5, 3), fill=(220, 160, 120, 255))
    result = composite(background, wardrobe, head)
    assert result.getpixel((4, 1))[:2] == (220, 160)
    print("green-carrier composite self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--portrait-430", type=Path)
    parser.add_argument("--portrait-266", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not all((args.input_dir, args.portrait_430, args.portrait_266, args.output_dir)):
        parser.error("--input-dir, --portrait-430, --portrait-266, and --output-dir are required")
    root = args.output_dir
    generated = args.input_dir / "generated"
    keyed = args.input_dir / "keyed"
    root.mkdir(parents=True, exist_ok=True)
    for folder in ("generated", "keyed", "references", "composites", "transparent"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    for kind in ("preview", "rgba", "matte", "qc", "hint"):
        (root / "keyed" / kind).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.portrait_430, root / "references" / "performer-430.png")
    shutil.copyfile(args.portrait_266, root / "references" / "performer-266.png")
    shutil.copyfile(args.input_dir / "generation-manifest.json", root / "generation-manifest.json")
    for name in ("head-generation-manifest.json", "sam3-manifest.json"):
        if (args.input_dir / name).is_file():
            shutil.copyfile(args.input_dir / name, root / name)
    for source in generated.glob("*.png"):
        shutil.copyfile(source, root / "generated" / source.name)
    for kind in ("preview", "rgba", "matte", "qc", "hint"):
        for source in (keyed / kind).glob("*.png"):
            shutil.copyfile(source, root / "keyed" / kind / source.name)
    if (keyed / "manifest.json").is_file():
        shutil.copyfile(keyed / "manifest.json", root / "keyed" / "manifest.json")
    with Image.open(generated / "base-carrier.png") as opened:
        carrier_size = opened.size
    with Image.open(keyed / "rgba" / "base-carrier.png") as opened:
        body = opened.convert("RGBA")
    green = Image.new("RGB", carrier_size, (0, 180, 0))
    entries = []
    for actor in ACTORS:
        with Image.open(keyed / "rgba" / f"head-{actor}.png") as opened:
            head = opened.convert("RGBA")
        for outfit in OUTFITS:
            with Image.open(keyed / "rgba" / f"{outfit}.png") as opened:
                wardrobe = opened.convert("RGBA")
            slug = f"performer-{actor}-{outfit}"
            save(composite(green, body, wardrobe, head), root / "composites" / f"{slug}.png")
            transparent = checkerboard(carrier_size)
            transparent.alpha_composite(body)
            transparent.alpha_composite(wardrobe)
            transparent.alpha_composite(head)
            save(transparent.convert("RGB"), root / "transparent" / f"{slug}.png")
            entries.append({
                "performer": actor, "outfit": outfit,
                "body_layer": "keyed/rgba/base-carrier.png",
                "head_layer": f"keyed/rgba/head-{actor}.png",
                "wardrobe_layer": f"keyed/rgba/{outfit}.png",
                "green_composite": f"composites/{slug}.png",
                "transparent_composite": f"transparent/{slug}.png",
            })
    generation = json.loads((args.input_dir / "generation-manifest.json").read_text())
    head_generation = json.loads((args.input_dir / "head-generation-manifest.json").read_text())
    sam3 = json.loads((args.input_dir / "sam3-manifest.json").read_text())
    (root / "manifest.json").write_text(json.dumps({
        "version": 1, "body_and_wardrobe_generation": generation,
        "head_generation": head_generation, "sam3": sam3, "composites": entries,
        "notes": ["No image registration was applied.",
                  "Head branches use the matte human-head carrier; body/wardrobe branches use the geometry-matched natural-extremity carrier.",
                  "SAM3 head/hair/neck masks are passed to CorridorKey as foreground hints; no fixed crop is applied."],
    }, indent=2) + "\n")
    write_review(root)


if __name__ == "__main__":
    main()
