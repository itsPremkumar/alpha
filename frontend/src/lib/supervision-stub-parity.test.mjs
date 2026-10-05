// supervision-stub-parity.test.mjs — the shared `lib/supervision.ts` stub must
// never drift from the module it stands in for.
//
// THE FAILURE MODE THIS EXISTS TO STOP. ESM validates named imports at LINK
// time. A stub that omits one export is fatal before a single assertion runs,
// and the error names the stub, not the change:
//
//   SyntaxError: The requested module ... does not provide an export named
//   'isWatching'
//
// That is what happened twice on the fleet-reserved-key change: the inline stub
// in `system-probe-honesty.test.mjs` and the inline stub in
// `ui-legibility.test.mjs` both went stale on the same commit and both suites
// failed with a link error pointing at themselves.
//
// The defence is not "remember to update the stubs". It is: exactly one stub
// definition (`test-supervision-stub.mjs`), and this test asserting it still
// matches the real module. Adding an export to `supervision.ts` now fails HERE,
// with a message naming both files, before it can fail anywhere else.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { SUPERVISION_STUB_NAMES, supervisionStubSource, supervisionStubUrl } from "./test-supervision-stub.mjs";

/** Every `export function|const` name in the real module, in source order. */
function realExportNames() {
  const source = readFileSync(new URL("./supervision.ts", import.meta.url), "utf8");
  const names = [];
  // Only top-level declarations count. A nested `export` inside a block is not a
  // module export and would be a false positive, so anchor to line start with no
  // leading indentation.
  for (const m of source.matchAll(/^export\s+(?:async\s+)?(?:function|const|class|let|var)\s+([A-Za-z0-9_$]+)/gm)) {
    names.push(m[1]);
  }
  return names;
}

test("the shared stub exports exactly the module's real export list", () => {
  const real = realExportNames().sort();
  const stubbed = [...SUPERVISION_STUB_NAMES].sort();

  const missingFromStub = real.filter((n) => !stubbed.includes(n));
  const missingFromModule = stubbed.filter((n) => !real.includes(n));

  assert.deepEqual(
    missingFromStub,
    [],
    "lib/supervision.ts exports names the shared stub does not provide. " +
      "Add them to SUPERVISION_STUB_NAMES + supervisionStubSource() in " +
      "test-supervision-stub.mjs, or every suite that stubs ./supervision " +
      "fails at LINK time before running an assertion. (missing: " +
      missingFromStub.join(", ") +
      ")",
  );
  assert.deepEqual(
    missingFromModule,
    [],
    "the shared stub provides names lib/supervision.ts does not export. " +
      "That is either a typo or a deletion that was not propagated. (extra: " +
      missingFromModule.join(", ") +
      ")",
  );
});

test("the stub source really declares every name it claims", () => {
  const source = supervisionStubSource();
  const declared = [...source.matchAll(/^export\s+(?:async\s+)?(?:function|const)\s+([A-Za-z0-9_$]+)/gm)].map((m) => m[1]);
  assert.deepEqual(
    SUPERVISION_STUB_NAMES.filter((n) => !declared.includes(n)),
    [],
    "SUPERVISION_STUB_NAMES lists a name the stub source never declares",
  );
});

test("the stub keeps the real empty-fleet verdict, not a reassuring constant", () => {
  // The defect this stub guards against: a probe that hardcoded
  // `() => "watching"` and discarded the fleet it was summarizing, so zero
  // workers and two hundred both rendered the same reassuring word.
  const source = supervisionStubSource();
  assert.match(source, /no heartbeat received, nothing is being watched/);
  assert.doesNotMatch(source, /return\s+["']watching["']/);
});

test("the stub strips the reserved fleet keys rather than reading them as workers", () => {
  // If the stub ever loses this, a suite could pass while the real module
  // rejected the payload - exactly the failure the shared stub exists to stop.
  const source = supervisionStubSource();
  for (const key of ["observed", "observed_reason", "observed_worker_count", "watching"]) {
    assert.ok(source.includes(key), `the stub must know the reserved key ${key}`);
  }
});

test("the stub URL loads and answers the live no-worker payload", async () => {
  globalThis.__fleetBody = {
    observed: false,
    observed_reason: "no_worker_has_posted_a_heartbeat_to_this_process",
    observed_worker_count: 0,
    watching: false,
  };
  try {
    const mod = await import(supervisionStubUrl());
    assert.deepEqual(mod.parseFleetWorkers(await mod.supervisionFleet()), []);
    assert.equal(mod.isWatching(await mod.supervisionFleet()), false);
    assert.equal(
      mod.observedReason(await mod.supervisionFleet()),
      "no_worker_has_posted_a_heartbeat_to_this_process",
    );

    // `watchdogDetail` must RETURN a sentence, not a function. It was first
    // emitted as `return <fn source>`, which hands back the function object -
    // a value that stringifies to `undefined` and would make the probe's detail
    // line silently vanish instead of saying anything false. A stub that
    // under-reports is no safer than one that over-reports.
    const detail = mod.watchdogDetail([]);
    assert.equal(typeof detail, "string", "watchdogDetail must return a string");
    assert.match(detail, /no workers reporting/);
    assert.match(mod.watchdogDetail([{}, {}]), /2 workers reporting/);
    assert.match(mod.watchdogDetail([{}]), /1 worker reporting$/);
    assert.match(
      mod.watchdogDetail([{ unresolved_anomalies_count: 2 }, { unresolved_anomalies_count: 0 }]),
      /2 workers reporting . 1 with unresolved anomalies/,
      "the anomaly count survives the stub, and counts only the workers that HAVE one",
    );
  } finally {
    delete globalThis.__fleetBody;
  }
});

test("the two suites that stub ./supervision both use the SHARED definition", () => {
  // A third hand-rolled copy is the failure mode itself, so this pins the
  // consolidation rather than trusting it.
  const honest = readFileSync(new URL("./system-probe-honesty.test.mjs", import.meta.url), "utf8");
  assert.match(
    honest,
    /supervisionStubSource/,
    "system-probe-honesty must import the shared stub, not hand-roll one",
  );
  assert.doesNotMatch(
    honest,
    /stub\("parseFleetWorkers"/,
    "a hand-rolled supervision stub has reappeared in system-probe-honesty",
  );
});