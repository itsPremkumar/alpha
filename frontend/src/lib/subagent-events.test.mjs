// Contract tests for subagent `task_*` custom events, the only live signal of
// what a delegated subagent is doing (backend: alpha/tools/builtins/task_tool.py).
//
// These pin the honesty rules the UI depends on:
//   1. a terminal outcome is settled by the FIRST terminal event and can never
//      be flipped by a replayed or late frame,
//   2. `task_running` never downgrades a settled task, and a step that arrives
//      out of order never rewinds the progress on screen,
//   3. an absent usage block stays absent — never a fabricated 0,
//   4. an unrecognized `task_*` type is ignored rather than guessed into a
//      status nobody reported,
//   5. task frames never leak into the message list.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./sse-reducer.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { createSseState, reduceSse, streamTasks, streamMessages } = await import(
  `data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`
);

/** Metadata first, so the frames that follow are not held as pending. */
const OPEN = { event: "metadata", data: { run_id: "run-1" } };
let seq = 0;
const taskFrame = (data) => ({ event: "custom", id: String(++seq), data: { run_id: "run-1", ...data } });

const run = (frames) => frames.reduce((state, frame) => reduceSse(state, frame), createSseState());

test("a task_started frame opens a running task with the run's own label", () => {
  const state = run([OPEN, taskFrame({ type: "task_started", task_id: "t1", description: "Map the chat UI", model_name: "gpt-x" })]);
  assert.deepEqual(state.tasks, [
    { id: "t1", status: "running", description: "Map the chat UI", modelName: "gpt-x" },
  ]);
  assert.equal(streamTasks(state), state.tasks);
});

test("the custom frame is still retained on the bounded channel", () => {
  const state = run([OPEN, taskFrame({ type: "task_started", task_id: "t1" })]);
  assert.equal(state.channels.custom?.event, "custom");
  assert.equal(state.channels.custom?.data?.type, "task_started");
});

test("task_running records the latest step, its position, and the reported total", () => {
  const state = run([
    OPEN,
    taskFrame({ type: "task_started", task_id: "t1", description: "Research" }),
    taskFrame({ type: "task_running", task_id: "t1", message: "Reading sse-reducer.ts", message_index: 3, total_messages: 7 }),
  ]);
  assert.equal(state.tasks[0].status, "running");
  assert.equal(state.tasks[0].message, "Reading sse-reducer.ts");
  assert.equal(state.tasks[0].messageIndex, 3);
  assert.equal(state.tasks[0].totalMessages, 7);
  assert.equal(state.tasks[0].description, "Research", "a step must not erase the label it started with");
});

test("a mid-run subscription still tracks a task whose start it missed", () => {
  const state = run([OPEN, taskFrame({ type: "task_running", task_id: "t2", message: "compiling", message_index: 1 })]);
  assert.equal(state.tasks.length, 1);
  assert.equal(state.tasks[0].status, "running");
  assert.equal(state.tasks[0].description, undefined, "an unreported description stays unreported");
});

test("the first terminal event wins; a later one cannot flip the outcome", () => {
  const state = run([
    OPEN,
    taskFrame({ type: "task_started", task_id: "t1" }),
    taskFrame({ type: "task_failed", task_id: "t1", error: "boom" }),
    taskFrame({ type: "task_completed", task_id: "t1", result: "should be ignored" }),
    taskFrame({ type: "task_failed", task_id: "t1", error: "second failure" }),
  ]);
  assert.equal(state.tasks.length, 1);
  assert.equal(state.tasks[0].status, "failed");
  assert.equal(state.tasks[0].error, "boom", "the reported reason must be the first one, not a replay");
});

test("task_running cannot downgrade a settled task back to running", () => {
  const state = run([
    OPEN,
    taskFrame({ type: "task_started", task_id: "t1" }),
    taskFrame({ type: "task_completed", task_id: "t1" }),
    taskFrame({ type: "task_running", task_id: "t1", message: "late step", message_index: 9, total_messages: 9 }),
  ]);
  assert.equal(state.tasks[0].status, "completed");
  assert.equal(state.tasks[0].message, undefined, "a late step must not be filed onto a finished task");
});

test("every terminal event maps to its own status, verbatim", () => {
  for (const [event, status] of [
    ["task_completed", "completed"],
    ["task_failed", "failed"],
    ["task_cancelled", "cancelled"],
    ["task_timed_out", "timed_out"],
  ]) {
    const state = run([OPEN, taskFrame({ type: "task_started", task_id: "t1" }), taskFrame({ type: event, task_id: "t1" })]);
    assert.equal(state.tasks[0].status, status, `${event} must report ${status}`);
    assert.equal(state.tasks.length, 1);
  }
});

test("an unrecognized task_* type is ignored, not guessed into a status", () => {
  const state = run([OPEN, taskFrame({ type: "task_mystery", task_id: "t1" })]);
  assert.deepEqual(state.tasks, [], "nobody reported an outcome, so there is none to show");
});

test("a start arriving after a settled task cannot resurrect it", () => {
  const state = run([
    OPEN,
    taskFrame({ type: "task_started", task_id: "t1" }),
    taskFrame({ type: "task_failed", task_id: "t1" }),
    taskFrame({ type: "task_started", task_id: "t1", description: "restarted" }),
  ]);
  assert.equal(state.tasks[0].status, "failed");
  assert.equal(state.tasks[0].description, undefined);
});

