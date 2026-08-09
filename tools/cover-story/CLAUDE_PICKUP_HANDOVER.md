# Cover Story layered-costume pipeline — pickup handover for Claude

Written 2026-08-07, end of a long GPU session. This is the self-contained state; read
`VITON_CLOTHING_POC_HANDOVER.md` and `LAYERED_COSTUME_PRODUCTION_STATUS.md` for the deep history.

> **Superseded in part — read "Visual review, 2026-08-08" at the bottom before acting on this
> file.** That review looked at the composites (this session's author could not) and measured
> several of the claims below to be wrong: the chroma-garment-ref run is *not* the strongest
> variant, the garment-ref experiment was confounded, and the BiRefNet swap reintroduced chroma
> fringing. The mechanics recorded here — bug fixes, pod state, file locations — are accurate and
> still apply.

## What the pipeline is

Cover Story (a Stash plugin) pre-generates composited covers from layers:
background → skin → clothed-body → hair, stacked in the browser. The asset factory lives in
`tools/cover-story/`, with the active PoC runner `run_qwen2512_skin_head_clothes_poc.py` (9-10
gated GPU stages) against a rented RunPod (ComfyUI + Qwen 2512/Edit 2511 + SAM3 + CorridorKey).

## The recipe that works (the strongest variant produced so far)

**Chroma carrier + garment-ref clothes + BiRefNet matting** — run `chroma-garmentref-poc-20260807`
completed with every stage green:

- carrier: green body paint on blue screen (the ORIGINAL chroma prompt — proven, do not switch)
- identity: deterministic — performer's head transplanted onto the carrier's body, recolored to the
  performer's face tone (0/0/0 px registration, tone (196,158,127) correct)
- clothes: masked Qwen Edit 2511 conditioned on a **WORN garment reference image** (image 2),
  `--garment-worn` — dropped the paint remnant from 18.7% to 3.6%
- extract: **local BiRefNet matting** (`--extract-mode birefnet`) — 0 interior holes on both
  plates, no pod/SSH/CorridorKey needed; runs ~1-2 min/plate on this VM's CPU
- composite gate: skin visible where the garment should be, 3.6% (threshold 5%)

Run flags (all implemented + self-tested):
`--clothes-mode garment-ref --garment-worn --extract-mode birefnet --auto-accept-envelope`

## Where things live

- Two completed runs with `review.html` on the NFS drive (`/mnt/Misc/sd/cover-story/`):
  - `chroma-garmentref-poc-20260807/` ← the good one. **Its composite was corrected locally after
    an aperture bug** (see below); `composite-buggy-aperture.png` is the broken evidence.
  - `grey-garmentref-poc-20260807-1235/` — grey natural-carrier experiment; complete data point,
    direction shelved (user verdict: not convincing).
- Review pages are self-contained HTML with per-stage galleries, auto-check results, an A/B vs the
  v3 chroma baseline, and a localStorage accept/reject/notes panel (export → JSON).
- Local tooling: `birefnet_ab.py`/`ben2_ab.py`/`birefnet_grey_test.py`/`ben2_metrics.py` (the
  keyer A/B, all results in `/mnt/Misc/sd/cover-story/birefnet-ab-v1/`), `birefnet_extract.py`
  (standalone matting), `poc_review.py` (review page), `run_poc_bulk.sh` (unattended run + page),
  models in `tools/cover-story/models/` (BiRefNet tiny + BEN2 ONNX).
- `.venv-birefnet/` has numpy + onnxruntime (the runner itself is Pillow-only system python3).

## Bugs fixed this session (do not reintroduce)

1. **Aperture bug** (the "dress top missing" failure): the birefnet extract path used the identity
   head envelope (dilated 97px, reaches below the bust) as the clothes plate's aperture, punching
   a hole through the dress. Correct: chroma path uses the color-derived `key_aperture()`;
   grey path uses the SAM head-stop mask (`envelope["head_stop"]`, now saved by
   `bootstrap_envelope`). Verify with the `no_skin_in_garment_region` composite gate.
2. **Grey-path screen threading**: `envelope_checks`, `silhouette_checks`, `preprocess_checks`,
   `compose_identity`, `identity_composite_checks` all had hardcoded `screen_foreground()` (blue).
   All take `screen=` now. Any new grey/neutral path: check for this class FIRST — it cost several
   GPU cycles.
3. **Tone sampling**: `sample_face_tone()` (skin-hue median, top third) returns the canonical
   (196,158,127); the SAM-head-box mean returned (147,114,93).
4. **Flat-lay garment references fail on coverage** (79% of paint left in the lower body). Use
   `--garment-worn` (dress on a model) — 3.6% remnant.
5. **comfy-kitchen version**: ComfyUI 0.30.x silently disables fp8 loading with kitchen < 0.2.26
   (`layout_cls` None crash). `pod_bootstrap.sh` step 6b pins it against requirements.txt.
6. Runner wrapper `run_poc_bulk.sh` had a `set -u` unbound `$EXTRA` bug — fixed (initialize `""`).

## Shelved / not the path forward

- **Grey carrier** (grey-suit AND natural/swimsuit variants): renders patchily, recolor amplifies
  blotches, user verdict negative. `--grey-carrier` remains implemented but unused.
- The diffusion-repaint identity constructions (all superseded by the deterministic transplant).
- CatVTON (SD1.5-based) — installed only behind a flag; the garment-ref Qwen edit is the primary
  clothes path now.

## Operator limitation (important)

**This applied to the 2026-08-07 author (deepseek-v4-flash, text-only). It does not apply to
Claude, which reads images directly with the Read tool** — no API key, no extra dependency, no
cost. The entire 2026-08-08 review below was done that way. Do not spend effort wiring in a
third-party vision model; just look at the PNGs.

The underlying lesson stands and is worth keeping: evaluation had been numeric proxies only, and
proxies missed a dress with its top missing. Compensations already in place: the
`no_skin_in_garment_region` gate (has teeth: catches the bug at 15.7%, passes the fix at 3.6%) and
the human review loop via `review.html`. Still to add: face-vs-body tone consistency and
edge-fringe/halo detection (the latter is specified with measured thresholds below).

## Pod state and next run

- Pod is OFF. `~/.config/cover-story/instance.json` points at the dead host — repoint on the next
  spin-up (mode 600, config beats env, placeholders count as unset).
- The persistent volume has all models (Qwen 2512/Edit 2511 fp8mixed, Qwen VL text encoder, VAE,
  SAM3, SDPose, CorridorKey) and ComfyUI v0.30.2 with comfy-kitchen 0.2.26. `pod_bootstrap.sh`
  repairs the venv/rsync/cache-lru on preflight.
- Next run: the SAME chroma+garment-ref+birefnet recipe on the **Viking tunic** (second outfit,
  stress test) — new output dir, then compare against the plum-dress composite.

## Git state

The session's work is **uncommitted** (new tooling, runner flags, handover docs, `models/`). The
whole `tools/cover-story/` VITON/matting line should be committed deliberately — check
`git status` first; don't commit `models/` (ONNX weights) or the venv.

## Read order for a fresh agent

1. This file
2. `VITON_CLOTHING_POC_HANDOVER.md` (the PoC spec + session results + verified model links)
3. `LAYERED_COSTUME_PIPELINE_REFERENCE.md` (the approved production recipe — the layer contract)
4. `run_qwen2512_skin_head_clothes_poc.py --self-test` (should pass) and `layered_costume_production.py self-test`

---

# Visual review, 2026-08-08

First review of the 2026-08-07 output by an operator that can see images. Both self-tests pass,
every stage check is green, the aperture-bug fix is genuine, and BiRefNet really does eliminate the
interior holes. The *verdict* was still wrong: `chroma-garmentref-poc-20260807` is visibly worse
than the `v3` baseline it A/B's itself against.

## 1. The garment-ref experiment was confounded — it is untested, not disproven

`GARMENT_REF_PROMPT` **drops the entire garment description**. `CLOTHES_PROMPT` names *"a high
collar, long sleeves, matching gloves, a full skirt and dark leather shoes"*; the garment-ref
prompt says only "the outfit from image 2". The output is missing exactly: collar, gloves, full
skirt, shoes — one for one. So the run tested "image **instead of** text", never "image **plus**
text".

The coverage justification also does not survive comparison. Worn-garment ref scored 3.6% paint
remnant against flat-lay's 18.7% — but **v3's plain prompt-driven clothes scored 0.78%**
(`cover-story-qwen2512-skin-head-clothes-poc-v3/checks.json`). Garment-ref was only ever compared
against a broken sibling, never against the reigning best. Catalog compliance is also a fail: every
outfit specifies closed footwear and gloves, and the composite has bare feet and bare hands.

**Next test:** garment image *plus* the full catalog description, one variable changed, A/B'd
against v3.

## 2. BiRefNet reintroduced chroma fringing — mechanism identified

Edge-band (3 px inner rim) pixels that are screen-colour-dominant:

| layer | v3 (CorridorKey) | 08-07 run (BiRefNet) |
|---|---|---|
| identity, blue | 9.3% | **27.7%** |
| hair, blue | 0.0% | **21.8%** |
| clothes, green | 6.4% | **40.2%** |

Cause: both extractors preserve source RGB (`alpha_policy` says so in both `poc.json`s), so the
difference is *alpha tightness*. CorridorKey is a chroma keyer — screen-coloured pixels are
transparent by definition, so spill cannot survive inside its matte. BiRefNet is a salient-object
segmenter with no colour semantics. Measured on the same plate: BiRefNet's matte is a strict
**superset** of CorridorKey's (only 1 px goes the other way), adding 6,632 px of which 2,663 are
green-dominant. CorridorKey retains **zero**.

`segment_source()` → `scoped_despill()` only despills *enclosed holes*, never open boundaries —
correct for CorridorKey, exactly wrong for BiRefNet. The 08-07 keying A/B did note the fringe
("spill baked into the source RGB; grey backgrounds remove it by construction") — but that was
written while the grey carrier was still the plan. Grey was shelved on generation quality and the
fringe mitigation went with it, never replaced. **That is the precise seam where this broke.**

Two fixes, both verified locally, no GPU:
- **Rim despill** — `despill_source()` over the alpha rim + partial-alpha band: 27.7% → **0.0%**,
  40.2% → **0.0%**, halo visually gone.
- **Hybrid matte** — BiRefNet alpha × chroma gate (`screen_foreground()`), i.e. shape from the
  learned model, edge precision from chroma. On v3's clothes plate: interior holes 3,730 vs
  CorridorKey's 4,426, rim spill **0.0%** vs 8.9% / 60.8%. **Strictly better than CorridorKey on
  both axes** — the two extractors are complementary, not alternatives.

## 3. The boat neckline is mask geometry, not prompt wording

`masks/clothes-body-mask.png` has a rectangular notch over the neck/upper chest with a flat
horizontal bottom edge. The neck is *outside* the editable region, so a high collar is
geometrically impossible — no prompt or reference image can produce one. The masks are byte-
identical between v3 and the 08-07 run (md5 match) and both composites show the same neckline.
STATUS.md has attributed this to prompt phrasing for several sessions; that is wrong.

The root cause is that **one mask does two jobs**: "where the model may paint" and "where the plate
must be transparent". Splitting them (edit mask includes neck and chest; transparency aperture is
face-only) makes deep necklines *and* high collars both possible — today neither is.

