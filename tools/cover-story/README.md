# Cover Story portrait generation

The production workflow and runner live here so the final recipe is
reproducible without storing the tokenized ComfyUI URL.

## Generate

Set the remote server URL, choose a unique run label, and run all 500
performers:

```sh
export COMFY_SERVER="http://HOST:PORT/?token=REDACTED"
COVER_STORY_LABEL=performers-v22 \
  tools/cover-story/run_production.sh
```

The final tested recipe is:

- native Krea 2 Turbo FP8;
- 12 steps;
- CFG 1;
- FilterBypass2 strength 1.5;
- deterministic base seed `2026072700`;
- no negative prompt;
- production prompt mode `performers-v6`.

Use the existing generator options for ranges, individual variants and dry
runs:

```sh
tools/cover-story/run_production.sh --start 201 --stop 300
tools/cover-story/run_production.sh --variant 5 --variant 52
tools/cover-story/run_production.sh --dry-run
```

`COVER_STORY_OUTPUT_DIR` overrides the default local output directory,
`/mnt/Misc/sd/cover-story/experiments/$COVER_STORY_LABEL`.
`COVER_STORY_SEED` overrides the deterministic base seed.

## Transparent performer production

Generate, key and encode all 500 performers:

```sh
export COMFY_SERVER="http://HOST:PORT/?token=REDACTED"
tools/cover-story/run_transparent_performers.sh --start 1
```

The runner uses the accepted chroma-key hint with green suppression and an
expanded wardrobe catalog covering casual, smart-casual, evening, professional,
date-night, summer, winter, glamorous, fitted, gala, punk, rock, alternative,
skater and edgy outfits. Wardrobe descriptions
use their natural wording without an added single-color instruction; occasional
keying failures are handled during review. Multi-garment outfits request
coordinated contrasting colors and distinct materials. It saves every raw green-screen PNG before CorridorKey runs,
writes full-resolution RGBA and QC PNGs, then exports only 600×900 AVIF q70
with lossless alpha. Progress and provenance are updated atomically after every
performer. CorridorKey is queued before the next portrait so its downloads and
local PNG/AVIF exports overlap the next generation. Restart the same command to
resume. Recovery and targeted retries:

```sh
tools/cover-story/run_transparent_performers.sh --variant 17 --variant 204
tools/cover-story/run_transparent_performers.sh --key-only --start 201
tools/cover-story/run_transparent_performers.sh --codec-only
```

The default output is
`/mnt/Misc/sd/cover-story/performers-transparent-v6-colorless-test`; override it with
`COVER_STORY_OUTPUT_DIR`. Open `review.html` there while reviewing the run.
With no arguments the wrapper generates only layered color test variant 2;
pass `--start 1` for the complete run.
Keep the raw and RGBA masters outside the deployed asset set; WebP can be
derived later if actual client compatibility requires it.

Review outfits or keyed results by pointing the existing reviewer at the
production root. Its folder selector exposes `raw`, `qc`, `corridorkey`, and
AVIF `assets`, while ratings stay in a separate production review file:

```sh
python3 -u tools/cover-story/review_headshots.py \
  --root /mnt/Misc/sd/cover-story \
  --reviews /mnt/Misc/sd/cover-story/reviews.json
```

## Performer backgrounds

Generate the six-master wide-background PoC:

```sh
export COMFY_SERVER="http://HOST:PORT/?token=REDACTED"
tools/cover-story/run_performer_backgrounds.sh --poc
```

Open `/mnt/Misc/sd/cover-story/performer-backgrounds/review.html`. Each
1920×1280 master is shown through four 2:3 CSS crop presets, with available
transparent performers stacked above it. If the PoC passes, resume the same
output directory without `--poc` to generate all 24 masters:

```sh
tools/cover-story/run_performer_backgrounds.sh
```

The runner is resumable and supports `--variant`, `--start`, `--stop`,
`--dry-run`, and `--codec-only`. It exports one AVIF q70 per master and records
prompts, seeds, hashes, compatibility tags, crop positions, zooms, and raw/AVIF
totals in `manifest.json`. No portrait crops or WebP fallbacks are stored.

## Transparent performer PoC

Generate the fixed same-seed palette/control comparison on green and blue screens,
then run all four images through CorridorKey:

```sh
export COMFY_SERVER="http://HOST:PORT/?token=REDACTED"
tools/cover-story/run_performer_cutout_poc.sh
```

Open `/mnt/Misc/sd/cover-story/performer-screen-poc-20260728/review.html`.
Each row shows the raw screen image, CorridorKey QC pass and transparent AVIF.
Set `COVER_STORY_OUTPUT_DIR` and `COVER_STORY_LABEL` together for another fresh run.

## Required ComfyUI assets

The saved API workflow is `workflows/krea2-turbo-fp8.json`. ComfyUI must have:

- `krea2_turbo_fp8_scaled.safetensors`;
- `qwen3vl_4b_fp8_scaled.safetensors`;
- `qwen_image_vae.safetensors`;
- `krea2filterbypass.safetensors`.

The runner inserts the bypass LoRA into the saved workflow at runtime.

## Review

```sh
python3 -u tools/cover-story/review_headshots.py \
  --host 0.0.0.0 \
  --port 8765
```

Ratings are written atomically to
`/mnt/Misc/sd/cover-story/experiments/reviews.json`.

## Build plugin assets

After rating the production and top-up groups:

```sh
tools/cover-story/build_assets.sh
```

This validates the final 500-image selection, writes exact provenance to
`runs/performers-static-final.json`, updates `personas.json`, and exports the
selected opaque 600×900 AVIF q60 YUV420 portraits plus
`plugins/cover-story/personas.js`.
It also validates `scene-assets.json` and exports the Viking pilot backgrounds,
alpha actors and fallback precomposed covers from
`/mnt/Misc/sd/cover-story/scenes`. Scene-layer export requires `avifenc`; it
writes AVIF q70 with lossless alpha alongside the WebP browser fallback.

See `ASSET_GENERATION_HANDOVER.md` for experiment history, A/B results,
curation and export details. The `run_*_ab.sh`, resume and top-up scripts are
historical/recovery helpers; use `run_production.sh` for new production runs.
