// swarm-send.test.mjs — the composer's send decision.
//
// The swarm toggle is dispatch, not advice: when on, the prompt is spent as a
// coordinator swarm and `/runs/stream` is never issued. These tests pin the
// mutual exclusivity in BOTH branches and the exact request payloads, so a
// future edit cannot quietly double-spend a prompt.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./swarm-send.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const mod = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(js)}`);

const baseInput = {
  swarmModeEnabled: false,
  content: "Refactor the auth module",
  swarmMode: "auto",
  swarmConcurrency: 4,
  assistantId: "lead_agent",
  modelName: "union-alpha",
};

test("swarm ON: the request is the swarm-create branch and carries no run path", () => {
  const req = mod.buildSwarmSendRequest({ ...baseInput, swarmModeEnabled: true });
  assert.equal(req.kind, "swarm");
  // The create body is exactly what POST /api/swarms takes.
  assert.deepEqual(req.create, {
    goal: "Refactor the auth module",
    mode: "auto",
    max_concurrency: 4,
  });
  // The run-async path is derived from the id the create returns.
  assert.equal(req.runAsyncPath("swarm-abc"), "/swarms/swarm-abc/run-async");
  // No run-stream field exists on this branch: the shapes are disjoint.
  assert.equal("runPath" in req, false);
  assert.equal("assistantId" in req, false);
  assert.equal("modelName" in req, false);
});

test("swarm OFF: the request is the run branch and carries no swarm create", () => {
  const req = mod.buildSwarmSendRequest({ ...baseInput, swarmModeEnabled: false });
  assert.equal(req.kind, "run");
  assert.equal(req.assistantId, "lead_agent");
  assert.equal(req.modelName, "union-alpha");
  assert.equal("create" in req, false);
  assert.equal("runAsyncPath" in req, false);
});

test("swarm ON with a non-default topology forwards mode and concurrency verbatim", () => {
  const req = mod.buildSwarmSendRequest({
    ...baseInput,
    swarmModeEnabled: true,
    swarmMode: "debate",
    swarmConcurrency: 8,
  });
  assert.deepEqual(req.create, {
    goal: "Refactor the auth module",
    mode: "debate",
    max_concurrency: 8,
  });
});

test("swarm OFF leaves the run path empty — ChatView owns the thread prefix", () => {
  // The helper never invents the `/threads/{id}` prefix: the caller knows the
  // thread id. An empty runPath is the honest placeholder.
  const req = mod.buildSwarmSendRequest({ ...baseInput, swarmModeEnabled: false });
  assert.equal(req.runPath, "");
});

test("mutual exclusivity holds for every toggle value", () => {
  for (const swarmModeEnabled of [true, false]) {
    const req = mod.buildSwarmSendRequest({ ...baseInput, swarmModeEnabled });
    if (swarmModeEnabled) {
      assert.equal(req.kind, "swarm");
      assert.equal("runPath" in req, false, "swarm branch must not carry a run path");
    } else {
      assert.equal(req.kind, "run");
      assert.equal("create" in req, false, "run branch must not carry a swarm create");
    }
  }
});
