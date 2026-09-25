// workflows.test.mjs — honesty pins for the Dynamic Workflow Engine client:
// server values pass through untouched (false stays false, null stays null,
// 0 stays 0), failures reject instead of returning fabricated data, and every
// path/method matches workflows.py.
// Pure Node test (node --test src/lib/workflows.test.mjs): transpiles
// workflows.ts and rewrites its ./http import to a data: URL stub — same
// pattern as load-failure-honesty.test.mjs.
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
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
export const DEFAULT_TIMEOUT_MS = 60000;
export const GATEWAY_BASE = "/api";
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

test("listWorkflows maps the {workflows,count} envelope verbatim", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/workflows");
    assert.equal(method, "GET");
    return {
      workflows: [{ id: "wf-1", name: "Demo", version: "1.0.0", description: "d", node_count: 3 }],
      count: 1,
    };
  });
  const list = await wf.listWorkflows();
  assert.deepEqual(list, [{ id: "wf-1", name: "Demo", version: "1.0.0", description: "d", node_count: 3 }]);
});

test("a non-array workflows payload maps to an empty list — no fabricated rows", async () => {
  setHttpHandler(() => ({ workflows: "not-an-array" }));
  assert.deepEqual(await wf.listWorkflows(), []);
});

test("getWorkflowRun preserves an honest waiting_approval state", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/workflows/runs/run_abc");
    assert.equal(method, "GET");
    return {
      run_id: "run_abc",
      workflow_id: "wf-1",
      graph_version: 2,
      status: "waiting_approval",
      state: { score: 1 },
      node_states: { n1: "succeeded", n2: "waiting" },
      completed_nodes: ["n1"],
      failed_nodes: [],
      waiting_nodes: ["n2"],
      waiting_reason: "Node 'n2' requires human approval before execution.",
      approval_request_id: "appr_1",
      created_at: "2026-09-24T00:00:00+00:00",
      updated_at: "2026-09-24T00:01:00+00:00",
    };
  });
  const run = await wf.getWorkflowRun("run_abc");
  assert.equal(run.status, "waiting_approval");
  assert.deepEqual(run.waiting_nodes, ["n2"]);
  assert.deepEqual(run.failed_nodes, []);
  assert.equal(run.node_states.n2, "waiting");
  assert.match(run.waiting_reason ?? "", /requires human approval/);
  assert.equal(run.graph_version, 2);
});

test("absent waiting fields stay null — never invented strings", async () => {
  setHttpHandler(() => ({ run_id: "r", workflow_id: "w", status: "running" }));
  const run = await wf.getWorkflowRun("r");
  assert.equal(run.waiting_reason, null);
  assert.equal(run.approval_request_id, null);
  assert.deepEqual(run.waiting_nodes, []);
});

test("replay report: false stays false, 0 emissions stays 0, mismatches untouched", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/workflows/runs/run_abc/replay");
    assert.equal(method, "POST");
    return {
      run_id: "run_abc",
      events_folded: 7,
      events_emitted_during_replay: 0,
      covered_fields: ["status", "state"],
      matches_live: false,
      mismatches: [{ field: "status", live: "failed", replayed: "running" }],
      replayed: { status: "running" },
    };
  });
  const report = await wf.replayWorkflowRun("run_abc");
  assert.equal(report.matches_live, false);
  assert.equal(report.events_emitted_during_replay, 0);
  assert.equal(report.events_folded, 7);
  assert.deepEqual(report.mismatches, [{ field: "status", live: "failed", replayed: "running" }]);
});

test("durability status: writable:false and write_failures pass through honestly", async () => {
  setHttpHandler((path) => {
    assert.equal(path, "/workflows/system/durability");
    return {
      dispatcher: { attached: false, write_failures: 2, last_error: "OSError: disk full" },
      store: { store_dir: "C:/ws/workflow_store", writable: false, writable_detail: "read-only volume", persisted_runs: ["run_1"], persisted_run_count: 1 },
    };
  });
  const d = await wf.getDurabilityStatus();
  assert.equal(d.store.writable, false);
  assert.match(d.store.writable_detail ?? "", /read-only/);
  assert.equal(d.dispatcher.attached, false);
  assert.equal(d.dispatcher.write_failures, 2);
  assert.equal(d.dispatcher.last_error, "OSError: disk full");
  assert.deepEqual(d.store.persisted_runs, ["run_1"]);
});

test("hydrate report keeps a degraded status and names corrupt runs", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/workflows/hydrate");
    assert.equal(method, "POST");
    return {
      status: "degraded",
      store_dir: "C:/ws/workflow_store",
      hydrated_runs: ["run_1"],
      skipped_existing: [],
      corrupt_runs: [{ run_id: "run_2", error: "projection failed validation" }],
      stale_projections: [],
      missing_graphs: [],
      missing_definitions: [],
      disclosures: ["run 3: no graph"],
    };
  });
  const report = await wf.hydrateWorkflowEngine();
  assert.equal(report.status, "degraded");
  assert.deepEqual(report.corrupt_runs, [{ run_id: "run_2", error: "projection failed validation" }]);
  assert.deepEqual(report.disclosures, ["run 3: no graph"]);
});

