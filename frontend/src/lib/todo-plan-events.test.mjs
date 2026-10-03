// Contract tests for `todos_updated` custom events -- the agent's own
// execution plan reaching the browser (backend:
// alpha/agents/todo_events.py, emitted by the write_todos wrapper in
// alpha/agents/middlewares/todo_middleware.py).
//
// The plan existed in graph state long before this: `write_todos` wrote to the
// `todos` channel and the run carried it faithfully, but nothing published it
// and nothing consumed it. These tests pin the rules the UI now depends on:
//
//   1. the newest report REPLACES the plan -- merging would resurrect items the
//      model deliberately dropped and show abandoned work as outstanding,
//   2. counters only move forward: a replayed or reordered frame must not make
//      a finished run look unfinished,
//   3. an unrecognized status degrades to `pending`, never to dropped -- hiding
//      a step the run reported would overstate how much is done,
//   4. `reportedAtAll` separates "no plan yet" from "an empty plan", which are
//      different facts and collapse into a lying panel if merged,
//   5. `settled` counts cancelled work: a dropped step is an outcome the agent
//      chose, not work left undone,
//   6. a frame that cannot move the plan forward is dropped WHOLESALE, so the
//      object reference stays stable and React skips the re-render,
//   7. there is deliberately no `failed` status -- the model never reports one,
//      so the UI must not be able to draw one.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./sse-reducer.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { createSseState, reduceSse, streamMessages, streamTodos, emptyTodoPlan } = await import(
  `data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`
);

const OPEN = { event: "metadata", data: { run_id: "run-1" } };
let seq = 0;
const todoFrame = (todos, extra = {}) => ({
  event: "custom",
  data: { type: "todos_updated", todos, ...extra },
});

/** Reduce a list of frames from a fresh run. */
function fold(frames) {
  let state = reduceSse(createSseState(), OPEN);
  for (const frame of frames) state = reduceSse(state, { ...frame, id: `e${++seq}` });
  return state;
}

const item = (content, status) => ({ content, status });

test("a run that never published a plan reports none at all", () => {
  const plan = streamTodos(reduceSse(createSseState(), OPEN));
  assert.equal(plan.reportedAtAll, false, "an unreported plan must not render as an empty one");
  assert.equal(plan.items.length, 0);
  assert.equal(plan.progress.total, 0);
});

test("the first plan is adopted verbatim", () => {
  const plan = streamTodos(fold([todoFrame([item("Read the config", "completed"), item("Write the patch", "in_progress")])]));
  assert.equal(plan.reportedAtAll, true);
  assert.equal(plan.items.length, 2);
  assert.equal(plan.items[0].status, "completed");
  assert.equal(plan.items[1].status, "in_progress");
  assert.equal(plan.progress.total, 2);
  assert.equal(plan.progress.completed, 1);
  assert.equal(plan.progress.in_progress, 1);
  assert.equal(plan.progress.settled, 1);
});

test("the newest report replaces the plan rather than merging into it", () => {
  // The model dropped "Read the config" from the plan. Merging would leave it
  // on screen as still pending, which is the abandoned-work lie this avoids.
  const plan = streamTodos(fold([
    todoFrame([item("Read the config", "pending"), item("Write the patch", "pending")]),
    todoFrame([item("Write the patch", "in_progress")]),
  ]));
  assert.equal(plan.items.length, 1);
  assert.equal(plan.items[0].content, "Write the patch");
  assert.equal(plan.progress.pending, 0);
});

test("counters never move backwards", () => {
  // A replayed earlier frame arrives after the later one. Honouring it would
  // un-finish a run that already reported the work complete.
  const plan = streamTodos(fold([
    todoFrame([item("a", "pending"), item("b", "pending")], { progress: { total: 2, completed: 0, settled: 0 } }),
    todoFrame([item("a", "completed"), item("b", "completed")], { progress: { total: 2, completed: 2, settled: 2 } }),
    todoFrame([item("a", "pending"), item("b", "pending")], { progress: { total: 2, completed: 0, settled: 0 } }),
  ]));
  assert.equal(plan.progress.settled, 2, "a stale frame must not un-complete finished work");
});

