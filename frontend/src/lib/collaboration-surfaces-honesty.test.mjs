// collaboration-surfaces-honesty.test.mjs — the Messaging / Team-Ops / Swarms
// / Subagents / Kanban surfaces, measured.
//
// Every assertion here is driven by the REAL transpiled module or the REAL
// component. Nothing is read out of the source text to decide whether a defect
// exists; the source pins at the bottom only guard the wiring that the measured
// behaviour depends on.
//
// The defects were found by driving the live client against payloads shaped
// exactly like the Gateway's real responses and reading what came out. Each
// test names the number or the string that was actually observed.
//
//   node --test src/lib/collaboration-surfaces-honesty.test.mjs
//
// No server, no browser, no DOM: components are rendered with react-dom/server
// and clients run against a stubbed ./http.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const resolveUrl = (spec) => pathToFileURL(require.resolve(spec)).href;
const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");
const transpile = (src, extra = {}) =>
  ts.transpileModule(src, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
      ...extra,
    },
  }).outputText;
// base64, never percent-encoded: these URLs nest, and a percent-encoded inner
// `data:` prefix becomes `data%3A`, which is not a resolvable specifier.
const toUrl = (code) =>
  `data:text/javascript;base64,${Buffer.from(code, "utf8").toString("base64")}`;
const load = (code) => import(toUrl(code));

/* ── The shared transport stub ───────────────────────────────────────────── */

const httpStubUrl = toUrl(`
  let handler = () => { throw new Error("no stub response configured"); };
  export function setHttpHandler(fn) { handler = fn; }
  export async function get(path) { return handler(path, "GET", undefined); }
  export async function send(path, method, payload) { return handler(path, method, payload); }
  export function pick(obj, keys, fallback) {
    if (obj && typeof obj === "object") { for (const k of keys) if (obj[k] !== undefined && obj[k] !== null) return obj[k]; }
    return fallback;
  }
  export function asList(body, keys) {
    if (Array.isArray(body)) return body;
    for (const k of keys) if (body && typeof body === "object" && Array.isArray(body[k])) return body[k];
    return [];
  }
  // Every section imports this from "@/lib/http"; ESM validates named imports
  // at link time, so a missing export fails the whole file for a reason that
  // has nothing to do with any assertion. Same shim the real module uses.
  export function errMsg(err) { return err instanceof Error ? err.message : "Something went wrong."; }
`);
const { setHttpHandler } = await import(httpStubUrl);

const compileTs = (name, deps = {}) => {
  let src = read(`./${name}.ts`);
  for (const [spec, url] of Object.entries(deps))
    src = src.replaceAll(`from "./${spec}"`, `from "${url}"`);
  return transpile(src);
};

const kanbanUrl = toUrl(compileTs("kanban", { http: httpStubUrl }));
const kanban = await load(compileTs("kanban", { http: httpStubUrl }));
const board = await load(compileTs("kanban-board", { kanban: kanbanUrl }));
const teamops = await load(compileTs("teamops", { http: httpStubUrl }));
const subagents = await load(compileTs("subagents", { http: httpStubUrl }));

/** localStorage stand-in, so kanban-board's board round-trips. */
globalThis.localStorage = {
  _d: {},
  getItem(k) {
    return this._d[k] ?? null;
  },
  setItem(k, v) {
    this._d[k] = String(v);
  },
  removeItem(k) {
    delete this._d[k];
  },
};

/* ── The real payloads the Gateway returns ───────────────────────────────── */

/**
 * `GET /api/company/kanban/tasks` -> `CompanyKanbanEngine.list_tasks` ->
 * `[t.model_dump() for t in tasks]`, and `KanbanTask` is a pydantic model
 * (backend/packages/harness/alpha/company/kanban.py:34-46). Its `status` is a
 * `TaskStatus` StrEnum of exactly six values (kanban.py:18-25).
 */
const serverTask = (over = {}) => ({
  id: "task-ccc",
  title: "Deploy the API",
  body: "ship it",
  status: "blocked",
  assignee: "scout",
  priority: "critical",
  department: "engineering",
  created_at: 1750000000.5,
  updated_at: 1750000100.5,
  result: "",
  tags: [],
  ...over,
});

/* ══ 1. The work board hid real server cards ═════════════════════════════ */

test("a real server card in a column-less status is still shown, not dropped", async () => {
  // The four columns used to be ready/in_progress/review/done while the server
  // has six statuses. Measured before the fix, with these three rows:
  //   header reads: Work board (3)   cards rendered: 1
  //   VANISH: [ 'task-aaa status=backlog', 'task-bbb status=todo' ]
  setHttpHandler(() => [
    serverTask({ id: "task-aaa", status: "backlog" }),
    serverTask({ id: "task-bbb", status: "todo" }),
    serverTask({ id: "task-ddd", status: "in_progress" }),
  ]);
  const tasks = await kanban.listKanbanTasks();

  // Every row survives the client…
  assert.equal(tasks.length, 3, "a card must not be dropped by the client");

  // …and every row lands in a column the board actually renders. This is the
  // assertion that was false before: `tasks.filter(t => t.status === col.id)`
  // over the four old columns matched 1 of these 3.
  const columns = kanban.KANBAN_COLUMNS.map((c) => c.id);
  for (const col of columns) {
    assert.ok(
      columns.includes(col),
      `column ${col} must be a real server status`,
    );
  }
  const placed = tasks.filter((t) => columns.includes(t.status));
  const unmapped = kanban.unmappedKanbanTasks(tasks);
  assert.equal(
    placed.length + unmapped.length,
    tasks.length,
    "every card must be in a column OR in the explicitly-labelled unmapped bucket — never nowhere",
  );
  assert.deepEqual(
    placed.map((t) => t.id),
    ["task-bbb", "task-ddd"],
    "todo and in_progress have columns of their own",
  );
  assert.deepEqual(
    unmapped.map((t) => t.id),
    ["task-aaa"],
    "backlog has no column of its own, so it is surfaced rather than dropped",
  );
});

