import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

/*
 * Mod kernel client contract.
 *
 * Three things are pinned here, and all three are honesty rather than shape:
 *
 *  1. **Absent is `null`, never `0`, `""` or `false`.** The router refuses to
 *     answer an unreadable block with a body of zeros, so a mapper that reached
 *     for `?? 0` (or `?? false` on `measurable`) would undo that from the other
 *     side and hand the panel a verdict the Gateway never gave.
 *  2. **The client calls the routes that exist, with the verbs and bodies they
 *     declare.** Each recorded call is compared against the exact path, verb
 *     and payload shape, so a renamed or repointed route fails here rather than
 *     at runtime.
 *  3. **Local refusals happen before a request.** A decision the store cannot
 *     hold, a reason out of bounds and an unknown verb are each refused with
 *     the allowed set named, and the recorded-call list proves nothing went
 *     out. A router that answers an unknown `decision` with an empty list would
 *     otherwise turn a typo into "no holds are waiting".
 *
 * Plus the two rules that decide what an operator reads: `failureText` keeps an
 * admin/approval refusal verbatim, and `holdExpiryView` never lets
 * `decision: "approved"` stand in for "this hold may release work".
 */

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const toDataUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
const transpile = (source, extra = {}) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, ...extra },
  }).outputText;

/* ── Recorded transport ────────────────────────────────────────────────── */

const calls = [];
const responses = {};

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

/* `lib/time.ts` is pure, so the real implementation runs — the expiry and age
 * helpers are the behaviour under test, not a stand-in for it. */
const timeUrl = toDataUrl(transpile(read("./time.ts")));

const modsSource = read("./mods.ts");
const modsUrl = toDataUrl(
  transpile(modsSource)
    .replace(/from "\.\/http"/g, `from "${httpStub}"`)
    .replace(/from "\.\/time"/g, `from "${timeUrl}"`),
);

globalThis.__calls = calls;
globalThis.__responses = responses;

const mods = await import(modsUrl);

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

/* ── 1. Exact routes, verbs and bodies ─────────────────────────────────── */

test("GET /mods maps the fleet envelope", async () => {
  resetCalls();
  record("GET /mods", {
    body: {
      total: 3,
      chain: [{ order: 0, mod: "audit-ledger", version: "1.0.0", priority: 0, first_party: true, subscribed_events: ["*"] }],
      mods: [{ name: "audit-ledger" }],
    },
  });
  const fleet = await mods.fetchModFleet();
  assert.deepEqual(lastCall(), { path: "/mods", method: "GET", body: undefined });
  assert.equal(fleet.total, 3);
  assert.equal(fleet.chain.length, 1);
  assert.equal(fleet.chain[0].mod, "audit-ledger");
  assert.equal(fleet.mods[0].name, "audit-ledger");
});

test("GET /mods/commands lists the contributed commands", async () => {
  resetCalls();
  record("GET /mods/commands", {
    body: { total: 1, commands: [{ name: "hold-list", mod_name: "approvals", description: "List holds.", requires_approval: false, registered_at: 1.5 }] },
  });
  const list = await mods.fetchModCommands();
  assert.deepEqual(lastCall(), { path: "/mods/commands", method: "GET", body: undefined });
  assert.equal(list.total, 1);
  assert.equal(list.commands[0].name, "hold-list");
  assert.equal(list.commands[0].requires_approval, false);
});

test("POST /mods/commands/{name} sends the router's payload envelope", async () => {
  resetCalls();
  record("POST /mods/commands/hold-list", { body: { command: "hold-list", mod_name: "approvals", status: "success", output: "2 pending", requires_approval: false } });
  const result = await mods.runModCommand("hold-list", { args: "pending" });
  assert.deepEqual(lastCall(), { path: "/mods/commands/hold-list", method: "POST", body: { payload: { args: "pending" } } });
  assert.equal(result.status, "success");
  assert.equal(result.output, "2 pending");
});

test("GET /mods/audit always carries a clamped limit and passes filters through", async () => {
  resetCalls();
  record("GET /mods/audit?event_name=tool.requested&limit=100", { body: { total: 0, entries: [], stats: { retained: 0, capacity: 500, events: 0, by_mod: {}, by_outcome: {} } } });
  await mods.fetchModAudit({ event_name: "tool.requested" });
  assert.equal(lastCall().path, "/mods/audit?event_name=tool.requested&limit=100");
  assert.equal(lastCall().method, "GET");

  resetCalls();
  record("GET /mods/audit?limit=1000", { body: {} });
  await mods.fetchModAudit({ limit: 99999 });
  assert.equal(lastCall().path, "/mods/audit?limit=1000");
});

