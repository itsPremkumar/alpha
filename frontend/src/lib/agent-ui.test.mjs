// Contract tests for the Agent Response UI derivations.
//
// These pin the readings the specialized cards depend on, each of
// which has a tempting wrong answer:
//   - a tool's kind comes from its own name, never a guess;
//   - an exit code is only the sandbox's `Exit Code: N` marker,
//     never a defaulted 0;
//   - a diff is only counted from real +/- lines, never invented;
//   - an absent argument, output or text renders nothing, never 0.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./agent-ui.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
  },
}).outputText;
const agentUi = await import(
  `data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`
);
const {
  classifyTool,
  isTerminalTool,
  isFileWriteTool,
  isSearchTool,
  isResearchTool,
  isBrowserTool,
  isReadTool,
  toolKindMeta,
  extractCommand,
  extractFilePath,
  extractQuery,
  extractDepth,
  extractUrl,
  extractBrowserAction,
  splitMcpName,
  domainOf,
  parseSearchResults,
  textCounts,
  summarizeTurnWork,
  parseExitCode,
  stripExitMarker,
  parseDiff,
  summarizeArgs,
  estimateTokens,
} = agentUi;

/* ------------------------- classifyTool ------------------------- */

test("classifies terminal tools by name", () => {
  for (const name of [
    "shell",
    "bash",
    "run_command",
    "powershell",
    "execute_command",
    "terminal",
  ]) {
    assert.equal(classifyTool(name), "terminal", name);
  }
  assert.equal(isTerminalTool("shell"), true);
});

test("classifies file write and edit tools by name", () => {
  assert.equal(classifyTool("write_file"), "file-write");
  assert.equal(classifyTool("create_file"), "file-write");
  assert.equal(classifyTool("str_replace"), "file-edit");
  assert.equal(classifyTool("apply_patch"), "file-edit");
  assert.equal(isFileWriteTool("write_file"), true);
  assert.equal(isFileWriteTool("str_replace"), true);
  // A terminal command is not a file write, even though both
  // can touch files.
  assert.equal(isFileWriteTool("shell"), false);
});

test("classifies search, read, web, agent, approval and mcp tools", () => {
  assert.equal(classifyTool("web_search"), "search");
  assert.equal(classifyTool("grep"), "search");
  assert.equal(isSearchTool("grep"), true);
  assert.equal(classifyTool("read_file"), "read");
  assert.equal(isReadTool("read_file"), true);
  assert.equal(classifyTool("web_fetch"), "web");
  assert.equal(classifyTool("message_agent"), "agent");
  assert.equal(classifyTool("request_approval"), "approval");
  assert.equal(classifyTool("mcp__github__create_issue"), "mcp");
});

test("an unknown or empty name resolves to generic, never a neighbour", () => {
  assert.equal(classifyTool(""), "generic");
  assert.equal(classifyTool(undefined), "generic");
  assert.equal(classifyTool("totally_new_tool"), "generic");
  // "totally_new_tool" contains no known substring, so it must
  // not be read as a search or a read.
  assert.equal(isSearchTool("totally_new_tool"), false);
  assert.equal(isReadTool("totally_new_tool"), false);
});

test("toolKindMeta labels every kind and never throws on generic", () => {
  assert.equal(toolKindMeta("terminal").label, "Terminal");
  assert.equal(toolKindMeta("file-write").label, "File write");
  assert.equal(toolKindMeta("research").label, "Research");
  assert.equal(toolKindMeta("browser").label, "Browser");
  assert.equal(toolKindMeta("read").label, "Read");
  assert.equal(toolKindMeta("generic").label, "Tool");
  // Every kind carries a tone the shared table can colour.
  for (const kind of [
    "terminal",
    "file-write",
    "file-edit",
    "search",
    "research",
    "browser",
    "read",
    "computer",
    "media",
    "web",
    "mcp",
    "agent",
    "approval",
    "generic",
  ]) {
    assert.ok(toolKindMeta(kind).tone, kind);
  }
});

