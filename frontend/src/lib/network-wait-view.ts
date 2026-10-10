import type { ThreadNetworkWait, ThreadNetworkWaits } from "./network-wait";

/**
 * Every sentence the in-chat network bubble renders.
 *
 * It lives here rather than beside the markup for the reason
 * `connectivityView()` in `lib/network.ts` does: a bubble whose wording is
 * written twice can disagree with itself, and the disagreement is the failure
 * that matters — "waiting for network" beside a clock that has stopped is how a
 * parked session reads as hung. One derivation, one source of truth, and the
 * test suite drives the exact function that produces each string.
 *
 * The component renders these; it must not branch on a state of its own.
 */

export type NetworkWaitKind = "waiting" | "resumed" | "gave_up" | "unavailable";

export interface NetworkWaitBubble {
  /** Which of the four states this bubble is showing. */
  kind: NetworkWaitKind;
  /** `Connectivity lost` / `Back online` / `Waiting stopped` / `Network status`. */
  title: string;
  /** The sentence a user reads. Stated in observable terms, never predicted. */
  detail: string;
  /** `18:42:05` when the server sent a timestamp, `null` otherwise. */
  lostAt: string | null;
  /** `18:47:27` when the wait ended, `null` while it is still open. */
  backAt: string | null;
  /** `5m 22s` when the server measured it, `null` otherwise. Never `0s`. */
  waited: string | null;
  /** Resume attempts so far. `null` when the server did not report one. */
  attempts: number | null;
  /**
   * Whether this deployment will ever give up on the wait.
   *
   * `false` is the load-bearing value: it is what lets the bubble say "for as
   * long as it takes" instead of hinting at a deadline it cannot know.
   */
  unbounded: boolean;
  tone: "amber" | "green" | "red" | "gray";
  /** Tooltip naming the routes behind every figure in this bubble. */
  title2: string;
}

const ROUTE = "GET /api/threads/{thread_id}/network-waits";

/**
 * A wall-clock ISO stamp as `HH:MM:SS` in the viewer's own timezone.
 *
 * Returns `null` for an absent or unparseable stamp rather than an epoch: the
 * epoch renders as 1970, which claims the outage happened then. The backend
 * deliberately sends `observed_age_seconds`-style ages instead of absolute
 * stamps everywhere else; this one is a real wall-clock event time, so the local
 * clock is the honest reading — and a stamp the client cannot parse stays a
 * disclosure rather than becoming a wrong time.
 */
export function formatClock(iso: string | null): string | null {
  if (!iso) return null;
  const parsed = Date.parse(iso);
  if (!Number.isFinite(parsed)) return null;
  const d = new Date(parsed);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/**
 * A measured duration as `5m 22s` / `1h 04m`.
 *
 * `null` is preserved end to end. A `0s` would say the outage cost nothing,
 * which is exactly the claim a missing stamp cannot support.
 */
export function formatDuration(seconds: number | null): string | null {
  if (seconds === null || !Number.isFinite(seconds)) return null;
  if (seconds < 0) return null;
  const whole = Math.floor(seconds);
  if (whole < 60) return `${whole}s`;
  const minutes = Math.floor(whole / 60);
  const secs = whole % 60;
  if (minutes < 60) return secs ? `${minutes}m ${String(secs).padStart(2, "0")}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return mins ? `${hours}h ${String(mins).padStart(2, "0")}m` : `${hours}h`;
}

/** The unmeasured disclosure. Every missing duration renders these words. */
export const DURATION_UNMEASURED = "wait duration not reported";
/** The unmeasured disclosure for a timestamp the client could not read. */
export const STAMP_UNREADABLE = "time not reported";

function isOpen(wait: ThreadNetworkWait): boolean {
  return wait.state === "waiting" || wait.state === "resuming";
}

/**
 * One bubble for one park.
 *
 * `nowMs` is the viewer's clock and is used **only** for the live counter while
 * a wait is still open. A settled row's duration comes from the server, because
 * the backend measured that interval and the client was not running for it — a
 * client-computed "5m 22s" an hour later would be this browser's arithmetic
 * presented as the runtime's.
 */
export function networkWaitBubble(wait: ThreadNetworkWait, opts: { unbounded: boolean; nowMs: number }): NetworkWaitBubble {
  const { unbounded, nowMs } = opts;
  const lostAt = formatClock(wait.first_waited_at);
  const stamp = lostAt ?? STAMP_UNREADABLE;

  if (isOpen(wait)) {
    return {
      kind: "waiting",
      title: "Connectivity lost",
      detail: unbounded
        ? "I'm waiting for the internet to come back. Nothing was lost — I'll pick this up automatically as soon as it does, for as long as it takes."
        : "I'm waiting for the internet to come back and will resume automatically. This deployment gives up after a set number of attempts.",
      lostAt,
      backAt: null,
      waited: liveElapsed(wait.first_waited_at, nowMs),
      attempts: wait.attempt,
      unbounded,
      tone: "amber",
      title2:
        `Recorded by ${ROUTE}. first_waited_at is when the link died; the counter is this browser's clock since then, ` +
        "not a server measurement. `bounded: false` means this deployment never gives up on a parked session.",
    };
  }

  if (wait.state === "gave_up") {
    return {
      kind: "gave_up",
      title: "Waiting stopped",
      detail:
        wait.last_error
          ? `Waiting for the internet stopped after this session ran out of resume attempts: ${wait.last_error}`
          : "Waiting for the internet stopped after this session ran out of resume attempts.",
      lostAt,
      backAt: formatClock(wait.terminal_at),
      waited: null,
      attempts: wait.attempt,
      unbounded,
      tone: "red",
      title2:
        `Recorded by ${ROUTE}. The wait settled as gave_up rather than resumed, so the work below did not continue automatically. ` +
        "Send another message, or resume the run, once the link is stable.",
    };
  }

  // `resumed` and `completed` both mean the wait ended in progress. An unfamiliar
  // state from a newer Gateway renders here too, verbatim in `title2` below,
  // rather than being snapped to a word this build never sent.
  const endedAt = formatClock(wait.terminal_at);
  const waited = settledDuration(wait);
  const resumeNote = wait.resumed_from_run_id ? "Picking up where I left off." : "The wait ended; the run was not relaunched.";
  return {
    kind: "resumed",
    title: "Back online",
    detail: `The internet came back at ${endedAt ?? STAMP_UNREADABLE}${
      waited ? `, after ${waited} of waiting` : ""
    }. ${resumeNote}`,
    lostAt,
    backAt: endedAt,
    waited,
    attempts: wait.attempt,
    unbounded,
    tone: "green",
    title2:
      `Recorded by ${ROUTE}. first_waited_at is when the link died and terminal_at is when the wait settled — ` +
      "a separate column from updated_at, which also moves when a failed resume merely writes its next backoff. " +
      (wait.state === "resumed" ? "" : `Server-reported state: ${wait.state}. `),
  };
}

