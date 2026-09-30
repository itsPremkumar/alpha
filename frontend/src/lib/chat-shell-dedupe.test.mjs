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

test("a project with no row yet reports no failure, because no read was attempted", async () => {
  const { railRowFor } = await loadChatShell();
  // First paint: the project list is known, but none of the per-project
  // conversation/crew reads have answered. The row does not exist yet.
  const row = railRowFor([], "p1");

  assert.equal(row.projectId, "p1");
  assert.equal(row.conversationCount, null, "an unread count is unknown, never 0");
  assert.equal(
    row.conversationError,
    null,
    "a read that was never attempted must not be reported as a read that failed",
  );
  assert.equal(row.crewError, null, "same for the crew read");
  assert.equal(row.members, null);
  assert.equal(row.leadsSelectedBot, null);
});

test("a real read failure is still reported as a failure", async () => {
  const { railRowFor } = await loadChatShell();
  // The distinction that matters: null means "nobody asked", a string means
  // "the server said no". Collapsing the two is the defect.
  const failed = railRowFor(
    [
      {
        projectId: "p1",
        conversationCount: null,
        conversationError: "The Gateway did not answer.",
        members: null,
        crewError: null,
        leadsSelectedBot: null,
      },
    ],
    "p1",
  );
  assert.equal(failed.conversationError, "The Gateway did not answer.");
  assert.notEqual(failed.conversationError, null, "a genuine failure must stay visible");
});

test("an existing row is returned untouched rather than replaced by the fallback", async () => {
  const { railRowFor } = await loadChatShell();
  const row = railRowFor(
    [
      {
        projectId: "p1",
        conversationCount: 7,
        conversationError: null,
        members: ["coder"],
        crewError: null,
        leadsSelectedBot: true,
      },
    ],
    "p1",
  );
  assert.equal(row.conversationCount, 7);
  assert.deepEqual(row.members, ["coder"]);
  assert.equal(row.leadsSelectedBot, true);
});

test("the agent selector is reachable from the keyboard, not click-only", () => {
  // The custom trigger the rail passes in was a bare `<div onClick>`: not
  // focusable, no Enter/Space handling, and announced as an unnamed group, so
  // the one control that picks the agent could not be operated without a mouse.
  // The `else` branch was already a real button; this pins the children branch
  // to the same contract.
  const menu = readFileSync(
    new URL("../components/chat-shell/BotDropdownMenu.tsx", import.meta.url),
    "utf8",
  );
  const branch = menu.slice(menu.indexOf("{children ? ("), menu.indexOf(") : ("));
  assert.match(branch, /role="button"/, "the custom trigger must expose a button role");
  assert.match(branch, /tabIndex=\{0\}/, "and must be focusable");
  assert.match(branch, /aria-expanded=\{open\}/, "and must report whether the menu is open");
  assert.match(branch, /aria-haspopup="true"/);
  assert.match(
    branch,
    /e\.key === "Enter" \|\| e\.key === " "/,
    "Enter and Space must open the menu, or the control is mouse-only",
  );
  assert.match(branch, /aria-label=/, "and it must name which agent it acts on");
});

test("the dropdown is the ONLY agent selector; the scrolling roster list is gone", () => {
  // Structural pin. The rail used to offer the same choice twice: a fixed-height
  // scrollable "AI Agents (N)" list AND the dropdown card below it. The list was
  // removed, so this asserts it cannot come back and that the dropdown still
  // receives the de-duped roster.
  //
  // The dropdown must remain a COMPLETE replacement, so this also pins that it
  // still carries the Lead Agent row - `onSelectBot(null)`, "Auto-routes" -
  // which the deleted list also provided. Without it, removing the list would
  // make the Lead Agent unselectable, and a structural pin on the file's
  // existence would happily pass over that regression.
  const rail = readFileSync(
    new URL("../components/chat-shell/BotWorkspaceRail.tsx", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(rail, /rosterBots\.map\(/, "the scrolling roster list must stay removed");
  assert.doesNotMatch(rail, /AI Agents<\/span>/, "the roster list header must stay removed");
  assert.doesNotMatch(rail, /setAgentsCollapsed/, "its collapse state must stay removed");
  assert.match(rail, /<BotDropdownMenu/, "the dropdown must remain");
  assert.match(rail, /bots=\{rosterBots\}/, "the dropdown must receive the de-duped roster");

  const menu = readFileSync(
    new URL("../components/chat-shell/BotDropdownMenu.tsx", import.meta.url),
    "utf8",
  );
  assert.match(
    menu,
    /onSelectBot\(null\)/,
    "the dropdown must keep a Lead Agent row, or removing the list made it unreachable",
  );
  assert.match(menu, /bots\.map\(/, "the dropdown must still render one row per bot");
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

test("the agent list comes FIRST in the dropdown; the actions come after it", () => {
  // Ordering is the whole point of this change. The bot list used to sit at the
  // BOTTOM, below five actions, inside a max-h-40 scroller - so opening the menu
  // to change agent showed six things that were not agent choices, and the
  // actual list was below the fold. The operator asked for the selection first
  // and the actions after.
  //
  // Asserted by INDEX, not by presence: every one of these blocks exists in both
  // orderings, so a presence check would pass either way and prove nothing.
  const menu = readFileSync(
    new URL("../components/chat-shell/BotDropdownMenu.tsx", import.meta.url),
    "utf8",
  );
  const iSwitch = menu.indexOf("Switch AI Agent");
  const iActions = menu.indexOf("Core Bot Actions");
  const iNewConversation = menu.indexOf("New Conversation");
  const iBotSettings = menu.indexOf("Bot settings");
  const iRows = menu.indexOf("bots.map(");
  const iLeadRow = menu.indexOf("Auto-routes");

  const parts = [
    ["Switch AI Agent", iSwitch],
    ["Core Bot Actions", iActions],
    ["New Conversation", iNewConversation],
    ["Bot settings", iBotSettings],
    ["bots.map(", iRows],
    ["Auto-routes", iLeadRow],
  ];
  for (const entry of parts) {
    assert.notEqual(entry[1], -1, entry[0] + " must still exist in the menu");
  }

  assert.ok(iRows < iActions, "per-bot rows must render before the action block");
  assert.ok(iSwitch < iActions, "the Switch AI Agent heading must precede the actions");
  assert.ok(iLeadRow < iActions, "the Lead Agent row must precede the actions");
  assert.ok(iActions < iNewConversation, "the actions stay together, after the list");
  assert.ok(iBotSettings > iActions, "including the last action");
});