test("custom events that are not task progress are left alone", () => {
  const state = run([
    OPEN,
    taskFrame({ type: "ralph_started", round: 1 }),
    taskFrame({ type: "task_started" }), // no task_id
    taskFrame({ type: 42, task_id: "t1" }), // not a string
    taskFrame({ hello: "world" }), // no type at all
  ]);
  assert.deepEqual(state.tasks, []);
});

test("a task without an id cannot be tracked, because nothing could key it", () => {
  const state = run([OPEN, taskFrame({ type: "task_started", task_id: "" })]);
  assert.deepEqual(state.tasks, []);
});

test("an out-of-order step never rewinds the progress on screen", () => {
  const state = run([
    OPEN,
    taskFrame({ type: "task_started", task_id: "t1" }),
    taskFrame({ type: "task_running", task_id: "t1", message: "step five", message_index: 5, total_messages: 9 }),
    taskFrame({ type: "task_running", task_id: "t1", message: "step two", message_index: 2, total_messages: 9 }),
  ]);
  assert.equal(state.tasks[0].messageIndex, 5);
  assert.equal(state.tasks[0].message, "step five");
});

test("the reported total never shrinks either", () => {
  const state = run([
    OPEN,
    taskFrame({ type: "task_running", task_id: "t1", message_index: 3, total_messages: 12 }),
    taskFrame({ type: "task_running", task_id: "t1", message_index: 4, total_messages: 5 }),
  ]);
  assert.equal(state.tasks[0].totalMessages, 12);
});

test("absent usage stays absent rather than becoming a measured zero", () => {
  const started = run([OPEN, taskFrame({ type: "task_started", task_id: "t1" })]);
  assert.equal(started.tasks[0].usage, undefined);

  const nullUsage = run([OPEN, taskFrame({ type: "task_running", task_id: "t1", usage: null })]);
  assert.equal(nullUsage.tasks[0].usage, undefined, "the backend sends usage:null when nothing was recorded");

  const empty = run([OPEN, taskFrame({ type: "task_running", task_id: "t1", usage: {} })]);
  assert.equal(empty.tasks[0].usage, undefined, "a block with no fields is not a measurement");
});

test("real usage totals are copied verbatim", () => {
  const state = run([
    OPEN,
    taskFrame({ type: "task_running", task_id: "t1", usage: { input_tokens: 120, output_tokens: 30, total_tokens: 150 } }),
    taskFrame({ type: "task_completed", task_id: "t1", usage: { input_tokens: 900, output_tokens: 100, total_tokens: 1000 } }),
  ]);
  assert.deepEqual(state.tasks[0].usage, { input_tokens: 900, output_tokens: 100, total_tokens: 1000 });
});

test("a non-numeric usage field is not reported as a number", () => {
  const state = run([OPEN, taskFrame({ type: "task_running", task_id: "t1", usage: { total_tokens: "many" } })]);
  assert.equal(state.tasks[0].usage, undefined);
});

test("parallel tasks are tracked independently, in first-seen order", () => {
  const state = run([
    OPEN,
    taskFrame({ type: "task_started", task_id: "a", description: "Explore" }),
    taskFrame({ type: "task_started", task_id: "b", description: "Map" }),
    taskFrame({ type: "task_running", task_id: "a", message: "grep" }),
    taskFrame({ type: "task_completed", task_id: "b" }),
  ]);
  assert.deepEqual(state.tasks.map((t) => t.id), ["a", "b"]);
  assert.deepEqual(state.tasks.map((t) => t.status), ["running", "completed"]);
});

test("task frames never leak into the rendered message list", () => {
  const state = run([
    OPEN,
    taskFrame({ type: "task_started", task_id: "t1", description: "Map the chat UI" }),
    taskFrame({ type: "task_running", task_id: "t1", message: "reading files" }),
    taskFrame({ type: "task_completed", task_id: "t1" }),
  ]);
  assert.deepEqual(streamMessages(state), [], "subagent progress is not the assistant's answer");
});

test("a frame carrying no task news leaves the array reference untouched", () => {
  const before = run([OPEN, taskFrame({ type: "task_started", task_id: "t1" })]);
  const after = reduceSse(before, { event: "debug", id: String(++seq), data: { run_id: "run-1", note: "hi" } });
  assert.equal(after.tasks, before.tasks, "a stable reference is what lets React skip the re-render");
});

test("duplicate delivery of the same event id is ignored", () => {
  const frame = { event: "custom", id: "same-id", data: { run_id: "run-1", type: "task_started", task_id: "t1" } };
  const state = run([OPEN, frame, frame]);
  assert.equal(state.tasks.length, 1);
});

test("the live task list is bounded, so a pathological fan-out cannot grow it forever", () => {
  let state = createSseState();
  state = reduceSse(state, OPEN);
  for (let index = 0; index < 200; index++) {
    state = reduceSse(state, taskFrame({ type: "task_started", task_id: `t${index}` }));
  }
  assert.ok(state.tasks.length <= 128, `expected the 128 ceiling, got ${state.tasks.length}`);
  assert.equal(state.tasks.length, 128);
});

test("per-task text is bounded rather than carried unbounded into the UI", () => {
  const state = run([OPEN, taskFrame({ type: "task_running", task_id: "t1", message: "x".repeat(50_000) })]);
  assert.ok(state.tasks[0].message.length <= 2000, `expected bounded step text, got ${state.tasks[0].message.length}`);
});
