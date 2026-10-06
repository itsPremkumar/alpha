import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import ts from "typescript";

/*
 * APEX client contract.
 *
 * Two things are pinned here, and both are honesty rather than shape:
 *
 *  1. **Absent is not zero.** The backend goes to real trouble to report an
 *     unreadable store as `count: null` and an unattached subsystem as
 *     `available: false`. A mapper that reached for `?? 0` would turn "we could
 *     not read the store" into "there are zero sessions", and the operator would
 *     read the second as a working system. Every counter here has a case where
 *     the input is missing and the output must still be null.
 *
 *  2. **The client calls the routes that exist.** Each recorded call is compared
 *     against the exact path and verb, so a renamed or repointed route fails
 *     here rather than at runtime.
 */

const require = createRequire(import.meta.url);
const here = (relative) => fileURLToPath(new URL(relative, import.meta.url));
const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const toDataUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
const transpile = (source, extra = {}) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, ...extra },
  }).outputText;

/* ── Recorded transport ────────────────────────────────────────────────── */

const calls = [];
let responses = {};

/*
 * The stub reads its state off `globalThis` rather than closing over module
 * locals: a `data:` URL module has no importable binding for them, and a
 * closure over locals defined in *this* module would capture a different array
 * than the one the assertions read.
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
export function errMsg(e) { return e instanceof Error ? e.message : String(e); }
`);

const apexUrl = toDataUrl(transpile(read("./apex.ts")).replace(/from "\.\/http"/g, `from "${httpStub}"`));

globalThis.__calls = calls;
globalThis.__responses = responses;

const apex = await import(apexUrl);

/**
 * Record one response.
 *
 * Accepts `{ body }` for a success or `{ reject }` for a refused request, so a
 * test can express the difference the client has to preserve: a rejected call
 * must reject, never resolve to an empty envelope.
 */
function record(key, outcome) {
  responses[key] = outcome;
}

function lastCall() {
  return calls[calls.length - 1];
}

/* ── Fixtures ──────────────────────────────────────────────────────────── */

const HEALTHY_CONTRACT = {
  profile: "autonomous",
  enabled: true,
  digest: "apxc-abc123",
  budget: {
    max_active_agents: 6,
    max_parallel_tasks: 4,
    max_delegation_depth: 3,
    max_replans: 10,
    max_retries_per_failure_class: 3,
    max_runtime_minutes: 480,
    max_tool_calls: 2500,
  },
  controls: { pause_allowed: true, user_takeover: true, emergency_stop: true },
  authority_granted: ["chat", "tools", "research"],
  protected_actions: { destructive_filesystem: "approval", secret_export: "deny" },
  policy_sites: { run_lifecycle: "alpha.runtime.runs.manager:RunManager" },
  policy_sites_live: ["run_lifecycle"],
  policy_sites_missing: [],
  note: "APEX composes these sites; it is not a second policy kernel",
};

const HEALTHY_STATUS = {
  schema: "alpha.apex.status.v1",
  contract: { available: true, ...HEALTHY_CONTRACT },
  fleet: {
    available: true,
    mode: "run",
    generation: 4,
    reason: "",
    estop_sentinel: false,
    admits_work: true,
  },
  sessions: { available: true, total: 3, by_state: { active: 1, completed: 2 }, active: 1, terminal: 2 },
  invariants: {
    declared: 12,
    live: 12,
    all_live: true,
    invariants: [
      {
        id: "I9",
        statement: "APEX cannot disable the emergency stop.",
        status: "live",
        live: true,
        module: "alpha.runtime.control",
        symbol: "read_state",
        reason: "",
      },
    ],
  },
};

/* ── Routes and verbs ──────────────────────────────────────────────────── */

test("status reads /apex/status with no params when unfiltered", async () => {
  record("GET /apex/status", { body: HEALTHY_STATUS });
  await apex.fetchApexStatus();
  assert.equal(lastCall().path, "/apex/status");
  assert.equal(lastCall().method, "GET");
});

test("status sends session_id and the invariant toggle as query params", async () => {
  record("GET /apex/status?session_id=apx-1&include_invariants=false", { body: HEALTHY_STATUS });
  await apex.fetchApexStatus({ sessionId: "apx-1", includeInvariants: false });
  const path = lastCall().path;
  assert.match(path, /session_id=apx-1/);
  assert.match(path, /include_invariants=false/);
});

test("a session id is url-encoded rather than interpolated raw", async () => {
  record("GET /apex/status?session_id=apx%2F..%2Fsneaky", { body: HEALTHY_STATUS });
  await apex.fetchApexStatus({ sessionId: "apx/../sneaky" });
  assert.match(lastCall().path, /session_id=apx%2F\.\.%2Fsneaky/);
});

