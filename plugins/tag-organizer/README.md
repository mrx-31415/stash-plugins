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

Use the **Link Tags** tab to match your local tags with the remote stash-box
tags that appear on your linked scenes — a batch review list, so you never
have to type tag names one by one. Run a scan for a provider and the plugin
builds a suggestion per remote tag:

- **Ready to link**: a local tag matches the remote name or one of its
  stash-box aliases exactly. These are pre-checked because linking is
  non-destructive.
- **Needs review**: the remote tag only near-matches local tags (a shared
  word, e.g. "Goth Girl" vs "Goth", or a near-duplicate like a plural or
  compound spelling). Each candidate is its own unchecked row.
- **Merge suggestions**: the remote name matches a local tag exactly while
  other local tags near-match it (your local "Goth" + "Goth Girl" vs remote
  "Goth"). The exact match is the default survivor. Checking the row links the
  survivor; tick the "merge" box next to exactly the variants you want folded
  in — each variant merges individually, so you can merge just one of several.

Matching uses stash-box itself as the semantic oracle instead of loose
string-distance guesses. During the scan every remote tag is resolved by name
on stash-box (one cached lookup each) and every fuzzy candidate is verified
with a stash-box search: a candidate whose name is its own separate stash-box
tag is dropped, and candidates that only share a generic word ("Anal Play" vs
"Breast Play", "Age Group" vs "Group Sex") are dropped unless stash-box's own
search ranks the remote tag for the candidate's name — i.e. unless stash-box
itself associates them. Local tags matching a stash-box alias are treated as
exact matches ("Goth Girl" links directly when stash-box's "Goth" lists it as
an alias), and near-duplicate spellings (plurals, compounds) need no
confirmation. The scan's final phase shows "verifying matches on the provider"
while these lookups run.

Apply processes the checked rows in one operation. For each row the plugin
resolves the remote tag by name on stash-box, revalidates the local tags,
merges any selected sources (with the same cycle, staleness, and stash-ID
conflict checks as Clean Up Tags), then updates the survivor: it adds the
stash ID, unions the remote aliases, and fills the description. Tag names are
never renamed — the local name stays, the stash ID becomes the stable link.
Local tags already linked to exactly the remote tag are omitted from the
list entirely (there is nothing to do). Rows whose local tag carries a
*different* stash ID for the provider show a warning chip and are not
pre-checked; applying replaces that ID when you check "Replace existing stash
ID if needed".
Because merges replace tags, applying rows that merge requires a fresh
database backup (or the same explicit override as Clean Up Tags). Failed rows
stay selected for retry.

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

Use the **Infer Tags** tab to suggest tags scenes are missing from their own
properties — no metadata provider needed:

- **Group tags**: 3+ performers and no group tag (Threesome family, Orgy,
  Group Sex, Foursome, Gangbang, Bukkake) suggests **Threesome** (3
  performers) or **Group Sex** (4+). For 3 performers the suggestion is
  sex-aware: **Threesome (BGG)** for two women + one man, **Threesome (BBG)**
  for one woman + two men, **Threesome (Lesbian)** for three women, plain
  **Threesome** when sexes are unknown or the trio is all male. A title or
  description mentioning threesome/three-way/trio
  suggests **Threesome** even with fewer performers (the missing third is
  often not yet in the local performer list). When the local cast is
  incomplete — missing performers or genders — the scan augments it from
  stash-box via the scene's stash IDs before classifying, so scenes whose
  local data hides the real composition still get the correct variant.
- **Compilation**: "compilation"/"best of" in the title.
- **Vintage**: release date before 2000.
- **Solo**: one local performer with a stash-box cast confirming a single
  performer. Unlinked scenes and multi-performer casts are excluded.

Suggestions are a review queue: each shows the scene, the suggested tag, and
the reason, with explicit **Apply** (adds the tag, reversible) and **Skip**
actions. Decisions persist in the plugin's review state, so a re-scan does
not re-suggest what you already handled.
