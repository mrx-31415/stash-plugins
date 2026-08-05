#!/usr/bin/env python3
"""The one combination not yet tested cleanly: reference-blanking alone, standard (uncontrolled)
preprocess, standard-width outline. Isolates whether the thigh-gap line and undersized chest seen
in the canny-preprocess run come from the reference-blanking fix or from canny-preprocess itself.

Reuses the aligned performer already on disk from the b-thick-outline experiment
(performer-aligned.png, built from the original uncontrolled preprocess before it was deleted) so
this costs one identity edit, not a full preprocess+align+edit chain."""

from pathlib import Path

import layered_costume_production as production
import run_ghost_foot_comparison as ghost
import run_qwen2512_skin_head_clothes_poc as poc

ALIGNED = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-ghost-foot-comparison/b-thick-outline/performer-aligned.png")
CARRIER = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-skin-head-clothes-poc-v3/carrier.png")
SKIN = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-skin-head-clothes-poc-v3/skin-tone.png")
ROOT = Path("/mnt/Misc/sd/cover-story/cover-story-qwen2512-ghost-foot-comparison/c-refblank-only")


def main():
    config = poc.load_config(poc.CONFIG_PATH)
    server = config["server"]
    production.RUN_ID = poc.POC_RUN_ID
    production.EDIT_MODEL = config["edit_model"]
    ROOT.mkdir(parents=True, exist_ok=True)
    work = ROOT / "_work"
    work.mkdir(exist_ok=True)

    identity = ghost.run_identity(server, ALIGNED, CARRIER, SKIN, ROOT, work, False,
                                  outline_width=None, label="-c")
    poc.soft_free(server)

    box = production.silhouette_box(identity)
    carrier_box = production.silhouette_box(CARRIER)
    drift = {"left": abs(box[0] - carrier_box[0]), "right": abs(box[2] - carrier_box[2]),
             "bottom": abs(box[3] - carrier_box[3])}
    crop = ghost.crop_feet(identity, ROOT / "feet-crop.png")
    print(f"C (reference-blank only)  body drift l/r/b {list(drift.values())}  "
          f"worst {max(drift.values())}  crop -> {crop}")
    print(f"wrote {identity}")


if __name__ == "__main__":
    main()
