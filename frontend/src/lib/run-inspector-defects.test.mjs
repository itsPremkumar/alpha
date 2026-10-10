// run-inspector-defects.test.mjs — the four discrepancies the run inspector
// did not surface, plus the legibility rules that make the rest readable.
//
// Every test here is REAL. The client module is transpiled and executed, the
// components are mounted through `react-dom/server`, and the assertions run
// against the emitted markup or the returned values. There is no regex over a
// source file anywhere in this file — a test that asserts against a copy of the
// code instead of the code is worse than no test, because it passes when the
// code is wrong.
//
// What each block pins, and the misreading it prevents:
//
//  1. SERVING MODEL. `run.model` is the CONFIGURED Alpha model name
//     (`alpha-free`); the model that answered is
//     `response_metadata.model_name` (`opencode-zen:space-bunny-free`). The
//     inspector showed only the alias under the label "model that served the
//     run", so an operator could not tell which model was on the other end.
//  2. FAILED TOOL CALL. `ToolMessage.status` defaults to `"success"` and the
//     persisted event feed often carries an EMPTY `alpha_tool_meta`, so a failed
//     `read_file` — and every failed `bash`, whose only evidence is the trailing
//     `Exit Code: N` the sandbox appends to ordinary output — resolved to
//     `completed` and wore the success colour.
//  3. RECURSION LIMIT. `errors/registry.py` folds `RecursionLimit` into
//     `RUN_QUOTA_EXCEEDED`, whose message is "A run or token budget for this
//     thread is exhausted". Rendering that message alone tells an operator their
//     thread is unusable for a reason that did not happen. The real
//     `error_type` is now shown beside the code.
//  4. SUBAGENT DELEGATION. `subagent.start/step/end` ride `stream_mode: custom`
//     frames, so a genuinely successful delegation can record ZERO of them on its
//     parent. `ThreadState.delegations` is where the delegation actually is.
//
// Node test (node --test src/lib/*.test.mjs): transpiles the TS/TSX with the
// local `typescript`, rewrites bare specifiers to real module files and loads
// the result — no dev server, no browser, no DOM shim.
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
 * files, not as nested `data:` URLs: this suite stacks component -> client ->
 * runs -> http-stub, and that nesting does not survive the data-URL scheme.
 * Relative specifiers are rewritten to sibling files, so Node resolves the
 * module graph exactly as it would for the app.
 */
const OUT_DIR = join(tmpdir(), `alpha-run-inspector-defects-${process.pid}`);
mkdirSync(OUT_DIR, { recursive: true });

function compile(relative, { jsx = false, specifiers = {} } = {}) {
  let code = ts.transpileModule(read(relative), {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
      jsx: jsx ? ts.JsxEmit.ReactJSX : undefined,
    },
  }).outputText;
  for (const [from, to] of Object.entries(specifiers)) {
    code = code.replace(
      new RegExp(`(from\\s+)"${from.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"`, "g"),
      `$1"${to}"`
    );
  }
  return code;
}

const emit = (name, code) => {
  writeFileSync(join(OUT_DIR, `${name}.mjs`), code, "utf8");
  return `./${name}.mjs`;
};
const loadFile = (name) => import(pathToFileURL(join(OUT_DIR, `${name}.mjs`)).href);

/* ── the stubbed transport ─────────────────────────────────────────────────── */

// Mirrors `api-client.ts`: a non-2xx becomes an error carrying the Gateway's
// own `detail`, so the assertions below test the real failure surface.
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

const jsxRuntime = { react: resolveUrl("react"), "react/jsx-runtime": resolveUrl("react/jsx-runtime") };
const lucide = pathToFileURL(here("../../node_modules/lucide-react/dist/esm/lucide-react.js")).href;
const timeRef = emit("time", compile("./time.ts"));
// UI primitives import browser focus/scroll helpers. Server-rendered markup
// tests do not exercise those effects, so emit explicit no-op hooks rather
// than leaving the `@/lib/a11y` alias unresolved — Node cannot resolve a path
// alias from a written module, and the file then dies with
// ERR_MODULE_NOT_FOUND before any assertion runs.
const a11yRef = emit(
  "a11y",
  `export function useFocusTrap() {} export function useScrollLock() {}`
);
const uiRef = emit("ui", compile("../components/ui.tsx", { jsx: true, specifiers: { ...jsxRuntime, "@/lib/a11y": a11yRef } }));
const toolPillRef = emit(
  "ToolPill",
  compile("../components/ToolPill.tsx", { jsx: true, specifiers: { ...jsxRuntime, "lucide-react": lucide } })
);
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
/**
 * Stand-ins for the three run-scoped surfaces `RunsSection` mounts.
 *
 * They own their own transports and their own contracts; the defects this
 * suite pins in the run list are in the list's own markup and state machine,
 * so these exist only to keep the render focused and deterministic. They are
 * NOT a copy of anything: each returns a plain function component that renders
 * a fixed marker element, and no assertion in this file depends on it.
 */
emit(
  "RunsSectionStubs",
  `
const marker = (name) => function Stub() {
  return { $$typeof: Symbol.for("react.transitional.element"), type: "div", key: null, ref: null, props: { "data-stub": name }, _owner: null };
};
export const RunInspectorSection = marker("RunInspectorSection");
export const RunUsagePanel = marker("RunUsagePanel");
export const RunReplayControls = marker("RunReplayControls");
`
);

