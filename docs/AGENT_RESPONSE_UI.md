# Agent Response UI (ARX)

**Status: shipped in `main`.** This is the design record behind the shipped feature, not an outstanding plan.

## 1. Problem

Alpha's chat transcript renders an assistant turn as: a flat markdown answer, a
collapsed "Thought process" mono block, a collapsed "Used N tools" receipt, an
inline plan, and (while streaming) a pinned status line. The *work* the agent
did — the commands it ran, the files it wrote, the searches, the subagents it
delegated to — is folded into raw JSON inside generic tool pills. A user cannot
see at a glance **what the agent did, what it ran, or copy the command it used**.

Every leading agent UI (Claude Code, Cursor, GitHub Copilot, OpenAI Codex CLI,
OpenClaw, assistant-ui, create-ui, agent-swarm, AgentGUI) converged on the same
set of surfaces. This plan brings those surfaces to Alpha's chat, inside the
repo's honesty-first contract.

## 2. Research synthesis — what the best agent chat UIs do

| Surface | Claude Code | Cursor | Copilot | Codex CLI | OpenClaw | assistant-ui |
| --- | --- | --- | --- | --- | --- | --- |
| Thinking | collapsible, amber, effort chip | collapsible "Reasoning" | collapsible reasoning | thinking view control | — | ReasoningPanel (step timeline) |
| Plan / todos | Ctrl+T checklist, pending/active/complete | Plan mode virtual file | — | TODO visibility | — | Chain-of-thought steps |
| Tool calls | tool row `name(args)`, expandable | tool cards, collapsible | tool-call boxes, real-time | structured tool cards | tool cards | ToolUIPart lifecycle states |
| Terminal | `Bash(cmd)` + output + exit | terminal command approval | collapsible terminal tool calls | shell executor, exit code | terminal panel | TerminalBlock (streams lines, exit status) |
| File writes | full diff, expandable, open in editor | diff view, accept/reject | — | apply_patch diffs | file tree | — |
| Subagents | `agent(task)` row, nested transcript | subagent avatars | Agent Debug panel | subagents w/ findings | child sessions, roster | — |
| Swarm | — | — | — | — | team mode, agent cards | — |
| Artifacts | — | artifact panel | artifacts panel (tree, grouped) | — | — | Artifact preview + panel |
| Code blocks | copy button + check | copy + check, 40+ langs | copy math source | syntax highlighting request | syntax highlighting | copy on every block |
| Message actions | copy/rate/regenerate | revert checkpoint | copy/revert/fork | /undo, /history | — | copy/revert/fork |

**Convergent patterns this plan adopts:**

1. **Progressive disclosure** — every rich block is collapsed to one honest line
   by default and expands on demand. Quiet by default, loud while running.
2. **Kind-specialised tool cards** — a shell command, a file write, a search and
   a web fetch are *different shapes*, not one generic pill. The icon, the
   collapsed summary and the expanded body all follow the kind.
3. **Copy is a first-class affordance** — every command, code block and file
   path has a one-click copy with a transient check.
4. **Honest states** — a call with no result is "running" (live) or "no result
   reported" (history), never a green check. Exit codes, durations and token
   counts are shown only when measured; absent renders *nothing*, never `0`.
5. **The stream is the source of truth** — durations are client-observed
   (`ToolTiming`), phases describe the newest reported state, and a streaming
   turn never looks finished.

## 3. What we build

New pure-derivation module + eight presentational components, wired into
`MessageItem`. Everything follows the repo rule: **derivation in `lib/`,
presentation in `components/`, honesty pinned by `*.test.mjs`.**

### 3.1 `lib/agent-ui.ts` (pure, no React)
- `classifyTool(name)` → `ToolKind` (`terminal | file-write | file-edit | search | read | web | mcp | agent | approval | generic`), from the tool's own name.
- `isTerminalTool` / `isFileWriteTool` / `isSearchTool` / `isReadTool` predicates.
- `extractCommand(args)` — the command string from a shell tool's args.
- `extractFilePath(args)` — the path from a write/edit tool's args.
- `parseExitCode(output)` — `number | null` from the sandbox's `Exit Code: N` tail (same marker `runs-inspector.ts` uses).
- `parseDiff(output)` — `{ added, removed, lines: DiffLine[] }` from a unified-diff body.
- `summarizeArgs(toolCall)` — compact `key: value` summary for a collapsed card.
- `estimateTokens(text)` — rough token estimate for the thinking block.
- `toolKindMeta(kind)` — `{ label, tone }` per kind (icon chosen in the component).

### 3.2 Components (`components/agent-ui/`)
- **`ThinkingBlock`** — amber-accented, gradient bar, shimmer while streaming, token estimate, markdown-rendered body, collapsible.
- **`TerminalBlock`** — dark terminal surface, command line with **Copy command**, streaming output lines, **exit-code badge** (0 green / non-zero red), running spinner + elapsed.
- **`FileWriteBlock`** — file-type icon + path with **Copy path**, `+N / −N` stat, line-by-line **diff view** (green/red), language tag.
- **`ToolCallCard`** — kind icon, name, collapsed args summary, status chip (7 honest states), duration, expandable args + output with **Copy output**.
- **`SubagentCard`** — agent name, model badge, task, status, steps, tokens, expandable nested transcript.
- **`SwarmBoard`** — when ≥2 subagents run in parallel, a wave board: parallel columns with a connecting spine, live/finished per column.
- **`ArtifactStrip`** — renders `message.artifacts` (currently typed but never shown) as chips that expand to a preview.
- **`CodeBlock`** — ReactMarkdown code-block override: language label + **Copy** button with check feedback.
- **`ResponseHeader`** — deferred. A per-message model/duration/token chip row
  needs wire fields `ChatMessage` does not carry (model, run duration, token
  usage live elsewhere in `ChatView`). Inventing them per message would violate
  the honesty contract, so this waits for a wire change rather than shipping
  with guessed data.