## 4. The skin recolor is a one-dimensional colour model

Measured on the recolored body vs the performer's own aligned bare body:

| region | hue spread | sat spread | value |
|---|---|---|---|
| recolored torso | ±7.8 | ±3.3 | 86.7 |
| real performer torso | ±20.3 | ±10.4 | 71.5 |

Mean hue 28.6 vs 28.4 — **the tone match is essentially perfect**; the variance is not.
`deterministic_skin_recolor()` computes `target_rgb * ratio`, so every output pixel is a scalar
multiple of one RGB vector: all pixels lie on a single line through the origin, and hue and
saturation are constant **by construction**. It cannot produce chromatic variation however the
ratio is computed. That flatness is what reads as waxy, and why the transplanted head looks like a
different material from the body.

**A Lab luminance/chrominance split does NOT fix this — do not redo it.** Tried 2026-08-08: keep L
from the carrier, take a/b from the performer. It corrects the statistics (sat spread 6.6 → 13.6,
value 85.7 → 63.8 against a 63.5 reference) and still looks wrong, because the carrier's L *is*
body paint — harsh painted contouring, non-photographic speculars, odd knee creases. Fixing only
chroma preserves exactly what looks fake.

**Promising, unproven:** warp the performer's own body pixels (`preprocessed.png`, already aligned
to within 3–4%, real photographic skin) onto the carrier's silhouette. A crude per-row run-aware
warp gives sat 37.6 ± 11.9 / value 64.0 against the reference's 36.8 ± 10.0 / 64.2 —
statistically indistinguishable from real skin, and visibly real texture. But warping each row
independently tears badly where arms merge into the torso (236 of 707 rows fell back to a whole-row
stretch). The principled version is a piecewise-affine warp driven by pose keypoints — **SDPose is
already on the pod**. Prove it before adopting it.

