/**
 * Pure derivations for in-chat transcript search.
 *
 * Search runs over what the transcript actually holds: the
 * message body, the thinking trace, the tool-call names and
 * their reported outputs, and the plan items. A match names
 * the message it belongs to and which field matched, so the
 * UI can scroll to the message and say *why* it matched.
 * An empty or whitespace-only query matches nothing — never
 * the whole transcript.
 */

import type { ChatMessage } from "../types/chat";

/** One search hit: the message plus what inside it matched. */
export interface TranscriptMatch {
  messageId: string;
  /** Message index in the searched list, for "3 of 12". */
  matchIndex: number;
  /** Total hits across the transcript. */
  matchTotal: number;
  /** Which fields carried the query. At least one, never invented. */
  fields: Array<"content" | "thinking" | "tools" | "plan">;
}

export interface SearchableMessage {
  id: string;
  content?: string;
  thinking?: string;
  toolCalls?: Array<{ name?: string; output?: string }>;
  todos?: Array<{ content?: string }>;
}

/**
 * All hits for `query` across `messages`, in transcript order.
 * Case-insensitive substring match. Returns `[]` for a blank
 * query rather than claiming everything matched.
 */
export function findTranscriptMatches(
  messages: SearchableMessage[],
  query: string,
): TranscriptMatch[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];

  const hits: Array<{ messageId: string; fields: TranscriptMatch["fields"] }> =
    [];
  for (const message of messages) {
    const fields: TranscriptMatch["fields"] = [];
    if (message.content && message.content.toLowerCase().includes(needle))
      fields.push("content");
    if (message.thinking && message.thinking.toLowerCase().includes(needle))
      fields.push("thinking");
    const toolHit = (message.toolCalls ?? []).some(
      (call) =>
        (call.name && call.name.toLowerCase().includes(needle)) ||
        (call.output && call.output.toLowerCase().includes(needle)),
    );
    if (toolHit) fields.push("tools");
    const planHit = (message.todos ?? []).some(
      (todo) => todo.content && todo.content.toLowerCase().includes(needle),
    );
    if (planHit) fields.push("plan");
    if (fields.length > 0) hits.push({ messageId: message.id, fields });
  }
  return hits.map((hit, index) => ({
    messageId: hit.messageId,
    matchIndex: index + 1,
    matchTotal: hits.length,
    fields: hit.fields,
  }));
}

/**
 * Human label for where a hit matched, e.g. `"content + tools"`.
 * Reads the match's own fields — nothing else.
 */
export function matchFieldLabel(match: TranscriptMatch): string {
  return match.fields.join(" + ");
}

/**
 * Adapt a `ChatMessage` to the searchable shape. A thin
 * projection — no field is renamed, none is dropped.
 */
export function toSearchable(message: ChatMessage): SearchableMessage {
  return {
    id: message.id,
    content: message.content,
    thinking: message.thinking,
    toolCalls: message.toolCalls,
    todos: message.todos,
  };
}
