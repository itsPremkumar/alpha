import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

/*
 * Side-effect journal client contract.
 *
 * Three things are pinned here, and all three are honesty rather than shape:
 *
 *  1. **Absent is `null`, never `0`, `""` or `false`.** The router refuses to
 *     answer a missing ledger with a body of zeros, so a mapper here that
 *     reached for `?? 0` would undo that from the other side and hand the view
 *     "there are zero unknowns" when the truth is "we did not look".
 *  2. **The client calls the routes that exist.** Each recorded call is
 *     compared against the exact path and verb, so a renamed or repointed
 *     route fails here rather than at runtime.
 *  3. **Local refusals happen before a request.** A reason out of bounds or a
 *     filter the server would 422 is refused with the bound named, and the
 *     recorded-call list proves nothing went out.
 */

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const toDataUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
const transpile = (source, extra = {}) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, ...extra },
  }).outputText;

/* ── Recorded transport ────────────────────────────────────────────────── */

const calls = [];
let responses = {};

/*
 * The stub reads its state off `globalThis` rather than closing over module
 * locals: a `data:` URL module has no importable binding for them, and a
 * closure over locals defined in *this* module would capture a different array
 * than the one the assertions read.
 *
 * `errMsg` mirrors the real helper's generic 401/403/404 sentences, so the
 * `failureText` tests can tell "kept the server's words" from "fell back to
 * the shared paraphrase".
 */
const httpStub = toDataUrl(`
export async function get(path, timeoutMs) {
  globalThis.__calls.push({ path, method: "GET", body: undefined });
  const entry = globalThis.__responses["GET " + path];
  if (!entry) throw new Error("no recorded response for GET " + path);
  if (entry.reject) throw new Error(entry.reject);
  return entry.body;
}
export async function send(path, method, payload) {
  globalThis.__calls.push({ path, method, body: payload });
  const entry = globalThis.__responses[method + " " + path];
  if (!entry) throw new Error("no recorded response for " + method + " " + path);
  if (entry.reject) throw new Error(entry.reject);
  return entry.body;
}
export function errMsg(err) {
  if (err instanceof Error) {
    const status = typeof err.status === "number" ? err.status : 0;
    if (status === 0) return err.message;
    if (status === 401) return "Not allowed — this action needs different permissions.";
    if (status === 403) return "Not allowed — this action needs admin rights or a feature flag.";
    if (status === 404) return "Not found — it may have been deleted.";
    return err.message;
  }
  return "Something went wrong.";
}
`);

const source = read("./side-effects.ts");
const effectsUrl = toDataUrl(transpile(source).replace(/from "\.\/http"/g, `from "${httpStub}"`));

globalThis.__calls = calls;
globalThis.__responses = responses;

const effects = await import(effectsUrl);

function record(key, outcome) {
  responses[key] = outcome;
}

function lastCall() {
  return calls[calls.length - 1];
}

function resetCalls() {
  calls.length = 0;
}

/** An `ApiError`-shaped failure: numeric status plus the server's own text. */
function apiFailure(status, message) {
  return Object.assign(new Error(message), { status });
}

/* ── Fixtures ──────────────────────────────────────────────────────────── */

const ENTRY_A = {
  tool_call_id: "call_a",
  tool_name: "shell",
  status: "unknown",
  level: "high_risk",
  thread_id: "thr_1",
  run_id: "run_1",
  user_id: "user-1",
  arguments_digest: "sha256:aaaa",
  result_digest: null,
  verdict: null,
  detail: "",
  owner_worker_id: "wk-3",
  lease_expires_at: "2026-10-08T10:00:00+00:00",
  attempt: 1,
  created_at: "2026-10-08T09:50:00+00:00",
  updated_at: "2026-10-08T09:51:00+00:00",
  needs_reconciliation: true,
  reconcilable: true,
};

const ENTRY_B = {
  tool_call_id: "call_b",
  tool_name: "http_fetch",
  status: "failed",
  level: "moderate",
  thread_id: "",
  run_id: "",
  user_id: "user-1",
  arguments_digest: "sha256:bbbb",
  result_digest: "sha256:cccc",
  verdict: "confirmed_failure",
  detail: "the endpoint answered 410",
  owner_worker_id: null,
  lease_expires_at: null,
  attempt: 2,
  created_at: "2026-10-08T09:40:00+00:00",
  updated_at: "2026-10-08T09:41:00+00:00",
  needs_reconciliation: false,
  reconcilable: false,
};

