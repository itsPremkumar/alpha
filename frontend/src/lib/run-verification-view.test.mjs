// run-verification-view.test.mjs — a run's verification posture, as the server
// actually reported it.
//
// Found on 2026-10-06. A completed dynamic run returned `acceptance_passed: false`
// with the reason "digest projection completed graph mechanics only; no domain
// task was executed", `execution_label: "local_digest_projection"`, a
// `verification` block, and one `node_verification` event whose payload named a
// check (`pytest -q`) that was NOT RUN. The panel read none of it:
// `node_verification`, `acceptance_reason`, `registered_verifiers`,
// `executor_bound` and `declared_nodes` each had zero occurrences in the frontend.
//
// The fixtures below are the real values from
// `backend/scripts/probe_dynamic_run_report.py`. Inventing tidy ones would let a
// derivation pass here and fail on the payload.

import { readFileSync } from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const toDataUrl = (s) => `data:text/javascript;charset=utf-8,${encodeURIComponent(s)}`;

const code = ts.transpileModule(read("./run-verification-view.ts"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const {
  nodeVerificationViews,
  runAcceptanceView,
  verificationPostureView,
  runVerificationView,
  measuredCount,
} = await import(toDataUrl(code));

// ---------------------------------------------------------------------------
// The real payload
// ---------------------------------------------------------------------------

/** `metadata` exactly as the digest-projection run returned it. */
const realMetadata = () => ({
  acceptance_passed: false,
  acceptance_reason: "digest projection completed graph mechanics only; no domain task was executed",
  execution_label: "local_digest_projection",
  node_runner_bound: true,
  compensation_runner_bound: false,
  graph_version: 1,
  execution_mode: "normal",
  compensation: [],
  verification: {
    registered_verifiers: 0,
    executor_bound: false,
    declared_nodes: ["task_05_dynamic_implementation"],
    outcomes_event_type: "node_verification",
  },
});

/** The real `node_verification` event. */
const realVerificationEvent = () => ({
  event_type: "node_verification",
  payload: {
    node_id: "task_05_dynamic_implementation",
    passed: false,
    status: "not_run",
    command: "pytest -q",
    reason: "the command was not run: no verification executor is bound in this host",
    resolved_via: "executor",
    evidence: null,
    duration_ms: 0.0,
  },
});

/** A run that journalled only ordinary progress events. */
const plainEvents = () => [
  { event_type: "node_started" },
  { event_type: "node_completed" },
  { event_type: "workflow_completed" },
];

// ---------------------------------------------------------------------------
// The load-bearing distinction: not_run is not a failure
// ---------------------------------------------------------------------------

test("a declared-but-unrun check is 'not run', never 'failed'", () => {
  // The server sends `passed: false` for a check it never executed, because
  // nothing passed. Dressing that as a failure would report the host's missing
  // executor as the work's inadequacy — the opposite claim.
  const [v] = nodeVerificationViews([realVerificationEvent()]);
  assert.equal(v.status, "not_run");
  assert.equal(v.ran, false);
  assert.equal(v.passed, false);
  assert.match(v.sentence, /^not run —/);
  assert.doesNotMatch(v.sentence, /failed/);
});

test("the server's own reason is quoted, not paraphrased", () => {
  const [v] = nodeVerificationViews([realVerificationEvent()]);
  assert.match(v.sentence, /no verification executor is bound in this host/);
});

test("an unrun check with no reason says the reason is missing", () => {
  // Claiming "not run" while inventing why would be the same defect one step in.
  const [v] = nodeVerificationViews([
    { event_type: "node_verification", payload: { node_id: "n", status: "not_run", passed: false } },
  ]);
  assert.match(v.sentence, /not run —/);
  assert.match(v.sentence, /no reason/);
});

test("a check that genuinely failed reads as failed", () => {
  const [v] = nodeVerificationViews([
    {
      event_type: "node_verification",
      payload: { node_id: "n", status: "failed", passed: false, reason: "assertion failed on line 12" },
    },
  ]);
  assert.equal(v.ran, true);
  assert.equal(v.tone, "red");
  assert.match(v.sentence, /^failed — assertion failed on line 12/);
  assert.doesNotMatch(v.sentence, /not run/);
});

test("a check that ran and passed reads as passed", () => {
  const [v] = nodeVerificationViews([
    { event_type: "node_verification", payload: { node_id: "n", status: "completed", passed: true } },
  ]);
  assert.equal(v.ran, true);
  assert.equal(v.tone, "green");
  assert.equal(v.sentence, "passed");
});

test("a missing status is reported as unreported, never as a pass", () => {
  const [v] = nodeVerificationViews([
    { event_type: "node_verification", payload: { node_id: "n", passed: true } },
  ]);
  assert.equal(v.status, null);
  assert.equal(v.tone, "gray");
  assert.match(v.sentence, /outcome not reported/);
  assert.doesNotMatch(v.sentence, /^passed$/);
});

test("a dropped passed flag with a real status is neither pass nor fail", () => {
  const [v] = nodeVerificationViews([
    { event_type: "node_verification", payload: { node_id: "n", status: "completed" } },
  ]);
  assert.equal(v.ran, true);
  assert.equal(v.passed, null);
  assert.equal(v.tone, "gray");
  assert.match(v.sentence, /no pass\/fail outcome/);
});

test("the node id is taken from the payload, falling back to the task id", () => {
  assert.equal(nodeVerificationViews([realVerificationEvent()])[0].nodeId, "task_05_dynamic_implementation");
  assert.equal(
    nodeVerificationViews([{ event_type: "node_verification", task_id: "t-9", payload: {} }])[0].nodeId,
    "t-9",
  );
});

test("only node_verification events are returned", () => {
  assert.equal(nodeVerificationViews(plainEvents()).length, 0);
  assert.equal(nodeVerificationViews([realVerificationEvent(), ...plainEvents()]).length, 1);
});

// ---------------------------------------------------------------------------
// Acceptance posture
// ---------------------------------------------------------------------------

test("the real digest run reports its own reason verbatim", () => {
  const a = runAcceptanceView(realMetadata());
  assert.equal(a.acceptancePassed, false);
  assert.match(a.sentence, /digest projection completed graph mechanics only/);
  assert.equal(a.tone, "amber");
  assert.doesNotMatch(a.sentence, /failed|inadequate/i);
});

test("an absent acceptance flag is unknown, never a limitation", () => {
  // The mirror of the original defect: claiming a limitation the server never
  // stated is as wrong as claiming a success it never earned.
  const a = runAcceptanceView({});
  assert.equal(a.acceptancePassed, null);
  assert.equal(a.tone, "gray");
  assert.match(a.sentence, /not reported by the server/);
  assert.doesNotMatch(a.sentence, /mechanics|digest/);
});

test("a true acceptance flag says so and is not downgraded", () => {
  const a = runAcceptanceView({ acceptance_passed: true, acceptance_reason: "all required leaves satisfied" });
  assert.equal(a.tone, "green");
  assert.equal(a.sentence, "all required leaves satisfied");
});

test("a false flag with no reason does not invent one", () => {
  const a = runAcceptanceView({ acceptance_passed: false });
  assert.equal(a.tone, "amber");
  assert.match(a.sentence, /no reason/);
  assert.doesNotMatch(a.sentence, /digest projection completed/);
});

// ---------------------------------------------------------------------------
// Verification posture
// ---------------------------------------------------------------------------

test("the real posture names the missing executor and the missing verifier", () => {
  const p = verificationPostureView(realMetadata());
  assert.equal(p.executorBound, false);
  assert.equal(p.registeredVerifiers, 0);
  assert.deepEqual(p.declaredNodes, ["task_05_dynamic_implementation"]);
  assert.match(p.sentence, /no verification executor is bound/);
  assert.match(p.sentence, /no verifier is registered/);
});

test("a zero verifier count is a measurement and survives", () => {
  // `0` and "not reported" lead to opposite decisions. `measuredCount` keeps
  // them apart, and this is the regression that would collapse them.
  assert.equal(measuredCount(0), 0);
  assert.equal(verificationPostureView(realMetadata()).registeredVerifiers, 0);
  assert.equal(verificationPostureView({ verification: { executor_bound: false } }).registeredVerifiers, null);
});

test("an absent verification block is not reported, never 'unavailable'", () => {
  const p = verificationPostureView({});
  assert.equal(p.executorBound, null);
  assert.match(p.sentence, /not reported by the server/);
  assert.doesNotMatch(p.sentence, /no verification executor is bound/);
});

test("a bound executor with registered verifiers reads as ready, not as missing", () => {
  const p = verificationPostureView({
    verification: { registered_verifiers: 2, executor_bound: true, declared_nodes: ["a"] },
  });
  assert.match(p.sentence, /2 verifier\(s\) registered and a verification executor is bound/);
});

test("registered verifiers with no executor still names the missing half", () => {
  const p = verificationPostureView({
    verification: { registered_verifiers: 3, executor_bound: false },
  });
  assert.match(p.sentence, /3 verifier\(s\) registered, but no verification executor is bound/);
});

// ---------------------------------------------------------------------------
// The whole reading
// ---------------------------------------------------------------------------

test("the real run yields one unrun check and flags it", () => {
  const v = runVerificationView(realMetadata(), [realVerificationEvent(), ...plainEvents()]);
  assert.equal(v.nodes.length, 1);
  assert.equal(v.hasUnrun, true);
  assert.equal(v.declaredNote, null);
});

test("a run with no declaration says nothing was checked", () => {
  // The common case. It must not read as "everything passed", which is the
  // absent-as-success shape.
  const v = runVerificationView(realMetadata(), plainEvents());
  assert.equal(v.nodes.length, 0);
  assert.equal(v.hasUnrun, false);
  assert.match(v.declaredNote, /no node declared a verification/);
  assert.doesNotMatch(v.declaredNote, /passed/i);
});

test("a run whose checks all passed has no unrun flag", () => {
  const v = runVerificationView(realMetadata(), [
    { event_type: "node_verification", payload: { node_id: "a", status: "completed", passed: true } },
  ]);
  assert.equal(v.hasUnrun, false);
  assert.equal(v.declaredNote, null);
});

test("an unreadable event list degrades instead of throwing", () => {
  for (const bad of [undefined, null, "nope", 7, {}]) {
    const v = runVerificationView(realMetadata(), bad);
    assert.equal(v.nodes.length, 0);
    assert.ok(v.declaredNote);
  }
});

test("a malformed metadata payload degrades instead of throwing", () => {
  for (const bad of [undefined, null, "nope", 7, []]) {
    const v = runVerificationView(bad, [realVerificationEvent()]);
    assert.equal(v.acceptance.acceptancePassed, null);
    assert.equal(v.posture.executorBound, null);
  }
});

test("a verification event with no payload still yields a row", () => {
  const [v] = nodeVerificationViews([{ event_type: "node_verification" }]);
  assert.equal(v.status, null);
  assert.equal(v.nodeId, null);
  assert.match(v.sentence, /not reported/);
});