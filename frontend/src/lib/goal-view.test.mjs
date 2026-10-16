// goal-view.test.mjs — the conversation-goal detail's pure projections.
//
// The budget line the goal dialog shows is the exact honesty seam for the
// thread-goal continuation contract: "N of M" when the Gateway reported both
// halves, the known half when it reported one, and never a fabricated pair or
// a fabricated zero. Pure Node test (node --test src/lib/goal-view.test.mjs):
// transpiles goal-view.ts, which imports nothing.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./goal-view.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const mod = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(compiled)}`);

test("both halves reported render as an N of M pair", () => {
  assert.equal(mod.budgetPairLabel(3, 10, "used"), "3 of 10");
});

test("a reported count with an unreported max keeps the count and discloses the gap", () => {
  // The Gateway sends these fields independently (older goal states carry
  // only the counter). Rendering "Not reported" would discard the measured
  // value; rendering "3 of 10" would claim a max nobody sent.
  assert.equal(mod.budgetPairLabel(3, null, "used"), "3 used (max not reported)");
});

test("a reported max with an unreported count keeps the max and discloses the gap", () => {
  assert.equal(mod.budgetPairLabel(null, 8, "recorded"), "max 8 (none recorded)");
});

test("an unreported pair is 'Not reported', never 0 of 0", () => {
  assert.equal(mod.budgetPairLabel(null, null, "used"), "Not reported");
});

test("negative or malformed halves read as unreported rather than invented", () => {
  assert.equal(mod.budgetPairLabel(-1, 0, "used"), "max 0 (none recorded)");
  assert.equal(mod.budgetPairLabel(4, -3, "recorded"), "4 recorded (max not reported)");
});

test("the panel renders both budget rows through this helper", () => {
  const panel = readFileSync(
    new URL("../components/ConversationGoalPanel.tsx", import.meta.url),
    "utf8",
  );
  assert.match(panel, /budgetPairLabel\(details\.continuation_count, details\.max_continuations, "used"\)/);
  assert.match(panel, /budgetPairLabel\(details\.no_progress_count, details\.max_no_progress_continuations, "recorded"\)/);
  // The old wording discarded a half-reported pair; it must not return.
  assert.doesNotMatch(panel, /Not fully reported/);
});