// The context-window bands are a pure derivation over the run payload, but the
// module still reads `get` from the transport, so it needs the same stub the
// rest of the graph gets. Without this entry the section's `@/lib/context-window`
// specifier reaches Node unresolved and dies as package `@/lib` before any
// assertion runs — a harness gap, not a defect in the section.
const contextWindowRef = emit("context-window", compile("./context-window.ts", { specifiers: { "./http": httpRef } }));

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
      "@/lib/context-window": contextWindowRef,
      "@/components/ui": uiRef,
      "@/components/ToolPill": toolPillRef,
      "./RunInspectorTimeline": timelineRef,
    },
  })
);
const {
  RunInspectorView,
  RunStatusPanel,
  ToolCallsPanel,
  DelegationPanel,
  WorkspacePanel,
  TokenPanel,
  DeliveryPanel,
} = await loadFile("RunInspectorSection");
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
  delegations: null,
  loadingDelegations: false,
  errors: {},
  loadingRun: false,
  showPicker: false,
  runFilter: "",
  copyingLink: false,
  linkNotice: null,
  selectionNote: null,
  loadingOlderRuns: false,
  olderRunsError: null,
};

const renderView = (state) =>
  renderToStaticMarkup(
    createElement(RunInspectorView, {
      state,
      onSelect() {},
      onReload() {},
      onQueryChange() {},
      onCopyLink() {},
      onLoadOlder() {},
      canLoadOlder: false,
    })
  );

/* ── payloads shaped like this Gateway's real responses ─────────────────────── */

/** The real discrepancy: configured alias vs. the model that actually answered. */
const ALIAS_RUN = {
  run_id: "e528f5a6-2c82-4959-aafd-a73da0996468",
  thread_id: "c10110de-79ae-4f88-8049-2b50c3436e82",
  assistant_id: "lead_agent",
  status: "success",
  metadata: { alpha_trace_id: "4e22ce768efb4ba8853515995cb95341" },
  kwargs: { input: { messages: [{ role: "user", content: "hi" }] } },
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
  token_usage_by_model: { "opencode-zen:space-bunny-free": { input_tokens: 52992, output_tokens: 252, total_tokens: 53244 } },
};

/**
 * A real `bash` failure: the command's own stdout followed by the sandbox's
 * trailing exit marker. `alpha_tool_meta` is `{}` and `ToolMessage.status` reads
 * `"success"` — exactly the shape `subagents/executor.py::_bash_evidence_status`
 * documents ("a nonzero bash exit returns ordinary text that `alpha_tool_meta`
 * still reports as success").
 */
const FAILED_BASH_MESSAGES = [
  {
    seq: 3,
    event_type: "llm.ai.response",
    category: "message",
    content: {
      content: "",
      type: "ai",
      id: "ai-1",
      response_metadata: { model_name: "opencode-zen:space-bunny-free" },
      tool_calls: [{ name: "bash", args: { command: "grep -q needle haystack.txt" }, id: "call-bash", type: "tool_call" }],
    },
    metadata: { caller: "lead_agent" },
    created_at: "2026-09-28T15:07:54.278320+00:00",
  },
  {
    seq: 4,
    event_type: "llm.tool.result",
    category: "message",
    content: {
      content: "Exit Code: 1",
      type: "tool",
      name: "bash",
      tool_call_id: "call-bash",
      artifact: null,
      status: "success",
      additional_kwargs: { alpha_tool_meta: {} },
    },
    metadata: {},
    created_at: "2026-09-28T15:07:55.596175+00:00",
  },
];

/** The same command, exit 0. A successful shell run must stay a success. */
const OK_BASH_MESSAGES = [
  FAILED_BASH_MESSAGES[0],
  { ...FAILED_BASH_MESSAGES[1], content: { ...FAILED_BASH_MESSAGES[1].content, content: "Exit Code: 0" } },
];

/**
 * A recursion-limit death. `errors/registry.py` classifies `RecursionLimit`
 * under `RUN_QUOTA_EXCEEDED`, whose message claims an exhausted budget, while
 * `metadata.error_type` names the exception that actually stopped the run.
 */
const RECURSION_EVENTS = [
  {
    seq: 1,
    event_type: "run.start",
    category: "trace",
    content: { chain: "lead_agent" },
    metadata: { caller: "lead_agent" },
    created_at: "2026-09-28T15:00:00.000000+00:00",
  },
  {
    seq: 2,
    event_type: "run.error",
    category: "error",
    content: "Recursion limit of 250 reached without hitting a stop condition",
    metadata: {
      error_type: "GraphRecursionError",
      error_code: "RUN_QUOTA_EXCEEDED",
      severity: "warning",
      retryable: false,
      error_message: "A run or token budget for this thread is exhausted.",
      error_correlation_id: "alpha.errors.run",
      recovery: "none",
    },
    created_at: "2026-09-28T15:00:12.000000+00:00",
  },
];

