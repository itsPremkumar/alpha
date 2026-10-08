// Contract tests for transcript search and transcript export.
//
// The pins that matter:
//   - a blank query matches nothing, never everything;
//   - a hit names the field that matched, never a guessed one;
//   - the export omits unreported fields instead of inventing them;
//   - tool output is indented, not fenced, so embedded fences
//     cannot break the document.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

async function loadModule(name) {
  const source = readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8");
  const code = ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  }).outputText;
  return import(
    `data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`
  );
}

const search = await loadModule("transcript-search");
const { findTranscriptMatches, matchFieldLabel } = search;
const exp = await loadModule("transcript-export");
const { exportToolCall, exportMessage, exportTranscript, exportFilename } = exp;

/* ------------------------- findTranscriptMatches ------------------------- */

const msgs = [
  { id: "m1", content: "How do I fix the login bug?" },
  {
    id: "m2",
    thinking: "The login flow needs a null check",
    toolCalls: [{ name: "shell", output: "npm test login" }],
    content: "Fixed.",
  },
  {
    id: "m3",
    toolCalls: [{ name: "code_search", output: "nothing" }],
    todos: [{ content: "Fix login validation" }],
  },
  { id: "m4", content: "Unrelated message" },
];

test("a blank query matches nothing, never the whole transcript", () => {
  assert.deepEqual(findTranscriptMatches(msgs, ""), []);
  assert.deepEqual(findTranscriptMatches(msgs, "   "), []);
});

test("matches name the message and the field that matched", () => {
  const hits = findTranscriptMatches(msgs, "login");
  assert.equal(hits.length, 3);
  assert.deepEqual(hits[0], {
    messageId: "m1",
    matchIndex: 1,
    matchTotal: 3,
    fields: ["content"],
  });
  // m2 matches in thinking and tool output.
  assert.deepEqual(hits[1].fields, ["thinking", "tools"]);
  // m3 matches in plan only.
  assert.deepEqual(hits[2], {
    messageId: "m3",
    matchIndex: 3,
    matchTotal: 3,
    fields: ["plan"],
  });
});

test("matching is case-insensitive and substring-based", () => {
  const hits = findTranscriptMatches(msgs, "LOGIN");
  assert.equal(hits.length, 3);
  const toolHits = findTranscriptMatches(msgs, "code_");
  assert.equal(toolHits.length, 1);
  assert.deepEqual(toolHits[0].fields, ["tools"]);
});

test("no match is an empty list, not null", () => {
  assert.deepEqual(findTranscriptMatches(msgs, "zzz-no-such-text"), []);
});

test("matchFieldLabel reads the hit's own fields", () => {
  assert.equal(
    matchFieldLabel({
      messageId: "x",
      matchIndex: 1,
      matchTotal: 1,
      fields: ["content", "tools"],
    }),
    "content + tools",
  );
});

/* ------------------------- exportToolCall ------------------------- */

test("a reported tool call exports its name, status, args and result", () => {
  const md = exportToolCall(
    {
      name: "shell",
      args: { command: "npm test" },
      output: "ok",
      status: "completed",
    },
    0,
  );
  assert.ok(md.includes("**1. shell** — completed"), md);
  assert.ok(md.includes('"command": "npm test"'), md);
  assert.ok(md.includes("    ok"), md);
});

test("a call with no result says so instead of borrowing a verdict", () => {
  const md = exportToolCall({ name: "shell", args: { command: "x" } }, 2);
  assert.ok(md.includes("**3. shell** — no result reported"), md);
});

test("tool output is indented, so embedded fences cannot break the doc", () => {
  const md = exportToolCall(
    { name: "t", output: "```\ncode\n```", status: "completed" },
    0,
  );
  // No new fence opened: the output rides as an indented block.
  assert.ok(!md.includes("```\n    ```"), md);
  assert.ok(md.includes("    ```"), md);
});

test("empty args render no args block", () => {
  const md = exportToolCall({ name: "t", status: "completed" }, 0);
  assert.ok(!md.includes("json"), md);
});

/* ------------------------- exportMessage / exportTranscript ------------------------- */

test("a message exports role, body, thinking, plan, tools and artifacts", () => {
  const md = exportMessage({
    id: "m",
    role: "assistant",
    content: "Done.",
    thinking: "check first",
    todos: [{ id: "t", content: "Fix it", status: "completed", index: 0 }],
    toolCalls: [{ id: "c", name: "shell", args: {}, status: "completed" }],
    artifacts: [{ id: "a", name: "f.ts", type: "code", content: "x" }],
    createdAt: "2026-10-08T10:00:00Z",
  });
  assert.ok(md.startsWith("## Alpha (2026-10-08T10:00:00Z)"), md);
  assert.ok(md.includes("Done."), md);
  assert.ok(md.includes("> check first"), md);
  assert.ok(md.includes("- [x] Fix it"), md);
  assert.ok(md.includes("**1. shell** — completed"), md);
  assert.ok(md.includes("- f.ts (code)"), md);
});

test("absent sections are omitted, never blank-headed", () => {
  const md = exportMessage({
    id: "m",
    role: "user",
    content: "Hi",
    createdAt: null,
  });
  assert.ok(!md.includes("Thinking"), md);
  assert.ok(!md.includes("Plan"), md);
  assert.ok(!md.includes("Tools"), md);
  assert.ok(!md.includes("Artifacts"), md);
});

test("an empty transcript exports a sentence, not an empty file", () => {
  const md = exportTranscript([], "My chat");
  assert.ok(md.includes("# My chat"), md);
  assert.ok(md.includes("No messages"), md);
});

test("messages join oldest-first behind rules", () => {
  const md = exportTranscript([
    { id: "a", role: "user", content: "Q", createdAt: null },
    { id: "b", role: "assistant", content: "A", createdAt: null },
  ]);
  assert.ok(md.indexOf("## You") < md.indexOf("## Alpha"), md);
  assert.ok(md.includes("---"), md);
});

test("exportFilename is timestamped and filesystem-safe", () => {
  const name = exportFilename(new Date("2026-10-08T10:00:00.000Z"));
  assert.ok(name.startsWith("alpha-conversation-2026-10-08T10-00-00"), name);
  assert.ok(name.endsWith(".md"), name);
  assert.ok(!name.includes(":"), name);
});
