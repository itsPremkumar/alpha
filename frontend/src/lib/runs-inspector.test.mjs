// runs-inspector.test.mjs — the run inspector's real contract.
//
// What is pinned here:
//
//  * the EXACT paths and verbs the inspector issues, one per read, so a route
//    typo or a swapped verb fails here instead of silently 404ing in the UI;
//  * the envelope mapping, fed the JSON this Gateway actually returns for real
//    runs (a successful run with tool calls, and a failed run whose `run.end`
//    event says `success` while the run record says `error`);
//  * the honesty inversions, asserted against RENDERED MARKUP produced by
//    `react-dom/server` from the real client output — not against a regex over
//    the source. A missing field must render as unknown; a 500 must surface the
//    server's reason instead of an empty list; an unknown status string must
//    pass through untouched; a 429 must not read as "no runs".
//
// The transport is stubbed; everything above it is the real code.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const here = (relative) => fileURLToPath(new URL(relative, import.meta.url));
const resolveUrl = (specifier) => pathToFileURL(require.resolve(specifier)).href;
const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");

/**
 * Transpiled modules are written to a temp directory and imported as real
 * files, not as nested `data:` URLs: this suite stacks five modules deep
 * (component -> client -> runs -> http stub), and that nesting does not survive
 * the data-URL scheme — a nested import silently fails to resolve its bindings.
 * Relative specifiers are rewritten to sibling files, so Node resolves the
 * module graph exactly as it would for the app.
 */
const OUT_DIR = join(tmpdir(), `alpha-run-inspector-test-${process.pid}`);
mkdirSync(OUT_DIR, { recursive: true });

/** Transpile a repo source file, rewriting its specifiers to real modules. */
function compile(relative, { jsx = false, specifiers = {} } = {}) {
  let code = ts.transpileModule(read(relative), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: jsx ? ts.JsxEmit.ReactJSX : undefined },
  }).outputText;
  for (const [from, to] of Object.entries(specifiers)) {
    code = code.replace(new RegExp(`(from\\s+)"${from.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"`, "g"), `$1"${to}"`);
  }
  return code;
}

/** Write a transpiled module next to its siblings and return its file URL. */
function emit(name, code) {
  writeFileSync(join(OUT_DIR, `${name}.mjs`), code, "utf8");
  return `./${name}.mjs`;
}
const loadFile = (name) => import(pathToFileURL(join(OUT_DIR, `${name}.mjs`)).href);

/* ── the stubbed transport ─────────────────────────────────────────────────── */

// Mirrors `api-client.ts`: a non-2xx becomes an error carrying the Gateway's
// own `detail`, so the assertions below test the real failure surface rather
// than a convenient string.
const httpStub = `
let handler = (path) => { throw new Error("no stub response configured for " + path); };
export function setHttpHandler(fn) { handler = fn; }
export function setHttpFailure(status, detail) {
  handler = () => { throw new ApiError(status, "Request failed (HTTP " + status + ")." + (detail ? " " + detail : "")); };
}
export async function get(path) { return handler(path, "GET", undefined); }
export async function send(path, method, payload) { return handler(path, method, payload); }
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
export function errMsg(err) { return err instanceof Error ? err.message : "Something went wrong."; }
export function pick(obj, keys, fallback) {
  if (obj && typeof obj === "object") {
    for (const k of keys) { if (obj[k] !== undefined && obj[k] !== null) return obj[k]; }
  }
  return fallback;
}
export function asList(body, keys) {
  if (Array.isArray(body)) return body;
  for (const k of keys) { if (body && typeof body === "object" && Array.isArray(body[k])) return body[k]; }
  return [];
}
export const DEFAULT_TIMEOUT_MS = 60000;
export const GATEWAY_BASE = "/api";
`;
const httpRef = emit("http", httpStub);
const { setHttpHandler, setHttpFailure } = await loadFile("http");

/* ── the real modules, above the stub ──────────────────────────────────────── */

const sseRef = emit("sse-reducer", compile("./sse-reducer.ts"));
const runsRef = emit("runs", compile("./runs.ts", { specifiers: { "./http": httpRef } }));
const inspectorRef = emit(
  "runs-inspector",
  compile("./runs-inspector.ts", { specifiers: { "./http": httpRef, "./runs": runsRef, "./sse-reducer": sseRef } })
);
const inspector = await loadFile("runs-inspector");

/* ── the real component, rendered ──────────────────────────────────────────── */

const jsxRuntime = { react: resolveUrl("react"), "react/jsx-runtime": resolveUrl("react/jsx-runtime") };
const lucide = pathToFileURL(here("../../node_modules/lucide-react/dist/esm/lucide-react.js")).href;
const timeRef = emit("time", compile("./time.ts"));
const uiRef = emit("ui", compile("../components/ui.tsx", { jsx: true, specifiers: jsxRuntime }));
const toolPillRef = emit(
  "ToolPill",
  compile("../components/ToolPill.tsx", { jsx: true, specifiers: { ...jsxRuntime, "lucide-react": lucide } })
);
// The picker filter and the timeline filter each own a pure client module, and
// the timeline also moved into its own component so it can own its controls.
const pickerRef = emit(
  "runs-inspector-picker",
  compile("./runs-inspector-picker.ts", { specifiers: { "./runs-inspector": inspectorRef, "./runs": runsRef } })
);
const timelineModuleRef = emit(
  "runs-inspector-timeline",
  compile("./runs-inspector-timeline.ts", { specifiers: { "./runs-inspector": inspectorRef, "./runs": runsRef } })
);
const timelineRef = emit(
  "RunInspectorTimeline",
  compile("../components/sections/RunInspectorTimeline.tsx", {
    jsx: true,
    specifiers: {
      ...jsxRuntime,
      "lucide-react": lucide,
      "@/lib/http": httpRef,
      "@/lib/time": timeRef,
      "@/lib/runs": runsRef,
      "@/lib/runs-inspector": inspectorRef,
      "@/lib/runs-inspector-timeline": timelineModuleRef,
      "@/components/ui": uiRef,
    },
  })
);
const componentRef = emit(
  "RunInspectorSection",
  compile("../components/sections/RunInspectorSection.tsx", {
    jsx: true,
    specifiers: {
      ...jsxRuntime,
      "lucide-react": lucide,
      "@/lib/http": httpRef,
      "@/lib/time": timeRef,
      "@/lib/runs": runsRef,
      "@/lib/runs-inspector": inspectorRef,
      "@/lib/runs-inspector-picker": pickerRef,
      "@/components/ui": uiRef,
      "@/components/ToolPill": toolPillRef,
      "./RunInspectorTimeline": timelineRef,
    },
  })
);
const { RunInspectorView, RunStatusPanel, ToolCallsPanel, WorkspacePanel, DeliveryPanel } =
  await loadFile("RunInspectorSection");
const { RunInspectorTimeline } = await loadFile("RunInspectorTimeline");
const { createElement } = await import(jsxRuntime.react);
const { renderToStaticMarkup } = await import(resolveUrl("react-dom/server"));

