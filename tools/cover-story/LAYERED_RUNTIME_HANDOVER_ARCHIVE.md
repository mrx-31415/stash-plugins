# Cover Story: layered runtime handover archive

Updated 2026-07-28 after runtime performer-card compositing was completed.

## Start here

Read the repository `AGENTS.md`, inspect the complete affected flow before
editing, and preserve the dirty worktree. The modified and untracked files are
the user's ongoing feature work; do not reset, clean, or overwrite unrelated
changes.

The remaining work is:

1. Assemble and replace shipped assets with the already approved
   performers/backgrounds. No further generation or review is needed.
2. Last, back up and rewrite Git history to remove obsolete binaries. This
   changes commit hashes and requires a coordinated force-push.

Use a fresh thread/work package for each materially different phase.

## Completed v9 performer production

Local review/output:

`/mnt/Misc/sd/cover-story/performers-production-v9-20260728`

Remote output:

`/workspace/cover-story/performers-production-v9-20260728`

The local run is complete and has no `FAILED` marker:

- 500 raw PNGs;
- 350 green-screen and 150 blue-screen QC PNGs;
- 350 green-screen and 150 blue-screen transparent PNGs;
- 350 green-screen and 150 blue-screen 600x900 AVIFs;
- `manifest.json`, `review.html`, and `COMPLETE`.

The workflow is host-local and sequential per performer:

1. generate one raw through ComfyUI;
2. key it on the Vast host with standalone CorridorKey;
3. start the next generation while rsync mirrors results locally and the local
   host encodes AVIF.

Relevant launch/pipeline files:

- `run_vast_performer_round.sh`
- `run_vast_performer_production.sh`
- `mirror_vast_performer_run.sh`
- `run_transparent_performers.py`
- `run_corridorkey_standalone.py`
- `encode_performer_assets.py`

Vast connection:

`ssh -p 40764 root@107.206.71.138`

Remote project:

- tools: `/workspace/stash-plugins/tools/cover-story`
- CorridorKey: `/workspace/CorridorKey`
- ComfyUI: `http://127.0.0.1:18188`

`/workspace` is not a persistent Vast volume. Do not destroy/recycle the
instance before anything still needed there has been mirrored locally.

## Completed alpha-hint fix and POC

The user identified performer-293 as a baffling key failure:

- raw:
  `raw/293-amanda-pierce-d03-c01-p16-w09-b38-c05_chroma-v9__00001_.png`
- wardrobe: `muted indigo casual short-sleeve jersey top`
- route: green screen, which is correct;
- checkpoint/settings: green CorridorKey model and green screen channel, also
  correct;
- failure: a large transparent hole across the indigo shirt.

Inspection proved the large hole already exists in `coarse_hint()` output
before CorridorKey inference. CorridorKey is following a bad seed; this is not
checkpoint selection.

Root cause:

`run_corridorkey_standalone.py::coarse_hint()` adds all pixels within an RGB
distance of 45 from the sampled screen color so enclosed screen pockets are
seeded as background. Muted/low-saturation indigo pixels can fall inside that
cube for a green screen even though green is not their dominant channel.

The implemented correction keeps the existing corner-connected and close-color
seeds, then applies one final screen-channel-dominance gate to the coarse alpha
mask. Pixels where another channel dominates are forced back to foreground.
The focused self-check now includes the 293-style muted opposite-hue case.

Successful visual POC:

- local:
  `/mnt/Misc/sd/cover-story/alpha-hint-dominance-poc-v2-20260728`;
- remote:
  `/workspace/cover-story/alpha-hint-dominance-poc-v2-20260728`;
- performer-293's indigo shirt is restored;
- performers 021, 050, and 068 retain clean screen removal;
- 293 recovered 5.13% opaque area; control alpha was effectively unchanged.

The earlier failed POC without the final-mask gate remains in
`alpha-hint-dominance-poc-20260728`; ignore it. No production output was
overwritten and no images were regenerated.

CorridorKey files/checkpoints on Vast:

- green:
  `CorridorKeyModule/checkpoints/CorridorKey_v1.0.safetensors`
- blue:
  `CorridorKeyModule/checkpoints/CorridorKeyBlue_1.0.safetensors`

The backend was previously verified to select the correct model and screen
channel. Current processing uses refiner 1.0, despill 1.0, auto despeckle on,
despeckle size 400, and GPU post-processing.

## Completed performer review, repair, and replacements

The shared reviewer writes:

`/mnt/Misc/sd/cover-story/reviews.json`

Run it with:

```bash
python3 -u tools/cover-story/review_headshots.py \
  --root /mnt/Misc/sd/cover-story \
  --reviews /mnt/Misc/sd/cover-story/reviews.json
```

