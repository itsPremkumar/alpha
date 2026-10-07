/**
 * The pure presentation layer over the group coordination API.
 *
 * Nothing here imports anything. That is deliberate and it is the reason this
 * file is separate from `group-coordination.ts`: the derivations below are the
 * parts most likely to be quietly wrong, and keeping them import-free means
 * `pnpm test` (which globs `src/lib/*.test.mjs`) can assert them without a
 * bundler, a server, or a Gateway.
 *
 * Four rules this file exists to keep, and which the War Room renders literally:
 *
 * 1. **Activity and health are two axes.** `activity` answers "what is it
 *    doing"; `health` answers "is the process answering". A bot can be `blocked`
 *    and `healthy` at once, and a UI that picks one of them to colour a dot
 *    throws away half the signal.
 * 2. **`crashed` is not `unresponsive`.** Only a hard crash verdict makes work
 *    reclaimable. A slow agent may be mid-tool-call, and taking its work would
 *    hand live work to a second agent.
 * 3. **Both membership counts travel.** `direct_count` beside `effective_count`.
 *    A header claiming "3 members" over six visible bots is a fabricated count.
 * 4. **Nothing renders without its evidence.** Every derived word here has a
 *    `reason` a machine can match on and a `detail` a human can read, because a
 *    red dot with no explanation is indistinguishable from a bug.
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

export type EvidenceSource = "run_store" | "heartbeat" | "registry" | "health" | "absent";

export type Attribution = "unattributed" | "run" | "heartbeat" | "claim" | "registry";

export type ClaimKind = "file" | "dir" | "symbol" | "task" | "artifact" | "requirement";

export type ClaimIntent = "reading" | "editing" | "reviewing";

export type ClaimState = "active" | "released" | "expired" | "orphaned";

/** What the run store says about the work an agent is doing. */
export interface RunEvidence {
  run_id: string;
  status: string | null;
  stop_reason: string | null;
  error: string | null;
  /** True when the store had no row at all. Distinct from a live row. */
  absent: boolean;
}

/** Why a state is believed. Nothing renders an activity without one. */
export interface ActivityEvidence {
  source: EvidenceSource;
  /** Stable machine word. A test asserts this, never the prose. */
  reason: string;
  /** The human sentence, for the tooltip. */
  detail: string;
  run: RunEvidence | null;
  last_heartbeat_at: number | null;
  seconds_since_heartbeat: number | null;
}

export interface AgentActivity {
  bot_name: string;
  activity: ActivityState;
  detail: string;
  tone: ActivityTone;
  since: number;
  run_id: string | null;
  claim_ids: string[];
  held_paths: string[];
  health: string | null;
  last_heartbeat_at: number | null;
  attributed_by: Attribution;
  room_name: string | null;
  evidence: ActivityEvidence;
}

export interface WorkClaim {
  claim_id: string;
  room_name: string;
  holder: string;
  kind: ClaimKind;
  subject: string;
  intent: ClaimIntent;
  project_id: string | null;
  run_id: string | null;
  detail: string;
  created_at: number;
  renewed_at: number;
  expires_at: number;
  state: ClaimState;
  orphaned_at: number | null;
  /** Set only from a hard crash verdict. Never from a slow agent. */
  orphan_evidence: Record<string, unknown> | null;
  live: boolean;
}

export interface SoftConflict {
  subject: string;
  kind: string;
  holders: string[];
  claim_ids: string[];
  reasons: string[];
  states: Record<string, ActivityState>;
  /** One of the holders is confirmed dead, so a peer may take the work. */
  reclaimable: boolean;
  dead_holder: string | null;
  detail: string;
}

export interface RoomCoordination {
  ok: boolean;
  room: string;
  project_id: string | null;
  agents: AgentActivity[];
  count: number;
  by_activity: Record<string, number>;
  by_tone: Record<string, number>;
  claims: WorkClaim[];
  live_claim_count: number;
  conflicts: SoftConflict[];
  orphaned: WorkClaim[];
  direct_count: number | null;
  effective_count: number;
}

