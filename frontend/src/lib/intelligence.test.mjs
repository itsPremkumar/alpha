// intelligence.test.mjs — `lib/intelligence.ts` against the real Gateway
// contract for `GET /api/intelligence/control-plane`.
//
// The control plane exists to answer "Is Alpha actually becoming more capable?"
// without ever fabricating an answer, so this file pins three things:
//
//   1. The exact route and verb. The client is read-only: it imports `get`
//      only, and a source-level pin fails if a `send` or a mutating verb ever
//      appears — this surface must never grow a write.
//   2. The envelope mapping. Every section arrives as
//      `{available, reason, data}`; a section the Gateway omitted is disclosed
//      as unavailable with its own reason, never assumed readable.
//   3. The honesty inversions, each named after the wrong reading it blocks:
//      absent counts are `null` (never `0`), the *basis* decides the value
//      cell (a smuggled `0` on an `unowned` row renders as words), the
//      loop-health fallback's missing `scored_attempts` stays `null` (never
//      `0`), and an unrecognised regime/basis renders verbatim in grey (never
//      green, never snapped to a known value).
//
// The transport is stubbed, so these run with no Gateway.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./intelligence.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

/** Load the module against a stubbed HTTP layer driven by `handler`. */
function load(handler) {
  const calls = [];
  const exports = {};
  const http = {
    async get(path) {
      calls.push({ path, method: "GET" });
      return handler(path);
    },
    send() {
      throw new Error("the control-plane client must never send");
    },
  };
  new Function("exports", "require", compiled)(exports, (dependency) => {
    assert.equal(dependency, "./http", "the client must reach the network only through lib/http");
    return http;
  });
  return { intel: exports, calls };
}

/** A complete, well-formed payload shaped like the backend's own output. */
function fullPayload() {
  return {
    schema_version: "alpha.control-plane.v1",
    mode: { available: true, reason: "", data: { enabled: false, mode: "observe_only", permits: { observe_only: true, act: false } } },
    loop_health: {
      available: true,
      reason: "",
      data: {
        report: {
          regime: "stable",
          scored_attempts: 4,
          consecutive_non_improving: 0,
          gain_per_attempt_trend: 0.012,
          pathway_alignment: 1,
          action_space_coverage_delta: 0,
          measured_noise_floor: null,
          subsystems_agreeing: 6,
          subsystems_total: 6,
          subsystems_unreconciled: [],
          bottleneck: "",
          recommended_action: "",
          reasons: [],
          contributions: {},
        },
        required_subsystems: ["pathway", "loop_health"],
        ledger: { candidates: 1, records: 4, enabled_subsystems: ["pathway"] },
        observed_events: 3,
      },
    },
    ledger: { available: true, reason: "", data: { candidates: 1, records: 4, enabled_subsystems: ["pathway"] } },
    journal: {
      available: true,
      reason: "",
      data: { entries: [{ index: 1 }, { index: 0 }], corrupt_lines: 0, integrity: { ok: true, checked: 2, broken_at: null, reason: "" } },
    },
    capability_fabric: { available: true, reason: "", data: { active: 3, status_counts: { active: 3 }, note: "active counts non-terminal learned experts" } },
    replay: { available: true, reason: "", data: { size: 12, capacity: 100, utilisation: 0.12, strata: { task: 12 }, unknown_configured_strata: [], oldest_age_seconds: 60.5, recent_evictions: [], path: "/tmp/replay.json" } },
    goals: { available: true, reason: "", data: { tracked: 2, note: "counted from a freshly read store" } },
    metrics: [
      { name: "learning_enabled", value: false, unit: "boolean", basis: "measured", reason: "", source: "alpha.intelligence.config" },
      { name: "loop_scored_attempts", value: 4, unit: "count", basis: "measured", reason: "", source: "alpha.intelligence.loop_health" },
      { name: "goals_tracked", value: 2, unit: "count", basis: "measured", reason: "", source: "alpha.harness.continuous.store" },
      { name: "mission_success_rate", value: null, unit: "ratio", basis: "unowned", reason: "no subsystem aggregates mission outcomes into a rate", source: "alpha.mission" },
      { name: "cost_per_success", value: null, unit: "currency", basis: "unowned", reason: "cost is priced per model and success is judged per run", source: "alpha.models.pricing" },
      { name: "model_availability", value: null, unit: "ratio", basis: "unowned", reason: "models are declared in config, not probed", source: "alpha.models" },
    ],
    summary: { measured: 9, unmeasured: 0, unavailable: 0, unowned: 11, total: 20, sources_unavailable: [], health_state: "stable" },
  };
}

