import type { ToolCall, ToolCallStatus, ToolCallVerdict } from "../types/chat";

export type SseFrame = { event: string; data: unknown; id?: string };
export type ReplayGapEvent = { type: "replay-gap"; runId?: string; lastEventId?: string; eventId?: string };
export type StreamMessage = { id: string; runId: string; content: string; toolCalls?: ToolCall[]; thinking?: string };

/**
 * Payload of an `event: error` frame, kept instead of discarded. The Gateway
 * sends a machine code and a human message; both are needed to tell a user
 * *why* a run failed, and the correlation id is what makes a failure
 * traceable in the logs.
 */
export type SseErrorDetail = { code?: string; message?: string; correlationId?: string; eventId?: string };

/** One resolved tool result, keyed by the tool call id it answers. */
type ToolResult = { eventId: string; status: ToolCallStatus; output: string };

/** Non-message stream channels the Gateway can emit. Latest frame wins. */
export const SSE_CHANNEL_EVENTS = ["custom", "updates", "debug", "tasks", "checkpoints"] as const;
export type SseChannelEvent = (typeof SSE_CHANNEL_EVENTS)[number];
export type SseChannelFrame = { event: SseChannelEvent; eventId?: string; data: unknown; truncated: boolean };

type ToolPart = { id?: string; index?: number; name?: string; args: string | Record<string, unknown> };
type Part = { id: string; content: string; snapshot: boolean; toolCalls: ToolPart[]; thinking: string };
type PartialMessage = StreamMessage & { parts: Part[]; firstEventId: string; visible: boolean };
export type SseState = {
  runId?: string;
  messages: Map<string, PartialMessage>;
  seen: Set<string>;
  pending: SseFrame[];
  lastEventId?: string;
  ended: boolean;
  failure?: "protocol" | "gap" | "server" | "interrupted";
  replayGap?: ReplayGapEvent;
  /** Why the run failed, as reported by `event: error`. Absent = not reported. */
  error?: SseErrorDetail;
  /**
   * Tool results, keyed by tool call id. Kept beside the messages rather than
   * inside them so a result arriving after its AI message is still attributed
   * to the right call, and so replaying a frame converges instead of appending.
   */
  toolResults: Map<string, ToolResult>;
  /** tool call id -> message key, so a late result re-projects only its own message. */
  toolOwners: Map<string, string>;
  /** Latest payload per non-message channel; bounded by the fixed key set. */
  channels: Partial<Record<SseChannelEvent, SseChannelFrame>>;
};

/* ── Wire contract for tool verdicts (see types/chat.ts ToolCallVerdict) ──── */

/** Backend stamp: `ToolResultMeta` in `tool_result_meta.py`. */
const TOOL_META_KEY = "agent_workspace_tool_meta";
/** Backend stamp: deterministic tool receipt in `tool_receipt.py`. */
const TOOL_RECEIPT_KEY = "agent_workspace_tool_receipt";
/** `subagents/status_contract.py` failure states, reported via additional_kwargs. */
const SUBAGENT_FAILURE_STATUSES = new Set(["failed", "cancelled", "timed_out", "polling_timed_out"]);
const VERDICT_TO_STATUS: Partial<Record<ToolCallVerdict, ToolCallStatus>> = {
  success: "completed",
  partial_success: "partial",
  error: "error",
  failed: "failed",
  unknown: "unknown",
};
const TOOL_MESSAGE_TYPES = new Set(["tool", "ToolMessage", "tool_message"]);
const CHANNEL_EVENTS: ReadonlySet<string> = new Set<string>(SSE_CHANNEL_EVENTS);

/* ── Bounds: nothing on the streaming path may grow without a ceiling ────── */

const MAX_TOOL_RESULTS = 2048;
const MAX_TOOL_OUTPUT_CHARS = 4000;
const MAX_CHANNEL_CHARS = 8000;
const MAX_ERROR_MESSAGE_CHARS = 1000;

export function createSseState(runId?: string): SseState {
  return { runId, messages: new Map(), seen: new Set(), pending: [], ended: false, toolResults: new Map(), toolOwners: new Map(), channels: {} };
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function identifier(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 && value.length <= 512 && !/[\r\n\0]/.test(value) ? value : undefined;
}

function compareIds(left: string, right: string): number {
  if (/^\d+(?:-\d+)?$/.test(left) && /^\d+(?:-\d+)?$/.test(right)) {
    const a = left.split("-").map(BigInt);
    const b = right.split("-").map(BigInt);
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      if ((a[i] || 0n) < (b[i] || 0n)) return -1;
      if ((a[i] || 0n) > (b[i] || 0n)) return 1;
    }
    return 0;
  }
  return left < right ? -1 : left > right ? 1 : 0;
}

