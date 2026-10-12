// Source for the embedded Stash worker; build-server.js bundles the shared workflow.
function server(core, gql, args, log, pause) {
  const plugin = "scene-import";
  const pending = {};
  // Go-backed arrays lose appended entries when their parent map is saved.
  let config = JSON.parse(JSON.stringify(gql("query{configuration{plugins}}").configuration.plugins[plugin] || {}));
  let state = config.state || null;
  function save(next = state) {
    state = next;
    config = { ...config, state };
    gql('mutation($input:Map!){configurePlugin(plugin_id:"scene-import",input:$input)}', { input: config });
  }
  function status(message) { if (state) { state.message = message; save(); } log(message); }
  function view() {
    let running = false;
    if (state?.workerJob) {
      const { findJob } = gql('query($id:ID!){findJob(input:{id:$id}){status error}}', { id: state.workerJob });
      running = state.phase !== "done" && !!findJob && ["READY", "RUNNING"].includes(findJob.status);
      if (!running && state.phase !== "done" && !state.error) state.error = findJob?.error || "Import stopped. Resume to continue saved progress.";
    }
    return { state, running, message: state?.message || "", error: state?.error || "" };
  }
  function queue() {
    const { runPluginTask } = gql('mutation($args:Map){runPluginTask(plugin_id:"scene-import",description:"Scene Import",args_map:$args)}', { args: { mode: "continue", token: state.token } });
    state.workerJob = runPluginTask;
    save();
  }
  if (args.mode === "status") {
    if (state && !["new", "retry", "done"].includes(state.phase)) {
      // Live reads must not overwrite a worker's newer checkpoint.
      state = { ...state };
      core.refreshRows(gql, state, () => {});
      if (state.job) {
        const { findJob: job } = gql('query($id:ID!){findJob(input:{id:$id}){status progress error}}', { id: state.job });
        if (job) {
          const label = { scan: "Scan and generation", generate: "Missing pHashes", identify: "StashDB", fallback: "ThePornDB metadata" }[state.phase];
          state.message = `${label}: ${job.status}${job.progress == null ? "" : ` (${Math.round(job.progress * 100)}%)`}`;
        }
      }
    }
    return view();
  }
  if (args.mode === "discard") {
    if (view().running) throw new Error("An import is still running");
    save(null);
    return view();
  }
  if (args.mode === "continue") {
    if (!state || state.token !== args.token || state.phase === "done") return view();
    const token = state.token;
    const workerJob = state.workerJob;
    function checkpoint(next) { save({ ...next, message: state?.message || "", token, workerJob }); }
    function wait(gql, id, message, unusedPause, observe) {
      const { findJob: job } = gql('query($id:ID!){findJob(input:{id:$id}){status progress error}}', { id });
      if (!job) throw new Error("Job history is unavailable. Check Stash's task log before continuing.");
      message(`${job.status}${job.progress == null ? "" : ` (${Math.round(job.progress * 100)}%)`}`);
      if (["FAILED", "CANCELLED", "STOPPING"].includes(job.status)) throw new Error(job.error || `Job ${job.status.toLowerCase()}`);
      if (job.status !== "FINISHED") throw pending;
      observe(job);
    }
    try {
      core.run(gql, state, checkpoint, status, pause, wait);
      delete state.error;
      save();
    } catch (error) {
      if (error === pending) queue();
      else { state.error = error.message; save(); log(`Import paused: ${error.message}`); }
    }
    return view();
  }
  if (!["scan", "retry"].includes(args.mode)) throw new Error("Unknown import operation");
  if (view().running) return view();
  const legacy = !state ? args.legacy : null;
  if (legacy) {
    if (!["new", "retry", "scan", "generate", "identify", "fallback", "link", "cleanup", "done"].includes(legacy.phase)
        || !Array.isArray(legacy.results) || ![legacy.ids || [], legacy.before || []].every((ids) => Array.isArray(ids) && ids.every((id) => typeof id === "string" && /^\d+$/.test(id)))) throw new Error("Saved browser import is invalid");
    state = JSON.parse(JSON.stringify(legacy));
  }
  if (args.mode === "retry" && state && state.phase !== "done") throw new Error("Finish or resume the pending import before starting a retry.");
  if (!state || state.phase === "done") state = { phase: args.mode === "retry" ? "retry" : "new", adoptIDs: state?.tracked ? [] : state?.ids || [], results: [], rows: [] };
  state.token = `${Date.now()}-${Math.random()}`;
  state.message = "Import queued on the Stash server";
  delete state.error;
  save();
  queue();
  return view();
}