### 3.3 Wiring
- `MessageItem.tsx` swaps its inline thinking/ToolGroup blocks for the new components (keeping `TaskList`, `HumanApprovalCard`, `SubagentList` contracts intact), and mounts `ResponseHeader` + `ArtifactStrip`.
- Markdown gains the `CodeBlock` override via `ReactMarkdown`'s `components` prop.

## 4. Honesty contract (non-negotiable)
- Absent optional field → renders nothing / muted "not reported", never `0` or a green check.
- `toolStatusView` stays the single status authority; the new cards consume it, never re-derive it.
- Durations are client-observed; a restored-from-history call shows no duration.
- Exit code shown only when the output actually carries the marker.
- Diff `+N/−N` computed from the diff body the run reported, never guessed.
- A streaming turn keeps its trailing indicator.

## 5. Verification
- `node node_modules/typescript\bin\tsc --noEmit` → 0 errors.
- `node --test src/lib/agent-ui.test.mjs` → green (kind classification, exit-code parsing, diff parsing, args summarisation, token estimate).
- Browser screenshot of a real chat turn showing every block, then fix what renders wrong.

## 6. Round 3 — full tool-inventory coverage (deep research)

Researched against the repo's own ground truth (`contracts/feature_manifest.json`,
136 tools) plus assistant-ui's WebSearch/Sources elements, Fazm's system-event
card, and the Copilot-cli collapsible-sections proposal:

- **Real search family**: `keyless_web_search`, `deep_web_search`,
  `deep_research` (`topic`/`depth`/`max_sources`), `compile_five_pass_search`,
  `session_search_tool`, `search_session_memory`, `search_project_docs`,
  `ast_grep_search`, `query_knowledge_graph`, `catalog_tool_search`,
  `generate_repo_map` — outputs carry `{title, url, description, source,
  position}` result objects.
- **Real browser tool**: `browser_navigate_and_inspect(action, url, x, y,
  goal)` — actions navigate/dom/click/screenshot/table/next/run/parse/close.
- **Real read tools**: `hashline_read(file_path)`, `present_file_tool`,
  `list_uploaded_files_tool` — sandbox-mapped absolute paths.
- **Computer/media**: `desktop_*`, `os_computer_tool`,
  `execute_sandboxed_computer_action`, `process_handle_tool`, `view_image_tool`.
- **MCP**: `mcp__<server>__<tool>` prefix grammar.

Built: `SearchResultsBlock` (query/topic pill, depth badge, "Searching…" /
"Read N sources", domain-avatar result rows), `BrowserBlock` (action-verb
chip, linked live URL, click coords), `ReadBlock` (path, language, line
count), MCP server chip in `ToolCallCard`, shared `DiffView` extracted from
`FileWriteBlock`. The classifier now orders computer-before-terminal and
boundary-matches short needles — both fixed after the new tests caught real
misclassifications (`hashline_read` read as shell via `sh`, `view_image_tool`
read as read via `view`).

## 7. Round 4 — Codex diff details (from the user's screenshot)

The Codex turn screenshot showed two things our diff lacked: **gutter line
numbers** (`218`, `219` beside the code) and a **copy button on the diff**.
`parseDiff` now counts old/new line numbers from `@@` hunk headers (blank
gutter before the first header — never guessed), `DiffView` renders the
old/new gutter, and the file block's open panel carries a stat + Copy-diff
header. Verified against a `@@ -218,2 +218,2 @@` fixture rendering
`218 218 context / 219 − removed / 219 + added`, matching the screenshot.

## 8. Round 5 — screenshot-driven extras (parallel-search URLs, turn summary)

From the user's two new screenshots (parallel web-search URL lists;
Codex's `32 files changed +369 −103` pill and "Edited files, ran commands"):

- `parseSearchResults` also reads a **plain URL list** — but only when
  *every* non-empty line is an http(s) URL; one prose line fails the
  whole parse rather than rendering half a list beside raw text.
- `summarizeTurnWork` + `TurnSummaryStrip`: per-kind counts plus the
  `+N/−N` totalled across reported diffs (`null` totals when no diff
  parsed — never `+0 −0`), rendered above a turn's blocks.

## 9. Round 6 — Agent Workspace split-pane (Claude-Artifacts pattern)

Research: Claude's split pane (thread = reasoning trail, right pane = session
product), VS Code Agents window (Changes/Files side panel beside chat),
ChatGPT Canvas. Built `WorkspaceSheet`: a right-docked tabbed sheet —
**Changes** (file diffs), **Terminal** (commands + exits), **Browser**
(pages + observations), **Artifacts** (deliverables) — every tab reusing the
transcript's own blocks so the surfaces cannot disagree. Rows derive from
`collectWorkspaceContent(messages)`: no network, no second source. Auto-opens
on a turn's first workspace content (explicit close wins per thread; thread
switch re-arms); toolbar toggle carries the live item count.

## 7. Out of scope (deliberately)- Structured content-parts on `ChatMessage` (needs a wire change) — the flat
  `content: string` stays; rich blocks hang off the existing fields.
- A side artifact *panel* (portaled) — chips expand inline for now.
- Syntax highlighting library — no new dependency; code blocks get copy +
  language label, styling stays in `.response-prose`.
