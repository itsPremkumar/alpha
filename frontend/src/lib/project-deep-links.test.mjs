// project-deep-links.test.mjs — a project that is *named* anywhere in the shell
// must also be *openable* from there.
//
// The gap this pins: the per-project drill-down (`ProjectInspectorSection`,
// reached through `ProjectsSection`) was reachable from exactly one place — the
// card's own "View more" — while a project was named in a dozen others (the
// conversation you are reading it from, the sidebar group it owns, the company
// that links it, the war room watching it, the board filtered to it). In each
// of those the only route was "go to Projects, then find it in the list", which
// is a search exercise the moment a second project exists.
//
// Two properties are asserted for the redirect itself, because both have a
// plausible wrong implementation:
//
//   1. it is a *request* carrying the project id (plus a nonce), not a mirror
//      of local state — the section lives in another view, so a bare
//      `setView("projects")` lands on the list rather than on the project;
//   2. it is consumed exactly once — a request left standing re-fires every
//      time the view is mounted, so closing the drill-down and coming back
//      would pop it open again.
//
// The per-surface tests then pin that each entry point exists *and* that a
// control which could open nothing (no project selected, "All projects", a
// project id that could not be resolved) is not offered at all.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function read(rel) {
  return readFileSync(new URL(`../components/${rel}`, import.meta.url), "utf8");
}

const chat = read("ChatView.tsx");
const section = read("sections/ProjectsSection.tsx");
const sidebar = read("ThreadSidebar.tsx");
const company = read("sections/CompanySection.tsx");
const detail = read("chat-shell/ProjectDetailPanel.tsx");
const menu = read("chat-shell/ProjectDropdownMenu.tsx");
const header = read("chat-shell/ProjectContextHeader.tsx");
const workforce = read("sections/WorkforceSection.tsx");
const board = read("sections/KanbanSection.tsx");

test("one handler carries the project id across views", () => {
  assert.match(
    chat,
    /const \[projectDetailRequest, setProjectDetailRequest\]/,
    "the redirect must be state the shell owns, not a view switch alone",
  );
  assert.match(
    chat,
    /setProjectDetailRequest\(\{ projectId, nonce: projectDetailNonceRef\.current \}\)/,
    "the request must carry the project AND a nonce: re-opening the project the " +
      "section already shows would otherwise be a state change React sees as unchanged",
  );

  // Order matters: mint the nonce, record the request, then switch views. A
  // switch recorded first would mount the section before the request existed.
  const nonce = chat.indexOf("projectDetailNonceRef.current += 1");
  const hand = chat.indexOf(
    "setProjectDetailRequest({ projectId, nonce: projectDetailNonceRef.current })",
  );
  const view = chat.indexOf('setView("projects")', hand);
  assert.ok(nonce !== -1, "the nonce must be minted");
  assert.ok(nonce < hand, "the nonce must be minted before the request is recorded");
  assert.ok(
    hand < view,
    "the view must switch after the request is recorded, or the section mounts empty",
  );
});

test("the requested project is opened once, then acknowledged", () => {
  const open = section.indexOf("openInspector(request.projectId)");
  assert.notEqual(open, -1, "the section must open the project the request names");

  const ack = section.indexOf("onOpenProjectRequestHandled?.()", open);
  assert.notEqual(
    ack,
    -1,
    "the request must be acknowledged, or it re-fires on the next mount and the " +
      "operator who closed the drill-down lands back in it",
  );

  assert.match(
    section,
    /if \(!request\) return;/,
    "a cleared request must be a no-op, not an open of project \"\"",
  );
  assert.match(
    section,
    /openProjectRequest\?: \{ projectId: string; nonce: number \} \| null;/,
    "the request shape belongs in the section's contract, not in a comment",
  );
});

test("the shell passes the request to the Projects view both ways", () => {
  assert.match(chat, /openProjectRequest=\{projectDetailRequest\}/);
  assert.match(
    chat,
    /onOpenProjectRequestHandled=\{\(\) => setProjectDetailRequest\(null\)\}/,
    "the owner must clear the request it is told was handled",
  );
  // Every consumer receives the same handler, so no surface can quietly grow a
  // second, differently-behaving redirect.
  const wired = chat.match(/onOpenProjectDetails=\{openProjectDetails\}/g) ?? [];
  assert.ok(
    wired.length >= 8,
    `expected the shell to hand the handler to at least 8 surfaces, found ${wired.length}`,
  );
});