test("classifies the real research, browser and read tools", () => {
  // Research wins over the `search` substring it contains.
  assert.equal(classifyTool("deep_research"), "research");
  assert.equal(classifyTool("deep_web_search"), "research");
  assert.equal(classifyTool("keyless_web_search"), "research");
  assert.equal(classifyTool("compile_five_pass_search"), "research");
  assert.equal(isResearchTool("deep_research"), true);
  // A plain web search stays a search.
  assert.equal(classifyTool("web_search"), "search");
  assert.equal(classifyTool("ast_grep_search"), "search");
  assert.equal(classifyTool("search_project_docs"), "search");
  assert.equal(isSearchTool("deep_research"), true);
  // Browser actions get their own kind.
  assert.equal(classifyTool("browser_navigate_and_inspect"), "browser");
  assert.equal(isBrowserTool("browser_navigate_and_inspect"), true);
  // Reads, including the sandbox hashline tools.
  assert.equal(classifyTool("hashline_read"), "read");
  assert.equal(classifyTool("present_file_tool"), "read");
  assert.equal(isReadTool("hashline_read"), true);
  // Computer and media kinds.
  assert.equal(classifyTool("desktop_screenshot_tool"), "computer");
  assert.equal(classifyTool("process_handle_tool"), "computer");
  assert.equal(classifyTool("view_image_tool"), "media");
});

/* --------------------- extractCommand / extractFilePath --------------------- */

test("extractCommand reads the command field the built-ins use", () => {
  assert.equal(extractCommand({ command: "npm test" }), "npm test");
  assert.equal(extractCommand({ cmd: "dir" }), "dir");
  assert.equal(extractCommand({ script: "echo hi" }), "echo hi");
  // No command-like field → empty, never a fabricated command.
  assert.equal(extractCommand({ path: "x" }), "");
  assert.equal(extractCommand(undefined), "");
  assert.equal(extractCommand(null), "");
});

test("extractFilePath reads the path field write/edit tools use", () => {
  assert.equal(extractFilePath({ file_path: "src/app.tsx" }), "src/app.tsx");
  assert.equal(extractFilePath({ path: "README.md" }), "README.md");
  assert.equal(extractFilePath({ filename: "a.py" }), "a.py");
  assert.equal(extractFilePath({}), "");
  assert.equal(extractFilePath(null), "");
});

/* --------------------- extractQuery / extractDepth / extractUrl --------------------- */

test("extractQuery reads the query field the search tools use", () => {
  assert.equal(extractQuery({ query: "auth bug" }), "auth bug");
  assert.equal(extractQuery({ topic: "oauth flows", depth: 3 }), "oauth flows");
  assert.equal(extractQuery({ pattern: "useState" }), "useState");
  assert.equal(extractQuery({ path: "x" }), "");
  assert.equal(extractQuery(null), "");
});

test("extractDepth reads the research depth verbatim, never defaulted", () => {
  assert.equal(extractDepth({ topic: "x", depth: 4 }), 4);
  // Absent is null, never a defaulted 3.
  assert.equal(extractDepth({ topic: "x" }), null);
  assert.equal(extractDepth(null), null);
});

test("extractUrl and extractBrowserAction read the browser fields", () => {
  assert.equal(
    extractUrl({ action: "navigate", url: "https://example.com" }),
    "https://example.com",
  );
  assert.equal(
    extractBrowserAction({ action: "click", x: 10, y: 20 }),
    "click",
  );
  assert.equal(extractUrl({}), "");
  assert.equal(extractBrowserAction({}), "");
});

/* --------------------- splitMcpName / domainOf --------------------- */

test("splitMcpName separates server from tool", () => {
  assert.deepEqual(splitMcpName("mcp__github__create_issue"), {
    server: "github",
    tool: "create_issue",
  });
  // A non-MCP name is never reshaped into a server claim.
  assert.deepEqual(splitMcpName("shell"), { server: null, tool: "shell" });
  assert.deepEqual(splitMcpName(""), { server: null, tool: "" });
});

test("domainOf reads the registrable host, or nothing", () => {
  assert.equal(domainOf("https://www.example.com/docs?q=1"), "example.com");
  assert.equal(domainOf("not a url"), "");
  assert.equal(domainOf(""), "");
});

/* --------------------- parseSearchResults / textCounts --------------------- */

test("parseSearchResults reads the result-object shape the search tools return", () => {
  const output = JSON.stringify([
    {
      title: "OAuth guide",
      url: "https://auth.example.com/guide",
      description: "How OAuth works",
      source: "example",
      position: 1,
    },
    {
      title: "Docs",
      url: "https://docs.example.org/x",
      description: "More docs",
    },
  ]);
  const results = parseSearchResults(output);
  assert.equal(results.length, 2);
  assert.equal(results[0].title, "OAuth guide");
  assert.equal(results[0].url, "https://auth.example.com/guide");
  assert.equal(results[0].snippet, "How OAuth works");
  assert.equal(results[0].source, "example");
  // A missing source falls back to the URL's domain.
  assert.equal(results[1].source, "docs.example.org");
});