/** The server's measured duration for a settled row, from its two stamps. */
function settledDuration(wait: ThreadNetworkWait): string | null {
  if (!wait.first_waited_at || !wait.terminal_at) return null;
  const start = Date.parse(wait.first_waited_at);
  const end = Date.parse(wait.terminal_at);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  return formatDuration(Math.max(0, (end - start) / 1000));
}

/**
 * The ticking counter for an open wait, from the browser's own clock.
 *
 * This is explicitly a *client* observation and never a server measurement: the
 * row has not ended, so no duration exists to report. A missing or unparseable
 * `first_waited_at` yields `null`, which the component renders as words — never
 * `0s`, which would read as "the outage is over" or "nothing has happened yet".
 */
function liveElapsed(firstWaitedAt: string | null, nowMs: number): string | null {
  if (!firstWaitedAt) return null;
  const start = Date.parse(firstWaitedAt);
  if (!Number.isFinite(start)) return null;
  return formatDuration(Math.max(0, (nowMs - start) / 1000));
}

/**
 * The whole timeline for one thread.
 *
 * `null` when there is genuinely nothing to show is impossible for a reported
 * payload: a thread with no parks returns `reported: true` and an empty list,
 * which the caller renders as nothing at all. The unreported case — a memory
 * backend, or a disabled network — gets its own bubble carrying the server's
 * reason, because silence about the timeline would read as "no outages".
 */
export function networkWaitTimeline(
  data: ThreadNetworkWaits | null,
  readFailed: boolean,
  nowMs: number,
): NetworkWaitBubble[] {
  if (data === null) {
    return [
      {
        kind: "unavailable",
        title: "Network status unavailable",
        detail: readFailed
          ? "The outage timeline for this chat could not be read, so whether the internet dropped during it is unknown rather than fine."
          : "The outage timeline for this chat is not reported by the backend.",
        lostAt: null,
        backAt: null,
        waited: null,
        attempts: null,
        unbounded: false,
        tone: "gray",
        title2: `${ROUTE} did not answer with a timeline. This is NOT a measurement that no outage occurred.`,
      },
    ];
  }

  if (!data.reported) {
    return [
      {
        kind: "unavailable",
        title: "Network status unavailable",
        // The server's own reason. `detail` is the explanatory sentence it sent
        // for exactly this deployment shape; falling back to a generic line
        // would discard the one fact that tells an operator what to fix.
        detail: data.detail || data.reason || "This process does not record per-thread outage timelines.",
        lostAt: null,
        backAt: null,
        waited: null,
        attempts: null,
        unbounded: false,
        tone: "gray",
        title2: `${ROUTE} reported reason \`${data.reason || "none"}\`. No park was recorded, which is NOT the same as no outage happening.`,
      },
    ];
  }

  if (data.waits.length === 0) {
    return [];
  }

  const unbounded = data.bounded === false;
  return data.waits.map((wait) => networkWaitBubble(wait, { unbounded, nowMs }));
}

/**
 * Whether this deployment waits forever.
 *
 * `null` is the answer to a third state: the backend did not say. It must not
 * be read as `false`, which would have the bubble hint at a deadline nobody
 * declared, or as `true`, which would promise patience nobody promised.
 */
export function waitUnbounded(data: ThreadNetworkWaits | null): boolean | null {
  return data === null ? null : data.bounded === null ? null : data.bounded === false;
}
