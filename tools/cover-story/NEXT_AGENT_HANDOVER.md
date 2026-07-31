# Cover Story: static portrait next-agent handover

Updated 2026-07-29 after assembling the unified final review group.

## Current production readiness

The accepted selections are combined into one reference-only viewer group:

- `static-performer-final-review-v1/raw`: 500 hard-linked PNGs;
- manifest: `static-performer-final-review-v1/manifest.json`;
- builder: `tools/cover-story/build_static_performer_final_review.py`;
- all images are 1024x1536 and retain exact accepted-source provenance.

The source breakdown is 361 original v3, 72 styling, 43 identity, 16 final,
5 closeout, and 3 runoff selections. The unrated final-candidates confirmation
group is not an input; its three B references merely identify the actual
round-robin winners (196 candidate 2, 239 candidate 1, and 266 candidate 1).
No image was regenerated and the v3 source run remains unchanged.

The approved group is now installed in the plugin:

- 500 opaque 600x900 AVIF q60/speed 6/YUV420 portraits in
  `plugins/cover-story/assets/performers`;
- 11,979,781 bytes (11.4 MiB) total;
- `tools/cover-story/runs/performers-static-final.json` records source and
  asset hashes, accepted sources, rounds, dimensions, and sizes;
- `tools/cover-story/personas.json` and `plugins/cover-story/personas.js`
  contain all 500 slot-aligned identities;
- performer pages/cards use the AVIF directly with no performer composition;
- Viking scene layering and its WebP fallbacks remain unchanged.

Stash performer IDs claim unused catalog portraits within the runtime session,
so the first 500 distinct performers encountered cannot show exact duplicate
assets. Synthetic director, photographer, and queue IDs retain the original
deterministic hash lookup.

The remaining production narrative below is historical context. Do not restart
portrait generation unless a newly reviewed defect requires a targeted
replacement.

An AVIF codec comparison was reviewed in
`static-performer-avif-quality-ab-v1/assets`: six performers covering all six
crops, with q70 paired separately against q60, q50, and q40. All test images
are opaque 600x900 YUV420 at speed 6. q60 was selected for the installed set
after q50 proved borderline too compressed.

The completed static v3 run has 500 reviewed assets: 367 keep, 83 maybe, and
50 reject. Feedback remediation is ready for pair review:

- `static-performer-feedback-styling-ab-v1`: 85 pairs / 170 verified PNGs;
- `static-performer-feedback-identity-ab-v1`: 156 pairs / 312 verified PNGs;
- both contain a checked 500-slot `feedback.json`;
- together they cover all 139 requested-change slots exactly;
- v3 and shipped assets remain unchanged.

Use the existing reviewer at `http://127.0.0.1:8765/`. Select the `raw` group
for each directory. Styling uses one same-seed B candidate. Identity rejects
use three candidates, identity maybes use two, and each candidate is paired
against the original v3 A image.

The exact v9 bandeau phrase was restored for slot 013. Eight criticized
electric-violet velvet slots now test bandeau/cardigan replacements, and all
six chainmail slots test an opaque fitted bandeau underlayer. Approved velvet
assets were not regenerated.

Both remediation groups were fully reviewed. Their results resolved 115 of
139 targeted slots and produced 24 explicit follow-ups. Those follow-ups are
now rendered in:

- `static-performer-feedback-final-ab-v1`: 44 pairs / 88 verified PNGs.

This final group includes 30 new face candidates, 11 styling refinements, the
selected slot-411 identity with longer wavy hair, and direct runoffs between
the two winning candidates for slots 471 and 476. Review this group before
assembling the final accepted-source manifest.

The final group was fully reviewed and left eight unresolved slots. Their
closeout is now rendered in:

- `static-performer-feedback-closeout-ab-v1`: 20 pairs / 40 verified PNGs.

It contains new pattern options for 031, a direct 107 runoff, younger identity
sets for 196 and 266, three outfit options for 239, broader identity-and-hair
sets for 360 and 430, and petrol-versus-rust bandeaus for 472. Review this
group before assembling the accepted-source manifest.

The closeout left multiple winners for 196, 239, and 266. Their reference-only
round-robin comparison is:

- `static-performer-feedback-runoff-ab-v1`: 7 pairs / 14 verified PNGs.

No images were regenerated for this runoff.

The runoff selected 196 candidate 2, 239 candidate 1, and 266 candidate 1.
Their original-v3-versus-finalist confirmation group is:

