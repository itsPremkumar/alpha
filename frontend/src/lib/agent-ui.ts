/**
 * Pure derivations for the Agent Response UI (ARX).
 *
 * Everything here is a *reading* of what the run already reported — a
 * tool name, an args object, an output string. No function predicts,
 * invents or defaults a value: an absent exit code, an absent path or
 * an empty diff returns `null`/`""` and the caller renders nothing.
 *
 * The tool-name vocabularies are matched against the names the Gateway
 * actually stamps (see `alpha.tools.builtins`); an unrecognized name
 * falls through to `generic`, which is the honest "we don't know what
 * kind of tool this is" answer rather than a guess.
 */

import type { ToolCall } from "../types/chat";

/** The shape of a tool call, for the cards that render it. */
export type ToolKind =
  | "terminal"
  | "file-write"
  | "file-edit"
  | "search"
  | "research"
  | "browser"
  | "read"
  | "computer"
  | "media"
  | "web"
  | "mcp"
  | "agent"
  | "approval"
  | "generic";

/** Per-kind presentation meta. The icon is chosen by the component. */
export interface ToolKindMeta {
  label: string;
  /** Tailwind tone for the kind's accent (icon + header chip). */
  tone:
    | "sky"
    | "emerald"
    | "violet"
    | "amber"
    | "orange"
    | "cyan"
    | "blue"
    | "fuchsia"
    | "rose"
    | "muted";
}

const TERMINAL_NAMES = [
  "shell",
  "bash",
  "terminal",
  "run_command",
  "run_shell",
  "exec",
  "execute_command",
  "command",
  "powershell",
  "cmd",
  "sh",
  "zsh",
  "run_terminal_command",
  "terminal_command",
];
const FILE_WRITE_NAMES = [
  "write_file",
  "create_file",
  "save_file",
  "write",
  "file_write",
  "make_file",
  "touch",
  "create_document",
];
const FILE_EDIT_NAMES = [
  "str_replace",
  "str_replace_editor",
  "edit_file",
  "file_edit",
  "replace",
  "apply_patch",
  "patch",
  "insert",
  "multiedit",
  "multi_edit",
];
const SEARCH_NAMES = [
  "search",
  "grep",
  "rg",
  "glob",
  "find",
  "code_search",
  "semantic_search",
  "search_code",
  "exa",
  "search_files",
  "list_files",
  "ast_grep_search",
  "catalog_tool_search",
  "session_search",
  "search_session_memory",
  "search_project_docs",
  "query_knowledge_graph",
  "generate_repo_map",
  "blackboard_query",
];
/** Long investigations: topic + depth rather than query + limit. */
const RESEARCH_NAMES = [
  "deep_research",
  "deep_web_search",
  "keyless_web_search",
  "compile_five_pass_search",
];
const BROWSER_NAMES = [
  "browser_navigate",
  "browser_supervisor",
  "browser_use",
  "navigate_and_inspect",
  "visual_verify",
  "web_ui_visual",
];
const READ_NAMES = [
  "read",
  "read_file",
  "cat",
  "file_read",
  "open_file",
  "less",
  "hashline_read",
  "present_file",
  "list_uploaded_files",
];
const COMPUTER_NAMES = [
  "desktop_screenshot",
  "desktop_inspect",
  "desktop_mouse",
  "desktop_keyboard",
  "desktop_window",
  "desktop_system",
  "os_computer",
  "computer_worker",
  "execute_sandboxed_computer",
  "process_handle",
];
const MEDIA_NAMES = ["view_image", "screenshot", "image"];
const WEB_NAMES = ["fetch", "web_fetch", "http_get", "url", "browse", "scrape"];
const AGENT_NAMES = [
  "message_agent",
  "task",
  "delegate",
  "subagent",
  "agent",
  "spawn_agent",
];
const APPROVAL_NAMES = [
  "request_approval",
  "approval",
  "human_approval",
  "confirm",
];

/**
 * Classify a tool by its own name. Precedence is deliberate and
 * order-sensitive — several real names contain each other's
 * substrings (`deep_research` contains `search`,
 * `desktop_screenshot` contains `screenshot`), so the longer,
 * more specific families win. Unknown names resolve to
 * `generic`, never to a neighbouring kind.
 */
