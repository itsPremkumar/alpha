// reliability-view.test.mjs — the Validation view is reachable and honest.
//
// Two separate contracts, tested together because they fail together:
//
//  1. **Reachability.** A section that is not in the view registry is dead
//     code, and one registered in only some of the three required places renders
//     nothing while the nav still offers it. The id must appear in the view
//     union, the tab list, and the render switch.
//
//  2. **Honesty.** This panel is read by someone deciding whether the agent is
//     actually working. Each of its states must stay distinguishable from a
//     healthy one: a failed read, a readable-but-empty ledger, and a pass are
//     three different sentences, and the first two must never borrow the third.
//
// node --test src/lib/*.test.mjs — no server, no browser, no DOM shim.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");

const navTabs = read("../components/NavTabs.tsx");
const chatView = read("../components/ChatView.tsx");
const section = read("../components/sections/ReliabilitySection.tsx");
const client = read("./reliability.ts");

test("the view id exists in the workspace union", () => {
  assert.match(navTabs, /export type WorkspaceView =[\s\S]*?\| "reliability";/);
});

test("the tab is declared exactly once, with a label and a blurb", () => {
  // A duplicated entry renders twice; the nav suite catches that for the whole
  // list, and this pins the one entry that answers "did the operator's click go
  // anywhere".
  const entries = navTabs.match(/\{ id: "reliability"/g) ?? [];
  assert.equal(entries.length, 1);
  assert.match(navTabs, /\{ id: "reliability", label: "Validation"[^}]*blurb: "[^"]+"/);
});

test("the section is lazy-imported and rendered for its view id", () => {
  // `next/dynamic`, not `React.lazy`: a lazy section cannot be server-rendered,
  // so the server shipped the Suspense fallback and React discarded the whole
  // server tree on hydration. Both are on-demand loads.
  assert.match(chatView, /dynamic\(\(\) => import\("@\/components\/sections\/ReliabilitySection"\)/);
  assert.match(chatView, /view === "reliability" \? \([\s\S]*?<ReliabilitySection \/>/);
});

test("the panel polls, because a wave is running somewhere else", () => {
  // A panel that reads once shows a snapshot and calls it progress.
  assert.match(section, /setInterval\(/);
  assert.match(section, /POLL_MS/);
});

test("a failed read is disclosed as a failed read, never as an empty result", () => {
  assert.match(section, /This is a failed read, not an empty result\./);
  // The server's own reason is rendered, not a generic apology.
  assert.match(section, /matrix\.reasonText/);
});

test("an unreadable ledger renders the server's notice rather than a table", () => {
  assert.match(section, /\{!matrix\.reported && <Notice message=\{matrix\.reasonText\} \/>\}/);
});

test("a readable but empty ledger says nothing was validated yet", () => {
  assert.match(section, /No workloads have run yet/);
  // And it must not be phrased as an absence of problems.
  assert.doesNotMatch(section, /No workloads have run yet[^"]*all (clear|good|passing)/i);
});

test("absent measurements render as a dash, never as 0", () => {
  assert.match(section, /const NOT_REPORTED = "—"/);
  assert.match(section, /matrix\.total === null \? "not reported" : String\(matrix\.total\)/);
  assert.match(section, /matrix\.broken === null \? "not reported" : String\(matrix\.broken\)/);
});

test("a truncated ledger discloses the bound instead of hiding it", () => {
  assert.match(section, /matrix\.truncated/);
  assert.match(section, /Showing \{matrix\.returned\} of \{matrix\.total\} recorded workloads/);
});

test("the broken workloads are named, so a red row is actionable", () => {
  assert.match(section, /did not pass/);
  assert.match(section, /verdictTone\(row\.verdict\)\.label/);
});

test("the server's own failure text is shown beside the verdict", () => {
  // A verdict of FAIL with no reason is an unactionable failure.
  assert.match(section, /workload\.serverError/);
  assert.match(section, /Gateway reported:/);
});

test("a poll that fails does not blank the last known matrix", () => {
  // Wiping known state on a transient poll failure would report "unknown" for
  // the one surface whose job is showing known state.
  assert.match(section, /if \(initial\) setLoading\(true\)/);
  assert.match(section, /loading && !matrix/);
});

test("an unrecognised verdict stays visible as written", () => {
  // The client preserves the monitor's word; the section renders that label
  // rather than substituting a bucket of its own.
  assert.match(client, /label: verdict \|\| "UNKNOWN"/);
  assert.match(section, /\{tone\.label\}/);
});

test("the panel states that verdicts come from evidence checks, not status codes", () => {
  assert.match(section, /evidence checks, not status codes/);
});