test("policy reads the named profile", async () => {
  // The stub answers per exact path, so the recorded body has to be the one the
  // route would return for *this* profile — otherwise the assertion is testing
  // the fixture rather than the mapping.
  record("GET /apex/policy?profile=apex_max", {
    body: {
      ...HEALTHY_CONTRACT,
      profile: "apex_max",
      budget: {
        max_active_agents: 12,
        max_parallel_tasks: 8,
        max_delegation_depth: 5,
        max_replans: 20,
        max_retries_per_failure_class: 4,
        max_runtime_minutes: 1440,
        max_tool_calls: 5000,
      },
    },
  });
  const contract = await apex.fetchApexPolicy("apex_max");
  assert.equal(contract.profile, "apex_max");
  assert.equal(contract.budget.max_tool_calls, 5000);
  assert.match(lastCall().path, /profile=apex_max/);
});

test("invariants reads /apex/invariants", async () => {
  record("GET /apex/invariants", { body: HEALTHY_STATUS.invariants });
  const report = await apex.fetchApexInvariants();
  assert.equal(report.declared, 12);
  assert.equal(report.live, 12);
});

test("create posts to /apex/sessions with the objective and profile", async () => {
  record("POST /apex/sessions", { body: { session: { session_id: "apx-77" } } });
  const created = await apex.createApexSession({
    objective: "fix the failing workflow",
    profile: "autonomous",
    acceptance_criteria: ["suite passes"],
  });
  assert.equal(created.session_id, "apx-77");
  assert.equal(lastCall().method, "POST");
  assert.equal(lastCall().body.objective, "fix the failing workflow");
  assert.deepEqual(lastCall().body.acceptance_criteria, ["suite passes"]);
});

test("cycle posts to the session route with no body", async () => {
  record("POST /apex/sessions/apx-77/cycle", {
    body: {
      session_id: "apx-77",
      decision: { action: "plan", reason: "awaiting_plan", confidence: 0.6, blocked: false },
      steps: [{ name: "load_session", outcome: "loaded", detail: "" }],
      state_before: "idle",
      state_after: "idle",
      changed: false,
    },
  });
  const result = await apex.runApexCycle("apx-77");
  assert.equal(result.decision.action, "plan");
  assert.equal(result.steps[0].name, "load_session");
  assert.equal(result.changed, false);
});

test("steer posts the instruction and never a prompt rewrite", async () => {
  record("POST /apex/sessions/apx-77/steer", { body: { constraint: { constraint_id: "cst-1" } } });
  await apex.steerApexSession("apx-77", "use local models only");
  assert.equal(lastCall().body.instruction, "use local models only");
  assert.deepEqual(Object.keys(lastCall().body), ["instruction"]);
});

/* ── Honesty: a missing block stays missing ────────────────────────────── */

test("a store the backend could not read becomes available:false, not zero", async () => {
  record("GET /apex/status", {
    body: {
      schema: "alpha.apex.status.v1",
      contract: { available: true, ...HEALTHY_CONTRACT },
      fleet: { available: true, mode: "run", generation: 1, admits_work: true },
      sessions: { available: false, reason: "JSONDecodeError: sessions.json is not JSON", count: null },
    },
  });
  const status = await apex.fetchApexStatus();
  assert.equal(status.sessions.available, false);
  assert.equal(status.sessions.total, undefined);
  assert.match(status.sessions.reason, /JSONDecodeError/);
  // The blocks that did answer must survive: one broken read cannot blank them.
  assert.equal(status.contract.available, true);
  assert.equal(status.fleet.available, true);
});

test("a block with no reason still discloses that it is unavailable", async () => {
  record("GET /apex/status", {
    body: {
      schema: "alpha.apex.status.v1",
      contract: { available: true, ...HEALTHY_CONTRACT },
      fleet: { available: false },
      sessions: { available: true, total: 0, by_state: {}, active: 0, terminal: 0 },
    },
  });
  const status = await apex.fetchApexStatus();
  assert.equal(status.fleet.available, false);
  assert.match(status.fleet.reason, /did not say why/);
});

test("an absent session total maps to null rather than 0", async () => {
  record("GET /apex/status", {
    body: {
      schema: "alpha.apex.status.v1",
      contract: { available: true, ...HEALTHY_CONTRACT },
      fleet: { available: true, mode: "run", admits_work: true },
      sessions: { available: true, by_state: {} },
    },
  });
  const status = await apex.fetchApexStatus();
  assert.equal(status.sessions.total, null);
  assert.equal(status.sessions.active, null);
  assert.equal(status.sessions.terminal, null);
});