// ── Presentation ────────────────────────────────────────────────────────────

/** `Badge` tones in `components/ui.tsx`, in severity order. */
export type BadgeTone = "green" | "amber" | "gray" | "blue" | "purple" | "cyan" | "red" | "indigo";

const ACTIVITY_TONE: Record<ActivityState, BadgeTone> = {
  working: "blue",
  idle: "green",
  blocked: "amber",
  // `unresponsive` is warn, not bad. It may be mid-tool-call; painting it as
  // dead would push an operator to reclaim work that is still running.
  unresponsive: "amber",
  crashed: "red",
  offline: "gray",
  unknown: "gray",
};

const ACTIVITY_LABEL: Record<ActivityState, string> = {
  working: "Working",
  idle: "Idle",
  blocked: "Blocked",
  unresponsive: "Unresponsive",
  crashed: "Crashed",
  offline: "Offline",
  unknown: "Not measured",
};

/** An unrecognised state word must render as visibly-not-measured, never green. */
export function activityBadge(agent: AgentActivity): { label: string; tone: BadgeTone } {
  const state = (agent.activity ?? "unknown") as ActivityState;
  const label = ACTIVITY_LABEL[state] ?? "Not measured";
  const tone = ACTIVITY_TONE[state] ?? "gray";
  return { label, tone };
}

/**
 * The second axis, rendered beside the first rather than folded into it.
 *
 * `null` means "no verdict was recorded", which is not the same as `healthy`,
 * and is not the same as `dead`. It gets its own muted word so an operator is
 * never told a bot is fine when the system simply did not look.
 */
export function healthBadge(agent: AgentActivity): { label: string; tone: BadgeTone } {
  const health = agent.health;
  if (!health) return { label: "Health unknown", tone: "gray" };
  if (health === "healthy") return { label: "Healthy", tone: "green" };
  if (health === "stale") return { label: "Stale", tone: "amber" };
  if (health === "stalled") return { label: "Stalled", tone: "red" };
  if (health === "sleeping") return { label: "Sleeping", tone: "indigo" };
  if (health === "dead") return { label: "Dead", tone: "red" };
  return { label: health, tone: "gray" };
}

/**
 * Whether the War Room may offer "take over this work".
 *
 * Deliberately narrower than "the holder looks bad". It requires a claim that
 * has already been *reconciled* into `orphaned`, and separately the holder's
 * activity to be `crashed`. Both, not either: an orphan with a live holder is
 * a bookkeeping bug, and a crashed holder with a live claim is work that has
 * not been handed over yet.
 */
export function reclaimAffordance(
  claim: WorkClaim,
  conflict: SoftConflict | null,
): { allowed: boolean; label: string; reason: string } {
  if (conflict?.reclaimable && conflict.claim_ids.includes(claim.claim_id)) {
    return {
      allowed: true,
      label: "Reclaim",
      reason: conflict.detail || `${conflict.dead_holder} is crashed, so this subject is free to take`,
    };
  }
  if (claim.state === "orphaned") return { allowed: true, label: "Reclaim", reason: "Its holder crashed" };
  if (claim.state !== "active") {
    return { allowed: false, label: "Reclaim", reason: `This claim is ${claim.state}, so nobody is holding it` };
  }
  return {
    allowed: false,
    label: "Reclaim",
    reason: "The holder has not crashed, so its work is still in progress",
  };
}

/**
 * The one number a room header is allowed to lead with.
 *
 * Uses the effective count. The direct count travels in the tooltip because it
 * is genuinely useful context, but a headline figure that disagrees with the
 * rows underneath it is worse than no headline at all.
 */
export function roomHeadline(snapshot: RoomCoordination): { primary: string; caption: string } {
  const effective = snapshot.effective_count ?? snapshot.agents.length;
  const direct = snapshot.direct_count;
  const bots = `${effective} ${effective === 1 ? "bot" : "bots"}`;
  if (direct == null) {
    return { primary: bots, caption: "resolved through the effective roster" };
  }
  if (direct === effective) {
    return { primary: bots, caption: "all members are direct" };
  }
  return {
    primary: bots,
    caption: `${direct} direct · ${effective - direct} inherited or rule-matched`,
  };
}

