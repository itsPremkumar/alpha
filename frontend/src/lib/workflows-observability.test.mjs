// workflows-observability.test.mjs — contract and honesty pins for the run
// inspector client (history, report, fork, simulate, control, executors).
//
// The honesty rules this file exists to enforce, because each one is a way a
// dashboard can quietly lie:
//
// - A missing measurement stays unknown. `timeline_source: "unavailable"` must
//   survive as "unavailable" and must NOT become an empty-but-valid-looking
//   timeline, and a missing `critical_path` must not become an empty path that
//   reads as "nothing is slow".
// - The report carries NO acceptance or verified field. If the server ever grows
//   one, this file fails so a completed run cannot start rendering as verified.
// - `unmatched: true` from a signal must survive: a signal nothing waited on
//   changed nothing, and the UI has to say so.
// - Failures reject with the server's reason instead of resolving to an empty
//   result that looks like "nothing was recorded".
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const httpStub = `
let handler = (path, method, payload) => { throw new Error("no stub response configured"); };
export function setHttpHandler(fn) { handler = fn; }
export async function get(path) { return handler(path, "GET", undefined); }
export async function send(path, method, payload) { return handler(path, method, payload); }
export function pick(obj, keys, fallback) {
  if (obj && typeof obj === "object") {
    const rec = obj;
    for (const k of keys) { if (rec[k] !== undefined && rec[k] !== null) return rec[k]; }
  }
  return fallback;
}
export function asList(body, keys) {
  if (Array.isArray(body)) return body;
  for (const k of keys) {
    if (body && typeof body === "object" && Array.isArray(body[k])) return body[k];
  }
  return [];
}
export function errMsg(err) { return err instanceof Error ? err.message : "Something went wrong."; }
`;
const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;
const stubUrl = toDataUrl(httpStub);

const source = readFileSync(new URL("./workflows.ts", import.meta.url), "utf8");
let code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
code = code.replace(/from\s+"\.\/http"/, `from "${stubUrl}"`);

const wf = await import(toDataUrl(code));
const { setHttpHandler } = await import(stubUrl);

const RUN = {
  run_id: "run-1",
  workflow_id: "wf-1",
  status: "running",
  node_states: {},
  completed_nodes: [],
  failed_nodes: [],
  waiting_nodes: [],
  state: {},
  metrics: {},
};

test("getRunHistory hits GET /workflows/runs/{id}/history and maps the timeline", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/workflows/runs/run-1/history");
    assert.equal(method, "GET");
    return {
      run_id: "run-1",
      count: 2,
      events: [
        { index: 1, event_id: "e1", event_type: "workflow_started", timestamp: "t1", node_id: null, reason: null },
        { index: 2, event_id: "e2", event_type: "node_completed", timestamp: "t2", node_id: "n1", reason: null },
      ],
    };
  });
  const history = await wf.getRunHistory("run-1");
  assert.equal(history.count, 2);
  assert.equal(history.events[0].index, 1);
  assert.equal(history.events[1].node_id, "n1");
  assert.equal(history.events[0].node_id, null, "an absent node must stay null, not become \"\"");
});

test("getRunHistory URL-encodes the run id", async () => {
  setHttpHandler((path) => {
    assert.equal(path, "/workflows/runs/run%2Fweird/history");
    return { run_id: "run/weird", count: 0, events: [] };
  });
  await wf.getRunHistory("run/weird");
});

test("getRunHistory rejects with the server's reason", async () => {
  setHttpHandler(() => {
    throw new Error("Run 'run-1' not found.");
  });
  await assert.rejects(() => wf.getRunHistory("run-1"), /not found/);
});