test("GET /mods/holds carries the validated decision and the clamped limit", async () => {
  resetCalls();
  record("GET /mods/holds?decision=pending&limit=500", { body: { total: 1, holds: [], store_file: "/home/x/.alpha/holds.json" } });
  await mods.fetchModHolds({ decision: "pending", limit: 9999 });
  assert.equal(lastCall().path, "/mods/holds?decision=pending&limit=500");

  resetCalls();
  record("GET /mods/holds?limit=100", { body: {} });
  await mods.fetchModHolds();
  assert.equal(lastCall().path, "/mods/holds?limit=100");
});

test("POST /mods/holds/{id}/{verb} sends the reason under the declared key", async () => {
  resetCalls();
  record("POST /mods/holds/h-1/approve", { body: { hold: { hold_id: "h-1", decision: "approved", tool_name: "run_command" } } });
  const decided = await mods.decideHold("h-1", "approve", "checked the impact preview");
  assert.deepEqual(lastCall(), { path: "/mods/holds/h-1/approve", method: "POST", body: { reason: "checked the impact preview" } });
  assert.equal(decided.hold.decision, "approved");

  resetCalls();
  record("POST /mods/holds/h-2/reject", { body: { hold: { hold_id: "h-2", decision: "rejected", tool_name: "shell" } } });
  await mods.decideHold("h-2", "reject", "");
  assert.deepEqual(lastCall(), { path: "/mods/holds/h-2/reject", method: "POST", body: { reason: "" } });
});

test("POST /mods/preview sends tool_name and tool_args, and never executes", async () => {
  resetCalls();
  record("POST /mods/preview", { body: { kind: "shell", measurable: true, summary: "rm -rf in a workspace", affected_paths: ["/tmp/a"], truncated: false, estimated_bytes: 12, reason: "", evidence: {} } });
  const preview = await mods.previewImpact("run_command", { cmd: "ls" });
  assert.deepEqual(lastCall(), { path: "/mods/preview", method: "POST", body: { tool_name: "run_command", tool_args: { cmd: "ls" } } });
  assert.equal(preview.measurable, true);
  assert.equal(preview.kind, "shell");
});

test("a command name is percent-encoded on the way out", async () => {
  resetCalls();
  record("POST /mods/commands/a%2Fb", { body: {} });
  await mods.runModCommand("a/b");
  assert.equal(lastCall().path, "/mods/commands/a%2Fb");
});

/* ── 2. Absent is null, never 0 / "" / false ───────────────────────────── */

test("every count and list in an empty envelope stays null, not zero", () => {
  assert.deepEqual(mods.mapFleet({}), { total: null, chain: null, mods: null });
  assert.deepEqual(mods.mapModCommandList({}), { total: null, commands: null });
  assert.deepEqual(mods.mapAuditPage({}), { total: null, entries: null, stats: null });
  assert.deepEqual(mods.mapHoldList({}), { total: null, holds: null, store_file: null });
  assert.deepEqual(mods.mapModCommandResult({}), {
    command: "",
    mod_name: null,
    status: null,
    output: null,
    requires_approval: null,
  });
  assert.deepEqual(mods.mapChainRow({}), {
    order: null,
    mod: "",
    version: null,
    priority: null,
    first_party: null,
    subscribed_events: null,
  });
});

test("an explicit empty list stays an empty list — it is not an absent one", () => {
  assert.deepEqual(mods.mapFleet({ chain: [], mods: [], total: 0 }), { total: 0, chain: [], mods: [] });
  assert.deepEqual(mods.mapModCommandList({ commands: [], total: 0 }).commands, []);
  assert.deepEqual(mods.mapAuditPage({ entries: [] }).entries, []);
  assert.deepEqual(mods.mapHoldList({ holds: [] }).holds, []);
});

test("an absent audit stats block is null, and a present one is never zeros", () => {
  assert.equal(mods.mapAuditStats(undefined), null);
  assert.equal(mods.mapAuditStats(null), null);

  const stats = mods.mapAuditStats({});
  assert.deepEqual(stats, { retained: null, capacity: null, events: null, by_mod: null, by_outcome: null });
  assert.notEqual(stats.events, 0);

  assert.deepEqual(mods.mapAuditStats({ retained: 7, capacity: 500, events: 12, by_mod: { "audit-ledger": 12 }, by_outcome: { allowed: 10 } }), {
    retained: 7,
    capacity: 500,
    events: 12,
    by_mod: { "audit-ledger": 12 },
    by_outcome: { allowed: 10 },
  });
});

