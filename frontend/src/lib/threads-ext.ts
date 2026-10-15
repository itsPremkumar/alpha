import { get, send, asList, pick } from "./http";

export interface ThreadGoal {
  goal: string | null;
  status: string | null;
  updated_at: string | null;
  continuation_count: number | null;
  max_continuations: number | null;
  no_progress_count: number | null;
  max_no_progress_continuations: number | null;
  last_evaluation: ThreadGoalEvaluation | null;
}

export interface ThreadGoalEvaluation {
  satisfied: boolean | null;
  blocker: string | null;
  reason: string | null;
  evidence_summary: string | null;
  run_id: string | null;
  evaluated_at: string | null;
  stand_down_reason: string | null;
}

/** Title from a ThreadResponse record: metadata.title, then values.title, else fallback. */
/**
 * The title a thread actually has, per the Gateway.
 *
 * `GET/POST /threads` omits `title` entirely for a thread that has never been
 * titled — measured live: all 31 rows from `/api/threads/search` came back with
 * keys `created_at, interrupts, metadata, status, thread_id, updated_at, values`
 * and **no** `title`. The title can live on the row, in `metadata`, or in
 * `values`, so every reader must go through here instead of touching
 * `thread.title` directly: a bare `thread.title.toLowerCase()` threw
 * `TypeError: Cannot read properties of undefined (reading 'toLowerCase')` and
 * took down the whole app on load.
 */
export function threadTitle(t: Record<string, unknown>): string {
  const meta = (t.metadata ?? {}) as Record<string, unknown>;
  const values = (t.values ?? {}) as Record<string, unknown>;
  const title = t.title ?? meta.title ?? values.title;
  return typeof title === "string" && title ? title : "Untitled";
}

export async function searchThreads(query: string, limit = 20): Promise<Array<Record<string, unknown>>> {
  // POST /threads/search has no title-query filter. Walk every offset page so
  // older local conversations remain searchable instead of stopping at 100.
  const pageSize = 500;
  const all: Array<Record<string, unknown>> = [];
  let offset = 0;
  let previousBoundary = "";
  while (true) {
    const data = await send<unknown>("/threads/search", "POST", { limit: pageSize, offset });
    const page = asList(data, ["threads", "results", "data"]);
    // A server that ignores `offset` would return the same page forever.
    const boundary =
      page.length > 0 ? `${String(pick(page[0], ["thread_id", "id"], ""))}:${String(pick(page[page.length - 1], ["thread_id", "id"], ""))}` : "";
    if (page.length === pageSize && boundary && boundary === previousBoundary) {
      throw new Error("Conversation search pagination did not advance; stopped to avoid an endless loop.");
    }
    previousBoundary = boundary;
    all.push(...page);
    if (page.length < pageSize) break;
    offset += page.length;
  }
  const q = query.trim().toLowerCase();
  return all
    .map((thread) => ({ thread_id: String(pick(thread, ["thread_id", "id"], "")), title: threadTitle(thread) }))
    .filter((thread) => !q || thread.thread_id.toLowerCase().includes(q) || thread.title.toLowerCase().includes(q))
    .slice(0, limit);
}

export async function renameThread(threadId: string, title: string): Promise<void> {
  // PATCH /threads/{id} merges {metadata} — a top-level {title} is ignored.
  await send(`/threads/${encodeURIComponent(threadId)}`, "PATCH", { metadata: { title } });
}

export async function deleteThread(threadId: string): Promise<void> {
  await send(`/threads/${encodeURIComponent(threadId)}`, "DELETE");
}

export async function branchThread(
  threadId: string,
  opts?: { messageId?: string; title?: string }
): Promise<{ thread_id: string }> {
  // POST /branches requires the target assistant message_id. Resolve it from
  // the latest server-side assistant message when the caller has none.
  let messageId = opts?.messageId;
  if (!messageId) {
    const { fetchThreadHistoryResult } = await import("./api");
    // A failed history load must surface as that failure — never fall through
    // to the misleading "no assistant message yet" empty-state below.
    const history = await fetchThreadHistoryResult(threadId);
    if (!history.ok) {
      throw new Error(`Couldn't load this chat's history to branch from — ${history.error}`);
    }
    messageId = [...history.value].reverse().find((m) => m.role === "assistant")?.id;
  }
  if (!messageId) throw new Error("No assistant message to branch from yet — send a message first.");
  const d = await send<Record<string, unknown>>(
    `/threads/${encodeURIComponent(threadId)}/branches`,
    "POST",
    { message_id: messageId, ...(opts?.title ? { title: opts.title } : {}) }
  );
  return { thread_id: String(pick(d, ["thread_id", "new_thread_id", "id"], "")) };
}

export async function moveThread(threadId: string, projectId: string | null): Promise<void> {
  await send(`/threads/${encodeURIComponent(threadId)}/move`, "POST", { project_id: projectId });
}

