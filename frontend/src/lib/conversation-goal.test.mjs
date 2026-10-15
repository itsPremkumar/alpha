import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";
import { moduleUrl } from "./test-modules.mjs";

const { ApiError, errMsg } = await import(moduleUrl("http"));
const source = readFileSync(
  new URL("../components/ChatView.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "ChatView.tsx",
  source,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
let handlerSource;
function visit(node) {
  if (
    ts.isVariableDeclaration(node) &&
    node.name.getText(ast) === "handleSaveGoal"
  ) {
    handlerSource = node.initializer.getText(ast);
  }
  ts.forEachChild(node, visit);
}
visit(ast);
assert.ok(handlerSource, "ChatView must define its conversation-goal handler");

const compile = (text) =>
  ts.transpileModule(text, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  }).outputText;

test("goal save discloses the Gateway reason when a run is in flight", async () => {
  const state = { goalText: null, editing: true, messages: [] };
  const dependencies = {
    activeThreadId: "thread-1",
    goalDraft: "Finish the verified production-readiness task",
    setGoal: async (threadId, objective) => {
      assert.equal(threadId, "thread-1");
      assert.equal(objective, "Finish the verified production-readiness task");
      throw new ApiError(
        409,
        "Thread has a run in flight. Set the goal after the run finishes.",
      );
    },
    setGoalText: (value) => {
      state.goalText = value;
    },
    setGoalEditing: (value) => {
      state.editing = value;
    },
    localThreadMetaRef: { current: {} },
    persistLocalHistory: async (operation) => operation(),
    setThreadMeta: () => {},
    flash: (message) => state.messages.push(message),
    errMsg,
  };
  const handleSaveGoal = new Function(
    ...Object.keys(dependencies),
    `${compile(`const handleSaveGoal = ${handlerSource};`)}; return handleSaveGoal;`,
  )(...Object.values(dependencies));

  await handleSaveGoal();

  assert.deepEqual(state.messages, [
    "Couldn't save the goal: Thread has a run in flight. Set the goal after the run finishes.",
  ]);
  assert.equal(state.goalText, null);
  assert.equal(state.editing, true);
});

test("thread goal hydration starts before potentially slow history and archive reads", () => {
  let loadMessagesBody;
  function findLoader(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === "loadMessages") {
      loadMessagesBody = node.body?.getText(ast) ?? "";
    }
    ts.forEachChild(node, findLoader);
  }
  findLoader(ast);

  assert.ok(loadMessagesBody, "ChatView must define its thread hydration loader");
  const goalStart = loadMessagesBody.indexOf("void loadGoal();");
  const historyStart = loadMessagesBody.indexOf("await fetchThreadHistoryResult(threadId)");
  assert.notEqual(goalStart, -1, "the goal read must be started independently");
  assert.notEqual(historyStart, -1, "the history read must remain part of thread hydration");
  assert.ok(goalStart < historyStart, "a slow history read must not delay goal receipt hydration");
});

test("goal save does not promote a successful empty Gateway response into an active goal", async () => {
  const state = { goalText: null, goalDetails: null, editing: true, messages: [] };
  const dependencies = {
    activeThreadId: "thread-1",
    goalDraft: "Finish the verified production-readiness task",
    setGoal: async () => ({
      goal: null,
      status: null,
      updated_at: null,
      continuation_count: null,
      max_continuations: null,
      no_progress_count: null,
      max_no_progress_continuations: null,
      last_evaluation: null,
    }),
    setGoalText: (value) => { state.goalText = value; },
    setGoalDetails: (value) => { state.goalDetails = value; },
    setGoalReadError: () => {},
    setGoalEditing: (value) => { state.editing = value; },
    localThreadMetaRef: { current: {} },
    persistLocalHistory: async (operation) => operation(),
    setThreadMeta: () => {},
    flash: (message) => state.messages.push(message),
    errMsg,
  };
  const handleSaveGoal = new Function(
    ...Object.keys(dependencies),
    `${compile(`const handleSaveGoal = ${handlerSource};`)}; return handleSaveGoal;`,
  )(...Object.values(dependencies));

  await handleSaveGoal();

  assert.equal(state.goalText, null);
  assert.equal(state.goalDetails, null);
  assert.equal(state.editing, false);
  assert.match(state.messages.at(-1), /reported no active goal/);
  assert.equal(dependencies.localThreadMetaRef.current["thread-1"].goal, null);
});