/** The delegation ledger as `ThreadState.delegations` serialises it. */
const THREAD_STATE = {
  values: {
    delegations: [
      {
        id: "call_task_1",
        run_id: "r-1",
        description: "Research the failing test",
        subagent_type: "general-purpose",
        status: "completed",
        result_brief: "Found the cause in tools.py:1818.",
        created_at: "2026-09-28T15:07:50.000000+00:00",
      },
      {
        id: "call_task_2",
        run_id: "r-OTHER",
        description: "A different run's delegation",
        subagent_type: "general-purpose",
        status: "completed",
        created_at: "2026-09-28T14:00:00.000000+00:00",
      },
      {
        // History written before `run_id` was tagged. It must NOT be attributed
        // to whichever run happens to be open.
        id: "call_task_legacy",
        description: "An untagged legacy delegation",
        subagent_type: "general-purpose",
        status: "completed",
        created_at: "2026-09-01T00:00:00.000000+00:00",
      },
    ],
  },
  next: [],
  metadata: {},
  checkpoint: { id: "cp-1" },
};

/* ══ 1. The model that actually served the run ═══════════════════════════════ */

test("the serving model is read from response_metadata, not from run.model", async () => {
  route({ "/runs/r-1/messages?limit=100": { data: FAILED_BASH_MESSAGES, has_more: false } });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");

  // `run.model` is the CONFIGURED name. It is not the provider's name.
  const record = inspector.toRunRecord(ALIAS_RUN);
  assert.equal(record.model, "alpha-free");

  const report = inspector.servingModelsFrom(transcript, record.model);
  assert.deepEqual(report.models, [{ model: "opencode-zen:space-bunny-free", responses: 1 }]);
  assert.equal(report.differsFromRecord, true, "the two names really are different on this run");
});

test("the status panel names the alias and the serving model as two separate facts", async () => {
  route({ "/runs/r-1/messages?limit=100": { data: FAILED_BASH_MESSAGES, has_more: false } });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");
  const record = inspector.toRunRecord(ALIAS_RUN);
  const markup = renderToStaticMarkup(
    createElement(RunStatusPanel, {
      record,
      error: null,
      serving: inspector.servingModelsFrom(transcript, record.model),
      failure: null,
    })
  );

  // The serving model IS on screen…
  assert.match(markup, /opencode-zen:space-bunny-free/);
  // …and the configured alias is on screen under its own, correct label…
  assert.match(markup, /model the run was configured with/);
  // …and the old label, which claimed the alias WAS the serving model, is gone.
  assert.doesNotMatch(
    markup,
    /model that served the run/,
    "the alias must not be labelled as the model that served the run"
  );
  assert.match(markup, /model that actually served this run/);
  // The two are explained, so the difference reads as information not noise.
  assert.match(markup, /two different names for two different things/);
});

test("a run with no model_name reports the serving model as unknown, not as the alias", () => {
  const report = inspector.servingModelsFrom(null, "alpha-free");
  assert.equal(report.models, null, "no response named a model, which is not the same as naming the alias");
  assert.equal(report.differsFromRecord, false, "nothing was compared, so nothing differs");

  const markup = renderToStaticMarkup(
    createElement(RunStatusPanel, {
      record: inspector.toRunRecord({ run_id: "r-1", status: "success" }),
      error: null,
      serving: report,
      failure: null,
    })
  );
  assert.match(markup, /model that actually served this run/);
  assert.match(markup, /not reported/);
});

test("a run that fell back across two providers reports both, not the first one", () => {
  const transcript = {
    entries: [
      { kind: "answer", model: "opencode-zen:space-bunny-free" },
      { kind: "answer", model: "opencode-zen:space-bunny-free" },
      { kind: "answer", model: "kilo:kilo-auto/free" },
    ],
    complete: true,
    hiddenCount: 0,
  };
  const report = inspector.servingModelsFrom(transcript, "alpha-free");
  assert.deepEqual(report.models, [
    { model: "opencode-zen:space-bunny-free", responses: 2 },
    { model: "kilo:kilo-auto/free", responses: 1 },
  ]);
});

/* ══ 2. A failed tool call must not wear the success colour ══════════════════ */

test("a nonzero shell exit is caught even when the body opens with ordinary stdout", async () => {
  route({ "/runs/r-1/messages?limit=100": { data: FAILED_BASH_MESSAGES, has_more: false } });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");
  const { calls } = inspector.toolCallsFrom(transcript);
  const [call] = calls;

  // The run's own status is preserved, not overridden: LangChain defaults
  // `ToolMessage.status` to "success" and the inspector invents no verdict.
  assert.equal(call.statusVerbatim, "success");
  assert.equal(call.status, "completed", "the run journal's own verdict is untouched");

  // …but the run's own evidence that the command failed is now carried.
  assert.equal(call.shellExitCode, 1, "the trailing Exit Code: N is the run's own evidence");
  assert.equal(call.statusConflictsOutput, true, "so the call must not render as a clean success");
});

test("a failed bash renders as contested, never as a green completed check", async () => {
  route({ "/runs/r-1/messages?limit=100": { data: FAILED_BASH_MESSAGES, has_more: false } });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");
  const { calls } = inspector.toolCallsFrom(transcript);
  const markup = renderToStaticMarkup(
    createElement(ToolCallsPanel, { calls, unattributedCount: 0, error: null })
  );
  assert.match(markup, /status conflicts with its own output/);
  assert.doesNotMatch(markup, /text-emerald-500/, "a contested call must not wear the success colour");
  // The run's own evidence is quoted, not paraphrased into a verdict.
  assert.match(markup, /Exit Code: 1/);
  // And the run's own recorded status is still shown verbatim.
  assert.match(markup, /run recorded status/);
});

