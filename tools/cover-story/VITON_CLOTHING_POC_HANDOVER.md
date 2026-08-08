# VITON clothing-stage PoC handover

Updated: 2026-08-07 after the offline keying A/B (no pod involved).

**Implementation status (same day):** the grey-carrier / VITON / matting path is now coded in
`run_qwen2512_skin_head_clothes_poc.py` and self-tested; the pod-side CatVTON install is in
`pod_bootstrap.sh` step 7 (flag-gated). See the bottom section for the exact run commands.

This document is the written target for the next pod session. It proposes
replacing the masked Qwen-Image-Edit clothes stage with a virtual try-on
(VITON) stage, and replaces chroma keying with learned matting on grey
backgrounds. It changes the generation recipe, not the shipped layer contract:
background → skin → clothed-body → hair stays exactly as the approved pipeline
defines it.

Read this after `LAYERED_COSTUME_PIPELINE_REFERENCE.md`. It does not supersede
that reference; it is an experimental alternative whose promotion requires the
visual-review gate below, exactly like `run_qwen2512_skin_head_clothes_poc.py`
before it.

## Keying A/B — evidence from 2026-08-07 (offline, no GPU)

Three-way comparison on the existing `qwen2512-skin-head-clothes-poc-v3`
plates (CorridorKey vs BiRefNet-general-tiny vs BEN2, all CPU-local):

| plate | region | CorridorKey | BiRefNet | BEN2 |
|---|---|---|---|---|
| identity | hair | 11 specks, 32 holes | 0, 0 | 1 speck, 0 |
| identity | neckline | 8 specks, 15 holes | 0, 0 | 0, 0 |
| clothes | feet/shoes | 7 specks, 9 holes | 0, 0 | 0, 1 hole |
| clothes | full | 7 specks, 11 holes (2693 px) | 0, 2 (2275 px) | 0, 3 (2921 px) |

- Both learned matting models eliminate the hole/speckle class on every known
  defect site; CorridorKey is the worst of the three on all of them.
- BiRefNet (tiny) ≥ BEN2 (base) here and was faster on CPU (78 s vs 139 s per
  pass). BEN2's confidence-guided refiner may shine at 4K/hair; not needed for
  the current 832x1248 canvas.
- **Grey-background invariance:** refilling the plate background with flat
  mid-grey and re-running BiRefNet changed the alpha by 0.48–0.93/255
  (0.5–0.9% of pixels moved >32). The matte does not depend on background
  colour. A grey screen therefore gives identical matting with zero visible
  fringe (grey spill is neutral where blue/green spill fringes).
- **Blue fringe** (~10% of edge-band pixels blue-dominant) is spill baked into
  the source RGB, not an alpha defect. Grey backgrounds remove it by
  construction; `scoped_despill()` on colour reconstruction stays for any
  legacy chroma plates.
- **Aperture:** matting models keep the flattened head blob as foreground
  (21,323/22,155 px) where CorridorKey keys it away. Fix is mechanical: post-
  multiply alpha by the stored aperture mask (`masks/clothes-blue-aperture.png`
  exists per outfit/pose). The aperture moves from "flatten-and-key-by-colour"
  to "stored mask applied to the alpha"; it must ship with the body asset.

Local tooling that produced this (all under `tools/cover-story/`):
`birefnet_ab.py`, `ben2_ab.py`, `birefnet_grey_test.py`, `ben2_metrics.py`,
`run_ab_serial.sh`; models in `models/` (`BiRefNet-general-tiny.onnx`,
`BEN2_Base.onnx`); outputs and `review.html` under
`/mnt/Misc/sd/cover-story/birefnet-ab-v1/`.

**OOM lesson for local runs:** this dev VM has 7.4 GB RAM. onnxruntime's
default CPU memory arena grows to 5 GB+ per process and systemd-oomd kills it.
Use `enable_cpu_mem_arena=False` (`make_session()` in `birefnet_ab.py`) and run
ONNX jobs one at a time. With that, inference is ~70–140 s per 832x1248 plate
at 1024² letterboxed.

## Objective

