// workspace-nav.test.mjs — every workspace view must be reachable from the
// navigation that owns it.
//
// The defect this pins: `NavTabs` hardcoded three dropdown groups
// (collaboration, operations, system) while `deliberation` was declared with
// `category: "core"` and no `isPrimary`. It was therefore rendered by no group
// in "More Views" — a view with a render case in `ChatView` that the menu
// could not open, and an active tab label on a dropdown that did not contain
// it. Nothing failed, because no test counted tabs against groups.
//
// These are source pins over the real component: they read `NavTabs.tsx` and
// compare the three declarations against each other, which is enough because
// the dropdown's membership is a pure function of them.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const SRC = readFileSync(new URL("../components/NavTabs.tsx", import.meta.url), "utf8");

/** The slice of source between `start` (inclusive) and `end` (exclusive). */
function slice(start, end) {
  const from = SRC.indexOf(start);
  assert.notEqual(from, -1, `expected to find \`${start}\``);
  const to = SRC.indexOf(end, from);
  assert.notEqual(to, -1, `expected to find \`${end}\` after \`${start}\``);
  return SRC.slice(from, to);
}

/** The `WorkspaceView` union — the full set of views the app can render. */
function unionIds() {
  const block = slice("export type WorkspaceView", ";");
  return [...block.matchAll(/"([a-z][a-z-]*)"/g)].map((m) => m[1]);
}

/** Tab declarations: id + category, paired in order of appearance. */
function tabs() {
  const block = slice("export const WORKSPACE_TABS", "\n];");
  const ids = [...block.matchAll(/\bid: "([a-z][a-z-]*)"/g)].map((m) => m[1]);
  const categories = [...block.matchAll(/category: "([a-z]+)"/g)].map((m) => m[1]);
  assert.equal(ids.length, categories.length, "every tab declares exactly one category");
  return ids.map((id, i) => ({ id, category: categories[i] }));
}

function groupCategories() {
  const block = slice("const SECONDARY_GROUPS", "\n];");
  return [...block.matchAll(/category: "([a-z]+)"/g)].map((m) => m[1]);
}

test("every workspace view has a tab, and every tab is a view", () => {
  const union = new Set(unionIds());
  const tabIds = tabs().map((t) => t.id);

  const unrenderable = tabIds.filter((id) => !union.has(id));
  assert.deepEqual(
    unrenderable,
    [],
    "WORKSPACE_TABS declares views ChatView cannot route to (add them to the WorkspaceView union or drop the tab)",
  );

  const unreachable = [...union].filter((id) => !tabIds.includes(id));
  assert.deepEqual(unreachable, [], "views with no tab are unreachable from the navigation");
});

test("every tab category has exactly one group in the More Views menu", () => {
  const groups = groupCategories();
  const used = [...new Set(tabs().map((t) => t.category))];

  const missing = used.filter((category) => !groups.includes(category));
  assert.deepEqual(
    missing,
    [],
    "a category with no dropdown group renders no menu entry — this is how " +
      "`deliberation` (category `core`) became unreachable from a menu that " +
      "showed its active label",
  );

  const empty = groups.filter((category) => !used.includes(category));
  assert.deepEqual(empty, [], "a group whose category no tab uses renders an empty heading");
});

test("the menu is driven by SECONDARY_GROUPS, not by a hardcoded copy", () => {
  // If someone re-inlines the three groups, the coverage tests above can still
  // pass against a list the render no longer reads.
  assert.match(SRC, /SECONDARY_GROUPS\.map\(/, "the dropdown must iterate SECONDARY_GROUPS");
  assert.doesNotMatch(
    SRC,
    /\.filter\(\(t\) => t\.category === "(collaboration|operations|system|core)"\)/,
    "per-category filters mean the groups list is no longer what decides membership",
  );
});

test("the previously unreachable view is in the menu", () => {
  const tab = tabs().find((t) => t.id === "deliberation");
  assert.ok(tab, "deliberation still declares a tab");
  assert.ok(
    groupCategories().includes(tab.category),
    `deliberation's category \`${tab.category}\` has a group`,
  );
});
