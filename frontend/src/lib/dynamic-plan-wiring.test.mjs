// dynamic-plan-wiring.test.mjs — the preview panel must keep using the honest
// derivation, and must not go back to printing a bare tool list.
//
// ## Why these are source pins
//
// No test harness in this repo loads `WorkflowsSection.tsx`: it is a 2400-line
// component with a deep import graph, and `dynamic-plan-view.test.mjs` drives the
// pure module directly. So the pure module can be perfectly correct while the
// panel ignores it — which is EXACTLY the defect this feature is about. The
// module was written because the panel printed:
//
//     Tools: {resources.tools.join(", ") || "alpha.tools"}
//
// while the same payload carried `metadata.unavailable_tools` listing most of
// those names. A unit test of a derivation nothing calls passes either way.
//
// Each pin names the specific string a refactor would drop.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const section = read("../components/sections/WorkflowsSection.tsx");
const view = read("./dynamic-plan-view.ts");

// ---------------------------------------------------------------------------
// The derivation is used
// ---------------------------------------------------------------------------

test("the section imports the resource view module", () => {
  assert.match(section, /import \{ planResourceView \} from "@\/lib\/dynamic-plan-view"/);
});

test("the preview derives its resource sentences from the module", () => {
  assert.match(section, /planResourceView\(dynamicPreview\.resources\)/);
});

// ---------------------------------------------------------------------------
// The original defect cannot come back
// ---------------------------------------------------------------------------

test("the bare tool list with its placeholder fallback is gone", () => {
  // This is the defect verbatim. `"alpha.tools"` is a module path the server
  // never sent, rendered as a tool name when the list was empty.
  const flat = section.replace(/\s+/g, " ");
  assert.doesNotMatch(
    flat,
    /resources\.tools\.join\(", "\) \|\| "alpha\.tools"/,
    "the unqualified tool list must not return; it named 12-17 tools of which at most 3 resolve",
  );
  assert.doesNotMatch(flat, /"alpha\.tools"/, "no fabricated module-path fallback");
});

test("the panel never prints the raw selected tool list as if it were available", () => {
  const flat = section.replace(/\s+/g, " ");
  assert.doesNotMatch(
    flat,
    /resources\.tools\.join\(/,
    "print the blocked/available split instead of the union of both",
  );
});

test("the 'Assembled' heading is not hardcoded", () => {
  // Measured live: all three prompts return `provisioned: false`, so a
  // hardcoded "Assembled" contradicts the payload every single time.
  assert.doesNotMatch(
    section,
    /Assembled Bot Specialists/,
    "the heading must follow metadata.provisioned, not assert assembly unconditionally",
  );
  assert.match(section, /\{planResources\.heading\}/, "the heading comes from the module");
});

// ---------------------------------------------------------------------------
// Every absence the module can name is actually rendered
// ---------------------------------------------------------------------------

test("the tool shortfall sentence is rendered", () => {
  assert.match(section, /\{planResources\.tools\.sentence\}/);
});

test("the provisioning sentence is rendered", () => {
  assert.match(section, /\{planResources\.provisioning\.sentence\}/);
});

test("the skills and MCP sentences are rendered", () => {
  assert.match(section, /\{planResources\.provisioning\.skillSentence\}/);
  assert.match(section, /\{planResources\.provisioning\.mcpSentence\}/);
});

test("the blocked tools are named, not merely counted", () => {
  // A count with no list is a claim with no evidence attached: an operator
  // cannot tell whether the missing tool matters to this plan.
  assert.match(section, /planResources\.tools\.blocked\.join\(", "\)/);
  assert.match(section, /planResources\.tools\.available\.join\(", "\)/);
});

// ---------------------------------------------------------------------------
// The module keeps the rules the panel relies on
// ---------------------------------------------------------------------------

test("the module distinguishes an absent reading from a measured empty one", () => {
  assert.match(view, /reported = unavailableRaw !== null/);
  assert.match(view, /availability not reported by the server/);
});

test("the module never turns an absent provisioned flag into false", () => {
  assert.match(view, /typeof metadata\.provisioned === "boolean" \? metadata\.provisioned : null/);
});

test("the module keeps the server's own skill generation sentence", () => {
  assert.match(view, /skillSentence = skillGeneration/);
  assert.match(view, /no generator bound/);
});