The clothed-body plate is currently a masked Qwen edit that redraws the whole
garment region from a prompt. That produces two independent geometries
(generated body, generated clothes) whose disagreement shows up at the
boundaries — the feet/shoe mismatch and the dark-cloth keying failures being
the historical examples. VITON warps a real garment image onto the person
image instead, so the body geometry is the person image's geometry and the
garment is a concrete asset rather than a text description.

## Architecture mapping (current → VITON)

| stage | today | with VITON |
|---|---|---|
| carrier | matte green paint on blue screen | plain grey bodysuit figure on flat grey background (same pose/silhouette contract) |
| `[skin]` | full-body recolor (vestigial for identity) | **becomes the VITON person image** — recolor to the outfit's tone group |
| garment | outfit *description* in the edit prompt | T2I-generated garment image per outfit (description still drives the T2I) |
| garment mask | clothes envelope (SAM, human-reviewed) | the same envelope — CatVTON accepts arbitrary masks |
| `[clothes]` | masked Qwen edit | VITON (CatVTON / IDM-VTON / OOTDiffusion) → dressed figure |
| `[extract]` | chroma key + aperture colour-flattening | BiRefNet matte + aperture post-mask |
| identity, hair, composite | unchanged | unchanged |

## Locked design decisions

1. **Person image = the tone-recolored carrier**, not the performer-specific
   skin plate. Body plates are performer-independent
   (`body:{theme}:{outfit}:{pose}:{tone}`, refs = carrier only) and reused
   across all 20 performers; a VITON stage conditioned on the performer would
   multiply generation to 1280 runs and break the reuse model. The tone-
   recolored carrier (the `[skin]` stage output, already produced today) keeps
   the plate a function of (outfit, pose, tone) only.
2. **Garment mask = the existing clothes envelope.** It is SAM-derived and
   human-reviewed; CatVTON's whole design point is arbitrary masks.
3. **Garment image = one T2I generation per outfit**, reused across performers
   and poses. A flat-lay or mannequin garment render is pose-agnostic; the
   try-on model performs the warp. Generate it on a neutral background.
4. **Extraction = BiRefNet matte + aperture post-mask.** No key colors, no
   spill handling, no green/blue channel-swap variants, no CorridorKey-on-pod.
   Matting runs locally (or trivially on the pod) and is background-colour-
   agnostic, which is what makes decision 5 safe.
5. **Grey background everywhere.** The carrier prompt becomes a plain
   grey-bodysuit figure on flat grey; all plates render on it. This kills the
   fringe and the key-colour machinery simultaneously. `deterministic_skin_
   recolor()` must switch its shading map from the green paint channel to
   luminance (the paint channel stops existing); verify parity locally before
   spending GPU.
6. **Tone variants = person-image variants.** Three recolors of the carrier
   (light/medium/dark) × one garment → three plates with an *identical*
   garment, instead of today's three independent Qwen draws. A fully covering
   outfit (e.g. space suit) stores one body image and maps all tones to it,
   per the existing catalog rule.
7. **Layer contract unchanged:** background → skin → clothed-body → hair, with
   the body plate owning hands, limbs, and footwear, and a transparent
   head/neck/upper-chest aperture.

## PoC plan (next pod session)

1. Pod setup: install the CatVTON ComfyUI node and weights (~2–3 GB, SD1.5
   backbone); existing Qwen/SAM/CorridorKey assets stay for fallback. Repoint
   `instance.json`, run `pod_bootstrap.sh`.
2. Generate one grey carrier (prompt edit: grey bodysuit on flat grey; keep
   pose, framing, feet-visible contract; record the prompt change per the
   one-targeted-change rule).
3. Generate one garment image per test outfit (start with the Victorian
   outfit-01 plum dress and the Viking tunic — one fantasy, one historical).
4. Run the VITON stage on the tone-recolored carrier with the existing clothes
   envelope; BiRefNet-matte; aperture post-mask.
5. Compare against the current recipe's output for the same outfit/pose/tone:
   closeups for neckline, hands, feet/shoes, hair, garment fidelity.
6. Gate on the checks below; keep the current recipe untouched until then.

## Acceptance criteria (promotion gate)

- Person image: VITON output keeps the carrier silhouette within ±2 px /
  ±0.5% on one pose across at least two performers' tone variants (the
  existing `silhouette_spread()` gate; garment bulk measured the same way as
  the current recipe allows).
