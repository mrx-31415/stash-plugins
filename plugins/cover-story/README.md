# Cover Story

Cover Story disguises Stash as a fictional workplace-safe film collection. It
replaces visible titles, people, studios, labels, descriptions, paths, images,
and playback with deterministic fictional alternatives while preserving normal
browsing, links, sorting, pagination, and filters.

Use the mask button in the navigation bar or press `Ctrl+Shift+S`. Enabling the
mode reloads the page. Disabling it requires confirmation and another reload.
The generated identities remain stable in that browser until the
`cover-story.seed` local-storage value is removed.

## Important limitation

This is a shoulder-surfing disguise, not a security boundary. Original data is
still available to Stash, its GraphQL API, browser developer tools, network
inspection, and resources loaded before activation. Free-text filter contents
and unpatchable third-party plugin UI may also remain visible.

Cover Story never writes fictional values to Stash. Edit, file-information,
history, marker, and mutation controls are hidden on scene pages while enabled.

## Installation

Add this package source under **Settings > Plugins > Available Plugins**:

```text
https://mrx-31415.github.io/stash-plugins/main/index.yml
```

Reload available packages, install **Cover Story**, and reload Stash.

## Assets

The initial implementation uses dependency-free procedural posters. See
[`assets/README.md`](assets/README.md) for the production asset prompt and slot
specification that will replace these stand-ins with curated cinematic layers.
Theme titles, description fragments, tags, studio vocabulary, and planned asset
paths live together in [`themes.js`](themes.js).

## Development

```sh
node plugins/cover-story/test.js
./build_site.sh /tmp/stash-plugins-site
```

The UI plugin API is experimental. Cover Story targets the component patch
points available in current Stash development builds.
