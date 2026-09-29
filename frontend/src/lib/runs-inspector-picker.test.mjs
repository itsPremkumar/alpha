// runs-inspector-picker.test.mjs — the run picker's own contract: the filter
// over the runs already loaded, and the permalink that reopens one run.
//
// What is pinned here:
//
//  * the real mapping, from the payloads this Gateway returns through the real
//    `toRunRecord` into the filter — a query is matched as a case-insensitive
//    substring of a run's `run_id`, `status`, `model` and `error` only;
//  * the honesty inversions, which are the whole point of a separate exported
//    flag. `[]` because the filter matched nothing and `[]` because the Gateway
//    reported no runs are opposite claims, and `applyRunFilter` has to keep
//    them apart. A caller that cannot tell them apart is a view that will tell
//    the operator the wrong thing;
//  * that a run the Gateway reported almost nothing about — no status, no
//    model, no error — is still filterable by its own id, and that the word
//    "null" does not match it, because an absent field is not a string.
//
// The transport is stubbed; the mapper, the filter and the hash helpers above
// it are the real code.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");

/**
 * Transpiled modules are written to a temp directory and imported as real
 * files, not as nested `data:` URLs, and relative specifiers are rewritten to
 * sibling files so Node resolves the graph exactly as it would for the app.
 */
const OUT_DIR = join(tmpdir(), `alpha-run-picker-test-${process.pid}`);
mkdirSync(OUT_DIR, { recursive: true });

function compile(relative, { jsx = false, specifiers = {} } = {}) {
  let code = ts.transpileModule(read(relative), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: jsx ? ts.JsxEmit.ReactJSX : undefined },
  }).outputText;
  for (const [from, to] of Object.entries(specifiers)) {
    code = code.replace(new RegExp(`(from\\s+)"${from.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"`, "g"), `$1"${to}"`);
  }
  return code;
}

function emit(name, code) {
  writeFileSync(join(OUT_DIR, `${name}.mjs`), code, "utf8");
  return `./${name}.mjs`;
}
const loadFile = (name) => import(pathToFileURL(join(OUT_DIR, `${name}.mjs`)).href);

/* ── the stubbed transport ─────────────────────────────────────────────────── */

// Only `toRunRecord` is exercised here, so nothing below this line issues a
// request; the stub exists because the real client module imports it.
const httpStub = `
export function setHttpHandler() {}
export async function get(path) { throw new Error("the picker filter makes no request, but " + path + " was fetched"); }
export async function send(path) { throw new Error("the picker filter makes no request, but " + path + " was sent"); }
export class ApiError extends Error { constructor(status, message) { super(message); this.status = status; } }
export function errMsg(err) { return err instanceof Error ? err.message : "Something went wrong."; }
export function pick(obj, keys, fallback) { return fallback; }
export function asList(body) { return Array.isArray(body) ? body : []; }
export const DEFAULT_TIMEOUT_MS = 60000;
export const GATEWAY_BASE = "/api";
`;
const httpRef = emit("http", httpStub);

/* ── the real modules, above the stub ──────────────────────────────────────── */

const sseRef = emit("sse-reducer", compile("./sse-reducer.ts"));
const runsRef = emit("runs", compile("./runs.ts", { specifiers: { "./http": httpRef } }));
const inspectorRef = emit(
  "runs-inspector",
  compile("./runs-inspector.ts", { specifiers: { "./http": httpRef, "./runs": runsRef, "./sse-reducer": sseRef } })
);
const inspector = await loadFile("runs-inspector");
emit("runs-inspector-picker", compile("./runs-inspector-picker.ts", { specifiers: { "./runs-inspector": inspectorRef } }));
const picker = await loadFile("runs-inspector-picker");

/* ── run rows built from payloads this Gateway actually returns ────────────── */

const SUCCESS_RUN = {
  run_id: "e528f5a6-2c82-4959-aafd-a73da0996468",
  thread_id: "c10110de-79ae-4f88-8049-2b50c3436e82",
  assistant_id: "lead_agent",
  status: "success",
  metadata: { alpha_trace_id: "4e22ce768efb4ba8853515995cb95341" },
  kwargs: { input: { messages: [{ role: "user", content: "Use the bash tool to run `echo alpha-inspector-probe`" }] } },
  multitask_strategy: "reject",
  created_at: "2026-09-28T15:07:39.158838+00:00",
  updated_at: "2026-09-28T15:08:30.372347+00:00",
  model: "alpha-free",
  error: null,
  total_tokens: 53244,
  message_count: 4,
  stop_reason: null,
};

