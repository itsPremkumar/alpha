// goals.test.mjs — honesty pins for the goals/missions/integrity client:
// server statuses and scores pass through untouched, paths match
// goal_contracts.py / goal_integrity.py / missions.py exactly, and failures
// reject instead of returning fabricated records.
// Pure Node test (node --test src/lib/goals.test.mjs): transpiles goals.ts and
// rewrites its ./http import to a data: URL stub — same pattern as
// load-failure-honesty.test.mjs.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const httpStub = `
let handler = (path, method, payload) => { throw new Error("no stub response configured"); };
export function setHttpHandler(fn) { handler = fn; }
export async function get(path) { return handler(path, "GET", undefined); }
export async function send(path, method, payload) { return handler(path, method, payload); }
export function pick(obj, keys, fallback) {
  if (obj && typeof obj === "object") {
    const rec = obj;
    for (const k of keys) { if (rec[k] !== undefined && rec[k] !== null) return rec[k]; }
  }
  return fallback;
}
export function asList(body, keys) {
  if (Array.isArray(body)) return body;
  for (const k of keys) {
    if (body && typeof body === "object" && Array.isArray(body[k])) return body[k];
  }
  return [];
}
export function errMsg(err) { return err instanceof Error ? err.message : "Something went wrong."; }
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
export const DEFAULT_TIMEOUT_MS = 60000;
export const GATEWAY_BASE = "/api";
`;
const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;
const stubUrl = toDataUrl(httpStub);

const source = readFileSync(new URL("./goals.ts", import.meta.url), "utf8");
let code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
code = code.replace(/from\s+"\.\/http"/, `from "${stubUrl}"`);

const goals = await import(toDataUrl(code));
const { setHttpHandler } = await import(stubUrl);

test("listGoalContracts maps the strict GoalContract fields", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/goals/contracts");
    assert.equal(method, "GET");
    return [
      { id: "gc_1", owner_id: "u1", created_at: 1760000000.25, updated_at: 1760000010, objective: "Ship digest", status: "active" },
    ];
  });
  const list = await goals.listGoalContracts();
  assert.deepEqual(list, [
    { id: "gc_1", owner_id: "u1", created_at: 1760000000.25, updated_at: 1760000010, objective: "Ship digest", status: "active" },
  ]);
});

test("a non-array contracts payload maps to an empty list — no fabricated contracts", async () => {
  setHttpHandler(() => ({ contracts: "nope" }));
  assert.deepEqual(await goals.listGoalContracts(), []);
});

test("createGoalContract posts exactly {objective} and keeps the returned status", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/goals/contracts");
    assert.equal(method, "POST");
    assert.deepEqual(payload, { objective: "Ship digest" });
    return { id: "gc_2", owner_id: "u1", created_at: 1, updated_at: 1, objective: "Ship digest", status: "active" };
  });
  const contract = await goals.createGoalContract("Ship digest");
  assert.equal(contract.status, "active");
  assert.equal(contract.id, "gc_2");
});

test("createGoalPlan posts the content object under the exact plans path", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/goals/contracts/gc_1/plans");
    assert.equal(method, "POST");
    assert.deepEqual(payload, { content: { steps: ["build", "verify"] } });
    return { id: "pl_1", owner_id: "u1", created_at: 1, updated_at: 1, contract_id: "gc_1", version: 1, content: { steps: ["build", "verify"] }, status: "draft" };
  });
  const plan = await goals.createGoalPlan("gc_1", { steps: ["build", "verify"] });
  assert.equal(plan.version, 1);
  assert.equal(plan.status, "draft");
  assert.equal(plan.contract_id, "gc_1");
});

test("approveGoalPlan targets the version-scoped approve path", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/goals/contracts/gc_1/plans/2/approve");
    assert.equal(method, "POST");
    return { id: "pl_2", owner_id: "u1", created_at: 1, updated_at: 2, contract_id: "gc_1", version: 2, content: {}, status: "approved" };
  });
  const plan = await goals.approveGoalPlan("gc_1", 2);
  assert.equal(plan.status, "approved");
});

test("transitionGoalAttempt sends {status} to the transition path and preserves it", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/goals/contracts/gc_1/attempts/at_9/transition");
    assert.equal(method, "POST");
    assert.deepEqual(payload, { status: "failed" });
    return { id: "at_9", owner_id: "u1", created_at: 1, updated_at: 2, plan_id: "pl_1", intent: "do it", status: "failed" };
  });
  const attempt = await goals.transitionGoalAttempt("gc_1", "at_9", "failed");
  assert.equal(attempt.status, "failed");
  assert.equal(attempt.plan_id, "pl_1");
});

test("auditGoalIntegrity posts goal+subtasks and preserves drift flags", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/goal-integrity/audit");
    assert.equal(method, "POST");
    assert.deepEqual(payload, { mission_goal: "Ship digest", subtasks: ["build the email", "kafka cluster"] });
    return {
      mission_goal: "Ship digest",
      audited_subtasks_count: 2,
      drift_score: 0.85,
      is_aligned: false,
      scope_creep_detected: true,
      overengineering_detected: false,
      flagged_tasks: [{ task_id: "task-1", description: "kafka cluster", reason: "Introduced unrequested complex infrastructure: kafka." }],
      findings: ["High goal drift detected (score=0.85)."],
      recommendations: ["Realign task queue."],
      timestamp: 1760000000,
    };
  });
  const report = await goals.auditGoalIntegrity("Ship digest", ["build the email", "kafka cluster"]);
  assert.equal(report.drift_score, 0.85);
  assert.equal(report.is_aligned, false);
  assert.equal(report.scope_creep_detected, true);
  assert.equal(report.overengineering_detected, false);
  assert.equal(report.flagged_tasks.length, 1);
  assert.match(report.flagged_tasks[0].reason, /unrequested complex infrastructure/);
});

test("an aligned audit stays aligned — boolean true passes through", async () => {
  setHttpHandler(() => ({
    mission_goal: "g", audited_subtasks_count: 1, drift_score: 0.15, is_aligned: true,
    scope_creep_detected: false, overengineering_detected: false,
    flagged_tasks: [], findings: ["ok"], recommendations: [], timestamp: 1,
  }));
  const report = await goals.auditGoalIntegrity("g", ["build tests"]);
  assert.equal(report.is_aligned, true);
  assert.equal(report.drift_score, 0.15);
  assert.deepEqual(report.flagged_tasks, []);
});

test("listMissions maps the {missions,count} envelope and mission fields", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/missions");
    assert.equal(method, "GET");
    return {
      missions: [
        { mission_id: "msn-1", owner: "local-user", objective: "Migrate billing", constraints: {}, budget: {}, status: "draft", thread_ids: ["thr-1"], artifacts: [], created_at: 1760000000, updated_at: 1760000000 },
      ],
      count: 1,
    };
  });
  const list = await goals.listMissions();
  assert.equal(list.length, 1);
  assert.equal(list[0].mission_id, "msn-1");
  assert.equal(list[0].status, "draft");
  assert.deepEqual(list[0].thread_ids, ["thr-1"]);
});

test("transitionMission puts ?to= in the query string (missions.py query param)", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/missions/msn-1/transition?to=paused");
    assert.equal(method, "POST");
    return { mission_id: "msn-1", owner: "local-user", objective: "Migrate billing", constraints: {}, budget: {}, status: "paused", thread_ids: [], artifacts: [], created_at: 1, updated_at: 2 };
  });
  const mission = await goals.transitionMission("msn-1", "paused");
  assert.equal(mission.status, "paused");
});

test("attachMissionThread posts {thread_id} to the threads path", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/missions/msn-1/threads");
    assert.equal(method, "POST");
    assert.deepEqual(payload, { thread_id: "thr-9" });
    return { mission_id: "msn-1", owner: "o", objective: "obj", constraints: {}, budget: {}, status: "active", thread_ids: ["thr-9"], artifacts: [], created_at: 1, updated_at: 2 };
  });
  const mission = await goals.attachMissionThread("msn-1", "thr-9");
  assert.deepEqual(mission.thread_ids, ["thr-9"]);
});

test("createMission sends objective with empty constraints/budget defaults", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/missions");
    assert.equal(method, "POST");
    assert.deepEqual(payload, { objective: "Long lived objective", constraints: {}, budget: {} });
    return { mission_id: "msn-2", owner: "o", objective: "Long lived objective", constraints: {}, budget: {}, status: "draft", thread_ids: [], artifacts: [], created_at: 1, updated_at: 1 };
  });
  const mission = await goals.createMission("Long lived objective");
  assert.equal(mission.status, "draft");
});

test("a failed mission list rejects instead of returning an empty view", async () => {
  setHttpHandler(() => {
    throw new Error("gateway down");
  });
  await assert.rejects(() => goals.listMissions(), /gateway down/);
  await assert.rejects(() => goals.listGoalContracts(), /gateway down/);
});

test("status whitelists mirror the server models", () => {
  assert.deepEqual([...goals.ATTEMPT_STATUSES], ["pending", "running", "succeeded", "failed", "cancelled"]);
  assert.deepEqual([...goals.MISSION_TRANSITION_TARGETS], ["active", "paused", "completed", "cancelled"]);
});
