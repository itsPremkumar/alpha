// bot-roster-filters.test.mjs — the Bots tab's filter and sort rules.
//
// Why these are pinned
// --------------------
// A filter is a claim about what a list contains. Inlined in JSX the rule
// drifts between three places at once — the predicate itself, the
// "Showing N of M" line, and each filter option's own count — and nothing
// fails; the numbers just stop meaning what the label says. `filterBots` is
// the single predicate `BotGallery` calls, so the rows on screen and the
// disclosure beside them are arithmetically the same by construction.
//
// The two honesty rules this file defends:
//
// 1. **Options come from the roster, never from a hardcoded list.** The
//    Gateway validates `status` against `active|sleeping|suspended|archived`
//    and its registry also writes `disabled`, so the old dropdown offered
//    `active / paused / disabled` — two words the fleet report never uses and
//    no way to reach the ones it does. A word the server reports that this
//    build has never seen must be *offered*, not silently unreachable.
// 2. **A null sorts last, never first and never as zero.** An unmeasured
//    reputation is not a low reputation, and a bot with no `last_active` is
//    not one last active in 1970. `new Date("").getTime()` is 0, so the
//    obvious implementation puts the least-known bots at the top.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const read = (relative) =>
  ts.transpileModule(readFileSync(new URL(relative, import.meta.url), "utf8"), {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  }).outputText;
const dataUrl = (code) =>
  `data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`;

const timeCode = await read("./time.ts");
const apiClientStub = `export async function apiFetch(){ throw new Error("raw transport"); }`;
const botsCode = (await read("./bots.ts")).replace(
  /from\s+"\.\/api-client"/,
  `from "${dataUrl(apiClientStub)}"`,
);
const workingCode = (await read("./bot-working-status.ts"))
  .replace(/from\s+"\.\/api-client"/, `from "${dataUrl(apiClientStub)}"`)
  .replace(/from\s+"\.\/time"/, `from "${dataUrl(timeCode)}"`);
// The filters module imports the status module by specifier, so the stub for
// `./bot-working-status` has to be the compiled real one.
const filtersCode = (await read("./bot-roster-filters.ts"))
  .replace(/from\s+"\.\/bots"/, `from "${dataUrl(botsCode)}"`)
  .replace(/from\s+"\.\/bot-working-status"/, `from "${dataUrl(workingCode)}"`);

const filters = await import(dataUrl(filtersCode));
const working = await import(dataUrl(workingCode));

const {
  DEFAULT_BOT_FILTERS,
  MODEL_NOT_REPORTED,
  STATUS_NOT_REPORTED,
  activeFilterCount,
  activeFilterLabels,
  botMatches,
  filterBots,
  hasUnreportedModel,
  hasUnreportedStatus,
  isDefaultFilters,
  searchHaystack,
  sortBots,
  uniqueModels,
  uniqueStatuses,
} = filters;

/* ── fixtures ─────────────────────────────────────────────────────────────── */

let seq = 0;
const bot = (over = {}) => ({
  name: `bot_${++seq}`,
  display_name: `Bot ${seq}`,
  role: "Engineer",
  department: "engineering",
  status: "active",
  model: "union-alpha",
  reputation_score: null,
  task_stats: {},
  unread_count: null,
  last_active: null,
  capabilities: [],
  skills: [],
  responsibilities: [],
  reports_to: null,
  succession_fallback: null,
  ...over,
});

const criteria = (over = {}) => ({ ...DEFAULT_BOT_FILTERS, ...over });

/* ── the search covers the roster's own text ─────────────────────────────── */

