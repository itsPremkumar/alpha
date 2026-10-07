// swarm-structure-wiring.test.mjs — the panel that renders a swarm must stay
// reachable, and the reads it depends on must stay mounted.
//
// ## The defect this pins
//
// On 2026-10-06 `frontend/src/lib/teamops.ts` exported `swarmDetails()` and
// `swarmMetrics()` against `GET /api/swarms/{id}` and `.../metrics`, and
// **nothing in `src/` imported either of them**. `grep` for the names returned
// only their own definitions. So the whole structure of a swarm — the task DAG,
// the assigned workers, the elected leader, the team load — was fetched by
// nobody, and the list rendered one line of aggregate counters per swarm.
//
// That is a wiring defect, and a wiring defect is invisible to a unit test of
// the pure view module: `swarm-structure-view.test.mjs` passes just as happily
// when nothing calls it. These pins are the ones that fail when the panel is
// orphaned again.
//
// They are SOURCE pins, which is the right tool here and the wrong tool for
// behaviour: the claim is "a section renders this and calls that", which no
// runtime assertion in a Node test can observe. Each pin names the specific
// string a refactor would drop.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");

const section = read("../components/sections/TeamOpsSection.tsx");
const teamops = read("./teamops.ts");
const view = read("./swarm-structure-view.ts");

/** Collapse whitespace so a prettier reflow cannot fail a wiring pin. */
const flat = (s) => s.replace(/\s+/g, " ");

// ---------------------------------------------------------------------------
// The routes
// ---------------------------------------------------------------------------

test("the event read keeps the route that ends in /events", () => {
  // Scoped to the `swarmEvents` function body. The file has an unrelated
  // `/events` occurrence (`/bots/events`), so a whole-file match would still pass
  // after this route was dropped.
  const start = teamops.indexOf("export async function swarmEvents");
  assert.ok(start > 0, "swarmEvents must exist");
  const body = teamops.slice(start, teamops.indexOf("\n}", start));
  assert.match(body, /\/swarms\/\$\{encodeURIComponent\(id\)\}\/events`/);
});

test("the event read rejects instead of resolving to an empty log", () => {
  // A failed read that resolved to `[]` would claim "the server recorded no
  // events" for a log nobody could open. The view distinguishes the two, and it
  // can only do that if this throws.
  const body = teamops.slice(teamops.indexOf("export async function swarmEvents"));
  const fn = body.slice(0, body.indexOf("\n}"));
  assert.doesNotMatch(
    fn.replace(/\s+/g, " "),
    /catch[\s\S]{0,80}return \[\]/,
    "swarmEvents must not swallow a failure into an empty timeline",
  );
  assert.match(fn, /return get</, "the read goes through the shared client, which rejects on non-2xx");
});

// ---------------------------------------------------------------------------
// The dependencies the panel needs are actually imported
// ---------------------------------------------------------------------------

test("the section calls the detail and event reads it renders", () => {
  // Asserting the NAME is not enough: `swarmDetails` also appears in a doc
  // comment and in a call, so removing the import would still match. The
  // meaningful claim is that both are CALLED with the swarm id.
  for (const name of ["swarmDetails", "swarmEvents"]) {
    assert.match(
      section,
      new RegExp(`${name}\\(props\\.swarmId\\)`),
      `${name}(props.swarmId) must be called — an imported-but-uncalled read renders nothing`,
    );
  }
});

test("the section imports the pure view module rather than re-deriving sentences", () => {
  // Every sentence lives in the view module so a list row and its own panel
  // cannot contradict each other for the same field.
  assert.match(section, /from "@\/lib\/swarm-structure-view"/);
  assert.match(section, /swarmStructureView\(/);
});

// ---------------------------------------------------------------------------
// The panel is reachable
// ---------------------------------------------------------------------------

test("the swarm row renders a control that opens the structure panel", () => {
  assert.match(section, /\bStructure\b/, "the panel needs a control; an unreachable panel is dead code");
  assert.match(section, /openSwarmStructure/, "the open/closed state must exist");
});

test("the structure panel is actually mounted in the swarm row", () => {
  // The toggle is not enough: a state variable that nothing renders is the same
  // dead code one step further in.
  const mount = section.match(/openSwarmStructure === s\.id && <SwarmStructurePanel swarmId=\{s\.id\} \/>/);
  assert.ok(mount, "SwarmStructurePanel must be mounted when its swarm is open");
});

test("the panel is defined in this file, not merely referenced", () => {
  assert.match(section, /function SwarmStructurePanel\(props: \{ swarmId: string \}\)/);
});

// ---------------------------------------------------------------------------
// The two reads settle independently
// ---------------------------------------------------------------------------

test("the panel's own reads use allSettled, so one failure cannot blank the other", () => {
  // Scoped to `SwarmStructurePanel`, NOT the whole file. Two other call sites
  // (lines ~158 and ~179) legitimately use `Promise.allSettled`, so a file-wide
  // `doesNotMatch(/Promise.all\(/)` would be checking unrelated code — and a
  // file-wide `match(/allSettled/)` would pass even if THIS pair used `all`.
  const start = section.indexOf("function SwarmStructurePanel");
  assert.ok(start > 0, "SwarmStructurePanel must exist");
  const body = section.slice(start, section.indexOf("\nfunction SwarmMessagesPanel", start));
  assert.match(body, /Promise\.allSettled/, "the two reads must settle independently");
  assert.doesNotMatch(body, /Promise\.all\(/, "Promise.all would make one failure hide the other");
});

test("a failed event read passes undefined, not an empty array", () => {
  // `[]` means "the server reported no events". Passing it on failure would make
  // the view print that sentence for a log that was never read.
  const body = section.slice(section.indexOf("function SwarmStructurePanel"));
  assert.match(body, /events\.status === "fulfilled" \? events\.value : undefined/);
});

test("a failed detail read is disclosed rather than shown as an empty swarm", () => {
  const body = section.slice(section.indexOf("function SwarmStructurePanel"));
  assert.match(body, /not because the plan has no tasks/);
});

// ---------------------------------------------------------------------------
// The view module keeps the honesty rules
// ---------------------------------------------------------------------------

test("the view reads `state` and never `status`", () => {
  // The live task payload has no `status` key. `task.status` is `undefined` for
  // every task, which renders a full DAG of blank rows.
  assert.match(view, /t\.state\b/, "the task state field is `state`");
  assert.doesNotMatch(
    view.replace(/taskId|status not reported/g, ""),
    /\bt\.status\b/,
    "the task payload has no `status` field to read",
  );
});

test("the view names each absence rather than defaulting to a zero", () => {
  for (const phrase of [
    "state not reported by the server",
    "no duration reported",
    "tokens not reported",
    "unassigned — no worker has claimed this task",
    "no leader election reported by the server",
    "team assignment not reported by the server",
  ]) {
    assert.ok(view.includes(phrase), `the view must be able to say "${phrase}"`);
  }
});

test("the section renders those absence notes and not a bare zero", () => {
  // A view module can be correct while the section discards its notes and prints
  // `0`, which is the defect this whole feature is about.
  const body = section.slice(section.indexOf("function SwarmStructurePanel"));
  assert.match(body, /t\.durationNote/, "an absent duration must render its own sentence");
  assert.match(body, /t\.tokenNote/, "absent tokens must render their own sentence");
  assert.match(body, /t\.workerNote/, "an unassigned task must render its own sentence");
  assert.match(body, /view\.leader\.note/, "a missing leader must render its own sentence");
});
