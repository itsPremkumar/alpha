import { get, send, asList, pick } from "./http";

export interface ThreadGoal {
  goal: string | null;
  updated_at?: string | null;
}

/** Title from a ThreadResponse record: metadata.title, then values.title, else fallback. */
function threadTitle(t: Record<string, unknown>): string {
  const meta = (t.metadata ?? {}) as Record<string, unknown>;
  const values = (t.values ?? {}) as Record<string, unknown>;
  const title = t.title ?? meta.title ?? values.title;
  return typeof title === "string" && title ? title : "Untitled";
}

export async function searchThreads(query: string, limit = 20): Promise<Array<Record<string, unknown>>> {
  // POST /threads/search returns a bare array of ThreadResponse (no query
  // filter server-side), so filter by title client-side to honor the query.
  const d = await send<unknown>("/threads/search", "POST", { limit: 100 });
  const list = asList(d, ["threads", "results", "data"]);
  const q = query.trim().toLowerCase();
  return list
    .map((t) => ({ thread_id: String(pick(t, ["thread_id", "id"], "")), title: threadTitle(t) }))
    .filter((t) => !q || t.thread_id.toLowerCase().includes(q) || t.title.toLowerCase().includes(q))
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
    const { fetchThreadHistory } = await import("./api");
    const history = await fetchThreadHistory(threadId);
    messageId = [...history].reverse().find((m) => m.role === "assistant")?.id;
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

export async function fetchGoal(threadId: string): Promise<ThreadGoal> {
  try {
    // GET /goal returns {goal: {objective, status, ...} | null} — the goal is
    // a state dict, not a plain string.
    const d = await get<Record<string, unknown>>(`/threads/${encodeURIComponent(threadId)}/goal`);
    const g = d.goal;
    if (g && typeof g === "object") {
      const rec = g as Record<string, unknown>;
      const objective = rec.objective ?? rec.text ?? rec.goal ?? null;
      return {
        goal: typeof objective === "string" && objective ? objective : null,
        updated_at: (rec.updated_at as string) ?? null,
      };
    }
    return { goal: typeof g === "string" && g ? g : null };
  } catch {
    return { goal: null };
  }
}

export async function setGoal(threadId: string, goal: string): Promise<void> {
  // PUT /goal requires {objective} — {goal} is rejected with 422.
  await send(`/threads/${encodeURIComponent(threadId)}/goal`, "PUT", { objective: goal });
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
