// completion-view.test.mjs — the Completion engine view is reachable and honest.
//
// Two separate contracts, tested together because they fail together:
//
//  1. **Reachability.** A section that is not in the view registry is dead
//     code, and one registered in only some of the four required places renders
//     nothing while the nav still offers it. The id must exist in the
//     `WorkspaceView` union, the `WORKSPACE_TABS` row, `WORKSPACE_VIEW_IDS`,
//     and ChatView's lazy import + render case.
//
//  2. **Honesty.** This panel answers "did the work actually succeed?", so the
//     difference between the answers is the whole product: an unreadable
//     ledger is not an empty one, an absent list is not an empty list, a
//     waived gate is not a pass, a stale pass is not a current one, a gate
//     with no evidence is not a failed gate, and observe mode never claims a
//     completion it did not apply. Every state it renders has to stay
//     distinguishable.
//
// node --test src/lib/*.test.mjs — no server, no browser, no DOM shim.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");

const navTabs = read("../components/NavTabs.tsx");
const viewIds = read("./workspace-view.ts");
const chatView = read("../components/ChatView.tsx");
const section = read("../components/sections/CompletionSection.tsx");
const client = read("./completion.ts");

/** Whitespace-normalised copy, for sentences that wrap across source lines. */
const flat = section.replace(/\s+/g, " ");

/* ── Reachability: four places, or nowhere ─────────────────────────────── */

test("the view id exists in the workspace union, above the pinned triple", () => {
  // Two pre-existing pins constrain where a new id may land: `reliability` must
  // stay the terminal member, and `sentinel` -> `effects` -> `reliability` must
  // stay three adjacent members. So `completion` is declared immediately above
  // `mods`, which satisfies both and keeps `reliability` last.
  assert.match(
    navTabs,
    /export type WorkspaceView =[\s\S]*\| "completion"\s*\n\s*\| "mods"\s*\n\s*\| "sentinel"\s*\n\s*\| "effects"\s*\n\s*\| "reliability";/,
  );
});