test("a zero shell exit stays a clean success", async () => {
  route({ "/runs/r-1/messages?limit=100": { data: OK_BASH_MESSAGES, has_more: false } });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");
  const { calls } = inspector.toolCallsFrom(transcript);
  assert.equal(calls[0].shellExitCode, null, "Exit Code: 0 is a success, not a failure signal");
  assert.equal(calls[0].statusConflictsOutput, false);
  const markup = renderToStaticMarkup(
    createElement(ToolCallsPanel, { calls, unattributedCount: 0, error: null })
  );
  assert.doesNotMatch(markup, /status conflicts with its own output/);
});

test("the remote provider's silent-command failure marker is read too", () => {
  const remote = [
    FAILED_BASH_MESSAGES[0],
    {
      ...FAILED_BASH_MESSAGES[1],
      content: { ...FAILED_BASH_MESSAGES[1].content, content: "Command exited with code 2" },
    },
  ];
  const transcript = { entries: [], complete: true, hiddenCount: 0 };
  // Build through the real reader so the mapping is exercised, not re-derived.
  route({ "/runs/r-1/messages?limit=100": { data: remote, has_more: false } });
  return inspector.fetchRunTranscript("t-1", "r-1").then((read) => {
    const { calls } = inspector.toolCallsFrom(read);
    assert.equal(calls[0].shellExitCode, 2);
    assert.equal(calls[0].statusConflictsOutput, true);
    assert.ok(transcript);
  });
});

test("a read_file error with no shell marker is still caught, and still claims no verdict", async () => {
  const conflicted = [
    {
      seq: 5,
      event_type: "llm.ai.response",
      category: "message",
      content: {
        content: "",
        type: "ai",
        id: "ai-2",
        tool_calls: [{ name: "read_file", args: { path: "/mnt/user-data/outputs/missing.txt" }, id: "call-read" }],
      },
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
        status: "success",
        additional_kwargs: { alpha_tool_meta: {} },
      },
      metadata: {},
      created_at: "2026-09-28T15:10:17.164037+00:00",
    },
  ];
  route({ "/runs/r-1/messages?limit=100": { data: conflicted, has_more: false } });
  const transcript = await inspector.fetchRunTranscript("t-1", "r-1");
  const { calls } = inspector.toolCallsFrom(transcript);
  assert.equal(calls[0].shellExitCode, null, "there is no shell marker here, and none is invented");
  assert.equal(calls[0].statusConflictsOutput, true);
  assert.equal(calls[0].status, "completed", "the run's journalled verdict is preserved, not overridden");
});

/* ══ 3. A recursion limit is not an exhausted budget ═════════════════════════ */

test("the coded failure is read with its real error_type, not just its message", async () => {
  route({ "/runs/r-1/events?limit=500": RECURSION_EVENTS });
  const timeline = await inspector.fetchRunTimeline("t-1", "r-1");
  const failure = inspector.runFailureFrom(timeline);
  assert.equal(failure.code, "RUN_QUOTA_EXCEEDED");
  assert.equal(failure.errorType, "GraphRecursionError");
  assert.equal(failure.message, "A run or token budget for this thread is exhausted.");
  assert.equal(failure.severity, "warning");
  assert.equal(failure.retryable, false);
  assert.equal(failure.recovery, "none");
  assert.equal(failure.correlationId, "alpha.errors.run");
  assert.equal(failure.detail, "Recursion limit of 250 reached without hitting a stop condition");
});

test("the panel shows the budget claim beside the exception that actually stopped the run", async () => {
  route({ "/runs/r-1/events?limit=500": RECURSION_EVENTS });
  const timeline = await inspector.fetchRunTimeline("t-1", "r-1");
  const markup = renderToStaticMarkup(
    createElement(RunStatusPanel, {
      record: inspector.toRunRecord({ run_id: "r-1", status: "error" }),
      error: null,
      serving: inspector.servingModelsFrom(null, null),
      failure: inspector.runFailureFrom(timeline),
    })
  );

  // The code is shown…
  assert.match(markup, /RUN_QUOTA_EXCEEDED/);
  // …the real error type is shown…
  assert.match(markup, /GraphRecursionError/);
  // …and the panel says out loud that one code covers several causes, so the
  // budget sentence is not a statement about this run.
  assert.match(markup, /One code covers several causes/);
  assert.match(markup, /RecursionLimit/);
  // The server's own wording is preserved verbatim, attributed to the code.
  assert.match(markup, /A run or token budget for this thread is exhausted/);
});

test("a run with no run.error event reports no failure, which is not a failed read", async () => {
  route({ "/runs/r-1/events?limit=500": [] });
  const timeline = await inspector.fetchRunTimeline("t-1", "r-1");
  assert.equal(inspector.runFailureFrom(timeline), null);
  assert.equal(inspector.runFailureFrom(null), null);
});