test("getRunReport maps measured observability from the event log", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/workflows/runs/run-1/report");
    assert.equal(method, "GET");
    return {
      run_id: "run-1",
      workflow_id: "wf-1",
      status: "completed",
      history_depth: 9,
      first_event: { index: 1, event_id: "e1", event_type: "workflow_started", timestamp: "t", idempotency_key: null, node_id: null, reason: null },
      last_event: { index: 9, event_id: "e9", event_type: "workflow_completed", timestamp: "t", idempotency_key: null, node_id: null, reason: null },
      status_transitions: [{ from: "running", to: "completed" }],
      applied_patches: 0,
      durability: { attached: true, write_failures: 0, last_error: null },
      provenance: { graph_version: 2, owner_id: "u1" },
      observability: {
        run_id: "run-1",
        status: "completed",
        terminal: true,
        nodes_total: 2,
        nodes_completed: 2,
        nodes_failed: 0,
        nodes_waiting: 0,
        waves_dispatched: 1,
        wave_metrics: [{ wave_index: 1, nodes: ["a", "b"], node_count: 2, concurrency: 2, elapsed_seconds: 0.5 }],
        timed_executions: 2,
        measured_executions: 2,
        timeline_source: "event_log",
        timeline_complete: true,
        total_measured_seconds: 0.5,
        slowest_nodes: [{ node_id: "a", duration_seconds: 0.4, status: "succeeded", tokens_consumed: 3 }],
        timed_out_nodes: [],
        critical_path: { path: ["a", "b"], total_seconds: 0.5, measured_nodes: 2, complete: true, reason: "" },
        timeline: [
          { node_id: "a", started_at: "t", ended_at: "t", duration_seconds: 0.4, status: "succeeded", tokens_consumed: 3, timed_out: false },
        ],
      },
    };
  });
  const report = await wf.getRunReport("run-1");
  assert.equal(report.observability.timeline_source, "event_log");
  assert.equal(report.observability.measured_executions, 2);
  assert.equal(report.observability.critical_path.complete, true);
  assert.deepEqual(report.observability.critical_path.path, ["a", "b"]);
  assert.equal(report.observability.wave_metrics[0].concurrency, 2);
  assert.equal(report.history_depth, 9);
});

test("getRunReport keeps an unavailable timeline unavailable, not empty-but-valid", async () => {
  setHttpHandler(() => ({
    run_id: "run-1",
    workflow_id: "wf-1",
    status: "running",
    history_depth: 1,
    first_event: null,
    last_event: null,
    status_transitions: [],
    applied_patches: 0,
    durability: {},
    provenance: {},
    observability: { timeline_source: "unavailable" },
  }));
  const report = await wf.getRunReport("run-1");
  assert.equal(report.observability.timeline_source, "unavailable");
  assert.equal(report.observability.measured_executions, 0);
  assert.deepEqual(report.observability.timeline, []);
  assert.equal(report.observability.critical_path.complete, false);
  assert.equal(report.observability.critical_path.path.length, 0);
});

test("getRunReport exposes no acceptance or verified field", async () => {
  setHttpHandler(() => ({
    run_id: "run-1",
    status: "completed",
    observability: { timeline_source: "event_log", status: "completed" },
  }));
  const report = await wf.getRunReport("run-1");
  assert.equal("acceptance_passed" in report, false, "a completed run is not an accepted run");
  assert.equal("verified" in report, false);
  assert.equal("acceptance_passed" in report.observability, false);
  assert.equal("verified" in report.observability, false);
});

test("getRunReport fails loudly when the server sends an acceptance field", async () => {
  // Guards the invariant above against a future server adding the claim: the
  // client must not silently start surfacing it as if it were meaningful.
  setHttpHandler(() => ({
    run_id: "run-1",
    status: "completed",
    observability: { timeline_source: "event_log", status: "completed", acceptance_passed: true },
  }));
  const report = await wf.getRunReport("run-1");
  assert.equal(
    "acceptance_passed" in report.observability,
    false,
    "the client maps only the measured execution fields, so a server-side acceptance claim cannot leak in",
  );
});