const FAILED_RUN = {
  run_id: "077c8cc1-5de8-4c50-bb2c-27ad1a62118a",
  thread_id: "653d0320-8809-4368-8bac-3e6f87e304d6",
  assistant_id: null,
  status: "error",
  kwargs: { input: { messages: [{ role: "user", content: "What is 17 * 23? Reply with only the number." }] }, config: null },
  multitask_strategy: "reject",
  created_at: "2026-09-28T14:53:29.158838+00:00",
  updated_at: "2026-09-28T14:53:35.789943+00:00",
  model: "union-alpha",
  error: "Error code: 401 - Failed to authenticate request with Clerk\nAutomatic recovery will not replay a non-transient model failure (generic).",
  total_tokens: 0,
  message_count: 1,
  stop_reason: "recovery_blocked",
  token_usage_by_model: null,
};

/** A run still going. Its row must never read as a finished one. */
const ACTIVE_RUN = {
  run_id: "a1c0ffee-0000-4000-8000-000000000001",
  thread_id: "c10110de-79ae-4f88-8049-2b50c3436e82",
  status: "running",
  model: "union-alpha",
  error: null,
  created_at: "2026-09-29T09:00:00.000000+00:00",
};

/**
 * A status string from a Gateway newer than this client knows. The filter
 * matches it verbatim, the way every other surface in the inspector does.
 */
const FOREIGN_RUN = {
  run_id: "a1c0ffee-0000-4000-8000-000000000002",
  thread_id: "c10110de-79ae-4f88-8049-2b50c3436e82",
  status: "quiesced_by_operator",
  model: "kilo:kilo-auto/free",
  error: null,
};

/**
 * A run the Gateway reported almost nothing about: no status, no model, no
 * error. This is a real shape, not a defensive one — every one of those fields
 * is `null` rather than a default, and the filter has to survive it.
 */
const UNREPORTED_RUN = {
  run_id: "a1c0ffee-0000-4000-8000-000000000003",
  thread_id: "c10110de-79ae-4f88-8049-2b50c3436e82",
};

const RUNS = [
  inspector.toRunRecord(SUCCESS_RUN),
  inspector.toRunRecord(FAILED_RUN),
  inspector.toRunRecord(ACTIVE_RUN),
  inspector.toRunRecord(FOREIGN_RUN),
  inspector.toRunRecord(UNREPORTED_RUN),
];

const ids = (runs) => runs.map((run) => run.run_id);

/* ══ 1. An empty query is the server's whole answer ═════════════════════════ */

test("an empty or whitespace-only query returns every row, unaltered", () => {
  for (const query of ["", " ", "\t\n  "]) {
    const filtered = picker.filterRuns(RUNS, query);
    assert.equal(filtered.length, RUNS.length, `query ${JSON.stringify(query)} must not hide a row`);
    assert.deepEqual(ids(filtered), ids(RUNS));
    // The very same array, not a copy of it: no filter was applied, so the
    // picker is still showing exactly what the Gateway sent.
    assert.equal(filtered, RUNS);
  }
});

test("a query is matched case-insensitively, with surrounding space ignored", () => {
  assert.deepEqual(ids(picker.filterRuns(RUNS, "  SUCCESS  ")), [SUCCESS_RUN.run_id]);
  assert.deepEqual(ids(picker.filterRuns(RUNS, "UnIoN-AlPhA")), [FAILED_RUN.run_id, ACTIVE_RUN.run_id]);
});

/* ══ 2. The four fields, and only the four fields ═══════════════════════════ */

test("a run is matched by its own id", () => {
  assert.deepEqual(ids(picker.filterRuns(RUNS, FAILED_RUN.run_id)), [FAILED_RUN.run_id]);
  // A substring of the id counts; the id is one of the run's own fields.
  assert.deepEqual(ids(picker.filterRuns(RUNS, "5DE8-4C50")), [FAILED_RUN.run_id]);
});

