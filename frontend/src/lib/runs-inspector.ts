/**
 * Run inspector client — the typed reads behind one run's full story.
 *
 * The Runs view answers "which runs exist"; this answers "what happened in
 * *this* run". Every field below is mapped from a real Gateway response, and
 * the mapping rules are the repo's own:
 *
 *  - **Absent is `null`, never a coerced `0` / `""` / `false`.** A count the
 *    server did not report renders as unknown, not as zero.
 *  - **Server enum strings are preserved verbatim.** `status` is
 *    `string | null`; an unknown status from a newer Gateway is never snapped
 *    to `success`, and a missing one is never mapped to `running`.
 *  - **A failed read rejects.** Every function here lets the transport's
 *    `ApiError` through with the server's own `detail`, so an empty list can
 *    only ever mean "the server said there is nothing".
 *  - **A bounded walk reports `complete: false`.** A partial transcript, event
 *    stream or history page is never presented as the whole thing.
 *
 * One deliberate non-substitution: the run's **terminal status, error and stop
 * reason come from the run record** (`GET .../runs/{rid}`), never from the
 * `run.end` event's `metadata.status`. A real run in this repository carries
 * `status: "error"` on the record while its own `run.end` event reports
 * `metadata.status: "success"` — the graph finished, the run did not.
 * Substituting one for the other erases exactly the failure a user opens an
 * inspector to find, so the inspector shows the record's status as the terminal
 * status and renders the event's own metadata as itself.
 */

import { get } from "./http";
import { fetchRunEventsPage, type RunEvent, type RunEventSeverity, runEventSeverity } from "./runs";
import { honestToolStatus } from "./sse-reducer";
import type { ToolCallStatus } from "../types/chat";

/* ── strict readers ────────────────────────────────────────────────────────── */