test("an unrecognized status degrades to pending, never to dropped", () => {
  const plan = streamTodos(fold([todoFrame([item("mystery step", "half_done")])]));
  assert.equal(plan.items.length, 1, "a reported step must never be hidden");
  assert.equal(plan.items[0].status, "pending");
  assert.equal(plan.progress.pending, 1);
});

test("there is no failed status to render", () => {
  // The wire contract has exactly four states. If a fifth ever appears it is a
  // contract change, and TaskList must be updated deliberately -- not silently
  // degraded into `pending`, which would misreport it as merely unfinished.
  const plan = streamTodos(fold([todoFrame([item("x", "completed"), item("y", "cancelled")])]));
  const statuses = new Set(plan.items.map((i) => i.status));
  assert.deepEqual([...statuses].sort(), ["cancelled", "completed"]);
});

test("cancelled work counts as settled, not as outstanding", () => {
  const plan = streamTodos(fold([todoFrame([item("kept", "completed"), item("dropped", "cancelled")])]));
  assert.equal(plan.progress.settled, 2);
  assert.equal(plan.progress.pending, 0);
  assert.equal(plan.progress.cancelled, 1);
});

test("server counters are trusted over locally counted ones", () => {
  // The server knows about items it clipped away; counting only what arrived
  // would understate the work and overstate the completion.
  const plan = streamTodos(fold([
    todoFrame([item("a", "completed")], { progress: { total: 4, completed: 1, in_progress: 1, pending: 2, cancelled: 0, settled: 1 } }),
  ]));
  assert.equal(plan.progress.total, 4);
  assert.equal(plan.progress.pending, 2);
});

test("counters fall back to counting the held items when none were sent", () => {
  const plan = streamTodos(fold([todoFrame([item("a", "completed"), item("b", "pending")])]));
  assert.equal(plan.progress.total, 2);
  assert.equal(plan.progress.completed, 1);
  assert.equal(plan.progress.pending, 1);
});

test("an item with no usable text is skipped, not rendered as an empty row", () => {
  const plan = streamTodos(fold([todoFrame([item("   ", "pending"), { status: "pending" }, item("real", "pending")])]));
  assert.equal(plan.items.length, 1);
  assert.equal(plan.items[0].content, "real");
});

test("long content is clipped and says so", () => {
  const plan = streamTodos(fold([todoFrame([item("x".repeat(2000), "pending")])]));
  assert.ok(plan.items[0].content.length <= 500);
  assert.ok(plan.items[0].content.endsWith("…"), "a clipped step must be visibly clipped");
});

test("an over-long plan is capped and reported as truncated", () => {
  const many = Array.from({ length: 400 }, (_, i) => item(`step ${i}`, "pending"));
  const plan = streamTodos(fold([todoFrame(many, { reported: 400, truncated: true })]));
  assert.equal(plan.items.length, 200);
  assert.equal(plan.truncated, true, "dropped steps must be stated, never silently hidden");
  assert.equal(plan.reported, 400);
});

test("truncation is inferred when the server under-reports", () => {
  const plan = streamTodos(fold([todoFrame([item("a", "pending")], { reported: 9 })]));
  assert.equal(plan.truncated, true, "reported > held means something was dropped");
});

test("a frame that changes nothing returns the same reference", () => {
  // React bails out on an unchanged reference. Returning a fresh object for a
  // no-op frame would re-render the whole plan on every text delta.
  const state = fold([todoFrame([item("a", "completed"), item("b", "in_progress")])]);
  const before = streamTodos(state);
  const after = streamTodos(reduceSse(state, { ...todoFrame([item("a", "completed"), item("b", "in_progress")]), id: "e-dup" }));
  assert.equal(after, before, "an identical plan must keep its reference");
});

test("a plan-bearing frame does not leak into the message list", () => {
  const state = fold([todoFrame([item("a", "completed")])]);
  assert.equal(streamMessages(state).length, 0);
});

test("junk payloads never throw", () => {
  for (const todos of [null, "nope", 42, [null, 3, {}, []], [{ content: { nested: true } }]]) {
    assert.doesNotThrow(() => fold([todoFrame(todos)]), `threw on ${JSON.stringify(todos)}`);
  }
});

test("emptyTodoPlan is the unreported shape", () => {
  const plan = emptyTodoPlan();
  assert.equal(plan.reportedAtAll, false);
  assert.equal(plan.items.length, 0);
  assert.equal(plan.progress.settled, 0);
});