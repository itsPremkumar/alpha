import { get, send, pick } from "./http";

/**
 * Whether the Gateway reports follow-up suggestions as enabled.
 *
 * ## Why this is tri-state
 *
 * This used to be `catch { return false }`, which meant a failed read was
 * rendered as "suggestions are off" - a claim the server never made. The
 * frontend guide is explicit that a failed call must not be resolved into a
 * value that looks like a real answer, because the UI then shows a confident
 * state derived from nothing.
 *
 * `null` means "the Gateway did not tell us". Callers must render that as
 * unknown rather than folding it into `false`; see `SettingsSection`, which
 * labels it "Unknown" and does not offer a control to change it.
 */
export async function suggestionsEnabled(): Promise<boolean | null> {
  try {
    const d = await get<Record<string, unknown>>("/suggestions/config");
    const raw = pick(d, ["enabled"], null);
    return typeof raw === "boolean" ? raw : null;
  } catch {
    return null;
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
