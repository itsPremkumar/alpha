// runs.test.mjs — pins the run-inspection client: the REST event cursor walk
// (`after_seq`/`event_types`/`task_id`), the errors-only severity mapping, the
// per-run/per-call token + cost mapping, and the honesty inversions (a missing
// number stays `null`, a partial stream says `complete: false`, a failed read
// rejects instead of looking like "nothing was recorded").
// Pure Node test (node --test src/lib/runs.test.mjs): transpiles runs.ts and
// rewrites its ./http import to a data: URL stub — same pattern as
// goals.test.mjs.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const httpStub = `
let handler = (path, method, payload) => { throw new Error("no stub response configured"); };
export function setHttpHandler(fn) { handler = fn; }
export async function get(path) { return handler(path, "GET", undefined); }
export async function send(path, method, payload) { return handler(path, method, payload); }
export function pick(obj, keys, fallback) {
  if (obj && typeof obj === "object") {
    const rec = obj;
    for (const k of keys) { if (rec[k] !== undefined && rec[k] !== null) return rec[k]; }
  }
  return fallback;
}
export function asList(body, keys) {
  if (Array.isArray(body)) return body;
  for (const k of keys) {
    if (body && typeof body === "object" && Array.isArray(body[k])) return body[k];
  }
  return [];
}
export function errMsg(err) { return err instanceof Error ? err.message : "Something went wrong."; }
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
export const DEFAULT_TIMEOUT_MS = 60000;
export const GATEWAY_BASE = "/api";
`;
const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;
const stubUrl = toDataUrl(httpStub);

const source = readFileSync(new URL("./runs.ts", import.meta.url), "utf8");
let code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
code = code.replace(/from\s+"\.\/http"/, `from "${stubUrl}"`);

const runs = await import(toDataUrl(code));
const { setHttpHandler } = await import(stubUrl);

/** N synthetic run events, seq-ordered, ending at `from + n - 1`. */
function eventRows(from, n, eventType = "llm.ai.response") {
  return Array.from({ length: n }, (_, i) => ({
    seq: from + i,
    event_type: eventType,
    category: eventType === "run.error" ? "error" : "message",
    content: { type: "ai", content: `step ${from + i}` },
    metadata: { caller: "lead_agent" },
    created_at: "2026-01-01T00:00:00+00:00",
  }));
}

test("listThreadRuns keeps every token field the Gateway reports", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/threads/t-1/runs");
    assert.equal(method, "GET");
    return [
      {
        run_id: "r-1",
        thread_id: "t-1",
        status: "success",
        model: "vendor/model-a",
        created_at: "2026-01-01T00:00:00+00:00",
        updated_at: "2026-01-01T00:00:30+00:00",
        total_input_tokens: 100,
        total_output_tokens: 20,
        total_tokens: 120,
        llm_call_count: 2,
        lead_agent_tokens: 100,
        subagent_tokens: 0,
        middleware_tokens: 20,
        message_count: 4,
        stop_reason: null,
        error: null,
        token_usage_by_model: { "vendor/model-a": { input_tokens: 100, output_tokens: 20, total_tokens: 120, cache_read_tokens: 40 } },
      },
    ];
  });

  const list = await runs.listThreadRuns("t-1");
  assert.equal(list.length, 1);
  assert.equal(list[0].total_tokens, 120);
  assert.equal(list[0].llm_call_count, 2);
  assert.equal(list[0].message_count, 4);
  assert.deepEqual(list[0].token_usage_by_model, {
    "vendor/model-a": { input_tokens: 100, output_tokens: 20, total_tokens: 120, cache_read_tokens: 40 },
  });
});

test("an older Gateway that omits token totals maps them to unknown, not zero", async () => {
  setHttpHandler(() => [{ run_id: "r-legacy", thread_id: "t-1", status: "success", created_at: "x" }]);

  const [run] = await runs.listThreadRuns("t-1");
  assert.equal(run.total_tokens, null);
  assert.equal(run.total_input_tokens, null);
  assert.equal(run.llm_call_count, null);
  assert.equal(run.token_usage_by_model, null);
  // A real zero stays a real zero — only absent fields become null.
  setHttpHandler(() => [{ run_id: "r-zero", thread_id: "t-1", status: "success", total_tokens: 0, llm_call_count: 0 }]);
  const [zero] = await runs.listThreadRuns("t-1");
  assert.equal(zero.total_tokens, 0);
  assert.equal(zero.llm_call_count, 0);
  // An empty per-model split is "not reported", not an empty breakdown.
  assert.equal(zero.token_usage_by_model, null);
});

test("the event page walk sends limit and pages forward with after_seq", async () => {
  const paths = [];
  setHttpHandler((path) => {
    paths.push(path);
    if (paths.length === 1) return eventRows(1, 500);
    return eventRows(501, 3);
  });

  const page = await runs.fetchRunEventsPage("t-1", "r-1");
  assert.equal(paths[0], "/threads/t-1/runs/r-1/events?limit=500");
  assert.equal(paths[1], "/threads/t-1/runs/r-1/events?limit=500&after_seq=500");
  assert.equal(page.events.length, 503);
  assert.equal(page.events[0].seq, 1);
  assert.equal(page.events[502].seq, 503);
  assert.equal(page.events[0].event_type, "llm.ai.response");
  assert.equal(page.events[0].category, "message");
  assert.equal(page.complete, true);
});

test("the first page can start from a caller-supplied cursor", async () => {
  const paths = [];
  setHttpHandler((path) => {
    paths.push(path);
    return [];
  });

  const page = await runs.fetchRunEventsPage("t-1", "r-1", { afterSeq: 42 });
  assert.equal(paths[0], "/threads/t-1/runs/r-1/events?limit=500&after_seq=42");
  assert.deepEqual(page, { events: [], complete: true });
});

test("the errors-only read asks the server for the failure-bearing event types", async () => {
  const paths = [];
  setHttpHandler((path) => {
    paths.push(path);
    return [];
  });

  await runs.fetchRunEventsPage("t-1", "r-1", {
    eventTypes: runs.RUN_ISSUE_EVENT_TYPES,
    taskId: "task-7",
    limit: 100,
  });

  // Colons are percent-encoded per value; the CSV separator stays literal and
  // the Gateway decodes before splitting on ",".
  assert.equal(
    paths[0],
    "/threads/t-1/runs/r-1/events?limit=100&event_types=run.error,llm.error,subagent.end,middleware%3Asafety_termination,middleware%3Aloop_detection&task_id=task-7"
  );
});

test("a bounded walk reports complete:false instead of a short stream as the whole run", async () => {
  setHttpHandler(() => eventRows(1, 10));

  const page = await runs.fetchRunEventsPage("t-1", "r-1", { limit: 5, maxEvents: 10 });
  assert.equal(page.events.length, 10);
  assert.equal(page.complete, false);
});

test("a page without a usable seq stops the walk instead of re-reading it", async () => {
  let calls = 0;
  setHttpHandler(() => {
    calls += 1;
    return eventRows(1, 5).map((row) => ({ ...row, seq: null }));
  });

  const page = await runs.fetchRunEventsPage("t-1", "r-1", { limit: 5 });
  assert.equal(calls, 1);
  assert.equal(page.complete, false);
  assert.equal(page.events[0].seq, null);
});

test("fetchRunEvents still returns a flat list for simple callers", async () => {
  setHttpHandler(() => eventRows(1, 2));
  const events = await runs.fetchRunEvents("t-1", "r-1");
  assert.equal(events.length, 2);
  assert.equal(events[1].seq, 2);
});

test("severity separates failures from interventions from ordinary steps", () => {
  const ev = (event_type, content = {}, metadata = {}) => ({
    seq: 1,
    event_type,
    category: "message",
    content,
    metadata,
    created_at: null,
    task_id: null,
  });

  assert.equal(runs.runEventSeverity(ev("run.error", "boom")), "error");
  assert.equal(runs.runEventSeverity(ev("llm.error", "boom")), "error");
  assert.equal(runs.runEventSeverity(ev("subagent.end", { status: "failed" })), "error");
  assert.equal(runs.runEventSeverity(ev("subagent.end", { status: "timed_out" })), "error");
  assert.equal(runs.runEventSeverity(ev("subagent.end", { status: "cancelled" })), "warn");
  assert.equal(runs.runEventSeverity(ev("subagent.end", { status: "completed" })), "info");
  assert.equal(runs.runEventSeverity(ev("middleware:safety_termination", { action: "suppress_tool_calls" })), "warn");
  assert.equal(runs.runEventSeverity(ev("middleware:loop_detection", { action: "hard_stop" })), "error");
  assert.equal(runs.runEventSeverity(ev("middleware:loop_detection", { action: "warn" })), "warn");
  assert.equal(runs.runEventSeverity(ev("llm.ai.response", { content: "hi" })), "info");

  // The errors-only predicate is exactly severity === "error".
  assert.equal(runs.isRunIssueEvent(ev("subagent.end", { status: "completed" })), false);
  assert.equal(runs.isRunIssueEvent(ev("subagent.end", { status: "failed" })), true);
  assert.equal(runs.isRunIssueEvent(ev("middleware:loop_detection", { action: "hard_stop" })), true);
});

test("event summaries are bounded one-liners", () => {
  const ev = (content) => ({ seq: 1, event_type: "llm.ai.response", category: "message", content, metadata: {}, created_at: null, task_id: null });

  assert.equal(runs.summarizeRunEvent(ev("plain error text")), "plain error text");
  assert.equal(runs.summarizeRunEvent(ev({ text: "multi\n  line" })), "multi line");
  assert.equal(runs.summarizeRunEvent(ev({ tool_calls: [{ name: "write_file" }, { name: "bash" }] })), "tool call: write_file, bash");
  const long = runs.summarizeRunEvent(ev("x".repeat(500)), 40);
  assert.equal(long.length, 41);
  assert.match(long, /^x{40}…$/);
});

test("fetchRunUsage maps per-model and per-call usage, preserving absent cost", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/threads/t-1/runs/r-1/usage");
    assert.equal(method, "GET");
    return {
      run_id: "r-1",
      thread_id: "t-1",
      model: "vendor/model-a",
      status: "success",
      total_input_tokens: 600,
      total_output_tokens: 100,
      total_tokens: 700,
      llm_call_count: 2,
      lead_agent_tokens: 120,
      subagent_tokens: 580,
      middleware_tokens: 0,
      by_model: [
        { model: "vendor/model-a", input_tokens: 600, output_tokens: 100, total_tokens: 700, cache_read_tokens: 40, cost: 0.0042 },
        { model: "vendor/unpriced", input_tokens: 0, output_tokens: 0, total_tokens: 0, cache_read_tokens: null, cost: null },
      ],
      by_model_source: "per_model",
      calls: [
        {
          seq: 7,
          call_index: 1,
          source: "llm_response",
          caller: "lead_agent",
          model: "vendor/model-a",
          status: null,
          input_tokens: 100,
          output_tokens: 20,
          total_tokens: 120,
          cache_read_tokens: 40,
          latency_ms: 1200,
          cost: 0.0002,
          created_at: "2026-01-01T00:00:00+00:00",
        },
        {
          seq: 9,
          call_index: null,
          source: "subagent",
          caller: "subagent",
          task_id: "task-1",
          model: "vendor/model-c",
          status: "completed",
          input_tokens: 500,
          output_tokens: 80,
          total_tokens: 580,
          cache_read_tokens: null,
          latency_ms: null,
          cost: null,
          created_at: "2026-01-01T00:00:10+00:00",
        },
      ],
      calls_complete: true,
      total_cost: 0.0042,
      currency: "USD",
      pricing_configured: true,
    };
  });

  const usage = await runs.fetchRunUsage("t-1", "r-1");
  assert.equal(usage.total_tokens, 700);
  assert.equal(usage.llm_call_count, 2);
  assert.equal(usage.by_model_source, "per_model");
  assert.equal(usage.by_model[0].cache_read_tokens, 40);
  assert.equal(usage.by_model[0].cost, 0.0042);
  assert.equal(usage.by_model[1].cost, null);
  assert.equal(usage.calls.length, 2);
  assert.equal(usage.calls[0].source, "llm_response");
  assert.equal(usage.calls[0].latency_ms, 1200);
  assert.equal(usage.calls[1].source, "subagent");
  assert.equal(usage.calls[1].task_id, "task-1");
  assert.equal(usage.calls[1].cost, null);
  assert.equal(usage.calls_complete, true);
  assert.equal(usage.total_cost, 0.0042);
  assert.equal(usage.currency, "USD");
});

test("an unpriced or unreadable usage payload never becomes zero", async () => {
  setHttpHandler(() => ({ run_id: "r-1", thread_id: "t-1", by_model_source: "unavailable", calls_complete: false }));

  const usage = await runs.fetchRunUsage("t-1", "r-1");
  assert.equal(usage.total_tokens, null);
  assert.equal(usage.llm_call_count, null);
  assert.equal(usage.total_cost, null);
  assert.equal(usage.currency, null);
  assert.equal(usage.pricing_configured, false);
  assert.equal(usage.by_model_source, "unavailable");
  assert.deepEqual(usage.by_model, []);
  assert.deepEqual(usage.calls, []);
  assert.equal(usage.calls_complete, false);
});

test("formatTokenCount keeps unknown distinct from zero", () => {
  assert.equal(runs.formatTokenCount(null), "—");
  assert.equal(runs.formatTokenCount(0), "0");
  assert.equal(runs.formatTokenCount(999), "999");
  assert.equal(runs.formatTokenCount(1500), "1.5k");
  assert.equal(runs.formatTokenCount(2_500_000), "2.50M");
});

test("formatCost returns null for an unmeasured cost, never 0", () => {
  assert.equal(runs.formatCost(null, "USD"), null);
  assert.equal(runs.formatCost(null, null), null);
  assert.equal(runs.formatCost(0, "USD"), "USD 0.00");
  assert.equal(runs.formatCost(0.0042, "USD"), "USD 0.004200");
  assert.equal(runs.formatCost(12.5, "USD"), "USD 12.50");
  assert.equal(runs.formatCost(1.25, null), "1.25");
});

test("a failed read rejects instead of looking like an empty or zero run", async () => {
  setHttpHandler(() => {
    throw new Error("gateway down");
  });

  await assert.rejects(() => runs.fetchRunUsage("t-1", "r-1"), /gateway down/);
  await assert.rejects(() => runs.fetchRunEventsPage("t-1", "r-1"), /gateway down/);
  await assert.rejects(() => runs.listThreadRuns("t-1"), /gateway down/);
});
