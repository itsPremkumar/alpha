// harness-lab-view.test.mjs — the Harness Lab view is reachable and honest.
//
// Two separate contracts, tested together because they fail together:
//
//  1. **Reachability.** A section that is not in the view registry is dead
//     code, and one registered in only some of the four required places renders
//     nothing while the nav still offers it. The id must exist in the
//     `WorkspaceView` union, the `WORKSPACE_TABS` row, `WORKSPACE_VIEW_IDS`,
//     and ChatView's lazy import + render case — while leaving the pre-existing
//     union/row adjacencies (`completion` -> `mods` -> `sentinel` -> `effects`
//     -> `reliability`) exactly where they were pinned.
//
//  2. **Honesty.** This panel answers "can this installation's harness improve
//     itself right now, and if not, what is missing?", so the difference
//     between the answers is the whole product: an absent capability row is not
//     a wired one, an unreadable store is not an empty tree, an absent list is
//     not an empty list, `executes: false` is not silence, an unreported count
//     is never 0, an unreported cost is never $0, resumability is the server's
//     tri-state answer rather than a locally derived "terminal", the ladder is
//     read from the Gateway rather than pinned here, the kill switch is never
//     re-checked locally, and every refusal is the server's own sentence.
//
// node --test src/lib/*.test.mjs — no server, no browser, no DOM shim.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");

const navTabs = read("../components/NavTabs.tsx");
const viewIds = read("./workspace-view.ts");
const chatView = read("../components/ChatView.tsx");
const section = read("../components/sections/HarnessLabSection.tsx");
const client = read("./harness-lab.ts");

/** Whitespace-normalised copy, for sentences that wrap across source lines. */
const flat = section.replace(/\s+/g, " ");

/* ── Reachability: four places, or nowhere ─────────────────────────────── */

test("the view id sits between execution-modes and completion, above the pinned run", () => {
  // `reliability` must stay the terminal member, and `completion` -> `mods` ->
  // `sentinel` -> `effects` -> `reliability` must stay a contiguous run (pinned
  // by completion-view/sentinel-view/effects-view tests). So the lab is
  // declared after `execution-modes` and before `completion`, outside the run.
  const from = navTabs.indexOf("export type WorkspaceView");
  const to = navTabs.indexOf(";", from);
  assert.ok(from !== -1 && to !== -1, "the union block must be sliceable");
  const union = navTabs.slice(from, to);
  assert.ok(
    union.indexOf('| "execution-modes"') < union.indexOf('| "harness-lab"') &&
      union.indexOf('| "harness-lab"') < union.indexOf('| "completion"'),
    "harness-lab must be declared between execution-modes and completion",
  );
});

test("the pre-existing completion → reliability run stays contiguous and terminal", () => {
  assert.match(
    navTabs,
    /\| "completion"\s*\n\s*\| "mods"\s*\n\s*\| "sentinel"\s*\n\s*\| "effects"\s*\n\s*\| "reliability";/,
  );
});

test("no comment inside the union range quotes an id", () => {
  // `unionIds()` in workspace-nav.test.mjs reads every quoted token between
  // `export type WorkspaceView` and the first semicolon, so a comment quoting
  // an id reports it as a duplicate union member.
  const from = navTabs.indexOf("export type WorkspaceView");
  const to = navTabs.indexOf(";", from);
  assert.ok(from !== -1 && to !== -1, "the union block must be sliceable");
  const comments = navTabs
    .slice(from, to)
    .split("\n")
    .filter((line) => line.trim().startsWith("//"));
  for (const line of comments) {
    assert.doesNotMatch(
      line,
      /"[a-z][a-z-]*"/,
      `this comment sits inside the union range and quotes an id: ${line.trim()}`,
    );
  }
});

test("the tab is declared exactly once, with a label, a blurb and its icon", () => {
  const entries = navTabs.match(/\{ id: "harness-lab"/g) ?? [];
  assert.equal(entries.length, 1, "a duplicated entry renders twice");
  assert.match(
    navTabs,
    /\{ id: "harness-lab", label: "Harness Lab"[^}]*blurb: "[^"]+"/,
  );
  assert.match(
    navTabs,
    /\{ id: "harness-lab",[^}]*category: "system"/,
    "the tab belongs to the system group",
  );
  assert.ok(
    navTabs.includes('icon: <FlaskConical className="size-3.5" />'),
    "the row carries the icon it imports",
  );
  assert.ok(
    navTabs.includes("FlaskConical,"),
    "the icon is imported, not merely used",
  );
});

