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

This validates and deduplicates the rated sources, writes sanitized selection
provenance to `runs/performers-production.json`, updates `personas.json`, and
exports the selected 600×900 WebPs plus `plugins/cover-story/personas.js`.

See `ASSET_GENERATION_HANDOVER.md` for experiment history, A/B results,
curation and export details. The `run_*_ab.sh`, resume and top-up scripts are
historical/recovery helpers; use `run_production.sh` for new production runs.