test("parseSearchResults reads a {results:[...]} envelope too", () => {
  const results = parseSearchResults(
    JSON.stringify({
      status: "ok",
      results: [{ title: "T", url: "https://x.io" }],
    }),
  );
  assert.equal(results.length, 1);
  assert.equal(results[0].title, "T");
});

test("parseSearchResults yields [] for prose, never an empty result list claim", () => {
  assert.deepEqual(parseSearchResults("No results found for that query."), []);
  assert.deepEqual(parseSearchResults(""), []);
  assert.deepEqual(parseSearchResults(undefined), []);
  // Rows without title and URL are not results.
  assert.deepEqual(parseSearchResults(JSON.stringify([{ position: 1 }])), []);
});

test("textCounts measures lines and chars, null for absent", () => {
  assert.deepEqual(textCounts("a\nb\nc"), { lines: 3, chars: 5 });
  assert.equal(textCounts(""), null);
  assert.equal(textCounts(null), null);
});

test("parseSearchResults reads a plain URL list, one URL per line", () => {
  const results = parseSearchResults(
    "https://auth.example.com/pkce\nhttps://datatracker.ietf.org/doc/html/rfc7636\n",
  );
  assert.equal(results.length, 2);
  assert.equal(results[0].url, "https://auth.example.com/pkce");
  assert.equal(results[0].title, "https://auth.example.com/pkce");
  assert.equal(results[0].source, "auth.example.com");
  assert.equal(results[1].source, "datatracker.ietf.org");
});

test("parseSearchResults refuses a half-prose list — all lines must be URLs", () => {
  // One prose line fails the whole parse: half a result list
  // beside raw text would be two competing truths.
  assert.deepEqual(
    parseSearchResults("https://example.com/a\nsee also the docs"),
    [],
  );
  assert.deepEqual(
    parseSearchResults("https://example.com/a\nnot a url at all"),
    [],
  );
});

test("summarizeTurnWork counts per kind and totals reported diffs", () => {
  const summary = summarizeTurnWork([
    { name: "shell", output: "ok\nExit Code: 0" },
    { name: "shell", output: "fail\nExit Code: 1" },
    { name: "write_file", output: "@@ -1,2 +1,3 @@\n ctx\n-old\n+new\n+more" },
    { name: "deep_web_search", output: "[]" },
    { name: "hashline_read", output: "x" },
    { name: "message_agent", output: "sent" },
  ]);
  assert.equal(summary.commands, 2);
  assert.equal(summary.files, 1);
  assert.equal(summary.searches, 1);
  assert.equal(summary.reads, 1);
  assert.equal(summary.agents, 1);
  assert.equal(summary.other, 0);
  assert.equal(summary.changedFiles, 1);
  assert.equal(summary.added, 2);
  assert.equal(summary.removed, 1);
});

test("summarizeTurnWork reports null change totals when no diff parsed", () => {
  const summary = summarizeTurnWork([
    { name: "write_file", output: "File written successfully" },
    { name: "shell", output: "ok" },
  ]);
  assert.equal(summary.files, 1);
  assert.equal(summary.changedFiles, 0);
  // No measured changes is not measured zero.
  assert.equal(summary.added, null);
  assert.equal(summary.removed, null);
});

/* --------------------------- parseExitCode --------------------------- */

test("parseExitCode reads the sandbox's Exit Code marker", () => {
  assert.equal(parseExitCode("running tests\nExit Code: 0"), 0);
  assert.equal(parseExitCode("boom\nExit Code: 1"), 1);
  assert.equal(parseExitCode("Exit Code: 2"), 2);
  assert.equal(parseExitCode("Command exited with code 3"), 3);
});

test("parseExitCode returns null when the output carries no marker", () => {
  // Absent is not zero: a missing marker must not read as success.
  assert.equal(parseExitCode("some output"), null);
  assert.equal(parseExitCode(""), null);
  assert.equal(parseExitCode(undefined), null);
  assert.equal(parseExitCode("Exit Code:"), null);
});

test("stripExitMarker removes the marker line, keeps pure output", () => {
  assert.equal(stripExitMarker("hello\nExit Code: 0"), "hello");
  // An output with no marker is returned unchanged (trimmed).
  assert.equal(stripExitMarker("hello"), "hello");
});