test("every column id is a status the server's TaskStatus enum can hold", () => {
  // alpha/company/kanban.py:18-25
  const serverEnum = [
    "backlog",
    "todo",
    "in_progress",
    "review",
    "done",
    "blocked",
  ];
  for (const col of kanban.KANBAN_COLUMNS) {
    assert.ok(
      serverEnum.includes(col.id),
      `column "${col.id}" is not in the server enum, so cards in it would never render`,
    );
  }
  // The specific values that used to be unreachable.
  assert.ok(
    !kanban.KANBAN_COLUMNS.some((c) => c.id === "ready"),
    "`ready` is not a value TaskStatus can hold; it was the old column id and 500'd on push",
  );
});

test("a row with no status is not filed under a real column", async () => {
  setHttpHandler(() => [{ id: "x", title: "no status key" }]);
  const [t] = await kanban.listKanbanTasks();
  assert.equal(
    t.status,
    "",
    "an absent status must stay absent, not be defaulted to a real column",
  );
  assert.ok(
    kanban.unmappedKanbanTasks([t]).length === 1,
    "it belongs in the unmapped bucket, where the view names it as not reported",
  );
});

/* ══ 2. Both move buttons used to rewrite a card to "ready" ══════════════ */

test("a move from a column-less status is refused, not silently rewritten to To do", () => {
  // Measured before the fix:
  //   status=backlog  indexOf=-1  back->ready  fwd->ready
  //   status=todo     indexOf=-1  back->ready  fwd->ready
  //   status=blocked  indexOf=-1  back->ready  fwd->ready
  // `Math.max(0, -1 + 1) === 0`, so BOTH directions resolved to order[0].
  //
  // `todo` was a *real* server status with no column then; it is the first
  // column now, so it is checked in the "real column" test below. What must
  // never resolve to a move is a status the board cannot represent.
  for (const unmapped of [
    "backlog",
    "blocked",
    "",
    "awaiting_approval",
    "ready",
  ]) {
    assert.equal(
      kanban.nextKanbanStatus(unmapped, 1),
      null,
      `moving forward from "${unmapped}" must be refused, not mapped to a real column`,
    );
    assert.equal(
      kanban.nextKanbanStatus(unmapped, -1),
      null,
      `moving back from "${unmapped}" must be refused, not mapped to a real column`,
    );
  }
});

test("a move between real columns moves exactly one step and stops at the ends", () => {
  assert.equal(kanban.nextKanbanStatus("todo", 1), "in_progress");
  assert.equal(kanban.nextKanbanStatus("in_progress", -1), "todo");
  assert.equal(kanban.nextKanbanStatus("in_progress", 1), "review");
  assert.equal(kanban.nextKanbanStatus("review", 1), "done");
  // Already at the end: no move, rather than a same-column no-op POST.
  assert.equal(kanban.nextKanbanStatus("done", 1), null);
  assert.equal(kanban.nextKanbanStatus("todo", -1), null);
});

/* ══ 3. The server's stage never reached a mirrored card ═════════════════ */

test("a card an agent moved on the server shows the server's stage, not a stale local one", async () => {
  // Measured before the fix, despite the doc comment claiming otherwise:
  //   server says status=done -> merged card status = in_progress
  //   second sync             -> merged card status = in_progress
  setHttpHandler(() => [serverTask({ status: "done" })]);
  const tasks = await kanban.listKanbanTasks();

  board.clearBoard();
  board.saveCard({
    ...board.emptyCard(),
    id: "card-1",
    title: "Deploy the API",
    status: "in_progress",
    serverId: "task-ccc",
    agent: "scout",
  });

  const once = board.mergeServerCards(board.loadCards(), tasks);
  assert.equal(once.length, 1);
  assert.equal(
    once[0].status,
    "done",
    "the server owns the stage of a card it owns",
  );

  // And it must STAY that way, or the next merge re-introduces the lie.
  const twice = board.mergeServerCards(once, tasks);
  assert.equal(
    twice[0].status,
    "done",
    "repeated syncs must converge, not revert to the local stage",
  );
});

test("a local edit the server has no field for still wins", async () => {
  setHttpHandler(() => [serverTask({ status: "in_progress" })]);
  const tasks = await kanban.listKanbanTasks();
  board.clearBoard();
  board.saveCard({
    ...board.emptyCard(),
    id: "card-1",
    title: "Edited by the operator",
    status: "todo",
    serverId: "task-ccc",
    agent: "scout",
  });
  const merged = board.mergeServerCards(board.loadCards(), tasks);
  assert.equal(
    merged[0].title,
    "Edited by the operator",
    "a local field the server does not carry must survive",
  );
  assert.equal(
    merged[0].status,
    "in_progress",
    "but the stage is the server's",
  );
});

/* ══ 4. A mirrored card claimed 0% and a creation time it never had ═════ */

test("a server-mirrored card claims no progress and no invented creation time", async () => {
  // Through the real client, so the epoch-seconds -> ISO conversion the client
  // performs is part of what is being measured.
  setHttpHandler(() => [serverTask()]);
  const tasks = await kanban.listKanbanTasks();
  board.clearBoard();
  const [card] = board.mergeServerCards([], tasks);

  assert.equal(
    card.progress,
    null,
    "the server's KanbanTask has no progress field; a measured 0% bar was fabricated",
  );
  assert.equal(
    card.createdAt,
    new Date(1750000000.5 * 1000).toISOString(),
    "createdAt must be the server's own created_at, not the moment the page loaded",
  );
  assert.equal(
    card.updatedAt,
    new Date(1750000100.5 * 1000).toISOString(),
    "updatedAt must be the server's own updated_at",
  );
  assert.equal(
    card.history[0].at,
    new Date(1750000100.5 * 1000).toISOString(),
    "the mirror history entry is stamped with the server's clock, not `new Date()`",
  );
  assert.equal(
    card.description,
    "ship it",
    "the server's `body` is the card's description; it was dropped entirely",
  );
});