test("search matches the fields an operator actually types", () => {
  const b = bot({
    name: "spec_cuda_kernel_opt_131e8a",
    display_name: "Cuda_Kernel_Opt Specialist",
    role: "Kernel Performance",
    model: "some-new-model",
    reports_to: "cto",
    capabilities: ["cuda", "profiling"],
    skills: ["cuda-tuning"],
    responsibilities: ["Own the kernel budget"],
  });
  for (const q of [
    "cuda",
    "SPEC_CUDA",
    "some-new-model",
    "cto",
    "profiling",
    "cuda-tuning",
    "Kernel Performance",
    "budget",
  ]) {
    assert.ok(botMatches(b, criteria({ search: q })), `${q} should match`);
  }
  assert.ok(!botMatches(b, criteria({ search: "quantum" })));
  assert.equal(
    searchHaystack(b).includes("cuda_kernel_opt_131e8a"),
    true,
    "the name is searchable",
  );
});

test("an empty search is not a filter", () => {
  assert.equal(isDefaultFilters(criteria({ search: "   " })), true);
});

/* ── options are derived from the roster ─────────────────────────────────── */

test("status options are what the roster reports, verbatim and sorted", () => {
  const bots = [
    bot({ status: "active" }),
    bot({ status: "sleeping" }),
    bot({ status: "archived" }),
    bot({ status: "sleeping" }),
    bot({ status: null }),
  ];
  assert.deepEqual(uniqueStatuses(bots), ["active", "archived", "sleeping"]);
  assert.equal(hasUnreportedStatus(bots), true);
  assert.equal(hasUnreportedStatus([bot({ status: "active" })]), false);
  // The unreported bucket and a named state are different populations.
  assert.equal(filterBots(bots, criteria({ status: "sleeping" })).length, 2);
  assert.equal(
    filterBots(bots, criteria({ status: STATUS_NOT_REPORTED })).length,
    1,
  );
  assert.equal(filterBots(bots, criteria({ status: "all" })).length, 5);
  assert.equal(
    filterBots(bots, criteria({ status: "paused" })).length,
    0,
    "a word nobody reports matches nobody",
  );
});

test("model options likewise, and an unreported model is its own facet", () => {
  const bots = [
    bot({ model: "union-alpha" }),
    bot({ model: "b" }),
    bot({ model: undefined }),
    bot({ model: "" }),
  ];
  assert.deepEqual(uniqueModels(bots), ["b", "union-alpha"]);
  assert.equal(hasUnreportedModel(bots), true);
  assert.equal(filterBots(bots, criteria({ model: "b" })).length, 1);
  assert.equal(
    filterBots(bots, criteria({ model: MODEL_NOT_REPORTED })).length,
    2,
    "absent and empty are both unreported",
  );
  assert.equal(filterBots(bots, criteria({ model: "all" })).length, 4);
});

/* ── the working facets ──────────────────────────────────────────────────── */

const verdict = (over) => ({
  key: "idle",
  label: "Idle",
  detail: "",
  tone: "muted",
  working: false,
  evidence: "health",
  ...over,
});

test("working facets are disjoint and cover the population", () => {
  const bots = [
    bot({ name: "a" }),
    bot({ name: "b" }),
    bot({ name: "c" }),
    bot({ name: "d" }),
    bot({ name: "e" }),
  ];
  const verdicts = new Map([
    ["a", verdict({ key: "working", working: true })],
    ["b", verdict({ key: "stalled", working: false, tone: "bad" })],
    ["c", verdict({ key: "idle", working: false })],
    ["d", verdict({ key: "dead", working: false })],
    ["e", verdict({ key: "unclassified", working: null })],
  ]);
  const statusOf = (b) => verdicts.get(b.name);
  const buckets = {
    working: filterBots(bots, criteria({ working: "working" }), statusOf).map(
      (b) => b.name,
    ),
    stalled: filterBots(bots, criteria({ working: "stalled" }), statusOf).map(
      (b) => b.name,
    ),
    "not-working": filterBots(
      bots,
      criteria({ working: "not-working" }),
      statusOf,
    ).map((b) => b.name),
    unknown: filterBots(bots, criteria({ working: "unknown" }), statusOf).map(
      (b) => b.name,
    ),
  };
  assert.deepEqual(
    buckets.working,
    ["a"],
    "a stalled bot must not also read as working",
  );
  assert.deepEqual(
    buckets.stalled,
    ["b"],
    "stalled has its own bucket, so 'not working' is not a dumping ground",
  );
  assert.deepEqual(buckets["not-working"].sort(), ["c", "d"]);
  assert.deepEqual(
    buckets.unknown,
    ["e"],
    "an unread verdict is not a 'not working' verdict",
  );
  // Exhaustive and non-overlapping: five bots, five buckets, no double counting.
  assert.equal(Object.values(buckets).flat().sort().join(","), "a,b,c,d,e");
  assert.equal(
    filterBots(bots, criteria({ working: "all" }), statusOf).length,
    bots.length,
  );
});

