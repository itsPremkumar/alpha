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
  ASSIGNMENT_INTENTS,
  ASSIGNMENT_KINDS,
  activityBadge,
  assignmentProblems,
  assignmentReceipt,
  assignmentWarnings,
  byUrgency,
  claimExpiryView,
  claimsBySubject,
  durationLabel,
  emptyAssignmentDraft,
  failureTitle,
  healthBadge,
  lastReadView,
  reclaimAffordance,
  roomHeadline,
} from "./group-coordination-model.ts";

const clientSource = readFileSync(new URL("./group-coordination.ts", import.meta.url), "utf8");
const panelSource = readFileSync(
  new URL("../components/sections/GroupCoordinationPanel.tsx", import.meta.url),
  "utf8",
);

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

// ── Work assignment ─────────────────────────────────────────────────────────────

/**
 * The assign form mirrors `POST /api/groups/{name}/claims`. Every bound below
 * is the server's own: if this mirror drifts, the form either offers what the
 * Gateway 422s on or hides a claim the Gateway would have taken.
 */
test("the assignment vocabulary is the server's, not the client's", () => {
  assert.deepEqual([...ASSIGNMENT_KINDS], ["file", "dir", "symbol", "task", "artifact", "requirement"]);
  assert.deepEqual([...ASSIGNMENT_INTENTS], ["reading", "editing", "reviewing"]);
});

test("a blank draft starts on the server's own defaults", () => {
  const draft = emptyAssignmentDraft();
  assert.equal(draft.holder, "");
  assert.equal(draft.subject, "");
  assert.equal(draft.ttl_seconds, 120);
  assert.equal(draft.kind, "task");
  assert.equal(draft.intent, "editing");
  // Nothing filled in, so nothing may be submitted yet.
  const fields = assignmentProblems(draft).map((p) => p.field);
  assert.ok(fields.includes("holder"));
  assert.ok(fields.includes("subject"));
  assert.ok(!fields.includes("kind"));
  assert.ok(!fields.includes("intent"));
  assert.ok(!fields.includes("ttl_seconds"));
});

test("every assignment problem names its field, so the message can sit beside the control", () => {
  const problems = assignmentProblems({
    ...emptyAssignmentDraft(),
    holder: "x".repeat(65),
    subject: "y".repeat(2001),
    detail: "z".repeat(501),
    kind: "portal",
    intent: "destroying",
    ttl_seconds: 4000,
  });
  const fields = new Set(problems.map((p) => p.field));
  for (const expected of ["holder", "subject", "detail", "kind", "intent", "ttl_seconds"]) {
    assert.ok(fields.has(expected), `expected a problem for ${expected}, got ${[...fields].join(", ")}`);
  }
  for (const p of problems) assert.ok(p.problem.length > 0, `${p.field} has an empty message`);
});

test("a claim with no holder would record nothing, and says so before the round trip", () => {
  const problems = assignmentProblems({ ...emptyAssignmentDraft(), holder: "   ", subject: "src/api.py" });
  assert.equal(problems.length, 1);
  assert.equal(problems[0].field, "holder");
  assert.match(problems[0].problem, /hold it/);
});

test("the ttl mirror refuses both ends of the server's open interval", () => {
  const base = { ...emptyAssignmentDraft(), holder: "scout", subject: "a" };
  // Field(gt=0, le=3600): zero is refused as firmly as an hour and a half.
  for (const ttl of [0, -5, 3601, Number.NaN]) {
    const problems = assignmentProblems({ ...base, ttl_seconds: ttl });
    assert.ok(
      problems.some((p) => p.field === "ttl_seconds"),
      `ttl ${ttl} should be refused`,
    );
  }
  for (const ttl of [1, 120, 3600]) {
    assert.deepEqual(assignmentProblems({ ...base, ttl_seconds: ttl }), []);
  }
});

test("a holder outside the roster is a warning, never a refusal", () => {
  // The store accepts any non-empty string, so refusing here would be the
  // client inventing a rule the server does not have. The disclosure is about
  // what happens *after*: the claim lands with nobody to match it.
  const draft = { ...emptyAssignmentDraft(), holder: "ghost", subject: "src/api.py" };
  const warnings = assignmentWarnings(draft, ["architect", "coder", "reviewer", "tester"]);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /ghost is not in this room's roster/);
  assert.match(warnings[0], /architect, coder, reviewer, tester/);
  assert.match(warnings[0], /will be recorded/);
});

