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

/* ── The enable picker never adopts a profile it cannot send ────────────── */

/*
 * `POST /apex/enable` refuses `off` by name, so `off` is not a rung the picker
 * may hold. Adopting it anyway was a live defect: a scope nobody had enabled
 * yet reads as `off`, the controlled `<select>` excluded `off` from its own
 * options, and the value the button sent therefore disagreed with the rung on
 * screen — a first-ever "Turn on" posted `{"profile":"off"}` and failed 422.
 * Each case below names a payload that would reintroduce it.
 */

test("the enable rungs are the contract profiles without off", () => {
  assert.deepEqual([...apex.ENABLE_PROFILES], ["assist", "autonomous", "apex_max"]);
  assert.equal(apex.ENABLE_PROFILES.includes("off"), false);
});

test("an unrecorded scope reads as off and is never adopted as a rung", () => {
  // The default state of a store nobody has configured — the exact payload
  // that made the first click on a fresh install fail.
  assert.equal(apex.profileToAdopt("off"), null);
});

test("a real profile the server confirmed is adopted verbatim", () => {
  for (const profile of ["assist", "autonomous", "apex_max"]) {
    assert.equal(apex.profileToAdopt(profile), profile);
  }
});

test("a profile from a newer build falls through rather than being snapped", () => {
  // Snapping `god_mode` to `assist` would enable a *different* authority than
  // the record names; an empty string must not silently become one either.
  assert.equal(apex.profileToAdopt("god_mode"), null);
  assert.equal(apex.profileToAdopt(""), null);
});