- Exposed skin carries the tone group's colour and reads naturally; no
  bodysuit-grey or paint-colour remnants inside the figure.
- Feet/shoes: one geometry — no double-exposure, no speckle; shoes warp onto
  the carrier's feet (compare against the current recipe's known-bad case).
- Aperture transparent after post-mask; neckline edge clean (no more ragged
  collar alpha).
- Garment fidelity: the Victorian dress and the Viking tunic are recognisably
  the T2I garment (colour, silhouette, texture), including when the try-on
  warps to a pose change.
- No fringe on the grey plates; composite clean at 832x1248 and 600x900.
- SFW: the intermediate skin-tone figure is never shipped; the garment region
  of the composite is fully covered per the outfit's design.

## Risks and open questions

1. **VITON on a skin-tone (nude) person image** — try-on models train on
   clothed/swimwear person images. Content-wise the pipeline already works on a
   painted nude carrier, so this is a model-behaviour question, not a policy
   one, but it is the first thing to test. Fallback: dress the person image in
   a minimal neutral base layer before VITON if the model misbehaves.
2. **Costume fidelity through garment warp** — Viking/space designs may warp
   worse than a plain Qwen redraw; the T2I garment image becomes load-bearing.
   If warp is the failure, re-test with OOTDiffusion (full-outfit fusion) or a
   garment-reference masked Qwen edit (the other alternative already noted in
   review: masked edits can follow a garment reference because the latent
   outside the mask is preserved).
3. **Matting resolution ceiling** — the A/B used the tiny BiRefNet at 1024²
   letterboxed (682x1024 effective). The full `BiRefNet-general-epoch_244`
   model and/or higher input resolution is an untested quality ceiling; try on
   the feet region if the tiny model's edges are not clean enough.
4. **Garment image per outfit vs per pose** — flat-lay should be pose-agnostic,
   but confirm the warp on all four pose families before generating the full
   catalog.
5. **`deterministic_skin_recolor` luminance mapping** — the green-paint channel
   no longer exists on grey carriers; verify the luminance-based shading map
   reproduces the current local-mean normalisation result before GPU spend.

## Session results (2026-08-07, live pod) — record these before the next pod

Runs: `grey-garmentref-poc-20260807-1235` (grey natural carrier) and `chroma-garmentref-poc-20260807`
(chroma carrier + garment-ref + BiRefNet), both with review.html in-root on the NFS drive. The
chroma run is the strongest variant produced: all stages green, identity 0/0/0 px, clothes paint
remnant 3.6%, **extract with 0 interior holes on both plates** (CorridorKey baselines were
2693/10419 px).

Decisions from the pod session:

1. **The grey carrier concept is shelved.** The grey-suit prompt rendered patchily (natural-skin
   head, near-black blotches, suit darker than the bg) and the deterministic recolor amplified the
   suit's irregular shading into splotchy skin. The natural-carrier variant (swimsuit) completed
   but did not convince. Conclusion: carriers stay chroma (green paint on blue); the matting swap
   and the garment reference are the two upgrades worth keeping. `--grey-carrier` remains
   implemented but is not the path forward.
2. **Flat-lay garment references fail on full-body coverage.** The flat lay left 79% of the paint
   remnant in the lower body (rows 800-1200) — a 2D garment cannot imply sleeves or skirt length.
   The **worn-garment reference** (`--garment-worn`, dress on a model) dropped the remnant from
   18.7% to 3.6%. Always use the worn variant.
3. **BiRefNet matting works on chroma plates** (background-colour-invariant, measured again on the
   live plates) and is the cleanest extractor so far (0 interior holes). Keep `--extract-mode
   birefnet`; CorridorKey is no longer needed for extraction.
4. **The blue halo** came from the preprocessed performer's chroma-blue background fringing the
   transplanted head. Grey-path fix was a grey preprocess background (`GREY_PREPROCESS_PROMPT`);
   for the chroma path the halo is keyed/matted away as before.