function textContent(value: unknown): string {
  if (typeof value === "string") return value;
  if (!Array.isArray(value)) return "";
  return value.map((item) => {
    if (typeof item === "string") return item;
    const block = record(item);
    return ["text", "text_delta", "output_text"].includes(String(block.type)) && typeof block.text === "string" ? block.text : "";
  }).join("");
}

/** Clip an over-long payload and say so, so nothing reads as silently short. */
function truncate(value: string, max: number): string {
  return value.length <= max ? value : `${value.slice(0, max)} [+${value.length - max} chars truncated]`;
}

/** Keep a channel payload renderable without letting one frame grow the state. */
function boundedPayload(value: unknown, max: number): { data: unknown; truncated: boolean } {
  let text: string;
  try {
    text = JSON.stringify(value) ?? "";
  } catch {
    text = String(value);
  }
  if (text.length <= max) return { data: value, truncated: false };
  return { data: { truncated: true, bytes: text.length }, truncated: true };
}

function firstString(source: Record<string, unknown>, keys: string[], max?: number): string | undefined {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "string" && value.trim()) return max === undefined ? value.trim() : truncate(value.trim(), max);
  }
  return undefined;
}

/**
 * Everything the Gateway put in an `event: error` payload. Previously this frame
 * set `failure: "server"` and threw the body away, so a failed run was
 * indistinguishable from any other failed run in the UI.
 */
export function sseErrorDetail(data: unknown, eventId?: string): SseErrorDetail {
  const payload = record(data);
  const code = identifier(payload.code) ?? identifier(payload.error_code) ?? identifier(payload.errorCode);
  const message = firstString(payload, ["message", "detail", "error", "reason", "description"], MAX_ERROR_MESSAGE_CHARS);
  const correlationId = identifier(payload.correlation_id) ?? identifier(payload.correlationId)
    ?? identifier(payload.request_id) ?? identifier(payload.trace_id);
  const id = eventId ?? identifier(payload.event_id);
  return { ...(code ? { code } : {}), ...(message ? { message } : {}), ...(correlationId ? { correlationId } : {}), ...(id ? { eventId: id } : {}) };
}

function verdict(value: unknown): ToolCallStatus | undefined {
  return typeof value === "string" ? VERDICT_TO_STATUS[value.trim().toLowerCase() as ToolCallVerdict] : undefined;
}

/**
 * Resolve a tool result's real status without ever inventing "success".
 *
 * Precedence mirrors the backend's own `_honest_tool_status` in
 * `finish_first_verifier_middleware.py`: LangChain's own failure marker, then
 * the tool-meta stamp, then the tool-receipt stamp, then a structured subagent
 * failure, then the bare `ToolMessage.status` field. A status that is absent,
 * blank or unrecognized resolves to `"unknown"` — an unverified result is not a
 * passing one.
 */
export function honestToolStatus(message: Record<string, unknown>): ToolCallStatus {
  const additional = record(message.additional_kwargs);
  if (message.status === "error") return "error";
  for (const key of [TOOL_META_KEY, TOOL_RECEIPT_KEY]) {
    const stamped = verdict(record(additional[key]).status);
    if (stamped) return stamped;
  }
  const subagent = additional.subagent_status;
  if (typeof subagent === "string" && SUBAGENT_FAILURE_STATUSES.has(subagent.trim())) return "error";
  return verdict(message.status) ?? "unknown";
}

/**
 * A tool result attributable to exactly one tool call.
 *
 * Returns undefined — never a guess — when the message is not a tool result or
 * carries no `tool_call_id`, so a tool result that cannot be attributed is
 * skipped instead of being guessed onto some other call.
 */
function toolResultFrom(message: Record<string, unknown>): { id: string; status: ToolCallStatus; output: string } | undefined {
  if (!TOOL_MESSAGE_TYPES.has(String(message.type || message.role))) return undefined;
  const id = identifier(message.tool_call_id);
  if (!id) return undefined;
  return { id, status: honestToolStatus(message), output: truncate(textContent(message.content), MAX_TOOL_OUTPUT_CHARS) };
}

