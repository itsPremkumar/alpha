// sentinel-view.test.mjs -- the Sentinel surface is reachable and honest.
//
// Two separate contracts, tested together because they fail together:
//
//  1. **Reachability.** A section that is not in the view registry is dead
//     code, and one registered in only some of the four required places renders
//     nothing while the nav still offers it. The id must exist in the
//     `WorkspaceView` union, the `WORKSPACE_TABS` row, `WORKSPACE_VIEW_IDS`, and
//     ChatView's lazy import + render case.
//
//  2. **Honesty.** This panel exists because "the engine repaired something"
//     and "the engine escalated everything" used to be the same table, and
//     because "nobody measured this" used to render as 0. Every state it renders
//     has to stay distinguishable: a folded window with no passes, a window
//     with passes but no per-signal detail, an unreported scanned total, an
//     unreported duration, an unrecognised verdict, and a refused decision.
//
// Pure Node test (node --test): reads the sources and pins them. No server, no
// browser, no DOM shim.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");

const navTabs = read("../components/NavTabs.tsx");
const viewIds = read("./workspace-view.ts");
const chatView = read("../components/ChatView.tsx");
const section = read("../components/sections/SentinelSection.tsx");
const client = read("./sentinel.ts");

/* ── Reachability: four places, or nowhere ─────────────────────────────── */

test("the view id exists in the workspace union", () => {
  // `reliability` must stay the terminal member, so `sentinel` is declared
  // before it rather than appended.
  assert.match(navTabs, /export type WorkspaceView =[\s\S]*\| "sentinel"\s*\n\s*\| "effects"\s*\n\s*\| "reliability";/);
});

