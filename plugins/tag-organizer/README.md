# Tag Organizer

Finds tags used by configured metadata providers on linked local scenes but
missing from the local tag collection. Results are sorted by affected scene
count and can be added individually or in batches. Existing scene tags are
never removed.

## Use

Open **Tag Organizer** from Stash's main navigation, select a provider, and
click **Scan**. Scans run as Stash jobs, so you can leave the page and return to
the saved progress. Scans and full syncs batch remote scene lookups to reduce
StashDB requests. Adding a result creates the local tag and applies it to the
verified linked scenes.

Select one or more results with the checkboxes and click **Add selected** to
process them in one operation. A failed tag does not prevent the remaining
selected tags from being processed; failed tags remain selected for retry.

The existing **Sync remote tags** task still adds every matching local tag to
linked scenes.

Optional plugin settings are off by default:

- **Sync when a tag is created** runs the full sync after creating a local tag.
- **Sync when scene metadata IDs change** processes only a scene whose linked
  metadata-provider IDs change.

Remote tags with no unique local name/alias match are ignored. Configure each
metadata provider and its API key in Stash before running the plugin.

## Upgrading from Pull Remote Tags

Tag Organizer uses the new plugin ID `tag-organizer`. Disable or uninstall
**Pull Remote Tags** before installing it; otherwise both plugins' automatic
hooks may run. Existing Pull Remote Tags settings do not migrate.