/** A real number, or `null` for anything the server did not measure. */
function count(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** A non-empty string, or `null`. Never `""` standing in for a field. */
function str(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

/** A present string including the empty one — for free-text bodies. */
function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

/** An explicit boolean, or `null` when the server sent no boolean. */
function flag(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function rec(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

/* ── the run record ───────────────────────────────────────────────────────── */

/** One message as it was submitted with the run, read from `kwargs.input`. */
export interface RunInputMessage {
  /** `user` / `system` / whatever the server recorded — verbatim. */
  role: string | null;
  /** The body, or `null` when it was not text this client can display. */
  text: string | null;
}

export interface RunModelSplit {
  model: string;
  input: number | null;
  output: number | null;
  total: number | null;
}

export interface RunRecord {
  run_id: string | null;
  thread_id: string | null;
  /** The server's own status string, verbatim. `null` when not reported. */
  status: string | null;
  error: string | null;
  stop_reason: string | null;
  /** The model that actually served this run. `null` when not reported. */
  model: string | null;
  assistant_id: string | null;
  multitask_strategy: string | null;
  created_at: string | null;
  updated_at: string | null;
  /** `metadata.alpha_trace_id` — the correlation id, when the run has one. */
  trace_id: string | null;
  total_input_tokens: number | null;
  total_output_tokens: number | null;
  total_tokens: number | null;
  llm_call_count: number | null;
  lead_agent_tokens: number | null;
  subagent_tokens: number | null;
  middleware_tokens: number | null;
  message_count: number | null;
  /** The messages the run was created with; `null` when not recorded. */
  input: RunInputMessage[] | null;
  /** False when `kwargs.input` was absent or not a readable message list. */
  input_readable: boolean;
  /** Per-model token split; `null` when the run reported none. */
  token_usage_by_model: RunModelSplit[] | null;
}

/**
 * Read a message body that may be plain text or a list of content blocks.
 * Anything else is `null` with `readable: false` — never an empty string,
 * which would render as "the user sent nothing".
 */
function messageBody(value: unknown): { text: string | null; readable: boolean } {
  if (typeof value === "string") return { text: value, readable: true };
  if (Array.isArray(value)) {
    const parts: string[] = [];
    for (const block of value) {
      const body = rec(block);
      if (!body) continue;
      const part = text(body.text);
      if (part !== null) parts.push(part);
    }
    return { text: parts.join("\n"), readable: true };
  }
  return { text: null, readable: false };
}

function toInputMessages(input: unknown): { messages: RunInputMessage[] | null; readable: boolean } {
  const container = rec(input);
  if (!container) return { messages: null, readable: false };
  const raw = container.messages ?? container.input ?? container;
  if (!Array.isArray(raw)) return { messages: null, readable: false };
  return {
    messages: raw.map((item) => {
      const message = rec(item);
      if (!message) return { role: null, text: null };
      return {
        role: str(message.role) ?? str(message.type),
        text: messageBody(message.content ?? message.text).text,
      };
    }),
    readable: true,
  };
}

/** Map one run record from the list, page, or detail route — they agree. */
export function toRunRecord(raw: unknown): RunRecord {
  const r = rec(raw) ?? {};
  const meta = rec(r.metadata);
  const kwargs = rec(r.kwargs);
  const input = toInputMessages(kwargs ? kwargs.input : null);
  const split = rec(r.token_usage_by_model);
  const byModel: RunModelSplit[] | null = split
    ? Object.entries(split).map(([model, usage]) => {
        const u = rec(usage) ?? {};
        return {
          model,
          input: count(u.input_tokens),
          output: count(u.output_tokens),
          total: count(u.total_tokens),
        };
      })
    : null;
  return {
    run_id: str(r.run_id) ?? str(r.id),
    thread_id: str(r.thread_id),
    status: str(r.status),
    error: text(r.error),
    stop_reason: text(r.stop_reason),
    model: str(r.model) ?? str(r.model_name),
    assistant_id: str(r.assistant_id) ?? str(r.assistant),
    multitask_strategy: str(r.multitask_strategy),
    created_at: str(r.created_at),
    updated_at: str(r.updated_at),
    trace_id: str(meta?.alpha_trace_id),
    total_input_tokens: count(r.total_input_tokens),
    total_output_tokens: count(r.total_output_tokens),
    total_tokens: count(r.total_tokens),
    llm_call_count: count(r.llm_call_count),
    lead_agent_tokens: count(r.lead_agent_tokens),
    subagent_tokens: count(r.subagent_tokens),
    middleware_tokens: count(r.middleware_tokens),
    message_count: count(r.message_count),
    input: input.messages,
    input_readable: input.readable,
    token_usage_by_model: byModel && byModel.length > 0 ? byModel : null,
  };
}

/** `GET /threads/{id}/runs/{rid}` — the run's own record and terminal state. */
export async function fetchRunRecord(threadId: string, runId: string): Promise<RunRecord> {
  const d = await get<unknown>(`/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}`);
  return toRunRecord(d);
}

/* ── the run list (keyset) ────────────────────────────────────────────────── */

export interface RunPageQuery {
  limit?: number;
  beforeCreatedAt?: string | null;
  beforeRunId?: string | null;
}

export interface RunPage {
  runs: RunRecord[];
  hasMore: boolean;
  nextBeforeCreatedAt: string | null;
  nextBeforeRunId: string | null;
}

export const RUN_PAGE_LIMIT = 50;
export const RUN_PAGE_MAX_LIMIT = 200;

/**
 * One keyset page of a thread's runs, newest first.
 *
 * `before_created_at` and `before_run_id` are the server's own cursor and must
 * travel together — one alone is a 422, so an incomplete cursor is not sent.
 */
export async function fetchRunPage(threadId: string, query: RunPageQuery = {}): Promise<RunPage> {
  const limit = Math.min(Math.max(Math.trunc(count(query.limit) ?? RUN_PAGE_LIMIT), 1), RUN_PAGE_MAX_LIMIT);
  const params = new URLSearchParams({ limit: String(limit) });
  const beforeCreatedAt = str(query.beforeCreatedAt);
  const beforeRunId = str(query.beforeRunId);
  if (beforeCreatedAt && beforeRunId) {
    params.set("before_created_at", beforeCreatedAt);
    params.set("before_run_id", beforeRunId);
  }
  const body = rec(await get<unknown>(`/threads/${encodeURIComponent(threadId)}/runs/page?${params.toString()}`));
  const data = body && Array.isArray(body.data) ? body.data : null;
  if (!data) throw new Error("The server returned an unreadable run page.");
  return {
    runs: data.map(toRunRecord),
    hasMore: body!.has_more === true,
    nextBeforeCreatedAt: str(body!.next_before_created_at),
    nextBeforeRunId: str(body!.next_before_run_id),
  };
}

export interface RunListResult {
  runs: RunRecord[];
  /** False when the bounded walk stopped before the end of the history. */
  complete: boolean;
}

export interface OlderRunsPage {
  runs: RunRecord[];
  /** False when the server reported another page after this one. */
  hasMore: boolean;
  /** The server's own next cursor; `null` when there is no further page. */
  nextBeforeCreatedAt: string | null;
  nextBeforeRunId: string | null;
}

/**
 * One keyset page of runs OLDER than a cursor.
 *
 * This is how a run the newest page does not contain is reached at all: the
 * cursor is the server's own `next_before_*` pair, and an incomplete pair is
 * refused rather than sent, because the route 422s on one cursor field alone.
 */
export async function fetchOlderRuns(
  threadId: string,
  beforeCreatedAt: string | null,
  beforeRunId: string | null,
  limit: number = RUN_PAGE_LIMIT
): Promise<OlderRunsPage> {
  const created = str(beforeCreatedAt);
  const runId = str(beforeRunId);
  if (!created || !runId) {
    // No complete cursor is not a reason to re-read the newest page: the caller
    // asked for older runs and there is nowhere to start from.
    return { runs: [], hasMore: false, nextBeforeCreatedAt: null, nextBeforeRunId: null };
  }
  const page = await fetchRunPage(threadId, { limit, beforeCreatedAt: created, beforeRunId: runId });
  return {
    runs: page.runs,
    hasMore: page.hasMore,
    nextBeforeCreatedAt: page.nextBeforeCreatedAt,
    nextBeforeRunId: page.nextBeforeRunId,
  };
}

/** Page cap for one bounded history walk. */
export const RUN_LIST_MAX_PAGES = 4;

/**
 * The newest runs for a thread, following the server's keyset cursor.
 *
 * Bounded on purpose: a thread with thousands of runs must not pull all of them
 * into a picker, and a stopped walk says so rather than pretending the list is
 * the whole history.
 */
export async function fetchRecentRuns(threadId: string, maxRuns = 100): Promise<RunListResult> {
  const cap = Math.max(1, Math.trunc(maxRuns));
  const runs: RunRecord[] = [];
  let beforeCreatedAt: string | null = null;
  let beforeRunId: string | null = null;

  for (let page = 0; page < RUN_LIST_MAX_PAGES; page++) {
    const result = await fetchRunPage(threadId, { limit: RUN_PAGE_LIMIT, beforeCreatedAt, beforeRunId });
    runs.push(...result.runs);
    if (!result.hasMore) return { runs: runs.slice(0, cap), complete: true };
    if (runs.length >= cap) return { runs: runs.slice(0, cap), complete: false };
    if (!result.nextBeforeCreatedAt || !result.nextBeforeRunId) return { runs, complete: false };
    beforeCreatedAt = result.nextBeforeCreatedAt;
    beforeRunId = result.nextBeforeRunId;
  }
  return { runs, complete: false };
}

/* ── the transcript ───────────────────────────────────────────────────────── */

/** What a transcript row is, derived from the server's own `event_type`. */
export type TranscriptKind = "prompt" | "answer" | "tool_call" | "tool_result" | "note" | "unknown";

export interface TranscriptEntry {
  /** The run-event `seq` this row was projected from. */
  seq: number | null;
  /** The server's event type, verbatim. */
  eventType: string;
  /** The server's category, verbatim. */
  category: string;
  createdAt: string | null;
  kind: TranscriptKind;
  /** `human` / `ai` / `tool` / `system` as recorded, or `null`. */
  role: string | null;
  messageId: string | null;
  /** The body as text, or `null` when the content was not readable text. */
  text: string | null;
  /** True when the body was present but could not be read as plain text. */
  textOpaque: boolean;
  /** `metadata.caller` — who produced it (`lead_agent`, a subagent, …). */
  caller: string | null;
  /** The `response_metadata.model_name` the model reported for this row. */
  model: string | null;
  /** The persisted message record, for the tool-call join. */
  message: Record<string, unknown>;
}

export interface Transcript {
  entries: TranscriptEntry[];
  /** False when the bounded walk stopped before the end of the transcript. */
  complete: boolean;
  /** Rows the server marked `hide_from_ui`; counted, never rendered. */
  hiddenCount: number;
}

const MESSAGE_PAGE_LIMIT = 100;
const MESSAGE_MAX_ROWS = 1000;
const MESSAGE_MAX_PAGES = 12;

function toTranscriptEntry(raw: unknown): TranscriptEntry {
  const row = rec(raw) ?? {};
  const message = rec(row.content) ?? {};
  const meta = rec(row.metadata) ?? {};
  const additional = rec(message.additional_kwargs) ?? {};
  const responseMeta = rec(message.response_metadata) ?? {};
  const eventType = str(row.event_type) ?? "unknown";
  const role = str(message.type) ?? str(message.role);
  const body = messageBody(message.content);
  const declared = str(row.category);

  let kind: TranscriptKind = "unknown";
  if (eventType === "llm.human.input") kind = "prompt";
  else if (eventType === "llm.ai.response") kind = "answer";
  else if (eventType === "llm.tool.result") kind = "tool_result";
  else if (role === "human") kind = "prompt";
  else if (role === "ai") kind = "answer";
  else if (role === "tool") kind = "tool_result";
  else if (role !== null) kind = "note";

  return {
    seq: count(row.seq),
    eventType,
    category: declared ?? "unknown",
    createdAt: str(row.created_at),
    kind,
    role,
    messageId: str(message.id),
    text: body.text,
    textOpaque: !body.readable,
    caller: str(meta.caller) ?? str(additional.caller),
    model: str(responseMeta.model_name) ?? str(meta.model_name),
    message,
  };
}

/**
 * The run's messages, oldest first, paged with the server's `after_seq`.
 *
 * The endpoint returns `{data, has_more}` with no total, so the walk follows
 * `has_more` and stops on a page with no usable `seq` — reporting
 * `complete: false` rather than presenting a prefix as the whole transcript.
 * Rows the server marked `hide_from_ui` are counted, not rendered.
 */
export async function fetchRunTranscript(threadId: string, runId: string): Promise<Transcript> {
  const base = `/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/messages`;
  const entries: TranscriptEntry[] = [];
  let hiddenCount = 0;
  let afterSeq: number | null = null;

  for (let page = 0; page < MESSAGE_MAX_PAGES; page++) {
    const params = new URLSearchParams({ limit: String(MESSAGE_PAGE_LIMIT) });
    if (afterSeq !== null) params.set("after_seq", String(afterSeq));
    const body = rec(await get<unknown>(`${base}?${params.toString()}`));
    const data = body && Array.isArray(body.data) ? body.data : null;
    if (!data) throw new Error("The server returned an unreadable message page.");
    for (const row of data) {
      const message = rec(rec(row)?.content) ?? {};
      const additional = rec(message.additional_kwargs) ?? {};
      if (additional.hide_from_ui === true) {
        hiddenCount += 1;
        continue;
      }
      entries.push(toTranscriptEntry(row));
    }
    if (body!.has_more !== true) return { entries, complete: true, hiddenCount };
    const lastSeq = count(rec(data[data.length - 1])?.seq);
    // `after_seq` is validated as >= 1 by the server, so a row without a
    // usable seq ends the walk instead of issuing a request it would reject.
    if (lastSeq === null || lastSeq < 1) return { entries, complete: false, hiddenCount };
    if (afterSeq !== null && lastSeq <= afterSeq) return { entries, complete: false, hiddenCount };
    if (entries.length >= MESSAGE_MAX_ROWS) return { entries, complete: false, hiddenCount };
    afterSeq = lastSeq;
  }
  return { entries, complete: false, hiddenCount };
}

/** The prompts the user actually sent for this run, in order. */
export function transcriptPrompts(transcript: Transcript): TranscriptEntry[] {
  return transcript.entries.filter((entry) => entry.kind === "prompt");
}

/**
 * The run's final answer — the last assistant row that carries text.
 *
 * A `llm.ai.response` row that only issued tool calls has empty text; treating
 * it as the answer would put a blank bubble where the answer should be.
 */
export function finalAnswer(transcript: Transcript): TranscriptEntry | null {
  for (let i = transcript.entries.length - 1; i >= 0; i--) {
    const entry = transcript.entries[i];
    if (entry.kind !== "answer") continue;
    if (entry.text !== null && entry.text.trim() !== "") return entry;
  }
  return null;
}

export interface RunMessageCount {
  /** How many message rows this read actually saw. */
  count: number;
  /**
   * True when the server said more rows exist than this read returned.
   *
   * This is the difference between "this run has 14 messages" and "the first
   * 50 rows are all we were sent". The endpoint's own default page size is 50,
   * so a bare single read silently truncates a long run's message count.
   */
  partial: boolean;
}

const MESSAGE_COUNT_PAGE = 200;
const MESSAGE_COUNT_MAX_ROWS = 4000;
const MESSAGE_COUNT_MAX_PAGES = 20;

/**
 * How many messages one run recorded, following `has_more` until the server
 * says there are no more or the bounded walk stops.
 *
 * `partial: true` is a real answer about the read, not about the run: the
 * caller must then present the count as a floor ("N+"), never as the total.
 */
export async function fetchRunMessageCount(threadId: string, runId: string): Promise<RunMessageCount> {
  const base = `/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/messages`;
  // Named `seen`, not `count`: a local `count` would shadow the `count()` reader
  // this module uses everywhere else, and the shadowed call is a tsc error.
  let seen = 0;
  let afterSeq: number | null = null;

  for (let page = 0; page < MESSAGE_COUNT_MAX_PAGES; page++) {
    const params = new URLSearchParams({ limit: String(MESSAGE_COUNT_PAGE) });
    if (afterSeq !== null) params.set("after_seq", String(afterSeq));
    const body = rec(await get<unknown>(`${base}?${params.toString()}`));
    const data = body && Array.isArray(body.data) ? body.data : null;
    if (!data) throw new Error("The server returned an unreadable message page.");
    seen += data.length;
    if (body!.has_more !== true) return { count: seen, partial: false };
    const lastSeq = count(rec(data[data.length - 1])?.seq);
    if (lastSeq === null || lastSeq < 1) return { count: seen, partial: true };
    if (afterSeq !== null && lastSeq <= afterSeq) return { count: seen, partial: true };
    if (seen >= MESSAGE_COUNT_MAX_ROWS) return { count: seen, partial: true };
    afterSeq = lastSeq;
  }
  return { count: seen, partial: true };
}

/* ── tool calls ───────────────────────────────────────────────────────────── */

/**
 * A narrow, disclosed detector for a result whose *text* is an error while the
 * run's journalled status claims success.
 *
 * It exists because the run journal and the checkpoint message disagree in real
 * runs: a `read_file` of a missing file is journalled as `status: "success"`
 * with an empty `additional_kwargs`, while the same run's tool meta records
 * `status: "error"`. The inspector neither invents a verdict from the text nor
 * hides the run's own status — it shows both and names the conflict.
 */
const ERROR_TEXT = /^\s*(error\b|traceback\b|exception\b|fatal\b|command not found\b)/i;

export interface ToolCallRecord {
  /** The provider's `tool_call_id`; `null` when the server sent none. */
  id: string | null;
  /** The name from the call, else from the result row; `null` if neither has one. */
  name: string | null;
  /** The arguments exactly as recorded — object, string, or `null`. */
  args: unknown;
  /** The arguments as display text, or `null` when they are not renderable. */
  argsText: string | null;
  /** `seq` of the `llm.ai.response` row that issued the call. */
  callSeq: number | null;
  callAt: string | null;
  caller: string | null;
  /**
   * The resolved verdict, or `null` when the run reported **no result at all**
   * for this call. `null` is the honest "not reported" state; it is never
   * turned into `completed`.
   */
  status: ToolCallStatus | null;
  /** The run's own status string for the result row, verbatim. */
  statusVerbatim: string | null;
  /** The result body verbatim, or `null` when the run recorded none. */
  resultText: string | null;
  resultSeq: number | null;
  resultAt: string | null;
  /** True when a result row exists but no call row matched its `tool_call_id`. */
  unattributed: boolean;
  /** True when the journalled status claims success but the output reads as an error. */
  statusConflictsOutput: boolean;
  /** `content.artifact` on the result row, when the run recorded one. */
  artifact: unknown;
}

export interface ToolCallList {
  calls: ToolCallRecord[];
  /** Result rows whose `tool_call_id` matched no call in this run. */
  unattributedCount: number;
}

function argsText(args: unknown): string | null {
  if (args === null || args === undefined) return null;
  if (typeof args === "string") return args;
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return null;
  }
}

function baseCall(id: string | null, name: string | null): ToolCallRecord {
  return {
    id,
    name,
    args: null,
    argsText: null,
    callSeq: null,
    callAt: null,
    caller: null,
    status: null,
    statusVerbatim: null,
    resultText: null,
    resultSeq: null,
    resultAt: null,
    unattributed: false,
    statusConflictsOutput: false,
    artifact: null,
  };
}

/**
 * Join a run's `llm.ai.response` calls to their `llm.tool.result` rows.
 *
 * A call with no result keeps `status: null` (rendered "no result reported", not
 * success). A result whose `tool_call_id` matches no call is kept as an
 * `unattributed` row rather than dropped or guessed onto another call. Verdicts
 * come from `honestToolStatus` — the same resolver the live transcript uses — so
 * the two surfaces cannot disagree about one call.
 */
export function toolCallsFrom(transcript: Transcript): ToolCallList {
  const calls: ToolCallRecord[] = [];
  const byId = new Map<string, ToolCallRecord>();
  let unattributedCount = 0;

  for (const entry of transcript.entries) {
    if (entry.kind !== "answer") continue;
    const declared = entry.message.tool_calls;
    if (!Array.isArray(declared)) continue;
    for (const raw of declared) {
      const call = rec(raw);
      if (!call) continue;
      const id = str(call.id);
      const record: ToolCallRecord = {
        ...baseCall(id, str(call.name)),
        args: call.args ?? null,
        argsText: argsText(call.args),
        callSeq: entry.seq,
        callAt: entry.createdAt,
        caller: entry.caller,
      };
      calls.push(record);
      if (id) byId.set(id, record);
    }
  }

  for (const entry of transcript.entries) {
    if (entry.kind !== "tool_result") continue;
    const id = str(entry.message.tool_call_id);
    const statusVerbatim = str(entry.message.status);
    const resultText = text(entry.message.content);
    const status = honestToolStatus(entry.message);
    const artifact = entry.message.artifact ?? null;
    const target = id ? byId.get(id) : undefined;
    if (!target) {
      // Keep the result: dropping it would make a recorded result look absent.
      unattributedCount += 1;
      const orphan = baseCall(id, str(entry.message.name));
      orphan.unattributed = true;
      orphan.status = status;
      orphan.statusVerbatim = statusVerbatim;
      orphan.resultText = resultText;
      orphan.resultSeq = entry.seq;
      orphan.resultAt = entry.createdAt;
      orphan.caller = entry.caller;
      orphan.artifact = artifact;
      orphan.statusConflictsOutput = status === "completed" && resultText !== null && ERROR_TEXT.test(resultText);
      calls.push(orphan);
      continue;
    }
    target.status = status;
    target.statusVerbatim = statusVerbatim;
    target.resultText = resultText;
    target.resultSeq = entry.seq;
    target.resultAt = entry.createdAt;
    if (target.name === null) target.name = str(entry.message.name);
    if (target.artifact === null) target.artifact = artifact;
    target.statusConflictsOutput = status === "completed" && resultText !== null && ERROR_TEXT.test(resultText);
  }

  return { calls, unattributedCount };
}

/* ── the event timeline ───────────────────────────────────────────────────── */

export interface TimelineEvent {
  /** The store-assigned, thread-global sequence. `null` when not reported. */
  seq: number | null;
  /** The server's event type, verbatim — never normalized or invented. */
  eventType: string;
  /** The server's category, verbatim. */
  category: string;
  createdAt: string | null;
  severity: RunEventSeverity;
  /** `metadata.task_id` for subagent rows, when the run recorded one. */
  taskId: string | null;
  /** The persisted payload, for the expandable detail view. */
  content: unknown;
  metadata: Record<string, unknown>;
}

export interface Timeline {
  events: TimelineEvent[];
  /** False when the bounded walk stopped before the end of the run's stream. */
  complete: boolean;
}

function toTimelineEvent(event: RunEvent): TimelineEvent {
  const meta = rec(event.metadata) ?? {};
  return {
    seq: event.seq,
    eventType: event.event_type,
    category: event.category,
    createdAt: event.created_at,
    severity: runEventSeverity(event),
    taskId: str(meta.task_id) ?? event.task_id,
    content: event.content,
    metadata: meta,
  };
}

/**
 * One run's full event stream, oldest first, over the server's own cursor.
 *
 * Every category is read — `trace`, `middleware`, `subagent` and `workspace`
 * rows are the only place some of a run's decisions are recorded at all.
 */
export async function fetchRunTimeline(threadId: string, runId: string): Promise<Timeline> {
  const page = await fetchRunEventsPage(threadId, runId);
  return { events: page.events.map(toTimelineEvent), complete: page.complete };
}

/* ── workspace changes ────────────────────────────────────────────────────── */

export interface WorkspaceFile {
  path: string | null;
  /** `created` / `modified` / `deleted` / `symlink_created` — verbatim. */
  status: string | null;
  root: string | null;
  sizeBefore: number | null;
  sizeAfter: number | null;
  binary: boolean | null;
  sensitive: boolean | null;
  diff: string | null;
  /** Why the diff is absent, when the server says (`binary`, `too_large`, …). */
  diffUnavailableReason: string | null;
  diffTruncated: boolean | null;
  additions: number | null;
  deletions: number | null;
}

export interface WorkspaceChangeSummary {
  created: number | null;
  modified: number | null;
  deleted: number | null;
  symlinkCreated: number | null;
  additions: number | null;
  deletions: number | null;
  truncated: boolean | null;
}

export interface WorkspaceChanges {
  /**
   * Whether the server could compare this run's snapshots at all.
   *
   * `false` is the important one: an unavailable comparison is NOT "no files
   * changed", and the counts below it are then not a measurement.
   */
  available: boolean | null;
  version: number | null;
  summary: WorkspaceChangeSummary | null;
  files: WorkspaceFile[];
}

function toWorkspaceFile(raw: unknown): WorkspaceFile {
  const f = rec(raw) ?? {};
  return {
    path: str(f.path),
    status: str(f.status) ?? str(f.kind),
    root: str(f.root),
    sizeBefore: count(f.size_before),
    sizeAfter: count(f.size_after),
    binary: flag(f.binary),
    sensitive: flag(f.sensitive),
    diff: text(f.diff),
    diffUnavailableReason: str(f.diff_unavailable_reason),
    diffTruncated: flag(f.diff_truncated),
    additions: count(f.additions),
    deletions: count(f.deletions),
  };
}

function toSummary(raw: unknown): WorkspaceChangeSummary | null {
  const s = rec(raw);
  if (!s) return null;
  return {
    created: count(s.created),
    modified: count(s.modified),
    deleted: count(s.deleted),
    symlinkCreated: count(s.symlink_created),
    additions: count(s.additions),
    deletions: count(s.deletions),
    truncated: flag(s.truncated),
  };
}

/**
 * `GET .../workspace-changes` — the run's own before/after comparison.
 *
 * A payload with no `summary` object is reported as `summary: null` (unknown),
 * never as a set of zeros.
 */
export async function fetchWorkspaceChangesForRun(threadId: string, runId: string): Promise<WorkspaceChanges> {
  const body = rec(
    await get<unknown>(`/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/workspace-changes`)
  );
  if (!body) throw new Error("The server returned an unreadable workspace-change report.");
  const files = Array.isArray(body.files) ? body.files.map(toWorkspaceFile) : [];
  return {
    available: flag(body.available),
    version: count(body.version),
    summary: toSummary(body.summary),
    files,
  };
}

/**
 * How many files a run changed — or `null` when the Gateway could not say.
 *
 * `available: false` means the server could not compare this run's workspace
 * snapshots at all. The counts it sends alongside that answer are not a
 * measurement, so this returns the honest unknown rather than `0`: a summary
 * tile that reads "0 file changes" claims the run touched nothing, which is a
 * different claim from "the comparison is unavailable".
 */
export function workspaceChangeCount(changes: WorkspaceChanges): number | null {
  if (changes.available !== true) return null;
  return changes.files.length;
}

/* ── delivered artifacts ──────────────────────────────────────────────────── */

/**
 * The run's delivery receipt, read from its own `run.delivery` event.
 *
 * This is a *delivery* record: which files the run presented, and whether the
 * presented set covered the produced set. It is not a verification verdict about
 * the run's answer, and it is not labelled as one.
 */
export interface DeliveryReceipt {
  presented: number | null;
  stage: string | null;
  satisfied: boolean | null;
  verificationSource: string | null;
  requirement: string | null;
  paths: string[];
  producedPaths: string[];
  presentedPaths: string[];
  matchedPaths: string[];
  /** The tool that presented each path, keyed by path. */
  byTool: Record<string, string[]>;
  seq: number | null;
  createdAt: string | null;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

/** `null` when the run recorded no delivery event at all. */
export function deliveryFrom(timeline: Timeline): DeliveryReceipt | null {
  const event = timeline.events.find((item) => item.eventType === "run.delivery");
  if (!event) return null;
  const content = rec(event.content) ?? {};
  const verification = rec(content.verification) ?? {};
  const byTool: Record<string, string[]> = {};
  const rawByTool = rec(content.by_tool);
  if (rawByTool) {
    for (const [tool, value] of Object.entries(rawByTool)) byTool[tool] = stringList(value);
  }
  return {
    presented: count(content.presented),
    stage: str(content.stage),
    satisfied: flag(content.satisfied),
    verificationSource: str(verification.source),
    requirement: str(verification.requirement),
    paths: stringList(content.paths),
    producedPaths: stringList(content.produced_paths),
    presentedPaths: stringList(content.presented_paths),
    matchedPaths: stringList(content.matched_paths),
    byTool,
    seq: event.seq,
    createdAt: event.createdAt,
  };
}

export interface ArtifactArchiveManifest {
  /**
   * How many files the archive endpoint will package for this run.
   *
   * The endpoint **rejects** (HTTP 409, "This response has no verified artifact
   * delivery") for a run with no delivery, which is a real answer rather than a
   * transport failure — so this function lets the rejection through and the
   * panel shows the server's own reason instead of claiming "0 artifacts".
   */
  fileCount: number | null;
}

/** `GET .../artifacts/archive` — the manifest for the run's downloadable zip. */
export async function fetchArtifactArchiveManifest(threadId: string, runId: string): Promise<ArtifactArchiveManifest> {
  const body = rec(
    await get<unknown>(`/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/artifacts/archive`)
  );
  if (!body) throw new Error("The server returned an unreadable artifact-archive manifest.");
  return { fileCount: count(body.file_count) };
}

/** A per-artifact reference recorded on a tool result row. */
export interface RunArtifact {
  toolCallId: string | null;
  toolName: string | null;
  artifact: unknown;
  seq: number | null;
  createdAt: string | null;
}

/**
 * Artifacts attached to this run's tool results, in the order they were
 * recorded. A run with none yields an empty list, which means the server
 * recorded none — not that delivery failed; that is the receipt's own claim.
 */
export function artifactsFrom(calls: ToolCallRecord[]): RunArtifact[] {
  const artifacts: RunArtifact[] = [];
  for (const call of calls) {
    if (call.artifact === null || call.artifact === undefined) continue;
    artifacts.push({
      toolCallId: call.id,
      toolName: call.name,
      artifact: call.artifact,
      seq: call.resultSeq,
      createdAt: call.resultAt,
    });
  }
  return artifacts;
}

/* ── thread context usage ─────────────────────────────────────────────────── */

export interface ThreadContextUsage {
  tokenCount: number | null;
  maxContextTokens: number | null;
  /** The server's own percentage, or `null` when it did not compute one. */
  percentage: number | null;
}

export interface ThreadTokenUsage {
  threadId: string | null;
  totalTokens: number | null;
  totalInputTokens: number | null;
  totalOutputTokens: number | null;
  totalRuns: number | null;
  /** `null` when the Gateway did not report a context measurement. */
  contextUsage: ThreadContextUsage | null;
}

/**
 * `GET /threads/{id}/token-usage` — the thread's totals and its context
 * occupancy. This is thread-wide, so it is labelled as such and never presented
 * as one run's cost.
 */
export async function fetchThreadTokenUsage(threadId: string): Promise<ThreadTokenUsage> {
  const body = rec(await get<unknown>(`/threads/${encodeURIComponent(threadId)}/token-usage`));
  if (!body) throw new Error("The server returned an unreadable token-usage report.");
  const context = rec(body.context_usage);
  return {
    threadId: str(body.thread_id),
    totalTokens: count(body.total_tokens),
    totalInputTokens: count(body.total_input_tokens),
    totalOutputTokens: count(body.total_output_tokens),
    totalRuns: count(body.total_runs),
    contextUsage: context
      ? {
          tokenCount: count(context.token_count),
          maxContextTokens: count(context.max_context_tokens),
          percentage: count(context.percentage),
        }
      : null,
  };
}

/* ── display helpers ──────────────────────────────────────────────────────── */

/** Counts and other measured numbers: a real value, or the honest unknown. */
export const UNKNOWN_VALUE = "—";

export function formatCount(value: number | null): string {
  return value === null ? UNKNOWN_VALUE : String(value);
}

/** A percentage the server computed; `null` stays unknown rather than `0%`. */
export function formatPercentage(value: number | null): string {
  return value === null ? UNKNOWN_VALUE : `${value.toFixed(1)}%`;
}

/**
 * The terminal-status wording for a run record.
 *
 * The status string is returned untouched; only the *presentation* differs, and
 * an unreported status is never described as `running` or as finished.
 */
export function terminalStatusLabel(status: string | null): string {
  if (status === null) return "terminal status not reported";
  return status;
}

/** True only for a status the run record actually reported as complete. */
export function isTerminalSuccess(status: string | null): boolean {
  return status === "success";
}

/**
 * True when the run record still reports an active status.
 *
 * A streaming run must never render as finished, so the inspector gates every
 * "this run is over" statement on the record's own status. An unknown status is
 * NOT treated as active or as finished — it is simply not known.
 */
const ACTIVE_STATUSES = new Set(["running", "pending", "queued", "in_progress"]);
export function isActive(status: string | null): boolean {
  return status !== null && ACTIVE_STATUSES.has(status);
}
