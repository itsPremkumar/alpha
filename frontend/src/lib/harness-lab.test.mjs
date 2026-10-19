import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

/*
 * Harness Lab client contract (`lib/harness-lab.ts`).
 *
 * Three things are pinned here, and all three are honesty rather than shape:
 *
 *  1. **Absent is `null`, never `0`, `""` or `false`, and a missing list is
 *     `null`, never `[]`.** The router refuses to answer an unreadable store
 *     with a body of zeros, so a mapper here that reached for `?? 0` would undo
 *     that from the other side and hand the view "nothing was recorded" when
 *     the truth is "we did not look".
 *  2. **The client calls the routes that exist.** Each recorded call is
 *     compared against the exact path and verb, including the query windows the
 *     router declares, so a renamed or repointed route fails here rather than
 *     at runtime.
 *  3. **Local refusals happen before a request.** A text out of bounds or an
 *     approval kind outside the privileged-kind vocabulary is refused locally
 *     with the bound named, and the recorded-call list proves nothing went out.
 */

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const toDataUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
const transpile = (source, extra = {}) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, ...extra },
  }).outputText;

/* ── Recorded transport ────────────────────────────────────────────────── */

const calls = [];
const responses = {};

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

const source = read("./harness-lab.ts");
const harnessUrl = toDataUrl(transpile(source).replace(/from "\.\/http"/g, `from "${httpStub}"`));

globalThis.__calls = calls;
globalThis.__responses = responses;

const harness = await import(harnessUrl);

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

const STATUS = {
  enabled: false,
  enabled_note: "Harness improvement is disabled (config.yaml -> harness_improvement.enabled: false).",
  mode: "sequential",
  settings: { mode: "sequential" },
  ceilings: { max_model_calls: 40 },
  promotion_policy: { automatic_global_promotion: false },
  config_hash: "hash_1",
  harness_capability: {
    capability: "harness_improvement_probe_execution",
    state: "absent",
    executes: false,
    missing_wiring: [{ wiring_point: "harness_improvement.executor", why_absent: "the Gateway injects no executor" }],
  },
  durability: "single-process JSON/JSONL",
  store: {
    root: "/tmp/harness_improvement",
    readable: true,
    counts: { bundles: 1, runs: 2, experiences: 3 },
    unreadable: [],
    reason: null,
  },
  active_bundle: {
    bundle_id: "b1",
    previous_bundle_id: null,
    activated_at: "2026-10-01T10:00:00+00:00",
    actor: "admin",
    reason: "operator activation",
    manifest_error: "malformed bundle: bad json",
  },
};

const RUN_ROW = {
  run_id: "run_1",
  created_at: "2026-10-01T09:00:00+00:00",
  updated_at: "2026-10-01T09:05:00+00:00",
  stage: "blocked",
  mode: "sequential",
  created_by: "admin",
  task_fingerprint: "fp_1",
  task_type: "general",
  outcome: "improve retrieval",
  base_bundle_id: "",
  successor_bundle_id: null,
  blocked_reason: "no executor is wired",
  error: null,
  cancel_requested: false,
  resumable: true,
  counts: { checkpoints: 1, proposals: 0, validations: 0, accepted_validations: 0, probes: 0, contrasts: 0, experiences: 0 },
  notes: ["parked naming the missing collaborator"],
};

const RUN_RECORD = {
  run_id: "run_1",
  stage: "blocked",
  mode: "sequential",
  created_at: "2026-10-01T09:00:00+00:00",
  updated_at: "2026-10-01T09:05:00+00:00",
  created_by: "admin",
  base_bundle_id: "",
  successor_bundle_id: null,
  task: { task: "make retrieval better", outcome: "fewer retries", task_type: "general", task_fingerprint: "fp_1" },
  blocked_reason: "no executor is wired",
  cancel_reason: null,
  cancel_requested: false,
  error: null,
  notes: ["parked"],
  history: [{ event: "stage_blocked", stage: "blocked", at: "2026-10-01T09:05:00+00:00", reason: "no executor" }],
  checkpoints: { snapshotting: { stage_key: "k1" } },
  proposals: [{}],
  validations: [],
  probe_outcomes: [],
  contrasts: [],
  experiences: [],
  budget_state: { spent_model_calls: 0 },
  composition: {},
  deliverable: {},
  compatibility: { alpha_runtime_version: "1.0", tool_schema_version: "2", policy_version: "3" },
};

