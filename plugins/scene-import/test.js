"use strict";
const assert = require("node:assert/strict");
const { run, linkedIDs, newSceneIDs, waitJob, sceneRow, refreshRows, createController, matchReadyScenes } = require("./scene-import.js");
const db = "https://stashdb.org/graphql";
const tp = "https://theporndb.net/graphql";
const existing = [{ endpoint: db, stash_id: "db-id", updated_at: "unused" }];
assert.deepEqual(newSceneIDs(["1", "2"], ["1", "2", "3"]), ["3"]);
assert.deepEqual(linkedIDs(existing, [{ remote_site_id: "tp-id" }], tp), {
  stash_ids: [{ endpoint: db, stash_id: "db-id" }, { endpoint: tp, stash_id: "tp-id" }],
});
assert.equal(linkedIDs(existing, [], tp).reason, "no match");
assert.equal(linkedIDs(existing, [{ remote_site_id: "a" }, { remote_site_id: "b" }], tp).reason, "multiple matches");
assert.equal(linkedIDs([{ endpoint: tp + "/", stash_id: "a" }], [{ remote_site_id: "a" }], tp).reason, "already linked");
assert.equal(linkedIDs([{ endpoint: tp, stash_id: "a" }], [{ remote_site_id: "b" }], tp).reason, "conflicting ID");
assert.deepEqual(existing, [{ endpoint: db, stash_id: "db-id", updated_at: "unused" }]);

