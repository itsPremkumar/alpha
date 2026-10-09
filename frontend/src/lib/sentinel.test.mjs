// sentinel.test.mjs -- the advanced Sentinel client contract.
//
// Pins the client to the REAL gateway contract in
// backend/app/gateway/routers/autonomy.py (prefix /api/autonomy): exact paths,
// exact verbs, the bounded reads, and the honesty inversions this plane can
// produce. There are four of them and each has a tempting wrong reading:
//
//  1. **A total nobody measured is `null`, never `0`.** `scanned_total`,
//     `duration_mean_s` and `auto_heal_passes` all come back null when the
//     server did not report them. `?? 0` in any mapper would hand the panel
//     "the engine saw no signals" when the truth is "nobody measured", and
//     those lead to opposite actions.
//  2. **A verdict is derived from counters, not asserted.**
//     `analyticsHealthView` returns a reason beside its label, and the three
//     un-measured/observing states stay distinguishable from each other.
//  3. **A bounded read says how many rows it hid.** `truncated` is read *and*
//     derived, so a Gateway that bounds a list without declaring it cannot look
//     internally consistent.
//  4. **An admin refusal is kept verbatim.** `failureText` is not `errMsg`:
//     the shared helper paraphrases a 403, which is right for an incidental
//     read and wrong for the answer to a deliberate write.
//
// Pure Node test (node --test): transpiles the TS modules and executes them
// against a stubbed http layer: no server, no browser.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const transpile = (file) =>
  ts.transpileModule(readFileSync(new URL(`./${file}`, import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;

const sources = {
  "api-client": transpile("api-client.ts"),
  http: transpile("http.ts"),
  supervisor: transpile("supervisor.ts"),
  sentinel: transpile("sentinel.ts"),
};

function load(source, dependencies = {}) {
  const exports = {};
  new Function("exports", "require", "process", "console", "fetch", source)(
    exports,
    (dependency) => {
      assert.ok(Object.hasOwn(dependencies, dependency), `Unexpected dependency: ${dependency}`);
      return dependencies[dependency];
    },
    { env: {} },
    console,
    () => {
      throw new Error("Raw fetch must not be used");
    },
  );
  return exports;
}

const http = load(sources["http"], { "./api-client": load(sources["api-client"], {}) });
const supervisor = load(sources["supervisor"], { "./http": { ...http } });
const client = load(sources["sentinel"], { "./http": http, "./supervisor": supervisor });

function fixture(respond) {
  const calls = [];
  const stub = {
    get: async (path) => {
      calls.push({ path, method: "GET", body: undefined });
      // Both key forms are accepted: the analytics/escalations fixtures are
      // written with a verb prefix (mirroring the POST keys) and the
      // supervisor ones without.
      if (respond[path]) return respond[path];
      if (respond["GET " + path]) return respond["GET " + path];
      throw new Error("no recorded response for GET " + path);
    },
    send: async (path, method, payload) => {
      calls.push({ path, method, body: payload });
      const key = `${method} ${path}`;
      if (respond[key]) return respond[key];
      throw new Error("no recorded response for " + key);
    },
    errMsg: (err) => (err instanceof Error ? err.message : "Something went wrong."),
    pick: (obj, keys, fallback) => {
      if (obj && typeof obj === "object") {
        for (const k of keys) if (obj[k] !== undefined && obj[k] !== null) return obj[k];
      }
      return fallback;
    },
  };
  return { calls, stub };
}

/**
 * A client module whose ENTIRE transport is stubbed.
 *
 * The supervisor half has to be re-loaded with the same stub, because the
 * re-exported `getSentinelSignals` / `runSentinelPass` / `getSentinelReports`
 * close over `./http` inside *their own* module scope. Loading only `sentinel`
 * would leave those three talking to the real `apiFetch`.
 */
function loadWith(responses) {
  const { calls, stub } = fixture(responses);
  const stubbedSupervisor = load(sources["supervisor"], { "./http": stub });
  const loaded = load(sources["sentinel"], { "./http": stub, "./supervisor": stubbedSupervisor });
  return { calls, loaded };
}

/* -- The re-exported autonomy routes stay where they were -- */

test("the supervisor's sentinel routes are re-exported, not duplicated", () => {
  // A second copy of the reports/signals mappers would let the two clients
  // disagree about what the same journal means.
  assert.equal(typeof client.getSentinelReports, "function");
  assert.equal(typeof client.runSentinelPass, "function");
  assert.equal(typeof client.getSentinelSignals, "function");
  assert.equal(client.getSentinelReports, supervisor.getSentinelReports);
  assert.equal(client.runSentinelPass, supervisor.runSentinelPass);
  assert.equal(client.getSentinelSignals, supervisor.getSentinelSignals);
});

test("runSentinelPass always sends auto_heal explicitly", async () => {
  // The supervisor client owns this route; the pin lives here too because the
  // panel drives it from this module, so a dropped flag would look like the
  // panel forgot rather than the client.
  const { calls, loaded } = loadWith({ "POST /autonomy/sentinel/run": { summary: "s", scanned: 0 } });

  await loaded.runSentinelPass(false);

  assert.equal(calls[0].path, "/autonomy/sentinel/run");
  assert.deepEqual(calls[0].body, { auto_heal: false });
});

test("the observe and history reads are re-exported with their real paths", async () => {
  const { calls, loaded } = loadWith({
    "GET /autonomy/sentinel/signals": { observe_only: true, fixes_applied: false, count: 0, signals: [], note: "n" },
  });

  const signals = await loaded.getSentinelSignals();

  assert.equal(calls[0].path, "/autonomy/sentinel/signals");
  assert.equal(signals.observe_only, true);
});

/* -- GET /autonomy/sentinel/analytics -- */

const ANALYTICS = {
  limit: 50,
  total_on_disk: 7,
  note: "folded in-process",
  analytics: {
    passes: 3,
    malformed: 0,
    first_recorded_at: "2026-10-01T00:00:00+00:00",
    last_recorded_at: "2026-10-01T00:02:00+00:00",
    passes_with_outcomes: 2,
    passes_without_outcomes: 1,
    outcome_count: 4,
    distinct_fingerprints: 3,
    scanned_total: 6,
    scanned_reporting_passes: 3,
    fixed_total: 1,
    reverted_total: 0,
    escalated_total: 3,
    error_total: 0,
    auto_heal_passes: 1,
    observe_passes: 2,
    trigger_counts: { api: 2, "supervisor-loop": 1 },
    status_counts: { fixed: 1, escalated: 3 },
    duration_measured_passes: 2,
    duration_min_s: 0.4,
    duration_mean_s: 1.2,
    duration_max_s: 2.0,
    kinds: [
      {
        kind: "missing_bom",
        occurrences: 3,
        passes: 2,
        fixed: 1,
        reverted: 0,
        escalated: 2,
        skipped: 0,
        other: 0,
        status_missing: 0,
        distinct_fingerprints: 3,
        first_seen: "2026-10-01T00:00:00+00:00",
        last_seen: "2026-10-01T00:02:00+00:00",
        verdict: "repaired",
        unknown_statuses: [],
      },
    ],
    repeats: [
      {
        fingerprint: "fp-abc",
        kind: "missing_bom",
        passes: 2,
        occurrences: 2,
        first_seen: "2026-10-01T00:00:00+00:00",
        last_seen: "2026-10-01T00:02:00+00:00",
        verdict: "repaired",
      },
    ],
    errors: [],
    cap: 50,
    dropped_by_cap: 4,
    disclosures: ["folded: 3 journal pass(es), oldest first"],
  },
};

test("analytics is read from the real path with the clamped limit", async () => {
  const payload = JSON.parse(JSON.stringify(ANALYTICS));
  payload.limit = 25; // the server echoes the limit it validated
  const { calls, loaded } = loadWith({ "GET /autonomy/sentinel/analytics?limit=25": payload });
  const env = await loaded.getSentinelAnalytics(25);
  assert.deepEqual(calls, [{ path: "/autonomy/sentinel/analytics?limit=25", method: "GET", body: undefined }]);
  assert.equal(env.limit, 25);
  assert.equal(env.total_on_disk, 7);
  assert.equal(env.analytics.passes, 3);
  assert.equal(env.analytics.scanned_total, 6);
  assert.equal(env.analytics.fixed_total, 1);
  assert.equal(env.analytics.escalated_total, 3);
  assert.equal(env.analytics.duration_mean_s, 1.2);
  assert.equal(env.analytics.duration_measured_passes, 2);
  assert.equal(env.analytics.auto_heal_passes, 1);
  assert.equal(env.analytics.observe_passes, 2);
  assert.deepEqual(env.analytics.trigger_counts, { api: 2, "supervisor-loop": 1 });
  assert.deepEqual(env.analytics.status_counts, { fixed: 1, escalated: 3 });
  assert.equal(env.analytics.kinds[0].kind, "missing_bom");
  assert.equal(env.analytics.kinds[0].verdict, "repaired");
  assert.equal(env.analytics.kinds[0].status_missing, 0);
  assert.equal(env.analytics.repeats[0].fingerprint, "fp-abc");
  assert.deepEqual(env.analytics.disclosures, ["folded: 3 journal pass(es), oldest first"]);
});

test("an unreported scanned total maps to null, never 0", async () => {
  const payload = JSON.parse(JSON.stringify(ANALYTICS));
  delete payload.analytics.scanned_total;
  delete payload.analytics.scanned_reporting_passes;
  const loaded = load(sources["sentinel"], { "./http": fixture({ "GET /autonomy/sentinel/analytics?limit=50": payload }).stub, "./supervisor": supervisor });

  const env = await loaded.getSentinelAnalytics();

  assert.equal(env.analytics.scanned_total, null);
  assert.equal(env.analytics.scanned_reporting_passes, 0);
  assert.equal(loaded.scannedText(env.analytics), "not reported by any pass");
});

test("a partially reported scanned total names how many passes it covers", async () => {
  const payload = JSON.parse(JSON.stringify(ANALYTICS));
  payload.analytics.scanned_total = 6;
  payload.analytics.scanned_reporting_passes = 2;
  const loaded = load(sources["sentinel"], { "./http": fixture({ "GET /autonomy/sentinel/analytics?limit=50": payload }).stub, "./supervisor": supervisor });

  const env = await loaded.getSentinelAnalytics();

  assert.equal(loaded.scannedText(env.analytics), "6");
  assert.match(loaded.scannedText({ ...env.analytics, scanned_total: null }), /summed over 2 of 3 pass/);
});

test("an unreported duration stays null and reads as not measured", async () => {
  const payload = JSON.parse(JSON.stringify(ANALYTICS));
  payload.analytics.duration_mean_s = null;
  const loaded = load(sources["sentinel"], { "./http": fixture({ "GET /autonomy/sentinel/analytics?limit=50": payload }).stub, "./supervisor": supervisor });

  const env = await loaded.getSentinelAnalytics();

  assert.equal(env.analytics.duration_mean_s, null);
  assert.equal(loaded.durationText(env.analytics.duration_mean_s), "not measured");
});

test("an unreported auto_heal split stays null for both counts", async () => {
  const payload = JSON.parse(JSON.stringify(ANALYTICS));
  payload.analytics.auto_heal_passes = null;
  payload.analytics.observe_passes = null;
  const loaded = load(sources["sentinel"], { "./http": fixture({ "GET /autonomy/sentinel/analytics?limit=50": payload }).stub, "./supervisor": supervisor });

  const env = await loaded.getSentinelAnalytics();

  assert.equal(env.analytics.auto_heal_passes, null);
  assert.equal(env.analytics.observe_passes, null);
});

test("an unfamiliar verdict string is preserved verbatim", async () => {
  const payload = JSON.parse(JSON.stringify(ANALYTICS));
  payload.analytics.kinds[0].verdict = "quarantined";
  const loaded = load(sources["sentinel"], { "./http": fixture({ "GET /autonomy/sentinel/analytics?limit=50": payload }).stub, "./supervisor": supervisor });

  const env = await loaded.getSentinelAnalytics();

  assert.equal(env.analytics.kinds[0].verdict, "quarantined");
  // An unrecognised word renders neutral gray, never green.
  assert.equal(loaded.verdictTone("quarantined"), "gray");
  assert.equal(loaded.verdictWords("quarantined"), "quarantined");
});

test("a corrupt journal rejects with the server's reason", async () => {
  const loaded = load(sources["sentinel"], {
    "./http": fixture({ "GET /autonomy/sentinel/analytics?limit=50": null }).stub,
    "./supervisor": supervisor,
  });

  // The stub throws when no response is recorded, which is how the real client
  // behaves for a non-2xx: `get` rejects, so the panel renders the reason.
  await assert.rejects(() => loaded.getSentinelAnalytics(), /no recorded response/);
});

/* -- Bounds are mirrored, not tightened -- */

test("clampLimit refuses out-of-bound values locally with the bound named", () => {
  assert.throws(() => client.clampLimit(0), />= 1/);
  assert.throws(() => client.clampLimit(-3), />= 1/);
  assert.throws(() => client.clampLimit(201), /<= 200/);
  assert.equal(client.clampLimit(7), 7);
  assert.equal(client.clampLimit(7.9), 7);
  assert.equal(client.clampLimit(Number.NaN), 50);
});

test("an out-of-bound analytics limit never reaches the gateway", async () => {
  const { calls, stub } = fixture({});
  const loaded = load(sources["sentinel"], { "./http": stub, "./supervisor": supervisor });

  await assert.rejects(() => loaded.getSentinelAnalytics(500), /<= 200/);
  assert.deepEqual(calls, []);
});

/* -- GET /autonomy/sentinel/kinds -- */

const KINDS = {
  known_kinds: ["missing_bom", "syntax_error"],
  repair_kinds: ["missing_bom"],
  unrecognised_repair_kinds: [],
  verification_commands: { syntax_check: ["python", "-c", "pass"] },
  source_root: "C:/repo",
  disclosures: ["recognised: the fault kinds the loop can route"],
  kinds: [
    { kind: "missing_bom", recognised: true, repair_registered: true, repair_note: "a repair function is registered by default" },
    { kind: "syntax_error", recognised: true, repair_registered: false, repair_note: "no repair strategy is registered" },
  ],
};

test("kinds are read from the real path with the declared posture", async () => {
  const { calls, stub } = fixture({ "GET /autonomy/sentinel/kinds": KINDS });
  const loaded = load(sources["sentinel"], { "./http": stub, "./supervisor": supervisor });

  const kinds = await loaded.getSentinelKinds();

  assert.deepEqual(calls, [{ path: "/autonomy/sentinel/kinds", method: "GET", body: undefined }]);
  assert.deepEqual(kinds.known_kinds, ["missing_bom", "syntax_error"]);
  assert.deepEqual(kinds.repair_kinds, ["missing_bom"]);
  assert.equal(kinds.kinds.length, 2);
  assert.equal(kinds.kinds[0].repair_registered, true);
  assert.equal(kinds.kinds[1].repair_registered, false);
  assert.deepEqual(kinds.verification_commands, { syntax_check: ["python", "-c", "pass"] });
  assert.equal(kinds.source_root, "C:/repo");
  assert.deepEqual(kinds.disclosures, ["recognised: the fault kinds the loop can route"]);
});

/* -- GET /autonomy/sentinel/escalations -- */

function escalation(id, overrides = {}) {
  return {
    escalation_id: id,
    domain: "sentinel",
    task_id: "fp-abc",
    from_ref: "sentinel:scripts",
    to_ref: "human",
    reason: "unknown",
    reason_class: "permanent",
    attempt: 3,
    max_attempts: 3,
    detail: "sentinel stopped at diagnose",
    status: "open",
    created_at_iso: "2026-10-01T00:00:00+00:00",
    acknowledged_by: null,
    resolved_by: null,
    resolution: "",
    ...overrides,
  };
}

const ESCALATIONS = {
  escalations: [escalation("esc-1"), escalation("esc-2", { status: "resolved" })],
  total: 5,
  returned: 2,
  truncated: true,
  status_filter: null,
  source: "C:/state/handoff-ledger/escalations.json",
  disclosures: ["source: the durable human-escalation store"],
};

test("escalations are read oldest-first with truncation disclosed twice", async () => {
  const { calls, stub } = fixture({ "GET /autonomy/sentinel/escalations?limit=50": ESCALATIONS });
  const loaded = load(sources["sentinel"], { "./http": stub, "./supervisor": supervisor });

  const rows = await loaded.getSentinelEscalations();

  assert.deepEqual(calls, [{ path: "/autonomy/sentinel/escalations?limit=50", method: "GET", body: undefined }]);
  assert.equal(rows.total, 5);
  assert.equal(rows.returned, 2);
  assert.equal(rows.truncated, true);
  assert.equal(rows.escalations.length, 2);
  assert.equal(rows.escalations[0].escalation_id, "esc-1");
  assert.equal(rows.escalations[0].attempt, 3);
  assert.equal(rows.escalations[1].resolution, null); // empty string, not ""
  assert.equal(rows.escalations[0].acknowledged_by, null);
  assert.equal(rows.source, "C:/state/handoff-ledger/escalations.json");
});

test("truncated is derived when the gateway bounds a list without declaring it", async () => {
  const payload = JSON.parse(JSON.stringify(ESCALATIONS));
  delete payload.truncated;
  const loaded = load(sources["sentinel"], { "./http": fixture({ "GET /autonomy/sentinel/escalations?limit=50": payload }).stub, "./supervisor": supervisor });

  const rows = await loaded.getSentinelEscalations();

  assert.equal(rows.truncated, true, "a 2-of-5 read must not look like the whole backlog");
});

test("an undeclared total falls back to the rows that arrived, not to zero", async () => {
  const payload = JSON.parse(JSON.stringify(ESCALATIONS));
  delete payload.total;
  delete payload.returned;
  delete payload.truncated;
  const loaded = load(sources["sentinel"], { "./http": fixture({ "GET /autonomy/sentinel/escalations?limit=50": payload }).stub, "./supervisor": supervisor });

  const rows = await loaded.getSentinelEscalations();

  assert.equal(rows.total, 2, "the rows that arrived are the total, not 0");
  assert.equal(rows.returned, 2);
  assert.equal(rows.truncated, false);
});

test("a status filter is sent verbatim and url-encoded", async () => {
  const { calls, stub } = fixture({ "GET /autonomy/sentinel/escalations?limit=10&status=open": ESCALATIONS });
  const loaded = load(sources["sentinel"], { "./http": stub, "./supervisor": supervisor });

  await loaded.getSentinelEscalations(10, "open");

  assert.equal(calls[0].path, "/autonomy/sentinel/escalations?limit=10&status=open");
});

test("an unreadable escalation store rejects instead of reading as an empty queue", async () => {
  const loaded = load(sources["sentinel"], { "./http": fixture({}).stub, "./supervisor": supervisor });

  await assert.rejects(() => loaded.getSentinelEscalations(), /no recorded response/);
});

/* -- The two decision routes -- */

test("acknowledge posts to the real path and renders the server's own note", async () => {
  const decision = {
    applied: true,
    note: "acknowledged by the caller; the underlying fault is unchanged and no repair was performed",
    escalation: escalation("esc-1", { status: "acknowledged" }),
  };
  const { calls, stub } = fixture({ "POST /autonomy/sentinel/escalations/esc-1/acknowledge": decision });
  const loaded = load(sources["sentinel"], { "./http": stub, "./supervisor": supervisor });

  const result = await loaded.acknowledgeSentinelEscalation("esc-1", "operator");

  assert.equal(calls[0].path, "/autonomy/sentinel/escalations/esc-1/acknowledge");
  assert.equal(calls[0].method, "POST");
  assert.deepEqual(calls[0].body, { by: "operator" });
  assert.equal(result.applied, true);
  assert.equal(result.escalation.status, "acknowledged");
  assert.match(result.note ?? "", /no repair was performed/);
});

test("resolve carries the note and an id is url-encoded", async () => {
  const decision = { applied: true, note: "resolved by the caller", escalation: escalation("esc-1") };
  const { calls, stub } = fixture({
    "POST /autonomy/sentinel/escalations/esc%2F1/resolve": decision,
  });
  const loaded = load(sources["sentinel"], { "./http": stub, "./supervisor": supervisor });

  await loaded.resolveSentinelEscalation("esc/1", "operator", "handled by hand");

  assert.equal(calls[0].path, "/autonomy/sentinel/escalations/esc%2F1/resolve");
  assert.deepEqual(calls[0].body, { by: "operator", note: "handled by hand" });
});

test("an over-long resolution note is refused locally, never sent", async () => {
  const { calls, stub } = fixture({});
  const loaded = load(sources["sentinel"], { "./http": stub, "./supervisor": supervisor });

  await assert.rejects(() => loaded.resolveSentinelEscalation("esc-1", "operator", "x".repeat(2001)), /<= 2000/);
  assert.deepEqual(calls, []);
});

test("failureText keeps a 403 verbatim where errMsg would paraphrase it", () => {
  const refusal = Object.assign(new Error("admin session required"), { status: 403 });
  assert.equal(client.failureText(refusal), "admin session required");
  // A network-level error has no numeric status and falls back to errMsg.
  assert.equal(client.failureText(new Error("Network error")), "Network error");
});

/* -- The derived headline verdict -- */

function analyticsWith(overrides = {}) {
  const base = JSON.parse(JSON.stringify(ANALYTICS.analytics));
  return { ...base, ...overrides };
}

test("a window with repairs is green and names how many", () => {
  const view = client.analyticsHealthView(analyticsWith({ fixed_total: 3, escalated_total: 2 }));
  assert.equal(view.tone, "green");
  assert.equal(view.label, "repairing");
  assert.match(view.reason, /3 verified repair/);
});

test("only reverts is amber, not green and not red", () => {
  const view = client.analyticsHealthView(analyticsWith({ fixed_total: 0, reverted_total: 2, escalated_total: 0 }));
  assert.equal(view.tone, "amber");
  assert.equal(view.label, "repairs reverted");
  assert.match(view.reason, /2 repair\(s\) failed verification/);
});

test("escalations with no repair is red", () => {
  const view = client.analyticsHealthView(analyticsWith({ fixed_total: 0, reverted_total: 0, escalated_total: 4 }));
  assert.equal(view.tone, "red");
  assert.equal(view.label, "escalating, not repairing");
});

test("no passes folded is gray and says nothing was measured", () => {
  const view = client.analyticsHealthView(analyticsWith({ passes: 0, passes_without_outcomes: 0 }));
  assert.equal(view.tone, "gray");
  assert.equal(view.label, "no passes recorded");
  assert.match(view.reason, /nothing here was measured/);
});

test("passes with no per-signal detail is its own gray state", () => {
  // A pass ran and recorded nothing per signal. That is a different fact from
  // "no pass ran", and must not read as the same thing.
  const view = client.analyticsHealthView(
    analyticsWith({ passes: 3, passes_without_outcomes: 3, outcome_count: 0, fixed_total: 0, reverted_total: 0, escalated_total: 0 }),
  );
  assert.equal(view.tone, "gray");
  assert.equal(view.label, "no per-signal detail recorded");
  assert.match(view.reason, /3 of 3 pass\(es\) recorded no per-outcome rows/);
});

test("passes that recorded neither repair nor escalation are observing", () => {
  const view = client.analyticsHealthView(
    analyticsWith({ fixed_total: 0, reverted_total: 0, escalated_total: 0, outcome_count: 4, passes_without_outcomes: 0 }),
  );
  assert.equal(view.tone, "blue");
  assert.equal(view.label, "observing");
});

/* -- The evidence line -- */

test("the evidence line separates the verdict word from its counters", () => {
  const kind = {
    kind: "missing_bom",
    occurrences: 3,
    passes: 2,
    fixed: 1,
    reverted: 0,
    escalated: 2,
    skipped: 0,
    other: 0,
    status_missing: 0,
    distinct_fingerprints: 3,
    first_seen: null,
    last_seen: null,
    verdict: "repaired",
    unknown_statuses: [],
  };

  assert.equal(client.kindEvidenceLine(kind), "seen 3x, repaired 1x, escalated 2x");
});

test("a missing status is named separately from an unrecognised one", () => {
  const kind = {
    kind: "x",
    occurrences: 3,
    passes: 1,
    fixed: 0,
    reverted: 0,
    escalated: 0,
    skipped: 0,
    other: 2,
    status_missing: 1,
    distinct_fingerprints: 0,
    first_seen: null,
    last_seen: null,
    verdict: "unmeasured",
    unknown_statuses: ["quarantined"],
  };

  const line = client.kindEvidenceLine(kind);
  assert.match(line, /no status recorded 1x/);
  assert.match(line, /other 1x/);
});

/* -- The verdict vocabulary -- */

test("every known verdict has a tone and a word", () => {
  for (const verdict of ["repaired", "reverted", "unrepaired", "deferred", "unmeasured"]) {
    assert.equal(typeof client.verdictTone(verdict), "string");
    assert.ok(client.verdictWords(verdict).length > 0);
  }
  assert.equal(client.verdictTone("repaired"), "green");
  assert.equal(client.verdictTone("unrepaired"), "red");
  assert.equal(client.verdictTone("reverted"), "amber");
  assert.equal(client.verdictTone("deferred"), "gray");
});

test("a duration under a second is milliseconds, not 0.00 s", () => {
  assert.equal(client.durationText(0.4), "400 ms");
  assert.equal(client.durationText(1.5), "1.50 s");
  assert.equal(client.durationText(null), "not measured");
  assert.equal(client.durationText(0), "0 ms");
});

/* -- The client is read-only apart from the two decision routes -- */

test("the client imports send for the two decision routes and nothing else", () => {
  // A client that could post to the observe path would describe a surface the
  // server does not have.
  const source = readFileSync(new URL("./sentinel.ts", import.meta.url), "utf8");
  const posts = source.match(/send</g) ?? [];
  assert.equal(posts.length, 1, "exactly one send call site (decideEscalation)");
  assert.match(source, /\/autonomy\/sentinel\/escalations\/\$\{encodeURIComponent\(escalationId\)\}\/\$\{verb\}/);
});