test("a counter table with a non-numeric value is unreadable, not silently dropped", () => {
  assert.equal(mods.mapAuditStats({ by_mod: { "audit-ledger": 12, broken: "x" } }).by_mod, null);
  assert.equal(mods.mapAuditStats({ by_outcome: [1, 2] }).by_outcome, null);
});

test("impact preview keeps measurable tri-state and every unmeasured field null", () => {
  const blank = mods.mapImpactPreview({});
  assert.equal(blank.measurable, null, "an absent measurable is a third state, not `false`");
  assert.equal(blank.estimated_bytes, null);
  assert.equal(blank.affected_paths, null);
  assert.equal(blank.truncated, null);
  assert.equal(blank.reason, null);

  const unmeasurable = mods.mapImpactPreview({ kind: "unknown", measurable: false, summary: "could not bound", affected_paths: [] });
  assert.equal(unmeasurable.measurable, false, "`false` is the Gateway's own \"could not compute\"");
  assert.deepEqual(unmeasurable.affected_paths, []);

  assert.equal(mods.mapImpactPreview({ measurable: "yes" }).measurable, null, "a non-boolean is unreadable, not true");
  assert.equal(mods.mapImpactPreview({ estimated_bytes: 4096 }).estimated_bytes, 4096);
});

test("a describe that failed keeps its error and reports nothing else", () => {
  const described = mods.mapModDescription({ name: "third-party-mod", error: "describe failed: RuntimeError" });
  assert.equal(described.name, "third-party-mod");
  assert.equal(described.error, "describe failed: RuntimeError");
  assert.equal(described.discrepancies, null, "no discrepancy list was sent, so none is claimed");
  assert.equal(described.granted_capabilities, null);
  assert.equal(described.priority, null);
  assert.equal(described.first_party, null);
});

test("declared and observed stay side by side, never merged", () => {
  const described = mods.mapModDescription({
    name: "sec-default",
    declared: { hooks: ["tool.requested"], calls: ["ctx.storage"] },
    observed: { hooks: ["tool.requested", "model.requested"] },
    discrepancies: ["declared hook never fired: notifications"],
  });
  assert.deepEqual(mods.manifestList(described.declared, "hooks"), ["tool.requested"]);
  assert.deepEqual(mods.manifestList(described.observed, "hooks"), ["tool.requested", "model.requested"]);
  assert.deepEqual(described.discrepancies, ["declared hook never fired: notifications"]);
  assert.equal(mods.manifestList(described.observed, "state_writes"), null, "an absent key is not an empty declaration");
  assert.equal(mods.manifestList(null, "hooks"), null, "no manifest at all is not an empty one");
  assert.equal(mods.manifestList(described.declared, "hooks").length, 1);
});

test("a list carrying a non-string is unreadable rather than a filtered list", () => {
  assert.equal(mods.mapModDescription({ subscribed_events: ["*", 7] }).subscribed_events, null);
  assert.equal(mods.mapModDescription({ required_capabilities: ["storage:read"] }).required_capabilities[0], "storage:read");
});

test("a hold keeps its verbatim decision and nullable timestamps", () => {
  const hold = mods.mapHold({ hold_id: "h-1", decision: "approved", tool_name: "run_command", expires_at: 0, decided_by: "" });
  assert.equal(hold.decision, "approved");
  assert.equal(hold.expires_at, 0, "0 is the store's own \"no expiry\" value and must survive");
  assert.equal(hold.decided_at, null);
  assert.equal(hold.impact, null);

  const blank = mods.mapHold({});
  assert.equal(blank.decision, "", blank.hold_id, "");
  assert.equal(blank.created_at, null);
  assert.equal(blank.risk_level, "");
});

test("an absent hold in a decision response is null, not a fabricated row", () => {
  assert.deepEqual(mods.mapHoldDecision({}), { hold: null });
  assert.deepEqual(mods.mapHoldDecision({ hold: null }), { hold: null });
  assert.equal(mods.mapHoldDecision({ hold: { hold_id: "h-9", decision: "expired" } }).hold.hold_id, "h-9");
});