## Matting: we are using the wrong checkpoint

`BiRefNet-general-tiny` is a *segmentation* model, which is why the alpha is near-binary and hair
edges are jagged. BiRefNet ships dedicated matting checkpoints:

- `BiRefNet_HR-matting` (Feb 2025) — trained at **2048×2048**. We currently matte at ~682×1024
  effective on an 832×1248 plate, so the matte is computed downsampled and upscaled.
- `BiRefNet-matting` — continuous alpha at 1024².
- `BiRefNet_dynamic-matting` — trained on 256²–2304², shape-robust.

As of mid-2026 there is no clear open-source successor to BiRefNet. For genuinely soft edges the
trimap family (ViTMatte, MatAnyone) is the right tool, and chroma dominance yields the trimap for
free — but try the matting checkpoints first.

Test order matters (one variable at a time): `BiRefNet-matting` at 1024² isolates *matting head vs
segmentation head*; `BiRefNet_HR-matting` then isolates *resolution*. Watch memory — this VM has
7.4 GB and 2048² is 4× the pixels; keep `enable_cpu_mem_arena=False` and run one ONNX job at a time.

### RESULT 2026-08-08: the matting checkpoint is not worth adopting

Tested `emrikol/birefnet-matting-onnx` (ONNX export of `ZhengPeng7/BiRefNet-matting`, SwinL,
940 MB, 1024²) against the shipped `BiRefNet-general-tiny` on the v3 plates:

