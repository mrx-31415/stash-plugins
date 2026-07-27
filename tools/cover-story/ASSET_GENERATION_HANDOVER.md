# Cover Story asset-generation handover

Last updated: 2026-07-27

This is the persistent handover for the Cover Story performer-portrait work.
The earlier `/tmp/cover-story-asset-generation-handover.md` describes the
original themed scene-compositing idea, but it predates the implemented
performer pipeline and is no longer an accurate current-state document.

## Current state

Generation, review and curation are complete:

- `performers-v16-production`: 500 logical variants, with 62 byte-identical
  duplicate renders ignored; 395 keep, 2 maybe and 103 reject.
- `performers-v21-reject-topup`: 62 reruns of original rejects; 44 keep and 18
  reject.
- Final selection: 439 portraits (395 baseline keeps plus 44 improved top-ups).
- Export: 439 independent 600×900 WebPs at quality 75/method 6, approximately
  11.2 MiB.

Run `tools/cover-story/build_assets.sh` to reproduce the catalog, sanitized
selection manifest, browser metadata and WebP assets from the rated source
PNGs. The builder validates every rating and source hash, collapses only
identical duplicates and fails if the expected selection changes.

`COMFY_SERVER` must contain the remote URL including its token for new
generation. The token and raw PNGs are deliberately not stored in this
repository.

## Review viewer

The viewer is running on:

```text
http://192.168.28.247:8765/
```

Its host command is:

```sh
python3 -u tools/cover-story/review_headshots.py \
  --host 0.0.0.0 \
  --port 8765
```

It reads images under `/mnt/Misc/sd/cover-story/experiments` and writes all
feedback atomically to:

```text
/mnt/Misc/sd/cover-story/experiments/reviews.json
```

The viewer:

- selects one experiment folder at a time, preventing every experiment from
  loading into one page;
- automatically opens complete `*_A`/`*_B` experiments in pair-review mode;
- shows one pair at a time and accepts click or left/right-arrow selection;
- records the winner as keep and loser as reject, with tie and neither actions;
- supports native fullscreen, `S` side-by-side/single switching and Space A/B
  toggling;
- ignores Synology `@eaDir` thumbnail files;
- lazy-loads images;
- supports keep, maybe, reject, apparent age, notes, search and status filters;
- has an **Auto-keep viewed** checkbox.

Auto-keep is off until enabled and the choice is persisted in browser local
storage. An unreviewed card is marked keep only after it has been at least 50%
visible and then scrolls above the viewport. Existing keep/maybe/reject ratings
and comments are never overwritten.

## Final production recipe

The final workflow is native Krea 2 Turbo FP8:

- UNet: `krea2_turbo_fp8_scaled.safetensors`
- Text encoder: `qwen3vl_4b_fp8_scaled.safetensors`
- VAE: `qwen_image_vae.safetensors`
- Filter-bypass LoRA: `krea2filterbypass.safetensors`
- Turbo workflow:
  `tools/cover-story/workflows/krea2-turbo-fp8.json`
- 12 steps
- CFG 1
- Filter-bypass strength 1.5
- Independent deterministic seed per performer plus base seed `2026072700`
- No negative prompt

The bypass LoRA is inserted by `experiment_headshots.py`; it is not baked into
the saved native FP8 workflow JSON.

### Identity construction

The production mode is `performers-v6`. It creates:

- 500 unique prompt-identity bundles;
- 500 unrelated hashed performer seeds;
- ages 21–34:
  - 324 performers in their twenties;
  - 176 in their early/mid thirties;
- 125 blonde, 125 brunette, 125 black-haired and 125 red-haired performers;
- athletic builds with the existing small/medium/full/large bust distribution;
- no gray hair, pixie cuts or blue-black hair;
- braids, twists, coils and cornrows only for Black or mixed profiles;
- a few pink and blue dyed accents;
- softer eyebrow, lip, makeup and distinguishing-feature language.

Three face-description strategies are rotated:

- `balanced`: soft eyes/brows/lips plus three structural traits;
- `minimal`: eyes plus two structural traits;
- `natural-prose`: full structural bundle written as one coherent description.

Their 500-profile counts are 167, 167 and 166 respectively.

The country/nationality prompt is now independently selected per performer.
Caucasian profiles draw from 20 European countries; Latin, Black, Asian, mixed
and Middle Eastern profiles have separate geographically appropriate pools.
There are no American identity prompts in v16.