test("a measured zero stays a real zero", async () => {
  record("GET /apex/status", {
    body: {
      schema: "alpha.apex.status.v1",
      contract: { available: true, ...HEALTHY_CONTRACT },
      fleet: { available: true, mode: "run", admits_work: true },
      sessions: { available: true, total: 0, by_state: {}, active: 0, terminal: 0 },
    },
  });
  const status = await apex.fetchApexStatus();
  assert.equal(status.sessions.total, 0);
});

/* ── Honesty: invariants never claim a pass they did not measure ───────── */

test("invariant rows carry live:false and the reason when a site is absent", async () => {
  record("GET /apex/invariants", {
    body: {
      declared: 12,
      live: 11,
      all_live: false,
      invariants: [
        {
          id: "I6",
          statement: "Every completion requires verification.",
          live: false,
          module: "alpha.mission.acceptance",
          symbol: "assert_acceptance_passed",
          reason: "ModuleNotFoundError: No module named 'alpha.mission.acceptance'",
        },
      ],
    },
  });
  const report = await apex.fetchApexInvariants();
  assert.equal(report.all_live, false);
  assert.equal(report.live, 11);
  assert.equal(report.declared, 12);
  assert.equal(report.invariants[0].live, false);
  assert.match(report.invariants[0].reason, /ModuleNotFoundError/);
});

test("the invariant block is absent when the poll skipped it", async () => {
  record("GET /apex/status", {
    body: {
      schema: "alpha.apex.status.v1",
      contract: { available: true, ...HEALTHY_CONTRACT },
      fleet: { available: true, mode: "run", admits_work: true },
      sessions: { available: true, total: 0, by_state: {}, active: 0, terminal: 0 },
    },
  });
  const status = await apex.fetchApexStatus();
  assert.equal(status.invariants, undefined);
});

/* ── Honesty: the emergency stop and delegated kernels ─────────────────── */

test("the emergency stop is surfaced as a fact, not a missing field", async () => {
  record("GET /apex/policy?profile=apex_max", { body: HEALTHY_CONTRACT });
  const contract = await apex.fetchApexPolicy("apex_max");
  assert.equal(contract.emergency_stop, true);
});

test("a contract with no controls block reports the stop as off, not unknown", async () => {
  record("GET /apex/policy?profile=apex_max", {
    body: {
      profile: "apex_max",
      enabled: true,
      digest: "d",
      budget: {},
      authority_granted: [],
      protected_actions: {},
      policy_sites: {},
      policy_sites_live: [],
      policy_sites_missing: ["run_lifecycle"],
    },
  });
  const contract = await apex.fetchApexPolicy("apex_max");
  assert.equal(contract.emergency_stop, false);
  assert.deepEqual(contract.policy_sites_missing, ["run_lifecycle"]);
});

test("missing policy sites are listed rather than implied absent", async () => {
  record("GET /apex/policy?profile=autonomous", { body: HEALTHY_CONTRACT });
  const contract = await apex.fetchApexPolicy("autonomous");
  assert.equal(contract.policy_sites.length, 1);
  assert.deepEqual(contract.policy_sites_live, ["run_lifecycle"]);
  assert.deepEqual(contract.policy_sites_missing, []);
  assert.match(contract.note, /not a second policy kernel/);
});

test("policy drift on a session is surfaced as a boolean, not guessed", async () => {
  record("GET /apex/status?session_id=apx-1", {
    body: {
      schema: "alpha.apex.status.v1",
      contract: { available: true, ...HEALTHY_CONTRACT },
      fleet: { available: true, mode: "run", admits_work: true },
      sessions: { available: true, total: 1, by_state: {}, active: 1, terminal: 0 },
      session: {
        available: true,
        session_id: "apx-1",
        objective: "obj",
        state: "active",
        profile: "autonomous",
        contract_digest: "apxc-old",
        mission_id: "",
        contract_drift: true,
        blocked_reason: "",
        cycle_count: 2,
        acceptance_criteria: [],
      },
    },
  });
  const status = await apex.fetchApexStatus({ sessionId: "apx-1" });
  assert.equal(status.session.available, true);
  assert.equal(status.session.contract_drift, true);
});

/* ── A refused request rejects with the server's reason ────────────────── */

test("a refused read rejects rather than resolving to an empty status", async () => {
  record("GET /apex/status", { reject: "HTTP 403: APEX control actions require an administrator" });
  await assert.rejects(() => apex.fetchApexStatus(), /administrator/);
});

test("the four profiles are exactly the ones the contract defines", () => {
  assert.deepEqual([...apex.APEX_PROFILES], ["off", "assist", "autonomous", "apex_max"]);
});