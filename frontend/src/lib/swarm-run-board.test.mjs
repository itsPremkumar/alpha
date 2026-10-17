// swarm-run-board.test.mjs — the chat board's honesty rules.
//
// A coordinator swarm executes in the Gateway's swarm runtime, so no stream
// event describes it. The chat board therefore reads the owner-scoped swarm
// list (the same source Team Ops reads) and must obey the same honesty rules
// without owning any lifecycle control:
//
// - only non-terminal swarms appear (finished runs stay in Team Ops history);
// - a failed refresh keeps the previous rows and names the reason — it never
//   reads as "nothing is running";
// - polling stops when nothing is refreshable (Team Ops' cadence and guard);
// - the component is read-only: it imports no mutating swarm action;
// - ChatView mounts it, and the mount hands lifecycle control to Team Ops.

import { readFileSync } from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";

const transpile = (at) =>
  ts.transpileModule(read(new URL(at, import.meta.url)), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;

// ---------------------------------------------------------------------------
// The real chain runs here: board → teamops.ts (real projections) → a stubbed
// http layer borrowed from lib/teamops.test.mjs's own harness. The projections
// under test (`swarmProgress`, `swarmProgressLabel`, `isTerminalSwarmStatus`)
// are pure, so the stub never fires for them; its job is only to satisfy the
// import so `listSwarms` etc. exist without a server.
// ---------------------------------------------------------------------------

const httpStub = `
let handler = (path, method, payload) => { throw new Error("no stub response configured"); };
export function setHttpHandler(fn) { handler = fn; }
export async function get(path) { return handler(path, "GET", undefined); }
export async function send(path, method, payload) { return handler(path, method, payload); }
export function pick(obj, keys, fallback) {
  if (obj && typeof obj === "object") {
    for (const key of keys) if (obj[key] !== undefined && obj[key] !== null) return obj[key];
  }
  return fallback;
}
export function asList(body, keys) {
  if (Array.isArray(body)) return body;
  for (const key of keys) if (body && typeof body === "object" && Array.isArray(body[key])) return body[key];
  return [];
}
`;

const stubUrl = toDataUrl(httpStub);
const teamopsUrl = toDataUrl(transpile("./teamops.ts").replace(/from\s+"\.\/http"/, `from "${stubUrl}"`));
const boardUrl = toDataUrl(transpile("./swarm-run-board.ts").replace(`from "@/lib/teamops"`, `from "${teamopsUrl}"`));
const { swarmRunRows } = await import(boardUrl);

// ---------------------------------------------------------------------------
// swarmRunRows: which swarms belong on the chat board
// ---------------------------------------------------------------------------

const swarm = (over = {}) => ({
  id: "swm-abc123",
  objective: "Survey the implementation plan",
  status: "running",
  mode: "read_fanout",
  maxConcurrency: 8,
  progress: { total: 12, completed: 5, running: 3, pending: 4 },
  ...over,
});

test("non-terminal swarms carry their honest row", () => {
  const rows = swarmRunRows([swarm()]);
  assert.deepEqual(
    rows.map((r) => ({ id: r.id, status: r.status, paused: r.paused, topology: r.topology, requestedConcurrency: r.requestedConcurrency })),
    [{ id: "swm-abc123", status: "running", paused: false, topology: "read_fanout", requestedConcurrency: "ceiling 8" }],
  );
  // The progress label is the Team Ops DAG's own language, reused verbatim.
  assert.ok(rows[0].progressLabel.length > 0);
  assert.ok(rows[0].progressLabel !== "undefined");
});

test("paused swarms stay visible and say so — they are not running", () => {
  const [row] = swarmRunRows([swarm({ status: "paused" })]);
  assert.equal(row.paused, true);
  assert.equal(row.status, "paused");
});

test("terminal swarms disappear from chat; Team Ops keeps the history", () => {
  const rows = swarmRunRows([
    swarm({ id: "swm-live" }),
    swarm({ id: "swm-done", status: "completed" }),
    swarm({ id: "swm-died", status: "failed" }),
    swarm({ id: "swm-cut", status: "cancelled" }),
    swarm({ id: "swm-broke", status: "budget_exhausted" }),
    swarm({ id: "swm-stuck", status: "stalled" }),
  ]);
  assert.deepEqual(rows.map((r) => r.id), ["swm-live"]);
});

test("an unreported topology and an auto ceiling are disclosed, never defaulted", () => {
  const [noMode] = swarmRunRows([swarm({ mode: undefined, maxConcurrency: 0 })]);
  assert.equal(noMode.topology, "");
  assert.equal(noMode.requestedConcurrency, "auto-sized");

  const [noConcurrency] = swarmRunRows([swarm({ maxConcurrency: undefined })]);
  assert.equal(noConcurrency.requestedConcurrency, "auto-sized");
});

test("an empty list is an empty answer, not an error", () => {
  assert.deepEqual(swarmRunRows([]), []);
});

// ---------------------------------------------------------------------------
// Component source pins: read-only monitor + ChatView mount
// ---------------------------------------------------------------------------

test("the board mutates nothing: no lifecycle action reaches it", () => {
  const component = read("../components/SwarmRunBoard.tsx");
  for (const forbidden of ["swarmAction(", "createSwarm(", "run-async", "SWARM_ACTIONS"]) {
    assert.ok(!component.includes(forbidden), `SwarmRunBoard must stay read-only; found "${forbidden}"`);
  }
  assert.ok(component.includes("onOpenTeamOps"), "the board hands the operator to Team Ops instead of mutating");
});

test("the board's polling follows the Team Ops discipline", () => {
  const component = read("../components/SwarmRunBoard.tsx");
  assert.ok(component.includes("hasRefreshableSwarms"), "polls only while a swarm is refreshable");
  assert.ok(component.includes("loadingRef"), "prevents overlapping reads");
  assert.ok(!component.includes("setInterval(() => {\n      void loadList();\n    }, 1000)"), "does not invent its own faster cadence");
});

test("ChatView mounts the board and never reconstructs swarm data from prose", () => {
  const chatView = read("../components/ChatView.tsx");
  assert.ok(chatView.includes("from \"@/components/SwarmRunBoard\""), "ChatView imports the board");
  assert.ok(chatView.includes("<SwarmRunBoard onOpenTeamOps={"), "ChatView mounts the board");
});

test("a failed first read is named, never silent, and never 'no swarm is running'", () => {
  const component = read("../components/SwarmRunBoard.tsx");
  assert.ok(
    component.includes("this is not a claim that no swarm is running"),
    "the empty-list-with-error state contradicts 'nothing running'",
  );
  assert.ok(
    /if \(!readError\) return null;/.test(component),
    "a successful empty read may hide the board — a failed one may not",
  );
});

test("the projection maps through the shared teamops projections, not a second copy", () => {
  const lib = read("./swarm-run-board.ts");
  assert.ok(lib.includes('from "@/lib/teamops"'), "swarm-run-board reuses the teamops mapping");
  assert.ok(lib.includes("swarmProgressLabel("), "progress language is the DAG's own");
});
