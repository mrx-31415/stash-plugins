# Layered costume asset-generation reference

This is the approved layered-costume extraction workflow. It is different from
generating clothing-only overlays.

The authoritative production handoff is
`LAYERED_COSTUME_PRODUCTION_HANDOVER.md`. The reviewed PoC is:

```text
/mnt/Misc/sd/cover-story/head-clothes-decomposition-poc-v1
```

An isolated follow-up probe is documented separately in
`LAYERED_COSTUME_PRODUCTION_HANDOVER.md` and
`LAYERED_COSTUME_PRODUCTION_STATUS.md`. The runner
`run_qwen2512_skin_head_clothes_poc.py` tests full-body carrier skin recolor,
loose head identity transfer, and independent clothing generation. It is
experimental and must not be mistaken for, or promoted over, this approved
production recipe without visual review.

## 1. Freeze pose carriers

Generate one canonical carrier per pose on an 832×1248 canvas — 2:3, matching
the 1024×1536 source portraits and the 600×900 card, so the card crop is a
scale rather than a choice about what to discard. The carrier
defines the head aperture, pose, body placement, floor contact, and scale.
Freeze it before generating performer or costume variants.

Keep a green and blue key variant for each frozen pose. Generate only the
green geometry; derive the blue variant deterministically by swapping its
green and blue channels. The variants must have identical geometry and hashes
recorded against the same accepted carrier.

## 2. Extract performer skin and hair

For each approved performer:

1. Use Qwen edit to transfer the performer identity onto the frozen carrier.
2. Use SAM3 to recolor the existing hair deterministically to the selected key
   color without changing its silhouette.
3. Recolor the existing face, ears, neck, clavicles, and upper chest through
   its SAM3 mask without changing geometry.
4. Use SAM3 plus dilation as a soft alpha hint.
5. Run CorridorKey to extract the hair plate and the face/skin/neck/upper-chest
   plate.

## 3. Extract the complete clothed-body plate

For each pose and outfit:

1. Select the frozen green or blue carrier before generation according to the
   outfit's key-color conflicts.
2. Generate a complete clothed body from that carrier with a mask covering the
   person minus the locked head/neck/upper-chest aperture.
3. Keep clothing, hands, exposed limbs, gloves, and footwear in this plate.
4. Do not try to segment the clothing itself into a clothing-only overlay.
5. Keep the carrier background and head aperture static; do not rekey them
   after outfit generation.
6. Use SAM3 plus dilation only as CorridorKey's broad foreground hint. The
   final alpha is CorridorKey's output; never intersect it with the SAM mask.

The result is an opaque clothed-body plate with a transparent head, neck, and
upper-chest opening.

## 4. Choose the key color per asset

Green and blue are interchangeable:

- use blue when the outfit contains substantial green;
- use green when the outfit contains substantial blue;
- otherwise choose the color with the least foreground conflict.

Choose the carrier variant before the outfit edit. Do not swap background key
colors afterward. The matching CorridorKey model must be used for the selected
color.

## 5. Composite order

```text
background
→ performer face/skin/neck plate
→ complete clothed-body plate
→ performer hair plate
```

The clothed-body plate owns body geometry, hands, arms, legs, clothing, and
footwear. The performer layers own identity regions and hair. Do not build a
full-body performer skin layer.

## 6. Review checks

Reject assets with:

- bad head-aperture or neckline registration;
- eyes, lips, earrings, or facial shadows in the hair plate;
- alpha holes through the clothed body;
- missing hands or shoes;
- green/blue residue;
- translation greater than 2 px or scale change greater than 0.5% on the
  canonical canvas, measured per image and across performers on one pose;
- obvious identity or visual-quality failures.

Review both the full 832×1248 composite and the final 600×900 card crop.

## Important preflight

Before expensive generation, verify that the Comfy endpoint exposes both
`CorridorKey` and `SAM3_Detect`. If CorridorKey is absent, use the standalone
CorridorKey route; do not substitute SAM3 as the final matte.
