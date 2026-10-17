import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const stub = `
let handler = () => undefined;
export function setHandler(fn) { handler = fn; }
export async function get(path) { return handler(path, "GET"); }
export async function send(path, method, payload, timeout) { return handler(path, method, payload, timeout); }
`;
const stubUrl = `data:text/javascript;charset=utf-8,${encodeURIComponent(stub)}`;
const source = readFileSync(new URL("./subagent-scenarios.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText.replace(
  /from\s+"\.\/http"/,
  `from "${stubUrl}"`,
);
const mod = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(js)}`);
const { setHandler } = await import(stubUrl);

test("the catalogue reads the shadow policy instead of assuming it", async () => {
  setHandler((path, method) => {
    assert.equal(path, "/subagents/scenarios");
    assert.equal(method, "GET");
    return { shadow_default: true, scenarios: [{ id: "direct", title: "Direct", summary: "s", dispatch: "none", requirements: [], estimated_children: 0, engine: "" }] };
  });
  const catalogue = await mod.getScenarioCatalogue();
  assert.equal(catalogue.shadowDefault, true);
  assert.equal(catalogue.scenarios[0].dispatch, "none");
});

test("a catalogue with no shadow_default reads false, not true", async () => {
  setHandler(() => ({ scenarios: [] }));
  const catalogue = await mod.getScenarioCatalogue();
  assert.equal(catalogue.shadowDefault, false, "an unreported policy must not read as the permissive one");
  assert.deepEqual(catalogue.scenarios, []);
});

test("a malformed scenario row is dropped rather than half-rendered", async () => {
  setHandler(() => ({ shadow_default: true, scenarios: [{ title: "no id" }, null, { id: "direct", dispatch: "none" }] }));
  const catalogue = await mod.getScenarioCatalogue();
  assert.equal(catalogue.scenarios.length, 1);
  assert.equal(catalogue.scenarios[0].id, "direct");
  assert.equal(catalogue.scenarios[0].summary, "", "an absent summary is empty, not invented");
});

test("the plan route sends only the capabilities the caller measured", async () => {
  setHandler((path, method, payload) => {
    assert.equal(path, "/subagents/plan");
    assert.equal(method, "POST");
    assert.deepEqual(payload, { prompt: "audit this", delegation_enabled: true });
    return { scenario: "direct", dispatch: "none", shadow: true, is_executable: false };
  });
  await mod.previewExecutionPlan({ prompt: "audit this", delegationEnabled: true });
});

test("an explicitly measured capability is sent, including a measured false", async () => {
  setHandler((path, method, payload) => {
    assert.equal(payload.arena_configured, false);
    assert.equal(payload.council_configured, null, "unknown travels as null, not as an absent field");
    return { scenario: "direct", shadow: true };
  });
  await mod.previewExecutionPlan({ prompt: "x", delegationEnabled: true, arena: false, council: null });
});

test("an unknown budget is omitted rather than sent as zero", async () => {
  setHandler((path, method, payload) => {
    assert.equal("child_budget" in payload, false);
    return { scenario: "direct" };
  });
  await mod.previewExecutionPlan({ prompt: "x", delegationEnabled: true, childBudget: null });
  setHandler((path, method, payload) => {
    assert.equal(payload.child_budget, 3);
    return { scenario: "direct" };
  });
  await mod.previewExecutionPlan({ prompt: "x", delegationEnabled: true, childBudget: 3 });
});

test("shadow and is_executable are read, never assumed", async () => {
  setHandler(() => ({ scenario: "tournament", dispatch: "arena", admitted_children: 8, estimated_children: 8 }));
  const plan = await mod.previewExecutionPlan({ prompt: "tournament please", delegationEnabled: true });
  assert.equal(plan.shadow, false, "an unreported flag is not the permissive default");
  assert.equal(plan.isExecutable, false);
  assert.equal(plan.admittedChildren, 8);
});

test("considered pairs survive as structured rows", async () => {
  setHandler(() => ({
    scenario: "direct",
    considered: [
      ["tournament", "Arena could not be read, so it is unknown rather than absent"],
      ["junk"],
    ],
    degradations: ["budget of 3"],
  }));
  const plan = await mod.previewExecutionPlan({ prompt: "x", delegationEnabled: true });
  assert.equal(plan.considered.length, 1, "a short pair is dropped, not rendered with empty text");
  assert.equal(plan.considered[0].scenario, "tournament");
  assert.match(plan.considered[0].why, /unknown rather than absent/);
  assert.deepEqual(plan.degradations, ["budget of 3"]);
});

test("a completely unrecognised plan body does not throw", async () => {
  setHandler(() => null);
  const plan = await mod.previewExecutionPlan({ prompt: "x", delegationEnabled: true });
  assert.equal(plan.scenario, "direct");
  assert.equal(plan.reason, "");
  assert.deepEqual(plan.considered, []);
  assert.equal(plan.isExecutable, false);
});

test("the headline distinguishes a direct plan from a real shape", async () => {
  setHandler(() => ({ scenario: "direct" }));
  assert.equal(mod.planHeadline(await mod.previewExecutionPlan({ prompt: "x", delegationEnabled: true })), "No delegation planned");
  setHandler(() => ({ scenario: "parallel_fan_out" }));
  assert.equal(mod.planHeadline(await mod.previewExecutionPlan({ prompt: "x", delegationEnabled: true })), "Planned: parallel fan out");
});

// --- planIsNoteworthy: a regression for a bug found by a live browser check ---

const _plan = (over) => ({
  scenario: "direct",
  dispatch: "none",
  reason: "",
  estimatedChildren: 0,
  admittedChildren: 0,
  considered: [],
  degradations: [],
  shadow: true,
  isExecutable: false,
  perceivedTier: "",
  complexity: 0,
  ...over,
});

test("a plain direct plan is silence", () => {
  assert.equal(mod.planIsNoteworthy(_plan({})), false);
});

test("a resolved shape is always worth showing", () => {
  assert.equal(mod.planIsNoteworthy(_plan({ scenario: "tournament", dispatch: "arena" })), true);
});

test("a budget reduction is worth showing", () => {
  assert.equal(mod.planIsNoteworthy(_plan({ degradations: ["budget of 3"] })), true);
});

test("a SKIPPED scenario is worth showing even with no degradations", () => {
  // This is the bug a live browser check caught. A skipped scenario lands in
  // `considered`, and only an explicit request or a budget cut populates
  // `degradations` - so gating on `degradations` alone hid the single most
  // informative case: "you asked for a tournament, Arena could not be read,
  // this will run directly instead".
  const plan = _plan({
    considered: [{ scenario: "tournament", why: "Arena could not be read, so it is unknown rather than absent" }],
  });
  assert.equal(plan.degradations.length, 0, "precondition: the old gate would have hidden this");
  assert.equal(mod.planIsNoteworthy(plan), true);
});