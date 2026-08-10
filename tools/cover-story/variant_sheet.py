#!/usr/bin/env python3
"""Compare many PoC variant runs on one page.

    python3 variant_sheet.py --roots '/mnt/Misc/sd/cover-story/pod-*' \
        --baseline /mnt/Misc/sd/cover-story/pod-20260808b-E-masksplit \
        --out /mnt/Misc/sd/cover-story/variant-sheet

poc_review.py renders one run in depth; this renders twenty shallowly, which is the harder problem
once a sweep starts. Two failures on 2026-08-08/09 came from evaluating runs one file at a time: a
skin gate whose floor sat below its own baseline passed three variants that had done nothing, and a
layer change reported as a 66% improvement turned out to be invisible. Both were obvious the moment
the numbers sat next to the picture.

So the page leads with a table, every metric is oriented the same way (green good, red bad), and the
dials that actually differ between runs are highlighted -- a sweep where twelve columns are identical
and one differs should say so, not make the reader diff two JSON files.

Degrades gracefully: a run stopped after [identity] has no composite, and says so rather than
vanishing. Reads each run's own files, so a run predating a metric still contributes what it has.
"""
import argparse
import html
import json
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layered_costume_production as production  # noqa: E402

# Same regions poc_review.py uses, so a closeup means the same thing on both pages.
CLOSEUPS = {
    "face / chest": (220, 60, 612, 480),
    "neckline": (250, 180, 582, 420),
    "waist": (250, 430, 582, 660),
    "feet": (250, 1030, 582, 1248),
}
# name -> (better direction, limit, format). "limit" is the gate where one exists.
METRICS = {
    "skin spread": ("high", 7.0, "{:.2f}"),
    "drift px": ("low", 2, "{}"),
    "paint remnant": ("low", 0.05, "{:.4f}"),
    "skin in garment": ("low", 0.05, "{:.4f}"),
    "uncovered figure": ("low", 0.02, "{:.4f}"),
    "fringe skin": ("low", 0.15, "{:.3f}"),
    "fringe hair": ("low", 0.15, "{:.3f}"),
    "fringe clothes": ("low", 0.15, "{:.3f}"),
}


def checks_by_name(root):
    path = root / "checks.json"
    if not path.is_file():
        return {}
    found = {}
    for stage, entries in json.loads(path.read_text(encoding="utf-8")).items():
        for entry in entries if isinstance(entries, list) else []:
            found[entry["name"]] = entry
    return found


def skin_spread(root):
    """Saturation spread over the body, the quantity that separates a one-dimensional recolor from
    real skin. Recomputed here rather than read from checks.json so runs that predate the metric --
    or stopped before the stage that records it -- still contribute a number."""
    identity = root / "identity-harmonized.png"
    if not identity.is_file():
        identity = root / "identity.png"
    head = root / "masks" / "identity-preserved-head.png"
    if not (identity.is_file() and head.is_file()):
        return None
    try:
        image = Image.open(identity).convert("RGB")
        mask = ImageChops.subtract(
            production.screen_foreground(image, "blue"),
            Image.open(head).convert("L").filter(ImageFilter.GaussianBlur(25))
            .point(lambda v: 255 if v > 127 else 0))
        return production.chroma_spread(image, mask)["saturation_spread"]
    except Exception:                                        # noqa: BLE001 - a sheet must not crash
        return None


def collect(root):
    checks = checks_by_name(root)
    poc = {}
    if (root / "poc.json").is_file():
        poc = json.loads((root / "poc.json").read_text(encoding="utf-8"))

    def detail(name, key=None, default=None):
        entry = checks.get(name)
        if not entry:
            return default
        value = entry["detail"]
        return value.get(key, default) if key and isinstance(value, dict) else value

    drift = detail("body_matches_carrier", "body_drift_px")
    fringe = {layer: detail(f"no_screen_fringe_{layer}", "fraction")
              for layer in ("identity-skin", "identity-hair", "clothes")}
    return {
        "name": root.name,
        "root": root,
        "variants": poc.get("variants", {}),
        "recipe": poc.get("recipe", {}),
        "stages": sorted({s for s in json.loads((root / "checks.json").read_text(encoding="utf-8"))}
                         ) if (root / "checks.json").is_file() else [],
        "failed": sorted(name for name, e in checks.items() if not e.get("passed", True)),
        "metrics": {
            "skin spread": skin_spread(root),
            "drift px": max(drift.values()) if isinstance(drift, dict) else None,
            "paint remnant": detail("no_paint_remnant_in_edit", "fraction"),
            "skin in garment": detail("no_skin_in_garment_region"),
            "uncovered figure": detail("figure_fully_covered_by_layers", "fraction"),
            "fringe skin": fringe["identity-skin"],
            "fringe hair": fringe["identity-hair"],
            "fringe clothes": fringe["clothes"],
        },
    }


def crops(run, out):
    """Write the closeups for one run and return their relative paths."""
    composite = run["root"] / "composite.png"
    source = composite if composite.is_file() else run["root"] / "identity.png"
    if not source.is_file():
        return {}
    made = {}
    with Image.open(source) as opened:
        image = opened.convert("RGB")
        thumb = image.copy()
        thumb.thumbnail((190, 320))
        name = f"{run['name']}-thumb.png"
        thumb.save(out / name)
        made["full"] = name
        for label, box in CLOSEUPS.items():
            if box[2] > image.width or box[3] > image.height:
                continue
            crop = image.crop(box)
            crop.thumbnail((240, 300))
            name = f"{run['name']}-{label.replace(' / ', '-').replace(' ', '')}.png"
            crop.save(out / name)
            made[label] = name
    return made


