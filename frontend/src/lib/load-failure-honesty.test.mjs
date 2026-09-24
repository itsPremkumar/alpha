// load-failure-honesty.test.mjs — wave-2 audit pins (batch S1-C).
//
// A FAILED fetch must never be rendered as an empty/zero success:
//  * api.ts exposes FetchResult-based entry points where ok:false is
//    distinguishable from a genuinely empty list; the legacy [] wrappers are
//    pinned as the explicit, documented ChatView-compat path ONLY.
//  * the Messages list helpers (comm/inbox/teamops) reject on gateway failure
//    instead of swallowing into [].
//  * the five audited components carry per-surface failure flags/states.
//
// Pure Node test (node --test src/lib/*.test.mjs): transpiles the modules and
// rewrites their relative imports to data: URL stubs — no server, no browser.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;
const transpile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
const read = (url) => readFileSync(new URL(url, import.meta.url), "utf8");

/* ── Stub 1: api-client for api.ts ─────────────────────────────────────── */

const apiClientStub = `
let next = { ok: true, json: async () => [] };
export function setResponse(r) { next = r; }
export async function apiFetch() {
  const r = next;
  if (r.error) throw r.error;
  return r;
}
`;
const apiClientStubUrl = toDataUrl(apiClientStub);

// Type-only import from "@/types/chat": provide named bindings in case the
// transpiler keeps the import statement.
const typesStubUrl = toDataUrl(`
export const ChatMessage = undefined;
export const Thread = undefined;
export const AIModel = undefined;
export const SlashCommandInfo = undefined;
export const SlashCommandResult = undefined;
export const AutonomousDetection = undefined;
`);

let apiCode = transpile(read("./api.ts"));
apiCode = apiCode.replace(/from\s+"\.\/api-client"/, `from "${apiClientStubUrl}"`);
apiCode = apiCode.replace(/from\s+"@\/types\/chat"/, `from "${typesStubUrl}"`);
const { fetchThreads, fetchThreadsResult, fetchThreadHistory, fetchThreadHistoryResult } = await import(toDataUrl(apiCode));
const { setResponse } = await import(apiClientStubUrl);

/* ── Stub 2: http for comm.ts / inbox.ts / teamops.ts ──────────────────── */

const httpStub = `
let fail = false;
export function setHttpFail(v) { fail = v; }
export async function get() {
  if (fail) throw new Error("gateway down");
  return {};
}
export async function send() { return {}; }
export function asList(body, keys) { return Array.isArray(body) ? body : []; }
export function pick(obj, keys, fallback) { return fallback; }
`;
const httpStubUrl = toDataUrl(httpStub);

// inbox.ts first (comm.ts imports it), with the same shared http stub.
let inboxCode = transpile(read("./inbox.ts"));
inboxCode = inboxCode.replace(/from\s+"\.\/http"/, `from "${httpStubUrl}"`);
const inboxUrl = toDataUrl(inboxCode);

let commCode = transpile(read("./comm.ts"));
commCode = commCode.replace(/from\s+"\.\/http"/, `from "${httpStubUrl}"`);
commCode = commCode.replace(/from\s+"\.\/inbox"/, `from "${inboxUrl}"`);
const { listRooms, rollCall, listRoomRuns, listDmThreads } = await import(toDataUrl(commCode));

let teamopsCode = transpile(read("./teamops.ts"));
teamopsCode = teamopsCode.replace(/from\s+"\.\/http"/, `from "${httpStubUrl}"`);
const { orgEvents } = await import(toDataUrl(teamopsCode));

const { fetchRoster } = await import(inboxUrl);
const { setHttpFail } = await import(httpStubUrl);

/* ── api.ts: failure is distinguishable from empty ─────────────────────── */

test("fetchThreadsResult reports gateway failure as ok:false, never as an empty list", async () => {
  setResponse({ error: new Error("The request could not be completed. Check your connection.") });
  const result = await fetchThreadsResult();
  assert.equal(result.ok, false);
  assert.match(result.error, /connection/);
});

test("a non-OK thread-list response maps to ok:false (no fabricated [])", async () => {
  setResponse({ ok: false, status: 503, json: async () => ({}) });
  const result = await fetchThreadsResult();
  assert.equal(result.ok, false);
  assert.match(result.error, /503/);
});

test("a genuinely empty thread list is ok:true with [] — empty ≠ failed", async () => {
  setResponse({ ok: true, status: 200, json: async () => [] });
  const result = await fetchThreadsResult();
  assert.equal(result.ok, true);
  assert.deepEqual(result.value, []);
});

test("fetchThreads keeps [] ONLY as the documented ChatView-compat wrapper", async () => {
  setResponse({ error: new Error("gateway down") });
  // Legacy contract (ChatView.tsx:185/257 relies on non-rejection):
  assert.deepEqual(await fetchThreads(), []);
  // …while the honest entry point still distinguishes the same failure:
  assert.equal((await fetchThreadsResult()).ok, false);
});