const JOURNAL_LINE = {
  event: "stage_blocked",
  at: "2026-10-01T09:05:00+00:00",
  stage: "blocked",
  reason: "no executor is wired",
  run_id: "run_1",
  candidate_id: "cand_1",
};

const BUNDLE_RAW = {
  bundle_id: "b1",
  parent_bundle_id: null,
  created_at: "2026-10-01T09:01:00+00:00",
  created_by: "engine",
  agent_profile: "default",
  promotion_state: "draft",
  component_count: 1,
  components: [{ component_id: "c1", kind: "prompt", sha256: "aa", size_bytes: 12 }],
  compatibility: { alpha_runtime_version: "1.0", tool_schema_version: "2", policy_version: "3" },
  provenance: { source_run_id: "run_1", source_task_type: "general" },
};

const PROMOTIONS = {
  events: [
    {
      event: "approved",
      bundle_id: "b1",
      previous_bundle_id: null,
      actor: "admin",
      reason: "operator approval recorded",
      at: "2026-10-01T10:00:00+00:00",
      gates: { decision: {}, granted: ["scripts"] },
    },
  ],
  unreadable: [],
  pointer: { bundle_id: "b1", previous_bundle_id: "b0", activated_at: "2026-10-01T10:01:00+00:00", actor: "admin", reason: "operator activation" },
  pointer_error: null,
  ladder: { draft: "statically_validated", statically_validated: "probed" },
  durability: "single-process JSON/JSONL",
};

const BUDGETS = {
  ceilings: { max_model_calls: 40 },
  ledger: [
    {
      run_id: "run_1",
      stage: "baseline_running",
      model: "m1",
      input_tokens: 10,
      output_tokens: 4,
      cost_usd: null,
      elapsed_ms: 1200,
      recorded_at: "2026-10-01T09:02:00+00:00",
      ceilings: { max_model_calls: 40 },
      model_calls: 1,
    },
  ],
  unreadable: [],
  complete: true,
  measured_model_calls: 1,
  note: "an unreported dimension stays null rather than reading as zero",
  durability: "single-process JSON/JSONL",
};

const EXPERIENCE_RAW = {
  experience_id: "e1",
  title: "tool guidance beats prompt text",
  lesson: "name the tool in the reminder rather than adding prose",
  finding: "benefit",
  scope: "task_type",
  task_type: "general",
  status: "active",
  domain_tags: ["retrieval"],
  component_kinds: ["prompt"],
  run_ids: ["run_1"],
  benefit: "fewer retries",
  counter_evidence: null,
  created_at: "2026-10-01T11:00:00+00:00",
  last_reviewed_at: null,
  review_condition: null,
  expires_at: null,
  superseded_by: null,
  staleness: [],
};

/* ── Routes and verbs ──────────────────────────────────────────────────── */

test("the approval-kind vocabulary is the server's two privileged kinds", () => {
  assert.deepEqual([...harness.HARNESS_APPROVAL_KINDS], ["scripts", "middleware"]);
});

test("status reads /harness/status", async () => {
  resetCalls();
  record("GET /harness/status", { body: STATUS });
  const mapped = await harness.fetchHarnessStatus();
  assert.equal(lastCall().path, "/harness/status");
  assert.equal(lastCall().method, "GET");
  assert.equal(lastCall().body, undefined);
  assert.equal(mapped.enabled, false);
});

