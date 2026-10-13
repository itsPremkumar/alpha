// model-menu.test.mjs — the composer's model menu must consolidate the
// model-related controls without inventing a health verdict nobody measured.
//
// The menu replaced a native `<select>` plus three icon buttons beside it. The
// split was forced by the element: a native `<option>` cannot host a health
// chip, a text field or a link, so "find & probe today's free models" (amber,
// unlabelled), "set an API key" and "open settings" had to live outside the
// control they configure. Everything here exists because the panel can hold
// them — and the one thing that could go wrong is a chip that claims more than
// the probe measured.
//
// The Gateway probes a *provider gateway*, not a model ID. So the load-bearing
// rule across this suite is: a model row may show its provider's verdict, and
// may only ever call it that. "healthy" beside a model would be a claim no
// probe made, and forty green rows would follow from one gateway check.
//
// Pure Node (node --test src/lib/*.test.mjs): no server, no browser, no DOM.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;

const source = read("./model-menu.ts");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;

// Both imports are `import type`, so the transpile elides them. The stubs stay
// as guards: if either ever becomes a value import, this fails loudly instead
// of resolving against a path that does not exist from here.
assert.doesNotMatch(code, /from\s+"@\//, "model-menu.ts must import types only, or the module cannot load here");
const {
  MODEL_GROUP_ORDER,
  GROUP_LABELS,
  QUICK_KEY_PROVIDERS,
  HEALTH_SCOPE_DISCLOSURE,
  groupModels,
  modelMarks,
  modelMenuLabel,
  modelMenuTitle,
  findProviderForModel,
  healthyChip,
  probeCounts,
  probeSummaryText,
  keyProviderTiers,
  selectedModelLabel,
} = await import(toDataUrl(code));

/** The subset of `AIModel` these helpers read. */
const model = (overrides = {}) => ({
  id: "m",
  name: "Model",
  provider: "somewhere",
  ...overrides,
});

/** The subset of `FreeProviderHealth` these helpers read, as `fetchFreeCatalog` builds it. */
const provider = (overrides = {}) => ({
  name: "openrouter",
  healthy: null,
  eligible: false,
  eligibilityKnown: true,
  modelIds: [],
  modelCount: null,
  modelsTruncated: false,
  latencyMs: null,
  ...overrides,
});

/* ══ Grouping ════════════════════════════════════════════════════════════ */

test("groupModels buckets free-keyless, quota and standard apart", () => {
  const groups = groupModels([
    model({ id: "free:pollinations", name: "Pollinations", is_free: true }),
    model({ id: "kilo:nemotron", name: "Nemotron", is_free: true, free_status: "quota" }),
    model({ id: "openai:gpt", name: "GPT", is_free: false }),
  ]);
  assert.deepEqual(groups.keyless.map((m) => m.id), ["free:pollinations"]);
  assert.deepEqual(groups.quota.map((m) => m.id), ["kilo:nemotron"]);
  assert.deepEqual(groups.standard.map((m) => m.id), ["openai:gpt"]);
});

test("groupModels treats the explicit free:<provider> prefixes as keyless without any status field", () => {
  // These gateways report `is_free` and nothing else — no `free_status`, no
  // `quota_type` — so the prefix list is the only thing that can sort them.
  for (const id of ["free:ovhcloud", "free:llm7", "free:aihorde"]) {
    const groups = groupModels([model({ id, is_free: true })]);
    assert.deepEqual(groups.keyless.map((m) => m.id), [id], `${id} must be keyless`);
    assert.equal(groups.quota.length, 0);
  }
});

test("groupModels sends alpha-free to keyless and leaves the groups in render order", () => {
  assert.deepEqual(groupModels([model({ id: "alpha-free", name: "Alpha Free" })]).keyless.map((m) => m.id), ["alpha-free"]);
  assert.deepEqual(MODEL_GROUP_ORDER, ["keyless", "quota", "standard"]);
  for (const key of MODEL_GROUP_ORDER) assert.ok(GROUP_LABELS[key], `${key} needs a label`);
});

/* ══ Capability marks: not reported is not "no" ═══════════════════════════ */

const CAPS = (over = {}) => ({ vision: null, tools: null, reasoningEffort: null, contextWindow: null, ...over });

test("an unmeasured capability renders nothing rather than a struck-out no", () => {
  // The wrong reading this exists to stop: `null` is *not reported*, and a
  // cross would be a claim the server never made about the model.
  assert.equal(modelMarks(model(), CAPS()), "");
  assert.equal(modelMenuLabel(model({ name: "M" }), CAPS()), "M");
});

test("a measured capability renders its mark, and unknowns alongside knowns stay partial", () => {
  assert.equal(modelMarks(model(), CAPS({ tools: true })), "🔧");
  assert.equal(modelMenuLabel(model({ name: "M" }), CAPS({ vision: true, tools: true })), "M 👁🔧");
});

test("the tooltip spells out each unknown rather than dropping it", () => {
  const title = modelMenuTitle(model({ name: "M" }), CAPS({ vision: false }), "128k");
  assert.match(title, /image input: no/);
  assert.match(title, /tools: not reported/);
  assert.match(title, /reasoning effort: not reported/);
  assert.match(title, /context: 128k tokens/);
});

test("the tooltip omits the context clause entirely when none was declared", () => {
  assert.doesNotMatch(modelMenuTitle(model({ name: "M" }), CAPS(), null), /context:/);
});

/* ══ Provider resolution and the health chip ══════════════════════════════ */

test("a model resolves to its provider by exact id, free: suffix, then declared field", () => {
  const p = provider({ name: "Kilo", modelIds: ["kilo/nemotron-3"] });
  assert.equal(findProviderForModel([p], model({ id: "kilo/nemotron-3", provider: "" })), p, "exact id");
  assert.equal(findProviderForModel([p], model({ id: "free:Kilo", provider: "ignored" })), p, "free: suffix, case-insensitive");
  assert.equal(findProviderForModel([p], model({ id: "other", provider: "kilo" })), p, "declared provider, case-insensitive");
});

test("a model with no identifiable provider gets no chip, not a borrowed verdict", () => {
  // This is the honesty spine: `null` here means "we do not know", and the row
  // renders nothing. Snapping it to the first provider would put a green tick
  // on a model the probe never associated with anything.
  const p = provider({ name: "openrouter", healthy: true });
  assert.equal(findProviderForModel([p], model({ id: "mystery", provider: "elsewhere" })), null);
  assert.equal(healthyChip([p], model({ id: "mystery", provider: "elsewhere" })), null);
});

test("healthyChip carries three states and always names the provider", () => {
  const listed = (healthy) => {
    const p = provider({ name: "kilo", healthy, modelIds: ["kilo/x"] });
    return healthyChip([p], model({ id: "kilo/x" }));
  };
  assert.equal(listed(true).key, "healthy");
  assert.equal(listed(false).key, "failing");
  // `healthy: null` is a third state: not probed is not failing, and is never green.
  assert.equal(listed(null).key, "not_probed");
  assert.doesNotMatch(listed(null).dotClass, /emerald/, "unprobed must not wear the healthy dot");
  for (const healthy of [true, false, null]) {
    assert.match(listed(healthy).label, /^provider /, "the chip must name the provider, never the model");
    assert.doesNotMatch(listed(healthy).label, /^healthy$/, "a bare 'healthy' would be a model claim");
  }
});

/* ══ Probe summary: every state spelled out ═══════════════════════════════ */

test("probeCounts buckets each state separately and counts eligibility", () => {
  const counts = probeCounts([provider({ healthy: true, eligible: true }), provider({ healthy: false }), provider({ healthy: null })]);
  assert.deepEqual(counts, { healthy: 1, failing: 1, notProbed: 1, total: 3, eligible: 1 });
});

test("an empty provider list is two different claims and reads as each", () => {
  // "No providers" and "the read failed" are opposite situations. Reporting a
  // confident `0/0 healthy` for either would hide which one happened.
  assert.match(probeSummaryText([], false), /reported no free providers/);
  assert.match(probeSummaryText([], true), /read failed/);
  assert.doesNotMatch(probeSummaryText([], false), /\d+ of 0/);
});

test("the summary names failing and unprobed providers separately, and omits zero buckets", () => {
  const text = probeSummaryText([provider({ healthy: true }), provider({ healthy: false }), provider({ healthy: null }), provider({ healthy: true, eligible: true })], false);
  assert.match(text, /2 of 4 providers healthy/);
  assert.match(text, /1 failing/);
  assert.match(text, /1 not probed/);
  assert.match(text, /1 eligible/);
  // A zero bucket adds no clause — "0 failing" is noise, and the number is
  // already implied by "2 of 4".
  assert.doesNotMatch(probeSummaryText([provider({ healthy: true }), provider({ healthy: true })], false), /failing|not probed/);
});

/* ══ Key-provider tiers ═══════════════════════════════════════════════════ */

test("keyProviderTiers lists only the tiers present, in label order", () => {
  assert.deepEqual(keyProviderTiers(QUICK_KEY_PROVIDERS), ["free", "trial", "paid"]);
  // An absent tier earns no heading — a group label over nothing reads as a
  // measured empty set rather than "this deployment offers none".
  assert.deepEqual(keyProviderTiers([{ id: "a", label: "A", tier: "paid" }]), ["paid"]);
  assert.deepEqual(keyProviderTiers([]), []);
});

test("the shipped provider list marks every commercial entry as billable", () => {
  // The tier is the difference between a key you add to never pay and one that
  // starts billing. A paid provider labelled as free is the expensive mistake.
  const commercial = QUICK_KEY_PROVIDERS.filter((p) => ["openai", "anthropic", "deepseek"].includes(p.id));
  assert.equal(commercial.length, 3);
  for (const p of commercial) assert.equal(p.tier, "paid", `${p.id} must be marked billable`);
  assert.match(HEALTH_SCOPE_DISCLOSURE, /per provider, not per model/);
});

/* ══ Trigger label ════════════════════════════════════════════════════════ */

test("selectedModelLabel resolves the id to a name and reports an unknown id honestly", () => {
  const models = [model({ id: "a", name: "Alpha" }), model({ id: "b", name: "Beta" })];
  assert.equal(selectedModelLabel(models, "b"), "Beta");
  // An id the list does not hold is `null`, not a fallback to the first row —
  // the trigger then renders its "auto-routed" default rather than lying.
  assert.equal(selectedModelLabel(models, "gone"), null);
  assert.equal(selectedModelLabel([], "a"), null);
});

/* ══ Source pins on the component ═════════════════════════════════════════ */

const menu = read("../components/ModelMenu.tsx");

test("ModelMenu renders the shared disclosure once, never per row", () => {
  // A per-row tint is what would make one gateway probe look like forty model
  // tests; the scope is stated once, at the bottom. Exactly two occurrences in
  // the component: the import, and the single render site.
  assert.equal((menu.match(/HEALTH_SCOPE_DISCLOSURE/g) || []).length, 2);
  // The sentence itself is owned by the lib, so this suite drives it directly
  // rather than scraping it out of markup.
  assert.match(HEALTH_SCOPE_DISCLOSURE, /per provider, not per model/);
  assert.match(HEALTH_SCOPE_DISCLOSURE, /not individually proven/);
  // The per-row chip, by contrast, always names the provider, and says what it
  // is not in its own tooltip.
  assert.match(menu, /\{chip\.label\}/);
  assert.match(menu, /a provider probe, not a test of this model/);
});

test("ModelMenu disables the probe while one is in flight", () => {
  assert.match(menu, /onClick=\{onRefresh\}\s*\n\s*disabled=\{refreshing\}/);
});

test("ModelMenu never paints a result from a click", () => {
  // The key save awaits the server, and a failure shows the server's own reason.
  assert.match(menu, /await onSaveKey\(provider, apiKey\)/);
  assert.match(menu, /err instanceof Error \? err\.message/);
});

test("ModelMenu closes before leaving for settings, and hides rows it cannot serve", () => {
  assert.match(menu, /\{\s*close\(\);\s*onOpenSettings\(\);/);
  // A surface with no probe or no settings route gets no dead control — and
  // the whole action block now also gates on having a disclosure to show,
  // because the disclosure moved inside it.
  assert.match(menu, /\{onRefresh \|\| onOpenSettings \|\| providers\.length > 0 \|\| props\.readFailed \? \(/);
  assert.match(menu, /\{onRefresh \? \(/);
  assert.match(menu, /\{onOpenSettings \? \(/);
});

test("ModelMenu pins its action block to the panel floor so the controls stay reachable", () => {
  // With fifty models above it, probe/key/settings sat below the fold — the
  // consolidation this menu exists for was only nominal until you scrolled the
  // whole catalog. The block is sticky, carries the panel's own background so
  // rows cannot show through it, and pulls up over the container's `space-y`
  // gap for the same reason.
  assert.match(menu, /sticky bottom-0/);
  assert.match(menu, /bg-card/);
  assert.match(menu, /-mx-2\.5 -mb-2\.5 -mt-2/);
  // The disclosure travels with it: a scope note you have to hunt for is not
  // a disclosure, so it renders inside the pinned block, not after it.
  const stickyIdx = menu.indexOf("sticky bottom-0");
  const disclosureIdx = menu.indexOf("HEALTH_SCOPE_DISCLOSURE", menu.indexOf("return ("));
  assert.ok(stickyIdx > 0 && disclosureIdx > stickyIdx, "the disclosure must render inside the pinned block");
});
