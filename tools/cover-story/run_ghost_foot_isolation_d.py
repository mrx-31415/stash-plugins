#!/usr/bin/env python3
"""Last isolation: canny-guided preprocess alone, reference-blanking removed. Reuses the aligned
performer already on disk from the a-canny-preprocess experiment, and calls body_masks() only for
its repaint/preserve masks -- the edit source is the *unblanked* aligned image, not the reference it
also returns, isolating canny-preprocess's own effect from reference-blanking's."""

from pathlib import Path

import layered_costume_production as production
import run_ghost_foot_comparison as ghost
import run_qwen2512_skin_head_clothes_poc as poc

ALIGNED = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-ghost-foot-comparison/a-canny-preprocess/performer-aligned.png")
CARRIER = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-skin-head-clothes-poc-v3/carrier.png")
SKIN = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-skin-head-clothes-poc-v3/skin-tone.png")
ROOT = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-ghost-foot-comparison/d-cannypre-only")


def main():
    config = poc.load_config(poc.CONFIG_PATH)
    server = config["server"]
    production.RUN_ID = poc.POC_RUN_ID
    production.EDIT_MODEL = config["edit_model"]
    ROOT.mkdir(parents=True, exist_ok=True)
    work = ROOT / "_work"
    work.mkdir(exist_ok=True)

    body_mask, preserved_head, _unused_reference = poc.body_masks(server, ALIGNED, CARRIER, ROOT, work)
    control = poc.identity_control(server, poc.IDENTITY_CONTROL, CARRIER, preserved_head, ROOT, work, False)
    identity = ROOT / "identity.png"
    print("[identity-d] generating (source = unblanked aligned image)", flush=True)
    poc.edit(server, ALIGNED, poc.IDENTITY_PROMPT, body_mask, SKIN,
             production.seed_for("qwen2512:identity-body"), "cover-story/ghost-foot/identity-d",
             identity, work, False, control=control, control_type=poc.IDENTITY_CONTROL,
             control_strength=production.CONTROL_STRENGTH)
    poc.soft_free(server)

    box = production.silhouette_box(identity)
    carrier_box = production.silhouette_box(CARRIER)
    drift = {"left": abs(box[0] - carrier_box[0]), "right": abs(box[2] - carrier_box[2]),
             "bottom": abs(box[3] - carrier_box[3])}
    crop = ghost.crop_feet(identity, ROOT / "feet-crop.png")
    print(f"D (canny-preprocess alone)  body drift l/r/b {list(drift.values())}  "
          f"worst {max(drift.values())}  crop -> {crop}")
    print(f"wrote {identity}")


if __name__ == "__main__":
    main()