test("a bot with no verdict derived for it belongs to the unknown bucket", () => {
  const bots = [bot({ name: "a" }), bot({ name: "b" })];
  const statusOf = (b) =>
    b.name === "a" ? verdict({ key: "working", working: true }) : undefined;
  assert.deepEqual(
    filterBots(bots, criteria({ working: "unknown" }), statusOf).map(
      (b) => b.name,
    ),
    ["b"],
  );
  assert.deepEqual(
    filterBots(bots, criteria({ working: "working" }), statusOf).map(
      (b) => b.name,
    ),
    ["a"],
  );
});

/* ── the inbox and run facets ────────────────────────────────────────────── */

test("the inbox facet separates unmeasured from zero", () => {
  const bots = [
    bot({ unread_count: 3 }),
    bot({ unread_count: 0 }),
    bot({ unread_count: null }),
  ];
  assert.equal(filterBots(bots, criteria({ activity: "unread" })).length, 1);
  assert.equal(filterBots(bots, criteria({ activity: "read" })).length, 1);
  assert.equal(
    filterBots(bots, criteria({ activity: "not-reported" })).length,
    1,
  );
  assert.equal(filterBots(bots, criteria({ activity: "all" })).length, 3);
  assert.equal(
    filterBots([bot({ unread_count: -2 })], criteria({ activity: "unread" }))
      .length,
    0,
    "a negative is not unread",
  );
});

test("the runs facet uses the field names the Gateway sends", () => {
  const bots = [
    bot({ task_stats: { total_runs: 12, completed: 10, failed: 2 } }),
    bot({ task_stats: { total_runs: 0, completed: 0, failed: 0 } }),
    bot({ task_stats: {} }),
  ];
  assert.equal(
    filterBots(bots, criteria({ runs: "measured" })).length,
    2,
    "a measured zero is still measured",
  );
  assert.equal(filterBots(bots, criteria({ runs: "unmeasured" })).length, 1);
  assert.equal(filterBots(bots, criteria({ runs: "failures" })).length, 1);
  assert.equal(
    filterBots(
      [bot({ task_stats: { total_runs: 3, failed: null } })],
      criteria({ runs: "failures" }),
    ).length,
    0,
    "an unreported failure count is not zero failures",
  );
});

/* ── sorting ─────────────────────────────────────────────────────────────── */

test("the default sort is the server's order, untouched", () => {
  const bots = [bot({ name: "z" }), bot({ name: "a" }), bot({ name: "m" })];
  assert.deepEqual(
    sortBots(bots, "server").map((b) => b.name),
    ["z", "a", "m"],
  );
});

test("name sorting is case-insensitive and falls back to the id", () => {
  const bots = [
    bot({ name: "c", display_name: "beta" }),
    bot({ name: "a", display_name: "Alpha" }),
    bot({ name: "b", display_name: "alpha" }),
  ];
  assert.deepEqual(
    sortBots(bots, "name").map((b) => b.name),
    ["a", "b", "c"],
  );
});