| plate | model | interior holes | rim spill | soft-edge px |
|---|---|---|---|---|
| identity | tiny (segmentation) | 10,370 | 48.5% | 13,747 |
| identity | **matting** | 10,145 | 50.0% | 16,493 |
| clothes | tiny (segmentation) | 3,345 | 62.4% | 9,683 |
| clothes | **matting** | 3,446 | 62.3% | 11,284 |

Holes and spill are unchanged. The soft-edge band grows ~20%, which is real but small, and the
hair/neckline checkerboards are visually indistinguishable. Cost: **13 min vs ~2 min per pair**
(6.5×) and 940 MB vs 224 MB. Not adopted.

**Why — and this matters more than the result.** The limit is the *source plate*, not the matte.
Zooming the hair edge on `identity.png` (a 70×70 crop at the left hairline) shows a soft, blurry
boundary with **19.6% intermediate pixels** — the generated hair has no crisp fine strands, just a
wide mushy gradient, and the few stray wisps present are so low-contrast against the blue that they
are nearly screen-coloured already. **No matting model can recover detail that was never rendered.**

Three consequences:
- **The "jagged hair" thread that has been open since 2026-08-06 is aimed at the wrong stage.** The
  jaggedness is an extractor thresholding a mushy gradient inconsistently; the gradient is the
  problem, and it comes from generation.
- That wide soft band is exactly where the blue spill lives, which is why despill matters far more
  than the choice of matting model. Prioritise the hybrid matte + rim despill accordingly.
- If soft hair edges are ever genuinely wanted, the useful model class is one that solves for
  *foreground colour as well as alpha* (FBA-style F/B/α decomposition), because that turns the
  blue-contaminated band into correct hair colour at partial alpha. A pure-alpha model cannot.

**Do not try `BiRefNet_HR-matting` on this VM.** At 1024² the SwinL export already peaked at
4.4 GB RSS with 1.8 GB headroom; 2048² is 4× the activations and would OOM. It would also face the
same source-resolution ceiling. Revisit only on a GPU box, or with tiling.

## Pod session 2, 2026-08-08 — the collar works

**Run E (`pod-20260808b-E-masksplit`) produced a proper stand collar — the first in the project's
history.** Plus gloves, shoes, corset seams, and **0.32% paint remnant**, the best of any run
(B 0.46%, 08-07 3.6%). All stages through `[clothes]` green.

| feature | 08-07 | run B | **run E** |
|---|---|---|---|
| high collar | no | no | **yes** |
| gloves / shoes / seams | no | yes | yes |
| paint remnant | 3.6% | 0.46% | **0.32%** |

The fix was one word. `CLOTHES_STOP_SAM_PROMPT` was `"head, face, ears and neck"`, and
`head_stop_region()` cuts the forbidden zone at the bottom of *that region's* bbox — so including
the neck put the cutoff at the neck base and made the neck unpaintable. Its own docstring already
said it "must stop at the neck base so the garment keeps its shoulders and collar"; the prompt
contradicted the intent. Dropping `"and neck"` moves the cutoff to the chin. The 97 px dilation
still guards the skull sideways and upward against the forbidden bonnet — the region is anisotropic
by design and only its downward extent was wrong.

**The aperture half needed no code.** `key_aperture()` is colour-derived, so it follows what the
model painted: a collar over the neck is garment and therefore opaque, while anything left
unpainted stays carrier paint and keys transparent so the skin layer shows through. Deep necklines
and high collars are now both reachable from one mask, which is what the catalog asks for.