5. **Grey-path screen threading**: `envelope_checks`, `silhouette_checks`, `preprocess_checks`,
   `compose_identity`, `identity_composite_checks` all had hardcoded blue foreground estimates;
   all now take `screen=`. This class of bug cost several GPU cycles — check any new grey/neutral
   path for it before running.
6. **Face tone**: `sample_face_tone()` (skin-hue median over the top third) returns the canonical
   tone (196,158,127) where the SAM-head-box mean returned (147,114,93).
8. **Pod env**: ComfyUI 0.30.x needs `comfy-kitchen==0.2.26` (0.2.10 silently disables fp8 loading
   — `layout_cls` None crash). `pod_bootstrap.sh` step 6b now pins it against requirements.txt.
9. **Aperture bug — the "dress top missing" failure (2026-08-07 evening).** The birefnet extract
   path used the identity head envelope as the clothes plate's transparent aperture. That mask is
   dilated 97px and reaches below the bust, so the post-multiply punched a hole through the dress
   bodice; the composite showed 15.7% of its garment region as bare skin (rows 200-500). The
   chroma path's color-derived `key_aperture()` (33K px vs ~100K px) is the correct aperture; the
   grey path needs the SAM head-stop mask, now saved by bootstrap_envelope (`head_stop`). Fixed
   and verified locally without a pod; `composite-buggy-aperture.png` kept as evidence.
10. **The evaluation gap this exposed**: the automatic checks verify mechanics (registration,
   bit-exactness, matting, paint residue) but not appearance, and the operator model cannot see
   images (deepseek-v4-flash; no vision model reachable — fetch_content blocks internal addresses
   and no Gemini key is configured). Compensation: `no_skin_in_garment_region` gate on the
   composite (skin visible where the garment should be, threshold 5% of the garment region —
   catches the buggy composite at 15.7%, passes the fixed one at 3.6%). More visual proxies to
   add: face-vs-body tone consistency (the "colors messed up" complaint) and edge fringe/halo
   detection. The human review loop stays the authority on aesthetics.

Runner flags added: `--clothes-mode garment-ref`, `--garment-worn`, `--extract-mode birefnet`,
`--auto-accept-envelope`, `--grey-carrier` (shelved). Wrapper `run_poc_bulk.sh` + review page
`poc_review.py`. Next pod: run the chroma + garment-ref + birefnet recipe on the Viking tunic and
compare the two composites from this session.

## Out of scope for this PoC

- Changing the shipped layer contract or the runtime browser stacking.
- Replacing the deterministic identity construction (head transplant + recolor)
  — it is the one proven part of the pipeline and stays as-is.
- The 64-outfit catalog generation; the PoC is one outfit × one pose × one
  tone family plus the comparison pair.
- Decommissioning CorridorKey/legacy outputs — keep the current recipe and
  v20d outputs intact as fallback and provenance (same rule as v19/v20d).

## Implemented state (2026-08-07, offline — all self-tests pass)

Runner: `run_qwen2512_skin_head_clothes_poc.py`

New flags:

| flag | meaning |
|---|---|
| `--grey-carrier` | flat-grey carrier (grey bodysuit on darker grey bg); requires `--clothes-mode viton` |
| `--clothes-mode qwen\|viton` | qwen = masked Qwen edit (current recipe, default); viton = CatVTON try-on |
| `--extract-mode corridorkey\|birefnet` | default: birefnet for grey/viton, else corridorkey |
| `--birefnet-model` / `--birefnet-python` | local matting model and venv interpreter (defaults to `models/` + `.venv-birefnet/`) |

New stages: `[garment]` (T2I flat-lay, viton mode only), `[clothes]` viton branch
(`run_viton`: person image = deterministic tone recolor of the carrier, local PIL, no GPU),
`[extract]` birefnet branch (local BiRefNet alpha + aperture post-mask, no pod/SSH/CorridorKey).

Grey plumbing: `production.screen_foreground(image, "grey")` (corner-distance),`deterministic_skin_recolor(paint_channel=None)` (luminance shading map; parity vs green-channel measured at
~12 levels mean on the real carrier), `scoped_despill(image, "grey")` no-op.