function withToolResult(state: SseState, result: { id: string; status: ToolCallStatus; output: string }, eventId: string): SseState {
  const previous = state.toolResults.get(result.id);
  // Replay and out-of-order delivery both converge on the highest event id.
  if (previous && compareIds(eventId, previous.eventId) <= 0) return state;
  if (!previous && state.toolResults.size >= MAX_TOOL_RESULTS) return state;
  const toolResults = new Map(state.toolResults);
  toolResults.set(result.id, { eventId, status: result.status, output: result.output });
  let next: SseState = { ...state, toolResults };
  // A result normally arrives AFTER the AI message that issued the call, so
  // re-project that one message from its stored parts. Touching only the owner
  // keeps the update O(1) per result and leaves every other message's identity
  // untouched, which is what keeps the streaming UI from flickering.
  const owner = state.toolOwners.get(result.id);
  if (owner === undefined) return next;
  const message = state.messages.get(owner);
  if (message) {
    const toolCalls = displayTools(project(message.parts).calls, toolResults);
    const messages = new Map(state.messages);
    messages.set(owner, { ...message, toolCalls });
    next = { ...next, messages };
  }
  return next;
}

/** Record which message owns each of its tool calls, so results can find it. */
function withToolOwners(state: SseState, key: string, calls: ToolPart[]): SseState {
  let owners: Map<string, string> | undefined;
  for (const call of calls) {
    if (!call.id || state.toolOwners.has(call.id) || owners?.has(call.id)) continue;
    if (state.toolOwners.size >= MAX_TOOL_RESULTS) break;
    owners = owners ?? new Map(state.toolOwners);
    owners.set(call.id, key);
  }
  return owners ? { ...state, toolOwners: owners } : state;
}

function withChannel(state: SseState, event: SseChannelEvent, data: unknown, eventId?: string): SseState {
  const bounded = boundedPayload(data, MAX_CHANNEL_CHARS);
  const frame: SseChannelFrame = { event, ...(eventId ? { eventId } : {}), data: bounded.data, truncated: bounded.truncated };
  return { ...state, channels: { ...state.channels, [event]: frame } };
}

function structuredContent(message: Record<string, unknown>): { toolCalls: ToolPart[]; thinking: string } {
  const blocks = Array.isArray(message.content) ? message.content.map(record) : [];
  const additional = record(message.additional_kwargs);
  const thinking = blocks.map((block) => {
    if (!["thinking", "thinking_delta", "reasoning"].includes(String(block.type))) return "";
    return typeof block.thinking === "string" ? block.thinking : typeof block.reasoning === "string" ? block.reasoning : typeof block.text === "string" ? block.text : "";
  }).join("") || (typeof additional.reasoning_content === "string" ? additional.reasoning_content : "");
  const calls = Array.isArray(message.tool_call_chunks) && message.tool_call_chunks.length ? message.tool_call_chunks
    : Array.isArray(message.tool_calls) && message.tool_calls.length ? message.tool_calls
      : Array.isArray(additional.tool_calls) && additional.tool_calls.length ? additional.tool_calls
        : blocks.filter((block) => block.type === "tool_use" || block.type === "tool_call");
  const toolCalls = calls.map((value): ToolPart => {
    const call = record(value);
    const fn = record(call.function);
    const args = call.args ?? call.input ?? fn.arguments;
    return {
      id: identifier(call.id),
      index: typeof call.index === "number" && Number.isSafeInteger(call.index) && call.index >= 0 ? call.index : undefined,
      name: typeof call.name === "string" ? call.name : typeof fn.name === "string" ? fn.name : undefined,
      args: typeof args === "string" ? args : record(args),
    };
  }).filter((call) => call.id !== undefined || call.index !== undefined);
  return { toolCalls, thinking };
}

function mergeTools(current: ToolPart[], incoming: ToolPart[]): ToolPart[] {
  const merged = current.map((call) => ({ ...call }));
  for (const call of incoming) {
    const previous = merged.find((item) => (call.id !== undefined && item.id === call.id)
      || (call.index !== undefined && item.index === call.index && (!call.id || !item.id || item.id === call.id)));
    if (!previous) {
      merged.push({ ...call });
      continue;
    }
    previous.id = call.id ?? previous.id;
    previous.index = call.index ?? previous.index;
    previous.name = call.name || previous.name;
    previous.args = typeof call.args === "string" && typeof previous.args === "string" ? previous.args + call.args : call.args;
  }
  return merged;
}