test("forkWorkflowRun posts the fork point and returns the new run id", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/workflows/runs/run-1/fork");
    assert.equal(method, "POST");
    assert.equal(payload.at_event_id, "e5");
    assert.equal("reset_completed_nodes" in payload, false, "an unset opt-in must not be sent as false");
    return {
      run_id: "fork_abc",
      workflow_id: "wf-1@fork:fork_abc",
      status: "running",
      source_run_id: "run-1",
      forked_at_event_id: "e5",
      forked_at_index: 5,
      graph_version: 1,
      inherited_completed_nodes: ["a"],
      inherited_state_keys: ["objective"],
      replayed_events: 5,
      notes: [],
    };
  });
  const forked = await wf.forkWorkflowRun("run-1", { at_event_id: "e5" });
  assert.equal(forked.run_id, "fork_abc");
  assert.notEqual(forked.run_id, "run-1");
  assert.equal(forked.source_run_id, "run-1");
  assert.deepEqual(forked.inherited_completed_nodes, ["a"]);
});

test("forkWorkflowRun surfaces the danger note when resetting completed nodes", async () => {
  setHttpHandler((_p, _m, payload) => {
    assert.equal(payload.reset_completed_nodes, true, "the dangerous opt-in must be sent explicitly");
    return {
      run_id: "fork_x",
      source_run_id: "run-1",
      forked_at_event_id: "e9",
      forked_at_index: 9,
      inherited_completed_nodes: [],
      inherited_state_keys: [],
      replayed_events: 9,
      notes: ["completed nodes were RESET: this fork will repeat their side effects"],
    };
  });
  const forked = await wf.forkWorkflowRun("run-1", { reset_completed_nodes: true });
  assert.equal(forked.inherited_completed_nodes.length, 0);
  assert.ok(forked.notes[0].includes("repeat their side effects"));
});

test("simulateWorkflow posts to /workflows/simulate and keeps the dry-run label", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/workflows/simulate");
    assert.equal(method, "POST");
    assert.equal(payload.workflow_id, "wf-1");
    assert.equal(payload.max_waves, 25);
    return {
      workflow_id: "wf-1",
      run_id: "sim-1",
      status: "completed",
      waves: 2,
      nodes_visited: ["a", "b"],
      node_outcomes: { a: "succeeded", b: "succeeded" },
      state_keys: ["objective"],
      simulated: true,
      execution_label: "dry_run_simulation",
      notes: [],
    };
  });
  const sim = await wf.simulateWorkflow({ workflow_id: "wf-1" });
  assert.equal(sim.simulated, true);
  assert.equal(sim.execution_label, "dry_run_simulation");
  assert.equal(sim.node_outcomes.b, "succeeded");
});

test("suspend and resume hit the documented paths and reasons", async () => {
  const seen = [];
  setHttpHandler((path, method) => {
    seen.push(`${method} ${path}`);
    return RUN;
  });
  await wf.suspendWorkflowRun("run-1", "operator hold");
  await wf.resumeWorkflowRun("run-1", "back to work");
  assert.deepEqual(seen, [
    "POST /workflows/runs/run-1/suspend?reason=operator%20hold",
    "POST /workflows/runs/run-1/resume?reason=back%20to%20work",
  ]);
});

test("suspend omits the reason query entirely when none is given", async () => {
  setHttpHandler((path) => {
    assert.equal(path, "/workflows/runs/run-1/suspend");
    return RUN;
  });
  await wf.suspendWorkflowRun("run-1");
});

test("signalWorkflowRun keeps unmatched true so a typo cannot look delivered", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/workflows/runs/run-1/signals");
    assert.equal(method, "POST");
    assert.equal(payload.event, "typo.event");
    assert.equal(payload.payload, null, "an absent payload is sent as null, not omitted or invented");
    return { run: RUN, event: "typo.event", matched_nodes: [], released_nodes: [], unmatched: true };
  });
  const result = await wf.signalWorkflowRun("run-1", "typo.event");
  assert.equal(result.unmatched, true);
  assert.deepEqual(result.matched_nodes, []);
  assert.deepEqual(result.released_nodes, []);
});

test("signalWorkflowRun reports a matched release", async () => {
  setHttpHandler(() => ({
    run: RUN,
    event: "deploy.ok",
    matched_nodes: ["waiter"],
    released_nodes: ["waiter"],
    unmatched: false,
  }));
  const result = await wf.signalWorkflowRun("run-1", "deploy.ok", { approver: "prem" });
  assert.equal(result.unmatched, false);
  assert.deepEqual(result.released_nodes, ["waiter"]);
});