/** Route a stub by path suffix; anything else fails loudly. */
function route(table) {
  const calls = [];
  setHttpHandler((path, method) => {
    calls.push(`${method} ${path}`);
    for (const [suffix, value] of Object.entries(table)) {
      if (path.endsWith(suffix)) return typeof value === "function" ? value(path) : value;
    }
    throw new Error(`unexpected request: ${method} ${path}`);
  });
  return calls;
}

function renderView(state) {
  return renderToStaticMarkup(createElement(RunInspectorView, { state, onSelect() {}, onReload() {} }));
}

const BASE_STATE = {
  threadId: "t-1",
  runs: [],
  runsComplete: true,
  runsError: null,
  selectedRunId: "r-1",
  record: null,
  transcript: null,
  timeline: null,
  workspace: null,
  usage: null,
  manifest: null,
  errors: {},
  loadingRun: false,
  showPicker: false,
};

/* ── payloads recorded from this Gateway ────────────────────────────────────── */

const SUCCESS_RUN = {
  run_id: "e528f5a6-2c82-4959-aafd-a73da0996468",
  thread_id: "c10110de-79ae-4f88-8049-2b50c3436e82",
  assistant_id: "lead_agent",
  status: "success",
  metadata: { alpha_trace_id: "4e22ce768efb4ba8853515995cb95341" },
  kwargs: {
    input: { messages: [{ role: "user", content: "Use the bash tool to run `echo alpha-inspector-probe`" }] },
    config: { configurable: { model_name: "alpha-free" } },
  },
  multitask_strategy: "reject",
  created_at: "2026-09-28T15:07:39.158838+00:00",
  updated_at: "2026-09-28T15:08:30.372347+00:00",
  model: "alpha-free",
  error: null,
  total_input_tokens: 52992,
  total_output_tokens: 252,
  total_tokens: 53244,
  llm_call_count: 2,
  lead_agent_tokens: 53244,
  subagent_tokens: 0,
  middleware_tokens: 0,
  message_count: 4,
  stop_reason: null,
  token_usage_by_model: { "kilo:kilo-auto/free": { input_tokens: 52992, output_tokens: 252, total_tokens: 53244 } },
};

const FAILED_RUN = {
  run_id: "077c8cc1-5de8-4c50-bb2c-27ad1a62118a",
  thread_id: "653d0320-8809-4368-8bac-3e6f87e304d6",
  assistant_id: null,
  status: "error",
  metadata: { alpha_trace_id: "e600c19c50174d1b88047a0bb38422c8" },
  kwargs: { input: { messages: [{ role: "user", content: "What is 17 * 23? Reply with only the number." }] }, config: null },
  multitask_strategy: "reject",
  created_at: "2026-09-28T14:53:29.158838+00:00",
  updated_at: "2026-09-28T14:53:35.789943+00:00",
  model: "union-alpha",
  error: "Error code: 401 - Failed to authenticate request with Clerk\nAutomatic recovery will not replay a non-transient model failure (generic).",
  total_input_tokens: 0,
  total_output_tokens: 0,
  total_tokens: 0,
  llm_call_count: 0,
  lead_agent_tokens: 0,
  subagent_tokens: 0,
  middleware_tokens: 0,
  message_count: 1,
  stop_reason: "recovery_blocked",
  token_usage_by_model: null,
};

/** The failed run's own `run.end` event, whose metadata contradicts the record. */
const FAILED_RUN_EVENTS = [
  { seq: 1, event_type: "run.start", category: "trace", content: null, metadata: { caller: "lead_agent", model_name: "union-alpha" }, created_at: "2026-09-28T14:53:31.035441+00:00" },
  { seq: 2, event_type: "llm.human.input", category: "message", content: { content: "What is 17 * 23? Reply with only the number.", type: "human" }, metadata: { caller: "lead_agent" }, created_at: "2026-09-28T14:53:31.456069+00:00" },
  { seq: 3, event_type: "llm.error", category: "trace", content: { error: "401 unauthorized" }, metadata: {}, created_at: "2026-09-28T14:53:31.852597+00:00" },
  // The graph finished, so the event says success. The run did not succeed.
  { seq: 4, event_type: "run.end", category: "outputs", content: null, metadata: { status: "success" }, created_at: "2026-09-28T14:53:32.786802+00:00" },
  { seq: 5, event_type: "run.delivery", category: "outputs", content: null, metadata: {}, created_at: "2026-09-28T14:53:32.841115+00:00" },
];

const TOOL_MESSAGES = [
  {
    seq: 2,
    event_type: "llm.human.input",
    category: "message",
    content: { content: "Use the bash tool to run echo alpha-inspector-probe", type: "human", id: "f2ec__user" },
    metadata: { caller: "lead_agent" },
    created_at: "2026-09-28T15:07:41.777571+00:00",
  },
  {
    seq: 3,
    event_type: "llm.ai.response",
    category: "message",
    content: {
      content: "",
      type: "ai",
      id: "lc_run--01a0",
      response_metadata: { model_name: "kilo:kilo-auto/free", latency_ms: 12456.9 },
      tool_calls: [{ name: "python_repl", args: { code: "print('alpha-inspector-probe')" }, id: "90c3a830", type: "tool_call" }],
    },
    metadata: { caller: "lead_agent", llm_call_index: 1 },
    created_at: "2026-09-28T15:07:54.278320+00:00",
  },
  {
    seq: 4,
    event_type: "llm.tool.result",
    category: "message",
    content: {
      content: "[stdout]\nalpha-inspector-probe",
      type: "tool",
      name: "python_repl",
      tool_call_id: "90c3a830",
      artifact: null,
      status: "success",
    },
    metadata: {},
    created_at: "2026-09-28T15:07:55.596175+00:00",
  },
  {
    seq: 5,
    event_type: "llm.ai.response",
    category: "message",
    content: { content: "It printed alpha-inspector-probe.", type: "ai", id: "lc_run--01a1", response_metadata: { model_name: "kilo:kilo-auto/free" }, tool_calls: [] },
    metadata: { caller: "lead_agent" },
    created_at: "2026-09-28T15:08:00.695018+00:00",
  },
];

/** A `read_file` of a missing file: journalled `success`, output reads as an error. */
const CONFLICTED_MESSAGES = [
  {
    seq: 5,
    event_type: "llm.ai.response",
    category: "message",
    content: { content: "", type: "ai", id: "ai-2", tool_calls: [{ name: "read_file", args: { path: "/mnt/user-data/outputs/missing.txt" }, id: "call-read", type: "tool_call" }] },
    metadata: { caller: "lead_agent" },
    created_at: "2026-09-28T15:10:05.000000+00:00",
  },
  {
    seq: 6,
    event_type: "llm.tool.result",
    category: "message",
    content: {
      content: "Error: File not found: /mnt/user-data/outputs/missing.txt",
      type: "tool",
      name: "read_file",
      tool_call_id: "call-read",
      artifact: null,
      // The journalled status really is "success"; the run's own tool meta
      // (alpha_tool_meta) records "error" and is not carried on this row.
      status: "success",
    },
    metadata: {},
    created_at: "2026-09-28T15:10:17.164037+00:00",
  },
];

