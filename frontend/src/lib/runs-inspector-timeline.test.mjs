// runs-inspector-timeline.test.mjs — the filterable run timeline's real contract.
//
// What is pinned here:
//
//  * store order is the server's and is never re-sorted — including when `seq`
//    arrives out of order, which is a fact about the read, not a defect to tidy;
//  * each filter returns EXACTLY the severity its label names, and the number
//    printed beside a button is the number of rows that button draws;
//  * a filter that matches nothing returns `[]` and says so in words that name
//    the FILTER, never in words that claim the run recorded nothing (the two are
//    opposite claims about the same screen);
//  * an empty list is all-zero counts and two `null` boundaries — never a
//    `Date.now()` value, never a synthesised timestamp;
//  * an event with `createdAt: null` or `seq: null` survives filtering and
//    summarising without being invented into a time or a sequence number;
//  * an event type this client has never seen survives verbatim;
//  * the component is reachable through the exact call signature the inspector
//    wires it with, and every state it claims to render is really in its source.
//
// The derivation module is transpiled and imported as a real file, the same way
// `runs-inspector.test.mjs` loads its modules — not as a nested `data:` URL,
// which does not survive a module graph. It has no runtime imports of its own
// (only type-only ones), so nothing above it needs stubbing. The component is
// NOT rendered here: the honesty of its markup rests on the numbers this module
// produces and on the one message function it is not free to reword.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import ts from "typescript";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");

const OUT_DIR = join(tmpdir(), `alpha-run-timeline-test-${process.pid}`);
mkdirSync(OUT_DIR, { recursive: true });

