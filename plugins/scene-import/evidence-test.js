"use strict";
const assert = require("node:assert/strict");
const { selectMatches, saveSelectedMetadata, run } = require("./scene-import.js");

module.exports = async function () {
  const db = "https://stashdb.org/graphql";
  const tp = "https://theporndb.net/graphql";
  const files = [{ duration: 2180.5, fingerprints: [{ type: "oshash", value: "local-os" }, { type: "phash", value: "local-ph" }] }];
  const weak = { remote_site_id: "weak", fingerprints: [{ algorithm: "phash", hash: "local-ph", duration: 2180 }] };
  const strong = { remote_site_id: "strong", title: "Correct title", fingerprints: [{ algorithm: "OSHASH", hash: "LOCAL-OS", duration: 2180 }] };
  assert.deepEqual(selectMatches(files, [weak, strong]), { matches: [strong], reason: "Exact OShash + duration" });
  assert.equal(selectMatches(files, [weak, { ...strong, fingerprints: [{ algorithm: "oshash", hash: "local-os", duration: 2310 }] }]).matches.length, 2, "An OShash match needs matching duration");
  assert.equal(selectMatches(files, [strong, { ...strong, remote_site_id: "same-evidence" }]).matches.length, 2, "Equal exact evidence remains ambiguous");
  const md5 = { remote_site_id: "md5", fingerprints: [{ algorithm: "MD5", hash: "FULL-CHECKSUM", duration: 2310 }] };
  const md5Files = [{ ...files[0], fingerprints: [...files[0].fingerprints, { type: "md5", value: "full-checksum" }] }];
  assert.deepEqual(selectMatches(md5Files, [strong, md5]), { matches: [md5], reason: "Exact MD5" });
  assert.equal(selectMatches(md5Files, [md5, { ...md5, remote_site_id: "md5-tie" }]).matches.length, 2);
  assert.equal(selectMatches(files, [weak, { ...weak, remote_site_id: "more-votes", fingerprints: Array(19).fill(weak.fingerprints[0]) }]).matches.length, 2, "Submission counts alone must not resolve competing pHash results");
  assert.deepEqual(selectMatches(files, [strong, { remote_site_id: "strong", fingerprints: [] }, weak]).matches.map((match) => match.remote_site_id), ["strong"], "Duplicate entries retain their evidence");

  const metadata = { ...strong, date: "2020-01-02", code: "chosen-code", details: "Chosen details", director: "Chosen director", urls: ["https://example.test/chosen"], image: "https://example.test/cover", studio: { stored_id: "known-studio" }, performers: [
    { stored_id: "known-performer", name: "Known Performer" },
    { name: "New Performer", remote_site_id: "remote-performer", gender: "female", height: "170", weight: "60", aliases: "Alias A,Alias B", images: ["https://example.test/portrait"] },
    { name: "New Performer", remote_site_id: "remote-performer" },
    { name: "Generic" },
  ], tags: [{ name: "New tag", remote_site_id: "remote-tag", description: "Description", alias_list: ["Tag alias"] }] };
  let reads = 0;
  const created = [];
  let input;
  const scene = { id: "chosen", files, organized: false, performers: [{ id: "existing-performer" }], tags: [{ id: "queue-tag", name: "[Scene Import Incomplete]" }], urls: ["https://example.test/local"], stash_ids: [{ endpoint: tp, stash_id: "keep-tp" }] };
  const gql = async (query, vars) => {
    if (query.includes("findScene(")) {
      reads++;
      return { findScene: reads > 1 ? { ...scene, tags: [...scene.tags, { id: "concurrent-tag" }], stash_ids: [...scene.stash_ids, { endpoint: "https://example.test/graphql", stash_id: "concurrent-id" }] } : scene };
    }
    if (query.includes("performerCreate(")) { created.push({ kind: "performer", input: vars.input }); return { performerCreate: { id: "new-performer" } }; }
    if (query.includes("tagCreate(")) { created.push({ kind: "tag", input: vars.input }); return { tagCreate: { id: "new-tag" } }; }
    if (query.includes("sceneUpdate(")) { input = vars.input; return { sceneUpdate: { ...scene, title: vars.input.title, stash_ids: vars.input.stash_ids } }; }
    throw new Error(`Unexpected selected-metadata request: ${query}`);
  };
  const saved = await saveSelectedMetadata(gql, "chosen", metadata, db);
  assert.equal(saved.title, "Correct title");
  assert.equal(created.filter((entity) => entity.kind === "performer").length, 1);
  assert.deepEqual(created[0].input, { name: "New Performer", stash_ids: [{ endpoint: db, stash_id: "remote-performer" }], alias_list: ["Alias A", "Alias B"], gender: "FEMALE", height_cm: 170, weight: 60, image: "https://example.test/portrait" });
  assert.deepEqual(created[1].input, { name: "New tag", description: "Description", stash_ids: [{ endpoint: db, stash_id: "remote-tag" }], aliases: ["Tag alias"] });
  assert.deepEqual(input.performer_ids, ["existing-performer", "known-performer", "new-performer"]);
  assert.deepEqual(input.tag_ids, ["queue-tag", "concurrent-tag", "new-tag"]);
  assert.equal(input.studio_id, "known-studio");
  assert.equal(input.cover_image, "https://example.test/cover");
  assert.deepEqual(input.urls, ["https://example.test/local", "https://example.test/chosen"]);
  assert.ok(input.stash_ids.some((link) => link.stash_id === "concurrent-id"));
  assert.ok(input.stash_ids.some((link) => link.endpoint === db && link.stash_id === "strong"));
  assert.equal(scene.stash_ids.length, 1, "Fetched objects must not be mutated");
  await assert.rejects(saveSelectedMetadata(async () => ({ findScene: { ...scene, files: [] } }), "chosen", metadata, db), /fingerprint evidence changed/);
  await assert.rejects(saveSelectedMetadata(async () => ({ findScene: { ...scene, stash_ids: [{ endpoint: db, stash_id: "conflict" }] } }), "chosen", metadata, db), /conflicts/);
  const organized = await saveSelectedMetadata(async (query) => {
    assert.ok(query.startsWith("query"));
    return { findScene: { ...scene, organized: true } };
  }, "chosen", metadata, db);
  assert.equal(organized.organized, true);
  let sceneWrittenOnFailure = false;
  await assert.rejects(saveSelectedMetadata(async (query) => {
    if (query.includes("findScene(")) return { findScene: scene };
    if (query.includes("performerCreate(")) throw new Error("Entity creation failed");
    if (query.includes("sceneUpdate(")) sceneWrittenOnFailure = true;
    throw new Error("Unexpected mutation");
  }, "chosen", metadata, db), /Entity creation failed/);
  assert.equal(sceneWrittenOnFailure, false);
  let newStudio;
  await saveSelectedMetadata(async (query, vars) => {
    if (query.includes("findScene(")) return { findScene: scene };
    if (query.includes("studioCreate(")) { newStudio = vars.input; return { studioCreate: { id: "new-studio" } }; }
    if (query.includes("sceneUpdate(")) { assert.equal(vars.input.studio_id, "new-studio"); return { sceneUpdate: scene }; }
    throw new Error(`Unexpected studio creation request: ${query}`);
  }, "chosen", { ...strong, studio: { name: "Child Studio", remote_site_id: "child-studio", aliases: "Child alias", parent: { stored_id: "parent-studio" } } }, db);
  assert.deepEqual(newStudio, { name: "Child Studio", stash_ids: [{ endpoint: db, stash_id: "child-studio" }], aliases: ["Child alias"], parent_id: "parent-studio" });

  let live = { id: "chosen", files, organized: false, title: "Filename", urls: [], performers: [], tags: [{ id: "queue-tag", name: "[Scene Import Incomplete]" }], stash_ids: [{ endpoint: tp, stash_id: "keep-tp" }] };
  let chosenWrite;
  const workflow = async (query, vars) => {
    if (query.includes("findJob(")) return { findJob: { status: "FINISHED" } };
    if (query.includes("findScenes(")) return { findScenes: { scenes: [live] } };
    if (query.includes("findScene(")) return { findScene: live };
    if (query.includes("scrapeSingleScene(")) {
      assert.equal(vars.source.stash_box_endpoint, db);
      return { scrapeSingleScene: [weak, strong] };
    }
    if (query.includes("sceneUpdate(")) {
      chosenWrite = vars.input;
      live = { ...live, title: vars.input.title, stash_ids: vars.input.stash_ids };
      return { sceneUpdate: live };
    }
    if (query.includes("findTags(")) return { findTags: { tags: [] } };
    if (query.includes("bulkSceneUpdate(")) return { bulkSceneUpdate: [] };
    throw new Error(`A selected strong match must not run an unrestricted Identify task: ${query}`);
  };
  const initial = { phase: "identify", job: "native-skipped-multiple", ids: ["chosen"], tracked: true, tagID: "queue-tag", stashdb: db, tpdb: tp, results: [] };
  const result = await run(workflow, initial, () => {}, () => {}, async () => {});
  assert.equal(chosenWrite.title, "Correct title", "Apply the stronger entry even when it is not result[0]");
  assert.ok(chosenWrite.stash_ids.some((link) => link.stash_id === "strong"));
  assert.equal(result.rows[0].stashdb, "Matched");
  assert.equal(result.rows[0].stashdbEvidence, "Exact OShash + duration");
  assert.equal(result.rows[0].completion, "Done");
  live = { ...live, stash_ids: [{ endpoint: tp, stash_id: "keep-tp" }] };
  let pending;
  await assert.rejects(run(async (query, vars) => {
    if (query.includes("sceneUpdate(")) throw new Error("Metadata write failed");
    return workflow(query, vars);
  }, { ...initial, phase: "identify", job: "native-skipped-multiple", results: [], earlyMatches: {} }, (next) => { pending = JSON.parse(JSON.stringify(next)); }, () => {}, async () => {}), /Metadata write failed/);
  assert.equal(pending.phase, "identify");
  assert.equal(pending.job, "native-skipped-multiple", "A failed exact-match save must remain resumable without repeating the native job");
};
