/**
 * Transcript export: the conversation as Markdown.
 *
 * Builds a faithful `.md` document from the `ChatMessage[]`
 * the transcript holds — roles, server-stamped times, bodies,
 * thinking traces, plans and tool calls with their reported
 * outcomes. A field the run never reported is omitted, never
 * filled in: no invented durations, no guessed exit codes.
 */

import type { ChatMessage, ToolCallStatus } from "../types/chat";

const STATUS_WORDS: Record<string, string> = {
  completed: "completed",
  failed: "failed",
  partial: "partial result",
  error: "error",
  unknown: "unknown outcome",
  running: "running",
};

/**
 * One tool call as Markdown. The status word comes only from
 * what the run reported; a call with no result says exactly
 * that instead of borrowing a verdict.
 */
export function exportToolCall(
  call: {
    name?: string;
    args?: Record<string, unknown>;
    output?: string;
    status?: ToolCallStatus;
  },
  index: number,
): string {
  const lines: string[] = [];
  const status = call.status
    ? (STATUS_WORDS[call.status] ?? call.status)
    : "no result reported";
  lines.push(`**${index + 1}. ${call.name || "unnamed tool"}** — ${status}`);
  if (call.args && Object.keys(call.args).length > 0) {
    lines.push("```json");
    lines.push(JSON.stringify(call.args, null, 2));
    lines.push("```");
  }
  if (call.output) {
    // Fenced output is indented rather than fenced: tool output
    // may itself contain fences, which would close ours early.
    const indented = call.output
      .split("\n")
      .map((line) => `    ${line}`)
      .join("\n");
    lines.push(`Result:\n\n${indented}`);
  }
  return lines.join("\n");
}

/** One message as Markdown. Empty sections are omitted. */
export function exportMessage(message: ChatMessage): string {
  const who =
    message.role === "user"
      ? "You"
      : message.role === "assistant"
        ? "Alpha"
        : "System";
  const when = message.createdAt ? ` (${message.createdAt})` : "";
  const lines: string[] = [`## ${who}${when}`, ""];

  if (message.content) lines.push(message.content, "");

  if (message.thinking) {
    lines.push("> Thinking:", ">");
    for (const line of message.thinking.split("\n")) lines.push(`> ${line}`);
    lines.push("");
  }

  if (message.todos && message.todos.length > 0) {
    lines.push("Plan:");
    lines.push("");
    for (const todo of message.todos) {
      const box =
        todo.status === "completed"
          ? "x"
          : todo.status === "cancelled"
            ? "~"
            : " ";
      lines.push(`- [${box}] ${todo.content}`);
    }
    lines.push("");
  }

  if (message.toolCalls && message.toolCalls.length > 0) {
    lines.push("Tools used:");
    lines.push("");
    message.toolCalls.forEach((call, index) => {
      lines.push(exportToolCall(call, index), "");
    });
  }

  if (message.artifacts && message.artifacts.length > 0) {
    lines.push("Artifacts delivered:");
    lines.push("");
    for (const artifact of message.artifacts)
      lines.push(`- ${artifact.name} (${artifact.type || "file"})`);
    lines.push("");
  }

  return lines.join("\n").trimEnd();
}

/**
 * The whole transcript as one Markdown document, oldest first.
 * An empty list yields a document that says so rather than an
 * empty file that reads as a failed export.
 */
export function exportTranscript(
  messages: ChatMessage[],
  title?: string,
): string {
  const heading = `# ${title || "Conversation export"}`;
  if (messages.length === 0)
    return `${heading}\n\n_No messages in this conversation._\n`;
  const bodies = messages.map(exportMessage);
  return `${heading}\n\n---\n\n${bodies.join("\n\n---\n\n")}\n`;
}

/** Download filename for an export: timestamped, filesystem-safe. */
export function exportFilename(now: Date = new Date()): string {
  const stamp = now.toISOString().replace(/[:.]/g, "-").slice(0, 19);
  return `alpha-conversation-${stamp}.md`;
}
