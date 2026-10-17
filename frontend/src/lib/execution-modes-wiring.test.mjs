// execution-modes-wiring.test.mjs - the mode catalogue must stay reachable,
// and its rows must stay read-only.
//
// WHY THIS IS A SOURCE-PIN SUITE
// ------------------------------
// `execution-modes-view.test.mjs` drives the pure projection and passes just as
// happily when nothing renders it. That is the exact wiring defect this repo has
// hit before: a client and a view module that no component imports (see
// `swarm-structure-wiring.test.mjs`, where `swarmDetails()` was fetched by
// nobody). The claim here is "a section renders this, and the nav offers it",
// which no runtime assertion in a Node test can observe, so these are source
// pins. Each names a specific string a refactor would silently drop.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");

const chatview = read("../components/ChatView.tsx");
const navTabs = read("../components/NavTabs.tsx");
const viewIds = read("./workspace-view.ts");
const section = read("../components/sections/ExecutionModesSection.tsx");
const arena = read("./arena.ts");

/** Collapse whitespace so a prettier reflow cannot fail a source pin. */
const flat = (s) => s.replace(/\s+/g, " ");

// ---------------------------------------------------------------------------
// The four-place wiring
// ---------------------------------------------------------------------------

test("the view id exists in WORKSPACE_VIEW_IDS, so ?view= resolves", () => {
  // Without this, `?view=execution-modes` silently falls back to `chat` while
  // the nav still offers a tab that appears to go nowhere.
  assert.match(viewIds, /"execution-modes"/);
});

test("the view id is a member of the WorkspaceView union", () => {
  assert.match(navTabs, /\| "execution-modes"/);
});

test("the nav offers the tab with a blurb", () => {
  const row = flat(navTabs).match(/\{ id: "execution-modes"[^}]*\}/);
  assert.ok(row, "no WORKSPACE_TABS row for execution-modes");
  assert.match(row[0], /label: "Execution modes"/);
  assert.match(row[0], /blurb: "[^"]+"/);
});

test("ChatView lazy-imports the section and renders it for this view", () => {
  assert.match(chatview, /import\("@\/components\/sections\/ExecutionModesSection"\)/);
  // The render case, not just the import: an import nothing renders is the
  // orphaned-panel defect.
  assert.match(
    flat(chatview),
    /view === "execution-modes" \? \( <Suspense fallback=\{<SectionFallback \/>\}> <ExecutionModesSection/,
  );
});

test("the section is handed a way to navigate to the owning view", () => {
  // A catalogue row that cannot take you anywhere is a list, not a way to find
  // the thing it lists.
  assert.match(section, /onOpenView\?: \(view: string\) => void/);
  assert.match(flat(chatview), /<ExecutionModesSection onOpenView=\{/);
});

// ---------------------------------------------------------------------------
// Read-only by construction
// ---------------------------------------------------------------------------

test("the section sends nothing: it is a catalogue, not a control panel", () => {
  // Every knob it could offer does not exist on the Gateway. A `send` here
  // would 405 or 404 the moment anyone turned a "value" into a control.
  assert.doesNotMatch(section, /\bsend\s*</);
  assert.doesNotMatch(section, /from "@\/lib\/http"[^;]*\bsend\b/);
});

test("the section reads the Arena ceilings through the arena client", () => {
  assert.match(section, /getArenaConfig\(\)/);
  assert.match(section, /getArenaCatalogue\(\)/);
});

test("the arena client reads the two real routes and invents no others", () => {
  assert.match(arena, /get<Record<string, unknown>>\("\/arena\/config"\)/);
  assert.match(arena, /get<Record<string, unknown>>\("\/arena\/profiles"\)/);
});

// ---------------------------------------------------------------------------
// The honesty the surface promises
// ---------------------------------------------------------------------------

test("an unreported value renders as a dash, never as zero", () => {
  assert.match(section, /function factValue/);
  assert.match(section, /if \(value === null\) return "—"/);
  assert.doesNotMatch(section, /return "0"/);
});

test("a failed read keeps the half that answered", () => {
  // `Promise.all` would throw away a `/arena/config` that answered when
  // `/arena/profiles` did not.
  assert.match(section, /Promise\.allSettled/);
});

test("each row carries its readiness state in the DOM", () => {
  // The three-state contract has to be inspectable, not only implied by colour.
  assert.match(section, /data-readiness=\{family\.readiness\}/);
  assert.match(section, /data-execution-mode=\{family\.id\}/);
});