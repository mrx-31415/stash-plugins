# Layered costume production pilot status

Updated: 2026-08-03

## v20d direct-alpha PoC — visual review pending

This deliberately small proof does not alter the v19 review gate below. Its
output root is `/mnt/Misc/sd/cover-story/layered-costume-production-v20d` and
its review page is `review.html` there.

- Accepted carrier: `carrier:center`, reviewed only for this technical proof.
- Generated, extracted, and composed: `identity:actor-154:center` and
  `body:victorian:outfit-01:center:medium`.
- The identity source contains face, hair, neck, clavicles and upper chest in
  one matte-green-screen image. The body has a wider shoulder silhouette and
  an open neckline.
- CorridorKey supplies the only final alpha. The runner applies it to the
  original source RGB, and partitions the head alpha into under-body skin and
  over-body hair. SAM selects only the layering split; it does not clip alpha.
- Before keying a body, the known head/neck/chest aperture is temporarily
  flattened to the chroma color. That makes its shaded green proxy
  unambiguous to CorridorKey; the original RGB is still used for the output.
  This removes the previous dark-green neck rim without assuming a fixed
  clothing silhouette.
- The proof still has some edge spill at hands, the outer skirt, and shoes.
  It is not accepted for production. Inspect the face/hair and neckline
  closeups in the review page before accepting this architecture.
- v20e through v20h tested a deliberately oversized chest underlap. A
  geometric edit envelope was correct, but the image model retained the green
  carrier suit inside it, so CorridorKey correctly returned only the original
  narrow exposed skin. The idea is rejected; v20d remains the active recipe.
- v20i produced a broad bare-chest preprocessing reference using opaque
  pasties, but the masked identity transfer collapsed it back to the narrow
  carrier scoop. v20j removed that mask and retained the broad chest, but
  radically reframed the head and body. Both are rejected; do not use the
  pasties prompt in the production identity path.
- v20k first tried an unmasked identity transfer with a carrier-sized
  reference. It still reframed the performer into a close chest portrait and
  changed identity, so it is rejected.
- A separate v20k torso-support probe is promising but is not production
  code: it starts with the aligned v20d identity image and permits an edit
  only in a wide face-excluding neck/shoulder/torso envelope. That preserves
  the head, hair, pose, arms, and lower body while removing the carrier top
  through the waist. The envelope must include the whole shoulder junction;
  a narrower version left untouched green suit there. CorridorKey supplies
  the final alpha from this source, and the accepted body/hair layers are
  composited above it. The v2 output has no green shoulder source under the
  dress neckline. Its fixed center-pose envelope and soft below-waist alpha
  are not ready for production; derive a pose-aware envelope and validate it
  on every pose before promotion.

## Dual-chroma carrier PoC

The next technical probe uses one generated carrier guide with a fully
chroma-green performer on a chroma-blue screen. Its exact counterpart is
derived deterministically by swapping green and blue channels, so the two
guides have identical pixels and geometry. CorridorKey is run once against
each opposite screen color. Only its alpha is retained; it is used as a
carrier/body edit envelope and never as foreground RGB.

This keeps skin and clothing as separate edits of the same base geometry.
It explicitly rejects the tempting but invalid shortcut of conditioning the
clothing edit on the already-generated skin plate.

The first center-pose run used a crude rounded-rectangle hint. It made the
green-screen reciprocal look broken, but that was a hint failure, not a
checkpoint failure. Rerunning the exact same reciprocal guides with a SAM
`person` hint produces clean, nearly identical full-body alphas from both
models (mean alpha difference 0.0902; foreground coverage 15.06% versus
15.09%). The SAM-derived hint is therefore mandatory; a geometric rectangle
must not be used. The dual-chroma guide is now a viable candidate for a
controlled body-envelope test, while the active production recipe remains
unchanged.

## Updated next POC plan — Qwen Image 2512 carriers

The newest remote carrier batch is `Qwen-Image-2512_00014_` through
`Qwen-Image-2512_00019_` under `/workspace/ComfyUI/output`. These are better
carrier candidates than the earlier v20d figure: they have a broad, natural
full-body silhouette rendered as green foreground on blue screen. The blue
screen has a gradient and soft floor shadow, which is acceptable for the
SAM-guided, blue-checkpoint keying path; it need not be flattened in the
source RGB.

The controlled POC sequence is now:

1. Select one center-pose Qwen Image 2512 carrier and keep its geometry as the
   sole base for both identity and clothing edits. Do not mix it with v20d
   geometry.
2. Normalize that carrier once to the canonical canvas, preserving its RGB.
3. Run SAM `person` on the actual carrier. Use its foreground mask as the
   hint; invert it only when a downstream node requests background.
4. Run CorridorKey with the correct screen-specific checkpoint, retaining
   alpha only. Use that alpha as a pose/support map, not as a clothing
   silhouette.
5. Build a generous body edit envelope from the support map minus the locked
   head/neck/chest region, then dilate it to permit garment bulk beyond the
   carrier contour. The envelope is an edit permission, never a final matte.
6. Generate identity/skin and clothing independently from this same carrier;
   never condition the clothing edit on the generated skin plate.
7. Run CorridorKey on each resulting source, apply only its alpha to the
   original source RGB, and composite background → skin → body → hair.
8. Accept only after registration, halo, neckline, shoulder, hand, and shoe
   closeups pass visual review. Keep v20d active until then.

### Qwen 2512 carrier/identity probe result

The first complete run used a regenerated 832×1248 Qwen Image 2512 carrier
with a bald, shapely green performer on blue. Its full-body identity reference
pulled the reference performer's proportions into the edit and was rejected.
The corrected identity pass uses the carrier's SAM person mask as the edit
permission and a head-only performer crop as the identity reference. It
produced a full-body, clothing-free skin plate that stays aligned to the
carrier's pose and silhouette. The revised proof is in
`/tmp/cover-story-v20k-qwen2512-identity-revised` (the NFS `/mnt/Misc` mount is
read-only from this host).

The existing clothing plate still leaves green sleeve/hand remnants and is not
accepted; regenerate the clothing edit with the same carrier mask before
calling this a complete composite. ComfyUI can release Qwen weights with its
`/free` endpoint (`unload_models=true`, `free_memory=true`) without restarting
the service or clearing output history; use that between Qwen and CorridorKey.

A two-stage identity variant is the better current proof: a small carrier-masked
head edit with the full performer reference preserves the face and long hair,
while a separate carrier-masked full-body skin edit supplies the aligned skin
underlay. The final split-head composite is
`/tmp/cover-story-v20k-qwen2512-final-split-head/composite-tight-neck-clamped.png`.
It is materially closer to the performer and keeps the carrier silhouette, but
still has a thin green edge around some hair/shoulder pixels. This is a
technical probe only; do not promote it or the clothing plate to v20d.