test("sweepWorkflowWaits posts to the sweep path", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/workflows/runs/run-1/sweep-waits");
    assert.equal(method, "POST");
    return { ...RUN, status: "failed" };
  });
  const swept = await wf.sweepWorkflowWaits("run-1");
  assert.equal(swept.status, "failed");
});

test("listWorkflowExecutors maps the explicit readiness contract", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/workflows/system/executors");
    assert.equal(method, "GET");
    return {
      bound: ["alpha.local.digest"],
      count: 1,
      domain_executors: ["alpha.local.model", "alpha.local.subagent", "alpha.local.tool"],
      domain_bound: [],
      domain_bindings_complete: false,
      public_dynamic_default_executor: "alpha.local.digest",
      domain_binding_policy: {
        mode: "host_managed_opt_in",
        configurable: false,
        reason: "operator approval and budget controls are not wired",
      },
      note: "a bound executor name means the node seam can resolve it",
    };
  });
  const listing = await wf.listWorkflowExecutors();
  assert.deepEqual(listing.bound, ["alpha.local.digest"]);
  assert.equal(listing.domain_bound.length, 0, "importing a module must never start spending tokens");
  assert.equal(listing.domain_executors.length, 3);
  assert.equal(listing.domain_bindings_complete, false);
  assert.equal(listing.public_dynamic_default_executor, "alpha.local.digest");
  assert.deepEqual(listing.domain_binding_policy, {
    mode: "host_managed_opt_in",
    configurable: false,
    reason: "operator approval and budget controls are not wired",
  });
  assert.equal(wf.getExecutorReadiness(listing), "projection");
});

test("listWorkflowExecutors keeps readiness unknown for an older Gateway response", async () => {
  setHttpHandler(() => ({
    bound: ["alpha.local.digest"],
    count: 1,
    domain_executors: ["alpha.local.model"],
    domain_bound: [],
    note: "legacy response",
  }));
  const listing = await wf.listWorkflowExecutors();
  assert.equal(listing.domain_bindings_complete, null);
  assert.equal(listing.public_dynamic_default_executor, null);
  assert.equal(listing.domain_binding_policy, null);
  assert.equal(wf.getExecutorReadiness(listing), "legacy");
});

test("executor readiness distinguishes unbound, partial, and host-bound states", () => {
  const base = {
    bound: [],
    count: 0,
    domain_executors: ["alpha.local.model", "alpha.local.tool"],
    domain_bound: [],
    domain_bindings_complete: false,
    public_dynamic_default_executor: "alpha.local.digest",
    domain_binding_policy: null,
    note: "",
  };
  assert.equal(wf.getExecutorReadiness({ ...base, bound: [] }), "unbound");
  assert.equal(
    wf.getExecutorReadiness({
      ...base,
      bound: ["alpha.local.model"],
      domain_bound: ["alpha.local.model"],
    }),
    "partially_host_bound",
  );
  assert.equal(
    wf.getExecutorReadiness({
      ...base,
      bound: ["alpha.local.model", "alpha.local.tool"],
      domain_bound: ["alpha.local.model", "alpha.local.tool"],
      domain_bindings_complete: true,
    }),
    "all_host_bound",
  );
  assert.equal(wf.getExecutorReadiness(null), "unknown");
});

test("every control rejects rather than returning an empty success", async () => {
  setHttpHandler(() => {
    throw new Error("boom");
  });
  await assert.rejects(() => wf.getRunReport("run-1"), /boom/);
  await assert.rejects(() => wf.forkWorkflowRun("run-1"), /boom/);
  await assert.rejects(() => wf.simulateWorkflow({ workflow_id: "wf-1" }), /boom/);
  await assert.rejects(() => wf.suspendWorkflowRun("run-1"), /boom/);
  await assert.rejects(() => wf.resumeWorkflowRun("run-1"), /boom/);
  await assert.rejects(() => wf.signalWorkflowRun("run-1", "e"), /boom/);
  await assert.rejects(() => wf.sweepWorkflowWaits("run-1"), /boom/);
  await assert.rejects(() => wf.listWorkflowExecutors(), /boom/);
});