def cell(metric, value):
    if value is None:
        return '<td class="na">—</td>'
    better, limit, fmt = METRICS[metric]
    good = value >= limit if better == "high" else value <= limit
    return f'<td class="{"good" if good else "bad"}">{fmt.format(value)}</td>'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--roots", nargs="+", required=True, help="run directories or globs")
    parser.add_argument("--baseline", help="run to diff dials against")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    paths = []
    for pattern in args.roots:
        path = Path(pattern)
        paths.extend(sorted(path.parent.glob(path.name)) if "*" in pattern else [path])
    runs = [collect(p) for p in paths if p.is_dir()]
    if not runs:
        sys.exit("no run directories matched")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for run in runs:
        run["crops"] = crops(run, out)

    # Only the dials that actually differ across the set are worth columns.
    baseline = next((r for r in runs if args.baseline and r["root"] == Path(args.baseline)), runs[0])
    keys = sorted({k for r in runs for k in r["variants"]})
    varying = [k for k in keys if len({json.dumps(r["variants"].get(k)) for r in runs}) > 1]

    rows = []
    for run in runs:
        dials = "".join(
            f'<td class="{"diff" if run["variants"].get(k) != baseline["variants"].get(k) else ""}">'
            f'{html.escape(str(run["variants"].get(k, "—")))}</td>' for k in varying)
        metrics = "".join(cell(m, run["metrics"][m]) for m in METRICS)
        failed = (f'<span class="fail">{len(run["failed"])} failed</span>'
                  if run["failed"] else '<span class="ok">all passed</span>')
        rows.append(f'<tr><th><a href="#{html.escape(run["name"])}">{html.escape(run["name"])}</a>'
                    f'<br><small>{failed}</small></th>{dials}{metrics}</tr>')

    galleries = []
    for run in runs:
        images = "".join(
            f'<figure><img src="{html.escape(name)}" alt="{html.escape(label)}">'
            f'<figcaption>{html.escape(label)}</figcaption></figure>'
            for label, name in run["crops"].items())
        notes = ""
        if run["failed"]:
            notes = ('<p class="fail">failed: '
                     + html.escape(", ".join(run["failed"])) + "</p>")
        if not (run["root"] / "composite.png").is_file():
            notes += ('<p class="na">no composite — run stopped early; crops are from '
                      "identity.png</p>")
        galleries.append(
            f'<section id="{html.escape(run["name"])}"><h2>{html.escape(run["name"])}</h2>'
            f'{notes}<div class="strip">{images}</div></section>')

    head = "".join(f"<th>{html.escape(k.replace('_', ' '))}</th>" for k in varying)
    head += "".join(f"<th>{html.escape(m)}</th>" for m in METRICS)
    limits = "".join("<td></td>" for _ in varying) + "".join(
        f'<td><small>{"≥" if d == "high" else "≤"}{l}</small></td>' for d, l, _ in METRICS.values())

    (out / "index.html").write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cover Story — variant sheet</title>
<style>
 :root {{ color-scheme: dark; }}
 body {{ margin:0; padding:1.5rem; background:#15171d; color:#e6e7ea;
        font:14px/1.55 -apple-system,"Segoe UI",Roboto,sans-serif; }}
 h1 {{ font-size:1.4rem; margin:0 0 .2rem; }} .sub {{ color:#9aa0ab; margin:0 0 1.5rem; }}
 .scroll {{ overflow-x:auto; }}
 table {{ border-collapse:collapse; font-size:.85rem; margin-bottom:2rem; }}
 th,td {{ border:1px solid #2a2e37; padding:.3rem .55rem; text-align:right; white-space:nowrap; }}
 th {{ background:#1d212a; text-align:left; font-weight:600; }}
 td.good {{ color:#6ee79f; }} td.bad {{ color:#ff9b95; }} td.na {{ color:#5c626d; }}
 td.diff {{ background:#2a2410; color:#f0d264; }}
 .fail {{ color:#ff9b95; }} .ok {{ color:#6ee79f; }}
 a {{ color:#7fb2ff; }}
 section {{ border-top:1px solid #2a2e37; padding-top:1rem; margin-bottom:1.5rem; }}
 h2 {{ font-size:1rem; margin:0 0 .4rem; }}
 .strip {{ display:flex; gap:.6rem; flex-wrap:wrap; }}
 figure {{ margin:0; }} figcaption {{ color:#8f96a2; font-size:.75rem; text-align:center; }}
 img {{ display:block; border:1px solid #2a2e37; border-radius:3px; background:#0e1015; }}
</style></head><body>
<h1>Variant sheet</h1>
<p class="sub">{len(runs)} runs. Highlighted cells differ from
<strong>{html.escape(baseline["name"])}</strong>. Green passes the gate, red does not, — means the
run never produced it.</p>
<div class="scroll"><table>
<tr><th>run</th>{head}</tr>
<tr><th><small>gate</small></th>{limits}</tr>
{"".join(rows)}
</table></div>
{"".join(galleries)}
</body></html>
""", encoding="utf-8")
    print(f"wrote {out / 'index.html'} ({len(runs)} runs, {len(varying)} dials differ)")


if __name__ == "__main__":
    main()