Important limitation: the ethnicity mix is inherited from the older 50 base
profiles and remains heavily Caucasian:

- 440 Caucasian
- 20 Latin
- 10 Black
- 10 Asian
- 10 mixed
- 10 Middle Eastern

The country variety is much better, but a future run intended to match a
different Stash library should rebalance these base counts rather than merely
changing nationality words.

### Pose, background, wardrobe and crop combinations

Every production performer receives an independently hashed combination from:

- 24 direct-gaze poses;
- 52 backgrounds:
  - colored studio backdrops;
  - homes, studios, galleries, lounges and cultural interiors;
  - courtyards, streets, parks, rooftops, terraces, waterfronts and other
    outdoor settings;
- 72 wardrobes:
  - knit tops, tees, shirts, tanks, hoodies and athletic tops;
  - sundresses, tube/bandeau tops, halters, camisoles, wrap dresses and other
    casual warm-weather clothing;
- six framing variants:
  - tight head-and-shoulders;
  - head and upper torso;
  - chest-up;
  - mid-torso;
  - waist-up;
  - wider environmental waist-up.

Crop variants are balanced at 83–84 images each. All 500 complete
pose/background/wardrobe/crop combinations are unique.

Pilot feedback favored casual clothing and outdoor/non-uniform backgrounds.
Those pools were expanded before v16. A reported overly strong dimple was
changed from “distinctive” to “subtle”.

## What the experiments established

### Model and workflow

- The older GGUF Turbo workflow was usable, but produced worse or noisier
  textures than the native FP8 Turbo model on the powerful remote GPU.
- Native raw/base could produce a pleasing face but had blurry textures and
  weak prompt adherence.
- Native Turbo FP8 was the best overall balance of detail, speed and adherence.
- Wan2.1 VAE upscale, raw/base variants and GGUF comparisons were explored but
  did not displace native Turbo FP8.

### Enhancer and bypass

- T-Enhancer, including strengths 0.7 and 1, tended to create a repeated
  “house face”.
- Raw/no enhancer preserved more identity variation but leaned older and
  weakened body/bust instructions.
- Controlled follow-up tests selected Filter-bypass strength 1.5 as the best
  tested compromise.
- Strength 3 made eyebrows and contrast more extreme.
- The earlier strength-5 GGUF bypass workflow improved body adherence but was
  too strong for the final native FP8 production recipe.

### Age and naturalness

The refinement A/B pilot contained 15 same-seed pairs:

- refined: 12 keep, 3 maybe, 0 reject;
- current: 8 keep, 2 maybe, 5 reject.

All five current-prompt rejects were too old. This led to:

- a production age range of 21–34;
- youthful adult age-band wording;
- restrained, natural makeup;
- explicit realistic pores and skin texture;
- negative prompts for aging, wax, plastic, cartoons and exaggerated
  proportions.

### Face-description strategies

The 48-image face-combination pilot tested six methods across eight same-seed
identity groups:

- full-current: 5 keep, 2 maybe, 1 reject;
- full-soft: 6 keep, 2 maybe;
- structure-only: 6 keep, 2 maybe;
- balanced: 7 keep, 1 maybe;
- minimal: 7 keep, 1 maybe;
- natural-prose: 7 keep, 1 maybe.

The three tied winners are rotated in production.

### Prompt identity versus seed

A fully crossed 6×6 experiment rendered six European prompt identities with
each of six seeds while holding age, hair, makeup, pose, body, wardrobe,
background and workflow constant.

The visual conclusion was that the prompt-identity bundle—nationality,
complexion, eyes and facial geometry—had a substantially stronger identity
effect than the seed. Seeds still mattered, but mostly varied a face within the
prompt-defined family.

Therefore production uses 500 unique identity bundles. It does not rely on
changing seeds around a small set of repeated prompt identities.

## Important files

- `tools/cover-story/README.md`
  - concise setup, generation and review instructions.
- `tools/cover-story/run_production.sh`
  - canonical final production recipe;
  - supports the generator's range, variant and dry-run options.
- `tools/cover-story/workflows/krea2-turbo-fp8.json`
  - saved native FP8 API workflow;
  - the production runner inserts FilterBypass2 at runtime.
