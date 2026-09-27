import type { ToolCall, ToolCallStatus } from "../types/chat";

/**
 * Where the current run is, derived ONLY from what the stream has actually
 * reported. Nothing here predicts or guesses: a phase is a description of the
 * newest state the Gateway sent, never an expectation of the next one.
 */
export type ActivityPhase = "waiting" | "thinking" | "tools" | "writing";

export interface ActivityState {
  phase: ActivityPhase;
  /** Tool calls observed in this turn. */
  toolTotal: number;
  /** Calls with no result reported yet — not a failure, not a success. */
  toolRunning: number;
  /** Calls whose outcome the run reported (any verdict, including `unknown`). */
  toolSettled: number;
  hasThinking: boolean;
  hasContent: boolean;
}

/** The subset of a message the activity derivation reads. */
export interface ActivitySource {
  content?: string;
  thinking?: string;
  toolCalls?: ToolCall[];
}

/**
 * True when the run reported ANY outcome for this call.
 *
 * Deliberately not "succeeded": `unknown`, `failed` and `error` are all
 * settled — the call is over. What it settled *as* is carried separately by
 * `ToolPill`, which is the only place a verdict is drawn.
 */
function isSettled(status?: ToolCallStatus): boolean {
  return typeof status === "string";
}

export function deriveActivity(messages: ActivitySource[]): ActivityState {
  let toolTotal = 0;
  let toolRunning = 0;
  let toolSettled = 0;
  let hasThinking = false;
  let hasContent = false;

  for (const message of messages) {
    if (typeof message.thinking === "string" && message.thinking.trim()) hasThinking = true;
    if (typeof message.content === "string" && message.content.trim()) hasContent = true;
    for (const call of message.toolCalls ?? []) {
      toolTotal += 1;
      if (isSettled(call.status)) toolSettled += 1;
      else toolRunning += 1;
    }
  }

  // A tool still in flight outranks everything: work is happening, and the
  // answer cannot be final. Existing content outranks thinking, because text
  // already on screen means generation has begun.
  const phase: ActivityPhase = toolRunning > 0 ? "tools"
    : hasContent ? "writing"
      : hasThinking ? "thinking"
        : "waiting";

  return { phase, toolTotal, toolRunning, toolSettled, hasThinking, hasContent };
}

/**
 * Plain-language label for the phase, stated as observable present tense.
 *
 * The tool counts are shown only when there is a real tool call to count, so
 * the line never claims "0 of 0 done" before any tool work has been reported.
 */
export function phaseLabel(state: ActivityState): string {
  switch (state.phase) {
    case "tools":
      return state.toolSettled > 0
        ? `Running tools… ${state.toolSettled} of ${state.toolTotal} done`
        : "Running tools…";
    case "thinking":
      return "Thinking…";
    case "writing":
      return "Writing answer…";
    default:
      return "Starting…";
  }
}

/**
 * Everything since the most recent user prompt.
 *
 * The transcript is append-only across turns, so a run's own work is exactly
 * the run of assistant messages after the last thing the user said. Returning
 * an empty list for an all-assistant history keeps prior turns' tool calls out
 * of this run's counters.
 */
export function currentTurn(messages: Array<{ role: string }>): number {
  for (let index = messages.length - 1; index >= 0; index--) {
    if (messages[index].role === "user") return index + 1;
  }
  return 0;
}

/**
 * Observed wall-clock timing for one tool call.
 *
 * `start` is when THIS CLIENT first saw the call, not when the backend began
 * it — the wire does not carry a start timestamp, so a backend duration would
 * be invented. `observedRunning` records whether the call was ever seen
 * without a result; without that, a call restored from history has no honest
 * duration and must render none rather than a fabricated `0s`.
 */
export interface ToolTiming {
  start: number;
  end?: number;
  observedRunning: boolean;
}

export function openTiming(now: number, settled: boolean): ToolTiming {
  return settled
    ? { start: now, end: now, observedRunning: false }
    : { start: now, observedRunning: true };
}

export function settleTiming(timing: ToolTiming, now: number): ToolTiming {
  if (timing.end !== undefined) return timing;
  return { ...timing, end: now };
}

/**
 * `""` when there is no honest duration to show: never observed running, or
 * no clock reading supplied. An empty string renders nothing at all rather
 * than a misleading zero.
 */
export function formatDuration(timing: ToolTiming | undefined, now: number): string {
  if (!timing || !timing.observedRunning) return "";
  return formatElapsed((timing.end ?? now) - timing.start);
}

/** `14s` under a minute, `1m 05s` beyond. `""` for a missing/invalid reading. */
export function formatElapsed(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  const total = Math.floor(ms / 1000);
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

/**
 * Backend stream heartbeat cadence, in seconds.
 *
 * `alpha/config/stream_bridge_config.py::DEFAULT_HEARTBEAT_INTERVAL_SECONDS`:
 * the Gateway writes an SSE comment whenever no event has arrived for this
 * long, so a healthy connection keeps delivering bytes even while a tool call
 * runs for minutes. The value is operator-configurable
 * (`stream_bridge.heartbeat_interval_seconds`), which is why the notice below
 * states only what this client measured.
 */
export const HEARTBEAT_IDLE_SECONDS = 15;

/** Silence worth surfacing: two missed heartbeats, so one late frame never trips it. */
export const SILENCE_NOTICE_MS = HEARTBEAT_IDLE_SECONDS * 2 * 1000;

/**
 * Milliseconds since this client last received *any* byte of the run's stream,
 * or `null` when no reading exists.
 *
 * Deliberately measured on the byte stream rather than on parsed frames: the
 * backend's heartbeat comments are invisible to the decoder but are exactly
 * what separates "quiet tool call" from "connection gone". Client-observed —
 * this describes the connection, never the run.
 */
export function streamSilence(lastByteAt: number | null, now: number): number | null {
  if (lastByteAt === null || !Number.isFinite(lastByteAt) || !Number.isFinite(now)) return null;
  const delta = now - lastByteAt;
  return delta > 0 ? delta : 0;
}

/**
 * `null` while the stream is within two heartbeat intervals of its last byte;
 * silence that short is just a long tool call or a late frame.
 *
 * Past that, the notice names ONLY the measurement. It must never say the run
 * has stalled — a reconfigured heartbeat cadence, a stalled proxy, and a
 * genuinely wedged run all look identical from here, so claiming one of them
 * would be a fabricated diagnosis.
 */
export function silenceNotice(lastByteAt: number | null, now: number): string | null {
  const silence = streamSilence(lastByteAt, now);
  if (silence === null || silence < SILENCE_NOTICE_MS) return null;
  return `No update received for ${formatElapsed(silence)}`;
}

/**
 * Header summary for a group of tool calls: distinct human names, capped, so a
 * 20-call turn stays one line. Names come verbatim from the run; only
 * duplication and order are normalized.
 */
export function summarizeToolNames(calls: ToolCall[], max = 3): string {
  const names: string[] = [];
  for (const call of calls) {
    const name = typeof call.name === "string" ? call.name.trim() : "";
    if (!name || names.includes(name)) continue;
    names.push(name);
  }
  if (names.length === 0) return "";
  const shown = names.slice(0, max);
  const extra = names.length - shown.length;
  return extra > 0 ? `${shown.join(", ")} +${extra} more` : shown.join(", ");
}