test("a roster holder, and an unknown roster, raise no warning", () => {
  assert.deepEqual(assignmentWarnings({ ...emptyAssignmentDraft(), holder: "coder", subject: "a" }, ["coder", "ghost"]), []);
  // An unreadable roster means we do not know — not that the holder is absent.
  assert.deepEqual(assignmentWarnings({ ...emptyAssignmentDraft(), holder: "anyone", subject: "a" }, []), []);
});

test("the receipt is written from the server's response, not from the draft", () => {
  const now = 1_000_000;
  const receipt = assignmentReceipt(
    claim({
      holder: "reviewer",
      subject: "war-room-ui-refresh",
      kind: "task",
      intent: "editing",
      created_at: now,
      expires_at: now + 600,
    }),
    now,
  );
  assert.match(receipt, /^reviewer holds "war-room-ui-refresh" — editing task, 10m lease\.$/);
});

/**
 * The receipt states the lease's *length*, never a countdown. It stays on the
 * board until the next action, so a relative time would sit above the claim
 * row's live countdown and disagree with it — measured live as `expires in
 * 10m` directly over `expires in 3m 10s` for the same claim.
 */
test("the receipt's time claim is a fixed property and cannot age into a contradiction", () => {
  const created = 1_000_000;
  const at = (offsetSeconds) => assignmentReceipt(claim({ created_at: created, expires_at: created + 600 }), created + offsetSeconds);

  // Two readings, 240s apart, produce the *same* sentence: nothing in it moves.
  assert.equal(at(0), at(240));
  assert.match(at(0), /10m lease/);
  // And it never borrows the countdown's wording, which is the row's job.
  assert.doesNotMatch(at(0), /expires in/);

  // An expiry the server never sent stays "not reported", never "forever".
  assert.match(assignmentReceipt(claim({ created_at: created, expires_at: Number.NaN }), created), /expiry not reported/);

  // The arithmetic is reported rather than resolved in the hold's favour.
  assert.match(assignmentReceipt(claim({ created_at: created, expires_at: created - 5 }), created), /lease already over/);
});

test("a claim the server did not confirm as live never claims a hold", () => {
  const receipt = assignmentReceipt(claim({ live: false, state: "expired" }), 0);
  assert.match(receipt, /nobody is holding it/);
  assert.doesNotMatch(receipt, /holds "src\/api\.py"/);
});

test("an expiry the server never sent is reported, never read as forever", () => {
  const view = claimExpiryView(claim({ expires_at: Number.NaN }), 1000);
  assert.equal(view.label, "expiry not reported");
  assert.equal(view.tone, "gray");
});

test("a live lease counts down and tightens to amber in its last 30s", () => {
  const now = 1000;
  assert.deepEqual(claimExpiryView(claim({ expires_at: now + 600 }), now), { label: "expires in 10m", tone: "green" });
  assert.deepEqual(claimExpiryView(claim({ expires_at: now + 45 }), now), { label: "expires in 45s", tone: "green" });
  assert.deepEqual(claimExpiryView(claim({ expires_at: now + 10 }), now), { label: "expires in 10s", tone: "amber" });
});

test("an expired claim reports how long ago, and stops counting", () => {
  const view = claimExpiryView(claim({ state: "expired", expires_at: 1000 }), 1600);
  assert.equal(view.label, "expired 10m ago");
  assert.equal(view.tone, "gray");
  // Just-expired does not read as "0s remaining", which looks like a full lease.
  assert.equal(claimExpiryView(claim({ expires_at: 1000 }), 1000.4).label, "expired just now");
});

test("durationLabel distinguishes a sub-second value from a zero", () => {
  assert.equal(durationLabel(0.4), "<1s");
  assert.equal(durationLabel(0), "<1s");
  assert.equal(durationLabel(42), "42s");
  assert.equal(durationLabel(600), "10m");
  assert.equal(durationLabel(630), "10m 30s");
  assert.equal(durationLabel(3600), "1h");
  assert.equal(durationLabel(5400), "1h 30m");
  // Nonsense input is "not reported", not a number someone could mistake for a reading.
  assert.equal(durationLabel(-1), "not reported");
  assert.equal(durationLabel(Number.NaN), "not reported");
});

/**
 * The off-boundary values, each of which per-unit rounding rendered as a
 * doubled unit. 599.7s is the exact case observed on the live board: a
 * 600-second lease read ~300ms after creation printed `9m 60s` in the claim
 * row, because the minutes floored to 9 while the seconds rounded to 60.
 *
 * A suite that only ever passed whole minutes (600, 630, 3600) could not see
 * this — the boundary values are precisely the ones that round correctly.
 */