test("an absent server timestamp leaves the field absent rather than inventing one", async () => {
  setHttpHandler(() => [{ id: "x", title: "t", status: "todo" }]);
  const [t] = await kanban.listKanbanTasks();
  assert.equal(
    t.createdAt,
    null,
    "an absent created_at must not become a number",
  );
  assert.equal(t.updatedAt, null);
  assert.equal(t.priority, null, "an absent priority must not become a string");
  assert.equal(t.result, null, "an empty result must not become a string");
});

test("a new card starts with no recorded progress, not a measured zero", () => {
  assert.equal(board.emptyCard().progress, null);
});

/* ══ 5. The client pushed statuses the server enum cannot parse ═════════ */

test("a local-only stage is refused with a reason instead of being posted and 500-ing", async () => {
  // Measured before the fix — the cast `status as "ready"|…` sent all eight
  // local stages, and `TaskStatus(new_status.lower())` (kanban.py:156) raises
  // for testing/approval/backlog-is-ok but testing and approval are not in the
  // enum at all:
  //   testing  -> new_status="testing"
  //   approval -> new_status="approval"
  const card = { ...board.emptyCard(), id: "c", serverId: "task-ccc" };

  for (const local of ["testing", "approval"]) {
    let sent = null;
    setHttpHandler((p, m, payload) => {
      sent = payload;
    });
    await assert.rejects(
      () => board.pushStatus(card, local),
      /no ".*" stage/i,
      `"${local}" has no server counterpart, so it must be refused locally rather than posted`,
    );
    assert.equal(sent, null, `"${local}" must never reach the wire`);
  }

  // A real stage does go on the wire, in the server's own spelling.
  const posted = [];
  setHttpHandler((p, m, payload) => {
    posted.push(payload);
  });
  for (const s of ["todo", "in_progress", "review", "done"]) {
    await board.pushStatus(card, s);
  }
  assert.deepEqual(
    posted.map((p) => p.new_status),
    ["todo", "in_progress", "review", "done"],
    "only statuses the server enum declares may be sent",
  );
  for (const p of posted) {
    assert.ok(
      ["backlog", "todo", "in_progress", "review", "done", "blocked"].includes(
        p.new_status,
      ),
    );
  }
});

/* ══ 6. Failed reads that rendered as empty lists ═══════════════════════ */

test("a down Gateway makes the subagent reads reject instead of resolving empty", async () => {
  setHttpHandler(() => {
    throw new Error("Request failed (HTTP 0). Network error");
  });
  // Measured before the fix: listLiveSubagents() -> [] and
  // subagentResult('sa-1') -> null, which the section rendered as
  // "Running now (0)" / "Nothing running" and "No result yet — it may still
  // be working."
  await assert.rejects(
    () => subagents.listLiveSubagents(),
    /Network error/,
    "a down Gateway must not look like a fleet with no helpers",
  );
  await assert.rejects(
    () => subagents.fetchLiveSubagentsStrict(),
    /Network error/,
  );
  await assert.rejects(
    () => subagents.subagentResult("sa-1"),
    /Network error/,
    "a failed result read must not look like a helper that is still working",
  );
});

test("a genuinely empty fleet and a genuinely empty result are still empty", async () => {
  setHttpHandler((path) => {
    if (path === "/subagents/control") return [];
    if (path.startsWith("/subagents/control/") && path.endsWith("/result")) {
      // The route's own "no deliverable" answer, verbatim from
      // subagent_control.py:124: `{"status": rec.status.value, "result": None}`.
      return { status: "running", result: null };
    }
    throw new Error(`unexpected path ${path}`);
  });
  assert.deepEqual(
    await subagents.listLiveSubagents(),
    [],
    "an empty fleet is a real answer and must stay empty",
  );
  assert.equal(
    await subagents.subagentResult("sa-1"),
    null,
    "a 200 with no deliverable is the server saying there is no result",
  );
});

test("a real result is returned verbatim, never nulled", async () => {
  setHttpHandler(() => ({ status: "completed", result: { summary: "done" } }));
  const r = await subagents.subagentResult("sa-1");
  assert.deepEqual(r, { status: "completed", result: { summary: "done" } });
});

/* ══ 7. An unrecognised status was painted the SUCCESS colour ═══════════ */

test("no status outside the completion set gets the green tone", () => {
  // Before: `s.status === "running" ? "blue" : s.status === "failed" ||
  // s.status === "error" ? undefined : "green"`. Measured with a newer-Gateway
  // status `awaiting_approval`, the tone came out "green".
  for (const s of [
    "cancelled",
    "stalled",
    "expired",
    "archived",
    "awaiting_approval",
    "",
    "brand_new_state",
  ]) {
    assert.notEqual(
      subagents.subagentStatusTone(s),
      "green",
      `"${s}" must not be painted as a success`,
    );
  }
  // A real completion is still green — the inverse error would be its own lie.
  assert.equal(subagents.subagentStatusTone("completed"), "green");
  assert.equal(subagents.subagentStatusTone("running"), "blue");
  assert.equal(subagents.subagentStatusTone("failed"), "red");
  assert.equal(subagents.subagentStatusTone("cancelled"), "amber");
});

test("the enum the server actually declares is the one the tone map covers", () => {
  // alpha/subagents/lifecycle.py::SubagentStatusEnum
  const serverEnum = [
    "created",
    "initializing",
    "ready",
    "running",
    "waiting",
    "blocked",
    "stalled",
    "completed",
    "failed",
    "recovering",
    "cancelled",
    "expired",
    "archived",
  ];
  assert.deepEqual(
    [...subagents.KNOWN_SUBAGENT_STATUSES],
    serverEnum,
    "the client's list of known statuses must match the server's enum exactly",
  );
  for (const s of serverEnum)
    assert.equal(subagents.isKnownSubagentStatus(s), true);
  assert.equal(
    subagents.isKnownSubagentStatus("awaiting_approval"),
    false,
    "a status from a newer Gateway must be flagged, not assumed known",
  );
});

