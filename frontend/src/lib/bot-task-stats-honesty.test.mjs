// bot-task-stats-honesty.test.mjs — a counter the Gateway never sends.
//
// The fleet bar, the bot card, the bot detail panel and the bot picker all read
// `task_stats.total` and `task_stats.succeeded`. The Gateway sends no such
// fields. Measured against a live `GET /api/bots` on this repo:
//
//   "task_stats": {"completed":0,"failed":0,"total_runs":0,"avg_duration_sec":0.0}
//
// So `task_stats.total` was `undefined` for every bot, and every
// `Number(bot.task_stats?.total) || 0` collapsed to 0. The rendered result was
// "0 Tasks done" on the fleet bar and "0 tasks" on all 18 cards, permanently and
// regardless of how much work the fleet had actually run — the one number an
// operator reads to answer "did any of this actually happen?".
//
// Note the contrast one line above the bug in lib/bots.ts: `avg_reputation`
// already refused to invent a number, and FleetHealthBar already rendered
// "unverified — none measured". The adjacent tile failed to do the same.
//
// These tests import the real modules (and render the real components through
// react-dom/server) so they fail if the mapping regresses.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";
import { moduleUrl } from "./test-modules.mjs";

const require = createRequire(import.meta.url);
const resolveUrl = (specifier) => require("node:url").pathToFileURL(require.resolve(specifier)).href;
const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const transpile = (source, extra = {}) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, ...extra },
  }).outputText;
const load = (code) => import(`data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`);

/* ── the real client, with only the transport stubbed ─────────────────────── */

const apiClientStub = `
export class ApiClientError extends Error {
  constructor(kind, status, detail) { super(detail || "Request failed"); this.name = "ApiClientError"; this.kind = kind; this.status = status; }
}
export async function apiFetch() { return { ok: true, status: 200, json: async () => ({}) }; }
`;

let botsCode = transpile(read("./bots.ts"));
botsCode = botsCode
  .replace(/from\s+"\.\/api-client"/, `from "${await (async () => `data:text/javascript;charset=utf-8,${encodeURIComponent(apiClientStub)}`)()}"`)
  .replace(/from\s+"@\/types\/bots"/, `from "${await (async () => `data:text/javascript;charset=utf-8,${encodeURIComponent(read("../types/bots.ts").split("export function")[0])}`)()}"`);
const { computeFleetHealth, totalRuns, completedRuns, failedRuns } = await load(botsCode);

/** A bot row shaped exactly like the live `GET /api/bots` response. */
const liveBot = (taskStats) => ({ name: "architect", status: "active", reputation_score: null, task_stats: taskStats });

// Verbatim task_stats from the live Gateway.
const LIVE_TASK_STATS = { completed: 0, failed: 0, total_runs: 0, avg_duration_sec: 0.0 };

/* ══ 1. The measured field is read, not a field that does not exist ══════ */

test("totalRuns reads total_runs, the field the Gateway actually sends", () => {
  assert.equal(totalRuns(liveBot({ total_runs: 7 })), 7);
});

test("the old field names are not silently read as a fallback", () => {
  // `total`/`succeeded` were the bug. Even if some row carried them, the client
  // must not treat an unknown key as the measured total.
  assert.equal(totalRuns(liveBot({ total: 9 })), null);
  assert.equal(completedRuns(liveBot({ succeeded: 9 })), null);
});

test("completedRuns and failedRuns read the real keys", () => {
  assert.equal(completedRuns(liveBot({ completed: 4 })), 4);
  assert.equal(failedRuns(liveBot({ failed: 2 })), 2);
});

/* ══ 2. A reported zero stays zero; an unreported counter is null ════════ */

test("a measured zero is preserved as zero, not confused with unmeasured", () => {
  assert.equal(totalRuns(liveBot(LIVE_TASK_STATS)), 0);
  assert.equal(totalRuns(liveBot({})), null);
  assert.equal(totalRuns(liveBot({ total_runs: null })), null);
  assert.equal(totalRuns(liveBot({ total_runs: "3" })), null, "a string is not a measurement");
  assert.equal(totalRuns(liveBot({ total_runs: NaN })), null, "NaN is not a measurement");
  assert.equal(totalRuns(liveBot({ total_runs: Infinity })), null);
});

/* ══ 3. The fleet aggregate ════════════════════════════════════════════ */

test("the fleet total sums MEASURED runs, so real work is no longer reported as zero", () => {
  const health = computeFleetHealth([
    liveBot({ total_runs: 3, completed: 2, failed: 1 }),
    liveBot({ total_runs: 4, completed: 4, failed: 0 }),
  ]);
  assert.equal(health.total_tasks, 7, "3+4 measured runs must not render as 0");
});

