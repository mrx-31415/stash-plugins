# Scene Import

Open **Import** in Stash's navigation and click **Scan & identify new scenes**.

Each import adds **[Scene Import Incomplete]** to its discovered scenes and removes
it once the pHash and both provider IDs are saved successfully. Later, use **Retry incomplete
imports** to check only that tagged queue and retry
scenes still missing a pHash, StashDB ID or ThePornDB ID. It does not scan files or include
untagged older library scenes. Fully linked scenes are skipped, and providers
already linked are not scraped again. Deleted scenes are ignored.
The browser queues scenes as it discovers them. Resume a pending scan after a
tab closes to recover scenes discovered since the last progress poll.

The queue is stored in Stash, so it survives changing browsers or clearing local
browser data. The currently saved import from an older version is adopted when
you resume it or start your next import/retry. Older runs no longer present in
saved browser progress cannot be recovered automatically; adding the tracking
tag to chosen scenes opts them in. Removing it opts scenes out of future retries.

Incomplete means a missing pHash or provider ID, not a general audit of metadata
fields or preview generation. Retry generates missing pHashes before metadata
jobs and never regenerates existing pHashes.
If StashDB later matches a scene first identified by ThePornDB, the normal
StashDB metadata pass runs. Its scalar fields and cover may replace earlier
values. Stash's native Identify task continues to skip scenes marked organized.

As soon as a discovered scene has a stored pHash, both providers are queried
while generation continues. Successful early lookups are saved with browser
progress and are not repeated on every poll. `Match found` is a candidate, while
`Matched` confirms a saved metadata/provider ID. Native metadata tasks still run
after the scan completes. Final ID saves use a fresh provider lookup.

The scan uses your configured folders and generation preferences, with video
perceptual hashes enabled. Only scene IDs absent before the scan are processed.
StashDB supplies metadata through Stash's Identify task. Missing studios,
performers and tags are created; ambiguous matches and ambiguous single-name
performers are skipped. Scenes are not marked organized. Ambiguous matches are
reported in progress rows without creating extra source-specific tags. Skipped
StashDB matches receive a read-only lookup to distinguish ambiguity from misses.
That final lookup also rechecks early ambiguous results. The latest candidate
count replaces the early result and takes precedence over legacy ambiguity
markers. Ambiguous StashDB rows show the number of distinct provider entries.

Multiple candidates are compared using their actual returned fingerprints.
A unique exact MD5 match wins first; otherwise a unique exact OShash match with
file duration within one second wins. Multiple equally strong entries remain
ambiguous. pHash and submission counts alone do not break a tie. Progress rows
show the selected evidence, such as `Exact OShash + duration`.

Native Identify handles single results. When it skips multiple results and one
has stronger exact evidence, the plugin applies that chosen entry's metadata
directly, preserving other provider IDs, existing performers/tags and URLs.
Missing related entities are created using the sceneTagger pattern; generic
single-name performers without disambiguation are skipped. Evidence is checked
again before saving, and metadata errors leave the run pending for resume.

Scenes still without a StashDB ID get a full metadata pass from ThePornDB using
the same Identify options, including cover images and creation of missing
studios, performers and tags. Unresolved multiple matches remain skipped.

For StashDB matches, ThePornDB adds only its Stash-ID, preserving other provider
IDs and all scene metadata. No-match, multiple-match and conflicting-ID results
are shown as scene links for manual review. Metadata results from both providers
are in Stash's task log.

The progress table updates while jobs run: each discovered file gets a thumbnail,
filename, pHash state, StashDB state, ThePornDB state and final result. A pHash is
shown as ready only once Stash has stored it. Provider matches are shown once
their IDs are saved; ambiguous matches are shown from provider lookup results.
During scans, progress reads only IDs newer than the initial snapshot instead of
reloading the entire library. The final snapshot comparison determines exactly
which scenes are imported. If a progress fetch fails, the last good rows remain;
task polling retries updates, and **Retry progress** is available when idle.
Legacy source-specific ambiguity tags are removed from the scenes processed by
this version after completion. Their tag definitions and other scenes are left
alone; only **[Scene Import Incomplete]** remains on unfinished imported scenes.

You can navigate to other Stash pages and return without losing updates; the
worker and progress live outside the page component. Keep this browser tab open
and run imports in only one tab. After a refresh or closing the tab, return and
click **Resume import**. Progress rows, the latest message and pending work are
saved in this browser. Server jobs already submitted continue while the browser
is closed, but later stages wait for you to resume. Discarding pending work does
not cancel Stash tasks or undo metadata changes. Scenes added concurrently by
another scan are included; avoid overlapping scans. Existing unmatched scenes
are excluded unless they carry the tracking tag. Failed per-scene links can be
retried through **Retry incomplete imports** after the current run finishes.

No filename guessing, fingerprint submission, scheduled scans, media deletion,
or additional dependencies. Matching uses the providers' scene-fragment search;
a unique returned candidate can still be wrong. Normal database backups apply
before bulk metadata changes.

Run checks with `node test.js`.
