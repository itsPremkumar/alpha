import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const httpStub = `
let handler = () => { throw new Error("no stub response configured"); };
export function setHttpHandler(fn) { handler = fn; }
export async function get(path) { return handler(path, "GET", undefined); }
export async function send(path, method, payload) { return handler(path, method, payload); }
export function asList(body, keys) {
  if (Array.isArray(body)) return body;
  for (const key of keys) if (body && Array.isArray(body[key])) return body[key];
  return [];
}
export function pick(obj, keys, fallback) {
  if (obj && typeof obj === "object") for (const key of keys) {
    if (obj[key] !== undefined && obj[key] !== null) return obj[key];
  }
  return fallback;
}
`;
const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;
const httpUrl = toDataUrl(httpStub);
const source = readFileSync(new URL("./threads-ext.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText.replace(/from\s+"\.\/http"/, `from "${httpUrl}"`);
const threads = await import(toDataUrl(compiled));
const { setHttpHandler } = await import(httpUrl);

test("fetchGoal preserves the server's evaluation and continuation measurements", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/threads/thread-1/goal");
    assert.equal(method, "GET");
    return { goal: {
      objective: "Ship the feature",
      status: "active",
      updated_at: "2026-10-15T10:00:00Z",
      continuation_count: 2,
      max_continuations: 8,
      no_progress_count: 1,
      max_no_progress_continuations: 2,
      last_evaluation: {
        satisfied: false,
        blocker: "goal_not_met_yet",
        reason: "The integration test is still failing.",
        evidence_summary: "pytest exited 1",
        run_id: "run-1",
        evaluated_at: "2026-10-15T10:00:00Z",
        stand_down_reason: "max_continuations_reached",
      },
    } };
  });

  assert.deepEqual(await threads.fetchGoal("thread-1"), {
    goal: "Ship the feature",
    status: "active",
    created_at: null,
    updated_at: "2026-10-15T10:00:00Z",
    continuation_count: 2,
    max_continuations: 8,
    no_progress_count: 1,
    max_no_progress_continuations: 2,
    last_evaluation: {
      satisfied: false,
      blocker: "goal_not_met_yet",
      reason: "The integration test is still failing.",
      evidence_summary: "pytest exited 1",
      run_id: "run-1",
      evaluated_at: "2026-10-15T10:00:00Z",
      stand_down_reason: "max_continuations_reached",
    },
  });
});

test("fetchGoal preserves a terminal completion receipt for the UI", async () => {
  setHttpHandler(() => ({ goal: {
    objective: "Ship the feature",
    status: "completed",
    continuation_count: 3,
    max_continuations: 8,
    last_evaluation: {
      satisfied: true,
      blocker: "none",
      reason: "Acceptance evidence is present.",
      evidence_summary: "The final verification passed.",
      run_id: "run-final",
      evaluated_at: "2026-10-15T12:30:00Z",
    },
  } }));

  assert.deepEqual(await threads.fetchGoal("thread-complete"), {
    goal: "Ship the feature",
    status: "completed",
    created_at: null,
    updated_at: null,
    continuation_count: 3,
    max_continuations: 8,
    no_progress_count: null,
    max_no_progress_continuations: null,
    last_evaluation: {
      satisfied: true,
      blocker: "none",
      reason: "Acceptance evidence is present.",
      evidence_summary: "The final verification passed.",
      run_id: "run-final",
      evaluated_at: "2026-10-15T12:30:00Z",
      stand_down_reason: null,
    },
  });
});

test("fetchGoal keeps an explicit server-side absence distinct from a read failure", async () => {
  setHttpHandler(() => ({ goal: null }));
  assert.deepEqual(await threads.fetchGoal("thread-2"), {
    goal: null,
    status: null,
    created_at: null,
    updated_at: null,
    continuation_count: null,
    max_continuations: null,
    no_progress_count: null,
    max_no_progress_continuations: null,
    last_evaluation: null,
  });
});

test("fetchGoal propagates Gateway failures instead of returning no goal", async () => {
  setHttpHandler(() => { throw new Error("Gateway unavailable"); });
  await assert.rejects(() => threads.fetchGoal("thread-3"), /Gateway unavailable/);
});

test("fetchGoal rejects malformed success envelopes", async () => {
  setHttpHandler(() => ({}));
  await assert.rejects(() => threads.fetchGoal("thread-4"), /malformed goal response/);
});

test("setGoal uses the exact Gateway request and returns the accepted state", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/threads/thread-5/goal");
    assert.equal(method, "PUT");
    assert.deepEqual(payload, { objective: "Verify the task" });
    return { goal: { objective: "Verify the task", status: "active", continuation_count: 0, max_continuations: 8 } };
  });
  const result = await threads.setGoal("thread-5", "Verify the task");
  assert.equal(result.goal, "Verify the task");
  assert.equal(result.status, "active");
  assert.equal(result.continuation_count, 0);
  assert.equal(result.max_continuations, 8);
});

test("goal state preserves Gateway-reported creation and update times", async () => {
  setHttpHandler(() => ({ goal: {
    objective: "Keep working on the durable goal",
    status: "active",
    created_at: "2026-10-14T10:00:00Z",
    updated_at: "2026-10-15T10:00:00Z",
  } }));
  const result = await threads.fetchGoal("thread-time");
  assert.equal(result.created_at, "2026-10-14T10:00:00Z");
  assert.equal(result.updated_at, "2026-10-15T10:00:00Z");
});