const SUMMARY = {
  reported: true,
  scope: "owner",
  total: 4,
  by_status: { pending: 0, in_flight: 0, completed: 1, failed: 1, unknown: 2, reconciled: 0 },
  by_level: { read_only: 1, low_risk: 1, moderate: 1, high_risk: 1, destructive: 0 },
  unknown: 2,
  oldest_unknown_age_seconds: 519.4,
  generated_at: "2026-10-08T10:00:00+00:00",
};

const QUEUE = {
  reported: true,
  scope: "owner",
  status_filter: "unknown",
  order: "oldest_first",
  count: 2,
  entries: [ENTRY_A, ENTRY_B],
};

/* ── Routes and verbs ──────────────────────────────────────────────────── */

test("summary reads /side-effects/summary with no query", async () => {
  resetCalls();
  record("GET /side-effects/summary", { body: SUMMARY });
  await effects.fetchSideEffectSummary();
  assert.equal(lastCall().path, "/side-effects/summary");
  assert.equal(lastCall().method, "GET");
  assert.equal(lastCall().body, undefined);
});

test("summary is not routed through the /{tool_call_id} catch-all", () => {
  // The router declares /summary before /{tool_call_id}; the client must name
  // the literal path or an id-shaped segment would answer instead.
  assert.match(source, /"\/side-effects\/summary"/);
});

test("an unfiltered list reads /side-effects with the server's default limit", async () => {
  resetCalls();
  record("GET /side-effects?limit=50", { body: QUEUE });
  await effects.fetchSideEffectList();
  assert.equal(lastCall().path, "/side-effects?limit=50");
  assert.equal(lastCall().method, "GET");
});

test("every filter reaches the query string", async () => {
  resetCalls();
  const path = effects.buildListPath({
    status: "unknown",
    level: "high_risk",
    tool_name: "shell",
    run_id: "run_1",
    thread_id: "thr_1",
    user_id: "user-1",
    limit: 7,
  });
  const params = new URLSearchParams(path.slice(path.indexOf("?") + 1));
  assert.equal(params.get("status"), "unknown");
  assert.equal(params.get("level"), "high_risk");
  assert.equal(params.get("tool_name"), "shell");
  assert.equal(params.get("run_id"), "run_1");
  assert.equal(params.get("thread_id"), "thr_1");
  assert.equal(params.get("user_id"), "user-1");
  assert.equal(params.get("limit"), "7");
});

test("an empty filter is omitted, never sent as a bare key", () => {
  // The router 422s `status=` (its own set does not contain ""), so an empty
  // string must read as "no filter" here rather than as a value.
  const path = effects.buildListPath({ status: "", level: "", tool_name: "" });
  assert.equal(path, "/side-effects?limit=50");
  assert.ok(!path.includes("status="));
  assert.ok(!path.includes("level="));
});

test("detail reads one entry by id, percent-encoded", async () => {
  resetCalls();
  record("GET /side-effects/call_a", { body: ENTRY_A });
  await effects.fetchSideEffect("call_a");
  assert.equal(lastCall().path, "/side-effects/call_a");
  assert.equal(lastCall().method, "GET");

  record("GET /side-effects/weird%2Fid", { body: ENTRY_A });
  await effects.fetchSideEffect("weird/id");
  assert.equal(lastCall().path, "/side-effects/weird%2Fid");
});

test("reconcile POSTs {verdict, reason} to .../reconcile", async () => {
  resetCalls();
  record("POST /side-effects/call_a/reconcile", {
    body: {
      tool_call_id: "call_a",
      verdict: "confirmed_success",
      status: "reconciled",
      reopened: false,
      escalated: false,
      reconcilable: false,
      entry: { ...ENTRY_A, status: "reconciled", reconcilable: false },
    },
  });
  const result = await effects.reconcileSideEffect("call_a", "confirmed_success", "re-ran the command; exit 0");
  assert.equal(lastCall().path, "/side-effects/call_a/reconcile");
  assert.equal(lastCall().method, "POST");
  assert.deepEqual(lastCall().body, { verdict: "confirmed_success", reason: "re-ran the command; exit 0" });
  assert.equal(result.status, "reconciled");
  assert.equal(result.reconcilable, false);
});