/**
 * Fold a message's accumulated parts into text/thinking/tool calls.
 *
 * Order-independent: parts are pre-sorted by event id, and a snapshot part
 * replaces rather than appends, so any delivery permutation converges.
 */
function project(parts: Part[]): { text: string; thinking: string; calls: ToolPart[] } {
  let text = "";
  let thinking = "";
  let calls: ToolPart[] = [];
  for (const part of parts) {
    text = part.snapshot ? part.content : text + part.content;
    thinking = part.snapshot ? part.thinking : thinking + part.thinking;
    calls = mergeTools(part.snapshot ? [] : calls, part.toolCalls);
  }
  return { text, thinking, calls };
}

function displayTools(calls: ToolPart[], results: Map<string, ToolResult>): ToolCall[] {
  return calls.filter((call) => call.id).map((call) => {
    let args = record(call.args);
    if (typeof call.args === "string") {
      try {
        args = record(JSON.parse(call.args));
      } catch {}
    }
    const result = call.id ? results.get(call.id) : undefined;
    // A call with no result yet carries no `status` key at all: the UI must be
    // able to tell "nothing reported" from "reported as completed".
    return {
      id: call.id!,
      name: call.name || "",
      args,
      ...(result ? { output: result.output, status: result.status } : {}),
    };
  });
}

export function reduceSse(state: SseState, frame: SseFrame): SseState {
  if (state.failure) return state;
  const data = record(frame.data);
  const runId = identifier(data.run_id) || state.runId;
  if (state.runId && runId !== state.runId) return { ...state, failure: "protocol" };
  if (frame.event === "metadata") {
    if (!runId) return { ...state, failure: "protocol" };
    let next: SseState = { ...state, runId, pending: [] as SseFrame[] };
    for (const pending of state.pending) next = reduceSse(next, pending);
    return next;
  }
  if (frame.event === "gap") return { ...state, runId, failure: "gap", replayGap: { type: "replay-gap", runId, lastEventId: state.lastEventId, eventId: identifier(frame.id) } };
  if (!runId) {
    if (state.pending.length >= 1024) return { ...state, failure: "protocol" };
    return { ...state, pending: [...state.pending, frame] };
  }
  const eventId = identifier(frame.id);
  const eventKey = eventId ? JSON.stringify([runId, eventId]) : undefined;
  if (eventKey && state.seen.has(eventKey)) return state;
  let next: SseState = {
    ...state,
    runId,
    seen: new Set(state.seen),
    messages: new Map(state.messages),
  };
  if (eventKey) next.seen.add(eventKey);
  if (eventId && (!state.lastEventId || compareIds(eventId, state.lastEventId) > 0)) next.lastEventId = eventId;
  if (frame.event === "error") return { ...next, failure: "server", error: sseErrorDetail(frame.data, eventId) };
  if (frame.event === "end") return { ...next, ended: true };
  if (frame.event.includes("|")) return next;
  // Sub-graph channels (`updates|child`) stay excluded by the check above;
  // only the root channel of each non-message mode is retained.
  if (CHANNEL_EVENTS.has(frame.event)) return withChannel(next, frame.event as SseChannelEvent, frame.data, eventId);
  if (frame.event === "values" && data.__interrupt__) return { ...next, failure: "interrupted" };
  let incoming: unknown[];
  let snapshot = false;
  if (frame.event === "messages" || frame.event === "messages-tuple") {
    if (!Array.isArray(frame.data) || frame.data.length !== 2) return { ...next, failure: "protocol" };
    const metadata = record(frame.data[1]);
    if (metadata.langgraph_checkpoint_ns || metadata.checkpoint_ns) return next;
    incoming = [frame.data[0]];
  } else if (frame.event === "values") {
    incoming = Array.isArray(data.messages) ? data.messages : [];
    snapshot = true;
  } else if (frame.event === "messages/partial" || frame.event === "messages/complete") {
    incoming = Array.isArray(frame.data) ? frame.data : [frame.data];
    snapshot = true;
  } else {
    return next;
  }
  for (const item of incoming) {
    const message = record(item);
    // A tool result is a fact ABOUT a tool call, not an assistant answer: it is
    // filed against its `tool_call_id` and never becomes a message of its own.
    const result = toolResultFrom(message);
    if (result) {
      // A result we cannot sequence cannot be attributed deterministically.
      if (!eventId) return { ...next, failure: "protocol" };
      next = withToolResult(next, result, eventId);
      continue;
    }
    if (!["ai", "AIMessageChunk", "AIMessage", "assistant"].includes(String(message.type || message.role))) continue;
    if (record(message.additional_kwargs).error_fallback) return { ...next, failure: "server" };
    const id = identifier(message.id);
    const content = textContent(message.content);
    const structured = structuredContent(message);
    if (!id || !eventId) {
      if (content || structured.thinking || structured.toolCalls.length) return { ...next, failure: "protocol" };
      continue;
    }
    const key = JSON.stringify([runId, id]);
    const previous = next.messages.get(key);
    if (!content && !structured.thinking && !structured.toolCalls.length && !previous) continue;
    const parts = [...(previous?.parts || []), { id: eventId, content, snapshot, ...structured }].sort((a, b) => compareIds(a.id, b.id));
    const { text, thinking, calls } = project(parts);
    const toolCalls = displayTools(calls, next.toolResults);
    next.messages.set(key, { id, runId, content: text, ...(thinking ? { thinking } : {}), ...(toolCalls.length ? { toolCalls } : {}), parts, firstEventId: parts[0].id, visible: previous?.visible || frame.event !== "values" });
    next = withToolOwners(next, key, calls);
  }
  return next;
}

