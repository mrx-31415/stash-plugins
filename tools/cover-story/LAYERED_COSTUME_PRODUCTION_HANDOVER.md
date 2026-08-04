# Cover Story layered costume production handover

Updated 2026-08-01 after the face/clothes/hair PoC review and production-design interview.

## Objective

Build a manually launched, resumable offline asset factory for Cover Story. It
pre-generates registered performer, clothing-body, and background layers. The
plugin later chooses approved assets deterministically and stacks them in the
browser. Generation never runs inside Stash and never writes to Stash.

Do not modify the plugin until the pilot gate in this document passes visual
review.

## Approved product scope

- 20 approved fictional performers from the existing performer catalog.
- Four pose families:
  1. standing, facing slightly left;
  2. standing, facing slightly right;
  3. centered frontal standing pose;
  4. standing and pointing outside the frame with one empty hand.
- If pointing repeatedly produces bad hands, replace pose 4 with a natural
  empty-hand, open-palm conversational gesture. Do not add a held prop.
- Four themes: Viking, Film Noir, Victorian, and Space.
- Four independent outfit designs for every theme and pose. Designs do not
  need to correspond between poses: `4 themes x 4 poses x 4 outfits = 64`
  clothed-body designs.
- Four backgrounds per theme: 16 total. Each background declares which of
  `left`, `right`, `center`, and `duo` it supports.
- Two-actor covers use only the left- and right-facing standing poses. The
  actors need not look as though they interacted during generation.
- Eight precomposed fallback covers per theme: 32 total.
- Above-shoulder or clean front-draped hair only. No headgear and no hairstyle
  that needs separate rear and front layers in v1.

This yields 80 performer-pose sets and 64 clothing designs, or 1,280 approved
performer/outfit combinations before background selection.

## Locked layer contract

Use this exact order:

```text
background
-> performer face/ears/neck/upper-chest skin plate
-> complete clothed-body plate
-> performer hair plate
```

Do not build a full-body performer skin layer. The complete clothed-body plate
owns the body geometry, clothing, hands, exposed arms or legs, gloves, and
footwear. This deliberately avoids transparent holes caused by imperfect
clothing segmentation.

The performer owns only face identity, ears, neck, upper chest through the
upper sternum, and hair. Apparent body shape may vary between poses and
outfits. Registration at the head aperture and neckline is still mandatory.

Every clothing design with visible natural skin has three reviewed variants:
`light`, `medium`, and `dark`. Assign every performer to one of those groups
manually in the catalog; do not infer it at runtime. A fully covered design may
store one body image and map all three tone keys to the same file.

The head/neck/upper-chest aperture of every clothed-body plate is transparent.
Natural skin elsewhere remains opaque and belongs to the selected tone
variant. The skin plate must extend beneath the deepest allowed neckline with
overlap; do not butt two masks together.

## PoC source of truth

Preserve the standalone experiment unchanged at:

```text
/mnt/Misc/sd/cover-story/head-clothes-decomposition-poc-v1
```

Its `manifest.json` status is
`blue_matrix_passed_visual_review_runtime_not_started`. Its README contains
older carrier-stop wording and is not the final status. The accepted review
artifacts and manifest are authoritative.

The important controls are:

- `references/control-identity-00038.png`: identity-transfer control;
- `references/control-face-00039.png`: face/skin extraction control;
- `references/control-hair-00040.png`: hair extraction control;
- `review/blue-matrix-identities.png`;
- `review/blue-matrix-clothing.png` and its revisions;
- `review/hardest-composite-layers.png`;
- `review/hardest-composite-closeups.png`.

The accepted lesson is not “blue instead of green.” Blue and green are
interchangeable key colors selected per asset. The successful technique is to
use a short Qwen edit to turn only the unwanted region into the selected key
color, then use CorridorKey with a soft SAM3+dilation hint. SAM is a hint, not
the final hard edge.

For footwear prompts that need SAM, request `left shoe` and `right shoe`
separately. The same rule applies to left/right hands. A generic `shoe` prompt
often returns only the best-matching shoe.

