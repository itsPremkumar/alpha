// supervisor.test.mjs — autonomy supervisor + Sentinel client (wave W-A).
//
// Pins the client to the REAL gateway contract in
// backend/app/gateway/routers/autonomy.py (prefix /api/autonomy): exact paths,
// exact verbs, the always-explicit {auto_heal} body, the capped journal read,
// and — most importantly — the honesty inversions this plane can produce:
// never-run loops must not render epoch 0, an unavailable config block must
// never read as "enabled", a corrupt journal (500) must reject instead of
// looking like empty history, and a repair pass can never be requested by
// omitting the flag.
//
// Pure Node test (node --test): transpiles the TS modules and executes them
// against a stubbed http layer — no server, no browser.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
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

const client = load(sources["api-client"]);

function fixture(respond) {
  const calls = [];
  const api = {
    ...client,
    GATEWAY_BASE: "/api",
    apiFetch: client.createApiClient({
      baseUrl: "/api",
      getCookie: () => "",
      fetch: async (url, init) => {
        calls.push({ url, ...init });
        return respond(url, init);
      },
    }),
  };
  const http = load(sources.http, { "./api-client": api });
  return { calls, supervisor: load(sources.supervisor, { "./http": http }) };
}

const LOOP = {
  sentinel: {
    description: "Observe-only sentinel pass: collect signals, escalate unknown kinds.",
    enabled: false,
    runs: 0,
    failures: 0,
    parked: false,
    park_reason: "",
    running: false,
    last_run_at: 0.0,
    last_duration_seconds: 0.0,
    last_error: "",
    last_summary: "",
    task_alive: false,
  },
};

test("getSupervisorStatus reads GET /api/autonomy/status and maps the supervisor block", async () => {
  const f = fixture(() =>
    Response.json({
      supervisor: {
        enabled: true,
        started_at: 1750000000.5,
        loops: {
          ...LOOP,
          review_queue: {
            description: "Deferred learning reviews.",
            enabled: true,
            runs: 3,
            failures: 1,
            parked: false,
            park_reason: "",
            running: false,
            last_run_at: 1750000100.0,
            last_duration_seconds: 0.42,
            last_error: "",
            last_summary: "pending=2 signal=deferred_reviews",
            task_alive: true,
          },
        },
      },
      config: {
        available: true,
        autonomy_enabled: true,
        bus_enabled: true,
        loops: {
          sentinel: { enabled: false, interval_seconds: 600, jitter_seconds: 30 },
          review_queue: { enabled: true, interval_seconds: 120, jitter_seconds: 10 },
        },
        note: "request-time config.yaml view",
      },
      notes: ["'supervisor' is the live AutonomySupervisor singleton."],
    }),
  );
  const s = await f.supervisor.getSupervisorStatus();
  assert.equal(f.calls[0].url, "/api/autonomy/status");
  assert.equal(s.supervisor_enabled, true);
  assert.equal(s.started_at, 1750000000.5);
  assert.equal(s.loops.length, 2);
  const review = s.loops.find((l) => l.loop_id === "review_queue");
  assert.equal(review.runs, 3);
  assert.equal(review.failures, 1);
  assert.equal(review.task_alive, true);
  assert.equal(review.config_enabled, true);
  assert.equal(review.interval_seconds, 120);
  assert.equal(review.last_summary, "pending=2 signal=deferred_reviews");
  assert.deepEqual(s.notes, ["'supervisor' is the live AutonomySupervisor singleton."]);
});

test("a never-run loop stays null — 0.0 last_run_at and \"\" summary never render as real values", async () => {
  const f = fixture(() =>
    Response.json({
      supervisor: { enabled: false, started_at: 0.0, loops: LOOP },
      config: { available: true, autonomy_enabled: false, bus_enabled: false, loops: {}, note: "n" },
      notes: [],
    }),
  );
  const s = await f.supervisor.getSupervisorStatus();
  const sentinel = s.loops[0];
  assert.equal(s.started_at, null, "0.0 started_at means not started, never epoch 0");
  assert.equal(sentinel.last_run_at, null, "0.0 last_run_at means never run");
  assert.equal(sentinel.last_duration_seconds, null);
  assert.equal(sentinel.last_error, null, "empty last_error means none, not an empty error");
  assert.equal(sentinel.last_summary, null);
  assert.equal(sentinel.runs, 0, "real measured zero counter stays zero");
  assert.equal(sentinel.enabled, false);
  assert.equal(sentinel.task_alive, false);
});

