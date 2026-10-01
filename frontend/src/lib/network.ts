import { get, send } from "./http";

/**
 * The Gateway's connectivity surface.
 *
 * Two routes, both owned by `app.gateway.routers.ops` and both reading the
 * durable-runtime monitor the Gateway lifespan installs:
 *
 * - `GET  /ops/network`          - the current reading plus the retry schedule
 * - `POST /ops/network/recheck`  - force one immediate measurement
 *
 * ## Why this is not `GET /api/system/vitals`
 *
 * The host system monitor already has an `internet` block, and it answers a
 * *different* question: a single TCP connect to `1.1.1.1:53` sampled on the
 * host-monitor tick. This plane answers the runtime's question — a four-state
 * link reading with hysteresis, per-endpoint round-trips, whether a network
 * operation may be attempted right now, and the automatic re-probe schedule —
 * and it is the one the durable-runtime contract is written against. Reading
 * either one and rendering the other would report a healthy link while the
 * runtime had parked work.
 *
 * ## The honesty rules this client enforces
 *
 * - `latency_ms` is `null` when no endpoint was reachable. `null` is *not* `0`,
 *   and `0` would read as the fastest possible link rather than as a failed
 *   measurement.
 * - `state` is the server's own string. A newer Gateway may send a state this
 *   build does not know; it is preserved verbatim rather than snapped to a
 *   known value, because a connectivity state is a claim about the machine and
 *   guessing one is worse than showing an unfamiliar word.
 * - `reported: false` is never treated as a healthy reading. The reason travels
 *   with it and the view shows it.
 * - A failed request rejects. It is never resolved to a "no problems" object.
 */

/** The four states the runtime defines. `unknown` is real and is never rounded. */
export const CONNECTIVITY_STATES = ["online", "degraded", "offline", "unknown"] as const;

export type KnownConnectivityState = (typeof CONNECTIVITY_STATES)[number];

/** One reachability endpoint and the round-trip the last probe measured. */
export interface ConnectivityTarget {
  name: string;
  reachable: boolean;
  /** Measured connect round-trip. Reported for unreachable targets too. */
  latency_ms: number | null;
  /** Classified transport failure, e.g. `timeout`, `dns_failure`. */
  failure_kind: string;
  /** Mechanism-only note. Never a raw exception message. */
  detail: string;
}

/** The automatic re-probe schedule, as the backend itself decided it. */
export interface ConnectivityRetry {
  /** True while the background poll loop is running in this process. */
  automatic: boolean;
  /** True only while the loop runs AND the link is not fully up. */
  retrying: boolean;
  /** Seconds until the next automatic probe: the drawn, jittered delay. */
  next_probe_seconds: number | null;
  poll_interval_seconds: number | null;
  backoff_max_seconds: number | null;
}

/** What one operator-triggered re-probe did. */
export interface ConnectivityRecheck {
  performed: boolean;
  reason: string;
  detail: string;
  changed: boolean | null;
  /** Consecutive agreeing observations collected so far. */
  consecutive_agreeing: number | null;
  /** Consecutive observations required to publish a recovery. */
  confirmations_required: number | null;
}

export interface ConnectivityParkedSessions {
  reported: boolean;
  reason: string;
  detail: string;
  open_waits: number | null;
  claimed: number | null;
  resumed: number | null;
  gave_up: number | null;
}

export interface Connectivity {
  /** False means this process is not measuring connectivity at all. */
  reported: boolean;
  reason: string;
  /**
   * The server's own state string, preserved verbatim.
   *
   * Typed as the known union *or* any string so a state added by a newer
   * Gateway reaches the view instead of being coerced into a known one.
   */
  state: KnownConnectivityState | (string & {}) | null;
  state_detail: string;
  /**
   * Whether a network operation may be attempted now. `true` for `unknown`:
   * not knowing is not knowing the link is down.
   */
  allows_network_attempt: boolean | null;
  /** Mean round-trip over the reachable endpoints. Null when none were reachable. */
  latency_ms: number | null;
  monitoring: boolean;
  /**
   * Seconds since the last completed probe, or `null` when nothing has been
   * measured.
   *
   * There is deliberately no absolute timestamp on this payload. The reading is
   * stamped from the monitor's monotonic clock, so exposing it as a wall-clock
   * time would be a confident wrong number; an age is the only thing a consumer
   * outside the backend can compute correctly.
   */
  observed_age_seconds: number | null;
  targets: ConnectivityTarget[];
  retry: ConnectivityRetry;
  parked_durability: string;
  parked_sessions: ConnectivityParkedSessions | null;
  /** Present only on the recheck route. */
  recheck: ConnectivityRecheck | null;
  notes: string[];
}

