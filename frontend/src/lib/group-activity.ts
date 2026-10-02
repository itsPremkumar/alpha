/**
 * Group activity: pure derivation over `GET /api/groups/{name}/activity`.
 *
 * This module is the client half of a distinction the server had to be taught:
 * **an agent that finished and an agent that died are different facts.**
 * `GET /{name}/members` resolves a single presence word from BotRegistry
 * lifecycle + last activity, and because nothing tells it a process died, it
 * resolves "active but quiet" to `idle` — which is also what a cleanly finished
 * agent looks like. Both rendered as one grey dot.
 *
 * The rules this module must keep, all of them enforced in
 * `group-activity.test.mjs`:
 *
 * - **`crashed` and `unresponsive` never share a tone.** `crashed` means the run
 *   store carries a named terminal reason. `unresponsive` means only that a
 *   heartbeat went quiet, which an agent inside a long tool call also does.
 *   Painting both alike would re-create the original lie one layer up.
 * - **`unknown` is never `idle`.** "We have no record of this member" and "this
 *   member finished" are opposite claims.
 * - **A count the server did not send is `null`, never `0`.** `count: 0` means
 *   the server measured an empty room; a failed read must not look like one.
 * - **Both membership counts travel together.** Rendering only `direct_count`
 *   would say "3 members" about a room with six visible bots.
 * - **An unfamiliar state string is preserved verbatim**, not snapped to one
 *   this build happens to know.
 */

export type ActivityState =
  | "working"
  | "idle"
  | "blocked"
  | "unresponsive"
  | "crashed"
  | "offline"
  | "unknown";

export type ActivityTone = "busy" | "ok" | "warn" | "bad" | "off" | "unknown";

/** How an agent's activity came to be attributed to this room. */
export type Attribution = "explicit" | "roster_match" | "unattributed";

export type ClaimState = "active" | "released" | "expired" | "orphaned";

export interface RunFact {
  run_id: string | null;
  status: string | null;
  stop_reason: string | null;
  error: string | null;
}

export interface ActivityEvidence {
  source: string | null;
  /** Stable machine word, so a test asserts the derivation not the prose. */
  reason: string | null;
  detail: string | null;
  run: RunFact | null;
  last_heartbeat_at: number | null;
  seconds_since_heartbeat: number | null;
}

export interface AgentActivity {
  bot_name: string;
  /** The verbatim server word, including one this build does not know. */
  activity: string;
  detail: string;
  tone: ActivityTone;
  since: number | null;
  run_id: string | null;
  claim_ids: string[];
  held_paths: string[];
  /** `alpha.bots.health`'s own verdict — a separate axis, never merged. */
  health: string | null;
  last_heartbeat_at: number | null;
  attributed_by: Attribution | string | null;
  evidence: ActivityEvidence | null;
}

export interface WorkClaim {
  claim_id: string;
  holder: string;
  kind: string;
  subject: string;
  intent: string;
  state: string;
  live: boolean;
  detail: string;
  orphaned_at: number | null;
}

export interface SoftConflict {
  subject: string;
  kind: string;
  holders: string[];
  claim_ids: string[];
  reasons: string[];
  states: Record<string, string>;
  reclaimable: boolean;
  dead_holder: string | null;
  detail: string;
}

export interface RoomActivity {
  room: string;
  agents: AgentActivity[];
  count: number | null;
  by_activity: Record<string, number>;
  by_tone: Record<string, number>;
  claims: WorkClaim[];
  live_claim_count: number | null;
  conflicts: SoftConflict[];
  orphaned: WorkClaim[];
  members: string[];
  direct_count: number | null;
  effective_members: string[];
  effective_count: number | null;
}

export const ACTIVITY_STATES: readonly string[] = [
  "working",
  "idle",
  "blocked",
  "unresponsive",
  "crashed",
  "offline",
  "unknown",
];

const TONES: Readonly<Record<string, ActivityTone>> = {
  working: "busy",
  idle: "ok",
  blocked: "warn",
  unresponsive: "warn",
  crashed: "bad",
  offline: "off",
  unknown: "unknown",
};

