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
const VIEW_SRC = readFileSync(new URL("./workspace-view.ts", import.meta.url), "utf8");

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

/**
 * `WORKSPACE_VIEW_IDS` in `lib/workspace-view.ts`.
 *
 * A **second** list of the same ids, in a different file, feeding
 * `isWorkspaceView` / `workspaceViewFromSearch`. It is not derived from the
 * `WorkspaceView` union, so nothing stopped the two drifting — and both
 * `run-inspector` and `reliability` shipped with a tab and a render case while
 * this list still said the view did not exist. The effect is silent and
 * specific: `?view=<id>` falls back to `chat`, so the view cannot be opened by
 * link, by search-driven navigation, or by anything else that resolves a view
 * from a URL.
 */
function routeViewIds() {
  const block = VIEW_SRC.slice(VIEW_SRC.indexOf("export const WORKSPACE_VIEW_IDS"), VIEW_SRC.indexOf("] as const"));
  // Line-anchored on purpose. The explanatory comments beside two of these
  // entries quote the id itself (``isWorkspaceView("reliability")``), so a
  // whole-array regex matches the prose too and reports every commented id as a
  // duplicate. Only a line that is nothing but an array entry counts.
  return [...block.matchAll(/^\s*"([a-z][a-z-]*)",?\s*$/gm)].map((m) => m[1]);
}

test("the routable view list agrees with the WorkspaceView union", () => {
  const union = unionIds();
  const routable = routeViewIds();

  // Duplicates in the routable list are harmless at runtime but mean the list
  // was edited by appending rather than by understanding the set.
  assert.deepEqual(
    routable.filter((id, index) => routable.indexOf(id) !== index),
    [],
    "WORKSPACE_VIEW_IDS contains a duplicate id",
  );

  const notRoutable = union.filter((id) => !routable.includes(id));
  assert.deepEqual(
    notRoutable,
    [],
    "views ChatView can render but isWorkspaceView() rejects: ?view=<id> would silently fall back to chat (add them to WORKSPACE_VIEW_IDS)",
  );

  const notRenderable = routable.filter((id) => !union.includes(id));
  assert.deepEqual(notRenderable, [], "WORKSPACE_VIEW_IDS names views with no WorkspaceView union member");
});

test("the routable view list is the same set the tabs declare", () => {
  assert.deepEqual(
    [...routeViewIds()].sort(),
    [...unionIds()].sort(),
    "WORKSPACE_VIEW_IDS and the WorkspaceView union are the same set of views",
  );
});

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

/* --- the view finder ----------------------------------------------------- */

test("the More Views panel can be filtered by what a view does, not only its name", () => {
  // 28 views behind one button is not discovery, it is a memory test. The
  // panel's real content is the list of blurbs, so an operator looking for
  // "what the agent remembers" types `memory`; the label happens to match, but
  // "where do I see what this cost" does not, and that row lives in the Usage
  // blurb. Searching only labels would make that row unfindable by intent.
  assert.match(SRC, /t\.label\.toLowerCase\(\)\.includes\(needle\)/, "the filter must read the label");
  assert.match(SRC, /t\.blurb\.toLowerCase\(\)\.includes\(needle\)/, "the filter must read the blurb");
  assert.match(SRC, /t\.id\.toLowerCase\(\)\.includes\(needle\)/, "the filter must read the view id too");
});