function threadGoalFromResponse(value: unknown): ThreadGoal {
  if (!value || typeof value !== "object" || !("goal" in value)) {
    throw new Error("Gateway returned a malformed goal response.");
  }
  const raw = (value as Record<string, unknown>).goal;
  if (raw === null) {
    return {
      goal: null,
      status: null,
      updated_at: null,
      continuation_count: null,
      max_continuations: null,
      no_progress_count: null,
      max_no_progress_continuations: null,
      last_evaluation: null,
    };
  }
  // Older Gateway builds returned the objective directly as a string.
  if (typeof raw === "string") {
    return {
      goal: raw || null,
      status: null,
      updated_at: null,
      continuation_count: null,
      max_continuations: null,
      no_progress_count: null,
      max_no_progress_continuations: null,
      last_evaluation: null,
    };
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("Gateway returned a malformed goal response.");
  }
  const record = raw as Record<string, unknown>;
  const objective = record.objective ?? record.text ?? record.goal;
  const evaluation = record.last_evaluation;
  const lastEvaluation = evaluation && typeof evaluation === "object" && !Array.isArray(evaluation)
    ? evaluation as Record<string, unknown>
    : null;
  const finiteNumber = (field: unknown): number | null =>
    typeof field === "number" && Number.isFinite(field) ? field : null;
  const text = (field: unknown): string | null => typeof field === "string" ? field : null;
  return {
    goal: typeof objective === "string" && objective ? objective : null,
    status: text(record.status),
    updated_at: text(record.updated_at),
    continuation_count: finiteNumber(record.continuation_count),
    max_continuations: finiteNumber(record.max_continuations),
    no_progress_count: finiteNumber(record.no_progress_count),
    max_no_progress_continuations: finiteNumber(record.max_no_progress_continuations),
    last_evaluation: lastEvaluation ? {
      satisfied: typeof lastEvaluation.satisfied === "boolean" ? lastEvaluation.satisfied : null,
      blocker: text(lastEvaluation.blocker),
      reason: text(lastEvaluation.reason),
      evidence_summary: text(lastEvaluation.evidence_summary),
      run_id: text(lastEvaluation.run_id),
      evaluated_at: text(lastEvaluation.evaluated_at),
      stand_down_reason: text(lastEvaluation.stand_down_reason),
    } : null,
  };
}

export async function fetchGoal(threadId: string): Promise<ThreadGoal> {
  // A failed read must remain a failure. Returning {goal:null} made an
  // unavailable Gateway indistinguishable from a server-confirmed cleared goal.
  const d = await get<unknown>(`/threads/${encodeURIComponent(threadId)}/goal`);
  return threadGoalFromResponse(d);
}

export async function setGoal(threadId: string, goal: string): Promise<ThreadGoal> {
  // PUT /goal requires {objective} — {goal} is rejected with 422.
  const response = await send<unknown>(`/threads/${encodeURIComponent(threadId)}/goal`, "PUT", { objective: goal });
  return threadGoalFromResponse(response);
}

export async function clearGoal(threadId: string): Promise<void> {
  await send(`/threads/${encodeURIComponent(threadId)}/goal`, "DELETE");
}

export async function compactThread(threadId: string): Promise<string> {
  // POST /compact returns {compacted, reason, removed_message_count,
  // preserved_message_count} — there is no summary field.
  const d = await send<Record<string, unknown>>(
    `/threads/${encodeURIComponent(threadId)}/compact`,
    "POST",
    {}
  );
  if (d.compacted) {
    const removed = Number(d.removed_message_count ?? 0);
    const kept = Number(d.preserved_message_count ?? 0);
    return `Context compacted: summarized ${removed} older message${removed === 1 ? "" : "s"}, kept ${kept} recent.`;
  }
  return String(pick(d, ["reason", "message"], "Nothing to compact — context is already fresh."));
}

export interface TokenUsage {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  contextPercent: number | null;
}

export async function fetchTokenUsage(threadId: string): Promise<TokenUsage | null> {
  try {
    // Backend: {total_tokens, total_input_tokens, total_output_tokens,
    // context_usage: {percentage}} — there are no input_tokens/prompt_tokens keys.
    const d = await get<Record<string, unknown>>(
      `/threads/${encodeURIComponent(threadId)}/token-usage`
    );
    const input = Number(pick(d, ["total_input_tokens", "input_tokens", "prompt_tokens"], 0));
    const output = Number(pick(d, ["total_output_tokens", "output_tokens", "completion_tokens"], 0));
    const ctx = d.context_usage;
    const pct =
      ctx && typeof ctx === "object"
        ? Number((ctx as Record<string, unknown>).percent ?? (ctx as Record<string, unknown>).percentage ?? NaN)
        : Number(d.context_percent ?? NaN);
    return {
      inputTokens: input,
      outputTokens: output,
      totalTokens: Number(pick(d, ["total_tokens"], input + output)),
      contextPercent: Number.isFinite(pct) ? Math.round(pct) : null,
    };
  } catch {
    return null;
  }
}
