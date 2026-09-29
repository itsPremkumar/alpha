// runtime-visibility.test.mjs — what the runtime knows that the UI does not
// show, and the honest-rendering rules that guard it.
//
// ── The finding that started this ─────────────────────────────────────────────
//
// The durable-runtime layer the operator cannot see, verified against the live
// Gateway (read-only, `GET`/`openapi`-free because this build disables docs):
//
//   * `runtime/side_effects/` — the per-effect ledger whose `UNKNOWN` state is
//     durable and enumerable — has ZERO production callers. A whole-backend scan
//     for `runtime.side_effects|SideEffectLedger|SideEffectStatus|SideEffectEntry|
//     SideEffectReclaimer|arguments_digest` returns only: the package's own three
//     modules, its SQL repository (`persistence/side_effects/`), the alembic
//     migration `0027_side_effect_ledger`, the `persistence/models/__init__.py`
//     table registration, and two test files. Nothing under `app/` at all — no
//     router, no service, no tool. The ledger is INERT, so a view over it would
//     render a permanently-empty UNKNOWN list and read as "nothing is uncertain"
//     when the truth is that nothing ever records an effect. Deliberately no
//     view is built here; the finding is the deliverable.
//   * `runtime/sessions/` — `derive_session_state` has exactly one importer,
//     `runtime/network/wait_registry.py`, for `session_state_for()`. No route.
//   * `runtime/network/` — `NetworkWaitService` IS constructed at gateway startup
//     (`app/gateway/deps.py`), stored on `app.state.network_waits`, and stopped in
//     the drain. A scan for readers of `app.state.network_waits` /
//     `NetworkWaitStatus` / `get_network_wait_service` finds ONLY `deps.py` itself.
//     No route reads it, so neither the four network states nor the durable
//     parked-session registry is observable.
//   * `runtime/supervisor/` — zero production callers outside its own package,
//     matching its guide's own admission that it "is not yet wired into the
//     Windows launcher".
//   * `runtime/shutdown.py` — the eight-phase drain's `is_clean` is consumed at
//     `deps.py:781` by a `logger` call and nothing else. Not a route, not a view.
//
// The one durable-runtime fact that IS routed is the run-level `stop_reason` on
// `GET /api/threads/{id}/runs/{rid}`, and it is rendered by both `RunsSection`
// and `RunInspectorSection`. That is the whole of the overlap.
//
// ── What this file actually asserts ───────────────────────────────────────────
//
// Real components, server-rendered through `react-dom/server`, driven with the
// EXACT payloads the live Gateway returned on 2026-09-29. No source-regex
// assertions about the component bodies, no assertion against a copy of the code.
//
//   1. `AutonomyPanel` — `GET /api/ops/integration-health` returns ~110 measured
//      autonomy fields (8 loops x 13 keys) plus 4 event-bus fields. The old client
//      kept them in an opaque `Rec` that no view ever read, while the four
//      coverage cards showed `loops 8/8 wired` in GREEN with a "complete" badge.
//      All eight loops are `enabled: false`. "8/8 wired" said nothing about
//      whether anything runs; "0/8 enabled" is the answer an operator needs.
//   2. `watchdogDetail` — the header probe summarized its value with the constant
//      `() => "watching"`, discarding the fleet it had just fetched.
//   3. `swarmProgressView` — `completed ?? 0` / `total ?? 0` produced the
//      accessible string "0 of 0 tasks complete".
//
// Node test (node --test src/lib/*.test.mjs): transpiles the TS/TSX with the
// local `typescript`, rewrites specifiers to file:/data: URLs, no server and no
// browser (every component here is rendered by the server renderer).
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const here = (relative) => fileURLToPath(new URL(relative, import.meta.url));
const resolveUrl = (specifier) => pathToFileURL(require.resolve(specifier)).href;
const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const toDataUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
const transpile = (source, extra = {}) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, ...extra },
  }).outputText;