/* ── Envelope mapping: absent is null, never zero ──────────────────────── */

test("summary maps every reported field verbatim", () => {
  const mapped = effects.mapSummary(SUMMARY);
  assert.equal(mapped.reported, true);
  assert.equal(mapped.scope, "owner");
  assert.equal(mapped.total, 4);
  assert.equal(mapped.unknown, 2);
  assert.equal(mapped.oldest_unknown_age_seconds, 519.4);
  assert.equal(mapped.generated_at, "2026-10-08T10:00:00+00:00");
  assert.deepEqual(mapped.by_status, SUMMARY.by_status);
  assert.deepEqual(mapped.by_level, SUMMARY.by_level);
});

test("an unreported summary is all nulls, never zeros or empty tables", () => {
  const mapped = effects.mapSummary({});
  assert.equal(mapped.reported, false);
  assert.equal(mapped.scope, null);
  assert.equal(mapped.total, null, "a missing total must not read as 0 recorded effects");
  assert.equal(mapped.unknown, null, "a missing unknown count must not read as an empty queue");
  assert.equal(mapped.by_status, null, "a missing table must not read as zero in every bucket");
  assert.equal(mapped.by_level, null);
  assert.equal(mapped.oldest_unknown_age_seconds, null, "an unreadable age must not read as 0 seconds old");
  assert.equal(mapped.generated_at, null);
});

test("an explicit null age stays null — it is 'nothing is waiting', not '0s'", () => {
  const mapped = effects.mapSummary({ ...SUMMARY, oldest_unknown_age_seconds: null, unknown: 0, total: 0 });
  assert.equal(mapped.oldest_unknown_age_seconds, null);
  assert.equal(mapped.unknown, 0, "a measured zero is still reported as 0");
  assert.equal(mapped.total, 0);
});

test("a count table with a non-numeric value is unreadable, not partial", () => {
  assert.equal(effects.mapSummary({ ...SUMMARY, by_status: { unknown: "two" } }).by_status, null);
  assert.equal(effects.mapSummary({ ...SUMMARY, by_status: [] }).by_status, null);
});

test("list maps the envelope verbatim", () => {
  const mapped = effects.mapList(QUEUE);
  assert.equal(mapped.reported, true);
  assert.equal(mapped.scope, "owner");
  assert.equal(mapped.status_filter, "unknown");
  assert.equal(mapped.order, "oldest_first");
  assert.equal(mapped.count, 2);
  assert.equal(mapped.entries.length, 2);
  assert.equal(mapped.entries[0].tool_call_id, "call_a");
  assert.equal(mapped.entries[1].tool_call_id, "call_b");
});

test("an omitted entries list maps to null, not []", () => {
  const mapped = effects.mapList({ reported: true, scope: "all", count: 0 });
  assert.equal(mapped.entries, null, "the Gateway sending no list is not the same as an empty ledger");
  assert.equal(mapped.count, 0);
  assert.equal(mapped.order, null);
  assert.equal(mapped.status_filter, null);
});

test("a reported empty list maps to []", () => {
  const mapped = effects.mapList({ reported: true, scope: "all", count: 0, entries: [] });
  assert.deepEqual(mapped.entries, []);
});

test("entry maps every reported field and nulls every absent one", () => {
  const mapped = effects.mapEntry(ENTRY_A);
  assert.equal(mapped.tool_call_id, "call_a");
  assert.equal(mapped.tool_name, "shell");
  assert.equal(mapped.status, "unknown");
  assert.equal(mapped.level, "high_risk");
  assert.equal(mapped.arguments_digest, "sha256:aaaa");
  assert.equal(mapped.attempt, 1);
  assert.equal(mapped.needs_reconciliation, true);
  assert.equal(mapped.reconcilable, true);
  assert.equal(mapped.lease_expires_at, "2026-10-08T10:00:00+00:00");
  assert.equal(mapped.result_digest, null);
  assert.equal(mapped.verdict, null);
});