const DELIVERY_EVENTS = [
  { seq: 12, event_type: "workspace_changes", category: "workspace", content: "1 file changed +1 -0", metadata: {}, created_at: "2026-09-28T15:10:41.110189+00:00" },
  {
    seq: 13,
    event_type: "run.delivery",
    category: "outputs",
    content: {
      presented: 1,
      paths: ["/mnt/user-data/outputs/inspector-demo.md"],
      by_tool: { present_files: ["/mnt/user-data/outputs/inspector-demo.md"] },
      verification: { source: "outputs_changed", requirement: "present_files_matches_produced_output" },
      produced_paths: ["/mnt/user-data/outputs/inspector-demo.md"],
      presented_paths: ["/mnt/user-data/outputs/inspector-demo.md"],
      matched_paths: ["/mnt/user-data/outputs/inspector-demo.md"],
      stage: "presented",
      satisfied: true,
    },
    metadata: {},
    created_at: "2026-09-28T15:10:41.163142+00:00",
  },
];

const WORKSPACE_AVAILABLE = {
  available: true,
  version: 1,
  summary: { created: 1, modified: 0, deleted: 0, symlink_created: 0, additions: 1, deletions: 0, truncated: false },
  files: [
    {
      path: "/mnt/user-data/outputs/inspector-demo.md",
      root: "outputs",
      status: "created",
      binary: false,
      sensitive: false,
      size_before: null,
      size_after: 16,
      diff: "--- a/mnt/user-data/outputs/inspector-demo.md\n+++ b/mnt/user-data/outputs/inspector-demo.md\n@@ -0,0 +1 @@\n+inspector demo",
      diff_truncated: false,
      diff_unavailable_reason: null,
      additions: 1,
      deletions: 0,
    },
  ],
  limits: { max_files: 200 },
};

const WORKSPACE_UNAVAILABLE = { available: false, version: 1, summary: { created: 0, modified: 0, deleted: 0, symlink_created: 0, additions: 0, deletions: 0, truncated: false }, files: [], limits: {} };

const THREAD_USAGE = {
  thread_id: "c10110de-79ae-4f88-8049-2b50c3436e82",
  total_tokens: 53244,
  total_input_tokens: 52992,
  total_output_tokens: 252,
  total_runs: 1,
  by_model: { "kilo:kilo-auto/free": { tokens: 53244, runs: 1 } },
  by_caller: { lead_agent: 53244, subagent: 0, middleware: 0 },
  context_usage: { token_count: 1204, max_context_tokens: 262144, percentage: 0.5 },
};

/* ══ 1. The exact reads: one path, one verb, per panel ═══════════════════════ */

test("each panel issues exactly one documented GET, with no invented query", async () => {
  const calls = route({
    "/runs/page?limit=50": { data: [SUCCESS_RUN], has_more: false, next_before_created_at: null, next_before_run_id: null },
    "/runs/r-1": SUCCESS_RUN,
    "/runs/r-1/messages?limit=100": { data: TOOL_MESSAGES, has_more: false },
    "/runs/r-1/events?limit=500": TOOL_MESSAGES,
    "/runs/r-1/workspace-changes": WORKSPACE_AVAILABLE,
    "/runs/r-1/artifacts/archive": { file_count: 1 },
    "/token-usage": THREAD_USAGE,
  });

  await inspector.fetchRunPage("t-1");
  await inspector.fetchRunRecord("t-1", "r-1");
  await inspector.fetchRunTranscript("t-1", "r-1");
  await inspector.fetchRunTimeline("t-1", "r-1");
  await inspector.fetchWorkspaceChangesForRun("t-1", "r-1");
  await inspector.fetchArtifactArchiveManifest("t-1", "r-1");
  await inspector.fetchThreadTokenUsage("t-1");

  assert.deepEqual(calls, [
    "GET /threads/t-1/runs/page?limit=50",
    "GET /threads/t-1/runs/r-1",
    "GET /threads/t-1/runs/r-1/messages?limit=100",
    "GET /threads/t-1/runs/r-1/events?limit=500",
    "GET /threads/t-1/runs/r-1/workspace-changes",
    "GET /threads/t-1/runs/r-1/artifacts/archive",
    "GET /threads/t-1/token-usage",
  ]);
});

test("thread and run ids are encoded into the path, never interpolated raw", async () => {
  const calls = route({ "/runs/r%201": SUCCESS_RUN, "/messages?limit=100": { data: [], has_more: false } });
  await inspector.fetchRunRecord("t 1/../x", "r 1");
  await inspector.fetchRunTranscript("t 1/../x", "r 1");
  assert.deepEqual(calls, [
    "GET /threads/t%201%2F..%2Fx/runs/r%201",
    "GET /threads/t%201%2F..%2Fx/runs/r%201/messages?limit=100",
  ]);
});

/* ══ 2. The run record ═════════════════════════════════════════════════════ */

test("a successful run record maps its prompt, model, tokens and per-model split", async () => {
  route({ "/runs/r-1": SUCCESS_RUN });
  const record = await inspector.fetchRunRecord("t-1", "r-1");
  assert.equal(record.status, "success");
  assert.equal(record.model, "alpha-free");
  assert.equal(record.error, null);
  assert.equal(record.stop_reason, null);
  assert.equal(record.trace_id, "4e22ce768efb4ba8853515995cb95341");
  assert.equal(record.total_tokens, 53244);
  assert.equal(record.message_count, 4);
  assert.deepEqual(record.input, [{ role: "user", text: "Use the bash tool to run `echo alpha-inspector-probe`" }]);
  assert.equal(record.input_readable, true);
  assert.deepEqual(record.token_usage_by_model, [
    { model: "kilo:kilo-auto/free", input: 52992, output: 252, total: 53244 },
  ]);
});

test("a failed run keeps its own error, stop reason and real zero token counts", async () => {
  route({ "/runs/r-1": FAILED_RUN });
  const record = await inspector.fetchRunRecord("t-1", "r-1");
  assert.equal(record.status, "error");
  assert.equal(record.stop_reason, "recovery_blocked");
  assert.match(record.error, /401/);
  // A real measured zero stays zero; a real null stays null.
  assert.equal(record.total_tokens, 0);
  assert.equal(record.llm_call_count, 0);
  assert.equal(record.token_usage_by_model, null);
});

test("an unreported field is null, never 0, \"\" or false", async () => {
  route({ "/runs/r-1": { run_id: "r-1" } });
  const record = await inspector.fetchRunRecord("t-1", "r-1");
  for (const key of [
    "status",
    "model",
    "error",
    "stop_reason",
    "created_at",
    "updated_at",
    "assistant_id",
    "trace_id",
    "total_tokens",
    "total_input_tokens",
    "llm_call_count",
    "lead_agent_tokens",
    "subagent_tokens",
    "middleware_tokens",
    "message_count",
  ]) {
    assert.equal(record[key], null, `${key} must be unknown, not coerced`);
  }
  assert.equal(record.input, null);
  assert.equal(record.input_readable, false);
  assert.equal(record.token_usage_by_model, null);
});

