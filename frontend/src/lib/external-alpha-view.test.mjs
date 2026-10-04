// external-alpha-view.test.mjs — the five places a workspace view must appear.
//
// A view that is not reachable from the view registry is dead code, so this
// pins every registration point. It also pins the two rules that make a new tab
// render correctly: exactly one WORKSPACE_TABS entry (the primary row and the
// More Views dropdown both derive from that array, so a duplicate renders the
// tab twice), and a category that has a dropdown heading.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");

const nav = read("../components/NavTabs.tsx");
const views = read("../lib/workspace-view.ts");
const chatView = read("../components/ChatView.tsx");
const section = read("../components/sections/ExternalAlphaSection.tsx");

test("external-alpha is in the workspace-view union", () => {
  assert.match(nav, /\| "external-alpha"/);
});

test("external-alpha is in WORKSPACE_VIEW_IDS", () => {
  assert.match(views, /"external-alpha"/);
});

test("the tab is registered exactly once in WORKSPACE_TABS", () => {
  // Both the primary row and the "More Views" dropdown read this one array, so
  // a second entry would render the tab twice.
  const entries = nav.match(/id:\s*"external-alpha"/g) ?? [];
  assert.equal(entries.length, 1, `expected exactly one WORKSPACE_TABS entry, found ${entries.length}`);
});

test("the tab declares a collaboration category that has a dropdown heading", () => {
  // The historical `deliberation` bug: a tab whose category had no heading in
  // SECONDARY_GROUPS was unreachable from the dropdown.
  const entry = nav.match(/\{ id: "external-alpha"[^\n]*\}/)?.[0] ?? "";
  assert.match(entry, /category: "collaboration"/);
  assert.match(nav, /\{ category: "collaboration", heading: "Collaboration & Team" \}/);
});

test("the section is loaded on demand", () => {
  // `next/dynamic`, not `React.lazy`: a lazy section cannot be server-rendered,
  // so the server shipped the Suspense fallback and React discarded the whole
  // server tree on hydration. Both forms are on-demand loads.
  assert.match(chatView, /dynamic\(\(\) => import\("@\/components\/sections\/ExternalAlphaSection"\)/);
});

test("the view has a render branch", () => {
  assert.match(chatView, /view === "external-alpha"/);
});

test("the tab is labelled for the cross-installation surface", () => {
  assert.match(nav, /label: "External Alpha"/);
});

test("the section is exported both ways ChatView consumes it", () => {
  assert.match(section, /export function ExternalAlphaSection\(\)/);
  assert.match(section, /export default ExternalAlphaSection/);
});

test("the section has every sub-tab", () => {
  for (const tab of ["overview", "conversations", "search", "timeline", "forensics"]) {
    assert.match(section, new RegExp(`id: "${tab}"`));
  }
});

// ── the advanced surface ───────────────────────────────────────────────────

test("search and analytics are wired to the Gateway, not faked locally", () => {
  assert.match(section, /searchTranscripts/);
  assert.match(section, /getTranscriptAnalytics/);
});

test("a search miss is not presented as proof nothing was sent", () => {
  // Retention prunes history, so "no results" has at least two causes and the UI
  // must not pick the reassuring one.
  assert.match(section, /not proof the message was never sent/);
});

test("search quality is named rather than implied", () => {
  // Ranked FTS and a substring scan are different; calling both "search" would
  // overstate the fallback.
  assert.match(section, /ranked full-text/);
  assert.match(section, /substring scan/);
});

test("the behaviour-trace empty state keeps the Gateway's caveat", () => {
  // An empty trace list means "the writer emitted none", not "nothing happened".
  assert.match(section, /hint=\{data\.note\}/);
  assert.match(section, /No behaviour traces recorded/);
});

test("an unavailable FTS5 build is stated, not silently degraded", () => {
  assert.match(section, /no FTS5/);
  assert.match(section, /analytics\.fts_available/);
});

test("analytics numbers are server-measured, not summed client-side", () => {
  // Rendering a locally-counted total would let a partial page look complete.
  assert.match(section, /analytics\.totals\.messages/);
});

// ── honesty guarantees the tab must not lose ────────────────────────────────

test("peer text is never injected as HTML", () => {
  // Peer text is untrusted remote data. Raw-HTML injection here would hand
  // another installation script execution in this operator's browser. Built
  // dynamically so the guard's own comment naming the risk cannot satisfy it.
  const sink = ["dangerously", "SetInnerHTML"].join("");
  assert.ok(!section.includes(sink), `ExternalAlphaSection must not use ${sink}`);
  assert.ok(!section.includes("<iframe"), "ExternalAlphaSection must not embed peer content in a frame");
});

test("the two sides are labelled distinctly", () => {
  assert.match(section, /transcriptRoleLabel/);
});

test("an off peer plane is distinguished from an empty transcript", () => {
  // enabled===false must be stated, because the plane defaults OFF and an
  // empty list would otherwise read as "no peers exist".
  assert.match(section, /enabled === false/);
  assert.match(section, /disabled on this installation/);
});

test("truncation is rendered rather than silently trimmed", () => {
  assert.match(section, /transcript\.truncated/);
  assert.match(section, /events_truncated/);
});

test("empty states state what they are not proof of", () => {
  const empties = section.match(/<EmptyState[^>]*hint="([^"]*)"/g) ?? [];
  assert.ok(empties.length >= 3, `expected several empty states, found ${empties.length}`);
  assert.ok(section.includes("not proof"), "an empty state must say what it does not prove");
});

test("the live stream is torn down on unmount", () => {
  // A leaked SSE reader holds a Gateway connection open, after which the
  // server-side queue drops events for this client.
  assert.match(section, /return \(\) => \{\s*unsubscribe\(\)/);
  assert.match(section, /subscribePeerEvents/);
});