test("the toggle adopts through the helper and offers only the enable rungs", () => {
  // The picker and the button read one list. A component that re-open-coded the
  // adoption test would be free to accept `off` again, and the two would drift
  // apart exactly where the defect lived — the value sent, the value shown.
  const source = read("../components/sections/ApexSection.tsx");

  assert.match(source, /profileToAdopt\(/, "the toggle must adopt through profileToAdopt");
  assert.equal(
    /APEX_PROFILES\.includes\(/.test(source),
    false,
    "adopting from the full profile list reintroduces the 'off' enable attempt",
  );
  assert.match(
    source,
    /ENABLE_PROFILES\.map\(/,
    "the enable picker's options are the same list the adoption helper checks",
  );
});

test("a confirmed switch change re-reads the rest of the panel, not just the switch", () => {
  // Two reads render side by side: `/mode` draws the switch, `/status` draws
  // "Active profile". Mutating only the first put "off (no mission control)"
  // directly beneath an ON badge — the same contradiction as the stale-status
  // defect, produced by staleness instead of by a wrong default. The panel must
  // re-run its other read on the server's confirmation, never on the click.
  const source = read("../components/sections/ApexSection.tsx");

  assert.match(
    source,
    /onChanged\?\.\(\)/,
    "the toggle notifies the panel after the confirmed re-read",
  );
  assert.match(
    source,
    /onChanged=\{refresh\}/,
    "the panel's status read is the thing that re-runs",
  );
  // The notification must sit inside the success path: a refused write leaves
  // the panel exactly as it was, so re-reading it there would only repaint the
  // same answer.
  const toggleBody = source.slice(source.indexOf("const toggle = async"), source.indexOf("if (loadError)"));
  assert.match(toggleBody, /onChanged\?\.\(\)/, "the notify lives inside toggle()");
  assert.ok(
    toggleBody.indexOf("onChanged?.()") < toggleBody.indexOf("} catch (exc)"),
    "a failed write must not re-read the panel as though something changed",
  );
});

/* ── The mode toggle ───────────────────────────────────────────────────── */

/*
 * The toggle's contract is that it renders what the server last confirmed and
 * nothing else. Every test below names a payload that would make a plausible
 * wrong word appear — a green switch over a dead store, an "already on" success
 * for a write that never landed, an enable that silently keeps the previous
 * authority.
 */

const OFF_MODE = {
  enabled: false,
  contract_enabled: false,
  profile: "off",
  scope_key: "u1",
  changed: false,
  contract_digest: "apxc-off",
  durable: false,
  reason: "",
  enabled_at: null,
  updated_at: 1700000000,
};

const ON_MODE = {
  ...OFF_MODE,
  enabled: true,
  contract_enabled: true,
  profile: "assist",
  contract_digest: "apxc-assist",
  changed: true,
  durable: true,
  enabled_at: 1700000000,
};

test("the toggle reads GET /apex/mode", async () => {
  record("GET /apex/mode", { body: OFF_MODE });
  const mode = await apex.fetchApexMode();
  assert.equal(lastCall().path, "/apex/mode");
  assert.equal(lastCall().method, "GET");
  assert.equal(mode.enabled, false);
  assert.equal(mode.contract_enabled, false);
});

test("an unknown scope is sent as a query parameter, not smuggled into the path", async () => {
  record("GET /apex/mode?scope_key=thread%2F7", { body: OFF_MODE });
  await apex.fetchApexMode("thread/7");
  assert.equal(lastCall().path, "/apex/mode?scope_key=thread%2F7");
});

test("enabling posts to /apex/enable with the named profile", async () => {
  record("POST /apex/enable", { body: ON_MODE });
  const mode = await apex.setApexMode(true, { profile: "assist" });
  assert.equal(lastCall().method, "POST");
  assert.equal(lastCall().path, "/apex/enable");
  assert.deepEqual(lastCall().body, { profile: "assist" });
  assert.equal(mode.enabled, true);
  assert.equal(mode.changed, true);
});

test("disabling posts to /apex/disable and sends NO profile", async () => {
  // Sending a profile on the disable route would imply the field is meaningful
  // there. The server keeps the previous profile so a later enable restores the
  // authority the operator had.
  record("POST /apex/disable", { body: { ...ON_MODE, enabled: false, contract_enabled: false, changed: true } });
  const mode = await apex.setApexMode(false, { profile: "apex_max" });
  assert.equal(lastCall().path, "/apex/disable");
  assert.deepEqual(lastCall().body, {});
  assert.equal(mode.enabled, false);
  assert.equal(mode.profile, "assist", "the retained profile is reported, not the one just passed");
});

test("a second enable reports changed=false rather than a fresh success", async () => {
  record("POST /apex/enable", { body: { ...ON_MODE, changed: false, reason: "already enabled at this profile" } });
  const mode = await apex.setApexMode(true, { profile: "assist" });
  assert.equal(mode.changed, false);
  assert.equal(mode.reason, "already enabled at this profile");
});

test("a write that did not persist reports durable=false", async () => {
  // The toggle changed an in-memory row a restart will forget. Reporting this
  // as a durable setting would be the "APEX is on" claim surviving a reboot.
  record("POST /apex/enable", { body: { ...ON_MODE, durable: false } });
  const mode = await apex.setApexMode(true, { profile: "assist" });
  assert.equal(mode.enabled, true);
  assert.equal(mode.durable, false);
});

test("a degraded mode store keeps its load_error instead of reading as a clean OFF", async () => {
  record("GET /apex/mode", {
    body: { ...OFF_MODE, load_error: "JSONDecodeError: Expecting property name enclosed in double quotes" },
  });
  const mode = await apex.fetchApexMode();
  assert.equal(mode.enabled, false, "fail-closed: an unreadable store grants nothing");
  assert.match(mode.load_error, /JSONDecodeError/, "and it discloses why");
});

test("an unrecognised stored profile keeps its load_note", async () => {
  record("GET /apex/mode", { body: { ...ON_MODE, profile: "apex_pro_max", load_note: "unknown profile" } });
  const mode = await apex.fetchApexMode();
  assert.equal(mode.enabled, true);
  assert.equal(mode.profile, "apex_pro_max", "rendered verbatim, never snapped to a known profile");
  assert.equal(mode.load_note, "unknown profile");
});

test("enabled and contract_enabled are kept as two separate claims", async () => {
  // The contradictory record: someone switched this scope on, but the frozen
  // contract grants nothing. Collapsing these would paint it green.
  record("GET /apex/mode", { body: { ...ON_MODE, enabled: true, contract_enabled: false } });
  const mode = await apex.fetchApexMode();
  assert.equal(mode.enabled, true);
  assert.equal(mode.contract_enabled, false);
});

test("a refused toggle rejects with the server's reason", async () => {
  record("POST /apex/enable", { reject: "HTTP 403: APEX control actions require an administrator" });
  await assert.rejects(() => apex.setApexMode(true, { profile: "assist" }), /administrator/);
});

test("an unknown profile's 422 names the valid profiles rather than a generic failure", async () => {
  record("POST /apex/enable", {
    reject: "HTTP 422: unknown APEX profile 'god_mode'; expected one of ['off', 'assist', 'autonomous', 'apex_max']",
  });
  await assert.rejects(() => apex.setApexMode(true, { profile: "god_mode" }), /apex_max/);
});

test("a refused mode read rejects rather than resolving to a defaulted OFF switch", async () => {
  // The dangerous failure: a read that fails and resolves to { enabled: false }
  // renders a confident OFF, which reads as a deliberate decision rather than
  // an unknown one.
  record("GET /apex/mode", { reject: "HTTP 500: mode store unreachable" });
  await assert.rejects(() => apex.fetchApexMode(), /unreachable/);
});

/* ── Session control: pause / resume / stop ──────────────────────────────── */

/*
 * The control verbs are a control plane, not a second lifecycle owner, and the
 * card renders only what the server confirmed. Every test below names a payload
 * that would make a plausible wrong claim: the click's intent painted as an
 * applied transition, the stop route's boundary note dropped, a 409 folded into
 * a quiet no-op, or an optional field inflated to zero.
 */

const SESSION_RECORD = {
  session_id: "apx-1",
  owner: "tester",
  objective: "ship the release",
  state: "paused",
  profile: "autonomous",
  contract_digest: "apxc-abc123",
  mission_id: "msn-9",
  thread_id: "thread-1",
  blocked_reason: "",
  cycle_count: 4,
  acceptance_criteria: ["tests pass"],
  created_at: 1700000000,
  updated_at: 1700000100,
};

test("pause posts to /apex/pause and maps the server's session, not the click's intent", async () => {
  record("POST /apex/pause", {
    body: { applied: true, reason: "", session: { ...SESSION_RECORD, state: "paused" }, note: null },
  });
  const outcome = await apex.setApexControl("pause");
  assert.equal(lastCall().path, "/apex/pause");
  assert.equal(lastCall().method, "POST");
  assert.deepEqual(lastCall().body, {}, "the caller's own scope is the server default; no scope_key is invented");
  assert.equal(outcome.applied, true);
  assert.equal(outcome.session.state, "paused");
});

test("an explicit scope travels as scope_key, never interpolated into the path", async () => {
  record("POST /apex/resume", { body: { applied: true, reason: "", session: SESSION_RECORD, note: null } });
  await apex.setApexControl("resume", { scopeKey: "thread/7" });
  assert.equal(lastCall().path, "/apex/resume");
  assert.deepEqual(lastCall().body, { scope_key: "thread/7" });
});

test("a verb that changed nothing keeps applied:false and the server's reason", async () => {
  record("POST /apex/pause", {
    body: { applied: false, reason: "already paused", session: SESSION_RECORD, note: null },
  });
  const outcome = await apex.setApexControl("pause");
  assert.equal(outcome.applied, false);
  assert.equal(outcome.reason, "already paused");
});

test("stop carries the RunManager boundary note through the mapping", async () => {
  // The stop verb parks the mission's session; it is not the fleet ESTOP. If
  // the note were dropped, the UI would imply in-flight work was interrupted.
  record("POST /apex/stop", {
    body: {
      applied: true,
      reason: "",
      session: { ...SESSION_RECORD, state: "paused" },
      note: "APEX park only — in-flight runs belong to RunManager (spec §24) and were not interrupted.",
    },
  });
  const outcome = await apex.setApexControl("stop");
  assert.match(outcome.note, /RunManager/);
});

test("the approval gate's 409 rejects with the route it names rather than resolving", async () => {
  record("POST /apex/resume", {
    reject:
      "HTTP 409: session apx-1 is parked awaiting approval (acceptance pending); it already decides nothing, and the control verbs do not move a blocked session — decide it with POST /api/apex/approvals/{approval_id}/approve or .../reject",
  });
  await assert.rejects(() => apex.setApexControl("resume"), /approvals/);
});

test("session fields absent from the payload map to null, never 0", async () => {
  record("POST /apex/pause", {
    body: { applied: true, reason: "", session: { session_id: "apx-1", state: "active" }, note: null },
  });
  const outcome = await apex.setApexControl("pause");
  assert.equal(outcome.session.cycle_count, null);
  assert.equal(outcome.session.created_at, null);
  assert.equal(outcome.session.blocked_reason, "");
});

/* ── The approval gate over HTTP ─────────────────────────────────────────── */

const PENDING_APPROVAL = {
  approval_id: "apr-1",
  session_id: "apx-1",
  status: "pending",
  note: "acceptance pending: tests pass",
  requester: "apex.executive",
  operator: "",
  requested_at: 1700000000,
  decided_at: null,
};

const APPROVALS_BODY = {
  available: true,
  count: 2,
  pending: 1,
  approvals: [
    PENDING_APPROVAL,
    { ...PENDING_APPROVAL, approval_id: "apr-0", status: "rejected", operator: "admin-1", decided_at: 1700000500 },
  ],
};

test("approvals reads GET /apex/approvals and keeps pending separate from count", async () => {
  record("GET /apex/approvals", { body: APPROVALS_BODY });
  const list = await apex.fetchApexApprovals();
  assert.equal(lastCall().path, "/apex/approvals");
  assert.equal(lastCall().method, "GET");
  assert.equal(list.available, true);
  assert.equal(list.count, 2);
  assert.equal(list.pending, 1);
  assert.equal(list.approvals.length, 2);
  assert.equal(list.approvals[0].approval_id, "apr-1");
  assert.equal(list.approvals[0].status, "pending");
});

test("a degraded approval store keeps count and pending null instead of 0", async () => {
  // "We could not look" and "nothing is pending" lead to opposite actions, so
  // the difference has to survive the mapping rather than defaulting to zero.
  record("GET /apex/approvals", {
    body: { available: false, reason: "JSONDecodeError: approvals.json is not JSON", count: null, approvals: [] },
  });
  const list = await apex.fetchApexApprovals();
  assert.equal(list.available, false);
  assert.equal(list.count, null);
  assert.equal(list.pending, null);
  assert.deepEqual(list.approvals, []);
  assert.match(list.reason, /JSONDecodeError/);
});

test("a measured zero pending stays a real zero", async () => {
  record("GET /apex/approvals", { body: { available: true, count: 0, pending: 0, approvals: [] } });
  const list = await apex.fetchApexApprovals();
  assert.equal(list.count, 0);
  assert.equal(list.pending, 0);
});

test("a bounded approvals list keeps the whole count beside the rows it sent", async () => {
  // The route bounds the rows but counts the whole backlog, so a panel that
  // derived `count` from `approvals.length` would understate the gate by
  // exactly the rows it hid.
  record("GET /apex/approvals", {
    body: { available: true, count: 500, pending: 3, returned: 200, truncated: true, approvals: [PENDING_APPROVAL] },
  });
  const list = await apex.fetchApexApprovals();
  assert.equal(list.count, 500, "the backlog is the whole set, not the returned rows");
  assert.equal(list.returned, 200);
  assert.equal(list.truncated, true);
  assert.notEqual(list.count, list.approvals.length);
});

test("truncation is derived when the Gateway sends no flag", async () => {
  // An older Gateway that bounds the list without declaring it must still be
  // disclosed — deriving it keeps the panel honest against a server bug.
  record("GET /apex/approvals", { body: { available: true, count: 500, returned: 200, approvals: [] } });
  const list = await apex.fetchApexApprovals();
  assert.equal(list.truncated, true);
});

test("an unbounded approvals list is not reported as truncated", async () => {
  record("GET /apex/approvals", { body: APPROVALS_BODY });
  const list = await apex.fetchApexApprovals();
  assert.equal(list.returned, null, "an unbounded read reports no bound rather than guessing one");
  assert.equal(list.truncated, false);
});

test("a degraded approvals read reports no bound rather than a zero bound", async () => {
  record("GET /apex/approvals", {
    body: { available: false, reason: "JSONDecodeError: approvals.json is not JSON", count: null, approvals: [] },
  });
  const list = await apex.fetchApexApprovals();
  assert.equal(list.returned, null);
  assert.equal(list.truncated, false);
});

test("a verdict posts to the approval's approve route with the note", async () => {
  record("POST /apex/approvals/apr-1/approve", {
    body: {
      approval: { ...PENDING_APPROVAL, status: "approved", operator: "admin-1", decided_at: 1700000900 },
      resumed: true,
      session: { ...SESSION_RECORD, state: "active" },
    },
  });
  const outcome = await apex.decideApexApproval("apr-1", "approve", { note: "looks fine" });
  assert.equal(lastCall().path, "/apex/approvals/apr-1/approve");
  assert.equal(lastCall().method, "POST");
  assert.deepEqual(lastCall().body, { note: "looks fine" });
  assert.equal(outcome.approval.status, "approved");
  assert.equal(outcome.resumed, true, "the response says the park was released");
  assert.equal(outcome.session.state, "active");
});

test("a reject posts to the reject route with resumed false and no session", async () => {
  // The asymmetry: reject does NOT un-park, so `resumed` is false and the
  // response carries no session. Inferring success from which button was
  // pressed would paint a refusal as a release.
  record("POST /apex/approvals/apr-1/reject", {
    body: {
      approval: { ...PENDING_APPROVAL, status: "rejected", operator: "admin-1", decided_at: 1700000900 },
      resumed: false,
      session: null,
    },
  });
  const outcome = await apex.decideApexApproval("apr-1", "reject");
  assert.equal(lastCall().path, "/apex/approvals/apr-1/reject");
  assert.deepEqual(lastCall().body, { note: "" }, "an omitted note still sends the field the route expects");
  assert.equal(outcome.resumed, false);
  assert.equal(outcome.session, null);
});

test("an approval id is url-encoded rather than interpolated raw", async () => {
  record("POST /apex/approvals/apr%2F..%2Fsneaky/approve", {
    body: { approval: PENDING_APPROVAL, resumed: false, session: null },
  });
  await apex.decideApexApproval("apr/../sneaky", "approve");
  assert.equal(lastCall().path, "/apex/approvals/apr%2F..%2Fsneaky/approve");
});

test("a second verdict's 409 rejects with the server's reason", async () => {
  record("POST /apex/approvals/apr-1/approve", {
    reject: "HTTP 409: approval apr-1 was already decided ('rejected'); a verdict is never overwritten",
  });
  await assert.rejects(() => apex.decideApexApproval("apr-1", "approve"), /already decided/);
});

/* ── Goals — the Goal Operating System's HTTP surface ────────────────────── */

const GOAL_RECORD = {
  goal_id: "gl-1",
  objective: "fix the browser",
  parent_goal_id: "",
  state: "executing",
  owner: "tester",
  priority: 50,
  risk: "R1",
  plan_version: 2,
  success_criteria: ["suite passes"],
  constraints: ["no network"],
  session_id: "apx-1",
  mission_id: "msn-9",
  current_strategy: "stepwise",
  blocked_reason: "",
  created_at: 1700000000,
  updated_at: 1700000100,
};

test("goals reads GET /apex/goals and maps the records verbatim", async () => {
  record("GET /apex/goals", { body: { available: true, count: 1, goals: [GOAL_RECORD] } });
  const list = await apex.fetchApexGoals();
  assert.equal(lastCall().path, "/apex/goals");
  assert.equal(lastCall().method, "GET");
  assert.equal(list.count, 1);
  assert.equal(list.goals[0].goal_id, "gl-1");
  assert.equal(list.goals[0].state, "executing");
  assert.deepEqual(list.goals[0].success_criteria, ["suite passes"]);
});

test("a goal store that could not be read keeps count null, not 0", async () => {
  record("GET /apex/goals", {
    body: { available: false, reason: "OSError: goals.json unreadable", count: null, goals: [] },
  });
  const list = await apex.fetchApexGoals();
  assert.equal(list.available, false);
  assert.equal(list.count, null);
  assert.deepEqual(list.goals, []);
  assert.match(list.reason, /OSError/);
});

test("creating a goal posts to /apex/goals with the caller's objective", async () => {
  record("POST /apex/goals", { body: { goal: GOAL_RECORD } });
  const goal = await apex.createApexGoal({ objective: "fix the browser", success_criteria: ["suite passes"] });
  assert.equal(lastCall().path, "/apex/goals");
  assert.equal(lastCall().method, "POST");
  assert.equal(lastCall().body.objective, "fix the browser");
  assert.equal(goal.goal_id, "gl-1");
  assert.equal(goal.session_id, "apx-1", "the session link survives the mapping — /decisions needs it");
});

test("one goal reads its own route with the id encoded", async () => {
  record("GET /apex/goals/gl%2F1", { body: { goal: GOAL_RECORD, tree: { goal: "gl-1", children: [] } } });
  const goal = await apex.fetchApexGoal("gl/1");
  assert.equal(lastCall().path, "/apex/goals/gl%2F1");
  assert.equal(goal.goal_id, "gl-1");
  assert.equal(goal.parent_goal_id, "");
});

/* ── active_session rides the mode read ──────────────────────────────────── */

test("the mode read carries the active session the control verbs act on", async () => {
  record("GET /apex/mode", { body: { ...OFF_MODE, active_session: SESSION_RECORD } });
  const mode = await apex.fetchApexMode();
  assert.equal(mode.active_session.session_id, "apx-1");
  assert.equal(mode.active_session.state, "paused");
  assert.equal(mode.active_session.cycle_count, 4);
  assert.deepEqual(mode.active_session.acceptance_criteria, ["tests pass"]);
});

test("a mode with no bound session keeps active_session null", async () => {
  // Null is a real answer ("no session exists for this scope"); an absent
  // key from an older Gateway must not become a fabricated record either.
  record("GET /apex/mode", { body: { ...OFF_MODE, active_session: null } });
  const mode = await apex.fetchApexMode();
  assert.equal(mode.active_session, null);

  record("GET /apex/mode", { body: OFF_MODE });
  const older = await apex.fetchApexMode();
  assert.equal(older.active_session, null);
});