import type { ToolCall } from "../types/chat";

export type SseFrame = { event: string; data: unknown; id?: string };
export type ReplayGapEvent = { type: "replay-gap"; runId?: string; lastEventId?: string; eventId?: string };
export type StreamMessage = { id: string; runId: string; content: string; toolCalls?: ToolCall[]; thinking?: string };
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
};

export function createSseState(runId?: string): SseState {
  return { runId, messages: new Map(), seen: new Set(), pending: [], ended: false };
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

function displayTools(calls: ToolPart[]): ToolCall[] {
  return calls.filter((call) => call.id).map((call) => {
    let args = record(call.args);
    if (typeof call.args === "string") {
      try {
        args = record(JSON.parse(call.args));
      } catch {}
    }
    return { id: call.id!, name: call.name || "", args };
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
  const next: SseState = {
    ...state,
    runId,
    seen: new Set(state.seen),
    messages: new Map(state.messages),
  };
  if (eventKey) next.seen.add(eventKey);
  if (eventId && (!state.lastEventId || compareIds(eventId, state.lastEventId) > 0)) next.lastEventId = eventId;
  if (frame.event === "error") return { ...next, failure: "server" };
  if (frame.event === "end") return { ...next, ended: true };
  if (frame.event.includes("|")) return next;
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
    let text = "";
    let thinking = "";
    let calls: ToolPart[] = [];
    for (const part of parts) {
      text = part.snapshot ? part.content : text + part.content;
      thinking = part.snapshot ? part.thinking : thinking + part.thinking;
      calls = mergeTools(part.snapshot ? [] : calls, part.toolCalls);
    }
    const toolCalls = displayTools(calls);
    next.messages.set(key, { id, runId, content: text, ...(thinking ? { thinking } : {}), ...(toolCalls.length ? { toolCalls } : {}), parts, firstEventId: parts[0].id, visible: previous?.visible || frame.event !== "values" });
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
