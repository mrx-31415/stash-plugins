(function (root) {
  "use strict";

  const key = "scene-import.pending";
  const trackingTag = "[Scene Import Incomplete]";
  const route = "/plugins/scene-import";
  const scenesQuery = "query { findScenes(filter:{per_page:-1}) { scenes { id } } }";
  const rowFields = "id title paths{screenshot} files{basename duration fingerprints{type value}} stash_ids{endpoint stash_id} tags{name}";
  const matchFields = "remote_site_id title code date details director urls image duration fingerprints{algorithm hash duration} studio{stored_id remote_site_id name image urls details aliases parent{stored_id remote_site_id name}} tags{stored_id remote_site_id name description alias_list} performers{stored_id remote_site_id name disambiguation gender urls birthdate ethnicity country eye_color height measurements fake_tits penis_length circumcised career_start career_end tattoos piercings aliases images details death_date hair_color weight}";
  const scrapeQuery = `query($source:ScraperSourceInput!,$input:ScrapeSingleSceneInput!){scrapeSingleScene(source:$source,input:$input){${matchFields}}}`;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function ambiguityTag(source) { return `Scene Import: ambiguous ${source}`; }

  function hasProviderID(scene, endpoint) {
    return scene.stash_ids.some((link) => link.stash_id && link.endpoint.replace(/\/$/, "") === endpoint?.replace(/\/$/, ""));
  }

  function hasPhash(scene) {
    return scene.files?.some((file) => file.fingerprints?.some((fp) => fp.type.toUpperCase() === "PHASH" && fp.value));
  }

  function matchStrength(files, candidate) {
    let strength = 0;
    for (const file of files || []) {
      for (const local of file.fingerprints || []) {
        const algorithm = local.type.toLowerCase();
        if (!["md5", "oshash"].includes(algorithm) || !local.value) continue;
        for (const remote of candidate.fingerprints || []) {
          if (remote.algorithm.toLowerCase() !== algorithm || !remote.hash || remote.hash.toLowerCase() !== local.value.toLowerCase()) continue;
          if (algorithm === "md5") strength = 2;
          else if (file.duration > 0 && remote.duration > 0 && Math.abs(file.duration - remote.duration) <= 1) strength = Math.max(strength, 1);
        }
      }
    }
    return strength;
  }

  function selectMatches(files, candidates) {
    const distinct = new Map();
    for (const candidate of candidates) {
      if (!candidate.remote_site_id) continue;
      const previous = distinct.get(candidate.remote_site_id);
      distinct.set(candidate.remote_site_id, previous ? { ...previous, fingerprints: [...(previous.fingerprints || []), ...(candidate.fingerprints || [])] } : candidate);
    }
    const matches = [...distinct.values()];
    const strongest = Math.max(0, ...matches.map((match) => matchStrength(files, match)));
    return { matches: strongest ? matches.filter((match) => matchStrength(files, match) === strongest) : matches, reason: strongest === 2 ? "Exact MD5" : strongest === 1 ? "Exact OShash + duration" : "Fingerprint lookup" };
  }

  async function saveSelectedMetadata(gql, id, selected, endpoint) {
    const query = `query($id:ID!){findScene(id:$id){${rowFields} organized urls performers{id} tags{id}}}`;
    let { findScene: scene } = await gql(query, { id });
    if (!scene) throw new Error("Scene no longer exists");
    if (scene.organized) return scene;
    if (!matchStrength(scene.files, selected)) throw new Error("Selected fingerprint evidence changed; retry the scene");
    if (linkedIDs(scene.stash_ids, [selected], endpoint).reason === "conflicting ID") throw new Error("Selected match conflicts with an existing provider ID");
    const entityIDs = new Map();
    async function resolve(kind, entity) {
      if (!entity) return null;
      if (entity.stored_id) return entity.stored_id;
      if (!entity.name) throw new Error(`Selected match has a ${kind} without a name`);
      if (kind === "performer" && !entity.disambiguation && entity.name.trim().split(/\s+/).length === 1) return null;
      const key = `${kind}:${entity.remote_site_id || entity.name}`;
      if (entityIDs.has(key)) return entityIDs.get(key);
      const fields = kind === "performer" ? ["name", "disambiguation", "urls", "birthdate", "ethnicity", "country", "eye_color", "measurements", "fake_tits", "career_start", "career_end", "tattoos", "piercings", "details", "death_date", "hair_color"] : kind === "studio" ? ["name", "urls", "image", "details"] : ["name", "description"];
      const input = Object.fromEntries(fields.filter((field) => entity[field] != null && entity[field] !== "").map((field) => [field, entity[field]]));
      if (entity.remote_site_id) input.stash_ids = [{ endpoint, stash_id: entity.remote_site_id }];
      const aliases = entity.alias_list || entity.aliases?.split(",").map((name) => name.trim()).filter(Boolean);
      if (aliases?.length) input[kind === "performer" ? "alias_list" : "aliases"] = aliases;
      if (kind === "studio" && entity.parent) input.parent_id = await resolve("studio", entity.parent);
      if (kind === "performer") {
        const gender = entity.gender?.trim().toUpperCase().replace(/[\s-]+/g, "_");
        if (["MALE", "FEMALE", "TRANSGENDER_MALE", "TRANSGENDER_FEMALE", "INTERSEX", "NON_BINARY"].includes(gender)) input.gender = gender;
        const circumcised = entity.circumcised?.trim().toUpperCase();
        if (["CUT", "UNCUT"].includes(circumcised)) input.circumcised = circumcised;
        for (const [source, target] of [["height", "height_cm"], ["weight", "weight"], ["penis_length", "penis_length"]]) {
          const value = Number.parseFloat(entity[source]);
          if (Number.isFinite(value)) input[target] = target === "penis_length" ? value : Math.trunc(value);
        }
        if (entity.images?.length) input.image = entity.images[0];
      }
      const type = kind[0].toUpperCase() + kind.slice(1);
      const result = await gql(`mutation($input:${type}CreateInput!){${kind}Create(input:$input){id}}`, { input });
      entityIDs.set(key, result[`${kind}Create`].id);
      return result[`${kind}Create`].id;
    }
    const studio = await resolve("studio", selected.studio);
    const performers = [];
    for (const performer of selected.performers || []) { const id = await resolve("performer", performer); if (id) performers.push(id); }
    const tags = [];
    for (const tag of selected.tags || []) { const id = await resolve("tag", tag); if (id) tags.push(id); }
    ({ findScene: scene } = await gql(query, { id }));
    if (!scene) throw new Error("Scene no longer exists");
    if (scene.organized) return scene;
    if (!matchStrength(scene.files, selected)) throw new Error("Selected fingerprint evidence changed; retry the scene");
    const link = linkedIDs(scene.stash_ids, [selected], endpoint);
    if (link.reason === "conflicting ID") throw new Error("Selected match conflicts with an existing provider ID");
    const input = { id };
    for (const field of ["title", "date", "details", "code", "director"]) if (selected[field]) input[field] = selected[field];
    if (selected.image) input.cover_image = selected.image;
    if (selected.urls?.length) input.urls = [...new Set([...(scene.urls || []), ...selected.urls])];
    if (studio) input.studio_id = studio;
    if (performers.length) input.performer_ids = [...new Set([...(scene.performers || []).map((performer) => performer.id), ...performers])];
    if (tags.length) input.tag_ids = [...new Set([...(scene.tags || []).map((tag) => tag.id), ...tags])];
    if (link.stash_ids) input.stash_ids = link.stash_ids;
    const { sceneUpdate } = await gql(`mutation($input:SceneUpdateInput!){sceneUpdate(input:$input){${rowFields}}}`, { input });
    return sceneUpdate;
  }

  async function trackScenes(gql, ids) {
    const { findTags } = await gql("query($name:String!){findTags(filter:{q:$name,per_page:-1}){tags{id name}}}", { name: trackingTag });
    let tag = findTags.tags.find((tag) => tag.name === trackingTag);
    if (!tag) ({ tagCreate: tag } = await gql("mutation($name:String!){tagCreate(input:{name:$name}){id}}", { name: trackingTag }));
    if (ids.length) await gql("mutation($input:BulkSceneUpdateInput!){bulkSceneUpdate(input:$input){id}}", { input: { ids, tag_ids: { ids: [tag.id], mode: "ADD" } } });
    return tag.id;
  }

  function sceneRow(scene, state) {
    const hasID = (endpoint) => hasProviderID(scene, endpoint);
    const early = state.earlyMatches?.[scene.id];
    const ambiguous = (source) => {
      const latest = early?.[source === "StashDB" ? "stashdb" : "tpdb"];
      if (latest !== undefined) return latest.length > 1;
      return (source === "StashDB" && state.ambiguousIDs?.includes(scene.id)) || (scene.tags || []).some((tag) => tag.name === ambiguityTag(source));
    };
    const result = state.results.find((r) => r.id === scene.id)?.result;
    const stashdb = hasID(state.stashdb) ? "Matched" : ambiguous("StashDB") ? "Ambiguous" : ["scan", "generate", "identify"].includes(state.phase) && early?.stashdb?.length === 1 ? "Match found" : ["scan", "generate"].includes(state.phase) ? "Waiting" : state.phase === "identify" ? "Searching" : "No saved match";
    let tpdb = hasID(state.tpdb) ? "Matched" : ambiguous("ThePornDB") ? "Ambiguous" : early?.tpdb?.length === 1 ? "Match found" : ["scan", "generate", "identify"].includes(state.phase) ? "Waiting" : state.phase === "fallback" && state.fallbackIDs?.includes(scene.id) ? "Searching" : "Waiting for ID lookup";
    if (result === "linked" || result === "already linked") tpdb = "Matched";
    else if (result === "multiple matches") tpdb = "Ambiguous";
    else if (result === "no match") tpdb = "No match";
    else if (result) tpdb = result;
    const review = result && (!["linked", "already linked", "no match"].includes(result) || (result === "no match" && stashdb !== "Matched"));
    return {
      id: scene.id, title: scene.title || scene.files?.[0]?.basename || `Scene ${scene.id}`,
      file: scene.files?.map((file) => file.basename).join(", ") || "",
      screenshot: scene.paths?.screenshot || "",
      files: scene.files,
      stashdbEvidence: state.matchReasons?.[scene.id]?.stashdb || "",
      tpdbEvidence: state.matchReasons?.[scene.id]?.tpdb || "",
      phash: hasPhash(scene) ? "Ready" : ["scan", "generate"].includes(state.phase) ? "Generating" : "Not available",
      stashdb, tpdb, completion: result ? review ? "Needs review" : !hasID(state.stashdb) || !hasID(state.tpdb) || !hasPhash(scene) ? "Incomplete" : "Done" : "In progress",
    };
  }

  async function matchReadyScenes(gql, state, save, status, pause) {
    state.earlyMatches = state.earlyMatches || {};
    for (const row of state.rows || []) {
      if (row.phash !== "Ready") continue;
      const matches = state.earlyMatches[row.id] = state.earlyMatches[row.id] || {};
      for (const [source, endpoint] of [["stashdb", state.stashdb], ["tpdb", state.tpdb]]) {
        if (matches[source] !== undefined || row[source] === "Matched") continue;
        status(`Matching during generation: ${row.file || row.title} (${source === "stashdb" ? "StashDB" : "ThePornDB"})`);
        try {
          const { scrapeSingleScene } = await gql(scrapeQuery, { source: { stash_box_endpoint: endpoint }, input: { scene_id: row.id } });
          const selected = selectMatches(row.files, scrapeSingleScene);
          matches[source] = selected.matches.map((match) => match.remote_site_id);
          state.matchReasons = state.matchReasons || {};
          state.matchReasons[row.id] = { ...state.matchReasons[row.id], [source]: selected.reason };
          row[`${source}Evidence`] = selected.reason;
          row[source] = matches[source].length > 1 ? "Ambiguous" : matches[source].length ? "Match found" : "No match yet";
        } catch (error) { state.progressError = `Early matching is temporarily unavailable: ${error.message}`; }
        save(state);
        await pause(500);
      }
    }
  }

  async function refreshRows(gql, state, save) {
    try {
      let scenes = [];
      if (state.phase === "scan") {
        // Poll new IDs only; the final full snapshot comparison still determines import scope.
        const after = state.before.reduce((max, id) => Math.max(max, Number(id)), 0);
        const { findScenes } = await gql(`query($after:Int!){findScenes(scene_filter:{id:{value:$after,modifier:GREATER_THAN}},filter:{per_page:-1}){scenes{${rowFields}}}}`, { after });
        scenes = findScenes.scenes;
      } else if (state.ids?.length) {
        const ids = state.ids;
        const { findScenes } = await gql(`query($ids:[ID!]){findScenes(ids:$ids,filter:{per_page:-1}){scenes{${rowFields}}}}`, { ids });
        scenes = findScenes.scenes;
      }
      state.rows = scenes.map((scene) => sceneRow(scene, state));
      delete state.progressError;
    } catch (error) {
      state.progressError = `Scene progress is temporarily unavailable: ${error.message}`;
    }
    save(state);
  }

  function newSceneIDs(before, after) {
    const existing = new Set(before);
    return after.filter((id) => !existing.has(id));
  }

  function linkedIDs(existing, matches, endpoint) {
    const ids = [...new Set(matches.map((m) => m.remote_site_id).filter(Boolean))];
    if (ids.length !== 1) return { reason: ids.length ? "multiple matches" : "no match" };
    const linked = existing.filter((id) => id.endpoint.replace(/\/$/, "") === endpoint.replace(/\/$/, ""));
    if (linked.length) {
      return { reason: linked.some((id) => id.stash_id === ids[0]) ? "already linked" : "conflicting ID" };
    }
    return { stash_ids: existing.map(({ endpoint, stash_id }) => ({ endpoint, stash_id })).concat({ endpoint, stash_id: ids[0] }) };
  }

  async function waitJob(gql, id, status, pause, observe = async () => {}) {
    for (;;) {
      const { findJob: job } = await gql("query($id:ID!){findJob(input:{id:$id}){status progress error}}", { id });
      if (!job) throw new Error("Job history is unavailable. Check Stash's task log before continuing.");
      status(`${job.status}${job.progress == null ? "" : ` (${Math.round(job.progress * 100)}%)`}`);
      await observe(job);
      if (job.status === "FINISHED") return;
      if (["FAILED", "CANCELLED", "STOPPING"].includes(job.status)) throw new Error(job.error || `Job ${job.status.toLowerCase()}`);
      await pause(3000);
    }
  }

  async function run(gql, state, save, status, pause = sleep) {
    if (!state || ["new", "retry"].includes(state.phase)) {
      const { configuration } = await gql("query{configuration{general{stashBoxes{name endpoint}} defaults{scan{scanGenerateCovers scanGeneratePreviews scanGenerateSprites scanGeneratePhashes scanGenerateImagePreviews scanGenerateImagePhashes scanGenerateThumbnails scanGenerateClipPreviews}}}}");
      const boxes = configuration.general.stashBoxes;
      const stashdb = boxes.find((b) => new URL(b.endpoint).hostname === "stashdb.org");
      const tpdb = boxes.find((b) => new URL(b.endpoint).hostname === "theporndb.net");
      if (!stashdb || !tpdb) throw new Error("Configure StashDB and ThePornDB under Metadata Providers first.");
      if (state?.adoptIDs?.length) await trackScenes(gql, state.adoptIDs);
      if (state?.phase === "retry") {
        status("Checking tracked imports for missing provider IDs");
        const tag = await trackScenes(gql, []);
        const { findScenes } = await gql(`query($tag:ID!){findScenes(scene_filter:{tags:{value:[$tag],modifier:INCLUDES}},filter:{per_page:-1}){scenes{${rowFields}}}}`, { tag });
        const completeScene = (scene) => hasProviderID(scene, stashdb.endpoint) && hasProviderID(scene, tpdb.endpoint) && hasPhash(scene);
        const incomplete = findScenes.scenes.filter((scene) => !completeScene(scene));
        const complete = findScenes.scenes.filter(completeScene).map((scene) => scene.id);
        if (complete.length) await gql("mutation($input:BulkSceneUpdateInput!){bulkSceneUpdate(input:$input){id}}", { input: { ids: complete, tag_ids: { ids: [tag], mode: "REMOVE" } } });
        const generateIDs = incomplete.filter((scene) => !hasPhash(scene)).map((scene) => scene.id);
        state = { phase: generateIDs.length ? "generate" : "identify", generateIDs, retry: true, tracked: true, tagID: tag, ids: incomplete.map((scene) => scene.id), identifyIDs: incomplete.filter((scene) => !hasProviderID(scene, stashdb.endpoint)).map((scene) => scene.id), stashdb: stashdb.endpoint, tpdb: tpdb.endpoint, results: [], rows: [] };
        save(state);
      } else {
        const { findScenes } = await gql(scenesQuery);
        state = { phase: "scan", before: findScenes.scenes.map((s) => s.id), stashdb: stashdb.endpoint, tpdb: tpdb.endpoint, results: [], rows: [] };
        // Persist the scene boundary before submitting any job; a lost response must not expand its scope.
        save(state);
        const { __typename, ...scan } = configuration.defaults.scan;
        const { metadataScan } = await gql("mutation($input:ScanMetadataInput!){metadataScan(input:$input)}", { input: { ...scan, rescan: false, scanGeneratePhashes: true } });
        state.job = metadataScan;
        save(state);
      }
    }
    if (state.phase === "scan") {
      if (!state.job) throw new Error("Scan submission was interrupted. Check Stash tasks, then discard this pending run and start again.");
      status("Waiting for scan and generation");
      await waitJob(gql, state.job, (message) => status(`Scan and generation: ${message}`), pause, async (job) => {
        await refreshRows(gql, state, save);
        // shortcut: discovery queueing needs this tab; use a server hook for unattended scans.
        const queued = new Set(state.queuedIDs || []);
        const discovered = (state.rows || []).map((row) => row.id).filter((id) => !queued.has(id));
        if (discovered.length) {
          state.tagID = await trackScenes(gql, discovered);
          state.queuedIDs = [...queued, ...discovered];
          save(state);
        }
        if (job.status === "RUNNING") await matchReadyScenes(gql, state, save, status, pause);
      });
      const { findScenes } = await gql(scenesQuery);
      state.ids = newSceneIDs(state.before, findScenes.scenes.map((s) => s.id));
      delete state.before;
      delete state.job;
      state.phase = "identify";
      save(state);
    }
    if (state.ids?.length && !state.tracked) {
      const queued = new Set(state.queuedIDs || []);
      const untagged = state.ids.filter((id) => !queued.has(id));
      if (untagged.length) state.tagID = await trackScenes(gql, untagged);
      state.tracked = true;
      save(state);
    }
    if (state.phase === "generate") {
      if (!state.job) {
        if (state.generateSubmitted) throw new Error("Generation submission was interrupted. Check Stash tasks before discarding this run.");
        state.generateSubmitted = true;
        save(state);
        const { metadataGenerate } = await gql("mutation($input:GenerateMetadataInput!){metadataGenerate(input:$input)}", { input: { sceneIDs: state.generateIDs, phashes: true, overwrite: false } });
        state.job = metadataGenerate;
        save(state);
      }
      await waitJob(gql, state.job, (message) => status(`Missing pHashes: ${message}`), pause, async (job) => {
        await refreshRows(gql, state, save);
        if (job.status === "RUNNING") await matchReadyScenes(gql, state, save, status, pause);
      });
      state.phase = "identify";
      delete state.job;
      delete state.generateSubmitted;
      save(state);
    }
    for (const phase of ["identify", "fallback"]) {
      if (state.phase !== phase) continue;
      if (phase === "fallback" && !state.fallbackIDs) {
        const { findScenes } = state.ids.length
          ? await gql("query($ids:[ID!]){findScenes(ids:$ids,filter:{per_page:-1}){scenes{id stash_ids{endpoint stash_id}}}}", { ids: state.ids })
          : { findScenes: { scenes: [] } };
        state.fallbackIDs = findScenes.scenes.filter((scene) => !hasProviderID(scene, state.stashdb) && !hasProviderID(scene, state.tpdb)).map((scene) => scene.id);
        save(state);
      }
      const ids = phase === "identify" ? state.identifyIDs || state.ids : state.fallbackIDs;
      const endpoint = phase === "identify" ? state.stashdb : state.tpdb;
      if (ids.length && !state.job) {
        if (state.identifySubmitted) throw new Error("Identify submission was interrupted. Check Stash tasks before discarding this run.");
        status(`Identifying ${ids.length} new scenes with ${phase === "identify" ? "StashDB" : "ThePornDB"}`);
        const fieldOptions = ["title", "date", "details", "code", "director"].map((field) => ({ field, strategy: "OVERWRITE" }));
        for (const field of ["studio", "performers", "tags"]) fieldOptions.push({ field, strategy: "MERGE", createMissing: true });
        state.identifySubmitted = true;
        save(state);
        const { metadataIdentify } = await gql("mutation($input:IdentifyMetadataInput!){metadataIdentify(input:$input)}", {
          input: { sceneIDs: ids, sources: [{ source: { stash_box_endpoint: endpoint } }], options: { fieldOptions, setCoverImage: true, setOrganized: false, skipMultipleMatches: true, skipSingleNamePerformers: true } },
        });
        state.job = metadataIdentify;
        save(state);
      }
      if (state.job) {
        await waitJob(gql, state.job, (message) => status(`${phase === "identify" ? "StashDB" : "ThePornDB metadata"}: ${message}`), pause, () => refreshRows(gql, state, save));
        {
          const source = phase === "identify" ? "stashdb" : "tpdb";
          state.ambiguousIDs = state.ambiguousIDs || [];
          for (const row of state.rows || []) {
            if (!ids.includes(row.id) || row[source] === "Matched") continue;
            status(`Checking skipped ${phase === "identify" ? "StashDB" : "ThePornDB"} match: ${row.file || row.title}`);
            let applying = false;
            try {
              const { scrapeSingleScene: matches } = await gql(scrapeQuery, { source: { stash_box_endpoint: endpoint }, input: { scene_id: row.id } });
              const selected = selectMatches(row.files, matches);
              const candidateIDs = selected.matches.map((match) => match.remote_site_id);
              state.earlyMatches = state.earlyMatches || {};
              state.earlyMatches[row.id] = { ...state.earlyMatches[row.id], [source]: candidateIDs };
              state.matchReasons = state.matchReasons || {};
              state.matchReasons[row.id] = { ...state.matchReasons[row.id], [source]: selected.reason };
              row[`${source}Evidence`] = selected.reason;
              if (source === "stashdb") state.ambiguousIDs = state.ambiguousIDs.filter((id) => id !== row.id);
              if (candidateIDs.length > 1 && source === "stashdb") {
                state.ambiguousIDs.push(row.id);
              }
              row[source] = candidateIDs.length > 1 ? "Ambiguous" : candidateIDs.length ? "Match found" : "No saved match";
              if (candidateIDs.length === 1 && selected.reason !== "Fingerprint lookup") {
                status(`Saving ${selected.reason} match: ${row.file || row.title}`);
                applying = true;
                const scene = await saveSelectedMetadata(gql, row.id, selected.matches[0], endpoint);
                Object.assign(row, sceneRow(scene, state));
              }
            } catch (error) {
              if (applying) throw error;
              state.progressError = `Skipped-match details are unavailable: ${error.message}`;
            }
            save(state);
            await pause(500);
          }
        }
      }
      state.phase = phase === "identify" ? "fallback" : "link";
      delete state.job;
      delete state.identifySubmitted;
      save(state);
    }
    if (state.phase === "link") {
      await refreshRows(gql, state, save);
      const done = new Set(state.results.map((r) => r.id));
      for (const id of state.ids) {
        if (done.has(id)) continue;
        status(`Adding ThePornDB IDs: ${state.results.length + 1}/${state.ids.length}`);
        const row = state.rows?.find((r) => r.id === id);
        if (row) row.tpdb = state.retry && row.tpdb === "Matched" ? "Checking saved ID" : "Searching";
        save(state);
        try {
          // Read immediately before writing: sceneUpdate replaces the entire stash_ids list.
          const query = `query($id:ID!){findScene(id:$id){${rowFields}}}`;
          let { findScene: scene } = await gql(query, { id });
          if (!scene) throw new Error("Scene no longer exists");
          let link;
          if (hasProviderID(scene, state.tpdb)) link = { reason: "already linked" };
          else {
            const { scrapeSingleScene: matches } = await gql(scrapeQuery, { source: { stash_box_endpoint: state.tpdb }, input: { scene_id: id } });
            ({ findScene: scene } = await gql(query, { id }));
            if (!scene) throw new Error("Scene no longer exists");
            const selected = selectMatches(scene.files, matches);
            state.matchReasons = state.matchReasons || {};
            state.matchReasons[id] = { ...state.matchReasons[id], tpdb: selected.reason };
            link = linkedIDs(scene.stash_ids, selected.matches, state.tpdb);
          }
          if (link.stash_ids) {
            if (row) row.tpdb = "Match found — saving ID";
            save(state);
            await gql("mutation($input:SceneUpdateInput!){sceneUpdate(input:$input){id}}", { input: { id, stash_ids: link.stash_ids } });
            scene = { ...scene, stash_ids: link.stash_ids };
          }
          state.results.push({ id, result: link.reason || "linked" });
          if (row) Object.assign(row, sceneRow(scene, state));
        } catch (error) {
          state.results.push({ id, result: `error: ${error.message}` });
          if (row) { row.tpdb = `Error: ${error.message}`; row.completion = "Needs review"; }
        }
        save(state);
        await pause(500);
      }
      state.phase = "cleanup";
      save(state);
    }
    if (state.phase === "cleanup") {
      await refreshRows(gql, state, save);
      const complete = state.progressError ? [] : (state.rows || []).filter((row) => row.completion === "Done").map((row) => row.id);
      if (complete.length) await gql("mutation($input:BulkSceneUpdateInput!){bulkSceneUpdate(input:$input){id}}", { input: { ids: complete, tag_ids: { ids: [state.tagID], mode: "REMOVE" } } });
      if (state.ids.length) {
        const { findTags } = await gql("query($name:String!){findTags(filter:{q:$name,per_page:-1}){tags{id name}}}", { name: "Scene Import: ambiguous" });
        const legacy = findTags.tags.filter((tag) => [ambiguityTag("StashDB"), ambiguityTag("ThePornDB")].includes(tag.name)).map((tag) => tag.id);
        state.ambiguousIDs = [...new Set([...(state.ambiguousIDs || []), ...(state.rows || []).filter((row) => row.stashdb === "Ambiguous").map((row) => row.id)])];
        if (legacy.length) await gql("mutation($input:BulkSceneUpdateInput!){bulkSceneUpdate(input:$input){id}}", { input: { ids: state.ids, tag_ids: { ids: legacy, mode: "REMOVE" } } });
      }
      state.phase = "done";
      save(state);
    }
    status(`All done: ${state.ids.length} ${state.retry ? "tracked scenes retried" : "new scenes"}; ${state.results.filter((r) => r.result === "linked").length} additional ThePornDB IDs added; ${state.rows?.filter((r) => r.completion === "Incomplete").length || 0} incomplete; ${state.rows?.filter((r) => r.completion === "Needs review").length || 0} need review.`);
    return state;
  }

  function createController(storage, gql, pause = sleep) {
    const state = JSON.parse(storage.getItem(key) || "null");
    let view = { state, message: state?.message || "", error: state?.error || "", running: false };
    const listeners = new Set();
    let task;
    function publish(next) {
      view = { ...view, ...next };
      if (view.state) storage.setItem(key, JSON.stringify({ ...view.state, message: view.message, error: view.error }));
      else storage.removeItem(key);
      for (const listener of listeners) listener(view);
    }
    return {
      snapshot: () => view,
      subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
      discard() { if (!view.running) publish({ state: null, message: "", error: "" }); },
      refresh() {
        if (!view.running && view.state) {
          const current = view.state;
          return refreshRows(gql, JSON.parse(JSON.stringify(current)), (next) => {
            if (!view.running && view.state === current) publish({ state: JSON.parse(JSON.stringify(next)) });
          });
        }
      },
      start(mode = "scan") {
        if (view.running) return task;
        let pending = view.state && view.state.phase !== "done" ? JSON.parse(JSON.stringify(view.state)) : null;
        if (mode === "retry" && pending) {
          publish({ error: "Finish or resume the pending import before starting a retry." });
          return Promise.resolve();
        }
        if (!pending) pending = { phase: mode === "retry" ? "retry" : "new", adoptIDs: view.state?.tracked ? [] : view.state?.ids || [], results: [], rows: [] };
        const resuming = !["new", "retry"].includes(pending.phase);
        publish({ state: pending, running: true, error: "", message: resuming ? "Resuming import…" : mode === "retry" ? "Starting tracked retry…" : "Starting import…" });
        task = run(gql, pending, (next) => publish({ state: JSON.parse(JSON.stringify(next)) }), (message) => publish({ message }), pause)
          .catch((error) => publish({ error: error.message }))
          .finally(() => publish({ running: false }));
        return task;
      },
    };
  }

  if (typeof module !== "undefined" && module.exports) module.exports = { run, linkedIDs, newSceneIDs, waitJob, sceneRow, refreshRows, createController, hasProviderID, matchReadyScenes, selectMatches, saveSelectedMetadata };
  const api = root.PluginApi;
  if (!api) return;
  const { React, libraries, register, patch, utils } = api;
  const { Button, Alert, Table, Badge } = libraries.Bootstrap;
  const { Link, NavLink } = libraries.ReactRouterDOM;
  const h = React.createElement;

  async function gql(query, variables = {}) {
    const client = utils.StashService.getClient();
    const document = libraries.Apollo.gql(query);
    const result = query.startsWith("mutation")
      ? await client.mutate({ mutation: document, variables })
      : await client.query({ query: document, variables, fetchPolicy: "network-only" });
    if (result.errors?.length) throw new Error(result.errors.map((e) => e.message).join("; "));
    return result.data;
  }

  const controller = createController(root.localStorage, gql);
  function Page() {
    const [view, setView] = React.useState(controller.snapshot);
    React.useEffect(() => {
      const unsubscribe = controller.subscribe(setView);
      setView(controller.snapshot());
      controller.refresh();
      return unsubscribe;
    }, []);
    const { state, running: busy, message, error } = view;
    const pending = state && state.phase !== "done";
    const rows = state?.rows || [];
    const badge = (text) => h(Badge, { variant: ["Matched", "Ready", "Done"].includes(text) ? "success" : ["Match found"].includes(text) ? "info" : ["Ambiguous", "Needs review", "Incomplete", "Not available", "No saved match", "No match"].includes(text) ? "warning" : text.startsWith("error:") || text.startsWith("Error:") ? "danger" : "secondary" }, text);
    return h("div", { className: "container my-4" },
      h("h2", null, "Scene Import"),
      h("p", null, "Scan your configured library folders, wait for generation, identify newly added scenes with StashDB, then use ThePornDB for metadata where StashDB did not match. Existing scenes are excluded."),
      h("p", null, "Both metadata passes create missing studios, performers and tags. For StashDB matches, ThePornDB adds only its Stash-ID. Multiple matches and conflicting IDs are left for review."),
      h("p", null, "Provider lookups start as soon as each pHash is ready, while generation continues. Match found means a candidate is ready; Matched means its metadata or ID has been saved."),
      h("p", null, "You can browse other Stash pages and return here while this tab keeps importing. After a refresh or closing the tab, return and click Resume import. Use one tab for imports."),
      h(Button, { onClick: () => controller.start(), disabled: busy }, busy ? "Import running…" : pending ? "Resume import" : "Scan & identify new scenes"),
      h(Button, { className: "ml-2", variant: "secondary", disabled: busy || !!pending, onClick: () => controller.start("retry") }, "Retry incomplete imports"),
      h("p", { className: "mt-2" }, "Unfinished imports keep [Scene Import Incomplete]. Retry checks only that queue, generates missing pHashes, and fetches missing provider matches. The tag is removed once the pHash and both IDs are saved successfully."),
      pending && !busy && h(Button, { className: "ml-2", variant: "secondary", onClick: () => controller.discard() }, "Discard pending run"),
      message && h(Alert, { className: "mt-3", variant: "info", role: "status" }, message),
      error && h(Alert, { className: "mt-3", variant: "danger", role: "alert" }, error),
      state?.progressError && h(Alert, { variant: "warning" }, state.progressError, " Last saved progress is kept.", !busy && h(Button, { className: "ml-2", size: "sm", variant: "secondary", onClick: () => controller.refresh() }, "Retry progress")),
      h("p", { className: "mt-3", role: "status" }, `${rows.length} scenes · ${rows.filter((row) => row.phash === "Ready").length} pHashes ready · ${rows.filter((row) => row.completion === "Done").length} done · ${rows.filter((row) => row.completion === "Incomplete").length} incomplete · ${rows.filter((row) => row.completion === "Needs review").length} need review`),
      rows.length > 0 && h(Table, { responsive: true, striped: true, size: "sm", className: "scene-import-table" },
        h("thead", null, h("tr", null, ["Found scene / file", "pHash", "StashDB", "ThePornDB", "Result"].map((label) => h("th", { key: label, scope: "col" }, label)))),
        h("tbody", null, rows.map((row) => h("tr", { key: row.id, className: "scene-card" },
          h("td", null, h(Link, { to: `/scenes/${row.id}`, "aria-label": `Open ${row.title}` }, "Open scene"), h("div", { className: "card-section" }, row.screenshot && h("img", { src: row.screenshot, className: "scene-card-image scene-import-thumbnail", alt: "", loading: "lazy" }), h("div", { className: "card-section-title" }, row.title), h("small", null, row.file))),
          h("td", null, badge(row.phash)), h("td", null, badge(row.stashdb), row.stashdbEvidence && h("small", { className: "d-block" }, row.stashdbEvidence), row.stashdb === "Ambiguous" && state.earlyMatches?.[row.id]?.stashdb && h("small", { className: "d-block" }, `${state.earlyMatches[row.id].stashdb.length} candidates`)), h("td", null, badge(row.tpdb), row.tpdbEvidence && h("small", { className: "d-block" }, row.tpdbEvidence)), h("td", null, badge(row.completion)))))
      ),
      !rows.length && state?.results.length > 0 && h("ul", null, state.results.map((r) => h("li", { key: r.id }, h(Link, { to: `/scenes/${r.id}` }, `Scene ${r.id}`), `: ${r.result}`)))
    );
  }
  register.route(route, Page);
  patch.before("MainNavBar.UtilityItems", (props) => [{ children: h(React.Fragment, null, props.children, h(NavLink, { className: "nav-utility", to: route }, h(Button, { className: "minimal d-flex align-items-center h-100", title: "Scene Import", "aria-label": "Scene Import" }, h(api.components.Icon, { icon: libraries.FontAwesomeSolid.faFileImport })))) }]);
})(typeof window !== "undefined" ? window : globalThis);
