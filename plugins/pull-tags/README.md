# Pull Remote Tags

Adds local tags to scenes when linked metadata providers return a tag with the
same name or alias. Existing scene tags are never removed.

## Use

Run **Sync remote tags** from Stash's Tasks page to process every scene.

Optional plugin settings are off by default:

- **Sync when a tag is created** runs the full sync after creating a local tag.
- **Sync when scene metadata IDs change** processes only a scene whose linked
  metadata-provider IDs change.

Remote tags with no unique local name/alias match are ignored. Configure each
metadata provider and its API key in Stash before running the plugin.