- `static-performer-feedback-final-candidates-ab-v1`: 3 pairs / 6 verified
  PNGs.

Do not resume either partial production directory:

- `static-performer-production-blur-q60-v1` has 18 obsolete slots;
- `static-performer-production-blur-q60-v2` has 20 obsolete slots.

Both predate the final wardrobe and/or bust wording. Preserve them, but start
the next full run in a fresh `static-performer-production-blur-q60-v3`
directory.

Performer 095 is locked to identity replacement v2 candidate R4:

- profile override: mid twenties, warm golden-beige skin, softly upturned
  hazel eyes, softly heart-shaped face, long glossy black curls;
- identity seed:
  `identity_seed("ana-torres-d05:identity-replacement-v2:4")`;
- reviewer status: sole `keep` in
  `performer-095-identity-replacement-v2/raw`.

The appearance-detail A/B favored the current catalog over removing facial
details: A 8, B 4, tie 12. Keep the existing balanced/minimal/natural-prose
rotation.

Two fitted-wardrobe A/B rounds are complete:

- `wardrobe-tightness-dress-ab-v1`: B 15, A 4, tie 3, neither 2;
- `wardrobe-tightness-confirm-ab-v2`: B 17, A 4, tie 2, neither 1.

The final `performer_palettes.py` has 230 unique outfits, a universal fitted
and close-tailored silhouette direction, 68 performer slots explicitly using
dresses, and no loose/oversized/boxy wording. Reviewer exceptions were folded
back into the catalog: simpler petrol-blue satin paneling, raven-black lace,
a square-neck fitted blazer underlayer, the accepted dusty-mauve wrap knit
top, and other pair-specific A winners.

The requested bust increase is applied at `BUST_VARIANTS`: Small became
Medium and Medium became Large. Existing Full and Large remain unchanged.
Two deterministic Large variants were subsequently promoted into a new tier
above Large. The resulting 500-profile distribution is Medium 52, Full 168,
Large 228, Very Large 52.

Use `run_remote_comfy.sh` for future runs. It opens an SSH tunnel to the remote
ComfyUI API while the existing local runner downloads, hashes, encodes, and
manifests each image directly into the local output directory. Do not restore
the rsync/staged-code workflow.

## Static pilot result

The direct-render pilot is complete at:

- `/mnt/Misc/sd/cover-story/static-performer-pilot-background-blur-v1`;
- codec A/B: `/mnt/Misc/sd/cover-story/static-performer-codec-ab-v1`.

The user selected:

- FilterBypass2 strength 1.5;
- opaque 600x900 AVIF;
- `avifenc -q 60 --speed 6 --yuv 420`;
- no alpha-quality or metadata-ignore arguments.

The six-slot pilot covers 014, 040, 048, 059, 067, and 087. Stronger
background-defocus wording reduced q70/YUV444 size by 13.9%. The selected
q60/YUV420 codec reduced that blurred set by another 26.9%, projecting about
13.8 MiB for 500 images. The codec A/B was approved. Whether to use the
stronger background-defocus wording for all 500 still needs an explicit
production choice.

Production generation, runtime replacement, deletion of the 439 tracked
WebPs, and Git-history rewriting have not started.

## Decision

Ship one opaque 600x900 AVIF per fictional performer, with the environment
generated directly into the portrait. Do not ship transparent performer layers
or separate performer backgrounds.

The direct render is now preferred because:

- subject and environment receive coherent lighting in one generation;
- the performer UI returns to one ordinary image per card;
- no chroma screen, CorridorKey pass, alpha edge, runtime composition, or
  background request is needed;
- representative opaque AVIFs were substantially smaller than the transparent
  layers even before removing the separate background pool.

The superseded layered implementation and all exact approved-source mappings
remain documented in `LAYERED_RUNTIME_HANDOVER_ARCHIVE.md`. Preserve that file
and all existing production outputs; they are useful recovery/provenance, not
the shipping plan.

## Measured size result

Three 600x900 samples were composited with the approved blur and encoded with
the existing AVIF q70 settings:

| Slot | old opaque WebP | transparent AVIF | opaque AVIF sample |
| --- | ---: | ---: | ---: |
| 001 | 35,640 bytes | 50,051 bytes | 25,376 bytes |
| 050 | 30,044 bytes | 37,504 bytes | 20,459 bytes |
| 068 | 26,208 bytes | 35,720 bytes | 17,880 bytes |

