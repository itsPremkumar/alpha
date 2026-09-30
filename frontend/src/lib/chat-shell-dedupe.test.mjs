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
    "../components/chat-shell/ChatShellLanding.tsx",
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

test("the empty-state landing shows no Gateway API probe and no HTTP routes", () => {
  // Removed at the operator's request: the "Gateway Starter Actions" block that
  // offered "Read the agent roster / GET /api/agents", "List installed skills /
  // GET /api/skills" and "Show the model catalog / GET /api/models", each
  // rendering its route in a <code> element under a "Direct API probe" caption.
  //
  // That is developer plumbing presented as the first things a new user is
  // offered, and it is the same complaint as the `POST /api/projects` line in
  // the new-project dialog: an HTTP verb is not something an operator chooses
  // from. The whole probe section is gone, not just its labels.
  const landing = readFileSync(
    new URL("../components/chat-shell/ChatShellLanding.tsx", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(landing, /Gateway Starter Actions/, "the probe block must stay removed");
  assert.doesNotMatch(landing, /Direct API probe/);
  assert.doesNotMatch(landing, /starterActions\(/, "it must not build a probe list");
  assert.doesNotMatch(landing, /<code[^>]*>\{action\.route\}<\/code>/, "no route in a code element");
  assert.doesNotMatch(landing, /action\.method === "POST"/, "no POST/GET branch for a probe");
  assert.doesNotMatch(landing, /data-read=/, "no probe result panel");

  // The landing must still be a landing: removing the probe must not have
  // removed the starter prompts that actually help someone begin.
  assert.match(landing, /onPickStarter/, "the starter prompts must remain");
});

test("the rail has no trailing Create Project button; creation stays reachable elsewhere", () => {
  // Removed at the operator's request: the dashed "Create Project" button that
  // sat at the very bottom of the rail, under "Show N more projects...", where
  // it read as a peer of the conversation list rather than a project action.
  //
  // Assertions are deliberately NARROW. An earlier draft of this test banned
  // `border-dashed` outright and failed - correctly - because the projects
  // EMPTY STATE also uses a dashed border, and that one is a different control
  // ("No projects for <bot> yet" plus a "Create a project" link) which was not
  // asked to be removed. So the pins target the removed button's own signature
  // rather than a style class two controls share.
  //
  // The test also asserts creation did NOT become unreachable: the agent
  // dropdown's "New Project" and the Projects header's "+" both still open the
  // same dialog. A removal test that only checked absence would have passed even
  // if this had been the only way to create a project.
  const rail = readFileSync(
    new URL("../components/chat-shell/BotWorkspaceRail.tsx", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(rail, /<span>Create Project<\/span>/, "the trailing button must stay removed");
  assert.doesNotMatch(rail, /Quick "\+ New Project" action/, "its comment must go with it");
  assert.doesNotMatch(rail, /FolderPlus/, "and its icon import must not be left orphaned");

  const opens = rail.match(/setCreating\(true\)/g) || [];
  assert.ok(
    opens.length >= 2,
    "at least two other controls must still open the project dialog: " + opens.length,
  );
  assert.match(rail, /onNewProject=\{\(\) => setCreating\(true\)\}/, "the agent dropdown keeps it");
  assert.match(rail, /aria-label="Create new project"/, "the Projects header plus keeps it");
  assert.match(rail, /\{creating && \(/, "and the dialog itself is still rendered");
});

test("each project row carries a + that starts a conversation in THAT project", () => {
  // The operator asked for a + on every project row, matching the one the
  // Standalone group already had on its header.
  //
  // Before this, starting a conversation inside a project took two clicks on a
  // collapsed row (expand, then "New in Project"), while the same action for a
  // standalone conversation took one. The cost of the same action depended on
  // which group the conversation belonged to.
  //
  // The pin asserts the control is wired to the ROW's own projectId, not to a
  // closure over some outer selection. A + that opens a conversation in the
  // wrong project is worse than no + at all, and a structural check that only
  // looked for a Plus icon would pass over exactly that bug.
  const rail = readFileSync(
    new URL("../components/chat-shell/BotWorkspaceRail.tsx", import.meta.url),
    "utf8",
  );
  const start = rail.indexOf("Start a conversation in THIS project");
  assert.notEqual(start, -1, "the per-project + must exist");
  // The explanatory comment above the control is ~700 chars on its own, so the
  // window has to clear it plus the button body or the assertions below read a
  // truncated region and fail for the wrong reason.
  const region = rail.slice(start, start + 2000);

  assert.match(
    region,
    /onClick=\{\(\) => onNewConversation\(row\.projectId\)\}/,
    "the + must open a conversation in ITS OWN project",
  );
  assert.match(region, /aria-label=\{`New conversation in /, "and it must be named for a screen reader");
  assert.match(
    region,
    /title=\{`Start a new conversation in /,
    "with a tooltip, because an icon-only control has no visible label",
  );
  assert.match(region, /<Plus className="size-3"/, "and it must render the Plus glyph");

  // Parity: the standalone group keeps its own +, so both groups offer one click.
  const pluses = rail.match(/onNewConversation\(/g) || [];
  assert.ok(pluses.length >= 3, "expected the per-project +, the panel button and the dropdown");
});

test("an expanded project shows its agents, and never turns an unread crew into zero", () => {
  // The rail already fetched this. `readProjectRailRows` reads per-project
  // presence, and `ProjectRailRow.members` is the member list it produced, so
  // surfacing it costs no new request.
  //
  // The load-bearing assertion is the three-way split. `members === null` means
  // the read did not answer; `[]` means the server said nobody is attached.
  // Collapsing them would make a failed read claim the server measured zero -
  // the exact defect class this suite keeps guarding. So a test that only
  // checked "the agent names render" would pass while the UI lied about a
  // failed read, which is why each state is pinned separately.
  const rail = readFileSync(
    new URL("../components/chat-shell/BotWorkspaceRail.tsx", import.meta.url),
    "utf8",
  );

  assert.match(rail, /row\.members === null/, "the unread case must be distinguished");
  assert.match(
    rail,
    /No agents attached to this project/,
    "an empty crew is a real answer and says so",
  );
  assert.match(rail, /row\.members\.length === 0/, "and it is checked as length, not truthiness");
  assert.match(rail, /row\.members\.map\(/, "the member list must be rendered");
  assert.match(rail, /<Users /, "with an icon, so the block reads as a group");

  // No new request: the strip must read the row it is already inside.
  assert.doesNotMatch(rail, /getCrew\(/, "the rail must not re-fetch crew it already has");

  // The unread branch must not leak the server's error reason as if it were a
  // fact about the project; it is a failure to read, and says so.
  assert.match(rail, /Crew not reported\./, "an unread crew states that, not a count");
  assert.match(rail, /row\.crewError \? "Crew not read\." : "Crew not reported\."/);
});

test("the empty project list never claims a project is empty when its badge says otherwise", () => {
  // The contradiction the operator reported: a row whose badge read 3, and
  // whose expanded body read "No conversations in this project yet."
  //
  // Two different sources produced those. The badge is `row.conversationCount`,
  // counted by the Gateway over the whole project. The list is `projThreads`,
  // which comes from `groupConversations(threads, ...)` on a thread set that
  // ChatView has ALREADY filtered to the selected agent. So "empty" here only
  // ever meant "empty for this agent" - and the copy said otherwise, in the
  // same row as a badge that said the opposite.
  //
  // Asserted on the copy, because the copy is the defect. A test that merely
  // checked the list renders would pass while the UI contradicted itself.
  const rail = readFileSync(
    new URL("../components/chat-shell/BotWorkspaceRail.tsx", import.meta.url),
    "utf8",
  );
  const start = rail.indexOf("{projThreads.length === 0 ? (");
  assert.notEqual(start, -1, "the empty-project branch must exist");
  // The inline rationale comments run long, so the window has to clear them
  // plus all three branches or the later assertions read a truncated region and
  // fail for the wrong reason.
  const region = rail.slice(start, start + 2600);

  // The unread case, so a failed count is not read as a zero either.
  assert.match(region, /row\.conversationCount === null/, "an unreported count stays unknown");
  assert.match(region, /Conversations not reported\./);

  // The contradiction case, which is the one that was wrong.
  assert.match(
    region,
    /row\.conversationCount > 0 \? \(/,
    "a non-zero project count must take its own branch",
  );
  assert.match(
    region,
    /in this project, none with/,
    "and that branch must say the project HAS conversations, naming the agent",
  );
  assert.match(region, /\{currentBotDisplayName\}/, "the agent must be named, as the standalone copy does");

  // The genuinely-empty case must still exist and keep its original wording.
  assert.match(region, /No conversations in this project yet\./);
});
