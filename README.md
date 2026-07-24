# Stash plugins

Personal plugins for [Stash](https://github.com/stashapp/stash), published as
a package source that can be installed from Stash's plugin settings.

## Package source

Add this URL under **Settings > Plugins > Available Plugins**:

```text
https://mrx-31415.github.io/stash-plugins/main/index.yml
```

## Plugins

### Cover Story

Disguises Stash as a deterministic, workplace-safe fictional film collection.
See the [plugin README](plugins/cover-story/README.md) for details and limits.

### Celebration O-Counter

Replaces the default O-counter drops with a discreet celebration sparkles
icon. See the [plugin README](plugins/celebration-ocounter/README.md) for
details.

### Pull Remote Tags

Adds matching local tags from linked metadata providers to scenes without
removing existing tags. See the [plugin README](plugins/pull-tags/README.md)
for details.

## Publishing

Plugins placed in the [`plugins`](plugins) directory are packaged and published
to the source index by the repository's GitHub Pages workflow.

## License

This repository is licensed under [AGPL-3.0](LICENCE).