The current 439 opaque WebPs total 11.20 MiB. The rejected layered plan would
have shipped 23.36 MiB of performer AVIFs plus 5.79 MiB of backgrounds. The
three opaque AVIF samples average about 21 KiB, suggesting roughly 10–11 MiB
for 500 portraits, but measure the real pilot and full export rather than
treating that estimate as a target.

## Work-package boundary

Handle only the static performer portrait path:

1. implement a resumable direct-generation/export pilot;
2. review and lock the recipe;
3. generate and review the 500 portraits;
4. export the final opaque AVIF catalog;
5. simplify performer runtime back to ordinary image paths;
6. update this handover with final sizes and remaining deployment work.

Do not:

- generate more transparent performers or wide performer backgrounds;
- run CorridorKey;
- overwrite or delete the approved v9 raw, QC, RGBA, or AVIF outputs;
- delete the currently shipped WebPs before the complete static set passes;
- change Viking scene layering or existing theme behavior;
- rewrite Git history;
- clean or reset the dirty worktree.

Use a fresh output directory and a unique label for every pilot/production run.

## Reuse these proven generation choices

The source of truth is `PRODUCTION_EXPANSION` in
`experiment_headshots.py`. It already provides 500 deterministic performer
profiles, seeds, poses, environment prompts, and six balanced crop variants.
Do not build a second identity or style catalog.

Keep:

- native Krea 2 Turbo FP8:
  `krea2_turbo_fp8_scaled.safetensors`;
- `qwen3vl_4b_fp8_scaled.safetensors`;
- `qwen_image_vae.safetensors`;
- workflow `workflows/krea2-turbo-fp8.json`;
- 12 steps, CFG 1, FilterBypass2 strength 1.5;
- no negative prompt;
- deterministic base seed `2026072700`;
- ages 21–34 with `age_wording="band"`;
- one independent prompt-identity bundle and hashed seed per performer;
- the rotated `balanced`, `minimal`, and `natural-prose` face strategies;
- realistic adult proportions, fine pores, natural skin texture, and lifelike
  hair detail;
- the current restrained makeup wording;
- upright posture and direct-gaze pose catalog;
- all six existing crop variants, balanced across the 500 slots.

Avoid:

- the rejected soft-glam makeup wording;
- cheek-dimple prompts;
- longer negative-prompt synonym lists;
- T-Enhancer and stronger filter-bypass values;
- speculative identity frameworks or new dependencies.

## Wardrobe lessons to preserve

Use `performer_palettes.py` directly. It contains one curated palette for every
one of the 230 wardrobe prompts used by `PRODUCTION_EXPANSION`.

For each slot:

1. take `style["wardrobe"]` from `PRODUCTION_EXPANSION`;
2. replace it with `WARDROBE_PALETTES[style["wardrobe"]]`;
3. keep the resulting garment/material/color phrase verbatim.

Keep the current catalog constraints:

- no lower-body or footwear phrases in portrait prompts;
- no hats, beanies, or baseball caps;
- complete garments such as dresses, gowns, suits, rompers, jumpsuits, and
  overalls remain valid;
- coordinated contrasting colors and distinct materials remain for layered
  outfits;
- teal does not need to be restored merely because chroma routing is gone;
  the reviewed palette already works;
- do not reintroduce generic single-color rewriting.

Green/blue adjacency and screen routing are obsolete for static generation.
Delete neither the palette nor its validation; only stop using color to choose
a chroma screen.

## Background and lighting direction

Use each slot's existing `style["background"]` from
`PRODUCTION_EXPANSION`. These 52 prompts already cover:

- softly blurred studios and contemporary interiors;
- homes, libraries, cafes, galleries, offices, and cultural spaces;
- courtyards, streets, parks, rooftops, terraces, waterfronts, and nature;
- broad diffused beauty light, open shade, window light, practical lamps,
  gentle fill, and restrained rim light.

Pass that environment to `generation_prompt()` instead of overriding it with
`SCREEN_BACKGROUNDS`. This is the important lighting change: the model should
render the performer, clothes, background, and light together.

Do not reuse the separate 30 background AVIFs in the final portraits. They were
approved as images, but baking existing cutouts over them preserves the flat
subject-lighting drawback. Keep them archived outside the deployed set.

The background-only foliage A/B found FilterBypass2 strength 1.5 could produce
artificial leaves, but the earlier direct portrait catalog successfully used
environment prompts at that performer setting. Include greenery in the pilot;
change the model recipe only if a controlled same-seed pilot shows a systematic
failure.

