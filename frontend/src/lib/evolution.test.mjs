// evolution.test.mjs — bounded evolution API client (wave W-C).
//
// Pins the client to the REAL gateway contract in
// backend/app/gateway/routers/evolution.py: exact paths, limit clamps
// (router clamps ledger to 500), identity/update-state honesty fields
// ("unknown" commit + real note preserved), the autonomy-safety default on
// gate (human_approved=false, autonomous_mode=false), and failures rejecting
// instead of rendering as empty success.
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
  evolution: transpile("evolution.ts"),
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
  return { calls, evolution: load(sources.evolution, { "./http": http }) };
}

test("getEvolutionLedger reads GET /api/evolution/ledger with the default limit", async () => {
  const f = fixture(() =>
    Response.json({ events: [{ event: "promoted", candidate_id: "ev-1", at: 1700000000.5 }] })
  );
  const events = await f.evolution.getEvolutionLedger();
  assert.equal(f.calls[0].url, "/api/evolution/ledger?limit=100");
  assert.equal(f.calls[0].method, "GET");
  assert.equal(events.length, 1);
  assert.equal(events[0].event, "promoted");
  assert.equal(events[0].candidate_id, "ev-1");
});

test("ledger limit is clamped to the router's 500 ceiling and floored at 1", async () => {
  const f = fixture(() => Response.json({ events: [] }));
  await f.evolution.getEvolutionLedger(9999);
  await f.evolution.getEvolutionLedger(0);
  assert.equal(f.calls[0].url, "/api/evolution/ledger?limit=500");
  assert.equal(f.calls[1].url, "/api/evolution/ledger?limit=1");
});

test("getEvolutionIdentity reads GET /api/evolution/identity and keeps honesty fields verbatim", async () => {
  const f = fixture(() =>
    Response.json({
      agentId: "alpha-deadbeef",
      identityVersion: 1,
      createdAt: "2026-01-01T00:00:00+00:00",
      alphaVersion: "2.1.0",
      gitCommit: "unknown",
      gitCommitSource: "unavailable",
      gitCommitNote: "git rev-parse could not run: [WinError 2]",
      os: "Windows",
      architecture: "AMD64",
      runtime: "python 3.12.0",
      repository: { owner: "owner", name: "repo" },
      releaseChannel: "stable",
      updateState: "IDLE",
      capabilities: ["identity", "release_check", "evolution_ledger", "auto_update"],
    })
  );
  const identity = await f.evolution.getEvolutionIdentity();
  assert.equal(f.calls[0].url, "/api/evolution/identity");
  assert.equal(f.calls[0].method, "GET");
  assert.equal(identity.agentId, "alpha-deadbeef");
  // "unknown" + the real note must survive — never replaced by a guess.
  assert.equal(identity.gitCommit, "unknown");
  assert.equal(identity.gitCommitSource, "unavailable");
  assert.equal(identity.gitCommitNote, "git rev-parse could not run: [WinError 2]");
  assert.equal(identity.updateState, "IDLE");
  assert.deepEqual(identity.capabilities, ["identity", "release_check", "evolution_ledger", "auto_update"]);
  assert.deepEqual(identity.repository, { owner: "owner", name: "repo" });
});

test("getEvolutionUpdateState reads GET /api/evolution/update-state and preserves CHECK_FAILED detail", async () => {
  const f = fixture(() =>
    Response.json({
      state: "CHECK_FAILED",
      checkedAt: "2026-01-02T00:00:00+00:00",
      installedVersion: "2.1.0",
      latestTag: null,
      error: "GitHub latest-release request was refused with HTTP 403 (rate limited)",
    })
  );
  const state = await f.evolution.getEvolutionUpdateState();
  assert.equal(f.calls[0].url, "/api/evolution/update-state");
  assert.equal(state.state, "CHECK_FAILED");
  assert.equal(state.latestTag, null);
  assert.equal(state.installedVersion, "2.1.0");
  assert.equal(state.error, "GitHub latest-release request was refused with HTTP 403 (rate limited)");
});

test("checkForEvolutionUpdate POSTs /api/evolution/update-check and returns the in-body state", async () => {
  const f = fixture(() =>
    Response.json({ state: "UP_TO_DATE", checkedAt: "t", installedVersion: "2.1.0", latestTag: "v2.1.0", error: null })
  );
  const state = await f.evolution.checkForEvolutionUpdate();
  assert.equal(f.calls[0].url, "/api/evolution/update-check");
  assert.equal(f.calls[0].method, "POST");
  assert.deepEqual(JSON.parse(f.calls[0].body), {});
  assert.equal(state.state, "UP_TO_DATE");
  assert.equal(state.latestTag, "v2.1.0");
});