test("an error code with no error_type does not invent one", () => {
  const failure = inspector.runFailureFrom({
    events: [
      {
        seq: 9,
        eventType: "run.error",
        category: "error",
        createdAt: null,
        severity: "error",
        taskId: null,
        content: "boom",
        metadata: { error_code: "RUN_EXECUTION_FAILED" },
      },
    ],
    complete: true,
  });
  assert.equal(failure.code, "RUN_EXECUTION_FAILED");
  assert.equal(failure.errorType, null);
  assert.equal(failure.retryable, null, "an absent boolean is unknown, not false");
  assert.equal(failure.message, null);
});

/* ══ 4. The delegation that the event stream did not record ══════════════════ */

test("the delegation ledger is read from the thread's own state channel", async () => {
  const calls = route({ "/threads/t-1/state": THREAD_STATE });
  const ledger = await inspector.fetchThreadDelegations("t-1");
  assert.deepEqual(calls, ["GET /threads/t-1/state"]);
  assert.equal(ledger.length, 3);
  assert.equal(ledger[0].id, "call_task_1");
  assert.equal(ledger[0].runId, "r-1");
  assert.equal(ledger[0].subagentType, "general-purpose");
  assert.equal(ledger[0].status, "completed");
  assert.equal(ledger[0].resultBrief, "Found the cause in tools.py:1818.");
});

test("a delegation tagged to another run is not shown under this one", () => {
  const ledger = [
    { id: "a", runId: "r-1", status: "completed" },
    { id: "b", runId: "r-OTHER", status: "completed" },
    { id: "c", runId: "r-1", status: "failed" },
  ];
  const { delegations, unattributed } = inspector.delegationsForRun(ledger, "r-1");
  assert.deepEqual(
    delegations.map((entry) => entry.id),
    ["a", "c"]
  );
  assert.equal(unattributed, 0);
});

test("an untagged legacy delegation is counted, never attributed to the open run", () => {
  const ledger = [
    { id: "a", runId: "r-1", status: "completed" },
    { id: "legacy", runId: null, status: "completed" },
  ];
  const { delegations, unattributed } = inspector.delegationsForRun(ledger, "r-1");
  assert.deepEqual(
    delegations.map((entry) => entry.id),
    ["a"],
    "an untagged entry must not be folded into whichever run is open"
  );
  assert.equal(unattributed, 1);
});

test("a thread with no delegations channel is unknown, not a run that delegated nothing", async () => {
  route({ "/threads/t-1/state": { values: { messages: [] }, next: [], metadata: {} } });
  const ledger = await inspector.fetchThreadDelegations("t-1");
  assert.equal(ledger, null);
  const markup = renderToStaticMarkup(
    createElement(DelegationPanel, {
      delegations: [],
      unattributed: 0,
      ledger,
      streamEventCount: 0,
      error: null,
      loading: false,
    })
  );
  assert.match(markup, /carried no/);
  assert.match(markup, /not the same as a run that delegated nothing/);
});

test("a ledger read that fails is an error, never an empty delegation list", async () => {
  setHttpFailure(500, "thread state store is unavailable");
  await assert.rejects(() => inspector.fetchThreadDelegations("t-1"), /thread state store is unavailable/);
  const markup = renderToStaticMarkup(
    createElement(DelegationPanel, {
      delegations: [],
      unattributed: 0,
      ledger: null,
      streamEventCount: null,
      error: "Request failed (HTTP 500). thread state store is unavailable",
      loading: false,
    })
  );
  assert.match(markup, /could not be read/);
  assert.match(markup, /thread state store is unavailable/);
  assert.match(markup, /unknown rather than none/);
  assert.doesNotMatch(markup, />Subagent delegation \(\d+\)/, "a failed read must not print a count");
});

test("a run that delegated with zero subagent events still shows the delegation", async () => {
  // Exactly the measured case: a genuinely successful delegation whose parent
  // run's event stream carries NO `subagent.*` rows, because those ride
  // `stream_mode: custom` frames the run never emitted.
  const markup = renderToStaticMarkup(
    createElement(DelegationPanel, {
      delegations: [
        {
          id: "call_task_1",
          runId: "r-1",
          description: "Research the failing test",
          subagentType: "general-purpose",
          status: "completed",
          stopReason: null,
          resultBrief: "Found the cause in tools.py:1818.",
          createdAt: "2026-09-28T15:07:50.000000+00:00",
        },
      ],
      unattributed: 0,
      ledger: [
        {
          id: "call_task_1",
          runId: "r-1",
          description: "Research the failing test",
          subagentType: "general-purpose",
          status: "completed",
          stopReason: null,
          resultBrief: "Found the cause in tools.py:1818.",
          createdAt: "2026-09-28T15:07:50.000000+00:00",
        },
      ],
      streamEventCount: 0,
      error: null,
      loading: false,
    })
  );
  assert.match(markup, /Research the failing test/);
  assert.match(markup, /general-purpose/);
  assert.match(markup, /Subagent delegation \(1\)/);
  // The disagreement between the two read paths is disclosed, not smoothed over.
  assert.match(markup, /recorded[\s\S]*0[\s\S]*subagent events/);
  assert.match(markup, /custom/);
});

