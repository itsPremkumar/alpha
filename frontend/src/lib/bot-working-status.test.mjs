// bot-working-status.test.mjs — "is this bot working right now?", honestly.
//
// The defect
// ----------
// The Bots tab answered that question from `isRecent(last_active, 90)` — a
// registry timestamp — while `alpha.bots.health` already owned the real verdict
// (healthy / stale / stalled / dead / sleeping / suspended / archived) and
// published it on `GET /api/bots/health/overview` with nothing rendering it.
// The two readings disagree exactly where it matters: a bot whose task lease
// expired mid-run is *stalled* and holds work that will not finish, and the
// card that said "Working" a second ago is all an operator could see.
//
// What is pinned here
// --------------------
// 1. The reads, their paths, and that a failure rejects instead of resolving to
//    an empty overview with `total: 0`.
// 2. Every absent field becoming `null`, never `0`/`false`/`""` — including the
//    summary counters, where "the server did not report stalled" and "the
//    engine found none" are opposite facts.
// 3. Every liveness state, plus the two states a newer Gateway can produce:
//    a word this build does not classify (rendered verbatim) and a `null`
//    liveness (no verdict at all).
// 4. An operator pause outranking the monitor, because the kill switch is the
//    strongest statement anyone has made about a bot.
// 5. The presence fallback labelling itself, so it cannot be mistaken for the
//    monitor's answer.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const read = (relative) =>
  readFileSync(new URL(relative, import.meta.url), "utf8");
const transpile = (source, extra = {}) =>
  ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
      ...extra,
    },
  }).outputText;
const dataUrl = (code) =>
  `data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`;
const load = (code) => import(dataUrl(code));

/* ── the real modules, with only the transport stubbed ─────────────────────── */

globalThis.__botApiCalls = [];
globalThis.__botApiFailure = null;
globalThis.__botApiResponses = {};

const apiClientStub = `
export async function apiFetch(path) {
  globalThis.__botApiCalls.push(path);
  if (globalThis.__botApiFailure) throw new Error(globalThis.__botApiFailure);
  const body = globalThis.__botApiResponses[path];
  return { ok: true, status: 200, json: async () => body };
}
`;

const timeCode = transpile(read("./time.ts"));
const statusCode = transpile(read("./bot-working-status.ts"))
  .replace(/from\s+"\.\/api-client"/, `from "${dataUrl(apiClientStub)}"`)
  .replace(/from\s+"\.\/time"/, `from "${dataUrl(timeCode)}"`);

const status = await load(statusCode);
const { PRESENCE_WINDOW_SECONDS } = await load(timeCode);

const okOverview = {
  timestamp: "2026-10-09T10:00:00+00:00",
  summary: {
    total: 57,
    healthy: 12,
    stale: 3,
    stalled: 2,
    dead: 1,
    sleeping: 5,
    suspended: 1,
    archived: 1,
  },
  fleet_health_score: 0.35,
  bots: [],
  stalled_workers: [],
};

const withResponses = (responses) => {
  globalThis.__botApiCalls = [];
  globalThis.__botApiFailure = null;
  globalThis.__botApiResponses = {
    "/bots/health/overview": okOverview,
    "/bots/kill-switch": {},
    ...responses,
  };
};

/* ── 1. The reads ─────────────────────────────────────────────────────────── */

test("the two reads are the Gateway's own routes, and nothing else is called", async () => {
  withResponses({});
  await status.fetchBotHealthOverview();
  await status.fetchPauseState();
  assert.deepEqual(
    [...globalThis.__botApiCalls],
    ["/bots/health/overview", "/bots/kill-switch"],
  );
});

test("a failed liveness read rejects with the server's reason, not an empty fleet", async () => {
  withResponses({});
  globalThis.__botApiFailure = "Gateway health read failed: 503";
  const result = await status.fetchBotHealthOverview();
  assert.equal(result.ok, false);
  assert.match(result.error, /503/);
  // The caller must not be handed a shape whose summary reads as a fleet with
  // no healthy bots — that is the whole point of the ReadResult.
  assert.equal(result.value, undefined);
});

test("a failed pause read rejects rather than answering 'nobody is paused'", async () => {
  withResponses({});
  globalThis.__botApiFailure = "kill switch unreadable";
  const result = await status.fetchPauseState();
  assert.equal(result.ok, false);
  assert.equal(result.value, undefined);
});

