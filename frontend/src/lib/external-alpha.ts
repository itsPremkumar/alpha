// Typed client for the External Alpha transcript surface.
//
// This reads the *conversation history* of the cross-installation plane: the
// remote envelopes from this installation's peer store, and the local Agent
// turns those envelopes produced. It is deliberately a separate client from
// `peer-network.ts`, which owns pairing and delivery control -- this one is
// read-mostly and must not grow into a second control surface.
//
// Honesty rules this client enforces, mirroring the backend projection:
//   * Every field is coerced. A field the server omitted becomes `null`, never
//     an empty string that would render as a fact.
//   * A transcript entry keeps its `role`, so the UI can render "from peer Alpha"
//     and "this Alpha replied" distinctly. Merging them would let a reader
//     believe a remote peer said something the local Agent said.
//   * `truncated` / `events_truncated` are surfaced, not swallowed. A bounded
//     response that renders as complete is the failure mode this tab exists to
//     avoid.
//   * No raw `fetch`: everything goes through `get`/`send` from `./http`, which
//     is what `peer-network.test.mjs` pins with a throwing `fetch`.
import { apiFetch } from "./api-client";
import { get, send, asList, errMsg, pick } from "./http";

export type TranscriptRole = "peer_message" | "local_reply" | "local_event";

export interface TranscriptPeerSummary {
  agent_id: string | null;
  name: string | null;
  description: string | null;
  version: string | null;
  capabilities: string[];
  skills: unknown[];
  trust: string | null;
  source: string | null;
  first_seen: string | null;
  last_seen: string | null;
  paired_at: string | null;
  auto_reply: boolean;
}

export interface TranscriptDelivery {
  recipient_id: string | null;
  status: string | null;
  transport: string | null;
  error: string | null;
  delivered_at: string | null;
  read_at: string | null;
}

export interface TranscriptToolCall {
  id: string | null;
  name: string | null;
  args: Record<string, unknown>;
  status: string | null;
}

export interface TranscriptEntry {
  entry_id: string;
  role: TranscriptRole;
  /** Which installation authored it: a peer, or this one. */
  side: "peer" | "local";
  sender_id: string | null;
  recipients: string[];
  kind: string;
  text: string;
  payload: Record<string, unknown>;
  status: string | null;
  created_at: string | null;
  delivered_at: string | null;
  read_at: string | null;
  delivery_error: string | null;
  deliveries: TranscriptDelivery[];
  tool_calls?: TranscriptToolCall[];
  caller?: string | null;
  latency_ms?: number | null;
  usage?: Record<string, unknown> | null;
  thread_id?: string | null;
  run_id?: string | null;
}

export interface TranscriptTurnDetail {
  run_id: string | null;
  thread_id: string | null;
  status: string | null;
  created_at: string | null;
  updated_at: string | null;
  error: string | null;
  stop_reason: string | null;
  model_name: string | null;
  peer_message_id: string | null;
  peer_agent_id: string | null;
  kind: string | null;
  message_count: number;
  llm_call_count: number;
  token_usage_by_model: Record<string, Record<string, number>>;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  events: TranscriptEntry[];
  /** True when `events` is a subset of what was stored. Never assume completeness. */
  events_truncated: boolean;
  events_shown: number;
  events_total: number;
}

export interface TranscriptIndexEntry {
  conversation_id: string;
  title: string;
  mode: string;
  status: string;
  participants: string[];
  created_at: string | null;
  updated_at: string | null;
  peer: TranscriptPeerSummary;
  counts: { messages: number; turns: number };
}

export interface TranscriptIndex {
  transcripts: TranscriptIndexEntry[];
  count: number;
  enabled: boolean;
}

export interface Transcript {
  conversation_id: string;
  conversation: Record<string, unknown>;
  peer: TranscriptPeerSummary;
  entries: TranscriptEntry[];
  count: number;
  truncated: boolean;
  counts: { messages: number; turns: number };
}

export interface PeerStreamEvent {
  type: string;
  at: string;
  seq?: number;
  data: Record<string, unknown>;
}

function str(record: Record<string, unknown>, key: string, fallback = ""): string {
  const value = record[key];
  return typeof value === "string" ? value : fallback;
}

function nullableStr(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === "string" ? value : null;
}