test("an unknown status string is preserved verbatim and never snapped to a known one", async () => {
  for (const status of ["quiesced_by_operator", "PARTIALLY_COMPLETED", "success ", "verified"]) {
    route({ "/runs/r-1": { run_id: "r-1", status } });
    const record = await inspector.fetchRunRecord("t-1", "r-1");
    assert.equal(record.status, status, `${status} must survive the mapping untouched`);
    assert.equal(inspector.isTerminalSuccess(record.status), status === "success");
    assert.equal(inspector.isActive(record.status), false, `${status} is not one of the active statuses`);
    assert.equal(inspector.terminalStatusLabel(record.status), status);
  }
  // A missing status is unknown — never mapped to `running`, never to success.
  assert.equal(inspector.isActive(null), false);
  assert.equal(inspector.isTerminalSuccess(null), false);
  assert.equal(inspector.terminalStatusLabel(null), "terminal status not reported");
});

/* ══ 3. The transcript, the prompt and the answer ═══════════════════════════ */

test("the transcript maps the prompt, the tool call and the final answer in order", async () => {
  route({ "/messages?limit=100": { data: TOOL_MESSAGES, has_more: false } });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");
  assert.equal(transcript.complete, true);
  assert.deepEqual(transcript.entries.map((e) => e.eventType), [
    "llm.human.input",
    "llm.ai.response",
    "llm.tool.result",
    "llm.ai.response",
  ]);
  assert.deepEqual(transcript.entries.map((e) => e.kind), ["prompt", "answer", "tool_result", "answer"]);
  assert.equal(transcript.entries[0].text, "Use the bash tool to run echo alpha-inspector-probe");
  assert.equal(transcript.entries[0].caller, "lead_agent");
  assert.equal(transcript.entries[1].model, "kilo:kilo-auto/free");
  assert.equal(inspector.transcriptPrompts(transcript).length, 1);
  const answer = inspector.finalAnswer(transcript);
  assert.equal(answer.text, "It printed alpha-inspector-probe.");
  assert.equal(answer.seq, 5);
});

test("an AI row that only issued tool calls is not the final answer", async () => {
  const onlyCalls = { data: TOOL_MESSAGES.slice(0, 3), has_more: false };
  route({ "/messages?limit=100": onlyCalls });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");
  // seq 3 is an `ai` row with empty text and a tool call: the answer is absent,
  // not a blank bubble.
  assert.equal(inspector.finalAnswer(transcript), null);
  assert.equal(inspector.transcriptPrompts(transcript).length, 1);
});

test("the transcript walk follows after_seq and reports a truncated read", async () => {
  const calls = [];
  const first = Array.from({ length: 100 }, (_, i) => ({ ...TOOL_MESSAGES[0], seq: i + 1 }));
  setHttpHandler((path) => {
    calls.push(path);
    if (calls.length === 1) return { data: first, has_more: true };
    return { data: TOOL_MESSAGES, has_more: false };
  });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");
  assert.equal(calls[0], "/threads/t-1/runs/r-1/messages?limit=100");
  assert.equal(calls[1], "/threads/t-1/runs/r-1/messages?limit=100&after_seq=100");
  assert.equal(transcript.entries.length, 104);
  assert.equal(transcript.complete, true);
});

test("a page with no usable seq stops the walk instead of looping or 422ing", async () => {
  let calls = 0;
  setHttpHandler(() => {
    calls += 1;
    return { data: [{ ...TOOL_MESSAGES[0], seq: null }], has_more: true };
  });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");
  assert.equal(calls, 1, "a row without seq cannot drive the cursor");
  assert.equal(transcript.complete, false);
  assert.equal(transcript.entries[0].seq, null);
});

test("a hidden row is counted, never rendered", async () => {
  route({
    "/messages?limit=100": {
      data: [
        { ...TOOL_MESSAGES[0], content: { ...TOOL_MESSAGES[0].content, additional_kwargs: { hide_from_ui: true } } },
        TOOL_MESSAGES[4],
      ],
      has_more: false,
    },
  });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");
  assert.equal(transcript.entries.length, 1);
  assert.equal(transcript.hiddenCount, 1);
  assert.equal(inspector.transcriptPrompts(transcript).length, 0);
});

test("the message count follows has_more, and reports a bounded read as partial", async () => {
  // One page, and the server says there is nothing after it: a real total.
  route({ "/messages?limit=200": { data: TOOL_MESSAGES, has_more: false } });
  assert.deepEqual(await inspector.fetchRunMessageCount("t-1", "r-1"), { count: 4, partial: false });

  // Two pages: the walk must follow the cursor rather than reporting 200.
  const page = (from, n) => Array.from({ length: n }, (_, i) => ({ ...TOOL_MESSAGES[0], seq: from + i }));
  const calls = [];
  setHttpHandler((path) => {
    calls.push(path);
    if (calls.length === 1) return { data: page(1, 200), has_more: true };
    return { data: page(201, 3), has_more: false };
  });
  assert.deepEqual(calls, []);
  const both = await inspector.fetchRunMessageCount("t-1", "r-1");
  assert.deepEqual(calls, [
    "/threads/t-1/runs/r-1/messages?limit=200",
    "/threads/t-1/runs/r-1/messages?limit=200&after_seq=200",
  ]);
  assert.deepEqual(both, { count: 203, partial: false });

  // A page the server says has more behind it, but whose last row carries no
  // usable seq: the count is a floor, never a total.
  let stops = 0;
  setHttpHandler(() => {
    stops += 1;
    return { data: [{ ...TOOL_MESSAGES[0], seq: null }], has_more: true };
  });
  assert.deepEqual(await inspector.fetchRunMessageCount("t-1", "r-1"), { count: 1, partial: true });
  assert.equal(stops, 1, "a row without seq cannot drive the cursor");

  // A malformed envelope is a failure, not a count of zero.
  route({ "/messages?limit=200": { data: "nope", has_more: false } });
  await assert.rejects(() => inspector.fetchRunMessageCount("t-1", "r-1"), /unreadable message page/);

  // No rows at all is the server's real answer, and it is complete.
  route({ "/messages?limit=200": { data: [], has_more: false } });
  assert.deepEqual(await inspector.fetchRunMessageCount("t-1", "r-1"), { count: 0, partial: false });
});

/* ══ 4. Tool calls: name, args, result, resolved status ═════════════════════ */