test("turn outcome: failed status, real failed_nodes and reason survive", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/workflows/turns");
    assert.equal(method, "POST");
    return {
      run_id: "run_x", workflow_id: "wf_x", mode: "normal", paradigm: "direct_agent",
      status: "failed", waves: 2, failed_nodes: ["n1"], reason: "fail-closed: 1 node(s) failed", handoff: null,
    };
  });
  const outcome = await wf.runWorkflowTurn({ prompt: "do the thing" });
  assert.equal(outcome.status, "failed");
  assert.deepEqual(outcome.failed_nodes, ["n1"]);
  assert.equal(outcome.reason, "fail-closed: 1 node(s) failed");
  assert.equal(outcome.handoff, null);
  assert.equal(outcome.waves, 2);
});

test("a failed step rejects instead of returning a fake run", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/workflows/runs/run_abc/step");
    assert.equal(method, "POST");
    throw new Error("HTTP 500 from gateway");
  });
  await assert.rejects(() => wf.stepWorkflowRun("run_abc"), /HTTP 500 from gateway/);
});

test("resolveRunApproval posts {approved,feedback} to the exact approvals path", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/workflows/runs/run_abc/approvals/n2");
    assert.equal(method, "POST");
    assert.deepEqual(payload, { approved: false, feedback: "too risky" });
    return { run_id: "run_abc", status: "failed", waiting_nodes: [] };
  });
  const run = await wf.resolveRunApproval("run_abc", "n2", false, "too risky");
  assert.equal(run.status, "failed");
});

test("startWorkflowRun posts the mode it was given", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/workflows/wf-1/runs");
    assert.equal(method, "POST");
    assert.deepEqual(payload, { initial_state: {}, mode: "normal" });
    return { run_id: "run_new", workflow_id: "wf-1", status: "pending" };
  });
  const run = await wf.startWorkflowRun("wf-1");
  assert.equal(run.run_id, "run_new");
});

test("checkpoints keep test_passed null when tests never ran", async () => {
  setHttpHandler((path) => {
    assert.equal(path, "/checkpoints");
    return [
      { checkpoint_id: "chk_1", label: "L", created_at: 1760000000.5, files_count: 4, test_passed: null, failure_count: 0, git_ref: null },
      { checkpoint_id: "chk_2", label: "T", created_at: 1760000001, files_count: 1, test_passed: true, failure_count: 0, git_ref: "refs/alpha-checkpoints/chk_2" },
    ];
  });
  const list = await wf.listCheckpoints();
  assert.equal(list[0].test_passed, null);
  assert.equal(list[1].test_passed, true);
  assert.equal(list[0].created_at, 1760000000.5);
});

test("jobs: null timestamps stay null (never epoch 0) and metadata title is surfaced", async () => {
  setHttpHandler((path) => {
    assert.match(path, /^\/jobs\?/);
    return [
      { job_id: "job-1", status: "queued", exit_code: 0, stdout: "", stderr: "", execution_seconds: 0, artifacts: [], error: null, started_at: null, completed_at: null, metadata: { title: "Build", priority: "high" } },
      { job_id: "job-2", status: "completed", exit_code: 1, stdout: "ok\n", stderr: "boom", execution_seconds: 1.25, artifacts: [], error: null, started_at: 100.5, completed_at: 101.75, metadata: {} },
    ];
  });
  const jobs = await wf.listJobs();
  assert.equal(jobs[0].started_at, null);
  assert.equal(jobs[0].completed_at, null);
  assert.equal(jobs[0].title, "Build");
  assert.equal(jobs[1].title, undefined);
  assert.equal(jobs[1].exit_code, 1);
  assert.equal(jobs[1].started_at, 100.5);
});

test("job logs map stdout/stderr/exit verbatim", async () => {
  setHttpHandler((path) => {
    assert.equal(path, "/jobs/job-1/logs");
    return { job_id: "job-1", status: "failed", exit_code: 2, stdout: "out", stderr: "err", execution_seconds: 3.5 };
  });
  const logs = await wf.getJobLogs("job-1");
  assert.equal(logs.exit_code, 2);
  assert.equal(logs.stdout, "out");
  assert.equal(logs.stderr, "err");
  assert.equal(logs.execution_seconds, 3.5);
});

test("plan history maps versions and keeps latest_source null when absent", async () => {
  setHttpHandler((path) => {
    assert.equal(path, "/workflows/wf-1/plans");
    return { workflow_id: "wf-1", versions: [1, 2], count: 2, latest_source: null };
  });
  const history = await wf.listWorkflowPlans("wf-1");
  assert.deepEqual(history.versions, [1, 2]);
  assert.equal(history.latest_source, null);
});