test("a run is matched by the status the server reported, verbatim", () => {
  assert.deepEqual(ids(picker.filterRuns(RUNS, "error")), [FAILED_RUN.run_id]);
  assert.deepEqual(ids(picker.filterRuns(RUNS, "running")), [ACTIVE_RUN.run_id]);
  // A status from a newer Gateway is a real value, matched as it stands.
  assert.deepEqual(ids(picker.filterRuns(RUNS, "quiesced_by_operator")), [FOREIGN_RUN.run_id]);
  // Substring, not equality: a prefix of the server's own word still matches it.
  assert.deepEqual(ids(picker.filterRuns(RUNS, "err")), [FAILED_RUN.run_id]);
  // A whole word the server never sent matches nothing, even though
  // "completed" is a word this client knows.
  assert.deepEqual(ids(picker.filterRuns(RUNS, "completed")), []);
});

test("a run is matched by the model that served it", () => {
  assert.deepEqual(ids(picker.filterRuns(RUNS, "alpha-free")), [SUCCESS_RUN.run_id]);
  assert.deepEqual(ids(picker.filterRuns(RUNS, "kilo:kilo-auto/free")), [FOREIGN_RUN.run_id]);
});

test("a run is matched by a substring of its recorded error", () => {
  assert.deepEqual(ids(picker.filterRuns(RUNS, "failed to authenticate")), [FAILED_RUN.run_id]);
  assert.deepEqual(ids(picker.filterRuns(RUNS, "401")), [FAILED_RUN.run_id]);
  assert.deepEqual(ids(picker.filterRuns(RUNS, "RECOVERY will not replay")), [FAILED_RUN.run_id]);
});

test("nothing but the run's own four fields is searched", () => {
  for (const [what, query] of [
    ["a token count", "53244"],
    ["a timestamp", "2026-09-28"],
    ["the stop reason", "recovery_blocked"],
    ["the prompt text", "alpha-inspector-probe"],
    ["the assistant", "lead_agent"],
    ["the trace id", "4e22ce768efb4ba8853515995cb95341"],
    ["the thread id", "c10110de-79ae-4f88-8049-2b50c3436e82"],
  ]) {
    assert.deepEqual(picker.filterRuns(RUNS, query), [], `the filter must not search ${what}`);
  }
});

/* ══ 3. The two empty states are different claims ═══════════════════════════ */

test("a query that matches nothing returns [], and says so", () => {
  assert.deepEqual(picker.filterRuns(RUNS, "no-such-run-anywhere"), []);
  assert.equal(picker.runMatchesQuery(RUNS[0], "no-such-run-anywhere"), false);
});

test("'the filter matched nothing' is distinguishable from 'the server reported no runs'", () => {
  // The filter's own empty answer…
  const missed = picker.applyRunFilter(RUNS, "no-such-run-anywhere");
  assert.deepEqual(missed.runs, []);
  assert.equal(missed.filtered, true, "a non-empty query that matched nothing must be marked as a filter result");
  assert.equal(missed.query, "no-such-run-anywhere");

  // …and the server's empty answer, which the filter did not cause.
  const serverEmpty = picker.applyRunFilter([], "");
  assert.deepEqual(serverEmpty.runs, []);
  assert.equal(serverEmpty.filtered, false, "an empty list with no query is the server's answer, not a filter result");
  assert.equal(serverEmpty.query, "");

  // A filter that did not empty the list is also not a filter result in the
  // "nothing matched" sense.
  const unfiltered = picker.applyRunFilter(RUNS, "");
  assert.equal(unfiltered.filtered, false);
  assert.equal(unfiltered.runs.length, RUNS.length);
  // The whole point: the same `runs: []` array, two different flags.
  assert.deepEqual(missed.runs, serverEmpty.runs);
  assert.notEqual(missed.filtered, serverEmpty.filtered, "the two empty states must not share one flag value");
});

test("a filter that keeps some rows is marked as a filter result and counts them", () => {
  const some = picker.applyRunFilter(RUNS, "union-alpha");
  assert.equal(some.filtered, true);
  assert.deepEqual(ids(some.runs), [FAILED_RUN.run_id, ACTIVE_RUN.run_id]);
  // The rows come back in the order the Gateway returned them, unmutated.
  assert.equal(some.runs[0], RUNS[1]);
  assert.equal(some.runs[1], RUNS[2]);
});

/* ══ 4. A run the Gateway reported almost nothing about ═════════════════════ */