test("a tool call carries its name, arguments, result and resolved status", async () => {
  route({ "/messages?limit=100": { data: TOOL_MESSAGES, has_more: false } });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");
  const { calls, unattributedCount } = inspector.toolCallsFrom(transcript);
  assert.equal(calls.length, 1);
  assert.equal(unattributedCount, 0);
  const [call] = calls;
  assert.equal(call.name, "python_repl");
  assert.equal(call.id, "90c3a830");
  assert.deepEqual(call.args, { code: "print('alpha-inspector-probe')" });
  assert.match(call.argsText, /alpha-inspector-probe/);
  assert.equal(call.callSeq, 3);
  assert.equal(call.callAt, "2026-09-28T15:07:54.278320+00:00");
  assert.equal(call.caller, "lead_agent");
  assert.equal(call.status, "completed");
  assert.equal(call.statusVerbatim, "success");
  assert.equal(call.resultText, "[stdout]\nalpha-inspector-probe");
  assert.equal(call.resultSeq, 4);
  assert.equal(call.statusConflictsOutput, false);
});

test("a call with no result at all is unknown — never success, never failure", async () => {
  route({ "/messages?limit=100": { data: TOOL_MESSAGES.slice(0, 2), has_more: false } });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");
  const { calls } = inspector.toolCallsFrom(transcript);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].status, null, "no result must mean unknown");
  assert.equal(calls[0].statusVerbatim, null);
  assert.equal(calls[0].resultText, null);

  const markup = renderToStaticMarkup(
    createElement(ToolCallsPanel, { calls, unattributedCount: 0, error: null })
  );
  assert.match(markup, /no result reported/);
  assert.doesNotMatch(markup, /text-emerald-500/, "an unanswered call must not wear the success colour");
  assert.doesNotMatch(markup, />completed</, "an unanswered call must not be labelled completed");
});

test("a result whose journalled status conflicts with its own output says so, and claims no verdict", async () => {
  route({ "/messages?limit=100": { data: CONFLICTED_MESSAGES, has_more: false } });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");
  const { calls } = inspector.toolCallsFrom(transcript);
  const [call] = calls;
  assert.equal(call.status, "completed", "the run's own journalled verdict is preserved, not overridden");
  assert.equal(call.statusVerbatim, "success");
  assert.equal(call.statusConflictsOutput, true);

  const markup = renderToStaticMarkup(createElement(ToolCallsPanel, { calls, unattributedCount: 0, error: null }));
  assert.match(markup, /status conflicts with its own output/);
  assert.match(markup, /Error: File not found/);
  assert.doesNotMatch(markup, /text-emerald-500/, "a contested status must not be painted as a clean success");
  // The run's own status string is still shown, verbatim (React escapes the
  // quotes in a text child, so assert on the label and the word).
  assert.match(markup, /run recorded status/);
  assert.match(markup, /success/);
});

test("a result that cannot be attributed to a call is listed, not dropped", async () => {
  route({
    "/messages?limit=100": {
      data: [{ ...TOOL_MESSAGES[2], content: { ...TOOL_MESSAGES[2].content, tool_call_id: "no-such-call" } }],
      has_more: false,
    },
  });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");
  const { calls, unattributedCount } = inspector.toolCallsFrom(transcript);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].unattributed, true);
  assert.equal(calls[0].name, "python_repl", "the result row's own name is kept");
  assert.equal(unattributedCount, 1);
  assert.equal(calls[0].status, "completed");
});

test("a tool result with no name and no id is reported as unreported, not blank", async () => {
  const nameless = {
    ...TOOL_MESSAGES[2],
    content: { ...TOOL_MESSAGES[2].content, name: null, tool_call_id: null },
  };
  route({ "/messages?limit=100": { data: [nameless], has_more: false } });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");
  const { calls } = inspector.toolCallsFrom(transcript);
  assert.equal(calls[0].name, null);
  assert.equal(calls[0].id, null);
  const markup = renderToStaticMarkup(createElement(ToolCallsPanel, { calls, unattributedCount: 1, error: null }));
  assert.match(markup, /name not reported/);
  assert.match(markup, /call id: not reported/);
});

test("an artifact on a result row reaches the artifact list", () => {
  const calls = [
    {
      id: "c1", name: "write_file", args: {}, argsText: "{}", callSeq: 3, callAt: null, caller: "lead_agent",
      status: "completed", statusVerbatim: "success", resultText: "OK", resultSeq: 4, resultAt: null,
      unattributed: false, statusConflictsOutput: false, artifact: { path: "/mnt/user-data/outputs/a.md", bytes: 16 },
    },
  ];
  const artifacts = inspector.artifactsFrom(calls);
  assert.equal(artifacts.length, 1);
  assert.equal(artifacts[0].toolName, "write_file");
  assert.deepEqual(artifacts[0].artifact, { path: "/mnt/user-data/outputs/a.md", bytes: 16 });
  assert.deepEqual(inspector.artifactsFrom([{ ...calls[0], artifact: null }]), []);
});

/* ══ 5. The event timeline ═════════════════════════════════════════════════ */

test("every category is read and the type and timestamp are the server's own", async () => {
  route({ "/events?limit=500": FAILED_RUN_EVENTS });
  const timeline = await inspector.fetchRunTimeline("t-1", "r-1");
  assert.equal(timeline.complete, true);
  assert.deepEqual(timeline.events.map((e) => e.eventType), [
    "run.start",
    "llm.human.input",
    "llm.error",
    "run.end",
    "run.delivery",
  ]);
  assert.deepEqual(timeline.events.map((e) => e.category), ["trace", "message", "trace", "outputs", "outputs"]);
  assert.equal(timeline.events[0].createdAt, "2026-09-28T14:53:31.035441+00:00");
  assert.deepEqual(timeline.events.map((e) => e.severity), ["info", "info", "error", "info", "info"]);
});

test("an unreported event timestamp stays null and renders as unknown", async () => {
  route({
    "/events?limit=500": [{ seq: null, event_type: "run.start", category: "trace", content: null, metadata: {}, created_at: null }],
  });
  const timeline = await inspector.fetchRunTimeline("t-1", "r-1");
  assert.equal(timeline.events[0].seq, null);
  assert.equal(timeline.events[0].createdAt, null);
  const markup = renderToStaticMarkup(createElement(RunInspectorTimeline, { timeline, error: null }));
  assert.match(markup, /time not reported|--:--/);
  assert.doesNotMatch(markup, /1970/, "a missing time must not be painted as the epoch");
});

test("an event type this client has never seen is rendered verbatim", async () => {
  route({
    "/events?limit=500": [{ seq: 9, event_type: "middleware:quantum_annealer", category: "middleware", content: null, metadata: {}, created_at: "2026-09-28T15:10:41.000000+00:00" }],
  });
  const timeline = await inspector.fetchRunTimeline("t-1", "r-1");
  assert.equal(timeline.events[0].eventType, "middleware:quantum_annealer");
  const markup = renderToStaticMarkup(createElement(RunInspectorTimeline, { timeline, error: null }));
  assert.match(markup, /middleware:quantum_annealer/);
});

/* ══ 6. Workspace changes: available is not the same as empty ═══════════════ */