The user reviewed all 500 original v9 QC images, the repair batch, and every
replacement follow-up. The performer phase is approved; do not ask for another
performer review.

The 20 key-failure raws were re-keyed without regeneration in:

- local:
  `/mnt/Misc/sd/cover-story/performers-production-v9-key-repair-20260728`;
- remote:
  `/workspace/cover-story/performers-production-v9-key-repair-20260728`.

Use the repaired QC, transparent PNG, and AVIF for these ten approved repairs:

`139, 145, 253, 260, 293, 302, 309, 430, 454, 462`.

Their original v9 raws remain the source raws. The other ten re-key attempts
still failed and were replaced by fresh generations.

Twenty fresh replacements were approved: the ten unresolved key failures plus
the ten original non-key quality rejects. Use this exact source mapping rather
than choosing the newest-looking directory:

- `/mnt/Misc/sd/cover-story/performers-production-v9-replacements-20260728`:
  `105, 151, 215, 217, 223, 328, 368, 378, 408, 439, 477, 479, 496`;
- `/mnt/Misc/sd/cover-story/performers-production-v9-replacements-hair-route-20260728`:
  `032, 316`;
- `/mnt/Misc/sd/cover-story/performers-production-v9-replacements-followup-20260728`:
  `117, 276, 495`;
- `/mnt/Misc/sd/cover-story/performers-production-v9-age-followup-20260728`:
  `420`;
- `/mnt/Misc/sd/cover-story/performers-production-v9-slot095-final-20260728`:
  `095`.

Performer-095 intentionally uses a fresh generation of original-v9 variant 239,
whose review praised its `top/bust/face`, mapped to output stem
`performer-095`. Repeated prompts of the original 095 identity rendered too old.

The blue-accented hair on 032 and 316 could not survive their catalog blue
screens. Their approved replacements preserve the hair but use non-green
clothes on green screens. The same routing treatment was tried for 276; that
face was rejected, and the approved 276 is in the follow-up directory above.

All selected raws differ from the rejected generations. Their QC PNGs and RGBA
masters were visually inspected, and every selected AVIF decoded as 600x900
RGBA. Production v9 outputs were not overwritten.

Several QC-only hard-link directories were created to simplify browser review:

- `performers-production-v9-replacements-review-20260728`;
- `performers-production-v9-current-review-20260728`;
- `performers-production-v9-final-two-review-20260728`.

They are review aliases, not artifact sources. Intermediate rejected generations
also remain locally. Use only the exact source mapping above when assembling the
final 500.

## Wardrobe/prompt state

`performer_palettes.py` directly contains one curated palette for every one of
the 230 wardrobe prompts actually used by `PRODUCTION_EXPANSION`. There is no
general palette framework.

Current invariants/self-checks:

- all 230 production wardrobes have explicit palettes;
- no palette mixes blue-adjacent and green-adjacent colors;
- green is the default screen; blue is used when visible clothing/accessories
  are green-adjacent;
- teal was removed because it was unsafe on both screens;
- lower-body and footwear phrases were removed from generated wardrobe
  prompts, while complete garments such as dresses, gowns, suits, rompers,
  jumpsuits, and overalls remain;
- prompts explicitly request standing upright;
- current routing is 350 green / 150 blue.

Do not undo this direct catalog approach or reintroduce pants/shoes into
portrait prompts. A few approved replacements have intentional one-off wardrobe
and screen overrides documented in the source mapping above; do not force them
back through catalog routing.

## Completed performer backgrounds

The background generation/review phase is approved. The final selection is 30
wide 1920x1280 AVIFs: 16 retained originals, eight replacements, and six
requested additions. Preserve the existing non-destructive runtime treatment:
CSS blur 5px plus scale 1.04.

Retain every original from:

`/mnt/Misc/sd/cover-story/performer-backgrounds/assets`

except background numbers:

`001, 002, 011, 015, 017, 021, 022, 024`.

Use these exact ten sources from:

`/mnt/Misc/sd/cover-story/performer-backgrounds-focused-r2-20260728/assets`

- replacements:
  `bg-r001-sunlit-conservatory.avif`,
  `bg-r002-resort-lobby.avif`,
  `bg-r011-coastal-grassland-v3.avif`,
  `bg-r021-bright-airport.avif`,
  `bg-r022-sunlit-yacht.avif`,
  `bg-r024-stocked-bookstore.avif`;
- resort-terrace additions:
  `bg-v020-clifftop-terrace.avif`,
  `bg-v020-garden-terrace.avif`;
- gallery additions:
  `bg-v023-contemporary-gallery.avif`,
  `bg-v023-sculpture-gallery.avif`.

Use these exact four sources from:

