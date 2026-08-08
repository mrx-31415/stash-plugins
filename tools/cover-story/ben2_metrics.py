#!/usr/bin/env python3
"""Consolidated metrics + review.html: CorridorKey vs BiRefNet vs BEN2, plus the
grey-background invariance numbers. Reads saved alphas only (no inference)."""
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from birefnet_ab import REGIONS, region_metrics  # noqa: E402

OUT = "/mnt/Misc/sd/cover-story/birefnet-ab-v1"
KEYERS = ["ck", "bn", "ben2"]
FILES = {
    "identity": "poc:qwen2512-identity-alpha.png",
    "clothes": "poc:qwen2512-clothes-alpha.png",
}
ALPHA = {
    "identity": {"ck": FILES["identity"], "bn": "identity-bn-alpha.png", "ben2": "identity-ben2-alpha.png"},
    "clothes": {"ck": FILES["clothes"], "bn": "clothes-bn-alpha.png", "ben2": "clothes-ben2-alpha.png"},
}
GREY = {"identity": "identity-bn-grey-alpha.png", "clothes": "clothes-bn-grey-alpha.png"}

ROWS = []
report = {}
for plate in ("identity", "clothes"):
    alphas = {}
    for k in KEYERS:
        if k == "ck":
            p = os.path.join("/mnt/Misc/sd/cover-story/cover-story-qwen2512-skin-head-clothes-poc-v3", ALPHA[plate][k])
        else:
            p = os.path.join(OUT, ALPHA[plate][k])
        alphas[k] = np.asarray(Image.open(p).convert("L"), dtype=np.uint8)
    if os.path.exists(os.path.join(OUT, GREY[plate])):
        grey = np.asarray(Image.open(os.path.join(OUT, GREY[plate])).convert("L"), dtype=np.uint8)
    else:
        grey = None
    entry = {"regions": {}, "grey_invariance": None}
    for rname, region in REGIONS.items():
        entry["regions"][rname] = {k: region_metrics(alphas[k], region) for k in KEYERS}
        for k in KEYERS:
            ROWS.append((plate, rname, k, entry["regions"][rname][k]))
    if grey is not None:
        d = np.abs(alphas["bn"].astype(int) - grey.astype(int))
        entry["grey_invariance"] = {"mean": round(float(d.mean()), 2), "pct_gt32": round(100 * float((d > 32).mean()), 1)}
        print(f"{plate}: grey invariance mean|diff| {d.mean():.2f}/255  >32: {100*(d>32).mean():.1f}%")
    report[plate] = entry

with open(os.path.join(OUT, "all-metrics.json"), "w") as fh:
    json.dump(report, fh, indent=2)

# --- review.html ---
html = [
    "<!doctype html><html><head><meta charset='utf-8'><title>Keyer A/B: CorridorKey vs BiRefNet vs BEN2</title>",
    "<style>body{font-family:sans-serif;margin:2rem;background:#111;color:#ddd}h1,h2,h3{color:#fff}",
    "table{border-collapse:collapse;margin:1rem 0}td,th{border:1px solid #444;padding:3px 8px;font-size:12px}",
    "th{background:#222}img{max-width:48%;border:1px solid #333;margin:2px}.good{color:#6ee7a0}.bad{color:#f8a3a3}</style></head><body>",
    "<h1>Keyer A/B on qwen2512-skin-head-clothes-poc-v3 plates</h1>",
    "<p>Lower is better for mid_px/specks/holes/hole_px. Grey invariance = BiRefNet alpha on a grey-filled background vs the chroma plate (lower = more background-color-invariant).</p>",
    "<table><tr><th>plate</th><th>region</th><th>metric</th>",
    *[f"<th>{k}</th>" for k in KEYERS],
    "<th>best</th></tr>",
]
for plate, rname, k, m in ROWS:
    pass
# build per (plate, region, metric) rows
order = ["fg_px", "mid_px", "specks", "holes", "hole_px"]
for plate in ("identity", "clothes"):
    for rname, region in REGIONS.items():
        for metric in order:
            vals = {k: report[plate]["regions"][rname][k][metric] for k in KEYERS}
            best = min(vals, key=vals.get)
            bestv = vals[best]
            row = [f"<td>{plate}</td><td>{rname}</td><td>{metric}</td>"]
            for k in KEYERS:
                cls = "good" if vals[k] == bestv and metric in ("mid_px", "specks", "holes", "hole_px") else ""
                row.append(f"<td class='{cls}'>{vals[k]}</td>")
            row.append(f"<td>{best}</td>")
            html.append("<tr>" + "".join(row) + "</tr>")
html.append("</table>")
for plate in ("identity", "clothes"):
    g = report[plate].get("grey_invariance")
    gtxt = f"grey invariance: mean|diff| {g['mean']}/255, &gt;32: {g['pct_gt32']}%" if g else "grey alphas missing"
    html.append(f"<h2>{plate} plate — {gtxt}</h2>")
    for rname in ("hair", "neckline", "waist", "feet"):
        if rname == "full":
            continue
        html.append(f"<h3>{rname} — CK | BiRefNet | BEN2 | BiRefNet(grey-bg)</h3>")
        imgs = [f"{plate}-ck-checker.png", f"{plate}-bn-checker.png", f"{plate}-ben2-checker.png"]
        if g:
            imgs.append(f"{plate}-bn-grey-checker.png")
        for src in imgs:
            html.append(f'<img src="{src}" style="max-width:32%">')
html.append("</body></html>")
open(os.path.join(OUT, "review.html"), "w").write("\n".join(html))
print("review.html written")
