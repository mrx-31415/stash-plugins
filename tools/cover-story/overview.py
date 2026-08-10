#!/usr/bin/env python3
"""One page for the whole investigation, grouped by question rather than by run.

    python3 overview.py --out /mnt/Misc/sd/cover-story/overview

variant_sheet.py answers "how do these twenty runs compare"; this answers "what did we learn and
what should we adopt". The grouping and the verdicts are editorial -- they are a reading of the
runs, not derivable from them -- so they live here as data rather than being inferred, and each one
names the number or picture it rests on.

Image-first on purpose. Every wrong call in this investigation came from reading a metric without
the picture beside it: a skin gate whose floor sat below its own baseline, a coverage gate that
passed visible gaps, a "66% improvement" that was invisible, and a variant that scored best while
showing a green halo.
"""
import argparse
import html
import json
from pathlib import Path

from PIL import Image

S = Path("/mnt/Misc/sd/cover-story")

# (title, verdict class, verdict text, [(label, run dir, file, caption)])
SECTIONS = [
    ("Garment — solved", "good",
     "Run E has the first catalog-faithful garment: stand collar, gloves, shoes, corset seams, "
     "0.32% paint remnant, zero fringe on all three layers. The collar took a one-word mask fix "
     "after weeks of prompt work could not produce it.",
     [("08-07 run", "chroma-garmentref-poc-20260807", "composite.png",
       "no collar, no gloves, no shoes — the prompt had dropped the description"),
      ("run B", "pod-20260808-B-garmentref", "composite.png",
       "description restored: gloves and shoes return, collar still impossible"),
      ("run E", "pod-20260808b-E-masksplit", "composite.png",
       "neck made paintable: collar appears. All five gates green")]),

    ("Prompt ladder — our accumulated prompt does less than we thought", "warn",
     "Qwen gets no better as the prompt grows: bare 0.0002, described 0.0000, locked 0.0027, "
     "full 0.0032. FLUX.2 needs the description (bare is 10x worse) then plateaus. But the metric "
     "and the eye disagree — 'described' scores best and looks softer than 'bare'. Which rung is "
     "best is a judgement on the pictures.",
     [("qwen bare", "ladder-20260810-qwen-bare", "clothes.png", "remnant 0.0002"),
      ("qwen described", "ladder-20260810-qwen-described", "clothes.png", "remnant 0.0000"),
      ("qwen full", "ladder-20260810-qwen-full", "clothes.png", "remnant 0.0032 — the tuned prompt"),
      ("flux2 full", "ladder-20260810-flux2-full", "clothes.png",
       "flatter at every rung; mask-margin halo visible")]),

    ("Skin — flatness fixable, hue mismatch open", "warn",
     "The recolor is a one-dimensional colour model: saturation is constant by construction (4.37 "
     "against real skin's 10.93). Asking for variation instead of 'blend and even out' fixes that "
     "(10.86). But the body then sits +44 red against the face where real skin sits (18,18,19) — "
     "right brightness, wrong colour. That is the open problem.",
     [("baseline", "pod-20260808b-E-masksplit", "identity.png", "spread 4.37 — flat, waxy"),
      ("skin-variation", "sweep-20260810-skin-variation", "identity-harmonized.png",
       "spread 10.86, but +44 red vs the face"),
      ("skin-carrier", "sweep-20260810-skin-carrier", "identity-harmonized.png",
       "scored highest (16.57) and is broken — green halo inflated the metric"),
      ("performer (real)", "pod-20260808b-E-masksplit", "preprocessed.png",
       "spread 10.93, delta (18,18,19) — the target")]),

    ("Coverage — no prompt fixes the underarm gap", "bad",
     "Every coverage variant made it worse: baseline 964 px, bulk 1071, sides 1091, canny 1177. "
     "The canny null is trustworthy because the distorted control produced 40x the gap — the "
     "ControlNet was demonstrably live, so 'it did not help' is an answer rather than a dead "
     "mechanism. The garment being narrower than the torso is a generation problem.",
     [("run E", "pod-20260808b-E-masksplit", "composite.png", "964 px uncovered"),
      ("cov-sides", "sweep-20260810-cov-sides", "composite.png", "1091 px — worse"),
      ("cov-canny", "sweep-20260810-cov-canny", "composite.png", "1177 px — worse"),
      ("cov-canny-distort", "sweep-20260810-cov-canny-distort", "composite.png",
       "40x gap: proof the control was live")]),
]

