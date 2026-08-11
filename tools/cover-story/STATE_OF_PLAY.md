# Cover Story layered costume — state of play

Written 2026-08-11. One document: what works, what was tried and killed, what is still open.

The older documents are layered and partly superseded — `CLAUDE_PICKUP_HANDOVER.md` carries a
correction banner, `LAYERED_COSTUME_PRODUCTION_STATUS.md` is a 1,500-line chronological log. Read
this first; use those for depth on a specific finding.

Browsable evidence: `/mnt/Misc/sd/cover-story/overview/index.html` (grouped by question, image-first).

---

## 1. What works

| area | state | evidence |
|---|---|---|
| **Identity (head)** | solved | deterministic transplant; `body_matches_carrier` 0/0/0 px, `head_region_bit_exact` 0 |
| **Garment** | solved | run E: stand collar, gloves, shoes, corset seams; paint remnant **0.32%** |
| **Extraction** | solved | fringe **0.000** on all three layers of a fresh run the gates were not tuned against |
| **Matting speed** | solved | GPU over SSH: **2m39s** per full run against ~25 min *per plate* locally |
| **Asset hygiene** | solved | skin plate bounded to the aperture; no nude torso ships |

**The collar was one word.** `CLOTHES_STOP_SAM_PROMPT` read `"head, face, ears and neck"`, and
`head_stop_region()` cuts the forbidden zone at the bottom of *that* region's bbox — so the neck was
unpaintable and no prompt or garment reference could ever produce a collar. The function's own
docstring already said it "must stop at the neck base so the garment keeps its shoulders and
collar"; the prompt contradicted the intent. Dropping `"and neck"` moved the cutoff to the chin.

**Extraction was a mechanism, not a tuning problem.** Both CorridorKey and BiRefNet preserve source
RGB, so the difference is *alpha tightness*: a chroma keyer makes screen colour transparent by
definition, a shape segmenter has no colour semantics. BiRefNet's matte is a strict superset of
CorridorKey's (1 px goes the other way) and the 6,632 px it adds include 2,663 screen-dominant ones.
`chroma_gate()` intersects the two; `edge_despill()` corrects the blend band.

---

## 2. Tried and rejected — with the measurement that killed each

Do not re-attempt these without new information. Each cost real time.