test("an empty optional field the server sent stays empty; an absent one is null", () => {
  assert.equal(effects.mapEntry(ENTRY_B).thread_id, "");
  assert.equal(effects.mapEntry(ENTRY_B).run_id, "");
  assert.equal(effects.mapEntry({ tool_call_id: "x" }).thread_id, null);
  assert.equal(effects.mapEntry({ tool_call_id: "x" }).lease_expires_at, null);
  assert.equal(effects.mapEntry(ENTRY_B).lease_expires_at, null);
});

test("an entry with no reconcilability claim maps to null, not false", () => {
  const mapped = effects.mapEntry({ tool_call_id: "call_x", status: "unknown" });
  assert.equal(mapped.reconcilable, null, "false would claim the server refused a verdict");
  assert.equal(mapped.needs_reconciliation, null, "false would claim the server found nothing to reconcile");
});

test("an unknown status/level/verdict string is preserved verbatim", () => {
  const mapped = effects.mapEntry({ ...ENTRY_A, status: "quarantined", level: "catastrophic", verdict: "shrug" });
  assert.equal(mapped.status, "quarantined");
  assert.equal(mapped.level, "catastrophic");
  assert.equal(mapped.verdict, "shrug");
});

test("reconcile response maps reopened/escalated as tri-state", () => {
  const full = effects.mapReconcileResult({
    tool_call_id: "call_a",
    verdict: "undetermined",
    status: "unknown",
    reopened: true,
    escalated: false,
    reconcilable: true,
    entry: ENTRY_A,
  });
  assert.equal(full.reopened, true);
  assert.equal(full.escalated, false);
  assert.equal(full.reconcilable, true);
  assert.equal(full.entry.tool_call_id, "call_a");

  const bare = effects.mapReconcileResult({ tool_call_id: "call_a" });
  assert.equal(bare.reopened, null, "absent must not read as 'not reopened'");
  assert.equal(bare.escalated, null, "absent must not read as 'not escalated'");
  assert.equal(bare.reconcilable, null);
  assert.equal(bare.entry, null);
});

/* ── Bounds ────────────────────────────────────────────────────────────── */

test("clampLimit mirrors the server's ge=1 le=500 window", () => {
  assert.equal(effects.clampLimit(1), 1);
  assert.equal(effects.clampLimit(500), 500);
  assert.equal(effects.clampLimit(0), 1, "the server refuses 0, so the client never sends it");
  assert.equal(effects.clampLimit(-5), 1);
  assert.equal(effects.clampLimit(5001), 500, "the server refuses 5001, so the client never sends it");
  assert.equal(effects.clampLimit(50.4), 50);
  assert.equal(effects.clampLimit(Number.NaN), 50);
  assert.equal(effects.clampLimit(undefined), 50);
  assert.equal(effects.clampLimit(null), 50);
  assert.equal(effects.clampLimit(Number.POSITIVE_INFINITY), 50, "a non-finite limit is not a limit: fall back to the default, not to the ceiling");
});

test("the limit sent is always a clamped integer", () => {
  assert.ok(effects.buildListPath({ limit: 0 }).endsWith("limit=1"));
  assert.ok(effects.buildListPath({ limit: 9999 }).endsWith("limit=500"));
  assert.ok(effects.buildListPath({ limit: -1 }).endsWith("limit=1"));
});

/* ── Local refusals happen before a request ────────────────────────────── */

test("an unknown status filter is refused locally, naming the allowed set", async () => {
  resetCalls();
  await assert.rejects(() => effects.fetchSideEffectList({ status: "settled" }), (err) => {
    assert.match(err.message, /unknown status "settled"/);
    assert.match(err.message, /pending/);
    assert.match(err.message, /reconciled/);
    return true;
  });
  assert.equal(calls.length, 0, "nothing should have been sent to earn a 422");
});

test("an unknown level filter is refused locally, naming the allowed set", async () => {
  resetCalls();
  await assert.rejects(() => effects.fetchSideEffectList({ level: "catastrophic" }), (err) => {
    assert.match(err.message, /unknown level "catastrophic"/);
    assert.match(err.message, /destructive/);
    return true;
  });
  assert.equal(calls.length, 0);
});