test("the unfiltered run list carries the server's default limit", async () => {
  resetCalls();
  record("GET /harness/runs?limit=50", { body: { runs: [RUN_ROW], unreadable: [], complete: true, durability: "d" } });
  const mapped = await harness.fetchHarnessRuns();
  assert.equal(lastCall().path, "/harness/runs?limit=50");
  assert.equal(lastCall().method, "GET");
  assert.equal(mapped.runs.length, 1);
});

test("a garbage run limit falls back to the server default; a bound is clamped onto the route's window", () => {
  assert.equal(harness.clampRunLimit(undefined), 50);
  assert.equal(harness.clampRunLimit(null), 50);
  assert.equal(harness.clampRunLimit(Number.NaN), 50);
  assert.equal(harness.clampRunLimit(0), 1, "ge=1 is the route's floor");
  assert.equal(harness.clampRunLimit(501), 500, "le=500 is the route's ceiling");
  assert.equal(harness.clampEventLimit(undefined), 500);
  assert.equal(harness.clampEventLimit(5000), 2000);
  assert.equal(harness.clampExperienceLimit(undefined), 100);
  assert.equal(harness.clampBudgetLimit(undefined), 200);
  assert.equal(harness.clampBudgetLimit(2001), 2000);
});

test("start POSTs the task contract, shaping only the fields that were provided", async () => {
  resetCalls();
  record("POST /harness/runs", { body: { run: { run_id: "run_1", stage: "blocked" }, resumable: true, next_stage: "baseline_running", durability: "d" } });
  const receipt = await harness.startHarnessRun({
    task: "make retrieval better",
    outcome: "fewer retries",
    domain_tags: ["retrieval"],
    task_type: "general",
  });
  assert.equal(lastCall().path, "/harness/runs");
  assert.equal(lastCall().method, "POST");
  assert.deepEqual(lastCall().body, {
    task: "make retrieval better",
    outcome: "fewer retries",
    domain_tags: ["retrieval"],
    task_type: "general",
  });
  assert.equal(receipt.run_id, "run_1");
  assert.equal(receipt.resumable, true);
});

test("a caller cannot choose the task fingerprint the server salts", async () => {
  resetCalls();
  record("POST /harness/runs", { body: { run: { run_id: "run_1", stage: "blocked" } } });
  await harness.startHarnessRun({ task: "t", outcome: "o", task_fingerprint: "chosen" });
  assert.equal("task_fingerprint" in lastCall().body, false, "the fingerprint is server-computed; a caller-supplied one must not travel");
});

test("run detail reads one run by id, percent-encoded", async () => {
  resetCalls();
  record("GET /harness/runs/run_1", { body: { run: RUN_RECORD, events: [JOURNAL_LINE], unreadable_event_lines: [], resumable: true, next_stage: "baseline_running", durability: "d" } });
  const mapped = await harness.fetchHarnessRun("run_1");
  assert.equal(lastCall().path, "/harness/runs/run_1");
  assert.equal(lastCall().method, "GET");
  assert.equal(mapped.run.run_id, "run_1");
  assert.equal(mapped.events[0].extra.candidate_id, "cand_1");

  record("GET /harness/runs/weird%2Fid", { body: { run: null, events: [], unreadable_event_lines: [], resumable: null, next_stage: null, durability: null } });
  await harness.fetchHarnessRun("weird/id");
  assert.equal(lastCall().path, "/harness/runs/weird%2Fid");
});

test("the events route carries its own limit window", async () => {
  resetCalls();
  record("GET /harness/runs/run_1/events?limit=500", { body: { run_id: "run_1", run_record_present: true, stage: "blocked", events: [JOURNAL_LINE], unreadable: [], limit: 500 } });
  const mapped = await harness.fetchHarnessRunEvents("run_1");
  assert.equal(lastCall().path, "/harness/runs/run_1/events?limit=500");
  assert.equal(mapped.run_record_present, true);
  assert.equal(mapped.limit, 500);
});