| approach | why it died |
|---|---|
| Dedicated BiRefNet **matting** checkpoints | holes and spill unchanged, soft-edge band +20%, **6.5× slower**. The limit is the source plate: the hair edge has **19.6% intermediate pixels** — a mushy gradient, not strands. No matte recovers detail that was never rendered. |
| **Full-plate upscale** before matting | identity 0.485 → **0.487** (worse), for 25–32 min/plate. BiRefNet letterboxes to 1024² regardless, so upscaling a whole plate leaves effective matte resolution unchanged. Helped only on a *head crop*, where the crop fills the box. |
| **Lab chroma transfer** for skin | fixes the statistics (spread 6.6 → 13.6, value 85.7 → 63.8) and still looks wrong. The carrier's luminance *is* body paint — harsh contouring, odd knee creases — so fixing only chroma preserves what looks fake. |
| **Per-row body warp** (performer's real skin) | statistically indistinguishable from real skin (sat 37.6 vs 36.8) but tears badly: 236 of 707 rows fall back where arms merge into the torso. Would need pose-keypoint piecewise-affine. Not worth it for **0.62%** of the frame. |
| **Grey carrier** (the figure) | renders patchily; the recolor amplifies blotches. Note `--grey-preprocess` (the *backdrop*) is a separate, still-untested idea. |
| **Skin harmonisation at low denoise** | 0.4 → +0.10 spread, 0.75 → +0.43, against **+6.5 needed**. At low denoise the model preserves what it is given rather than re-colouring. |
| **`--harmonize-source carrier`** | scored *highest of everything* (16.57) and is broken: fails `outside_mask_unchanged`, and a green halo inflates the metric. `SKIN_BLEND_EDGE_GUARD` holds the mask off the silhouette, so the outer ring of paint is never repainted. |
| **Lower clothes denoise** as a halo fix | halo unchanged (−15 to −18 throughout) and the garment is destroyed: **54% of paint left** at 0.6. Qwen shows the same −13 halo, so it was never a FLUX.2 defect. |
| **Coverage prompts / canny** for the underarm gap | all *worse* than baseline's 964 px — bulk 1,071, sides 1,091, canny 1,177. |
| **FLUX.2 Klein as clothes editor** | works mechanically (`outside_mask_unchanged` 0, remnant 0.0026) but the garment is consistently flatter than Qwen's at every prompt rung. |

**The canny null is trustworthy**, unusually. `--control-distort 0.8` produced **40× the gap**, proving
the ControlNet was live — so "it did not help" is an answer rather than a dead mechanism. This
matters because on 2026-08-04 three control runs at strength 1.0 showed nothing and "the ControlNet
is inert" was nearly written up as fact. **Any control test must run at strength ~3.0 and include a
distorted control**, or its null means nothing.

---

## 3. Still open

### 3.1 Skin hue — specified, not solved

The recolor is a **one-dimensional colour model**: `target_rgb * ratio` puts every pixel on one line
through colour space, so saturation is constant *by construction*.

| | saturation spread | body-vs-face delta |
|---|---|---|
| deterministic recolor | 4.37 | (70, 65, 54) — too bright, right hue |
| `--skin-blend-prompt variation`, denoise 1.0 | **10.86** | (44, 14, −1) — **right brightness, wrong colour** |
| performer's real body | 10.93 | (18, 18, 19) — uniform |

Asking for *variation* instead of "blend and even out" fixes the flatness. But the body then reads
**+44 red against the face**, where real skin differs uniformly across channels. That is what a
reviewer notices first, and `face_body_tone_match` now gates it (magnitude and **hue spread**).

**Next:** `--skin-blend-prompt tone`, which names a target instead of pointing at her face. One edit,
no downloads.

### 3.2 Underarm gap — 964 px, cause unknown

Not prompts, not controls. The garment is painted narrower than the carrier's torso. The layer split
made it *visible* rather than filling it with skin, which is correct but not a fix.

**Leading hypothesis, untested:** the clothes mask is `dilate(person, 97)`, extending ~48 px into
open backdrop. Generous into *empty space* is not the same as covering the *body*. Bounding the mask
to the figure plus a modest margin may address this and 3.3 together.

### 3.3 Background inside the editable mask

**80,414 background pixels are regenerated on every run.** Measured between two clothes plates:
100.0% of differing pixels sit inside the mask, none outside, and the in-mask backdrop returns at
green 156 against 172 outside. Both models do it; Qwen is only *accidentally* faithful.

This is a latent design flaw that a model swap exposed. Fix: stop putting background in an editable
mask. Local work, no pod.

### 3.4 Hair — a generation problem, not an extraction one

The source hair edge is a soft gradient with **19.6% intermediate pixels** and no strand detail. The
extraction thread that ran from 2026-08-06 was aimed at the wrong stage. The only real fix is
generating at higher resolution and downsampling (`--carrier-size` exists), or a model that outputs
foreground colour as well as alpha (FBA-style).

---

## 4. Built but never run

- **`--skin-blend-prompt tone`** — the next skin arm.
- **`--carrier-model flux1|flux2`** — three carrier generators comparable on one seed. Untested;
  the plastic look may be the base model, in which case much downstream work treated a symptom.
- **`--grey-preprocess`** — ran once, scored 1.80 spread (flatter) with little visible difference.
  Unexplained.
- **`--neck-overlap`** — more real chest transplanted. Needs pose-keypoint alignment to be worth
  much; alignment is currently a single face-box fit at ratio 1.048.
- **CatVTON on a FLUX.1 Fill backbone** — the config holding the VITON-HD benchmark. Wrapper is
  installed; backbone is not.

## 5. Known papercuts

- `flux2_clip_type_supported` is gated on the *carrier* family, so it never runs for a FLUX.2
  **editor**. It happened not to matter — the `"flux2"` guess was right — but that is luck.
- Preflight fails once on every new pod: `BatchMode=yes` plus an unknown host key. Fix with
  `StrictHostKeyChecking=accept-new`, as `standalone_alpha()` already does.
- No preflight check on **volume quota**. A 24 GB download half-completed, left a partial, and broke
  `scp` for every subsequent arm — surfacing as `scp: close remote: Failure`, which says nothing.
  The real error only appears with `scp -O`: *disk quota exceeded*.

## 6. Lessons that cost the most

**Metrics without the picture beside them failed four times.** A skin gate whose floor (4.0) sat
*below* its own baseline (4.37) passed three variants that had done nothing. A coverage gate at 0.02
passed 964 visibly wrong pixels. A layer change reported as a "66% improvement" was invisible in the
composite. A variant scored best of everything while showing a green halo. **Set thresholds from a
run, never from reasoning, and never report a number without looking.**

**`pgrep -f` / `pkill -f` match the watching command's own command line.** Once killed a launcher
(exit 144, relaunch silently never happened), once deadlocked a waiter against itself and left the
pod idle. No driver script uses either.

**Confounded experiments read as failures.** The garment-ref run dropped the outfit description *and*
changed conditioning at once; its output was missing exactly the four features the description named.
It was recorded as "garment references do not work" when it had never been tested.

**BiRefNet peaks near 6 GB per plate on CPU** and OOM-killed the 7.4 GB dev box three times.
`birefnet_extract.py` now refuses below a memory floor and points at the pod. Matting belongs on GPU.