`/mnt/Misc/sd/cover-story/performer-backgrounds-foliage-bypass-off-20260728/assets`

- replacements:
  `bg-r015-riverside-promenade.avif`,
  `bg-r017-green-forest.avif`;
- green-nature additions:
  `bg-v018-green-meadow.avif`,
  `bg-v018-garden-path.avif`.

All 30 selected AVIFs decode as 1920x1280. Their PNG sources and manifests
remain beside the AVIFs. The focused round contains two obsolete rejected 011
attempts in addition to its manifest-selected v3; do not select files by
directory recency or globbing.

Foliage A/B review proved that `krea2filterbypass.safetensors` at strength 1.5
caused the artificial leaf rendering: bypass-off won all three controlled
pairs. `run_performer_backgrounds.py` now accepts `--bypass-strength 0`; use
bypass-off for any future background generation. This does not change the
approved performer-generation recipe.

Review aliases:

- `performer-backgrounds-focused-r2-qc-20260728`;
- `performer-backgrounds-foliage-ab-bypass-20260728`;
- `performer-backgrounds-final-qc-20260728`.

The shared ratings remain in:

`/mnt/Misc/sd/cover-story/reviews.json`.

## Runtime compositing

Runtime performer-card composition is complete. It reuses the Viking pilot's
native image layers and shared Curator fallback path:

- `makeCover().performerComposition()` deterministically pairs an available
  transparent performer AVIF with one approved wide background AVIF;
- `PerformerCard.Image` renders the background and performer as separate
  layers;
- Stash Curator external performer cards and source references use the same
  composition path;
- any missing or failed layer falls back to the existing approved WebP
  portrait;
- performer detail headers continue using the existing WebP portrait;
- Viking scene cards, players, precomposed-cover fallback, theme selection,
  and procedural fallback for themes without assets are unchanged;
- the shared DOM replacement now ignores an image already inside a layered
  poster, preventing repeated Curator source-reference wrapping.

CSS keeps the approved non-destructive background treatment exactly:
`blur(5px)` and `scale(1.04)`. Performer sources remain transparent 600x900
AVIF layers; backgrounds remain wide 1920x1280 AVIF layers.

Only these representative approved runtime assets are currently copied:

- performers from the main v9 production: `001`, `050`, `068`, installed as
  `assets/performers/actor-001.avif`, `actor-050.avif`, and `actor-068.avif`;
- retained approved backgrounds `003`, `013`, and `020`, installed under
  `assets/performer-backgrounds/`.

The runtime allowlists only those three performers and three backgrounds.
Every other performer therefore continues to use its current shipped WebP
portrait without a failed AVIF request. Do not delete the WebPs yet.

### Remaining final-asset assembly

Handle this as the next separate work package:

1. Assemble `performer-001` through `performer-500` strictly from the main v9,
   repaired, and replacement source mappings documented above. Install them as
   `assets/performers/actor-NNN.avif`; do not select by glob recency.
2. Reconcile and export the runtime persona catalog for all 500 approved
   performer slots so its `actor-NNN` image paths match those AVIF names.
3. Assemble the exact 30 approved backgrounds documented above under
   `assets/performer-backgrounds/`.
4. Replace the three-entry `performerLayers` and `performerBackgrounds`
   allowlists in `cover-story.js` with the assembled sets.
5. Validate all performers as 600x900 RGBA with real transparency and all
   backgrounds as 1920x1280, then visually check cards across the six catalog
   crop variants and the background categories.
6. Only after the complete new set passes should removal of the old WebP
   portraits be considered. Git-history cleanup remains a later package.

## Git-history cleanup

Removing old binaries from Git history is possible, but it is intentionally
last. Before rewriting:

- finish and verify the new asset set;
- make a recoverable repository backup/mirror;
- identify exact obsolete paths and sizes;
- coordinate with anyone using existing clones/branches;
- use a history-rewrite tool such as `git filter-repo`;
- verify repository size and plugin tests;
- force-push only with explicit user approval.

Never combine the history rewrite with ordinary feature edits.

## Completed focused checks

The repair package ran:

```bash
uv run --with numpy --with pillow \
  python tools/cover-story/run_corridorkey_standalone.py --self-test
uv run --with pillow \
  python tools/cover-story/run_transparent_performers.py --self-test
python3 tools/cover-story/review_headshots.py --self-test
bash -n \
  tools/cover-story/mirror_vast_performer_run.sh \
  tools/cover-story/run_vast_performer_production.sh \
  tools/cover-story/run_vast_performer_round.sh
git diff --check
```

All passed. The four-image alpha-hint POC also passed. No repository files were
changed during repair/replacement production except this handover update; the
pre-existing dirty worktree remains the user's ongoing feature work.
