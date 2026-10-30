"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
require("./build-server.js");
const source = fs.readFileSync(require.resolve("./scene-import-server.js"), "utf8");
let config = { otherSetting: "preserved" };
let number = 0;
const jobs = new Map();
const queue = [];
const seen = [];
const tagged = new Set();
const db = "https://stashdb.org/graphql";
const tp = "https://theporndb.net/graphql";
let scanned = false;
let progressOffline = false;
const scene = { id: "2", title: "New scene", files: [{ basename: "new.mp4", fingerprints: [{ type: "phash", value: "abc" }] }], stash_ids: [], tags: [] };
function enqueue(kind, args) {
  const id = String(++number);
  jobs.set(id, { status: "READY", kind, args });
  queue.push(id);
  return id;
}
function gql(query, vars) {
  if (query.includes("configurePlugin")) { config = JSON.parse(JSON.stringify(vars.input)); return { configurePlugin: config }; }
  if (query.includes("configuration{")) {
    // Stash exposes map-owned Go slices as fresh array views on each read.
    const loaded = config.state ? { ...config, state: { ...config.state, get results() { return config.state.results.slice(); } } } : config;
    return { configuration: { plugins: { "scene-import": loaded }, general: { stashBoxes: [{ endpoint: db }, { endpoint: tp }] }, defaults: { scan: {} } } };
  }
  if (query.includes("runPluginTask")) return { runPluginTask: enqueue("worker", vars.args) };
  if (query.includes("findJob")) return { findJob: jobs.get(vars.id) || null };
  if (query.includes("metadataScan")) return { metadataScan: enqueue("scan") };
  if (query.includes("metadataIdentify")) {
    assert.deepEqual(Array.from(vars.input.options.performerGenders), ["FEMALE", "TRANSGENDER_FEMALE", "INTERSEX", "NON_BINARY"]);
    return { metadataIdentify: enqueue("identify", vars.input) };
  }
  if (query.includes("findTags")) return { findTags: { tags: vars.name === "[Scene Import Incomplete]" ? [{ id: "tag", name: vars.name }] : [] } };
  if (query.includes("bulkSceneUpdate")) {
    for (const id of vars.input.ids) if (vars.input.tag_ids.mode === "ADD") tagged.add(id); else tagged.delete(id);
    return { bulkSceneUpdate: [] };
  }
  if (query.includes("findScenes")) {
    if (progressOffline) throw new Error("Scene reads unavailable");
    return { findScenes: { scenes: vars.after != null || vars.ids ? scanned ? [scene] : [] : [{ id: "1" }, ...(scanned ? [scene] : [])] } };
  }
  if (query.includes("findScene")) return { findScene: scene };
  if (query.includes("scrapeSingleScene")) return { scrapeSingleScene: [{ remote_site_id: "tp-id" }] };
  if (query.includes("sceneUpdate")) { scene.stash_ids = vars.input.stash_ids; return { sceneUpdate: scene }; }
  throw new Error(`Unexpected query: ${query}`);
}
function execute(args) {
  return vm.runInNewContext(source, { input: { Args: args }, gql: { Do: gql }, log: { Info() {} }, util: { Sleep() {} } }).Output;
}
assert.equal(execute({ mode: "scan" }).running, true);
assert.equal(execute({ mode: "scan" }).running, true);
assert.equal(queue.length, 1, "Concurrent starts reuse the queued import");
assert.throws(() => execute({ mode: "discard" }), /still running/);
const first = queue.shift();
jobs.get(first).status = "RUNNING";
execute(jobs.get(first).args);
jobs.get(first).status = "FINISHED";
assert.deepEqual(queue.map((id) => jobs.get(id).kind), ["scan", "worker"], "The continuation releases the task queue to the scan");
const scanJob = jobs.get(queue[0]);
scanJob.status = "RUNNING";
scanJob.progress = 0.375;
scanned = true;
const checkpoint = JSON.stringify(config);
const live = execute({ mode: "status" });
assert.match(live.message, /Scan and generation: RUNNING \(38%\)/);
assert.equal(live.state.rows[0].id, "2", "New scenes appear while the native scan is still running");
assert.equal(live.state.rows[0].phash, "Ready");
assert.equal(JSON.stringify(config), checkpoint, "Polling must not overwrite worker checkpoints or tag scenes");
scene.files[0].fingerprints = [];
assert.equal(execute({ mode: "status" }).state.rows[0].phash, "Generating", "Polls read the current stored fingerprints");
scene.files[0].fingerprints = [{ type: "phash", value: "abc" }];
// No browser requests from here until the entire import finishes.
let loops = 0;
while (queue.length) {
  assert.ok(++loops < 15, "Import must finish without endless continuation jobs");
  const id = queue.shift();
  const job = jobs.get(id);
  job.status = "RUNNING";
  seen.push(job.kind);
  if (job.kind === "worker") execute(job.args);
  if (job.kind === "scan") scanned = true;
  if (job.kind === "identify") scene.stash_ids.push({ endpoint: job.args.sources[0].source.stash_box_endpoint, stash_id: "db-id" });
  job.status = "FINISHED";
}
const result = execute({ mode: "status" });
assert.equal(result.running, false);
assert.equal(result.state.phase, "done");
assert.equal(result.state.rows[0].completion, "Done");
assert.equal(result.state.results.length, 1, "Per-scene results survive appending to a loaded Go-backed checkpoint");
assert.equal(result.state.results[0].result, "linked");
assert.equal(scene.stash_ids.length, 2);
assert.equal(tagged.size, 0);
assert.equal(config.otherSetting, "preserved");
assert.match(result.message, /All done/);
const oldToken = config.state.token;
execute({ mode: "discard" });
execute({ mode: "continue", token: oldToken });
assert.equal(config.state, null, "Stale continuation cannot revive discarded progress");
assert.throws(() => execute({ mode: "scan", legacy: { phase: "link", ids: ["invalid"], results: [] } }), /invalid/);
// A stopped native job leaves a resumable error instead of looping or expanding scope.
execute({ mode: "scan", legacy: { phase: "scan", before: ["1"], results: [], rows: [], job: "failed", stashdb: db, tpdb: tp } });
jobs.set("failed", { status: "FAILED", error: "scan failed" });
const resume = queue.shift();
jobs.get(resume).status = "RUNNING";
execute(jobs.get(resume).args);
jobs.get(resume).status = "FINISHED";
assert.match(execute({ mode: "status" }).error, /scan failed/);
assert.equal(queue.length, 0);
jobs.set("active-native", { status: "RUNNING", progress: 0.5 });
jobs.set("active-worker", { status: "READY" });
for (const [phase, label] of [["generate", "Missing pHashes"], ["identify", "StashDB"], ["fallback", "ThePornDB metadata"]]) {
  config.state = { phase, ids: ["2"], rows: [{ id: "2", title: "Last saved row" }], results: [], stashdb: db, tpdb: tp, job: "active-native", workerJob: "active-worker" };
  const savedState = JSON.stringify(config);
  const progress = execute({ mode: "status" });
  assert.equal(progress.running, true);
  assert.equal(progress.message, `${label}: RUNNING (50%)`);
  assert.equal(progress.state.rows[0].stashdb, "Matched");
  assert.equal(progress.state.rows[0].tpdb, "Matched");
  assert.equal(progress.state.rows[0].completion, "Done", "Live saved metadata reports completion without a final link result");
  assert.equal(JSON.stringify(config), savedState);
}
progressOffline = true;
const offline = execute({ mode: "status" });
assert.equal(offline.state.rows[0].title, "Last saved row");
assert.match(offline.state.progressError, /Scene reads unavailable/);
progressOffline = false;
assert.equal(execute({ mode: "status" }).state.progressError, undefined);
console.log("Scene Import background checks passed");
