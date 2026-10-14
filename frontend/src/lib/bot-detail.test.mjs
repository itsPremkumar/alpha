import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

/**
 * Regression coverage for the bot detail client.
 *
 * The page this backs is a *reader*, so the whole risk of it is showing a
 * plausible-looking value the Gateway never sent. These tests pin the two
 * directions that matter:
 *
 *   - absent stays absent (null / "not reported"), never 0 / "" / [] / false
 *   - a value the server did send survives verbatim, including enums this
 *     build has never heard of
 *
 * plus the one distinction the new sections are built around: **a list the
 * Gateway did not send is `null`, and a list it sent empty is `[]`.** Those two
 * payloads drive opposite operator conclusions and must never render alike.
 *
 * LOADER NOTE. `bot-detail.ts` imports `./api-client`, `./bots`,
 * `./bot-working-status` and `./kanban` with extensionless specifiers, so the
 * repo's `data:text/javascript` trick cannot resolve them. It is transpiled
 * through `ts.transpileModule` and loaded with an injected `require` that only
 * satisfies those known dependencies and asserts on anything else — the same
 * pattern as `bots-activity-client.test.mjs`.
 */

const compiled = new Map(
  ["api-client", "bots", "bot-working-status", "kanban", "bot-detail"].map((name) => [
    name,
    ts.transpileModule(readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8"), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText,
  ]),
);

function load(name, dependencies = {}) {
  const exports = {};
  new Function("exports", "require", "process", "console", compiled.get(name))(
    exports,
    (dependency) => {
      assert.ok(Object.hasOwn(dependencies, dependency), `Unexpected dependency: ${dependency}`);
      return dependencies[dependency];
    },
    { env: {} },
    { error: () => {} },
  );
  return exports;
}

// The collaborators are never called by the mapping helpers under test; they
// only have to exist so the module evaluates.
const {
  normalizeInbox,
  normalizeChat,
  normalizePerformance,
  normalizeProjects,
  normalizeGroups,
  normalizeCompanies,
  normalizeEvents,
  normalizeOrgPosition,
  kanbanTasksFor,
  workRunsForBot,
  enumOrUnknown,
} = load("bot-detail", {
  "./api-client": { apiFetch: async () => ({}) },
  "./bots": { normalizeBot: (raw) => raw },
  "./bot-working-status": {
    fetchPauseState: async () => ({ ok: true, value: {} }),
    normalizeWorkReport: (raw) => raw,
    normalizeHealthOverview: (raw) => raw,
    workRunsFor: (report, bot) => report.active_runs_by_bot?.[bot.trim().toLowerCase()] ?? [],
  },
  "./kanban": { listKanbanTasks: async () => [] },
});

/* -------------------------------------------------------------------------- */
/* Inbox                                                                      */
/* -------------------------------------------------------------------------- */

test("normalizeInbox keeps an unreported unread_count null instead of zero", () => {
  const box = normalizeInbox({ messages: [] });
  assert.equal(box.unread_count, null, "an absent unread_count is 'not reported', not '0 unread'");
  assert.deepEqual(box.messages, []);
});

test("normalizeInbox preserves a real unread_count", () => {
  const box = normalizeInbox({ messages: [{ id: "1" }], unread_count: 4 });
  assert.equal(box.unread_count, 4);
  assert.equal(box.messages.length, 1);
});

test("normalizeInbox coerces a wrongly-typed unread_count to null, not 0", () => {
  // `"0"` is a string. Reading it as a number would be a coercion the client
  // invented; reading it as 0 would be worse - it would claim the server
  // measured zero. Absent is the only honest answer.
  const box = normalizeInbox({ messages: [], unread_count: "0" });
  assert.equal(box.unread_count, null);
});

test("normalizeInbox survives a missing messages key", () => {
  const box = normalizeInbox({ unread_count: 1 });
  assert.deepEqual(box.messages, []);
  assert.equal(box.unread_count, 1);
});

/* -------------------------------------------------------------------------- */
/* Chat history                                                               */
/* -------------------------------------------------------------------------- */

test("normalizeChat keeps the canonical thread id when the server sent one", () => {
  const chat = normalizeChat({
    bot_name: "tester",
    canonical_thread_id: "bot-chat-tester",
    unread_count: 2,
    recent: [{ id: "m1" }],
  });
  assert.equal(chat.canonical_thread_id, "bot-chat-tester");
  assert.equal(chat.bot_name, "tester");
  assert.equal(chat.unread_count, 2);
  assert.equal(chat.recent.length, 1);
});

test("normalizeChat maps an omitted canonical id to null, never to a fabricated slug", () => {
  // Deriving `bot-chat-${name}` client-side would present an id the Gateway did
  // not confirm; a 404 on the derived thread would then look like data loss.
  const chat = normalizeChat({ bot_name: "tester" });
  assert.equal(chat.canonical_thread_id, null);
  assert.equal(chat.unread_count, null);
  assert.equal(chat.recent, null, "an absent `recent` is 'no list arrived', not an empty history");
});

test("normalizeChat keeps an explicitly empty recent list distinct from an absent one", () => {
  assert.deepEqual(normalizeChat({ recent: [] }).recent, []);
  assert.equal(normalizeChat({}).recent, null);
});

/* -------------------------------------------------------------------------- */
/* Projects                                                                   */
/* -------------------------------------------------------------------------- */

test("normalizeProjects maps every field the route sends", () => {
  const p = normalizeProjects({
    bot: "tester",
    projects: [
      {
        id: "p1",
        name: "Alpha",
        status: "active",
        role: "lead",
        member_status: "active",
        updated_at: "2026-01-01T00:00:00Z",
      },
    ],
    unreadable: [{ id: "p2", name: "Beta", reason: "crew store corrupt" }],
    memory_scoped: "project",
    memory_note: "Read GET /api/projects/{id}/memory.",
  });
  assert.equal(p.bot, "tester");
  assert.equal(p.projects.length, 1);
  assert.equal(p.projects[0].role, "lead");
  assert.equal(p.projects[0].member_status, "active");
  assert.equal(p.unreadable.length, 1);
  assert.equal(p.unreadable[0].reason, "crew store corrupt");
  assert.equal(p.memory_scoped, "project");
  assert.equal(p.memory_note, "Read GET /api/projects/{id}/memory.");
});

test("normalizeProjects keeps an unread project list as null, not []", () => {
  // `[]` would render "this bot is in no projects" - a claim about the fleet
  // that a Gateway which omitted the key never made.
  const p = normalizeProjects({ bot: "tester" });
  assert.equal(p.projects, null);
  assert.equal(p.unreadable, null);
});

test("normalizeProjects distinguishes a sent-empty list from an absent one", () => {
  assert.deepEqual(normalizeProjects({ projects: [] }).projects, []);
  assert.equal(normalizeProjects({}).projects, null);
});

test("normalizeProjects keeps a missing memory scope null instead of inventing 'project'", () => {
  // Hardcoding the scope would claim the Gateway confirmed it on a build whose
  // route predates the field.
  assert.equal(normalizeProjects({ projects: [] }).memory_scoped, null);
  assert.equal(normalizeProjects({ projects: [] }).memory_note, null);
});

/* -------------------------------------------------------------------------- */
/* Groups                                                                     */
/* -------------------------------------------------------------------------- */

const ROOM = {
  name: "alpha",
  room_id: "r1",
  project_id: null,
  mode: "broadcast",
  lifecycle: "active",
  origins: ["inherited"],
  effective: true,
  direct_count: 1,
  effective_count: 4,
  parent_count: 1,
  path: ["root", "alpha"],
};

test("normalizeGroups keeps origins verbatim so an inherited member is not read as a direct one", () => {
  const g = normalizeGroups({ bot: "tester", rooms: [ROOM], unreadable: [], rooms_checked: 3, note: "n" });
  assert.deepEqual(g.rooms[0].origins, ["inherited"]);
  assert.equal(g.rooms[0].effective, true);
  assert.equal(g.rooms[0].direct_count, 1);
  assert.equal(g.rooms[0].effective_count, 4);
  assert.deepEqual(g.rooms[0].path, ["root", "alpha"]);
  assert.equal(g.rooms_checked, 3);
});

test("normalizeGroups keeps both counts and never folds one into the other", () => {
  const g = normalizeGroups({ rooms: [{ ...ROOM, direct_count: null, effective_count: 7 }] });
  assert.equal(g.rooms[0].direct_count, null, "an unreported direct count is not 0");
  assert.equal(g.rooms[0].effective_count, 7, "the effective count still travels beside it");
});

test("normalizeGroups treats an unread room list as null, not as 'no groups'", () => {
  const g = normalizeGroups({ bot: "tester" });
  assert.equal(g.rooms, null);
  assert.equal(g.unreadable, null);
  assert.equal(g.rooms_checked, null);
  assert.equal(g.note, null);
});

test("normalizeGroups preserves a room whose lifecycle this build has never seen", () => {
  const g = normalizeGroups({ rooms: [{ ...ROOM, lifecycle: "dormant-iii" }] });
  assert.equal(g.rooms[0].lifecycle, "dormant-iii");
  assert.equal(g.rooms[0].mode, "broadcast");
});

test("normalizeGroups coerces a non-boolean effective flag to false rather than to true", () => {
  // `?? false` would turn `"no"` (a truthy string) into "can take part", which
  // is the one reading that must never be guessed.
  const g = normalizeGroups({ rooms: [{ ...ROOM, effective: "no" }] });
  assert.equal(g.rooms[0].effective, false);
});

/* -------------------------------------------------------------------------- */
/* Companies                                                                  */
/* -------------------------------------------------------------------------- */

test("normalizeCompanies keeps a terminated employment instead of dropping it", () => {
  const c = normalizeCompanies({
    bot: "tester",
    companies: [
      {
        company_id: "c1",
        name: "Acme",
        archetype: "startup",
        state: "active",
        created_at: "2026-01-01T00:00:00Z",
        employment: { agent_handle: "tester", status: "terminated", role_id: "r1", unit_id: null },
        role: { id: "r1", name: "Engineer" },
        unit: null,
      },
    ],
    companies_checked: 2,
    note: "n",
  });
  assert.equal(c.companies.length, 1);
  assert.equal(c.companies[0].employment.status, "terminated", "'no longer works here' is not 'never worked here'");
  assert.equal(c.companies[0].role.name, "Engineer");
  assert.equal(c.companies[0].unit, null, "a dangling unit id stays null, never a stub");
  assert.equal(c.companies_checked, 2);
});

test("normalizeCompanies keeps an unread employment list as null, not []", () => {
  const c = normalizeCompanies({ bot: "tester" });
  assert.equal(c.companies, null);
  assert.equal(c.companies_checked, null);
  assert.equal(c.note, null);
});

test("normalizeCompanies preserves an unrecognised company state verbatim", () => {
  const c = normalizeCompanies({
    companies: [{ company_id: "c1", name: "Acme", archetype: "", state: "winding-down", employment: {} }],
  });
  assert.equal(c.companies[0].state, "winding-down");
});

/* -------------------------------------------------------------------------- */
/* Org events                                                                 */
/* -------------------------------------------------------------------------- */

test("normalizeEvents derives `complete` only when both numbers arrived", () => {
  assert.equal(normalizeEvents({ events: [{ a: 1 }, { a: 2 }], count: 2 }).complete, true);
  // A short list under a larger count is real information, not something to
  // smooth over: the page says the list is partial.
  assert.equal(normalizeEvents({ events: [{ a: 1 }], count: 9 }).complete, false);
});

test("normalizeEvents leaves `complete` null when either half is missing", () => {
  // Claiming completeness from a list alone would assert the server counted
  // the same rows.
  assert.equal(normalizeEvents({ events: [{ a: 1 }] }).complete, null);
  assert.equal(normalizeEvents({ events: [] }).complete, null);
  assert.equal(normalizeEvents({}).events, null, "an absent event list is not 'no events'");
  assert.equal(normalizeEvents({}).count, null);
  assert.equal(normalizeEvents({}).complete, null);
});

/* -------------------------------------------------------------------------- */
/* Client-side filters                                                        */
/* -------------------------------------------------------------------------- */

test("kanbanTasksFor matches the assignee exactly and case-insensitively", () => {
  const tasks = [
    { id: "1", assignee: "tester" },
    { id: "2", assignee: "Tester" },
    { id: "3", assignee: "test-engineer" },
    { id: "4", assignee: "" },
    { id: "5" },
  ];
  assert.deepEqual(
    kanbanTasksFor(tasks, "TESTER").map((t) => t.id),
    ["1", "2"],
    "a substring is not an assignee, and an unassigned task is not this bot's",
  );
});

test("kanbanTasksFor refuses to match everything for a blank bot name", () => {
  // `""` lowercased matches `""`, so a naive filter would claim every
  // unassigned task on the board belongs to this bot.
  assert.deepEqual(kanbanTasksFor([{ id: "1", assignee: "" }], "   "), []);
});

test("workRunsForBot returns nothing when the run store could not be read at all", () => {
  // `reported: false` is the Gateway's own refusal. Rendering its (absent)
  // runs as "idle" would be the exact inversion this page exists to stop.
  assert.deepEqual(workRunsForBot({ reported: false, reason: "no run store", active_runs_by_bot: { tester: [{}] } }, "tester"), []);
});

test("workRunsForBot delegates for a readable report", () => {
  const report = { reported: true, reason: null, active_runs_by_bot: { tester: [{ run_id: "r1" }] } };
  assert.equal(workRunsForBot(report, "tester").length, 1);
});

/* -------------------------------------------------------------------------- */
/* Performance                                                                 */
/* -------------------------------------------------------------------------- */

test("normalizePerformance maps the route's real keys, not the plausible ones", () => {
  // `alpha.bots.performance` returns `completed_runs` / `failed_runs` /
  // `avg_duration_seconds`. Reading `completed` / `failed` / `avg_duration_sec`
  // would render three permanently-"not reported" rows over a payload that
  // carried every value — a confident claim the server sent nothing.
  const p = normalizePerformance({
    bot_name: "tester",
    display_name: "Tester",
    role: "QA",
    department: "engineering",
    reputation_score: 0.42,
    reputation_tier: "rising",
    success_rate_percent: 87.5,
    basis: "based on 8 recorded run(s)",
    total_runs: 8,
    completed_runs: 7,
    failed_runs: 1,
    avg_duration_seconds: 12.5,
  });
  assert.equal(p.total_runs, 8);
  assert.equal(p.completed_runs, 7);
  assert.equal(p.failed_runs, 1);
  assert.equal(p.avg_duration_seconds, 12.5);
  assert.equal(p.reputation_score, 0.42);
  assert.equal(p.reputation_tier, "rising");
  assert.equal(p.success_rate_percent, 87.5);
  assert.equal(p.basis, "based on 8 recorded run(s)");
});

test("normalizePerformance keeps the unmeasured zero accompanied by the route's own basis", () => {
  // A bot with no runs sends `total_runs: 0`, `avg_duration_seconds: 0.0` and
  // `basis: "no recorded runs - unverified"`, with `reputation_score` null.
  // The zero is real; only the basis sentence keeps it from reading as a
  // measured idle run history.
  const p = normalizePerformance({
    reputation_score: null,
    reputation_tier: null,
    success_rate_percent: null,
    basis: "no recorded runs - unverified",
    total_runs: 0,
    completed_runs: 0,
    failed_runs: 0,
    avg_duration_seconds: 0,
  });
  assert.equal(p.reputation_score, null, "an unmeasured score is null, never 0 or a flattering 1.0");
  assert.equal(p.success_rate_percent, null);
  assert.equal(p.total_runs, 0, "a measured zero is a real answer");
  assert.equal(p.basis, "no recorded runs - unverified");
});

test("normalizePerformance preserves an unknown reputation tier verbatim", () => {
  const p = normalizePerformance({ reputation_score: 0.5, reputation_tier: "luminary-iv" });
  assert.equal(p.reputation_tier, "luminary-iv");
});

test("normalizePerformance leaves an omitted tier null rather than inventing one", () => {
  const p = normalizePerformance({ reputation_score: 0.5 });
  assert.equal(p.reputation_tier, null);
  assert.equal(p.basis, null);
});

/* -------------------------------------------------------------------------- */
/* Organization chart                                                         */
/* -------------------------------------------------------------------------- */

const CHART = {
  departments: { engineering: [{ name: "tester", role: "QA" }] },
  tree: [
    {
      name: "ceo",
      display_name: "Boss",
      role: "CEO",
      department: "executive",
      status: "active",
      reputation_score: 1,
      subordinates: [
        {
          name: "tester",
          display_name: "Tester",
          role: "QA",
          department: "engineering",
          status: "active",
          reputation_score: 0.4,
          subordinates: [{ name: "junior-qa", display_name: "J", role: "QA", department: "engineering" }],
        },
      ],
    },
  ],
  graph: {
    nodes: [
      { id: "ceo", label: "Boss", role: "CEO", department: "executive", avatar: null, status: "active" },
      { id: "tester", label: "Tester", role: "QA", department: "engineering", avatar: null, status: "active" },
      { id: "junior-qa", label: "J", role: "QA", department: "engineering", avatar: null, status: "active" },
    ],
    edges: [{ from: "ceo", to: "tester", relation: "reports_to" }],
  },
  total_bots: 3,
};

test("normalizeOrgPosition reads the chart's own shape rather than top-level guesses", () => {
  // The chart's top level is `{departments, tree, graph, total_bots}`. There is
  // no top-level `department` or `reports_to`, so reading those would render two
  // permanently-absent rows over a chart that holds the answer nested.
  const o = normalizeOrgPosition(CHART, "tester");
  assert.equal(o.node.department, "engineering");
  assert.equal(o.node.status, "active");
  assert.equal(o.node.label, "Tester");
  assert.equal(o.total_bots, 3);
  assert.equal(o.is_root, false);
});

test("normalizeOrgPosition reports who reports to this bot — the fact no other row carries", () => {
  const o = normalizeOrgPosition(CHART, "tester");
  assert.deepEqual(o.direct_reports, ["junior-qa"]);
});

test("normalizeOrgPosition marks a top-level tree node as a root", () => {
  const o = normalizeOrgPosition(CHART, "ceo");
  assert.equal(o.is_root, true);
  assert.deepEqual(o.direct_reports, ["tester"]);
});

test("normalizeOrgPosition distinguishes 'no subordinates' from 'not reported'", () => {
  // An empty list is the chart's answer that the bot has nobody under it;
  // `null` is a chart that carried no tree entry to answer at all.
  const empty = normalizeOrgPosition(
    { ...CHART, tree: [{ name: "tester", subordinates: [] }] },
    "tester",
  );
  assert.deepEqual(empty.direct_reports, []);

  const noTree = normalizeOrgPosition(
    { graph: CHART.graph, total_bots: 3 },
    "tester",
  );
  assert.equal(noTree.direct_reports, null);
  assert.equal(noTree.is_root, null, "a bot the tree omits is neither a root nor a subordinate");
});

test("normalizeOrgPosition says the chart has no node for a bot it excludes", () => {
  // Retired bots are dropped from the chart, so `node: null` is the chart's
  // answer — not a failed read, and not a row of blanks.
  const o = normalizeOrgPosition(CHART, "archived-bot");
  assert.equal(o.node, null);
  assert.equal(o.is_root, null);
  assert.equal(o.direct_reports, null);
});

test("normalizeOrgPosition matches the bot name case-insensitively", () => {
  const o = normalizeOrgPosition(CHART, "Tester");
  assert.equal(o.node.id, "tester");
});

/* -------------------------------------------------------------------------- */
/* Shared helpers                                                             */
/* -------------------------------------------------------------------------- */

test("enumOrUnknown preserves an unrecognised status verbatim", () => {
  // A newer Gateway may send a status this build has never seen. Displaying it
  // is honest; snapping it to "active" would invent a claim.
  const res = enumOrUnknown("hibernating", ["active", "paused", "disabled"]);
  assert.equal(res.value, "hibernating");
  assert.equal(res.known, false);
});

test("enumOrUnknown marks a known status as known", () => {
  const res = enumOrUnknown("paused", ["active", "paused", "disabled"]);
  assert.equal(res.value, "paused");
  assert.equal(res.known, true);
});

test("enumOrUnknown reports a non-string status as unreported, never as 'active'", () => {
  const res = enumOrUnknown(null, ["active", "paused", "disabled"]);
  assert.equal(res.value, "not reported");
  assert.equal(res.known, false);
});
