// test-supervision-stub.mjs — ONE inline stub for `lib/supervision.ts`.
//
// WHY THIS FILE EXISTS. Three separate suites each hand-wrote their own inline
// copy of this module's exports: `agent-status.test.mjs` (imports the REAL
// module, so it never needed one), `system-probe-honesty.test.mjs` and
// `ui-legibility.test.mjs`. When `parseFleetWorkers` was taught about the
// server's reserved `observed` / `watching` keys, BOTH stubs went stale on the
// same commit and both suites died with:
//
//   SyntaxError: The requested module ... does not provide an export named
//   'isWatching'
//
// which is a link error in a file that has nothing to do with the change, in a
// suite that has not even started running. That is the most expensive kind of
// test failure: it points at the test, not at the defect.
//
// ESM validates named imports at link time, so a stub that omits an export is
// fatal regardless of whether the code under test ever calls it. The only
// durable answer is a single definition that must be updated when the module's
// exports change - so the next person adding an export edits this file once and
// finds out immediately, in one place.
//
// `SUPERVISION_STUB_NAMES` is the tripwire: `supervision-stub-parity.test.mjs`
// fails if this stub and the real module ever disagree about the export list.

const NL = "\n";

/** Every export `./supervision` must provide for a stub to link. */
export const SUPERVISION_STUB_NAMES = [
  "supervisionFleet",
  "supervisionAnomalies",
  "recoverWorker",
  "adoptOrphans",
  "parseFleetWorkers",
  "fetchFleetWorkers",
  "watchdogDetail",
  "fetchAnomaliesStrict",
  "observedReason",
  "isWatching",
];

/**
 * `watchdogDetail` keeps the real semantics rather than a constant, so a suite
 * driving it cannot pass for the wrong reason (the defect it replaced: a probe
 * that hardcoded `() => "watching"` and discarded the value it summarized).
 */
function realWatchdogDetail(workers) {
  const n = Array.isArray(workers) ? workers.length : 0;
  if (n === 0) return "no workers reporting — no heartbeat received, nothing is being watched";
  const anomalies = (Array.isArray(workers) ? workers : []).filter(
    (w) => w && w.unresolved_anomalies_count !== null && w.unresolved_anomalies_count !== undefined && w.unresolved_anomalies_count > 0,
  ).length;
  const base = `${n} ${n === 1 ? "worker" : "workers"} reporting`;
  return anomalies > 0 ? `${base} · ${anomalies} with unresolved anomalies` : base;
}

/**
 * The stub module source.
 *
 * Reads its fleet body from `globalThis.__fleetBody` so a suite can exercise
 * the real `observed` / `observed_reason` / `watching` contract against the
 * exact payload the Gateway sends (captured in
 * `system-probe-honesty.test.mjs`). Unset, it behaves as "zero workers, not
 * watching", which is the default install.
 */
export function supervisionStubSource() {
  return [
    `const body = () => globalThis.__fleetBody || { observed: false, observed_reason: "no_worker_has_posted_a_heartbeat_to_this_process", observed_worker_count: 0, watching: false };`,
    `export function supervisionFleet() { return Promise.resolve(body()); }`,
    `export function supervisionAnomalies() { return Promise.resolve([]); }`,
    `export function recoverWorker() { return Promise.resolve(""); }`,
    `export function adoptOrphans() { return Promise.resolve(""); }`,
    // Real semantics: strips the reserved keys, throws on a genuinely unreadable
    // body, and keeps an empty map an empty fleet.
    `export function parseFleetWorkers(b) {`,
    `  if (Array.isArray(b)) return b;`,
    `  if (b && typeof b === "object") {`,
    `    const nested = [b.workers, b.data].find((v) => Array.isArray(v));`,
    `    if (Array.isArray(nested)) return nested;`,
    `    const entries = Object.entries(b).filter(([k]) => ["observed","observed_reason","observed_worker_count","watching"].indexOf(k) === -1);`,
    `    if (entries.length === 0) return [];`,
    `    if (entries.every(([, v]) => v && typeof v === "object" && !Array.isArray(v))) return entries.map(([k, v]) => Object.assign({ worker_id: k }, v));`,
    `  }`,
    `  throw new Error("The server returned an unreadable fleet payload.");`,
    `}`,
    `export function fetchFleetWorkers() { return Promise.resolve(parseFleetWorkers(body())); }`,
    // NOTE: `realWatchdogDetail.toString()` inserted after a bare `return`
    // would make `watchdogDetail` RETURN THE FUNCTION rather than call it -
    // `watchdogDetail([])` would then be a function object, which stringifies to
    // `undefined`, so the probe's detail line silently became a function. It is
    // emitted as an IIFE so the stub actually evaluates the real semantics.
    `export function watchdogDetail(workers) { return (${realWatchdogDetail.toString()})(workers); }`,
    `export function fetchAnomaliesStrict() { return Promise.resolve([]); }`,
    `export function observedReason(b) { const r = b && typeof b === "object" ? b.observed_reason : null; return typeof r === "string" && r !== "" ? r : null; }`,
    `export function isWatching(b) { const w = b && typeof b === "object" ? b.watching : null; return typeof w === "boolean" ? w : null; }`,
  ].join(NL);
}

/** The stub as a `data:` URL, ready to substitute for `./supervision`. */
export function supervisionStubUrl() {
  return `data:text/javascript;charset=utf-8,${encodeURIComponent(supervisionStubSource())}`;
}