**Not run:** E's `[extract]`/`[composite]`, the denoise-1.0 skin test, run A, run D.

### The blocker: BiRefNet OOMs this VM, reproducibly

`[extract]` was killed twice by the kernel OOM killer at ~5.9 GB anon-rss. Diagnosed under a 4 GB
`ulimit -v`: it fails inside BiRefNet's decoder at
`/decoder/decoder_block1/dec_att/aspp_deforms.2/atrous_conv/Mul_8` — a deformable-convolution ASPP
block — with `std::bad_alloc` at 2.9 GB RSS.

Environment, not pipeline: 7.4 GB RAM plus 4 GB swap, but **3.3 GB of that swap is already held** by
long-lived desktop processes (`opencode` ~350 MB, several `node`, `gnome-shell`), leaving ~740 MB.
The matting has been running on a vanishing margin all day. `sudo` needs a password so swap cannot
be grown. `intra_op_num_threads=1` avoids the peak but takes **>10 min per plate**, so it is not a
workaround.

**The fix is GPU matting, and it is the next thing to build.** The pod has 32 GB of VRAM idle while
a 7.4 GB CPU box does matting on the critical path *between* GPU stages. `pod_bootstrap.sh` step 8
already prepares the pod environment and `birefnet_ab.available_providers()` already selects CUDA
when present; what is missing is the runner rsyncing `models/*.onnx` to the volume and invoking
`birefnet_extract.py` over SSH, mirroring `standalone_alpha()`. This removes the OOM class, removes
CPU time from between GPU stages, and is pure local code — no pod needed to write it.

Cheap stopgap if a composite is needed before then: closing `opencode` frees ~350 MB of swap.

## Pod session results, 2026-08-08

**Run B — `pod-20260808-B-garmentref`, complete, every stage green. Best result the pipeline has
produced.** Recipe: `--clothes-mode garment-ref --garment-worn --extract-mode birefnet
--auto-accept-envelope`.

| | 08-07 run | v3 baseline | **run B** |
|---|---|---|---|
| paint remnant | 3.6% | 0.78% | **0.46%** |
| gloves / shoes | missing | present | **present** |
| hair fringe | 0.218 | 0.000 | **0.000** |
| clothes fringe | 0.402 | 0.064 | **0.000** |

Both diagnoses confirmed independently: restoring the dropped description brought back gloves, shoes
and seam structure, while **the collar stayed missing** — the one feature predicted to be
unreachable by prompt, because it is mask geometry. The fringe thresholds were calibrated on
historical plates and this was the first run they judged that they were not fitted to; all three
read exactly 0.000.

**Skin harmonisation — clean negative, do not retry as-is.** Measured over the body mask:

| denoise | saturation spread | gained | registration |
|---|---|---|---|
| 0.4 | 4.41 → 4.51 | +0.10 | 0/0/0 |
| 0.75 | 4.41 → 4.84 | +0.43 | 0/0/0 |
| *real skin target* | *10.93* | *needs ~+6.5* | |

At low denoise the model preserves what is there rather than re-colouring it, and the trend is
nowhere near the target. **Registration held at 0/0/0 throughout**, so the masked-edit safety
mechanism works — the failure is the regime, not the machinery.

**The untested follow-up:** a masked recolor at **denoise 1.0**. The `[skin]` stage's full diffusion
recolor *does* produce real variation; the difference is denoise, not the model. The earlier warning
that 1.0 reproduces the abandoned repaint was about a *mask-free* edit, and there is now direct
evidence the mask plus `SKIN_BLEND_EDGE_GUARD` holds registration. One edit to test.

**Run A (`--grey-preprocess`) never ran** — the pod stopped during its carrier stage. Still
unanswered. All its prerequisites are verified: `rebackground()` measured correct on real pod data
(1,035,773 px corrected, max delta 37, figure untouched), and `sample_face_tone()` excludes neutral
grey by construction (`r>g` fails).

**Three bugs cost pod time, all mine, all avoidable:**
1. `preflight()` referenced `args` — module-level function, `NameError` on first pod call. Caught at
   `--stop-after preflight`, the cheapest possible place.
2. `head_region_bit_exact` compared against `performer-aligned.png` after `rebackground()` changed
   what the transplant pastes from. `compose_identity()` now returns the actual transplant source.
3. **`pgrep -f` / `pkill -f` on a pattern matching the watching command's own command line** — twice.
   Once killed a launcher (exit 144, relaunch silently never happened), once deadlocked a waiter
   against itself and left the pod idle for minutes. Never do this; `drive.sh` uses neither.

