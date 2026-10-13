// Contract tests for the sidebar's single scroll region and its fixed row cap.
//
// Two facts had to hold at once, and the old code had neither:
//
//   1. The sidebar handed its entire scroll budget to the Projects section
//      while Standalone — where 237 of 278 conversations live — got no scroll
//      region at all: `overflow: visible`, and the outer `overflow-y-auto`
//      measured scrollHeight === clientHeight === 438, so it could never
//      scroll. An operator could not reach an old conversation by scrolling.
//   2. The remedy on offer was a six-row cap plus a "Show N more" click —
//      which, without a working scroller, just swapped one unreachable list
//      for an overflowing one.
//
// So both halves are pinned: ONE scroller that carries the history footer
// with it, and a single fixed number governing how many rows show before the
// operator's own Show more / Show fewer decision.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const rail = readFileSync(
  new URL("../components/chat-shell/BotWorkspaceRail.tsx", import.meta.url),
  "utf8",
);
const sidebar = readFileSync(
  new URL("../components/ThreadSidebar.tsx", import.meta.url),
  "utf8",
);

/**
 * Walk `<div>` nesting from `from` and return the offset just past the div
 * opened there. A self-closing `<div … />` opens and closes at once.
 */
function divCloseOffset(source, from) {
  let depth = 0;
  const tags = source.slice(from).match(/<\/?div\b[^>]*>/g) ?? [];
  let cursor = from;
  for (const tag of tags) {
    const at = source.indexOf(tag, cursor);
    cursor = at + tag.length;
    if (tag.startsWith("</")) {
      depth -= 1;
    } else if (!tag.endsWith("/>")) {
      depth += 1;
    }
    if (depth === 0) return cursor;
  }
  return -1;
}

test("the rail is natural height so the sidebar's scroller can scroll", () => {
  // `h-full` pinned the rail to exactly its container's height, leaving the
  // parent with scrollHeight === clientHeight and nothing to scroll.
  const root = rail.match(
    /<div\s+className="([^"]+)"\s+data-shell="bot-workspace"/,
  );
  assert.ok(root, "rail root div not found");
  assert.ok(
    !/\bh-full\b/.test(root[1]),
    `rail root must not be height-locked to its container, got: ${root[1]}`,
  );
});

test("Projects is not a nested scroller competing for the scroll budget", () => {
  // The section declares className *before* aria-label, so the match has to
  // stop at the tag's own `>` — scanning forward would read a child's
  // className and pass on a section that still scrolls internally.
  const tag = rail.match(/<section\b[^>]*aria-label="Projects"[^>]*>/);
  assert.ok(tag, "Projects section not found");
  const cls = tag[0].match(/className="([^"]+)"/);
  assert.ok(cls, "Projects section has no className");
  assert.ok(
    !/overflow-y-auto/.test(cls[1]),
    `Projects must render in full, not scroll internally: ${cls[1]}`,
  );
});

test("a fixed number decides how many rows show", () => {
  // One number, one place. Two literals is how "show 6" in the standalone
  // list and "show 6" in the project list stop meaning the same thing.
  const limit = rail.match(/^\s*(?:export\s+)?const SIDEBAR_ROW_LIMIT = (\d+);$/m);
  assert.ok(limit, "SIDEBAR_ROW_LIMIT must be the single source of the cap");
  assert.ok(
    Number(limit[1]) > 0,
    `the fixed number must be positive, got ${limit[1]}`,
  );

  const slices = rail.match(/\.slice\(0,\s*[^)]+\)/g) ?? [];
  assert.equal(
    slices.filter((s) => /SIDEBAR_ROW_LIMIT/.test(s)).length,
    2,
    "both lists must slice on the shared constant",
  );
  assert.ok(
    !slices.some((s) => /^\.\s*slice\(0,\s*\d+\)$/.test(s.replace(/\s+/g, ""))),
    `no bare numeric cap may remain beside the constant: ${slices.join(" | ")}`,
  );
});

test("Show more / Show fewer stay available on both lists", () => {
  // The manual override is the feature: the cap is a default, not a wall.
  assert.match(
    rail,
    /Show \{hiddenStandalone\} more chats\.\.\./,
    "the standalone list needs its Show more control",
  );
  assert.match(
    rail,
    /Show \{hiddenProjects\} more projects\.\.\./,
    "the project list needs its Show more control",
  );
  assert.equal(
    (rail.match(/^\s*Show fewer$/gm) ?? []).length,
    2,
    "each list needs its Show fewer control",
  );
  for (const flag of ["showAllStandalone", "showAllProjects"]) {
    const uses = rail.split(flag).length - 1;
    assert.ok(
      uses >= 3,
      `${flag} must be declared, read and cleared — found ${uses} uses`,
    );
  }
  // The control must actually flip: both lists can expand and both can
  // collapse again, so neither may read the flag without writing it.
  assert.match(rail, /setShowAllStandalone\(true\)/);
  assert.match(rail, /setShowAllStandalone\(false\)/);
  assert.match(rail, /setShowAllProjects\(true\)/);
  assert.match(rail, /setShowAllProjects\(false\)/);
});

test("the sidebar has exactly one scroll region", () => {
  const scrollers = sidebar.match(/overflow-y-auto/g) ?? [];
  assert.equal(
    scrollers.length,
    1,
    `expected one scroll region, found ${scrollers.length}`,
  );
  assert.match(
    sidebar,
    /<div className="flex-1 min-h-0 overflow-y-auto">/,
    "the one scroller must be the sidebar's flex child",
  );
});

test("the history footer rides inside that scroll region", () => {
  const at = sidebar.indexOf('<div className="flex-1 min-h-0 overflow-y-auto">');
  assert.ok(at >= 0, "scroll region not found");
  const close = divCloseOffset(sidebar, at);
  assert.ok(close > at, "scroll region never closes");

  const footer = sidebar.indexOf("Erase local history");
  assert.ok(footer > at, "footer is not in the document after the scroller");
  assert.ok(
    footer < close,
    "the footer is pinned *below* the scroller — it must travel with it, so " +
      "one scrollbar reaches every conversation, Backup/Restore and the version chip",
  );
  assert.ok(
    sidebar.indexOf("</aside>") > close,
    "the scroller must close inside the aside",
  );
});
