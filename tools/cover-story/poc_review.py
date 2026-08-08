#!/usr/bin/env python3
"""Render a bulk review.html for a PoC run root on the shared NFS drive.

Usage:
  python3 poc_review.py --root /mnt/Misc/sd/cover-story/grey-garmentref-poc-v1 \
      [--v3-root /mnt/Misc/sd/cover-story/cover-story-qwen2512-skin-head-clothes-poc-v3] \
      [--out review.html]

Sections: recipe + per-stage check summary, a per-stage image gallery with the automatic
check results, the envelope auto-accept banner (bulk mode), alpha checkerboards, composite
closeups, an A/B against the v3 chroma run (composite / feet / neckline / clothes), and a
localStorage-backed accept/reject/notes panel for bulk review. The export button copies the
decisions as JSON for pasting back to the operator. Missing files degrade gracefully so a
partially-failed run still produces a useful page.
"""

import argparse
import html
import json
from pathlib import Path

from PIL import Image, ImageDraw

CLOSEUPS = {
    "face-hair": (220, 0, 612, 320),
    "neckline": (250, 180, 582, 420),
    "waist": (250, 430, 582, 660),
    "feet-shoes": (250, 1030, 582, 1248),
}
REVIEW_ITEMS = [
    ("envelope", "Envelope masks (auto-accepted in bulk)", "head/neck/chest aperture + clothes edit region on the carrier"),
    ("identity", "Identity plate", "likeness vs the shipped portrait; tone; seam at neck/shoulders"),
    ("garment", "Garment flat lay", "plum dress: colour, full length, clean background, fabric"),
    ("clothes", "Clothes plate", "garment fidelity, neckline, hands, feet/shoes, no grey remnants"),
    ("composite", "Composite + closeups", "fringe, waist, feet/shoes, hair, overall coherence"),
    ("vs-v3", "A/B vs v3 chroma run", "feet/shoes and neckline: is the matting recipe visibly cleaner?"),
    ("promote", "Promote decision", "grey carrier + matting + garment-ref recipe vs current chroma recipe"),
]


def checkerboard(size, cell=32):
    image = Image.new("RGBA", size, (42, 42, 42, 255))
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(86, 86, 86, 255))
    return image


def alpha_checker(source_path, alpha_path, out):
    source = Image.open(source_path).convert("RGBA")
    alpha = Image.open(alpha_path).convert("L")
    rgba = source.copy()
    rgba.putalpha(alpha)
    Image.alpha_composite(checkerboard(source.size), rgba).convert("RGB").save(out)


