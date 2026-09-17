import { get, send, pick } from "./http";

export async function suggestionsEnabled(): Promise<boolean> {
  try {
    const d = await get<Record<string, unknown>>("/suggestions/config");
    return Boolean(pick(d, ["enabled"], true));
  } catch {
    return false;
  }
}

/** Generate follow-up question chips for a thread. */
export async function suggestFollowUps(
  threadId: string,
  messages: Array<{ role: string; content: string }>,
  n = 3
): Promise<string[]> {
  // Backend route: POST /api/threads/{thread_id}/suggestions {messages, n} -> {suggestions}
  const d = await send<Record<string, unknown>>(
    `/threads/${encodeURIComponent(threadId)}/suggestions`,
    "POST",
    { messages: messages.slice(-8), n }
  );
  const list = d.suggestions;
  return Array.isArray(list) ? list.filter((s): s is string => typeof s === "string") : [];
}

/** Rewrite a composer draft before sending. Returns the original text on failure. */
export async function polishDraft(text: string, threadId?: string): Promise<{ text: string; changed: boolean }> {
  const d = await send<Record<string, unknown>>("/input-polish", "POST", {
    text,
    thread_id: threadId ?? undefined,
  });
  return {
    text: String(pick(d, ["rewritten_text", "text"], text)),
    changed: Boolean(pick(d, ["changed"], false)),
  };
}
