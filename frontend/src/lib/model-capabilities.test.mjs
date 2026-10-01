/**
 * Model-capability derivation: the honesty contract.
 *
 * The invariant under test throughout: **a capability nobody reported is not a
 * capability the model lacks.** `null` is a third state, distinct from `false`,
 * and every helper here must preserve that distinction. A regression that folds
 * unknown into "no" tells the user their agent cannot read images when in fact
 * the provider was never asked — and that mistake is invisible until someone
 * picks a vision model for a screenshot task and it silently fails.
 *
 * Runs under plain `node --test`; the module is transpiled on load.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { moduleUrl } from "./test-modules.mjs";

// `moduleUrl` appends `.ts` itself. A static `import()` of the returned data
// URL cannot destructure at the top level, so the namespace is bound first and
// the exports are read off it below.
const capabilitiesModule = await import(await moduleUrl("model-capabilities"));

const {
  toCapability,
  modelCapabilities,
  effectiveContextWindow,
  formatContextWindow,
  formatPrice,
  capabilityBadges,
  indexDiscoveredModels,
  findDiscoveredModel,
} = capabilitiesModule;

const MODEL = {
  id: "alpha-free",
  name: "Alpha Free",
  provider: "alpha-free",
};

/* ------------------------------------------------------------------ */
/* toCapability                                                        */
/* ------------------------------------------------------------------ */

test("a real boolean passes through unchanged", () => {
  assert.equal(toCapability(true), true);
  assert.equal(toCapability(false), false);
});

test("anything that is not a boolean is unknown, not false", () => {
  for (const value of [undefined, null, 0, 1, "", "true", "false", {}, [], NaN]) {
    assert.equal(toCapability(value), null, `expected null for ${JSON.stringify(value)}`);
  }
});

/* ------------------------------------------------------------------ */
/* Tri-state preservation                                              */
/* ------------------------------------------------------------------ */

test("an undeclared vision flag reads as unknown, never as no", () => {
  const caps = modelCapabilities({ ...MODEL }, null);
  assert.equal(caps.vision, null);
  // The inverse is the actual defect this suite exists to catch.
  assert.notEqual(caps.vision, false);
});

test("an explicit false stays false", () => {
  const caps = modelCapabilities({ ...MODEL, supports_vision: false }, null);
  assert.equal(caps.vision, false);
});

test("an explicit true stays true", () => {
  const caps = modelCapabilities({ ...MODEL, supports_vision: true }, null);
  assert.equal(caps.vision, true);
});

test("generation modalities are unknown without a parameter list", () => {
  const caps = modelCapabilities({ ...MODEL }, null);
  assert.equal(caps.imageGeneration, null);
  assert.equal(caps.video, null);
  assert.equal(caps.speech, null);
});

test("a configured entry keeps its own flags when discovery knows nothing", () => {
  const caps = modelCapabilities(
    { ...MODEL, supports_vision: true, supports_reasoning_effort: true },
    null,
  );
  assert.equal(caps.vision, true);
  assert.equal(caps.reasoningEffort, true);
});

test("discovery fills a capability the configured entry never declared", () => {
  const caps = modelCapabilities({ ...MODEL }, { id: "alpha-free", supports_vision: true });
  assert.equal(caps.vision, true);
});

test("a configured false is not overwritten by a discovery true", () => {
  // First-wins by name is the server's own merge order; the client must not
  // silently upgrade a deliberate "no".
  const caps = modelCapabilities({ ...MODEL, supports_vision: false }, { id: "alpha-free", supports_vision: true });
  assert.equal(caps.vision, false);
});

/* ------------------------------------------------------------------ */
/* Reasoning effort                                                    */
/* ------------------------------------------------------------------ */

test("a declared ladder implies the entry accepts an effort", () => {
  const caps = modelCapabilities({ ...MODEL, reasoning_efforts: ["low", "medium", "high"] }, null);
  assert.equal(caps.reasoningEffort, true);
});

test("an empty ladder is not a declared ladder", () => {
  const caps = modelCapabilities({ ...MODEL, reasoning_efforts: [] }, null);
  assert.equal(caps.reasoningEffort, null);
});

test("supports_reasoning_effort alone is honoured", () => {
  assert.equal(modelCapabilities({ ...MODEL, supports_reasoning_effort: true }, null).reasoningEffort, true);
  assert.equal(modelCapabilities({ ...MODEL, supports_reasoning_effort: false }, null).reasoningEffort, false);
});

