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
const panelSource = readFileSync(
  new URL("../components/ConversationGoalPanel.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "ChatView.tsx",
  source,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const handlerSources = new Map();
function visit(node) {
  if (
    ts.isVariableDeclaration(node) &&
    ["handleSaveGoal", "handleClearGoal"].includes(node.name.getText(ast))
  ) {
    handlerSources.set(node.name.getText(ast), node.initializer.getText(ast));
  }
  ts.forEachChild(node, visit);
}
visit(ast);
assert.ok(handlerSources.has("handleSaveGoal"), "ChatView must define its conversation-goal save handler");
assert.ok(handlerSources.has("handleClearGoal"), "ChatView must define its conversation-goal clear handler");

const compile = (text) =>
  ts.transpileModule(text, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  }).outputText;

test("goal save discloses the Gateway reason when a run is in flight", async () => {
  const state = { goalText: null, editing: true, saveError: null, messages: [] };
  const dependencies = {
    activeThreadId: "thread-1",
    activeThreadIdRef: { current: "thread-1" },
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
    setGoalSaveError: (value) => { state.saveError = value; },
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
    `${compile(`const handleSaveGoal = ${handlerSources.get("handleSaveGoal")};`)}; return handleSaveGoal;`,
  )(...Object.values(dependencies));

  await handleSaveGoal();

  assert.deepEqual(state.messages, [
    "Couldn't save the goal: Thread has a run in flight. Set the goal after the run finishes.",
  ]);
  assert.equal(state.goalText, null);
  assert.equal(state.editing, true);
  assert.equal(state.saveError, "Thread has a run in flight. Set the goal after the run finishes.");
  assert.equal(dependencies.goalDraft, "Finish the verified production-readiness task");
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

test("the persistent goal strip opens full details and labels evaluator output honestly", () => {
  assert.match(source, /<ConversationGoalPanel/);
  assert.match(source, /detailsOpen=\{goalDetailsOpen\}/);
  assert.match(panelSource, /aria-haspopup="dialog"/);
  assert.match(panelSource, /role="dialog"/);
  assert.match(panelSource, /aria-modal="true"/);
  assert.match(panelSource, /Open goal details:/);
  assert.match(panelSource, /Edit conversation goal/);
  assert.match(panelSource, /Your draft is still here\. Retry after the current run finishes\./);
  assert.match(panelSource, /whitespace-normal break-words/);
  assert.match(panelSource, /Latest evaluator report/);
  assert.match(panelSource, /elapsedLabel\(details\?\.created_at, now\)/);
  assert.match(panelSource, /Updated \$\{new Intl\.DateTimeFormat/);
  assert.match(panelSource, /Reports satisfied/);
  assert.match(panelSource, /Reports not satisfied/);
  assert.match(panelSource, /This evaluator report is not measured acceptance verification/);
  assert.match(panelSource, /Goal satisfaction has not been measured/);
  assert.match(panelSource, /last_evaluation\.evaluated_at/);
  assert.match(panelSource, /last_evaluation\.run_id/);
  assert.match(panelSource, /does not mean Alpha is continuously working or guaranteed to run for years/);
});

test("goal save does not promote a successful empty Gateway response into an active goal", async () => {
  const state = { goalText: null, goalDetails: null, editing: true, messages: [] };
  const dependencies = {
    activeThreadId: "thread-1",
    activeThreadIdRef: { current: "thread-1" },
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
    setGoalSaveError: () => {},
    setGoalEditing: (value) => { state.editing = value; },
    setGoalDetailsOpen: () => {},
    localThreadMetaRef: { current: {} },
    persistLocalHistory: async (operation) => operation(),
    setThreadMeta: () => {},
    flash: (message) => state.messages.push(message),
    errMsg,
  };
  const handleSaveGoal = new Function(
    ...Object.keys(dependencies),
    `${compile(`const handleSaveGoal = ${handlerSources.get("handleSaveGoal")};`)}; return handleSaveGoal;`,
  )(...Object.values(dependencies));

  await handleSaveGoal();

  assert.equal(state.goalText, null);
  assert.equal(state.goalDetails, null);
  assert.equal(state.editing, false);
  assert.match(state.messages.at(-1), /reported no active goal/);
  assert.equal(dependencies.localThreadMetaRef.current["thread-1"].goal, null);
});

test("a deferred goal save archives its owner without repainting the newly active thread", async () => {
  let resolveSave;
  const writes = [];
  const visibleUpdates = [];
  const dependencies = {
    activeThreadId: "thread-1",
    activeThreadIdRef: { current: "thread-1" },
    goalDraft: "Goal for thread one",
    setGoal: (threadId, objective) => {
      assert.equal(threadId, "thread-1");
      assert.equal(objective, "Goal for thread one");
      return new Promise((resolve) => { resolveSave = resolve; });
    },
    setGoalText: (value) => visibleUpdates.push(["text", value]),
    setGoalDetails: (value) => visibleUpdates.push(["details", value]),
    setGoalReadError: (value) => visibleUpdates.push(["readError", value]),
    setGoalSaveError: (value) => visibleUpdates.push(["saveError", value]),
    setGoalEditing: (value) => visibleUpdates.push(["editing", value]),
    setGoalDetailsOpen: (value) => visibleUpdates.push(["detailsOpen", value]),
    localThreadMetaRef: { current: { "thread-1": { botName: null, goal: null } } },
    persistLocalHistory: async (operation) => operation(),
    setThreadMeta: (threadId, meta) => writes.push([threadId, meta]),
    flash: () => {},
    errMsg,
  };
  const handleSaveGoal = new Function(
    ...Object.keys(dependencies),
    `${compile(`const handleSaveGoal = ${handlerSources.get("handleSaveGoal")};`)}; return handleSaveGoal;`,
  )(...Object.values(dependencies));

  const pending = handleSaveGoal();
  dependencies.activeThreadIdRef.current = "thread-2";
  resolveSave({ goal: "Goal for thread one", status: "active" });
  await pending;

  assert.equal(dependencies.localThreadMetaRef.current["thread-1"].goal, "Goal for thread one");
  assert.deepEqual(writes, [["thread-1", { goal: "Goal for thread one" }]]);
  assert.deepEqual(visibleUpdates, [], "thread one’s late response must not change thread two’s visible controls");
});

test("a deferred goal clear archives its owner without clearing the newly active thread", async () => {
  let resolveClear;
  const writes = [];
  const visibleUpdates = [];
  const dependencies = {
    activeThreadId: "thread-1",
    activeThreadIdRef: { current: "thread-1" },
    clearGoal: (threadId) => {
      assert.equal(threadId, "thread-1");
      return new Promise((resolve) => { resolveClear = resolve; });
    },
    setGoalText: (value) => visibleUpdates.push(["text", value]),
    setGoalDetails: (value) => visibleUpdates.push(["details", value]),
    setGoalReadError: (value) => visibleUpdates.push(["readError", value]),
    setGoalEditing: (value) => visibleUpdates.push(["editing", value]),
    setGoalDetailsOpen: (value) => visibleUpdates.push(["detailsOpen", value]),
    localThreadMetaRef: { current: { "thread-1": { botName: null, goal: "Old goal" } } },
    persistLocalHistory: async (operation) => operation(),
    setThreadMeta: (threadId, meta) => writes.push([threadId, meta]),
    errMsg,
  };
  const handleClearGoal = new Function(
    ...Object.keys(dependencies),
    `${compile(`const handleClearGoal = ${handlerSources.get("handleClearGoal")};`)}; return handleClearGoal;`,
  )(...Object.values(dependencies));

  const pending = handleClearGoal();
  dependencies.activeThreadIdRef.current = "thread-2";
  resolveClear();
  await pending;

  assert.equal(dependencies.localThreadMetaRef.current["thread-1"].goal, null);
  assert.deepEqual(writes, [["thread-1", { goal: null }]]);
  assert.deepEqual(visibleUpdates, [], "thread one’s late response must not clear thread two’s visible controls");
});