test("the harness row sits immediately between the effects and completion rows", () => {
  // WORKSPACE_TABS is declared once: the primary row and the More Views
  // dropdown both derive from it, so ordering here is ordering in the menu.
  // The new row goes between `effects` and `completion`, which keeps the
  // completion -> mods adjacency that completion-view.test.mjs pins.
  assert.match(
    navTabs,
    /\{ id: "effects",[^\n]*\},\s*\n\s*\{ id: "harness-lab",[^\n]*\},\s*\n\s*\{ id: "completion",/,
  );
  assert.match(navTabs, /\{ id: "completion",[^\n]*\},\s*\n\s*\{ id: "mods",/);
});

test("the routable view list declares the id, after execution-modes", () => {
  // Without this entry `?view=harness-lab` falls back to `chat` while the nav
  // still offers an active "Harness Lab" label. Line-anchored so a comment
  // quoting the id does not count as the entry itself.
  assert.match(viewIds, /^\s*"harness-lab",\s*$/m);
  assert.ok(
    viewIds.indexOf('  "harness-lab",') > viewIds.indexOf('  "execution-modes",'),
    "the routable list mirrors the late additions: harness-lab after execution-modes",
  );
});

test("the section is lazy-imported and rendered for its view id", () => {
  assert.match(
    chatView,
    /dynamic\(\s*\(\) =>\s*import\("@\/components\/sections\/HarnessLabSection"\)/,
  );
  assert.ok(chatView.includes("default: m.HarnessLabSection"));
  assert.match(
    chatView,
    /view === "harness-lab" \? \([\s\S]*?<HarnessLabSection \/>/,
  );
  // The render chain is a first-match-wins ladder, and the lazy import order
  // mirrors it: the branch follows execution-modes and precedes supervisor.
  const branch = chatView.indexOf('view === "harness-lab"');
  assert.ok(
    branch > chatView.indexOf('view === "execution-modes"'),
    "the harness-lab branch must follow the execution-modes branch",
  );
  assert.ok(
    branch < chatView.indexOf('view === "supervisor"'),
    "the harness-lab branch must precede the supervisor branch",
  );
});

/* ── Absent is not empty, unreported is not zero ───────────────────────── */

test("absent counts, costs and sizes render as absent, never zeroed", () => {
  assert.match(
    section,
    /function countText\(count: number \| null\): string \{\s*return count === null \? "not reported" : String\(count\);\s*\}/,
  );
  assert.match(
    section,
    /function costText\(cost: number \| null\): string \{\s*return cost === null \? "not reported" : `\$\$\{cost\}`;\s*\}/,
  );
  assert.match(
    section,
    /function sizeText\(size: number \| null\): string \{\s*return size === null \? "size not reported" : /,
  );
  assert.ok(
    section.includes("no timestamp reported"),
    "an absent timestamp is named, not rendered as the epoch",
  );
  assert.ok(
    section.includes("timestamp not readable"),
    "an unreadable timestamp is distinct from an absent one",
  );
});

test("an absent list and an empty list are different answers everywhere", () => {
  for (const title of [
    "The Gateway sent no run list",
    "The Gateway sent no bundle list",
    "The Gateway sent no promotion log",
    "The Gateway sent no experience list",
    "The Gateway sent no budget ledger",
    "The Gateway sent no component list",
    "The Gateway sent no ladder",
    "The Gateway sent no capability row",
    "The Gateway sent no store block",
    "The Gateway sent no journal tail for this run",
    "The Gateway sent no diff block",
    "the Gateway sent no manifest for it",
  ]) {
    assert.ok(section.includes(title), `absent state missing: ${title}`);
  }
  assert.ok(section.includes("That is not a claim that the tree is empty"));
  assert.ok(
    section.includes("that is not a claim that nothing was recorded"),
  );
});

test("an unreadable store shows its own reason, and unreadable rows are named, not dropped", () => {
  assert.ok(
    section.includes(
      'The store could not be read: ${store.reason ?? "no reason reported"}',
    ),
  );
  assert.ok(section.includes("store row(s) could not be read and are not counted above"));
  assert.ok(
    section.includes(
      'data-harness-store={store.readable === true ? "readable" : store.readable === false ? "unreadable" : "unknown"}',
    ),
    "readability is tri-state, with an unreported state of its own",
  );
  assert.ok(
    section.includes(
      "Store readability is not reported, so no count below can be read as a measurement.",
    ),
  );
  assert.ok(
    section.includes("rather than 0"),
    "an absent counts block is disclosed instead of reading as zero",
  );
});

test("each listing discloses the rows it could not read", () => {
  for (const pattern of [
    "run file(s) could not be read and are not shown",
    "bundle file(s) could not be read and are not shown",
    "promotion log file(s) could not be read and are not shown",
    "experience file(s) could not be read and are not shown",
    "ledger file(s) could not be read and are not shown",
  ]) {
    assert.ok(section.includes(pattern), `unreadable rows must be named: ${pattern}`);
  }
});

test("the active pointer keeps absent, recorded and manifest-degraded apart", () => {
  assert.ok(section.includes('data-harness-active="none"'));
  assert.ok(section.includes('"manifest-degraded" : "recorded"'));
  assert.ok(
    section.includes(
      "The active pointer is recorded, but its manifest could not be read: ${active.manifest_error}",
    ),
  );
  assert.ok(
    section.includes(
      "No active bundle is recorded. A start is therefore expected to refuse with the absent-baseline reason rather than inventing one.",
    ),
  );
});

/* ── The execution plane and the ladder are the server's claims ─────────── */

test("the execution plane discloses missing wiring by name and never claims it is complete", () => {
  assert.ok(flat.includes("that is not the same as wiring being complete"));
  assert.ok(
    section.includes(
      'capability.executes === false ? "does not execute" : "execution not reported"',
    ),
  );
  assert.ok(section.includes('{row.wiring_point || "wiring point not reported"}'));
  assert.ok(section.includes('{row.why_absent || "no reason reported"}'));
  assert.ok(
    flat.includes("refused or parked with the missing collaborator named — never faked"),
  );
});

test("resumability is the server's tri-state answer, never re-derived", () => {
  assert.ok(section.includes("resumable (the server's answer)"));
  assert.ok(section.includes("not resumable (the server's answer)"));
  assert.ok(section.includes('return "resumability not reported";'));
  assert.match(section, /resumableText\(run\.resumable\)/);
  assert.match(section, /resumableText\(detail\.resumable\)/);
  assert.ok(section.includes("next stage: {detail.next_stage}"));
  // No local terminal-stage table: the badges may tone a stage, but nothing
  // here derives "the run is over" from a stage list of its own.
  assert.doesNotMatch(
    section,
    /\b(?:TERMINAL|TERMINAL_STAGES|STAGE_ORDER|LADDER_STAGES)\b/,
  );
});

test("the promotion ladder is rendered from the Gateway payload, never pinned locally", () => {
  assert.ok(section.includes("The ladder is read from the Gateway, never pinned here."));
  assert.ok(section.includes("Object.entries(promotions.ladder)"));
  assert.ok(
    section.includes(
      "The Gateway sent no ladder; the stage transitions are not reported here.",
    ),
  );
  assert.doesNotMatch(section, /\b(?:LADDER|LADDER_STAGES|STAGES)\b/);
});

/* ── The kill switch is the server's answer, not a local gate ───────────── */

test("the kill switch is never re-checked here: controls gate on busy/selection only", () => {
  assert.ok(
    flat.includes(
      "The kill switch is never re-checked here; whichever answer the server gives is rendered.",
    ),
  );
  assert.ok(flat.includes("the kill switch must not trap work in flight"));
  assert.ok(section.includes("disabled={busy}"));
  assert.ok(section.includes("disabled={busy || selectedBundleId === null}"));
  assert.ok(section.includes("disabled={busy || activeBundleId === null}"));
  assert.doesNotMatch(
    section,
    /disabled=\{[^}]*\benabled\b/,
    "no control may gate on the feature flag — the server's refusal is the answer",
  );
  assert.ok(
    flat.includes("not a bug to hide the form for"),
    "the form stays visible while the feature is disabled",
  );
});

/* ── Refusals verbatim, then re-read the authoritative views ────────────── */

test("server refusals are rendered verbatim, including the disabled and absent-baseline answers", () => {
  assert.ok(section.includes("message={actionError}"));
  assert.equal(
    (section.match(/setActionError\(failureText\(err\)\)/g) ?? []).length,
    7,
    "every rejected mutation renders the server's own sentence",
  );
  assert.ok(flat.includes("409 harness_baseline_absent</span> is rendered verbatim above"));
  assert.ok(
    flat.includes('writes answer <span className="font-mono">503</span> naming the setting'),
    "the disabled-mode refusal names its status and the setting it comes from",
  );
});

test("every mutation re-reads the authoritative views instead of painting its receipt", () => {
  assert.ok(section.includes("await Promise.all([loadRuns(), loadStatus()]);"));
  assert.ok(section.includes("await Promise.all([loadRuns(), loadRunDetail(runId), loadStatus()]);"));
  assert.ok(section.includes("await Promise.all([loadRuns(), loadRunDetail(runId)]);"));
  assert.ok(section.includes("await loadPromotions();"));
  assert.ok(
    section.includes(
      "await Promise.all([loadPromotions(), loadBundles(), loadBundleDetail(selectedBundleId)]);",
    ),
  );
  assert.ok(
    section.includes(
      "await Promise.all([loadStatus(), loadBundles(), loadPromotions(), loadBundleDetail(selectedBundleId)]);",
    ),
  );
  assert.ok(section.includes("await Promise.all([loadStatus(), loadBundles(), loadPromotions()]);"));
  assert.ok(
    flat.includes("The list below is re-read from the store rather than built from this receipt."),
  );
  assert.ok(flat.includes("Nothing is painted as promoted from a click"));
});

test("six reads fail independently: each section names its own read and retry", () => {
  for (const what of [
    'what="the harness posture"',
    'what="the run timeline"',
    'what="the bundle tree"',
    'what="the promotion history"',
    'what="the experience library"',
    'what="the budget ledger"',
  ]) {
    assert.ok(section.includes(what), `the section must render ${what} independently`);
  }
  assert.ok(
    section.includes("Promise.allSettled"),
    "one failed read must not blank the five that answered",
  );
});

/* ── The details that carry the honesty contract ────────────────────────── */

test("experience staleness is reported, not applied", () => {
  assert.ok(section.includes('"staleness not reported"'));
  assert.ok(section.includes('"not stale per the recorded conditions"'));
  assert.ok(section.includes('`stale: ${entry.staleness.join("; ")}`'));
  assert.ok(section.includes("Staleness is reported, not applied"));
});

test("the diff is requested without bodies and the panel says so", () => {
  assert.ok(
    section.includes("fetchHarnessBundleDiff(bundleId, { includeContents: false })"),
  );
  assert.ok(flat.includes("the client asks the server for the diff without them"));
});

test("rollback targets the pointer, then the status, then the list — in that order", () => {
  assert.ok(
    section.includes(
      "promotions?.pointer?.bundle_id ?? status?.active_bundle?.bundle_id ?? bundles?.active_bundle?.bundle_id ?? null",
    ),
  );
});

test("only granted approval kinds are sent, and the server stays the authority on pending", () => {
  assert.ok(
    section.includes(
      "const selectedApprovals = HARNESS_APPROVAL_KINDS.filter((kind) => approvals[kind] === true);",
    ),
  );
  assert.ok(flat.includes("it can never satisfy a substantive gate"));
  assert.ok(flat.includes("The server stays the authority on which approvals are pending."));
});

test("the load-bearing states carry data-harness hooks for their tests", () => {
  for (const hook of [
    "data-harness-capability=",
    "data-harness-store=",
    "data-harness-active=",
    "data-harness-ceilings=",
    "data-harness-run=",
    "data-harness-run-detail=",
    "data-harness-bundle=",
    "data-harness-bundle-detail=",
    "data-harness-bundle-diff=",
    "data-harness-promotions",
    "data-harness-ladder",
    "data-harness-experience=",
    "data-harness-budget-row=",
  ]) {
    assert.ok(section.includes(hook), `missing hook: ${hook}`);
  }
});

test("the section is wired through the shared client, not through fetch", () => {
  assert.ok(
    !/\bfetch\s*\(/.test(section),
    "components must call the client, never fetch directly",
  );
  assert.ok(section.includes('from "@/lib/harness-lab";'));
  assert.ok(section.includes("failureText"));
});

test("the client exposes every helper the section renders", () => {
  for (const name of [
    "HARNESS_APPROVAL_KINDS",
    "failureText",
    "fetchHarnessStatus",
    "fetchHarnessRuns",
    "fetchHarnessRun",
    "fetchHarnessBundles",
    "fetchHarnessBundle",
    "fetchHarnessBundleDiff",
    "fetchHarnessPromotions",
    "fetchHarnessExperiences",
    "fetchHarnessBudgets",
    "startHarnessRun",
    "resumeHarnessRun",
    "cancelHarnessRun",
    "approveHarnessCandidate",
    "rejectHarnessCandidate",
    "activateHarnessBundle",
    "rollbackHarnessBundle",
  ]) {
    assert.ok(
      client.includes(name),
      `${name} must be exported or defined by the harness-lab module`,
    );
    assert.ok(
      section.includes(name),
      `${name} must be imported and used by the section`,
    );
  }
});