/** Ordering that puts the rows an operator must act on first. */
export function byUrgency(agents: AgentActivity[]): AgentActivity[] {
  const rank: Record<string, number> = {
    crashed: 0,
    blocked: 1,
    unresponsive: 2,
    offline: 3,
    working: 4,
    idle: 5,
    unknown: 6,
  };
  return [...agents].sort((a, b) => {
    const byRank = (rank[a.activity] ?? 7) - (rank[b.activity] ?? 7);
    return byRank !== 0 ? byRank : a.bot_name.localeCompare(b.bot_name);
  });
}

/** Group the live claims by subject so an overlap reads as one row. */
export function claimsBySubject(claims: WorkClaim[]): { subject: string; claims: WorkClaim[] }[] {
  const map = new Map<string, WorkClaim[]>();
  for (const claim of claims) {
    const bucket = map.get(claim.subject);
    if (bucket) bucket.push(claim);
    else map.set(claim.subject, [claim]);
  }
  return [...map.entries()]
    .map(([subject, claimsForSubject]) => ({ subject, claims: claimsForSubject }))
    .sort((a, b) => b.claims.length - a.claims.length || a.subject.localeCompare(b.subject));
}

/** A failure reason short enough for an `ErrorBox` title. */
export function failureTitle(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error ?? "");
  if (/abort/i.test(message)) return "The read was cancelled";
  if (/network|fetch|failed to fetch/i.test(message)) return "Could not reach the Gateway";
  return "Could not load coordination";
}

// ── Work assignment ─────────────────────────────────────────────────────────
//
// The War Room can *release* and *reclaim* a claim, but until now it could not
// create one — so "assign work" was the one verb an operator could not perform
// from the surface built to watch work. These helpers mirror the request
// contract of `POST /api/groups/{name}/claims` so the form can explain a
// refusal before it sends, while the Gateway stays the authority: its 422 is
// still rendered verbatim if the mirror and the server ever disagree.

/** The server's own vocabulary. `create_claim` 422s on anything outside it. */
export const ASSIGNMENT_KINDS = ["file", "dir", "symbol", "task", "artifact", "requirement"] as const;

export const ASSIGNMENT_INTENTS = ["reading", "editing", "reviewing"] as const;

/** Bounds mirrored from the `ClaimRequest` model. */
export const HOLDER_MAX_LENGTH = 64;
export const SUBJECT_MAX_LENGTH = 2000;
export const DETAIL_MAX_LENGTH = 500;
/** `ttl_seconds: Field(default=120.0, gt=0, le=3600)` — an open-ended claim never expires. */
export const TTL_MIN_SECONDS = 1;
export const TTL_MAX_SECONDS = 3600;
export const TTL_DEFAULT_SECONDS = 120;

export interface AssignmentDraft {
  holder: string;
  kind: string;
  subject: string;
  intent: string;
  detail: string;
  ttl_seconds: number;
}

/** A blank draft. The kind and intent start on the server's own defaults. */
export function emptyAssignmentDraft(): AssignmentDraft {
  return {
    holder: "",
    kind: "task",
    subject: "",
    intent: "editing",
    detail: "",
    ttl_seconds: TTL_DEFAULT_SECONDS,
  };
}

export interface AssignmentProblem {
  /** Matches the form control's `name`, so the message can be rendered beside it. */
  field: string;
  problem: string;
}

/**
 * Blocking problems, each naming the field and the bound it broke.
 *
 * This is a pre-flight mirror, never a gate on its own: the form stays
 * submittable and the server's own 422 is what actually decides. The point is
 * that an operator gets told *which field* and *which limit* before a round
 * trip, instead of "Request failed (HTTP 422)".
 */