test("tool support is inferred only from a real answer, never from silence", () => {
  assert.equal(modelCapabilities({ ...MODEL }, null).tools, null);
  assert.equal(modelCapabilities({ ...MODEL, reasoning_efforts: ["high"] }, null).tools, true);
});

test("an explicit tools value wins over the ladder inference", () => {
  const caps = modelCapabilities({ ...MODEL, supports_tools: false, reasoning_efforts: ["high"] }, null);
  assert.equal(caps.tools, false);
});

/* ------------------------------------------------------------------ */
/* Modality detection                                                  */
/* ------------------------------------------------------------------ */

test("a recognised modality parameter reports that modality only", () => {
  // The provider answered and named `image`, so the other two are real "no"s
  // rather than unknowns.
  const caps = modelCapabilities({ ...MODEL }, { id: "x", supported_parameters: ["image", "temperature"] });
  assert.equal(caps.imageGeneration, true);
  assert.equal(caps.video, false);
  assert.equal(caps.speech, false);
});

test("video and speech are read independently", () => {
  const caps = modelCapabilities({ ...MODEL }, { id: "x", supported_parameters: ["video", "audio"] });
  assert.equal(caps.video, true);
  assert.equal(caps.speech, true);
  assert.equal(caps.imageGeneration, false);
});

test("a namespaced parameter is read from its base name", () => {
  const caps = modelCapabilities({ ...MODEL }, { id: "x", supported_parameters: ["image.high"] });
  assert.equal(caps.imageGeneration, true);
});

test("an unrecognised parameter list reports unknown rather than all-no", () => {
  // Nothing was recognised, so we cannot tell "no modalities" from "a
  // vocabulary we do not know". That distinction is the whole point.
  const caps = modelCapabilities({ ...MODEL }, { id: "x", supported_parameters: ["temperature", "top_p"] });
  assert.equal(caps.imageGeneration, null);
  assert.equal(caps.video, null);
  assert.equal(caps.speech, null);
});

test("an entirely unknown vocabulary is still unknown", () => {
  const caps = modelCapabilities({ ...MODEL }, { id: "x", supported_parameters: ["quantum_entanglement"] });
  assert.equal(caps.imageGeneration, null);
});

test("a non-array parameter list is unknown", () => {
  assert.equal(modelCapabilities({ ...MODEL }, { id: "x", supported_parameters: "image" }).imageGeneration, null);
});

/* ------------------------------------------------------------------ */
/* Context window                                                      */
/* ------------------------------------------------------------------ */

test("the endpoint window is preferred over the published card", () => {
  const value = effectiveContextWindow({
    id: "x",
    context_length: 200000,
    endpoint_context_length: 128000,
    endpoint_max_completion_tokens: 16000,
  });
  assert.equal(value, 128000);
});

test("published plus completion is used when no endpoint window exists", () => {
  const value = effectiveContextWindow({ id: "x", context_length: 100000, endpoint_max_completion_tokens: 8000 });
  assert.equal(value, 108000);
});

test("published alone is the last resort", () => {
  assert.equal(effectiveContextWindow({ id: "x", context_length: 64000 }), 64000);
});

test("no discovery row means no window", () => {
  assert.equal(effectiveContextWindow(null), null);
  assert.equal(effectiveContextWindow(undefined), null);
});

test("a zero or negative window is not a measurement", () => {
  assert.equal(effectiveContextWindow({ id: "x", context_length: 0 }), null);
  assert.equal(effectiveContextWindow({ id: "x", endpoint_context_length: -5 }), null);
});

test("a configured window is the fallback when discovery is silent", () => {
  assert.equal(modelCapabilities({ ...MODEL, context_window: 128000 }, null).contextWindow, 128000);
});

test("a discovery window beats the configured one", () => {
  const caps = modelCapabilities({ ...MODEL, context_window: 128000 }, { id: "x", endpoint_context_length: 200000 });
  assert.equal(caps.contextWindow, 200000);
});

/* ------------------------------------------------------------------ */
/* Formatting                                                          */
/* ------------------------------------------------------------------ */

