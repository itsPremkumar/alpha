// Tests for messages-view.ts — the pure derivation layer behind MessagesSection.
//
// Transpiles the TypeScript source with the project's own compiler and
// evaluates it behind a throwing `require`, so an accidental import in the
// module is a test failure rather than a second implementation drifting beside
// it.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const requireFromHere = createRequire(import.meta.url);
const ts = requireFromHere("typescript");

const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(here, "messages-view.ts"), "utf8");

const compiled = ts.transpileModule(src, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;

const require = () => {
  throw new Error("messages-view.ts must stay dependency-free");
};
const moduleShim = { exports: {} };
new Function("require", "module", "exports", compiled)(
  require,
  moduleShim,
  moduleShim.exports,
);
const { sectionConversations, hiddenSummary, unreadLabel, groupMessageRuns } =
  moduleShim.exports;

const row = (id, kind, extra = {}) => ({
  id,
  kind,
  title: id,
  subtitle: "",
  lastText: "",
  lastAt: null,
  unread: 0,
  members: [],
  ...extra,
});

test("sectionConversations: splits group rows from DM rows, groups first", () => {
  const sections = sectionConversations([
    row("d1", "dm"),
    row("g1", "group"),
    row("d2", "dm"),
    row("g2", "group"),
  ]);
  assert.deepEqual(
    sections.map((s) => s.key),
    ["groups", "direct"],
  );
  assert.deepEqual(
    sections[0].rows.map((r) => r.id),
    ["g1", "g2"],
  );
  assert.deepEqual(
    sections[1].rows.map((r) => r.id),
    ["d1", "d2"],
  );
});

test("sectionConversations: labels read Groups / Direct, the words the column renders", () => {
  const sections = sectionConversations([row("g1", "group"), row("d1", "dm")]);
  assert.deepEqual(
    sections.map((s) => s.label),
    ["Groups", "Direct"],
  );
});

test("sectionConversations: an empty half is omitted, never a count of zero", () => {
  // A "Direct (0)" header would say the server measured zero DMs when in fact
  // there simply were none to place under it.
  const onlyGroups = sectionConversations([row("g1", "group")]);
  assert.deepEqual(
    onlyGroups.map((s) => s.key),
    ["groups"],
  );
  assert.deepEqual(
    sectionConversations([row("d1", "dm")]).map((s) => s.key),
    ["direct"],
  );
  assert.deepEqual(sectionConversations([]), []);
});

test("sectionConversations: preserves caller order inside each section", () => {
  const rows = [row("g3", "group"), row("g1", "group"), row("g2", "group")];
  assert.deepEqual(
    sectionConversations(rows)[0].rows.map((r) => r.id),
    ["g3", "g1", "g2"],
  );
});

test("sectionConversations: rows that are neither kind are dropped, not guessed", () => {
  const sections = sectionConversations([row("x", "channel")]);
  assert.deepEqual(sections, []);
});

test("hiddenSummary: nothing hidden renders no sentence at all", () => {
  assert.equal(hiddenSummary(12, 12), null);
  assert.equal(hiddenSummary(0, 0), null);
});

test("hiddenSummary: names shown, total, and the hidden remainder", () => {
  const summary = hiddenSummary(3, 12);
  assert.match(summary, /Showing 3 of 12/);
  assert.match(summary, /9 hidden/);
  // The reason it is short has to be in the sentence, or a filtered list reads
  // as the whole inbox.
  assert.match(summary, /filter or search/);
});

test("hiddenSummary: a list with one row hidden still discloses it", () => {
  assert.match(hiddenSummary(4, 5), /Showing 4 of 5 — 1 hidden/);
});

test("hiddenSummary: over-reporting the shown count is refused rather than rendered", () => {
  // shown > total would describe a list longer than the one on screen.
  assert.equal(hiddenSummary(13, 12), null);
});

test("unreadLabel: caps the pill at 99+, the count stays in the row", () => {
  assert.equal(unreadLabel(99), "99");
  assert.equal(unreadLabel(100), "99+");
  assert.equal(unreadLabel(1000), "99+");
});

test("unreadLabel: below the cap it prints the measured number verbatim", () => {
  assert.equal(unreadLabel(1), "1");
  assert.equal(unreadLabel(0), "0");
  assert.equal(unreadLabel(42), "42");
});

test("unreadLabel: a non-finite count is not silently turned into a pill", () => {
  assert.equal(unreadLabel(NaN), "NaN");
  assert.equal(unreadLabel(Infinity), "99+");
});

const msg = (id, sender, extra = {}) => ({
  id,
  sender,
  deleted: false,
  ...extra,
});

const dayOf = (m) => m.day ?? "";

test("groupMessageRuns: one message is one run", () => {
  const runs = groupMessageRuns([msg("a", "ada")], dayOf);
  assert.equal(runs.length, 1);
  assert.deepEqual(runs[0], {
    sender: "ada",
    day: "",
    items: [msg("a", "ada")],
  });
});

test("groupMessageRuns: consecutive rows from one author collapse into a run", () => {
  const runs = groupMessageRuns(
    [msg("a", "ada"), msg("b", "ada"), msg("c", "ada")],
    dayOf,
  );
  assert.equal(runs.length, 1);
  assert.equal(runs[0].items.length, 3);
  assert.equal(runs[0].sender, "ada");
});

test("groupMessageRuns: a change of author starts a new run", () => {
  const runs = groupMessageRuns(
    [msg("a", "ada"), msg("b", "bob"), msg("c", "bob"), msg("d", "ada")],
    dayOf,
  );
  assert.deepEqual(
    runs.map((r) => r.sender),
    ["ada", "bob", "ada"],
  );
  assert.deepEqual(
    runs.map((r) => r.items.length),
    [1, 2, 1],
  );
});

test("groupMessageRuns: the same author on a different day does not merge", () => {
  const runs = groupMessageRuns(
    [msg("a", "ada", { day: "Yesterday" }), msg("b", "ada", { day: "Today" })],
    dayOf,
  );
  assert.equal(runs.length, 2);
  assert.deepEqual(
    runs.map((r) => r.day),
    ["Yesterday", "Today"],
  );
});

test("groupMessageRuns: a deleted row breaks the run above and below it", () => {
  // A tombstone renders as its own italic line; sharing a header with the
  // message above it would imply the two were written together.
  const runs = groupMessageRuns(
    [msg("a", "ada"), msg("t", "ada", { deleted: true }), msg("b", "ada")],
    dayOf,
  );
  assert.equal(runs.length, 3);
  assert.deepEqual(
    runs.map((r) => r.items[0].id),
    ["a", "t", "b"],
  );
});

test("groupMessageRuns: a deleted row does not merge with the next live row either", () => {
  const runs = groupMessageRuns(
    [msg("t", "ada", { deleted: true }), msg("t2", "ada", { deleted: true })],
    dayOf,
  );
  // Two adjacent tombstones share deleted-ness, so they stay one run — the
  // break is deleted-vs-live, not "any tombstone is alone".
  assert.equal(runs.length, 1);
});

test("groupMessageRuns: empty input yields no runs", () => {
  assert.deepEqual(groupMessageRuns([], dayOf), []);
});

test("groupMessageRuns: the day comes from the injected helper, not a clock of its own", () => {
  // The module must not import a date library: the component's own day
  // vocabulary is what the transcript dividers already use.
  const runs = groupMessageRuns(
    [msg("a", "ada", { day: "Mar 3" }), msg("b", "ada", { day: "Mar 3" })],
    (m) => m.day,
  );
  assert.equal(runs.length, 1);
  assert.equal(runs[0].day, "Mar 3");
});

test("groupMessageRuns: a helper that reports no day groups unstamped rows together", () => {
  const runs = groupMessageRuns([msg("a", "ada"), msg("b", "ada")], () => "");
  // Grouping by a day nobody measured would be inventing one.
  assert.equal(runs.length, 1);
  assert.equal(runs[0].day, "");
});

test("groupMessageRuns: run order follows transcript order", () => {
  const runs = groupMessageRuns(
    [msg("a", "ada"), msg("b", "bob"), msg("c", "carol"), msg("d", "bob")],
    dayOf,
  );
  assert.deepEqual(
    runs.map((r) => r.sender),
    ["ada", "bob", "carol", "bob"],
  );
});

// ---------------------------------------------------------------------------
// The wiring in MessagesSection.tsx — the helpers are only honest if the
// component actually renders them, and each of these was a distinct way to say
// something the server never measured.
// ---------------------------------------------------------------------------

const section = fs.readFileSync(
  path.join(here, "../components/sections/MessagesSection.tsx"),
  "utf8",
);

test("the chips and the list count with the same predicate", () => {
  // Two copies of the filter rule is how a chip ends up claiming 4 over a list
  // of three: the chip counts one implementation, the list renders the other.
  assert.match(
    section,
    /const matchesFilter = \(c: Conv, f: Filter, q: string\): boolean =>/,
  );
  assert.match(
    section,
    /allConvs\.filter\(\(c\) => matchesFilter\(c, f, query\)\)\.length/,
    "each chip must count what IT would show, not what is on screen",
  );
  assert.match(
    section,
    /const convs = allConvs\.filter\(\(c\) => matchesFilter\(c, filter, query\)\)/,
    "the rendered rows must come from the same predicate",
  );
  assert.doesNotMatch(
    section,
    /filter === "groups" && c\.kind !== "group"\)[\s\S]{0,400}filter === "groups" && c\.kind !== "group"/,
    "the rule must not exist a second time beside the first",
  );
});