export function assignmentProblems(draft: AssignmentDraft): AssignmentProblem[] {
  const problems: AssignmentProblem[] = [];
  const holder = draft.holder.trim();
  const subject = draft.subject.trim();

  if (!holder) {
    problems.push({ field: "holder", problem: "a claim needs an agent to hold it" });
  } else if (holder.length > HOLDER_MAX_LENGTH) {
    problems.push({
      field: "holder",
      problem: `${holder.length} characters — the server accepts at most ${HOLDER_MAX_LENGTH}`,
    });
  }

  if (!subject) {
    problems.push({ field: "subject", problem: "name what is being worked on" });
  } else if (subject.length > SUBJECT_MAX_LENGTH) {
    problems.push({
      field: "subject",
      problem: `${subject.length} characters — the server accepts at most ${SUBJECT_MAX_LENGTH}`,
    });
  }

  if (draft.detail.length > DETAIL_MAX_LENGTH) {
    problems.push({
      field: "detail",
      problem: `${draft.detail.length} characters — the server accepts at most ${DETAIL_MAX_LENGTH}`,
    });
  }

  if (!ASSIGNMENT_KINDS.includes(draft.kind as (typeof ASSIGNMENT_KINDS)[number])) {
    problems.push({ field: "kind", problem: `"${draft.kind}" is not one of ${ASSIGNMENT_KINDS.join(", ")}` });
  }

  if (!ASSIGNMENT_INTENTS.includes(draft.intent as (typeof ASSIGNMENT_INTENTS)[number])) {
    problems.push({ field: "intent", problem: `"${draft.intent}" is not one of ${ASSIGNMENT_INTENTS.join(", ")}` });
  }

  const ttl = draft.ttl_seconds;
  if (typeof ttl !== "number" || !Number.isFinite(ttl) || ttl <= 0 || ttl > TTL_MAX_SECONDS) {
    problems.push({
      field: "ttl_seconds",
      problem: `${String(ttl)}s is outside the ${TTL_MIN_SECONDS}s..${TTL_MAX_SECONDS}s the server accepts`,
    });
  }

  return problems;
}

/**
 * Non-blocking disclosures — things the server will happily record that would
 * nevertheless mislead an operator reading the board back.
 *
 * Deliberately separate from `assignmentProblems`: an unknown holder is *legal*
 * (the store takes any string) and refusing it would be the client inventing a
 * rule the server does not have. It is a warning because the claim would land
 * with no member of this room to match it.
 */
export function assignmentWarnings(draft: AssignmentDraft, roster: string[]): string[] {
  const holder = draft.holder.trim();
  if (!holder || roster.length === 0) return [];
  if (roster.includes(holder)) return [];
  return [
    `${holder} is not in this room's roster (${roster.join(", ")}) — the claim will be recorded, but no member of this room will match it.`,
  ];
}

/**
 * The receipt after the server has confirmed a claim.
 *
 * Written from the *response*, never from the draft: an optimistic sentence
 * built from what the operator typed is exactly the unconfirmed success this
 * surface must not paint. A claim that came back not-live says so rather than
 * claiming a hold the server did not record.
 *
 * It states the lease's **length**, not a countdown. This sentence stays on the
 * board until the next action, so a relative "expires in 10m" ages into a
 * contradiction beside the claim row's live countdown — measured on the live
 * board as `expires in 10m` directly above `expires in 3m 10s` for the same
 * claim, forty seconds after assignment. The lease length is a fixed property
 * of the record: it cannot go stale, and it is a *different quantity* from the
 * remaining time the row beneath it is counting down, so the two can never be
 * read as competing answers to one question.
 */
export function assignmentReceipt(claim: WorkClaim, nowSeconds: number): string {
  if (!claim.live) {
    return `"${claim.subject}" was recorded for ${claim.holder} as ${claim.state}, so nobody is holding it right now.`;
  }
  return `${claim.holder} holds "${claim.subject}" — ${claim.intent} ${claim.kind}, ${leaseLength(claim, nowSeconds)}.`;
}