CatVTON choice: **chflame163/ComfyUI_CatVTON_Wrapper** — it pads to 768x1024 and restores the
output to input size/position (`restore_padding_image`), so carrier registration survives. The
plain pzc163 node crops instead and would need an inverted crop transform — do not swap them.
Node class `CatVTONWrapper`; inputs `image`, `mask` (MASK, via `ImageToMask` red channel),
`refer_image`, `mask_grow=0` (envelope already dilated), `fp16`, seed/steps/cfg.

Pod setup: `pod_bootstrap.sh` step 7 clones the wrapper into `custom_nodes/` and downloads its
weights under `models/CatVTON/`, gated behind the flag file `/workspace/runpod-slim/install-catvton`
which preflight touches in viton mode (so the chroma recipe never pays the download). Weights land
on the persistent volume. All four links verified live (2026-08-07):

| what | source | size |
|---|---|---:|
| wrapper node | `git clone https://github.com/chflame163/ComfyUI_CatVTON_Wrapper.git` | small |
| SD1.5 inpainting (diffusers) | `huggingface.co/stable-diffusion-v1-5/stable-diffusion-inpainting` — runwayml is a redirect stub whose files 404 | ~4 GB |
| VAE (explicitly loaded by the wrapper) | `huggingface.co/stabilityai/sd-vae-ft-mse` → `models/CatVTON/sd-vae-ft-mse/` | 319 MB |
| CatVTON attn ckpt (mix) | `huggingface.co/zhengchong/CatVTON` → `models/CatVTON/mix-48k-1024/attention/` (attn_ckpt_version="mix" maps to this subfolder; the repo has no plain attn_ckpt/ dir) | 189 MB |

~4.5 GB one-time, once, on the persistent volume. The pipeline needs only `scheduler/` and `unet/`
from the inpainting repo (CatVTON has no text conditioning — `encoder_hidden_states=None`), but
snapshot_download fetches the full repo; that is fine. Mask polarity matches ours (`mask >= 0.5`
is the region the garment is generated into).

### Pod run commands (once the pod is up)

```bash
python3 tools/cover-story/run_qwen2512_skin_head_clothes_poc.py --init-config
# fill in server / ssh_target / ssh_port (config beats env; placeholders count as unset)

# 1. preflight: repairs the pod (uv/rsync/torch gate), checks SAM3 + edit model, and -- in
#    viton mode only -- touches the catvton flag and verifies the node.
python3 tools/cover-story/run_qwen2512_skin_head_clothes_poc.py --stop-after preflight \
  --grey-carrier --clothes-mode garment-ref --extract-mode birefnet

# 2. BULK MODE (operator off): one command runs every stage unattended (envelope auto-accepted),
#    then renders review.html into the run root on the shared NFS drive. A partial page is still
#    produced if a stage's auto-checks fail.
tools/cover-story/run_poc_bulk.sh --output-dir /mnt/Misc/sd/cover-story/grey-garmentref-poc-v1
# → open <output-dir>/review.html: per-stage gallery + auto-check results, envelope banner,
#   alpha checkerboards, composite closeups, A/B vs the v3 chroma run, and a localStorage
#   accept/reject/notes panel (export copies the JSON for pasting back).

# 3. re-run a single stage after a fix (resumable by file existence):
python3 tools/cover-story/run_qwen2512_skin_head_clothes_poc.py --stop-after clothes --force \
  --grey-carrier --clothes-mode garment-ref --extract-mode birefnet --output-dir ...
```

Run stages individually (no `--auto-accept-envelope`, `--stop-after <stage>`) when the operator
is present; the envelope and identity gates then block as designed.

Clothes modes: `garment-ref` (Qwen 2511 masked edit conditioned on the T2I garment image — the
primary path; no SD1.5, native 832x1248) and `viton` (CatVTON try-on — the geometry probe, needs
the ~4.5 GB one-time download).

Open items to verify on the pod: VITON behavior on the skin-tone person image, garment fidelity
on the flat lay, and silhouette spread of the try-on output (the ±2px gate). The CatVTON weight
layout question is closed — the pipeline resolves `attn_ckpt_version="mix"` to
`models/CatVTON/mix-48k-1024/attention/` and loads the VAE from `models/CatVTON/sd-vae-ft-mse/`,
which the bootstrap now provisions exactly.