## Generation settings to preserve

Keep the metadata-proven edit graph fixed while establishing production:

- `qwen_image_edit_2511_int8_convrot.safetensors`;
- `qwen_2.5_vl_7b_fp8_scaled.safetensors`;
- `qwen_image_vae.safetensors`;
- AuraFlow shift 3.1;
- CFGNorm 1;
- 40 steps, CFG 4, Euler/simple, denoise 1;
- `index_timestep_zero` reference latents;
- Lightning disabled.

Carrier generation uses Qwen Image 2512 at 1328 square, 50 steps, CFG 4. Save
the workflow's 1024-square scaled output as the canonical editable source. All
editable assets stay on a 1024-square canvas. Crop/resize only complete final
composites to the 600x900 Stash card size.

Use short prompts. Do not add long anatomical checklists after a failure. Make
one targeted prompt change while model, sampler, steps, CFG, reference order,
and all other settings remain fixed.

The runner must record the exact prompt rather than reconstructing it from the
current catalog. Prompts, negative prompts, reference order, seed, workflow
settings, model filenames, and input/output hashes are immutable attempt
provenance.

## Experimental Qwen 2512 skin/head/clothes pickup PoC

This is an isolated technical probe, not a change to the locked production
contract above. It deliberately tests a simpler aligned-body recipe before any
promotion of v20d:

1. Generate one fresh 832x1248 Qwen Image 2512 carrier: matte green person on
   a matte blue screen.
2. Run SAM3 `person` and a head/face/hair/neck/clavicles/shoulders/upper-chest
   prompt. The person mask is the full-body skin-edit mask; the head result is
   dilated and used only for the identity edit.
3. Recolor the entire visible carrier person, including the head, to natural
   skin while preserving carrier geometry.
4. Transfer performer identity only inside the dilated head mask, keeping the
   recolored body and carrier pose unchanged.
5. Generate clothing independently from the untouched carrier using its body
   edit envelope; do not condition clothing on the generated skin plate.
6. Run the blue CorridorKey path for each resulting plate, retain only its
   alpha, and apply that alpha to the original plate RGB. Do not use
   CorridorKey's returned RGBA RGB as the layer source.
7. Composite background → skin → hair → clothes, allowing the clothing plate
   to cover the identity shoulder/hair underlap.

For the carrier's CorridorKey hint, use a traditional Pillow chroma estimate
instead of asking SAM to find the person. The blue background is detected by
blue-channel dominance over red/green, lightly cleaned and softened, then
inverted to a foreground hint. This hint is only guidance; CorridorKey still
returns the final alpha, and the original plate RGB remains the composited
source. Keep SAM for the edit masks and the optional hair layering split.

The resumable runner is:

```text
tools/cover-story/run_qwen2512_skin_head_clothes_poc.py
```

Run it only after the remote ComfyUI service has been restarted and the
connection variables are set; the runner never restarts ComfyUI itself:

```bash
export COMFY_SERVER='http://HOST:PORT/?token=REDACTED'
export COVER_STORY_SSH_TARGET='root@HOST'
export COVER_STORY_SSH_PORT='SSH_PORT'
python3 tools/cover-story/run_qwen2512_skin_head_clothes_poc.py
```

The default output is the writable
`/tmp/cover-story-qwen2512-skin-head-clothes-poc`; pass `--output-dir` to
resume elsewhere. `--force` regenerates completed stages. The runner uses
ComfyUI's `/free` endpoint between large model phases and stores standalone
CorridorKey work under its own `qwen2512-skin-head-clothes-poc` remote cache
namespace. It does not modify the v20d output. Run
`--self-test` before reconnecting to a server. Keep all results experimental
until visual review confirms neckline, shoulders, hair spill, identity,
registration, and garment silhouette.

RunPod's public ComfyUI proxy may return HTTP 403/error 1010 for Python's
default `urllib` user-agent even though the UI and curl are reachable. The
shared `comfy.py` client therefore sends an explicit `CoverStoryComfy/1.0`
user-agent on API, upload, and image-download requests.

