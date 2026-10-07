// run-verification-wiring.test.mjs — the panel must keep reading the run's real
// verification posture.
//
// ## Why these are source pins
//
// No test harness in this repo loads `WorkflowsSection.tsx` (2400 lines, deep
// import graph), so `run-verification-view.test.mjs` drives the pure module
// directly. A pure module can be perfectly correct while the panel ignores it —
// which is precisely the defect this feature is about. Measured before the fix:
// `node_verification`, `acceptance_reason`, `registered_verifiers`,
// `executor_bound` and `declared_nodes` each had ZERO occurrences in the entire
// frontend, while the server sent all five.
//
// Each pin names the specific string a refactor would drop.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const section = read("../components/sections/WorkflowsSection.tsx");
const view = read("./run-verification-view.ts");
const flat = (s) => s.replace(/\s+/g, " ");

// ---------------------------------------------------------------------------
// The derivation is used
// ---------------------------------------------------------------------------

test("the section imports the verification view module", () => {
  assert.match(section, /import \{ runVerificationView \} from "@\/lib\/run-verification-view"/);
});

test("the result panel derives its verification reading from the module", () => {
  assert.match(section, /runVerificationView\(/);
  assert.match(flat(section), /dynamicResult\.metadata/);
});

test("the events come from the already-fetched run log, not a second request", () => {
  // `loadRun` fetches the events; a second `getRunEvents` call in the render path
  // would re-request the same log on every render.
  const idx = section.indexOf("runVerificationView(");
  const window = section.slice(idx, idx + 420);
  assert.match(flat(window), /events\?\.events/);
  assert.doesNotMatch(window, /getRunEvents\(/);
});

// ---------------------------------------------------------------------------
// The defect cannot come back
// ---------------------------------------------------------------------------

test("the two-branch acceptance ternary is gone", () => {
  // The old block decided the posture from `metadata.acceptance_passed === true`
  // alone and printed one of two fixed sentences, discarding
  // `acceptance_reason`, `execution_label` and the whole `verification` block.
  assert.doesNotMatch(
    flat(section),
    /acceptance_passed === true \? "Domain acceptance verified by the bound executor\."/,
    "the hardcoded two-sentence posture must not return",
  );
});

test("the panel does not decide the posture from the boolean alone", () => {
  const idx = section.indexOf("Dynamic Execution Result");
  const window = section.slice(idx, idx + 4200);
  assert.doesNotMatch(
    flat(window),
    /metadata\.acceptance_passed === true \?/,
    "the posture must come from runVerificationView, which quotes the server's reason",
  );
});

test("the hardcoded 'Graph mechanics completed only' sentence is gone", () => {
  // True, and it named none of: which nodes declared a check, that the named
  // check was NOT RUN, or why the posture exists.
  assert.doesNotMatch(section, /Graph mechanics completed only; the default digest executor/);
});

// ---------------------------------------------------------------------------
// Each absence the module can name is rendered
// ---------------------------------------------------------------------------

test("the acceptance sentence and executor label are rendered", () => {
  assert.match(section, /\{verification\.acceptance\.sentence\}/);
  assert.match(section, /\{verification\.acceptance\.executionLabel\}/);
});

test("the verification posture sentence and declared nodes are rendered", () => {
  assert.match(section, /\{verification\.posture\.sentence\}/);
  assert.match(section, /verification\.posture\.declaredNodes\.join\(", "\)/);
});

test("the no-declaration note is rendered, so silence is not read as success", () => {
  assert.match(section, /\{verification\.declaredNote\}/);
});

test("the per-node verification rows render their own sentence and command", () => {
  assert.match(section, /verification\.nodes\.map\(/);
  assert.match(section, /\{n\.sentence\}/);
  assert.match(section, /\{n\.command\}/);
});

test("the per-node tone drives the colour rather than a single style", () => {
  // One style for every row would render an unrun check and a real failure
  // identically, which is the distinction the module exists to preserve.
  const idx = section.indexOf("verification.nodes.map(");
  const window = flat(section.slice(idx, idx + 900));
  assert.match(window, /n\.tone === "red"/);
  assert.match(window, /n\.tone === "amber"/);
});

// ---------------------------------------------------------------------------
// The module keeps the rules the panel relies on
// ---------------------------------------------------------------------------

test("the module decides `ran` from the status word, not from `passed`", () => {
  // `not_run` reports `passed: false` because nothing passed. Deriving `ran` from
  // `passed` would render every unrun check as a failure.
  assert.match(view, /const ran = status !== null && status !== "not_run"/);
});

test("the module quotes the server's reason for an unrun check", () => {
  assert.match(view, /not run — \$\{reason\}/);
});

test("the module keeps an absent acceptance flag distinct from a false one", () => {
  assert.match(view, /typeof md\.acceptance_passed === "boolean" \? md\.acceptance_passed : null/);
});

test("the module states that nothing declared a verification rather than that all passed", () => {
  assert.match(view, /no node declared a verification for this run/);
  // Scoped to CODE, not prose: the module's own rules table quotes
  // "all checks passed" as the wrong rendering, so a whole-file search matches
  // the documentation of the rule instead of a violation of it.
  const code = view
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|\n)\s*\/\/[^\n]*/g, "$1");
  assert.doesNotMatch(code, /all checks passed/, "the phrase may appear only in the docs");
});