export function classifyTool(name: string | undefined | null): ToolKind {
  const raw = (name || "").toLowerCase();
  if (!raw) return "generic";
  // Short needles (`sh`, `cmd`, `zsh`) match on word boundaries
  // only: a bare substring read `hashline_read` as a shell because
  // of the `sh` in `hashline`. Longer needles stay substrings.
  const has = (...needles: string[]) =>
    needles.some((n) =>
      n.length > 3
        ? raw.includes(n)
        : new RegExp(`(^|[^a-z0-9])${n}([^a-z0-9]|$)`).test(raw),
    );

  if (has(...APPROVAL_NAMES)) return "approval";
  // Computer-action tools before terminal: `execute_sandboxed_computer_action`
  // contains `exec`, but it drives computer actions, not a shell.
  if (has(...COMPUTER_NAMES)) return "computer";
  if (has(...TERMINAL_NAMES)) return "terminal";
  if (has(...FILE_WRITE_NAMES)) return "file-write";
  if (has(...FILE_EDIT_NAMES)) return "file-edit";
  if (has(...AGENT_NAMES)) return "agent";
  if (has(...RESEARCH_NAMES)) return "research";
  if (has(...BROWSER_NAMES)) return "browser";
  if (has(...WEB_NAMES)) return "web";
  if (has(...SEARCH_NAMES)) return "search";
  if (has(...READ_NAMES)) return "read";
  if (has(...MEDIA_NAMES)) return "media";
  // MCP tools arrive as `mcp__<server>__<tool>`; the prefix is the
  // only reliable signal, so it is checked last and explicitly.
  if (raw.startsWith("mcp__") || raw.startsWith("mcp:")) return "mcp";
  return "generic";
}

export function isTerminalTool(name?: string | null): boolean {
  return classifyTool(name) === "terminal";
}
export function isFileWriteTool(name?: string | null): boolean {
  const kind = classifyTool(name);
  return kind === "file-write" || kind === "file-edit";
}
export function isSearchTool(name?: string | null): boolean {
  const kind = classifyTool(name);
  return kind === "search" || kind === "research";
}
export function isResearchTool(name?: string | null): boolean {
  return classifyTool(name) === "research";
}
export function isBrowserTool(name?: string | null): boolean {
  return classifyTool(name) === "browser";
}
export function isReadTool(name?: string | null): boolean {
  return classifyTool(name) === "read";
}

/** Presentation meta per kind. One table so every card colours alike. */
export function toolKindMeta(kind: ToolKind): ToolKindMeta {
  switch (kind) {
    case "terminal":
      return { label: "Terminal", tone: "sky" };
    case "file-write":
      return { label: "File write", tone: "emerald" };
    case "file-edit":
      return { label: "File edit", tone: "violet" };
    case "search":
      return { label: "Search", tone: "amber" };
    case "research":
      return { label: "Research", tone: "orange" };
    case "browser":
      return { label: "Browser", tone: "blue" };
    case "read":
      return { label: "Read", tone: "cyan" };
    case "computer":
      return { label: "Computer", tone: "muted" };
    case "media":
      return { label: "Media", tone: "fuchsia" };
    case "web":
      return { label: "Web", tone: "blue" };
    case "mcp":
      return { label: "MCP", tone: "fuchsia" };
    case "agent":
      return { label: "Agent", tone: "rose" };
    case "approval":
      return { label: "Approval", tone: "amber" };
    default:
      return { label: "Tool", tone: "muted" };
  }
}

/**
 * The command line a shell tool was asked to run, or `""` when the
 * args carry no command-like field. Reads the common field names the
 * built-ins use; never fabricates a command.
 */
