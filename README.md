# Stash plugins

Personal plugins for [Stash](https://github.com/stashapp/stash), published as
a package source that can be installed from Stash's plugin settings.

## Package source

Add this URL under **Settings > Plugins > Available Plugins**:

```text
https://mrx-31415.github.io/stash-plugins/main/index.yml
```

This one source offers every plugin below, including those developed in their
own repositories. Development builds of the same plugins are at
`https://mrx-31415.github.io/stash-plugins/dev/index.yml`.

## Plugins

### Stash Curator, Auto Seek and Incognito

Developed in their own repositories,
[stash-curator](https://github.com/mrx-31415/stash-curator),
[stash-auto-seek](https://github.com/mrx-31415/stash-auto-seek) and
[stash-incognito](https://github.com/mrx-31415/stash-incognito), and mirrored here
from their releases. Installs from Curator's own source keep receiving updates there.

### Cover Story

Disguises Stash as a deterministic, workplace-safe fictional film collection.
See the [plugin README](plugins/cover-story/README.md) for details and limits.

### Celebration O-Counter

Replaces the default O-counter drops with a discreet celebration sparkles
icon. See the [plugin README](plugins/celebration-ocounter/README.md) for
details.

### Tag Organizer

Finds remote tag gaps and adds matching local tags to linked scenes without
removing existing tags. See the [plugin README](plugins/tag-organizer/README.md)
for details.

## Publishing

Plugins placed in the [`plugins`](plugins) directory are packaged and published
to the source index by the repository's GitHub Pages workflow.

Plugins from other repositories are listed in [`sources.json`](sources.json)
by the package index they publish. `build_hub.py` downloads each listed
package, checks it against its index's sha256, and serves it from
`<channel>/<id>/<version>/`. The workflow runs hourly and deploys only when an
index changed; a plugin repository can trigger it immediately with a
`repository_dispatch` of type `plugin-released`. Run `python3
build_hub_test.py` to check the mirroring locally (needs PyYAML).

## License

This repository is licensed under [AGPL-3.0](LICENCE).
