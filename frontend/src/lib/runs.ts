import { get, send, asList, pick } from "./http";

/** Per-model token split persisted on a run (absent when never reported). */
export interface RunModelUsage {
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  cache_read_tokens: number | null;
}

export interface RunInfo {
  run_id: string;
  thread_id: string;
  status: string;
  assistant_id: string;
  model: string;
  created_at: string;
  updated_at: string;
  error: string | null;
  stop_reason: string | null;
  total_input_tokens: number | null;
  total_output_tokens: number | null;
  total_tokens: number | null;
  llm_call_count: number | null;
  lead_agent_tokens: number | null;
  subagent_tokens: number | null;
  middleware_tokens: number | null;
  message_count: number | null;
  token_usage_by_model: Record<string, RunModelUsage> | null;
}

/** A count is a real number or the honest unknown — never a coerced 0. */
function count(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function toModelUsage(raw: unknown): RunModelUsage {
  const rec = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  return {
    input_tokens: count(rec.input_tokens),
    output_tokens: count(rec.output_tokens),
    total_tokens: count(rec.total_tokens),
    cache_read_tokens: count(rec.cache_read_tokens),
  };
}

function toRun(r: Record<string, unknown>): RunInfo {
  const rawSplit = r.token_usage_by_model;
  const split =
    rawSplit && typeof rawSplit === "object" && !Array.isArray(rawSplit)
      ? Object.fromEntries(
          Object.entries(rawSplit as Record<string, unknown>).map(([model, usage]) => [model, toModelUsage(usage)])
        )
      : null;
  return {
    run_id: String(pick(r, ["run_id", "id"], "")),
    thread_id: String(pick(r, ["thread_id"], "")),
    status: String(pick(r, ["status"], "unknown")),
    assistant_id: String(pick(r, ["assistant_id", "assistant"], "")),
    model: String(pick(r, ["model", "model_name"], "—")),
    created_at: String(pick(r, ["created_at"], "")),
    updated_at: String(pick(r, ["updated_at"], "")),
    error: (r.error as string | null) ?? null,
    stop_reason: (r.stop_reason as string | null) ?? null,
    total_input_tokens: count(r.total_input_tokens),
    total_output_tokens: count(r.total_output_tokens),
    total_tokens: count(r.total_tokens),
    llm_call_count: count(r.llm_call_count),
    lead_agent_tokens: count(r.lead_agent_tokens),
    subagent_tokens: count(r.subagent_tokens),
    middleware_tokens: count(r.middleware_tokens),
    message_count: count(r.message_count),
    token_usage_by_model: split && Object.keys(split).length > 0 ? split : null,
  };
}

export async function listThreadRuns(threadId: string): Promise<RunInfo[]> {
  const d = await get<unknown>(`/threads/${encodeURIComponent(threadId)}/runs`);
  return asList(d, ["runs", "data"]).map((r) => toRun(r));
}

export async function fetchRun(threadId: string, runId: string): Promise<RunInfo> {
  const d = await get<Record<string, unknown>>(
    `/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}`
  );
  return toRun(d);
}

export async function cancelRun(threadId: string, runId: string): Promise<void> {
  await send(`/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/cancel`, "POST", {});
}

export async function fetchRunMessages(
  threadId: string,
  runId: string
): Promise<Array<Record<string, unknown>>> {
  const d = await get<unknown>(
    `/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/messages`
  );
  return asList(d, ["messages", "data"]);
}

// ---------------------------------------------------------------------------
// Run event stream (the REST cursor surface behind run replay)
// ---------------------------------------------------------------------------

export interface RunEvent {
  /** Store-assigned, thread-global and strictly increasing. */
  seq: number | null;
  event_type: string;
  category: string;
  content: unknown;
  metadata: Record<string, unknown>;
  created_at: string | null;
  task_id: string | null;
}

/** Page size for one run-events request. The server accepts 1..2000. */
export const RUN_EVENT_PAGE_LIMIT = 500;
/** Hard cap on one cursor walk, so a huge run cannot pull an unbounded stream. */
export const RUN_EVENT_MAX_EVENTS = 4000;
/** Page cap for the same walk; exceeding either reports `complete: false`. */
export const RUN_EVENT_MAX_PAGES = 8;

export interface RunEventsQuery {
  /** Forward cursor: the store returns the first page with `seq > afterSeq`. */
  afterSeq?: number | null;
  /** `event_types` filter — one CSV query param, the server's own filter. */
  eventTypes?: readonly string[] | null;
  taskId?: string | null;
  limit?: number;
  maxEvents?: number;
}

export interface RunEventsPage {
  events: RunEvent[];
  /** False when the bounded walk stopped before the end of the run's stream. */
  complete: boolean;
}

function toRunEvent(raw: Record<string, unknown>): RunEvent {
  const metadata =
    raw.metadata && typeof raw.metadata === "object" && !Array.isArray(raw.metadata)
      ? (raw.metadata as Record<string, unknown>)
      : {};
  return {
    seq: count(raw.seq),
    event_type: text(raw.event_type, "unknown"),
    category: text(raw.category, "unknown"),
    content: raw.content,
    metadata,
    created_at: typeof raw.created_at === "string" ? raw.created_at : null,
    task_id: typeof metadata.task_id === "string" && metadata.task_id ? metadata.task_id : null,
  };
}

/** One page of a run's events. Kept for callers that only need a flat list. */
export async function fetchRunEvents(threadId: string, runId: string, query: RunEventsQuery = {}): Promise<RunEvent[]> {
  const page = await fetchRunEventsPage(threadId, runId, query);
  return page.events;
}

/**
 * Walk a run's event stream with the server's `after_seq` cursor.
 *
 * The endpoint returns a bare array with no total, so a short page ends the
 * walk. Any other stop (event cap, page cap, a page without a usable `seq`)
 * reports `complete: false` — a partial stream is never presented as the run's
 * whole history.
 */
export async function fetchRunEventsPage(threadId: string, runId: string, query: RunEventsQuery = {}): Promise<RunEventsPage> {
  const limit = Math.min(Math.max(Math.trunc(count(query.limit) ?? RUN_EVENT_PAGE_LIMIT), 1), 2000);
  const maxEvents = Math.max(limit, Math.trunc(count(query.maxEvents) ?? RUN_EVENT_MAX_EVENTS));
  const types = query.eventTypes && query.eventTypes.length > 0 ? [...query.eventTypes] : null;
  const taskId = query.taskId ? String(query.taskId) : null;
  const base = `/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/events`;

  const events: RunEvent[] = [];
  let afterSeq: number | null = count(query.afterSeq);

  for (let page = 0; page < RUN_EVENT_MAX_PAGES; page++) {
    const params: string[] = [`limit=${limit}`];
    if (types) params.push(`event_types=${types.map((t) => encodeURIComponent(t)).join(",")}`);
    if (taskId) params.push(`task_id=${encodeURIComponent(taskId)}`);
    if (afterSeq !== null) params.push(`after_seq=${afterSeq}`);
    const d = await get<unknown>(`${base}?${params.join("&")}`);
    const rows = asList(d, ["events", "data"]).map((r) => toRunEvent(r));
    events.push(...rows);
    if (rows.length < limit) return { events, complete: true };
    const lastSeq = rows[rows.length - 1]?.seq ?? null;
    if (lastSeq === null || (afterSeq !== null && lastSeq <= afterSeq)) return { events, complete: false };
    if (events.length >= maxEvents) return { events, complete: false };
    afterSeq = lastSeq;
  }
  return { events, complete: false };
}

// ---------------------------------------------------------------------------
// Severity — the "errors only" filter
// ---------------------------------------------------------------------------

/**
 * Event types the server can narrow to for an errors-only read. These are the
 * only persisted types that carry failure evidence (see the run event stream
 * contract): a root-chain error, an LLM callback error, a terminal subagent
 * status, and the two middleware audits that record a stop.
 */
export const RUN_ISSUE_EVENT_TYPES: readonly string[] = [
  "run.error",
  "llm.error",
  "subagent.end",
  "middleware:safety_termination",
  "middleware:loop_detection",
];

/** Terminal subagent statuses that are failures rather than clean finishes. */
export const RUN_FAILED_SUBAGENT_STATUSES: readonly string[] = ["failed", "timed_out"];

export type RunEventSeverity = "error" | "warn" | "info";

function eventField(event: RunEvent, field: string): unknown {
  const content = event.content;
  if (!content || typeof content !== "object" || Array.isArray(content)) return undefined;
  return (content as Record<string, unknown>)[field];
}

/**
 * Classify one run event for the filter.
 *
 * `error` — root or LLM failure, a failed/timed-out subagent, a hard stop.
 * `warn` — a recorded intervention (safety suppression, loop warning, a
 * cancelled subagent) that is not a failure.
 * `info` — everything else, including a completed subagent.
 */
export function runEventSeverity(event: RunEvent): RunEventSeverity {
  switch (event.event_type) {
    case "run.error":
    case "llm.error":
      return "error";
    case "subagent.end": {
      const status = eventField(event, "status");
      if (typeof status === "string" && RUN_FAILED_SUBAGENT_STATUSES.includes(status)) return "error";
      if (status === "cancelled") return "warn";
      return "info";
    }
    case "middleware:safety_termination":
      return "warn";
    case "middleware:loop_detection":
      return eventField(event, "action") === "hard_stop" ? "error" : "warn";
    default:
      return "info";
  }
}

/** True when the event is failure evidence (the "errors only" predicate). */
export function isRunIssueEvent(event: RunEvent): boolean {
  return runEventSeverity(event) === "error";
}

/** A bounded, human-readable one-liner for one event row. */
export function summarizeRunEvent(event: RunEvent, maxChars = 240): string {
  const raw = event.content;
  let text = "";
  if (typeof raw === "string") {
    text = raw;
  } else if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const rec = raw as Record<string, unknown>;
    if (typeof rec.text === "string") text = rec.text;
    else if (typeof rec.content === "string") text = rec.content;
    else if (typeof rec.error === "string") text = rec.error;
    else if (typeof rec.action === "string") text = rec.action;
    else if (Array.isArray(rec.tool_calls) && rec.tool_calls.length > 0) {
      const names = rec.tool_calls
        .map((c) => (c && typeof c === "object" ? String((c as Record<string, unknown>).name ?? "") : ""))
        .filter(Boolean);
      text = names.length > 0 ? `tool call: ${names.join(", ")}` : "tool call";
    } else {
      text = JSON.stringify(rec);
    }
  }
  const oneLine = text.replace(/\s+/g, " ").trim();
  return oneLine.length > maxChars ? `${oneLine.slice(0, maxChars)}…` : oneLine;
}

