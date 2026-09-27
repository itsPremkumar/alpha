/**
 * Reasoning-effort ladder: honesty invariants for the composer picker.
 *
 * These pin the *inversions* — the ways a level picker can start lying — not
 * just the happy path. Every case here corresponds to a failure that is
 * invisible in the UI: a rung the server never sent, a clamp the user was not
 * told about, a "Default" that quietly pins a level forever, or a model with no
 * ladder being offered a working-looking menu.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/lib/reasoning-effort.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const mod = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`);

const {
  DEFAULT_EFFORT,
  FALLBACK_LADDER,
  clampEffort,
  effortLabel,
  effortOptions,
  effortUnavailableReason,
  modelEffortLadder,
  modelSupportsEffort,
  normalizeEffort,
  reconcileEffortForModel,
} = mod;

const OPENAI = { reasoning_efforts: ["low", "medium", "high", "xhigh", "max"], default_reasoning_effort: "high" };
const GPT51 = { reasoning_efforts: ["none", "low", "medium", "high"] };
const NO_LADDER = { reasoning_efforts: null };
const UNDECLARED = {};

// --- the ladder itself -------------------------------------------------------

test("the canonical ladder is ordered weakest to strongest", () => {
  assert.deepEqual([...FALLBACK_LADDER], ["none", "minimal", "low", "medium", "high", "xhigh", "max"]);
  const ranks = FALLBACK_LADDER.map((rung) => FALLBACK_LADDER.indexOf(rung));
  assert.deepEqual(ranks, [...ranks].sort((a, b) => a - b));
});

test("normalizeEffort accepts the spellings providers and agents actually use", () => {
  const cases = [
    ["x-high", "xhigh"],
    ["x_high", "xhigh"],
    ["XHigh", "xhigh"],
    ["EXTRA_HIGH", "xhigh"],
    ["off", "none"],
    ["OFF", "none"],
    ["disabled", "none"],
    ["min", "minimal"],
    ["ultra", "max"],
    ["ultrathink", "max"],
    ["maximum", "max"],
    ["adaptive", "high"],
    ["  High  ", "high"],
  ];
  for (const [input, expected] of cases) {
    assert.equal(normalizeEffort(input), expected, `${input} -> ${expected}`);
  }
});

test("normalizeEffort maps every 'no explicit request' spelling to null, not to a rung", () => {
  // The trap: if "default" resolved to a level, the UI would show a checkmark
  // next to a level the provider never chose.
  for (const input of [null, undefined, "", "   ", "default", "DEFAULT", "auto", "provider", "inherit"]) {
    assert.equal(normalizeEffort(input), null, `${String(input)} should be null`);
  }
});

test("normalizeEffort refuses a value that names no rung", () => {
  for (const input of ["bogus", "gpt-5", "veryhigh", 42, true, {}, []]) {
    assert.equal(normalizeEffort(input), null, `${String(input)} should be unrecognized`);
  }
});

test("normalizeEffort never treats a boolean as a rung", () => {
  // `true` is what a truthy check produces from an old `thinking_enabled` flag.
  // Coercing it to a level would silently switch the model to a rung.
  assert.equal(normalizeEffort(true), null);
  assert.equal(normalizeEffort(false), null);
});

// --- a model's declared ladder ------------------------------------------------

test("a declared ladder is ordered by the canonical ladder, not by the server's order", () => {
  assert.deepEqual(modelEffortLadder({ reasoning_efforts: ["max", "low", "high"] }), ["low", "high", "max"]);
});

test("a declared ladder drops unknown rungs and duplicates", () => {
  assert.deepEqual(modelEffortLadder({ reasoning_efforts: ["low", "low", "nope", "HIGH"] }), ["low", "high"]);
});

test("an alias inside a declared ladder is rewritten, not discarded", () => {
  assert.deepEqual(modelEffortLadder({ reasoning_efforts: ["off", "x-high", "high"] }), ["none", "high", "xhigh"]);
});

test("a model that declares nothing is not a model that declares zero levels", () => {
  for (const model of [NO_LADDER, UNDECLARED, { reasoning_efforts: [] }, { reasoning_efforts: "low" }, null, undefined]) {
    assert.deepEqual(modelEffortLadder(model), []);
    assert.equal(modelSupportsEffort(model), false);
  }
});

test("one declared rung is still a ladder the picker can use", () => {
  assert.deepEqual(modelEffortLadder({ reasoning_efforts: ["high"] }), ["high"]);
  assert.equal(modelSupportsEffort({ reasoning_efforts: ["high"] }), true);
});

// --- clamping matches the server ---------------------------------------------

test("clamp picks the strongest declared rung at or below the request", () => {
  assert.equal(clampEffort("high", ["low", "medium", "high", "xhigh", "max"]), "high");
  assert.equal(clampEffort("xhigh", ["low", "medium", "high"]), "high", "above the ceiling clamps down");
  assert.equal(clampEffort("max", ["low", "medium", "high", "xhigh"]), "xhigh");
});

test("clamp raises a request below every declared rung to the floor", () => {
  // The one place raising is correct: answering "less" than asked is the only
  // option left, and the alternative is a 400.
  assert.equal(clampEffort("none", ["low", "medium", "high"]), "low");
  assert.equal(clampEffort("minimal", ["medium", "high"]), "medium");
});

test("clamp never returns more effort than requested, except to reach the declared floor", () => {
  const ladder = ["low", "medium", "high", "xhigh", "max"];
  for (const request of FALLBACK_LADDER) {
    const clamped = clampEffort(request, ladder);
    if (!clamped) continue;
    const belowFloor = FALLBACK_LADDER.indexOf(request) < FALLBACK_LADDER.indexOf(ladder[0]);
    if (belowFloor) {
      // Deliberate: a request under the floor is raised to it, because the
      // alternative is a provider 400 rather than a cheaper answer. The picker
      // discloses this raise in the open menu rather than applying it silently.
      assert.equal(clamped, ladder[0], `${request} should be raised to the floor`);
      continue;
    }
    assert.ok(
      FALLBACK_LADDER.indexOf(clamped) <= FALLBACK_LADDER.indexOf(request),
      `${request} clamped to ${clamped} would increase effort`,
    );
  }
});

test("clamp against an empty ladder is null, not a guess", () => {
  assert.equal(clampEffort("high", []), null);
  assert.equal(clampEffort("high", null), null);
});

// --- reconciling a stored selection against a new model ----------------------

test("a selection the new model serves is kept verbatim", () => {
  assert.equal(reconcileEffortForModel("xhigh", OPENAI), "xhigh");
  assert.equal(reconcileEffortForModel("low", OPENAI), "low");
});

test("a selection the new model does not serve falls back to Default, not to a silent clamp", () => {
  // Falling back to a clamp would show one level while the run uses another.
  assert.equal(reconcileEffortForModel("max", GPT51), DEFAULT_EFFORT);
  assert.equal(reconcileEffortForModel("xhigh", NO_LADDER), DEFAULT_EFFORT);
  assert.equal(reconcileEffortForModel("max", UNDECLARED), DEFAULT_EFFORT);
});

test("Default survives every model, including one with no ladder", () => {
  for (const model of [OPENAI, GPT51, NO_LADDER, UNDECLARED, null]) {
    assert.equal(reconcileEffortForModel(DEFAULT_EFFORT, model), DEFAULT_EFFORT);
  }
});

test("a stored selection is normalized on read, so an old alias is honoured not dropped", () => {
  assert.equal(reconcileEffortForModel("x-high", OPENAI), "xhigh");
  assert.equal(reconcileEffortForModel("MAX", OPENAI), "max");
});

test("garbage in localStorage falls back to Default rather than to a rung", () => {
  for (const stored of ["", "   ", "null", "undefined", "{}", "turbo"]) {
    assert.equal(reconcileEffortForModel(stored, OPENAI), DEFAULT_EFFORT, `${JSON.stringify(stored)}`);
  }
});

// --- the option list ---------------------------------------------------------

test("the option list is Default first, then the model's rungs, strongest last", () => {
  const options = effortOptions(OPENAI);
  assert.equal(options[0].value, DEFAULT_EFFORT);
  assert.deepEqual(
    options.slice(1).map((o) => o.value),
    ["low", "medium", "high", "xhigh", "max"],
  );
  // Default must never be offered twice as a rung.
  assert.equal(options.filter((o) => o.value === DEFAULT_EFFORT).length, 1);
});

test("the option list offers only rungs the model declares", () => {
  const values = effortOptions(GPT51).map((o) => o.value);
  assert.ok(!values.includes("xhigh"));
  assert.ok(!values.includes("max"));
  assert.deepEqual(values, [DEFAULT_EFFORT, "none", "low", "medium", "high"]);
});

test("a model with no ladder offers only Default, so the menu is never a dead control", () => {
  for (const model of [NO_LADDER, UNDECLARED, null]) {
    const values = effortOptions(model).map((o) => o.value);
    assert.deepEqual(values, [DEFAULT_EFFORT], `for ${JSON.stringify(model)}`);
  }
});

test("the model default is labelled on its rung and named in the Default hint", () => {
  const options = effortOptions(OPENAI);
  assert.equal(options.find((o) => o.value === "high").isModelDefault, true);
  assert.match(options[0].hint, /High/, "the Default hint must disclose the entry's own rung");
  // A model that pins no rung must not claim one does.
  assert.doesNotMatch(effortOptions({ reasoning_efforts: ["low"] })[0].hint, /entry runs/);
});

test("every rung carries a trade-off hint, and Default is not left blank", () => {
  for (const option of effortOptions(OPENAI)) {
    assert.ok(option.hint && option.hint.length > 10, `${option.value} needs a hint`);
  }
});

test("the option list honours server-supplied labels", () => {
  const options = effortOptions(OPENAI, FALLBACK_LADDER, { xhigh: "Extra High (server)" });
  assert.equal(options.find((o) => o.value === "xhigh").label, "Extra High (server)");
  // An unlabelled rung still renders something readable rather than blank.
  assert.equal(options.find((o) => o.value === "max").label, "max");
});

// --- the trigger label -------------------------------------------------------

test("the trigger label is the selected rung, or Default", () => {
  assert.equal(effortLabel("xhigh", OPENAI), "Extra High");
  assert.equal(effortLabel(DEFAULT_EFFORT, OPENAI), "Default");
  assert.equal(effortLabel("garbage", OPENAI), "Default");
  assert.equal(effortLabel("xhigh", OPENAI, { xhigh: "Deep" }), "Deep");
});

// --- why the control is unavailable ------------------------------------------

test("a model with rungs is available, with no reason to state", () => {
  assert.equal(effortUnavailableReason(OPENAI, [OPENAI, NO_LADDER], "openai"), null);
});

test("unavailability states a reason instead of rendering a dead control", () => {
  const reason = effortUnavailableReason(NO_LADDER, [OPENAI, NO_LADDER], "plain");
  assert.ok(reason && /declares no reasoning-effort levels/.test(reason), String(reason));
});

test("with auto-routing selected, the reason points at the model rather than at a missing model", () => {
  const withRungs = effortUnavailableReason(null, [OPENAI], "default");
  assert.ok(withRungs && /Pick a model/.test(withRungs), String(withRungs));
  const noneAtAll = effortUnavailableReason(null, [NO_LADDER], "default");
  assert.ok(noneAtAll && /No configured model/.test(noneAtAll), String(noneAtAll));
});

test("an unresolvable model id is reported as fixed, never as a load failure", () => {
  const reason = effortUnavailableReason(undefined, [OPENAI], "model-that-vanished");
  assert.ok(reason && /declares no reasoning-effort levels/.test(reason), String(reason));
});

// --- source pins: the control is actually wired to the request ---------------
//
// The pure helpers above can all be correct while the picker is never mounted
// or the selection never reaches the run body. These read the real sources, the
// same way the other client contract tests in this repo do.

const chatView = readFileSync(new URL("../src/components/ChatView.tsx", import.meta.url), "utf8");
const composer = readFileSync(new URL("../src/components/Composer.tsx", import.meta.url), "utf8");
const picker = readFileSync(new URL("../src/components/ReasoningEffortPicker.tsx", import.meta.url), "utf8");

test("a non-default effort is sent with the run, and the default is omitted", () => {
  // `default` must be omitted rather than sent as a level: the run boundary
  // rejects a value that names no rung, so sending the literal string would
  // 422 every turn.
  assert.match(chatView, /reasoningEffort !== DEFAULT_EFFORT \? \{ reasoning_effort: reasoningEffort \} : \{\}/);
  assert.match(chatView, /reasoning_effort: reasoningEffort/);
});

test("the composer mounts the picker, and only when a handler is supplied", () => {
  assert.match(composer, /<ReasoningEffortPicker/);
  // A read-only surface must not render a control it cannot drive.
  assert.match(composer, /\{onEffortChange && \(\s*<ReasoningEffortPicker/);
});

test("a model switch re-resolves the effort instead of carrying a dead rung across", () => {
  const reconciliations = chatView.match(/reconcileEffortForModel\(/g) ?? [];
  assert.ok(reconciliations.length >= 3, `expected the switch, settings, and restore paths to reconcile, found ${reconciliations.length}`);
});

test("a stored effort is only restored when the serving model declares that rung", () => {
  assert.match(chatView, /reconcileEffortForModel\(savedEffort, target\)/);
  // A rung that no longer applies must be cleared, not left in storage to be
  // re-read on every load.
  assert.match(chatView, /localStorage\.removeItem\("alpha_reasoning_effort"\)/);
});

test("the picker offers no rung the model did not declare", () => {
  // The list is built from the declared ladder alone; there is no path in the
  // component that appends a rung of its own.
  assert.match(picker, /effortOptions\(model, ladder, labels\)/);
  assert.doesNotMatch(picker, /FALLBACK_LADDER\.map\(/);
  assert.doesNotMatch(picker, /FALLBACK_LADDER\.forEach\(/);
});

test("the picker states the fixed reason rather than rendering a dead control", () => {
  assert.match(picker, /effortUnavailableReason\(/);
  assert.match(picker, /Reasoning fixed/);
  assert.doesNotMatch(picker, /disabled=\{true\}[\s\S]{0,80}effort/i, "a permanently disabled menu implies a pending state that does not exist");
});
