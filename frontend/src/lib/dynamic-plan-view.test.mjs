// dynamic-plan-view.test.mjs — the honesty rules for a dynamic-workflow preview.
//
// Found on 2026-10-06. `WorkflowsSection.tsx` rendered
//
//     Tools: {resources.tools.join(", ") || "alpha.tools"}
//
// and never read `resources.metadata`, whose `unavailable_tools` listed most of
// those same names. Measured with `backend/scripts/probe_dynamic_tool_arithmetic.py`
// across three prompts on the live Gateway:
//
//     prompt            tools selected   unavailable_tools   actually available
//     narrow-refactor   12               9                   3
//     research          17               14                  3
//     boost-build       12               9                   3
//
// The fixtures below use those exact numbers. A test that invents round figures
// can pass against a derivation that would fail on the real payload.

import { readFileSync } from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const toDataUrl = (s) => `data:text/javascript;charset=utf-8,${encodeURIComponent(s)}`;

const code = ts.transpileModule(read("./dynamic-plan-view.ts"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
// The module imports nothing, so the transpiled output is self-contained.
const { planToolAvailability, planProvisioning, planResourceView, measuredCount } = await import(
  toDataUrl(code)
);

// ---------------------------------------------------------------------------
// Fixtures taken from the live payload
// ---------------------------------------------------------------------------

/** The research prompt's real arithmetic: 17 selected, 14 unavailable. */
const researchResources = (over = {}) => ({
  goal_id: "goal-1",
  bots: { bot_code_specialist_2722: {}, bot_qa_auditor_8a9d: {}, bot_learning_engine_3a1b: {} },
  skills: [],
  tools: [
    "search_web",
    "read_url_content",
    "deep_research",
    "five_pass_search",
    "session_search",
    "query_knowledge_graph",
    "bot_roster_tool",
    "subagent_control",
    "a2a_tool",
    "group_chat_tool",
    "swarm_tool",
    "discipline_team_tool",
    "read_file",
    "write_file",
    "replace_file_content",
    "run_command",
    "view_file",
  ],
  mcp_servers: [],
  model_tier: "deep_reasoning",
  metadata: {
    total_bots: 3,
    provisioned: false,
    total_tools: 17,
    unavailable_tools: [
      "search_web",
      "read_url_content",
      "five_pass_search",
      "bot_roster_tool",
      "subagent_control",
      "a2a_tool",
      "group_chat_tool",
      "swarm_tool",
      "discipline_team_tool",
      "read_file",
      "write_file",
      "replace_file_content",
      "run_command",
      "view_file",
    ],
    total_skills: 0,
    skill_generation: "not requested",
    mcp_generation: "not requested",
  },
  ...over,
});

// ---------------------------------------------------------------------------
// The tool list: the defect this module exists for
// ---------------------------------------------------------------------------

test("the real 17-selected / 14-unavailable payload yields 3 available", () => {
  const a = planToolAvailability(researchResources());
  assert.equal(a.selected.length, 17);
  assert.equal(a.blocked.length, 14);
  assert.equal(a.available.length, 3);
  assert.deepEqual(a.available, ["deep_research", "session_search", "query_knowledge_graph"]);
});

test("the sentence names the shortfall rather than printing the list", () => {
  const a = planToolAvailability(researchResources());
  assert.equal(a.sentence, "14 of 17 selected tool(s) are unavailable");
  // The bare list is exactly what the panel used to print.
  assert.doesNotMatch(a.sentence, /search_web/);
});

test("a fully available plan says so and does not alarm", () => {
  const a = planToolAvailability(researchResources({ metadata: { unavailable_tools: [] } }));
  assert.equal(a.blocked.length, 0);
  assert.equal(a.available.length, 17);
  assert.match(a.sentence, /all 17 selected tool\(s\) are available/);
});

test("an absent metadata block is 'not reported', never '0 unavailable'", () => {
  // The opposite error from the original defect, and just as wrong: claiming a
  // healthy tool set for a payload that never measured one.
  for (const bad of [undefined, null, {}, { metadata: undefined }, { metadata: null }]) {
    const a = planToolAvailability({ tools: ["read_file"], ...(bad ? { metadata: bad.metadata } : {}) });
    assert.equal(a.reported, false);
    assert.match(a.sentence, /availability not reported/);
    assert.doesNotMatch(a.sentence, /unavailable/);
  }
});

test("an empty unavailable list is a measurement, distinct from an absent one", () => {
  const measured = planToolAvailability({ tools: ["a"], metadata: { unavailable_tools: [] } });
  const absent = planToolAvailability({ tools: ["a"], metadata: {} });
  assert.equal(measured.reported, true);
  assert.equal(absent.reported, false);
  assert.notEqual(measured.sentence, absent.sentence);
});

test("a name in unavailable_tools that was never selected does not inflate the count", () => {
  // The count is about the SELECTED list. A blocked tool that this plan did not
  // ask for is not a shortfall in this plan.
  const a = planToolAvailability({
    tools: ["a", "b"],
    metadata: { unavailable_tools: ["a", "zzz", "yyy"] },
  });
  assert.equal(a.blocked.length, 1);
  assert.equal(a.sentence, "1 of 2 selected tool(s) are unavailable");
});

test("an empty selection says so rather than falling back to a placeholder name", () => {
  // `tools.join(", ") || "alpha.tools"` printed a module path when the list was
  // empty — a value the server never sent.
  const a = planToolAvailability({ tools: [], metadata: { unavailable_tools: [] } });
  assert.equal(a.sentence, "no tools were selected for this plan");
  assert.doesNotMatch(a.sentence, /alpha\.tools/);
});

test("non-string junk in either list is ignored rather than rendered", () => {
  const a = planToolAvailability({
    tools: ["ok", 7, null, "", "two"],
    metadata: { unavailable_tools: [null, "ok"] },
  });
  assert.deepEqual(a.selected, ["ok", "two"]);
  assert.deepEqual(a.blocked, ["ok"]);
});

// ---------------------------------------------------------------------------
// Provisioning, skills and MCP
// ---------------------------------------------------------------------------

test("provisioned:false is stated as a limitation, not glossed", () => {
  const p = planProvisioning(researchResources());
  assert.equal(p.provisioned, false);
  assert.match(p.sentence, /not provisioned/);
  assert.match(p.sentence, /plan on this Gateway, not a live roster/);
});

test("the heading drops 'Assembled' unless the server said provisioning happened", () => {
  // The heading asserted assembly the payload denied. Measured live: every one
  // of the three prompts returned `provisioned: false`.
  assert.equal(planResourceView(researchResources()).heading, "Planned Bot Specialists & Tools");
  assert.equal(
    planResourceView({ ...researchResources(), metadata: { provisioned: true } }).heading,
    "Assembled Bot Specialists & Tools",
  );
});

test("an absent provisioned flag is not reported, never false and never true", () => {
  const p = planProvisioning({ tools: [], metadata: {} });
  assert.equal(p.provisioned, null);
  assert.match(p.sentence, /not reported/);
  assert.doesNotMatch(p.sentence, /not provisioned/);
});

test("no metadata at all reads as not reported", () => {
  assert.match(planProvisioning({ tools: [] }).sentence, /not reported/);
});

test("the server's own skill sentence is kept verbatim", () => {
  // Two real values, from two prompts. They mean different things and neither is
  // "no skills found".
  const notRequested = planProvisioning(researchResources());
  assert.equal(notRequested.skillSentence, "not requested");

  const blocked = planProvisioning({
    metadata: { total_skills: 0, skill_generation: "unavailable — no generator bound" },
  });
  assert.equal(blocked.skillSentence, "unavailable — no generator bound");
  assert.doesNotMatch(blocked.skillSentence, /no skills/i);
});

test("total_skills:0 is a fact about the request, not about the installation", () => {
  // With no generation sentence the count still may not be presented as a
  // catalogue fact.
  const p = planProvisioning({ metadata: { total_skills: 0 } });
  assert.equal(p.totalSkills, 0);
  assert.match(p.skillSentence, /0 skills selected for this plan/);
  assert.doesNotMatch(p.skillSentence, /exist|available skills|no skills found/i);
});

test("a missing total_skills and a zero one are different readings", () => {
  assert.match(planProvisioning({ metadata: {} }).skillSentence, /not reported/);
  assert.match(planProvisioning({ metadata: { total_skills: 0 } }).skillSentence, /0 skills selected/);
});

test("MCP generation falls back to a not-reported sentence, not to silence", () => {
  assert.equal(planProvisioning(researchResources()).mcpSentence, "not requested");
  assert.match(planProvisioning({ metadata: {} }).mcpSentence, /not reported/);
});

test("counts preserve zero and reject junk", () => {
  assert.equal(measuredCount(0), 0);
  assert.equal(measuredCount(3), 3);
  for (const bad of [null, undefined, NaN, -1, "3", Infinity]) {
    assert.equal(measuredCount(bad), null, `${String(bad)} must not read as a count`);
  }
});

// ---------------------------------------------------------------------------
// Whole-view degradation
// ---------------------------------------------------------------------------

test("a non-object resources payload degrades instead of throwing", () => {
  for (const bad of [undefined, null, "nope", 7, []]) {
    const v = planResourceView(bad);
    assert.equal(v.tools.selected.length, 0);
    assert.equal(v.tools.reported, false);
    assert.match(v.provisioning.sentence, /not reported/);
  }
});

test("the real payload renders a coherent whole", () => {
  const v = planResourceView(researchResources());
  assert.equal(v.tools.blocked.length, 14);
  assert.equal(v.tools.available.length, 3);
  assert.equal(v.provisioning.totalBots, 3);
  assert.equal(v.heading, "Planned Bot Specialists & Tools");
});