test("an empty reason is refused locally with the bound named", async () => {
  resetCalls();
  await assert.rejects(() => effects.reconcileSideEffect("call_a", "confirmed_success", ""), (err) => {
    assert.match(err.message, /1–2000 characters/);
    assert.match(err.message, /\(got 0\)/);
    return true;
  });
  assert.equal(calls.length, 0, "an empty reason is the verdict-with-no-basis this ledger exists to prevent");
});

test("a reason over the server's 2000-character bound is refused locally", async () => {
  resetCalls();
  await assert.rejects(() => effects.reconcileSideEffect("call_a", "confirmed_success", "x".repeat(2001)), (err) => {
    assert.match(err.message, /1–2000 characters/);
    assert.match(err.message, /\(got 2001\)/);
    return true;
  });
  assert.equal(calls.length, 0);
});

test("reasons at both bounds are accepted and sent raw", async () => {
  resetCalls();
  record("POST /side-effects/call_a/reconcile", { body: { tool_call_id: "call_a", verdict: "confirmed_success", status: "reconciled" } });
  await effects.reconcileSideEffect("call_a", "confirmed_success", "x");
  assert.equal(lastCall().body.reason, "x");

  await effects.reconcileSideEffect("call_a", "confirmed_success", "x".repeat(2000));
  assert.equal(lastCall().body.reason.length, 2000);
});

test("an unknown verdict is refused locally, naming the allowed set", async () => {
  resetCalls();
  await assert.rejects(() => effects.reconcileSideEffect("call_a", "shrug", "no idea"), (err) => {
    assert.match(err.message, /unknown verdict "shrug"/);
    assert.match(err.message, /confirmed_success/);
    assert.match(err.message, /undetermined/);
    return true;
  });
  assert.equal(calls.length, 0);
});

/* ── Failures reject; they never resolve to an empty envelope ──────────── */

test("a refused read rejects with the server's reason", async () => {
  resetCalls();
  record("GET /side-effects/summary", { reject: "side_effect_store_unavailable" });
  await assert.rejects(() => effects.fetchSideEffectSummary(), /side_effect_store_unavailable/);
});

test("a refused reconcile rejects rather than resolving to a settled entry", async () => {
  resetCalls();
  record("POST /side-effects/call_a/reconcile", { reject: "side_effect_transition_refused" });
  await assert.rejects(() => effects.reconcileSideEffect("call_a", "confirmed_success", "checked"), /side_effect_transition_refused/);
});

/* ── failureText keeps a refusal verbatim ──────────────────────────────── */

test("failureText keeps an ApiError message instead of errMsg's paraphrase", () => {
  const err = apiFailure(403, "reconciliation requires an administrator (caller 'user-1' is a member)");
  assert.equal(effects.failureText(err), "reconciliation requires an administrator (caller 'user-1' is a member)");
  assert.notEqual(effects.failureText(err), "Not allowed — this action needs admin rights or a feature flag.");
});

test("failureText keeps a 409 transition refusal verbatim", () => {
  const err = apiFailure(409, "side-effect entry 'call_a' was already settled by an earlier verdict");
  assert.equal(effects.failureText(err), "side-effect entry 'call_a' was already settled by an earlier verdict");
});

test("failureText falls back to errMsg for anything without a status", () => {
  assert.equal(effects.failureText("boom"), "Something went wrong.");
  assert.equal(effects.failureText(null), "Something went wrong.");
  // A locally-thrown refusal carries no status, so its own message survives.
  assert.match(effects.failureText(new Error("reason must be 1–2000 characters (got 0)")), /1–2000/);
});

/* ── Source pins ───────────────────────────────────────────────────────── */

test("the client talks to the gateway only through get/send", () => {
  assert.ok(!/\bfetch\s*\(/.test(source), "components and clients must not call fetch directly");
  assert.match(source, /import \{ errMsg, get, send \} from "\.\/http";/);
});

test("the client never coerces a missing count to zero", () => {
  // `?? 0`, `|| 0` and `Number(x ?? 0)` are the three ways this plane would
  // turn "could not read the ledger" into "the ledger is empty".
  assert.ok(!/\?\?\s*0\b/.test(source), "no count may fall back to 0");
  assert.ok(!/\|\|\s*0\b/.test(source), "no count may fall back to 0");
});