test("the conversation footer links to the project being read", () => {
  assert.match(
    chat,
    /View full project details/,
    "the bottom of a project conversation is where the project is most likely to " +
      "be asked about, and it had no route out at all",
  );
  assert.match(
    chat,
    /openProjectDetails\(activeProjectId\)/,
    "the raw id must drive the link: a project whose name never resolved can still " +
      "be opened by id, and hiding the link is the one case where it is needed most",
  );
  assert.match(chat, /disabled=\{!activeProjectId\}/, "no id, no control");
});

test("the chat header offers the drill-down beside the in-chat overview", () => {
  assert.match(
    menu,
    /onOpenDetails\?: \(projectId: string\) => void;/,
    "the menu must declare the route it offers",
  );
  assert.match(
    menu,
    /onOpenDetails\(project\.id\)/,
    "the menu must open the project it was opened for, not the project list",
  );
  assert.match(
    menu,
    /Full project details/,
    "the row must say which of the two projects surfaces it opens",
  );
  assert.match(
    header,
    /onOpenDetails=\{onOpenProjectDetails\}/,
    "the header must thread the handler through to the menu",
  );
  assert.match(
    header,
    /No fallback here on purpose/,
    "a fallback that only switches the view would advertise a full read and open " +
      "the list instead — the row must be withheld rather than mislabelled",
  );
});

test("every other surface that names a project renders its own route", () => {
  // Sidebar: one route per project group header.
  assert.match(
    sidebar,
    /onOpenProjectDetails\(group\.key\)/,
    "the sidebar groups conversations BY project; its group header must reach that project",
  );
  assert.match(sidebar, /Open full project details for/);

  // Companies: the portfolio lists linked projects by id.
  assert.match(
    company,
    /onOpenProjectDetails\(p\.project_id\)/,
    "a company's linked projects are reached from outside the Projects tab by definition",
  );

  // The in-chat inspector's header, on every one of its tabs.
  assert.match(detail, /onOpenProjectDetails\(project\.id\)/);

  // The Workforce pickers: presence panel and war room.
  const opened = workforce.match(/props\.onOpenProjectDetails\?\.\(/g) ?? [];
  assert.equal(
    opened.length,
    2,
    `both project pickers in the Workforce view must offer the read, found ${opened.length}`,
  );

  // The board, while it is filtered to one project.
  assert.match(board, /props\.onOpenProjectDetails\?\.\(projectFilter\)/);
});

test("a control that could open nothing is not offered", () => {
  // Board: "All projects" is no project, and "" is the honest *No project*
  // half of the filter — neither has an id to open.
  assert.match(
    board,
    /projectFilter && projectFilter !== "all" && props\.onOpenProjectDetails/,
    "the link must be withheld rather than pointing at nothing",
  );

  // Workforce: a picker with no selection names no project.
  assert.match(workforce, /selectedProject && props\.onOpenProjectDetails/);
  assert.match(workforce, /projectId && props\.onOpenProjectDetails/);

  // The rail menu only exists when the shell can honour it.
  assert.match(
    read("chat-shell/BotWorkspaceRail.tsx"),
    /onOpenDetails=\{onOpenProjectDetails\}/,
    "the sidebar rail's project menu must carry the handler too",
  );

  // And the row itself needs a project to open.
  assert.match(menu, /project && onOpenDetails &&/);
});

test("a project id in the URL is an entry point — the cross-route case", () => {
  // The bot's own page (`/bots/[name]`) is a *different route*: none of the
  // in-memory requests above survive that jump, so the URL is the only
  // channel there is. It is folded into the same one-shot request rather than
  // into a second mechanism, so there stays exactly one owner of the answer
  // to "which project is open" — and the nonce/acknowledge contract above is
  // inherited instead of reimplemented.
  assert.match(
    chat,
    /new URLSearchParams\(window\.location\.search\)\.get\("project"\)/,
    "the shell reads the parameter the bot page links with",
  );
  assert.match(
    chat,
    /if \(projectId\) openProjectDetails\(projectId\)/,
    "and hands it to the same handler every other surface uses",
  );

  // Declared after `openProjectDetails`, so the effect body closes over an
  // initialised binding, and it runs post-hydration for the reason the view
  // seed documents: reading `window` during the first render is the SSR
  // mismatch that discarded the server-rendered tree.
  const declared = chat.indexOf("const openProjectDetails = ");
  const seed = chat.indexOf('get("project")');
  assert.notEqual(declared, -1, "the handler must exist");
  assert.ok(declared !== -1 && seed > declared, "the seed must be declared after the handler");
});