// ---------------------------------------------------------------------------
// Per-run token usage / cost
// ---------------------------------------------------------------------------

export interface RunUsageModelRow {
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  cache_read_tokens: number | null;
  cost: number | null;
}

/**
 * One row of per-call usage. `llm_response` is a single model call; `subagent`
 * is a delegated execution's cumulative usage snapshot (subagents run outside
 * the parent journal's callback boundary, so they are reported per execution).
 */
export interface RunUsageCallRow {
  seq: number | null;
  call_index: number | null;
  source: "llm_response" | "subagent";
  caller: string | null;
  task_id: string | null;
  model: string | null;
  status: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  cache_read_tokens: number | null;
  latency_ms: number | null;
  cost: number | null;
  created_at: string | null;
}

export type RunUsageModelSource = "per_model" | "run_totals" | "unavailable";

export interface RunUsage {
  run_id: string;
  thread_id: string;
  model: string | null;
  status: string;
  total_input_tokens: number | null;
  total_output_tokens: number | null;
  total_tokens: number | null;
  llm_call_count: number | null;
  lead_agent_tokens: number | null;
  subagent_tokens: number | null;
  middleware_tokens: number | null;
  by_model: RunUsageModelRow[];
  by_model_source: RunUsageModelSource;
  calls: RunUsageCallRow[];
  /** False when the bounded server-side walk stopped before the run's end. */
  calls_complete: boolean;
  total_cost: number | null;
  currency: string | null;
  pricing_configured: boolean;
}