/** A numeric time or `null`. Never coerced to 0: absent is not the epoch. */
function asTime(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  // Epoch-seconds or epoch-millis arrive as a bare number; a non-numeric string
  // is an ISO stamp. `Number("2026-01-01")` is NaN, so the branch is safe.
  if (!Number.isNaN(Number(trimmed))) {
    const n = Number(trimmed);
    return Number.isFinite(n) && n > 0 ? n : null;
  }
  const parsed = Date.parse(trimmed);
  return Number.isNaN(parsed) ? null : parsed;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asStringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((v) => String(v)) : [];
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

/**
 * The server's state word, or the verbatim string when this build does not know
 * it. Never snapped to `idle` or `unknown`: a future state must render as
 * visibly-not-measured, and inventing a state the server did not claim is the
 * error class this whole feature exists to remove.
 */
export function activityTone(activity: string): ActivityTone {
  return TONES[activity] ?? "unknown";
}

/** `true` only for a word this build recognises. */
export function isKnownActivity(activity: string): boolean {
  return Object.prototype.hasOwnProperty.call(TONES, activity);
}

/**
 * Most-urgent first, so a crashed agent is never buried under idle rows.
 * Unrecognised words sort last rather than first — a word this build cannot
 * judge must not be promoted above one it can.
 */
export function urgencyRank(activity: string): number {
  const order: Record<string, number> = {
    crashed: 0,
    unresponsive: 1,
    blocked: 2,
    working: 3,
    unknown: 4,
    idle: 5,
    offline: 6,
  };
  return order[activity] ?? 7;
}

export function sortByUrgency(agents: AgentActivity[]): AgentActivity[] {
  return [...agents].sort((a, b) => {
    const byRank = urgencyRank(a.activity) - urgencyRank(b.activity);
    return byRank !== 0 ? byRank : a.bot_name.localeCompare(b.bot_name);
  });
}

export function toRunFact(raw: unknown): RunFact | null {
  const r = asRecord(raw);
  if (!Object.keys(r).length) return null;
  return {
    run_id: asStringOrNull(r.run_id),
    status: asStringOrNull(r.status),
    stop_reason: asStringOrNull(r.stop_reason),
    error: asStringOrNull(r.error),
  };
}

export function toActivityEvidence(raw: unknown): ActivityEvidence | null {
  const r = asRecord(raw);
  if (!Object.keys(r).length) return null;
  return {
    source: asStringOrNull(r.source),
    reason: asStringOrNull(r.reason),
    detail: asStringOrNull(r.detail),
    run: toRunFact(r.run),
    last_heartbeat_at: asTime(r.last_heartbeat_at),
    seconds_since_heartbeat: asNumber(r.seconds_since_heartbeat),
  };
}

export function toAgentActivity(raw: unknown): AgentActivity {
  const r = asRecord(raw);
  const activity = typeof r.activity === "string" ? r.activity : "unknown";
  return {
    bot_name: String(asStringOrNull(r.bot_name) ?? ""),
    activity,
    detail: String(asStringOrNull(r.detail) ?? ""),
    tone: activityTone(activity),
    since: asTime(r.since),
    run_id: asStringOrNull(r.run_id),
    claim_ids: asStringArray(r.claim_ids),
    held_paths: asStringArray(r.held_paths),
    health: asStringOrNull(r.health),
    last_heartbeat_at: asTime(r.last_heartbeat_at),
    attributed_by: asStringOrNull(r.attributed_by),
    evidence: toActivityEvidence(r.evidence),
  };
}

export function toWorkClaim(raw: unknown): WorkClaim {
  const r = asRecord(raw);
  return {
    claim_id: String(asStringOrNull(r.claim_id) ?? ""),
    holder: String(asStringOrNull(r.holder) ?? ""),
    kind: String(asStringOrNull(r.kind) ?? ""),
    subject: String(asStringOrNull(r.subject) ?? ""),
    intent: String(asStringOrNull(r.intent) ?? ""),
    state: String(asStringOrNull(r.state) ?? ""),
    // A missing `live` is not `false`: the server not saying is not a verdict.
    live: r.live === true,
    detail: String(asStringOrNull(r.detail) ?? ""),
    orphaned_at: asTime(r.orphaned_at),
  };
}

export function toSoftConflict(raw: unknown): SoftConflict {
  const r = asRecord(raw);
  const states = asRecord(r.states);
  const mapped: Record<string, string> = {};
  for (const [k, v] of Object.entries(states)) mapped[k] = String(v);
  return {
    subject: String(asStringOrNull(r.subject) ?? ""),
    kind: String(asStringOrNull(r.kind) ?? ""),
    holders: asStringArray(r.holders),
    claim_ids: asStringArray(r.claim_ids),
    reasons: asStringArray(r.reasons),
    states: mapped,
    reclaimable: r.reclaimable === true,
    dead_holder: asStringOrNull(r.dead_holder),
    detail: String(asStringOrNull(r.detail) ?? ""),
  };
}

/** Bucket counts, keeping a missing bucket absent rather than zero-filled. */
function toCounts(raw: unknown): Record<string, number> {
  const r = asRecord(raw);
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(r)) {
    if (typeof v === "number" && Number.isFinite(v)) out[k] = v;
  }
  return out;
}

