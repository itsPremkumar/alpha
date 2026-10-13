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
