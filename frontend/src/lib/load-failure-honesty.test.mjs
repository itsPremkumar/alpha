// load-failure-honesty.test.mjs — wave-2 audit pins (batch S1-C).
//
// A FAILED fetch must never be rendered as an empty/zero success:
//  * api.ts exposes FetchResult-based entry points where ok:false is
//    distinguishable from a genuinely empty list; the legacy [] wrappers are
//    pinned as the explicit legacy-compatibility path only.
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
export class ApiClientError extends Error {
  constructor(kind, status, detail) {
    super(detail || "Request failed");
    this.name = "ApiClientError";
    this.kind = kind;
    this.status = status;
  }
}
let next = { ok: true, json: async () => [] };
let queue = [];
export function setResponse(r) { next = r; queue = []; }
export function setResponses(responses) { queue = [...responses]; }
export async function apiFetch(_path, init) {
  const r = queue.length > 0 ? queue.shift() : next;
  if (r.error) throw r.error;
  // A response that never arrives stands in for a hung server connection:
  // only the caller's own abort deadline can end this request.
  if (r.pending) {
    return new Promise((_resolve, reject) => {
      const signal = init && init.signal;
      if (!signal) return;
      if (signal.aborted) { reject(new Error("aborted")); return; }
      signal.addEventListener("abort", () => reject(new Error("aborted")));
    });
  }
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
const { setResponse, setResponses } = await import(apiClientStubUrl);

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

test("thread list follows every offset page instead of stopping at the old ceiling", async () => {
  const firstPage = Array.from({ length: 200 }, (_, index) => ({
    thread_id: `thread-${index + 1}`,
    metadata: { title: `Conversation ${index + 1}` },
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  }));
  setResponses([
    { ok: true, status: 200, json: async () => firstPage },
    { ok: true, status: 200, json: async () => [] },
  ]);
  const result = await fetchThreadsResult();
  assert.equal(result.ok, true);
  assert.equal(result.value.length, 200);
  assert.equal(result.value.at(-1).thread_id, "thread-200");
});

test("fetchThreads keeps [] ONLY as the documented legacy compatibility wrapper", async () => {
  setResponse({ error: new Error("gateway down") });
  // Legacy contract retained for older external callers:
  assert.deepEqual(await fetchThreads(), []);
  // …while the honest entry point still distinguishes the same failure:
  assert.equal((await fetchThreadsResult()).ok, false);
});

test("thread history failure is ok:false — never an empty conversation", async () => {
  setResponse({ error: new Error("gateway down") });
  const result = await fetchThreadHistoryResult("thread-1");
  assert.equal(result.ok, false);
  // Compatibility wrapper stays [] only for older external callers.
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

test("thread history follows every backward page and restores chronological order", async () => {
  setResponses([
    {
      ok: true,
      status: 200,
      json: async () => ({
        data: [
          { seq: 20, run_id: "r2", event_type: "human_message", content: { type: "human", content: "newest" }, created_at: "2026-01-02T00:00:00Z" },
        ],
        has_more: true,
        next_before_seq: 20,
      }),
    },
    {
      ok: true,
      status: 200,
      json: async () => ({
        data: [
          { seq: 10, run_id: "r1", event_type: "llm.ai.response", content: { type: "ai", content: "oldest answer" }, created_at: "2026-01-01T00:00:00Z" },
        ],
        has_more: false,
        next_before_seq: null,
      }),
    },
  ]);
  const result = await fetchThreadHistoryResult("thread-1");
  assert.equal(result.ok, true);
  assert.deepEqual(result.value.map((message) => message.content), ["oldest answer", "newest"]);
});

test("a truncated thread page is partial history, flagged as such, not a complete answer", async () => {
  setResponses([
    {
      ok: true,
      status: 200,
      json: async () => ({
        data: [
          { seq: 20, run_id: "r2", event_type: "human_message", content: { type: "human", content: "newest" }, created_at: "2026-01-02T00:00:00Z" },
        ],
        has_more: true,
        next_before_seq: 20,
      }),
    },
    { ok: false, status: 503, json: async () => ({}) },
  ]);
  const result = await fetchThreadHistoryResult("thread-1");
  assert.equal(result.ok, true);
  // The rows that did arrive are real history and must not be discarded.
  assert.equal(result.value.length, 1);
  assert.equal(result.value[0].content, "newest");
  // …but the truncation is disclosed, with the cursor to resume from.
  assert.match(result.incomplete, /503/);
  assert.equal(result.resumeCursor, 20);
});

test("a truncated history resumes from the oldest accepted page, not the newest", async () => {
  const page = (seq, next, hasMore) => ({
    ok: true,
    status: 200,
    json: async () => ({
      data: [{ seq, run_id: `r${seq}`, event_type: "human_message", content: { type: "human", content: `m${seq}` } }],
      has_more: hasMore,
      next_before_seq: next,
    }),
  });
  // Newest page 90 -> 80 -> 70 accepted, then the walk fails.
  setResponses([page(90, 80, true), page(80, 70, true), page(70, 60, true), { ok: false, status: 503, json: async () => ({}) }]);
  const result = await fetchThreadHistoryResult("thread-1");
  assert.equal(result.ok, true);
  assert.deepEqual(result.value.map((m) => m.content), ["m70", "m80", "m90"]);
  // 60 is the cursor the server handed back WITH page 70, i.e. exactly where
  // the next (older) page starts. The newest page's cursor (80) would re-fetch
  // page 70's range, and the rejected page's own cursor (70) would skip it.
  assert.equal(result.resumeCursor, 60);
  assert.match(result.incomplete, /503/);
});

test("a non-decreasing history cursor is reported instead of looping forever", async () => {
  const page = {
    ok: true,
    status: 200,
    json: async () => ({
      data: [{ seq: 20, run_id: "r1", event_type: "human_message", content: { type: "human", content: "x" } }],
      has_more: true,
      next_before_seq: 999,
    }),
  };
  setResponses([page, page, page]);
  const result = await fetchThreadHistoryResult("thread-1");
  // The already-received rows are kept, and the bad cursor stops the walk.
  assert.equal(result.ok, true);
  assert.equal(result.value.length, 1);
  assert.match(result.incomplete, /non-decreasing or invalid cursor/);
});

test("a malformed 200 message envelope is a failure, not an empty conversation", async () => {
  setResponse({ ok: true, status: 200, json: async () => ({ unexpected: "shape" }) });
  const result = await fetchThreadHistoryResult("thread-1");
  assert.equal(result.ok, false);
  assert.match(result.error, /unreadable message page/);
});

test("a malformed 200 thread-list envelope is a failure, not an empty history", async () => {
  setResponse({ ok: true, status: 200, json: async () => ({ unexpected: "shape" }) });
  const result = await fetchThreadsResult();
  assert.equal(result.ok, false);
  assert.match(result.error, /unreadable thread list/);
});

test("a thread record without an id is rejected rather than listed as a broken row", async () => {
  setResponse({ ok: true, status: 200, json: async () => [{ metadata: { title: "no id" } }] });
  const result = await fetchThreadsResult();
  assert.equal(result.ok, false);
  assert.match(result.error, /invalid thread record/);
});

test("a truncated thread list returns the rows that loaded and discloses the gap", async () => {
  const firstPage = Array.from({ length: 200 }, (_, index) => ({
    thread_id: `thread-${index + 1}`,
    metadata: { title: `Conversation ${index + 1}` },
    updated_at: "2026-01-01T00:00:00Z",
  }));
  setResponses([
    { ok: true, status: 200, json: async () => firstPage },
    { ok: false, status: 503, json: async () => ({}) },
  ]);
  const result = await fetchThreadsResult();
  assert.equal(result.ok, true);
  assert.equal(result.value.length, 200);
  assert.match(result.incomplete, /503/);
  assert.equal(result.resumeCursor, 200);
});

test("a page request that never answers fails on a deadline instead of hanging the UI", async () => {
  // A promise that never settles stands in for a hung server connection.
  setResponse({ pending: true });
  const started = Date.now();
  const result = await fetchThreadsResult();
  assert.equal(result.ok, false);
  assert.match(result.error, /timed out after 30 seconds/);
  assert.ok(Date.now() - started < 35_000);
});

test("every fetch result surface is bounded, not silently truncated", () => {
  const src = read("./api.ts");
  assert.match(src, /PAGE_REQUEST_TIMEOUT_MS = 30_000/);
  // A bounded page walk is fine; a bounded total is the old data-loss bug.
  assert.doesNotMatch(src, /\.slice\(0,\s*100\)/);
  assert.doesNotMatch(src, /\.slice\(-100\)/);
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

test("api.ts documents the [] wrappers as legacy-only compatibility", () => {
  const src = read("./api.ts");
  // Each compat wrapper's immediately-preceding doc block must name the
  // LEGACY-COMPAT contract and direct new code to the honest result entry point.
  for (const fn of ["fetchThreads(", "fetchThreadHistory("]) {
    const idx = src.indexOf(`export async function ${fn}`);
    assert.ok(idx > 0, `missing ${fn}`);
    const doc = src.slice(Math.max(0, idx - 1000), idx);
    assert.match(doc, /LEGACY-COMPAT/);
    assert.match(doc, fn.startsWith("fetchThreads")
      ? /fetchThreadsResult\(\)/
      : /fetchThreadHistoryResult\(\)/);
  }
  assert.match(src, /export async function fetchThreadsResult/);
  assert.match(src, /export async function fetchThreadHistoryResult/);
  // The compat wrappers delegate to the honest entry points — no second,
  // silently-swallowing implementation.
  assert.match(src, /const result = await fetchThreadsResult\(limit\);/);
  assert.match(src, /const result = await fetchThreadHistoryResult\(threadId\);/);
});
