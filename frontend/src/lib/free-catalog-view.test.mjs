// free-catalog-view.test.mjs — the free-models dropdown must make the header's
// counts checkable, without inventing anything the server did not measure.
//
// Found by looking at what the control actually was: a button reading
// `Free models: 8/10 healthy, 8 eligible.` whose `title` repeated the same
// sentence. Three of its numbers were unopenable — which 8 of the 10 providers
// were healthy, which 2 were not, and what "eligible" excluded. The dropdown is
// the evidence for a count that was already being asserted.
//
// The per-provider strings live in `lib/freeCatalogView.ts` precisely so this
// suite can drive the function that produces each sentence, rather than scraping
// prose out of JSX. Pure Node (node --test src/lib/*.test.mjs): no server, no
// browser, no DOM shim, no renderer.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;

const source = read("./freeCatalogView.ts");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
// `freeModels` is imported for types only, so the transpile elides it. The stub
// stays in place as a guard: if a value import is ever added here, this fails
// loudly instead of resolving against a nonexistent path.
const stubUrl = toDataUrl(`export const unusedStub = null;`);
const { healthLabel, healthDot, latencyText, modelCountText, truncationText, freeCatalogSummary, emptyFilterText } =
  await import(toDataUrl(code.replace(/from\s+"\.\/freeModels"/, `from "${stubUrl}"`)));

/** The subset each helper reads, shaped as `fetchFreeCatalog` builds it. */
function provider(overrides = {}) {
  return {
    name: "openrouter",
    healthy: null,
    eligible: false,
    eligibilityKnown: true,
    modelIds: [],
    modelCount: null,
    modelsTruncated: false,
    latencyMs: null,
    ...overrides,
  };
}

/* ══ Tri-state health is three states, not two ════════════════════════════ */

test("an unprobed provider is named 'not probed', never dressed as healthy", () => {
  // `healthy: null` is what the server sends for a provider it never probed or
  // whose probe was inconclusive. Rendering it green is the defect this whole
  // surface exists to prevent — it was previously a hardcoded `bg-emerald-500`
  // that survived `0/10 healthy`.
  const unprobed = provider({ healthy: null });
  assert.equal(healthLabel(unprobed), "not probed");
  assert.equal(healthDot(unprobed), "bg-muted-foreground/40");
  assert.notEqual(healthDot(unprobed), "bg-emerald-500");
  assert.notEqual(healthDot(unprobed), "bg-red-500");
});

test("a proven-healthy provider is green and a failing one is red", () => {
  assert.equal(healthLabel(provider({ healthy: true })), "healthy");
  assert.equal(healthDot(provider({ healthy: true })), "bg-emerald-500");
  assert.equal(healthLabel(provider({ healthy: false })), "failing");
  assert.equal(healthDot(provider({ healthy: false })), "bg-red-500");
});

/* ══ Absence is worded, never zeroed ══════════════════════════════════════ */

test("an unmeasured latency reads 'not reported', never 0 ms and never a dash", () => {
  // Zero is the fastest possible round-trip, so rendering `null` as `0` invents
  // a measurement. A bare dash with no label is the other half of the same
  // failure: a number nobody can interpret.
  assert.equal(latencyText(provider({ latencyMs: null })), "not reported");
  assert.doesNotMatch(latencyText(provider({ latencyMs: null })), /0(\.0)?\s*ms/);
  assert.doesNotMatch(latencyText(provider({ latencyMs: null })), /—|-/);
  assert.equal(latencyText(provider({ latencyMs: 812.53 })), "812.5 ms");
  // A genuinely measured zero is a real reading and keeps its value.
  assert.equal(latencyText(provider({ latencyMs: 0 })), "0.0 ms");
});

test("an unreported model count says so rather than counting the prefix that arrived", () => {
  // 2 IDs arrived and the server sent no total: that is unreported, not 2.
  const p = provider({ modelIds: ["a", "b"], modelCount: null });
  assert.equal(modelCountText(p), "models not reported");
  assert.doesNotMatch(modelCountText(p), /2 model/);
});

test("a reported model count is the provider's true total, singular or plural", () => {
  assert.equal(modelCountText(provider({ modelCount: 1 })), "1 model");
  assert.equal(modelCountText(provider({ modelCount: 61 })), "61 models");
  assert.equal(modelCountText(provider({ modelCount: 0 })), "0 models");
  // "1 models" is a count nobody typed.
  assert.doesNotMatch(modelCountText(provider({ modelCount: 1 })), /1 models/);
});

/* ══ A bounded prefix must announce itself ════════════════════════════════ */

