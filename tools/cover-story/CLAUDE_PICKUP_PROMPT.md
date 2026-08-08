# Claude pickup prompt — Cover Story layered-costume pipeline

Paste this to hand the work to Claude. It is self-contained; the handover it references has the
full detail.

---

You are picking up the Cover Story layered-costume asset pipeline in the stash-plugins repository
at /home/johan/tools/stash-plugins. Read FIRST, in order:

1. `tools/cover-story/CLAUDE_PICKUP_HANDOVER.md` (current state, verified, written 2026-08-07)
2. `tools/cover-story/VITON_CLOTHING_POC_HANDOVER.md` (PoC spec, session results, verified model download links)
3. `tools/cover-story/LAYERED_COSTUME_PIPELINE_REFERENCE.md` (the approved production recipe/layer contract)

Then run the self-tests and confirm they pass:
- `python3 tools/cover-story/run_qwen2512_skin_head_clothes_poc.py --self-test`
- `python3 tools/cover-story/layered_costume_production.py self-test`

CONTEXT: We pre-generate composited cover layers (background → skin → clothed-body → hair) for a
Stash plugin, rendered by a staged PoC runner against a rented RunPod (ComfyUI 0.30.2, Qwen 2512 +
Qwen Edit 2511 + SAM3, all models on the persistent /workspace volume). The strongest working
recipe, fully verified end-to-end, is: chroma carrier (green paint on blue) + deterministic head
transplant identity + masked Qwen Edit clothes conditioned on a WORN garment reference image
(`--garment-worn`) + LOCAL BiRefNet matting (`--extract-mode birefnet`, no pod/CorridorKey needed
for extraction). Two completed runs with review pages exist on the NFS drive
(/mnt/Misc/sd/cover-story/); the chroma one is good, the grey-carrier experiment is shelved.

STATUS: tasks 1 and 4 below are DONE/OBSOLETE as of 2026-08-08 — see the "Visual review,
2026-08-08" section at the bottom of CLAUDE_PICKUP_HANDOVER.md, which supersedes several claims in
this prompt. Claude reads images directly with the Read tool, so there is no image-blind operator
and nothing to wire in. The current agreed plan is Phase 1 in that section.

TASKS, in priority order:

1. [DONE 2026-08-08] Reviewed. The chroma-garment-ref run is NOT the strongest variant — it is
   visibly worse than the v3 baseline: flat featureless dress, no collar/gloves/shoes, plus a blue
   hair halo and green dress fringe. Findings and measurements are in the handover.

2. Add the two missing automated visual-proxy gates (the session proved numeric checks must cover
   appearance, not just mechanics): (a) face-vs-body skin-tone consistency on the composite, and
   (b) edge fringe/halo detection around the figure. Both are cheap PIL computations; wire them
   into the composite stage with thresholds that have teeth (like `no_skin_in_garment_region`,
   which catches a real bug at 15.7% and passes the fix at 3.6%).

3. If a human verdict says the recipe is acceptable, run the second-outfit stress test on the
   next pod: the SAME recipe on the Viking tunic (catalog: viking/outfit-01, rust-brown tunic).
   New output dir under /mnt/Misc/sd/cover-story/. The pod is OFF; repoint
   ~/.config/cover-story/instance.json (mode 600, config beats env) to the new host/port the
   user provides, run preflight (it repairs the pod via pod_bootstrap.sh), then the bulk wrapper:
   `tools/cover-story/run_poc_bulk.sh --output-dir ...` (add `--garment-worn` — it is already
   defaulted in the wrapper? CHECK: the wrapper hardcodes the grey recipe flags; adapt it for the
   chroma recipe or invoke the runner directly).

4. [OBSOLETE] Wiring in a vision model is unnecessary — Claude reads PNGs directly via the Read
   tool. Just look at the composite and the closeups.

KNOWN PITFALLS (all cost GPU cycles last session — do not reintroduce):
- The birefnet extract aperture must be key_aperture() (chroma) or the SAM head-stop mask (grey),
  NEVER the dilated identity envelope — that punched a hole through the dress bodice.
- screen_foreground() calls must be threaded with the right screen; hardcoded "blue" on a
  non-blue plate misreads the whole canvas as figure.
- Flat-lay garment references fail full-body coverage; always use the worn-garment variant.
- ComfyUI 0.30.x requires comfy-kitchen==0.2.26 (bootstrap step 6b enforces it).
- The runner is Pillow-only (no numpy); the matting venv is .venv-birefnet.

Do NOT commit models/ or the venv. The session's code changes are uncommitted — review `git
status` and commit the tooling + docs deliberately. Keep the deterministic identity construction
untouched — it is the one proven part (0/0/0 px registration).