type Rec = Record<string, unknown>;

function rec(value: unknown): Rec {
  return value && typeof value === "object" ? (value as Rec) : {};
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

/** A string, or `null` when the server did not send one. Never a placeholder. */
function strOrNull(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function bool(value: unknown): boolean {
  return Boolean(value);
}

/** `true`/`false`, or `null` when the server measured nothing. */
function boolOrNull(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

/** A finite number, or `null`. `0` and `NaN` are never invented from absent data. */
function numOrNull(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value;
}

function toTarget(value: unknown): ConnectivityTarget {
  const r = rec(value);
  return {
    name: str(r.name, "?"),
    reachable: bool(r.reachable),
    latency_ms: numOrNull(r.latency_ms),
    failure_kind: str(r.failure_kind),
    detail: str(r.detail),
  };
}

function toRetry(value: unknown): ConnectivityRetry {
  const r = rec(value);
  return {
    automatic: bool(r.automatic),
    retrying: bool(r.retrying),
    next_probe_seconds: numOrNull(r.next_probe_seconds),
    poll_interval_seconds: numOrNull(r.poll_interval_seconds),
    backoff_max_seconds: numOrNull(r.backoff_max_seconds),
  };
}

function toRecheck(value: unknown): ConnectivityRecheck | null {
  if (value === null || value === undefined) return null;
  const r = rec(value);
  return {
    performed: bool(r.performed),
    reason: str(r.reason),
    detail: str(r.detail),
    changed: boolOrNull(r.changed),
    consecutive_agreeing: numOrNull(r.consecutive_agreeing),
    confirmations_required: numOrNull(r.confirmations_required),
  };
}

function toParked(value: unknown): ConnectivityParkedSessions | null {
  if (value === null || value === undefined) return null;
  const r = rec(value);
  return {
    reported: bool(r.reported),
    reason: str(r.reason),
    detail: str(r.detail),
    open_waits: numOrNull(r.open_waits),
    claimed: numOrNull(r.claimed),
    resumed: numOrNull(r.resumed),
    gave_up: numOrNull(r.gave_up),
  };
}

/** Map the server payload verbatim, keeping every absence as `null`. */
export function toConnectivity(body: unknown): Connectivity {
  const d = rec(body);
  return {
    reported: bool(d.reported),
    reason: str(d.reason),
    state: strOrNull(d.state),
    state_detail: str(d.state_detail),
    allows_network_attempt: boolOrNull(d.allows_network_attempt),
    latency_ms: numOrNull(d.latency_ms),
    monitoring: bool(d.monitoring),
    observed_age_seconds: numOrNull(d.observed_age_seconds),
    targets: Array.isArray(d.targets) ? (d.targets as unknown[]).map(toTarget) : [],
    retry: toRetry(d.retry),
    parked_durability: str(d.parked_durability, "unavailable"),
    parked_sessions: toParked(d.parked_sessions),
    recheck: toRecheck(d.recheck),
    notes: Array.isArray(d.notes) ? (d.notes as unknown[]).map((n) => String(n)) : [],
  };
}

/** Read the current connectivity reading. Rejects with the server's reason. */
export async function fetchConnectivity(): Promise<Connectivity> {
  return toConnectivity(await get<unknown>("/ops/network"));
}

/**
 * Force one immediate measurement through the backend's own recheck route.
 *
 * The response is the *new* reading, so a caller re-renders from this rather
 * than assuming the link came back. Rejects with the server's reason; a probe
 * that did not run is answered with `performed: false`, which is a successful
 * HTTP call reporting a failed measurement — never an exception, and never a
 * success the caller has to guess at.
 */
export async function recheckConnectivity(): Promise<Connectivity> {
  return toConnectivity(await send<unknown>("/ops/network/recheck", "POST"));
}

/** How a connectivity reading should read in the workspace strip. */
export interface ConnectivityView {
  /** The measured or reported figure, e.g. `18 ms`, `offline`, `unknown`. */
  value: string;
  /** The noun beside the value. A dash is only ever rendered with words here. */
  label: string;
  /** Names the route, the unit, and why an absent value is absent. */
  title: string;
  tone: "green" | "amber" | "red" | "gray";
  /** Whether offering the manual Retry control can actually change the answer. */
  canRetry: boolean;
  /** A sentence to show in the row when something is wrong. `null` when healthy. */
  detail: string | null;
}

const ROUTE = "GET /api/ops/network";

/** A `—` with no words beside it is the defect this whole strip is built around. */
const UNREPORTED_LATENCY = "latency not reported";

function ms(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  // Sub-millisecond is real on loopback, so never round a measured value to 0.
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ms`;
}

/** The sentence explaining an automatic re-probe, or `null` when idle. */
function retrySentence(status: Connectivity): string | null {
  if (!status.retry.automatic) {
    return "Automatic re-probing is switched off in this process, so nothing will retry on its own.";
  }
  if (!status.retry.retrying) return null;
  const wait = status.retry.next_probe_seconds;
  const ceiling = status.retry.backoff_max_seconds;
  if (wait === null) return "The backend is re-probing automatically until the link returns.";
  const target = ceiling === null ? "" : ` (backing off toward ${Math.round(ceiling)}s)`;
  return `Backend is re-probing automatically in ~${Math.round(wait)}s${target} and keeps trying until the link returns.`;
}

/** The unreachable endpoints, named, so "degraded" is actionable. */
function unreachableSentence(status: Connectivity): string | null {
  const down = status.targets.filter((t) => !t.reachable).map((t) => t.name);
  if (down.length === 0) return null;
  return `Unreachable: ${down.join(", ")}.`;
}

/** How many poll intervals a reading may age by before it is disclosed as stale. */
const STALE_POLL_INTERVALS = 3;

/**
 * Whether the reading is older than the poll cadence, and says so.
 *
 * This is reachable, not theoretical: while the link is down the backend
 * deliberately backs off toward `backoff_max_seconds` (five minutes by
 * default), so a ten-second UI refresh can be showing a reading that is minutes
 * old. Presenting that as current is the one thing this entry must not do — and
 * it is exactly what makes the manual Retry worth having.
 */
function staleSentence(status: Connectivity): string | null {
  const age = status.observed_age_seconds;
  const interval = status.retry.poll_interval_seconds;
  if (age === null || interval === null) return null;
  if (age <= STALE_POLL_INTERVALS * interval) return null;
  return `Reading is ${Math.round(age)}s old against a ${Math.round(interval)}s poll cadence, so it may be out of date — use Retry to measure now.`;
}

/**
 * Derive the strip's connectivity entry from the server's reading.
 *
 * Every branch states its own absence. The three cases that must never collapse
 * into each other are: the link is down, the probe could not run (`unknown`),
 * and nothing was measured at all (a failed read, or `reported: false`).
 */
export function connectivityView(status: Connectivity | null, readFailed: boolean): ConnectivityView {
  if (status === null) {
    return readFailed
      ? {
          value: "—",
          label: "internet not reported",
          tone: "gray",
          canRetry: true,
          detail: "The connectivity status could not be read, so the link is unknown rather than fine.",
          title:
            `Internet connectivity: NOT REPORTED. ${ROUTE} did not answer, so no state and no round-trip time are known. ` +
            "This dash is NOT a measured 0 ms and NOT a healthy link.",
        }
      : {
          value: "—",
          label: "internet not reported yet",
          tone: "gray",
          canRetry: false,
          detail: null,
          title: `Internet connectivity: not reported yet. ${ROUTE} has not answered for this view.`,
        };
  }

  if (!status.reported) {
    // Nothing is being measured. This is the honest answer, and it is a
    // different claim from "the link is fine" and from "the link is down".
    return {
      value: "—",
      label: "internet not measured, not reported",
      tone: "gray",
      // A recheck could not help: there is no probe in this process to run.
      canRetry: false,
      detail: `The backend is not measuring connectivity (${status.reason}).`,
      title:
        `Internet connectivity: NOT MEASURED. ${ROUTE} reported reason "${status.reason}", so no state and no round-trip ` +
        "time exist. This dash is NOT a measured 0 ms, NOT a healthy link, and NOT a confirmed outage.",
    };
  }

  const retryNote = retrySentence(status);
  const downNote = unreachableSentence(status);
  const staleNote = staleSentence(status);

  if (status.state === "online") {
    const measured = status.latency_ms !== null;
    return {
      // A healthy row stays quiet: the server's own "Connectivity confirmed."
      // sentence is not repeated beside a green reading. Staleness IS surfaced,
      // because a reading older than the poll cadence is not current — and that
      // is reachable precisely because the backend backs off while offline.
      detail: staleNote,
      value: ms(status.latency_ms),
      label: measured ? "internet" : `internet, ${UNREPORTED_LATENCY}`,
      tone: "green",
      // Only a stale reading earns a control: a fresh healthy link has nothing
      // to re-measure.
      canRetry: staleNote !== null,
      title: measured
        ? `Internet connectivity: ONLINE. Mean measured TCP connect round-trip ${status.latency_ms} ms across the reachable endpoints, from ${ROUTE} (latency_ms). It is a TCP connect only — no HTTP request and no payload — so it measures the route, not any provider's health.`
        : `Internet connectivity: ONLINE, but ${UNREPORTED_LATENCY}. The link state is confirmed while no reachable endpoint produced a round-trip time in the last probe. This dash is NOT 0 ms.`,
    };
  }

  const base = {
    detail: [downNote, retryNote, staleNote, status.state_detail].filter(Boolean).join(" ") || null,
  };

  if (status.state === "degraded") {
    const reported = status.latency_ms !== null;
    return {
      ...base,
      value: ms(status.latency_ms),
      label: reported ? "internet partial" : `internet partial, ${UNREPORTED_LATENCY}`,
      tone: "amber",
      canRetry: true,
      title: `Internet connectivity: PARTIAL. Some endpoints answered and some did not, so a network operation is still attempted (allows_network_attempt=${
        status.allows_network_attempt === null ? "unknown" : String(status.allows_network_attempt)
      }). A degraded link is not an outage and never parks work. From ${ROUTE}.`,
    };
  }

  if (status.state === "offline") {
    return {
      ...base,
      value: "offline",
      label: "internet",
      tone: "red",
      canRetry: true,
      title:
        "Internet connectivity: OFFLINE — a confirmed outage, corroborated across consecutive probes. Work needing the link is " +
        `parked and resumes automatically when it returns. The backend keeps re-probing${status.retry.next_probe_seconds === null ? "" : ` (next in ~${Math.round(status.retry.next_probe_seconds)}s)`}. From ${ROUTE}.`,
    };
  }

  if (status.state === "unknown") {
    return {
      ...base,
      value: "unknown",
      label: "internet unknown",
      tone: "amber",
      canRetry: true,
      title:
        "Internet connectivity: UNKNOWN — the probe could not run or returned nothing. This is never reported as offline, because " +
        "a broken probe is not a dead link, and a network operation is still attempted. Use Retry to ask for a fresh measurement. From " +
        `${ROUTE}.`,
    };
  }

  // A state this build does not know. Render the server's word untouched rather
  // than snapping it to one of the four we do understand.
  //
  // `state` is nullable and the four known values are already handled above, so
  // a null lands here too — and that is a THIRD claim, not an unrecognised word:
  // the process says it is measuring connectivity and named no state. Printing
  // the raw `null` would read as a state literally called "null", so it is
  // disclosed as an absent state instead.
  if (status.state === null) {
    return {
      ...base,
      value: "—",
      // The label carries the words the strip's dash rule requires; "not named"
      // alone would leave a bare glyph next to an ambiguous noun.
      label: "internet state not reported",
      tone: "gray",
      canRetry: true,
      title:
        `Internet connectivity: NOT REPORTED. ${ROUTE} reports it is measuring connectivity but sent no state. ` +
        "This dash is NOT a measured value, NOT a healthy link, and NOT a confirmed outage.",
    };
  }

  return {
    ...base,
    value: status.state,
    label: "internet (state reported by the Gateway)",
    tone: "amber",
    canRetry: true,
    title: `Internet connectivity: the Gateway reported the state "${status.state}", which this UI build does not recognise. It is shown verbatim rather than mapped onto a known state. From ${ROUTE}.`,
  };
}