test("a truncation the server flagged is disclosed with both counts", () => {
  // `catalog_dict()` sends at most 25 IDs per provider and sets
  // `models_truncated`. A silent 25-of-61 list presents itself as the whole
  // catalog, which is the exact reading this disclosure exists to stop.
  const shown = Array.from({ length: 25 }, (_, i) => `m/${i}`);
  const text = truncationText(
    provider({ modelIds: shown, modelCount: 61, modelsTruncated: true }),
  );
  assert.ok(text, "a flagged truncation must be visible");
  assert.match(text, /Showing 25 of 61/);
  assert.match(text, /bounded prefix/);
});

test("a truncation is disclosed even when the server forgot the flag", () => {
  // `models_truncated: false` with 2 IDs against a total of 40 is still a
  // partial list. Trusting the flag alone would let a mismatch hide it.
  const text = truncationText(
    provider({ modelIds: ["a", "b"], modelCount: 40, modelsTruncated: false }),
  );
  assert.match(text ?? "", /Showing 2 of 40/);
});

test("a complete list carries no truncation sentence", () => {
  assert.equal(
    truncationText(provider({ modelIds: ["a", "b"], modelCount: 2, modelsTruncated: false })),
    null,
  );
  // An unreported total with no models shown is not a truncation.
  assert.equal(truncationText(provider({ modelIds: [], modelCount: null })), null);
  // Exactly matching counts are complete, not a rounding artefact.
  assert.equal(truncationText(provider({ modelIds: ["a"], modelCount: 1 })), null);
});

test("a flagged truncation with an unreported total still discloses itself", () => {
  // The server cut the list but sent no count. "Of an unreported number" is
  // clumsy and honest; a silent partial list is neither.
  const text = truncationText(
    provider({ modelIds: ["a"], modelCount: null, modelsTruncated: true }),
  );
  assert.match(text ?? "", /Showing 1 of an unreported number/);
});

/* ══ The headline counts keep the states apart ════════════════════════════ */

test("the summary separates failing from unmeasured", () => {
  // `8/10 healthy` alone cannot say whether the other two failed or were simply
  // never probed — two completely different operator problems.
  const summary = freeCatalogSummary([
    provider({ healthy: true, eligible: true }),
    provider({ healthy: true, eligible: true }),
    provider({ healthy: true, eligible: true }),
    provider({ healthy: false }),
    provider({ healthy: false }),
    provider({ healthy: null }),
    provider({ healthy: null }),
    provider({ healthy: null }),
    provider({ healthy: null }),
    provider({ healthy: null }),
  ]);
  assert.deepEqual(summary, {
    healthy: 3,
    failing: 2,
    notProbed: 5,
    eligible: 3,
    total: 10,
  });
  // 3 + 2 + 5 === 10: no provider is counted twice, and none is dropped.
  assert.equal(summary.healthy + summary.failing + summary.notProbed, summary.total);
});

test("an empty or unread catalog summarises to zeroes, not a healthy pass", () => {
  assert.deepEqual(freeCatalogSummary([]), {
    healthy: 0,
    failing: 0,
    notProbed: 0,
    eligible: 0,
    total: 0,
  });
  // Every provider unmeasured is still `notProbed`, never `healthy`.
  const unmeasured = freeCatalogSummary([
    provider({ healthy: null }),
    provider({ healthy: null }),
  ]);
  assert.equal(unmeasured.healthy, 0);
  assert.equal(unmeasured.notProbed, 2);
});

test("eligibility is counted from the server's flag, never inferred from health", () => {
  // A healthy provider the server did not mark eligible is not eligible; the
  // router picks one model per provider, so these are separate questions.
  const summary = freeCatalogSummary([
    provider({ healthy: true, eligible: true }),
    provider({ healthy: true, eligible: false }),
    provider({ healthy: false, eligible: true }),
  ]);
  assert.equal(summary.eligible, 2);
  assert.equal(summary.healthy, 2);
});

test("an emptied healthy-only filter names the hidden providers as failing", () => {
  // "No healthy provider" is dangerously close to "no providers", which would
  // read as an empty catalog. The sentence must name the count and say they are
  // failing or unmeasured rather than absent.
  assert.match(emptyFilterText(2), /No healthy provider/);
  assert.match(emptyFilterText(2), /2 providers are failing or unmeasured/);
  assert.match(emptyFilterText(1), /1 provider is failing or unmeasured/);
  assert.match(emptyFilterText(2), /uncheck the filter/);
});

/* ══ Structural pins on the component that renders these ═════════════════ */