test("a run with no status, model or error is filterable by its id and crashes on nothing", () => {
  const bare = RUNS[4];
  assert.equal(bare.status, null);
  assert.equal(bare.model, null);
  assert.equal(bare.error, null);

  assert.deepEqual(ids(picker.filterRuns(RUNS, bare.run_id)), [bare.run_id]);
  // The absent fields match nothing — and, critically, they are not the string
  // "null", so a query for that word cannot sweep up every unreported run.
  for (const query of ["null", "undefined", "not reported", "none", "alpha", "error", "success", "running"]) {
    assert.equal(picker.runMatchesQuery(bare, query), false, `"${query}" must not match an unreported field`);
  }
  // A query no run in the list answers to comes back empty rather than
  // returning the unreported run as a consolation prize.
  for (const query of ["null", "undefined", "not reported", "none"]) {
    assert.deepEqual(picker.filterRuns(RUNS, query), [], `"${query}" must not reach the unreported run`);
  }
  // The other four rows are untouched by any of it.
  assert.deepEqual(ids(picker.filterRuns(RUNS, "")), ids(RUNS));
});

/* ══ 5. The permalink ══════════════════════════════════════════════════════ */

test("a permalink round-trips through the hash it writes", () => {
  const hash = picker.runInspectorHash("c10110de-79ae-4f88-8049-2b50c3436e82", "e528f5a6-2c82-4959-aafd-a73da0996468");
  assert.equal(hash, "#run-inspector/c10110de-79ae-4f88-8049-2b50c3436e82/e528f5a6-2c82-4959-aafd-a73da0996468");
  assert.deepEqual(picker.parseRunInspectorHash(hash), {
    threadId: "c10110de-79ae-4f88-8049-2b50c3436e82",
    runId: "e528f5a6-2c82-4959-aafd-a73da0996468",
  });
  // The leading "#" is the browser's, not ours: both forms parse the same.
  assert.deepEqual(
    picker.parseRunInspectorHash(hash.slice(1)),
    picker.parseRunInspectorHash(hash),
    "a hash read from location.hash must parse the same as the one we wrote"
  );
});

test("a permalink names the conversation it belongs to, so a foreign one is detectable", () => {
  // The thread id is carried, not discarded: a link for another conversation
  // must be recognisable as such, because opening it here would read a run
  // that does not belong to the conversation on screen.
  const foreign = picker.runInspectorHash("653d0320-8809-4368-8bac-3e6f87e304d6", FAILED_RUN.run_id);
  assert.deepEqual(picker.parseRunInspectorHash(foreign), {
    threadId: "653d0320-8809-4368-8bac-3e6f87e304d6",
    runId: FAILED_RUN.run_id,
  });
});

test("a hash that names no run parses as null, never as a guess", () => {
  for (const hash of [
    null,
    undefined,
    "",
    "#",
    "#/some/other/app/t-1/r-1",
    "#run-inspector",
    `#run-inspector/${"c10110de-79ae-4f88-8049-2b50c3436e82"}`,
    `#run-inspector//${FAILED_RUN.run_id}`,
    `#run-inspector/c10110de-79ae-4f88-8049-2b50c3436e82/`,
    `#run-inspector/c10110de-79ae-4f88-8049-2b50c3436e82/%zz`,
    `#run-inspector/%zz/${FAILED_RUN.run_id}`,
  ]) {
    assert.equal(picker.parseRunInspectorHash(hash), null, `${JSON.stringify(hash)} must name no run`);
  }
});

test("the permalink replaces whatever hash the page already had", () => {
  const base = "http://127.0.0.1:2026/?q=runs";
  const link = picker.runPermalink(base, "t-1", "r-1");
  assert.equal(link, "http://127.0.0.1:2026/?q=runs#run-inspector/t-1/r-1");
  // Copying twice must not stack two fragments into something unparseable.
  assert.equal(picker.runPermalink(link, "t-1", "r-1"), link);
  assert.deepEqual(picker.parseRunInspectorHash(link.split("#")[1]), { threadId: "t-1", runId: "r-1" });
});

test("ids that are not URL-safe survive the round trip", () => {
  const link = picker.runPermalink("http://127.0.0.1:2026/", "thread/one two", "run?#1");
  assert.deepEqual(picker.parseRunInspectorHash(link.split("#")[1]), { threadId: "thread/one two", runId: "run?#1" });
});

test("the picker filter adds no request of its own", () => {
  // `get`/`send` in the stub above throw by design. Everything asserted in
  // this file ran without one, which is the pin: the filter is a view over
  // rows already in memory, not a second query the Gateway never answered.
  assert.equal(inspector.toRunRecord(SUCCESS_RUN).total_tokens, 53244, "the real mapper still reads the real payload");
  assert.equal(picker.filterRuns(RUNS, "alpha-free").length, 1);
});