test("an unavailable config block keeps every flag null and shows the server's real error", async () => {
  const f = fixture(() =>
    Response.json({
      supervisor: { enabled: true, started_at: 1.0, loops: LOOP },
      config: { available: false, error: "FileNotFoundError: config.yaml missing" },
      notes: [],
    }),
  );
  const s = await f.supervisor.getSupervisorStatus();
  assert.equal(s.config.available, false);
  assert.equal(s.config.error, "FileNotFoundError: config.yaml missing");
  assert.equal(s.config.autonomy_enabled, null, "unknown master switch is never rendered as false/enabled");
  assert.equal(s.config.bus_enabled, null);
  assert.equal(s.loops[0].config_enabled, null);
  assert.equal(s.loops[0].interval_seconds, null);
});

test("a loop missing from config: loops keeps its config flags null", async () => {
  const f = fixture(() =>
    Response.json({
      supervisor: { enabled: true, started_at: 1.0, loops: LOOP },
      config: { available: true, autonomy_enabled: true, bus_enabled: true, loops: {}, note: "n" },
      notes: [],
    }),
  );
  const s = await f.supervisor.getSupervisorStatus();
  assert.equal(s.loops[0].config_enabled, null);
});

test("getSentinelReports maps the journal envelope and passes disclosures through verbatim", async () => {
  const f = fixture(() =>
    Response.json({
      reports: [
        {
          recorded_at: "2026-09-24T10:00:00+00:00",
          trigger: "supervisor",
          auto_heal: false,
          report: {
            duration_s: 1.234,
            scanned: 4,
            summary: "scanned 4 signal(s): 0 fixed, 0 reverted, 0 escalated",
            fixed: 0,
            reverted: 0,
            escalated: 1,
            errors: ["verify timeout on one candidate"],
            outcomes: [{ kind: "missing_bom", status: "escalated" }],
          },
        },
      ],
      order: "oldest_first",
      total: 1,
      cap: 50,
      source: "C:/state/sentinel-reports/reports.jsonl",
      disclosures: [
        "source: C:/state/sentinel-reports/reports.jsonl",
        "history: newest 1 of 1 recorded pass(es) shown, oldest first; the journal file is append-only and is never rewritten",
      ],
    }),
  );
  const h = await f.supervisor.getSentinelReports();
  assert.equal(f.calls[0].url, "/api/autonomy/sentinel/reports?limit=50");
  assert.equal(h.total, 1);
  assert.equal(h.cap, 50);
  assert.equal(h.source, "C:/state/sentinel-reports/reports.jsonl");
  assert.equal(h.reports[0].trigger, "supervisor");
  assert.equal(h.reports[0].auto_heal, false);
  assert.equal(h.reports[0].report.escalated, 1);
  assert.deepEqual(h.reports[0].report.errors, ["verify timeout on one candidate"]);
  assert.equal(h.reports[0].report.outcomes.length, 1);
  assert.equal(h.disclosures.length, 2);
});

test("an empty journal maps to an empty list — no fabricated passes", async () => {
  const f = fixture(() =>
    Response.json({
      reports: [],
      order: "oldest_first",
      total: 0,
      cap: 50,
      source: "C:/state/sentinel-reports/reports.jsonl",
      disclosures: ["history: no passes recorded yet (journal present but empty)"],
    }),
  );
  const h = await f.supervisor.getSentinelReports();
  assert.deepEqual(h.reports, []);
  assert.equal(h.total, 0);
});

test("the report limit is clamped to the router's 1..200 window", async () => {
  const f = fixture(() => Response.json({ reports: [], total: 0, cap: 1, source: "s", disclosures: [] }));
  await f.supervisor.getSentinelReports(0);
  assert.match(f.calls[0].url, /limit=1$/);
  await f.supervisor.getSentinelReports(9999);
  assert.match(f.calls[1].url, /limit=200$/);
});

test("a corrupt journal (500) rejects instead of looking like an empty history", async () => {
  const f = fixture(() =>
    new Response(JSON.stringify({ detail: "corrupt sentinel report journal C:/x/reports.jsonl at line 4: invalid JSON" }), {
      status: 500,
      headers: { "content-type": "application/json" },
    }),
  );
  await assert.rejects(() => f.supervisor.getSentinelReports(), /corrupt sentinel report journal/);
});