test("an available comparison maps its counts, files and diff", async () => {
  route({ "/workspace-changes": WORKSPACE_AVAILABLE });
  const changes = await inspector.fetchWorkspaceChangesForRun("t-1", "r-1");
  assert.equal(changes.available, true);
  assert.equal(changes.summary.created, 1);
  assert.equal(changes.files.length, 1);
  assert.equal(changes.files[0].status, "created");
  assert.match(changes.files[0].diff, /inspector demo/);
  assert.equal(changes.files[0].diffUnavailableReason, null);
});

test("an unavailable comparison is not a measurement of zero changes", async () => {
  route({ "/workspace-changes": WORKSPACE_UNAVAILABLE });
  const changes = await inspector.fetchWorkspaceChangesForRun("t-1", "r-1");
  assert.equal(changes.available, false);
  assert.equal(changes.files.length, 0);
  const markup = renderToStaticMarkup(createElement(WorkspacePanel, { changes, error: null }));
  assert.match(markup, /not available for this run/);
  assert.match(markup, /could not compare/);
  assert.doesNotMatch(markup, /no file changes/, "an unavailable comparison must not read as 'no files changed'");
  assert.doesNotMatch(markup, />0<\/div>/, "the unavailable report's zeros must not be painted as counts");
});

test("a file whose diff the Gateway withheld says why, instead of showing nothing", async () => {
  route({
    "/workspace-changes": {
      ...WORKSPACE_AVAILABLE,
      files: [{ ...WORKSPACE_AVAILABLE.files[0], diff: null, diff_unavailable_reason: "binary" }],
    },
  });
  const changes = await inspector.fetchWorkspaceChangesForRun("t-1", "r-1");
  assert.equal(changes.files[0].diff, null);
  const markup = renderToStaticMarkup(createElement(WorkspacePanel, { changes, error: null }));
  assert.match(markup, /No diff: the Gateway recorded &quot;binary&quot;./);
});

test("a comparison with no summary object reports unknown totals, not zeros", async () => {
  route({ "/workspace-changes": { available: true, version: 1, files: [] } });
  const changes = await inspector.fetchWorkspaceChangesForRun("t-1", "r-1");
  assert.equal(changes.summary, null);
  const markup = renderToStaticMarkup(createElement(WorkspacePanel, { changes, error: null }));
  assert.match(markup, /no summary object/);
});

test("workspaceChangeCount is a measurement only when the comparison was available", async () => {
  // The real payload this Gateway returns for a run it could not compare:
  // available:false, a zeroed summary, and an empty file list.
  route({ "/workspace-changes": WORKSPACE_UNAVAILABLE });
  const unavailable = await inspector.fetchWorkspaceChangesForRun("t-1", "r-1");
  assert.equal(unavailable.available, false);
  assert.equal(unavailable.files.length, 0);
  assert.equal(
    inspector.workspaceChangeCount(unavailable),
    null,
    "an unavailable comparison must not become a count of 0 files changed"
  );

  // A real measured zero - available, compared, nothing changed - stays 0.
  route({
    "/workspace-changes": {
      ...WORKSPACE_AVAILABLE,
      files: [],
      summary: { created: 0, modified: 0, deleted: 0, symlink_created: 0, additions: 0, deletions: 0, truncated: false },
    },
  });
  const compared = await inspector.fetchWorkspaceChangesForRun("t-1", "r-1");
  assert.equal(compared.available, true);
  assert.equal(inspector.workspaceChangeCount(compared), 0);

  route({ "/workspace-changes": WORKSPACE_AVAILABLE });
  assert.equal(inspector.workspaceChangeCount(await inspector.fetchWorkspaceChangesForRun("t-1", "r-1")), 1);

  // No `available` field at all: unknown, not zero.
  route({ "/workspace-changes": { version: 1, files: [] } });
  const unreported = await inspector.fetchWorkspaceChangesForRun("t-1", "r-1");
  assert.equal(unreported.available, null);
  assert.equal(inspector.workspaceChangeCount(unreported), null);
});

/* ══ 7. The delivery receipt ═══════════════════════════════════════════════ */

test("the delivery receipt is read from the run's own event and labelled as delivery", async () => {
  route({ "/events?limit=500": DELIVERY_EVENTS });
  const timeline = await inspector.fetchRunTimeline("t-1", "r-1");
  const receipt = inspector.deliveryFrom(timeline);
  assert.equal(receipt.presented, 1);
  assert.equal(receipt.stage, "presented");
  assert.equal(receipt.satisfied, true);
  assert.equal(receipt.verificationSource, "outputs_changed");
  assert.equal(receipt.requirement, "present_files_matches_produced_output");
  assert.deepEqual(receipt.paths, ["/mnt/user-data/outputs/inspector-demo.md"]);
  assert.deepEqual(receipt.byTool, { present_files: ["/mnt/user-data/outputs/inspector-demo.md"] });

  const markup = renderToStaticMarkup(
    createElement(DeliveryPanel, { receipt, manifest: { fileCount: 1 }, artifacts: [], timelineError: null, error: null })
  );
  assert.match(markup, /delivered artifacts/i);
  assert.match(markup, /inspector-demo\.md/);
  assert.match(markup, /This is a delivery receipt, not a verdict on the answer/);
  // A satisfied delivery requirement is not a verified run.
  assert.doesNotMatch(markup, /\bverified run\b/i);
  assert.doesNotMatch(markup, />verified</i);
});

test("a run with no delivery event reports the server's absence, not a failure", async () => {
  route({ "/events?limit=500": [FAILED_RUN_EVENTS[0]] });
  const timeline = await inspector.fetchRunTimeline("t-1", "r-1");
  assert.equal(inspector.deliveryFrom(timeline), null);
  const markup = renderToStaticMarkup(
    createElement(DeliveryPanel, { receipt: null, manifest: null, artifacts: [], timelineError: null, error: null })
  );
  assert.match(markup, /no delivery event for this run/);
});

/* ══ 8. Token usage ════════════════════════════════════════════════════════ */

test("thread context usage maps its own fields and keeps an absent one null", async () => {
  route({ "/token-usage": THREAD_USAGE });
  const usage = await inspector.fetchThreadTokenUsage("t-1");
  assert.equal(usage.totalTokens, 53244);
  assert.equal(usage.totalRuns, 1);
  assert.deepEqual(usage.contextUsage, { tokenCount: 1204, maxContextTokens: 262144, percentage: 0.5 });

  route({ "/token-usage": { thread_id: "t-1", total_tokens: 0 } });
  const bare = await inspector.fetchThreadTokenUsage("t-1");
  assert.equal(bare.contextUsage, null, "an unreported context measurement is null");
  assert.equal(bare.totalTokens, 0, "a real zero stays zero");
  assert.equal(bare.totalRuns, null);
});

test("an unreported percentage is unknown, not 0%", () => {
  assert.equal(inspector.formatPercentage(null), "—");
  assert.equal(inspector.formatPercentage(0), "0.0%");
  assert.equal(inspector.formatCount(null), "—");
  assert.equal(inspector.formatCount(0), "0");
});

