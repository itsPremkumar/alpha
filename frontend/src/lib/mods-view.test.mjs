// mods-view.test.mjs — the Mod kernel panel is reachable and honest.
//
// Two separate contracts, tested together because they fail together:
//
//  1. **Reachability.** A section that is not in the view registry is dead
//     code, and one registered in only some of the four required places renders
//     nothing while the nav still offers it. The id must exist in the
//     `WorkspaceView` union, the `WORKSPACE_TABS` row, `WORKSPACE_VIEW_IDS`,
//     and ChatView's lazy import + render case.
//
//  2. **Honesty.** This panel exists because "is the chain empty?", "did the
//     describe succeed?" and "may I read this?" are three different questions
//     that all used to render as an empty box. Every state it renders has to
//     stay distinguishable: a failed read, a list the Gateway omitted, an
//     actually-empty chain, a description that raised, a preview that could not
//     be computed, an approval whose hold has since expired, and a refusal from
//     the server.
//
// node --test src/lib/*.test.mjs — no server, no browser, no DOM shim.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");

const navTabs = read("../components/NavTabs.tsx");
const viewIds = read("./workspace-view.ts");
const chatView = read("../components/ChatView.tsx");
const section = read("../components/sections/ModsSection.tsx");
const client = read("./mods.ts");

/* ── Reachability: four places, or nowhere ─────────────────────────────── */

test("the view id exists in the workspace union", () => {
  // Two pre-existing pins constrain where a new id may land: `reliability` must
  // stay the terminal member, and `sentinel` -> `effects` -> `reliability` must
  // stay three adjacent members. So `mods` is declared above the sentinel/
  // effects pair — which satisfies both, and keeps `reliability` last.
  assert.match(navTabs, /export type WorkspaceView =[\s\S]*\| "mods"\s*\n\s*\| "sentinel"\s*\n\s*\| "effects"\s*\n\s*\| "reliability";/);
});