test("resume POSTs an empty body and maps the receipt", async () => {
  resetCalls();
  record("POST /harness/runs/run_1/resume", { body: { run: { run_id: "run_1", stage: "blocked" }, resumable: true, next_stage: "baseline_running", durability: "d" } });
  const receipt = await harness.resumeHarnessRun("run_1");
  assert.equal(lastCall().path, "/harness/runs/run_1/resume");
  assert.equal(lastCall().method, "POST");
  assert.deepEqual(lastCall().body, {});
  assert.equal(receipt.next_stage, "baseline_running");
});

test("cancel POSTs {} when no reason is given and {reason} when one is", async () => {
  resetCalls();
  record("POST /harness/runs/run_1/cancel", { body: { run: { run_id: "run_1", stage: "blocked" }, cancel_requested: true, applied_at_next_boundary: true, note: "durable request", durability: "d" } });
  const receipt = await harness.cancelHarnessRun("run_1");
  assert.equal(lastCall().path, "/harness/runs/run_1/cancel");
  assert.equal(lastCall().method, "POST");
  assert.deepEqual(lastCall().body, {}, "an empty reason must not override the server's own default");
  assert.equal(receipt.cancel_requested, true);
  assert.equal(receipt.applied_at_next_boundary, true);

  resetCalls();
  record("POST /harness/runs/run_1/cancel", { body: { run: { run_id: "run_1", stage: "cancelled" }, cancel_requested: true, applied_at_next_boundary: false, note: "", durability: "d" } });
  await harness.cancelHarnessRun("run_1", "operator is stopping the cycle");
  assert.deepEqual(lastCall().body, { reason: "operator is stopping the cycle" });
});

test("bundles list and one bundle read their paths", async () => {
  resetCalls();
  record("GET /harness/bundles", { body: { bundles: [BUNDLE_RAW], unreadable: [], complete: true, active_bundle: null } });
  const list = await harness.fetchHarnessBundles();
  assert.equal(lastCall().path, "/harness/bundles");
  assert.equal(list.bundles[0].source_run_id, "run_1");
  assert.equal(list.active_bundle, null, "no pointer is null, not an empty pointer");

  resetCalls();
  record("GET /harness/bundles/b1", {
    body: {
      bundle: BUNDLE_RAW,
      components: [
        { component_id: "c1", kind: "prompt", sha256: "aa", size_bytes: 12, content: "hello", missing: false },
        { component_id: "c2", kind: "script", sha256: "cc", size_bytes: 5, content: null, missing: true },
      ],
      redactions: ["c2: credentials"],
      note: "a missing component body means the store does not hold it",
    },
  });
  const detail = await harness.fetchHarnessBundle("b1");
  assert.equal(lastCall().path, "/harness/bundles/b1");
  assert.equal(detail.components[1].missing, true);
  assert.equal(detail.components[1].content, null);
  assert.deepEqual(detail.redactions, ["c2: credentials"]);
});

test("the diff path carries base and include_contents only when set", async () => {
  resetCalls();
  record("GET /harness/bundles/b1/diff", { body: { diff: { base_bundle_id: "b0", candidate_bundle_id: "b1", added: [], removed: [], modified: [], unchanged: [] }, contents: { added: {}, modified: {} }, redactions: [], note: "" } });
  await harness.fetchHarnessBundleDiff("b1");
  assert.equal(lastCall().path, "/harness/bundles/b1/diff", "no options means the parent and bodies included, per the route's own defaults");

  resetCalls();
  record("GET /harness/bundles/b1/diff?base=b0&include_contents=false", { body: { diff: null, contents: null, redactions: [], note: "" } });
  await harness.fetchHarnessBundleDiff("b1", { base: "b0", includeContents: false });
  assert.equal(lastCall().path, "/harness/bundles/b1/diff?base=b0&include_contents=false");
});