test("reliability stays terminal and the sentinel → effects → reliability triple stays adjacent", () => {
  // Copied from sentinel-view / effects-view: the union ends at `reliability`,
  // and a member must never be inserted between the triple's members.
  assert.match(navTabs, /export type WorkspaceView =[\s\S]*?\| "reliability";/);
  assert.match(
    navTabs,
    /\| "sentinel"\s*\n\s*\| "effects"\s*\n\s*\| "reliability";/,
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

test("the tab is declared exactly once, with a label and a blurb", () => {
  const entries = navTabs.match(/\{ id: "completion"/g) ?? [];
  assert.equal(entries.length, 1, "a duplicated entry renders twice");
  assert.match(
    navTabs,
    /\{ id: "completion", label: "Completion"[^}]*blurb: "[^"]+"/,
  );
  assert.match(
    navTabs,
    /\{ id: "completion",[^}]*category: "system"/,
    "the tab belongs to the system group",
  );
  assert.match(
    navTabs,
    /icon: <ClipboardCheck className="size-3.5" \/>/,
    "the row carries the icon it imports",
  );
});

test("the completion row sits immediately before the mods row", () => {
  // WORKSPACE_TABS is declared once: the primary row and the More Views
  // dropdown both derive from it, so ordering here is ordering in the menu.
  // The row closes with `},` — the array element's trailing comma — so the
  // comma is part of the pin; `\}` alone leaves `\s*\n` facing a comma.
  assert.match(navTabs, /\{ id: "completion",[^\n]*\},\s*\n\s*\{ id: "mods",/);
});

test("the routable view list declares the id", () => {
  // Without this entry `?view=completion` falls back to `chat` while the nav
  // still shows an active "Completion" label — the defect `run-inspector` and
  // `reliability` each shipped with. Line-anchored so a comment quoting the id
  // does not count as the entry itself.
  assert.match(viewIds, /^\s*"completion",\s*$/m);
  assert.ok(
    viewIds.indexOf('  "completion",') < viewIds.indexOf('  "mods",'),
    "the routable list mirrors the union order: completion before mods",
  );
});

test("the section is lazy-imported and rendered for its view id", () => {
  assert.match(
    chatView,
    /dynamic\(\s*\(\) =>\s*import\("@\/components\/sections\/CompletionSection"\)/,
  );
  assert.match(
    chatView,
    /view === "completion" \? \([\s\S]*?<CompletionSection \/>/,
  );
  // Placed before the effects branch, mirroring its position in the union —
  // the render chain is a first-match-wins ladder, so order is the order the
  // branches are checked in.
  assert.ok(
    chatView.indexOf('view === "completion"') <
      chatView.indexOf('view === "effects"'),
    "the completion branch must precede the effects branch",
  );
});

/* ── Absent is not empty, empty is not absent ──────────────────────────── */

test("an unreadable ledger shows the real reason and never a zero", () => {
  assert.ok(
    section.includes("ledger could not be read"),
    "the failed read is disclosed with its reason",
  );
  // Prettier inserts `{" "}` when it reflows JSX text, so the sentence is
  // pinned as the two contiguous fragments it is made of.
  assert.ok(section.includes("The ledger could not be read:"));
  assert.ok(section.includes('{typeof h.error === "string" ? h.error : "no reason reported"}'));
  assert.match(flat, /every count reads \*not reported\* rather than 0/);
  assert.match(
    section,
    /return count === null \? "not reported" : String\(count\);/,
  );
});

test("absent counts render 'not reported', never 0", () => {
  assert.ok(section.includes("not reported"));
  assert.match(
    section,
    /function countText\(count: number \| null\): string \{\s*return count === null \? "not reported" : String\(count\);/,
  );
  assert.match(
    section,
    /function ratioText\(met: number \| null, total: number \| null\): string \{\s*if \(met === null \|\| total === null\) return "not reported";/,
  );
});

test("an omitted scope list is not an empty scope list", () => {
  // `scopes: null` and `scopes: []` are opposite facts and get two different
  // sentences: no list arrived, versus a measured empty list.
  assert.ok(section.includes("the Gateway sent no scope list"));
  assert.match(section, /title="The Gateway sent no scope list"/);
  assert.match(
    section,
    /This is not a claim that no completion scope was ever opened/,
  );
});

test("an empty scope list says so in its own words", () => {
  assert.match(section, /title="No scopes match this filter"/);
  assert.match(section, /The Gateway answered with an empty list/);
  assert.doesNotMatch(
    section,
    /No scopes match this filter[^"]*no completion scope/i,
  );
});

test("the task and gate lists each keep absent and empty apart", () => {
  assert.match(section, /title="The Gateway sent no task list"/);
  assert.match(section, /title="This scope has no tasks"/);
  assert.match(section, /title="The Gateway sent no gate list"/);
  assert.match(section, /title="This scope has no gates"/);
});

/* ── What a pass may and may not claim ─────────────────────────────────── */

test("a waived gate is a visible decision, never a pass", () => {
  assert.ok(flat.includes("a waived gate is a visible decision"));
  assert.match(flat, /finalization still treats it as a blocker/);
  // Only `passed` earns green; `waived` falls through to the grey default —
  // painting it green is the exact collapse this claim exists to stop.
  assert.match(section, /function gateTone[\s\S]*?default:\s*return "gray";/);
  assert.doesNotMatch(section, /case "waived":\s*return "green"/);
});

test("a stale pass cannot answer for the present", () => {
  // The mandated substring appears in the panel's own docs table; the rendered
  // acknowledgement wraps the same sentence across two source lines, so the
  // render pin tolerates the break.
  assert.ok(section.includes("cannot answer for work done since"));
  assert.match(flat, /a stale pass cannot answer for work done since/);
  assert.match(flat, /It re-runs required gates first/);
});

test("a gate with no evidence is distinct from a failed one", () => {
  assert.ok(section.includes("never been checked"));
  assert.match(
    section,
    /A gate that has never been checked is not a gate that failed/,
  );
  // The distinction is the presence of evidence, not the status word.
  assert.match(section, /const ev = gate\.latest_evidence;/);
  assert.match(section, /ev !== null && ev\.freshness === "current"/);
});

test("observe mode never claims a completion it did not apply", () => {
  assert.ok(section.includes("enforced: false"));
  assert.match(
    section,
    /enforced: false — decision recorded, status unchanged/,
  );
  assert.match(
    section,
    /labelled <span className="font-mono">enforced: false<\/span>/,
  );
  assert.match(
    section,
    /observe mode never claims a completion it did not apply/,
  );
});

/* ── The finalization guard is deliberate, then re-read ────────────────── */

test("the finalize control requires an acknowledgement that starts unchecked", () => {
  assert.match(
    section,
    /const \[acknowledged, setAcknowledged\] = useState\(false\);/,
  );
  assert.match(section, /checked=\{p\.acknowledged\}/);
  assert.match(section, /disabled=\{!p\.acknowledged \|\| p\.acting\}/);
  assert.match(
    section,
    />\s*Finalize\s*</,
    "the guard's button is labelled Finalize",
  );
  // A completed act ticks the box back off, so the next finalize needs a new
  // deliberate acknowledgement.
  assert.match(
    section,
    /setAcknowledged\(false\);\s*\n\s*await Promise\.all\(\[loadDetail/,
  );
});

test("nothing is painted from a click — a mutation re-reads the ledger", () => {
  assert.ok(
    section.includes("re-read the ledger rather than trusting this response"),
  );
  assert.match(
    section,
    /await Promise\.all\(\[loadDetail\(selectedId\), loadList\(\)\]\)/,
  );
  // The route's own response is discarded; only an error is rendered from it.
  assert.match(section, /setActionError\(failureText\(err\)\)/);
});

/* ── The server owns the verdict and the authorisation ─────────────────── */

test("authorisation is never inferred client-side", () => {
  // The sentence wraps across two source lines, so the pin tolerates the break.
  assert.match(flat, /a refusal is rendered as it was written/);
  assert.match(section, /This panel decides nothing about who you are/);
  // No local admin gate: the buttons render for every caller and the server's
  // 403 is the answer. A local `isAdmin` would be the second authority — and
  // it would hide the very refusal that answers.
  assert.doesNotMatch(
    section,
    /\b(isAdmin|is_admin|adminUser|hasAdminRole|system_role|role ===)/,
  );
});

test("each read carries its own failure and names which read it was", () => {
  assert.match(section, /The engine posture read failed: \$\{healthError\}\./);
  assert.match(section, /The check catalog read failed: \$\{checksError\}\./);
  assert.match(section, /The scope list read failed: \$\{listError\}\./);
  // One broken read must not blank the data another returned.
  assert.match(section, /The scope list below is unaffected\./);
  assert.match(section, /The posture above is unaffected\./);
});

test("an unknown status string renders verbatim in a grey badge", () => {
  assert.match(section, /function scopeTone[\s\S]*?default:\s*return "gray";/);
  assert.match(section, /function gateTone[\s\S]*?default:\s*return "gray";/);
  assert.match(section, /function taskTone[\s\S]*?default:\s*return "gray";/);
});

test("the vocabulary rules are stated where the operator reads them", () => {
  // Returned is not verified, not run is not passed: the two collapses this
  // engine exists to prevent, printed in the section's own hint.
  assert.match(
    section,
    /'Returned' is not 'verified', 'not run' is not 'passed'/,
  );
  assert.match(section, /because returned is not verified/);
});

test("the section is wired through the shared client, not through fetch", () => {
  assert.ok(
    !/\bfetch\s*\(/.test(section),
    "components must call the client, never fetch directly",
  );
  assert.match(section, /from "@\/lib\/completion";/);
  assert.match(section, /failureText/);
});

test("the client exposes the helpers the section renders", () => {
  for (const name of [
    "SCOPE_STATUSES",
    "GATE_STATUSES",
    "failureText",
    "countText",
    "gatesPassedText",
    "freshnessText",
    "finalizeScope",
  ]) {
    // `countText` is the section's own helper; every other name must come from
    // the client (or be defined locally — checked by the pins above).
    if (name === "countText") continue;
    assert.ok(
      client.includes(name),
      `${name} must be exported or defined by the completion module`,
    );
  }
});