**Process lesson:** batch every queued run into one detached driver from the start instead of
launching and babysitting them one at a time. Most of the wasted pod time was idle, not compute.

## Implemented 2026-08-08 (local block — no GPU, both self-tests green)

New in `layered_costume_production.py`, all self-tested:

- `screen_dominant()`, `matte_edge()`, `edge_despill()` — despill restricted to the band just
  inside a matte's boundary plus every partial-alpha pixel. `segment_source()` now calls it, so
  every path (PoC *and* production `extract()`) gets it. Deliberately not a whole-plate despill:
  that is what `segment_source()` used to do and it rewrote pixels whose alpha was already right.
- `chroma_gate()` — the hybrid matte. Zeroes alpha where the source is still screen-dominant, so a
  learned model supplies shape and chroma supplies edge precision.
- `fringe_fraction()` + `FRINGE_LIMIT` (0.15) — the halo gate. Measured separation is wide:
  CorridorKey 6.4–9.3%, BiRefNet 21.8–40.2%, either one after `edge_despill()` 0.0%.

New in `birefnet_extract.py`: `--upscale MODEL` runs super-resolution over the plate **before**
matting and scales the alpha back down, so the upscaled pixels only ever inform the matte and never
reach the output — RGB and 0/0/0 registration are untouched. Tiled (512/32 overlap) because a whole
plate at 2× is 1664×2496 and this VM has 7.4 GB; measured ~500 MB RSS, so it is comfortable.

New in the runner: `--matte-upscale` / `--upscale-model` (**off by default** — proven on a head
crop, not yet on a whole plate), `chroma_gate()` applied to both birefnet alphas (idempotent, so a
resumed run re-gates to the same result), and three `no_screen_fringe_*` checks in `[composite]`.

`GARMENT_DESCRIPTION` is now a single constant shared by `CLOTHES_PROMPT`, `GARMENT_REF_PROMPT` and
`GREY_GARMENT_REF_PROMPT`, with a self-test asserting every clothes prompt carries `{description}`
and that the four lost features (collar, gloves, full skirt, shoes) survive into it. This is the
confound fix; the garment-ref run can now be repeated as a real test.

### Stack measured end to end — the chroma gate is the whole win; full-plate upscale is not

Run through the real `production` functions on v3's plates:

| layer | stage | interior holes | fringe |
|---|---|---|---|
| identity | 1 birefnet (ships today) | 10,370 | 0.485 |
| identity | 2 + upscale-before-matte | 10,434 | 0.487 |
| identity | 3 + chroma gate + edge despill | 10,529 | **0.000** |
| clothes | 1 birefnet (ships today) | 3,345 | 0.624 |
| clothes | 2 + upscale-before-matte | 3,344 | 0.552 |
| clothes | 3 + chroma gate + edge despill | 3,739 | **0.000** |

**The chroma gate plus edge despill takes fringe to zero on both plates**, at a small hole cost
(+159 identity, +394 clothes) from removing screen-coloured pixels that had been bridging. Good
trade; this is the part to keep.

**Full-plate upscale is not worth it and should not be turned on.** Identity went 0.485 → 0.487
(marginally *worse*), clothes 0.624 → 0.552 (modest), for 25–32 min per plate. That contradicts the
head-crop result (64.3% → 57.2%) and the reason is instructive: BiRefNet letterboxes its input to
1024² regardless, so upscaling a whole 832×1248 plate to 1664×2496 and then squeezing it back to
1024² leaves the **effective matte resolution unchanged**. In the head-crop test the crop *filled*
the 1024 box, giving ~2.6× the effective resolution. So the measured gain there was mostly
resolution, not learned sharpening — the earlier conclusion over-generalised from a crop to a plate.

Consequences: `--matte-upscale` stays **off by default**; it is only worth anything **region-scoped**
(matte the head box separately at native scale and merge), and even then the chroma gate already
takes spill to zero, so the remaining value is edge *placement*, which the fringe metric does not
measure. Do not spend GPU on full-plate upscaling on the strength of the head-crop number.

### GPU matting: measured cost, auto-detection, and what is still missing

**Full-plate upscale-then-matte measured 25m30s per plate on this CPU VM.** That is why
`--matte-upscale` ships off by default, and why the head-crop scoping mattered on CPU.

- `birefnet_ab.available_providers()` picks `CUDAExecutionProvider` when the runtime actually lists
  it, CPU otherwise — auto-detected, no flag, so the same script runs on this VM and on a pod.
  Verified locally: this VM reports `['AzureExecutionProvider', 'CPUExecutionProvider']` and
  correctly selects CPU.