## Minimum implementation

Add one small static production runner rather than adding branches throughout
the CorridorKey runner. Reuse:

- `PRODUCTION_EXPANSION` and `generation_prompt()` from
  `experiment_headshots.py`;
- `WARDROBE_PALETTES`;
- the existing Comfy queue/download helper and native workflow;
- atomic manifest writing and hashing patterns already in the tools;
- the existing reviewer;
- the existing AVIF encoder.

The runner should:

- accept `--variant`, `--start`, `--stop`, `--dry-run`, and `--codec-only`;
- be restartable without regenerating completed slots;
- generate one raw PNG per slot into a fresh run directory;
- record slot, prompt, seed, model settings, source hash, AVIF hash, dimensions,
  and byte size in `manifest.json`;
- crop/resize to 600x900 using the existing export behavior;
- encode opaque AVIF at q70, speed 6, stripping EXIF/XMP/ICC;
- fail if an exported AVIF is not 600x900 or unexpectedly has alpha;
- leave raw PNG masters outside the plugin.

Do not add a new prompt framework, workflow, codec dependency, or database.

## Pilot before 500

Generate a small deterministic pilot that covers:

- all six crop variants;
- casual knit/tee, layered casual, professional, dress, evening, and
  alternative wardrobe examples;
- bright and dark interiors;
- outdoor open shade;
- greenery/foliage;
- warm practical light and cool daylight.

Review for:

- face quality and age;
- wardrobe adherence, material detail, and complete garment rendering;
- coherent subject/background light;
- crop consistency and headroom;
- hands or unexpected full-body framing;
- readable text, brands, background people, or distorted architecture;
- AVIF quality and size at normal card and detail-page display sizes.

Use the existing reviewer and shared ratings location:

```sh
python3 -u tools/cover-story/review_headshots.py \
  --root /mnt/Misc/sd/cover-story \
  --reviews /mnt/Misc/sd/cover-story/reviews.json
```

Do not start all 500 until the user approves the pilot.

## Production and asset assembly

After pilot approval:

1. generate slots 001–500 into a new static-production directory;
2. review all 500 and generate targeted replacements only for rejects;
3. preserve an exact source mapping for every accepted slot;
4. export `actor-001.avif` through `actor-500.avif`;
5. update the runtime persona catalog to all 500 accepted profiles;
6. point performer image paths directly at the opaque AVIFs;
7. remove performer-only composition code and CSS while preserving Viking
   scene layering;
8. verify cards, Curator performer references, performer headers, and failure
   behavior;
9. run the Cover Story self-check and full plugin build once.

Keep the old WebPs and the three layered pilot performers/backgrounds until the
complete AVIF set is installed and visually verified. Their later deletion is
a separate, explicit cleanup step.

## Runtime simplification target

The final performer runtime should be boring:

- `performerImage()` returns the selected persona's opaque AVIF path;
- `PerformerCard.Image`, performer headers, and Curator performer references
  use that ordinary image;
- no `performerComposition()`;
- no `PerformerPoster`;
- no performer background allowlist;
- no performer layer failure state;
- no `.cover-story-performer` composition CSS.

Retain all scene-only Viking code, theme definitions, AVIF/WebP `<picture>`
fallbacks, precomposed scene covers, and procedural theme fallback.

## Relevant files

- `tools/cover-story/experiment_headshots.py`
- `tools/cover-story/performer_palettes.py`
- `tools/cover-story/comfy.py`
- `tools/cover-story/review_headshots.py`
- `tools/cover-story/workflows/krea2-turbo-fp8.json`
- the new static runner created by the next work package
- `plugins/cover-story/personas.js`
- `plugins/cover-story/cover-story.js`
- `plugins/cover-story/cover-story.css`
- `plugins/cover-story/test.js`
- `tools/cover-story/LAYERED_RUNTIME_HANDOVER_ARCHIVE.md`

## Completion checks

At minimum:

```sh
python3 NEW_STATIC_RUNNER.py --self-test
python3 tools/cover-story/review_headshots.py --self-test
node plugins/cover-story/test.js
./build_site.sh /tmp/stash-plugins-site
git diff --check
```

Replace `NEW_STATIC_RUNNER.py` with the actual minimal runner path. Record the
pilot directory, production directory, exact final size, review completion,
replacement mapping, and runtime state here before handing off again.