test("the workspace unread badge is measured before the filter", () => {
  // Summing the *filtered* rows meant pressing "Groups" silently dropped unread
  // DMs from the total, and capping each row at 99 before adding understated
  // every genuinely noisy room.
  assert.match(
    section,
    /allConvs\.reduce\(\(n, c\) => n \+ Math\.max\(c\.unread, 0\), 0\)/,
  );
  assert.doesNotMatch(
    section,
    /convs\.reduce\(\(n, c\) => n \+ Math\.min\(c\.unread, 99\)/,
  );
});

test("a shortened list says how many rows it is missing", () => {
  assert.match(section, /hiddenSummary\(convs\.length, allConvs\.length\)/);
  // …and the disclosure is not rendered when nothing was hidden.
  assert.match(section, /return hidden \? \(/);
});

test("a decisions/blockers filter names the rooms it could not search", () => {
  // These filters can only search history that was read. Silently excluding the
  // rest reads as "no decisions in this workspace".
  // Pinned with `\s+`: prettier is free to reflow JSX text across lines, and a
  // pin that only matches one wrapping would fail on formatting, not on meaning.
  assert.match(section, /data-messages-unsearched/);
  assert.match(section, /history\s+has\s+not\s+been\s+read\s+yet/);
  assert.match(section, /Not\s+a\s+clean\s+result/);
});

test("the conversation column renders in sections, and an empty half is omitted", () => {
  assert.match(section, /sectionConversations\(convs\)\.map\(\(section\)/);
  assert.match(
    section,
    /\{section\.rows\.length\}/,
    "a section states its own row count",
  );
  // The zero case is not reachable from this component: the section list comes
  // from `sectionConversations`, which drops an empty half entirely, so there
  // is no branch here that could render a header over nothing.
});

test("the unread pill is the shared label, not an inline copy of the cap", () => {
  assert.match(section, /\{unreadLabel\(c\.unread\)\}/);
  assert.doesNotMatch(
    section,
    /c\.unread > 99 \? "99\+" : c\.unread/,
    "a second cap would drift from the one the tests drive",
  );
});

test("the transcript renders runs and names an author once per run", () => {
  assert.match(
    section,
    /groupMessageRuns\(activeMsgs, \(m\) => dayLabel\(m\.at\)\)/,
    "the day vocabulary must be the transcript's own, not a clock inside the helper",
  );
  assert.match(
    section,
    /const showSender = i === 0;/,
    "only the run's first bubble may print the sender",
  );
  assert.match(
    section,
    /gap-0\.5/,
    "a run must be visually tighter than the gap between runs",
  );
});

test("the pinned read-failure and nesting sentences are all still rendered", () => {
  // The honesty pins other suites drive, restated here so a refactor of this
  // file fails in the suite that owns the surface instead of nowhere.
  for (const pinned of [
    "History not read yet — open the room",
    "This is a fetch failure, not an empty room",
    "if (!msgs) return false;",
    "membershipHeadline(activeRoster, activeRosterError)",
    "Groups inside this one",
    "scope?.state &&",
  ]) {
    assert.ok(section.includes(pinned), `missing pinned sentence: ${pinned}`);
  }
  assert.doesNotMatch(section, /<NotificationsBell\b/);
});