test("requestEvolutionUpdate POSTs the admin-only apply handoff without a client ref", async () => {
  const f = fixture(() => Response.json({ ok: true, state: "APPLY_REQUESTED", transaction_id: "upd-test" }));
  const result = await f.evolution.requestEvolutionUpdate(true);
  assert.equal(f.calls[0].url, "/api/evolution/update-apply");
  assert.equal(f.calls[0].method, "POST");
  assert.deepEqual(JSON.parse(f.calls[0].body), { force: true });
  assert.equal(result.transaction_id, "upd-test");
});

test("proposeEvolutionCandidate POSTs /api/evolution/candidates with the documented body", async () => {
  const f = fixture(() =>
    Response.json({ candidate_id: "ev-1", surface: "prompt", target: "summarizer", status: "candidate" })
  );
  const candidate = await f.evolution.proposeEvolutionCandidate({
    surface: "prompt",
    target: "summarizer",
    payload: { variant: "b" },
  });
  assert.equal(f.calls[0].url, "/api/evolution/candidates");
  assert.equal(f.calls[0].method, "POST");
  assert.deepEqual(JSON.parse(f.calls[0].body), {
    surface: "prompt",
    target: "summarizer",
    payload: { variant: "b" },
    parent_id: null,
  });
  assert.equal(candidate.candidate_id, "ev-1");
});

test("recordEvolutionBenchmark POSTs /api/evolution/candidates/{id}/benchmark", async () => {
  const f = fixture(() =>
    Response.json({ candidate_id: "ev-1", status: "benchmarking", benchmark: { passed: 5, failed: 0 } })
  );
  const updated = await f.evolution.recordEvolutionBenchmark("ev-1", { passed: 5, failed: 0 });
  assert.equal(f.calls[0].url, "/api/evolution/candidates/ev-1/benchmark");
  assert.equal(f.calls[0].method, "POST");
  assert.deepEqual(JSON.parse(f.calls[0].body), { benchmark: { passed: 5, failed: 0 } });
  assert.equal(updated?.status, "benchmarking");
});

test("gateEvolutionCandidate defaults to NO autonomy and NO approval (omission never grants autonomy)", async () => {
  const f = fixture(() => Response.json({ promoted: false, reason: "awaiting human approval" }));
  const outcome = await f.evolution.gateEvolutionCandidate("ev-1");
  assert.equal(f.calls[0].url, "/api/evolution/candidates/ev-1/gate");
  assert.equal(f.calls[0].method, "POST");
  assert.deepEqual(JSON.parse(f.calls[0].body), {
    baseline: {},
    human_approved: false,
    autonomous_mode: false,
  });
  assert.equal(outcome.promoted, false);
  assert.equal(outcome.reason, "awaiting human approval");
});

test("rollbackEvolutionCandidate POSTs /api/evolution/candidates/{id}/rollback with an encoded reason", async () => {
  const f = fixture(() => Response.json({ rolled_back: true }));
  const rolled = await f.evolution.rollbackEvolutionCandidate("ev-1", "worse than baseline");
  assert.equal(f.calls[0].url, "/api/evolution/candidates/ev-1/rollback?reason=worse%20than%20baseline");
  assert.equal(f.calls[0].method, "POST");
  assert.equal(rolled, true);
});

test("a gateway failure rejects instead of rendering an empty ledger", async () => {
  const f = fixture(() => ({ ok: false, status: 503, json() { assert.fail("error body must not be read"); } }));
  await assert.rejects(f.evolution.getEvolutionLedger(), (err) => err.status === 503);
  await assert.rejects(f.evolution.getEvolutionIdentity(), (err) => err.status === 503);
  await assert.rejects(f.evolution.getEvolutionUpdateState(), (err) => err.status === 503);
});

// ── Contract pin: every consumed path must exist in the backend router ──────
const ROUTER = new URL("../../../backend/app/gateway/routers/evolution.py", import.meta.url);
const ROUTE_DECORATORS = [
  '@router.post("/candidates"',
  '@router.post("/candidates/{candidate_id}/benchmark"',
  '@router.post("/candidates/{candidate_id}/gate"',
  '@router.post("/candidates/{candidate_id}/rollback"',
  '@router.get("/ledger"',
  '@router.get("/identity"',
  '@router.post("/update-check"',
  '@router.get("/update-state"',
  '@router.post("/update-apply"',
  '@router.post("/update-skip"',
  '@router.post("/update-recover"',
];

test("all evolution routes exist in the backend router (contract pin)", { skip: !existsSync(ROUTER) }, () => {
  const src = readFileSync(ROUTER, "utf8");
  for (const decorator of ROUTE_DECORATORS) {
    assert.ok(src.includes(decorator), `router is missing ${decorator}`);
  }
});
