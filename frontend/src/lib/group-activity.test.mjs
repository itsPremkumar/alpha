/**
 * Contract for the group-activity derivation.
 *
 * The honesty inversions here are the point: a crashed agent must not look idle,
 * an unread count must not look like zero, and a word this build does not know
 * must render as unknown rather than snapped to a state the server never sent.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  ACTIVITY_STATES,
  activityHeadline,
  activityTone,
  availableSubjects,
  conflictRows,
  isKnownActivity,
  membershipHeadline,
  sortByUrgency,
  toAgentActivity,
  toRoomActivity,
  toWorkClaim,
  urgencyRank,
} from "./group-activity.ts";

function agent(overrides = {}) {
  return toAgentActivity({
    bot_name: "coder",
    activity: "working",
    detail: "editing routes",
    since: 1000,
    run_id: "r1",
    claim_ids: ["cl-1"],
    held_paths: ["src/api.py"],
    health: "healthy",
    last_heartbeat_at: 1000,
    attributed_by: "explicit",
    evidence: { source: "heartbeat", reason: "heartbeat_fresh", detail: "editing routes", run: null },
    ...overrides,
  });
}

function snapshot(overrides = {}) {
  return toRoomActivity({
    room: "crew",
    agents: [agent()],
    count: 1,
    by_activity: { working: 1 },
    by_tone: { busy: 1 },
    claims: [],
    live_claim_count: 0,
    conflicts: [],
    orphaned: [],
    members: ["coder"],
    direct_count: 1,
    effective_members: ["coder"],
    effective_count: 1,
    ...overrides,
  });
}

/* ------------------------------------------------------------------ tones */

test("crashed and unresponsive never share a tone", () => {
  // The whole feature. If these ever match, a UI painting both alike re-creates
  // the original lie: a dead agent indistinguishable from a mid-tool-call one.
  assert.notEqual(activityTone("crashed"), activityTone("unresponsive"));
  assert.equal(activityTone("crashed"), "bad");
  assert.equal(activityTone("unresponsive"), "warn");
});

test("an unfamiliar state word renders as unknown, never as idle", () => {
  assert.equal(activityTone("some-future-state"), "unknown");
  assert.equal(activityTone(""), "unknown");
  assert.equal(isKnownActivity("some-future-state"), false);
});

test("an unfamiliar word is preserved verbatim rather than snapped", () => {
  assert.equal(agent({ activity: "quiescing" }).activity, "quiescing");
  assert.equal(agent({ activity: "quiescing" }).tone, "unknown");
});

test("every declared state has a tone", () => {
  for (const state of ACTIVITY_STATES) {
    assert.ok(activityTone(state), state);
    assert.ok(isKnownActivity(state), state);
  }
});

test("a missing activity field reads as unknown, not idle", () => {
  assert.equal(toAgentActivity({ bot_name: "x" }).activity, "unknown");
});

/* --------------------------------------------------------------- ordering */

test("urgency puts a crashed agent above working, idle and offline", () => {
  assert.ok(urgencyRank("crashed") < urgencyRank("unresponsive"));
  assert.ok(urgencyRank("unresponsive") < urgencyRank("working"));
  assert.ok(urgencyRank("working") < urgencyRank("idle"));
  assert.ok(urgencyRank("idle") < urgencyRank("offline"));
});

test("an unrecognised word sorts last, not first", () => {
  // A word this build cannot judge must not be promoted above one it can.
  assert.ok(urgencyRank("some-future-state") > urgencyRank("offline"));
});

test("sortByUrgency does not mutate its input", () => {
  const input = [agent({ bot_name: "a", activity: "idle" }), agent({ bot_name: "b", activity: "crashed" })];
  const before = input.map((a) => a.activity);
  sortByUrgency(input);
  assert.deepEqual(input.map((a) => a.activity), before);
});

test("sortByUrgency surfaces the crashed agent first", () => {
  const sorted = sortByUrgency([
    agent({ bot_name: "idle-one", activity: "idle" }),
    agent({ bot_name: "dead-one", activity: "crashed" }),
    agent({ bot_name: "busy-one", activity: "working" }),
  ]);
  assert.equal(sorted[0].bot_name, "dead-one");
});

/* ------------------------------------------------------------ null vs zero */

test("absent numbers are null, never 0", () => {
  // 0 claims the server measured zero; absent claims nobody asked.
  const s = snapshot({ count: undefined, live_claim_count: undefined });
  assert.equal(s.count, null);
  assert.equal(s.live_claim_count, null);
});

test("a real zero count survives as 0", () => {
  assert.equal(snapshot({ count: 0, live_claim_count: 0 }).count, 0);
});

test("a null last_heartbeat_at is not the epoch", () => {
  // `new Date(0)` renders as 1970 and would claim the event happened then.
  assert.equal(agent({ last_heartbeat_at: null }).last_heartbeat_at, null);
  assert.equal(agent({ last_heartbeat_at: "" }).last_heartbeat_at, null);
});

test("a numeric epoch stamp and an ISO stamp both parse", () => {
  assert.equal(agent({ last_heartbeat_at: 1700000000 }).last_heartbeat_at, 1700000000);
  assert.ok(agent({ last_heartbeat_at: "2026-01-01T00:00:00Z" }).last_heartbeat_at);
});

