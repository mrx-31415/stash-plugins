# Cover Story production asset pack

Open [`prompt-generator.html`](prompt-generator.html) in a browser to create and
copy complete themed 2×2 actor-sheet prompts.

The runtime currently uses procedural SVG stand-ins. Generate and curate the
following WebP assets before wiring the production pack into the manifest.

## Shared direction

- Cinematic editorial photography with restrained film grading.
- Workplace-safe PG: no sexuality, gore, weapons, drugs, brands, watermarks,
  or baked-in text.
- Consistent 50 mm-equivalent lens, eye-level camera, soft directional light,
  realistic proportions, and generous separation between subjects.

## Actor packs (20)

For each `actor-01` through `actor-20`, first approve an 800×1200 portrait.
Use that portrait as the identity reference for two 1200×1800 three-quarter or
full-body poses: one facing slightly left and one slightly right. Generate poses
on a flat chroma-key background with no cast shadow, reflection, loose props, or
cropped limbs. Export alpha WebP after edge, hair, and color-spill inspection.

Prompt skeleton:

```text
Use case: photorealistic-natural
Asset type: reusable fictional film-cast character
Primary request: create a workplace-safe cinematic editorial portrait of a fictional adult actor
Style/medium: realistic photography with subtle film grading
Composition/framing: [portrait / full-body three-quarter pose], eye-level 50 mm lens
Lighting/mood: soft directional studio light, neutral and approachable
Constraints: preserve the supplied fictional identity exactly; plain contemporary clothing; no logo, text, watermark, provocative pose, or real public figure
```

## Sets (12)

Create empty 1920×1080 environments in three camera families: centered,
left-leading, and right-leading. Produce daylight, warm-interior, and cool-night
lighting variants. Leave marked visual space for one subject on either side and
two subjects near center; do not include recognizable brands, people, text, or
hard foreground obstructions.

## Foreground overlays (12)

Create 1920×1080 alpha WebP overlays matching the same camera/light families:
window edge, curtain, foliage, table edge, shelving, doorway, practical lamp,
car interior edge, soft flare, rain-on-glass, subtle haze, and neutral studio
equipment. Keep shadows within the overlay and reject glass/hair edges that do
not matte cleanly.

## Acceptance checklist

- Identity matches across each portrait and pose pair.
- Perspective, horizon, light direction, and color temperature match a declared
  set family.
- Transparent corners are fully clear with no chroma fringe.
- Subject remains recognizable at card size.
- No text, watermark, brand, unsafe content, or resemblance to a public figure.
- Total compressed plugin asset budget remains between 40 and 60 MB.