test("context windows format compactly", () => {
  assert.equal(formatContextWindow(128000), "128K");
  assert.equal(formatContextWindow(1000000), "1M");
  assert.equal(formatContextWindow(1048576), "1M");
  assert.equal(formatContextWindow(2400000), "2.4M");
  assert.equal(formatContextWindow(800), "800");
});

test("an unknown window formats to null, not to zero", () => {
  assert.equal(formatContextWindow(null), null);
  assert.equal(formatContextWindow(0), null);
});

test("a zero price pair is Free, because free is a real price", () => {
  assert.equal(formatPrice(0, 0), "Free");
});

test("a paid price formats both sides", () => {
  assert.equal(formatPrice(3, 15), "$3 in · $15 out / 1M");
});

test("a half-known price is not a price", () => {
  assert.equal(formatPrice(3, null), null);
  assert.equal(formatPrice(null, 15), null);
});

/* ------------------------------------------------------------------ */
/* Badges                                                              */
/* ------------------------------------------------------------------ */

test("every capability gets a badge, in a fixed order", () => {
  const badges = capabilityBadges(modelCapabilities({ ...MODEL }, null));
  assert.deepEqual(
    badges.map((b) => b.key),
    ["vision", "tools", "thinking", "reasoningEffort", "imageGeneration", "video", "speech"],
  );
});

test("the three states are visually distinct", () => {
  const badges = capabilityBadges(modelCapabilities({ ...MODEL, supports_vision: true, supports_tools: false }, null));
  const vision = badges.find((b) => b.key === "vision");
  const tools = badges.find((b) => b.key === "tools");
  const speech = badges.find((b) => b.key === "speech");
  assert.notEqual(vision.className, tools.className);
  assert.notEqual(tools.className, speech.className);
  assert.match(speech.className, /border-dashed/);
});

test("a no badge is struck through, an unknown badge is not", () => {
  const badges = capabilityBadges(modelCapabilities({ ...MODEL, supports_tools: false }, null));
  const tools = badges.find((b) => b.key === "tools");
  const vision = badges.find((b) => b.key === "vision");
  assert.match(tools.className, /line-through/);
  assert.doesNotMatch(vision.className, /line-through/);
});

test("each badge's tooltip states the evidence", () => {
  const badges = capabilityBadges(modelCapabilities({ ...MODEL, supports_vision: true }, null));
  assert.match(badges.find((b) => b.key === "vision").title, /Accepts image input/);
  assert.match(badges.find((b) => b.key === "speech").title, /not reported/);
});

/* ------------------------------------------------------------------ */
/* Discovery indexing                                                  */
/* ------------------------------------------------------------------ */

test("indexing keys by id and keeps the first of a duplicate", () => {
  const index = indexDiscoveredModels([
    { id: "a", context_length: 1 },
    { id: "a", context_length: 999 },
    { id: "b" },
  ]);
  assert.equal(index.size, 2);
  assert.equal(index.get("a").context_length, 1);
});

test("indexing tolerates junk without throwing", () => {
  const index = indexDiscoveredModels([null, undefined, { id: "" }, { id: "ok" }, "nope"]);
  assert.deepEqual([...index.keys()], ["ok"]);
  assert.equal(indexDiscoveredModels(null).size, 0);
});

test("an exact id matches first", () => {
  const index = indexDiscoveredModels([{ id: "free:opencode-zen:space-bunny-free" }]);
  assert.ok(findDiscoveredModel("free:opencode-zen:space-bunny-free", index));
});

test("a router id resolves to its concrete gateway model", () => {
  // `alpha-free` is the router; discovery reports the real gateway model. The
  // UI must still be able to attach its richer facts.
  const index = indexDiscoveredModels([{ id: "space-bunny-free", context_length: 128000 }]);
  assert.equal(findDiscoveredModel("free:opencode-zen:space-bunny-free", index).context_length, 128000);
});

test("a bare id resolves through the free prefix", () => {
  const index = indexDiscoveredModels([{ id: "space-bunny-free" }]);
  assert.ok(findDiscoveredModel("free:opencode-zen:space-bunny-free", index));
});

test("an unknown id resolves to null rather than a wrong row", () => {
  const index = indexDiscoveredModels([{ id: "something-else" }]);
  assert.equal(findDiscoveredModel("free:opencode-zen:space-bunny-free", index), null);
  assert.equal(findDiscoveredModel(null, index), null);
});