/** `10m lease`, `lease already over`, or `expiry not reported`. Never a countdown. */
function leaseLength(claim: WorkClaim, nowSeconds: number): string {
  if (!Number.isFinite(claim.expires_at)) return "expiry not reported";
  // `created_at` is the anchor: verified against the live Gateway, a 600s
  // claim writes `created_at` and `expires_at` exactly 600 apart. A record
  // whose creation the server did not report falls back to the read time,
  // which is the same figure the countdown would have shown.
  const start = Number.isFinite(claim.created_at) ? claim.created_at : nowSeconds;
  const length = claim.expires_at - start;
  // A lease of zero or less cannot be held. Reporting the arithmetic rather
  // than picking a side keeps `live: true` and this figure from silently
  // disagreeing in the operator's favour.
  if (length <= 0) return "lease already over";
  return `${durationLabel(length)} lease`;
}

/**
 * When a claim's lease runs out, in words.
 *
 * `expires_at` is epoch **seconds** (verified against the live Gateway: a
 * 600s claim wrote `created_at` and `expires_at` 600 apart). An absent or
 * non-finite expiry is "not reported" — never rendered as never-expiring,
 * which is the one reading that would leave an operator trusting a dead lease.
 */
export function claimExpiryView(claim: WorkClaim, nowSeconds: number): { label: string; tone: BadgeTone } {
  if (typeof claim.expires_at !== "number" || !Number.isFinite(claim.expires_at)) {
    return { label: "expiry not reported", tone: "gray" };
  }
  const remaining = claim.expires_at - nowSeconds;
  if (claim.state === "expired" || remaining <= 0) {
    const over = Math.max(0, -remaining);
    return { label: over < 1 ? "expired just now" : `expired ${durationLabel(over)} ago`, tone: "gray" };
  }
  return { label: `expires in ${durationLabel(remaining)}`, tone: remaining <= 30 ? "amber" : "green" };
}

/**
 * Compact duration. Sub-second input renders as `<1s`, never `0s`.
 *
 * The value is rounded to whole seconds **once**, up front, and every larger
 * unit is derived from that single integer. Rounding each unit independently
 * is a carry bug: a 600-second lease read 300ms after it was created leaves
 * 599.7s, which floored to `9` minutes while its remainder rounded to `60`
 * seconds — so the claim row read `9m 60s`. The same split produced `60s`
 * instead of `1m` at 59.7, `59m 60s` instead of `1h` at 3599.7, and `1h 60m`
 * instead of `2h` at 7199.7. One integer, two derivations, no carry.
 */
export function durationLabel(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "not reported";
  if (seconds < 1) return "<1s";
  const total = Math.round(seconds);
  if (total < 60) return `${total}s`;
  if (total < 3600) {
    const m = Math.floor(total / 60);
    const s = total % 60;
    return s === 0 ? `${m}m` : `${m}m ${s}s`;
  }
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

/** How often a live room re-reads itself while the toggle is on. */
export const LIVE_POLL_MS = 5000;

/**
 * How old the coordination read on screen is.
 *
 * A "live" board whose last successful read is older than two poll intervals
 * is the one healthy-looking case that can still be lying: the toggle stays on
 * while the reads fail, and a green LIVE dot over a five-minute-old snapshot
 * is the failure this discloses. `null` is "no read yet", which is a different
 * claim from "read a moment ago" and gets its own grey word.
 */
export function lastReadView(
  lastReadAtMs: number | null,
  nowMs: number,
): { label: string; tone: BadgeTone; stale: boolean } {
  if (lastReadAtMs == null || !Number.isFinite(lastReadAtMs)) {
    return { label: "no read yet", tone: "gray", stale: true };
  }
  const age = nowMs - lastReadAtMs;
  const ago = age < 1000 ? "just now" : durationLabel(age / 1000);
  const label = ago === "just now" ? "read just now" : `read ${ago} ago`;
  const stale = age >= LIVE_POLL_MS * 2;
  return { label, tone: stale ? "amber" : "green", stale };
}