The two-stage body split is now considered unnecessary complexity. The next
identity probe should use one carrier-derived edit mask, deliberately loose
from the head through the neck, clavicles, shoulders and a small upper-chest
underlap. It must stop before the garment body region. The clothing edit is an
independent edit of the same carrier and covers that underlap, so identity does
not need to repaint the whole body or determine the clothing silhouette.

That probe is runnable as the isolated
`run_qwen2512_skin_head_clothes_poc.py` script. It generates a new blue-screen
carrier, uses SAM only to make the full-body skin mask and the dilated
head/shoulder identity mask, performs the full-body skin recolor before the
head identity edit, and generates clothing from the untouched carrier. Each
plate is keyed with the blue CorridorKey path; only the returned alpha is kept,
then applied to that plate's original RGB. The final order is background →
skin → hair → clothes, with the clothing plate covering the shoulder/hair
underlap. Its default output is the writable `/tmp` PoC root and it never
changes the v20d production output.

Pickup command after restarting the remote ComfyUI service:

```bash
python3 tools/cover-story/run_qwen2512_skin_head_clothes_poc.py \
  --server "$COMFY_SERVER" \
  --ssh-target "$COVER_STORY_SSH_TARGET" \
  --ssh-port "$COVER_STORY_SSH_PORT"
```

The runner is resumable, uses ComfyUI `/free` between large model phases, and
keeps CorridorKey's remote cache under the separate
`qwen2512-skin-head-clothes-poc` namespace. Validate wiring without a server
with `--self-test`. This remains an experimental replacement candidate only;
v20d is still the production reference until the composite passes visual review.

The carrier prompt constants were derived from the positive and negative prompt
embedded in the reviewed `carrier-raw_00006_.png` screenshot artifact. Use
`--force` on the next run if an older `carrier.png` is already present, so the
new carrier is actually regenerated.

They no longer match that artifact verbatim: on 2026-08-03 the figure sentence
was changed to "She has a slender, feminine, statuesque hourglass figure, and
ample cleavage", dropping "curvy" and "with balanced hips and shoulders". This
is a single deliberate change, per the handover's rule of one targeted prompt
edit with all other settings fixed. The rest of the carrier prompt is
unchanged, including the anatomical enumeration and "feet visible" — both
depart from the handover's style guidance, but they are what the reviewed
artifact used, and every downstream layer registers to the geometry this prompt
produces.

The next extraction revision replaces the SAM `person` result as the
CorridorKey hint with a Pillow-derived blue-background hint: classify
blue-dominant pixels, clean/soften that background estimate, and invert it to
foreground. SAM still supplies the full-body/head edit masks. This addresses
small internal holes in the SAM person hint without turning SAM into a hard
final matte; CorridorKey alpha remains authoritative.

The RunPod direct SSH endpoint works for non-interactive rsync when owner/group
metadata preservation is disabled; the runner now uses `--no-owner --no-group`
for both staging and result retrieval.

The superseded immutable probes remain available:
`layered-costume-production-v20` lacked usable facial landmarks,
`layered-costume-production-v20b` had a crew neck, and v20c retained the
dark neck-proxy rim.

### Mask-free recolor + reviewed envelope gate (implemented, not yet run)

`run_qwen2512_skin_head_clothes_poc.py` was revised to address a specific
architectural concern: SAM3 masks were driving `ImageCompositeMasked`'s hard
paste boundary in the Qwen edit graph, so any imprecision in a SAM edge became
a visible seam in the output RGB, not just a soft hint. This is the same
category of failure behind the v20e-v20k neck-rim/spill rejections above.

Two changes, both self-tested (`--self-test` passes) but not yet exercised
against a live server:

- The full-body skin recolor is now a mask-free, full-image, prompt-only edit
  (`edit_graph(..., mask=None)`), mirroring the already-accepted technique in
  `run_green_carrier_poc.py`: Qwen's own semantic understanding draws the
  color boundary instead of a pasted SAM mask. `edit_graph` already supported
  `mask=None` natively; a real bug in `edit()` (`pick(result, "-masked")`
  called unconditionally, even with no mask node) was fixed as part of this.