function toUsageModelRow(raw: unknown): RunUsageModelRow {
  const rec = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  return {
    model: typeof rec.model === "string" && rec.model ? rec.model : null,
    input_tokens: count(rec.input_tokens),
    output_tokens: count(rec.output_tokens),
    total_tokens: count(rec.total_tokens),
    cache_read_tokens: count(rec.cache_read_tokens),
    cost: count(rec.cost),
  };
}

function toUsageCallRow(raw: unknown): RunUsageCallRow {
  const rec = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  return {
    seq: count(rec.seq),
    call_index: count(rec.call_index),
    source: rec.source === "subagent" ? "subagent" : "llm_response",
    caller: typeof rec.caller === "string" && rec.caller ? rec.caller : null,
    task_id: typeof rec.task_id === "string" && rec.task_id ? rec.task_id : null,
    model: typeof rec.model === "string" && rec.model ? rec.model : null,
    status: typeof rec.status === "string" && rec.status ? rec.status : null,
    input_tokens: count(rec.input_tokens),
    output_tokens: count(rec.output_tokens),
    total_tokens: count(rec.total_tokens),
    cache_read_tokens: count(rec.cache_read_tokens),
    latency_ms: count(rec.latency_ms),
    cost: count(rec.cost),
    created_at: typeof rec.created_at === "string" && rec.created_at ? rec.created_at : null,
  };
}

