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
    const key = name.toLowerCase();
    return rows.map(function (row) {
      return row.name.toLowerCase() === key ? Object.assign({}, row, { is_local: true }) : row;
    });
  }

  function terminalJobStatus(status) {
    return ["FINISHED", "FAILED", "CANCELLED"].includes(status);
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
      assert.equal(terminalJobStatus("FINISHED"), true);
      assert.equal(terminalJobStatus("RUNNING"), false);
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
    const [job, setJob] = React.useState(null);
    const localTags = React.useRef(new Set());
    const filteredRows = visibleRows(rows, search, showLocal, localTags.current);
    const scanActive = Boolean(job && !terminalJobStatus(job.status));

    React.useEffect(function () {
      runOperation({ mode: "providers" })
        .then(function (result) {
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

    async function scan() {
      setBusy("scan");
      setRows([]);
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
              active: true,
              role: "tab",
              "aria-controls": "tag-organizer-find-panel",
              "aria-selected": true,
            },
            "Find Missing Tags"
          )
        )
      ),
      React.createElement(
        "div",
        {
          id: "tag-organizer-find-panel",
          role: "tabpanel",
          "aria-labelledby": "tag-organizer-find-tab",
        },
      React.createElement(
        "div",
        { className: "form-row align-items-end mb-3" },
        React.createElement(
          Form.Group,
          { className: "col-md-5 mb-2" },
          React.createElement(Form.Label, { htmlFor: "tag-organizer-search" }, "Search tags"),
          React.createElement(
            "div",
            { className: "d-flex" },
            React.createElement(Form.Control, {
              id: "tag-organizer-search",
              type: "search",
              value: search,
              placeholder: "Search by tag name",
              onChange: function (event) {
                setSearch(event.target.value);
              },
            }),
            React.createElement(
              Button,
              {
                variant: "outline-secondary",
                className: "ml-2",
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
          "div",
          { className: "col-md-auto mb-2" },
          React.createElement(
            Button,
            {
              variant: showLocal ? "secondary" : "outline-secondary",
              "aria-pressed": showLocal,
              onClick: function () {
                setShowLocal(!showLocal);
              },
            },
            "Show local tags"
          )
        )
      ),
      React.createElement(
        Form.Group,
        { className: "mb-3" },
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
        { onClick: scan, disabled: !provider || Boolean(busy) || scanActive, className: "mb-3" },
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
              className: "ml-2 mb-3",
              disabled: Boolean(busy),
              onClick: cancelScan,
            },
            busy === "cancel" ? "Stopping…" : "Cancel scan"
          )
        : null,
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