export function extractCommand(
  args: Record<string, unknown> | undefined | null,
): string {
  if (!args) return "";
  for (const key of [
    "command",
    "cmd",
    "shell",
    "script",
    "input",
    "command_line",
  ]) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

/**
 * The file path a write/edit tool touched, or `""` when the args carry
 * no path-like field.
 */
export function extractFilePath(
  args: Record<string, unknown> | undefined | null,
): string {
  if (!args) return "";
  for (const key of [
    "file_path",
    "path",
    "filename",
    "file",
    "target",
    "location",
    "destination",
  ]) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

/**
 * The sandbox's exit-code marker, as written at the tail of a shell
 * tool's output. Same grammar `runs-inspector.ts` reads, so the chat
 * card and the run inspector never disagree about what "the exit code"
 * is. `null` when the output carries no marker.
 */
const EXIT_MARKER =
  /(?:^|\n)Exit Code: (-?\d+)\s*$|^Command exited with code (-?\d+)\s*$/;

export function parseExitCode(
  output: string | undefined | null,
): number | null {
  if (typeof output !== "string" || !output) return null;
  const match = EXIT_MARKER.exec(output.trim());
  if (!match) return null;
  const parsed = Number(match[1] ?? match[2]);
  return Number.isInteger(parsed) ? parsed : null;
}

/** Strip the exit-code marker line so the terminal body shows pure output. */
export function stripExitMarker(output: string): string {
  return output
    .replace(/(?:^|\n)Exit Code: -?\d+\s*$/, "")
    .replace(/^Command exited with code -?\d+\s*$/, "")
    .trim();
}

/** One line of a parsed diff. */
export interface DiffLine {
  type: "add" | "remove" | "context" | "hunk";
  text: string;
  /**
   * 1-based old/new file line numbers, counted from the `@@`
   * hunk headers Codex-style diffs carry. `null` when the diff
   * has no hunk headers — line numbers are then not guessed.
   */
  oldNo: number | null;
  newNo: number | null;
}

export interface ParsedDiff {
  added: number;
  removed: number;
  lines: DiffLine[];
}

/**
 * Parse a unified-diff body (the shape `str_replace` / `apply_patch`
 * results carry). Counts `+`/`-` lines; `@@` hunk headers and `\`
 * "no newline" markers are classified but not counted. An output that
 * is not a diff (no `+`/`-`/`@@` lines) yields an empty diff — the
 * caller then renders the raw output instead of an empty diff view.
 *
 * Hunk headers (`@@ -218,2 +218,3 @@`) seed the old/new line
 * counters, so each line carries its file line numbers the way
 * Codex-style diffs do. Lines before the first hunk header carry
 * `null` numbers rather than counted-from-1 guesses.
 */
export function parseDiff(output: string | undefined | null): ParsedDiff {
  const lines: DiffLine[] = [];
  let added = 0;
  let removed = 0;
  if (typeof output !== "string" || !output) return { added, removed, lines };

  let sawDiffLine = false;
  let oldNo: number | null = null;
  let newNo: number | null = null;
  for (const rawLine of output.split("\n")) {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(rawLine);
    if (hunk || rawLine.startsWith("@@")) {
      sawDiffLine = true;
      if (hunk) {
        oldNo = Number(hunk[1]);
        newNo = Number(hunk[2]);
      }
      lines.push({ type: "hunk", text: rawLine, oldNo: null, newNo: null });
    } else if (rawLine.startsWith("+")) {
      sawDiffLine = true;
      added += 1;
      lines.push({ type: "add", text: rawLine.slice(1), oldNo: null, newNo });
      if (newNo !== null) newNo += 1;
    } else if (rawLine.startsWith("-")) {
      sawDiffLine = true;
      removed += 1;
      lines.push({
        type: "remove",
        text: rawLine.slice(1),
        oldNo,
        newNo: null,
      });
      if (oldNo !== null) oldNo += 1;
    } else if (rawLine.startsWith("\\")) {
      // "\ No newline at end of file" — metadata, not content.
      continue;
    } else {
      lines.push({ type: "context", text: rawLine, oldNo, newNo });
      if (oldNo !== null) oldNo += 1;
      if (newNo !== null) newNo += 1;
    }
  }
  return sawDiffLine
    ? { added, removed, lines }
    : { added: 0, removed: 0, lines: [] };
}

/**
 * A compact `key: value` summary of a tool call's arguments, for the
 * collapsed card header. Long values are truncated so a 20-call turn
 * stays one line each. Keys are read in a stable order; only
 * string/number/boolean scalars are shown — nested objects and arrays
 * are summarized by their type, never dumped.
 */
export function summarizeArgs(
  toolCall: ToolCall,
  maxValues = 3,
  maxValueChars = 48,
): string {
  const args = toolCall.args;
  if (!args || typeof args !== "object") return "";
  const entries = Object.entries(args);
  if (entries.length === 0) return "";

  const parts: string[] = [];
  for (const [key, value] of entries) {
    if (parts.length >= maxValues) break;
    let text: string;
    if (typeof value === "string") text = value;
    else if (typeof value === "number" || typeof value === "boolean")
      text = String(value);
    else if (Array.isArray(value))
      text = `${value.length} item${value.length === 1 ? "" : "s"}`;
    else if (value && typeof value === "object") text = "{…}";
    else continue; // null / undefined / function — nothing honest to say
    const trimmed = text.trim();
    if (!trimmed) continue;
    const clipped =
      trimmed.length > maxValueChars
        ? `${trimmed.slice(0, maxValueChars)}…`
        : trimmed;
    parts.push(`${key}: ${clipped}`);
  }
  return parts.join(" · ");
}

/**
 * A rough token estimate for a text block, for the thinking card's
 * "≈ N tokens" hint. ~4 chars per token is the standard approximation;
 * the figure is always labelled approximate and is never presented as
 * a measured count.
 */
export function estimateTokens(text: string | undefined | null): number | null {
  if (typeof text !== "string" || !text.trim()) return null;
  return Math.max(1, Math.round(text.trim().length / 4));
}

/**
 * The search query (or research topic) a search-family tool was
 * asked about, or `""` when the args carry none. Reads the field
 * names the real tools use — `query` on the search tools,
 * `topic` on `deep_research`, `pattern` on the code searchers.
 */
export function extractQuery(
  args: Record<string, unknown> | undefined | null,
): string {
  if (!args) return "";
  for (const key of ["query", "q", "topic", "pattern", "question", "prompt"]) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

/**
 * The investigation depth a research tool was asked for
 * (`deep_research`'s 1–5 scale), or `null` when the args
 * carry none. Returned verbatim — never clamped, never defaulted.
 */
export function extractDepth(
  args: Record<string, unknown> | undefined | null,
): number | null {
  const value = args?.depth;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * The URL a browser/web tool was pointed at, or `""` when the
 * args carry none.
 */
export function extractUrl(
  args: Record<string, unknown> | undefined | null,
): string {
  if (!args) return "";
  for (const key of ["url", "href", "link", "uri"]) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

/**
 * The browser action verb (`navigate`, `click`, …), or `""`
 * when the args carry none.
 */
export function extractBrowserAction(
  args: Record<string, unknown> | undefined | null,
): string {
  const value = args?.action;
  return typeof value === "string" && value.trim() ? value : "";
}

/**
 * Split an MCP tool name into its server and tool halves.
 * `mcp__github__create_issue` → `{ server: "github", tool: "create_issue" }`.
 * Anything else yields `{ server: null, tool: <the name> }` — the
 * name is never reshaped into a claim about a server.
 */
export function splitMcpName(name: string | undefined | null): {
  server: string | null;
  tool: string;
} {
  const raw = name || "";
  const match =
    /^mcp__([^_]+)__(.+)$/.exec(raw) ?? /^mcp:([^:]+):(.+)$/.exec(raw);
  if (!match) return { server: null, tool: raw };
  return { server: match[1], tool: match[2] };
}

/** One parsed search result: title, URL, snippet, source. */
export interface SearchResult {
  title: string;
  url: string;
  snippet: string;
  source: string;
}

/** Registrable host of a result URL, or `""` when unparseable. */
export function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

/**
 * Parse a search-family tool's reported output into results.
 *
 * The real tools return JSON-ish payloads carrying result
 * objects with `title`/`url`/`description`/`source` fields
 * (see `keyless_web_search_tool.py`). This reads that shape:
 * a JSON array, or an object with a `results` array. It also
 * reads a plain URL list — one http(s) URL per line, the shape
 * parallel-search UIs print — but ONLY when every non-empty
 * line is a URL; a single prose line fails the whole parse
 * rather than rendering half a result list beside raw text.
 * Anything else yields `[]`, and the caller renders the raw
 * output instead of an empty result list.
 */
export function parseSearchResults(
  output: string | undefined | null,
): SearchResult[] {
  if (typeof output !== "string" || !output.trim()) return [];
  const fromObjects = (list: unknown[]): SearchResult[] => {
    const results: SearchResult[] = [];
    for (const item of list) {
      if (!item || typeof item !== "object") continue;
      const row = item as Record<string, unknown>;
      const title =
        typeof row.title === "string"
          ? row.title
          : typeof row.name === "string"
            ? row.name
            : "";
      const url =
        typeof row.url === "string"
          ? row.url
          : typeof row.link === "string"
            ? row.link
            : "";
      const snippet =
        typeof row.description === "string"
          ? row.description
          : typeof row.snippet === "string"
            ? row.snippet
            : "";
      const source =
        typeof row.source === "string" ? row.source : domainOf(url);
      if (!title && !url) continue;
      results.push({ title: title || url, url, snippet, source });
    }
    return results;
  };

  let parsed: unknown;
  try {
    parsed = JSON.parse(output);
  } catch {
    parsed = undefined;
  }
  if (parsed !== undefined) {
    const list = Array.isArray(parsed)
      ? parsed
      : parsed &&
          typeof parsed === "object" &&
          Array.isArray((parsed as Record<string, unknown>).results)
        ? ((parsed as Record<string, unknown>).results as unknown[])
        : [];
    return fromObjects(list);
  }
  // Line-delimited JSON objects.
  const rows: unknown[] = [];
  for (const line of output.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("{")) continue;
    try {
      rows.push(JSON.parse(trimmed));
    } catch {
      /* a non-JSON line is not a result */
    }
  }
  if (rows.length > 0) return fromObjects(rows);
  // Plain URL list: every non-empty line must be an http(s) URL.
  const lines = output
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  if (
    lines.length > 0 &&
    lines.every((line) => /^https?:\/\/\S+$/.test(line))
  ) {
    return lines.map((url) => ({
      title: url,
      url,
      snippet: "",
      source: domainOf(url),
    }));
  }
  return [];
}

/** Line/char counts of a text body, for read-block headers. */
export function textCounts(
  text: string | undefined | null,
): { lines: number; chars: number } | null {
  if (typeof text !== "string" || !text) return null;
  return { lines: text.split("\n").length, chars: text.length };
}

/**
 * One turn's work, summarised for the summary strip.
 *
 * Counts calls per display category and totals the `+N/−N`
 * across every diff the turn actually reported. A turn with
 * no parsed diffs carries `null` change totals rather than
 * `+0 −0` — "no measured changes" is not "measured zero".
 */
export interface TurnWorkSummary {
  commands: number;
  files: number;
  searches: number;
  reads: number;
  browser: number;
  agents: number;
  other: number;
  changedFiles: number;
  added: number | null;
  removed: number | null;
}

export function summarizeTurnWork(
  toolCalls: Array<{ name?: string | null; output?: string }>,
): TurnWorkSummary {
  const summary: TurnWorkSummary = {
    commands: 0,
    files: 0,
    searches: 0,
    reads: 0,
    browser: 0,
    agents: 0,
    other: 0,
    changedFiles: 0,
    added: null,
    removed: null,
  };
  let added = 0;
  let removed = 0;
  let sawDiff = false;
  for (const call of toolCalls) {
    const kind = classifyTool(call.name);
    switch (kind) {
      case "terminal":
        summary.commands += 1;
        break;
      case "file-write":
      case "file-edit": {
        summary.files += 1;
        const diff = parseDiff(
          typeof call.output === "string" ? call.output : "",
        );
        if (diff.lines.length > 0) {
          sawDiff = true;
          summary.changedFiles += 1;
          added += diff.added;
          removed += diff.removed;
        }
        break;
      }
      case "search":
      case "research":
        summary.searches += 1;
        break;
      case "read":
        summary.reads += 1;
        break;
      case "browser":
        summary.browser += 1;
        break;
      case "agent":
        summary.agents += 1;
        break;
      default:
        summary.other += 1;
        break;
    }
  }
  if (sawDiff) {
    summary.added = added;
    summary.removed = removed;
  }
  return summary;
}
