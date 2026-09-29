// project-overview.test.mjs — the Projects view's per-project detail surface.
//
// Found by using the UI: the Projects list answered "which projects exist" and
// nothing else. `instructions`, `created_at`/`updated_at` and the project id were
// in the payload and never rendered; every one of the ~22 read-only
// GET /projects/{id}/* routes was invisible; and the live per-project surface
// (Workforce) kept its own project dropdown that defaults to the FIRST project,
// so opening a project and hunting for its state landed somewhere else.
//
// The client contract is ./project-detail.test.mjs. These tests pin the WIRING —
// that the panel is reachable, that the collapsed card says something, and that
// the deep surface is handed the project the operator actually opened.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");

const projectsSection = read("../components/sections/ProjectsSection.tsx");
const overviewPanel = read("../components/sections/ProjectOverviewPanel.tsx");
const workforce = read("../components/sections/WorkforceSection.tsx");
const chatView = read("../components/ChatView.tsx");

test("the detail panel is actually rendered inside an expanded project card", () => {
  // A client module nothing imports is dead code. The panel has to be mounted by
  // the section the operator reaches from the project list.
  assert.match(projectsSection, /import\s*\{[^}]*ProjectOverviewPanel[^}]*\}\s*from\s*"@\/components\/sections\/ProjectOverviewPanel"/);
  assert.match(projectsSection, /<ProjectOverviewPanel[\s\S]{0,200}projectId=\{p\.id\}/);
});

test("a collapsed project card states what the server already told it", () => {
  // These four fields all arrive in the GET /projects payload. Rendering none of
  // them is why a project said nothing about itself until you opened it.
  assert.match(projectsSection, /p\.instructions/, "instructions must be shown, not only editable");
  assert.match(projectsSection, /p\.updated_at/, "the project must say when it last changed");
  assert.match(projectsSection, /p\.id\.slice\(0, 8\)/, "the project id must be reachable from the UI");
  assert.match(projectsSection, /Project id: \$\{p\.id\}/, "and the full id must be available");
  // The chat and bot counts come from data already fetched for this card.
  assert.match(projectsSection, /\{total\} chat\{total === 1 \? "" : "s"\}/);
  assert.match(projectsSection, /\{members\.length\} bot\{members\.length === 1 \? "" : "s"\}/);
});

test("the project view never renders a server-tracked conversation as absent", () => {
  // `projectThreads()` is the project's own record; the grouped list is the
  // workspace list. They disagree while a page is in flight, for an archived
  // thread, or after a partial page — and the difference used to be dropped, so
  // the card's chat count silently under-reported.
  assert.match(projectsSection, /serverOnlyThreads/);
  assert.match(
    projectsSection,
    /On the server but not in this list/,
    "a conversation the server holds must be shown, not discarded",
  );
  // And the old wording that claimed they were merely 'tracked' is gone.
  assert.doesNotMatch(projectsSection, /Tracked on the server/);
});

test("the deep per-project surface is handed the project that was opened", () => {
  // Switching views alone is not enough: Workforce defaulted to the first
  // project, so the jump landed on a different project than the one opened.
  assert.match(chatView, /setFocusedProjectId\(projectId\)/, "the opened project must be recorded");
  assert.match(chatView, /focusedProjectId=\{focusedProjectId\}/, "and passed to the workforce view");

  // Both of Workforce's project selectors must honour it — a single guarded one
  // still lets the other silently fall back to the first project.
  const focused = workforce.match(/props\.focusedProjectId/g) || [];
  assert.ok(
    focused.length >= 3,
    `expected the focus in the prop, the war-room selector and the presence selector, found ${focused.length}`,
  );
  assert.match(workforce, /const focused = props\.focusedProjectId && p\.some/);
  // The fallback must be the first project, and only when the focus does not apply.
  assert.match(workforce, /focused \? \(props\.focusedProjectId as string\) : p\[0\]\.id/);
});

test("the panel never paints a failed or unknown read as a healthy one", () => {
  // A section that silently vanishes is indistinguishable from a project with no
  // decisions, so a failure must carry the server's reason and be named.
  assert.match(overviewPanel, /could not be read/, "a failed section is named, not hidden");
  assert.match(overviewPanel, /\{props\.section\.error\}/, "and shows the server's own reason");
  assert.match(overviewPanel, /detail\.partial/, "a partial read is disclosed up front");

  // Absent is not zero: a count the server did not send must render unknown.
  assert.match(overviewPanel, /props\.value === null \? "—" : props\.value/);
  assert.match(overviewPanel, /never verified/, "last_verified: null is never verified");

  // An absent timestamp must never reach `new Date("")`, which is the epoch and
  // would claim the event happened in 1970.
  assert.match(overviewPanel, /if \(!props\.value\)[\s\S]{0,120}not reported/);
});

test("the panel is read-only, and points at the live controls rather than copying them", () => {
  // The live surfaces (war-room, RSI, perpetual, canary, blueprints) already have
  // a home in the Workforce view. Duplicating them here would give the operator
  // two controls for one thing; the panel links instead.
  assert.match(overviewPanel, /onOpenLive/);
  assert.match(overviewPanel, /Live controls/);
  assert.doesNotMatch(overviewPanel, /\bmethod:\s*"(POST|PATCH|PUT|DELETE)"/, "the digest must not mutate");
  assert.doesNotMatch(overviewPanel, /<input/, "a read-only digest must not offer editable fields");
});