test("the same rule holds for batch and batch-item badges", () => {
  for (const s of ["cancelled", "partial", "stalled", "brand_new_state", ""]) {
    assert.notEqual(
      subagents.batchStatusTone(s),
      "green",
      `batch "${s}" must not read as success`,
    );
    assert.notEqual(
      subagents.batchItemStatusTone(s),
      "green",
      `item "${s}" must not read as success`,
    );
  }
  assert.equal(subagents.batchStatusTone("completed"), "green");
});

/* ══ 8. An absent `enabled` became a green "on" badge ════════════════════ */

test("an absent enabled flag is unknown, not enabled", async () => {
  setHttpHandler(() => ({ subagents: [{ name: "scout", description: "d" }] }));
  const [c] = await subagents.listSubagentCatalog();
  assert.equal(
    c.enabled,
    null,
    "Boolean(pick(s, ['enabled'], true)) made an absent flag TRUE, drawn as a green 'on' badge",
  );
  assert.equal(
    c.model,
    "",
    "an absent model must not be invented as the literal 'inherit'",
  );

  setHttpHandler(() => ({
    subagents: [
      { name: "scout", description: "d", enabled: true, model: "inherit" },
    ],
  }));
  const [on] = await subagents.listSubagentCatalog();
  assert.equal(on.enabled, true, "a real true is still true");
  setHttpHandler(() => ({
    subagents: [{ name: "scout", description: "d", enabled: false }],
  }));
  const [off] = await subagents.listSubagentCatalog();
  assert.equal(off.enabled, false, "a real false is still false");
});

/* ══ 9. The swarm's concurrency was invisible ═══════════════════════════ */

test("every swarm counter is resolved, and an absent one says so", () => {
  const full = teamops.swarmProgress({
    total: 7,
    completed: 2,
    running: 3,
    pending: 1,
    failed: 1,
    cancelled: 0,
  });
  assert.equal(full.total.count, 7);
  assert.equal(full.completed.count, 2);
  // These four were mapped by the client and rendered NOWHERE.
  assert.equal(full.running.count, 3);
  assert.equal(full.pending.count, 1);
  assert.equal(full.failed.count, 1);
  assert.equal(full.cancelled.count, 0);
  assert.equal(full.percent, (2 / 7) * 100);
  assert.equal(full.measured, true);
});

test("absent swarm counters are unknown, never a zero", () => {
  const p = teamops.swarmProgress({ total: 7 });
  assert.equal(p.total.count, 7);
  assert.equal(p.completed.count, null, "an absent completed must not be 0");
  assert.equal(p.completed.note, "not reported");
  assert.equal(p.running.count, null);
  assert.equal(p.percent, null, "no ratio is drawn from a missing numerator");
  // Before the fix this produced the aria-label "7 of 7 tasks complete".
  assert.equal(
    teamops.swarmProgressLabel(p),
    "Completed count not reported by the server",
  );
});

test("an unmeasured progress block yields no ratio and says so", () => {
  for (const p of [
    teamops.swarmProgress(undefined),
    teamops.swarmProgress(null),
    teamops.swarmProgress({}),
  ]) {
    assert.equal(p.measured, false);
    assert.equal(p.percent, null);
    assert.equal(
      teamops.swarmProgressLabel(p),
      "Task progress not reported by the server",
    );
  }
  // The exact string the old code produced from `?? 0` on both counters.
  assert.doesNotMatch(
    teamops.swarmProgressLabel(teamops.swarmProgress({})),
    /0 of 0/,
    "a zero-denominator ratio is not information",
  );
});

test("an empty plan is a real answer, worded as one", () => {
  const p = teamops.swarmProgress({
    total: 0,
    pending: 0,
    running: 0,
    completed: 0,
    failed: 0,
    cancelled: 0,
  });
  assert.equal(p.measured, true);
  assert.equal(p.percent, null, "0 of 0 must not divide to a 0% bar");
  assert.equal(teamops.swarmProgressLabel(p), "No tasks in this swarm yet");
});

test("the swarm list preserves the server's enum and stops fabricating ids", async () => {
  setHttpHandler(() => [
    {
      swarm_id: "swm-1",
      goal: "ship",
      mode: "auto",
      status: "awaiting_approval",
      revision: 0,
      max_concurrency: 8,
      quality_score: 0.91,
      terminal_reason: "needs a human",
    },
  ]);
  const [s] = await teamops.listSwarms();
  assert.equal(
    s.status,
    "awaiting_approval",
    "a newer-Gateway status is preserved verbatim",
  );
  assert.equal(s.id, "swm-1", "the plan's own id is used");
  assert.equal(
    s.revision,
    0,
    "a real revision 0 must not be turned into 'absent' by `|| undefined`",
  );
  assert.equal(s.qualityScore, 0.91);
  assert.equal(s.terminalReason, "needs a human");

  setHttpHandler(() => [{ goal: "no id key" }]);
  const [noId] = await teamops.listSwarms();
  assert.equal(
    noId.id,
    "",
    "an absent id must not become a fabricated `swarm-0`",
  );
  assert.equal(noId.status, "", "an absent status must not become a known one");
});

/* ══ 10. The digest failure wore a sentence ════════════════════════════ */

test("a failed digest read rejects; an empty digest is an empty string", async () => {
  setHttpHandler(() => {
    throw new Error("Request failed (HTTP 500). Digest unavailable.");
  });
  await assert.rejects(
    () => teamops.executiveDigest(),
    /Digest unavailable/,
    "the old catch returned a sentence the section rendered as if it were a briefing",
  );

  setHttpHandler(() => ({}));
  assert.equal(
    await teamops.executiveDigest(),
    "",
    "a 2xx with no digest is an empty answer, distinct from a failure",
  );

  setHttpHandler(() => ({ digest: "Q3 revenue is up." }));
  assert.equal(await teamops.executiveDigest(), "Q3 revenue is up.");
});