When using a direct RunPod SSH endpoint, the standalone CorridorKey transfer
must use a connection that supports non-interactive commands and rsync. The
runner disables rsync owner/group preservation because RunPod's `/workspace`
mount rejects those metadata updates.

If the standalone package is installed below another workspace root, set
`COVER_STORY_CORRIDORKEY_ROOT`; the RunPod setup used during this probe is
`/workspace/runpod-slim/CorridorKey`.

The PoC defaults to the v20d edit checkpoint but accepts
`COVER_STORY_EDIT_MODEL` for disposable environments. The RunPod setup used
here exposes `qwen_image_edit_fp8_e4m3fn.safetensors` instead of the v20d
2511 checkpoint, so set that override before resuming.

## Asset pipeline

### 1. Catalog

Store stable IDs and free-form human-written descriptions for performers,
poses, themes, outfits, and backgrounds. At minimum record:

- performer source path/hash and manual skin-tone group;
- pose ID and carrier source/hash;
- theme and outfit description;
- stable asset ID;
- every generation attempt and its seed;
- key color chosen for each extraction;
- review state and reviewer note;
- the accepted attempt pointer.

An accepted asset may later point to a newer accepted attempt without changing
its stable runtime ID. Never overwrite or discard the old attempt.

### 2. Carriers

Generate and review one canonical carrier for each pose, then freeze it. A
carrier defines canvas, head aperture, pose, body placement, crop, and contact
with the floor. The left/right carriers must be usable together in a duo.

The production carrier prompt should remain as short as the accepted PoC
prompt. “Green shoes” or “green stockings” is sufficient; do not require
Qwen to reason about “fully visible feet” when the full-body framing already
provides that constraint.

### 3. Performer preprocessing and transfer

For every performer and pose:

1. preprocess the approved performer portrait with a single-reference Qwen
   edit when necessary to expose a compatible deep neckline and clean hair
   outline;
2. transfer the performer head, neck, visible upper chest, and hair onto the
   frozen carrier geometry;
3. make one derivative in which only hair is the chosen key color;
4. make one derivative in which only face, ears, neck, clavicles, and visible
   upper-chest skin are the chosen key color;
5. extract the skin and hair plates with CorridorKey, using SAM3+dilation only
   as an alpha hint.

Prefer one reference per edit. The PoC found multi-reference instruction
following unreliable. If an edit genuinely needs two references, keep carrier
as image 1 and performer as image 2 and record that order.

Identity matching is best effort. Human review rejects obvious identity or
quality failures, but production does not need an automated face-similarity
score.

### 4. Clothing bodies

Generate a complete body from the matching pose carrier using the free-form
outfit description. The result includes clothing, hands or exposed limbs,
gloves, and footwear. Turn only the head/neck/upper-chest aperture and external
background into the selected key color, then key those regions away. Do not
segment the garment itself to transparency.

For clothing with exposed skin, produce light, medium, and dark variants. Small
model differences between tone variants are accepted. Each variant must still
pass registration and visual review independently.

If the outfit contains substantial blue, prefer green keying. If it contains
substantial green, prefer blue keying. Choose the lower-conflict color using a
simple pixel-count check over the intended foreground, record the decision,
and let the reviewer reject ambiguous cases. Do not introduce another key
model in v1.

### 5. Backgrounds and fallbacks

Generate four empty 1920x1080 backgrounds per theme with declared layout
support. Reject people, text, recognizable brands, or hard foreground objects
that prevent actor placement.

After the asset pack passes, create eight representative precomposed WebP
fallback covers per theme. These are load-failure fallbacks, not the primary
runtime path.

## Seeds, retries, and resumability

- Start with one deterministic seed per requested asset.
- If an automatic check fails, automatically try at most two later deterministic
  seeds.
- A human rejection may be rerun explicitly with the next deterministic seed.
- Never regenerate an accepted attempt implicitly.
- A rerun skips files whose recorded hashes and settings still match.
- Refuse a nonempty output directory that has no compatible manifest.
- Write manifests atomically after every completed attempt.
- Record rejected and interrupted attempts; do not record a partial output as
  accepted.

