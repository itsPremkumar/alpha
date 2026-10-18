import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

/**
 * Structural pins for the bot detail **page**.
 *
 * The client suite (`bot-detail.test.mjs`) drives the mappers. This file pins
 * the things a mapper test cannot see, because they are properties of how the
 * page is assembled rather than of any one function:
 *
 *   1. **The fan-out is `Promise.allSettled`.** One `Promise.all` anywhere in
 *      `fetchBotDetail` would let a single 503 blank every section that did
 *      answer, which is the one failure this page exists not to have.
 *   2. **Every section has its own read.** The page claims to answer
 *      "everything about one bot"; a section that renders from another
 *      section's payload is a claim the read does not support.
 *   3. **Presentation lives in `detail-primitives.tsx`.** A second `Section`
 *      in the view is how "Projects (0)" ends up over a failed read — the
 *      empty state and the error state drift apart with nothing failing.
 *   4. **Redirections use `workspaceViewUrl`.** Hand-built `/?view=` strings
 *      bypass the one helper that owns the query shape.
 */

const view = readFileSync(new URL("../components/bots/BotDetailView.tsx", import.meta.url), "utf8");
const primitives = readFileSync(
  new URL("../components/bots/detail-primitives.tsx", import.meta.url),
  "utf8",
);
const client = readFileSync(new URL("./bot-detail.ts", import.meta.url), "utf8");

test("the detail fan-out settles each section independently", () => {
  assert.match(client, /Promise\.allSettled\(/, "sections must settle independently");
  assert.doesNotMatch(
    client.replace(/Promise\.allSettled\(/g, ""),
    /Promise\.all\(/,
    "a bare Promise.all would let one failed read blank the sections that answered",
  );
});

test("the client reads every route the page's sections render", () => {
  // Each of these is a distinct Gateway route; dropping one silently turns its
  // section into a permanent "loading" that nothing will ever resolve.
  for (const route of [
    "/bots/${key}/performance",
    "/bots/${key}/inbox",
    "/bots/${key}/chat",
    "/bots/${key}/projects",
    "/bots/${key}/groups",
    "/bots/${key}/companies",
    "/bots/health/overview",
    "/bots/organization-chart",
    "/bots/working",
    "/bots/events",
    "/bots/${key}/capabilities",
  ]) {
    assert.ok(client.includes(route), `expected the client to read ${route}`);
  }
  // The kill switch is read through `bot-working-status`, which owns that
  // envelope; the page must still consume it, which the section test below
  // pins rather than this one.
  const working = readFileSync(new URL("./bot-working-status.ts", import.meta.url), "utf8");
  assert.ok(working.includes("/bots/kill-switch"));
});

test("the page renders the sections the client reads", () => {
  for (const title of [
    "Identity",
    "Soul",
    "Capabilities",
    "Skills & toolsets",
    "Working now",
    "Performance",
    "Liveness",
    "Projects",
    "Groups & subgroups",
    "Company employment",
    "Board tasks",
    "Chat history",
    "Direct messages",
    "Routines",
    "Organization position",
    "Fleet context",
    "Organization events",
  ]) {
    assert.ok(view.includes(`"${title}"`), `expected a "${title}" section`);
  }
});

test("presentation primitives are imported, never re-declared in the view", () => {
  // A local `function Section` would let the new sections diverge from the
  // ones that already render an error state, and nothing would fail.
  assert.doesNotMatch(view, /function Section\b/);
  assert.doesNotMatch(view, /function Row\b/);
  assert.doesNotMatch(view, /function TagList\b/);
  assert.doesNotMatch(view, /function StatusBadge\b/);
  assert.match(view, /from "\.\/detail-primitives"/);
});

test("the primitives module owns the failed-read and not-reported sentences", () => {
  assert.match(primitives, /Could not load this section\./);
  assert.match(primitives, /not reported/);
  assert.match(primitives, /unknown to this build/);
});

test("redirections are built by workspaceViewUrl, never by a hand-built query", () => {
  assert.match(view, /workspaceViewUrl\(r\.view\)/);
  assert.match(view, /workspaceViewUrl\("projects",\s*"\/",\s*\{ project: row\.id \}\)/);
  assert.match(view, /\{row\.id && \(/, "do not offer a project link without a project id");
  // A hand-built `/?view=` would bypass the helper that owns the shape and
  // would not survive a change to how a view is addressed.
  assert.doesNotMatch(view, /href=\{`\/\?view=/);
});

test("the page withholds a chat deep-link nothing can honour", () => {
  // `ChatView` reads no bot parameter, so a link promising to open *this*
  // bot's chat would perform a selection it cannot make. The canonical thread
  // id is printed instead, which is a fact rather than a control.
  assert.doesNotMatch(view, /view=chat&bot=/);
  assert.doesNotMatch(view, /&bot=\$\{/);
  assert.match(view, /Canonical thread id/);
});

test("the board read is its own state, not folded into the bundle", () => {
  // `fetchKanbanTasks` lives behind a different client with a different
  // failure mode; folding it into `fetchBotDetail` would take the profile
  // down with a board outage. The filter helpers stay in the client (they are
  // pure), but the *read* does not.
  assert.match(view, /fetchKanbanTasks\(\)/);
  assert.match(view, /const \[kanban, setKanban\]/);
  const bundle = client.slice(client.indexOf("export interface BotDetailBundle"), client.indexOf("export interface BotInboxMessage"));
  assert.doesNotMatch(bundle, /kanban/i, "the per-section bundle must not grow a board read");
});

test("both counts travel together on every group row", () => {
  // "1 direct" over six visible members would be a fabricated count, so the
  // page renders the direct and effective numbers as a pair.
  assert.match(view, /room\.direct_count/);
  assert.match(view, /room\.effective_count/);
});