async function main() {
  const tagUpdates = [];
  let tagExists = false;
  function tagReply(query, vars) {
    if (query.includes("findTags(")) {
      if (vars.name === "Scene Import: ambiguous") return { findTags: { tags: [] } };
      assert.equal(vars.name, "[Scene Import Incomplete]");
      return { findTags: { tags: tagExists ? [{ id: "queue-tag", name: vars.name }] : [] } };
    }
    if (query.includes("tagCreate(")) {
      assert.equal(vars.name, "[Scene Import Incomplete]");
      tagExists = true;
      return { tagCreate: { id: "queue-tag" } };
    }
    if (query.includes("bulkSceneUpdate(")) {
      assert.deepEqual(vars.input.tag_ids.ids, ["queue-tag"]);
      assert.ok(vars.input.ids.length, "Tag changes must never have an empty scene scope");
      tagUpdates.push(vars.input);
      return { bulkSceneUpdate: vars.input.ids.map((id) => ({ id })) };
    }
  }
  let scanComplete = false;
  let identifyComplete = false;
  let fallbackComplete = false;
  let saved;
  const writes = [];
  const jobs = [];
  const identified = [];
  const fallbackLinks = new Map();
  const updatedLinks = new Map();
  let libraryReads = 0;
  function detail(id) {
    return {
      id, title: `Title ${id}`, paths: { screenshot: `/scene/${id}/screenshot` },
      files: [{ basename: `file-${id}.mp4`, fingerprints: scanComplete ? [{ type: "phash", value: "abc" }] : [] }],
      stash_ids: updatedLinks.get(id) || (id === "3" && identifyComplete ? existing : fallbackLinks.get(id) || []),
      tags: id === "4" && identifyComplete ? [{ name: "Scene Import: ambiguous StashDB" }, ...(fallbackComplete ? [{ name: "Scene Import: ambiguous ThePornDB" }] : [])] : [],
    };
  }
  const gql = async (query, vars) => {
    const tagged = tagReply(query, vars);
    if (tagged) return tagged;
    if (query.includes("configuration{")) return { configuration: { general: { stashBoxes: [{ endpoint: db }, { endpoint: tp }] }, defaults: { scan: { __typename: "ScanMetadataOptions", scanGeneratePreviews: false, scanGeneratePhashes: false } } } };
    if (query.includes("findScenes(")) {
      if (vars?.after != null) {
        assert.equal(vars.after, 2);
        assert.match(query, /scene_filter:\{id:\{value:\$after,modifier:GREATER_THAN\}\}/);
        return { findScenes: { scenes: ["3", "4", "5"].map(detail) } };
      }
      if (vars?.ids) {
        assert.match(query, /per_page:-1/, "Scene status and fallback lookups must include imports larger than one page");
        return { findScenes: { scenes: vars.ids.map(detail) } };
      }
      libraryReads++;
      return { findScenes: { scenes: (scanComplete ? ["1", "2", "3", "4", "5"] : ["1", "2"]).map((id) => ({ id })) } };
    }
    if (query.includes("metadataScan(")) {
      assert.equal(vars.input.scanGeneratePhashes, true);
      assert.equal(vars.input.scanGeneratePreviews, false);
      assert.equal(vars.input.rescan, false);
      assert.equal(Object.hasOwn(vars.input, "__typename"), false);
      return { metadataScan: "scan" };
    }
    if (query.includes("findJob(")) {
      jobs.push(vars.id);
      if (vars.id === "scan") scanComplete = true;
      else if (vars.id === "identify") identifyComplete = true;
      else fallbackComplete = true;
      return { findJob: { status: "FINISHED" } };
    }
    if (query.includes("metadataIdentify(")) {
      assert.equal(scanComplete, true);
      const source = vars.input.sources[0].source.stash_box_endpoint;
      identified.push({ source, ids: vars.input.sceneIDs });
      if (source === db) assert.deepEqual(vars.input.sceneIDs, ["3", "4", "5"]);
      else {
        assert.equal(source, tp);
        assert.deepEqual(vars.input.sceneIDs, ["4", "5"], "Only StashDB misses receive ThePornDB metadata");
        fallbackLinks.set("5", [{ endpoint: tp, stash_id: "fallback-id" }]);
      }
      assert.equal(vars.input.options.skipMultipleMatches, true);
      assert.equal(vars.input.options.setOrganized, false);
      assert.equal(vars.input.options.setCoverImage, true);
      assert.equal(vars.input.options.skipMultipleMatchTag, undefined, "Only the incomplete queue tag should be created");
      assert.deepEqual(vars.input.options.fieldOptions.find((f) => f.field === "performers"), { field: "performers", strategy: "MERGE", createMissing: true });
      return { metadataIdentify: source === db ? "identify" : "fallback" };
    }
    if (query.includes("scrapeSingleScene(")) {
      if (vars.source.stash_box_endpoint === db) return { scrapeSingleScene: vars.input.scene_id === "4" ? [{ remote_site_id: "db-a" }, { remote_site_id: "db-b" }] : [] };
      assert.equal(identifyComplete, true);
      assert.equal(vars.source.stash_box_endpoint, tp);
      assert.ok(["3", "4", "5"].includes(vars.input.scene_id));
      return { scrapeSingleScene: vars.input.scene_id === "3" ? [{ remote_site_id: "tp-id" }] : vars.input.scene_id === "5" ? [{ remote_site_id: "fallback-id" }] : [{ remote_site_id: "a" }, { remote_site_id: "b" }] };
    }
    if (query.includes("findScene(")) return { findScene: detail(vars.id) };
    if (query.includes("sceneUpdate(")) { writes.push(vars.input); updatedLinks.set(vars.input.id, vars.input.stash_ids); return { sceneUpdate: { id: vars.input.id } }; }
    throw new Error(`Unexpected query: ${query}`);
  };
  const save = (state) => { saved = JSON.parse(JSON.stringify(state)); };
  const result = await run(gql, null, save, () => {}, async () => {});
  assert.deepEqual(jobs, ["scan", "identify", "fallback"]);
  assert.deepEqual(identified, [{ source: db, ids: ["3", "4", "5"] }, { source: tp, ids: ["4", "5"] }]);
  assert.deepEqual(writes, [{ id: "3", stash_ids: [{ endpoint: db, stash_id: "db-id" }, { endpoint: tp, stash_id: "tp-id" }] }]);
  assert.equal(result.phase, "done");
  assert.deepEqual(result.results, [{ id: "3", result: "linked" }, { id: "4", result: "multiple matches" }, { id: "5", result: "already linked" }]);
  assert.equal(saved.phase, "done");
  assert.equal(libraryReads, 2, "Progress polls must not re-read the entire library");
  assert.deepEqual(result.rows.map((row) => [row.id, row.phash, row.stashdb, row.tpdb, row.completion]), [
    ["3", "Ready", "Matched", "Matched", "Done"],
    ["4", "Ready", "Ambiguous", "Ambiguous", "Needs review"],
    ["5", "Ready", "No saved match", "Matched", "Incomplete"],
  ]);
  assert.deepEqual(tagUpdates.slice(0, 2), [
    { ids: ["3", "4", "5"], tag_ids: { ids: ["queue-tag"], mode: "ADD" } },
    { ids: ["3"], tag_ids: { ids: ["queue-tag"], mode: "REMOVE" } },
  ]);
  await run(gql, { phase: "link", ids: ["3", "4"], results: [{ id: "3", result: "linked" }], tpdb: tp }, save, () => {}, async () => {});
  assert.equal(writes.length, 1, "Resume must not repeat completed scene writes");
  await run(async () => { throw new Error("An empty import must not query or identify the library"); }, { phase: "fallback", ids: [], results: [], stashdb: db, tpdb: tp }, save, () => {}, async () => {});
  const beforeFallback = identified.length;
  await run(gql, { phase: "fallback", ids: ["3"], results: [{ id: "3", result: "linked" }], stashdb: db, tpdb: tp }, save, () => {}, async () => {});
  assert.equal(identified.length, beforeFallback, "No fallback metadata job when every scene has a StashDB ID");
  await run(gql, { phase: "fallback", ids: ["5"], fallbackIDs: ["5"], job: "fallback", identifySubmitted: true, results: [{ id: "5", result: "already linked" }], stashdb: db, tpdb: tp }, save, () => {}, async () => {});
  assert.equal(identified.length, beforeFallback, "Resume must wait for the existing fallback job without resubmitting it");
  await assert.rejects(waitJob(async () => ({ findJob: { status: "FAILED", error: "generation failed" } }), "scan", () => {}, async () => {}), /generation failed/);
  await assert.rejects(waitJob(async () => ({ findJob: null }), "scan", () => {}, async () => {}), /history is unavailable/);
  await assert.rejects(run(gql, { phase: "identify", ids: ["3"], identifySubmitted: true }, save, () => {}, async () => {}), /submission was interrupted/);
  let polls = 0;
  let pauses = 0;
  const observed = [];
  await waitJob(async () => ({ findJob: { status: ++polls === 1 ? "RUNNING" : "FINISHED" } }), "scan", () => {}, async () => { pauses++; }, async (job) => { observed.push(job.status); });
  assert.equal(pauses, 1);
  assert.deepEqual(observed, ["RUNNING", "FINISHED"]);
  const unready = detail("7");
  unready.files[0].fingerprints = [];
  const scanState = { phase: "scan", stashdb: db, tpdb: tp, results: [] };
  assert.equal(sceneRow(unready, scanState).phash, "Generating");
  assert.equal(sceneRow(unready, { ...scanState, phase: "link", results: [{ id: "7", result: "no match" }] }).completion, "Needs review");
  assert.equal(sceneRow({ ...detail("3"), stash_ids: existing }, { ...scanState, phase: "link", results: [{ id: "3", result: "no match" }] }).completion, "Incomplete", "A missing provider ID keeps a scene incomplete");
  const offline = { phase: "link", ids: ["7"], results: [], rows: [{ id: "7", title: "Keep this row" }] };
  await refreshRows(async () => { throw new Error("offline"); }, offline, () => {});
  assert.equal(offline.rows[0].title, "Keep this row");
  assert.match(offline.progressError, /offline/);
  await refreshRows(async () => ({ findScenes: { scenes: [detail("7")] } }), offline, () => {});
  assert.equal(offline.rows[0].file, "file-7.mp4");
  assert.equal(offline.progressError, undefined, "A successful retry clears the stale progress warning");

  const pending = { phase: "link", ids: ["7"], results: [], stashdb: db, tpdb: tp, message: "Previous progress" };
  const data = new Map([["scene-import.pending", JSON.stringify(pending)]]);
  const storage = { getItem: (key) => data.get(key), setItem: (key, value) => data.set(key, value), removeItem: (key) => data.delete(key) };
  let resolveMatch;
  let signalLookup;
  const atLookup = new Promise((resolve) => { signalLookup = resolve; });
  let lookupCount = 0;
  const controller = createController(storage, async (query, vars) => {
    const tagged = tagReply(query, vars);
    if (tagged) return tagged;
    if (query.includes("findScenes(")) return { findScenes: { scenes: [detail("7")] } };
    if (query.includes("scrapeSingleScene(")) {
      lookupCount++;
      signalLookup();
      return new Promise((resolve) => { resolveMatch = resolve; });
    }
    if (query.includes("findScene(")) return { findScene: detail("7") };
    if (query.includes("sceneUpdate(")) { updatedLinks.set("7", vars.input.stash_ids); return { sceneUpdate: { id: "7" } }; }
    throw new Error(`Unexpected controller query: ${query}`);
  }, async () => {});
  assert.equal(controller.snapshot().message, "Previous progress");
  let firstPageUpdates = 0;
  const leavePage = controller.subscribe(() => { firstPageUpdates++; });
  const backgroundRefresh = controller.refresh();
  const task = controller.start();
  await backgroundRefresh;
  await atLookup;
  assert.equal(controller.start(), task, "Returning to the page cannot start a duplicate worker");
  assert.equal(controller.snapshot().running, true);
  leavePage();
  const beforeLeave = firstPageUpdates;
  let returnedView = controller.snapshot();
  const leaveAgain = controller.subscribe((next) => { returnedView = next; });
  assert.equal(returnedView.state.rows[0].file, "file-7.mp4");
  resolveMatch({ scrapeSingleScene: [{ remote_site_id: "tp-7" }] });
  await task;
  assert.equal(firstPageUpdates, beforeLeave, "Unmounted pages do not receive worker updates");
  assert.equal(returnedView.running, false);
  assert.equal(returnedView.state.rows[0].completion, "Incomplete");
  assert.match(returnedView.message, /All done/);
  assert.equal(lookupCount, 1);
  leaveAgain();
  const restored = createController(storage, async () => { throw new Error("Restoring must not issue requests"); });
  assert.equal(restored.snapshot().state.rows[0].completion, "Incomplete");
  assert.match(restored.snapshot().message, /All done/);
  const legacy = { ...pending, phase: "done", results: [{ id: "7", result: "multiple matches" }] };
  storage.setItem("scene-import.pending", JSON.stringify(legacy));
  const upgraded = createController(storage, async (query) => {
    assert.ok(query.startsWith("query"), "Displaying an older completed run must remain read-only");
    return { findScenes: { scenes: [detail("7")] } };
  });
  await upgraded.refresh();
  assert.equal(upgraded.snapshot().state.rows[0].completion, "Needs review");
  assert.deepEqual(upgraded.snapshot().state.results, legacy.results);
  let refreshCount = 0;
  const retryController = createController(storage, async (query) => {
    assert.ok(query.startsWith("query"), "Retry progress must not submit or repeat jobs");
    if (++refreshCount === 1) throw new Error("NetworkError when attempting to fetch resource.");
    return { findScenes: { scenes: [detail("7")] } };
  });
  await retryController.refresh();
  assert.match(retryController.snapshot().state.progressError, /NetworkError/);
  await retryController.refresh();
  assert.equal(retryController.snapshot().state.progressError, undefined);
  assert.equal(retryController.snapshot().state.rows[0].completion, "Needs review");

  const retryLinks = new Map([
    ["10", [{ endpoint: db, stash_id: "db-10" }, { endpoint: tp, stash_id: "tp-10" }]],
    ["20", [{ endpoint: db, stash_id: "db-20" }]],
    ["30", [{ endpoint: tp, stash_id: "tp-30" }]],
    ["40", []],
  ]);
  const queued = new Set(["10", "20", "30", "40", "deleted"]);
  const retryIdentifies = [];
  const removed = [];
  function retryScene(id) { return { ...detail(id), tags: [], stash_ids: retryLinks.get(id) || [] }; }
  const retryGql = async (query, vars) => {
    if (query.includes("configuration{")) return { configuration: { general: { stashBoxes: [{ endpoint: db }, { endpoint: tp }] }, defaults: { scan: {} } } };
    if (query.includes("findTags(")) return { findTags: { tags: vars.name === "[Scene Import Incomplete]" ? [{ id: "queue-tag", name: vars.name }] : [] } };
    if (query.includes("bulkSceneUpdate(")) {
      assert.ok(vars.input.ids.length);
      assert.deepEqual(vars.input.tag_ids.ids, ["queue-tag"]);
      if (vars.input.tag_ids.mode === "ADD") vars.input.ids.forEach((id) => queued.add(id));
      else { removed.push([...vars.input.ids]); vars.input.ids.forEach((id) => queued.delete(id)); }
      return { bulkSceneUpdate: [] };
    }
    if (query.includes("findScenes(")) {
      if (vars.tag) {
        assert.equal(vars.tag, "queue-tag");
        assert.match(query, /tags:\{value:\[\$tag\],modifier:INCLUDES\}/);
        return { findScenes: { scenes: [...queued].filter((id) => retryLinks.has(id)).map(retryScene) } };
      }
      assert.ok(vars.ids, "Retries must never query all scene IDs");
      assert.ok(vars.ids.every((id) => ["20", "30", "40"].includes(id)), "Complete and untracked scenes must stay outside the retry");
      return { findScenes: { scenes: vars.ids.map(retryScene) } };
    }
    if (query.includes("metadataIdentify(")) {
      const source = vars.input.sources[0].source.stash_box_endpoint;
      retryIdentifies.push({ source, ids: vars.input.sceneIDs });
      if (source === db) {
        assert.deepEqual(vars.input.sceneIDs, ["30", "40"]);
        retryLinks.get("30").push({ endpoint: db, stash_id: "db-30" });
      } else {
        assert.deepEqual(vars.input.sceneIDs, ["40"]);
        retryLinks.get("40").push({ endpoint: tp, stash_id: "tp-40" });
      }
      return { metadataIdentify: "retry-job" };
    }
    if (query.includes("findJob(")) return { findJob: { status: "FINISHED" } };
    if (query.includes("findScene(")) return { findScene: retryScene(vars.id) };
    if (query.includes("scrapeSingleScene(")) {
      const source = vars.source.stash_box_endpoint;
      if (source === db) { assert.equal(vars.input.scene_id, "40"); return { scrapeSingleScene: [] }; }
      assert.equal(vars.input.scene_id, "20", "An existing ThePornDB ID must not be scraped again");
      return { scrapeSingleScene: [{ remote_site_id: "tp-20" }] };
    }
    if (query.includes("sceneUpdate(")) { retryLinks.set(vars.input.id, vars.input.stash_ids); return { sceneUpdate: { id: vars.input.id } }; }
    throw new Error(`Retry must not scan files or execute unexpected requests: ${query}`);
  };
  let retrySaved;
  const saveRetry = (next) => { retrySaved = JSON.parse(JSON.stringify(next)); };
  const retryResult = await run(retryGql, { phase: "retry", adoptIDs: ["40"], results: [], rows: [] }, saveRetry, () => {}, async () => {});
  assert.deepEqual(retryResult.ids, ["20", "30", "40"]);
  assert.deepEqual(retryIdentifies, [{ source: db, ids: ["30", "40"] }, { source: tp, ids: ["40"] }]);
  assert.deepEqual(removed, [["10"], ["20", "30"]]);
  assert.ok(queued.has("40"), "Missing upstream entries must stay in the queue for later retries");
  assert.ok(!queued.has("20") && !queued.has("30"), "The queue tag is removed once both IDs are present");
  assert.equal(retryResult.rows.find((row) => row.id === "40").completion, "Incomplete");
  const identifyCount = retryIdentifies.length;
  await assert.rejects(run(async (query, vars) => {
    if (query.includes("bulkSceneUpdate(")) throw new Error("Tag removal failed");
    return retryGql(query, vars);
  }, { ...retryResult, phase: "cleanup" }, saveRetry, () => {}, async () => {}), /Tag removal failed/);
  assert.equal(retrySaved.phase, "cleanup");
  await run(retryGql, retrySaved, saveRetry, () => {}, async () => {});
  assert.equal(retryIdentifies.length, identifyCount, "Resuming tag cleanup must not repeat metadata jobs");
  assert.equal(retrySaved.phase, "done");

  const emptyRetry = await run(async (query, vars) => {
    if (query.includes("findScenes(") && vars.tag) return { findScenes: { scenes: [] } };
    if (query.includes("metadataIdentify(") || query.includes("metadataScan(") || query.includes("bulkSceneUpdate(")) throw new Error("Empty queues must not submit work");
    return retryGql(query, vars);
  }, { phase: "retry", results: [], rows: [] }, () => {}, () => {}, async () => {});
  assert.equal(emptyRetry.phase, "done");
  assert.deepEqual(emptyRetry.ids, []);

  const earlyState = { phase: "scan", stashdb: db, tpdb: tp, results: [], rows: [{ id: "90", file: "ready.mp4", phash: "Ready" }, { id: "91", file: "waiting.mp4", phash: "Generating" }] };
  const earlyCalls = [];
  const earlyGql = async (query, vars) => {
    assert.ok(query.startsWith("query"), "Early matching must not submit metadata writes");
    assert.equal(vars.input.scene_id, "90", "Only scenes with a completed pHash can be matched early");
    earlyCalls.push(vars.source.stash_box_endpoint);
    return { scrapeSingleScene: vars.source.stash_box_endpoint === db ? [{ remote_site_id: "db-90" }] : [{ remote_site_id: "tp-a" }, { remote_site_id: "tp-b" }] };
  };
  await matchReadyScenes(earlyGql, earlyState, () => {}, () => {}, async () => {});
  await matchReadyScenes(earlyGql, earlyState, () => {}, () => {}, async () => {});
  assert.deepEqual(earlyCalls, [db, tp], "Successful lookups must not repeat on every generation poll");
  assert.equal(sceneRow(detail("90"), earlyState).stashdb, "Match found");
  assert.equal(sceneRow(detail("90"), earlyState).tpdb, "Ambiguous");
  const staleScene = { ...detail("91"), stash_ids: [], tags: [{ name: "Scene Import: ambiguous StashDB" }] };
  const staleState = { phase: "identify", stashdb: db, tpdb: tp, ambiguousIDs: ["91"], earlyMatches: { "91": { stashdb: ["db-a", "db-b"] } }, results: [] };
  assert.equal(sceneRow(staleScene, staleState).stashdb, "Ambiguous");
  staleState.earlyMatches["91"].stashdb = ["db-single"];
  assert.equal(sceneRow(staleScene, staleState).stashdb, "Match found", "A later single candidate must override early and legacy ambiguity");
  staleState.earlyMatches["91"].stashdb = [];
  assert.notEqual(sceneRow(staleScene, staleState).stashdb, "Ambiguous", "A later miss must clear obsolete ambiguity too");
  let finalStashdbLookups = 0;
  const corrected = await run(async (query, vars) => {
    const scene = { ...detail("91"), tags: [{ name: "Scene Import: ambiguous StashDB" }], stash_ids: [{ endpoint: tp, stash_id: "tp-91" }] };
    if (query.includes("findJob(")) return { findJob: { status: "FINISHED" } };
    if (query.includes("findScenes(")) return { findScenes: { scenes: [scene] } };
    if (query.includes("findScene(")) return { findScene: scene };
    if (query.includes("scrapeSingleScene(")) {
      assert.equal(vars.source.stash_box_endpoint, db);
      finalStashdbLookups++;
      return { scrapeSingleScene: [{ remote_site_id: "db-single" }, { remote_site_id: "db-single" }] };
    }
    if (query.includes("findTags(")) return { findTags: { tags: [] } };
    throw new Error(`Correcting an early ambiguity must not submit duplicate metadata jobs: ${query}`);
  }, { ...staleState, tracked: true, retry: true, tagID: "queue-tag", ids: ["91"], job: "existing-identify-job", earlyMatches: { "91": { stashdb: ["db-a", "db-b"] } } }, () => {}, () => {}, async () => {});
  assert.equal(finalStashdbLookups, 1, "Ambiguous rows must still receive their final provider lookup");
  assert.deepEqual(corrected.earlyMatches["91"].stashdb, ["db-single"]);
  assert.deepEqual(corrected.ambiguousIDs, []);
  assert.equal(corrected.rows[0].stashdb, "No saved match", "A unique candidate must not falsely claim saved metadata");
  assert.equal(corrected.rows[0].completion, "Incomplete");
  const recoverEarly = { ...earlyState, rows: [{ id: "90", phash: "Ready" }], earlyMatches: {} };
  let earlyAttempts = 0;
  await matchReadyScenes(async (query, vars) => {
    if (vars.source.stash_box_endpoint === db && ++earlyAttempts === 1) throw new Error("NetworkError");
    return { scrapeSingleScene: [] };
  }, recoverEarly, () => {}, () => {}, async () => {});
  assert.match(recoverEarly.progressError, /NetworkError/);
  await matchReadyScenes(async (query, vars) => {
    assert.equal(vars.source.stash_box_endpoint, db, "Only failed early lookups should retry");
    earlyAttempts++;
    return { scrapeSingleScene: [] };
  }, recoverEarly, () => {}, () => {}, async () => {});
  assert.equal(earlyAttempts, 2);

  let scanPolls = 0;
  let generating = true;
  const overlapped = [];
  const overlapLinks = new Map([["2", []], ["3", []]]);
  function overlapScene(id) { return { ...detail(id), tags: [], stash_ids: overlapLinks.get(id), files: [{ basename: `overlap-${id}.mp4`, fingerprints: id === "2" || !generating ? [{ type: "phash", value: "ready" }] : [] }] }; }
  const overlapGql = async (query, vars) => {
    const tagged = tagReply(query, vars);
    if (tagged) return tagged;
    if (query.includes("findJob(")) {
      if (vars.id === "generation" && ++scanPolls === 1) return { findJob: { status: "RUNNING" } };
      generating = false;
      return { findJob: { status: "FINISHED" } };
    }
    if (query.includes("findScenes(")) {
      if (vars?.ids || vars?.after != null) return { findScenes: { scenes: ["2", "3"].map(overlapScene) } };
      return { findScenes: { scenes: ["1", "2", "3"].map((id) => ({ id })) } };
    }
    if (query.includes("scrapeSingleScene(")) {
      if (generating) { assert.equal(vars.input.scene_id, "2"); overlapped.push(vars.source.stash_box_endpoint); }
      return { scrapeSingleScene: [{ remote_site_id: `${vars.source.stash_box_endpoint === db ? "db" : "tp"}-${vars.input.scene_id}` }] };
    }
    if (query.includes("metadataIdentify(")) {
      assert.equal(generating, false, "Native metadata saves should start after scan completion");
      assert.deepEqual(overlapped, [db, tp], "Both provider matches must have started before generation finishes");
      vars.input.sceneIDs.forEach((id) => overlapLinks.get(id).push({ endpoint: db, stash_id: `db-${id}` }));
      return { metadataIdentify: "metadata" };
    }
    if (query.includes("findScene(")) return { findScene: overlapScene(vars.id) };
    if (query.includes("sceneUpdate(")) { overlapLinks.set(vars.input.id, vars.input.stash_ids); return { sceneUpdate: { id: vars.input.id } }; }
    throw new Error(`Unexpected overlapping-generation query: ${query}`);
  };
  const overlapResult = await run(overlapGql, { phase: "scan", before: ["1"], job: "generation", stashdb: db, tpdb: tp, results: [] }, () => {}, () => {}, async () => {});
  assert.deepEqual(overlapped, [db, tp]);
  assert.ok(overlapResult.rows.every((row) => row.completion === "Done"));
  let phashGenerated = false;
  const generatedIDs = [];
  const hashRetry = await run(async (query, vars) => {
    const tagged = tagReply(query, vars);
    if (tagged) return tagged;
    if (query.includes("configuration{")) return { configuration: { general: { stashBoxes: [{ endpoint: db }, { endpoint: tp }] }, defaults: { scan: {} } } };
    const scene = { ...detail("80"), tags: [], stash_ids: [{ endpoint: db, stash_id: "db-80" }, { endpoint: tp, stash_id: "tp-80" }], files: [{ basename: "missing-phash.mp4", fingerprints: phashGenerated ? [{ type: "phash", value: "ready" }] : [] }] };
    if (query.includes("findScenes(")) return { findScenes: { scenes: [scene] } };
    if (query.includes("metadataGenerate(")) {
      assert.deepEqual(vars.input, { sceneIDs: ["80"], phashes: true, overwrite: false });
      generatedIDs.push(...vars.input.sceneIDs);
      phashGenerated = true;
      return { metadataGenerate: "generate" };
    }
    if (query.includes("findJob(")) return { findJob: { status: "FINISHED" } };
    if (query.includes("findScene(")) return { findScene: scene };
    throw new Error(`A pHash-only retry must not rescrape existing provider IDs: ${query}`);
  }, { phase: "retry", results: [], rows: [] }, () => {}, () => {}, async () => {});
  assert.deepEqual(generatedIDs, ["80"]);
  assert.equal(hashRetry.rows[0].completion, "Done");
  assert.ok(tagUpdates.some((update) => update.tag_ids.mode === "REMOVE" && update.ids.includes("80")));
  const dataPending = new Map([["scene-import.pending", JSON.stringify({ phase: "identify", ids: ["1"], job: "active", results: [] })]]);
  const guard = createController({ getItem: (key) => dataPending.get(key), setItem: (key, value) => dataPending.set(key, value), removeItem: (key) => dataPending.delete(key) }, async () => { throw new Error("A retry must not replace active pending work"); });
  await guard.start("retry");
  assert.equal(guard.snapshot().state.job, "active");
  assert.match(guard.snapshot().error, /pending import/);
  const legacyUpdates = [];
  const migrated = await run(async (query, vars) => {
    if (query.includes("findScenes(")) return { findScenes: { scenes: [
      { ...detail("2"), tags: [], stash_ids: [{ endpoint: db, stash_id: "db-2" }, { endpoint: tp, stash_id: "tp-2" }] },
      { ...detail("4"), tags: [{ name: "Scene Import: ambiguous StashDB" }], stash_ids: [{ endpoint: tp, stash_id: "tp-4" }] },
    ] } };
    if (query.includes("findTags(")) return { findTags: { tags: [{ id: "legacy-db", name: "Scene Import: ambiguous StashDB" }, { id: "legacy-tp", name: "Scene Import: ambiguous ThePornDB" }, { id: "leave-alone", name: "Scene Import: ambiguous other" }] } };
    if (query.includes("bulkSceneUpdate(")) { legacyUpdates.push(vars.input); return { bulkSceneUpdate: [] }; }
    throw new Error(`Unexpected legacy cleanup request: ${query}`);
  }, { phase: "cleanup", tracked: true, tagID: "queue-tag", ids: ["2", "4"], stashdb: db, tpdb: tp, results: [{ id: "2", result: "already linked" }, { id: "4", result: "already linked" }] }, () => {}, () => {}, async () => {});
  assert.deepEqual(legacyUpdates, [
    { ids: ["2"], tag_ids: { ids: ["queue-tag"], mode: "REMOVE" } },
    { ids: ["2", "4"], tag_ids: { ids: ["legacy-db", "legacy-tp"], mode: "REMOVE" } },
  ]);
  assert.ok(migrated.ambiguousIDs.includes("4"), "Removing legacy tags must preserve row ambiguity feedback");
  let currentLinks = [{ endpoint: db, stash_id: "db-90" }];
  const foreign = { endpoint: "https://example.test/graphql", stash_id: "foreign-90" };
  await run(async (query, vars) => {
    const scene = { ...detail("90"), tags: [], stash_ids: currentLinks.map((link) => ({ ...link })) };
    if (query.includes("findScenes(")) return { findScenes: { scenes: [scene] } };
    if (query.includes("findScene(")) return { findScene: scene };
    if (query.includes("scrapeSingleScene(")) { currentLinks.push(foreign); return { scrapeSingleScene: [{ remote_site_id: "tp-90" }] }; }
    if (query.includes("sceneUpdate(")) {
      assert.ok(vars.input.stash_ids.some((link) => link.stash_id === "foreign-90"), "An ID added while the provider lookup is running must be preserved");
      currentLinks = vars.input.stash_ids;
      return { sceneUpdate: { id: "90" } };
    }
    if (query.includes("findTags(")) return { findTags: { tags: [] } };
    if (query.includes("bulkSceneUpdate(")) return { bulkSceneUpdate: [] };
    throw new Error(`Unexpected concurrent ID preservation request: ${query}`);
  }, { phase: "link", tracked: true, tagID: "queue-tag", ids: ["90"], stashdb: db, tpdb: tp, results: [] }, () => {}, () => {}, async () => {});
  await require("./evidence-test.js")();
  console.log("Scene Import checks passed");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