test("a filtered list says how many of the views matched", () => {
  // Without this, 5 rows under a 28-view panel reads as the whole list: the
  // operator concludes the other 23 views were removed rather than filtered.
  assert.match(
    SRC,
    /data-view-match-count=\{matchedCount\}/,
    "the match count must be rendered so it can be driven directly",
  );
  assert.match(
    SRC,
    /`\$\{matchedCount\} of \$\{secondaryTabs\.length\} views match/,
    "the sentence must name both numbers, the shown and the total",
  );
});

test("a heading is never rendered without rows under it", () => {
  // A group heading over nothing is a claim that a category is empty. When the
  // filter removes every row in a category, that heading must go with them —
  // otherwise a narrowed list shows four groups and three of them are lies.
  assert.match(
    SRC,
    /\.filter\(\(entry\) => entry\.tabs\.length > 0\)/,
    "groups with no matching views must be dropped, not rendered empty",
  );
});

test("a filter that matches nothing says so, and names what the filter reads", () => {
  // Silence after a search is indistinguishable from a broken panel: the
  // operator cannot tell whether the view does not exist or whether they
  // spelled it differently from the blurb.
  assert.match(SRC, /No view matches/, "the empty filter state must be worded");
  assert.match(SRC, /filter reads each view's/, "and say that it reads names and descriptions");
});

test("Escape clears the query before it closes the panel", () => {
  // Closing on the first press throws away a half-typed query and lands the
  // operator back at the top of the list they were narrowing.
  assert.match(SRC, /if \(query\.trim\(\)\)/, "an active query is cleared first");
  assert.match(SRC, /setQuery\(""\)/, "by clearing it, not by closing");
  assert.match(SRC, /\}, \[dropdownOpen, placePanel, query\]\);/, "the handler must see the current query");
});

test("the finder takes focus when the panel opens", () => {
  // A panel that opens and waits for a mouse move is a panel that invites
  // scanning 25 rows instead of typing three characters.
  assert.match(
    SRC,
    /if \(dropdownOpen && panel\) searchRef\.current\?\.focus\(\)/,
    "opening focuses the finder, and only once a panel exists to focus it into",
  );
  assert.match(SRC, /ref=\{searchRef\}/, "the input holds the ref that focus targets");
  // Keyed on `panel`, not just `dropdownOpen`. The panel is portalled with
  // `visibility: hidden` until `placePanel` has measured it and `setPanel`
  // lands in a second commit, so a focus effect that runs on the opening commit
  // alone calls `.focus()` on a hidden element and never runs again. This is a
  // BEHAVIOURAL pin: the source-line version of it passed while the finder
  // never actually took focus.
  assert.match(
    SRC,
    /\}, \[dropdownOpen, panel\]\);/,
    "the focus effect must re-run once the panel is placed, or it focuses a still-hidden node",
  );
});

test("closing the panel clears the query", () => {
  // Reopening the menu must not show a stale filter that still hides most of
  // the views, which reads as a navigation that lost views.
  assert.match(SRC, /setPanel\(null\);\s*setQuery\(""\)/, "close resets both the panel and the query");
});

test("the finder's derivation runs after the list it reads", () => {  // THE REGRESSION THIS PINS. The match block was written above
  // `const secondaryTabs = ...`, and `matchedGroups` reads it. `const` is not
  // hoisted, so every server render threw
  //   ReferenceError: Cannot access 'secondaryTabs' before initialization
  // which Next answers with a 500 on the *document* — not on one view, on all
  // 35 of them, because the nav is in the shell. It was invisible in unit
  // tests (this file only reads source) and invisible in the dev server until
  // the first render, and it made every route in the product return 500.
  //
  // Position is therefore the behaviour here: the block must sit below the
  // declaration it reads. `useCallback`/`useEffect` are allowed above because
  // their bodies run after render, so they can legally close over later
  // bindings.
  const line = (needle, fromEnd = false) => {
    const text = SRC.split("\n");
    const index = fromEnd ? text.length - 1 - [...text].reverse().findIndex((t) => t.includes(needle)) : text.findIndex((t) => t.includes(needle));
    assert.notEqual(index, -1, `expected to find \`${needle}\` in NavTabs.tsx`);
    return index;
  };
  const secondary = line("const secondaryTabs =");
  const matched = line("const matchedGroups =");
  const needleAt = line("const needle =");
  // The render return is the LAST `return (` in the component, not the first:
  // the component already returns early inside `placePanel` and the focus
  // effect, and matching one of those would falsely "find" a return above the
  // derivation and fail a correct file.
  const ret = line("return (", true);
  assert.ok(
    secondary < matched,
    `matchedGroups (line ${matched + 1}) must be declared after secondaryTabs (line ${secondary + 1}); ` +
      "it reads it during render, so this order is what makes SSR throw a 500 on every route",
  );
  assert.ok(secondary < needleAt, "needle is derived from the same list and must follow it too");
  assert.ok(matched < ret, "the derivation must still precede the return that renders it");
});

test("the Vitals detail view is wired in all four places", () => {
  // The header vitals strip summarises six measurements and its clusters now
  // open this view. A view the nav offers but the view registry does not know
  // silently falls back to `chat`, which is exactly the defect class this
  // file was written for.
  const sources = {
    union: SRC,
    workspaceView: readFileSync(new URL("./workspace-view.ts", import.meta.url), "utf8"),
    chatView: readFileSync(new URL("../components/ChatView.tsx", import.meta.url), "utf8"),
  };
  for (const [name, src] of Object.entries(sources)) {
    assert.ok(src.includes("vitals"), `${name} must declare the vitals view`);
  }
  assert.match(sources.chatView, /VitalsSection/, "ChatView must import the section");
  assert.match(sources.chatView, /view === "vitals"/, "ChatView must render a case for it");
});

test("the vitals strip opens the detail page rather than dead-ending", () => {
  // A strip that only summarises is a dead end: the operator's next question
  // is always "show me". The clusters are the target, not a chevron beside
  // them, so there is one obvious thing to click.
  const vitals = readFileSync(new URL("../components/WorkspaceVitals.tsx", import.meta.url), "utf8");
  assert.match(vitals, /onOpenVitals/, "the strip must accept an open handler");
  assert.match(vitals, /onOpen=\{onOpenVitals\}/, "every cluster must pass it to Cluster");
  assert.match(vitals, /<button[\s\S]{0,400}onClick=\{onOpen\}/, "Cluster must render a real button that navigates");
});