- `pod_bootstrap.sh` step 8 builds a flag-gated matting venv on the volume
  (`$VOLUME/install-matting`, same pattern as CatVTON) and **gates on `CUDAExecutionProvider` being
  listed, not on the wheel installing**. Stock `onnxruntime`, or an `onnxruntime-gpu` built against
  a different CUDA runtime than the pod has, both install cleanly and then fall back to CPU
  silently — a 25-minute no-op instead of an error.
- Preflight touches that flag when `--matte-upscale` is set.

**Not done: the runner still mattes locally.** The pod-side environment is prepared and verified,
but nothing invokes it over SSH yet. Making it real means rsyncing `models/*.onnx` to the volume and
running `birefnet_extract.py` remotely, the way `standalone_alpha()` already does for CorridorKey.

**Keep the local CPU path as a fallback when that lands.** Being able to re-extract historical plates
without renting anything is what made 2026-08-08 productive — the v3 plates were re-extracted five
ways (CorridorKey, BiRefNet, the matting checkpoint, the hybrid, upscaled) to find the spill
mechanism. Auto-detection preserves exactly that: pod up, fast; pod down, still works.

### Skin tone: composite first, then harmonise (implemented, not yet run)

The user's call, and it is better than either alternative tried on 2026-08-08. The evidence:

| region | hue spread | sat spread |
|---|---|---|
| torso — deterministic recolor | ±5.8 | ±2.0 |
| torso — diffused `[skin]` stage | ±19.8 | ±8.3 |
| torso — performer (real) | ±21.9 | ±9.9 |

Diffusion produces essentially real-skin chroma statistics; the deterministic recolor cannot, by
construction. **The `[skin]` stage already does this and is vestigial** — nothing downstream reads
`skin-tone.png`. It was dropped from the identity path for one reason: up to 7 px of drift against
the carrier, while the clothes plate is built from the carrier directly, so the layers disagreed.

**That drift is not inherent to diffusion recolor.** Line 1761: *"Single reference and mask-free"* —
the stage runs a whole-frame edit with no mask. A masked edit gets `outside_mask_unchanged: 0` by
construction, the same property the clothes stage and the seam smoothing already rely on.

Two dead ends worth not repeating, both measured:
- **Chroma-only transfer from the diffused plate** fails. Blurring the chroma field enough to
  survive 7 px of drift flattens hue spread from 12.27 to **1.50** — it makes the deficit worse.
  Colour is not the whole gap; diffusion is also repairing the *shading*, and that cannot be taken
  without geometry.
- **Prompting a tone** is weaker than compositing one. `SKIN_PROMPT` says "one fair, warm natural
  skin tone" — the same words for every performer. The composite states the target in pixels, and
  the transplanted head is in frame as the reference.

Implemented (local; the run itself needs a pod):

- `production.chroma_spread()` — saturation mean and spread, the quantitative acceptance metric that
  did not exist before. Spread is the one that matters: the *mean* was already correct on the flat
  plate, so a mean-based check would have passed it.
- `production.edit_graph(..., denoise=1.0)` and `edit(..., denoise=...)` — **denoise was hardcoded
  1.0 everywhere**, and that is the wrong regime here: at 1.0 a body-shaped mask regenerates the
  body, which is the abandoned 2026-08-05 construction whose proportions varied by seed. Every
  existing caller keeps 1.0; self-tested both ways.
- `harmonize_skin()` + `SKIN_BLEND_PROMPT` (same shape as the proven `SEAM_PROMPT`) and
  `skin_blend_checks()`, behind `--harmonize-skin` / `--skin-blend-denoise` (default 0.4).
- The mask is the carrier's body, minus the dilated head, **eroded off the silhouette by
  `SKIN_BLEND_EDGE_GUARD`**. `ImageCompositeMasked` only guarantees bit-exactness *outside* the
  mask, so a mask reaching the boundary would let a body-wide edit shrink the silhouette within it.
  Self-tested on all four cases (body editable, head protected, silhouette ring protected,
  background never editable).