- `tools/cover-story/experiment_headshots.py`
  - all prompt pools and experiment modes;
  - production mode `performers-v6`;
  - deterministic identities, seeds and style combinations.
- `tools/cover-story/comfy.py`
  - workflow prompt/seed preparation;
  - queue, polling, retry and image download logic.
- `tools/cover-story/review_headshots.py`
  - browser reviewer and JSON review API.
- `tools/cover-story/workflows/krea2-turbo-fp8.json`
  - final native FP8 workflow.
- `tools/cover-story/curate_personas.py`
  - validates, deduplicates and selects the final reviewed production sources;
  - writes sanitized curation provenance and persona metadata.
- `tools/cover-story/export_personas.py`
  - crops selected sources to 600×900;
  - exports WebP at quality 75/method 6;
  - writes the runtime `personas.js` manifest.
- `tools/cover-story/personas.json`
  - current 439-persona catalog.
- `tools/cover-story/runs/performers-production.json`
  - final selection provenance, source hashes and generation metadata.
- `plugins/cover-story/assets/performers/`
  - current shipped local performer portraits.
- `plugins/cover-story/personas.js`
  - current browser runtime metadata.

## Current plugin assets

The repository currently contains:

- 439 curated performer WebPs;
- 600×900 pixels each;
- WebP quality 75, method 6;
- approximately 11.2 MiB total.

These assets are already wired into performer cards/pages through
`plugins/cover-story/cover-story.js` and `plugins/cover-story/personas.js`.
They replaced the abstract performer placeholders after the local plugin was
uploaded and refreshed on the Stash host at `192.168.1.100`.

The source PNGs are generation masters and are not shipped. Curation and export
are deterministic through `tools/cover-story/build_assets.sh`. Inter-frame
compression is not useful for independent portraits.

## Validation commands

```sh
python3 -m py_compile \
  tools/cover-story/comfy.py \
  tools/cover-story/experiment_headshots.py \
  tools/cover-story/review_headshots.py

python3 tools/cover-story/comfy.py --self-test
python3 tools/cover-story/review_headshots.py --self-test
node plugins/cover-story/test.js
```

The Comfy helper self-test opens a temporary localhost socket and may require
running outside a restricted sandbox.

## Comfy bootstrap state

The sibling repository `/home/johan/tools/comfy-bootstrap` was previously
updated with:

- `workflows/krea2-turbo-fp8.json`;
- manifest links for native Krea 2 Turbo FP8, Qwen FP8 text encoder and Qwen
  VAE.

The filter-bypass LoRA asset was already known to the bootstrap manifest from
earlier work. `LoraLoaderModelOnly` is a core ComfyUI node, so the chosen
bypass-2 production workflow adds no custom-node dependency.

Those sibling-repository changes were uncommitted when last inspected. Verify
their current status before assuming they are published or installed.

## Repository cautions

Do not commit:

- the remote ComfyUI access token;
- raw generated PNG batches;
- `reviews.json` unless deliberately sanitized and chosen as repository
  provenance;
- Python `__pycache__` directories.

## Next actions

1. Reinstall or refresh the local plugin and inspect performer cards/pages.
2. Only after the performer pool is settled, return to identity-edit variants
   and themed scene/background/foreground composition.

## Negative-prompt A/B

At 280 reviewed v16 images, ratings were 209 keep, 2 maybe and 69 reject.
Forty-three notes explicitly mentioned plastic/AI/doll-like faces, waxy skin
or dead eyes. The strongest prompt correlation was makeup wording:

- `Polished soft-glam...defined lashes...satin lips`: 37/74 reject (50%);
- the other three makeup variants: 8/59 to 13/69 reject (14–19%).

The `subtle cheek dimple` feature was also rejected in 20/31 images (65%),
including 17 explicit dimple notes. Production now reuses the successful
understated makeup wording in place of soft-glam and replaces the dimple with
a tiny jawline beauty mark. Both are in-place replacements so deterministic
assignments remain stable.

`run_negative_prompt_ab.sh` generates 24 same-seed pairs in
`performers-v17-negative-ab`. Arm A uses the v16 negative prompt. Arm B adds
targeted manga/CGI/beauty-filter/porcelain-skin/glassy-eye concepts. The
selection balances known plastic/doll failures and keeps across all three
face strategies.

```sh
COMFY_SERVER="$COMFY_SERVER" \
  tools/cover-story/run_negative_prompt_ab.sh
```