test("an unparseable evidence stamp stays null", () => {
  const a = agent({ evidence: { source: "heartbeat", reason: "r", detail: "d", last_heartbeat_at: "not-a-time" } });
  assert.equal(a.evidence.last_heartbeat_at, null);
});

/* --------------------------------------------------------- missing evidence */

test("a missing evidence block is null rather than a fabricated reason", () => {
  const a = toAgentActivity({ bot_name: "coder", activity: "working", evidence: undefined });
  assert.equal(a.evidence, null);
});

test("a crashed agent keeps its evidence and its run id", () => {
  // The user's requirement: the work must not simply disappear.
  const a = agent({
    activity: "crashed",
    tone: undefined,
    run_id: "r7",
    held_paths: ["src/api.py"],
    evidence: {
      source: "run_store",
      reason: "recoverable_stop_reason",
      detail: "r7 stopped: orphan_recovered",
      run: { run_id: "r7", status: "error", stop_reason: "orphan_recovered", error: "lease expired" },
    },
  });
  assert.equal(a.activity, "crashed");
  assert.equal(a.tone, "bad");
  assert.equal(a.run_id, "r7");
  assert.deepEqual(a.held_paths, ["src/api.py"]);
  assert.equal(a.evidence.run.stop_reason, "orphan_recovered");
});

/* ------------------------------------------------------------- headline */

test("a failed read names its reason and never reads as all-idle", () => {
  const text = activityHeadline(null, "gateway unreachable");
  assert.match(text, /gateway unreachable/);
  assert.doesNotMatch(text, /0 working/);
});

test("an unread snapshot says so", () => {
  assert.match(activityHeadline(null), /not read/);
});

test("a room with no recorded work is not '0 working'", () => {
  assert.match(activityHeadline(snapshot({ agents: [], count: 0 })), /no members have any recorded work/);
});

test("the headline names crashed agents and overlapping claims", () => {
  const s = snapshot({
    agents: [agent({ bot_name: "coder", activity: "crashed" }), agent({ bot_name: "qa", activity: "working" })],
    count: 2,
    conflicts: [{ subject: "a.py", holders: ["coder", "qa"], reclaimable: true }],
  });
  const text = activityHeadline(s);
  assert.match(text, /crashed \(coder\)/);
  assert.match(text, /1 overlapping/);
});

test("an unreported count is disclosed rather than defaulted", () => {
  assert.match(activityHeadline(snapshot({ count: null })), /count not reported/);
});

/* --------------------------------------------------------- membership */

test("both membership counts travel together", () => {
  assert.match(membershipHeadline(snapshot({ direct_count: 2, effective_count: 6 })), /6 members \(2 direct\)/);
});

test("a missing count discloses instead of showing a number", () => {
  assert.match(membershipHeadline(snapshot({ direct_count: null })), /not reported/);
  assert.match(membershipHeadline(snapshot({ effective_count: null })), /not reported/);
});

/* ------------------------------------------------------------- claims */

test("a claim's live flag is only true when the server said so", () => {
  assert.equal(toWorkClaim({ claim_id: "cl-1", live: true }).live, true);
  // A missing `live` is not a verdict that the claim is dead.
  assert.equal(toWorkClaim({ claim_id: "cl-1" }).live, false);
});

test("orphaned subjects are the work a peer can pick up", () => {
  const s = snapshot({
    orphaned: [
      toWorkClaim({ claim_id: "a", subject: "src/api.py", state: "orphaned" }),
      toWorkClaim({ claim_id: "b", subject: "src/api.py", state: "orphaned" }),
      toWorkClaim({ claim_id: "c", subject: "src/db.py", state: "orphaned" }),
    ],
  });
  assert.deepEqual(availableSubjects(s), ["src/api.py", "src/db.py"]);
});

test("reclaimable conflicts sort above live ones", () => {
  const s = snapshot({
    conflicts: [
      { subject: "live", holders: ["a", "b"], reclaimable: false },
      { subject: "free", holders: ["a", "b"], reclaimable: true },
    ],
  });
  assert.equal(conflictRows(s)[0].subject, "free");
});

test("reclaimable is only true when the server said so", () => {
  const s = snapshot({ conflicts: [{ subject: "x", holders: ["a"], reclaimable: "yes" }] });
  assert.equal(s.conflicts[0].reclaimable, false);
});

/* ------------------------------------------------------ envelope safety */

test("a non-object payload maps to an empty, honest snapshot", () => {
  const s = toRoomActivity("nonsense");
  assert.deepEqual(s.agents, []);
  assert.equal(s.count, null);
  assert.equal(s.direct_count, null);
  assert.match(activityHeadline(s), /count not reported/);
});

test("a bucket the server omitted stays absent rather than zero-filled", () => {
  const s = toRoomActivity({ by_activity: { working: 2 } });
  assert.deepEqual(s.by_activity, { working: 2 });
  assert.equal("crashed" in s.by_activity, false);
});

test("health stays a separate axis and is never folded into activity", () => {
  const a = agent({ activity: "blocked", health: "dead" });
  assert.equal(a.activity, "blocked");
  assert.equal(a.health, "dead");
});
