/**
 * Pure derivations for the Agent Workspace sheet.
 *
 * The sheet is the split-pane beside the chat: files the turn
 * changed, pages the browser touched, commands it ran, and
 * artifacts it delivered — collected from the `ChatMessage[]`
 * already on screen. No network, no second source: every row
 * links back to the message (and tool call) that reported it.
 * A category with nothing reported is an empty list, and the
 * sheet counts only what one of its tabs can render — a call
 * with no tab belongs to the transcript, not to this badge.
 */

import type { ArtifactItem, ChatMessage, ToolCall } from "../types/chat";
import { classifyTool, parseDiff, type ParsedDiff } from "./agent-ui";

/** One tool call plus the message that carries it. */
export interface WorkspaceCall {
  messageId: string;
  call: ToolCall;
}

/** One file change: the reported diff beside its provenance. */
export interface WorkspaceFileChange extends WorkspaceCall {
  path: string;
  variant: "file-write" | "file-edit";
  added: number;
  removed: number;
  diff: ParsedDiff;
  /** True when the output parsed as a diff at all. */
  hasDiff: boolean;
}

/** One artifact plus the message that delivered it. */
export interface WorkspaceArtifact {
  messageId: string;
  artifact: ArtifactItem;
}

export interface WorkspaceContent {
  files: WorkspaceFileChange[];
  browser: WorkspaceCall[];
  terminal: WorkspaceCall[];
  artifacts: WorkspaceArtifact[];
  /** Rows across the tabs. `0` means the sheet is empty. */
  total: number;
}

/**
 * Exactly one bucket per `WorkspaceTab`, and nothing else.
 *
 * The badge is summed from this list rather than written out beside
 * it, and `workspace-sheet.test.mjs` pins it against both the
 * interface and `countFor`. That three-way check is what stops the
 * badge counting a row no tab can render — the defect that let a
 * header advertise 68 items over tabs summing to 8.
 */
const ROW_KEYS = ["files", "browser", "terminal", "artifacts"] as const;

function filePathOf(call: ToolCall): string {
  const args = call.args ?? {};
  for (const key of [
    "file_path",
    "path",
    "filename",
    "file",
    "target",
    "location",
    "destination",
  ]) {
    const value = (args as Record<string, unknown>)[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

/**
 * Split a transcript's tool calls and artifacts into the sheet's
 * tabs. Reads only `toolCalls`/`artifacts` the run reported;
 * message bodies are never scanned.
 */
export function collectWorkspaceContent(
  messages: ChatMessage[],
): WorkspaceContent {
  const content: WorkspaceContent = {
    files: [],
    browser: [],
    terminal: [],
    artifacts: [],
    total: 0,
  };
  for (const message of messages) {
    for (const call of message.toolCalls ?? []) {
      const entry: WorkspaceCall = { messageId: message.id, call };
      switch (classifyTool(call.name)) {
        case "file-write":
        case "file-edit": {
          const diff = parseDiff(
            typeof call.output === "string" ? call.output : "",
          );
          content.files.push({
            ...entry,
            path: filePathOf(call),
            variant: classifyTool(call.name) as "file-write" | "file-edit",
            added: diff.added,
            removed: diff.removed,
            diff,
            hasDiff: diff.lines.length > 0,
          });
          break;
        }
        case "browser":
          content.browser.push(entry);
          break;
        case "terminal":
          content.terminal.push(entry);
          break;
        default:
          // Reads, searches, research and unclassified calls have no
          // tab in this sheet. They are reported in the transcript by
          // TurnSummaryStrip, so they are not collected here: a
          // bucket nothing renders would only let the badge advertise
          // rows the operator cannot open.
          break;
      }
    }
    for (const artifact of message.artifacts ?? []) {
      content.artifacts.push({ messageId: message.id, artifact });
    }
  }
  content.total = ROW_KEYS.reduce((sum, key) => sum + content[key].length, 0);
  return content;
}