test("the tab is declared exactly once, with a label and a blurb", () => {
  const entries = navTabs.match(/\{ id: "mods"/g) ?? [];
  assert.equal(entries.length, 1, "a duplicated entry renders twice");
  assert.match(navTabs, /\{ id: "mods", label: "Mods"[^}]*blurb: "[^"]+"/);
  assert.match(navTabs, /\{ id: "mods",[^}]*category: "system"/, "the tab belongs to the system group");
});

test("the routable view list declares the id", () => {
  // Without this entry `?view=mods` falls back to `chat` while the nav still
  // shows an active "Mods" label — the defect `run-inspector`, `reliability`
  // and `effects` each shipped with.
  assert.match(viewIds, /^\s*"mods",\s*$/m);
});

test("the section is lazy-imported and rendered for its view id", () => {
  assert.match(chatView, /dynamic\(\s*\(\) =>\s*import\("@\/components\/sections\/ModsSection"\)/);
  assert.match(chatView, /view === "mods" \? \([\s\S]*?<ModsSection \/>/);
});

/* ── Four reads, four failures ─────────────────────────────────────────── */

test("the chain, commands, ledger and holds are read independently", () => {
  // One `Promise.all` would blank whichever answers arrived beside a
  // rejection: an admin-only ledger that 403s must not look like a workspace
  // with no policy chain in it.
  assert.match(section, /await Promise\.allSettled\(\[/);
  for (const call of ["fetchModFleet()", "fetchModCommands()", "fetchModAudit({", "fetchModHolds({"]) {
    assert.ok(section.includes(call), `missing independent read ${call}`);
  }
  assert.doesNotMatch(section, /Promise\.all\(/, "no all-or-nothing read anywhere in the section");
});

test("each read carries its own error and its own notice", () => {
  assert.match(section, /setFleetError\(failureText\(fleetResult\.reason\)\)/);
  assert.match(section, /setCommandsError\(failureText\(commandsResult\.reason\)\)/);
  assert.match(section, /setAuditError\(failureText\(auditResult\.reason\)\)/);
  assert.match(section, /setHoldsError\(failureText\(holdsResult\.reason\)\)/);
  assert.match(section, /The chain read failed: \$\{fleetError\}\./);
  assert.match(section, /The command list read failed: \$\{commandsError\}\./);
  assert.match(section, /The audit ledger read failed: \$\{auditError\}\./);
  assert.match(section, /The hold store read failed: \$\{holdsError\}\./);
  // Every notice names what the *other* blocks still do, so a 403 on the
  // admin blocks cannot read as the whole panel being down.
  assert.match(section, /The commands, ledger and holds below are unaffected\./);
  assert.match(section, /The chain, commands and holds above and below are unaffected\./);
});

/* ── The two admin blocks say what their refusal means ─────────────────── */

test("a failed admin read is disclosed as a refusal, not as an empty queue", () => {
  assert.match(section, /an absent ledger answers 503 rather than an empty list — neither is "no records"/);
  assert.match(section, /a member's read answers 403 — that refusal is the answer, not an empty queue/);
});

test("the pending set is the default hold view", () => {
  assert.match(section, /useState<string>\("pending"\)/);
  // The filter is always shown beside its own empty state, so "no holds" is
  // never rendered without the decision it filtered on.
  assert.match(section, /No holds are recorded as "\$\{holdDecision\}"/);
  assert.match(section, /No holds match this filter/);
});

/* ── Honest renderings of every absence ────────────────────────────────── */

test("absent counts render 'not reported', never 0", () => {
  assert.match(section, /function countText\(count: number \| null\): string \{\s*return count === null \? "not reported" : String\(count\);/);
  assert.match(section, /countText\(fleet\?\.total \?\? null\)/);
  assert.doesNotMatch(section, /\?\?\s*0\b/);
  assert.doesNotMatch(section, /\|\|\s*0\b/);
});

test("an omitted chain is distinguished from an empty chain", () => {
  assert.match(section, /The Gateway sent no chain list/);
  assert.match(section, /this is not a claim that no mods are registered/i);
  assert.match(section, /No mods are registered/);
  assert.match(section, /The Gateway returned an empty chain/);
});

test("an omitted list is distinguished from an empty one, in all three blocks", () => {
  assert.match(section, /The Gateway sent no command list/);
  assert.match(section, /The Gateway sent no entry list/);
  assert.match(section, /The Gateway sent no hold list/);
  assert.match(section, /No mod commands are registered/);
  assert.match(section, /No records match this filter/);
  assert.doesNotMatch(section, /No mod commands are registered[^"]*no commands exist/i);
});

test("a describe that failed is reported as a failure, never as blank fields", () => {
  assert.match(section, /describe failed for \$\{described\.name\}: \$\{described\.error\}\./);
  assert.match(section, /no field below it is claimed/);
  assert.match(section, /Descriptions that failed/);
  assert.match(section, /a describe that raised reports an error, never a blank mod/);
});

test("manifest discrepancies keep 'absent' and 'none' apart", () => {
  assert.match(section, /discrepancies not reported — the Gateway sent no manifest comparison for this mod/);
  assert.match(section, /no discrepancies reported — declared and observed agree/);
});

test("declared hooks and observed hooks travel side by side, never merged", () => {
  assert.match(section, /hooks declared:/);
  assert.match(section, /hooks observed:/);
  assert.match(section, /manifestList\(described\?\.declared \?\? null, "hooks"\)/);
  assert.match(section, /manifestList\(described\?\.observed \?\? null, "hooks"\)/);
});

test("a declared capability is stated as documentation, never a grant", () => {
  assert.match(section, /A declared requirement is documentation, never a grant/);
});

test("a description with no chain row is reported, not dropped", () => {
  assert.match(section, /have no row in the dispatch chain/);
  assert.match(section, /reported rather than dropped/);
});

/* ── Impact preview: unmeasurable is not harmless ──────────────────────── */

test("the preview states that it executes nothing", () => {
  assert.match(section, /This preview executes nothing/);
  assert.match(section, /bounded command parsing plus a read-only filesystem walk/);
});

test("measurable keeps its three states, each with its own words", () => {
  assert.match(section, /preview\.measurable === true/);
  assert.match(section, /preview\.measurable === false/);
  assert.match(section, /preview\.measurable === null/);
  assert.match(section, /could not compute/);
  assert.match(section, /measurable not reported/);
  assert.match(section, /it is never a claim that nothing is at stake/);
});

test("a preview that could not be bounded says so for its figures too", () => {
  // `size` is JSX text, not a template literal, so there is no `$` before the
  // expression — the pin matches the shape that is actually shipped.
  assert.match(section, /size \{preview\.estimated_bytes === null \? "not reported"/);
  assert.match(section, /preview\.truncated === true/);
  assert.match(section, /listing truncated/);
});

test("malformed preview arguments are refused locally, with the reason", () => {
  assert.match(section, /tool_args is not valid JSON/);
  assert.match(section, /tool_args must be a JSON object/);
});

/* ── Commands: the route's own gate, rendered ──────────────────────────── */

test("an approval-gated command is labelled and its 409 shown verbatim", () => {
  assert.match(section, /requires approval/);
  assert.match(section, /this route refuses the command with 409 — it has no approval flow/);
  // No client-side transition rule: the Run button's only disable is the
  // in-flight guard, so the server's 409 is what the operator actually reads.
  assert.match(section, /onClick=\{\(\) => void runCommand\(command\)\} disabled=\{running !== null\}/);
  assert.doesNotMatch(section, /command\.requires_approval\s*&&\s*[^}]*disabled/);
});

test("a command run renders the handler's own status, with null as 'not reported'", () => {
  assert.match(section, /runStatus === null \|\| runStatus === "" \? "status not reported"/);
  assert.match(section, /output not reported/);
  assert.match(section, /approval requirement not reported/);
});

test("a refused command run keeps the server's sentence", () => {
  assert.match(section, /setRunError\(failureText\(err\)\)/);
});

/* ── Holds: a decision, an expiry, and who made it ─────────────────────── */

test("expiry is read beside the decision and never gates a control", () => {
  assert.match(section, /const expiry = holdExpiryView\(hold\);/);
  assert.match(section, /an expired hold voids even an approved decision/);
  assert.match(section, /the store decides whether a hold stands, never this panel/i);
  // Disclosure only: no button's disable expression may reference the expiry.
  assert.match(section, /disabled=\{decisionDisabled\}/);
  assert.doesNotMatch(section, /decisionDisabled = [^;]*expiry/);
});

test("the store's own durability bound is stated where the store path is", () => {
  assert.match(section, /a durable file for one Gateway process, not a shared multi-worker approval store/);
  assert.match(section, /path not reported/);
});

test("the decision form is opt-in and gated on the acknowledgement", () => {
  assert.match(section, /const \[acknowledged, setAcknowledged\] = useState\(false\);/);
  assert.match(section, /checked=\{acknowledged\}/);
  assert.match(section, /const decisionDisabled = submitting \|\| !acknowledged \|\| reasonTooLong;/);
  assert.match(section, /\{reason\.length\}\/\{HOLD_REASON_MAX_LENGTH\} characters/);
});

test("nothing is painted before the store records the decision", () => {
  assert.match(section, /const decision = await decideHold\(holdId, verb, reason\);\s*\n\s*setRecorded\(decision\.hold\)/);
  // …and the queue is re-read, because one row's claim is not the queue.
  assert.match(section, /setDeciding\(null\);\s*\n\s*void load\(\);/);
});

test("a refusal keeps the server's own words, and the operator is server-assigned", () => {
  assert.match(section, /setSubmitError\(failureText\(err\)\)/);
  assert.match(section, /the body never supplies the operator's name/);
  assert.match(section, /operator \$\{recorded\.decided_by\}/);
});

/* ── Unknown values stay unknown ───────────────────────────────────────── */

test("an unknown outcome or decision string renders verbatim in a grey badge", () => {
  assert.match(section, /function outcomeTone[\s\S]*?default:\s*return "gray";/);
  assert.match(section, /function decisionTone[\s\S]*?default:\s*return "gray";/);
  assert.match(section, /The kernel's own outcome string/);
  assert.match(section, /The store's own decision string/);
});

test("the risk string is never run through a colour table", () => {
  // Three unrelated `RiskLevel` vocabularies exist in the harness; colouring
  // one against a scale nobody declared is a claim the payload does not make.
  assert.match(section, /function riskTitle\(\): string/);
  assert.doesNotMatch(section, /function riskTone/);
  assert.match(section, /shown verbatim/);
});

/* ── The server owns authorisation and durability ──────────────────────── */

test("authorisation is never inferred on the client", () => {
  // No admin gate in the panel: audit and holds render for every caller and
  // the server's 403 is rendered as written. A local `isAdmin` would be the
  // second authority — and it would hide the very refusal that answers.
  assert.doesNotMatch(section, /\b(isAdmin|is_admin|adminUser|hasAdminRole|system_role|role ===)/);
  assert.match(section, /There is no client-side role check/);
});

test("the panel states what is durable and what is not", () => {
  assert.match(section, /process-local/);
  assert.match(section, /cross-worker exactly-once/);
  assert.match(section, /the event journal behind the audit ledger/i);
});

test("the panel states that a decision never touches a run", () => {
  // The sentence wraps across two source lines, so the pin tolerates the break.
  assert.match(section, /never\s+starts, cancels or resumes a run/);
});

test("the section is wired through the shared client, not through fetch", () => {
  assert.ok(!/\bfetch\s*\(/.test(section), "components must call the client, never fetch directly");
  assert.match(section, /from "@\/lib\/mods";/);
});

test("the client exposes every constant the section renders", () => {
  for (const name of ["AUDIT_LIMIT_DEFAULT", "HOLDS_LIMIT_DEFAULT", "HOLD_DECISIONS", "HOLD_REASON_MAX_LENGTH"]) {
    assert.ok(client.includes(`export const ${name}`), `${name} must be exported`);
  }
});
