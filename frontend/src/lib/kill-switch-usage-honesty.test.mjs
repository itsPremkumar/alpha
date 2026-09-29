// kill-switch-usage-honesty.test.mjs — two surfaces that reported nothing as
// "nothing", plus a safety badge painted from a constant.
//
// F-A. The Usage series. `GET /api/console/usage` answers `ConsoleUsageResponse`
// (backend/app/gateway/routers/console.py:105): `days` is a LIST and `by_model`
// is a DICT keyed by model id. `fetchConsoleUsage` looked for `daily`/`series`
// (never sent) and handed `by_model` to `asList`, which only accepts an array.
// Both fell through to `[]`, so DashboardSection rendered "No daily data yet."
// and "No per-model data yet." against a 200 full of real usage — a non-answer
// displayed as an answer, in the one surface whose job is reporting measurement.
//
// F-B. The emergency-stop badge. `GET /api/bots/kill-switch` answers with
// `global_kill_switch_active`. `killSwitchState` looked for `active`/`engaged`,
// found neither, and returned the hardcoded `false` from `pick`'s fallback — so
// BotOpsSection painted a GREEN "running" badge and "team working normally"
// from a constant, and a 500 painted the same green badge because the `catch`
// returned the same `false`. It was structurally incapable of showing an
// engaged stop. `false` ("off") and unknown are different claims; the state is
// now tri-state.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;
const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const transpile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;

/** http.ts stub whose responses the test controls. */
const makeHttp = () => {
  const state = { usage: null, kill: null, failUsage: false, failKill: false };
  const src = `
let state = null;
export function __set(s) { state = s; }
export async function get(path) {
  if (path === "/console/usage") {
    if (state.failUsage) throw new Error("HTTP 503 console usage unavailable");
    // The usage payload is set as the whole state in some tests, so accept it
    // either at \`state.usage\` (sibling flags) or as the state itself.
    return state.usage !== undefined ? state.usage : state;
  }
  if (path === "/bots/kill-switch") {
    if (state.failKill) throw new Error("HTTP 500 kill-switch subsystem down");
    return state.kill !== undefined ? state.kill : state;
  }
  return {};
}
export async function send() { return {}; }
export function asList(body, keys) {
  if (Array.isArray(body)) return body;
  for (const k of keys) if (body && typeof body === "object" && Array.isArray(body[k])) return body[k];
  return [];
}
export function pick(obj, keys, fallback) {
  for (const k of keys) if (obj && obj[k] !== undefined && obj[k] !== null) return obj[k];
  return fallback;
}
`;
  return { url: toDataUrl(src), state };
};

const http = makeHttp();
const httpModule = await import(http.url);

async function load(file) {
  const url = toDataUrl(transpile(read(file)).replace(/from\s+"\.\/http"/, `from "${http.url}"`));
  return import(url);
}

const { fetchConsoleUsage } = await load("./workspace.ts");
const teamops = await load("./teamops.ts");
const { killSwitchState } = teamops;
// Sanity: the two modules under test are distinct, and both loaded for real.
assert.notEqual(fetchConsoleUsage, teamops.killSwitchState);

/* ══ F-A: the usage series and the by-model breakdown ═══════════════════ */

// Verbatim shape of ConsoleUsageResponse, with the two shapes that matter:
// `days` as a list, `by_model` as a dict keyed by model id.
const LIVE_USAGE = {
  days: [
    { date: "2026-09-27", total_tokens: 1200, input_tokens: 1000, output_tokens: 200, runs: 3, cost: 0.0 },
    { date: "2026-09-28", total_tokens: 2508790, input_tokens: 2504636, output_tokens: 4154, runs: 41, cost: 0.0 },
  ],
  by_model: {
    "kilo:kilo-auto/free": { tokens: 1487750, runs: 20, cost: null, input_tokens: 1400000, cache_read_tokens: 0 },
    "pollinations:openai-fast": { tokens: 40288, runs: 8, cost: 0.0, input_tokens: 40288, cache_read_tokens: 0 },
  },
  total_tokens: 2508790,
  total_runs: 41,
  total_cost: null,
  currency: null,
};