test("a failed delegation is not painted as a completed one", () => {
  const entry = {
    id: "call_task_1",
    runId: "r-1",
    description: "Do the thing",
    subagentType: "general-purpose",
    status: "failed",
    stopReason: "turn_capped",
    resultBrief: null,
    createdAt: "2026-09-28T15:07:50.000000+00:00",
  };
  const markup = renderToStaticMarkup(
    createElement(DelegationPanel, {
      delegations: [entry],
      unattributed: 0,
      ledger: [entry],
      streamEventCount: 3,
      error: null,
      loading: false,
    })
  );
  assert.match(markup, />failed</);
  assert.match(markup, /stopped: turn_capped/);
  assert.doesNotMatch(markup, /text-emerald-500/, "a failed delegation must not wear the success colour");
  // A missing result brief is named as missing, not read as success.
  assert.match(markup, /no result brief/);
  assert.match(markup, /not a statement that the work succeeded/);
});

test("the whole view wires the delegation read into the panels", () => {
  const record = inspector.toRunRecord(ALIAS_RUN);
  const markup = renderView({
    ...BASE_STATE,
    record,
    delegations: null,
    loadingDelegations: false,
    errors: { delegations: "Request failed (HTTP 500). thread state store is unavailable" },
  });
  assert.match(markup, /Subagent delegation/);
  // A failed ledger read degrades only its own panel: the run record, the model
  // and the token panel all still render.
  assert.match(markup, /Terminal status/);
  assert.match(markup, /Tokens and model/);
  assert.match(markup, /thread state store is unavailable/);
});

/* ══ 5. Legibility: every header stat names its unit and its source ══════════ */

test("no stat tile prints a bare dash that could be zero, unknown, or N/A", () => {
  // A run whose every optional field is absent. Every tile must say "not
  // reported" in words, never a glyph standing in for three different states.
  const record = inspector.toRunRecord({ run_id: "r-1", status: "error" });
  const markup = renderToStaticMarkup(
    createElement(RunStatusPanel, {
      record,
      error: null,
      serving: { models: null, differsFromRecord: false },
      failure: null,
    })
  );
  // The assertion is on the VALUE SLOT (`>—<`), not on the whole panel: an em
  // dash inside explanatory prose is punctuation, a bare one in a value slot
  // is the ambiguity this rule exists to remove.
  assert.doesNotMatch(markup, />—/, "no tile may render the bare unknown glyph as its value");
  assert.match(markup, /not reported/);
});

test("a measured zero stays a real zero and is distinguishable from an absent value", () => {
  const record = inspector.toRunRecord({
    run_id: "r-1",
    status: "error",
    total_tokens: 0,
    total_input_tokens: 0,
    total_output_tokens: 0,
    llm_call_count: 0,
    message_count: 0,
  });
  const markup = renderToStaticMarkup(
    createElement(TokenPanel, { record, usage: null, error: null })
  );
  assert.match(markup, />0</, "a measured zero is a value, not an absence");
  // The "not reported" wording belongs only to the fields that are absent.
  assert.match(markup, /lead agent tokens[\s\S]{0,400}not reported/);
});