// ------------------------------------------------------------------- routing

test("the plane is read from one exact GET route", async () => {
  const { intel, calls } = load(() => fullPayload());
  await intel.fetchControlPlane();
  assert.deepEqual(calls, [{ path: "/intelligence/control-plane", method: "GET" }]);
});

test("the client is strictly read-only", () => {
  assert.doesNotMatch(source, /\bsend\s*[(<]/, "the client must not import `send`");
  assert.doesNotMatch(source, /method:\s*"(POST|PATCH|PUT|DELETE)"/);
  assert.doesNotMatch(source, /\b(create|update|delete|promote|rollback|evaluate|set|write)\w*\s*\(/);
});

test("the section and basis lists match the backend contract", () => {
  // These are the backend's `SOURCE_SECTIONS` and `VALID_BASIS`, pinned here
  // so a rename on either side fails a test instead of a panel's card grid.
  const { intel } = load(() => ({}));
  assert.deepEqual([...intel.SOURCE_SECTIONS], ["mode", "loop_health", "ledger", "journal", "capability_fabric", "replay", "goals"]);
  assert.deepEqual([...intel.METRIC_BASES], ["measured", "unmeasured", "unavailable", "unowned"]);
  for (const id of intel.SOURCE_SECTIONS) assert.ok(intel.SECTION_LABELS[id], `section ${id} has no label`);
});

test("the schema version this build renders is the backend's own", () => {
  // The panel discloses a mismatched schema instead of silently dropping the
  // fields it does not know, so the string has to be the server's literal.
  const { intel } = load(() => fullPayload());
  assert.equal(intel.CONTROL_PLANE_SCHEMA, "alpha.control-plane.v1");
  assert.equal(intel.toControlPlane(fullPayload()).schema_version, intel.CONTROL_PLANE_SCHEMA);
});

// ------------------------------------------------------------- the envelopes

test("every section of a complete payload maps with its data intact", async () => {
  const { intel } = load(() => fullPayload());
  const plane = await intel.fetchControlPlane();
  assert.equal(plane.schema_version, "alpha.control-plane.v1");
  for (const id of intel.SOURCE_SECTIONS) {
    assert.equal(plane[id].available, true, `section ${id} should be available`);
    assert.equal(plane[id].data === null, false, `section ${id} should carry data`);
  }
  assert.equal(plane.mode.data.mode, "observe_only");
  assert.equal(plane.loop_health.data.report.regime, "stable");
  assert.equal(plane.ledger.data.candidates, 1);
  assert.equal(plane.journal.data.integrity.ok, true);
  assert.equal(plane.journal.data.entry_count, 2, "the entry count is derived from the list that arrived");
  assert.equal(plane.capability_fabric.data.active, 3);
  assert.equal(plane.replay.data.size, 12);
  assert.equal(plane.goals.data.tracked, 2);
  assert.equal(plane.metrics.length, 6);
  assert.equal(plane.summary.total, 20);
});

test("an unavailable section keeps its reason and carries no data", () => {
  const { intel } = load(() => ({}));
  const plane = intel.toControlPlane({
    ...fullPayload(),
    goals: { available: false, reason: "RuntimeError: goal store degraded, not enumerated: goals.json is corrupt", data: { tracked: 99 } },
  });
  assert.equal(plane.goals.available, false);
  assert.match(plane.goals.reason, /goal store degraded/);
  assert.equal(plane.goals.data, null, "an unreadable source must not leak a stale figure");
});

test("a section the Gateway omitted is disclosed, never assumed available", () => {
  const { intel } = load(() => ({}));
  const payload = fullPayload();
  delete payload.journal;
  const plane = intel.toControlPlane(payload);
  assert.equal(plane.journal.available, false);
  assert.match(plane.journal.reason, /did not include this section/);
  assert.equal(intel.sectionDisclosure(plane.journal), plane.journal.reason);
});

test("sectionDisclosure names every way a card can show nothing", () => {
  const { intel } = load(() => ({}));
  assert.equal(intel.sectionDisclosure({ available: true, reason: "", data: { ok: 1 } }), null, "a readable section has no disclosure");
  assert.equal(
    intel.sectionDisclosure({ available: false, reason: "OSError: disk gone", data: null }),
    "OSError: disk gone",
    "the server's own reason is the sentence",
  );
  assert.equal(
    intel.sectionDisclosure({ available: false, reason: "", data: null }),
    "the Gateway reported this source unavailable without stating a reason",
    "an unavailable source with no reason must still say something",
  );
  assert.equal(
    intel.sectionDisclosure({ available: true, reason: "", data: null }),
    "the Gateway reported this source available but sent no data",
    "available-but-empty is a third state, not a blank card",
  );
});

// -------------------------------------------------------- the honesty pins

test("summary counts the Gateway did not send are null, never zero", () => {
  const { intel } = load(() => ({}));
  const summary = intel.toControlPlane({ schema_version: "alpha.control-plane.v1" }).summary;
  assert.equal(summary.measured, null);
  assert.equal(summary.unmeasured, null);
  assert.equal(summary.unavailable, null);
  assert.equal(summary.unowned, null);
  assert.equal(summary.total, null);
  assert.equal(summary.sources_unavailable, null, "an unreported source list is null, not []");
  assert.equal(summary.health_state, null);
  assert.match(intel.summaryCountsLine(summary), /measured count not reported/, "the line must say the counts never arrived");
});

test("a summary count the Gateway really measured as zero survives as zero", () => {
  const { intel } = load(() => ({}));
  const summary = intel.toControlPlane(fullPayload()).summary;
  assert.equal(summary.unmeasured, 0, "a real measured zero must not become null");
  assert.equal(summary.measured, 9);
  assert.match(intel.summaryCountsLine(summary), /^9 measured · 0 unmeasured/);
});

test("metrics absent from the payload are null, distinct from an empty list", () => {
  const { intel } = load(() => ({}));
  const payload = fullPayload();
  delete payload.metrics;
  assert.equal(intel.toControlPlane(payload).metrics, null, "no list sent must not read as 'no metrics exist'");
  assert.equal(intel.toControlPlane({ ...fullPayload(), metrics: [] }).metrics.length, 0, "an empty list is the server's real answer");
});

test("the basis decides the value cell, not the number", () => {
  const { intel } = load(() => ({}));
  // The smuggling case: an `unowned` row arrives carrying a raw 0 — the view
  // must render the ownership words, never the figure.
  const smuggled = { name: "mission_success_rate", value: 0, unit: "ratio", basis: "unowned", reason: "no owner", source: "alpha.mission" };
  assert.equal(intel.metricValueText(smuggled), "no aggregate owner");
  assert.doesNotMatch(intel.metricValueText(smuggled), /^0$/, "a smuggled zero rendered as a measured figure");

  // A real measured zero is still a zero — the opposite failure.
  const measuredZero = { name: "ledger_candidates", value: 0, unit: "count", basis: "measured", reason: "", source: "alpha.intelligence.evidence_ledger" };
  assert.equal(intel.metricValueText(measuredZero), "0");

  // Measured but the value did not arrive: not reported, never 0.
  assert.equal(intel.metricValueText({ ...measuredZero, value: null }), "not reported");

  // Booleans are measurements too: false is an answer, not an absence.
  assert.equal(intel.metricValueText({ name: "learning_enabled", value: false, unit: "boolean", basis: "measured", reason: "", source: "x" }), "false");

  // An unknown basis from a newer Gateway never renders its number.
  const unknown = { name: "novelty", value: 0.42, unit: "ratio", basis: "estimated", reason: "", source: "alpha.x" };
  assert.equal(intel.metricValueText(unknown), "value not shown");

  // A missing basis says so.
  assert.equal(intel.metricValueText({ name: "x", value: 1, unit: "count", basis: "", reason: "", source: "x" }), "basis not reported");
});

test("basis labels stay verbatim and tones stay epistemic", () => {
  const { intel } = load(() => ({}));
  assert.equal(intel.metricBasisLabel("measured"), "measured");
  assert.equal(intel.metricBasisLabel("estimated"), "estimated", "an unknown basis is shown as itself");
  assert.equal(intel.metricBasisLabel(""), "basis not reported");
  assert.equal(intel.metricBasisTone("measured"), "blue", "measured means evidence, not health — not green");
  assert.equal(intel.metricBasisTone("unavailable"), "red", "a source that could not be read is the alarm");
  assert.equal(intel.metricBasisTone("unowned"), "amber");
  assert.equal(intel.metricBasisTone("unmeasured"), "gray");
  assert.equal(intel.metricBasisTone("estimated"), "gray", "an unknown basis gets no verdict colour");
});

test("the loop-health fallback's missing scored_attempts stays null, never zero", () => {
  const { intel } = load(() => ({}));
  // The backend's config-failure fallback: regime + reason, and deliberately
  // no scored_attempts, because no assessment ran.
  const plane = intel.toControlPlane({
    ...fullPayload(),
    loop_health: {
      available: true,
      reason: "",
      data: {
        report: { regime: "insufficient_data", reason: "loop health could not be computed: ValueError: bad config" },
        required_subsystems: [],
        ledger: {},
        observed_events: 0,
      },
    },
  });
  const report = plane.loop_health.data.report;
  assert.equal(report.regime, "insufficient_data");
  assert.equal(report.scored_attempts, null, "'no assessment ran' must not become a measured 0 attempts");
  assert.match(report.reason, /could not be computed/);
  assert.equal(intel.healthStateTone(report.regime), "gray", "insufficient_data is never green");
});

test("health states map to tones without ever guessing", () => {
  const { intel } = load(() => ({}));
  assert.equal(intel.healthStateTone("improving"), "green");
  assert.equal(intel.healthStateTone("stable"), "green");
  assert.equal(intel.healthStateTone("saturating"), "amber");
  assert.equal(intel.healthStateTone("regressing"), "red");
  assert.equal(intel.healthStateTone("insufficient_data"), "gray");
  assert.equal(intel.healthStateTone(null), "gray", "an unreported state is not a healthy one");
  assert.equal(intel.healthStateTone("quantum"), "gray", "a regime from a newer Gateway gets no colour verdict");
  assert.equal(intel.healthStateText(null), "health state not reported");
  assert.equal(intel.healthStateText("quantum"), "quantum", "and renders verbatim");
});

test("the unavailable-sources line appears only when there is something to say", () => {
  const { intel } = load(() => ({}));
  assert.equal(intel.unavailableSourcesLine(null), null, "an unreported list is announced by the cards themselves");
  assert.equal(intel.unavailableSourcesLine([]), null, "nothing was unavailable");
  assert.equal(
    intel.unavailableSourcesLine(["goals"]),
    "1 source could not be read: goals. Each card below carries its own reason.",
  );
  assert.match(intel.unavailableSourcesLine(["journal", "goals"]), /2 sources could not be read: journal, goals/);
});

test("an absent summary field never fabricates a section count", () => {
  const { intel } = load(() => ({}));
  const summary = intel.toControlPlane({ summary: { measured: 5 } }).summary;
  assert.equal(summary.measured, 5, "what the server did send survives");
  assert.equal(summary.total, null, "what it did not send stays null");
  assert.equal(summary.health_state, null);
  assert.match(intel.summaryCountsLine(summary), /total not reported/);
});
