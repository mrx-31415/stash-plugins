# Scene Import

Open **Import** in Stash's navigation and click **Scan & identify new scenes**.

Each import adds **[Scene Import Incomplete]** to its discovered scenes and removes
it once the pHash and both provider IDs are saved successfully. Later, use **Retry incomplete
imports** to check only that tagged queue and retry
scenes still missing a pHash, StashDB ID or ThePornDB ID. It does not scan files or include
untagged older library scenes. Fully linked scenes are skipped, and providers
already linked are not scraped again. Deleted scenes are ignored.
The server queues discovered scenes after the scan finishes. The import continues
when you close the browser.

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

Provider matching starts after scan and generation finish. `Match found` is a
candidate, while `Matched` confirms a saved metadata/provider ID. Final ID saves
use a fresh provider lookup.

The scan uses your configured folders and generation preferences, with video
perceptual hashes enabled. Only scene IDs absent before the scan are processed.
StashDB supplies metadata through Stash's Identify task. Missing studios,
performers and tags are created; male and transgender male performers are skipped
in both provider passes and direct selected-match saves. Performers whose gender
is unknown may still be imported. Existing database entries are left intact.
Ambiguous matches and ambiguous single-name
performers are skipped. Scenes are not marked organized. Ambiguous matches are
reported in progress rows without creating extra source-specific tags. Skipped
StashDB matches receive a read-only lookup to distinguish ambiguity from misses.
That final lookup also rechecks early ambiguous results. The latest candidate
count replaces the early result and takes precedence over legacy ambiguity
markers. Ambiguous StashDB rows show the number of distinct provider entries.

Multiple candidates are compared using their actual returned fingerprints.
A unique exact MD5 match wins first; otherwise a unique exact OShash match with
file duration within one second wins; otherwise a unique entry with a pHash
within 8 bits and duration within five seconds of the same fingerprint wins
(Stash's tagger thresholds), unless the entry's own duration is more than five
seconds off. Multiple equally strong entries remain ambiguous.
Submission counts alone do not break a tie. Progress rows show the selected
evidence, such as `Exact OShash + duration` or `pHash + duration`.

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

The progress table reads current scenes and native job percentages while jobs run:
each discovered file gets a thumbnail,
filename, pHash state, StashDB state, ThePornDB state and final result. A pHash is
shown as ready only once Stash has stored it. Provider matches are shown once
their IDs are saved; ambiguous matches are shown from provider lookup results.
The final snapshot comparison determines exactly which scenes are imported.
The browser combines saved checkpoints with live progress reads; it does not drive
the import or write checkpoints. A scene shows Done once its pHash and both IDs
are saved, without waiting for the final linking pass. If a
progress fetch fails, the last good rows remain and the next poll retries.
Legacy source-specific ambiguity tags are removed from the scenes processed by
this version after completion. Their tag definitions and other scenes are left
alone; only **[Scene Import Incomplete]** remains on unfinished imported scenes.

Imports run on Stash's server using its embedded JavaScript runtime, with no
additional runtime or service to install. You can navigate elsewhere, refresh,
close the browser, or return from another browser to see saved progress. The
server saves the scene boundary, each phase and per-scene results in plugin
configuration. Loaded checkpoints are converted to native JavaScript objects so
appended results survive saving in Stash's embedded runtime. Each
native scan/generation/Identify job runs before the next queued import
continuation, so the plugin does not block Stash's task queue while waiting.

If Stash restarts or a job fails, return and click **Resume import**. Cancelling
an import continuation in Tasks also leaves resumable progress. Discarding
pending work does not cancel Stash tasks or undo metadata changes. Scenes added
concurrently by another scan are included; avoid overlapping scans. Existing
unmatched scenes are excluded unless they carry the tracking tag. Failed
per-scene links can be retried through **Retry incomplete imports** after the
current run finishes. Older browser progress is transferred to the server when
you click Resume import; keep that browser's saved data until the transfer succeeds.

No filename guessing, fingerprint submission, scheduled scans, media deletion,
or additional dependencies. Matching uses the providers' scene-fragment search;
a unique returned candidate can still be wrong. Normal database backups apply
before bulk metadata changes.

After changing the shared workflow or `server.js`, run `node build-server.js`
to rebuild the embedded worker. Run checks with `node test.js`,
`node evidence-test.js`, `node server-test.js`, and `node build-server.js --check`.
The server test executes a complete import through queued native jobs without
any browser polling and checks resume failures, duplicate starts, and stale tasks.