/* ══ 9. Honesty inversions: a failed read is never an empty answer ═══════════ */

test("a 500 rejects with the server's reason instead of resolving to an empty list", async () => {
  setHttpFailure(500, "run event store is unavailable");
  await assert.rejects(() => inspector.fetchRunTimeline("t-1", "r-1"), /run event store is unavailable/);
  await assert.rejects(() => inspector.fetchRunTranscript("t-1", "r-1"), /run event store is unavailable/);
  await assert.rejects(() => inspector.fetchWorkspaceChangesForRun("t-1", "r-1"), /run event store is unavailable/);
  await assert.rejects(() => inspector.fetchArtifactArchiveManifest("t-1", "r-1"), /run event store is unavailable/);
  await assert.rejects(() => inspector.fetchRunRecord("t-1", "r-1"), /run event store is unavailable/);
  await assert.rejects(() => inspector.fetchThreadTokenUsage("t-1"), /run event store is unavailable/);
  await assert.rejects(() => inspector.fetchRunPage("t-1"), /run event store is unavailable/);
});

test("a 429 on the run list does not read as 'no runs'", async () => {
  setHttpFailure(429, "rate limit exceeded, retry in 30s");
  await assert.rejects(() => inspector.fetchRecentRuns("t-1"), /rate limit exceeded, retry in 30s/);
  await assert.rejects(() => inspector.fetchRunPage("t-1"), /rate limit exceeded/);

  const markup = renderView({
    ...BASE_STATE,
    selectedRunId: null,
    showPicker: true,
    runs: [],
    runsError: "Request failed (HTTP 429). rate limit exceeded, retry in 30s",
  });
  assert.match(markup, /could not be read/);
  assert.match(markup, /rate limit exceeded, retry in 30s/);
  assert.doesNotMatch(markup, /reported no runs for this conversation/);
});

test("an empty run list from a healthy server is reported as the server's answer", async () => {
  const markup = renderView({ ...BASE_STATE, selectedRunId: null, showPicker: true, runs: [], runsError: null, runsComplete: true });
  assert.match(markup, /reported no runs for this conversation/);
  assert.match(markup, /not a failed read/);
});

test("fetchOlderRuns follows the server's own cursor, and refuses a partial one", async () => {
  let served = 0;
  const handler = () => {
    served += 1;
    // The first page says another page follows and hands back its cursor; the
    // second says this was the last one.
    if (served === 1) {
      return {
        data: [inspector.toRunRecord({ run_id: "r-old-1", status: "success" })],
        has_more: true,
        next_before_created_at: "2026-01-03T00:00:00+00:00",
        next_before_run_id: "r-old-1",
      };
    }
    return {
      data: [inspector.toRunRecord({ run_id: "r-old-3", status: "success" })],
      has_more: false,
      next_before_created_at: null,
      next_before_run_id: null,
    };
  };
  // `route` matches on a path suffix, so the full URLs are the keys here.
  const firstUrl = "/threads/t-1/runs/page?limit=50&before_created_at=2026-01-02T00%3A00%3A00%2B00%3A00&before_run_id=r-old-2";
  const secondUrl = "/threads/t-1/runs/page?limit=50&before_created_at=2026-01-03T00%3A00%3A00%2B00%3A00&before_run_id=r-old-1";
  const calls = route({ [firstUrl]: handler, [secondUrl]: handler });

  const page = await inspector.fetchOlderRuns("t-1", "2026-01-02T00:00:00+00:00", "r-old-2");
  assert.equal(calls[0], `GET ${firstUrl}`);
  assert.equal(page.runs.length, 1);
  assert.equal(page.runs[0].run_id, "r-old-1");
  assert.equal(page.hasMore, true);
  assert.equal(page.nextBeforeCreatedAt, "2026-01-03T00:00:00+00:00");
  assert.equal(page.nextBeforeRunId, "r-old-1");

  const end = await inspector.fetchOlderRuns("t-1", page.nextBeforeCreatedAt, page.nextBeforeRunId);
  assert.equal(calls[1], `GET ${secondUrl}`);
  assert.equal(end.runs[0].run_id, "r-old-3");
  assert.equal(end.hasMore, false, "the server said this was the last page");
  assert.equal(end.nextBeforeCreatedAt, null);

  // A half cursor is refused locally rather than sent: the route 422s on one
  // cursor field alone, and silently re-reading the newest page would show the
  // user the wrong runs.
  const before = calls.length;
  const empty = { runs: [], hasMore: false, nextBeforeCreatedAt: null, nextBeforeRunId: null };
  assert.deepEqual(await inspector.fetchOlderRuns("t-1", null, "r-old-2"), empty);
  assert.deepEqual(await inspector.fetchOlderRuns("t-1", "2026-01-02T00:00:00+00:00", null), empty);
  assert.deepEqual(await inspector.fetchOlderRuns("t-1", null, null), empty);
  assert.equal(calls.length, before, "a partial cursor must not issue a request");
});

test("one failed panel degrades only itself and shows the server's reason", async () => {
  route({
    "/runs/r-1": SUCCESS_RUN,
    "/messages?limit=100": { data: TOOL_MESSAGES, has_more: false },
    "/events?limit=500": FAILED_RUN_EVENTS,
    "/workspace-changes": WORKSPACE_AVAILABLE,
  });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");
  const markup = renderView({
    ...BASE_STATE,
    record: inspector.toRunRecord(SUCCESS_RUN),
    transcript,
    timeline: null,
    errors: { timeline: "Request failed (HTTP 500). run event store is unavailable" },
  });
  // The timeline panel states the reason…
  assert.match(markup, /event stream could not be read/);
  assert.match(markup, /run event store is unavailable/);
  // …while the panels that DID read keep their own real data, and the delivery
  // panel refuses to claim a receipt it could not read.
  assert.match(markup, /Terminal status/);
  assert.match(markup, /success/);
  assert.match(markup, /Use the bash tool to run echo alpha-inspector-probe/);
  assert.match(markup, /python_repl/);
  assert.match(markup, /delivery receipt lives on its event stream, which could not be read/);
  assert.doesNotMatch(markup, /Event timeline \(\d+\)/, "a failed timeline must not render a count");
});

test("a 409 from the archive endpoint surfaces its reason, not '0 artifacts'", async () => {
  setHttpFailure(409, "This response has no verified artifact delivery");
  await assert.rejects(
    () => inspector.fetchArtifactArchiveManifest("t-1", "r-1"),
    /This response has no verified artifact delivery/
  );

  const markup = renderToStaticMarkup(
    createElement(DeliveryPanel, {
      receipt: null,
      manifest: null,
      artifacts: [],
      timelineError: null,
      error: "Request failed (HTTP 409). This response has no verified artifact delivery",
    })
  );
  assert.match(markup, /archive manifest could not be read/);
  assert.match(markup, /no verified artifact delivery/);
  assert.doesNotMatch(markup, /0 artifacts/);
});

