import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import ts from "typescript";

/**
 * Regression coverage for the left-rail roster de-duplication.
 *
 * THE DEFECT (visible in the operator's own reference screenshot): the same
 * specialist was listed twice in a row - "Cuda_Kernel_Opt Specialist",
 * "Solidity_Security Specialist", "Cuda_Kernel_Opt Specialist",
 * "Solidity_Security Specialist". Two causes, both real:
 *
 *   1. The roster is assembled from more than one source (the bot registry plus
 *      a crew-membership read), so a bot that is both a registry entry and a
 *      project member arrives twice.
 *   2. The rows key on `name`, so React also logs a duplicate-key warning.
 *
 * Both the inline list and the dropdown must read the SAME de-duped list. If
 * only one is de-duped, the two surfaces disagree about how many bots exist,
 * which is worse than the duplicate row: the dropdown would offer a bot the
 * list does not show.
 *
 * LOADER NOTE. `chat-shell.ts` has relative imports, so the repo's usual
 * `data:text/javascript` trick (see `activity.test.mjs`) cannot resolve them and
 * fails with ERR_UNSUPPORTED_RESOLVE_REQUEST. The module is therefore
 * transpiled into a temp directory beside stubs for its three siblings. The
 * stubs are never called by the code under test; they satisfy ESM linking only.
 * Each stub exports exactly the names the real module imports, because a missing
 * name fails loudly at link time rather than silently yielding undefined.
 */

const chatShellSource = readFileSync(new URL("./chat-shell.ts", import.meta.url), "utf8");

const NL = String.fromCharCode(10);

const SIBLING_STUBS = {
  "time.mjs": [
    "export const absoluteStamp = () => null;",
    "export const relTime = () => null;",
    "export const isRecent = () => false;",
    "export const PRESENCE_WINDOW_SECONDS = 300;",
  ].join(NL),
  "projects.mjs": [
    "export const listProjectAgents = async () => new Map();",
    "export const projectThreads = async () => ({ threads: [], conversationCount: 0, error: null });",
  ].join(NL),
  "threads-ext.mjs": "export const threadTitle = () => null;",
};

let cached = null;
async function loadChatShell() {
  if (cached) return cached;
  const dir = mkdtempSync(join(tmpdir(), "alpha-chat-shell-"));
  for (const [name, body] of Object.entries(SIBLING_STUBS)) {
    writeFileSync(join(dir, name), body, "utf8");
  }
  const compiled = ts.transpileModule(chatShellSource, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const rewritten = compiled
    .replace(/from\s*"\.\/time"/g, 'from "./time.mjs"')
    .replace(/from\s*"\.\/projects"/g, 'from "./projects.mjs"')
    .replace(/from\s*"\.\/threads-ext"/g, 'from "./threads-ext.mjs"');
  const file = join(dir, "chat-shell.mjs");
  writeFileSync(file, rewritten, "utf8");
  cached = await import(pathToFileURL(file).href);
  return cached;
}

test("the same bot name yields one row, not two", async () => {
  const { dedupeBots } = await loadChatShell();
  const roster = [
    { name: "Cuda_Kernel_Opt Specialist" },
    { name: "Solidity_Security Specialist" },
    { name: "Cuda_Kernel_Opt Specialist" },
    { name: "Data Engineer" },
    { name: "Solidity_Security Specialist" },
  ];
  const out = dedupeBots(roster).map((b) => b.name);
  assert.deepEqual(out, [
    "Cuda_Kernel_Opt Specialist",
    "Solidity_Security Specialist",
    "Data Engineer",
  ]);
  assert.equal(new Set(out).size, out.length, "no name may repeat");
});

test("first wins, so a later duplicate cannot overwrite the registry record", async () => {
  const { dedupeBots } = await loadChatShell();
  const first = { name: "coder", source: "registry" };
  const second = { name: "coder", source: "membership-projection" };
  const out = dedupeBots([first, second]);
  assert.equal(out.length, 1);
  assert.equal(out[0].source, "registry", "the first record must be the one kept");
});

test("surrounding whitespace does not make one bot look like two", async () => {
  const { dedupeBots } = await loadChatShell();
  const out = dedupeBots([{ name: "coder" }, { name: "  coder  " }]);
  assert.equal(out.length, 1, "identity is the trimmed name");
});

test("a nameless row is dropped rather than rendered as a blank unselectable row", async () => {
  const { dedupeBots } = await loadChatShell();
  const out = dedupeBots([{ name: "coder" }, { name: "" }, { name: "   " }, { name: null }, {}]);
  assert.deepEqual(
    out.map((b) => b.name),
    ["coder"],
  );
});

test("render order follows first appearance, so a reordering read shows a real change", async () => {
  const { dedupeBots } = await loadChatShell();
  const a = dedupeBots([{ name: "a" }, { name: "b" }, { name: "a" }]).map((x) => x.name);
  const b = dedupeBots([{ name: "b" }, { name: "a" }, { name: "b" }, { name: "a" }]).map((x) => x.name);
  assert.deepEqual(a, ["a", "b"]);
  assert.deepEqual(b, ["b", "a"]);
});

test("an empty roster stays empty rather than becoming a placeholder row", async () => {
  const { dedupeBots } = await loadChatShell();
  assert.deepEqual(dedupeBots([]), []);
});

test("the list and the dropdown are fed the SAME de-duped roster", () => {
  // Structural pin: a behavioural test cannot render two React surfaces here
  // (no jsdom in this suite), so this guards the wiring that makes them agree.
  // It would pass vacuously if `rosterBots` stopped existing, so it asserts the
  // memo declaration too.
  const rail = readFileSync(
    new URL("../components/chat-shell/BotWorkspaceRail.tsx", import.meta.url),
    "utf8",
  );
  assert.match(rail, /const rosterBots = useMemo\(\(\) => dedupeBots\(bots\), \[bots\]\)/);
  assert.doesNotMatch(rail, /\{bots\.map\(/, "the inline list must not read the raw roster");
  assert.doesNotMatch(rail, /\bbots=\{bots\}/, "the dropdown must not receive the raw roster");
  assert.match(rail, /bots=\{rosterBots\}/, "the dropdown receives the de-duped roster");
});

test("no HTTP method or route is rendered as visible text in the rail surfaces", () => {
  // The operator asked for this explicitly. `POST /api/projects` was rendered as
  // a <code> element in the new-project dialog: developer plumbing shown to a
  // person choosing a name for their work.
  const files = [
    "../components/chat-shell/NewProjectDialog.tsx",
    "../components/chat-shell/BotWorkspaceRail.tsx",
    "../components/chat-shell/ProjectDetailPanel.tsx",
    "../components/chat-shell/BotDropdownMenu.tsx",
    "../components/chat-shell/ProjectDropdownMenu.tsx",
  ];
  for (const rel of files) {
    const src = readFileSync(new URL(rel, import.meta.url), "utf8");
    // Strip comments: a comment may legitimately name a route while explaining
    // why the route is not shown to the operator.
    const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    const rendered = code.match(
      /<(code|kbd|samp)[^>]*>[^<]*\b(GET|POST|PUT|PATCH|DELETE)\b[^<]*<\/(code|kbd|samp)>/gi,
    );
    assert.equal(
      rendered,
      null,
      rel + " renders an HTTP method as visible text: " + (rendered ? rendered.join(", ") : ""),
    );
  }
});