function num(record: Record<string, unknown>, key: string): number {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function bool(record: Record<string, unknown>, key: string): boolean {
  return record[key] === true;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function strings(value: unknown): string[] {
  return asList({ items: value }, ["items"]).map((item) => String(item));
}

const ROLES: TranscriptRole[] = ["peer_message", "local_reply", "local_event"];

function toRole(value: unknown): TranscriptRole {
  // An unrecognised role must not be guessed into a speech role. Falling back to
  // `local_event` keeps it out of the "who spoke" column entirely.
  return ROLES.includes(value as TranscriptRole) ? (value as TranscriptRole) : "local_event";
}

function toPeerSummary(raw: unknown): TranscriptPeerSummary {
  const source = record(raw);
  return {
    agent_id: nullableStr(source, "agent_id"),
    name: nullableStr(source, "name"),
    description: nullableStr(source, "description"),
    version: nullableStr(source, "version"),
    capabilities: strings(source.capabilities),
    skills: Array.isArray(source.skills) ? source.skills : [],
    trust: nullableStr(source, "trust"),
    source: nullableStr(source, "source"),
    first_seen: nullableStr(source, "first_seen"),
    last_seen: nullableStr(source, "last_seen"),
    paired_at: nullableStr(source, "paired_at"),
    auto_reply: bool(source, "auto_reply"),
  };
}

function toDelivery(raw: unknown): TranscriptDelivery {
  const source = record(raw);
  return {
    recipient_id: nullableStr(source, "recipient_id"),
    status: nullableStr(source, "status"),
    transport: nullableStr(source, "transport"),
    error: nullableStr(source, "error"),
    delivered_at: nullableStr(source, "delivered_at"),
    read_at: nullableStr(source, "read_at"),
  };
}

function toToolCall(raw: unknown): TranscriptToolCall {
  const source = record(raw);
  return {
    id: nullableStr(source, "id"),
    name: nullableStr(source, "name"),
    args: record(source.args),
    status: nullableStr(source, "status"),
  };
}

function toEntry(raw: unknown): TranscriptEntry {
  const source = record(raw);
  const rawSide = str(source, "side");
  return {
    entry_id: str(source, "entry_id"),
    role: toRole(source.role),
    side: rawSide === "peer" ? "peer" : "local",
    sender_id: nullableStr(source, "sender_id"),
    recipients: strings(source.recipients),
    kind: str(source, "kind", "message"),
    // Remote text arrives here and MUST be rendered escaped. It is untrusted
    // data from another installation, never instruction.
    text: str(source, "text"),
    payload: record(source.payload),
    status: nullableStr(source, "status"),
    created_at: nullableStr(source, "created_at"),
    delivered_at: nullableStr(source, "delivered_at"),
    read_at: nullableStr(source, "read_at"),
    delivery_error: nullableStr(source, "delivery_error"),
    deliveries: asList({ items: source.deliveries }, ["items"]).map(toDelivery),
    tool_calls: asList({ items: source.tool_calls }, ["items"]).map(toToolCall),
    caller: nullableStr(source, "caller"),
    latency_ms: typeof source.latency_ms === "number" ? source.latency_ms : null,
    usage: source.usage && typeof source.usage === "object" ? record(source.usage) : null,
    thread_id: nullableStr(source, "thread_id"),
    run_id: nullableStr(source, "run_id"),
  };
}

function toTurnDetail(raw: unknown): TranscriptTurnDetail {
  const source = record(raw);
  const usageByModel: Record<string, Record<string, number>> = {};
  for (const [model, counts] of Object.entries(record(source.token_usage_by_model))) {
    const bucket = record(counts);
    usageByModel[model] = {
      input_tokens: num(bucket, "input_tokens"),
      output_tokens: num(bucket, "output_tokens"),
      total_tokens: num(bucket, "total_tokens"),
    };
  }
  return {
    run_id: nullableStr(source, "run_id"),
    thread_id: nullableStr(source, "thread_id"),
    status: nullableStr(source, "status"),
    created_at: nullableStr(source, "created_at"),
    updated_at: nullableStr(source, "updated_at"),
    error: nullableStr(source, "error"),
    stop_reason: nullableStr(source, "stop_reason"),
    model_name: nullableStr(source, "model_name"),
    peer_message_id: nullableStr(source, "peer_message_id"),
    peer_agent_id: nullableStr(source, "peer_agent_id"),
    kind: nullableStr(source, "kind"),
    message_count: num(source, "message_count"),
    llm_call_count: num(source, "llm_call_count"),
    token_usage_by_model: usageByModel,
    total_input_tokens: num(source, "total_input_tokens"),
    total_output_tokens: num(source, "total_output_tokens"),
    total_tokens: num(source, "total_tokens"),
    events: asList({ items: source.events }, ["items"]).map(toEntry),
    events_truncated: source.events_truncated === true,
    events_shown: num(source, "events_shown"),
    events_total: num(source, "events_total"),
  };
}

function toIndexEntry(raw: unknown): TranscriptIndexEntry {
  const source = record(raw);
  const counts = record(source.counts);
  return {
    conversation_id: str(source, "conversation_id"),
    title: str(source, "title", "Untitled peer conversation"),
    mode: str(source, "mode", "unknown"),
    status: str(source, "status", "unknown"),
    participants: strings(source.participants),
    created_at: nullableStr(source, "created_at"),
    updated_at: nullableStr(source, "updated_at"),
    peer: toPeerSummary(source.peer),
    counts: { messages: num(counts, "messages"), turns: num(counts, "turns") },
  };
}

export interface TranscriptSearchResult {
  query: string;
  entries: TranscriptEntry[];
  count: number;
  /**
   * Whether the Gateway used FTS5 ranking. When false it fell back to a bounded
   * substring scan, which finds fewer things — so the UI says "substring
   * match" rather than implying ranked full-text search either way.
   */
  fts_available: boolean;
  empty_query: boolean;
}

export interface TranscriptAnalytics {
  totals: Record<string, number>;
  conversations: number;
  modes: Record<string, number>;
  kinds: Record<string, number>;
  directions: Record<string, number>;
  statuses: Record<string, number>;
  fts_available: boolean;
  retention_days: number;
}

export interface TraceEnvelope {
  schema_version?: number;
  event_id?: string | null;
  seq?: number | null;
  event_type?: string | null;
  trace_id?: string | null;
  agent_name?: string | null;
  agent_depth?: number | null;
  node?: string | null;
  layer?: number | null;
  severity?: string | null;
  ts_wall?: string | null;
  payload_bytes?: number | null;
  payload_sha256?: string | null;
  payload?: Record<string, unknown>;
}

export interface TurnTrace {
  conversation_id: string;
  run_id: string;
  traces: TraceEnvelope[];
  count: number;
  scanned: number;
  has_more: boolean;
  after_seq: number | null;
  note: string;
}

export async function searchTranscripts(options: {
  q: string;
  limit?: number;
  conversation_id?: string | null;
  direction?: "inbound" | "outbound" | null;
}): Promise<TranscriptSearchResult> {
  const query = new URLSearchParams();
  // An empty q is sent rather than short-circuited, so the Gateway stays the
  // authority on "no query means no results".
  query.set("q", options.q);
  query.set("limit", String(Math.max(1, Math.min(options.limit ?? 50, 200))));
  if (options.conversation_id) query.set("conversation_id", options.conversation_id);
  if (options.direction === "inbound" || options.direction === "outbound") query.set("direction", options.direction);
  const body = await get<Record<string, unknown>>(`/peer-network/transcripts/search?${query.toString()}`);
  return {
    query: str(body, "query", options.q),
    entries: asList(body, ["entries"]).map(toEntry),
    count: num(body, "count"),
    fts_available: body.fts_available === true,
    empty_query: body.empty_query === true,
  };
}

export async function getTranscriptAnalytics(): Promise<TranscriptAnalytics> {
  const body = await get<Record<string, unknown>>("/peer-network/transcripts/analytics");
  return {
    totals: Object.fromEntries(
      Object.entries(record(body.totals)).flatMap(([key, value]) =>
        typeof value === "number" && Number.isFinite(value) ? [[key, value] as [string, number]] : [],
      ),
    ),
    conversations: num(body, "conversations"),
    modes: countMap(body.modes),
    kinds: countMap(body.kinds),
    directions: countMap(body.directions),
    statuses: countMap(body.statuses),
    fts_available: body.fts_available === true,
    retention_days: num(body, "retention_days"),
  };
}

export async function getTurnTrace(conversationId: string, runId: string): Promise<TurnTrace> {
  const body = await get<Record<string, unknown>>(
    `/peer-network/transcripts/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(runId)}/trace`,
  );
  return {
    conversation_id: str(body, "conversation_id", conversationId),
    run_id: str(body, "run_id", runId),
    traces: asList(body, ["traces"]).map((raw) => record(raw)),
    count: num(body, "count"),
    scanned: num(body, "scanned"),
    has_more: body.has_more === true,
    after_seq: typeof body.after_seq === "number" ? body.after_seq : null,
    note: str(body, "note"),
  };
}

/** Coerce a `{key: count}` histogram; a non-object becomes an empty map, never `{}`-vs-missing ambiguity. */
function countMap(value: unknown): Record<string, number> {
  return Object.fromEntries(
    Object.entries(record(value)).flatMap(([key, count]) =>
      typeof count === "number" && Number.isFinite(count) ? [[key, count] as [string, number]] : [],
    ),
  );
}

export async function listTranscripts(options: { limit?: number } = {}): Promise<TranscriptIndex> {
  const limit = Math.max(1, Math.min(options.limit ?? 50, 200));
  const body = await get<Record<string, unknown>>(`/peer-network/transcripts?limit=${limit}`);
  return {
    transcripts: asList(body, ["transcripts"]).map(toIndexEntry),
    count: num(body, "count"),
    // `enabled: false` means the plane is OFF, which is not the same as "no
    // traffic". The UI must be able to say which one it is.
    enabled: body.enabled === true,
  };
}

export async function getTranscript(conversationId: string, options: { limit?: number } = {}): Promise<Transcript> {
  const limit = Math.max(1, Math.min(options.limit ?? 1000, 1000));
  const body = await get<Record<string, unknown>>(
    `/peer-network/transcripts/${encodeURIComponent(conversationId)}?limit=${limit}`,
  );
  const counts = record(body.counts);
  return {
    conversation_id: str(body, "conversation_id", conversationId),
    conversation: record(body.conversation),
    peer: toPeerSummary(body.peer),
    entries: asList(body, ["entries"]).map(toEntry),
    count: num(body, "count"),
    truncated: body.truncated === true,
    counts: { messages: num(counts, "messages"), turns: num(counts, "turns") },
  };
}

export async function getTurnForensics(conversationId: string, runId: string): Promise<TranscriptTurnDetail> {
  const body = await get<Record<string, unknown>>(
    `/peer-network/transcripts/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(runId)}`,
  );
  return toTurnDetail(body.turn);
}

export async function exportTranscript(conversationId: string): Promise<Record<string, unknown>> {
  const body = await get<Record<string, unknown>>(
    `/peer-network/transcripts/${encodeURIComponent(conversationId)}/export`,
  );
  return { ...body, entries: asList(body, ["entries"]).map(toEntry) };
}

/**
 * Subscribe to the peer-network event stream.
 *
 * `EventSource` is deliberately not used: it cannot carry this API's cookie
 * auth plus the `Last-Event-ID` reconnect cursor reliably across browsers, and
 * the repo pins a no-raw-transport rule for frontend streams. The returned
 * unsubscribe MUST be called on unmount -- a leaked reader keeps a Gateway
 * connection open and the server-side queue will start dropping events.
 */
export function subscribePeerEvents(
  onEvent: (event: PeerStreamEvent) => void,
  onGap?: (event: PeerStreamEvent) => void,
): () => void {
  let closed = false;
  const controller = new AbortController();

  void (async () => {
    try {
      // `apiFetch`, not raw `fetch`: it supplies cookie auth, base-URL
      // normalization and the shared error shape, exactly as the chat stream
      // does. `get`/`send` are unusable here because they buffer a JSON body.
      const response = await apiFetch("/peer-network/events", { method: "GET", signal: controller.signal });
      if (!response.body) return;
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!closed) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // SSE frames are separated by a blank line. A partial frame stays in the
        // buffer rather than being parsed as a complete event.
        let split = buffer.indexOf("\n\n");
        while (split !== -1) {
          const frame = buffer.slice(0, split);
          buffer = buffer.slice(split + 2);
          const parsed = parseSseFrame(frame);
          if (parsed) {
            if (parsed.type === "stream.overflow" || parsed.type === "stream.reset") {
              onGap?.(parsed);
            } else {
              onEvent(parsed);
            }
          }
          split = buffer.indexOf("\n\n");
        }
      }
    } catch (cause) {
      if (!closed) {
        onEvent({ type: "stream.error", at: "", data: { detail: errMsg(cause) } });
      }
    }
  })();

  return () => {
    closed = true;
    controller.abort();
  };
}