test("kanbanEvents rejects rather than resolving an empty feed", async () => {
  setHttpHandler(() => {
    throw new Error("Request failed (HTTP 500). events unavailable");
  });
  await assert.rejects(
    () => kanban.kanbanEvents(),
    /events unavailable/,
    "a failed event feed must not look like a quiet board",
  );
  setHttpHandler(() => ({ events: [{ event_id: "e1" }] }));
  assert.equal((await kanban.kanbanEvents()).length, 1);
});

/* ══ 11. The rendered markup, not the source text ═══════════════════════ */

const LUCIDE = pathToFileURL(
  fileURLToPath(
    new URL(
      "../../node_modules/lucide-react/dist/esm/lucide-react.js",
      import.meta.url,
    ),
  ),
).href;

const uiUrl = toUrl(
  transpile(read("../components/ui.tsx"), { jsx: ts.JsxEmit.ReactJSX })
    .replace(/from\s+"react"/, `from "${resolveUrl("react")}"`)
    .replace(
      /from\s+"react\/jsx-runtime"/,
      `from "${resolveUrl("react/jsx-runtime")}"`,
    ),
);
const timeUrl = toUrl(transpile(read("./time.ts")));
const teamopsProgressUrl = toUrl(transpile(read("./teamops-progress.ts")));
/**
 * The swarm structure view module.
 *
 * `loadSection` maps every `@/lib/*` import the section uses onto a data URL,
 * because a data-URL module has no hierarchical base to resolve a bare specifier
 * against. Adding an import to the section without adding its mapping here threw
 * `Invalid relative URL or base scheme is not hierarchical` — reported by the
 * test runner as "asynchronous activity after the test ended", which is why the
 * message does not name the module.
 */
const swarmStructureViewUrl = toUrl(
  transpile(read("./swarm-structure-view.ts")),
);
const teamopsUrl = toUrl(compileTs("teamops", { http: httpStubUrl }));
const subagentsUrl = toUrl(compileTs("subagents", { http: httpStubUrl }));
const kanbanBoardUrl = toUrl(compileTs("kanban-board", { kanban: kanbanUrl }));
const inboxUrl = toUrl(`
  export const fetchRoster = async () => { throw new Error("stub: not called"); };
  export const registerRosterAgent = async () => { throw new Error("stub: not called"); };
  export const sendAgentMessage = async () => { throw new Error("stub: not called"); };
  export const fetchInbox = async () => { throw new Error("stub: not called"); };
  export const setRosterStatus = async () => { throw new Error("stub: not called"); };
`);

const loadSection = async (file) => {
  const code = transpile(read(`../components/sections/${file}`), {
    jsx: ts.JsxEmit.ReactJSX,
  })
    .replace(/from\s+"react"/, `from "${resolveUrl("react")}"`)
    .replace(
      /from\s+"react\/jsx-runtime"/,
      `from "${resolveUrl("react/jsx-runtime")}"`,
    )
    .replace(/from\s+"lucide-react"/, `from "${LUCIDE}"`)
    .replace(/from\s+"@\/components\/ui"/, `from "${uiUrl}"`)
    .replace(/from\s+"@\/lib\/http"/, `from "${httpStubUrl}"`)
    .replace(/from\s+"@\/lib\/time"/, `from "${timeUrl}"`)
    .replace(
      /from\s+"@\/lib\/teamops-progress"/,
      `from "${teamopsProgressUrl}"`,
    )
    .replace(
      /from\s+"@\/lib\/swarm-structure-view"/,
      `from "${swarmStructureViewUrl}"`,
    )
    .replace(/from\s+"@\/lib\/teamops"/, `from "${teamopsUrl}"`)
    .replace(/from\s+"@\/lib\/kanban"/, `from "${kanbanUrl}"`)
    .replace(/from\s+"@\/lib\/kanban-board"/, `from "${kanbanBoardUrl}"`)
    .replace(/from\s+"@\/lib\/subagents"/, `from "${subagentsUrl}"`)
    .replace(/from\s+"@\/lib\/inbox"/, `from "${inboxUrl}"`);
  return load(code);
};

const { createElement: h } = await import(resolveUrl("react"));
const { renderToStaticMarkup } = await import(resolveUrl("react-dom/server"));
const TeamOps = await loadSection("TeamOpsSection.tsx");
const visibleText = (markup) =>
  markup
    .replace(/<span class="sr-only">[\s\S]*?<\/span>/g, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&middot;|&#x2f;|&amp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();

test("the Team Ops board renders real cards in unmapped statuses instead of hiding them", () => {
  // Render the board's own column-bucketing decision, not a source regex.
  const tasks = [
    { id: "t1", title: "A", status: "backlog", assignee: "", description: "" },
    {
      id: "t2",
      title: "B",
      status: "todo",
      assignee: "scout",
      description: "",
    },
    {
      id: "t3",
      title: "C",
      status: "blocked",
      assignee: "scout",
      description: "",
    },
    {
      id: "t4",
      title: "D",
      status: "in_progress",
      assignee: "scout",
      description: "",
    },
  ];
  const columns = kanban.KANBAN_COLUMNS.map((c) => ({
    ...c,
    items: tasks.filter((t) => t.status === c.id),
  }));
  const other = kanban.unmappedKanbanTasks(tasks);
  const rendered =
    columns.reduce((n, c) => n + c.items.length, 0) + other.length;
  assert.equal(rendered, 4, "every card reaches a rendered bucket");
  assert.deepEqual(
    columns.find((c) => c.id === "in_progress").items.map((t) => t.id),
    ["t4"],
  );
  assert.deepEqual(other.map((t) => t.id).sort(), ["t1", "t3"]);
});

test("the source no longer contains the bare '?' move-button glyph or the '??' assignee prefix", () => {
  // Verified byte-wise during the audit: TeamOpsSection.tsx held the ASCII
  // characters '?' and '??' as the entire visible content of the two move
  // buttons and as the assignee prefix. A reviewer should be able to confirm
  // the glyphs are now lucide icons with names.
  const src = read("../components/sections/TeamOpsSection.tsx");
  const code = src
    .split("\n")
    .filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l))
    .join("\n");
  assert.doesNotMatch(
    code,
    />\s*\?\s*<\/button>/,
    "a bare '?' is still the entire content of a move button",
  );
  // The `??` prefix, asserted on the assignee line itself. It has to be tied
  // to the element that renders an assignee, otherwise any unrelated `??`
  // (a nullish coalesce, say) would satisfy it and the check would be vacuous.
  assert.doesNotMatch(
    code,
    />\s*\?\?\s*\{t\.assignee\}/,
    "the literal '??' assignee prefix is still in the markup",
  );
  assert.match(
    code,
    /<User className="size-3" aria-hidden="true" \/> \{t\.assignee\}/,
    "the assignee must be rendered with a real glyph, not a bare '??'",
  );
  assert.match(
    code,
    /lucide|aria-label/,
    "the controls must carry a glyph and a name",
  );
  // Both move controls are named, and each name carries the card it acts on.
  const moveButtons = [...code.matchAll(/aria-label=\{[^}]*Move[^}]*\}/g)].map(
    (m) => m[0],
  );
  assert.ok(
    moveButtons.length >= 2,
    `both move buttons carry an aria-label, found ${moveButtons.length}`,
  );
  for (const label of moveButtons) {
    assert.match(
      label,
      /\$\{label\}/,
      "the accessible name must name the card, so two rows' buttons differ",
    );
  }
});

