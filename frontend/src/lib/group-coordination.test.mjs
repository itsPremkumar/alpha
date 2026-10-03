/**
 * Group coordination: the pure derivations, plus the client route contract.
 *
 * These functions decide what an operator is told about work that is in
 * progress right now. A regression here is not cosmetic — it would let a
 * crashed agent look idle, let an `unresponsive` agent's work be reclaimed out
 * from under a live tool call, or let a room header report a member count that
 * disagrees with the rows beneath it.
 *
 * Network calls are out of scope. The backend contract is pinned by
 * backend/tests/test_group_coordination_routes.py.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  activityBadge,
  byUrgency,
  claimsBySubject,
  failureTitle,
  healthBadge,
  reclaimAffordance,
  roomHeadline,
} from "./group-coordination-model.ts";

const clientSource = readFileSync(new URL("./group-coordination.ts", import.meta.url), "utf8");

function agent(over = {}) {
  return {
    bot_name: "scout",
    activity: "idle",
    detail: "no run attributed",
    tone: "ok",
    since: 0,
    run_id: null,
    claim_ids: [],
    held_paths: [],
    health: null,
    last_heartbeat_at: null,
    attributed_by: "unattributed",
    room_name: "engineering",
    evidence: {
      source: "registry",
      reason: "registered",
      detail: "in the roster with no run",
      run: null,
      last_heartbeat_at: null,
      seconds_since_heartbeat: null,
    },
    ...over,
  };
}

function claim(over = {}) {
  return {
    claim_id: "c1",
    room_name: "engineering",
    holder: "scout",
    kind: "file",
    subject: "src/api.py",
    intent: "editing",
    project_id: null,
    run_id: null,
    detail: "",
    created_at: 0,
    renewed_at: 0,
    expires_at: 9e12,
    state: "active",
    orphaned_at: null,
    orphan_evidence: null,
    live: true,
    ...over,
  };
}

test("the client calls only real coordination routes", () => {
  for (const path of [
    "/api/groups/${encodeURIComponent(room)}/coordination",
    "/api/groups/${encodeURIComponent(room)}/claims",
    "/api/groups/${encodeURIComponent(room)}/claims/${encodeURIComponent(claimId)}/release",
    "/api/groups/${encodeURIComponent(room)}/claims/${encodeURIComponent(claimId)}/reclaim",
    "/api/groups/${encodeURIComponent(room)}/reconcile",
    "/api/groups/tree",
  ]) {
    assert.ok(clientSource.includes(path), `missing route ${path}`);
  }
});

test("the client never coerces a failed read into an empty room", () => {
  // No `catch` that returns `[]`, and no `?? []` on the coordination read.
  // "No claims exist" and "the read failed" must stay different facts.
  const read = clientSource.slice(
    clientSource.indexOf("fetchRoomCoordination"),
    clientSource.indexOf("fetchGroupTree"),
  );
  assert.ok(!/catch/.test(read), "the coordination read must let its error propagate");
  assert.ok(!/\?\?\s*\[\]/.test(read), "the coordination read must not default to an empty list");
});

// ── Rule 1: activity and health are two axes ───────────────────────────────

test("a blocked agent that is still answering shows both facts, not one", () => {
  const a = agent({ activity: "blocked", health: "healthy" });
  assert.equal(activityBadge(a).label, "Blocked");
  assert.equal(activityBadge(a).tone, "amber");
  assert.equal(healthBadge(a).label, "Healthy");
  assert.equal(healthBadge(a).tone, "green");
});

test("an unrecorded health verdict does not read as a healthy one", () => {
  const badge = healthBadge(agent({ health: null }));
  assert.equal(badge.tone, "gray");
  assert.match(badge.label, /unknown/i);
});

test("activity and health can disagree without one overwriting the other", () => {
  const a = agent({ activity: "idle", health: "stalled" });
  assert.equal(activityBadge(a).label, "Idle");
  assert.equal(healthBadge(a).label, "Stalled");
});

// ── Rule 2: crashed is not unresponsive ────────────────────────────────────

test("crashed is red and unresponsive is amber", () => {
  // Collapsing these two is what would push an operator to reclaim work that is
  // still running, so the tones must differ and neither may be muted.
  assert.equal(activityBadge(agent({ activity: "crashed" })).tone, "red");
  assert.equal(activityBadge(agent({ activity: "unresponsive" })).tone, "amber");
  assert.notEqual(activityBadge(agent({ activity: "crashed" })).tone, activityBadge(agent({ activity: "unresponsive" })).tone);
});

test("an unrecognised state word is not measured, never healthy", () => {
  // A future backend state must render as visibly-unknown rather than silently
  // folding into `idle`, which is the conflating this whole axis exists to stop.
  const badge = activityBadge(agent({ activity: "teleporting" }));
  assert.equal(badge.tone, "gray");
  assert.match(badge.label, /not measured/i);
});

test("the most urgent members sort first", () => {
  const ordered = byUrgency([
    agent({ bot_name: "a", activity: "idle" }),
    agent({ bot_name: "b", activity: "working" }),
    agent({ bot_name: "c", activity: "crashed" }),
    agent({ bot_name: "d", activity: "blocked" }),
  ]);
  assert.deepEqual(ordered.map((a) => a.activity), ["crashed", "blocked", "working", "idle"]);
});

// ── Rule 3: both membership counts travel ──────────────────────────────────

test("the headline count is the effective one, and the direct one is context", () => {
  const headline = roomHeadline({
    effective_count: 6,
    direct_count: 3,
    agents: new Array(6).fill(agent()),
  });
  assert.equal(headline.primary, "6 bots");
  assert.match(headline.caption, /3 direct/);
  assert.match(headline.caption, /3 inherited or rule-matched/);
});

test("a room where every member is direct says so rather than showing arithmetic", () => {
  const headline = roomHeadline({ effective_count: 4, direct_count: 4, agents: new Array(4).fill(agent()) });
  assert.equal(headline.primary, "4 bots");
  assert.match(headline.caption, /all members are direct/);
});

test("an unreadable roster count is disclosed, not rendered as zero", () => {
  const headline = roomHeadline({ effective_count: 2, direct_count: null, agents: new Array(2).fill(agent()) });
  assert.equal(headline.primary, "2 bots");
  assert.doesNotMatch(headline.caption, /0 direct/);
});

// ── Rule 4: a crash is evidence, a hand-off is a decision ──────────────────

test("a live claim is never reclaimable, whatever the conflict says", () => {
  // `unresponsive` is not dead. Offering reclaim here would hand live work to a
  // second agent, which is the exact failure this rule prevents.
  const affordance = reclaimAffordance(
    claim({ state: "active" }),
    { subject: "src/api.py", kind: "file", holders: ["scout", "other"], claim_ids: ["c1", "c2"], reasons: [], states: {}, reclaimable: false, dead_holder: null, detail: "" },
  );
  assert.equal(affordance.allowed, false);
  assert.match(affordance.reason, /has not crashed/);
});

test("an orphaned claim is reclaimable and says why", () => {
  const affordance = reclaimAffordance(claim({ state: "orphaned", live: false }), null);
  assert.equal(affordance.allowed, true);
  assert.match(affordance.reason, /crashed/i);
});

test("a reclaimable conflict unlocks only the claims it actually names", () => {
  const conflict = {
    subject: "src/api.py",
    kind: "file",
    holders: ["scout", "other"],
    claim_ids: ["c2"],
    reasons: [],
    states: {},
    reclaimable: true,
    dead_holder: "other",
    detail: "other is crashed so it is available to take",
  };
  assert.equal(reclaimAffordance(claim({ claim_id: "c2" }), conflict).allowed, true);
  // c1 is a different claim in the same room; the conflict does not speak for it.
  assert.equal(reclaimAffordance(claim({ claim_id: "c1" }), conflict).allowed, false);
});

test("a released or expired claim is not reclaimable because nobody holds it", () => {
  for (const state of ["released", "expired"]) {
    const affordance = reclaimAffordance(claim({ state, live: false }), null);
    assert.equal(affordance.allowed, false);
    assert.match(affordance.reason, new RegExp(state));
  }
});

// ── Claims grouping ────────────────────────────────────────────────────────

test("two agents on one subject group into a single row", () => {
  // Rendered as two separate rows, a contested file reads as two fine rows.
  const groups = claimsBySubject([
    claim({ claim_id: "c1", holder: "scout" }),
    claim({ claim_id: "c2", holder: "other" }),
  ]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].subject, "src/api.py");
  assert.equal(groups[0].claims.length, 2);
});

test("the most contested subject sorts to the top", () => {
  const groups = claimsBySubject([
    claim({ claim_id: "c1", subject: "a.py" }),
    claim({ claim_id: "c2", subject: "b.py" }),
    claim({ claim_id: "c3", subject: "b.py" }),
    claim({ claim_id: "c4", subject: "b.py" }),
  ]);
  assert.equal(groups[0].subject, "b.py");
  assert.equal(groups[0].claims.length, 3);
});

// ── Failed reads are named ─────────────────────────────────────────────────

test("a failed read gets a specific title, never a generic one", () => {
  assert.match(failureTitle(new Error("Failed to fetch")), /Could not reach the Gateway/);
  assert.match(failureTitle(new Error("The operation was aborted")), /cancelled/i);
  assert.match(failureTitle(new Error("boom")), /Could not load coordination/);
});