test("the daily series is read from `days`, the field the Gateway sends", async () => {
  httpModule.__set({ ...LIVE_USAGE, failUsage: false });
  const { series } = await fetchConsoleUsage();
  assert.equal(series.length, 2, "a 200 carrying two days must not render as 'No daily data yet.'");
  assert.deepEqual(series.map((p) => p.day), ["2026-09-27", "2026-09-28"]);
  assert.equal(series[1].tokens, 2508790);
});

test("the by-model breakdown is read from a dict, using its keys as the model names", async () => {
  httpModule.__set({ ...LIVE_USAGE, failUsage: false });
  const { byModel } = await fetchConsoleUsage();
  assert.equal(byModel.length, 2, "an object-shaped by_model must not fall through to []");
  assert.deepEqual(byModel.map((m) => m.model).sort(), [
    "kilo:kilo-auto/free",
    "pollinations:openai-fast",
  ]);
  assert.equal(byModel.find((m) => m.model === "kilo:kilo-auto/free").tokens, 1487750);
});

test("an unpriced model keeps cost null rather than becoming 0.00", async () => {
  httpModule.__set({ ...LIVE_USAGE, failUsage: false });
  const { byModel } = await fetchConsoleUsage();
  assert.equal(byModel.find((m) => m.model === "kilo:kilo-auto/free").cost, null);
});

test("an array-shaped by_model is still accepted (older/alternate envelope)", async () => {
  httpModule.__set({ days: [], by_model: [{ model: "x", tokens: 5, cost: 1.5 }], failUsage: false });
  const { byModel } = await fetchConsoleUsage();
  assert.deepEqual(byModel, [{ model: "x", tokens: 5, cost: 1.5 }]);
});

test("a genuinely empty usage window is still empty — and the read rejects on failure", async () => {
  httpModule.__set({ days: [], by_model: {}, total_tokens: 0, failUsage: false });
  const { series, byModel } = await fetchConsoleUsage();
  assert.deepEqual(series, [], "a real empty window must still be []");
  assert.deepEqual(byModel, []);

  // Shape 2: a failure must reject, never resolve as an empty usage report.
  httpModule.__set({ ...LIVE_USAGE, failUsage: true });
  await assert.rejects(fetchConsoleUsage(), /503/);
});

/* ══ F-B: the emergency stop is a measurement, not a constant ══════════ */

// Verbatim from the live Gateway.
const LIVE_KILL = {
  global_kill_switch_active: false,
  reason: "",
  engaged_at: null,
  paused_bots: {},
  paused_count: 0,
};

test("the live kill-switch response maps to a definite 'off'", async () => {
  httpModule.__set({ kill: LIVE_KILL, failKill: false });
  const state = await killSwitchState();
  assert.equal(state.active, false, "the reported field is global_kill_switch_active");
});

test("an ENGAGED stop is reported as engaged — the case the old lookup could never show", async () => {
  httpModule.__set({
    kill: { ...LIVE_KILL, global_kill_switch_active: true, reason: "operator halt", paused_count: 18 },
    failKill: false,
  });
  const state = await killSwitchState();
  assert.equal(state.active, true, "a green 'running' badge on an engaged stop was the defect");
  assert.equal(state.reason, "operator halt");
  assert.equal(state.paused_count, 18);
});

test("a field the Gateway did not send is unknown, never a green 'off'", async () => {
  httpModule.__set({ kill: { something_else: true }, failKill: false });
  const state = await killSwitchState();
  assert.equal(state.active, null, "'off' and 'unknown' are different claims");
});

test("a failed read REJECTS instead of resolving to 'off' (no false all-clear)", async () => {
  httpModule.__set({ kill: LIVE_KILL, failKill: true });
  await assert.rejects(killSwitchState(), /500/);
});