test("a duration just short of a unit boundary carries, never doubles the unit", () => {
  assert.equal(durationLabel(599.7), "10m"); // was "9m 60s" — observed live
  assert.equal(durationLabel(59.7), "1m"); // was "60s"
  assert.equal(durationLabel(3599.7), "1h"); // was "59m 60s"
  assert.equal(durationLabel(7199.7), "2h"); // was "1h 60m"
  assert.equal(durationLabel(3659.7), "1h 1m");
  assert.equal(durationLabel(90), "1m 30s");
  assert.equal(durationLabel(59.4), "59s"); // rounds down, stays a seconds label
});

// ── Live freshness ─────────────────────────────────────────────────────────────

test("a room never read says so rather than reading as just-read", () => {
  const view = lastReadView(null, 10_000);
  assert.equal(view.label, "no read yet");
  assert.equal(view.stale, true);
  assert.equal(view.tone, "gray");
});

test("a fresh read is green and an aged one is disclosed as stale", () => {
  const now = 60_000;
  assert.deepEqual(lastReadView(now - 200, now), { label: "read just now", tone: "green", stale: false });
  assert.deepEqual(lastReadView(now - 4_000, now), { label: "read 4s ago", tone: "green", stale: false });
  // Past two poll intervals the LIVE toggle is still on while the reads are
  // failing — the one healthy-looking case that can still be lying.
  const stale = lastReadView(now - 11_000, now);
  assert.equal(stale.stale, true);
  assert.equal(stale.tone, "amber");
  assert.match(stale.label, /read 11s ago/);
});

test("the staleness threshold is two poll intervals, stated in the module", async () => {
  const { LIVE_POLL_MS } = await import("./group-coordination-model.ts");
  assert.equal(LIVE_POLL_MS, 5000);
  const now = 100_000;
  assert.equal(lastReadView(now - LIVE_POLL_MS * 2, now).stale, true);
  assert.equal(lastReadView(now - LIVE_POLL_MS * 2 + 1, now).stale, false);
});

// ── The poll cannot stack ─────────────────────────────────────────────────────

/**
 * The interval fires on a fixed cadence no matter how long a read takes, so
 * without a lock a 60-second coordination read under a 5-second tick opens
 * twelve concurrent requests. The consequence is not only load: the oldest
 * request can settle *last*, so its failure lands on top of a newer success
 * and the panel displays an error beside a freshness chip that says the board
 * was read six seconds ago. That pairing is self-contradicting, and it was
 * observed live against the Gateway while it was busy.
 *
 * The lock has to be a ref, not `loading` state — state is not visible to the
 * next tick's closure until React re-renders, which is exactly the window the
 * second tick lands in.
 */
test("a read is never started while one is already in flight", () => {
  assert.match(panelSource, /if \(!room \|\| loadingRef\.current\) return;/);
  assert.match(panelSource, /loadingRef\.current = true;/);
  // Released on *every* exit. Released only after a success would leave the
  // panel permanently locked out of every future read after one failure.
  assert.match(panelSource, /finally \{[\s\S]{0,120}?loadingRef\.current = false;/);
});

test("the guard reads the room from a ref, so there is one load for every room", () => {
  // A `useCallback` bound to `props.room` produces a new function per room and
  // cannot re-read the *new* room after a mid-flight change — the `finally`
  // would call back into the room it just left.
  assert.match(panelSource, /const room = roomRef\.current;/);
  assert.match(panelSource, /if \(roomRef\.current !== room\) void load\(\);/);
});

test("a read that finished after the operator changed rooms is dropped", () => {
  // Both the success and the failure path must be gated: applying the success
  // paints one room's agents under another's name, and applying the failure
  // blanks a room that never failed.
  const gates = panelSource.match(/if \(roomRef\.current !== room\) return;/g) || [];
  assert.equal(gates.length, 2, `expected a stale gate on both paths, found ${gates.length}`);
});

test("changing rooms invalidates the board rather than reusing the old one", () => {
  // A snapshot left over from the previous room reads as this room's roster,
  // and a `lastReadAt` carried across would date a room we no longer watch.
  const effect = /roomRef\.current = props\.room;[\s\S]{0,400}?setLastReadAt\(null\);/;
  assert.match(panelSource, effect);
});