test("an empty diff is still a valid answer, not a failure", async () => {
  resetCalls();
  record("GET /harness/bundles/b1/diff", {
    body: {
      diff: { base_bundle_id: "b0", candidate_bundle_id: "b1", added: [], removed: [], modified: [], unchanged: ["c1"] },
      contents: { added: {}, modified: {} },
      redactions: [],
      note: "an empty diff with retained_baseline composition is a valid outcome",
    },
  });
  const detail = await harness.fetchHarnessBundleDiff("b1");
  assert.deepEqual(detail.diff.added, []);
  assert.deepEqual(detail.diff.unchanged, ["c1"]);
});

test("experience filters reach the query string and empties are omitted", async () => {
  resetCalls();
  record("GET /harness/experiences?limit=100", { body: { experiences: [], unreadable: [], complete: true, total: 0, limit: 100 } });
  await harness.fetchHarnessExperiences();
  assert.equal(lastCall().path, "/harness/experiences?limit=100");

  resetCalls();
  const path = harness.buildExperiencesPath({ task_type: "general", scope: "", status: "active", domain_tag: ["retrieval", ""], limit: 7 });
  assert.ok(!path.includes("scope="), "an empty filter is omitted, never sent as a bare key");
  const params = new URLSearchParams(path.slice(path.indexOf("?") + 1));
  assert.equal(params.get("task_type"), "general");
  assert.equal(params.get("status"), "active");
  assert.deepEqual(params.getAll("domain_tag"), ["retrieval"]);
  assert.equal(params.get("limit"), "7");
});

test("experience detail and promotions read their paths", async () => {
  resetCalls();
  record("GET /harness/experiences/e1", { body: { metadata: EXPERIENCE_RAW, markdown: "# lesson", staleness: ["expires_at passed"], redactions: [] } });
  const detail = await harness.fetchHarnessExperience("e1");
  assert.equal(lastCall().path, "/harness/experiences/e1");
  assert.equal(detail.experience.experience_id, "e1");
  assert.deepEqual(detail.staleness, ["expires_at passed"]);

  resetCalls();
  record("GET /harness/promotions", { body: PROMOTIONS });
  const promotions = await harness.fetchHarnessPromotions();
  assert.equal(lastCall().path, "/harness/promotions");
  assert.deepEqual(promotions.ladder, { draft: "statically_validated", statically_validated: "probed" });
  assert.deepEqual(promotions.events[0].granted, ["scripts"], "the granted kinds are read from the event's gates block");
});

test("approve POSTs {reason, approvals} and keeps its receipt", async () => {
  resetCalls();
  record("POST /harness/candidates/b1/approve", { body: { approved: true, bundle_id: "b1", recorded: {}, gates: {}, evidence: {}, note: "approval is recorded, not applied" } });
  const receipt = await harness.approveHarnessCandidate("b1", { reason: "looks good", approvals: ["scripts"] });
  assert.equal(lastCall().path, "/harness/candidates/b1/approve");
  assert.equal(lastCall().method, "POST");
  assert.deepEqual(lastCall().body, { reason: "looks good", approvals: ["scripts"] });
  assert.equal(receipt.approved, true);
  assert.equal(receipt.note, "approval is recorded, not applied");

  resetCalls();
  record("POST /harness/candidates/b1/approve", { body: { approved: true, bundle_id: "b1", note: "" } });
  await harness.approveHarnessCandidate("b1");
  assert.deepEqual(lastCall().body, { reason: "", approvals: [] }, "the route's own defaults travel explicitly");
});

