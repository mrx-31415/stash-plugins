(function (root) {
  "use strict";

  function mergeGapRows(gaps, rows, localTags) {
    rows.forEach(function (row) {
      const key = row.name.toLowerCase();
      const existing = gaps.get(key);
      if (existing) {
        existing.scene_ids.push.apply(existing.scene_ids, row.scene_ids);
        existing.scene_count = existing.scene_ids.length;
      } else {
        gaps.set(key, {
          name: row.name,
          scene_count: row.scene_count,
          scene_ids: row.scene_ids.slice(),
          is_local: localTags.has(key),
        });
      }
    });
    return Array.from(gaps.values()).sort(function (left, right) {
      return right.scene_count - left.scene_count || left.name.localeCompare(right.name);
    });
  }

  function visibleRows(rows, search, showLocal, localTags) {
    const query = search.toLowerCase();
    const knownLocalTags = localTags || new Set();
    return rows.filter(function (row) {
      const isLocal = row.is_local || knownLocalTags.has(row.name.toLowerCase());
      return (showLocal || !isLocal) && row.name.toLowerCase().includes(query);
    });
  }

  function markLocal(rows, name) {
    return markLocalNames(rows, [name]);
  }

  function markLocalNames(rows, names) {
    const keys = new Set(names.map(function (name) { return name.toLowerCase(); }));
    return rows.map(function (row) {
      return keys.has(row.name.toLowerCase()) ? Object.assign({}, row, { is_local: true }) : row;
    });
  }

  function terminalJobStatus(status) {
    return ["FINISHED", "FAILED", "CANCELLED"].includes(status);
  }

  const PULL_PLACEHOLDER = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 160 90'%3E%3Crect width='160' height='90' fill='%23343a40'/%3E%3Ctext x='80' y='48' fill='white' text-anchor='middle' font-size='12'%3ENo screenshot%3C/text%3E%3C/svg%3E";

  function pullRows(state) {
    return state && Array.isArray(state.rows) ? state.rows : [];
  }

  function pullSummary(state) {
    const rows = pullRows(state);
    return {
      current: Number(state && state.scanned) || 0,
      total: Number(state && state.total) || 0,
      changed: Number(state && state.changed) || 0,
      tagsAdded: Number(state && state.tags_added) || 0,
      failures: Number(state && state.failure_count) || rows.filter(function (row) { return row.error; }).length,
    };
  }

  function pullEmptyState(state) {
    if (!state || state.status === "waiting") return "No pull has run yet. Click Pull now to process all configured metadata providers.";
    if (state.status === "running") return "No changed or failed scenes yet.";
    if (state.status === "completed") return "No scenes received new matching tags.";
    return "No scene results were recorded.";
  }

  function activeTab(current, tab) {
    return current === tab;
  }

  function resumeCleanupTab(current, state) {
    return state && state.status === "running" ? "cleanup" : current;
  }

  function cleanupTags(review) {
    return review && Array.isArray(review.items) ? review.items : [];
  }

  function cleanupDuplicates(review) {
    return cleanupTags(review);
  }

  function cleanupSplits(review) {
    return cleanupTags(review);
  }

  function cleanupReviewArgs(token, section, view, selectedIds) {
    return {
      mode: "cleanup_review",
      cleanup_token: token,
      section: section,
      page: view.page,
      per_page: view.per_page || 50,
      query: view.query,
      filter: view.filter,
      sort: view.sort,
      selected_ids: selectedIds,
    };
  }

  function cleanupCandidateArgs(token, parentTagId, page) {
    return {
      mode: "cleanup_candidates",
      cleanup_token: token,
      parent_tag_id: String(parentTagId),
      page: page,
      per_page: 100,
    };
  }

  function splitCandidateGroups(candidates) {
    const groups = [];
    const byAlias = new Map();
    (candidates || []).forEach(function (candidate) {
      const alias = candidate.alias || "No alias";
      if (!byAlias.has(alias)) {
        const group = { alias: alias, exact: [], fuzzy: [], manual: [] };
        byAlias.set(alias, group);
        groups.push(group);
      }
      const group = byAlias.get(alias);
      if (candidate.exact) group.exact.push(candidate);
      else if (candidate.remote_name || candidate.scene_count) group.fuzzy.push(candidate);
      else group.manual.push(candidate);
    });
    return groups;
  }

  function updateSplitChoices(current, splitId, candidate, changes, remove) {
    const next = Object.assign({}, current);
    const choices = (next[splitId] || []).slice();
    const index = choices.findIndex(function (item) { return item.candidate_id === candidate.id; });
    if (remove) {
      if (index >= 0) choices.splice(index, 1);
    } else {
      const base = {
        candidate_id: candidate.id,
        action: candidate.action || "child-only",
        child_name: candidate.child_name || candidate.remote_name || candidate.alias || "",
        remove_alias: true,
      };
      choices[index >= 0 ? index : choices.length] = Object.assign({}, index >= 0 ? choices[index] : base, changes || {});
    }
    if (choices.length) next[splitId] = choices;
    else delete next[splitId];
    return next;
  }

  function duplicateReviewed(group, choice) {
    return Boolean(
      choice && choice.survivor_id && choice.source_ids && choice.source_ids.length &&
      (!(group.conflicts || []).length || choice.override_remote_ids)
    );
  }

  function cleanupApplyArgs(token, backupReady, junkIds, duplicateChoices, splitChoices) {
    return {
      mode: "cleanup_apply",
      cleanup_token: token,
      backup_confirmed: Boolean(backupReady),
      junk_ids: Array.from(junkIds || []).map(String),
      duplicates: Object.keys(duplicateChoices || {}).map(function (groupId) {
        const choice = duplicateChoices[groupId] || {};
        return {
          group_id: groupId,
          survivor_id: choice.survivor_id || "",
          source_ids: (choice.source_ids || []).map(String),
          override_remote_ids: Boolean(choice.override_remote_ids),
        };
      }),
      splits: Object.keys(splitChoices || {}).map(function (tagId) {
        return {
          tag_id: tagId,
          candidates: (splitChoices[tagId] || []).map(function (candidate) {
            return {
              candidate_id: candidate.candidate_id || candidate.id,
              action: candidate.action || "child-only",
              child_name: candidate.child_name,
              remove_alias: candidate.remove_alias !== false,
            };
          }),
        };
      }),
    };
  }

  function cleanupCanApply(state, token, backupReady, hasSelections) {
    return Boolean(
      token && backupReady && hasSelections && state &&
      ["completed", "applied"].includes(state.status)
    );
  }

  function cleanupPageInfo(review) {
    const page = Number(review && review.page) || 1;
    const perPage = Number(review && review.per_page) || 50;
    const total = Number(review && review.total) || 0;
    return {
      start: total ? ((page - 1) * perPage) + 1 : 0,
      end: Math.min(page * perPage, total),
      hasPrevious: page > 1,
      hasNext: page * perPage < total,
    };
  }

  function newScanToken() {
    if (typeof window !== "undefined" && window.crypto && window.crypto.randomUUID) {
      return window.crypto.randomUUID();
    }
    return Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
  }

  function storedScan() {
    try {
      return JSON.parse(window.localStorage.getItem("tag-organizer.scan"));
    } catch (error) {
      return null;
    }
  }

  function saveScan(scan) {
    window.localStorage.setItem("tag-organizer.scan", JSON.stringify(scan));
  }

  const api = root.PluginApi;
  if (!api) {
    if (typeof process !== "undefined" && process.argv.includes("--self-test")) {
      const assert = require("node:assert/strict");
      const gaps = new Map();
      const localTags = new Set(["existing", "alias"]);
      mergeGapRows(gaps, [{ name: "New", scene_count: 1, scene_ids: ["1"] }], localTags);
      const merged = mergeGapRows(gaps, [
          { name: "new", scene_count: 1, scene_ids: ["2"] },
          { name: "Another", scene_count: 1, scene_ids: ["3"] },
          { name: "Existing", scene_count: 1, scene_ids: ["4"] },
          { name: "ALIAS", scene_count: 1, scene_ids: ["5"] },
        ], localTags);
      assert.deepEqual(
        merged,
        [
          { name: "New", scene_count: 2, scene_ids: ["1", "2"], is_local: false },
          { name: "ALIAS", scene_count: 1, scene_ids: ["5"], is_local: true },
          { name: "Another", scene_count: 1, scene_ids: ["3"], is_local: false },
          { name: "Existing", scene_count: 1, scene_ids: ["4"], is_local: true },
        ]
      );
      assert.deepEqual(visibleRows(merged, "NE", false).map(function (row) { return row.name; }), ["New"]);
      assert.deepEqual(visibleRows(merged, "", false).map(function (row) { return row.name; }), ["New", "Another"]);
      assert.deepEqual(visibleRows(merged, "ist", true).map(function (row) { return row.name; }), ["Existing"]);
      assert.deepEqual(
        visibleRows([{ name: "Blowjob", is_local: false }], "", false, new Set(["blowjob"])),
        []
      );
      const added = markLocal(merged, "NEW");
      assert.equal(added[0].is_local, true);
      assert.deepEqual(visibleRows(added, "", false).map(function (row) { return row.name; }), ["Another"]);
      assert.equal(markLocalNames(merged, ["another"])[2].is_local, true);
      assert.equal(terminalJobStatus("FINISHED"), true);
      assert.equal(terminalJobStatus("RUNNING"), false);
      const pull = {
        status: "completed",
        scanned: 3,
        total: 3,
        changed: 1,
        tags_added: 2,
        failure_count: 1,
        rows: [
          { scene_id: "1", title: "Changed", added_tags: ["Oral", "Anal"] },
          { scene_id: "2", title: "Failed", added_tags: [], error: "update failed" },
        ],
      };
      assert.deepEqual(pullSummary(pull), { current: 3, total: 3, changed: 1, tagsAdded: 2, failures: 1 });
      assert.equal(pullRows(pull)[0].added_tags[0], "Oral");
      assert.equal(Boolean(pullRows(pull)[1].error), true);
      assert.equal(pullEmptyState(null).startsWith("No pull has run"), true);
      assert.equal(pullEmptyState({ status: "running", rows: [] }), "No changed or failed scenes yet.");
      assert.equal(pullEmptyState({ status: "completed", rows: [] }), "No scenes received new matching tags.");
      assert.equal(activeTab("pull", "pull"), true);
      assert.equal(activeTab("pull", "find"), false);
      assert.equal(resumeCleanupTab("find", { status: "running" }), "cleanup");
      assert.equal(resumeCleanupTab("find", { status: "completed" }), "find");
      const cleanup = {
        status: "completed",
        cleanup_token: "cleanup-token",
      };
      assert.equal(cleanupTags({ items: [{ name: "Unused" }] })[0].name, "Unused");
      assert.equal(cleanupDuplicates({ items: [{ score: 0.9 }] })[0].score, 0.9);
      assert.equal(cleanupSplits({ items: [{ candidate_count: 2 }] })[0].candidate_count, 2);
      assert.deepEqual(
        cleanupReviewArgs("cleanup-token", "tags", { page: 2, query: "old", filter: "selected", sort: "name_asc" }, ["1"]),
        { mode: "cleanup_review", cleanup_token: "cleanup-token", section: "tags", page: 2, per_page: 50, query: "old", filter: "selected", sort: "name_asc", selected_ids: ["1"] }
      );
      assert.deepEqual(cleanupCandidateArgs("cleanup-token", "3", 2), {
        mode: "cleanup_candidates", cleanup_token: "cleanup-token", parent_tag_id: "3", page: 2, per_page: 100,
      });
      const grouped = splitCandidateGroups([
        { id: "exact", alias: "Alias", exact: true },
        { id: "fuzzy", alias: "Alias", remote_name: "Close" },
        { id: "manual", alias: "Alias", scene_count: 0 },
      ]);
      assert.deepEqual(grouped[0].exact.map(function (item) { return item.id; }), ["exact"]);
      assert.deepEqual(grouped[0].fuzzy.map(function (item) { return item.id; }), ["fuzzy"]);
      assert.deepEqual(grouped[0].manual.map(function (item) { return item.id; }), ["manual"]);
      let retained = updateSplitChoices({}, "parent-1", grouped[0].exact[0], { action: "parent-only" });
      retained = updateSplitChoices(retained, "parent-1", { id: "page-2", child_name: "Other" }, {});
      retained = updateSplitChoices(retained, "parent-2", { id: "page-2", child_name: "Other" }, {});
      assert.equal(retained["parent-1"][0].action, "parent-only");
      assert.equal(retained["parent-1"][1].candidate_id, "page-2");
      assert.equal(retained["parent-2"][0].candidate_id, "page-2");
      assert.equal(duplicateReviewed({ conflicts: [] }, { survivor_id: "1", source_ids: ["2"] }), true);
      assert.equal(duplicateReviewed({ conflicts: [{}] }, { survivor_id: "1", source_ids: ["2"] }), false);
      const cleanupArgs = cleanupApplyArgs(
        "cleanup-token",
        true,
        new Set(["1"]),
        { "duplicate-1-2": { survivor_id: "1", source_ids: ["2"] } },
        { "3": [{ candidate_id: "candidate", action: "child-only" }] }
      );
      assert.deepEqual(cleanupArgs.duplicates[0].source_ids, ["2"]);
      assert.equal(cleanupArgs.splits[0].candidates[0].action, "child-only");
      assert.equal(cleanupCanApply(cleanup, "cleanup-token", true, true), true);
      assert.equal(cleanupCanApply(cleanup, "cleanup-token", false, true), false);
      assert.deepEqual(cleanupPageInfo({ page: 1, per_page: 1, total: 72 }), {
        start: 1, end: 1, hasPrevious: false, hasNext: true,
      });
      console.log("self-check passed");
    }
    return;
  }

  const { React, patch, register, libraries, utils } = api;
  const { Alert, Badge, Button, Form, Nav, ProgressBar, Spinner, Table } = libraries.Bootstrap;
  const { NavLink } = libraries.ReactRouterDOM;
  const { gql } = libraries.Apollo;
  const { faTags } = libraries.FontAwesomeSolid;
  const { getClient } = utils.StashService;
  const route = "/plugins/tag-organizer";

  const operationMutation = gql`
    mutation TagOrganizerOperation($args: Map) {
      runPluginOperation(plugin_id: "tag-organizer", args: $args)
    }
  `;

  const backupMutation = gql`
    mutation TagOrganizerBackup {
      backupDatabase(input: { download: true, includeBlobs: false })
    }
  `;

  const scanTaskMutation = gql`
    mutation TagOrganizerScan($args: Map) {
      runPluginTask(plugin_id: "tag-organizer", description: "Tag Organizer scan", args_map: $args)
    }
  `;

  const jobQuery = gql`
    query TagOrganizerJob($input: FindJobInput!) {
      findJob(input: $input) {
        id
        status
        progress
        error
      }
    }
  `;

  const stopJobMutation = gql`
    mutation StopTagOrganizerJob($id: ID!) {
      stopJob(job_id: $id)
    }
  `;

  async function runOperation(args) {
    const result = await getClient().mutate({
      mutation: operationMutation,
      variables: { args },
    });
    const value = result.data && result.data.runPluginOperation;
    return value && value.output ? value.output : value;
  }

  async function backupDatabase() {
    const result = await getClient().mutate({ mutation: backupMutation });
    return result.data && result.data.backupDatabase;
  }

  async function runScanTask(args) {
    const result = await getClient().mutate({
      mutation: scanTaskMutation,
      variables: { args },
    });
    return result.data && result.data.runPluginTask;
  }

  async function getJob(id) {
    const result = await getClient().query({
      query: jobQuery,
      variables: { input: { id: id } },
      fetchPolicy: "no-cache",
    });
    return result.data && result.data.findJob;
  }

  async function stopJob(id) {
    const result = await getClient().mutate({
      mutation: stopJobMutation,
      variables: { id: id },
    });
    return result.data && result.data.stopJob;
  }

  function errorMessage(error) {
    return error && error.message ? error.message : String(error);
  }

  function TagOrganizerPage() {
    const [active, setActive] = React.useState("find");
    const [providers, setProviders] = React.useState([]);
    const [provider, setProvider] = React.useState("");
    const [rows, setRows] = React.useState([]);
    const [busy, setBusy] = React.useState("");
    const [status, setStatus] = React.useState("");
    const [warning, setWarning] = React.useState("");
    const [error, setError] = React.useState("");
    const [progress, setProgress] = React.useState(null);
    const [search, setSearch] = React.useState("");
    const [showLocal, setShowLocal] = React.useState(false);
    const [selectedNames, setSelectedNames] = React.useState(new Set());
    const [job, setJob] = React.useState(null);
    const [pullState, setPullState] = React.useState(null);
    const [pullBusy, setPullBusy] = React.useState(false);
    const [pullJob, setPullJob] = React.useState(null);
    const [pullError, setPullError] = React.useState("");
    const [cleanupState, setCleanupState] = React.useState({ status: "loading", tag_count: 0, duplicate_count: 0, split_count: 0, failure_count: 0 });
    const [cleanupToken, setCleanupToken] = React.useState("");
    const [cleanupJob, setCleanupJob] = React.useState(null);
    const [cleanupBusy, setCleanupBusy] = React.useState(false);
    const [cleanupError, setCleanupError] = React.useState("");
    const [cleanupMessage, setCleanupMessage] = React.useState("");
    const [cleanupWarning, setCleanupWarning] = React.useState("");
    const [cleanupBackup, setCleanupBackup] = React.useState(false);
    const [cleanupJunk, setCleanupJunk] = React.useState(new Set());
    const [cleanupDuplicatesChoice, setCleanupDuplicatesChoice] = React.useState({});
    const [cleanupSplitsChoice, setCleanupSplitsChoice] = React.useState({});
    const [cleanupSection, setCleanupSection] = React.useState("tags");
    const [cleanupViews, setCleanupViews] = React.useState({
      tags: { page: 1, query: "", filter: "unused", sort: "usage_desc" },
      duplicates: { page: 1, per_page: 1, query: "", filter: "all", sort: "score_desc" },
      splits: { page: 1, query: "", filter: "all", sort: "name_asc" },
    });
    const [cleanupReviews, setCleanupReviews] = React.useState({});
    const [cleanupLoading, setCleanupLoading] = React.useState(false);
    const [cleanupSplitParentId, setCleanupSplitParentId] = React.useState("");
    const [cleanupCandidateReview, setCleanupCandidateReview] = React.useState(null);
    const [cleanupCandidateLoading, setCleanupCandidateLoading] = React.useState(false);
    const localTags = React.useRef(new Set());
    const filteredRows = visibleRows(rows, search, showLocal, localTags.current);
    const selectedRows = rows.filter(function (row) {
      return selectedNames.has(row.name.toLowerCase());
    });
    const scanActive = Boolean(job && !terminalJobStatus(job.status));
    const pullStateRows = pullRows(pullState);
    const pullStateSummary = pullSummary(pullState);
    const pullActive = Boolean(pullState && pullState.status === "running");
    const cleanupReview = cleanupReviews[cleanupSection] || { items: [], page: 1, per_page: 50, total: 0 };
    const cleanupTagRows = cleanupSection === "tags" ? cleanupTags(cleanupReview) : [];
    const cleanupDuplicateRows = cleanupSection === "duplicates" ? cleanupDuplicates(cleanupReview) : [];
    const cleanupSplitRows = cleanupSection === "splits" ? cleanupSplits(cleanupReview) : [];
    const cleanupCandidateGroups = splitCandidateGroups(cleanupCandidateReview && cleanupCandidateReview.items);
    const cleanupSplitParentIndex = cleanupSplitRows.findIndex(function (split) { return String(split.tag_id) === String(cleanupSplitParentId); });
    const cleanupView = cleanupViews[cleanupSection];
    const cleanupPage = cleanupPageInfo(cleanupReview);
    const cleanupApplyResult = cleanupState && cleanupState.apply_result;
    const cleanupHasSelections = cleanupJunk.size > 0 ||
      Object.keys(cleanupDuplicatesChoice).length > 0 ||
      Object.keys(cleanupSplitsChoice).length > 0;
    const cleanupReady = cleanupCanApply(
      cleanupState,
      cleanupToken,
      cleanupBackup,
      cleanupHasSelections
    );

    React.useEffect(function () {
      Promise.all([runOperation({ mode: "providers" }), runOperation({ mode: "pull_status" })])
        .then(function (results) {
          const result = results[0];
          setPullState(results[1]);
          const available = result.providers || [];
          (result.local_tag_names || []).forEach(function (name) {
            localTags.current.add(name);
          });
          setProviders(available);
          const saved = storedScan();
          const savedProvider = saved && available.some(function (item) {
            return item.endpoint === saved.provider;
          }) ? saved.provider : "";
          const stashdb = available.find(function (item) {
            return item.endpoint.includes("stashdb.org");
          });
          setProvider(savedProvider || (stashdb || available[0] || {}).endpoint || "");
        })
        .catch(function (loadError) {
          setError(errorMessage(loadError));
        });
    }, []);

    React.useEffect(function () {
      runOperation({ mode: "cleanup_status" })
        .then(function (savedCleanup) {
          setCleanupState(savedCleanup);
          if (savedCleanup && savedCleanup.cleanup_token) setCleanupToken(savedCleanup.cleanup_token);
          setActive(function (current) { return resumeCleanupTab(current, savedCleanup); });
          setCleanupError("");
        })
        .catch(function (loadError) {
          const message = errorMessage(loadError);
          setCleanupError(message);
          setCleanupState({ status: "failed", tag_count: 0, duplicate_count: 0, split_count: 0, failure_count: 0, error: message });
        });
    }, []);

    React.useEffect(function () {
      const saved = storedScan();
      if (saved && saved.token) {
        setJob(saved);
      }
    }, []);

    React.useEffect(function () {
      if (!job || !job.token) return undefined;
      let stopped = false;
      let timer = null;
      let polls = 0;

      async function poll() {
        try {
          const nativeJob = job.id ? await getJob(job.id) : null;
          const includeRows = polls === 0 || polls % 4 === 0;
          let scanState = await runOperation({
            mode: "scan_status",
            scan_token: job.token,
            include_rows: includeRows,
          });
          polls += 1;
          const nativeStatus = nativeJob && nativeJob.status;
          const terminal = terminalJobStatus(nativeStatus) ||
            scanState.status === "completed" || scanState.status === "failed";
          if (terminal && !Object.prototype.hasOwnProperty.call(scanState, "rows")) {
            scanState = await runOperation({
              mode: "scan_status",
              scan_token: job.token,
              include_rows: true,
            });
          }
          if (stopped) return;
          if (Object.prototype.hasOwnProperty.call(scanState, "rows")) {
            setRows(scanState.rows || []);
          }
          setError("");
          setProgress({ current: scanState.scanned || 0, total: scanState.total || 0 });
          const next = Object.assign({}, job, {
            status: nativeStatus || scanState.status,
            current: scanState.scanned || 0,
            total: scanState.total || 0,
            failure_count: scanState.failure_count || 0,
          });
          setJob(function (current) {
            if (!current || current.token !== job.token) return current;
            const merged = Object.assign({}, current, next);
            if (current.id && !next.id) merged.id = current.id;
            saveScan(merged);
            return merged;
          });
          if (!terminal) {
            setStatus("Scanning " + (scanState.scanned || 0) + " of " + (scanState.total || "?") + " linked scene(s)…");
            timer = setTimeout(poll, 5000);
            return;
          }
          setBusy("");
          if (nativeStatus === "FAILED" || nativeStatus === "CANCELLED" || scanState.status === "failed") {
            setError(scanState.error || (nativeJob && nativeJob.error) || "The scan did not complete.");
            setStatus("");
          } else {
            setStatus(
              "Scanned " + (scanState.scanned || 0) + " linked scene(s); found " +
                (scanState.row_count || (scanState.rows || []).length) + " tag(s)."
            );
            if (scanState.failure_count) {
              setWarning(scanState.failure_count + " remote lookup(s) failed, so these results may be incomplete.");
            }
          }
        } catch (pollError) {
          if (stopped) return;
          setError(errorMessage(pollError));
          timer = setTimeout(poll, 5000);
        }
      }

      poll();
      return function () {
        stopped = true;
        if (timer) clearTimeout(timer);
      };
    }, [job && job.id, job && job.token]);

    React.useEffect(function () {
      if (!pullState || pullState.status !== "running" || pullBusy) return undefined;
      let stopped = false;
      let timer = null;

      async function pollPull() {
        try {
          const next = await runOperation({ mode: "pull_status" });
          if (stopped) return;
          setPullState(next);
          setPullError("");
          if (next.status === "running") timer = setTimeout(pollPull, 3000);
        } catch (pollError) {
          if (stopped) return;
          setPullError(errorMessage(pollError));
          timer = setTimeout(pollPull, 5000);
        }
      }

      pollPull();
      return function () {
        stopped = true;
        if (timer) clearTimeout(timer);
      };
    }, [pullState && pullState.status, pullJob && pullJob.id, pullBusy]);

    React.useEffect(function () {
      if (!cleanupToken || !cleanupState || cleanupState.status !== "running" || cleanupBusy) return undefined;
      let stopped = false;
      let timer = null;

      async function pollCleanup() {
        try {
          const nativeJob = cleanupJob && cleanupJob.id ? await getJob(cleanupJob.id) : null;
          const next = await runOperation({ mode: "cleanup_status", cleanup_token: cleanupToken });
          if (stopped) return;
          setCleanupState(next);
          setCleanupError("");
          const nativeTerminal = nativeJob && terminalJobStatus(nativeJob.status);
          if (next.status === "running" && !nativeTerminal) {
            timer = setTimeout(pollCleanup, 3000);
          } else if (next.status === "running" && nativeTerminal) {
            setCleanupState(Object.assign({}, next, { status: "failed", error: nativeJob.error || "The cleanup scan did not complete." }));
          }
        } catch (pollError) {
          if (stopped) return;
          setCleanupError(errorMessage(pollError));
          timer = setTimeout(pollCleanup, 5000);
        }
      }

      pollCleanup();
      return function () {
        stopped = true;
        if (timer) clearTimeout(timer);
      };
    }, [cleanupToken, cleanupState && cleanupState.status, cleanupJob && cleanupJob.id, cleanupBusy]);

    React.useEffect(function () {
      if (!cleanupToken || !cleanupState || !["completed", "applied"].includes(cleanupState.status)) return undefined;
      let stopped = false;
      const selectedIds = cleanupSection === "tags"
        ? Array.from(cleanupJunk)
        : cleanupSection === "duplicates"
          ? Object.keys(cleanupDuplicatesChoice)
          : Object.keys(cleanupSplitsChoice);
      setCleanupLoading(true);
      runOperation(cleanupReviewArgs(cleanupToken, cleanupSection, cleanupView, selectedIds))
        .then(function (review) {
          if (stopped) return;
          setCleanupReviews(function (current) { return Object.assign({}, current, { [cleanupSection]: review }); });
          setCleanupError("");
          setCleanupLoading(false);
        })
        .catch(function (loadError) {
          if (stopped) return;
          setCleanupError(errorMessage(loadError));
          setCleanupLoading(false);
        });
      return function () { stopped = true; };
    }, [cleanupToken, cleanupState && cleanupState.status, cleanupSection, cleanupView,
      cleanupView && cleanupView.filter === "selected" && cleanupSection === "tags" ? Array.from(cleanupJunk).join("\0") : "",
      cleanupView && cleanupView.filter === "selected" && cleanupSection === "duplicates" ? Object.keys(cleanupDuplicatesChoice).join("\0") : "",
      cleanupView && cleanupView.filter === "selected" && cleanupSection === "splits" ? Object.keys(cleanupSplitsChoice).join("\0") : ""]);

    React.useEffect(function () {
      if (cleanupSection !== "splits" || !cleanupSplitParentId || !cleanupToken) return undefined;
      let stopped = false;
      setCleanupCandidateLoading(true);
      runOperation(cleanupCandidateArgs(cleanupToken, cleanupSplitParentId, 1))
        .then(function (review) {
          if (stopped) return;
          setCleanupCandidateReview(review);
          setCleanupError("");
          setCleanupCandidateLoading(false);
        })
        .catch(function (loadError) {
          if (stopped) return;
          setCleanupError(errorMessage(loadError));
          setCleanupCandidateLoading(false);
        });
      return function () { stopped = true; };
    }, [cleanupToken, cleanupSection, cleanupSplitParentId]);

    async function scan() {
      setBusy("scan");
      setRows([]);
      setSelectedNames(new Set());
      setStatus("");
      setWarning("");
      setError("");
      setProgress({ current: 0, total: 0 });
      try {
        const token = newScanToken();
        const pending = { token: token, provider: provider, status: "STARTING" };
        saveScan(pending);
        setJob(pending);
        const id = await runScanTask({ mode: "scan", provider: provider, scan_token: token });
        if (!id) throw new Error("Stash did not return a scan job ID");
        const next = { id: String(id), token: token, provider: provider, status: "READY" };
        saveScan(next);
        setJob(next);
      } catch (scanError) {
        setError(errorMessage(scanError));
      } finally {
        setBusy("");
      }
    }

    async function cancelScan() {
      if (!job || !job.id) return;
      setBusy("cancel");
      try {
        await stopJob(job.id);
        setStatus("Stopping scan…");
      } catch (cancelError) {
        setError(errorMessage(cancelError));
      } finally {
        setBusy("");
      }
    }

    async function scanCleanup() {
      setActive("cleanup");
      setCleanupBusy(true);
      setCleanupError("");
      setCleanupMessage("");
      setCleanupWarning("");
      setCleanupBackup(false);
      setCleanupJob(null);
      setCleanupJunk(new Set());
      setCleanupDuplicatesChoice({});
      setCleanupSplitsChoice({});
      setCleanupReviews({});
      setCleanupSplitParentId("");
      setCleanupCandidateReview(null);
      const token = newScanToken();
      setCleanupToken(token);
      setCleanupState({ cleanup_token: token, status: "running", scanned: 0, total: 0, progress_phase: "tags", progress_detail: "", tag_count: 0, duplicate_count: 0, split_count: 0, failure_count: 0 });
      try {
        const id = await runScanTask({ mode: "cleanup_scan", cleanup_token: token });
        if (!id) throw new Error("Stash did not return a cleanup scan job ID");
        setCleanupJob({ id: String(id) });
      } catch (scanError) {
        const message = errorMessage(scanError);
        setCleanupError(message);
        setCleanupState(function (current) { return Object.assign({}, current, { status: "failed", error: message }); });
      } finally {
        setCleanupBusy(false);
      }
    }

    async function backupCleanup() {
      setCleanupBusy(true);
      setCleanupError("");
      setCleanupWarning("");
      try {
        const link = await backupDatabase();
        if (!link) throw new Error("Stash did not return a database backup");
        setCleanupBackup(true);
        setCleanupMessage("Database backup created. Reviewed cleanup changes are ready to apply.");
      } catch (backupError) {
        setCleanupBackup(false);
        setCleanupError(errorMessage(backupError));
      } finally {
        setCleanupBusy(false);
      }
    }

    async function applyCleanup() {
      if (!cleanupReady) return;
      setCleanupBusy(true);
      setCleanupError("");
      setCleanupMessage("");
      setCleanupWarning("");
      try {
        const result = await runOperation(
          cleanupApplyArgs(
            cleanupToken,
            cleanupBackup,
            cleanupJunk,
            cleanupDuplicatesChoice,
            cleanupSplitsChoice
          )
        );
        setCleanupState(function (current) { return Object.assign({}, current, { status: "applied", apply_result: result }); });
        setCleanupBackup(false);
        setCleanupMessage(
          "Cleanup applied: " + (result.deleted || []).length + " deleted, " +
            (result.merged || []).length + " merged, " +
            (result.scene_updates || []).length + " scene(s) updated."
        );
        if ((result.failures || []).length || (result.warnings || []).length) {
          setCleanupWarning(
            (result.failures || []).length + " failure(s), " +
              (result.warnings || []).length + " warning(s). Review the apply result before rescanning."
          );
        }
      } catch (applyError) {
        setCleanupError(errorMessage(applyError));
      } finally {
        setCleanupBusy(false);
      }
    }

    async function pullNow() {
      setActive("pull");
      setPullBusy(true);
      setPullError("");
      setPullJob(null);
      setPullState({ status: "running", scanned: 0, total: 0, changed: 0, tags_added: 0, failure_count: 0, rows: [] });
      try {
        const id = await runScanTask({ mode: "pull" });
        if (!id) throw new Error("Stash did not return a pull job ID");
        setPullJob({ id: String(id) });
      } catch (pullStartError) {
        const message = errorMessage(pullStartError);
        setPullError(message);
        setPullState(function (current) {
          return Object.assign({}, current, { status: "failed", error: message });
        });
      } finally {
        setPullBusy(false);
      }
    }

    async function add(row) {
      setBusy(row.name);
      setWarning("");
      setError("");
      try {
        const result = await runOperation({
          mode: "add",
          provider: provider,
          name: row.name,
          scene_ids: row.scene_ids,
        });
        setRows(function (current) {
          return markLocal(current, row.name);
        });
        localTags.current.add(row.name.toLowerCase());
        setSelectedNames(function (current) {
          const next = new Set(current);
          next.delete(row.name.toLowerCase());
          return next;
        });
        setStatus(
          (result.created ? "Created " + row.name + " and applied it" : "Applied " + row.name) +
            " to " + result.applied + " scene(s)."
        );
        if (result.failed || result.error) {
          setWarning(
            result.failed + " scene(s) were not updated." +
              (result.error ? " " + result.error : " Run Sync remote tags to retry.")
          );
        }
      } catch (addError) {
        setError(errorMessage(addError));
      } finally {
        setBusy("");
      }
    }

    async function addSelected() {
      const selected = selectedRows.slice();
      if (!selected.length) return;
      setBusy("add-many");
      setWarning("");
      setError("");
      try {
        const result = await runOperation({
          mode: "add_many",
          provider: provider,
          items: selected.map(function (row) {
            return { name: row.name, scene_ids: row.scene_ids };
          }),
        });
        const results = result.results || [];
        const resolvedNames = results.filter(function (item) {
          return item.resolved;
        }).map(function (item) {
          return item.name;
        });
        const completedNames = results.filter(function (item) {
          return item.resolved && !item.error && !item.failed;
        }).map(function (item) {
          return item.name;
        });
        setRows(function (current) {
          return markLocalNames(current, resolvedNames);
        });
        resolvedNames.forEach(function (name) {
          localTags.current.add(name.toLowerCase());
        });
        setSelectedNames(function (current) {
          const next = new Set(current);
          completedNames.forEach(function (name) {
            next.delete(name.toLowerCase());
          });
          return next;
        });
        setStatus(
          "Processed " + (result.processed || results.length) + " tag(s); applied " +
            (result.applied || 0) + " scene tag assignment(s)."
        );
        const failed = results.filter(function (item) {
          return item.error || item.failed;
        });
        if (failed.length) {
          setWarning(
            failed.length + " tag(s) had update failures: " +
              failed.map(function (item) {
                return item.name + (item.error ? " (" + item.error + ")" : "");
              }).join("; ")
          );
        }
      } catch (addError) {
        setError(errorMessage(addError));
      } finally {
        setBusy("");
      }
    }

    function changeDuplicateChoice(group, field, value) {
      setCleanupDuplicatesChoice(function (current) {
        const next = Object.assign({}, current);
        const choice = Object.assign({ survivor_id: "", source_ids: [], override_remote_ids: false }, next[group.id] || {});
        if (field === "survivor_id") {
          choice.survivor_id = value;
          choice.source_ids = choice.source_ids.filter(function (id) { return id !== value; });
        } else if (field === "source_ids") {
          choice.source_ids = value;
        } else {
          choice[field] = value;
        }
        next[group.id] = choice;
        return next;
      });
    }

    function changeSplitChoice(splitId, candidate, changes, remove) {
      setCleanupSplitsChoice(function (current) {
        return updateSplitChoices(current, splitId, candidate, changes, remove);
      });
    }

    function loadCandidatePage(page) {
      if (!cleanupSplitParentId || cleanupCandidateLoading) return;
      setCleanupCandidateLoading(true);
      runOperation(cleanupCandidateArgs(cleanupToken, cleanupSplitParentId, page))
        .then(function (review) {
          setCleanupCandidateReview(review);
          setCleanupError("");
          setCleanupCandidateLoading(false);
        })
        .catch(function (loadError) {
          setCleanupError(errorMessage(loadError));
          setCleanupCandidateLoading(false);
        });
    }

    function bulkAliasCandidates(candidates, operation, action) {
      const splitId = String(cleanupSplitParentId);
      setCleanupSplitsChoice(function (current) {
        let next = current;
        candidates.forEach(function (candidate) {
          const selected = (next[splitId] || []).some(function (item) { return item.candidate_id === candidate.id; });
          if (operation === "select" || (operation === "action" && selected)) {
            next = updateSplitChoices(next, splitId, candidate, action ? { action: action } : {});
          } else if (operation === "clear") {
            next = updateSplitChoices(next, splitId, candidate, {}, true);
          }
        });
        return next;
      });
    }

    function changeCleanupView(changes) {
      setCleanupViews(function (current) {
        return Object.assign({}, current, {
          [cleanupSection]: Object.assign({}, current[cleanupSection], changes, changes.page ? {} : { page: 1 }),
        });
      });
    }

    function renderCandidateTable(label, candidates, collapsed) {
      if (!candidates.length) return null;
      const splitId = String(cleanupSplitParentId);
      const content = React.createElement(
        Table,
        { bordered: true, responsive: true, size: "sm", className: "mb-2" },
        React.createElement("thead", null, React.createElement("tr", null,
          React.createElement("th", null, "Use"),
          React.createElement("th", null, "Child name"),
          React.createElement("th", null, "Evidence"),
          React.createElement("th", null, "Score"),
          React.createElement("th", null, "Scenes"),
          React.createElement("th", null, "Action"),
          React.createElement("th", null, "Remove alias")
        )),
        React.createElement("tbody", null, candidates.map(function (candidate) {
          const choice = (cleanupSplitsChoice[splitId] || []).find(function (item) { return item.candidate_id === candidate.id; });
          const selected = Boolean(choice);
          return React.createElement("tr", { key: candidate.id },
            React.createElement("td", null, React.createElement("input", {
              type: "checkbox", checked: selected, disabled: cleanupBusy || cleanupState.status === "applied",
              onChange: function (event) { changeSplitChoice(splitId, candidate, {}, !event.target.checked); },
              "aria-label": "Select " + (candidate.remote_name || candidate.child_name),
            })),
            React.createElement("td", null, React.createElement(Form.Control, {
              size: "sm", value: choice ? choice.child_name : candidate.child_name || "",
              disabled: cleanupBusy || cleanupState.status === "applied",
              onChange: function (event) { changeSplitChoice(splitId, candidate, { child_name: event.target.value }); },
            })),
            React.createElement("td", { className: "small" },
              candidate.remote_name || "Manual",
              (candidate.evidence_scene_ids || []).length ? React.createElement("div", null,
                candidate.evidence_scene_ids.map(function (sceneId, index) {
                  return React.createElement(React.Fragment, { key: sceneId },
                    index ? ", " : "",
                    React.createElement("a", { href: "/scenes/" + sceneId, target: "_blank", rel: "noopener noreferrer" }, sceneId)
                  );
                })
              ) : null
            ),
            React.createElement("td", null, candidate.score || 0),
            React.createElement("td", null, candidate.scene_count || 0),
            React.createElement("td", null, React.createElement(Form.Control, {
              as: "select", size: "sm", value: choice ? choice.action : candidate.action || "child-only",
              disabled: cleanupBusy || cleanupState.status === "applied",
              onChange: function (event) { changeSplitChoice(splitId, candidate, { action: event.target.value }); },
            },
              React.createElement("option", { value: "child-only" }, "Child only"),
              React.createElement("option", { value: "parent-only" }, "Parent only"),
              React.createElement("option", { value: "parent-plus-child" }, "Parent + child"),
              React.createElement("option", { value: "skip" }, "Skip")
            )),
            React.createElement("td", null, React.createElement("input", {
              type: "checkbox", checked: choice ? choice.remove_alias !== false : true,
              disabled: cleanupBusy || cleanupState.status === "applied",
              onChange: function (event) { changeSplitChoice(splitId, candidate, { remove_alias: event.target.checked }); },
              "aria-label": "Remove alias " + candidate.alias,
            }))
          );
        }))
      );
      return collapsed
        ? React.createElement("details", { className: "mb-2" }, React.createElement("summary", null, label + " (" + candidates.length + ")"), content)
        : React.createElement("div", { className: "mb-2" }, React.createElement("strong", null, label + " (" + candidates.length + ")"), content);
    }

    return React.createElement(
      "div",
      { className: "container-fluid mt-3" },
      React.createElement("h2", null, "Tag Organizer"),
      React.createElement(
        "p",
        { className: "text-muted" },
        "Scan linked scenes for tags from the selected metadata provider. Create missing local tags or apply existing matches to those scenes; existing scene tags are never removed."
      ),
      React.createElement(
        Nav,
        { variant: "tabs", role: "tablist", className: "mb-3" },
        React.createElement(
          Nav.Item,
          null,
          React.createElement(
            Nav.Link,
            {
              as: "button",
              type: "button",
              id: "tag-organizer-find-tab",
              active: activeTab(active, "find"),
              role: "tab",
              "aria-controls": "tag-organizer-find-panel",
              "aria-selected": activeTab(active, "find"),
              onClick: function () { setActive("find"); },
            },
            "Find Missing Tags"
          )
        ),
        React.createElement(
          Nav.Item,
          null,
          React.createElement(
            Nav.Link,
            {
              as: "button",
              type: "button",
              id: "tag-organizer-pull-tab",
              active: activeTab(active, "pull"),
              role: "tab",
              "aria-controls": "tag-organizer-pull-panel",
              "aria-selected": activeTab(active, "pull"),
              onClick: function () { setActive("pull"); },
            },
            "Pull Remote Tags"
          )
        ),
        React.createElement(
          Nav.Item,
          null,
          React.createElement(
            Nav.Link,
            {
              as: "button",
              type: "button",
              id: "tag-organizer-cleanup-tab",
              active: activeTab(active, "cleanup"),
              role: "tab",
              "aria-controls": "tag-organizer-cleanup-panel",
              "aria-selected": activeTab(active, "cleanup"),
              onClick: function () { setActive("cleanup"); },
            },
            "Clean Up Tags"
          )
        )
      ),
      React.createElement(
        "div",
        {
          id: "tag-organizer-find-panel",
          role: "tabpanel",
          "aria-labelledby": "tag-organizer-find-tab",
          hidden: !activeTab(active, "find"),
        },
      React.createElement(
        "div",
        { className: "d-flex align-items-end flex-nowrap mb-3" },
        React.createElement(
          Form.Group,
          { className: "flex-grow-1 mb-0", style: { minWidth: 0 } },
          React.createElement(Form.Label, { htmlFor: "tag-organizer-provider" }, "Metadata provider"),
          React.createElement(
            Form.Control,
            {
              as: "select",
              id: "tag-organizer-provider",
              value: provider,
              disabled: Boolean(busy) || scanActive,
              onChange: function (event) {
                setProvider(event.target.value);
                setRows([]);
                setSelectedNames(new Set());
                setJob(null);
                window.localStorage.removeItem("tag-organizer.scan");
                setStatus("");
                setWarning("");
              },
            },
            providers.map(function (item) {
              return React.createElement("option", { key: item.endpoint, value: item.endpoint }, item.name);
            })
          )
        ),
        React.createElement(
          Button,
          { onClick: scan, disabled: !provider || Boolean(busy) || scanActive, className: "ml-2 text-nowrap" },
          busy === "scan"
            ? React.createElement(
                React.Fragment,
                null,
                React.createElement(Spinner, { animation: "border", size: "sm", className: "mr-2" }),
                "Scanning…"
              )
            : scanActive
              ? "Scanning…"
            : "Scan"
        ),
        scanActive
          ? React.createElement(
              Button,
              {
                variant: "outline-danger",
                className: "ml-2 text-nowrap",
                disabled: Boolean(busy),
                onClick: cancelScan,
              },
              busy === "cancel" ? "Stopping…" : "Cancel scan"
            )
          : null
      ),
      React.createElement(
        "div",
        { className: "d-flex align-items-end flex-nowrap mb-3" },
        React.createElement(
          Form.Group,
          { className: "flex-grow-1 mb-0", style: { minWidth: 0 } },
          React.createElement(Form.Label, { htmlFor: "tag-organizer-search" }, "Search tags"),
          React.createElement(
            "div",
            { className: "d-flex", style: { minWidth: 0 } },
            React.createElement(Form.Control, {
              id: "tag-organizer-search",
              type: "search",
              value: search,
              placeholder: "Search by tag name",
              onChange: function (event) {
                setSearch(event.target.value);
              },
              style: { minWidth: 0 },
            }),
            React.createElement(
              Button,
              {
                variant: "outline-secondary",
                className: "ml-2 text-nowrap",
                disabled: !search,
                onClick: function () {
                  setSearch("");
                },
                "aria-label": "Clear tag search",
              },
              "Clear"
            )
          )
        ),
        React.createElement(
          Button,
          {
            variant: showLocal ? "secondary" : "outline-secondary",
            className: "ml-2 text-nowrap",
            "aria-pressed": showLocal,
            onClick: function () {
              setShowLocal(!showLocal);
            },
          },
          "Show local tags"
        ),
        selectedRows.length
          ? React.createElement(
              Button,
              {
                variant: "primary",
                className: "ml-2 text-nowrap",
                disabled: Boolean(busy) || scanActive,
                onClick: addSelected,
              },
              busy === "add-many"
                ? React.createElement(
                    React.Fragment,
                    null,
                    React.createElement(Spinner, { animation: "border", size: "sm", className: "mr-2" }),
                    "Adding " + selectedRows.length + "…"
                  )
                : "Add selected (" + selectedRows.length + ")"
            )
          : null
        ),
      progress
        ? React.createElement(ProgressBar, {
            className: "mb-3",
            now: progress.total ? (progress.current / progress.total) * 100 : 0,
            label: progress.current + " / " + progress.total,
          })
        : null,
      !providers.length && !error
        ? React.createElement(Alert, { variant: "info" }, "Configure a Stash-box metadata provider and API key before scanning.")
        : null,
      status ? React.createElement(Alert, { variant: "success" }, status) : null,
      warning ? React.createElement(Alert, { variant: "warning" }, warning) : null,
      error ? React.createElement(Alert, { variant: "danger" }, error) : null,
      filteredRows.length < rows.length
        ? React.createElement(
            "p",
            { className: "text-muted", "aria-live": "polite" },
            "Showing " + filteredRows.length + " of " + rows.length + " tags"
          )
        : null,
      rows.length && !filteredRows.length
        ? React.createElement("p", { className: "text-muted" }, "No tags match the current filters")
        : null,
      filteredRows.length
        ? React.createElement(
            Table,
            { striped: true, responsive: true },
            React.createElement(
              "thead",
              null,
              React.createElement(
                "tr",
                null,
                React.createElement("th", null, "Select"),
                React.createElement("th", null, "Tag"),
                React.createElement("th", null, "Scenes"),
                React.createElement("th", null)
              )
            ),
            React.createElement(
              "tbody",
              null,
              filteredRows.map(function (row) {
                return React.createElement(
                  "tr",
                  { key: row.name },
                  React.createElement(
                    "td",
                    null,
                    React.createElement("input", {
                      type: "checkbox",
                      className: "mr-2",
                      checked: selectedNames.has(row.name.toLowerCase()),
                      disabled: Boolean(busy) || scanActive,
                      onChange: function (event) {
                        setSelectedNames(function (current) {
                          const next = new Set(current);
                          if (event.target.checked) {
                            next.add(row.name.toLowerCase());
                          } else {
                            next.delete(row.name.toLowerCase());
                          }
                          return next;
                        });
                      },
                      "aria-label": "Select " + row.name,
                    }),
                    row.name,
                    row.is_local
                      ? React.createElement(Badge, { variant: "secondary", className: "ml-2" }, "Local")
                      : null
                  ),
                  React.createElement("td", null, row.scene_count),
                  React.createElement(
                    "td",
                    { className: "text-right" },
                    React.createElement(
                      Button,
                      {
                        size: "sm",
                        disabled: Boolean(busy) || scanActive,
                        onClick: function () {
                          add(row);
                        },
                        "aria-label": (row.is_local ? "Apply " : "Add ") + row.name,
                      },
                      busy === row.name ? (row.is_local ? "Applying…" : "Adding…") : row.is_local ? "Apply" : "Add"
                    )
                  )
                );
              })
            )
          )
        : null
      ),
      React.createElement(
        "div",
        {
          id: "tag-organizer-pull-panel",
          role: "tabpanel",
          "aria-labelledby": "tag-organizer-pull-tab",
          hidden: !activeTab(active, "pull"),
        },
        React.createElement(
          "div",
          { className: "d-flex justify-content-between align-items-center mb-3" },
          React.createElement(
            "div",
            { className: "mr-3" },
            React.createElement("h3", { className: "h5 mb-1" }, "Pull Remote Tags"),
            React.createElement(
              "p",
              { className: "text-muted mb-0" },
              "Process all configured metadata providers and add matching local tags to linked scenes."
            )
          ),
          React.createElement(
            Button,
            {
              onClick: pullNow,
              disabled: !providers.length || pullBusy || pullActive,
              className: "text-nowrap",
            },
            pullBusy
              ? React.createElement(
                  React.Fragment,
                  null,
                  React.createElement(Spinner, { animation: "border", size: "sm", className: "mr-2" }),
                  "Starting…"
                )
              : pullActive
                ? "Pulling…"
                : "Pull now"
          )
        ),
        !providers.length
          ? React.createElement(Alert, { variant: "info" }, "Configure a Stash-box metadata provider and API key before pulling.")
          : null,
        pullState && pullState.status !== "waiting"
          ? React.createElement(
              React.Fragment,
              null,
              React.createElement(ProgressBar, {
                className: "mb-2",
                now: pullStateSummary.total ? (pullStateSummary.current / pullStateSummary.total) * 100 : 0,
                label: pullStateSummary.current + " / " + (pullStateSummary.total || "?"),
              }),
              React.createElement(
                "p",
                { className: "text-muted", "aria-live": "polite" },
                (pullState.status === "running" ? "Pulling" : pullState.status === "completed" ? "Last pull completed" : "Pull failed") +
                  " · Processed " + pullStateSummary.current + (pullStateSummary.total ? " / " + pullStateSummary.total : "") +
                  " scene(s) · " + pullStateSummary.changed + " changed · " + pullStateSummary.tagsAdded + " tag(s) added" +
                  (pullStateSummary.failures ? " · " + pullStateSummary.failures + " failure(s)" : "")
              )
            )
          : null,
        pullError ? React.createElement(Alert, { variant: "danger" }, pullError) : null,
        pullState && pullState.error && pullState.error !== pullError
          ? React.createElement(Alert, { variant: "danger" }, pullState.error)
          : null,
        pullStateRows.length
          ? React.createElement(
              Table,
              { striped: true, bordered: true, responsive: true },
              React.createElement(
                "thead",
                null,
                React.createElement(
                  "tr",
                  null,
                  React.createElement("th", null, "Scene"),
                  React.createElement("th", null, "Added tags"),
                  React.createElement("th", null, "Result")
                )
              ),
              React.createElement(
                "tbody",
                null,
                pullStateRows.map(function (row, index) {
                  const title = row.title || "(untitled)";
                  const sceneURL = "/scenes/" + row.scene_id;
                  const tags = row.added_tags || [];
                  return React.createElement(
                    "tr",
                    { key: String(row.scene_id) + "-" + index, className: row.error ? "table-danger" : undefined },
                    React.createElement(
                      "td",
                      null,
                      React.createElement(
                        "div",
                        { className: "d-flex align-items-center" },
                        React.createElement(
                          "a",
                          { href: sceneURL, className: "mr-2", "aria-label": "Open " + title },
                          React.createElement("img", {
                            src: row.screenshot || PULL_PLACEHOLDER,
                            alt: title + " cover",
                            loading: "lazy",
                            style: { width: "96px", height: "54px", objectFit: "cover" },
                            onError: function (event) {
                              event.currentTarget.src = PULL_PLACEHOLDER;
                            },
                          })
                        ),
                        React.createElement("a", { href: sceneURL }, title)
                      )
                    ),
                    React.createElement(
                      "td",
                      null,
                      tags.length
                        ? tags.map(function (name) {
                            return React.createElement(Badge, { key: name, variant: "info", className: "mr-1" }, name);
                          })
                        : React.createElement("span", { className: "text-muted" }, "—")
                    ),
                    React.createElement(
                      "td",
                      null,
                      row.error
                        ? React.createElement(
                            "div",
                            null,
                            React.createElement(Badge, { variant: "danger" }, "Failed"),
                            React.createElement("div", { className: "small mt-1" }, row.error)
                          )
                        : React.createElement(Badge, { variant: "success" }, "Updated")
                    )
                  );
                })
              )
            )
          : React.createElement("p", { className: "text-muted" }, pullEmptyState(pullState))
      ),
      React.createElement(
        "div",
        {
          id: "tag-organizer-cleanup-panel",
          role: "tabpanel",
          "aria-labelledby": "tag-organizer-cleanup-tab",
          hidden: !activeTab(active, "cleanup"),
        },
        React.createElement(
          "div",
          { className: "d-flex justify-content-between align-items-center mb-3" },
          React.createElement(
            "div",
            { className: "mr-3" },
            React.createElement("h3", { className: "h5 mb-1" }, "Clean Up Tags"),
            React.createElement(
              "p",
              { className: "text-muted mb-0" },
              "Review unused tags, fuzzy duplicate suggestions, and alias splits before changing the library."
            )
          ),
          React.createElement(
            Button,
            {
              onClick: scanCleanup,
              disabled: cleanupBusy || Boolean(cleanupState && cleanupState.status === "running"),
              className: "text-nowrap",
            },
            cleanupBusy && (!cleanupState || cleanupState.status !== "completed")
              ? React.createElement(
                  React.Fragment,
                  null,
                  React.createElement(Spinner, { animation: "border", size: "sm", className: "mr-2" }),
                  "Scanning…"
                )
              : "Scan tags"
          )
        ),
        cleanupState && cleanupState.status === "running"
          ? React.createElement(
              React.Fragment,
              null,
              React.createElement(ProgressBar, {
                className: "mb-2",
                now: cleanupState.total ? (Number(cleanupState.scanned || 0) / cleanupState.total) * 100 : 0,
                label: cleanupState.progress_phase === "tags"
                  ? (cleanupState.scanned || 0) + " / " + (cleanupState.total || "?") + " tag(s)"
                  : cleanupState.progress_phase === "duplicates"
                    ? (cleanupState.scanned || 0) + " / " + (cleanupState.total || "?") + " tag(s) for duplicate review"
                    : (cleanupState.scanned || 0) + " / " + (cleanupState.total || "?") + " alias-bearing tag(s)",
              }),
              React.createElement(
                "p",
                { className: "text-muted" },
                cleanupState.progress_phase === "tags"
                  ? "Loading tag inventory…"
                  : cleanupState.progress_phase === "duplicates"
                    ? "Finding fuzzy duplicate suggestions…"
                    : cleanupState.progress_detail || "Building the review plan…"
              )
            )
          : null,
        cleanupState && cleanupState.status === "completed"
          ? React.createElement(
              Alert,
              { variant: "info" },
              "Review plan ready."
            )
          : null,
        cleanupState && cleanupState.status === "applied"
          ? React.createElement(Alert, { variant: "success" }, "This review plan has been applied. Scan again before any further changes.")
          : null,
        cleanupMessage ? React.createElement(Alert, { variant: "success" }, cleanupMessage) : null,
        cleanupWarning ? React.createElement(Alert, { variant: "warning" }, cleanupWarning) : null,
        cleanupApplyResult && (cleanupApplyResult.failures || []).length
          ? React.createElement(
              Alert,
              { variant: "warning" },
              React.createElement("strong", null, "Skipped changes: "),
              React.createElement(
                "ul",
                { className: "mb-0" },
                cleanupApplyResult.failures.map(function (failure, index) {
                  return React.createElement("li", { key: String(index) }, (failure.kind || "change") + (failure.tag_id ? " " + failure.tag_id : "") + ": " + failure.error);
                })
              )
            )
          : null,
        cleanupError ? React.createElement(Alert, { variant: "danger" }, cleanupError) : null,
        cleanupState && cleanupState.error
          ? React.createElement(Alert, { variant: "danger" }, cleanupState.error)
          : null,
        cleanupState && cleanupState.failure_count
          ? React.createElement(Alert, { variant: "warning" }, cleanupState.failure_count + " remote lookup(s) failed while building the plan.")
          : null,
        cleanupState && cleanupState.status === "waiting"
          ? React.createElement(Alert, { variant: "secondary" }, "No cleanup plan is available. Scan tags to build one.")
          : null,
        cleanupState && cleanupState.status === "loading"
          ? React.createElement("p", { className: "text-muted" }, React.createElement(Spinner, { animation: "border", size: "sm", className: "mr-2" }), "Loading cleanup status…")
          : null,
        cleanupState && cleanupState.status !== "waiting"
          ? React.createElement(
              "div",
              { className: "d-flex flex-wrap mb-3", "aria-label": "Cleanup overview" },
              React.createElement(Badge, { variant: "secondary", className: "mr-2 mb-1" }, (cleanupState.tag_count || 0) + " tags"),
              React.createElement(Badge, { variant: "secondary", className: "mr-2 mb-1" }, (cleanupState.duplicate_count || 0) + " fuzzy groups"),
              React.createElement(Badge, { variant: "secondary", className: "mr-2 mb-1" }, (cleanupState.split_count || 0) + " alias-bearing tags"),
              React.createElement(Badge, { variant: cleanupState.failure_count ? "warning" : "secondary", className: "mr-2 mb-1" }, (cleanupState.failure_count || 0) + " lookup failures"),
              React.createElement(Badge, { variant: cleanupState.status === "failed" ? "danger" : cleanupState.status === "applied" ? "success" : "info", className: "mb-1" }, cleanupState.status)
            )
          : null,
        cleanupState && ["completed", "applied"].includes(cleanupState.status)
          ? React.createElement(
              React.Fragment,
              null,
              React.createElement(
                Nav,
                { variant: "tabs", role: "tablist", className: "mb-3" },
                [
                  ["tags", "Junk tags", cleanupState.tag_count],
                  ["duplicates", "Duplicates", cleanupState.duplicate_count],
                  ["splits", "Alias splits", cleanupState.split_count],
                ].map(function (tab) {
                  return React.createElement(
                    Nav.Item,
                    { key: tab[0] },
                    React.createElement(
                      Nav.Link,
                      {
                        as: "button",
                        type: "button",
                        active: cleanupSection === tab[0],
                        role: "tab",
                        "aria-selected": cleanupSection === tab[0],
                        onClick: function () { setCleanupSection(tab[0]); },
                      },
                      tab[1] + " (" + (tab[2] || 0) + ")"
                    )
                  );
                })
              ),
              React.createElement(
                "div",
                { className: "d-flex align-items-end flex-wrap mb-3" },
                React.createElement(
                  Form.Group,
                  { className: "flex-grow-1 mr-2 mb-2", style: { minWidth: "220px" } },
                  React.createElement(Form.Label, null, "Search " + (cleanupSection === "tags" ? "tags" : cleanupSection)),
                  React.createElement(Form.Control, {
                    type: "search",
                    value: cleanupView.query,
                    placeholder: "Search by tag name",
                    onChange: function (event) { changeCleanupView({ query: event.target.value }); },
                  })
                ),
                React.createElement(
                  Form.Group,
                  { className: "mr-2 mb-2" },
                  React.createElement(Form.Label, null, "Show"),
                  React.createElement(
                    Form.Control,
                    { as: "select", value: cleanupView.filter, onChange: function (event) { changeCleanupView({ filter: event.target.value }); } },
                    cleanupSection === "tags"
                      ? [React.createElement("option", { key: "unused", value: "unused" }, "Unused tags"), React.createElement("option", { key: "all", value: "all" }, "All tags"), React.createElement("option", { key: "selected", value: "selected" }, "Selected tags")]
                      : cleanupSection === "duplicates"
                        ? [React.createElement("option", { key: "all", value: "all" }, "All groups"), React.createElement("option", { key: "conflicts", value: "conflicts" }, "Remote conflicts"), React.createElement("option", { key: "selected", value: "selected" }, "Selected groups")]
                        : [React.createElement("option", { key: "all", value: "all" }, "All splits"), React.createElement("option", { key: "selected", value: "selected" }, "Selected splits")]
                  )
                ),
                React.createElement(
                  Form.Group,
                  { className: "mb-2" },
                  React.createElement(Form.Label, null, "Sort"),
                  React.createElement(
                    Form.Control,
                    { as: "select", value: cleanupView.sort, onChange: function (event) { changeCleanupView({ sort: event.target.value }); } },
                    cleanupSection === "tags"
                      ? [React.createElement("option", { key: "usage_desc", value: "usage_desc" }, "Usage, high first"), React.createElement("option", { key: "usage_asc", value: "usage_asc" }, "Usage, low first"), React.createElement("option", { key: "name_asc", value: "name_asc" }, "Name A–Z"), React.createElement("option", { key: "name_desc", value: "name_desc" }, "Name Z–A")]
                      : cleanupSection === "duplicates"
                        ? [React.createElement("option", { key: "score_desc", value: "score_desc" }, "Similarity"), React.createElement("option", { key: "name_asc", value: "name_asc" }, "Name A–Z")]
                        : [React.createElement("option", { key: "name_asc", value: "name_asc" }, "Name A–Z"), React.createElement("option", { key: "aliases_desc", value: "aliases_desc" }, "Most aliases"), React.createElement("option", { key: "scenes_desc", value: "scenes_desc" }, "Most scenes")]
                  )
                )
              )
            )
          : null,
        cleanupLoading ? React.createElement("p", { className: "text-muted" }, React.createElement(Spinner, { animation: "border", size: "sm", className: "mr-2" }), "Loading review rows…") : null,
        !cleanupLoading && cleanupState && ["completed", "applied"].includes(cleanupState.status) && !cleanupReview.total
          ? React.createElement("p", { className: "text-muted" }, "No items match the current search and filters.")
          : null,
        cleanupTagRows.length
          ? React.createElement(
              React.Fragment,
              null,
              React.createElement("h4", { className: "h6" }, "Junk tags"),
              React.createElement(
                "p",
                { className: "text-muted" },
                "Select only tags you have reviewed for deletion. Selections stay checked while paging and filtering."
              ),
              React.createElement(
                Table,
                { striped: true, bordered: true, responsive: true },
                React.createElement(
                  "thead",
                  null,
                  React.createElement(
                    "tr",
                    null,
                    React.createElement("th", null, "Delete"),
                    React.createElement("th", null, "Tag"),
                    React.createElement("th", null, "Usage incl. children"),
                    React.createElement("th", null, "Objects")
                  )
                ),
                React.createElement(
                  "tbody",
                  null,
                  cleanupTagRows.map(function (row) {
                    const counts = row.counts || {};
                    return React.createElement(
                      "tr",
                      { key: row.id },
                      React.createElement(
                        "td",
                        null,
                        React.createElement("input", {
                          type: "checkbox",
                          checked: cleanupJunk.has(String(row.id)),
                          disabled: cleanupBusy || cleanupState.status === "applied",
                          onChange: function (event) {
                            setCleanupJunk(function (current) {
                              const next = new Set(current);
                              if (event.target.checked) next.add(String(row.id));
                              else next.delete(String(row.id));
                              return next;
                            });
                          },
                          "aria-label": "Delete " + row.name,
                        })
                      ),
                      React.createElement("td", null, row.name, row.aliases && row.aliases.length ? " (" + row.aliases.join(", ") + ")" : ""),
                      React.createElement("td", null,
                        row.usage || 0,
                        row.usage !== row.direct_usage
                          ? React.createElement("div", { className: "small text-muted" }, (row.direct_usage || 0) + " direct")
                          : null
                      ),
                      React.createElement(
                        "td",
                        { className: "small" },
                        "Scenes " + (counts.scene_count || 0) +
                          " · Markers " + (counts.scene_marker_count || 0) +
                          " · Images " + (counts.image_count || 0) +
                          " · Galleries " + (counts.gallery_count || 0) +
                          " · Performers " + (counts.performer_count || 0) +
                          " · Studios " + (counts.studio_count || 0) +
                          " · Groups " + (counts.group_count || 0)
                      )
                    );
                  })
                )
              )
            )
          : null,
        cleanupDuplicateRows.length
          ? React.createElement(
              React.Fragment,
              null,
              React.createElement("h4", { className: "h6" }, "Duplicates"),
              React.createElement("p", { className: "text-muted" }, "Choose the survivor and explicitly select its source tags. Remote identity conflicts require the override checkbox."),
              React.createElement("div", { className: "d-flex justify-content-between align-items-center mb-2" },
                React.createElement("strong", null, "Duplicate group " + cleanupReview.page + " of " + cleanupReview.total),
                React.createElement("div", null,
                  React.createElement(Button, {
                    size: "sm", variant: "outline-secondary", className: "mr-2",
                    disabled: !cleanupPage.hasPrevious,
                    onClick: function () { changeCleanupView({ page: cleanupReview.page - 1 }); },
                  }, "Previous group"),
                  React.createElement(Button, {
                    size: "sm", variant: "primary",
                    disabled: !cleanupPage.hasNext,
                    onClick: function () { changeCleanupView({ page: cleanupReview.page + 1 }); },
                  }, "Next group")
                )
              ),
              cleanupDuplicateRows.map(function (group) {
                const choice = cleanupDuplicatesChoice[group.id] || { survivor_id: "", source_ids: [], override_remote_ids: false };
                return React.createElement(
                  "div",
                  { key: group.id, className: "border rounded p-2 mb-2" },
                  React.createElement(
                    "div",
                    { className: "d-flex align-items-center mb-2 flex-wrap" },
                    React.createElement("strong", { className: "mr-2" }, "Similarity " + group.score),
                    React.createElement(Badge, { variant: duplicateReviewed(group, choice) ? "success" : "secondary", className: "mr-2" }, duplicateReviewed(group, choice) ? "Reviewed" : "Unreviewed"),
                    group.conflicts && group.conflicts.length
                      ? React.createElement(Badge, { variant: "warning", className: "mr-2" }, "Remote identity conflict")
                      : null
                  ),
                  React.createElement(Table, { bordered: true, responsive: true, size: "sm" },
                    React.createElement("thead", null, React.createElement("tr", null,
                      React.createElement("th", null, "Survivor"),
                      React.createElement("th", null, "Source"),
                      React.createElement("th", null, "Name"),
                      React.createElement("th", null, "Aliases"),
                      React.createElement("th", null, "Usage"),
                      React.createElement("th", null, "Remote state")
                    )),
                    React.createElement("tbody", null, (group.tags || []).map(function (tag) {
                      const id = String(tag.id);
                      const checked = choice.source_ids.indexOf(id) >= 0;
                      return React.createElement("tr", { key: id },
                        React.createElement("td", null, React.createElement("input", {
                          type: "radio", name: "survivor-" + group.id, checked: choice.survivor_id === id,
                          disabled: cleanupBusy || cleanupState.status === "applied",
                          onChange: function () { changeDuplicateChoice(group, "survivor_id", id); },
                          "aria-label": "Keep " + tag.name,
                        })),
                        React.createElement("td", null, React.createElement("input", {
                          type: "checkbox", checked: checked,
                          disabled: cleanupBusy || cleanupState.status === "applied" || id === choice.survivor_id,
                          onChange: function (event) {
                            const next = choice.source_ids.slice();
                            if (event.target.checked) next.push(id);
                            else next.splice(next.indexOf(id), 1);
                            changeDuplicateChoice(group, "source_ids", next);
                          },
                          "aria-label": "Merge source " + tag.name,
                        })),
                        React.createElement("td", null, tag.name),
                        React.createElement("td", null, (tag.aliases || []).join(", ") || "—"),
                        React.createElement("td", null, tag.usage || 0),
                        React.createElement("td", { className: tag.conflict_endpoints && tag.conflict_endpoints.length ? "text-warning" : "" },
                          tag.conflict_endpoints && tag.conflict_endpoints.length
                            ? "Conflict: " + tag.conflict_endpoints.join(", ")
                            : (tag.remote_endpoints || []).join(", ") || "None"
                        )
                      );
                    }))
                  ),
                  React.createElement(
                    "label",
                    { className: "small text-warning font-weight-bold" },
                    React.createElement("input", {
                      type: "checkbox",
                      className: "mr-2",
                      checked: Boolean(choice.override_remote_ids),
                      disabled: cleanupBusy || cleanupState.status === "applied" || !(group.conflicts || []).length,
                      onChange: function (event) { changeDuplicateChoice(group, "override_remote_ids", event.target.checked); },
                    }),
                    "Override conflicting remote identities"
                  )
                );
              })
            )
          : null,
        cleanupSplitRows.length
          ? React.createElement(
              React.Fragment,
              null,
              React.createElement("h4", { className: "h6" }, "Alias splits"),
              React.createElement("p", { className: "text-muted" }, "Open one alias-bearing parent to load its bounded candidate review. Choices stay selected while navigating."),
              React.createElement(
                Table,
                { striped: true, bordered: true, responsive: true },
                React.createElement("thead", null, React.createElement("tr", null,
                  React.createElement("th", null, "Tag"),
                  React.createElement("th", null, "Aliases"),
                  React.createElement("th", null, "Scenes"),
                  React.createElement("th", null, "Candidates"),
                  React.createElement("th", null, "Review")
                )),
                React.createElement("tbody", null, cleanupSplitRows.map(function (split) {
                  return React.createElement("tr", { key: split.tag_id, className: String(split.tag_id) === String(cleanupSplitParentId) ? "table-active" : "" },
                    React.createElement("td", null, split.name || split.tag_id),
                    React.createElement("td", null, (split.aliases || []).join(", ") || "—"),
                    React.createElement("td", null, split.scene_count || 0),
                    React.createElement("td", null, split.candidate_count || 0),
                    React.createElement("td", null, React.createElement(Button, {
                      size: "sm", variant: "outline-primary",
                      onClick: function () { setCleanupCandidateReview(null); setCleanupSplitParentId(String(split.tag_id)); },
                    }, String(split.tag_id) === String(cleanupSplitParentId) ? "Open" : "Review"))
                  );
                }))
              ),
              cleanupSplitParentId ? React.createElement(
                "div",
                { className: "border rounded p-3 mb-3" },
                React.createElement("div", { className: "d-flex justify-content-between align-items-center flex-wrap mb-2" },
                  React.createElement("div", null,
                    React.createElement("h5", { className: "mb-1" }, cleanupCandidateReview && cleanupCandidateReview.parent ? cleanupCandidateReview.parent.name : "Loading parent…"),
                    cleanupCandidateReview && cleanupCandidateReview.parent
                      ? React.createElement("div", { className: "small text-muted" },
                          "Aliases: " + ((cleanupCandidateReview.parent.aliases || []).join(", ") || "none") +
                          " · Scenes: " + (cleanupCandidateReview.parent.scene_count || 0) +
                          " · Candidates: " + (cleanupCandidateReview.parent.candidate_count || 0)
                        )
                      : null
                  ),
                  React.createElement("div", null,
                    React.createElement(Button, {
                      size: "sm", variant: "outline-secondary", className: "mr-2",
                      disabled: cleanupSplitParentIndex <= 0,
                      onClick: function () { setCleanupCandidateReview(null); setCleanupSplitParentId(String(cleanupSplitRows[cleanupSplitParentIndex - 1].tag_id)); },
                    }, "Previous parent"),
                    React.createElement(Button, {
                      size: "sm", variant: "outline-secondary",
                      disabled: cleanupSplitParentIndex < 0 || cleanupSplitParentIndex >= cleanupSplitRows.length - 1,
                      onClick: function () { setCleanupCandidateReview(null); setCleanupSplitParentId(String(cleanupSplitRows[cleanupSplitParentIndex + 1].tag_id)); },
                    }, "Next parent")
                  )
                ),
                cleanupCandidateLoading ? React.createElement("p", { className: "text-muted" }, React.createElement(Spinner, { animation: "border", size: "sm", className: "mr-2" }), "Loading up to 100 candidates…") : null,
                !cleanupCandidateLoading && cleanupCandidateReview && !cleanupCandidateReview.total
                  ? React.createElement("p", { className: "text-muted" }, "No candidates for this parent.")
                  : null,
                !cleanupCandidateLoading ? cleanupCandidateGroups.map(function (group) {
                  const visible = group.exact.concat(group.fuzzy, group.manual);
                  return React.createElement("section", { key: group.alias, className: "border-top pt-2 mt-2" },
                    React.createElement("div", { className: "d-flex align-items-center flex-wrap mb-2" },
                      React.createElement("strong", { className: "mr-2" }, "Alias: " + group.alias),
                      React.createElement(Button, {
                        size: "sm", variant: "outline-primary", className: "mr-2",
                        disabled: !group.exact.length || cleanupState.status === "applied",
                        onClick: function () { bulkAliasCandidates(group.exact, "select"); },
                      }, "Select exact matches"),
                      React.createElement(Form.Control, {
                        as: "select", size: "sm", defaultValue: "", className: "mr-2", style: { width: "auto" },
                        disabled: cleanupState.status === "applied",
                        onChange: function (event) { if (event.target.value) bulkAliasCandidates(visible, "action", event.target.value); event.target.value = ""; },
                        "aria-label": "Set action for selected " + group.alias + " candidates",
                      },
                        React.createElement("option", { value: "" }, "Set selected action…"),
                        React.createElement("option", { value: "child-only" }, "Child only"),
                        React.createElement("option", { value: "parent-only" }, "Parent only"),
                        React.createElement("option", { value: "parent-plus-child" }, "Parent + child"),
                        React.createElement("option", { value: "skip" }, "Skip")
                      ),
                      React.createElement(Button, {
                        size: "sm", variant: "outline-secondary", disabled: cleanupState.status === "applied",
                        onClick: function () { bulkAliasCandidates(visible, "clear"); },
                      }, "Clear visible selections")
                    ),
                    renderCandidateTable("Exact matches", group.exact, false),
                    renderCandidateTable("Fuzzy matches", group.fuzzy, true),
                    renderCandidateTable("Manual / no evidence", group.manual, false)
                  );
                }) : null,
                cleanupCandidateReview && cleanupCandidateReview.total
                  ? React.createElement("div", { className: "d-flex justify-content-between align-items-center" },
                      React.createElement("span", { className: "small text-muted" },
                        "Candidates " + (((cleanupCandidateReview.page - 1) * cleanupCandidateReview.per_page) + 1) + "–" +
                        Math.min(cleanupCandidateReview.page * cleanupCandidateReview.per_page, cleanupCandidateReview.total) + " of " + cleanupCandidateReview.total
                      ),
                      React.createElement("div", null,
                        React.createElement(Button, {
                          size: "sm", variant: "outline-secondary", className: "mr-2",
                          disabled: cleanupCandidateLoading || cleanupCandidateReview.page <= 1,
                          onClick: function () { loadCandidatePage(cleanupCandidateReview.page - 1); },
                        }, "Previous candidates"),
                        React.createElement(Button, {
                          size: "sm", variant: "outline-secondary",
                          disabled: cleanupCandidateLoading || cleanupCandidateReview.page * cleanupCandidateReview.per_page >= cleanupCandidateReview.total,
                          onClick: function () { loadCandidatePage(cleanupCandidateReview.page + 1); },
                        }, "Next candidates")
                      )
                    )
                  : null
              ) : null
            )
          : null,
        cleanupState && ["completed", "applied"].includes(cleanupState.status) && cleanupReview.total
          ? React.createElement(
              "div",
              { className: "d-flex justify-content-between align-items-center mb-3" },
              React.createElement("span", { className: "text-muted" },
                cleanupSection === "duplicates"
                  ? "Duplicate group " + cleanupReview.page + " of " + cleanupReview.total
                  : "Showing " + cleanupPage.start + "–" + cleanupPage.end + " of " + cleanupReview.total
              ),
              React.createElement(
                "div",
                null,
                React.createElement(Button, {
                  size: "sm",
                  variant: "outline-secondary",
                  className: "mr-2",
                  disabled: !cleanupPage.hasPrevious,
                  onClick: function () { changeCleanupView({ page: cleanupReview.page - 1 }); },
                }, "Previous"),
                React.createElement(Button, {
                  size: "sm",
                  variant: "outline-secondary",
                  disabled: !cleanupPage.hasNext,
                  onClick: function () { changeCleanupView({ page: cleanupReview.page + 1 }); },
                }, "Next")
              )
            )
          : null,
        cleanupState && ["completed", "applied"].includes(cleanupState.status)
          ? React.createElement(
              React.Fragment,
              null,
              React.createElement(
                "div",
                { className: "border-top mt-4 pt-3 d-flex align-items-center" },
                React.createElement(
                  Button,
                  {
                    variant: cleanupBackup ? "success" : "outline-primary",
                    onClick: backupCleanup,
                    disabled: cleanupBusy || cleanupState.status === "applied",
                    className: "mr-2",
                  },
                  cleanupBackup ? "Database backup ready" : "Create database backup"
                ),
                React.createElement(
                  Button,
                  {
                    variant: "danger",
                    onClick: applyCleanup,
                    disabled: !cleanupReady || cleanupState.status === "applied",
                  },
                  cleanupBusy ? "Applying…" : "Apply reviewed cleanup"
                )
              ),
              !cleanupBackup
                ? React.createElement("p", { className: "small text-muted mt-2" }, "A fresh database-only backup is required before applying. This gate resets after a page reload.")
                : null
            )
          : null
      )
    );
  }

  register.route(route, TagOrganizerPage);

  patch.before("MainNavBar.UtilityItems", function (props) {
    const { Icon } = api.components;
    return [
      {
        children: React.createElement(
          React.Fragment,
          null,
          props.children,
          React.createElement(
            NavLink,
            {
              className: "nav-utility",
              exact: true,
              to: route,
            },
            React.createElement(
              Button,
              {
                className: "minimal d-flex align-items-center h-100",
                title: "Tag Organizer",
                "aria-label": "Tag Organizer",
              },
              React.createElement(Icon, {
                icon: faTags,
              })
            )
          )
        ),
      },
    ];
  });
})(typeof window === "undefined" ? {} : window);