test("the fleet total is null — never 0 — when no bot reported a counter", () => {
  const health = computeFleetHealth([liveBot({}), liveBot({}), liveBot({ total_runs: null })]);
  assert.equal(health.total_tasks, null, "claiming 0 tasks done would state the server measured no work");
});

test("a partially-measured fleet is null rather than a misleading partial sum", () => {
  // Summing only the rows that answered would count 3 and silently drop the
  // other bot, which reads as "the fleet ran 3 tasks" — a measurement nobody
  // made. An incomplete count must not masquerade as a complete one.
  const health = computeFleetHealth([liveBot({ total_runs: 3 }), liveBot({})]);
  assert.equal(health.total_tasks, null);
});

test("an empty fleet reports no tasks rather than a fabricated zero", () => {
  assert.equal(computeFleetHealth([]).total_tasks, null);
});

test("the live all-zero response still reads as a measured zero", () => {
  // Every bot on a default install has total_runs: 0. That IS a measurement, so
  // the honest rendering is "0" — the fix must not turn a real zero into
  // "not measured", which would be a different kind of lie.
  const health = computeFleetHealth([liveBot(LIVE_TASK_STATS), liveBot(LIVE_TASK_STATS)]);
  assert.equal(health.total_tasks, 0);
});

test("reputation and task counters stay independent", () => {
  const health = computeFleetHealth([
    { name: "a", status: "active", reputation_score: 0.9, task_stats: { total_runs: 2 } },
    { name: "b", status: "active", reputation_score: null, task_stats: { total_runs: 0 } },
  ]);
  assert.equal(health.avg_reputation, 0.9);
  assert.equal(health.total_tasks, 2);
});

/* ══ 4. The rendered markup ════════════════════════════════════════════ */

const renderBar = async (health) => {
  const code = transpile(read("../components/bots/FleetHealthBar.tsx"), { jsx: ts.JsxEmit.ReactJSX })
    .replace(/from\s+"react"/, `from "${resolveUrl("react")}"`)
    .replace(/from\s+"react\/jsx-runtime"/, `from "${resolveUrl("react/jsx-runtime")}"`)
    .replace(/from\s+"lucide-react"/, `from "${await (async () => {
      const { pathToFileURL } = require("node:url");
      const { fileURLToPath } = require("node:url");
      return pathToFileURL(fileURLToPath(new URL("../../node_modules/lucide-react/dist/esm/lucide-react.js", import.meta.url))).href;
    })()}"`);
  const mod = await load(code);
  const { createElement } = await import(resolveUrl("react"));
  const { renderToStaticMarkup } = await import(resolveUrl("react-dom/server"));
  return renderToStaticMarkup(createElement(mod.FleetHealthBar, { health }));
};

test("the fleet bar shows the measured total, not a hard-coded 0", async () => {
  const markup = await renderBar(computeFleetHealth([liveBot({ total_runs: 12, completed: 10 })]));
  assert.match(markup, /Tasks done/);
  assert.match(markup, />12</, "a measured 12 must render as 12");
});

test("the fleet bar discloses an unreported counter instead of printing 0", async () => {
  const markup = await renderBar(computeFleetHealth([liveBot({})]));
  assert.match(markup, /not measured/);
  assert.doesNotMatch(markup, />0\s*Tasks done/, "0 Tasks done would claim the server measured no work");
});

/* ══ 5. No `|| 0` coercion survives on these counters ═══════════════════ */

test("no component or client coerces a task counter to zero with ||", () => {
  for (const file of [
    "./bots.ts",
    "../components/bots/BotProfileCard.tsx",
    "../components/bots/BotDetailPanel.tsx",
    "../components/bots/ActiveBotPicker.tsx",
  ]) {
    const src = read(file);
    assert.doesNotMatch(src, /task_stats\?\.(total|succeeded)\b/, `${file} still reads a field the Gateway never sends`);
    assert.doesNotMatch(src, /task_stats\.[a-z_]+\s*\|\|\s*0/, `${file} still coerces a counter to 0`);
  }
});

test("the declared type matches the Gateway's field names", () => {
  const src = read("../types/bots.ts");
  assert.match(src, /total_runs\?/);
  assert.match(src, /completed\?/);
  assert.doesNotMatch(src, /^\s*total\?:/m, "BotTaskStats must not declare the non-existent `total`");
  assert.doesNotMatch(src, /^\s*succeeded\?:/m, "BotTaskStats must not declare the non-existent `succeeded`");
});