test("reject, activate and rollback POST their routes", async () => {
  resetCalls();
  record("POST /harness/candidates/b1/reject", { body: { rejected: true, bundle_id: "b1", promotion_state: "rejected" } });
  const rejection = await harness.rejectHarnessCandidate("b1", "not supported by the evidence");
  assert.equal(lastCall().path, "/harness/candidates/b1/reject");
  assert.deepEqual(lastCall().body, { reason: "not supported by the evidence" });
  assert.equal(rejection.promotion_state, "rejected");

  resetCalls();
  record("POST /harness/active/b1/activate", { body: { activated: true, bundle_id: "b1", previous_bundle_id: "b0", pointer: {}, note: "rollback is one step" } });
  const activation = await harness.activateHarnessBundle("b1", "operator activation");
  assert.equal(lastCall().path, "/harness/active/b1/activate");
  assert.deepEqual(lastCall().body, { reason: "operator activation" });
  assert.equal(activation.previous_bundle_id, "b0");

  resetCalls();
  record("POST /harness/active/b1/rollback", { body: { rolled_back: true, restored_bundle_id: "b0", from_bundle_id: "b1", pointer: {}, note: "prior known-good" } });
  const rollback = await harness.rollbackHarnessBundle("b1");
  assert.equal(lastCall().path, "/harness/active/b1/rollback");
  assert.deepEqual(lastCall().body, {}, "an empty reason must not override the server's own default");
  assert.equal(rollback.restored_bundle_id, "b0");
});

test("budgets carry the ledger window and an optional run filter", async () => {
  resetCalls();
  record("GET /harness/budgets?limit=200", { body: BUDGETS });
  const mapped = await harness.fetchHarnessBudgets();
  assert.equal(lastCall().path, "/harness/budgets?limit=200");
  assert.equal(mapped.measured_model_calls, 1);
  assert.equal(mapped.ledger[0].cost_usd, null, "an unreported cost stays null, never 0");

  resetCalls();
  record("GET /harness/budgets?run_id=run_1&limit=5", { body: BUDGETS });
  await harness.fetchHarnessBudgets({ run_id: "run_1", limit: 5 });
  assert.equal(lastCall().path, "/harness/budgets?run_id=run_1&limit=5");
});

/* ── Mapping: absent is null, never zero and never [] ──────────────────── */

test("a run row maps every reported field verbatim", () => {
  const mapped = harness.mapRunRow(RUN_ROW);
  assert.equal(mapped.run_id, "run_1");
  assert.equal(mapped.stage, "blocked");
  assert.equal(mapped.resumable, true);
  assert.equal(mapped.blocked_reason, "no executor is wired");
  assert.equal(mapped.cancel_requested, false);
  assert.deepEqual(mapped.counts, RUN_ROW.counts);
  assert.deepEqual(mapped.notes, RUN_ROW.notes);
});

test("an absent field on a run row is null and an absent counts block is null", () => {
  const mapped = harness.mapRunRow({ run_id: "run_x", stage: "queued" });
  assert.equal(mapped.resumable, null, "false would claim the server answered 'no'");
  assert.equal(mapped.cancel_requested, null);
  assert.equal(mapped.counts, null, "{} would claim every counter was measured as zero");
  assert.equal(mapped.notes, null, "[] would claim the server sent an empty note list");
  assert.equal(mapped.task_type, null);
});

test("a counts block with one non-numeric value is unreadable, not partial", () => {
  assert.equal(harness.mapRunRow({ ...RUN_ROW, counts: { ...RUN_ROW.counts, probes: "two" } }).counts, null);
  assert.equal(harness.mapRunRow({ ...RUN_ROW, counts: [] }).counts, null);
});

test("an unknown stage string is preserved verbatim", () => {
  assert.equal(harness.mapRunRow({ ...RUN_ROW, stage: "teleported" }).stage, "teleported");
});