Use `/mnt/Misc/sd/cover-story/layered-costume-production-v1` as the initial
local output root. Give every revised recipe a new run ID or directory rather
than using `--force` over reviewed work.

## Automatic checks

Automatic checks should reject obvious technical failures, not pretend to
judge aesthetics:

- expected file exists, decodes, has the expected mode and dimensions;
- source and output hashes match the manifest;
- translation from the frozen carrier is at most 2 px at 1024 square;
- scale change is at most 0.5%;
- no manual scaling correction of performer layers;
- masks are neither empty nor implausibly large;
- expected connected regions exist where applicable;
- left and right hands/shoes are present when the design exposes them;
- the head aperture is transparent and the clothed body is otherwise complete;
- the skin plate backs the deepest neckline with a small overlap;
- no green/blue key residue remains in a full-size composite or its 600x900
  card crop;
- hair contains no eyes, brows, lips, earrings, or facial-shadow ghosts;
- no alpha holes appear through the clothed body.

Do not automatically enforce face identity, historical costume quality,
fashion coherence, or plausible hair depth. Those remain human decisions.

## Human review contract

Review individual performer-pose sets and individual clothing-tone variants.
A family becomes ready only when every required member is accepted.

The review output must show:

- the complete final composite at 1024 square and 600x900 card size;
- checkerboard previews of every raw alpha layer;
- toggles for background, skin, body/clothing, and hair;
- face/hair and neckline close-ups;
- hands and shoes in the full composite (dedicated close-ups are optional
  because they belong to the opaque clothed-body plate);
- automatic check results, prompt, seed, key color, and reviewer note.

States are `pending`, `accepted`, and `rejected`. Rejection requires a short
reason. Reuse the existing Cover Story static HTML/reviewer conventions; do
not build a second review application.

## Pilot gate

Before generating the complete catalog, run:

- four performers spanning light, medium, and dark skin and varied approved
  hair silhouettes;
- all four poses;
- one clothing design from each theme for every pose, including all required
  skin-tone variants;
- all 64 performer/theme/pose composites;
- representative solo and duo backgrounds;
- full-size and 600x900 review.

Stop and revise the offline workflow if any pose cannot pass across the four
pilot performers, any theme cannot provide a passing outfit for every pose,
or duo registration is visibly wrong. Do not change the plugin to accommodate
a failing asset recipe.

After the pilot passes, generate the remaining 20-performer/64-outfit catalog.
Measure compressed AVIF/WebP size from the pilot before setting a package-size
limit. Do not treat the older 40-60 MiB guideline as a hard gate until measured.

## Minimal repository implementation

Promote only the accepted workflow into `tools/cover-story`. The standalone
PoC remains evidence, not an imported Python package.

Prefer one Python runner with phases such as `generate`, `extract`, `compose`,
`review`, `export`, and `self-test`, plus one JSON catalog. Reuse:

- `tools/cover-story/comfy.py` for ComfyUI API submission and downloads;
- the existing atomic manifest/hash patterns in the performer runners;
- `tools/cover-story/export_scene_assets.py` for AVIF/WebP encoding and
  fallback composition;
- the existing review HTML conventions.

Use the sibling `../comfy-bootstrap` repository to declare the exact editable
Comfy workflows, model files, CorridorKeyBlue model, and custom-node
dependencies needed by a disposable Vast instance. Never store the Comfy URL
token, SSH credentials, or Hugging Face token in either repository.

The CorridorKeyBlue model source supplied for bootstrap is:

```text
https://huggingface.co/nikopueringer/CorridorKeyBlue_1.0/resolve/main/CorridorKeyBlue_1.0.safetensors
```

CorridorKey has two separately packaged checkpoints. Do not rename one format
and use it as the other:

- the accepted standalone CorridorKey backend uses
  `/workspace/CorridorKey/CorridorKeyModule/checkpoints/CorridorKeyBlue_1.0.safetensors`;
- the ComfyUI `ComfyUI-CorridorKey` node uses
  `/workspace/ComfyUI/custom_nodes/ComfyUI-CorridorKey/models/CorridorKeyBlue_1.0.pth`.