test("icon-only destructive controls in Team Ops carry an accessible name", () => {
  const src = read("../components/sections/TeamOpsSection.tsx");
  const code = src
    .split("\n")
    .filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l))
    .join("\n");
  // The job Cancel button and the room Send button were `<Ban/>` / `<Send/>`
  // alone. `Btn` forwards `title` but not `aria-label`, so the name is carried
  // as an sr-only child, which is what a screen reader actually reads.
  for (const glyph of [
    '<Ban className="size-3.5" aria-hidden="true" />',
    '<Send className="size-3.5" aria-hidden="true" />',
  ]) {
    const at = code.indexOf(glyph);
    assert.ok(at > 0, `expected the ${glyph} control`);
    const after = code.slice(at, at + 400);
    assert.match(
      after,
      /sr-only/,
      `a glyph-only control at offset ${at} still has no accessible name`,
    );
  }
});

/* ══ 12. Wiring pins: the section must keep calling what was fixed ═══════ */

const teamopsSrc = read("../components/sections/TeamOpsSection.tsx");
const codeOnly = (src) =>
  src
    .split("\n")
    .filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l))
    .join("\n");

test("the swarm lifecycle verbs are locked while a request is in flight", () => {
  const code = codeOnly(teamopsSrc);
  // `run-async` starts a background runner and `step` advances a scheduler
  // tick; a double-click on either previously did it twice.
  assert.match(code, /busySwarm/, "the swarm row needs an in-flight flag");
  assert.match(
    code,
    /disabled=\{busy\}/,
    "the lifecycle buttons must disable while in flight",
  );
  assert.match(code, /setBusySwarm\(id\)/);
  assert.match(
    code,
    /setBusySwarm\(null\)/,
    "the lock must be released in a finally",
  );
  assert.match(
    code,
    /if \(busySwarm\) return;/,
    "a second press must be refused, not queued",
  );
});

test("an unrecognised swarm status is not given the full verb set", async () => {
  // swarmActions is exported so this is measured, not grepped.
  const mod = await loadSection("TeamOpsSection.tsx");
  assert.deepEqual(mod.swarmActions("completed"), []);
  assert.deepEqual(mod.swarmActions("cancelled"), []);
  assert.deepEqual(mod.swarmActions("paused"), ["resume", "cancel"]);
  // Before: any status not in the terminal list got run-async + step + pause +
  // cancel, so a brand-new terminal state offered "Run".
  assert.deepEqual(mod.swarmActions("awaiting_approval"), ["cancel"]);
  assert.deepEqual(mod.swarmActions(""), ["cancel"]);
  assert.deepEqual(mod.swarmActions("planning"), [
    "run-async",
    "step",
    "pause",
    "cancel",
  ]);
});

test("every Team Ops read is inside an allSettled or its own try, so no list is silently empty", () => {
  const body = codeOnly(teamopsSrc).match(
    /const load = async \(\) => \{[\s\S]*?\n  \};/,
  )[0];
  assert.ok(body, "TeamOpsSection must still have load()");
  for (const call of [
    "listGroups()",
    "listSwarms()",
    "listJobs()",
    "listMcpTasks(",
    "companyKpis()",
    "executiveDigest()",
  ]) {
    assert.ok(body.includes(call), `load() must still read ${call}`);
  }
  // The FIRST trio is the one that mattered: with `Promise.all` a single
  // rejection discards the three lists that already resolved, so a `/jobs`
  // 500 blanked the group and swarm lists too.
  assert.match(
    body,
    /Promise\.allSettled\(\[listGroups\(\), listSwarms\(\), listJobs\(\)\]\)/,
    "the groups/swarms/jobs reads must settle independently, so one failure cannot discard the rest",
  );
  assert.match(
    body,
    /Promise\.allSettled\(\[executiveDigest\(\), companyKpis\(\)\]\)/,
    "the digest and KPI reads must settle independently too",
  );
  assert.match(
    body,
    /await listMcpTasks\(props\.threadId\)/,
    "the MCP read keeps its own try/catch rather than joining the shared one",
  );
  for (const key of ["groups", "swarms", "jobs", "mcp", "digest", "kpis"]) {
    assert.ok(
      body.includes(`"${key}"`) || body.includes(`${key}:`),
      `load() must track a failure flag for ${key}, so a failed read is not an empty list`,
    );
  }
});