test("BotOpsSection paints no green badge while the stop state is unknown", () => {
  const src = read("../components/sections/BotOpsSection.tsx");
  // The seed must not be a definite "off" before the first read resolves.
  assert.match(src, /useState<KillSwitchState>\(\{ active: null/, "the initial state must be unknown, not off");
  // The badge tone comes from the tri-state view, never from a bare `?:` that
  // collapses an unknown into the green branch.
  assert.match(src, /tone=\{killView\.tone\}/, "the badge must read the tri-state view");
  assert.match(src, /\{killView\.badge\}/);
  assert.doesNotMatch(src, /<Badge tone=\{kill\.active \? undefined : "green"\}>/, "an unknown stop must never read green");
  assert.match(src, /state unknown/);
  // And an engaged stop is now reachable in the UI at all.
  assert.match(src, /is ENGAGED/);
});

/* ══ F-C: a failed room read is not an empty room ══════════════════════ */

test("groupMessages rejects on a failed read instead of returning []", async () => {
  const stub = `
export async function get(path) { throw new Error("HTTP 404 room not found"); }
export async function send() { return {}; }
export function asList() { return []; }
export function pick(o, k, f) { return f; }
`;
  const url = toDataUrl(stub);
  const teamops = await import(toDataUrl(transpile(read("./teamops.ts")).replace(/from\s+"\.\/http"/, `from "${url}"`)));
  await assert.rejects(() => teamops.groupMessages("no-such-room"), /404/);
  // Same stub against the channels client, so a swapped import cannot make this
  // assertion pass vacuously.
  const ch = await import(toDataUrl(transpile(read("./channels.ts")).replace(/from\s+"\.\/http"/, `from "${url}"`)));
  await assert.rejects(() => ch.channelStatus(), /404/);
});

test("groupMessages has no catch-to-empty anywhere in its body", () => {
  const src = read("./teamops.ts");
  const start = src.indexOf("export async function groupMessages");
  assert.ok(start > 0, "missing groupMessages");
  const body = src.slice(start, src.indexOf("export async function startGroupRun", start));
  const code = body.replace(/\/\/.*$/gm, "");
  assert.doesNotMatch(code, /\bcatch\b/, "a swallowed failure renders as 'No messages yet'");
  assert.doesNotMatch(code, /return \[\]/);
});

test("TeamOpsSection renders a failed room read as a failure, not an empty room", () => {
  const src = read("../components/sections/TeamOpsSection.tsx");
  assert.match(src, /groupMsgError/, "a per-room failure flag is required");
  assert.match(src, /Could not read this room/, "the failure must be visible");
  // "No messages yet" may only be reachable for a loaded, empty list.
  assert.match(src, /loaded\.length === 0/);
  assert.match(src, /loaded == null/);
});

/* ══ F-D: shape 5 on the group auto-run ════════════════════════════════ */

test("the auto-run button is disabled while its run is in flight", () => {
  // POST /groups/{name}/runs mints a fresh run id per request and each run
  // fans out to a subagent per member plus a moderator pass, so a second click
  // duplicates real autonomous work.
  const src = read("../components/sections/TeamOpsSection.tsx");
  assert.match(src, /disabled=\{!groupObjective\.trim\(\) \|\| runningGroup === g\.name\}/);
  assert.match(src, /if \(runningGroup\) return;/, "the handler must refuse a second click before awaiting");
  assert.match(src, /finally \{[\s\S]*setRunningGroup\(null\)/);
});

test("the objective is cleared only after the server accepted the run", () => {
  // Clearing it in the .then kept it non-empty for the whole request window,
  // which is exactly what left the button enabled.
  const src = read("../components/sections/TeamOpsSection.tsx");
  const fn = src.slice(src.indexOf("const autoRun = async"), src.indexOf("const tabs:"));
  assert.ok(fn.indexOf("await startGroupRun(") < fn.indexOf('setGroupObjective("")'), "clear only after the confirmed mutation");
  assert.doesNotMatch(fn, /\.then\(\(\) => setGroupObjective\(""\)\)/, "the draft must not be cleared in a .then");
});