/** Per-run, per-model and per-model-call usage with estimated cost. */
export async function fetchRunUsage(threadId: string, runId: string): Promise<RunUsage> {
  const d = await get<Record<string, unknown>>(
    `/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/usage`
  );
  const rec = (d && typeof d === "object" ? d : {}) as Record<string, unknown>;
  const source = rec.by_model_source;
  return {
    run_id: text(rec.run_id),
    thread_id: text(rec.thread_id),
    model: typeof rec.model === "string" && rec.model ? rec.model : null,
    status: text(rec.status, "unknown"),
    total_input_tokens: count(rec.total_input_tokens),
    total_output_tokens: count(rec.total_output_tokens),
    total_tokens: count(rec.total_tokens),
    llm_call_count: count(rec.llm_call_count),
    lead_agent_tokens: count(rec.lead_agent_tokens),
    subagent_tokens: count(rec.subagent_tokens),
    middleware_tokens: count(rec.middleware_tokens),
    by_model: (Array.isArray(rec.by_model) ? rec.by_model : []).map(toUsageModelRow),
    by_model_source: source === "per_model" || source === "run_totals" ? source : "unavailable",
    calls: (Array.isArray(rec.calls) ? rec.calls : []).map(toUsageCallRow),
    calls_complete: rec.calls_complete === true,
    total_cost: count(rec.total_cost),
    currency: typeof rec.currency === "string" && rec.currency ? rec.currency : null,
    pricing_configured: rec.pricing_configured === true,
  };
}

/** `null` means the number is unknown — never rendered as `0`. */
export function formatTokenCount(value: number | null): string {
  if (value === null) return "—";
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

/**
 * A cost string, or `null` when the server reported no cost. `pricingConfigured`
 * lets the caller say *why* (no pricing configured vs. no price for this model).
 */
export function formatCost(cost: number | null, currency: string | null): string | null {
  if (cost === null) return null;
  if (cost === 0) return currency ? `${currency} 0.00` : "0.00";
  if (cost < 0.01) return `${currency ? `${currency} ` : ""}${cost.toFixed(6)}`;
  return `${currency ? `${currency} ` : ""}${cost.toFixed(2)}`;
}

export interface WorkspaceChange {
  path: string;
  kind: string;
  diff?: string | null;
}

export async function fetchWorkspaceChanges(threadId: string, runId: string): Promise<WorkspaceChange[]> {
  const d = await get<unknown>(
    `/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/workspace-changes`
  );
  return asList(d, ["changes", "files", "data"]).map((c) => ({
    path: String(pick(c, ["path", "file"], "")),
    kind: String(pick(c, ["kind", "change", "status"], "modified")),
    diff: (c.diff as string | null) ?? null,
  }));
}

/** Ask the backend for clean regenerate input for an assistant answer. Returns null when unsupported. */
export async function prepareRegenerate(threadId: string, messageId: string): Promise<Record<string, unknown> | null> {
  try {
    // Backend requires {message_id} — the target assistant message id.
    return await send<Record<string, unknown>>(
      `/threads/${encodeURIComponent(threadId)}/runs/regenerate/prepare`,
      "POST",
      { message_id: messageId }
    );
  } catch {
    return null;
  }
}

/** Ask the backend for edit-replay input. Returns null when unsupported. */
export async function prepareEditRegenerate(
  threadId: string,
  humanMessageId: string,
  replacementText: string
): Promise<Record<string, unknown> | null> {
  try {
    // Backend requires {human_message_id, replacement_text}.
    return await send<Record<string, unknown>>(
      `/threads/${encodeURIComponent(threadId)}/runs/edit-regenerate/prepare`,
      "POST",
      { human_message_id: humanMessageId, replacement_text: replacementText }
    );
  } catch {
    return null;
  }
}