Pass `--dry-run` to inspect all 48 jobs without queueing them. Rate A and B
without checking this mapping, then compare paired outcomes. Adopt B only if
it rescues more A rejects than it damages A keeps; otherwise keep the shorter
v16 negative prompt and rely on the positive-prompt fixes.

### Result

All 24 pairs were rated:

- B won 9;
- A won 7;
- 5 tied;
- 3 were rejected on both sides.

The 9–7 decisive split is indistinguishable from chance and does not justify
the longer B negative prompt. B won 6–1 among the 12 deliberately selected
plastic-prone seeds, but A won 6–3 among the 12 previously successful seeds.
The treatment can move some hard cases in a better direction, but also
regresses good cases and does not remove the model's synthetic face prior.
Keep the shorter A negative prompt. A future paired test should change a
workflow-level variable, such as filter-bypass strength 2 versus 1, rather
than add more negative-prompt synonyms.

## Filter-bypass strength A/B

`run_bypass_strength_ab.sh` reruns the 12 plastic-prone seeds from the
negative-prompt test. Both arms use the shorter v16 negative prompt and the
revised production prompts. The only changed variable is filter-bypass LoRA
strength:

- A: strength 2;
- B: strength 1.

```sh
COMFY_SERVER="$COMFY_SERVER" \
  tools/cover-story/run_bypass_strength_ab.sh
```

This creates 12 pairs in `performers-v18-bypass-ab`, which the review viewer
opens automatically in pair mode. Pass `--dry-run` to inspect all 24 jobs
without queueing them.

### Result

Strength 1 won 10 pairs and tied 2; strength 2 won none. Strength 1 clearly
reduces the synthetic doll-face failure, but the visual review found a
tradeoff: faces and bodies can become less conventionally attractive and less
idealized.

The next test brackets the likely compromise rather than changing CFG at the
same time:

- A: strength 1;
- B: strength 1.5.

```sh
COMFY_SERVER="$COMFY_SERVER" \
  tools/cover-story/run_bypass_midpoint_ab.sh
```

This creates 12 pairs in `performers-v19-bypass-midpoint-ab`. After choosing
the bypass strength, test CFG 1 versus 1.25 at that fixed strength. Krea 2
Turbo is intended to run guidance-free, so CFG above 1.25 is not the next
useful direction.

### Midpoint result

Strength 1.5 won 7 pairs, strength 1 won 2, 2 tied and 1 was rejected on both
sides. Together with strength 1 beating strength 2 by 10–0, use strength 1.5
as the provisional production setting.

The next paired test fixes bypass strength at 1.5 and changes only CFG:

- A: CFG 1;
- B: CFG 1.25.

```sh
COMFY_SERVER="$COMFY_SERVER" \
  tools/cover-story/run_cfg_ab.sh
```

This creates 12 pairs in `performers-v20-cfg-ab`. At CFG 1 the KSampler is
guidance-free, so the negative conditioning has no effect; that is part of
what this test intentionally measures.

### CFG result and recommended recipe

CFG 1.25 won 6 pairs, CFG 1 won 5, and image 52 was rejected on both sides.
The result is indistinguishable from chance. Prefer CFG 1 because it is
Krea 2 Turbo's native guidance-free setting and removes ineffective negative
conditioning.

Recommended settings for the next production generation:

- native Krea 2 Turbo FP8;
- 12 steps;
- CFG 1;
- FilterBypass2 strength 1.5;
- no negative prompt;
- revised production prompts without soft-glam makeup or cheek dimples.

Do not spend more generation time trying to rescue image 52; reject that
identity/seed combination. Do not test another bypass unless the recommended
recipe shows a systematic failure in a fresh mixed sample.

The installed `krea2filterbypass.safetensors` was inspected directly. It is
an F32 `[1, 12]` tensor with exact zeroes everywhere except vectors 9 and 10
(`-0.51171875` and `-0.890625`), so it is already the two-vector
FilterBypass2 design. The newer Fedor bypass publishes the same design with
only insignificant rounding differences; do not spend an A/B run on it.
FilterBypass3 adds vector 11, while skc3vo changes all 12 vectors. Those are
meaningfully different tests, but also more likely to alter expression,
style and anatomy, so only test them if strength 1.5 remains unsatisfactory.
