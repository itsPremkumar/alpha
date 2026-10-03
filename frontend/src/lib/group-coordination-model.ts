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