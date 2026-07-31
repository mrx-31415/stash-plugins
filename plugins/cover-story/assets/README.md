# Cover Story assets

The performer UI uses 500 curated opaque 600×900 AVIF portraits from
`tools/cover-story/personas.json`. Rebuild the browser manifest and images with:

```sh
tools/cover-story/build_assets.sh
```

The source PNGs remain outside the repository; the manifests record their
paths and SHA-256 hashes. Performer portraits use
`avifenc -q 60 --speed 6 --yuv 420`.

The Viking theme is the scene-cover vertical slice: four backgrounds and two
performers in left/right poses are stacked at runtime. Layers are exported as
AVIF q70 with WebP fallbacks; alpha is lossless in both formats. Eight
title-free precomposed WebP covers remain as load-failure fallbacks. Other
themes continue to use procedural posters until their asset packs pass the same
UI review. Scene source provenance and fallback composition are in
`tools/cover-story/scene-assets.json`; runtime placement metadata is in
`plugins/cover-story/themes.js`.

New portrait runs avoid age wording in image prompts. Reviewers record an
apparent age from the finished portrait; curation uses that value for the fake
birthdate while retaining the original intended age in generation provenance.

## Remaining production asset pack

Open [`prompt-generator.html`](prompt-generator.html) in a browser to create and
copy complete themed 2×2 actor-sheet prompts.

Generate and curate the following WebP assets when expanding beyond the Viking
pilot.

## Shared direction

- Cinematic editorial photography with restrained film grading.
- Workplace-safe PG: no sexuality, gore, weapons, drugs, brands, watermarks,
  or baked-in text.
- Consistent 50 mm-equivalent lens, eye-level camera, soft directional light,
  realistic proportions, and generous separation between subjects.

## Actor packs

Select identities from the approved performer pool. Use each portrait as the
identity reference for two 1200×1800 three-quarter or full-body poses: one
facing slightly left and one slightly right. Generate poses on a flat chroma-key
background with no cast shadow, reflection, loose props, or cropped limbs.
Export alpha WebP after edge, hair, and color-spill inspection.

Generate one pose per image. The older 2×2 prompt generator remains available
as a fallback if individual identity edits drift.

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

## Foreground overlays

Create 1920×1080 alpha WebP overlays matching the same camera/light families:
window edge, curtain, foliage, table edge, shelving, doorway, practical lamp,
car interior edge, soft flare, rain-on-glass, subtle haze, and neutral studio
equipment. Keep shadows within the overlay and reject glass/hair edges that do
not matte cleanly.

Foregrounds are optional and were deliberately omitted from the Viking pilot.

## Acceptance checklist

- Identity matches across each portrait and pose pair.
- Perspective, horizon, light direction, and color temperature match a declared
  set family.
- Transparent corners are fully clear with no chroma fringe.
- Subject remains recognizable at card size.
- No text, watermark, brand, unsafe content, or resemblance to a public figure.
- Total compressed plugin asset budget remains between 40 and 60 MB.
