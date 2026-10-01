// project-inspector.test.mjs — the full per-project read.
//
// The defect this pins: the Projects view answered "which projects exist, and
// how many chats and bots does this one hold". Every other `GET /projects/{id}/*`
// route was unreachable from it, and the surfaces that did expose them (Workforce)
// kept their own project picker defaulting to the FIRST project — so opening a
// project and hunting for its state answered with a different project.
//
// Three things are asserted here:
//   1. The exact routes and verbs. A read that silently hits a route that does
//      not exist is a permanently empty panel that looks like a quiet project.
//   2. Partial reads. One failing route blanks one block and is named; it must
//      not blank the other sixteen, and it must never reject the whole read.
//   3. The honesty inversions — absent is not zero, `last_verified: null` is
//      never verified, an unreadable room is not a solo crew, and a kill switch
//      that was not reported is not a healthy "off".
//
// The transport is stubbed, so these run with no Gateway.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./project-inspector.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

/** Load the module against a stubbed HTTP layer driven by `handler`. */
function loadInspector(handler) {
  const calls = [];
  const exports = {};
  const http = {
    async get(path) {
      calls.push({ path, method: "GET" });
      return handler(path);
    },
  };
  new Function("exports", "require", compiled)(exports, (dependency) => {
    assert.equal(dependency, "./http", "the inspector must reach the network only through lib/http");
    return http;
  });
  return { inspector: exports, calls };
}