test("audit rewrites are absent until the server sends them", () => {
  assert.equal(mods.mapAuditEntry({ outcome: "allowed" }).rewrites, null);
  assert.deepEqual(mods.mapAuditEntry({ rewrites: [{ mod: "evidence", reason: "added digest" }] }).rewrites, [
    { mod: "evidence", reason: "added digest" },
  ]);
  assert.deepEqual(mods.mapAuditEntry({ rewrites: [{ nope: true }] }).rewrites, [{ mod: null, reason: null }]);

  const entry = mods.mapAuditEntry({ event: "tool.requested", outcome: "allowed", chain: ["sec-default"], payload_digest: "ab12" });
  assert.equal(entry.event, "tool.requested");
  assert.equal(entry.duration_ms, null);
  assert.equal(entry.timestamp, null);
  assert.deepEqual(entry.chain, ["sec-default"]);
});

test("an unknown decision or outcome string is preserved verbatim", () => {
  assert.equal(mods.mapHold({ decision: "escalated-somewhere" }).decision, "escalated-somewhere");
  assert.equal(mods.mapAuditEntry({ outcome: "rewritten" }).outcome, "rewritten");
  assert.equal(mods.mapModCommandResult({ status: "deferred" }).status, "deferred");
});

test("requires_approval is false only when the flag is not true", () => {
  assert.equal(mods.mapModCommand({ requires_approval: true }).requires_approval, true);
  assert.equal(mods.mapModCommand({ requires_approval: false }).requires_approval, false);
  assert.equal(mods.mapModCommand({}).requires_approval, false, "the wire field defaults to False, so absence still means no gate");
  assert.equal(mods.mapModCommand({ requires_approval: "true" }).requires_approval, false, "a non-boolean never opens a gate");
});

/* ── 3. Bounds mirrored, not tightened ─────────────────────────────────── */

test("audit and holds limits clamp onto their own server windows", () => {
  assert.equal(mods.clampAuditLimit(undefined), 100);
  assert.equal(mods.clampAuditLimit(null), 100);
  assert.equal(mods.clampAuditLimit(Number.NaN), 100);
  assert.equal(mods.clampAuditLimit(0), 1);
  assert.equal(mods.clampAuditLimit(-50), 1);
  assert.equal(mods.clampAuditLimit(9999), 1000);
  assert.equal(mods.clampAuditLimit(37.6), 38);

  assert.equal(mods.clampHoldsLimit(undefined), 100);
  assert.equal(mods.clampHoldsLimit(0), 1);
  assert.equal(mods.clampHoldsLimit(9999), 500);
});

test("an unknown hold decision is refused locally, naming the allowed set", () => {
  assert.doesNotThrow(() => mods.assertHoldDecision(undefined));
  assert.doesNotThrow(() => mods.assertHoldDecision(""));
  assert.doesNotThrow(() => mods.assertHoldDecision("pending"));
  assert.throws(() => mods.assertHoldDecision("approve"), /unknown hold decision "approve".*pending.*approved.*rejected.*expired/s);
  assert.throws(() => mods.buildHoldsPath({ decision: "nope" }), /unknown hold decision/);
});

test("an out-of-bound hold reason is refused locally, naming the bound", () => {
  assert.doesNotThrow(() => mods.assertHoldReason(""));
  assert.doesNotThrow(() => mods.assertHoldReason("x".repeat(2000)));
  assert.throws(() => mods.assertHoldReason("x".repeat(2001)), /at most 2000 characters \(got 2001\)/);
});

test("an unknown hold verb is refused locally, naming the two the router has", () => {
  assert.throws(() => mods.assertHoldVerb("delete"), /unknown hold verb "delete".*approve.*reject/s);
  assert.doesNotThrow(() => mods.assertHoldVerb("approve"));
  assert.doesNotThrow(() => mods.assertHoldVerb("reject"));
});

test("a local refusal throws before anything is sent", async () => {
  resetCalls();
  await assert.rejects(() => mods.decideHold("h-1", "delete", ""), /unknown hold verb/);
  await assert.rejects(() => mods.decideHold("h-1", "approve", "x".repeat(2001)), /at most 2000 characters/);
  await assert.rejects(() => mods.previewImpact("   "), /tool_name must be a non-empty tool name/);
  assert.equal(calls.length, 0, "no request may go out behind a local refusal");
});

test("buildAuditPath always carries a limit and drops empty filters", () => {
  assert.equal(mods.buildAuditPath({}), "/mods/audit?limit=100");
  assert.equal(mods.buildAuditPath({ limit: 5 }), "/mods/audit?limit=5");
  assert.equal(mods.buildAuditPath({ event_name: "a b", outcome: "" }), "/mods/audit?event_name=a+b&limit=100");
  assert.equal(mods.buildAuditPath({ mod_name: "audit-ledger", outcome: "allowed" }), "/mods/audit?mod_name=audit-ledger&outcome=allowed&limit=100");
});