/* ----------------------------- parseDiff ----------------------------- */

test("parseDiff counts additions and removals from a real diff", () => {
  const diff = parseDiff(
    "@@ -1,3 +1,4 @@\n context\n-removed\n+added\n+also added",
  );
  assert.equal(diff.added, 2);
  assert.equal(diff.removed, 1);
  // 1 hunk header + 1 context + 1 removal + 2 additions.
  assert.equal(diff.lines.length, 5);
  assert.equal(diff.lines[0].type, "hunk");
  assert.equal(diff.lines[2].type, "remove");
  assert.equal(diff.lines[3].type, "add");
});

test("parseDiff numbers lines from the hunk headers, Codex-style", () => {
  const diff = parseDiff(
    "@@ -218,2 +218,3 @@\n context line\n-removed line\n+added line\n+second add",
  );
  const [hunk, context, removed, added, secondAdd] = diff.lines;
  // Hunk headers carry no numbers of their own.
  assert.equal(hunk.oldNo, null);
  assert.equal(hunk.newNo, null);
  // Context advances both counters.
  assert.deepEqual([context.oldNo, context.newNo], [218, 218]);
  // A removal shows the old number only; additions the new.
  assert.deepEqual([removed.oldNo, removed.newNo], [219, null]);
  assert.deepEqual([added.oldNo, added.newNo], [null, 219]);
  assert.deepEqual([secondAdd.oldNo, secondAdd.newNo], [null, 220]);
});

test("parseDiff leaves numbers blank before the first hunk header", () => {
  const diff = parseDiff("+added with no hunk");
  assert.equal(diff.lines.length, 1);
  assert.equal(diff.lines[0].oldNo, null);
  assert.equal(diff.lines[0].newNo, null);
});

test("parseDiff yields an empty diff for non-diff output", () => {
  // A plain message is not a diff; the caller renders the raw text.
  const diff = parseDiff("File written successfully");
  assert.equal(diff.added, 0);
  assert.equal(diff.removed, 0);
  assert.equal(diff.lines.length, 0);
  assert.equal(parseDiff("").lines.length, 0);
  assert.equal(parseDiff(undefined).lines.length, 0);
});

/* --------------------------- summarizeArgs --------------------------- */

test("summarizeArgs renders compact key: value pairs", () => {
  const toolCall = {
    id: "1",
    name: "search",
    args: { query: "auth bug", limit: 10 },
  };
  const summary = summarizeArgs(toolCall);
  assert.ok(summary.includes("query: auth bug"), summary);
  assert.ok(summary.includes("limit: 10"), summary);
});

test("summarizeArgs truncates long values and caps the count", () => {
  const toolCall = {
    id: "1",
    name: "write_file",
    args: {
      file_path: "x",
      content: "a".repeat(200),
      extra: "y",
      another: "z",
    },
  };
  const summary = summarizeArgs(toolCall, 2, 10);
  // Only two key: value pairs survive the cap.
  assert.ok(!summary.includes("another"), summary);
  // The long content value is clipped.
  assert.ok(summary.includes("…"), summary);
});

test("summarizeArgs summarizes arrays and objects by type, skips nulls", () => {
  const toolCall = {
    id: "1",
    name: "t",
    args: { list: [1, 2, 3], obj: { a: 1 }, nothing: null, text: "ok" },
  };
  const summary = summarizeArgs(toolCall);
  assert.ok(summary.includes("list: 3 items"), summary);
  assert.ok(summary.includes("obj: {…}"), summary);
  assert.ok(summary.includes("text: ok"), summary);
  // null is skipped, never rendered as "nothing: null".
  assert.ok(!summary.includes("nothing"), summary);
});

test("summarizeArgs returns empty for empty or absent args", () => {
  assert.equal(summarizeArgs({ id: "1", name: "t", args: {} }), "");
  assert.equal(summarizeArgs({ id: "1", name: "t", args: undefined }), "");
});

/* --------------------------- estimateTokens --------------------------- */

test("estimateTokens approximates from length and labels nothing as measured", () => {
  // ~4 chars per token.
  assert.equal(estimateTokens("abcd"), 1);
  assert.equal(estimateTokens("a".repeat(40)), 10);
  // Absent or blank text is null, never 0.
  assert.equal(estimateTokens(""), null);
  assert.equal(estimateTokens("   "), null);
  assert.equal(estimateTokens(undefined), null);
});