const loadUrl = (url) => import(url);
const reactRewrites = (code) =>
  code
    .replace(/from "react\/jsx-runtime"/g, `from "${resolveUrl("react/jsx-runtime")}"`)
    .replace(/from "react"/g, `from "${resolveUrl("react")}"`);
const LUCIDE = pathToFileURL(here("../../node_modules/lucide-react/dist/esm/lucide-react.js")).href;

const { createElement } = await import(resolveUrl("react"));
const { renderToStaticMarkup } = await import(resolveUrl("react-dom/server"));
const render = (component, props) => renderToStaticMarkup(createElement(component, props));
/** Visible (non-sr-only, non-aria-hidden) text, whitespace-collapsed. */
const visibleText = (markup) =>
  markup
    .replace(/<span class="sr-only">[\s\S]*?<\/span>/g, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&middot;|&#x2f;|&#x2014;|&mdash;/g, " ")
    .replace(/\s+/g, " ")
    .trim();

//: The class `Badge` maps tone="green" to (components/ui.tsx).
const GREEN = /text-emerald-600/;
const AMBER = /text-amber-600/;
const NEUTRAL = /bg-muted text-muted-foreground/;

/* ── Fixtures: the exact payloads the live Gateway returned ────────────────── */

const LOOPS = ["sentinel", "perpetual", "review_queue", "skill_curator", "enterprise_heartbeat", "swarm_status", "free_models_sync", "self_update"];

/** One real loop row, verbatim from `GET /api/ops/integration-health`. */
const liveLoop = (id, over = {}) => ({
  description: `${id} loop`,
  enabled: false,
  runs: 0,
  failures: 0,
  parked: false,
  park_reason: "",
  running: false,
  last_run_at: 0,
  last_duration_seconds: 0,
  last_error: "",
  last_summary: "",
  task_alive: false,
  ...over,
});

/** The measured integration-health payload: 134/61/42/8 wired, nothing running. */
const LIVE_HEALTH = {
  manifest_found: true,
  manifest_version: "1.0",
  coverage: {
    tools: { total: 134, wired: 134, intentionally_unwired: 0, excluded: 0 },
    routers: { total: 61, wired: 61, intentionally_unwired: 0, excluded: 0 },
    middlewares: { total: 42, wired: 42, intentionally_unwired: 0, excluded: 0 },
    loops: { total: 8, wired: 8, intentionally_unwired: 0, excluded: 0 },
  },
  autonomy: {
    enabled: true,
    started_at: 1790676998.9155316,
    loops: Object.fromEntries(LOOPS.map((id) => [id, liveLoop(id)])),
  },
  event_bus: { enabled: true, published: 3, dropped_total: 0, subscribers: [] },
  peer_network: { enabled: false },
  capabilities: {
    moa_engine: { enabled: false, module: "alpha.deliberation.moa_engine", target: "MoAEngine", kind: "engine", description: "Mixture-of-Agents.", loadable: false },
  },
  unwired: [],
  generated_at: "2026-09-29T01:00:16Z",
};

/* ── The client, behind a recording `./http` ───────────────────────────────── */

function loadIntegration(getImpl) {
  const calls = [];
  const exports = {};
  const compiled = ts.transpileModule(read("./integration.ts"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  new Function("exports", "require", compiled)(exports, (dependency) => {
    assert.equal(dependency, "./http", `integration.ts pulled an unexpected dependency: ${dependency}`);
    return { get: (path) => { calls.push({ path, method: "GET" }); return getImpl(path); } };
  });
  return { integration: exports, calls };
}

/* ── The real components ──────────────────────────────────────────────────── */

const apiUrl = toDataUrl(transpile(read("./api-client.ts")));
const httpUrl = toDataUrl(transpile(read("./http.ts")).replace(/from "\.\/api-client"/g, `from "${apiUrl}"`));
const integrationUrl = toDataUrl(transpile(read("./integration.ts")).replace(/from "\.\/http"/g, `from "${httpUrl}"`));
const uiUrl = toDataUrl(reactRewrites(transpile(read("../components/ui.tsx"), { jsx: ts.JsxEmit.ReactJSX })));
const sectionUrl = toDataUrl(
  reactRewrites(transpile(read("../components/sections/IntegrationSection.tsx"), { jsx: ts.JsxEmit.ReactJSX }))
    .replace(/from "@\/components\/ui"/g, `from "${uiUrl}"`)
    .replace(/from "@\/lib\/integration"/g, `from "${integrationUrl}"`)
    .replace(/from "@\/lib\/http"/g, `from "${httpUrl}"`)
    .replace(/from "lucide-react"/g, `from "${LUCIDE}"`),
);
const integrationSection = await loadUrl(sectionUrl);

/**
 * The payload the view is actually given.
 *
 * It is produced by running the REAL client over the live-shaped body rather
 * than hand-writing the mapped shape. A fixture typed to match the component
 * would let the mapper drift and the test would still be green — which is the
 * failure mode this file exists to rule out, so the client is the only path from
 * wire bytes to props.
 */
const mapHealth = (body) => loadIntegration(async () => body).integration.fetchIntegrationHealth();

/* ══ 1. The client keeps what the API measures ═════════════════════════════ */

test("every autonomy loop the Gateway reports reaches the client as a row", async () => {
  const { integration, calls } = loadIntegration(async () => LIVE_HEALTH);
  const health = await integration.fetchIntegrationHealth();
  assert.deepEqual(calls, [{ path: "/ops/integration-health", method: "GET" }]);
  assert.equal(health.autonomy.reported, true);
  assert.equal(health.autonomy.enabled, true);
  assert.deepEqual(health.autonomy.loops.map((l) => l.id), [...LOOPS].sort());
  for (const loop of health.autonomy.loops) {
    // The per-loop fields an operator actually needs, none of them dropped.
    assert.equal(typeof loop.enabled, "boolean", `${loop.id}.enabled`);
    assert.equal(typeof loop.runs, "number", `${loop.id}.runs`);
    assert.equal(typeof loop.failures, "number", `${loop.id}.failures`);
    assert.equal(typeof loop.parked, "boolean", `${loop.id}.parked`);
    assert.equal(typeof loop.running, "boolean", `${loop.id}.running`);
    assert.equal(typeof loop.last_run_at, "number", `${loop.id}.last_run_at`);
    assert.equal(typeof loop.task_alive, "boolean", `${loop.id}.task_alive`);
  }
});

test("the event bus and the peer-network flag are no longer dropped on the floor", async () => {
  const { integration } = loadIntegration(async () => LIVE_HEALTH);
  const health = await integration.fetchIntegrationHealth();
  assert.deepEqual(health.event_bus, {
    reported: true, enabled: true, published: 3, dropped_total: 0, subscribers: 0,
  });
  assert.deepEqual(health.peer_network, { reported: true, enabled: false });
});

test("an unreported counter is null, never a fabricated zero", async () => {
  const { integration } = loadIntegration(async () => ({
    ...LIVE_HEALTH,
    autonomy: { enabled: true, started_at: 1, loops: { sentinel: liveLoop("sentinel", { runs: undefined, failures: "many" }) } },
    event_bus: { enabled: true },
  }));
  const health = await integration.fetchIntegrationHealth();
  const [loop] = health.autonomy.loops;
  assert.equal(loop.runs, null, "a missing run count is unknown, not 0");
  assert.equal(loop.failures, null, "a non-numeric failure count is unknown, not 0");
  // A measured zero is still a zero: the two must stay distinguishable.
  const measured = await loadIntegration(async () => LIVE_HEALTH).integration.fetchIntegrationHealth();
  assert.equal(measured.autonomy.loops[0].runs, 0);
});

test("a payload with no autonomy block is reported as unmeasured, not as empty", async () => {
  // An absent key and an empty measured set are different claims; a bare
  // `loops: []` cannot carry that difference.
  const { integration } = loadIntegration(async () => ({}));
  const health = await integration.fetchIntegrationHealth();
  assert.equal(health.autonomy.reported, false);
  assert.equal(health.event_bus.reported, false);
  assert.equal(health.peer_network.reported, false);
  assert.equal(health.autonomy.enabled, null);
  const measured = await loadIntegration(async () => ({ ...LIVE_HEALTH, autonomy: { enabled: true, loops: {} } }))
    .integration.fetchIntegrationHealth();
  assert.equal(measured.autonomy.reported, true, "an answered-but-empty block is a measurement");
  assert.deepEqual(measured.autonomy.loops, []);
});

/* ══ 2. The view renders the runtime state, not just the manifest ══════════ */

test("every loop is named, and its off state is stated rather than implied", async () => {
  const markup = render(integrationSection.AutonomyPanel, { health: await mapHealth(LIVE_HEALTH) });
  for (const id of LOOPS) {
    assert.ok(markup.includes(`data-autonomy-loop="${id}"`), `loop ${id} is not rendered at all`);
  }
  const text = visibleText(markup);
  assert.match(text, /0\/8 loops enabled/, `the panel must say how many loops are on, got: ${text}`);
  // Every loop must carry its own off-state, not one summary line for the set.
  assert.equal(
    (text.match(/disabled in config/g) || []).length,
    LOOPS.length,
    `each of the ${LOOPS.length} loops must name its own state, got: ${text}`,
  );
  // The single most important disclosure: the supervisor is on, and every loop
  // it registered is switched off. Neither of those was visible before.
  assert.match(text, /supervisor enabled/);
  assert.equal((text.match(/never run/g) || []).length, LOOPS.length);
});

test("the event bus reports its measured counters instead of being absent", async () => {
  const text = visibleText(render(integrationSection.AutonomyPanel, { health: await mapHealth(LIVE_HEALTH) }));
  assert.match(text, /Event bus/);
  assert.match(text, /3 events published/);
  assert.match(text, /0 dropped/);
  assert.match(text, /0 subscribers/);
  assert.match(text, /peer network disabled/);
});

test("an unreported autonomy block is disclosed, never drawn as an idle supervisor", async () => {
  const health = await mapHealth({});
  const text = visibleText(render(integrationSection.AutonomyPanel, { health }));
  assert.match(text, /was not reported/i);
  assert.doesNotMatch(text, /0\/0 loops enabled/, "an unmeasured block must not render a ratio");
  assert.doesNotMatch(text, /no loops reported at runtime/);
});

test("an answered-but-empty loop set is stated as an answer, not as a failure", async () => {
  const health = await mapHealth({ ...LIVE_HEALTH, autonomy: { enabled: true, started_at: 1, loops: {} } });
  const text = visibleText(render(integrationSection.AutonomyPanel, { health }));
  assert.match(text, /No autonomy loops reported/i);
  assert.doesNotMatch(text, /was not reported/i, "the server did answer, so this is not the unmeasured branch");
});

test("a parked loop says why it is parked, and does not read as healthy", async () => {
  const health = await mapHealth({
    ...LIVE_HEALTH,
    autonomy: {
      enabled: true,
      started_at: 1,
      loops: { perpetual: liveLoop("perpetual", { enabled: true, parked: true, park_reason: "restart budget exhausted", runs: 12, failures: 12 }) },
    },
  });
  const markup = render(integrationSection.AutonomyLoopRow, { loop: health.autonomy.loops[0] });
  const text = visibleText(markup);
  assert.match(text, /parked: restart budget exhausted/);
  assert.match(text, /12 runs · 12 failures/);
  assert.doesNotMatch(markup, GREEN, "a parked loop must not wear the success colour");
});

test("a running loop is the only loop that earns the success colour", async () => {
  const enabled = await mapHealth({
    ...LIVE_HEALTH,
    autonomy: { enabled: true, started_at: 1, loops: { sentinel: liveLoop("sentinel", { enabled: true, running: true, runs: 4, last_run_at: Math.floor(Date.now() / 1000) - 30 }) } },
  });
  const running = render(integrationSection.AutonomyLoopRow, { loop: enabled.autonomy.loops[0] });
  assert.match(visibleText(running), /running now/);
  assert.match(visibleText(running), /4 runs/);
  // A relative age, so the assertion cannot fail on a second-boundary tick.
  assert.match(visibleText(running), /last run (29|30|31)s ago/);
  assert.match(running, GREEN);
  const off = await mapHealth(LIVE_HEALTH);
  const disabled = render(integrationSection.AutonomyLoopRow, { loop: off.autonomy.loops.find((l) => l.id === "sentinel") });
  assert.doesNotMatch(disabled, GREEN, "a loop that is off is not a success");
  assert.match(visibleText(disabled), /disabled in config/);
});

test("a loop's own last error is shown, not swallowed by the summary", async () => {
  const health = await mapHealth({
    ...LIVE_HEALTH,
    autonomy: { enabled: true, started_at: 1, loops: { skill_curator: liveLoop("skill_curator", { enabled: true, last_error: "prune pass raised KeyError", runs: 3, failures: 1 }) } },
  });
  const text = visibleText(render(integrationSection.AutonomyLoopRow, { loop: health.autonomy.loops[0] }));
  assert.match(text, /last error: prune pass raised KeyError/);
  assert.match(text, /3 runs · 1 failures/);
});

/* ══ 3. "8/8 wired" must not read as "8/8 running" ════════════════════════ */

test("the loops coverage card cross-references wiring against the runtime", async () => {
  const health = await mapHealth(LIVE_HEALTH);
  // 8 of 8 declared-and-wired, 0 of 8 enabled. Both numbers must be visible,
  // because only the second one says whether anything is scheduled.
  assert.match(visibleText(render(integrationSection.AutonomyPanel, { health })), /0\/8 loops enabled/);
  assert.equal(health.coverage.loops.wired, 8);
  assert.equal(health.coverage.loops.total, 8);
  assert.equal(health.autonomy.loops.filter((l) => l.enabled).length, 0);
});

test("the coverage badge says 'fully wired', which is the only claim it supports", () => {
  // The old badge read "complete" in green, next to 8 disabled loops. "complete"
  // reads as a health verdict; "fully wired" reads as the manifest fact it is.
  const source = read("../components/sections/IntegrationSection.tsx");
  assert.match(source, />fully wired</);
  assert.doesNotMatch(source, /tone="green">complete</);
});

/* ══ 4. The watchdog probe summarized its value with a constant ════════════ */

const supervisionUrl = toDataUrl(transpile(read("./supervision.ts")).replace(/from "\.\/http"/g, `from "${httpUrl}"`));
const { watchdogDetail, parseFleetWorkers } = await loadUrl(supervisionUrl);

test("an empty fleet is reported as an unobserved watchdog, not as 'watching'", () => {
  // `GET /api/supervision/fleet` answers HTTP 200 with `{}` on the measured
  // deployment: no worker has ever posted a heartbeat. The old probe rendered
  // the constant "watching" here and for every other fleet size.
  assert.equal(watchdogDetail([]), "no workers reporting — no heartbeat received, nothing is being watched");
  assert.doesNotMatch(watchdogDetail([]), /watching/);
});

test("the detail follows the fleet instead of a hardcoded word", () => {
  const workers = (n, anomalousIndices = []) =>
    Array.from({ length: n }, (_, i) => ({
      worker_id: `w${i}`, status: "running", last_heartbeat_elapsed_seconds: 1,
      progress_percent: 10, current_action: "step", active_task_id: `t${i}`,
      unresolved_anomalies_count: anomalousIndices.includes(i) ? 1 : 0,
    }));
  assert.equal(watchdogDetail(workers(1)), "1 worker reporting");
  assert.match(watchdogDetail(workers(7)), /^7 workers reporting$/);
  assert.match(watchdogDetail(workers(3, [0, 2])), /3 workers reporting · 2 with unresolved anomalies/);
  // An unreported anomaly count must not be counted as a passing worker.
  assert.match(
    watchdogDetail([{ worker_id: "w0", status: "running", last_heartbeat_elapsed_seconds: 1, progress_percent: 1, current_action: "", active_task_id: null, unresolved_anomalies_count: null }]),
    /^1 worker reporting$/,
  );
  // Every distinct fleet size must produce distinct wording, or the probe is a
  // constant with extra steps.
  const rendered = [0, 1, 2, 7].map((n) => watchdogDetail(workers(n)));
  assert.equal(new Set(rendered).size, rendered.length);
});

test("the real `{}` payload parses to zero workers rather than throwing", () => {
  // The shape the live Gateway actually sends. `parseFleetWorkers({})` must not
  // be a failure: it is a measured answer, and the detail function words it.
  assert.deepEqual(parseFleetWorkers({}), []);
});

test("a failed watchdog read reaches the UI as the server's reason", async () => {
  // `supervisionFleet` used to `catch { return null }`, which forced the probe
  // to invent "The Gateway returned no watchdog data." — the same fabrication
  // the company probe was fixed for. The strict reader now propagates.
  const stub = toDataUrl(`
    export async function get() { throw new Error("Request failed (HTTP 503). watchdog unavailable"); }
    export async function send() { throw new Error("unused"); }
    export function asList() { return []; }
  `);
  const strict = await loadUrl(toDataUrl(transpile(read("./supervision.ts")).replace(/from "\.\/http"/g, `from "${stub}"`)));
  await assert.rejects(() => strict.supervisionFleet(), /HTTP 503\)\. watchdog unavailable/);
});

/* ══ 5. A zero-denominator progress bar is not a reading ══════════════════ */

const { swarmProgressView } = await loadUrl(toDataUrl(transpile(read("./teamops-progress.ts"))));

test("a swarm plan that reports neither counter says so instead of '0 of 0'", () => {
  // The exact old rendering: `${completed ?? 0} of ${total ?? 0} tasks complete`.
  const view = swarmProgressView({});
  assert.doesNotMatch(view.label, /^0 of 0/);
  assert.match(view.label, /not reported/i);
  assert.equal(view.fraction, null, "an unmeasured plan must not draw a bar");
  assert.equal(swarmProgressView(undefined).fraction, null);
  assert.equal(swarmProgressView(null).fraction, null);
});

test("a measured zero stays a measured zero, and a real ratio still computes", () => {
  assert.deepEqual(swarmProgressView({ total: 0, completed: 0 }), { label: "plan has no tasks", fraction: 100 });
  assert.deepEqual(swarmProgressView({ total: 4, completed: 0 }), { label: "0 of 4 tasks complete", fraction: 0 });
  assert.deepEqual(swarmProgressView({ total: 4, completed: 2 }), { label: "2 of 4 tasks complete", fraction: 50 });
  assert.deepEqual(swarmProgressView({ total: 4, completed: 9 }), { label: "9 of 4 tasks complete", fraction: 100 });
  // A non-finite or absent counter is unknown, not 0.
  assert.equal(swarmProgressView({ total: 4, completed: Number.NaN }).fraction, null);
  assert.equal(swarmProgressView({ total: 4 }).fraction, null);
  assert.equal(swarmProgressView({ total: Number.POSITIVE_INFINITY, completed: 1 }).fraction, null);
  // The three claims must stay distinguishable from one another.
  const labels = new Set([
    swarmProgressView({}).label,
    swarmProgressView({ total: 0, completed: 0 }).label,
    swarmProgressView({ total: 4, completed: 0 }).label,
  ]);
  assert.equal(labels.size, 3);
});

test("the section actually renders the shared helper rather than its own maths", () => {
  // A guard on wiring, not on prose: if the section ever re-derives
  // `completed ?? 0` locally the test above would still pass on a dead module.
  const src = read("../components/sections/TeamOpsSection.tsx");
  assert.match(src, /from "@\/lib\/teamops-progress"/);
  assert.match(src, /swarmProgressView\(s\.progress\)/);
  assert.doesNotMatch(src, /completed \?\? 0/, "the section still invents a zero counter");
  assert.doesNotMatch(src, /total \?\? 1/, "the section still invents a denominator");
});
