// Contract tests for the delegation-graph projection (phase 3.4).
//
// The backend stamps every `task_*` event with the child's delegation
// identity (`parent_id`, `depth`, `agent_name`, `subagent_type`, `run_id`,
// `swarm_id`; backend: alpha/tools/builtins/task_tool.py). These pin the
// honesty rules the tree view depends on:
//   1. the reducer adopts only validated wire values and only once — the
//      client never guesses a parent edge, depth, or attribution;
//   2. a task whose `parent_id` names an id this stream never saw renders as
//      a root, never attached to a guessed parent, and the count is disclosed;
//   3. a missing `parent_id` is a real root (the lead's direct child);
//   4. a corrupt cycle degrades to roots instead of hanging the render;
//   5. layout depth is presentation geometry — the server-reported
//      `task.depth` is never overwritten.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const graphSource = readFileSync(new URL("./delegation-graph.ts", import.meta.url), "utf8");
const graphCode = ts.transpileModule(graphSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { buildDelegationGraph, hasDelegationNesting } = await import(
  `data:text/javascript;charset=utf-8,${encodeURIComponent(graphCode)}`
);

const sseSource = readFileSync(new URL("./sse-reducer.ts", import.meta.url), "utf8");
const sseCode = ts.transpileModule(sseSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { createSseState, reduceSse } = await import(
  `data:text/javascript;charset=utf-8,${encodeURIComponent(sseCode)}`
);

/** Metadata first, so the frames that follow are not held as pending. */
const OPEN = { event: "metadata", data: { run_id: "run-1" } };
let seq = 0;
const taskFrame = (data) => ({ event: "custom", id: String(++seq), data: { run_id: "run-1", ...data } });
const run = (frames) => frames.reduce((state, frame) => reduceSse(state, frame), createSseState());

const ALL_FIELDS = {
  parent_id: "p1",
  depth: 2,
  agent_name: "researcher",
  subagent_type: "general-purpose",
  swarm_id: "sw-9",
};

/* ── Reducer: adoption of the delegation fields ─────────────────────────── */

test("task_started folds every validated delegation field into the task", () => {
  const state = run([OPEN, taskFrame({ type: "task_started", task_id: "c1", description: "child", ...ALL_FIELDS })]);
  assert.deepEqual(state.tasks, [
    {
      id: "c1",
      status: "running",
      description: "child",
      parentId: "p1",
      depth: 2,
      agentName: "researcher",
      subagentType: "general-purpose",
      swarmId: "sw-9",
    },
  ]);
});

test("the frame's own run_id envelope key is never folded as delegation data", () => {
  const state = run([OPEN, taskFrame({ type: "task_started", task_id: "t1" })]);
  assert.equal(state.tasks[0].runId, undefined);
});

test("frames without phase-3.4 fields add no keys at all (older Gateway)", () => {
  const state = run([OPEN, taskFrame({ type: "task_started", task_id: "t1" })]);
  assert.deepEqual(state.tasks, [{ id: "t1", status: "running" }]);
});

test("invalid delegation values stay absent — never guessed into shape", () => {
  const state = run([
    OPEN,
    taskFrame({
      type: "task_started",
      task_id: "c1",
      parent_id: 42,
      depth: 0,
      agent_name: "",
      subagent_type: { name: "x" },
      run_id: "",
      swarm_id: "x".repeat(513),
    }),
  ]);
  assert.deepEqual(state.tasks, [{ id: "c1", status: "running" }]);
});

test("depth is accepted only inside the validated 1..64 band", () => {
  const depthOf = (depth) =>
    run([OPEN, taskFrame({ type: "task_started", task_id: "t1", depth })]).tasks[0].depth;
  assert.equal(depthOf(1), 1);
  assert.equal(depthOf(64), 64);
  assert.equal(depthOf(65), undefined);
  assert.equal(depthOf(2.5), undefined);
  assert.equal(depthOf("2"), undefined);
});

test("a task's delegation identity is set once — a later frame never rewrites it", () => {
  const state = run([
    OPEN,
    taskFrame({ type: "task_started", task_id: "c1", ...ALL_FIELDS }),
    taskFrame({ type: "task_running", task_id: "c1", parent_id: "other", depth: 9, agent_name: "someone-else" }),
  ]);
  assert.equal(state.tasks[0].parentId, "p1");
  assert.equal(state.tasks[0].depth, 2);
  assert.equal(state.tasks[0].agentName, "researcher");
});

/* ── Graph projection: edges only between known ids ─────────────────────── */

const task = (id, extra = {}) => ({ id, status: "running", ...extra });

test("a reported parent nests its child one level down, siblings in stream order", () => {
  const graph = buildDelegationGraph([
    task("a", { parent_id: undefined }),
    task("b", { parentId: "a", depth: 2 }),
    task("c", { parentId: "a", depth: 2 }),
  ]);
  assert.equal(graph.roots.length, 1);
  const root = graph.roots[0];
  assert.equal(root.task.id, "a");
  assert.equal(root.depth, 1);
  assert.deepEqual(root.children.map((n) => n.task.id), ["b", "c"]);
  assert.equal(root.children[0].depth, 2);
  assert.equal(graph.unseenParents, 0);
  assert.equal(graph.cycleDetached, 0);
});

test("a missing parent_id is a real root — never a guessed edge", () => {
  const graph = buildDelegationGraph([task("a"), task("b"), task("c", { parentId: "b", depth: 2 })]);
  assert.deepEqual(graph.roots.map((n) => n.task.id), ["a", "b"]);
  assert.equal(graph.unseenParents, 0, "an unreported parent is not an unseen parent");
});

test("a parent_id naming an id this stream never saw renders as a root and is counted", () => {
  const graph = buildDelegationGraph([task("c1", { parentId: "vanished-parent", depth: 3 })]);
  assert.deepEqual(graph.roots.map((n) => n.task.id), ["c1"]);
  assert.equal(graph.roots[0].children.length, 0);
  assert.equal(graph.unseenParents, 1);
});

test("a self-edge renders as a root", () => {
  const graph = buildDelegationGraph([task("a", { parentId: "a", depth: 2 })]);
  assert.deepEqual(graph.roots.map((n) => n.task.id), ["a"]);
  assert.equal(graph.cycleDetached, 0);
});

test("a corrupt cycle detaches to roots instead of hanging the render", () => {
  const graph = buildDelegationGraph([
    task("a", { parentId: "c", depth: 3 }),
    task("b", { parentId: "a", depth: 2 }),
    task("c", { parentId: "b", depth: 2 }),
    task("leaf", { parentId: "c", depth: 3 }),
  ]);
  assert.deepEqual([...graph.roots.map((n) => n.task.id)].sort(), ["a", "b", "c"]);
  assert.equal(graph.cycleDetached, 3);
  const cycleRoot = graph.roots.find((n) => n.task.id === "c");
  assert.deepEqual(cycleRoot.children.map((n) => n.task.id), ["leaf"], "a chain leading into the cycle keeps its edge");
  assert.equal(graph.roots.find((n) => n.task.id === "leaf"), undefined);
});

test("layout depth is derived from edges and never overwrites the reported depth", () => {
  const reported = task("grandchild", { parentId: "child", depth: 99 });
  const graph = buildDelegationGraph([task("root"), task("child", { parentId: "root", depth: 50 }), reported]);
  const root = graph.roots[0];
  assert.equal(root.depth, 1);
  assert.equal(root.children[0].depth, 2, "layout depth, not the reported one");
  assert.equal(root.children[0].children[0].depth, 3);
  assert.equal(reported.depth, 99, "the server-reported depth stays untouched");
});

test("hasDelegationNesting is true only when a real edge exists", () => {
  assert.equal(hasDelegationNesting([task("a"), task("b")]), false);
  assert.equal(hasDelegationNesting([]), false);
  assert.equal(hasDelegationNesting([task("a"), task("b", { parentId: "a", depth: 2 })]), true);
  // An unseen parent is not nesting: the child renders as a root.
  assert.equal(hasDelegationNesting([task("b", { parentId: "gone", depth: 2 })]), false);
});

/* ── Wiring pins: the tree mounts, and nothing invents a parent ─────────── */

test("the tree component imports the projection and never writes a parent itself", () => {
  const component = readFileSync(new URL("../components/agent-ui/DelegationGraph.tsx", import.meta.url), "utf8");
  assert.match(component, /buildDelegationGraph/);
  assert.doesNotMatch(component, /parentId:/, "the component must not construct delegation fields");
});

test("ChatView mounts the tree and keeps the flat list only as the fallback", () => {
  const chatView = readFileSync(new URL("../components/ChatView.tsx", import.meta.url), "utf8");
  assert.match(chatView, /from "@\/components\/agent-ui\/DelegationGraph"/);
  assert.match(chatView, /hasDelegationNesting\(subagentTasks\)/);
  assert.match(chatView, /<DelegationGraph tasks=\{subagentTasks\} \/>/);
});