test("the section reports each read's own failure rather than one shared error", () => {
  const code = codeOnly(teamopsSrc);
  for (const key of ["groups", "swarms", "jobs", "mcp", "digest", "kpis"]) {
    assert.ok(
      code.includes(`readErrors.${key}`),
      `readErrors.${key} must be rendered, not just stored`,
    );
  }
  // A tab count must not read 0 when nothing was measured.
  assert.match(code, /const tabCount = \(n: number, key: string\) =>/);
  assert.match(
    code,
    /loading \? "…" : readErrors\[key\] \? "\?" : String\(n\)/,
  );
  // A KPI the server sent with no value rendered an EMPTY bold slot, next to
  // an invented name. Both are claims the server never made.
  assert.match(
    code,
    /\? "not reported"\s*: String\(raw\)/,
    "an absent KPI value must be named, not rendered as an empty slot",
  );
  assert.match(
    code,
    /the server sent no label/,
    "an absent KPI name must be disclosed, not replaced with 'KPI 3'",
  );
});

test("SubagentsSection reads the fleet strictly and locks the spawn control", () => {
  const code = codeOnly(read("../components/sections/SubagentsSection.tsx"));
  // The name alone is not enough — a local try/catch around the same reader
  // restores the catch-and-empty. The call itself must be unwrapped.
  //
  // The `\s*` and the optional trailing comma are load-bearing, both added after
  // a prettier reflow broke this pin. Prettier wrapped
  // `Promise.allSettled([listSubagentCatalog(), fetchLiveSubagentsStrict()])`
  // across three lines and added a trailing comma, so the single-space literal no
  // longer matched — turning the suite red while the behaviour it guards was
  // entirely intact. A pin that asserts *line shape* rather than behaviour fails
  // the next time any formatter runs, and the failure looks like a regression in
  // the honesty contract rather than a formatting change.
  assert.match(
    code,
    /\[\s*listSubagentCatalog\(\),\s*fetchLiveSubagentsStrict\(\)\s*,?\s*\]/,
    "the fleet must be read by the strict reader, not a locally re-wrapped one that catches to []",
  );
  assert.doesNotMatch(
    code,
    /fetchLiveSubagentsStrict\(\)[\s\S]{0,80}catch/,
    "the strict reader must not be re-wrapped in a catch that returns []",
  );
  assert.match(code, /setSpawning\(true\)/);
  assert.match(
    code,
    /if \(!text \|\| spawning\) return;/,
    "a second click inside the request window must not spawn a second helper",
  );
  assert.match(code, /disabled=\{!objective\.trim\(\) \|\| spawning\}/);
  assert.match(
    code,
    /subagentStatusTone\(s\.status\)/,
    "the badge tone must come from the status, not a hardcoded ternary",
  );
  assert.doesNotMatch(
    code,
    /\? "blue" : s\.status === "failed"/,
    "the old tone ternary that painted every other status green",
  );
  // `Boolean(pick(s, ["enabled"], true))` made an absent flag TRUE, drawn green.
  // The tri-state now lives in `enabledView()`, so the panel calls that instead
  // of branching on `=== null` inline. The pin follows the behaviour rather than
  // the old literal: an absent flag must still take its own branch, and the
  // section must not draw a tone for it itself.
  assert.match(
    code,
    /enabledView\(c\.enabled\)/,
    "the panel must derive its enabled badge from the tri-state helper",
  );
  assert.match(
    code,
    /enabledView\(def\.enabled\)/,
    "the detail header must use the same tri-state helper as the list row",
  );
  assert.match(
    code,
    /en\.tone === "muted"/,
    "an absent enabled flag must take its own branch, not the truthy one",
  );
  assert.doesNotMatch(
    code,
    /enabled \? "green" : "gray"/,
    "a two-state badge would paint an unreported flag green again",
  );
  // A failed result read used to render "it may still be working".
  assert.match(
    code,
    /Could not read this helper's result/,
    "a failed result read must be named as a failure, with the server's reason",
  );
  assert.doesNotMatch(
    code,
    /"No result yet — it may still be working\."/,
    "the invented 'still working' explanation for a failed read must be gone",
  );
});