test("listWorkflowRuns maps runs envelope and preserves statuses honestly", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/workflows/runs?status=running&limit=10");
    assert.equal(method, "GET");
    return {
      runs: [
        {
          run_id: "run_dyn_1",
          workflow_id: "wf_dyn_1",
          status: "running",
          graph_version: 3,
          active_nodes: ["task_02"],
          completed_nodes: ["task_01"],
          failed_nodes: [],
          waiting_nodes: [],
          waiting_reason: null,
          created_at: "2026-09-24T12:00:00Z",
          updated_at: "2026-09-24T12:05:00Z",
        },
      ],
      count: 1,
    };
  });
  const runs = await wf.listWorkflowRuns({ status: "running", limit: 10 });
  assert.equal(runs.length, 1);
  assert.equal(runs[0].run_id, "run_dyn_1");
  assert.equal(runs[0].status, "running");
  assert.equal(runs[0].graph_version, 3);
  assert.deepEqual(runs[0].completed_nodes, ["task_01"]);
});

test("perceiveDynamicWorkflow calls /dynamic/perceive and maps intent & goal", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/workflows/dynamic/perceive");
    assert.equal(method, "POST");
    assert.equal(payload.prompt, "boost feature implementation");
    return {
      perception: {
        raw_prompt: "boost feature implementation",
        intent_type: "boost",
        primary_domain: "engineering",
        secondary_domains: [],
        complexity_score: 0.85,
        execution_tier: "multiagent_swarm",
        detected_slash_command: null,
        suggested_slash_command: "/swarm",
        extracted_goals: ["Build feature"],
      },
      goal: {
        goal_id: "goal_123",
        title: "Dynamic Goal",
        description: "Decomposed tasks",
        tasks: [{ task_id: "t1", title: "Task 1", category: "coding", assigned_role: "coder", depends_on: [], node_type: "agent" }],
        execution_waves: [["t1"]],
        acceptance_criteria: ["Pass"],
        saga_compensations: [],
      },
      resources: {
        goal_id: "goal_123",
        bots: {},
        skills: [],
        tools: ["read_file"],
        mcp_servers: [],
        model_tier: "deep_reasoning",
      },
    };
  });
  const res = await wf.perceiveDynamicWorkflow("boost feature implementation");
  assert.equal(res.perception.intent_type, "boost");
  assert.equal(res.perception.execution_tier, "multiagent_swarm");
  assert.equal(res.goal.tasks.length, 1);
});

test("executeDynamicWorkflow calls /dynamic/execute and maps full telemetry", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/workflows/dynamic/execute");
    assert.equal(method, "POST");
    assert.equal(payload.auto_execute, true);
    return {
      run_id: "run_exec_1",
      workflow_id: "wf_exec_1",
      status: "completed",
      perception: { intent_type: "build" },
      goal: { goal_id: "g1" },
      resources: { tools: ["run_command"] },
      total_steps: 5,
      completed_nodes: ["t1", "t2"],
      failed_nodes: [],
      compensated_nodes: [],
      node_outputs: { t1: "ok" },
      replans_count: 0,
      duration_ms: 120.5,
      error_summary: null,
      metadata: { acceptance_passed: true },
    };
  });
  const res = await wf.executeDynamicWorkflow({ prompt: "build system", auto_execute: true });
  assert.equal(res.run_id, "run_exec_1");
  assert.equal(res.status, "completed");
  assert.equal(res.total_steps, 5);
  assert.deepEqual(res.completed_nodes, ["t1", "t2"]);
  assert.equal(res.duration_ms, 120.5);
});

test("replanWorkflowRun and compensateWorkflowRun map responses cleanly", async () => {
  setHttpHandler((path, method) => {
    if (path.includes("/replan")) {
      assert.equal(method, "POST");
      return { status: "committed", new_graph_version: 2, patch_operations: ["add_node"], run: { run_id: "r1", workflow_id: "w1", status: "running" } };
    }
    if (path.includes("/compensate")) {
      assert.equal(method, "POST");
      return { run_id: "r1", status: "compensated", compensated_nodes: ["comp_t1"], count: 1 };
    }
    throw new Error(`unexpected path: ${path}`);
  });
  const replan = await wf.replanWorkflowRun("r1", { failed_node_id: "t1" });
  assert.equal(replan.status, "committed");
  assert.equal(replan.new_graph_version, 2);
  assert.deepEqual(replan.patch_operations, ["add_node"]);

  const comp = await wf.compensateWorkflowRun("r1");
  assert.equal(comp.status, "compensated");
  assert.deepEqual(comp.compensated_nodes, ["comp_t1"]);
  assert.equal(comp.count, 1);
});