def img(src, maxw=560):
    return f'<img src="{html.escape(src)}" style="max-width:{maxw}px;border:1px solid #333;margin:2px">'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="PoC run output root")
    parser.add_argument("--v3-root", default="/mnt/Misc/sd/cover-story/cover-story-qwen2512-skin-head-clothes-poc-v3")
    parser.add_argument("--out", default="review.html")
    args = parser.parse_args()

    root = Path(args.root)
    v3 = Path(args.v3_root)
    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    checks = json.loads((root / "checks.json").read_text(encoding="utf-8")) if (root / "checks.json").is_file() else {}
    poc = json.loads((root / "poc.json").read_text(encoding="utf-8")) if (root / "poc.json").is_file() else {}

    parts = ["<!doctype html><html><head><meta charset='utf-8'><title>Cover Story PoC bulk review</title>",
             "<style>body{font-family:sans-serif;margin:2rem;background:#111;color:#ddd}",
             "h1,h2,h3{color:#fff}table{border-collapse:collapse;margin:1rem 0}",
             "td,th{border:1px solid #444;padding:3px 8px;font-size:12px}th{background:#222}",
             ".pass{color:#6ee7a0}.fail{color:#f8a3a3}.banner{background:#3a2f10;border:1px solid #a8842a;padding:8px 12px;margin:1rem 0}",
             "section{margin:2rem 0;border-top:1px solid #333;padding-top:1rem}",
             ".rev{border:1px solid #444;padding:8px;margin:6px 0;background:#171717}",
             "button{margin:2px;padding:2px 10px}button.ok{background:#1d4d2b}button.bad{background:#5a2020}button.mid{background:#4d421d}",
             ".notes{width:100%;background:#0e0e0e;color:#ddd;border:1px solid #444}</style></head><body>"]

    recipe = poc.get("recipe", {})
    parts.append(f"<h1>Cover Story PoC bulk review — {html.escape(root.name)}</h1>")
    parts.append(f"<p>recipe: {recipe.get('carrier','?')} carrier / clothes={recipe.get('clothes','?')} / "
                 f"extract={recipe.get('extract','?')} · generated {poc.get('version','?')}</p>")
    if not poc:
        parts.append("<p class='fail'>no poc.json yet — run is incomplete; page shows whatever exists.</p>")

    # Envelope banner
    envelope_status = {}
    if (root / "masks" / "envelope-status.json").is_file():
        envelope_status = json.loads((root / "masks" / "envelope-status.json").read_text(encoding="utf-8"))
    if envelope_status.get("auto_accepted"):
        parts.append("<div class='banner'><b>Envelope AUTO-ACCEPTED in bulk mode</b> — inspect the overlay below and "
                     "flag it in the review panel if the head aperture or clothes region is wrong.</div>")

    # Check summary
    parts.append("<section><h2>Automatic checks</h2><table><tr><th>stage</th><th>check</th><th>result</th><th>detail</th></tr>")
    for stage, stage_checks in checks.items():
        for c in stage_checks:
            cls = "pass" if c["passed"] else "fail"
            detail = html.escape(json.dumps(c["detail"], default=str))[:120] if c.get("detail") is not None else ""
            parts.append(f"<tr><td>{stage}</td><td>{html.escape(c['name'])}</td>"
                         f"<td class='{cls}'>{'PASS' if c['passed'] else 'FAIL'}</td><td>{detail}</td></tr>")
    parts.append("</table></section>")

    def gallery(title, images, blurb=""):
        parts.append(f"<section><h2>{title}</h2>{f'<p>{blurb}</p>' if blurb else ''}")
        for src in images:
            p = root / src
            if p.is_file():
                parts.append(img(src))
            else:
                parts.append(f"<span class='fail'>missing: {html.escape(src)}</span>")
        parts.append("</section>")

    gallery("1. Carrier (grey bodysuit on grey bg)", ["carrier.png"],
            "suit must be visibly lighter than the background; feet visible")
    gallery("2. Envelope (auto-accepted in bulk)", ["masks/envelope-review.png"],
            "head/neck/chest aperture tinted one colour, clothes region another — both must cover what the review gate expects")
    gallery("3. Preprocessed performer", ["preprocessed.png"], "aligned, no reference collapse")
    gallery("4. Identity plate", ["identity.png", "identity-toned-body.png"],
            "likeness, tone, neck/shoulder seam")
    gallery("5. Garment flat lay", ["garment.png"], "plum dress, full length, clean background")
    gallery("6. Clothes plate", ["clothes.png", "clothes-raw.png"], "garment fidelity, hands, feet/shoes")
    gallery("7. Alphas (checkerboard)", ["identity-skin-rgba.png", "identity-hair-rgba.png", "clothes-rgba.png"],
            "holes/fringe visible against the checkerboard")

    if (root / "composite.png").is_file():
        parts.append("<section><h2>8. Composite + closeups</h2>")
        parts.append(img("composite.png"))
        card = Image.open(root / "composite.png")
        if card.size != (600, 900):
            card = card.resize((600, 900), Image.Resampling.LANCZOS)
        card.save(root / "card.png")
        parts.append(img("card.png"))
        for name, box in CLOSEUPS.items():
            im = Image.open(root / "composite.png").crop(box)
            fname = f"closeup-{name}.png"
            im.save(root / fname)
            parts.append(img(fname))
        parts.append("</section>")

    # A/B vs v3
    parts.append("<section><h2>9. A/B vs v3 chroma run (same outfit: plum Victorian dress)</h2>")
    for name, box in [("composite", None), ("feet-shoes", CLOSEUPS["feet-shoes"]), ("neckline", CLOSEUPS["neckline"])]:
        new_src = f"ab-new-{name}.png"
        v3_src = f"ab-v3-{name}.png"
        if (root / "composite.png").is_file():
            if name == "composite":
                Image.open(root / "composite.png").convert("RGB").save(root / new_src)
            else:
                Image.open(root / "composite.png").convert("RGB").crop(box).save(root / new_src)
        if (v3 / "composite.png").is_file():
            if name == "composite":
                Image.open(v3 / "composite.png").convert("RGB").save(root / v3_src)
            else:
                Image.open(v3 / "composite.png").convert("RGB").crop(box).save(root / v3_src)
        parts.append(f"<h3>{name} — new (left) vs v3 (right)</h3>")
        if (root / new_src).is_file():
            parts.append(img(new_src))
        if (root / v3_src).is_file():
            parts.append(img(v3_src))
    parts.append("</section>")

    # Review panel
    items_json = json.dumps(REVIEW_ITEMS)
    parts.append("<section><h2>Review panel</h2><p>Selections persist in this browser (localStorage). "
                 "Click <b>export</b> when done and paste the JSON back to the operator.</p>"
                 f"<div id='panel' data-items='{html.escape(items_json)}'></div>"
                 "<button onclick='exportDecisions()'>export decisions</button> "
                 "<button onclick='localStorage.removeItem(\"poc-review\");location.reload()'>reset</button>"
                 "<pre id='out'></pre></section>")
    parts.append("""
<script>
const ITEMS = JSON.parse(document.getElementById('panel').dataset.items);
const KEY = 'poc-review';
let state = JSON.parse(localStorage.getItem(KEY) || '{}');
function render() {
  document.getElementById('panel').innerHTML = ITEMS.map(([id, title, blurb], i) => {
    const s = state[id] || {};
    return `<div class='rev'><b>${i+1}. ${title}</b><br><small>${blurb}</small><br>
      <button class='ok' onclick='set("${id}","accept")'>accept</button>
      <button class='mid' onclick='set("${id}","maybe")'>maybe</button>
      <button class='bad' onclick='set("${id}","reject")'>reject</button>
      <span> → ${s.decision || '<i>undecided</i>'}</span><br>
      <textarea class='notes' rows='2' placeholder='notes' onchange='note("${id}",this.value)'>${(s.notes||'').replace(/</g,'&lt;')}</textarea></div>`;
  }).join('');
}
function set(id, decision) { state[id] = state[id] || {}; state[id].decision = decision;
  localStorage.setItem(KEY, JSON.stringify(state)); render(); }
function note(id, value) { state[id] = state[id] || {}; state[id].notes = value;
  localStorage.setItem(KEY, JSON.stringify(state)); }
function exportDecisions() {
  document.getElementById('out').textContent =
    JSON.stringify({run: location.pathname.split('/').pop(), root: location.pathname, decisions: state}, null, 2);
}
render();
</script>""")
    parts.append("</body></html>")
    out.write_text("\n".join(parts))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
