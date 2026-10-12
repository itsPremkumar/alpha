import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

/*
 * Completion-engine client contract.
 *
 * Three things are pinned here, and all three are honesty rather than shape:
 *
 *  1. **Absent is `null`, never `0`, `""` or `false`.** The router refuses to
 *     answer an unreadable ledger with a body of zeros, so a mapper here that
 *     reached for `?? 0` would undo that from the other side and hand the view
 *     "zero gates passed" when the truth is "we could not read the ledger".
 *  2. **The client calls the routes that exist.** Each recorded call is
 *     compared against the exact path and verb, so a renamed or repointed
 *     route fails here rather than at runtime.
 *  3. **Local refusals happen before a request.** A reason out of bounds or a
 *     filter the server would 422 is refused with the bound named, and the
 *     recorded-call list proves nothing went out.
 *
 * The vocabulary rules this surface is built around, each with a tempting
 * wrong reading, live in the last third of the file: a `waived` gate is not a
 * pass, a `not_run` check is not a failure, and a `stale` pass is not current.
 */

const read = (relative) =>
  readFileSync(new URL(relative, import.meta.url), "utf8");
const toDataUrl = (code) =>
  `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
const transpile = (source, extra = {}) =>
  ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
      ...extra,
    },
  }).outputText;

/* ── Recorded transport ────────────────────────────────────────────────── */

const calls = [];
let responses = {};

/*
 * The stub reads its state off `globalThis` rather than closing over module
 * locals: a `data:` URL module has no importable binding for them, and a
 * closure over locals defined in *this* module would capture a different array
 * than the one the assertions read.
 *
 * `errMsg` mirrors the real helper's generic 401/403/404 sentences, so the
 * `failureText` tests can tell "kept the server's words" from "fell back to
 * the shared paraphrase".
 */
const httpStub = toDataUrl(`
export async function get(path, timeoutMs) {
  globalThis.__calls.push({ path, method: "GET", body: undefined });
  const entry = globalThis.__responses["GET " + path];
  if (!entry) throw new Error("no recorded response for GET " + path);
  if (entry.reject) throw new Error(entry.reject);
  return entry.body;
}
export async function send(path, method, payload) {
  globalThis.__calls.push({ path, method, body: payload });
  const entry = globalThis.__responses[method + " " + path];
  if (!entry) throw new Error("no recorded response for " + method + " " + path);
  if (entry.reject) throw new Error(entry.reject);
  return entry.body;
}
export function errMsg(err) {
  if (err instanceof Error) {
    const status = typeof err.status === "number" ? err.status : 0;
    if (status === 0) return err.message;
    if (status === 401) return "Not allowed — this action needs different permissions.";
    if (status === 403) return "Not allowed — this action needs admin rights or a feature flag.";
    if (status === 404) return "Not found — it may have been deleted.";
    return err.message;
  }
  return "Something went wrong.";
}
`);

const source = read("./completion.ts");
const completionUrl = toDataUrl(
  transpile(source).replace(/from "\.\/http"/g, `from "${httpStub}"`),
);

globalThis.__calls = calls;
globalThis.__responses = responses;

const completion = await import(completionUrl);

function record(key, outcome) {
  responses[key] = outcome;
}

function lastCall() {
  return calls[calls.length - 1];
}

function resetCalls() {
  calls.length = 0;
}

/** An `ApiError`-shaped failure: numeric status plus the server's own text. */
function apiFailure(status, message) {
  return Object.assign(new Error(message), { status });
}

/* ── Fixtures ──────────────────────────────────────────────────────────── */

const SCOPE_A = {
  scope_id: "sc-abc123",
  owner_id: "user-1",
  thread_id: "thr_1",
  mode: "coding",
  status: "verifying",
  status_reason: "",
  request_snapshot: "Ship the parser",
  request_revision: 1,
  contract_valid: true,
  contract_digest: "sha256:bbbb",
  requirements_total: 3,
  requirements_met: 2,
  tasks_total: 4,
  tasks_verified: 1,
  tasks_in_flight: 1,
  gates_total: 5,
  gates_required: 3,
  gates_passed: 2,
  gates_stale: 1,
  evidence_total: 6,
  dispatches_total: 2,
  finalize_attempts: 0,
  no_progress_count: 0,
  budget_exhausted: false,
  created_at: "2026-10-10T09:00:00+00:00",
  updated_at: "2026-10-10T09:30:00+00:00",
  finished_at: null,
  final_status: null,
};

const GATE_PASSED = {
  gate_id: "gate-1",
  task_id: "task-1",
  title: "Tests pass",
  required: true,
  kind: "pytest",
  check_ref: "pytest.run",
  expectation: { kind: "exit_code", value: 0 },
  status: "passed",
  evidence_ids: ["ev-1"],
  risk_tier: "medium",
  approval_policy: "automatic",
  definition_digest: "sha256:cccc",
  attempts: 1,
  last_failure: "",
  waiver_reason: "",
  latest_evidence: {
    evidence_id: "ev-1",
    gate_id: "gate-1",
    task_id: "task-1",
    check_ref: "pytest.run",
    check_version: "1",
    outcome: "passed",
    reason: "",
    summary: "42 passed",
    exit_code: 0,
    output_preview: "42 passed in 3.1s",
    output_truncated: false,
    freshness: "current",
    invalidation_reason: "",
    duration_seconds: 3.1,
    worker: "ci",
    ended_at: "2026-10-10T09:29:00+00:00",
  },
  approval_live: true,
};

const GATE_WAIVED = {
  ...GATE_PASSED,
  gate_id: "gate-2",
  status: "waived",
  waiver_reason: "operator waived",
  latest_evidence: null,
  approval_live: null,
};

const GATE_STALE = {
  ...GATE_PASSED,
  gate_id: "gate-3",
  status: "stale",
  latest_evidence: {
    ...GATE_PASSED.latest_evidence,
    freshness: "stale",
    invalidation_reason: "definition changed",
  },
};

const TASK_A = {
  task_id: "task-1",
  title: "Write the parser",
  objective: "",
  parent_id: null,
  depends_on: [],
  gate_ids: ["gate-1"],
  state: "returned",
  owner: "agent-1",
  owns: ["src/parser.py"],
  expected_outputs: ["src/parser.py"],
  integration: false,
  dispatch_ids: ["dp-1"],
  blocked_reason: "",
  attempts: 1,
  max_attempts: 3,
  depth: 0,
  children: [],
  gate_statuses: { "gate-1": "passed" },
  dispatchable: false,
  readiness_reason: "already in flight",
  blocking: [],
};

const REFUSAL_BODY = {
  detail: "illegal transition from complete to running",
  code: "illegal_transition",
  subject: "sc-abc123",
};

const CONTRACT_INVALID_BODY = {
  detail:
    "contract for scope 'sc-abc123' has 2 blocking findings; work may not start",
  code: "contract_invalid",
  subject: "sc-abc123",
  findings: [
    {
      code: "unknown_check",
      severity: "error",
      message: "no check named 'shell.run'",
      subject_id: "gate-9",
    },
    {
      code: "gate_unbound",
      severity: "warning",
      message: "gate-4 covers no requirement",
      subject_id: "gate-4",
    },
  ],
};

/* ── Absent is `null`, never `0` ────────────────────────────────────────── */

test("mapHealth: an unreadable ledger reports count null, never 0", () => {
  const health = completion.mapHealth({
    readable: false,
    error: "ledger truncated at byte 41",
    path: "/x/completion.json",
  });
  assert.equal(health.count, null);
  assert.equal(health.events, null);
  assert.equal(health.readable, false);
  assert.equal(health.error, "ledger truncated at byte 41");
});

test("mapHealth: an absent block stays absent rather than becoming defaults", () => {
  const health = completion.mapHealth({});
  assert.equal(health.enabled, null);
  assert.equal(health.mode, null);
  assert.equal(health.enforcing, null);
  assert.equal(health.checks, null);
  assert.equal(health.limits, null);
});

test("mapScopeSummary: a count the Gateway did not send is null, never 0", () => {
  const summary = completion.mapScopeSummary({
    scope_id: "sc-1",
    status: "planning",
  });
  assert.equal(summary.gates_passed, null);
  assert.equal(summary.tasks_total, null);
  assert.equal(summary.requirements_met, null);
  assert.equal(summary.budget_exhausted, null);
});

test("mapScopeSummary: a real 0 is preserved as 0, not turned into null", () => {
  const summary = completion.mapScopeSummary({
    scope_id: "sc-1",
    status: "running",
    gates_passed: 0,
    tasks_verified: 0,
  });
  assert.equal(summary.gates_passed, 0);
  assert.equal(summary.tasks_verified, 0);
});

test("mapScopeList: an omitted list is null and distinct from an empty one", () => {
  const missing = completion.mapScopeList({
    reported: true,
    scope: "owner",
    count: 0,
  });
  assert.equal(missing.scopes, null);

  const empty = completion.mapScopeList({
    reported: true,
    scope: "owner",
    count: 0,
    scopes: [],
  });
  assert.deepEqual(empty.scopes, []);
});

test("mapScopeList: reported false survives so the view cannot read it as zero rows", () => {
  const list = completion.mapScopeList({ reported: false });
  assert.equal(list.reported, false);
  assert.equal(list.count, null);
  assert.equal(list.scopes, null);
});

test("mapEventList: a journal that could not be read is not an empty journal", () => {
  const missing = completion.mapEventList({ scope_id: "sc-1" });
  assert.equal(missing.events, null);
  const empty = completion.mapEventList({
    scope_id: "sc-1",
    count: 0,
    events: [],
  });
  assert.deepEqual(empty.events, []);
});

test("mapContract: an absent findings list is null, not an unqualified valid contract", () => {
  const contract = completion.mapContract({ contract_id: "ct-1" });
  assert.equal(contract.findings, null);
  assert.equal(contract.requirements, null);
  assert.equal(contract.valid, null);
});

/* ── Enum strings are preserved verbatim ────────────────────────────────── */

test("mapScopeSummary: an unknown status string survives instead of being snapped", () => {
  const summary = completion.mapScopeSummary({
    scope_id: "sc-1",
    status: "awaiting_cosmic_harmony",
  });
  assert.equal(summary.status, "awaiting_cosmic_harmony");
});

test("mapGate: an unknown gate status survives instead of being snapped to pending", () => {
  const gate = completion.mapGate({ gate_id: "g", status: "transcendent" });
  assert.equal(gate.status, "transcendent");
});

test("mapTask: an unknown task state survives instead of being snapped to waiting", () => {
  const task = completion.mapTask({ task_id: "t", state: "pondering" });
  assert.equal(task.state, "pondering");
});

test("mapGate: an unknown freshness survives instead of being snapped to current", () => {
  const gate = completion.mapGate({
    gate_id: "g",
    status: "passed",
    latest_evidence: { evidence_id: "e", freshness: "frozen" },
  });
  assert.equal(gate.latest_evidence.freshness, "frozen");
});

test("mapTask: a gate_statuses table with a non-string value is unreadable, not partial", () => {
  const task = completion.mapTask({
    task_id: "t",
    state: "ready",
    gate_statuses: { "gate-1": "passed", "gate-2": 42 },
  });
  assert.equal(task.gate_statuses, null);
});

test("mapEvent: a payload with a non-string value is unreadable, not partial", () => {
  const event = completion.mapEvent({
    event_id: "e1",
    kind: "gate.passed",
    payload: { gate_id: "gate-1", attempts: 3 },
  });
  assert.equal(event.payload, null);
});

/* ── The routes, one by one ─────────────────────────────────────────────── */

test("fetchHealth calls GET /completion/health", async () => {
  resetCalls();
  record("GET /completion/health", { body: { readable: true, count: 7 } });
  await completion.fetchHealth();
  assert.deepEqual(lastCall(), {
    path: "/completion/health",
    method: "GET",
    body: undefined,
  });
});

test("fetchChecks calls GET /completion/checks", async () => {
  resetCalls();
  record("GET /completion/checks", { body: { checks: ["file.exists"] } });
  const checks = await completion.fetchChecks();
  assert.deepEqual(lastCall(), {
    path: "/completion/checks",
    method: "GET",
    body: undefined,
  });
  assert.deepEqual(checks.checks, ["file.exists"]);
});

test("fetchScopes calls GET /completion/scopes with a clamped limit", async () => {
  resetCalls();
  record("GET /completion/scopes?limit=100", {
    body: { reported: true, scopes: [] },
  });
  await completion.fetchScopes();
  assert.equal(lastCall().path, "/completion/scopes?limit=100");
});

test("fetchScopes carries every declared filter", async () => {
  resetCalls();
  record(
    "GET /completion/scopes?thread_id=thr_1&status=running&owner_id=user-1&include_terminal=false&limit=25",
    {
      body: { reported: true, scopes: [] },
    },
  );
  await completion.fetchScopes({
    thread_id: "thr_1",
    status: "running",
    owner_id: "user-1",
    include_terminal: false,
    limit: 25,
  });
  assert.equal(
    lastCall().path,
    "/completion/scopes?thread_id=thr_1&status=running&owner_id=user-1&include_terminal=false&limit=25",
  );
});

test("fetchScope, fetchContract, fetchReconciliation and the preview hit their exact paths", async () => {
  resetCalls();
  record("GET /completion/scopes/sc-1", { body: { scope: SCOPE_A } });
  record("GET /completion/scopes/sc-1/contract", {
    body: { contract_id: "ct-1" },
  });
  record("GET /completion/scopes/sc-1/reconciliation", {
    body: { basis: "heuristic_clause_match", clean: true },
  });
  record("GET /completion/scopes/sc-1/finalization/preview", {
    body: { complete: false, enforced: false },
  });

  await completion.fetchScope("sc-1");
  await completion.fetchContract("sc-1");
  await completion.fetchReconciliation("sc-1");
  await completion.fetchFinalizationPreview("sc-1");

  assert.deepEqual(
    calls.map((c) => `${c.method} ${c.path}`),
    [
      "GET /completion/scopes/sc-1",
      "GET /completion/scopes/sc-1/contract",
      "GET /completion/scopes/sc-1/reconciliation",
      "GET /completion/scopes/sc-1/finalization/preview",
    ],
  );
});

test("fetchEvents carries the default event limit and an optional kind", async () => {
  resetCalls();
  record("GET /completion/scopes/sc-1/events?limit=200", {
    body: { events: [] },
  });
  await completion.fetchEvents("sc-1");
  assert.equal(lastCall().path, "/completion/scopes/sc-1/events?limit=200");

  record("GET /completion/scopes/sc-1/events?limit=50&kind=gate.failed", {
    body: { events: [] },
  });
  await completion.fetchEvents("sc-1", { limit: 50, kind: "gate.failed" });
  assert.equal(
    lastCall().path,
    "/completion/scopes/sc-1/events?limit=50&kind=gate.failed",
  );
});

test("fetchApprovalBrief hits the nested gate path", async () => {
  resetCalls();
  record("GET /completion/scopes/sc-1/gates/gate-1/approval", {
    body: { gate_id: "gate-1" },
  });
  await completion.fetchApprovalBrief("sc-1", "gate-1");
  assert.equal(
    lastCall().path,
    "/completion/scopes/sc-1/gates/gate-1/approval",
  );
});

/* ── Mutations: verbs, bodies, and the paths they must not take ─────────── */

test("workerReturned posts to the return route and can never be a verify", async () => {
  resetCalls();
  record("POST /completion/scopes/sc-1/d dp/dp-1/return", { body: {} });
  record("POST /completion/scopes/sc-1/d dispatches/dp-1/return", { body: {} });
  const real = "POST /completion/scopes/sc-1/dispatches/dp-1/return";
  record(real, { body: { task_id: "task-1", state: "returned" } });

  const result = await completion.workerReturned("sc-1", "dp-1", "done", [
    "out/a.txt",
  ]);
  assert.equal(lastCall().method, "POST");
  assert.equal(
    lastCall().path,
    "/completion/scopes/sc-1/dispatches/dp-1/return",
  );
  assert.deepEqual(lastCall().body, {
    reason: "done",
    artifacts: ["out/a.txt"],
  });
  // The route records RETURNED. It cannot hand a task VERIFIED.
  assert.equal(result.state, "returned");
});

test("verifyTask posts the mode and worker verbatim", async () => {
  resetCalls();
  record("POST /completion/scopes/sc-1/tasks/task-1/verify", {
    body: { task_id: "task-1", state: "verified", passed: true, ran: 2 },
  });
  const result = await completion.verifyTask(
    "sc-1",
    "task-1",
    "reverify",
    "ci",
  );
  assert.deepEqual(lastCall().body, { mode: "reverify", worker: "ci" });
  assert.equal(result.passed, true);
});

test("finalizeScope sends rerun_required and omits the gate list when none is given", async () => {
  resetCalls();
  record("POST /completion/scopes/sc-1/finalize", {
    body: { complete: false, enforced: true },
  });
  await completion.finalizeScope("sc-1");
  assert.deepEqual(lastCall().body, { rerun_required: true });
});

test("finalizeScope sends an explicit gate list only when one is given", async () => {
  resetCalls();
  record("POST /completion/scopes/sc-1/finalize", {
    body: { complete: false },
  });
  await completion.finalizeScope("sc-1", false, ["gate-1", "gate-2"]);
  assert.deepEqual(lastCall().body, {
    rerun_required: false,
    rerun_gate_ids: ["gate-1", "gate-2"],
  });
});

test("approveGate, revokeGate, reviewGate and runGate hit their own nested paths", async () => {
  resetCalls();
  record("POST /completion/scopes/sc-1/gates/gate-1/approve", { body: {} });
  record("POST /completion/scopes/sc-1/gates/gate-1/revoke", {
    body: { revoked: true },
  });
  record("POST /completion/scopes/sc-1/gates/gate-1/review", {
    body: { gate_id: "gate-1", status: "passed" },
  });
  record("POST /completion/scopes/sc-1/gates/gate-1/run", {
    body: { evidence_id: "ev-9", outcome: "passed" },
  });

  await completion.approveGate("sc-1", "gate-1", "ops", "reviewed");
  await completion.revokeGate("sc-1", "gate-1", "changed my mind");
  await completion.reviewGate("sc-1", "gate-1", "pass", "ops", "looks right");
  await completion.runGate("sc-1", "gate-1", "ci");

  assert.deepEqual(
    calls.map((c) => c.path),
    [
      "/completion/scopes/sc-1/gates/gate-1/approve",
      "/completion/scopes/sc-1/gates/gate-1/revoke",
      "/completion/scopes/sc-1/gates/gate-1/review",
      "/completion/scopes/sc-1/gates/gate-1/run",
    ],
  );
  assert.deepEqual(calls[0].body, { approver: "ops", reason: "reviewed" });
  assert.deepEqual(calls[2].body, {
    verdict: "pass",
    reviewer: "ops",
    notes: "looks right",
  });
});

test("start, schedule and the other bodyless verbs send no payload", async () => {
  resetCalls();
  record("POST /completion/scopes/sc-1/start", { body: { started: true } });
  record("POST /completion/scopes/sc-1/schedule", {
    body: { dispatchable: [] },
  });

  await completion.startScope("sc-1");
  await completion.scheduleScope("sc-1");

  assert.equal(lastCall().body, undefined);
  assert.equal(calls[0].path, "/completion/scopes/sc-1/start");
  assert.equal(calls[1].path, "/completion/scopes/sc-1/schedule");
});

test("reviseContract and addTasks send the lists under the server's own keys", async () => {
  resetCalls();
  record("POST /completion/scopes/sc-1/contract/revise", {
    body: { revised: true },
  });
  record("POST /completion/scopes/sc-1/tasks", { body: { added: true } });

  await completion.reviseContract("sc-1", [{ intent: "works" }]);
  await completion.addTasks("sc-1", [{ title: "t" }], [{ gate_id: "g" }]);

  assert.deepEqual(lastCall().body, {
    tasks: [{ title: "t" }],
    gates: [{ gate_id: "g" }],
  });
  assert.deepEqual(calls[0].body, { requirements: [{ intent: "works" }] });
});

test("recoverScope sends host_states, never a bare hostStates", async () => {
  resetCalls();
  record("POST /completion/scopes/sc-1/recover", { body: { folded: 1 } });
  await completion.recoverScope("sc-1", { "dp-1": "finished" });
  assert.deepEqual(lastCall().body, { host_states: { "dp-1": "finished" } });
});

test("blockTask and cancelScope send the reason verbatim", async () => {
  resetCalls();
  record("POST /completion/scopes/sc-1/tasks/task-1/block", { body: {} });
  record("POST /completion/scopes/sc-1/cancel", { body: {} });

  await completion.blockTask("sc-1", "task-1", "waiting on a credential");
  await completion.cancelScope("sc-1", "the operator stopped it");

  assert.deepEqual(calls[0].body, { reason: "waiting on a credential" });
  assert.deepEqual(calls[1].body, { reason: "the operator stopped it" });
});

test("dispatchTask sends the agent, defaulting to an unattributed request", async () => {
  resetCalls();
  record("POST /completion/scopes/sc-1/dispatch", {
    body: { dispatch_id: "dp-1", state: "requested" },
  });
  await completion.dispatchTask("sc-1", "task-1");
  assert.deepEqual(lastCall().body, { task_id: "task-1", agent: "" });
});

test("confirmLaunch and launchFailed report what the host said, not an inference", async () => {
  resetCalls();
  record("POST /completion/scopes/sc-1/dispatches/dp-1/confirm", {
    body: { dispatch_id: "dp-1", state: "started", handle: "sess-9" },
  });
  record("POST /completion/scopes/sc-1/dispatches/dp-1/launch-failed", {
    body: { dispatch_id: "dp-1", state: "launch_failed" },
  });

  const confirmed = await completion.confirmLaunch("sc-1", "dp-1", "sess-9");
  const failed = await completion.launchFailed(
    "sc-1",
    "dp-1",
    "the host refused the job",
  );

  assert.equal(confirmed.state, "started");
  assert.equal(confirmed.handle, "sess-9");
  assert.equal(failed.state, "launch_failed");
  assert.deepEqual(calls[0].body, { handle: "sess-9" });
  assert.deepEqual(calls[1].body, { error: "the host refused the job" });
});

/* ── Local refusals happen before a request ─────────────────────────────── */

test("an unknown scope status is refused locally with the allowed set named", async () => {
  resetCalls();
  await assert.rejects(
    () => completion.fetchScopes({ status: "dunno" }),
    /unknown status "dunno"/,
  );
  assert.equal(calls.length, 0);
});

test("an unknown event kind is refused locally with the allowed set named", async () => {
  resetCalls();
  await assert.rejects(
    () => completion.fetchEvents("sc-1", { kind: "nothing.happened" }),
    /unknown kind "nothing.happened"/,
  );
  assert.equal(calls.length, 0);
});

test("an out-of-bounds reason is refused locally with the bound named", async () => {
  resetCalls();
  await assert.rejects(
    () => completion.blockTask("sc-1", "task-1", ""),
    /reason must be 1–2000 characters/,
  );
  await assert.rejects(
    () => completion.cancelScope("sc-1", "x".repeat(2001)),
    /reason must be 1–2000 characters/,
  );
  assert.equal(calls.length, 0);
});

test("an unknown verify mode is refused locally with the allowed set named", async () => {
  resetCalls();
  await assert.rejects(
    () => completion.verifyTask("sc-1", "task-1", "hope"),
    /unknown mode "hope"/,
  );
  assert.equal(calls.length, 0);
});

test("an unknown review verdict is refused locally with the allowed set named", async () => {
  resetCalls();
  await assert.rejects(
    () => completion.reviewGate("sc-1", "gate-1", "maybe", "ops"),
    /unknown verdict "maybe"/,
  );
  assert.equal(calls.length, 0);
});

/* ── Bounds are mirrored, not tightened ─────────────────────────────────── */

test("clampScopeLimit copies the server's ge=1, le=500 window", () => {
  assert.equal(
    completion.clampScopeLimit(undefined),
    completion.SCOPE_LIMIT_DEFAULT,
  );
  assert.equal(
    completion.clampScopeLimit(null),
    completion.SCOPE_LIMIT_DEFAULT,
  );
  assert.equal(
    completion.clampScopeLimit(Number.NaN),
    completion.SCOPE_LIMIT_DEFAULT,
  );
  assert.equal(completion.clampScopeLimit(0), 1);
  assert.equal(completion.clampScopeLimit(-9), 1);
  assert.equal(completion.clampScopeLimit(500), 500);
  assert.equal(completion.clampScopeLimit(5000), completion.SCOPE_LIMIT_MAX);
  assert.equal(completion.clampScopeLimit(12.6), 13);
});

test("clampEventLimit copies the server's ge=1, le=2000 window", () => {
  assert.equal(
    completion.clampEventLimit(undefined),
    completion.EVENT_LIMIT_DEFAULT,
  );
  assert.equal(completion.clampEventLimit(0), 1);
  assert.equal(completion.clampEventLimit(2000), 2000);
  assert.equal(completion.clampEventLimit(99999), completion.EVENT_LIMIT_MAX);
});

test("an empty filter is omitted rather than sent as a value", () => {
  assert.equal(
    completion.buildScopeListPath({ status: "", thread_id: "" }),
    "/completion/scopes?limit=100",
  );
});

/* ── A refusal keeps the server's words ─────────────────────────────────── */

test("failureText keeps a numeric-status refusal verbatim", () => {
  const err = apiFailure(
    403,
    "completion mutations require an administrator (caller 'u1' is a member)",
  );
  assert.equal(
    completion.failureText(err),
    "completion mutations require an administrator (caller 'u1' is a member)",
  );
});

test("failureText falls back to errMsg's paraphrase for a status-less error", () => {
  assert.equal(completion.failureText(apiFailure(0, "boom")), "boom");
  assert.equal(completion.failureText(new Error("boom")), "boom");
  // A bare string carries no server reason for `errMsg` to preserve, so the
  // generic sentence stands — there is nothing verbatim worth keeping.
  assert.equal(completion.failureText("boom"), "Something went wrong.");
});

test("mapRefusal reads the router's {detail, code} shape", () => {
  const refusal = completion.mapRefusal(REFUSAL_BODY);
  assert.equal(refusal.code, "illegal_transition");
  assert.equal(refusal.subject, "sc-abc123");
  assert.equal(refusal.findings, null);
});

test("mapRefusal carries every finding on a contract_invalid", () => {
  const refusal = completion.mapRefusal(CONTRACT_INVALID_BODY);
  assert.equal(refusal.code, "contract_invalid");
  assert.equal(refusal.findings.length, 2);
  assert.equal(refusal.findings[0].code, "unknown_check");
  assert.equal(refusal.findings[1].severity, "warning");
});

test("mapRefusal still yields the sentence for a body that is plain text", () => {
  const refusal = completion.mapRefusal("Not Found");
  assert.equal(refusal.detail, "Not Found");
  assert.equal(refusal.code, null);
});

test("isEngineRefusal tells the router's own code from a Starlette route 404", () => {
  assert.equal(completion.isEngineRefusal(REFUSAL_BODY), true);
  assert.equal(completion.isEngineRefusal({ detail: "Not Found" }), false);
});

/* ── The vocabulary: waived is not passed, not_run is not failed ────────── */

test("PASSED_TONES holds only what may look like a success", () => {
  assert.ok(completion.PASSED_TONES.includes("passed"));
  assert.ok(completion.PASSED_TONES.includes("current"));
  assert.ok(completion.PASSED_TONES.includes("met"));
  assert.ok(completion.PASSED_TONES.includes("complete"));
  // A waived gate, a stale pass and a check that never ran are decisions to be
  // shown, not green ticks to be softened.
  assert.ok(!completion.PASSED_TONES.includes("waived"));
  assert.ok(!completion.PASSED_TONES.includes("stale"));
  assert.ok(!completion.PASSED_TONES.includes("not_run"));
  assert.ok(!completion.PASSED_TONES.includes("blocked"));
  assert.ok(!completion.PASSED_TONES.includes("unknown"));
});

test("VERIFIED_TONES holds exactly the one task state that means the gates passed", () => {
  assert.deepEqual([...completion.VERIFIED_TONES], ["verified"]);
});

test("freshnessText: a gate with no evidence has never been checked", () => {
  const text = completion.freshnessText(completion.mapGate(GATE_WAIVED));
  assert.match(text, /never been checked/);
  assert.doesNotMatch(text, /passed|current/);
});

test("freshnessText: a stale pass is named as unable to answer for the present", () => {
  const text = completion.freshnessText(completion.mapGate(GATE_STALE));
  assert.match(text, /stale/);
  assert.match(text, /cannot answer/);
});

test("freshnessText: an unreported freshness is a third state, not a pass", () => {
  const gate = completion.mapGate({
    ...GATE_PASSED,
    latest_evidence: { evidence_id: "ev-1", freshness: null },
  });
  assert.equal(completion.freshnessText(gate), "freshness not reported");
});

test("freshnessText: a current pass says only what it is", () => {
  assert.equal(
    completion.freshnessText(completion.mapGate(GATE_PASSED)),
    "freshness: current",
  );
});

test("gatesPassedText keeps a missing count distinct from a measured zero", () => {
  assert.equal(
    completion.gatesPassedText(completion.mapScopeSummary(SCOPE_A)),
    "2",
  );
  assert.equal(
    completion.gatesPassedText(
      completion.mapScopeSummary({ scope_id: "sc", status: "planning" }),
    ),
    "not reported",
  );
  assert.equal(
    completion.gatesPassedText(
      completion.mapScopeSummary({
        scope_id: "sc",
        status: "planning",
        gates_passed: 0,
      }),
    ),
    "0",
  );
});

/* ── The full projection round-trips ────────────────────────────────────── */

test("mapScopeDetail keeps every section and its own absence", () => {
  const detail = completion.mapScopeDetail({
    scope: SCOPE_A,
    contract: {
      contract_id: "ct-sc-abc123",
      valid: true,
      findings: [],
      requirements: [],
    },
    tasks: [TASK_A],
    gates: [GATE_PASSED, GATE_WAIVED, GATE_STALE],
    dispatches: [
      {
        dispatch_id: "dp-1",
        task_id: "task-1",
        state: "returned",
        verified: false,
      },
    ],
    ownership: [
      { task_id: "task-1", owner: "agent-1", paths: ["src/parser.py"] },
    ],
    budget: { max_tasks: 40, gate_runs_used: 6, exhausted: false },
    final_report: null,
    scheduling: { dispatchable: ["task-2"] },
    mode: "enforce",
    enforcing: true,
    policy_id: "completion.v1",
  });

  assert.equal(detail.scope.scope_id, "sc-abc123");
  assert.equal(detail.tasks.length, 1);
  assert.equal(detail.tasks[0].state, "returned");
  assert.equal(detail.gates.length, 3);
  assert.equal(detail.dispatches[0].state, "returned");
  assert.equal(detail.ownership[0].paths[0], "src/parser.py");
  assert.equal(detail.budget.gate_runs_used, 6);
  assert.equal(detail.final_report, null);
  assert.equal(detail.enforcing, true);
});

test("mapScopeDetail: an absent section is null, never a hollow object", () => {
  const detail = completion.mapScopeDetail({ scope: SCOPE_A });
  assert.equal(detail.tasks, null);
  assert.equal(detail.gates, null);
  assert.equal(detail.dispatches, null);
  assert.equal(detail.budget, null);
  assert.equal(detail.scheduling, null);
});

test("mapFinalReport keeps enforced:false so observe mode never claims a completion", () => {
  const report = completion.mapFinalReport({
    status: "verifying",
    enforced: false,
    complete: false,
    blockers: [
      {
        code: "gate_stale",
        message: "gate-3 is stale",
        subject_id: "gate-3",
        kind: "unverified",
      },
    ],
    checks_ran: 3,
    checks_not_run: 1,
    summary: "[observe] would not complete: 1 blocker",
    attempt: 1,
  });
  assert.equal(report.enforced, false);
  assert.equal(report.complete, false);
  assert.equal(report.blockers[0].code, "gate_stale");
  assert.equal(report.checks_not_run, 1);
});

test("mapFinalReport: an absent complete flag is not reported, not false", () => {
  const report = completion.mapFinalReport({ status: "verifying" });
  assert.equal(report.complete, null);
  assert.equal(report.enforced, null);
  assert.equal(report.blockers, null);
});

test("mapFinalReport keeps the reconciliation's three separate absence lists", () => {
  const report = completion.mapFinalReport({
    complete: false,
    reconciliation: {
      basis: "heuristic_clause_match",
      clauses_seen: 4,
      unrepresented: ["also ship docs"],
      unmet: [],
      unverifiable: [],
      clean: false,
    },
  });
  assert.deepEqual(report.reconciliation.unrepresented, ["also ship docs"]);
  assert.deepEqual(report.reconciliation.unmet, []);
  assert.equal(report.reconciliation.basis, "heuristic_clause_match");
});

test("mapVerifyResult keeps the fields the verification result actually carries", () => {
  const result = completion.mapVerifyResult({
    task_id: "task-1",
    state: "verified",
    passed: true,
    ran: 3,
  });
  assert.equal(result.task_id, "task-1");
  assert.equal(result.passed, true);
  assert.equal(result.ran, 3);
});

test("mapOwnership keeps a lapsed expiry readable rather than dropping the claim", () => {
  const ownership = completion.mapOwnership({
    task_id: "t-1",
    owner: "agent-1",
    paths: [],
    claimed_at: "2026-10-10T09:00:00+00:00",
    expires_at: null,
  });
  assert.equal(ownership.expires_at, null);
  assert.equal(ownership.owner, "agent-1");
});
