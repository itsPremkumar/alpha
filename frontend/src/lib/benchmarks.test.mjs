// benchmarks.test.mjs — benchmark plane client (wave W-C).
//
// Pins the client to the REAL gateway contract in
// backend/app/gateway/routers/benchmarks.py: exact paths, exact method verbs,
// JSON bodies, envelope mapping ({suites}/{results}), the runner's own
// reported shapes (suite summary carries a CASE COUNT, not a case list;
// results carry result_id/suite/case_id/passed/score/detail/duration_sec),
// the 200-result clamp, and gateway failures rejecting instead of rendering
// as empty success.
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
  benchmarks: transpile("benchmarks.ts"),
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
  return { calls, benchmarks: load(sources.benchmarks, { "./http": http }) };
}

test("listBenchmarkSuites reads GET /api/benchmarks/suites and maps the suites envelope", async () => {
  const f = fixture(() =>
    Response.json({
      suites: [
        { name: "workforce-smoke", version: "v1", cases: 2 },
        { name: "eval-basic", version: "v3", cases: 12 },
      ],
    })
  );
  const suites = await f.benchmarks.listBenchmarkSuites();
  assert.equal(f.calls[0].url, "/api/benchmarks/suites");
  assert.equal(f.calls[0].method, "GET");
  assert.equal(suites.length, 2);
  assert.equal(suites[0].name, "workforce-smoke");
  assert.equal(suites[0].version, "v1");
  // The API reports a COUNT of cases — never presented as a list of results.
  assert.equal(suites[0].cases, 2);
  assert.equal(suites[1].cases, 12);
});

test("suite summaries coerce a missing case count to 0 instead of inventing one", async () => {
  const f = fixture(() => Response.json({ suites: [{ name: "empty", version: "v1" }] }));
  const [suite] = await f.benchmarks.listBenchmarkSuites();
  assert.equal(suite.cases, 0);
});

test("listBenchmarkResults reads GET /api/benchmarks/results with the default limit", async () => {
  const f = fixture(() =>
    Response.json({
      results: [
        {
          result_id: "r1",
          suite: "workforce-smoke@v1",
          case_id: "evidence-gate",
          passed: true,
          score: 1,
          detail: "evidence gate enforces commit+tests+lint",
          duration_sec: 0.004,
          created_at: 1700000000.25,
        },
      ],
    })
  );
  const rows = await f.benchmarks.listBenchmarkResults();
  assert.equal(f.calls[0].url, "/api/benchmarks/results?limit=50");
  assert.equal(f.calls[0].method, "GET");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].result_id, "r1");
  assert.equal(rows[0].suite, "workforce-smoke@v1");
  assert.equal(rows[0].case_id, "evidence-gate");
  assert.equal(rows[0].passed, true);
  assert.equal(rows[0].score, 1);
  assert.equal(rows[0].detail, "evidence gate enforces commit+tests+lint");
  assert.equal(rows[0].duration_sec, 0.004);
});

test("the result limit is clamped to the router's 200 ceiling and floored at 1", async () => {
  const f = fixture(() => Response.json({ results: [] }));
  await f.benchmarks.listBenchmarkResults(9999);
  await f.benchmarks.listBenchmarkResults(0);
  assert.equal(f.calls[0].url, "/api/benchmarks/results?limit=200");
  assert.equal(f.calls[1].url, "/api/benchmarks/results?limit=1");
});

test("runBenchmarkSuite POSTs /api/benchmarks/suites/{name}/run and maps the report", async () => {
  const f = fixture(() =>
    Response.json({
      suite: "workforce-smoke",
      version: "v1",
      total: 2,
      passed: 2,
      failed: 0,
      results: [
        { result_id: "r1", suite: "workforce-smoke@v1", case_id: "evidence-gate", passed: true, score: 1, detail: "ok", duration_sec: 0.01, created_at: 10 },
        { result_id: "r2", suite: "workforce-smoke@v1", case_id: "recovery-bounded", passed: true, score: 1, detail: "ok", duration_sec: 0.01, created_at: 11 },
      ],
    })
  );
  const report = await f.benchmarks.runBenchmarkSuite("workforce-smoke");
  assert.equal(f.calls[0].url, "/api/benchmarks/suites/workforce-smoke/run");
  assert.equal(f.calls[0].method, "POST");
  // No case filter sent => empty body, exactly as RunSuiteRequest expects.
  assert.deepEqual(JSON.parse(f.calls[0].body), {});
  assert.equal(report.suite, "workforce-smoke");
  assert.equal(report.version, "v1");
  assert.equal(report.total, 2);
  assert.equal(report.passed, 2);
  assert.equal(report.failed, 0);
  assert.equal(report.results.length, 2);
  assert.equal(report.results[0].case_id, "evidence-gate");
});

test("runBenchmarkSuite forwards an explicit case_ids filter (URL-encoded suite name)", async () => {
  const f = fixture(() =>
    Response.json({ suite: "eval/basic", version: "v3", total: 1, passed: 0, failed: 1, results: [] })
  );
  await f.benchmarks.runBenchmarkSuite("eval/basic", ["case-a"]);
  assert.equal(f.calls[0].url, "/api/benchmarks/suites/eval%2Fbasic/run");
  assert.deepEqual(JSON.parse(f.calls[0].body), { case_ids: ["case-a"] });
});

test("an unknown suite 404 rejects instead of rendering a fake zero report", async () => {
  const f = fixture(() => ({ ok: false, status: 404, json() { assert.fail("error body must not be read"); } }));
  await assert.rejects(f.benchmarks.runBenchmarkSuite("nope"), (err) => err.status === 404);
});

test("a gateway failure rejects instead of rendering an empty suite list", async () => {
  const f = fixture(() => ({ ok: false, status: 503, json() { assert.fail("error body must not be read"); } }));
  await assert.rejects(f.benchmarks.listBenchmarkSuites(), (err) => err.status === 503);
  await assert.rejects(f.benchmarks.listBenchmarkResults(), (err) => err.status === 503);
});

// ── Contract pin: every consumed path must exist in the backend router ──────
const ROUTER = new URL("../../../backend/app/gateway/routers/benchmarks.py", import.meta.url);
const ROUTE_DECORATORS = [
  '@router.get("/suites"',
  '@router.post("/suites/{name}/run"',
  '@router.get("/results"',
];

test("all 3 benchmark routes exist in the backend router (contract pin)", { skip: !existsSync(ROUTER) }, () => {
  const src = readFileSync(ROUTER, "utf8");
  for (const decorator of ROUTE_DECORATORS) {
    assert.ok(src.includes(decorator), `router is missing ${decorator}`);
  }
});