const transpiled = ts.transpileModule(read("./runs-inspector-timeline.ts"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
writeFileSync(join(OUT_DIR, "runs-inspector-timeline.mjs"), transpiled, "utf8");
const timeline = await import(pathToFileURL(join(OUT_DIR, "runs-inspector-timeline.mjs")).href);

const componentSource = read("../components/sections/RunInspectorTimeline.tsx");

/**
 * Prose is not code. A doc comment that says "there is no `Date.now()` here"
 * would otherwise trip a check that no `Date.now()` is here, so the
 * implementation checks below run against comments stripped.
 */
const stripComments = (source) => source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^[ \t]*\/\/.*$/gm, "");
const componentCode = stripComments(componentSource);

/* ── fixtures ───────────────────────────────────────────────────────────────── */

/** A `TimelineEvent`, with only the fields this client maps for a real row. */
const event = (over = {}) => ({
  seq: 1,
  eventType: "run.start",
  category: "trace",
  createdAt: "2026-09-28T15:07:39.158838+00:00",
  severity: "info",
  taskId: null,
  content: null,
  metadata: {},
  ...over,
});

/**
 * A real failed run's stream: six events, `seq` deliberately NOT ascending in
 * array order (a page boundary returned as it came), one `error`, one `warn`, an
 * event type this client has never seen, and one row the Gateway gave neither a
 * sequence nor a time for.
 */
const STREAM = [
  event({ seq: 1, eventType: "run.start", category: "trace", createdAt: "2026-09-28T14:53:31.035441+00:00" }),
  event({ seq: 2, eventType: "llm.human.input", category: "message", createdAt: "2026-09-28T14:53:31.456069+00:00" }),
  event({ seq: 3, eventType: "llm.error", category: "trace", severity: "error", createdAt: "2026-09-28T14:53:31.852597+00:00" }),
  // A cancelled subagent: a recorded intervention, not a failure.
  event({ seq: 5, eventType: "subagent.end", category: "subagent", severity: "warn", taskId: "task-7", content: { status: "cancelled" } }),
  // Out of order on purpose: seq 4 arrived after seq 5 in this page.
  event({ seq: 4, eventType: "middleware:quantum_annealer", category: "middleware", createdAt: "2026-09-28T14:53:32.100000+00:00" }),
  // A row the store returned with neither a sequence nor a time.
  event({ seq: null, eventType: "run.end", category: "outputs", createdAt: null }),
];

const ORDER = ["run.start", "llm.human.input", "llm.error", "subagent.end", "middleware:quantum_annealer", "run.end"];

/* ══ 1. The filters keep the server's order ══════════════════════════════════ */

test("every filter returns the events in the order the store returned them", () => {
  // Out-of-order `seq` is a fact about the read, not a defect to sort away.
  assert.deepEqual(STREAM.map((row) => row.seq), [1, 2, 3, 5, 4, null]);
  for (const filter of ["all", "errors", "warnings"]) {
    const rows = timeline.filterTimelineEvents(STREAM, filter);
    const positions = rows.map((row) => STREAM.indexOf(row));
    assert.deepEqual(
      positions,
      positions.slice().sort((a, b) => a - b),
      `${filter} must be a subsequence of the store's own order, never a re-sort`
    );
  }
  assert.deepEqual(timeline.filterTimelineEvents(STREAM, "all").map((row) => row.eventType), ORDER);
});

test("'all' returns every event, in order, as its own list", () => {
  const rows = timeline.filterTimelineEvents(STREAM, "all");
  assert.equal(rows.length, STREAM.length);
  assert.notEqual(rows, STREAM, "the filter must hand back its own array, not the caller's");
  rows.reverse();
  assert.deepEqual(
    STREAM.map((row) => row.eventType),
    ORDER,
    "mutating the returned list must not reorder the caller's events"
  );
});

test("'errors' returns only error severity — never a warning", () => {
  const rows = timeline.filterTimelineEvents(STREAM, "errors");
  assert.deepEqual(rows.map((row) => row.eventType), ["llm.error"]);
  for (const row of rows) {
    assert.equal(row.severity, "error", "an Errors button that lists a warning makes a different claim than its label");
  }
});

test("'warnings' returns only warn severity — never an error", () => {
  const rows = timeline.filterTimelineEvents(STREAM, "warnings");
  assert.deepEqual(rows.map((row) => row.eventType), ["subagent.end"]);
  for (const row of rows) assert.equal(row.severity, "warn");
});

test("the table is the behaviour: three filters, three labels, three severities", () => {
  assert.deepEqual(timeline.TIMELINE_FILTERS.map((option) => option.id), ["all", "errors", "warnings"]);
  assert.deepEqual(timeline.TIMELINE_FILTERS.map((option) => option.label), ["All", "Errors", "Warnings"]);
  assert.deepEqual(timeline.TIMELINE_FILTERS.map((option) => option.severity), [null, "error", "warn"]);
});

test("each filter's count is exactly the number of rows it will draw", () => {
  const summary = timeline.timelineSummary(STREAM);
  for (const option of timeline.TIMELINE_FILTERS) {
    assert.equal(
      timeline.filterCount(summary, option.id),
      timeline.filterTimelineEvents(STREAM, option.id).length,
      `the ${option.label} count must be the number of rows it will show`
    );
  }
  assert.equal(timeline.filterCount(summary, "all"), 6);
  assert.equal(timeline.filterCount(summary, "errors"), 1);
  assert.equal(timeline.filterCount(summary, "warnings"), 1);
});

test("a filter that matches nothing returns an empty list, not everything", () => {
  const onlyInfo = [event(), event({ seq: 2, eventType: "run.end", category: "outputs" })];
  assert.deepEqual(timeline.filterTimelineEvents(onlyInfo, "errors"), []);
  assert.deepEqual(timeline.filterTimelineEvents(onlyInfo, "warnings"), []);
  assert.deepEqual(timeline.filterTimelineEvents([], "all"), []);
  // Fail-closed on an id this client does not define: showing the whole stream
  // under an unknown filter label would claim a narrowing that never happened.
  assert.deepEqual(timeline.filterTimelineEvents(STREAM, "everything"), []);
  assert.equal(timeline.filterCount(timeline.timelineSummary(STREAM), "everything"), 0);
  assert.equal(timeline.timelineFilterLabel("everything"), "Unknown filter");
  assert.equal(timeline.timelineFilterLabel("errors"), "Errors");
});

/* ══ 2. A filter that matched nothing says so, in its own name ═══════════════ */

test("an empty filtered list is named as the filter's answer, never the run's", () => {
  const summary = timeline.timelineSummary([event(), event({ seq: 2, eventType: "run.end", category: "outputs" })]);
  const message = timeline.timelineNoMatchMessage("errors", summary);
  assert.match(message, /The Errors filter matched none of the 2 events the Gateway returned for this run\./);
  assert.match(message, /not a claim that the run recorded nothing/);
  // The opposite claim is the defect this wording exists to prevent.
  assert.doesNotMatch(message, /recorded no events/i);
  assert.doesNotMatch(message, /no events for this run/i);
  assert.match(timeline.timelineNoMatchMessage("warnings", summary), /^The Warnings filter matched none of the 2 events/);
  // Singular when the run really did record exactly one event.
  const one = timeline.timelineSummary([event()]);
  assert.match(timeline.timelineNoMatchMessage("errors", one), /none of the 1 event the Gateway returned/);
});

/* ══ 3. The summary counts what the server reported ═════════════════════════ */

test("the summary counts every severity and names its store-order boundaries", () => {
  const summary = timeline.timelineSummary(STREAM);
  assert.deepEqual(summary, {
    total: 6,
    errors: 1,
    warnings: 1,
    info: 4,
    // The first and last event that reported a time, raw, in store order.
    first: "2026-09-28T14:53:31.035441+00:00",
    last: "2026-09-28T14:53:32.100000+00:00",
  });
  assert.equal(summary.total, summary.errors + summary.warnings + summary.info, "no row may fall between the buckets");
});

test("an empty list is all-zero counts and two nulls — never a Date.now() value", () => {
  const summary = timeline.timelineSummary([]);
  assert.deepEqual(summary, { total: 0, errors: 0, warnings: 0, info: 0, first: null, last: null });
  for (const [key, value] of Object.entries(summary)) {
    assert.notEqual(typeof value, "string", `${key} must be a number or null, never a synthesised value`);
  }
  assert.notEqual(summary.first, new Date().toISOString(), "a 'now' fallback is exactly the fabricated stamp");
  assert.equal(timeline.filterCount(summary, "all"), 0, "a count is 0 only because the server reported no events");
});

test("a run whose every event is untimed has no boundaries, not invented ones", () => {
  const untimed = [
    event({ createdAt: null }),
    event({ seq: 2, eventType: "run.end", category: "outputs", createdAt: null }),
  ];
  const summary = timeline.timelineSummary(untimed);
  assert.equal(summary.total, 2, "an untimed event is still an event the run recorded");
  assert.equal(summary.info, 2);
  assert.equal(summary.first, null);
  assert.equal(summary.last, null);
});

test("a null seq and a null createdAt are carried through, never invented", () => {
  const hollow = [
    event({ seq: null, eventType: "run.end", category: "outputs", createdAt: null }),
    event({ seq: 2, eventType: "llm.error", severity: "error", createdAt: "2026-09-28T14:53:31.852597+00:00" }),
  ];
  const all = timeline.filterTimelineEvents(hollow, "all");
  assert.equal(all[0].seq, null, "an unreported seq stays null, never 0");
  assert.equal(all[0].createdAt, null, "an unreported time stays null, never the epoch");
  assert.equal(all[0].eventType, "run.end", "the row itself is still a row the run recorded");

  const errors = timeline.filterTimelineEvents(hollow, "errors");
  assert.deepEqual(errors.map((row) => row.eventType), ["llm.error"]);
  const summary = timeline.timelineSummary(hollow);
  assert.equal(summary.total, 2);
  assert.equal(summary.errors, 1);
  assert.equal(summary.info, 1);
  assert.equal(summary.first, "2026-09-28T14:53:31.852597+00:00", "an unreported time must not hide a real one");
  assert.equal(summary.last, "2026-09-28T14:53:31.852597+00:00");
});

test("a severity this client cannot classify is counted, not relabelled as info", () => {
  const odd = [{ ...event({ seq: 9 }), severity: "catastrophe" }];
  const summary = timeline.timelineSummary(odd);
  assert.equal(summary.total, 1, "the row is still counted in the read");
  assert.equal(summary.info, 0, "an unknown severity must never be folded into info");
  assert.equal(summary.errors, 0);
  assert.equal(summary.warnings, 0);
  assert.deepEqual(timeline.filterTimelineEvents(odd, "errors"), []);
  assert.deepEqual(timeline.filterTimelineEvents(odd, "warnings"), []);
  assert.equal(timeline.filterTimelineEvents(odd, "all").length, 1, "but it is still in the whole read");
});

test("an event type this client has never seen survives filtering verbatim", () => {
  const rows = timeline.filterTimelineEvents(STREAM, "all");
  const stranger = rows.find((row) => row.eventType === "middleware:quantum_annealer");
  assert.ok(stranger, "an unknown event type must not be dropped");
  assert.equal(stranger.eventType, "middleware:quantum_annealer");
  assert.equal(stranger.category, "middleware");
  assert.equal(stranger.severity, "info", "it is an ordinary row for filtering, and it keeps its own name");
  assert.equal(
    timeline.filterTimelineEvents(STREAM, "errors").some((row) => row.eventType === "middleware:quantum_annealer"),
    false
  );
  assert.equal(timeline.timelineSummary(STREAM).info, 4);
});

/* ══ 4. The module is pure: no transport, no clock ═══════════════════════════ */

test("the module imports nothing at runtime, so it cannot fetch or read a clock", () => {
  const code = stripComments(transpiled);
  assert.doesNotMatch(code, /^\s*import\s/m, "every import must be type-only and erased at transpile time");
  assert.doesNotMatch(code, /Date\.now|new Date\(|fetch\(/, "no synthesised clock, no transport");
});

/* ══ 5. The component is reachable, and only through its contract ════════════ */

test("the component is exported with the exact props the inspector wires it with", () => {
  assert.match(
    componentSource,
    /export function RunInspectorTimeline\(props: \{ timeline: Timeline \| null; error: string \| null \}\)/,
    'the call site is `<RunInspectorTimeline timeline={…} error={…} />`'
  );
  const exports = [...componentSource.matchAll(/^export (function|const|class) (\w+)/gm)].map((match) => match[2]);
  assert.deepEqual(exports, ["RunInspectorTimeline"], "one named export, so the inspector cannot wire a different one");
});

test("the component filters through this module and never re-derives a severity", () => {
  // The one severity mapping lives in runs-inspector.ts; the component may only
  // read `event.severity`, never rebuild it from an event type.
  assert.match(componentCode, /from "@\/lib\/runs-inspector-timeline"/);
  for (const call of ["filterTimelineEvents", "timelineSummary", "filterCount", "timelineNoMatchMessage", "TIMELINE_FILTERS"]) {
    assert.ok(componentCode.includes(call), `the component must use ${call} rather than its own copy`);
  }
  assert.doesNotMatch(componentCode, /llm\.error|middleware:loop_detection|subagent\.end/, "no second severity table");
  assert.doesNotMatch(componentCode, /Date\.now|new Date\(/, "the component never mints a timestamp");
  assert.match(componentCode, /import \{ absoluteStamp, clockTime \} from "@\/lib\/time"/);
  assert.doesNotMatch(componentCode, /toLocaleTimeString|toLocaleString/, "no second time formatter");
});

test("every state the component claims to render is present in its source", () => {
  // A failed read shows the server's reason and no count; an unread one says it
  // is reading; an empty one says the server answered; a partial one discloses
  // the stopped walk. Each is a distinct claim, so each has to be reachable.
  for (const needle of [
    "could not be read, so the timeline is unavailable",
    "Reading the run",
    "reported no persisted events for this run",
    "not a failed read",
    "partial read",
    "the bounded read stopped before the end",
    "Every category the run journalled, in store order",
    "aria-pressed",
    'role="group"',
    "time not reported",
    "not shown; this preview is bounded",
  ]) {
    assert.ok(componentCode.includes(needle), `the component must render: ${needle}`);
  }
  // The event count appears only on the title, and only when the read landed
  // and did not fail.
  assert.match(componentCode, /const failed = props\.error !== null;/);
  assert.match(
    componentCode,
    /title=\{`Event timeline\$\{failed \|\| !props\.timeline \? "" : ` \(\$\{props\.timeline\.events\.length\}\)`\}`\}/
  );
  assert.match(componentCode, /failed \|\| !props\.timeline \? null/, "a failed or unread read shows no complete/partial badge");
});
