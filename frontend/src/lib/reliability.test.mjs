// reliability.test.mjs — the real-work validation client and its headline.
//
// This surface answers the one question the whole reliability campaign exists
// to settle: did the agent actually DO the work, or did it return a success and
// a paragraph of prose? The failure modes that matter are all ways that
// question can be answered wrongly:
//
//   * an unreadable ledger rendered as "0 workloads, nothing broken" — a failed
//     read presented as a clean bill of health;
//   * a verdict word this build has never seen snapped to PASS (the exact
//     dishonesty the monitor's own evidence checks were written to prevent,
//     reintroduced one layer up);
//   * an unknown verdict assumed to be fine because it is not in a failure list;
//   * "no workloads yet" rendered as a passing matrix.
//
// The transport is stubbed so the real path and the real envelope mapping are
// pinned without a Gateway. `verdictTone` and `matrixHeadline` are the real
// exported functions driven with the payloads the Gateway actually returns.
//
// node --test src/lib/*.test.mjs — no server, no browser, no DOM shim.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const transpile = (src) =>
  ts.transpileModule(src, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
const dataUrl = (code) => `data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`;
const load = (code) => import(`data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`);

/* ── The transport stub ──────────────────────────────────────────────────── */

const calls = [];
const STUB_URL = dataUrl(`
  let handler = () => { throw new Error("no stub configured"); };
  export function setHttpHandler(fn) { handler = fn; }
  export async function get(path) { return handler(path, "GET", undefined); }
  export class ApiError extends Error {}
`);
const code = transpile(readFileSync(new URL("./reliability.ts", import.meta.url), "utf8")).replace(
  /from\s+"\.\/http"/,
  `from "${STUB_URL}"`,
);
const { toReliability, fetchReliability, verdictTone, matrixHeadline } = await load(code);
const { setHttpHandler } = await import(STUB_URL);

/**
 * Record every call and answer with `answer`.
 *
 * The handler ignores the stub's own arguments and closes over `answer`
 * instead, so naming them here would shadow this function's parameters and
 * turn `serve(new Error(...))` into a resolution — the "a failed read rejects"
 * test would then pass for the wrong reason.
 */
function serve(answer) {
  calls.length = 0;
  setHttpHandler((path, method) => {
    calls.push({ path, method });
    if (answer instanceof Error) throw answer;
    return answer;
  });
}

/* ── Fixtures: the exact payloads the Gateway returns ────────────────────── */

const HEALTHY = {
  reported: true,
  reason: "reported",
  ledger_path: "C:/alpha/backend/.alpha/reliability/workload-ledger.json",
  total: 3,
  passed: 2,
  broken: 1,
  counts: { PASS: 2, FAIL: 1 },
  returned: 3,
  truncated: false,
  workloads: [
    {
      workload: "A",
      title: "Coding: find a real bug, fix it, prove it",
      kind: "coding",
      verdict: "PASS",
      detail: "terminal status success with no error",
      run_id: "abcdef1234567890",
      thread_id: "t-1",
      elapsed_s: 91.4,
      model: "space-bunny-free",
      server_error: null,
      checked_at: "2026-10-04T07:00:00Z",
    },
    {
      workload: "C",
      title: "Research with citations",
      kind: "research",
      verdict: "FAIL",
      detail: "NO citations found in a 240-char answer",
      run_id: "c0ffee0000000000",
      thread_id: "t-2",
      elapsed_s: 42,
      model: "space-bunny-free",
      server_error: null,
      checked_at: "2026-10-04T06:59:00Z",
    },
  ],
};

const UNREADABLE = {
  reported: false,
  reason: "ledger_unreadable",
  ledger_path: "C:/alpha/backend/.alpha/reliability/workload-ledger.json",
  total: null,
  passed: null,
  broken: null,
  counts: {},
  returned: 0,
  truncated: false,
  workloads: [],
};

/* ── Route and verb ──────────────────────────────────────────────────────── */

test("reads the exact operator route with GET", async () => {
  serve(HEALTHY);
  await fetchReliability();
  assert.deepEqual(calls, [{ path: "/ops/reliability", method: "GET" }]);
});

test("a failed read rejects instead of resolving to an empty matrix", async () => {
  serve(new Error("gateway down"));
  await assert.rejects(fetchReliability(), /gateway down/);
});

/* ── Envelope mapping ────────────────────────────────────────────────────── */

test("maps the envelope verbatim and keeps every optional absence null", () => {
  const matrix = toReliability(HEALTHY);
  assert.equal(matrix.reported, true);
  assert.equal(matrix.total, 3);
  assert.equal(matrix.passed, 2);
  assert.equal(matrix.broken, 1);
  assert.equal(matrix.truncated, false);
  assert.equal(matrix.returned, 3);
  assert.deepEqual(matrix.counts, { PASS: 2, FAIL: 1 });
  const [a, c] = matrix.workloads;
  assert.equal(a.key, "A");
  assert.equal(a.kind, "coding");
  assert.equal(a.runId, "abcdef1234567890");
  assert.equal(a.elapsedSeconds, 91.4);
  assert.equal(a.model, "space-bunny-free");
  assert.equal(a.serverError, null, "an absent server error is null, not an empty string");
  assert.equal(c.verdict, "FAIL");
});

test("an unreadable ledger keeps its counts null rather than becoming zero", () => {
  const matrix = toReliability(UNREADABLE);
  assert.equal(matrix.reported, false);
  assert.equal(matrix.reason, "ledger_unreadable");
  assert.equal(matrix.total, null);
  assert.equal(matrix.passed, null);
  assert.equal(matrix.broken, null);
  assert.match(matrix.reasonText, /failed read, not an empty result/);
});

test("a missing ledger says nothing has been measured, not that all is well", () => {
  const matrix = toReliability({ ...UNREADABLE, reason: "ledger_not_found" });
  assert.equal(matrix.reason, "ledger_not_found");
  assert.match(matrix.reasonText, /NOT a passing matrix/);
});

test("a ledger path is carried so a missing file is diagnosable from the payload", () => {
  assert.match(toReliability(UNREADABLE).ledgerPath, /workload-ledger\.json$/);
});

test("an unfamiliar reason is disclosed with the server's own word", () => {
  const matrix = toReliability({ ...UNREADABLE, reason: "ledger_locked_by_monitor" });
  assert.match(matrix.reasonText, /ledger_locked_by_monitor/);
});

test("garbage counts are dropped rather than coerced to zero", () => {
  const matrix = toReliability({ ...HEALTHY, counts: { PASS: "two", FAIL: 1, ERROR: null } });
  assert.deepEqual(matrix.counts, { FAIL: 1 });
});

test("an unknown verdict word survives the client verbatim", () => {
  const matrix = toReliability({
    ...HEALTHY,
    workloads: [{ workload: "Z", verdict: "DEGRADED_BUT_RUNNING", checked_at: "2026-10-04T07:00:00Z" }],
  });
  assert.equal(matrix.workloads[0].verdict, "DEGRADED_BUT_RUNNING");
});

test("a row with no key still has a visible identity", () => {
  const matrix = toReliability({ ...HEALTHY, workloads: [{ verdict: "PASS" }] });
  assert.equal(matrix.workloads[0].key, "row 1");
});

test("wrongly typed fields become null instead of being coerced", () => {
  const matrix = toReliability({
    ...HEALTHY,
    workloads: [{ workload: "A", verdict: "PASS", elapsed_s: "fast", run_id: 7, title: 3 }],
  });
  const row = matrix.workloads[0];
  assert.equal(row.elapsedSeconds, null);
  assert.equal(row.runId, null);
  assert.equal(row.title, null);
});

test("a missing verdict is UNKNOWN rather than an invented PASS", () => {
  const matrix = toReliability({ ...HEALTHY, workloads: [{ workload: "A" }] });
  assert.equal(matrix.workloads[0].verdict, "UNKNOWN");
});

test("a non-array workloads field yields no rows rather than throwing", () => {
  assert.deepEqual(toReliability({ ...HEALTHY, workloads: "nope" }).workloads, []);
});

/* ── Verdict tones: an unknown word is never assumed to be fine ──────────── */

test("known verdicts map to their own tones", () => {
  assert.deepEqual(verdictTone("PASS"), { tone: "green", label: "PASS", broken: false });
  assert.deepEqual(verdictTone("FAIL"), { tone: "red", label: "FAIL", broken: true });
  assert.deepEqual(verdictTone("ERROR"), { tone: "red", label: "ERROR", broken: true });
  assert.deepEqual(verdictTone("UNVERIFIED"), { tone: "amber", label: "UNVERIFIED", broken: true });
  assert.deepEqual(verdictTone("SKIP"), { tone: "gray", label: "SKIP", broken: false });
});

test("an unrecognised verdict is amber AND treated as needing attention", () => {
  // The critical inversion: absent from the failure list does NOT mean fine.
  const tone = verdictTone("SOMETHING_NEW");
  assert.equal(tone.broken, true);
  assert.equal(tone.tone, "amber");
  assert.equal(tone.label, "SOMETHING_NEW", "shown as written, not mapped onto a known bucket");
});

test("an empty verdict word does not render as a blank badge", () => {
  assert.equal(verdictTone("").label, "UNKNOWN");
  assert.equal(verdictTone("").broken, true);
});

/* ── The headline must never read as healthy on an unread matrix ─────────── */

test("an unreadable ledger headlines as not reported, never as a pass", () => {
  const headline = matrixHeadline(toReliability(UNREADABLE));
  assert.equal(headline.value, "not reported");
  assert.equal(headline.tone, "gray");
  assert.doesNotMatch(headline.value, /passed/);
});

test("a readable but empty ledger says no workloads yet, not 0/0 passed", () => {
  const headline = matrixHeadline(toReliability({ reported: true, reason: "reported", total: 0, passed: 0, broken: 0, counts: {}, returned: 0, truncated: false, workloads: [] }));
  assert.equal(headline.value, "no workloads yet");
  assert.equal(headline.tone, "gray");
  assert.doesNotMatch(headline.value, /passed/);
});

test("an all-passing matrix is green and says so plainly", () => {
  const headline = matrixHeadline(toReliability({ ...HEALTHY, total: 2, passed: 2, broken: 0, counts: { PASS: 2 } }));
  assert.equal(headline.value, "2/2 passed");
  assert.equal(headline.tone, "green");
});

test("a partially passing matrix is amber and names the break count", () => {
  const headline = matrixHeadline(toReliability(HEALTHY));
  assert.equal(headline.value, "2/3 passed");
  assert.equal(headline.tone, "amber");
  assert.match(headline.detail, /1 of 3 recorded workloads did not pass/);
});

test("a matrix where nothing passed is red, not amber", () => {
  const headline = matrixHeadline(toReliability({ ...HEALTHY, total: 2, passed: 0, broken: 2, counts: { FAIL: 2 } }));
  assert.equal(headline.tone, "red");
});

test("the headline attributes the verdict to the evidence check, not the status code", () => {
  // A run can end status=success and still have performed no work; the
  // headline must not let the status code stand in for the check.
  assert.match(matrixHeadline(toReliability(HEALTHY)).detail, /evidence check's, not the run's status code/);
});
