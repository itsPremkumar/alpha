// Contract tests for the workspace-sheet collectors.
//
// The sheet renders what the transcript holds, so these pin the
// split: file writes land on Changes with their parsed diff,
// browser/terminal calls on their own tabs, artifacts travel
// with the message that delivered them, and the total counts
// only reported rows — never an invented one.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

async function transpile(name, dir = "./") {
  const source = readFileSync(
    new URL(`${dir}${name}.ts`, import.meta.url),
    "utf8",
  );
  return ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  }).outputText;
}

function dataUrl(code) {
  return `data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`;
}

// workspace-sheet.ts imports its classifiers from ./agent-ui at
// runtime, so the sheet is loaded with that specifier rewritten to
// the transpiled agent-ui module. `../types/chat` needs no stub:
// the sheet imports it with `import type`, which transpiles away.
const agentUiCode = await transpile("agent-ui");
const sheetCode = (await transpile("workspace-sheet")).replaceAll(
  'from "./agent-ui"',
  `from "${dataUrl(agentUiCode)}"`,
);
const { collectWorkspaceContent } = await import(dataUrl(sheetCode));

const messages = [
  {
    id: "m1",
    role: "assistant",
    content: "Done.",
    createdAt: null,
    toolCalls: [
      {
        id: "c1",
        name: "shell",
        args: { command: "npm test" },
        output: "ok\nExit Code: 0",
        status: "completed",
      },
      {
        id: "c2",
        name: "write_file",
        args: { file_path: "a.ts" },
        output: "@@ -1,1 +1,2 @@\n-old\n+new\n+more",
        status: "completed",
      },
    ],
  },
  {
    id: "m2",
    role: "assistant",
    content: "Researched.",
    createdAt: null,
    toolCalls: [
      {
        id: "c3",
        name: "browser_navigate_and_inspect",
        args: { action: "navigate", url: "https://x.io" },
        output: "ok",
        status: "completed",
      },
      {
        id: "c4",
        name: "deep_web_search",
        args: { query: "q" },
        output: "[]",
        status: "completed",
      },
    ],
    artifacts: [
      { id: "a1", name: "report.md", type: "document", content: "# R" },
    ],
  },
  { id: "m3", role: "user", content: "Thanks", createdAt: null },
];

test("tool calls split into the sheet's tabs with their message attached", () => {
  const content = collectWorkspaceContent(messages);
  assert.equal(content.terminal.length, 1);
  assert.equal(content.terminal[0].messageId, "m1");
  assert.equal(content.terminal[0].call.name, "shell");
  assert.equal(content.browser.length, 1);
  assert.equal(content.browser[0].call.name, "browser_navigate_and_inspect");
  assert.equal(content.searches.length, 1);
  assert.equal(content.artifacts.length, 1);
  assert.equal(content.artifacts[0].messageId, "m2");
  assert.equal(content.artifacts[0].artifact.name, "report.md");
});

test("file changes carry the parsed diff and the path", () => {
  const content = collectWorkspaceContent(messages);
  assert.equal(content.files.length, 1);
  const file = content.files[0];
  assert.equal(file.path, "a.ts");
  assert.equal(file.variant, "file-write");
  assert.equal(file.hasDiff, true);
  assert.equal(file.added, 2);
  assert.equal(file.removed, 1);
});

test("a file write with no diff still lands on Changes, marked as such", () => {
  const content = collectWorkspaceContent([
    {
      id: "m",
      role: "assistant",
      content: "",
      createdAt: null,
      toolCalls: [
        {
          id: "c",
          name: "write_file",
          args: { file_path: "b.ts" },
          output: "written",
          status: "completed",
        },
      ],
    },
  ]);
  assert.equal(content.files.length, 1);
  assert.equal(content.files[0].hasDiff, false);
});

test("the total counts only reported rows", () => {
  const content = collectWorkspaceContent(messages);
  // 2 (m1) + 2 calls + 1 artifact (m2) = 5. The user message adds none.
  assert.equal(content.total, 5);
  assert.deepEqual(collectWorkspaceContent([]), {
    files: [],
    browser: [],
    terminal: [],
    reads: [],
    searches: [],
    other: [],
    artifacts: [],
    total: 0,
  });
});

test("unknown tools land on Other, never dropped", () => {
  const content = collectWorkspaceContent([
    {
      id: "m",
      role: "assistant",
      content: "",
      createdAt: null,
      toolCalls: [{ id: "c", name: "frobnicate", args: {} }],
    },
  ]);
  assert.equal(content.other.length, 1);
  assert.equal(content.total, 1);
});