test("status maps the capability row, the store and the degraded pointer", () => {
  const mapped = harness.mapStatus(STATUS);
  assert.equal(mapped.enabled, false);
  assert.equal(mapped.capability.state, "absent");
  assert.equal(mapped.capability.executes, false);
  assert.equal(mapped.capability.missing_wiring[0].wiring_point, "harness_improvement.executor");
  assert.equal(mapped.store.readable, true);
  assert.deepEqual(mapped.store.counts, { bundles: 1, runs: 2, experiences: 3 });
  assert.equal(mapped.active_bundle.manifest, null, "the manifest did not arrive");
  assert.equal(mapped.active_bundle.manifest_error, "malformed bundle: bad json", "a degraded pointer is named, never an absent one");
  assert.deepEqual(mapped.promotion_policy, { automatic_global_promotion: false });
});

test("an absent half of status is null, never a default claim", () => {
  const mapped = harness.mapStatus({});
  assert.equal(mapped.enabled, null, "false would claim the operator's setting");
  assert.equal(mapped.capability, null, "a missing capability row is not 'wiring complete'");
  assert.equal(mapped.store, null, "a missing store block is not an empty store");
  assert.equal(mapped.active_bundle, null, "a missing pointer is not an active bundle");
  assert.equal(mapped.ceilings, null);
});

test("a counts block on the store with a non-numeric value is unreadable, not partial", () => {
  assert.equal(harness.mapStatus({ ...STATUS, store: { ...STATUS.store, counts: { bundles: 1, runs: "two" } } }).store.counts, null);
});

test("the capability row's absent missing-wiring list stays null", () => {
  const mapped = harness.mapStatus({ harness_capability: { state: "absent" } });
  assert.equal(mapped.capability.missing_wiring, null, "[] would claim the wiring is complete");
});

test("a bundle row reads provenance and compatibility from their sub-records", () => {
  const mapped = harness.mapBundleRow(BUNDLE_RAW);
  assert.equal(mapped.bundle_id, "b1");
  assert.equal(mapped.promotion_state, "draft");
  assert.equal(mapped.source_run_id, "run_1");
  assert.equal(mapped.alpha_runtime_version, "1.0");
  assert.equal(mapped.components.length, 1);
  assert.equal(mapped.components[0].sha256, "aa");
});

test("an absent provenance on a bundle row is null, not a fabricated identity", () => {
  const mapped = harness.mapBundleRow({ bundle_id: "b1", promotion_state: "draft" });
  assert.equal(mapped.source_run_id, null);
  assert.equal(mapped.parent_bundle_id, null);
  assert.equal(mapped.components, null);
});

test("the run record maps its task, history and counts", () => {
  const mapped = harness.mapRunRecord(RUN_RECORD);
  assert.equal(mapped.task_text, "make retrieval better");
  assert.equal(mapped.task_outcome, "fewer retries");
  assert.equal(mapped.task_fingerprint, "fp_1");
  assert.deepEqual(mapped.checkpoint_stages, ["snapshotting"]);
  assert.equal(mapped.proposal_count, 1);
  assert.equal(mapped.validation_count, 0);
  assert.equal(mapped.probe_outcome_count, 0);
  assert.equal(mapped.history[0].stage, "blocked");
  assert.deepEqual(mapped.budget_state, { spent_model_calls: 0 });
});

test("an absent run record half maps to null rather than an empty claim", () => {
  const mapped = harness.mapRunRecord({ run_id: "run_x" });
  assert.equal(mapped.checkpoint_stages, null, "[] would claim no checkpoint was taken");
  assert.equal(mapped.proposal_count, null, "0 would claim no proposal was authored");
  assert.equal(mapped.history, null);
  assert.equal(mapped.notes, null);
});

test("a journal line keeps known fields out of extra and keeps the rest verbatim", () => {
  const mapped = harness.mapEventRow(JOURNAL_LINE);
  assert.equal(mapped.event, "stage_blocked");
  assert.equal(mapped.stage, "blocked");
  assert.deepEqual(mapped.extra, { candidate_id: "cand_1" });
  assert.equal(harness.mapEventRow({}).extra, null, "no extra keys is null, not {}");
});