test("the client never calls fetch directly", () => {
  assert.doesNotMatch(
    read("./bot-working-status.ts"),
    /\bfetch\(/,
    "the shared client owns transport",
  );
});

/* ── 2. Absent is null, never zero ────────────────────────────────────────── */

test("a summary block that omits a state reads null, not 0", () => {
  const normalized = status.normalizeHealthOverview({
    timestamp: "2026-10-09T10:00:00+00:00",
    summary: { total: 57, healthy: 12 },
    bots: [],
  });
  assert.equal(normalized.summary.total, 57);
  assert.equal(normalized.summary.healthy, 12);
  // "The server did not report stalled" is not "the engine found none".
  assert.equal(normalized.summary.stalled, null);
  assert.equal(normalized.summary.dead, null);
  assert.equal(normalized.fleet_health_score, null);
  assert.equal(normalized.timestamp, "2026-10-09T10:00:00+00:00");
});

test("no summary at all leaves every counter null", () => {
  const normalized = status.normalizeHealthOverview({});
  for (const key of [
    "total",
    "healthy",
    "stale",
    "stalled",
    "dead",
    "sleeping",
    "suspended",
    "archived",
  ]) {
    assert.equal(normalized.summary[key], null, `${key} must stay null`);
  }
  assert.deepEqual(normalized.bots, []);
  assert.deepEqual(normalized.stalled_workers, []);
});

test("a non-finite or wrongly typed value is not a measurement", () => {
  const normalized = status.normalizeHealthOverview({
    summary: { total: "57", healthy: NaN, stalled: Infinity, dead: 3 },
    fleet_health_score: "0.4",
  });
  assert.equal(normalized.summary.total, null, "a string is not a count");
  assert.equal(normalized.summary.healthy, null);
  assert.equal(normalized.summary.stalled, null);
  assert.equal(normalized.summary.dead, 3);
  assert.equal(normalized.fleet_health_score, null);
});

test("row mapping keeps nulls null and drops non-objects", () => {
  const normalized = status.normalizeHealthOverview({
    // A newest-Gateway liveness word must survive verbatim.
    bots: [
      {
        bot_name: "QA_Lead",
        liveness: "quarantined",
        seconds_since_heartbeat: null,
      },
      null,
      7,
      "x",
    ],
    stalled_workers: [
      { bot_name: "coder", liveness: "stalled", active_task_id: "run_1" },
    ],
  });
  assert.equal(
    normalized.bots.length,
    1,
    "non-object rows are dropped rather than guessed at",
  );
  assert.equal(normalized.bots[0].bot_name, "QA_Lead");
  assert.equal(normalized.bots[0].liveness, "quarantined");
  assert.equal(normalized.bots[0].seconds_since_heartbeat, null);
  assert.equal(normalized.bots[0].is_responsive, null);
  assert.equal(normalized.stalled_workers.length, 1);
  assert.equal(normalized.stalled_workers[0].active_task_id, "run_1");
});

/* ── 3. The verdict table ────────────────────────────────────────────────── */

const row = (over = {}) => ({
  bot_name: "coder",
  status: "active",
  liveness: "healthy",
  is_responsive: true,
  active_task_id: null,
  last_heartbeat: "2026-10-09T10:00:00+00:00",
  seconds_since_heartbeat: 12.4,
  heartbeat_parse_error: false,
  lease_expired: false,
  ...over,
});

const BOT = { name: "coder", last_active: "2026-10-09T09:00:00+00:00" };

test("healthy with a task in hand is working, and the task is named", () => {
  const s = status.workingStatusFor(BOT, row({ active_task_id: "run_42" }));
  assert.equal(s.key, "working");
  assert.equal(s.working, true);
  assert.equal(s.evidence, "health");
  assert.match(s.detail, /run_42/);
  assert.match(s.detail, /heartbeat 12s ago/);
});

test("healthy with no task is idle — reachability is not work", () => {
  const s = status.workingStatusFor(BOT, row());
  assert.equal(s.key, "idle");
  assert.equal(s.working, false);
  assert.match(s.detail, /no task reported/);
});

test("a stale heartbeat with a task is still work, with its age stated", () => {
  const s = status.workingStatusFor(
    BOT,
    row({
      liveness: "stale",
      seconds_since_heartbeat: 120,
      active_task_id: "run_7",
    }),
  );
  assert.equal(s.working, true);
  assert.equal(s.tone, "warn", "the age must qualify the claim");
  assert.match(s.detail, /run_7/);
  assert.match(
    s.detail,
    /124s ago|120s ago/,
    "the measured age is quoted, not a threshold word",
  );
});

test("stalled is the state that must never read as working", () => {
  const s = status.workingStatusFor(
    BOT,
    row({
      liveness: "stalled",
      lease_expired: true,
      is_responsive: false,
      active_task_id: "run_9",
    }),
  );
  assert.equal(s.key, "stalled");
  assert.equal(s.working, false);
  assert.equal(s.tone, "bad");
  assert.match(s.detail, /run_9/);
  assert.match(s.detail, /lease has expired/);
});

test("dead reads as dead, with no invented elapsed number", () => {
  const s = status.workingStatusFor(
    BOT,
    row({
      liveness: "dead",
      is_responsive: false,
      seconds_since_heartbeat: 420,
    }),
  );
  assert.equal(s.key, "dead");
  assert.equal(s.working, false);
  assert.match(s.detail, /heartbeat 420s ago/);
});

test("an unparseable heartbeat says so instead of a sentinel age", () => {
  const s = status.workingStatusFor(
    BOT,
    row({
      liveness: "dead",
      seconds_since_heartbeat: null,
      heartbeat_parse_error: true,
    }),
  );
  assert.equal(s.key, "dead");
  assert.match(s.detail, /heartbeat timestamp unreadable/);
  assert.doesNotMatch(
    s.detail,
    /\d{4,}/,
    "no 999999-style sentinel may appear",
  );
});

test("sleeping and the out-of-service states are not working, and say why", () => {
  assert.equal(
    status.workingStatusFor(
      BOT,
      row({ liveness: "sleeping", active_task_id: null }),
    ).key,
    "sleeping",
  );
  assert.equal(
    status.workingStatusFor(BOT, row({ liveness: "sleeping" })).working,
    false,
  );
  assert.equal(
    status.workingStatusFor(BOT, row({ liveness: "suspended" })).key,
    "suspended",
  );
  assert.equal(
    status.workingStatusFor(BOT, row({ liveness: "archived" })).key,
    "archived",
  );
  assert.equal(
    status.workingStatusFor(BOT, row({ liveness: "sandboxed" })).key,
    "unclassified",
  );
});

test("a liveness word from a newer Gateway is rendered verbatim, never snapped", () => {
  const s = status.workingStatusFor(BOT, row({ liveness: "quarantined" }));
  assert.equal(s.key, "unclassified");
  assert.equal(
    s.label,
    "quarantined",
    "the badge carries the server's own word",
  );
  assert.equal(
    s.working,
    null,
    "an unreadable verdict is not a 'not working' verdict",
  );
  assert.match(s.detail, /"quarantined"/);
});

test("no liveness field at all is unknown, not idle", () => {
  const s = status.workingStatusFor(BOT, row({ liveness: null }));
  assert.equal(s.key, "unknown");
  assert.equal(s.working, null);
  assert.equal(s.evidence, "health");
});

test("responsiveness the server did not report is a word, not a boolean", () => {
  const s = status.workingStatusFor(BOT, row({ is_responsive: null }));
  assert.match(s.detail, /responsiveness not reported/);
});

test("the heartbeat words quote the engine's own measurement", () => {
  assert.equal(
    status.heartbeatWords(row({ seconds_since_heartbeat: 61.4 })),
    "heartbeat 61s ago",
  );
  assert.equal(
    status.heartbeatWords(
      row({ seconds_since_heartbeat: null, heartbeat_parse_error: true }),
    ),
    "heartbeat timestamp unreadable",
  );
  assert.equal(
    status.heartbeatWords(
      row({
        seconds_since_heartbeat: null,
        heartbeat_parse_error: false,
        last_heartbeat: null,
      }),
    ),
    "no heartbeat recorded",
  );
});

/* ── 4. Presence fallback ────────────────────────────────────────────────── */

test("with no health row, a recent activity stamp is a labelled presence reading", () => {
  const s = status.workingStatusFor(
    { name: "coder", last_active: new Date(Date.now() - 20_000).toISOString() },
    null,
  );
  assert.equal(s.key, "working");
  assert.equal(s.working, true);
  assert.equal(s.evidence, "presence");
  assert.match(
    s.detail,
    /presence only/,
    "the fallback must not borrow the monitor's authority",
  );
});

test("an old activity stamp reads idle, still labelled as presence", () => {
  const s = status.workingStatusFor(
    {
      name: "coder",
      last_active: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
    },
    null,
  );
  assert.equal(s.key, "idle");
  assert.equal(s.evidence, "presence");
  assert.match(s.detail, /3h ago/);
});

test("an unreadable timestamp with no report is unknown, not 'idle since 1970'", () => {
  const s = status.workingStatusFor(
    { name: "coder", last_active: "not-a-time" },
    null,
  );
  assert.equal(s.key, "unknown");
  assert.equal(s.working, null);
  assert.equal(s.evidence, "none");
  assert.match(s.detail, /nothing was measured/);
});

/* ── 5. Operator pause wins ──────────────────────────────────────────────── */

test("a paused bot is not working, whatever the monitor says", () => {
  const s = status.workingStatusFor(BOT, row({ active_task_id: "run_1" }), {
    pausedReason: "Bot paused: suspicious spend",
  });
  assert.equal(s.key, "paused");
  assert.equal(s.working, false);
  assert.equal(s.evidence, "pause");
  assert.match(
    s.detail,
    /suspicious spend/,
    "the server's own reason is quoted verbatim",
  );
});

test("an absent pausedReason leaves the monitor in charge", () => {
  assert.equal(
    status.workingStatusFor(BOT, row(), { pausedReason: null }).key,
    "idle",
  );
  assert.equal(
    status.workingStatusFor(BOT, row({ active_task_id: "r" })).key,
    "working",
  );
});

/* ── 6. Kill-switch projection ───────────────────────────────────────────── */

test("the pause reader keeps an absent field unreported rather than off", async () => {
  withResponses({});
  const result = await status.fetchPauseState();
  assert.equal(result.ok, true);
  assert.equal(
    result.value.active,
    null,
    "no flag means not reported, not 'off'",
  );
  assert.equal(result.value.paused, null);
  assert.equal(result.value.reason, null);
});

test("paused bots are keyed case-insensitively with the server's reason", async () => {
  withResponses({
    "/bots/kill-switch": {
      global_kill_switch_active: true,
      reason: "Emergency stop",
      paused_bots: { Coder: { reason: "Bot paused: under review" } },
    },
  });
  const result = await status.fetchPauseState();
  assert.equal(result.value.active, true);
  assert.equal(result.value.reason, "Emergency stop");
  assert.deepEqual(result.value.paused, { coder: "Bot paused: under review" });
});

test("a paused row with no reason still says it is paused", async () => {
  withResponses({ "/bots/kill-switch": { paused_bots: { coder: {} } } });
  const result = await status.fetchPauseState();
  assert.equal(result.value.paused.coder, "paused by an operator");
});

test("the row lookup folds case, so a mixed-case roster name finds its row", () => {
  const index = status.healthRowIndex([
    row({ bot_name: "QA_Lead" }),
    row({ bot_name: "coder", liveness: "dead" }),
  ]);
  assert.equal(index.get("qa_lead").liveness, "healthy");
  assert.equal(status.healthRowFor(index, "coder").liveness, "dead");
  // The lookup, not the stored key, does the folding: a caller passing the
  // roster's own mixed-case name must still hit the engine's lowercased row.
  assert.equal(status.healthRowFor(index, "Coder").liveness, "dead");
  assert.equal(status.healthRowFor(index, "coder ").liveness, "dead");
  assert.equal(
    status.healthRowFor(index, "nobody"),
    null,
    "an absent row is null, not a fabricated one",
  );
});

test("a row with no name is not indexed", () => {
  const index = status.healthRowIndex([row({ bot_name: null })]);
  assert.equal(index.size, 0);
});

test("a duplicated row cannot flip a verdict: the first one wins", () => {
  const index = status.healthRowIndex([
    row({ liveness: "healthy" }),
    row({ liveness: "dead" }),
  ]);
  assert.equal(index.get("coder").liveness, "healthy");
});

/* ── 7. The facet buckets are disjoint ───────────────────────────────────── */

test("every verdict lands in exactly one facet, and stalled is not 'not working'", () => {
  const cases = [
    [status.workingStatusFor(BOT, row({ active_task_id: "r" })), "working"],
    [status.workingStatusFor(BOT, row()), "not-working"],
    [
      status.workingStatusFor(BOT, row({ liveness: "sleeping" })),
      "not-working",
    ],
    [
      status.workingStatusFor(BOT, row({ liveness: "suspended" })),
      "not-working",
    ],
    [
      status.workingStatusFor(
        BOT,
        row({ liveness: "stalled", lease_expired: true }),
      ),
      "stalled",
    ],
    [status.workingStatusFor(BOT, row({ liveness: "dead" })), "not-working"],
    [
      status.workingStatusFor(BOT, row({ liveness: "unknown_word" })),
      "unknown",
    ],
    [status.workingStatusFor(BOT, row({ liveness: null })), "unknown"],
  ];
  for (const [verdict, expected] of cases) {
    assert.equal(
      status.workingFacetOf(verdict),
      expected,
      `${verdict.key} must be ${expected}`,
    );
  }
});

test("needsAttention is the population a human has to act on", () => {
  const keys = (over) =>
    status.needsAttention(
      status.workingStatusFor(
        BOT,
        row(over),
        over.pausedReason ? { pausedReason: over.pausedReason } : {},
      ),
    );
  assert.equal(
    keys({ liveness: "stalled", lease_expired: true }),
    true,
    "stuck work is always actionable",
  );
  assert.equal(
    keys({ active_task_id: "r", pausedReason: "Bot paused: under review" }),
    true,
    "an operator's stop outranks the monitor",
  );
  assert.equal(
    keys({ liveness: "dead" }),
    false,
    "a resting fleet reads dead; escalating it would make the notice permanent and ignored",
  );
  assert.equal(
    keys({ liveness: "dead", active_task_id: "run_9" }),
    false,
    "a dead bot holding a task is said so on its own card detail, not escalated here",
  );
  assert.equal(
    keys({ liveness: "healthy", active_task_id: "r" }),
    false,
    "a working bot is not an emergency",
  );
  assert.equal(keys({ liveness: "healthy" }), false);
  assert.equal(keys({ liveness: "sleeping" }), false);
});

test("a dead bot holding a task says the task may be stuck", () => {
  const s = status.workingStatusFor(
    BOT,
    row({
      liveness: "dead",
      active_task_id: "run_9",
      seconds_since_heartbeat: 900,
    }),
  );
  assert.match(
    s.detail,
    /run_9 may be stuck/,
    "the engine's recovery queue keys on exactly this pair",
  );
});

/* ── 8. The gallery wires it, and the card renders it ────────────────────── */

test("BotGallery shares one derived verdict between cards and filters", async () => {
  const src = read("../components/bots/BotGallery.tsx");
  assert.match(
    src,
    /workingStatusFor\(/,
    "the status must be derived from the lib, not inlined in JSX",
  );
  assert.match(
    src,
    /working=\{workingByBot\.get\(bot\.name\)\}/,
    "the card receives the shared verdict",
  );
  assert.match(
    src,
    /healthRowIndex\(/,
    "rows are matched by the name key the engine writes",
  );
  assert.match(
    src,
    /healthRowFor\(/,
    "and the lookup folds that key rather than trusting the caller",
  );
  // The two reads fail independently — one broken read must not blank the other.
  assert.match(src, /fetchBotHealthOverview\(\)/);
  assert.match(src, /fetchPauseState\(\)/);
  assert.match(
    src,
    /fetchBotHealthOverview\([\s\S]{0,400}?fetchPauseState\(/,
    "they are issued separately",
  );
});

test("a failed roster read is disclosed instead of reading as an empty fleet", () => {
  const src = read("../components/bots/BotGallery.tsx");
  assert.match(
    src,
    /loadError\?:\s*string \| null/,
    "the gallery can be told why the roster read failed",
  );
  assert.match(
    src,
    /loadError && \(/,
    "and renders the failure rather than absorbing it",
  );
  const chat = read("../components/ChatView.tsx");
  assert.match(
    chat,
    /loadError=\{botsError\}/,
    "ChatView passes the roster read's own reason",
  );
  // In the empty grid the failure sentence has to WIN the ternary: the server
  // reporting no bots is still the right sentence when it genuinely did.
  assert.ok(
    src.indexOf("No bots can be shown — the roster read failed") <
      src.indexOf("The Gateway reported no bots."),
    "the roster read failure is stated before the honest empty-fleet sentence",
  );
});

test("the card renders the shared badge and its detail line", () => {
  const card = read("../components/bots/BotProfileCard.tsx");
  assert.match(card, /WorkingStatusBadge status=\{working\}/);
  assert.match(card, /<WorkingStatusDetail status=\{working\} \/>/);
  // The presence dot stays a presence reading: the two are allowed to differ.
  assert.match(card, /presentNow /);
});

test("a card with no verdict passed in shows no working claim", () => {
  const view = read("../components/bots/WorkingStatusView.tsx");
  assert.match(view, /if \(!status\) return null;/);
  assert.match(view, /data-working-key=\{status\.key\}/);
  assert.match(
    view,
    /data-working-detail=\{status\.key\}/,
    "the detail line carries its key for the suite",
  );
});