const menuSource = read("../components/FreeCatalogMenu.tsx");

test("the panel is portalled and repositioned, not clipped by an ancestor", () => {
  // The nav's own dropdown was a hostage to `overflow-x-auto` wrapping a
  // `<main class="overflow-hidden">`: 4 visible pixels of a 1060px menu. This one
  // renders into `document.body` and is placed against the real viewport by the
  // shared pure geometry helper.
  assert.match(menuSource, /createPortal\(/);
  assert.match(menuSource, /placeFloatingPanel\(/);
  assert.match(menuSource, /position: "fixed"/);
  // Repositioned on the events that move a fixed element out from under it.
  assert.match(menuSource, /addEventListener\("resize", placePanel\)/);
  assert.match(menuSource, /addEventListener\("scroll", placePanel, true\)/);
  // Escape and an outside click both dismiss, and neither unmounts the row
  // before its own click lands — hence both refs, not just the trigger's.
  assert.match(menuSource, /event\.key === "Escape"/);
  assert.match(
    menuSource,
    /triggerRef\.current\?\.contains\(target\) \|\| panelRef\.current\?\.contains\(target\)/,
  );
});

test("opening the list must not re-probe, and the probe is disabled in flight", () => {
  // Clicking to read the numbers used to re-probe every gateway: slow, and it
  // mutated the very health state it was reporting. The trigger now only opens,
  // and the probe moved inside where it can disable itself.
  assert.match(menuSource, /onClick=\{\(\) => \(open \? close\(\) : setOpen\(true\)\)\}/);
  assert.match(menuSource, /onClick=\{onRefresh\}\s*\n\s*disabled=\{refreshing\}/);
  assert.match(menuSource, /refreshing \? "Re-probing providers…" : "Re-probe live"/);
  // The trigger never calls the refresh itself.
  const trigger = menuSource.match(/<button[\s\S]{0,700}?Free keyless models[\s\S]{0,900}?<\/button>/);
  assert.ok(trigger, "expected the free-models trigger button");
  assert.doesNotMatch(trigger[0], /onRefresh/, "the trigger must not fire the probe");
  assert.match(trigger[0], /aria-label=/, "a 6px dot plus a truncated word needs a name");
  assert.match(trigger[0], /FREE_TONE_DOT\[tone\]/, "the dot must come from the measured tone");
});

test("an empty list and a failed read are rendered as the different claims they are", () => {
  // `providers: []` means the server reported no providers; a rejected read
  // means nobody answered. Conflating them turns a broken read into an empty
  // catalog — the failure this whole surface is guarded against.
  assert.match(menuSource, /The server reported no free providers/);
  assert.match(menuSource, /The catalog read failed, so no provider was measured/);
  assert.match(menuSource, /No provider list was returned, so nothing can be shown/);
  assert.match(menuSource, /error\s*\?\s*"The catalog read failed/);
});

test("the panel states that health is measured per provider, not per model", () => {
  // The load-bearing disclosure. A green provider with a list of IDs beneath it
  // could otherwise be read as "each of these was proven to answer", which is a
  // measurement the router never makes — it probes a gateway, not a model.
  assert.match(menuSource, /Health is measured per provider, not per model/);
  assert.match(menuSource, /not individually proven/);
});

test("no model row borrows the provider's colour", () => {
  // The model IDs render in one neutral chip class. Tinting them by the parent's
  // health is precisely the per-model claim that was never measured.
  const rows = menuSource.match(/key=\{id\}[\s\S]{0,300}?<\/li>/g) ?? [];
  assert.ok(rows.length > 0, "expected at least one model-ID row");
  for (const row of rows) {
    assert.doesNotMatch(row, /emerald|red-|amber/);
  }
});

test("an unreported eligibility is not rendered as 'not eligible'", () => {
  // Three states, not two: eligible, known-not-eligible, and undisclosed.
  assert.match(menuSource, /eligibility not reported/);
  assert.match(menuSource, /provider\.eligibilityKnown \?/);
});

test("the view helpers have exactly one definition", () => {
  // They moved to `lib/` so the tests can drive them. A copy left behind in the
  // component would be a second sentence for the same measurement, and the two
  // would drift.
  for (const name of ["healthLabel", "healthDot", "latencyText", "modelCountText", "truncationText"]) {
    assert.doesNotMatch(
      menuSource,
      new RegExp(`(?:function|const)\\s+${name}\\b`),
      `${name} must be imported from lib/freeCatalogView, not redefined here`,
    );
    assert.match(menuSource, new RegExp(`\\b${name}\\b`), `${name} should be used by the panel`);
  }
});