/* ── 4. Refusals keep the server's words ───────────────────────────────── */

test("failureText keeps a status-carrying message verbatim", () => {
  const admin = mods.failureText(apiFailure(403, "Admin privileges are required to read the mod audit ledger."));
  assert.equal(admin, "Admin privileges are required to read the mod audit ledger.");
  assert.notEqual(admin, "Not allowed — this action needs admin rights or a feature flag.");

  const conflict = mods.failureText(apiFailure(409, "this command requires approval; use the hold store"));
  assert.equal(conflict, "this command requires approval; use the hold store");

  const unavailable = mods.failureText(apiFailure(503, "no audit ledger is installed on this deployment"));
  assert.equal(unavailable, "no audit ledger is installed on this deployment");
});

test("failureText falls back to the shared helper for anything without a status", () => {
  assert.equal(mods.failureText(new Error("Network unreachable")), "Network unreachable");
  assert.equal(mods.failureText("boom"), "Something went wrong.");
});

/* ── 5. Expiry travels beside the decision ─────────────────────────────── */

const NOW = 1_700_000_000_000;
const SECONDS = 1000;

test("holdExpiryView never lets decision alone carry the verdict", () => {
  // The store keeps an approved hold past its TTL: approval is recorded, expiry
  // is time-based, and the panel must not read the first as the second.
  const approvedButPast = mods.mapHold({ hold_id: "h-1", decision: "approved", expires_at: NOW / SECONDS - 60 });
  const view = mods.holdExpiryView(approvedButPast, NOW);
  assert.equal(view.past, true);
  assert.equal(view.tone, "amber");
  assert.equal(view.label, "expiry time passed");
});

test("holdExpiryView reports each absence as its own fact", () => {
  assert.deepEqual(mods.holdExpiryView(mods.mapHold({ hold_id: "h", expires_at: null }), NOW), {
    label: "no expiry reported",
    tone: "gray",
    past: false,
  });
  assert.deepEqual(mods.holdExpiryView(mods.mapHold({ hold_id: "h", expires_at: 0 }), NOW), {
    label: "no expiry reported",
    tone: "gray",
    past: false,
  });
  // A stamp that parses to no date is "unreadable", which is not "not expired".
  assert.equal(mods.holdExpiryView(mods.mapHold({ hold_id: "h", expires_at: 1e-9 }), NOW).label, "expiry time unreadable");
});

test("holdExpiryView reads a future hold as a stamp, not as a countdown", () => {
  const view = mods.holdExpiryView(mods.mapHold({ hold_id: "h", expires_at: NOW / SECONDS + 3600 }), NOW);
  assert.equal(view.past, false);
  assert.equal(view.tone, "gray");
  assert.match(view.label, /^expires /);
  assert.doesNotMatch(view.label, /in 1 hour/);
});

test("holdAge reads an absent creation time as absent, never as just now", () => {
  assert.equal(mods.holdAge(mods.mapHold({ hold_id: "h", created_at: null }), NOW), null);
  assert.equal(mods.holdAge(mods.mapHold({ hold_id: "h", created_at: (NOW - 120 * SECONDS) / SECONDS }), NOW), "2m ago");
});

/* ── 6. Source pins ────────────────────────────────────────────────────── */

test("the client declares no client-side role check", () => {
  assert.doesNotMatch(modsSource, /\b(isAdmin|is_admin|adminUser|hasAdminRole|role ===|role !==)\b/);
});

test("the client never reaches for a zero or a false default on a nullable count", () => {
  assert.doesNotMatch(modsSource, /\?\?\s*0\b/);
  assert.doesNotMatch(modsSource, /\?\?\s*false\b/);
  assert.doesNotMatch(modsSource, /\|\|\s*0\b/);
  assert.doesNotMatch(modsSource, /\.length\s*\?\?/);
});

test("the client declares every route the panel renders", () => {
  for (const route of ["/mods", "/mods/commands", "/mods/audit", "/mods/holds", "/mods/preview"]) {
    assert.ok(modsSource.includes(route), `missing route ${route}`);
  }
  // Each of the five is a distinct surface: two member reads, two admin reads
  // and one read-only preview. A client that dropped one would leave a block
  // in the section reading a route it never calls.
  assert.match(modsSource, /return `\/mods\/audit\?/);
  assert.match(modsSource, /return `\/mods\/holds\?/);
});
