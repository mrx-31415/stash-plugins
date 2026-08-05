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

Use the **Pull Remote Tags** tab to run the all-provider sync from the page.
It keeps the latest run's progress and changed or failed scene rows, including
added canonical local tag names and scene links.

Use **Clean Up Tags** for a persisted, review-first cleanup plan. It lists all
tags with per-object usage counts, suggests fuzzy duplicate groups, and shows
remote evidence for splitting aliases into child tags. For each duplicate
group, pick the target tag to keep and explicitly check the source tag(s) to
merge into it — a live summary always states "Will merge X into Y" so the
outcome is unambiguous before you apply. For alias splits, open a parent tag
and use "Select all as children" to turn every one of its aliases into a
child tag in one click (including aliases with no remote scene evidence),
or "Select all (incl. no-evidence)" within a single alias's evidence group.
Create the required database-only backup before applying changes, or check
"Apply without a fresh backup" to proceed anyway if you already trust your
backups — that override, like the backup gate itself, resets on every page
reload. Deletes are attempted individually, so marker-primary failures are
reported without stopping the remaining review.

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

Cleanup review plans are persisted under Stash's plugin configuration
directory. A successful backup is required again after a page reload. Cleanup
revalidates tag IDs, names, aliases, remote IDs, and hierarchy before every
tag mutation; scene split updates change scene tag assignments only.

Applying does not require a rescan to keep going: successfully deleted,
merged, or split items are cleared from the review automatically, while any
that failed (or hit an unresolved remote identity conflict) stay selected for
retry. Pick more tags, apply again, and repeat against the same plan — start
a new scan only when you want a fully fresh snapshot of the library.

## Upgrading from Pull Remote Tags

Tag Organizer uses the new plugin ID `tag-organizer`. Disable or uninstall
**Pull Remote Tags** before installing it; otherwise both plugins' automatic
hooks may run. Existing Pull Remote Tags settings do not migrate.