function parseSseFrame(frame: string): PeerStreamEvent | null {
  let id: number | undefined;
  let type = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) continue; // keepalive comment
    if (line.startsWith("id:")) {
      const parsed = Number(line.slice(3).trim());
      if (Number.isFinite(parsed)) id = parsed;
    } else if (line.startsWith("event:")) {
      type = line.slice(6).trim() || "message";
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (!dataLines.length) return null;
  try {
    const parsed = record(JSON.parse(dataLines.join("\n")));
    return {
      type: str(parsed, "type", type),
      at: str(parsed, "at"),
      seq: typeof parsed.seq === "number" ? parsed.seq : id,
      data: record(parsed.data),
    };
  } catch {
    return null;
  }
}

/** Pick a human label for a transcript role without inventing a speaker. */
export function transcriptRoleLabel(role: TranscriptRole): string {
  if (role === "peer_message") return "From peer Alpha";
  if (role === "local_reply") return "This Alpha replied";
  return "Local Agent activity";
}

export const PEER_TRANSCRIPT_EVENT_TYPES = [
  "message.outbound",
  "message.inbound",
  "message.read",
  "peer.paired",
  "peer.trust_changed",
  "pairing.rotated",
  "conversation.created",
  "message.turn_started",
  "message.turn_failed",
  "message.turn_skipped",
  "discovery.completed",
] as const;

export function isTranscriptEvent(type: string): boolean {
  return (PEER_TRANSCRIPT_EVENT_TYPES as readonly string[]).includes(type);
}

export { pick };