DECISIONS = [
    ("good", "Adopt", "Mask fix making the neck paintable — the collar. One word in "
     "CLOTHES_STOP_SAM_PROMPT."),
    ("good", "Adopt", "GPU matting over SSH. 2m39s per full run against 25 min per plate locally, "
     "and it ended three OOM kills of the dev box."),
    ("good", "Adopt", "Chroma gate + edge despill. Fringe 0.000 on all three layers of a fresh run "
     "they were not tuned against."),
    ("warn", "Open", "Skin hue: variation prompt fixes flatness but leaves the body warmer than the "
     "face. Next arm is --skin-blend-prompt tone, which names a target instead of pointing at her "
     "face."),
    ("warn", "Open", "Underarm gap: not a prompt problem, not a control problem. Likely needs the "
     "garment mask to stop including background, or a different clothes construction."),
    ("bad", "Reject", "skin-carrier — breaks outside_mask_unchanged; the edge guard leaves "
     "unrepainted green."),
    ("bad", "Reject", "Lower clothes denoise as a halo fix — does not reduce the halo (-15 to -18 "
     "throughout) and destroys the garment (54% paint left at 0.6). Qwen shows the same -13 halo, "
     "so it was never a FLUX.2 defect."),
    ("bad", "Reject", "Dedicated BiRefNet matting checkpoints, full-plate upscale, Lab chroma "
     "transfer, grey carrier — each measured and each a dead end."),
]


def crop_for(path, out, name, box=(180, 40, 650, 1240), width=210):
    if not path.is_file():
        return None
    with Image.open(path) as opened:
        image = opened.convert("RGB")
        box = (box[0], box[1], min(box[2], image.width), min(box[3], image.height))
        crop = image.crop(box)
        crop.thumbnail((width, 900))
        crop.save(out / name)
    return name


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    blocks = []
    for title, cls, verdict, items in SECTIONS:
        cards = []
        for label, run, filename, caption in items:
            name = crop_for(S / run / filename, out,
                            f"{run}-{filename.replace('.png','')}.png")
            if not name:
                continue
            cards.append(
                f'<figure><img src="{html.escape(name)}" alt="{html.escape(label)}">'
                f'<figcaption><strong>{html.escape(label)}</strong><br>'
                f'{html.escape(caption)}</figcaption></figure>')
        blocks.append(f'<section><h2>{html.escape(title)}</h2>'
                      f'<p class="verdict {cls}">{html.escape(verdict)}</p>'
                      f'<div class="strip">{"".join(cards)}</div></section>')

    rows = "".join(f'<tr><td class="tag {c}">{html.escape(k)}</td><td>{html.escape(t)}</td></tr>'
                   for c, k, t in DECISIONS)

    (out / "index.html").write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cover Story — where things stand</title>
<style>
 :root {{ color-scheme: dark; }}
 body {{ margin:0; padding:2rem 1.5rem 4rem; background:#15171d; color:#e6e7ea;
        font:15px/1.6 -apple-system,"Segoe UI",Roboto,sans-serif; }}
 .wrap {{ max-width:1500px; margin:0 auto; }}
 h1 {{ font-size:1.6rem; margin:0 0 .2rem; }}
 .sub {{ color:#9aa0ab; margin:0 0 2rem; }}
 h2 {{ font-size:1.15rem; margin:0 0 .4rem; }}
 section {{ border-top:1px solid #2a2e37; padding-top:1.4rem; margin-bottom:2.4rem; }}
 .verdict {{ max-width:95ch; margin:0 0 1rem; padding-left:.7rem; border-left:3px solid; }}
 .verdict.good {{ border-color:#6ee79f; }} .verdict.warn {{ border-color:#f0d264; }}
 .verdict.bad {{ border-color:#ff9b95; }}
 .strip {{ display:flex; gap:1rem; flex-wrap:wrap; align-items:flex-start; }}
 figure {{ margin:0; width:210px; }}
 img {{ display:block; width:100%; border:1px solid #2a2e37; border-radius:4px; background:#0e1015; }}
 figcaption {{ color:#9aa0ab; font-size:.8rem; margin-top:.35rem; }}
 figcaption strong {{ color:#e6e7ea; }}
 table {{ border-collapse:collapse; margin:0 0 2.5rem; }}
 td {{ border-bottom:1px solid #2a2e37; padding:.45rem .8rem; vertical-align:top; }}
 td:last-child {{ max-width:90ch; }}
 .tag {{ font-weight:600; white-space:nowrap; }}
 .tag.good {{ color:#6ee79f; }} .tag.warn {{ color:#f0d264; }} .tag.bad {{ color:#ff9b95; }}
 a {{ color:#7fb2ff; }}
</style></head><body><div class="wrap">
<h1>Cover Story — where things stand</h1>
<p class="sub">Grouped by question, not by run. Verdicts are a reading of the evidence and each one
names the number or picture it rests on.</p>
<table>{rows}</table>
{"".join(blocks)}
<section><h2>Detail</h2><p class="verdict good">
<a href="../ladder-sheet-20260810/index.html">ladder sheet</a> — the 11 prompt/denoise arms.
<a href="../variant-sheet-20260810/index.html">variant sheet</a> — the skin and coverage sweep.
<a href="../review-20260808/index.html">08-08 review</a> — how the extraction defects were found.
</p></section>
</div></body></html>
""", encoding="utf-8")
    print(f"wrote {out / 'index.html'}")


if __name__ == "__main__":
    main()