- Identity/clothing edits still need an edit-permission envelope (an earlier
  unmasked identity attempt in v20k reframed the whole figure, so mask-free
  isn't viable there). That envelope is now a **frozen, human-reviewed
  asset**: `bootstrap_envelope()` runs SAM once per carrier and writes
  `masks/envelope-status.json` as `pending_review` plus
  `masks/envelope-review.png` (head/clothes regions tinted over the carrier).
  `load_accepted_envelope()` refuses to hand the mask to any edit call until
  a human flips that status to `accepted`. `main()` now generates the
  carrier, hints, and skin plate freely, then hard-stops before the
  identity/clothing edits if the envelope hasn't been reviewed.
- The clothes CorridorKey extraction previously used the SAM-derived
  `clothes_mask` as its hint (missed by the "next extraction revision" note
  above, which only fixed identity). It now uses the same Pillow
  blue-dominance hint as identity, so no SAM output feeds CorridorKey
  anywhere in this script.

### Review corrections applied on 2026-08-03

Review of the above revision found two defects that would have made the first
live run fail regardless of prompt quality. Both are fixed and covered by
`--self-test`; still not run against a server.

- **The hint's source, not its radius, was wrong.** The earlier note framed the
  risk as a 9px versus 97px dilation. That understated it: `build_hints()`
  derived one hint from `carrier.png` and fed it to CorridorKey for both
  generated plates. No dilation of a *bald, unclothed* carrier silhouette
  reaches a full skirt hem, and the same gap silently applied to the identity
  plate, whose hair falls outside the bald carrier outline. Production never
  did this — `extract()` runs its detector on the plate being keyed. The PoC
  now does the same via `plate_hints()`, and the 9px dilation is retained.
  `build_hints()` survives only as the drift reference and provenance.
- **The two edit envelopes were butted together, not overlapped.** Dilating
  both the `person` and the broad head/shoulder/upper-chest region by 97 and
  subtracting produced exactly 0px of overlap, with the clothes envelope
  starting 209px below the top of the body — so `CLOTHES_PROMPT` asked for
  "aligned shoulders, clean high collar" in a region the mask forbade painting.
  That is the v20k shoulder-junction failure recorded above, and it violates
  "do not butt two masks together" in the handover. The clothes envelope now
  subtracts `head_stop_region()`: the narrow `head, face, ears and neck` SAM
  result dilated generously to protect the skull halo, then clipped at the neck
  base so the garment keeps its shoulders. The cutoff row comes from the SAM
  bbox, so it stays pose-aware rather than a fixed center-pose constant.

Three supporting changes:

- `envelope-review.png` was unreadable. `Image.paste()` with an explicit mask
  ignores the source alpha, so both tinted regions rendered as flat opaque
  blocks and the reviewer could not see the carrier the envelope is drawn
  against. It now composites a scaled-alpha tint.
- `load_accepted_envelope()` verifies the recorded `source_sha256` against the
  current carrier and the recorded mask hashes before releasing the masks. An
  acceptance previously survived any later carrier change.
- A `drift_check()` runs after the mask-free skin recolor: silhouette bbox
  shift plus mean absolute difference over the carrier's blue region, with
  generous limits (16px, 24 levels) chosen to catch a reframe or a redrawn
  background rather than VAE round-trip noise. At denoise 1.0 with no latent
  mask nothing else constrains registration, so `SKIN_PROMPT` was cut back to
  the two-clause shape validated in `run_green_carrier_poc.py`. Its previous
  "do not add hair, bra, pasties…" list was inert: `edit_graph()` hardcodes an
  empty negative conditioning, so those tokens only ever entered as positive
  ones — the same mistake recorded for v20i above.

`MaxFilter(97)` on an 832×1248 mask cost ~25s per call. `dilate()` iterates
`MaxFilter(3)`, which is bit-identical for square structuring elements and runs
in ~3s. No new dependency: the repo has no dependency manifest and every
`tools/cover-story/` script is Pillow plus stdlib.

### Prompt revision, 2026-08-03

`edit_graph` hardcodes its negative conditioning to the empty string, so in the
three edit prompts every "No X" reached the model as a *positive* token. Only
`CARRIER_PROMPT` has a working negative, via `generation_graph`. The edit
prompts were rewritten to the shape validated in `run_green_carrier_poc.py` —
two to four short sentences, no enumerations, no negations.

`IDENTITY_PROMPT` lost two enumerations, the inert "Do not copy image 2's body"
negation, and a sentence that explained pipeline architecture to the model
("so the clothing layer can cover it"); the hair instruction is now phrased as
image content the model can act on.

`CLOTHES_PROMPT` had two substantive errors, not just verbosity. It instructed
the model to preserve image 1's green **hair** — the carrier is bald, which
both `CARRIER_PROMPT` and the `carrier_is_bald` check assert — inviting it to
invent some. And it forbade a residual "green suit", vocabulary belonging to
the v20d carrier rather than this nude green-painted one. Both are gone. The
outfit enumeration stays: that is the free-form design description the handover
asks for, not a constraint checklist.

The performer reference remains the full-body 832×1248 preprocess image.
STATUS.md is ambiguous here — the full-body reference was rejected once for
pulling the performer's proportions into the edit, but the two-stage variant
that preserved face and long hair also used a full reference with a small
carrier-masked head edit, which is what this runner does. The `identity` stage
gate will show body-bleed if it occurs; the fix would be a head crop, not
prompt wording, since the negation that used to guard this was inert.

### First end-to-end run, 2026-08-03 — composite produced, not accepted

Output root: `/mnt/Misc/sd/cover-story/cover-story-qwen2512-skin-head-clothes-poc-v2`.
All nine stages ran. Every automatic check passes. The composite is still
rejected: three defects below are invisible to the checks as written.

**Confirmed working.** The two fixes this revision existed for both hold on
real data. `outside_mask_unchanged` is exactly 0 for the identity and clothing
edits, so `ImageCompositeMasked` preserves the plate bit-for-bit and
`FluxKontextImageScale` is a no-op at 832×1248. The mask-free skin recolor
holds registration at `[3, 1, 4, 7]` px with a background difference of 2.79 —
at denoise 1.0 with nothing but the prompt constraining it. And the hint fix
is proven: `identity_extends_past_carrier` 14874, `clothes_extends_past_carrier`
67963, i.e. hair beyond the bald silhouette and skirt bulk beyond the body both
survive keying, which a carrier-derived hint would have clipped.

**Defect 1 — CorridorKey erodes dark garment pixels.** The skirt drape has torn,
scalloped edges and speckled holes. This is not the hint, which classifies 0% of
the dress as background; blue-dominance is negative across the whole garment.
Measured on dress pixels the hint calls foreground:

| dress brightness (max channel) | mean alpha | dropped |
|---|---|---|
| 32–63 | 232.9 | **8.5%** |
| 64–95 | 254.5 | 0.2% |
| 96+ | 255.0 | 0.0% |

CorridorKeyBlue cannot resolve dark, low-saturation cloth against blue. This is
what the reference document's per-outfit key-colour rule is for, and the PoC
ignores it by hardcoding the blue path. A plum dress should key green. Fixing
this requires the green carrier variant and the green checkpoint.

**Defect 2 — RETRACTED on 2026-08-04; the hair/skin split is fine.** The claim
was that SAM `hair` covered only the crown, on the evidence that the hair plate
is 5.1% of the head layers. That ratio is meaningless: the skin plate contains
the entire body down to y=1189, so hair will always be a few percent of it. The
bbox (294, 82, 455, 277) is genuinely the hair's extent — this performer's hair
stops above the shoulder line.

Measured against a darkness ground truth inside the head envelope (max channel
< 90 within CorridorKey's alpha), SAM's mask captures 13529 of 15211 dark pixels
at 97.3% precision. Of the 1682 it misses, the row distribution splits cleanly:
~940 px at y=100–299 are real hair wisps at the boundary, and ~737 px at
y=350–499 are cleavage and under-chin shadow that the mask is *right* to
exclude. Real recall on hair is therefore ~93.5%, and the residual is edge
wisps, not structure. SAM keeps this job.

**Defect 3 — bare skin shows at the waist.** A consequence of defect 1: where
the clothing alpha is eroded, the nude skin plate below shows through, as
narrow skin slivers between bodice and sleeves. The composite must be SFW even
though the intermediate skin plate is not, so any clothing-alpha erosion is a
correctness problem, not only a cosmetic one.

**Defect 4 — the PoC composited hair *under* the clothing.** It stacked
background, skin, hair, clothing; `production.compose()` stacks background,
skin, clothing-body, hair, and records that order in the manifest. Fixed
2026-08-04. It changed 0 px on this run because the hair stops above the
neckline, which is why an end-to-end review missed it — it only shows on hair
long enough to fall over a shoulder.

Also noted: `CLOTHES_PROMPT` asks for a high collar and the model produced a
wide boat neckline, exposing more aperture than intended. The skin plate backs
it correctly, so this is cosmetic.

### Identity transfer, 2026-08-04 — measured, not solved

The bar is that a composite must match the already-shipped portrait
(`plugins/cover-story/assets/performers/actor-NNN.avif`). `actor-266` is Laura
Everly: pale blue-gray eyes, fair skin, narrow face. The accepted plate is a
broad-faced, tanned, dark-eyed woman — a different person.

`identity_metrics.py` scores a candidate head against that portrait; see its
docstring for the calibration and why skin tone is reported but not scored.
Runner is `run_identity_ab.py`; outputs in
`.../cover-story-qwen2512-identity-ab` and `.../cover-story-qwen2512-mannequin-probe`.

**Establish the noise floor before believing any ranking.** One construction
across four seeds spans ~8.7 points of score and 18 points of iris warmth —
larger than the gap between most constructions. Six single-sample attempts were
ranked before this was measured, and that ranking did not survive it. Only rows
with n=4 below are trustworthy; `altmodel` is the one single sample far enough
out (36.82) to survive the caveat, and the alternate edit model is genuinely
worse.

| construction | n | score mean | iris mean | face mean | kept carrier's eyes |
|---|---|---|---|---|---|
| headcrop | 4 | 20.75 | -20.5 | 29.07 | 2/4 |
| blank-head carrier | 4 | 22.50 | -16.8 | ~37 | 1/4 |
| soft-face carrier | 4 | 22.58 | -15.5 | 37.00 | 0/4 |
| baseline | 4 | 23.01 | -26.8 | 32.94 | 3/4 |

Reference is score 0, iris b-r +6. The carrier reads about -29.

**What works.** A head-only performer crop as image 2 beats the full body on all
three signals — the reference document's own probe log said so and the PoC was
passing the whole body. Softening the carrier's face removes the dominant
failure mode: the carrier's eyes reasserting themselves, 3/4 at baseline down to
0-1/4. Both effects are real; neither is sufficient.

**What does not work.** Two-stage (33.71 against baseline 34.56, i.e. nothing),
reference alignment, and the alternate edit model. Making the carrier head
*blanker* than "softened" adds nothing — the benefit saturates.

**Superseded by the inverted transfer below.** Everything in this table paints
the performer's face *into* the carrier and competes with the face already
there. None of it reliably produces the right person.

### Inverted transfer, 2026-08-04 — identity solved

`run_phase3_probe.py`. The performer is image 1, the mask covers her *body*, and
her head sits **outside** it, where `ImageCompositeMasked` is bit-exact. Identity
cannot drift because nothing repaints it; the body is repainted into the
carrier's silhouette instead.

| construction | n | score mean | range | iris mean | heads bit-exact |
|---|---|---|---|---|---|
| **inverted transfer** | 2 | **12.71** | 12.71-12.71 | **-6.0** | **2/2** |
| *(ceiling: preprocessed)* | - | *12.33* | - | - | - |
| headless carrier | 4 | 17.46 | 15.9-18.5 | -11.0 | - |
| head-crop reference | 4 | 20.75 | 17.4-26.1 | -20.5 | - |
| baseline | 4 | 23.01 | 19.8-25.3 | -26.8 | - |

Within 0.38 of the ceiling, so repainting the body costs almost nothing in
identity, and `outside_mask_unchanged` is 0 on every seed. The spread is the
point: 0.00 here against 8.7 for the best generative construction. Copying the
head instead of generating it removes the lottery, which no amount of prompt or
reference tuning did.

**Alignment is the hard part, and cost two runs.** Matching bounding boxes
between two images silently compares different objects. First the performer's
head box was matched against `masks/identity-head-mask.png`, which is not a head
but the identity *envelope* dilated 97 px to cover shoulders and upper chest —
350x436 against a real head of ~150x190, scaling her up 2.3x. Then head-to-head:
the carrier is *bald*, so its head box is a bare skull (147 px) while hers
includes hair past the shoulders (210 px), shrinking her to 0.700 and putting her
feet off the frame. Faces are the same object in both images and give 1.070.
The guard now checks the *outcome* — silhouette heights must match within 12%
after alignment — because the first guard tested the input scale ratio, which is
the quantity that was wrong, and passed 0.700 happily.

**Mask feathering is not cosmetic.** The repaint mask must be asymmetric: the
outer boundary dilated 25 px into flat background and feathered 8 px, the inner
boundary against the preserved head left hard. A hard outer edge leaves a pale
contour tracing the figure, because the regenerated background is not quite the
same blue as the original. That artifact also *inflated the drift measurement* —
`silhouette_box` counted the contour as figure, reporting 65 px of pose drift
where the feathered version reports 14 px. Feathering the inner boundary instead
would blend generated pixels into the head and lose the bit-exactness the whole
approach rests on.

**Open: pose registration.** Worst silhouette drift is 14 px against the
reference document's 2 px limit, concentrated at the feet — the residue of a 7%
alignment error. This is the cost the plan predicted for abandoning the frozen
carrier. See the control-image entry below for what that residue actually is and
why a ControlNet does not remove it.

**Prompt trap worth remembering.** "Mannequin head" in a carrier prompt
propagates to the whole figure — the first attempt produced a shop dummy with
joint seams and no navel, unusable because that body is also what the clothing
plate drapes onto. The word belongs in the *negative* prompt with `shop dummy,
doll, plastic, joint seams, ball joints, segmented limbs, glossy skin`, while
the face is described positively by what it lacks. `featureless body` must stay
out of that negative: it argues against the blank face. Both lists are needed at
once — supplying either alone loses what the other fixed.

### Control images on the repaint, 2026-08-04 — negative result

Output root: `.../cover-story-qwen2512-phase3-probe`, runs `phase3-canny-*` and
`phase3-openpose-*`, same two seeds as the uncontrolled run so the control image
is the only variable.

**The ControlNet works, but only above strength ~1.** At strength 1.0 it has no
authority; at 3.0 it tracks the control image to within a pixel.

| run | control demands | feet land at | delta vs uncontrolled |
|---|---|---|---|
| uncontrolled, **different seed** | — | 1194 | **0.92** |
| canny, strength 1.0 | y=1183 | 1195 | 0.43 |
| openpose, strength 1.0 | — | 1195 | 0.36 |
| canny, strength 1.0, control squashed to 0.8 | **y=1001** | 1195 | 0.44 |
| canny, strength 3.0, control squashed to 0.8 | **y=1001** | **1002** | **7.01** |

**Read the first three rows alone and the honest-looking conclusion is "the
ControlNet is inert" — and that conclusion was written here before the fourth
and fifth rows existed. It was wrong.** Those controls described roughly what
the model was going to draw anyway, so a working ControlNet and a dead one
produce the same near-zero delta. A control that *disagrees* is the only
measurement that separates them: squashing it to 0.8 asks for feet 194 px above
where the model lands unaided. Strength 1.0 ignored that; strength 3.0 obeyed it
to 1 px.

The general lesson is worth more than the result: **a control experiment whose
control agrees with the expected output cannot fail, and therefore cannot tell
you anything.** Distort the control and confirm the model follows before
concluding anything from a null result. The civitai notes for this ControlNet
quote strength 1.0–1.5 against Qwen Image Edit; on this pairing 1.0 is simply
below the threshold where it bites.

**What the 14 px actually is, which matters more than the null result.**
Measuring the inputs rather than only the outputs:

```
carrier                     (217,  87, 518, 1181)   height 1094
performer-aligned (image 1) (210,  76, 517, 1247)   height 1171   feet clipped at the frame edge
output                      (222,  76, 516, 1195)
```

Aligning her *face* to the carrier's face leaves her 77 px taller than the
carrier, with her feet 66 px lower. The sampler already reconciles 52 of those
66 px; the 14 px is what is left of a conflict present in the input. It is
structural: her height-to-face ratio is not the carrier's, so a scale and a
translation cannot satisfy "head lands on the carrier's head" and "feet land on
the carrier's feet" at once, and the repaint mask genuinely extends to 1247
because it is the union of both silhouettes.

A control image at sufficient strength overrides that geometry rather than
inheriting it, and doing so fixes the registration outright:

| run | left | right | bottom | identity score |
|---|---|---|---|---|
| no control | 5 | 2 | **14** | 12.71 |
| canny, strength 3.0 | 1 | 2–3 | **1–2** | 12.66 |
| openpose, strength 3.0 | 1 | 2–3 | **2–7** | 12.51 |

*(ceiling 12.33; `outside_mask_unchanged: 0` and heads bit-exact on every run)*

**Registration is inside the reference document's ±2 px with canny at strength
3.0**, down from 14 px, at no cost to identity and none to anatomy — thighs,
knees and feet were compared side by side against the uncontrolled run and are
equivalent. The feared failure, a taller woman squashed into a shorter carrier's
outline, did not happen.

Canny holds the silhouette tighter than openpose (worst 2 px against 7), which
is expected: it states the outline where a skeleton states only joints. Openpose
scores marginally better on identity, but its range (12.36–12.66) overlaps
canny's and n=2, so that ordering is **not** established — do not repeat the
earlier mistake of ranking constructions off single samples.

The upstream alternative remains available and may still be cleaner: generate
`preprocessed.png` under the carrier's pose so her proportions are the carrier's
from the start, at which point face-aligned implies feet-aligned and the repaint
has nothing to reconcile. It is no longer *needed* for registration.

**The drift metric was reporting a floor it could never reach.** `top` is not
drift — the carrier is bald and her hair falls past her shoulders, so their
silhouettes legitimately begin at different heights, and including it made a run
whose real worst error was 2 px report 11. The probe now reports body drift
(left/right/bottom) with the head offset alongside it.

**Still open, and unrelated to control:** hands render soft, with fingers barely
separated against the carrier's crisp ones, in *every* inverted-transfer run
including uncontrolled. A faint pale band also survives beside the figure in all
of them. Both predate the control work and neither has been investigated.

**Also fixed here:** `edit_graph`'s `control_type` was passing `"canny"`, which
`SetUnionControlNetType` rejects — its options are strings like
`"canny/lineart/anime_lineart/mlsd"`. `production.CONTROL_TYPES` now maps short
names to the node's literal options. The graph would have failed validation on
first use.

**SDPose is on the pod.** `sdpose_wholebody_fp16.safetensors` (1.9 GB,
Comfy-Org/SDPose) in `models/checkpoints/`, driving `SDPoseKeypointExtractor` +
`SDPoseDrawKeypoints` via `production.pose_graph()`. It is a checkpoint, not a
diffusion model, because the extractor needs a MODEL and a VAE and reads a
`heatmap_head` that only that file carries. Pose extraction on the carrier works
and is fast; it is the ControlNet downstream of it that does nothing.

**Canny on a chroma-key plate finds nothing.** ComfyUI's `Canny` node returned
795 lit pixels for a whole standing figure: matte green paint on a matte blue
screen is nearly isoluminant (luma 95 against 71), so a luminance gradient
detector has almost no edge to find. The probe derives the outline from
`screen_foreground()` instead, which separates them on colour and whose boundary
*is* the silhouette — 18,553 px, and the only geometry a uniformly painted body
has to offer anyway.

### GPU memory: what `/free` actually does, 2026-08-04

`soft_free()` was sending `{"unload_models": true, "free_memory": true}`. Those
are not two intensities of one thing (ComfyUI `main.py`, the `q.get_flags()`
block):

- `unload_models` → `unload_all_models()` → `detach()` →
  `unpatch_model(offload_device)`. Weights move to **CPU RAM**; VRAM is freed
  and the RAM copy survives.
- `free_memory` → `e.reset()`, which wipes the execution cache. That drops the
  last reference to the ModelPatcher, so the RAM copy is collected too and the
  next run re-reads the model from disk — 19 GiB for the edit model.

Sending both is what made alternating the edit model with SAM slow. The pod has
186 GB of RAM against roughly 36 GB of weights, so `free_memory` now defaults
off (`soft_free(server, drop_from_ram=True)` restores the old behaviour).

The other half is a launch flag, not code: the default `HierarchicalCache` calls
`clean_unused()` after every prompt and evicts node outputs absent from the
*current* prompt, so a SAM graph still evicts the edit model's loader even with
`free_memory` off. `--cache-lru 20` keeps them both; `pod_bootstrap.sh` step 6
restarts ComfyUI with it when missing, and `CUSTOM_ARGS="--cache-lru 20"` on the
RunPod template is the tidier equivalent (`/start.sh` appends it).

**Only one `soft_free` is structurally necessary**, and it is the one before
`[extract]`: CorridorKey is a separate process on the same GPU and ComfyUI
cannot know it needs the VRAM back. The calls around the envelope stage were
removed — `load_models_gpu` evicts its own LRU model when it needs room, and
with weights kept in RAM that eviction is a PCIe copy rather than a re-read.

### Green-key probe, 2026-08-04 — the catalog was right

Output root: `.../cover-story-qwen2512-skin-head-clothes-poc-v2/green-key-probe`.

`layered-costume-catalog.json` already assigns the plum Victorian walking dress
(`victorian` / `outfit-01`) `"key_color": "green"`. The PoC hardcoded blue for
every stage and so contradicted its own catalog. The probe re-ran only the
clothing plate against a green key to test that.

The green carrier is the blue carrier's G/B channels swapped —
`production.carrier_variant()`'s own `swap-green-blue-channels-v1` processor,
which is its own inverse — so blue-screen/green-body becomes green-screen/blue-body
with bit-identical geometry, and the existing envelope mask applies unchanged.

**Result: green wins, and the mechanism is narrower than "erodes dark garment".**
Both keyers hold the garment *interior* perfectly — mean alpha 254.9 on
plum-coloured pixels, ~0% dropped, on both. The failure is confined to boundary
and blend pixels, where a plum/blue mix stays blue-dominant and reads as screen,
while a plum/green mix does not because plum has almost no green channel.
Classifying every transparent pixel by what it is in the plate:

| cut as transparent | blue | green |
|---|---|---|
| legitimate screen | 98.7% | 99.5% |
| plum garment | 371 | 76 |
| dark/shadowed | 675 | 335 |

(step-3 sampling; ×9 for pixels — roughly 9400 px of garment lost on blue against
3700 on green.) The lost region is the shadowed crease between arm and torso,
which is where the composite showed bare skin.

**SFW impact.** Skin-coloured pixels visible below the neckline in the composite:
**1451 on blue, 72 on green.**

**Caveat: this is not a pure A/B.** The green plate is a separate generation, so
the dress itself differs — the green run produced a smoother sheath without the
pointed overskirt swags and without the arm gaps. The alpha metrics above are
generation-independent and support the key-colour argument on their own, but the
dramatic visual improvement is partly a different, easier-to-key garment.

**Action taken.** `CLOTHES_KEY_COLOR = "green"` now drives the clothing plate:
the prompt's two colour words, the carrier variant, the aperture, CorridorKey's
screen and `segment_source`. Skin and identity stay on blue — `SKIN_PROMPT` has
to distinguish "the green person" from "the blue background", which a
single-colour carrier cannot express, so the PoC keeps its two-colour design
rather than adopting production's uniform one.

**The variant is derived, not generated.** `carrier_for_screen()` swaps G/B when
the carrier's screen does not already match, reusing
`production.carrier_variant()`'s `swap-green-blue-channels-v1`. The reason is
correctness, not cost: skin and identity come from the blue carrier and the
composite stacks all three layers by exact pixel coordinates, so a separately
generated green carrier would be a different pose and nothing would line up.
The swap is its own inverse, which is what lets one generation serve both keys.

Two new checks close the gap this created. `clothes_plate_screen` compares the
plate's own `screen_color()` against the configured key — stages resume by file
existence, so without it a plate left over from a run at the other key colour
would compose silently wrong. And `self_test()` asserts the swap moves the
screen and paint but not a single pixel of geometry, that swapping twice is the
identity, and that the aperture follows the screen (passing the wrong screen
returns the whole canvas instead of the figure, so the assertion has teeth).

Note for anyone resuming the existing v2 run: its `clothes.png` is a blue plate
and now fails `clothes_plate_screen` by design. Regenerate that stage with
`--force`.

#### Two checks that were secretly blue-only

Moving one layer to a green key exposed two places where "blue" was baked in as
if it were "the screen". Both blocked the run, which is the good outcome — but
both had been passing on wrong reasoning before, and one was corrupting a real
input rather than only a report.

**`no_green_remnant_in_edit`** measured green inside the clothing envelope,
correct while green meant *body paint*. On the green key green is the *screen*
showing through around the garment, so it failed a good plate at 0.2628 while
the actual paint remnant was 0.0078. Now `no_paint_remnant_in_edit`, reading
`APERTURE_COLOR[screen]`. Measured both ways round:

| plate | green | blue |
|---|---|---|
| blue key (paint green) | **0.0038** paint | 0.1999 screen |
| green key (paint blue) | 0.2628 screen | **0.0078** paint |

**`blue_screen_foreground()`**, now `screen_foreground(image, screen)`, was the
worse of the two. Its foreground is `invert(blue-dominant)`, so on a green plate
the green background is not blue-dominant and counts as *figure*. That inflated
`clothes_no_interior_holes` to 778670 and `clothes_extends_past_carrier` to
165692 — but it also fed `plate_hints()`, so CorridorKey received a hint
covering the whole canvas. A reporting bug and an input bug in one function.
With the screen threaded through: holes 778670 → **1269**, beyond-carrier
165692 → **50850**.

Worth recording: the green-key probe ran with that broken whole-canvas hint and
green still beat blue. The advantage measured above is therefore understated,
not inflated.

#### Result

All nine stages pass on the green key. Skin visible below the neckline across
the three composites — the SFW measure that matters:

| composite | skin px |
|---|---|
| blue key | 1451 |
| green key, broken hint | 72 |
| green key, corrected hint | 79 |

The corrected hint does not change the picture (79 vs 72 is noise); what it
changes is that every check now measures what it claims to. `clothes.png`,
`clothes-rgba.png` and `composite.png` are the green-key artifacts; the blue
attempt is preserved as `*-attempt1-rejected-bluekey.png`.

Still open, unchanged by this work: identity fidelity (two-stage pass,
STATUS.md's earlier note), and `CLOTHES_PROMPT`'s high collar still rendering as
a boat neckline.

**Revised severity, 2026-08-04.** Inspecting `clothes.png` against
`clothes-rgba.png`'s alpha directly: the *generated plate is clean* — a fully
draped dress with no exposed skin and no torn hem. Every defect in the composite
comes from the alpha. And it is losing regions, not edges: wedges between each
arm and the bodice where sleeve meets shadowed torso, and claw-shaped bites
through the lower drape folds. The 8.5% figure understated this because it was
measured only within one brightness band on pixels the hint already called
foreground. Defects 1 and 3 are one defect — CorridorKeyBlue cannot separate
dark, low-saturation plum from blue — and the outfit's key colour is the fix,
not the hint and not the prompt.

**Checks that passed while all of the above was true.** Worth recording, because
the pattern recurs: `clothes_no_interior_holes` measures interior holes and the
erosion is at the boundary; `clothes_coverage_plausible` is a wide band; nothing
examines the hair/skin split at all. Two further checks were found satisfiable
by the very failure they should catch — `green_body_paint_removed` passed when
the green figure had vanished entirely, and `preprocess_matches_carrier_scale`
scored 1.017 on an image that *was* the carrier. The second was replaced by
`preprocess_is_not_a_copy_of_reference`; the first remains, guarded by
`registration`.

### Reference-collapse: what this edit model will and will not do

Seven identity/preprocess generations established a hard constraint, and it is
the most transferable result of the session.

**A mask-free edit at denoise 1.0 does not blend two references — it returns
one, and the prompt decides which.** `VAEEncode(image1)` feeds a latent that is
then fully replaced by noise, so image 1 has no structural advantage; both
images are only conditioning. Emphasising image 1 kept the performer and
ignored image 2's pose completely; emphasising image 2 returned image 2
verbatim (whole-image difference 4.08–15.58 against ~32 for a genuine
transfer). This held with image 2 as the skin plate *and* as the green carrier,
and with the pronoun ambiguity in "keep her face" removed. The wiring is
byte-identical to the validated `run_green_carrier_poc`, so this is model
behaviour, not a graph defect.

**A masked edit is the opposite**, because `SetLatentNoiseMask` preserves the
latent outside the mask. That is why the identity and clothing edits behave.

**The construction that works** is single-reference with the target framing
*described* rather than referenced: "Zoom out to show her whole body standing,
and remove her clothing so her body is bare." That reached scale 0.99 against
the carrier with identity intact, which no two-reference phrasing achieved.
Prompts should be imperative "change X to Y" clauses acting on image 1, with no
pronouns that could bind across images.

**Identity transfer remains open.** With the reference at scale 0.99, correctly
framed and bare through the envelope, the transferred face still keeps the
carrier's dark eyes, fuller lips and broader nose rather than the performer's.
Alignment helped measurably from 2.5× to ~1.0 and then stopped helping: 0.90 and
0.99 produce indistinguishable results. The bottleneck is that regenerating
inside the mask at denoise 1.0 lets image 1's spatial prior and the model's own
face prior outweigh the reference. The next thing to try is the two-stage pass
in the section above — feed `identity.png` back as image 1 so the carrier's
prior is already weakened. This matters for the product, not just fidelity: the
plugin ships `personas.js` portraits, and a cover whose face does not match its
persona image is a visible inconsistency.

Every rejected attempt is preserved in the output root under its own name
(`preprocessed-attempt1..6`, `skin-tone-attempt1..2`, `identity-attempt1..2`).

### Environment gaps found the expensive way

`rsync` was absent from the pod, and the CorridorKey venv's interpreter was a
dangling symlink into `/root/.local/share/uv`, which does not survive pod
recreation while `/workspace` does. Both surfaced only at the extraction stage,
after every generation had run. `preflight` now checks the CorridorKey install
and remote `rsync`; it already checks `SAM3_Detect` and that the configured edit
model is present.

### Surviving a pod migration, 2026-08-04

Migrating to a second pod reproduced the dangling interpreter exactly, which
confirms it is structural rather than a one-off: `/` and `/root` are a 150 GB
ephemeral overlay, `/workspace` is the network volume. CorridorKey's 9.4 GB venv
lives on the volume and arrives intact; only the interpreter it points at,
`/root/.local/share/uv/python/cpython-3.13-linux-x86_64-gnu/bin/python3.13`,
is gone.

The fix does not rebuild the venv. `pod_bootstrap.sh` keeps uv's data directory
on the volume at `/workspace/runpod-slim/uv` and symlinks
`/root/.local/share/uv` at it, so every path already recorded inside the venv
stays valid. It also installs `uv` itself onto the volume, reinstalls `rsync`
(genuinely per-pod: it is an apt package on the overlay), and gates on
`import torch` rather than on the symlink resolving — a resolving symlink proves
nothing if the compiled extensions do not load against the interpreter that
arrived.

`preflight` scp's the repo's copy up to `/workspace/runpod-slim/bootstrap.sh`
and runs it before the CorridorKey checks, so the repo is the single source of
truth and a stale copy on the volume heals itself. The script is idempotent; a
healthy pod costs one `import torch`. Migration is now: put the new host, port
and proxy URL in `instance.json`, run `--stop-after preflight`.

Not yet covered: the ComfyUI side of a new pod. Model presence is checked, not
provisioned, so a volume without the edit model still fails at `preflight`
rather than being repaired by it.

`edit_model_present` verifies the checkpoint is *listed*, not that it *loads*.
`qwen_image_edit_2511_int8_convrot.safetensors` — the bootstrap-declared file —
cannot load on ComfyUI 0.26.2, whose `QUANT_ALGOS` supports only
`float8_e4m3fn`, `float8_e5m2` and `nvfp4`, and it fails with
`KeyError: 'int8_tensorwise'` at `UNETLoader`. Use
`qwen_image_edit_2511_fp8mixed.safetensors` from the same Comfy-Org repository;
its header declares `float8_e4m3fn` only. `comfy-bootstrap.json` should be
updated to declare the fp8mixed variant.

`soft_free` between consecutive edit stages was removed: they share
`EDIT_MODEL`, so freeing forced a 19 GiB VRAM reload each time. It is still
called before SAM, before the edit model, and before CorridorKey. Lowering CFG
would not help — classifier-free guidance costs two forward passes at any CFG
above 1.0, and the handover fixes CFG at 4 and steps at 40.

### Staged run procedure

The runner had no preflight: `verify_nodes()` was never called, and the SSH and
standalone-CorridorKey settings were only exercised after four Qwen generations
had already run, so a wrong `COVER_STORY_CORRIDORKEY_ROOT` surfaced at the very
end. It also carried a stale hardcoded instance address as the `--ssh-target`
default, which both violates the handover's rule against instance addresses in
the repository and let an unset variable silently target a recycled host.

`--ssh-target`/`--ssh-port` are now required (via flag or environment), and the
run is divided into gated stages. Each writes its results to `checks.json` and
refuses to continue on failure:

Instance settings come from `~/.config/cover-story/instance.json` rather than a
set of exported variables. That path is outside any git worktree, so the Comfy
token and SSH details cannot be committed by accident, as the handover requires.

```bash
python3 tools/cover-story/run_qwen2512_skin_head_clothes_poc.py --init-config
# writes the template at mode 600; fill in server, ssh_target, ssh_port,
# corridorkey_root and output_dir, then:

for stage in preflight carrier envelope skin identity clothes extract composite; do
  python3 tools/cover-story/run_qwen2512_skin_head_clothes_poc.py --stop-after "$stage"
done
```

Resolution order is **flag, then config file, then environment**, and the run
prints where every setting came from with the token redacted. The config
deliberately outranks the environment: a stale exported `COMFY_SERVER` must not
silently shadow a stored setting. An untouched `HOST`/`PORT`/`TOKEN` placeholder
counts as unset, so a half-filled file is refused rather than dialled.

**Use the bootstrap-declared edit model.** `comfy-bootstrap.json` declares only
`qwen_image_edit_2511_int8_convrot.safetensors`, with a download URL. The
`qwen_image_edit_fp8_e4m3fn.safetensors` the earlier RunPod image happened to
carry is a *different* model — no `2511` version tag, different quantization,
and not a declared dependency. The handover lists the 2511 checkpoint among the
settings to preserve, and the identity edit feeds two references through
`TextEncodeQwenImageEditPlus`, which is exactly where edit-model versions
diverge. Install the declared checkpoint and leave `edit_model` at its default,
so a failure is attributable to the recipe rather than to the model.

| stage | automatic checks | inspect |
|---|---|---|
| `preflight` | SAM3_Detect exposed, edit model present, SSH reachable, CorridorKey installed | — |
| `carrier` | coverage, green figure, blue background, feet visible, bald | `carrier.png` |
| `envelope` | masks plausible, envelopes overlap, clothes clears the silhouette, skull halo protected | `masks/envelope-review.png` — **human gate** |
| `skin` | green paint removed, background intact, still bald, registration | `skin-tone.png` |
| `identity` | outside the mask bit-identical, inside changed | `identity.png` — **human gate for likeness** |
| `clothes` | same, plus no green remnant in the garment region | `clothes.png` |
| `extract` | alpha coverage, no interior holes, alpha extends past the carrier silhouette | `*-alpha.png` |
| `composite` | — | `composite.png` |

`identity` and `clothes` reuse `production.outside_mask_changed()`:
`ImageCompositeMasked` leaves everything outside the mask bit-identical, so the
comparison is exact, and it counts only strictly-zero mask pixels so soft SAM
edges cannot false-alarm. This assumes `FluxKontextImageScale` is a no-op at
832×1248 — a Kontext-native resolution — so on the first run treat a nonzero
`outside_mask_unchanged` count as a diagnostic about image/mask alignment
rather than proof the edit misbehaved.

`extract`'s `*_extends_past_carrier` check is the one that proves out the hint
fix above: alpha that stops at the carrier silhouette means garment bulk or
hair was clipped.

Read this after `LAYERED_COSTUME_PRODUCTION_HANDOVER.md` and
`LAYERED_COSTUME_PIPELINE_REFERENCE.md`. This file records the implemented
state and the safe pickup point; it does not replace either specification.

## Pickup point

The v19 four-performer pilot is fully generated, extracted, and composed. It
is intentionally stopped at the human-review gate. Do not start full
production or modify `plugins/cover-story` until that review passes.

- Output root: `/mnt/Misc/sd/cover-story/layered-costume-production-v19`
- Review: `/mnt/Misc/sd/cover-story/layered-costume-production-v19/review.html`
- Manifest status: `composed_pending_visual_review`
- Generation attempts: 124, all `generated_pending_review`, no retry seeds
- Extracted layers: 80, all automatic checks passing
  - 16 skin
  - 16 hair
  - 48 clothed body
- Composites: 64, all automatic checks passing and awaiting human review
- Plugin integration: not started
- Full production: not started

The scoped implementation files are currently untracked in Git. Inspect and
stage them deliberately; do not assume this work has been committed.

## Implemented production flow

The resumable runner is `layered_costume_production.py`; its catalog is
`layered-costume-catalog.json`. It supports `generate`, `extract`, `compose`,
`review`, `export`, and `self-test`, uses `--server` with `COMFY_SERVER`
fallback, and records immutable prompts, seeds, models, settings, reference
order, file hashes, and retry attempts in an atomically replaced manifest.

The accepted layer order remains:

1. background;
2. performer face/neck/upper-chest skin plate;
3. complete clothed-body plate including hands and footwear;
4. hair plate.

No full-body performer skin plate is produced.

### Static carrier variants

The green canonical carriers remain the accepted sources. A blue variant is
derived deterministically by swapping the green and blue image channels. The
variant has identical geometry and is selected before the outfit edit based
on the outfit's key-color conflict. There is no post-generation background
swap or rekey edit.

Body generation edits the carrier's person region minus a SAM3-dilated entire
head/neck/upper-chest aperture. The background and aperture remain static.

### Extraction recipes

Final body mattes use the standalone CorridorKey installation under
`/workspace`. SAM3 provides a broad, dilated person/garment hint with an
entire-head/neck/chest subtraction, but it does not clip the returned alpha.
The immutable recipe is:

- `class_gate.enabled: false`
- `edge_cleanup: corridorkey-alpha-neighbor-despill-v2`
- CorridorKey output is the final body alpha

Performer skin and hair sources contain two simultaneous key colors. One
CorridorKey screen pass cannot remove both unwanted regions, so those layers
retain the narrow SAM class gate after CorridorKey:

- `class_gate.enabled: true`
- one-pixel dilation and one-pixel softness
- `edge_cleanup: sam-class-gate-neighbor-despill-v2`

This distinction matters: clothing is not hard-segmented by SAM.

## Evaluation already performed

All raw body plates, body mattes, skin mattes, hair mattes, backgrounds, and
64 composites were inspected as contact sheets. The final pass showed:

- complete clothing, hands, and footwear;
- transparent head/neck/chest apertures;
- no obvious green/blue background fringe;
- aligned performer skin and hair in the composites;
- no failed generation, layer, or composite checks.

Some isolated hair QC thumbnails look short or partial. In the composites
they align with their source pose/hairstyle and were not automatically
rejected. Human review should still inspect face/hair and neckline closeups.

The HTML review includes layer toggles, checkerboard layer previews, full
composites, 600x900 card crops, face/hair, neckline, hands/shoes closeups, and
prompt/seed/hash provenance.

## Problems found and fixed

- Post-edit background swapping caused green/gray outer halos. Replaced with
  static geometry-identical green/blue carrier variants.
- A broad body background mask caused a thick chroma ring. Removed the
  background edit and reduced the final class-gate expansion.
- Generic SAM `clothing` missed a Viking tunic. Body hints now union person,
  upper garments, trousers, hands, and footwear, then subtract the entire
  keyed head/neck/chest region.
- The runner multiplied body CorridorKey alpha by a near-raw SAM mask,
  turning the hint into a hard segmentation gate and creating holes. Body
  output now uses CorridorKey alpha directly.
- Removing that gate globally made dual-key performer plates nearly
  full-frame. The gate is now explicit only for performer skin/hair layers.
- A low-saturation brown hair pixel could be classified as green residue by
  ratio alone. Residue now also requires more than 16 levels of absolute
  channel separation; saturated chroma probes still fail correctly.
- Extraction reused the local name `theme` and accidentally enabled a filter
  after the first body. Jobs are now selected before iteration and progress
  reports the full expected count.
- Repeated standalone calls opened many SSH connections. SSH multiplexing now
  reuses a short-lived control socket under `/tmp`.
- Immutable performer-mask provenance retained its original `invert: false`
  field so validated performer attempts resume without a semantic-only
  signature mismatch.

## Safe next steps

1. Open `review.html` and review all 64 composites, especially face/hair,
   neckline, hands, and footwear closeups.
2. Record human decisions in a reviewer JSON file and apply them with:

   ```bash
   python3 tools/cover-story/layered_costume_production.py review \
     --output-dir /mnt/Misc/sd/cover-story/layered-costume-production-v19 \
     --reviews /path/to/reviews.json
   ```

   Rejections require notes. Do not silently accept all entries merely because
   automatic checks passed.
3. If a generated attempt is human-rejected, regenerate only its filtered job
   with `--retry-rejected`; the runner permits at most two automatic retry
   seeds after failed checks.
4. Re-run `compose` and `review` after any accepted replacement layer.
5. Run `export` only after the human pilot gate passes.
6. Stop again before full production or plugin integration and obtain explicit
   approval.

Useful checks:

```bash
python3 tools/cover-story/layered_costume_production.py self-test
python3 -m py_compile tools/cover-story/layered_costume_production.py
```

For extraction, use `COMFY_SERVER` from the environment and supply the
user-provided SSH target and port at runtime. Keep all server URLs, tokens,
SSH credentials, and instance addresses out of the repository and manifests.

## Current cautions

- `review.html` is static. Toggle state is only visual; decisions are persisted
  by passing reviewer JSON back to the `review` phase.
- The external v19 output directory is authoritative for this pilot and is not
  part of the Git worktree.
- Do not delete or overwrite v19 to obtain a clean rerun. Start a new versioned
  output root so provenance remains immutable and v19 stays reviewable.
- Do not replace standalone CorridorKey with SAM3 or the Comfy CorridorKey
  node for final production mattes.