test("receipts read the nested run record", () => {
  const receipt = harness.mapRunReceipt({ run: { run_id: "run_1", stage: "blocked" }, resumable: true, next_stage: "baseline_running", durability: "d" });
  assert.equal(receipt.run_id, "run_1");
  assert.equal(receipt.stage, "blocked");
  assert.equal(harness.mapRunReceipt({}).resumable, null);
  const cancel = harness.mapCancelReceipt({ run: { run_id: "run_1" }, cancel_requested: true, applied_at_next_boundary: false, note: "n" });
  assert.equal(cancel.cancel_requested, true);
  assert.equal(cancel.applied_at_next_boundary, false);
});

test("an experience's staleness is preserved, including its absence", () => {
  assert.deepEqual(harness.mapExperience(EXPERIENCE_RAW).staleness, []);
  assert.equal(harness.mapExperience({ experience_id: "e2" }).staleness, null, "a missing staleness list is not 'not stale'");
});

/* ── Local refusals happen before a request ────────────────────────────── */

test("a reason over the server bound is refused locally, naming the bound", () => {
  resetCalls();
  assert.throws(() => harness.assertReason("x".repeat(2001)), /2000 characters/);
  assert.doesNotThrow(() => harness.assertReason("x".repeat(2000)));
  assert.equal(calls.length, 0, "a local refusal must not send a request");
});

test("an approval kind outside the privileged vocabulary is refused locally", () => {
  resetCalls();
  assert.throws(() => harness.assertApprovalKinds(["permissions"]), /unknown approval kind "permissions"/);
  assert.doesNotThrow(() => harness.assertApprovalKinds(["scripts", "middleware"]));
  assert.equal(calls.length, 0);
});

test("a start with an empty or over-long text is refused before any request", async () => {
  resetCalls();
  await assert.rejects(() => harness.startHarnessRun({ task: "", outcome: "o" }), /task must be 1–20000 characters/);
  await assert.rejects(() => harness.startHarnessRun({ task: "t", outcome: "" }), /outcome must be 1–4000 characters/);
  await assert.rejects(() => harness.startHarnessRun({ task: "x".repeat(20001), outcome: "o" }), /task must be 1–20000 characters/);
  await assert.rejects(() => harness.startHarnessRun({ task: "t", outcome: "o", run_id: "x".repeat(129) }), /run_id must be 1–128 characters/);
  assert.equal(calls.length, 0);
});

test("an approve with a foreign approval kind is refused before any request", async () => {
  resetCalls();
  await assert.rejects(() => harness.approveHarnessCandidate("b1", { approvals: ["permissions"] }), /unknown approval kind/);
  await assert.rejects(() => harness.approveHarnessCandidate("b1", { reason: "x".repeat(2001) }), /2000 characters/);
  assert.equal(calls.length, 0);
});

/* ── failureText keeps a refusal verbatim ──────────────────────────────── */

test("a refusal that carries a status keeps the server's own sentence", () => {
  const refusal = apiFailure(409, "no operator approval is recorded for this candidate; record one with POST /api/harness/candidates/b1/approve first");
  assert.equal(harness.failureText(refusal), refusal.message, "errMsg would swap a 403/404 detail for a generic sentence; a promotion refusal's words are the answer");
  assert.equal(harness.failureText(apiFailure(404, "no harness bundle 'b1' is stored")), "no harness bundle 'b1' is stored");
  assert.equal(harness.failureText(apiFailure(503, "Harness improvement is disabled (config.yaml -> harness_improvement.enabled: false).")), "Harness improvement is disabled (config.yaml -> harness_improvement.enabled: false).");
});

test("a failure without a status falls back to the shared helper", () => {
  assert.equal(harness.failureText("boom"), "Something went wrong.");
  assert.equal(harness.failureText(new Error("network down")), "network down");
});

test("the client reaches the Gateway only through the shared transport", () => {
  assert.doesNotMatch(source, /\bfetch\s*\(/, "the client must use get/send from ./http");
  assert.match(source, /from "\.\/http"/);
});