Gates on the run: `outside_mask_unchanged` 0, `body_matches_carrier` 0/0/0, and
`skin_gained_chromatic_variation` (saturation spread ≥ 4.0, against the flat plate's 2.0 and real
skin's 9.9).

**Separate problem, do not blame it on this:** the odd knee creases persist in *every* synthetic
variant including the diffused one. They are a carrier-generation artifact and no recolor will fix
them.

### Identity construction: backdrop correction before the transplant (changed, with approval)

`head_transplant()` composites the performer through a **geometric** mask (`preserve` = SAM head
dilated by `NECK_OVERLAP`), so whatever backdrop surrounds her hair inside that mask is pasted onto
the plate along with her: **17.6% of the mask, 6,406 px of 36,377**, measured on the 08-07 run.
That is invisible only while her backdrop and the carrier's are the same colour — and it is what
made "just decouple `GREY_PREPROCESS_PROMPT`" a broken one-liner, because a grey backdrop would land
as a grey halo that `screen_foreground(..., "blue")` then reads as *figure*.

The user approved altering the identity construction on 2026-08-08. Implemented:

- `production.rebackground(image, alpha, from_rgb, to_rgb)` — swaps one known flat backdrop for
  another, correcting the **blend band** and not just the solid background:
  `C' = C + (1-a)*(B_to - B_from)`. Deliberately avoids recovering the unmixed foreground `F`,
  because that means dividing by `a` and amplifies noise worst at the wispy hair edge this exists to
  fix. Opaque pixels are untouched by construction. Self-tested on all three cases (opaque,
  half-covered, solid backdrop) plus the swap-for-itself no-op.
- `aligned_performer()` now pads the aligned canvas with the **performer's own backdrop** instead of
  the carrier's, so the frame shares one `from_rgb`. Padding with the destination colour would be
  shifted by the full delta as though it were hers.
- `compose_identity()` mattes the aligned performer locally (BiRefNet, no pod) and calls
  `rebackground()` before the transplant, writing `performer-aligned-rebackgrounded.png`.

**Registration is unaffected** — that is the body below the head, which never enters this path.

This is not a no-op even today: the two backdrops already differ, performer `(1, 84, 139)` against
carrier `(8, 84, 168)`, i.e. 29 levels of blue across those 6,406 pixels. So it also removes a small
pre-existing seam nobody had noticed.

**Screen threading — done.** `preprocess_checks()` and `aligned_performer()` now take separate
screens for the preprocessed plate and the carrier, because the two no longer share a backdrop under
`--grey-preprocess`. Self-tested with a negative control: keying a grey plate as blue must *fail*
coverage, so the threading is exercised rather than merely present. (Note `screen_foreground(screen=
"grey")` estimates the backdrop from border bands and therefore copes with a blue frame too — the
failure only shows in the grey-plate-keyed-as-blue direction, which is why the control runs that way
round.)

**Cost this added, measured on the first pod run:** `compose_identity()` now runs a local BiRefNet
matte of the aligned performer, ~2 min of CPU per run on this VM. Tolerable for a single PoC run;
for a production sweep of 4 performers x 4 poses it is ~30 min of CPU on the critical path. Two
fixes, both cheap and neither done yet:

- **Cache it.** `performer-aligned.png` is deterministic for a given performer and pose, so the
  matte only needs computing once, not once per run. This is the easy win.
- **Or route it to the GPU** once the runner learns to call `birefnet_extract.py` over SSH; the pod
  environment for that is already prepared by bootstrap step 8.

**Bug caught on first pod contact, 2026-08-08:** `preflight()` referenced `args.matte_upscale`, but
it is a module-level function taking explicit parameters -- `NameError` on the very first call
against the pod. The self-tests do not cover `preflight()` because it needs a live server, so this
class of mistake reaches the pod. It is now a proper parameter. Worth remembering that
`--stop-after preflight` is the cheapest possible place to discover such a thing, and worth running
first on every new pod for exactly that reason.

## Agreed plan (Phase 1 first, all of it local and GPU-free)

1. ~~Matting checkpoint swap and measurement.~~ **Done 2026-08-08 — negative, not adopted.** See
   the RESULT block above; the limiting factor is the source plate's soft hair edge, not the matte.
2. Hybrid matte + rim despill wired into the birefnet extract path. **Now the highest-value item**,
   since the checkpoint result showed despill dominates model choice for this failure.
3. Edge-fringe/halo gate on the composite. Measured separation is wide: 8.9 / 60.8 / 0.0.
4. Recompose the existing plates and review.

Then (needs a pod, one variable per run): garment-ref **plus** full description; the mask/aperture
split with catalog-declared skin zones; and only after those pass, the Viking tunic stress test.

Deliberately not doing: grey carrier (shelved), CatVTON, and the tunic before the recipe is fixed.

## Housekeeping

`tools/cover-story/models/` is **427 MB of ONNX weights** and must never be committed; likewise
`.venv-birefnet/`. Add both to `.gitignore`. `plugins/cover-story/assets/performers/tmpgiuhjpgy/`
is a stray temp dir. Run-directory `checks.json` files record the pod's SSH host — keep run data
out of the repo and out of commit messages.