test("a malformed envelope is a failure, not an empty list", async () => {
  route({ "/runs/page?limit=50": { has_more: false } });
  await assert.rejects(() => inspector.fetchRunPage("t-1"), /unreadable run page/);
  route({ "/messages?limit=100": { data: "not-a-list", has_more: false } });
  await assert.rejects(() => inspector.fetchRunTranscript("t-1", "r-1"), /unreadable message page/);
  route({ "/workspace-changes": "nope" });
  await assert.rejects(() => inspector.fetchWorkspaceChangesForRun("t-1", "r-1"), /unreadable workspace-change report/);
  route({ "/token-usage": 42 });
  await assert.rejects(() => inspector.fetchThreadTokenUsage("t-1"), /unreadable token-usage report/);
});

/* ══ 10. The terminal status, rendered ══════════════════════════════════════ */

test("the run record's status is the terminal status — the run.end event's is not", () => {
  const record = inspector.toRunRecord(FAILED_RUN);
  const markup = renderToStaticMarkup(createElement(RunStatusPanel, { record, error: null }));
  assert.match(markup, />error</, "the record's own status word is rendered");
  assert.match(markup, /recovery_blocked/);
  assert.match(markup, /union-alpha/);
  assert.match(markup, /The Gateway recorded this error for the run/);
  assert.doesNotMatch(markup, /text-emerald-500/, "a failed run must not wear the success colour");
  // The event's contradicting `metadata.status` must not become the verdict.
  assert.doesNotMatch(markup, /recorded this run as success/);
});

test("a completed run is labelled completed and never verified", () => {
  const record = inspector.toRunRecord(SUCCESS_RUN);
  const markup = renderToStaticMarkup(createElement(RunStatusPanel, { record, error: null }));
  assert.match(markup, />success</);
  assert.match(markup, /Completed is not the same as verified/);
  assert.doesNotMatch(markup, />verified</i);
  assert.doesNotMatch(markup, /run verified/i);
});

test("a still-active run says so, and a missing status says it is unknown", () => {
  for (const status of ["running", "pending", "queued", "in_progress"]) {
    const markup = renderToStaticMarkup(
      createElement(RunStatusPanel, { record: inspector.toRunRecord({ run_id: "r-1", status }), error: null })
    );
    assert.match(markup, /still reports this run as active/);
    assert.doesNotMatch(markup, /Completed is not the same as verified/);
  }
  const unknown = renderToStaticMarkup(createElement(RunStatusPanel, { record: inspector.toRunRecord({ run_id: "r-1" }), error: null }));
  assert.match(unknown, /did not report a status/);
  assert.match(unknown, /status not reported/);
  assert.doesNotMatch(unknown, /still reports this run as active/);
  assert.doesNotMatch(unknown, /Completed/);
});

test("a missing model reads as not reported, not as a default model", () => {
  const markup = renderToStaticMarkup(
    createElement(RunStatusPanel, { record: inspector.toRunRecord({ run_id: "r-1", status: "success" }), error: null })
  );
  assert.match(markup, /model that served the run/);
  assert.match(markup, /not reported/);
  assert.doesNotMatch(markup, /alpha-free|union-alpha/, "no model may be invented for a run that reported none");
});

test("an unreported token total renders as unknown, never as 0", () => {
  const record = inspector.toRunRecord({ run_id: "r-1", status: "success" });
  const markup = renderView({ ...BASE_STATE, record });
  assert.match(markup, /total tokens/);
  assert.match(markup, /not reported/);
  // The only `0` allowed in the whole view would come from a measured zero.
  assert.doesNotMatch(markup, /0 tokens/);
  // …and the missing per-model split is named, with the reason it is not zero.
  assert.match(markup, /reported no per-model token split/);
  assert.match(markup, /That is not zero tokens/);
});

test("a measured zero stays a real zero in the same tiles", () => {
  const markup = renderView({ ...BASE_STATE, record: inspector.toRunRecord(FAILED_RUN) });
  assert.match(markup, />0</, "the Gateway measured zero tokens for this run, so zero is shown");
  assert.match(markup, /recovery_blocked/);
});

/* ══ 11. The whole view, on a real run ══════════════════════════════════════ */

test("the assembled view shows the prompt, the answer, the tool call and the events", async () => {
  route({
    "/runs/page?limit=50": { data: [SUCCESS_RUN], has_more: false, next_before_created_at: null, next_before_run_id: null },
    "/runs/r-1": SUCCESS_RUN,
    "/messages?limit=100": { data: TOOL_MESSAGES, has_more: false },
    "/events?limit=500": DELIVERY_EVENTS,
    "/workspace-changes": WORKSPACE_AVAILABLE,
    "/artifacts/archive": { file_count: 1 },
    "/token-usage": THREAD_USAGE,
  });

  const [record, transcript, timeline, workspace, manifest, usage] = await Promise.all([
    inspector.fetchRunRecord("t-1", "r-1"),
    inspector.fetchRunTranscript("t-1", "r-1"),
    inspector.fetchRunTimeline("t-1", "r-1"),
    inspector.fetchWorkspaceChangesForRun("t-1", "r-1"),
    inspector.fetchArtifactArchiveManifest("t-1", "r-1"),
    inspector.fetchThreadTokenUsage("t-1"),
  ]);

  const markup = renderView({
    ...BASE_STATE,
    showPicker: true,
    runs: [record],
    record,
    transcript,
    timeline,
    workspace,
    usage,
    manifest,
  });

  for (const needle of [
    "e528f5a6-2c82-4959-aafd-a73da0996468", // the run id
    "alpha-free", // the model that served it
    "Use the bash tool to run", // the prompt as submitted
    "It printed alpha-inspector-probe.", // the final answer
    "python_repl", // the tool name
    "print(&#x27;alpha-inspector-probe&#x27;)", // the arguments
    "[stdout]", // the tool result
    "run.delivery", // a real event type
    "workspace_changes", // a real event type
    "Terminal status",
    "Prompt and answer",
    "Tool calls",
    "Event timeline",
    "Workspace changes",
    "Delivered artifacts",
    "Tokens and model",
  ]) {
    assert.ok(markup.includes(needle), `the view must show ${needle}`);
  }
});

test("the picker marks an active run as active and shows a foreign status verbatim", async () => {
  const markup = renderView({
    ...BASE_STATE,
    showPicker: true,
    selectedRunId: null,
    runs: [
      inspector.toRunRecord({ run_id: "r-live", status: "running", model: "m", total_tokens: null }),
      inspector.toRunRecord({ run_id: "r-weird", status: "quiesced_by_operator", model: "m", total_tokens: null }),
    ],
    runsComplete: false,
  });
  assert.match(markup, /r-live/);
  assert.match(markup, /still active/);
  assert.match(markup, /quiesced_by_operator/);
  assert.match(markup, /tokens not reported/);
  assert.match(markup, /the newest page only/);
});
