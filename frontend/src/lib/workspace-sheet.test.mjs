// Contract tests for the workspace-sheet collectors.
//
// The sheet renders what the transcript holds, so these pin the
// split: file writes land on Changes with their parsed diff,
// browser/terminal calls on their own tabs, artifacts travel
// with the message that delivered them — and the total counts
// only rows a tab can render, so the header badge can never
// advertise work the operator has no way to open.
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
  assert.equal(content.files.length, 1);
  assert.equal(content.browser.length, 1);
  assert.equal(content.browser[0].call.name, "browser_navigate_and_inspect");
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

test("the total equals exactly the rows the four tabs can show", () => {
  const content = collectWorkspaceContent(messages);
  // 1 file + 1 terminal + 1 browser + 1 artifact = 4.
  // `deep_web_search` has no tab, so the badge must not count it —
  // a total the operator cannot reach is the reported bug.
  assert.equal(content.total, 4);
  assert.equal(
    content.total,
    content.files.length +
      content.terminal.length +
      content.browser.length +
      content.artifacts.length,
  );
  assert.deepEqual(collectWorkspaceContent([]), {
    files: [],
    browser: [],
    terminal: [],
    artifacts: [],
    total: 0,
  });
});

test("a call with no tab is never counted in the badge", () => {
  const content = collectWorkspaceContent([
    {
      id: "m",
      role: "assistant",
      content: "",
      createdAt: null,
      toolCalls: [
        { id: "c1", name: "deep_web_search", args: { query: "q" } },
        { id: "c2", name: "read_file", args: { path: "a.ts" } },
        { id: "c3", name: "frobnicate", args: {} },
      ],
    },
  ]);
  // Reads, searches and unclassified calls are reported by
  // TurnSummaryStrip in the transcript; the sheet has no tab for
  // them, so it must not advertise a row it cannot render.
  assert.equal(content.files.length, 0);
  assert.equal(content.terminal.length, 0);
  assert.equal(content.browser.length, 0);
  assert.equal(content.artifacts.length, 0);
  assert.equal(content.total, 0);
});

test("every bucket the badge counts has a tab to render it in", () => {
  const source = readFileSync(
    new URL("./workspace-sheet.ts", import.meta.url),
    "utf8",
  );
  const iface = source.match(/export interface WorkspaceContent \{([\s\S]*?)\n\}/)[1];
  const buckets = [...iface.matchAll(/^\s*(\w+):/gm)]
    .map((m) => m[1])
    .filter((key) => key !== "total");

  const rowKeys = [
    ...source.match(/const ROW_KEYS = \[([^\]]+)\]/)[1].matchAll(/"(\w+)"/g),
  ].map((m) => m[1]);

  const view = readFileSync(
    new URL("../components/WorkspaceSheet.tsx", import.meta.url),
    "utf8",
  );
  const counted = [...view.matchAll(/case "(\w+)":\s*\n\s*return content\.(\w+)\.length;/g)]
    .map((m) => m[2]);

  assert.equal(
    counted.length,
    4,
    "every WorkspaceTab case must name the bucket it counts",
  );
  assert.deepEqual(
    [...buckets].sort(),
    [...counted].sort(),
    "WorkspaceContent may hold no bucket the sheet has no tab for",
  );
  assert.deepEqual(
    [...rowKeys].sort(),
    [...buckets].sort(),
    "the badge must be summed from every bucket, and no others",
  );
  // `total` is derived from ROW_KEYS, never written out a third time
  // where it could drift away from the tabs it summarizes.
  assert.match(
    source,
    /content\.total = ROW_KEYS\.reduce/,
    "total must be derived from ROW_KEYS rather than listed inline",
  );
});