The sibling bootstrap manifest already declares the green and blue ComfyUI
`.pth` models, but they are currently linked to a Krea workflow. Add one
Cover Story production workflow that links only the required Qwen 2512, Qwen
Edit 2511, SAM3, CorridorKey green/blue, and custom-node dependencies. Do not
download unrelated Krea models merely to install CorridorKey.

## Disposable Vast connection

Supply connection details as environment variables for each rental. Never put
an instance address or tokenized public Comfy URL in this document, a catalog,
or a manifest.

```bash
export COVER_STORY_SSH_TARGET='root@HOST'
export COVER_STORY_SSH_PORT='SSH_PORT'
export COVER_STORY_COMFY_REMOTE_PORT='REMOTE_COMFY_PORT'
# Optional: change only if local port 8080 is already occupied.
export COVER_STORY_COMFY_LOCAL_PORT='8080'
```

The local port is not a local ComfyUI server. It is only the listening end of
the SSH forward. If it is omitted, the runner should default it to `8080`.

Open a shell with:

```bash
ssh -p "$COVER_STORY_SSH_PORT" "$COVER_STORY_SSH_TARGET"
```

Use SSH for cloning/syncing `comfy-bootstrap`, installing the standalone
CorridorKey package and checkpoint, restarting ComfyUI, and remote diagnosis.
Prefer a resumable bootstrap-managed download. If the standalone package is
not represented in bootstrap yet, its blue checkpoint belongs at the exact
`.safetensors` path above.

For Comfy API access, keep this tunnel running in a separate terminal:

```bash
ssh -N \
  -p "$COVER_STORY_SSH_PORT" \
  -L "$COVER_STORY_COMFY_LOCAL_PORT:localhost:$COVER_STORY_COMFY_REMOTE_PORT" \
  "$COVER_STORY_SSH_TARGET"
```

Then launch the local production runner. It talks through the tunnel; no local
ComfyUI installation or server is required:

```bash
export COMFY_SERVER="http://127.0.0.1:$COVER_STORY_COMFY_LOCAL_PORT"
python3 tools/cover-story/BUILD_RUNNER_NAME.py PHASE --server "$COMFY_SERVER"
```

The production runner is `tools/cover-story/layered_costume_production.py`. It
also accepts `COMFY_SERVER` when `--server` is omitted.

After dependency setup and a ComfyUI restart, verify that the API exposes
`/object_info/CorridorKey` and `/object_info/SAM3_Detect` before queueing an
expensive generation. The API URL runs workflows; it cannot install custom
nodes or checkpoints, which is why SSH access is also required.

GPU work may be serialized through one queue. Parallel GPU scheduling and a
service daemon are out of scope until measured production throughput requires
them.

## Runtime handoff after the gate

The existing plugin already performs deterministic theme/background/actor
selection and uses precomposed fallbacks. Extend that mechanism only after the
asset gate passes:

1. choose theme, background, layout, pose, performer, and outfit
   deterministically from the scene ID and browser seed;
2. map the performer to the manually assigned skin-tone clothing variant;
3. stack the registered skin, clothed-body, and hair layers at one actor
   placement;
4. use left/right pose pairs for duo layouts;
5. keep the selection stable between visits until the Cover Story browser seed
   is reset;
6. use the theme's precomposed cover if any layer fails to load.

The runtime remains read-only. It creates no Stash image candidate, performs
no GraphQL mutation, and does not replace a performer or scene cover.

## Completion criteria

The production handoff is complete when:

- all four carriers are frozen and hashed;
- all 80 performer-pose sets are accepted;
- all 64 clothing designs and every required tone variant are accepted;
- all 16 backgrounds and 32 fallbacks are accepted;
- manifests contain immutable attempt provenance and stable accepted pointers;
- the asset exporter reproduces plugin files from approved sources;
- the measured package size is documented;
- the plugin runtime path and its existing tests pass after integration;
- Viking, Film Noir, Victorian, and Space each pass solo, duo, full-resolution,
  and 600x900 visual review.