test("every stat tile carries a title and an aria-label naming the value", () => {
  const record = inspector.toRunRecord(ALIAS_RUN);
  const markup = renderToStaticMarkup(
    createElement(TokenPanel, { record, usage: null, error: null })
  );
  // The abbreviated "53.2k" is only checkable against a bill if the exact figure
  // is reachable, so the exact number and its unit live in the tooltip.
  assert.match(markup, /title="53\.2k, 53244 tokens, as reported on this run&#x27;s record"/);
  assert.match(markup, /aria-label="total tokens this run: 53\.2k, 53244 tokens/);
  // A count tile says it is a count, not a token figure.
  assert.match(markup, /title="2, llm_call_count/);
  // A measured zero is disambiguated from an absent value by its own wording.
  assert.match(
    markup,
    /zero here does not mean the run delegated nothing/,
    "0 subagent tokens must not read as a claim about delegation"
  );
});

test("an icon-only control is never left unlabelled", () => {
  const timeline = {
    events: [
      {
        seq: 1,
        eventType: "run.start",
        category: "trace",
        createdAt: "2026-09-28T15:00:00.000000+00:00",
        severity: "info",
        taskId: null,
        content: null,
        metadata: {},
      },
    ],
    complete: true,
  };
  const markup = renderToStaticMarkup(
    createElement(RunInspectorTimeline, { timeline, error: null })
  );
  // The severity badge names its own source: it is this client's classification,
  // not a severity the Gateway published.
  assert.match(markup, /classified by this client/);
  // The disclosure control is named, not a bare triangle.
  assert.match(markup, /payload &amp; metadata/);
  // Every filter button is a real button with a pressed state and a title.
  assert.match(markup, /aria-pressed="true"/);
});

test("a failed request surfaces the server's reason; an empty one says so", () => {
  const failed = renderToStaticMarkup(
    createElement(RunInspectorTimeline, {
      timeline: null,
      error: "Request failed (HTTP 429). rate limit exceeded, retry in 30s",
    })
  );
  assert.match(failed, /rate limit exceeded, retry in 30s/);
  assert.doesNotMatch(failed, /Event timeline \(\d+\)/, "a failed read prints no count");

  const empty = renderToStaticMarkup(createElement(RunInspectorTimeline, { timeline: { events: [], complete: true }, error: null }));
  assert.match(empty, /reported no persisted events/);
  assert.match(empty, /not a failed read/);
});

test("a workspace summary the Gateway declined to send never renders as six bare dashes", () => {
  const markup = renderToStaticMarkup(
    createElement(WorkspacePanel, {
      changes: {
        available: true,
        version: 1,
        // A comparison the Gateway sent with every count absent.
        summary: { created: null, modified: null, deleted: null, symlinkCreated: null, additions: null, deletions: null, truncated: false },
        files: [],
      },
      error: null,
    })
  );
  assert.doesNotMatch(markup, />—/, "an absent count must not print the bare unknown glyph");
  assert.match(markup, /not reported/);
  assert.match(markup, /files created/);
});

test("a per-model bucket with absent counts names which one, not a bare dash", () => {
  // `String(x ?? UNKNOWN_VALUE)` is how six confident labels once sat over six
  // bare dashes; the same mistake inside a split row reads as "— in / — out",
  // which is not a measurement of anything.
  const record = inspector.toRunRecord({
    run_id: "r-1",
    status: "success",
    token_usage_by_model: { "opencode-zen:space-bunny-free": { input_tokens: null, output_tokens: null, total_tokens: null } },
  });
  const markup = renderToStaticMarkup(createElement(TokenPanel, { record, usage: null, error: null }));
  assert.doesNotMatch(markup, />—/, "no slot in the split may print the bare unknown glyph");
  assert.match(markup, /input not reported/);
  assert.match(markup, /output not reported/);
});

/* ══ 6. The run list, rendered for real ════════════════════════════════════ */

/**
 * `RunsSection` above a stubbed transport, rendered through the real component.
 *
 * The three child surfaces it mounts (`RunInspectorSection`, `RunUsagePanel`,
 * `RunReplayControls`) are stubbed: they own their own transports, and the
 * defects pinned here are all in the list's own markup and state machine. The
 * list itself — the cards, the status tones, the stop control, the glance
 * tiles — is the real code under test.
 */
const runsSectionRef = emit(
  "RunsSection",
  compile("../components/sections/RunsSection.tsx", {
    jsx: true,
    specifiers: {
      ...jsxRuntime,
      "lucide-react": lucide,
      "@/lib/http": httpRef,
      "@/lib/time": timeRef,
      "@/lib/runs": runsRef,
      "@/lib/runs-inspector": inspectorRef,
      "@/components/ui": uiRef,
      // The three run-scoped surfaces, stubbed to a marker.
      "./RunInspectorSection": "./RunsSectionStubs.mjs",
      "./RunUsagePanel": "./RunsSectionStubs.mjs",
      "./RunReplayControls": "./RunsSectionStubs.mjs",
    },
  })
);
const { RunListCard, runListStatusTone } = await loadFile("RunsSection");

/**
 * One run's row, rendered for real.
 *
 * `RunsSection` reads its list in a `useEffect`, which `react-dom/server`
 * never runs — so asserting on the container would mean asserting on the
 * "No runs yet" frame the operator never sees. The row is therefore rendered
 * directly from the real exported component, over the real `runs.ts` mapping:
 * a raw Gateway row put through the REAL mapper, so an absent field arrives as
 * the `null` the client actually produces rather than as a hand-written `null`
 * this suite invented.
 */
const RUNS = await loadFile("runs");
const LIST_ROWS = [
  {
    run_id: "run-ok",
    thread_id: "t-1",
    assistant_id: "lead_agent",
    status: "success",
    model: "alpha-free",
    error: null,
    created_at: "2026-09-28T15:07:39.158838+00:00",
    updated_at: "2026-09-28T15:08:30.372347+00:00",
    total_tokens: 53244,
    llm_call_count: 2,
  },
  {
    run_id: "run-failed",
    thread_id: "t-1",
    assistant_id: "lead_agent",
    status: "error",
    model: "alpha-free",
    error: "Error code: 401 - Failed to authenticate request with Clerk",
    created_at: "2026-09-28T14:53:29.158838+00:00",
    total_tokens: 0,
    llm_call_count: 0,
  },
  {
    // No created_at, no token total: a real shape, not a defensive one.
    run_id: "run-bare",
    thread_id: "t-1",
    status: "quiesced_by_operator",
    model: "alpha-free",
    error: null,
  },
  {
    // A run still going, so the stop control is the one under test.
    run_id: "run-live",
    thread_id: "t-1",
    assistant_id: "lead_agent",
    status: "running",
    model: "alpha-free",
    error: null,
    created_at: "2026-09-29T09:00:00.000000+00:00",
    total_tokens: null,
    llm_call_count: 0,
  },
];

setHttpHandler(() => LIST_ROWS);
const mappedRows = await RUNS.listThreadRuns("t-1");

const renderCard = (index, { selected = false, cancelling = false } = {}) =>
  renderToStaticMarkup(
    createElement(RunListCard, {
      run: mappedRows[index],
      selected,
      cancelling,
      onSelect() {},
      onCancel() {},
    })
  );

// Row order, named once so every assertion below can say which run it means.
const [OK, FAILED, BARE, LIVE] = [0, 1, 2, 3];

test("a run with no reported creation time says so instead of rendering nothing", () => {
  // The row used to render `""` — a blank where a time belongs, which reads as
  // "no delay" rather than "the Gateway sent no time".
  const bare = renderCard(BARE);
  assert.match(bare, /created time not reported/);
  // A run that DID report one shows a real time, not the same placeholder.
  const ok = renderCard(OK);
  assert.match(ok, /Inspect run run-ok, status success, created \d/);
  assert.doesNotMatch(ok, /created time not reported/);
});

test("an unreported token total is never printed as a dash with a unit", () => {
  // `formatTokenCount(null)` is "—", so the row used to read "— tokens" *and*
  // "token totals not reported" at once, in the same tile.
  for (const index of [FAILED, BARE, LIVE]) {
    const markup = renderCard(index);
    assert.doesNotMatch(markup, /— tokens/, "no row may print a bare dash with a unit");
    assert.doesNotMatch(markup, />\u2014</, "no value slot may print the bare unknown glyph");
  }
  assert.match(renderCard(BARE), /token totals not reported by this Gateway/);
  // The measured run's figure is on screen, with its exact value reachable.
  const ok = renderCard(OK);
  assert.match(ok, /53\.2k tokens/);
  assert.match(ok, /title="53244 tokens, as reported by the Gateway on this run&#x27;s record"/);
  // A measured ZERO is a value and stays on screen.
  const zero = renderCard(FAILED);
  assert.match(zero, />0 tokens</);
  assert.doesNotMatch(zero, /token totals not reported/);
});

test("a failed run is not painted in the same muted grey as an unrecognised status", () => {
  // `statusTone` mapped error/failed to `undefined`, which `Badge` renders in
  // the same muted default as the catch-all `gray` — so a failed run and a
  // status from a newer Gateway were indistinguishable in the list.
  assert.equal(runListStatusTone("error"), "red");
  assert.equal(runListStatusTone("failed"), "red");
  assert.equal(runListStatusTone("quiesced_by_operator"), "gray");
  assert.equal(runListStatusTone("success"), "green");
  assert.equal(runListStatusTone("running"), "blue");
  // `Badge tone="red"` is what the failure branch must reach; the grey branch is
  // the muted `bg-muted text-muted-foreground` default.
  assert.match(renderCard(FAILED), /text-red-600/);
  assert.doesNotMatch(renderCard(FAILED), /bg-muted text-muted-foreground/);
  const foreign = renderCard(BARE);
  assert.doesNotMatch(foreign, /text-red-600/, "an unrecognised status is not a failure");
  assert.match(foreign, /bg-muted text-muted-foreground/, "it wears the neutral tone instead");
  assert.match(foreign, /quiesced_by_operator/, "and it is still shown verbatim");
});

test("a run card is a labelled control, not an anonymous click target", () => {
  const markup = renderCard(OK);
  assert.match(markup, /role="button"/);
  assert.match(markup, /aria-label="Inspect run run-ok, status success, created \d/);
  assert.match(markup, /aria-pressed="false"/);
  // The full run id is reachable even though the row shows a truncated one.
  assert.match(markup, /title="run-ok"/);
  assert.match(renderCard(OK, { selected: true }), /aria-pressed="true"/);
});

test("the configured model is not presented as the model that served the run", () => {
  const markup = renderCard(OK);
  assert.match(markup, /configured model/);
  assert.doesNotMatch(
    markup,
    /model that served the run/,
    "the configured alias must not be labelled as the provider that answered"
  );
});

test("the stop control disables while its request is in flight", () => {
  // Without this, a double-click sent two POST /cancel for one run.
  const idle = renderCard(LIVE);
  assert.match(idle, /Stop this run</);
  // Anchored on the attribute, not the word: the button's class list always
  // carries `disabled:opacity-40`, which is a style hook and not a state.
  assert.doesNotMatch(idle, /<button[^>]*\sdisabled=""/, "an idle stop control is enabled");
  const busy = renderCard(LIVE, { cancelling: true });
  assert.match(busy, /<button[^>]*\sdisabled=""/, "the control must disable while the request is in flight");
  assert.match(busy, /Stopping…/);
  assert.match(busy, /The cancellation request is in flight/);
  // A finished run carries no stop control at all.
  assert.doesNotMatch(renderCard(OK), /Stop this run/);
});

/* ══ 7. The delivery receipt is not a verification of the run ═══════════════ */

test("the delivery receipt is not labelled a verification of the run", () => {
  const markup = renderToStaticMarkup(
    createElement(DeliveryPanel, {
      receipt: {
        presented: 1,
        stage: "presented",
        satisfied: true,
        verificationSource: "outputs_changed",
        requirement: "present_files_matches_produced_output",
        paths: ["/mnt/user-data/outputs/demo.md"],
        producedPaths: ["/mnt/user-data/outputs/demo.md"],
        presentedPaths: ["/mnt/user-data/outputs/demo.md"],
        matchedPaths: ["/mnt/user-data/outputs/demo.md"],
        byTool: {},
        seq: 13,
        createdAt: "2026-09-28T15:10:41.163142+00:00",
      },
      manifest: { fileCount: 1 },
      artifacts: [],
      timelineError: null,
      error: null,
    })
  );
  assert.match(markup, /not a verdict on the answer and not a verification of the run/);
  assert.doesNotMatch(markup, />verified against</, "the receipt's own field must not read as a run verdict");
  assert.match(markup, /how that check was made/);
});