/** A complete, well-formed payload for every route the inspector reads. */
function payloads(overrides = {}) {
  const base = {
    "/projects/p1": { id: "p1", name: "Launch", instructions: "ship it", presentation: { theme: "dark" }, status: "active", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-02-01T00:00:00Z" },
    "/projects/p1/state": { project_id: "p1", goal: "ship", phase: "build", active_tasks: 2, blocked_tasks: 0, completed_tasks: 5, failed_tasks: 1, active_agents: 3, arch_version: "v3", open_conflicts: 0, open_risks: ["slow CI"], last_verified: null, latest_decision: "d1", updated_at: "2026-02-01T00:00:00Z" },
    "/projects/p1/crew": { project_id: "p1", members: [{ bot_name: "alice", role_in_project: "lead", status: "active", current_task_id: null, blocked_reason: null, last_activity: "2026-02-01T00:00:00Z" }], room: null, collaboration: { orchestration_mode: "moderated", moderator: "alice", max_concurrent_speakers: 3, mention_policy: "strict", auto_handoff: true, conflict_policy: "moderator", lock_policy: "advisory", require_evidence: true, memory_budget_chars: 6000, transcript_digest_n: 40, standup_interval_turns: 10 }, shared_memory: {}, state: {}, active_locks: [], recent_events: [], updated_at: "2026-02-01T00:00:00Z" },
    "/projects/p1/threads?limit=500&offset=0": [{ thread_id: "t1", display_name: "Kickoff", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-02T00:00:00Z", metadata: {} }],
    "/projects/p1/memory": { project_id: "p1", goal: "ship", phase: "build", recent_decisions: [], active_locks: [], constitution_hash: "none", transcript_digest: null, open_questions: [], member_briefs: { alice: "shipping" }, pending_mentions: {}, updated_at: "2026-02-01T00:00:00Z" },
    "/projects/p1/constitution": { project_id: "p1", present: false, template: "# Constitution" },
    "/projects/p1/context?bot_role=worker": { project_id: "p1", bot_role: "worker", sections: { overview: "Goal: ship" }, constitution_hash: null },
    "/projects/p1/decisions": { project_id: "p1", decisions: [], count: 0 },
    "/projects/p1/events?after_seq=0&limit=1000": { project_id: "p1", events: [] },
    "/projects/p1/locks": { project_id: "p1", locks: [], pending_requests: [] },
    "/projects/p1/handoffs": { project_id: "p1", handoffs: [] },
    "/projects/p1/approvals": { project_id: "p1", approvals: [] },
    "/projects/p1/checkpoints": { project_id: "p1", checkpoints: [] },
    "/projects/p1/war-room": { project_id: "p1", status: "active", state: { goal: "ship" }, contracts: [], living_spec: null, cost_summary: null, standup: null, checkpoints: [], leaderboard: [], canary_history: [], visual_qa: [], avo_lineage: null, epistemic_claims: [], rsi_status: null, trajectories: [], handoffs: [], decisions: [], events: [], kill_switch: { active: false, reason: "", paused_bots: {} } },
    "/projects/p1/self-config/status": { project_id: "p1", active_profile: {}, analyses_performed: 0, last_analysis: null },
    "/projects/p1/meta-compiler/lineage": { project_id: "p1", active_head: {}, active_scorecard: {}, total_generations: 0, blueprints_count: 0, pareto_frontier: [], history: [] },
    "/projects/p1/perpetual/status": { project_id: "p1", telemetry: {}, active_goal: null, all_goals: [], tasks: [], stagnation_incidents: [], latest_consolidation: null },
  };
  return { ...base, ...overrides };
}

// ------------------------------------------------------------------- routing

test("every read uses a real project route, and the id is encoded", async () => {
  const expected = [
    "/projects/project%2F1",
    "/projects/project%2F1/state",
    "/projects/project%2F1/crew",
    "/projects/project%2F1/threads?limit=500&offset=0",
    "/projects/project%2F1/memory",
    "/projects/project%2F1/constitution",
    "/projects/project%2F1/context?bot_role=worker",
    "/projects/project%2F1/decisions",
    "/projects/project%2F1/events?after_seq=0&limit=1000",
    "/projects/project%2F1/locks",
    "/projects/project%2F1/handoffs",
    "/projects/project%2F1/approvals",
    "/projects/project%2F1/checkpoints",
    "/projects/project%2F1/war-room",
    "/projects/project%2F1/self-config/status",
    "/projects/project%2F1/meta-compiler/lineage",
    "/projects/project%2F1/perpetual/status",
  ];
  const { inspector, calls } = loadInspector(() => ({}));
  await inspector.inspectProject("project/1");
  const paths = calls.map((c) => c.path).sort();
  assert.deepEqual(paths, expected.sort());
});

test("the client is strictly read-only", () => {
  // Every mutation here already has a control surface in the Workforce view,
  // whose project picker defaults to the FIRST project. A copy of a button here
  // would act on a different project than the one on screen.
  assert.doesNotMatch(source, /\bsend\s*[(<]/, "the inspector must not import `send`");
  assert.doesNotMatch(source, /method:\s*"(POST|PATCH|PUT|DELETE)"/);
  assert.doesNotMatch(source, /\b(delete|archive|restore|create|attach|detach|patch)\w*\s*\(/);
});

test("the events read clamps to the server's own bounds", async () => {
  // `limit` is le=1000 and `after_seq` ge=0 on the route; an unbounded value is
  // a 422, which would look like a project with no activity.
  const { inspector, calls } = loadInspector(() => ({}));
  await inspector.inspectProject("p1");
  const events = calls.find((c) => c.path.includes("/events"));
  assert.ok(events, "the event feed is read");
  assert.match(events.path, /after_seq=0&limit=1000/);
});

// ---------------------------------------------------------------- partial reads

test("one failing route blanks one section and names it, and never rejects the read", async () => {
  const good = payloads();
  const { inspector } = loadInspector((path) => {
    if (path === "/projects/p1/approvals") throw new Error("approval queue unavailable");
    return good[path];
  });

  const inspection = await inspector.inspectProject("p1");
  // The whole read still resolved...
  assert.equal(inspection.partial, true);
  // ...the failure is local to its own block...
  assert.equal(inspection.approvals.status, "error");
  assert.match(inspection.approvals.error, /approval queue unavailable/);
  // ...and every other route still answered.
  assert.equal(inspection.record.status, "ok");
  assert.equal(inspection.state.status, "ok");
  assert.equal(inspection.crew.status, "ok");
  assert.equal(inspection.warRoom.status, "ok");
  assert.equal(inspection.perpetual.status, "ok");
});

test("every route failing still resolves, with every section carrying its reason", async () => {
  const { inspector } = loadInspector(() => {
    throw new Error("gateway unavailable");
  });
  const inspection = await inspector.inspectProject("p1");
  assert.equal(inspection.partial, true);
  const failed = inspector.inspectionSections(inspection);
  assert.equal(failed.length, 17, "every read is accounted for in the disclosure");
  for (const [, section] of failed) {
    assert.equal(section.status, "error");
    assert.match(section.error, /gateway unavailable/);
  }
});

test("a clean read is not reported as partial", async () => {
  const good = payloads();
  const { inspector } = loadInspector((path) => good[path]);
  const inspection = await inspector.inspectProject("p1");
  assert.equal(inspection.partial, false);
  // And the disclosure list names every section, so the panel header has one
  // source rather than a second hand-maintained copy.
  assert.equal(inspector.inspectionSections(inspection).length, 17);
});

// ------------------------------------------------------------------- honesty

test("counts the server did not send stay null instead of becoming zero", async () => {
  const good = payloads();
  // The crew read omits `message_count`; the state read omits every counter.
  good["/projects/p1/state"] = { project_id: "p1", goal: "", phase: "" };
  good["/projects/p1/crew"] = {
    ...good["/projects/p1/crew"],
    room: { name: "room", mode: "moderated", moderator: null, members: ["alice", "bob"], parked: false },
  };
  const { inspector } = loadInspector((path) => good[path]);
  const inspection = await inspector.inspectProject("p1");

  const state = inspection.state.data;
  assert.equal(state.active_tasks, null);
  assert.equal(state.completed_tasks, null);
  assert.equal(state.failed_tasks, null);
  assert.equal(state.active_agents, null);
  assert.equal(state.open_conflicts, null);

  // A room whose message count was never reported is null, not 0 — a project
  // with an unread counter is not a project with a silent crew.
  assert.equal(inspection.crew.data.room.message_count, null);
});

test("last_verified: null is never verified", async () => {
  const good = payloads();
  good["/projects/p1/state"] = { ...good["/projects/p1/state"], last_verified: null };
  const { inspector } = loadInspector((path) => good[path]);
  const inspection = await inspector.inspectProject("p1");
  // The distinction the panel renders: "never verified" is not "verified".
  assert.equal(inspection.state.data.last_verified, null);

  good["/projects/p1/state"] = { ...good["/projects/p1/state"], last_verified: "2026-02-01T10:00:00Z" };
  const later = loadInspector((path) => good[path]);
  const withStamp = await later.inspector.inspectProject("p1");
  assert.equal(withStamp.state.data.last_verified, "2026-02-01T10:00:00Z");
});

test("a solo crew with no room is null, and an unreadable room is an error", async () => {
  const solo = payloads();
  const soloResult = loadInspector((path) => solo[path]);
  assert.equal((await soloResult.inspector.inspectProject("p1")).crew.data.room, null);

  // A non-object room is a shape this build cannot read. Reporting it as `null`
  // would claim the project is solo, which is the opposite claim.
  const broken = payloads();
  broken["/projects/p1/crew"] = { ...broken["/projects/p1/crew"], room: "moderated" };
  const brokenResult = loadInspector((path) => broken[path]);
  const inspection = await brokenResult.inspector.inspectProject("p1");
  assert.equal(inspection.crew.status, "error");
  assert.match(inspection.crew.error, /unreadable project room/);
});

test("settings without an orchestration mode reject instead of defaulting", async () => {
  const good = payloads();
  good["/projects/p1/crew"] = {
    ...good["/projects/p1/crew"],
    collaboration: { mention_policy: "strict" },
  };
  const { inspector } = loadInspector((path) => good[path]);
  const inspection = await inspector.inspectProject("p1");
  assert.equal(inspection.crew.status, "error");
  assert.match(inspection.crew.error, /without an orchestration mode/);
});

test("an unknown orchestration mode is preserved verbatim", async () => {
  const good = payloads();
  good["/projects/p1/crew"] = {
    ...good["/projects/p1/crew"],
    collaboration: { ...good["/projects/p1/crew"].collaboration, orchestration_mode: "byzantine" },
  };
  const { inspector } = loadInspector((path) => good[path]);
  const inspection = await inspector.inspectProject("p1");
  assert.equal(inspection.crew.data.collaboration.orchestration_mode, "byzantine");
});

test("a kill switch that was not reported is null, never a healthy off", async () => {
  const good = payloads();
  // The process answered but named no kill-switch block at all.
  good["/projects/p1/war-room"] = { ...good["/projects/p1/war-room"], kill_switch: undefined };
  const { inspector } = loadInspector((path) => good[path]);
  const inspection = await inspector.inspectProject("p1");
  const kill = inspection.warRoom.data.kill_switch;
  // `false` would be a measurement this read never made.
  assert.equal(kill.active, null);
  assert.deepEqual(kill.paused_bots, {});

  const measured = payloads();
  measured["/projects/p1/war-room"] = { ...measured["/projects/p1/war-room"], kill_switch: { active: true, reason: "incident", paused_bots: { alice: "task-1" } } };
  const engaged = loadInspector((path) => measured[path]);
  const engagedInspection = await engaged.inspector.inspectProject("p1");
  assert.equal(engagedInspection.warRoom.data.kill_switch.active, true);
  assert.equal(engagedInspection.warRoom.data.kill_switch.reason, "incident");
});

test("a project record without an id or a name is unreadable, not rendered blank", async () => {
  const good = payloads();
  good["/projects/p1"] = { name: "Launch" };
  const { inspector } = loadInspector((path) => good[path]);
  const inspection = await inspector.inspectProject("p1");
  assert.equal(inspection.record.status, "error");
  assert.match(inspection.record.error, /without an id or a name/);
});

test("a crew member with no bot name is rejected, not shown as an unremovable agent", async () => {
  const good = payloads();
  good["/projects/p1/crew"] = { ...good["/projects/p1/crew"], members: [{ role_in_project: "lead", status: "active" }] };
  const { inspector } = loadInspector((path) => good[path]);
  const inspection = await inspector.inspectProject("p1");
  assert.equal(inspection.crew.status, "error");
  assert.match(inspection.crew.error, /without a bot name/);
});

test("an unreadable memory envelope rejects instead of reading as 'nothing shared'", async () => {
  const good = payloads();
  good["/projects/p1/memory"] = { detail: "not memory" };
  const { inspector } = loadInspector((path) => good[path]);
  const inspection = await inspector.inspectProject("p1");
  assert.equal(inspection.memory.status, "error");
  assert.match(inspection.memory.error, /without a project id/);
});

test("a transcript digest is read as a block, and a non-block stays null", async () => {
  const good = payloads();
  good["/projects/p1/memory"] = {
    ...good["/projects/p1/memory"],
    transcript_digest: { messages: [{ from: "alice", intent: "proposal", text: "ship", at: "2026-02-01T00:00:00Z" }], total: 7, compacted_upto: 2, summary: "earlier: agreed" },
  };
  const { inspector } = loadInspector((path) => good[path]);
  const digest = (await inspector.inspectProject("p1")).memory.data.transcript_digest;
  assert.equal(digest.total, 7);
  assert.equal(digest.messages[0].from, "alice");
  assert.equal(digest.summary, "earlier: agreed");

  const broken = payloads();
  broken["/projects/p1/memory"] = { ...broken["/projects/p1/memory"], transcript_digest: "ship it" };
  const brokenResult = loadInspector((path) => broken[path]);
  assert.equal((await brokenResult.inspector.inspectProject("p1")).memory.data.transcript_digest, null);
});

test("an agent with no brief is absent, not given an empty one", async () => {
  const good = payloads();
  const { inspector } = loadInspector((path) => good[path]);
  const memory = (await inspector.inspectProject("p1")).memory.data;
  assert.equal(memory.member_briefs.alice, "shipping");
  // bob contributed nothing recorded, so there is no key for him. An empty
  // string would read as "bob reported nothing", which is a different claim.
  assert.equal("bob" in memory.member_briefs, false);
});

test("an open question, risk, or contract with no text is shown, not dropped", async () => {
  const good = payloads();
  good["/projects/p1/state"] = { ...good["/projects/p1/state"], open_risks: [{ description: "flaky probe" }] };
  good["/projects/p1/war-room"] = {
    ...good["/projects/p1/war-room"],
    contracts: [{ task_id: "task-1", title: "", assignee_bot: "alice", status: "open", evidence_receipts: [] }],
  };
  const { inspector } = loadInspector((path) => good[path]);
  const inspection = await inspector.inspectProject("p1");
  // An object-shaped risk must not render as a blank bullet.
  assert.deepEqual(inspection.state.data.open_risks, ["flaky probe"]);
  // A contract with zero receipts is a real "no evidence", not an unknown count.
  assert.equal(inspection.warRoom.data.contracts[0].evidence_count, 0);
});

test("conversation pagination that does not advance stops instead of looping", async () => {
  // A server that ignores `offset` would otherwise spin forever on a project
  // with a full page of conversations.
  const page = Array.from({ length: 500 }, (_, i) => ({ thread_id: `t${i}`, display_name: `c${i}` }));
  const { inspector } = loadInspector(() => page);
  const inspection = await inspector.inspectProject("p1");
  assert.equal(inspection.conversations.status, "error");
  assert.match(inspection.conversations.error, /did not advance/);
});

test("an unreadable conversation list rejects instead of looking like an empty project", async () => {
  const good = payloads();
  good["/projects/p1/threads?limit=500&offset=0"] = { projects: [] };
  const { inspector } = loadInspector((path) => good[path]);
  const inspection = await inspector.inspectProject("p1");
  assert.equal(inspection.conversations.status, "error");
  assert.match(inspection.conversations.error, /unreadable project conversation list/);
});

test("the context read follows the requested role, and a role filter is not a failure", async () => {
  const good = payloads();
  // An architect role the server filters to fewer sections is a CORRECT answer:
  // the read succeeds with an empty `sections` map rather than reporting a
  // problem, because "this role may not see that" is not "the read failed".
  good["/projects/p1/context?bot_role=architect"] = { project_id: "p1", bot_role: "architect", sections: {}, constitution_hash: null };
  const { inspector, calls } = loadInspector((path) => good[path]);
  const inspection = await inspector.inspectProject("p1", "architect");
  assert.equal(inspection.context.status, "ok");
  assert.deepEqual(inspection.context.data.sections, {});
  assert.equal(inspection.context.data.bot_role, "architect");
  assert.equal(calls.some((c) => c.path.includes("bot_role=architect")), true);
});

test("an enum this build does not know is carried through, never coerced", async () => {
  const good = payloads();
  good["/projects/p1/approvals"] = {
    project_id: "p1",
    approvals: [{ request_id: "a1", bot_name: "alice", action_type: "write_file", risk_level: "cosmic", status: "awaiting_review", created_at: "2026-02-01T00:00:00Z" }],
  };
  const { inspector } = loadInspector((path) => good[path]);
  const approval = (await inspector.inspectProject("p1")).approvals.data[0];
  assert.equal(approval.risk_level, "cosmic");
  assert.equal(approval.status, "awaiting_review");
});

test("epoch and ISO timestamps both survive the mapping", async () => {
  const good = payloads();
  good["/projects/p1/locks"] = {
    project_id: "p1",
    locks: [{ lock_id: "l1", scope: "file", path: "src/a.py", owner_bot: "alice", created_at: 1_770_000_000.5, expires_at: 1_770_003_600.0 }],
    pending_requests: [],
  };
  const { inspector } = loadInspector((path) => good[path]);
  const lock = (await inspector.inspectProject("p1")).locks.data.locks[0];
  assert.equal(lock.created_at, 1_770_000_000.5);
  assert.equal(lock.expires_at, 1_770_003_600);
  // A lock with no expiry is null, not 0 — an epoch-0 expiry is a real expiry.
  const noExpiry = payloads();
  noExpiry["/projects/p1/locks"] = { project_id: "p1", locks: [{ lock_id: "l1", scope: "file", path: "a", owner_bot: "alice" }], pending_requests: [] };
  const second = loadInspector((path) => noExpiry[path]);
  assert.equal((await second.inspector.inspectProject("p1")).locks.data.locks[0].expires_at, null);
});