test("an unmeasured value sorts last, never first and never as zero", () => {
  const bots = [
    bot({
      name: "mid",
      reputation_score: 0.5,
      last_active: "2026-10-01T00:00:00Z",
      task_stats: { total_runs: 4 },
    }),
    bot({
      name: "none",
      reputation_score: null,
      last_active: null,
      task_stats: {},
    }),
    bot({
      name: "top",
      reputation_score: 0.9,
      last_active: "2026-10-09T00:00:00Z",
      task_stats: { total_runs: 9 },
    }),
    bot({
      name: "zero",
      reputation_score: 0,
      last_active: "2026-10-02T00:00:00Z",
      task_stats: { total_runs: 0 },
    }),
  ];
  assert.deepEqual(
    sortBots(bots, "reputation").map((b) => b.name),
    ["top", "mid", "zero", "none"],
    "a measured 0 ranks above an unmeasured bot — 0 is a score, null is a missing one",
  );
  assert.deepEqual(
    sortBots(bots, "recent").map((b) => b.name),
    ["top", "zero", "mid", "none"],
  );
  assert.deepEqual(
    sortBots(bots, "tasks").map((b) => b.name),
    ["top", "mid", "zero", "none"],
  );
  assert.equal(
    sortBots(bots, "reputation")[3].name,
    "none",
    "the unmeasured bot is last, which is where honesty puts it",
  );
});

test("an unreadable timestamp is not the epoch", () => {
  const bots = [
    bot({ name: "garbage", last_active: "" }),
    bot({ name: "real", last_active: "2020-01-01T00:00:00Z" }),
  ];
  // `new Date("").getTime()` is 0, so a naive implementation ranks `garbage`
  // as the least recent and would still be wrong: it is *unmeasured*.
  assert.deepEqual(
    sortBots(bots, "recent").map((b) => b.name),
    ["real", "garbage"],
  );
});

test("department sorting groups by the reported department", () => {
  const bots = [
    bot({ name: "b", department: "research" }),
    bot({ name: "a", department: "engineering" }),
  ];
  assert.deepEqual(
    sortBots(bots, "department").map((b) => b.name),
    ["a", "b"],
  );
});

/* ── the disclosure line ─────────────────────────────────────────────────── */

test("activeFilterCount counts only the narrowing facets", () => {
  assert.equal(activeFilterCount(DEFAULT_BOT_FILTERS), 0);
  assert.equal(
    activeFilterCount(criteria({ search: "  x  ", working: "stalled" })),
    2,
  );
  assert.equal(
    activeFilterCount(criteria({ sort: "name" })),
    1,
    "a sort changes the view, so it is disclosed",
  );
  assert.equal(
    activeFilterCount(criteria({ department: "all", model: "all" })),
    0,
  );
});

test("the disclosure names each facet with the facet's own value", () => {
  const labels = activeFilterLabels(
    criteria({ search: "cuda", working: "not-working", sort: "recent" }),
  );
  assert.deepEqual(labels, [
    'search "cuda"',
    "working: not working",
    "sorted recent",
  ]);
  assert.deepEqual(
    activeFilterLabels(
      criteria({ status: STATUS_NOT_REPORTED, model: MODEL_NOT_REPORTED }),
    ),
    ["status not reported", "model not reported"],
  );
  assert.deepEqual(
    activeFilterLabels(DEFAULT_BOT_FILTERS),
    [],
    "nothing active means nothing hidden",
  );
});

/* ── the gallery uses one predicate ──────────────────────────────────────── */

test("BotGallery filters and sorts through this module, not inline JSX", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(
    new URL("../components/bots/BotGallery.tsx", import.meta.url),
    "utf8",
  );

  assert.match(
    src,
    /filterBots\(bots, filters, statusOf\)/,
    "one predicate drives the rendered rows",
  );
  assert.match(src, /sortBots\(/, "and one sort");
  assert.match(
    src,
    /activeFilterLabels\(filters\)/,
    "the disclosure is derived from the same criteria",
  );
  assert.match(
    src,
    /activeFilterCount\(filters\)/,
    "and the clear button names how many it clears",
  );
  // The filter options must be derived from the roster, so a state word this
  // build has never seen is reachable instead of unreachable.
  assert.match(src, /uniqueStatuses\(bots\)/);
  assert.match(src, /uniqueModels\(bots\)/);
  assert.doesNotMatch(
    src,
    /<option value="paused">/,
    "the hardcoded status list must not come back",
  );
});