test("the tab is declared exactly once, with a label and a blurb", () => {
  const entries = navTabs.match(/\{ id: "sentinel"/g) ?? [];
  assert.equal(entries.length, 1, "a duplicated entry renders twice");
  assert.match(navTabs, /\{ id: "sentinel", label: "Sentinel"[^}]*blurb: "[^"]+"/);
  assert.match(navTabs, /\{ id: "sentinel",[^}]*category: "system"/, "the tab belongs to the system group");
});

test("the routable view list declares the id", () => {
  // Without this entry `?view=sentinel` falls back to `chat` while the nav
  // still shows an active "Sentinel" label -- the defect `run-inspector` and
  // `reliability` each shipped with.
  assert.match(viewIds, /^\s*"sentinel",\s*$/m);
});

test("the section is lazy-imported and rendered for its view id", () => {
  assert.match(chatView, /dynamic\(\s*\(\) =>\s*import\("@\/components\/sections\/SentinelSection"\)/);
  assert.match(chatView, /view === "sentinel" \? \([\s\S]*?<SentinelSection \/>/);
});

test("the section is not reachable from another view's registry", () => {
  // A second mount would be a second, silently diverging copy of the panel.
  assert.equal((section.match(/export function SentinelSection/g) ?? []).length, 1);
});

/* ── The panel holds no sentence of its own ────────────────────────────── */

test("every claim in the panel comes from the client's own helpers", () => {
  // A second copy of a phrase is how a KPI card and a table row end up
  // contradicting each other for the same value.
  for (const helper of [
    "analyticsHealthView",
    "verdictTone",
    "verdictWords",
    "kindEvidenceLine",
    "scannedText",
    "durationText",
    "failureText",
  ]) {
    assert.match(section, new RegExp(`${helper}\\(`), `the section must call ${helper}`);
  }
});

test("the health badge renders the client's reason beside its label", () => {
  // A word with no reason attached is a claim nobody can check.
  assert.match(section, /const health = analyticsHealthView\(analytics\)/);
  assert.match(section, /\{health\.reason\}/);
  assert.match(section, /title=\{health\.reason\}/);
});

test("an unrecognised verdict renders neutral gray, never green", () => {
  // The tone table lives in the client so the panel cannot branch on `=== null`
  // inline and snap an unknown word to a known one.
  assert.match(section, /tone=\{verdictTone\(kind\.verdict\)\}/);
  assert.match(section, /tone=\{verdictTone\(repeat\.verdict\)\}/);
  assert.match(client, /case "repaired":\s*return "green"/);
  assert.match(client, /default:\s*return "gray"/);
});

test("the scanned total and the duration never render as 0 when unreported", () => {
  // Both go through the helpers that return words for a null, so a mapper
  // reaching for `?? 0` cannot reach the markup.
  assert.match(section, /value=\{scannedText\(analytics\)\}/);
  assert.match(section, /value=\{durationText\(analytics\.duration_mean_s\)\}/);
  assert.match(client, /return "not measured"/);
  assert.match(client, /not reported by any pass/);
});

test("the capped window is disclosed as a window", () => {
  // A capped total that reads as the whole journal is the failure this exists
  // to stop.
  assert.match(section, /analytics\.dropped_by_cap\} older pass\(es\) are outside this window/);
  assert.match(section, /describe the window, not the whole journal/);
});

test("the disclosures are rendered, not summarised", () => {
  assert.match(section, /analytics\.disclosures\.map/);
  assert.match(section, /kinds\.disclosures\.map/);
  assert.match(section, /rows\.disclosures\.map/);
  assert.match(section, /reports\.disclosures\.map/);
});

/* ── Every read fails alone ───────────────────────────────────────────── */

test("no single Promise.all blanks a panel that answered", () => {
  // Six panels, six independent `useCallback` loads. One `Promise.all` would
  // let a corrupt journal present itself as an entirely empty plane.
  const panels = section.match(/function \w+Panel\(/g) ?? [];
  assert.ok(panels.length >= 5, `expected the panels to load independently, found ${panels.length}`);
  assert.doesNotMatch(section, /Promise\.all\(/);
  assert.doesNotMatch(section, /Promise\.allSettled\(\[/);
});

test("each read names itself when it fails", () => {
  assert.match(section, /Sentinel analytics unavailable/);
  assert.match(section, /Fault-kind registry unavailable/);
  assert.match(section, /Sentinel handoffs unavailable/);
  assert.match(section, /Sentinel report journal unreadable/);
  assert.match(section, /Signal collection failed/);
});

test("an unreadable store is never rendered as an empty queue", () => {
  // The 503 names the file; the panel must have its own error box rather than
  // falling through to "nothing is waiting on a human".
  assert.match(section, /error && !rows\)/);
  assert.match(section, /Nothing is waiting on a human/);
});

/* ── Decisions are opt-in, in-flight-locked, and re-read ─────────────── */

test("the repair pass is opt-in per click and defaults to observe", () => {
  assert.match(section, /useState\(false\)/);
  assert.match(section, /onChange=\{\(e\) => setAutoHeal\(e\.target\.checked\)\}/);
  // `auto_heal` is always sent; omission is never a way to request a repair.
  assert.match(section, /await runSentinelPass\(autoHeal\)/);
});

test("the safety model is stated beside the control that engages it", () => {
  assert.match(section, /reverts anything that comes back red/);
  assert.match(section, /escalated, never guessed at/);
  assert.match(section, /An observe pass registers no repair\s+strategy at all/);
});

test("a decision re-reads the list and never paints its own click", () => {
  assert.match(section, /await load\(\);/);
  assert.doesNotMatch(section, /setRows\(\[.*resolved/);
  assert.match(section, /setPending\(`\$\{verb\}:\$\{escalation\.escalation_id\}`\)/);
  assert.match(section, /disabled=\{pending !== null\}/, "a double-click cannot record two decisions");
});

test("a refused decision keeps the server's own words", () => {
  // `errMsg` paraphrases a 403, which is right for an incidental read and
  // wrong for the answer to a deliberate write.
  assert.match(section, /failureText\(e\)/);
  assert.doesNotMatch(section, /errMsg\(e\)/);
  assert.match(client, /if \(typeof status === "number" && status >= 400\) return err\.message/);
});

test("authorisation is never inferred client-side", () => {
  // There is no role check: the buttons appear for every caller and a member's
  // 403 is rendered through `failureText`.
  assert.doesNotMatch(section, /is_admin|isAdmin|role ===/);
});

/* ── Nothing is painted optimistically ────────────────────────────────── */

test("no panel writes state from a click without a server round-trip", () => {
  assert.match(section, /const result =\s*verb === "acknowledge"/);
  assert.match(section, /props\.onNotice\(result\.note/);
  assert.match(section, /await load\(\)/);
});

test("the panel is otherwise read-only", () => {
  // It observes, it runs a pass, and it records a human decision. It does not
  // cancel, resume or replay a run.
  for (const forbidden of ["cancelRun", "resumeRun", "replayRun", "deleteThread"]) {
    assert.doesNotMatch(section, new RegExp(forbidden));
  }
  const posts = (client.match(/send</g) ?? []).length;
  assert.equal(posts, 1, "exactly one send call site (decideEscalation)");
});