test("runSentinelPass posts auto_heal:false by default — omission can never request repairs", async () => {
  const f = fixture(() =>
    Response.json({
      duration_s: 0.5,
      scanned: 2,
      summary: "scanned 2 signal(s): 0 fixed, 0 reverted, 0 escalated",
      fixed: 0,
      reverted: 0,
      escalated: 0,
      errors: [],
      outcomes: [],
    }),
  );
  const r = await f.supervisor.runSentinelPass();
  assert.equal(f.calls[0].url, "/api/autonomy/sentinel/run");
  assert.equal(f.calls[0].method, "POST");
  assert.deepEqual(JSON.parse(f.calls[0].body), { auto_heal: false });
  assert.equal(r.scanned, 2);
  assert.equal(r.fixed, 0);
  assert.deepEqual(r.errors, []);
});

test("runSentinelPass(autoHeal=true) sends the explicit repair flag and keeps counts verbatim", async () => {
  const f = fixture(() =>
    Response.json({
      duration_s: 9.5,
      scanned: 7,
      summary: "scanned 7 signal(s): 2 fixed, 1 reverted, 1 escalated, 1 error(s)",
      fixed: 2,
      reverted: 1,
      escalated: 1,
      errors: ["commit refused: dirty tree"],
      outcomes: [
        { kind: "missing_bom", status: "fixed", paths: ["a.py"] },
        { kind: "missing_bom", status: "reverted", paths: ["b.py"] },
      ],
    }),
  );
  const r = await f.supervisor.runSentinelPass(true);
  assert.deepEqual(JSON.parse(f.calls[0].body), { auto_heal: true });
  assert.equal(r.fixed, 2);
  assert.equal(r.reverted, 1);
  assert.equal(r.escalated, 1);
  assert.equal(r.duration_s, 9.5);
  assert.equal(r.outcomes.length, 2);
  assert.equal(r.outcomes[0].status, "fixed");
});

test("a failing run rejects with the gateway's real reason", async () => {
  const f = fixture(() =>
    new Response(JSON.stringify({ detail: "RuntimeError: repo root unavailable" }), {
      status: 500,
      headers: { "content-type": "application/json" },
    }),
  );
  await assert.rejects(() => f.supervisor.runSentinelPass(false), /repo root unavailable/);
});

test("getSentinelSignals keeps observe_only/fixes_applied strict and signal fields verbatim", async () => {
  const f = fixture(() =>
    Response.json({
      observe_only: true,
      fixes_applied: false,
      count: 1,
      note: "observe-only collection (SentinelRunner.collect): no diagnosis, no fixes, no commits.",
      signals: [
        {
          source: "logs",
          kind: "traceback",
          severity: "high",
          message: "Unhandled exception in worker",
          fingerprint: "abc123",
          detected_at: "2026-09-24T10:05:00+00:00",
          context: { file: "worker.py" },
        },
      ],
    }),
  );
  const s = await f.supervisor.getSentinelSignals();
  assert.equal(f.calls[0].url, "/api/autonomy/sentinel/signals");
  assert.equal(s.observe_only, true);
  assert.equal(s.fixes_applied, false);
  assert.equal(s.count, 1);
  assert.equal(s.signals[0].severity, "high");
  assert.equal(s.signals[0].kind, "traceback");
  assert.equal(s.signals[0].fingerprint, "abc123");
  assert.deepEqual(s.signals[0].context, { file: "worker.py" });
});

test("contract pin: the four autonomy routes exist in the real router", () => {
  const router = readFileSync(
    new URL("../../../backend/app/gateway/routers/autonomy.py", import.meta.url),
    "utf8",
  );
  assert.ok(existsSync(new URL("../../../backend/app/gateway/routers/autonomy.py", import.meta.url)));
  assert.match(router, /prefix="\/api\/autonomy"/);
  assert.match(router, /@router\.get\(\s*\n?\s*"\/status"/);
  assert.match(router, /"\/sentinel\/reports"/);
  assert.match(router, /@router\.post\(\s*\n?\s*"\/sentinel\/run"/);
  assert.match(router, /"\/sentinel\/signals"/);
});