export function streamMessages(state: SseState): StreamMessage[] {
  return [...state.messages.values()]
    .filter((message) => message.visible)
    .sort((a, b) => compareIds(a.firstEventId, b.firstEventId) || a.id.localeCompare(b.id))
    .map(({ id, runId, content, toolCalls, thinking }) => ({ id, runId, content, ...(toolCalls ? { toolCalls } : {}), ...(thinking ? { thinking } : {}) }));
}

export function runIdFromLocation(location: string | null | undefined, threadId: string): string | undefined {
  if (!location) return undefined;
  const match = location.match(/(?:^|\/)threads\/([^/?#]+)\/runs\/([^/?#]+)$/);
  if (!match) return undefined;
  try {
    return decodeURIComponent(match[1]) === threadId ? identifier(decodeURIComponent(match[2])) : undefined;
  } catch {
    return undefined;
  }
}

export function createSseDecoder(consume: (frame: SseFrame) => void, onRetry?: (delay: number) => void) {
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  let event = "message";
  let id: string | undefined;
  let data: string[] = [];
  let size = 0;
  const line = (value: string) => {
    size += value.length;
    if (size > 1048576) throw new Error("SSE frame exceeds the limit.");
    if (!value) {
      if (data.length) {
        const text = data.join("\n");
        let parsed: unknown;
        try {
          parsed = JSON.parse(text);
        } catch {
          throw new Error("Invalid SSE data.");
        }
        consume({ event, id, data: parsed });
      }
      event = "message";
      id = undefined;
      data = [];
      size = 0;
      return;
    }
    if (value.startsWith(":")) return;
    const colon = value.indexOf(":");
    const field = colon < 0 ? value : value.slice(0, colon);
    const content = colon < 0 ? "" : value.slice(colon + 1).replace(/^ /, "");
    if (field === "event") event = content;
    if (field === "id" && !content.includes("\0")) id = content;
    if (field === "data") data.push(content);
    if (field === "retry" && /^\d+$/.test(content)) onRetry?.(Math.min(Number(content), 30000));
  };
  const drain = (final: boolean) => {
    for (;;) {
      const index = buffer.search(/[\r\n]/);
      if (index < 0 || (!final && buffer[index] === "\r" && index === buffer.length - 1)) break;
      const length = buffer[index] === "\r" && buffer[index + 1] === "\n" ? 2 : 1;
      line(buffer.slice(0, index));
      buffer = buffer.slice(index + length);
    }
    if (buffer.length + size > 1048576) throw new Error("SSE frame exceeds the limit.");
  };
  return {
    push(chunk: Uint8Array) {
      buffer += decoder.decode(chunk, { stream: true });
      drain(false);
    },
    finish() {
      buffer += decoder.decode();
      drain(true);
      if (buffer || data.length) throw new Error("Incomplete SSE frame.");
    },
  };
}