test("thread history failure is ok:false — never an empty conversation", async () => {
  setResponse({ error: new Error("gateway down") });
  const result = await fetchThreadHistoryResult("thread-1");
  assert.equal(result.ok, false);
  // Compat wrapper (ChatView.tsx:277 relies on non-rejection) stays [] only there.
  assert.deepEqual(await fetchThreadHistory("thread-1"), []);
});

test("thread history success maps run-event rows to messages", async () => {
  setResponse({
    ok: true,
    status: 200,
    json: async () => [
      { seq: 0, run_id: "r1", event_type: "human_message", content: { type: "human", content: "hello" }, created_at: "2026-01-01T00:00:00Z" },
    ],
  });
  const result = await fetchThreadHistoryResult("thread-1");
  assert.equal(result.ok, true);
  assert.equal(result.value.length, 1);
  assert.equal(result.value[0].role, "user");
  assert.equal(result.value[0].content, "hello");
});

/* ── comm/inbox/teamops: failures propagate instead of swallowing ──────── */

for (const [name, fn] of [
  ["listRooms", () => listRooms()],
  ["rollCall", () => rollCall()],
  ["listRoomRuns", () => listRoomRuns("room-1")],
  ["listDmThreads", () => listDmThreads("thread-1")],
  ["fetchRoster", () => fetchRoster("thread-1")],
  ["orgEvents", () => orgEvents(15)],
]) {
  test(`${name} rejects on gateway failure instead of returning []`, async () => {
    setHttpFail(true);
    try {
      await assert.rejects(fn, /gateway down/);
    } finally {
      setHttpFail(false);
    }
  });
}

/* ── Components carry the per-surface failure states ───────────────────── */

test("RunsSection never renders a failed run-detail load as zero counts", () => {
  const src = read("../components/sections/RunsSection.tsx");
  assert.doesNotMatch(src, /setDetail\(\{\s*messages:\s*0/);
  assert.match(src, /detailError/);
  assert.match(src, /detailError \? \(/);
});

test("MemorySection flags failed loads instead of substituting empty data", () => {
  const src = read("../components/sections/MemorySection.tsx");
  assert.doesNotMatch(src, /catch\(\(\) => \(\{ facts: \[\]/);
  assert.doesNotMatch(src, /\.catch\(\(\) => \{\}\)/);
  for (const flag of ["factsError", "overviewError", "workingError", "beliefsError", "skillsError"]) {
    assert.match(src, new RegExp(flag));
  }
});

test("KanbanSection surfaces project and server-board fetch failures", () => {
  const src = read("../components/sections/KanbanSection.tsx");
  assert.doesNotMatch(src, /\.catch\(\(\) => setProjects\(\[\]\)\)/);
  assert.match(src, /projectsError/);
  assert.match(src, /serverBoardDown/);
  assert.match(src, /showing local cards only/i);
});

test("MessagesSection tracks every list fetch failure with a flag", () => {
  const src = read("../components/sections/MessagesSection.tsx");
  assert.doesNotMatch(src, /orgEvents\(15\)\.catch\(\(\) => \[\]\)/);
  assert.doesNotMatch(src, /listDmThreads\(props\.threadId\)\.catch\(\(\) => \[\]/);
  assert.doesNotMatch(src, /fetchRoster\(props\.threadId\)\.catch\(\(\) => \[\]\)/);
  assert.doesNotMatch(src, /listRoomRuns\(props\.sel\.name\)\.then\(setRuns\)\.catch\(\(\) => setRuns\(\[\]\)\)/);
  for (const flag of ["roomsError", "dmsError", "rosterError", "presenceError", "eventsError", "runsError"]) {
    assert.match(src, new RegExp(flag));
  }
});

test("WorkspaceVitals uses three-state probe handling, not catch-to-empty", () => {
  const src = read("../components/WorkspaceVitals.tsx");
  assert.doesNotMatch(src, /probeAll\(\)\.catch\(\(\) => \[\]\)/);
  assert.match(src, /probesFailed/);
  assert.match(src, /Gateway status unavailable/);
  assert.match(src, /Subsystem status unavailable/);
});

test("api.ts documents the [] wrappers as ChatView-compat ONLY", () => {
  const src = read("./api.ts");
  // Each compat wrapper's immediately-preceding doc block must name the
  // LEGACY-COMPAT contract, the fenced caller, and the ONLY exemption.
  for (const fn of ["fetchThreads(", "fetchThreadHistory("]) {
    const idx = src.indexOf(`export async function ${fn}`);
    assert.ok(idx > 0, `missing ${fn}`);
    const doc = src.slice(Math.max(0, idx - 1000), idx);
    assert.match(doc, /LEGACY-COMPAT/);
    assert.match(doc, /ONLY/);
    assert.match(doc, /ChatView\.tsx/);
  }
  assert.match(src, /export async function fetchThreadsResult/);
  assert.match(src, /export async function fetchThreadHistoryResult/);
  // The compat wrappers delegate to the honest entry points — no second,
  // silently-swallowing implementation.
  assert.match(src, /const result = await fetchThreadsResult\(limit\);/);
  assert.match(src, /const result = await fetchThreadHistoryResult\(threadId\);/);
});