test("SubagentsSection distinguishes a failed item read from an empty batch", () => {
  const code = codeOnly(read("../components/sections/SubagentsSection.tsx"));
  // The name alone is not enough — the error must be the branch that RENDERS.
  assert.match(
    code,
    /\{itemErrors\[b\.id\] \? \(\s*<ErrorBox/,
    "a failed item read must render as an error, not fall through to the empty state",
  );
  assert.match(code, /Items could not be read/);
  assert.match(code, /batchStatusTone\(b\.status\)/);
  assert.match(code, /batchItemStatusTone\(it\.status\)/);
  assert.match(
    code,
    /busyBatch/,
    "a batch verb must be locked while in flight",
  );
  assert.match(
    code,
    /if \(busyBatch\) return;/,
    "a second press must be refused, not queued",
  );
  // Scoped to the batch block AND tied to each handler: a loose search for
  // `disabled={busy}` would still match the sibling button and pass even if one
  // of them had lost its lock.
  const start = code.indexOf("const act = async (b: Batch");
  assert.ok(start > 0, "BatchesBlock must keep its per-batch verb handler");
  const block = code.slice(start);
  for (const verb of ["pauseBatch", "resumeBatch", "cancelBatch"]) {
    // Whitespace-tolerant for the same reason as the `Promise.allSettled` pin
    // above: prettier reflowed `act(b, () => pauseBatch(...))` onto three lines,
    // and a literal `indexOf` then failed while the control it guards was still
    // present and still owned by its `<Btn`. The claim is "this verb is invoked
    // inside the per-batch handler", so that is what is matched.
    const at = block.search(
      new RegExp(`act\\(b,\\s*\\(\\)\\s*=>\\s*${verb}\\(`),
    );
    assert.ok(at > 0, `expected the ${verb} control`);
    // Walk back to the opening `<Btn` that owns this handler.
    const open = block.lastIndexOf("<Btn", at);
    const control = block.slice(open, at);
    assert.match(
      control,
      /disabled=\{busy\}/,
      `the ${verb} control must render disabled while its request is in flight`,
    );
  }
  assert.doesNotMatch(
    code,
    /\(items\[b\.id\] \|\| \[\]\)\.length === 0/,
    "the old expression made a failed read and an empty batch identical",
  );
  // And the empty state must now say the SERVER reported none.
  assert.match(code, /The server reported no items in this batch/);
});

test("KanbanSection locks a card move and reverts only its own move", () => {
  const code = codeOnly(read("../components/sections/KanbanSection.tsx"));
  assert.match(
    code,
    /if \(busyCard\) return;/,
    "two presses in one request window interleaved through saveCard's read-modify-write",
  );
  assert.match(code, /setBusyCard\(card\.id\)/);
  assert.match(
    code,
    /const current = loadCards\(\)\.find\(\(c\) => c\.id === card\.id\)/,
    "the revert must check the card is still where this call put it",
  );
  assert.match(
    code,
    /current\.status === to/,
    "a later successful move must not be clobbered by an earlier failure",
  );
  assert.match(
    code,
    /progress not recorded/,
    "a card with no recorded progress must say so, not draw 0%",
  );
  // The percentage is still rendered, but only inside the branch that proves a
  // number was recorded — an unguarded `{c.progress}%` is what painted a
  // measured "0%" on every server-mirrored card. The CARD row is asserted here;
  // the editor has its own branch, below.
  assert.match(
    code,
    /\{c\.progress === null \? \(\s*<span className="text-\[10px\] text-muted-foreground">progress not recorded<\/span>/,
    "the card's progress row must branch on the nullable value",
  );
  assert.match(
    code,
    /c\.progress === null \? "—" : `\$\{c\.progress\}%`/,
    "the editor must show an unrecorded progress as unknown, not as 0%",
  );
});

test("MessagesSection distinguishes a failed room read from an empty room", () => {
  const code = codeOnly(read("../components/sections/MessagesSection.tsx"));
  assert.match(
    code,
    /roomRead\?\.state === "failed"/,
    "the transcript must gate its empty state on a successful read",
  );
  assert.match(code, /This is a fetch failure, not an empty room/);
  assert.match(
    code,
    /History not read yet — open the room/,
    "an unopened room's history has not been read; the list must not claim it is empty",
  );
  assert.match(
    code,
    /if \(!msgs\) return false;/,
    "a decision/blocker filter must not silently drop unread rooms",
  );
  assert.match(
    code,
    /startingRun/,
    "POST /groups/{name}/runs mints a new run per request; the control must be locked",
  );
  assert.match(code, /if \(!text \|\| startingRun\) return;/);
  assert.match(
    code,
    /postingVerdict/,
    "posting a verdict twice is two messages in the room log",
  );
  assert.match(
    code,
    /disabled=\{props\.postingVerdict\}/,
    "the control must render disabled while the post is in flight",
  );
  // `\s+`: the pre-commit prettier hook reflows JSX conditionals across lines,
  // so a pin written for one wrapping would fail on formatting, not on meaning.
  assert.match(code, /postingVerdict\s*\?\s*"Posting/);
});
test("the presence dot no longer matches a status by substring", () => {
  const code = codeOnly(read("../components/sections/MessagesSection.tsx"));
  assert.doesNotMatch(
    code,
    /\/active\|working\|online\|idle\/i/,
    "the chained regex made `inactive` green (it contains 'active') and `idle` green too",
  );
  // The dot is now selected from the server's resolved presence state rather
  // than pattern-matched out of a raw status word, so there is no word list to
  // keep in sync here. `unknown` is neutral and `offline` is destructive.
  assert.match(code, /state === "online"/);
  assert.match(code, /state === "busy"/);
  assert.match(code, /state === "idle"/);
  assert.match(code, /state === "offline"/);
  assert.match(code, /state === "unknown"/);
  // A state the server never reported renders as a neutral dot, not as green.
  assert.match(code, /return "bg-muted-foreground\/40"/);
});

test("the details toggle is not inert below the lg breakpoint", () => {
  const code = codeOnly(read("../components/sections/MessagesSection.tsx"));
  assert.doesNotMatch(
    code,
    /className="hidden lg:flex w-72/,
    "the pane was display:none below lg, so the ⓘ button toggled nothing at those widths",
  );
  assert.match(
    code,
    /max-lg:absolute/,
    "below lg the pane must become a visible drawer rather than vanish",
  );
  assert.match(
    code,
    /aria-expanded=\{showDetails\}/,
    "the toggle must report the state it controls",
  );
});

/* ══ 13. Recorded as CORRECT — pinned so they cannot silently regress ═══ */

test("three-state group message reading in Team Ops is intact", () => {
  // Recorded correct during the audit: `openMessages` distinguishes undefined
  // (not read), null (read produced nothing) and [] (quiet room), and the
  // reason from `groupMessages` is rendered.
  const code = codeOnly(teamopsSrc);
  assert.match(code, /groupMsgError\[g\.name\]/);
  // The apostrophe is JSX-escaped in the rendered source.
  assert.match(code, /Could not read this room&apos;s messages/);
  assert.match(code, /Messages unavailable .* the Gateway did not return/);
  assert.match(code, /No messages yet . say hello below/);
});

test("the group auto-run control was already guarded, and stays guarded", () => {
  // This is the twin of the MessagesSection defect fixed above: TeamOps had
  // the `runningGroup` lock from the start, and it must not be lost.
  const code = codeOnly(teamopsSrc);
  assert.match(code, /runningGroup/);
  assert.match(
    code,
    /disabled=\{!groupObjective\.trim\(\) \|\| runningGroup === g\.name\}/,
  );
});

test("the swarm blackboard already had a post lock and an honest empty state", () => {
  const code = codeOnly(teamopsSrc);
  assert.match(code, /disabled=\{posting \|\| !draft\.trim\(\)\}/);
  assert.match(code, /Blackboard unavailable: /);
  assert.match(code, /No messages on this swarm blackboard yet/);
  assert.match(code, /setPosting\(false\)/);
});

test("the group/message send guard in MessagesSection was already correct", () => {
  const code = codeOnly(read("../components/sections/MessagesSection.tsx"));
  assert.match(code, /if \(!draft\.trim\(\) \|\| sending\) return;/);
  assert.match(code, /disabled=\{!draft\.trim\(\) \|\| sending\}/);
});