export function toRoomActivity(raw: unknown): RoomActivity {
  const r = asRecord(raw);
  return {
    room: String(asStringOrNull(r.room) ?? ""),
    agents: Array.isArray(r.agents) ? r.agents.map(toAgentActivity) : [],
    count: asNumber(r.count),
    by_activity: toCounts(r.by_activity),
    by_tone: toCounts(r.by_tone),
    claims: Array.isArray(r.claims) ? r.claims.map(toWorkClaim) : [],
    live_claim_count: asNumber(r.live_claim_count),
    conflicts: Array.isArray(r.conflicts) ? r.conflicts.map(toSoftConflict) : [],
    orphaned: Array.isArray(r.orphaned) ? r.orphaned.map(toWorkClaim) : [],
    members: asStringArray(r.members),
    direct_count: asNumber(r.direct_count),
    effective_members: asStringArray(r.effective_members),
    effective_count: asNumber(r.effective_count),
  };
}

/** Members whose claims a peer could pick up right now. */
export function availableSubjects(snapshot: RoomActivity): string[] {
  return [...new Set(snapshot.orphaned.map((c) => c.subject))].sort();
}

/** Reclaimable conflicts first: those are the ones a peer can act on. */
export function conflictRows(snapshot: RoomActivity): SoftConflict[] {
  return [...snapshot.conflicts].sort((a, b) => Number(b.reclaimable) - Number(a.reclaimable));
}

/**
 * One honest line about the room.
 *
 * A failed read is its own sentence naming the reason, never "0 working" or
 * "everyone idle". A room with no activity at all is also not that: it says the
 * room has no recorded work.
 */
export function activityHeadline(snapshot: RoomActivity | null, error?: string | null): string {
  if (error) return `activity not read - ${error}`;
  if (!snapshot) return "activity not read yet";
  if (snapshot.count === null) return "activity count not reported";
  if (snapshot.agents.length === 0) return "no members have any recorded work yet";

  const crashed = snapshot.agents.filter((a) => a.activity === "crashed").map((a) => a.bot_name);
  const working = snapshot.agents.filter((a) => a.activity === "working").length;
  const parts = [`${working} working`];
  if (crashed.length) parts.push(`${crashed.length} crashed (${crashed.join(", ")})`);
  const unresponsive = snapshot.agents.filter((a) => a.activity === "unresponsive").length;
  if (unresponsive) parts.push(`${unresponsive} unresponsive`);
  if (snapshot.conflicts.length) parts.push(`${snapshot.conflicts.length} overlapping`);
  return parts.join(" · ");
}

/**
 * Both membership counts, or neither.
 *
 * Reporting `direct_count` without `effective_count` would let a header say
 * "3 members" over six visible bots, which is a fabricated count rather than a
 * rounded one.
 */
export function membershipHeadline(snapshot: RoomActivity): string {
  if (snapshot.direct_count === null || snapshot.effective_count === null) {
    return "membership count not reported";
  }
  return `${snapshot.effective_count} member${snapshot.effective_count === 1 ? "" : "s"} (${snapshot.direct_count} direct)`;
}
