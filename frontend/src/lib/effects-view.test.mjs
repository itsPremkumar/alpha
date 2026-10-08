// effects-view.test.mjs — the Effect journal is reachable and honest.
//
// Two separate contracts, tested together because they fail together:
//
//  1. **Reachability.** A section that is not in the view registry is dead
//     code, and one registered in only some of the four required places renders
//     nothing while the nav still offers it. The id must exist in the
//     `WorkspaceView` union, the `WORKSPACE_TABS` row, `WORKSPACE_VIEW_IDS`,
//     and ChatView's lazy import + render case.
//
//  2. **Honesty.** This panel exists because "is the ledger empty?" and "could
//     the ledger be read?" are opposite answers that both looked like an empty
//     table. Every state it renders has to stay distinguishable: a failed read,
//     a list the Gateway omitted, an empty queue, an entry nobody may reconcile,
//     and a refusal from the server.
//
// node --test src/lib/*.test.mjs — no server, no browser, no DOM shim.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");

const navTabs = read("../components/NavTabs.tsx");
const viewIds = read("./workspace-view.ts");
const chatView = read("../components/ChatView.tsx");
const section = read("../components/sections/EffectsSection.tsx");
const client = read("./side-effects.ts");

/* ── Reachability: four places, or nowhere ─────────────────────────────── */

test("the view id exists in the workspace union", () => {
  // `reliability` must stay the terminal member, so `effects` is declared
  // before it rather than appended.
  assert.match(navTabs, /export type WorkspaceView =[\s\S]*\| "effects"\s*\n\s*\| "reliability";/);
});

