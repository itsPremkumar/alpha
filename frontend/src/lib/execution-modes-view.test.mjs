// execution-modes-view.test.mjs - which execution modes exist, where each is
// used, and above all whether its value is MEASURED or merely DOCUMENTED.
//
// Why this suite exists
// ---------------------
// The operator could not find Arena or the dynamic workflow because nothing
// listed them. The obvious fix - a panel printing every mode and its numbers -
// is exactly where this codebase's honesty rule bites, because most of those
// numbers are NOT readable from the Gateway.
//
// Verified while building this: the Gateway has no PUT/PATCH for subagent
// settings, arena config or global orchestration mode, and no GET route for
// council strategies, execution tiers, or the effective subagent ceilings.
// `lib/subagent-capacity.ts` is pure arithmetic with no route behind it.
//
// So the load-bearing assertion in this file is the negative one: a family the
// Gateway cannot report must NOT gain a number. A panel that printed
// "max_total_per_run: 6" would be printing what this build's source knows while
// looking like a reading of the running system.
//
// Arena is the exception and is genuinely live - `GET /arena/config` and
// `GET /arena/profiles` both answer - so those rows are pinned to the real
// payload shape, including the nulls a partial read must produce.
//
// Pure Node (node --test src/lib/*.test.mjs): no server, no DOM, no renderer.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const toDataUrl = (source) =>
  `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;

const source = readFileSync(new URL("./execution-modes-view.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;

// `arena` is imported for TYPES only, so the transpile elides it. The stub is a
// guard: a real value import would resolve against a nonexistent path and fail
// loudly rather than silently passing.
const stubUrl = toDataUrl(`export const unusedStub = null;`);
const { buildModeFamilies, liveFamilyCount, summarySentence } = await import(
  toDataUrl(code.replace(/from\s+"\.\/arena"/, `from "${stubUrl}"`))
);

const byId = (families, id) => families.find((f) => f.id === id);
const fact = (family, label) => family.facts.find((f) => f.label === label);

/** The ceilings exactly as the live Gateway answered them. */
const liveConfig = {
  enabled: true,
  defaultAgents: 4,
  maxAgents: 64,
  defaultWave: 4,
  maxWave: 32,
  estimateTokensPerCall: 60000,
  confirmationOverCalls: 100,
  maxCallsPerRun: 1000,
  maxTokensPerRun: 50000000,
  maxWallSecondsPerRun: 7200,
  maxJudgeAttempts: 2,
  reasoningBankSeed: true,
};

const liveCatalogue = {
  profiles: [
    { name: "quick", agents: 3, wave: 3, description: "Smoke competition." },
    { name: "standard", agents: 5, wave: 4, description: "Default." },
  ],
  modes: [
    { mode: "decide", description: "Competitors answer, a judge picks.", implemented: true },
    { mode: "plan", description: "Project the call count first.", implemented: true },
  ],
};

// ---------------------------------------------------------------------------
// The catalogue answers "what exists, and where do I go"
// ---------------------------------------------------------------------------

test("the families the operator asked about are all listed", () => {
  const ids = buildModeFamilies({ config: null, catalogue: null }).map((f) => f.id);
  for (const id of ["arena", "council", "deliberation", "workflow-tier", "orchestration", "delegation"]) {
    assert.ok(ids.includes(id), `missing family: ${id}`);
  }
});

test("every family names the view that owns it and what to do there", () => {
  // A catalogue row that only names a mode is a list, not a way to find it.
  for (const f of buildModeFamilies({ config: null, catalogue: null })) {
    assert.ok(f.view.trim().length > 0, `${f.id} names no view`);
    assert.ok(f.action.trim().length > 0, `${f.id} offers no action`);
    assert.ok(f.blurb.trim().length > 0, `${f.id} has no blurb`);
  }
});

// ---------------------------------------------------------------------------
// The load-bearing negative: no invented numbers
// ---------------------------------------------------------------------------

test("a family with no readable value carries no facts at all", () => {
  const families = buildModeFamilies({ config: null, catalogue: null });
  for (const f of families.filter((x) => x.readiness !== "live")) {
    assert.deepEqual(f.facts, [], `${f.id} invented facts it cannot read`);
  }
});

test("an unreadable family says so instead of showing a number", () => {
  const families = buildModeFamilies({ config: null, catalogue: null });
  for (const f of families.filter((x) => x.readiness === "not-exposed")) {
    // The property is that the absence is explained, not that the sentence
    // contains a particular word.
    assert.ok(f.readinessNote.trim().length > 0, `${f.id} gives no reason for being unreadable`);
    // It must not smuggle a measured-looking figure into the explanation.
    assert.ok(!/\b\d/.test(f.readinessNote), `${f.id} states a number it cannot read: ${f.readinessNote}`);
  }
});

test("a family badged live must actually show something measured", () => {
  // A green "Live from Gateway" chip above zero facts is a claim with nothing
  // behind it. Staged deliberation is a fully working view that this catalogue
  // simply has no read for, so it must not be badged live.
  for (const f of buildModeFamilies({ config: null, catalogue: null })) {
    if (f.readiness !== "live") continue;
    assert.ok(f.facts.length > 0, `${f.id} is badged live but reports nothing`);
  }
});

test("staged deliberation is reachable but not claimed as measured", () => {
  const d = byId(buildModeFamilies({ config: null, catalogue: null }), "deliberation");
  assert.equal(d.readiness, "not-exposed");
  assert.equal(d.view, "deliberation");
  assert.deepEqual(d.facts, []);
});

test("the subagent ceilings are never stated as measured values", () => {
  // `config.example.yaml` does set max_total_per_run=6 and max_running=3, but
  // no route reads them, so this panel must not print them as if it had.
  const families = buildModeFamilies({ config: null, catalogue: null });
  const delegation = byId(families, "delegation");
  assert.equal(delegation.readiness, "not-exposed");
  const serialised = JSON.stringify(delegation);
  assert.ok(!/max_total_per_run|max_running|\b6 total\b/.test(serialised), serialised);
});

test("the council strategy members are not listed as though read from the Gateway", () => {
  // They are a client-side union in `lib/deliberation.ts`; naming them here
  // would present a source constant as a server reading.
  const council = byId(buildModeFamilies({ config: null, catalogue: null }), "council");
  assert.equal(council.readiness, "not-exposed");
  const text = JSON.stringify(council);
  for (const strategy of ["ensemble", "round_robin", "debate"]) {
    assert.ok(!text.includes(`"${strategy}"`), `council listed ${strategy} as if measured`);
  }
});

test("the execution tier is marked auto-routed, never offered as a control", () => {
  const tier = byId(buildModeFamilies({ config: null, catalogue: null }), "workflow-tier");
  // It must not read as "live", or the panel would imply a current value.
  assert.equal(tier.readiness, "auto-routed");
  assert.deepEqual(tier.facts, []);
});

// ---------------------------------------------------------------------------
// Arena is genuinely live, and renders what the Gateway answered
// ---------------------------------------------------------------------------

test("arena reports live once a read answered, with the measured ceilings", () => {
  const arena = byId(buildModeFamilies({ config: liveConfig, catalogue: liveCatalogue }), "arena");
  assert.equal(arena.readiness, "live");
  assert.equal(arena.readinessNote, "");
  assert.equal(fact(arena, "Default competitors").value, 4);
  assert.equal(fact(arena, "Maximum competitors").value, 64);
  assert.equal(fact(arena, "Confirmation required above calls").value, 100);
  assert.equal(fact(arena, "Estimated tokens per call").value, 60000);
});

test("arena lists each profile and mode the Gateway reported", () => {
  const arena = byId(buildModeFamilies({ config: liveConfig, catalogue: liveCatalogue }), "arena");
  assert.equal(fact(arena, "Profile · standard").value, "5 competitors, wave 4");
  assert.equal(fact(arena, "Mode · plan").value, "implemented");
});

test("a mode the Gateway does not implement is reported, not snapped to a known one", () => {
  const catalogue = {
    profiles: [],
    modes: [{ mode: "quantum", description: null, implemented: false }],
  };
  const arena = byId(buildModeFamilies({ config: liveConfig, catalogue }), "arena");
  // Offering an unimplemented mode as if it ran would invent capability.
  assert.equal(fact(arena, "Mode · quantum").value, "not implemented");
});

test("an absent implementation list is not the same as none implemented", () => {
  const catalogue = { profiles: [], modes: [{ mode: "decide", description: null, implemented: null }] };
  const arena = byId(buildModeFamilies({ config: liveConfig, catalogue }), "arena");
  assert.equal(fact(arena, "Mode · decide").value, "implementation not reported");
});

test("a ceiling the Gateway did not send is null, never zero", () => {
  // 0 would read as "you may run zero competitors", which is an instruction,
  // not an absence.
  const partial = { ...liveConfig, maxAgents: null, confirmationOverCalls: null };
  const arena = byId(buildModeFamilies({ config: partial, catalogue: null }), "arena");
  assert.equal(fact(arena, "Maximum competitors").value, null);
  assert.equal(fact(arena, "Confirmation required above calls").value, null);
  assert.notEqual(fact(arena, "Maximum competitors").value, 0);
});

test("a failed arena read is disclosed and does not render as a live empty block", () => {
  const arena = byId(
    buildModeFamilies({ config: null, catalogue: null, arenaError: "503 backend unavailable" }),
    "arena",
  );
  // "No Arena configured" and "the read failed" are opposite answers.
  assert.equal(arena.readiness, "not-exposed");
  assert.match(arena.readinessNote, /503 backend unavailable/);
});

test("a partial arena read keeps the half it got", () => {
  const arena = byId(buildModeFamilies({ config: liveConfig, catalogue: null }), "arena");
  assert.equal(fact(arena, "Maximum competitors").value, 64);
  assert.equal(fact(arena, "Profile · standard"), undefined);
});

// ---------------------------------------------------------------------------
// The summary makes a measured claim
// ---------------------------------------------------------------------------

test("the summary counts what is actually measured", () => {
  const families = buildModeFamilies({ config: liveConfig, catalogue: liveCatalogue });
  // Arena is the only family this catalogue measures, so one - not two.
  assert.equal(liveFamilyCount(families), 1);
  const sentence = summarySentence(families);
  assert.match(sentence, /1 of 6/);
  assert.match(sentence, /measured/);
});

test("with no reads at all nothing is claimed to be measured", () => {
  const families = buildModeFamilies({ config: null, catalogue: null });
  // Arena is the only family this catalogue can measure at all. Reporting 0
  // here is the honest answer: "nothing measured", not "nothing exists".
  assert.equal(liveFamilyCount(families), 0);
  assert.match(summarySentence(families), /0 of 6/);
  assert.match(summarySentence(families), /measured/);
});