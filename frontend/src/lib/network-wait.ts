import { get } from "./http";

/**
 * One thread's internet-outage timeline.
 *
 * One route, owned by `app.gateway.routers.thread_runs`:
 *
 * - `GET /threads/{thread_id}/network-waits` — every park this thread recorded,
 *   plus the live link reading.
 *
 * ## Why this is not `GET /api/ops/network`
 *
 * `lib/network.ts` answers *"is the internet up right now"*: a four-state link
 * reading with per-endpoint round-trips and a re-probe schedule. It is a
 * property of the **machine**, and it is the same for every thread on it.
 *
 * This answers *"what happened to this conversation"*: when the link died, when
 * the work stopped waiting, how long it waited, and whether it is waiting still.
 * A thread that survived an outage an hour ago has a perfectly healthy link now
 * and a lost half-hour of work in its history, so the live reading alone cannot
 * show it.
 *
 * ## The honesty rules this client enforces
 *
 * - `wait_seconds: null` is **unmeasured**, never `0`. "We could not measure how
 *   long it waited" and "it took no time" lead to opposite conclusions, so the
 *   mapper keeps the null and the view renders words.
 * - `reported: false` is never an empty timeline. A `database.backend: memory`
 *   deployment has nowhere durable to record a park, so "this thread was never
 *   interrupted" would be a claim that deployment cannot make.
 * - **Settled rows are returned and kept.** An outage that ended an hour ago is
 *   still part of this conversation. A UI that rendered only the open wait would
 *   erase the event the user is asking about the moment it resolved.
 * - An unfamiliar `state` string is preserved verbatim rather than snapped to a
 *   known one, because a state from a newer Gateway is a claim about a machine
 *   this build has never heard of.
 * - A failed request rejects. It is never resolved to "no outages".
 */

/** One park: when the link died, and when (or whether) the wait ended. */
export interface ThreadNetworkWait {
  wait_id: string;
  /** The run that parked. Absent for a park recorded before a run row existed. */
  run_id: string | null;
  /** `waiting` / `resuming` / `resumed` / `completed` / `gave_up`, verbatim. */
  state: string;
  reason: string;
  attempt: number;
  /** When the link was lost. */
  first_waited_at: string | null;
  /**
   * When the wait ended — resumed, completed, or given up on.
   *
   * `null` on an open row means *still waiting*, which is a state, not a zero
   * timestamp. This is deliberately not `updated_at`: a failed resume attempt
   * rewrites the backoff and moves `updated_at`, so reading the recovery time
   * off it would report a *scheduled retry* as the moment the link came back.
   */
  terminal_at: string | null;
  /** When the next resume is allowed to be attempted. */
  next_attempt_at: string | null;
  last_error: string | null;
  /** The continuation that picked the work back up. */
  resumed_from_run_id: string | null;
}

export interface ThreadNetworkWaits {
  /** False means this process records no per-thread outage timeline at all. */
  reported: boolean;
  reason: string;
  detail: string;
  /** The live link reading, the same projection `GET /api/ops/network` returns. */
  connectivity: Record<string, unknown>;
  /**
   * Whether a parked session may ever be given up on.
   *
   * `false` — the default — means this deployment waits indefinitely. `null`
   * means the deployment did not say, which is a third state and must not be
   * read as "it will give up eventually".
   */
  bounded: boolean | null;
  max_attempts: number | null;
  /** Every park this thread recorded, newest first. Includes settled rows. */
  waits: ThreadNetworkWait[];
  /** Only the row that is still waiting. `null` when nothing is parked. */
  open_wait: ThreadNetworkWait | null;
  /**
   * Seconds the open wait has been parked.
   *
   * `null` while the wait is open *by construction* — it has not ended yet — and
   * `null` for an unmeasurable stamp. A view that needs a live timer derives it
   * from `open_wait.first_waited_at` and its own clock, because only this
   * browser knows what time it is now.
   */
  wait_seconds: number | null;
  /** Rows the server knows about, which may exceed the returned page. */
  total_waits: number | null;
  returned_waits: number | null;
}

type Rec = Record<string, unknown>;

function rec(value: unknown): Rec {
  return value && typeof value === "object" ? (value as Rec) : {};
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function strOrNull(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function bool(value: unknown): boolean {
  return Boolean(value);
}

function boolOrNull(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

/** A finite number, or `null`. `0` is never invented out of an absent field. */
function numOrNull(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value;
}

function toWait(value: unknown): ThreadNetworkWait {
  const r = rec(value);
  return {
    wait_id: str(r.wait_id),
    run_id: strOrNull(r.run_id),
    state: str(r.state),
    reason: str(r.reason),
    attempt: numOrNull(r.attempt) ?? 0,
    first_waited_at: strOrNull(r.first_waited_at),
    terminal_at: strOrNull(r.terminal_at),
    next_attempt_at: strOrNull(r.next_attempt_at),
    last_error: strOrNull(r.last_error),
    resumed_from_run_id: strOrNull(r.resumed_from_run_id),
  };
}

/** Map the server payload verbatim, keeping every absence as `null`. */
export function toThreadNetworkWaits(body: unknown): ThreadNetworkWaits {
  const d = rec(body);
  const waits = Array.isArray(d.waits) ? (d.waits as unknown[]).map(toWait) : [];
  return {
    reported: bool(d.reported),
    reason: str(d.reason),
    detail: str(d.detail),
    connectivity: rec(d.connectivity),
    bounded: boolOrNull(d.bounded),
    max_attempts: numOrNull(d.max_attempts),
    waits,
    open_wait: d.open_wait === null || d.open_wait === undefined ? null : toWait(d.open_wait),
    wait_seconds: numOrNull(d.wait_seconds),
    total_waits: numOrNull(d.total_waits),
    returned_waits: numOrNull(d.returned_waits),
  };
}

/**
 * Read one thread's outage timeline. Rejects with the server's reason.
 *
 * A rejected call must surface its error rather than resolve to an empty
 * timeline: "this thread never lost its internet" and "we could not read whether
 * it did" are opposite answers to the question the bubble exists to answer.
 */
export async function fetchThreadNetworkWaits(threadId: string): Promise<ThreadNetworkWaits> {
  return toThreadNetworkWaits(await get<unknown>(`/threads/${encodeURIComponent(threadId)}/network-waits`));
}