test("the tab is declared exactly once, with a label and a blurb", () => {
  const entries = navTabs.match(/\{ id: "effects"/g) ?? [];
  assert.equal(entries.length, 1, "a duplicated entry renders twice");
  assert.match(navTabs, /\{ id: "effects", label: "Effects"[^}]*blurb: "[^"]+"/);
  assert.match(navTabs, /\{ id: "effects",[^}]*category: "system"/, "the tab belongs to the system group");
});

test("the routable view list declares the id", () => {
  // Without this entry `?view=effects` falls back to `chat` while the nav
  // still shows an active "Effects" label — the defect `run-inspector` and
  // `reliability` each shipped with.
  assert.match(viewIds, /^\s*"effects",\s*$/m);
});

test("the section is lazy-imported and rendered for its view id", () => {
  assert.match(chatView, /dynamic\(\s*\(\) =>\s*import\("@\/components\/sections\/EffectsSection"\)/);
  assert.match(chatView, /view === "effects" \? \([\s\S]*?<EffectsSection \/>/);
});

/* ── Two reads, two failures ───────────────────────────────────────────── */

test("summary and list are read independently through allSettled", () => {
  // One `Promise.all` would blank whichever answer arrived beside a rejection:
  // "how much is there" and "which rows" must not fail as a single fact.
  assert.match(section, /await Promise\.allSettled\(\[/);
  assert.match(section, /fetchSideEffectSummary\(\)/);
  assert.match(section, /fetchSideEffectList\(\{/);
});

test("each read carries its own error and its own notice", () => {
  assert.match(section, /setSummaryError\(failureText\(summaryResult\.reason\)\)/);
  assert.match(section, /setListError\(failureText\(listResult\.reason\)\)/);
  assert.match(section, /The summary read failed: \$\{summaryError\}\. The list below is unaffected\./);
  assert.match(section, /The entry list read failed: \$\{listError\}\. The summary above is unaffected\./);
});

test("a failed summary does not blank a list that answered", () => {
  // The list stays rendered; only its own error line is added.
  assert.match(section, /list === null && listError === null \?/);
  assert.match(section, /list === null \?/);
});

/* ── The queue is the default view ─────────────────────────────────────── */

test("the default filter is the reconciliation queue", () => {
  assert.match(section, /useState<string>\("unknown"\)/);
});

/* ── Honest renderings of every absence ────────────────────────────────── */

test("an omitted entry list is not an empty ledger", () => {
  assert.match(section, /The Gateway sent no entry list/);
  assert.match(section, /not a claim that no effects were recorded/);
});

test("an empty entry list says so in its own words", () => {
  assert.match(section, /No entries match this filter/);
  assert.doesNotMatch(section, /No entries match this filter[^"]*no effects (were )?recorded/i);
});

test("a failed read is disclosed as a failed read, never as an empty result", () => {
  assert.match(section, /This is a failed read, not an empty ledger\./);
});

test("absent counts render 'not reported', never 0", () => {
  assert.match(section, /function countText\(count: number \| null\): string \{\s*return count === null \? "not reported" : String\(count\);/);
  assert.match(section, /countText\(summary\.total\)/);
  assert.match(section, /countText\(summary\.unknown\)/);
});

test("the oldest-unknown age distinguishes 'nothing waiting' from 'not reported'", () => {
  // Both are `null` on the wire; only a *reported* summary may say the queue
  // is clear, because an unreported one has not looked.
  assert.match(section, /summary\.reported \? "no unknown entry waiting" : "not reported"/);
  assert.doesNotMatch(section, /oldest_unknown_age_seconds === null[^?]*\? "0s"/);
});

test("an unknown status or level string renders verbatim in a grey badge", () => {
  assert.match(section, /function statusTone[\s\S]*?default:\s*return "gray";/);
  assert.match(section, /function levelTone[\s\S]*?default:\s*return "gray";/);
});

/* ── Reconciling: the server owns the verdict ──────────────────────────── */

test("the entry is re-read fresh before a verdict is offered", () => {
  // The table row may be seconds stale; offering a verdict on an entry
  // somebody already settled is how a 409 gets blamed on the operator.
  assert.match(section, /setFreshEntry\(await fetchSideEffect\(toolCallId\)\)/);
  assert.match(section, /A \*fresh\* read/);
});

test("the form is offered only for reconcilable === true", () => {
  assert.match(section, /const canReconcile = entry\.reconcilable === true;/);
  assert.match(section, /entry\.reconcilable === false \?/);
  assert.match(section, /!canReconcile \?/);
});

test("an unreported reconcilability shows words and no button", () => {
  // A disabled button would imply a pending verdict the server never promised.
  assert.match(section, /reconcilability not reported/);
  const branch = section.slice(section.indexOf("reconcilability not reported"));
  assert.ok(!/<Btn[^>]*>Record verdict<\/Btn>/.test(branch.slice(0, branch.indexOf("!canReconcile"))), "no submit button beside the unreported line");
});

test("a settled or in-flight entry is explained, not offered a form", () => {
  assert.match(section, /The server reports this entry cannot take a verdict/);
  assert.match(section, /refused with 409/);
});

test("the submit is opt-in and gated on a non-empty reason", () => {
  assert.match(section, /const submitDisabled = props\.submitting \|\| !props\.acknowledged \|\| reasonEmpty;/);
  assert.match(section, /useState\(false\);\s*\n\s*const \[submitting/, "the acknowledgement checkbox starts unchecked");
  assert.match(section, /checked=\{props\.acknowledged\}/);
});

test("nothing is painted before the server confirms the verdict", () => {
  assert.match(section, /const confirmed = await reconcileSideEffect\(selectedId, verdict, reason\);\s*\n\s*setResult\(confirmed\)/);
});

test("the response's own claims are rendered, with null kept as 'not reported'", () => {
  assert.match(section, /reopened: \$\{props\.result\.reopened === null \? "not reported"/);
  assert.match(section, /escalated: \$\{props\.result\.escalated === null \? "not reported"/);
});

test("a successful verdict re-reads the rest of the ledger", () => {
  // The response speaks for one row; the queue and the counts are the rest.
  assert.match(section, /setAcknowledged\(false\);\s*\n\s*\/\/ Re-read everything[\s\S]*?void load\(\);/);
});

test("a refusal keeps the server's own words", () => {
  // `errMsg` would replace a 403 or a 409 with a generic sentence — and a
  // reconciliation refusal *is* the answer.
  assert.match(section, /setSubmitError\(failureText\(err\)\)/);
  assert.match(section, /setFreshError\(failureText\(err\)\)/);
  assert.match(section, /failureText,/);
});

test("authorisation is never inferred on the client", () => {
  // There is no admin gate in the panel: the server decides, and its 403 is
  // rendered as written. A local `isAdmin` would be the second authority.
  assert.doesNotMatch(section, /\b(isAdmin|is_admin|adminUser|hasAdminRole|system_role|role ===)/);
});

test("the panel states that a verdict never touches a run", () => {
  assert.match(section, /never cancels, resumes or replays a run/);
});

test("the panel states that digests are all that crosses the API", () => {
  assert.match(section, /arguments and results never cross it/);
  assert.match(section, /digests only/i);
});

test("scope and identity come from the server, not from this screen", () => {
  assert.match(section, /Nothing on this screen decides who you are/);
});

test("the section is wired through the shared client, not through fetch", () => {
  assert.ok(!/\bfetch\s*\(/.test(section), "components must call the client, never fetch directly");
  assert.match(section, /from "@\/lib\/side-effects";/);
});

test("the client exposes every constant the section renders", () => {
  for (const name of ["SIDE_EFFECT_STATUSES", "SIDE_EFFECT_LEVELS", "RECONCILE_VERDICTS", "SIDE_EFFECT_LIMIT_DEFAULT"]) {
    assert.ok(client.includes(`export const ${name}`), `${name} must be exported`);
  }
});
