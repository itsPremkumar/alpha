// tool-status-honesty.test.mjs — tool-call outcome honesty (wave-2 audit).
//
// The frontend used to render a green check and the word "executed" for EVERY
// tool call: `ToolCall.status` was never read, and the reducer never populated
// `output`/`status` (it emitted only `{id, name, args}` and filtered `ToolMessage`
// rows out of the stream entirely). An errored tool therefore looked exactly like
// a successful one, and the "Result" panel was dead code.
//
// These tests are REAL, not structural:
//
//  * ToolPill is mounted and server-rendered through `react-dom/server`, so the
//    assertions run against actual emitted markup (icon name, colour class,
//    visible wording, data attribute) — not a source-code regex.
//  * The reducer assertions feed real LangGraph-shaped SSE frames through
//    `reduceSse` and inspect `streamMessages` output.
//
// Node test (node --test src/lib/*.test.mjs): transpiles the TS/TSX with the
// local `typescript`, rewrites bare/bare-ish specifiers to file: URLs and loads
// the result as a data: URL — no dev server, no browser, no DOM shim needed
// because the component is rendered with the server renderer.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const here = (relative) => fileURLToPath(new URL(relative, import.meta.url));
const resolveUrl = (specifier) => pathToFileURL(require.resolve(specifier)).href;
const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const transpile = (source, extra = {}) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, ...extra },
}).outputText;
const load = async (code) => import(`data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`);

/* ── ToolPill, rendered for real ─────────────────────────────────────────── */

const pillCode = transpile(read("../components/ToolPill.tsx"), { jsx: ts.JsxEmit.ReactJSX })
  // `@/types/chat` is a type-only import, so it is elided; the value imports
  // are pointed at the real packages on disk.
  .replace(/from\s+"react"/, `from "${resolveUrl("react")}"`)
  .replace(/from\s+"react\/jsx-runtime"/, `from "${resolveUrl("react/jsx-runtime")}"`)
  .replace(/from\s+"lucide-react"/, `from "${pathToFileURL(here("../../node_modules/lucide-react/dist/esm/lucide-react.js")).href}"`);

const { ToolPill, toolStatusView } = await load(pillCode);
const { createElement } = await import(resolveUrl("react"));
const { renderToStaticMarkup } = await import(resolveUrl("react-dom/server"));

const render = (toolCall) => renderToStaticMarkup(createElement(ToolPill, { toolCall }));
const renderOpen = (toolCall) => renderToStaticMarkup(createElement(ToolPill, { toolCall, defaultOpen: true }));

//: Icon class lucide emits, e.g. `lucide lucide-circle-check`.
const icon = (markup, name) => new RegExp(`lucide-${name}\\b`).test(markup);

/* ── reduceSse, fed real LangGraph frames ─────────────────────────────────── */

const reducerCode = transpile(read("./sse-reducer.ts"));
const { createSseState, reduceSse, streamMessages, honestToolStatus, sseErrorDetail, SSE_CHANNEL_EVENTS }
  = await load(reducerCode);

const reduce = (frames, state = createSseState("run-1")) => frames.reduce(reduceSse, state);

/** AI message that issues one tool call. */
const aiWithCall = (id, eventId, { content = "", name = "search", args = { query: "x" } } = {}) => ({
  event: "messages",
  id: eventId,
  data: [{ type: "AIMessageChunk", id, content, tool_call_chunks: [{ id: "call-1", index: 0, name, args }] }, {}],
});

/** Tool result, as LangChain serialises it onto the wire. */
const toolResult = (eventId, extra = {}) => ({
  event: "messages",
  id: eventId,
  data: [{ type: "tool", id: "toolmsg-1", tool_call_id: "call-1", name: "search", content: "3 results", status: "success", ...extra }, {}],
});

const onlyToolCall = (state) => streamMessages(state)[0].toolCalls[0];

/* ══ 1. A failed tool call must never render as success ════════════════════ */

test("an errored tool renders as an error, not a green check", () => {
  const markup = render({ id: "call-1", name: "search", args: {}, status: "error", output: "boom" });
  // The old markup: a green CheckCircle2 plus the word "executed".
  assert.doesNotMatch(markup, /lucide-circle-check/, "errored tool still draws a success check");
  assert.doesNotMatch(markup, /text-emerald-500/, "errored tool still draws success colour");
  assert.doesNotMatch(markup, />executed</, "errored tool is still labelled executed");
  // The truthful rendering: error icon, error colour, error wording.
  assert.ok(icon(markup, "octagon-alert"), "expected the error icon");
  assert.match(markup, /text-red-500/);
  assert.match(markup, />error</);
  assert.match(markup, /data-tool-status="error"/);
});

test("a failed tool surfaces the error text the run actually reported", () => {
  const markup = renderOpen({ id: "call-1", name: "search", args: {}, status: "failed", output: "Error: 401 unauthorized" });
  assert.doesNotMatch(markup, /lucide-circle-check/);
  assert.ok(icon(markup, "circle-x"), "expected the failure icon");
  assert.match(markup, /Error output/);
  assert.match(markup, /Error: 401 unauthorized/);
  assert.match(markup, /data-tool-status="failed"/);
});

test("failed and error are textually distinct, not the same red word", () => {
  const failed = render({ id: "c", name: "t", args: {}, status: "failed" });
  const errored = render({ id: "c", name: "t", args: {}, status: "error" });
  assert.match(failed, />failed</);
  assert.match(errored, />error</);
  assert.match(failed, /data-tool-status="failed"/);
  assert.match(errored, /data-tool-status="error"/);
});

/* ══ 2. Missing / unknown status must never render as success ══════════════ */

test("a tool call with no status at all is not drawn as success", () => {
  const markup = render({ id: "call-1", name: "search", args: {} });
  assert.doesNotMatch(markup, /lucide-circle-check/, "unreported status still draws a success check");
  assert.doesNotMatch(markup, /text-emerald-500/);
  assert.doesNotMatch(markup, />executed</);
  assert.match(markup, /data-tool-status="not-reported"/);
  assert.match(markup, /no result reported/);
  assert.ok(icon(markup, "circle-slash"), "expected the not-reported icon");
});

test("an explicit unknown status is distinct from success AND from missing", () => {
  const unknown = render({ id: "c", name: "t", args: {}, status: "unknown" });
  const missing = render({ id: "c", name: "t", args: {} });
  for (const markup of [unknown, missing]) {
    assert.doesNotMatch(markup, /lucide-circle-check/);
    assert.doesNotMatch(markup, /text-emerald-500/);
  }
  assert.match(unknown, /data-tool-status="unknown"/);
  assert.match(missing, /data-tool-status="not-reported"/);
  assert.notEqual(unknown, missing, "unknown and not-reported must render differently");
  assert.ok(icon(unknown, "circle-help"), "expected the unknown icon");
});

test("a partial result is neither success nor failure", () => {
  const markup = render({ id: "c", name: "t", args: {}, status: "partial", output: "truncated at 10 rows" });
  assert.doesNotMatch(markup, /lucide-circle-check/);
  assert.doesNotMatch(markup, /text-emerald-500/);
  assert.match(markup, /text-amber-500/);
  assert.match(markup, /data-tool-status="partial"/);
  assert.ok(icon(markup, "triangle-alert"));
});

test("only a reported completion renders as success", () => {
  const markup = render({ id: "c", name: "t", args: {}, status: "completed", output: "3 results" });
  assert.match(markup, /lucide-circle-check/);
  assert.match(markup, /text-emerald-500/);
  assert.match(markup, />completed</);
  assert.match(markup, /data-tool-status="success"/);
});

test("running is not success either", () => {
  const markup = render({ id: "c", name: "t", args: {}, status: "running" });
  assert.doesNotMatch(markup, /lucide-circle-check/);
  assert.doesNotMatch(markup, /text-emerald-500/);
  assert.match(markup, /data-tool-status="running"/);
  assert.match(markup, />running</);
});

/* ══ 3. toolStatusView: the single source of the mapping ═══════════════════ */

test("toolStatusView maps every declared status and never invents success", () => {
  assert.deepEqual(
    ["running", "completed", "failed", "partial", "error", "unknown", undefined]
      .map((status) => toolStatusView(status).state),
    ["running", "success", "failed", "partial", "error", "unknown", "not-reported"],
  );
  assert.equal(toolStatusView(undefined).isSuccess, false);
  assert.equal(toolStatusView("unknown").isSuccess, false);
  assert.equal(toolStatusView("completed").isSuccess, true);
  // A status outside the known set is not success, and not a crash.
  assert.equal(toolStatusView("weird").isSuccess, false);
  assert.equal(toolStatusView("weird").state, "not-reported");
  assert.equal(toolStatusView(undefined).isFailure, true);
});

test("every state has its own glyph and its own wording, and none but success wears green", () => {
  const table = [
    { status: "running", state: "running", label: "running", glyph: "circle-dashed" },
    { status: "completed", state: "success", label: "completed", glyph: "circle-check" },
    { status: "failed", state: "failed", label: "failed", glyph: "circle-x" },
    { status: "partial", state: "partial", label: "partial", glyph: "triangle-alert" },
    { status: "error", state: "error", label: "error", glyph: "octagon-alert" },
    { status: "unknown", state: "unknown", label: "unknown", glyph: "circle-help" },
    { status: undefined, state: "not-reported", label: "no result reported", glyph: "circle-slash" },
  ];
  const rendered = table.map(({ status, state, label, glyph }) => {
    const view = toolStatusView(status);
    assert.equal(view.state, state, `status ${String(status)} must resolve to ${state}`);
    assert.equal(view.label, label);
    assert.equal(view.isSuccess, state === "success");
    const markup = render({ id: "c", name: "t", args: {}, status });
    assert.match(markup, new RegExp(`data-tool-status="${state}"`));
    assert.ok(icon(markup, glyph), `${state} must draw ${glyph}`);
    if (state !== "success") {
      assert.doesNotMatch(markup, /lucide-circle-check/, `${state} must not draw the success check`);
      assert.doesNotMatch(markup, /text-emerald-500/, `${state} must not wear the success colour`);
      assert.doesNotMatch(view.label, /complet|success|executed|\bok\b/i, `${state} wording claims success`);
    }
    return markup;
  });
  assert.equal(new Set(table.map((row) => row.label)).size, table.length, "labels must be unique");
  assert.equal(new Set(table.map((row) => row.glyph)).size, table.length, "glyphs must be unique");
  assert.equal(new Set(rendered).size, rendered.length, "every state must render differently");
});

/* ══ 4. The reducer actually delivers results to the component ═════════════ */

test("a tool result reaches its tool call instead of being dropped", () => {
  const state = reduce([aiWithCall("answer", "100-1"), toolResult("100-2")]);
  assert.deepEqual(streamMessages(state)[0].toolCalls, [
    { id: "call-1", name: "search", args: { query: "x" }, output: "3 results", status: "completed" },
  ]);
});

test("a tool result is never rendered as an assistant answer of its own", () => {
  const state = reduce([aiWithCall("answer", "100-1"), toolResult("100-2")]);
  assert.equal(streamMessages(state).length, 1);
  assert.equal(streamMessages(state)[0].id, "answer");
});

test("a tool call with no result yet carries no status at all", () => {
  const call = onlyToolCall(reduce([aiWithCall("answer", "100-1")]));
  assert.equal("status" in call, false, "an in-flight call must not claim a status");
  assert.equal("output" in call, false);
  // …and that is exactly what the pill renders as "not reported", not success.
  assert.equal(toolStatusView(call.status).isSuccess, false);
  assert.match(render(call), /data-tool-status="not-reported"/);
});

test("an errored result reaches the pill as an error", () => {
  const call = onlyToolCall(reduce([
    aiWithCall("answer", "100-1"),
    toolResult("100-2", { content: "Error: tool blew up", status: "error" }),
  ]));
  assert.equal(call.status, "error");
  const markup = renderOpen(call);
  assert.doesNotMatch(markup, /lucide-circle-check/);
  assert.match(markup, /data-tool-status="error"/);
  assert.match(markup, /Error: tool blew up/, "the Result branch is no longer dead code");
});

test("the backend's success-default trap: status:\"success\" + error meta = error", () => {
  // LangChain defaults ToolMessage.status to "success"; the authoritative
  // verdict lives in the agent_workspace_tool_meta stamp.
  const call = onlyToolCall(reduce([
    aiWithCall("answer", "100-1"),
    toolResult("100-2", {
      content: "Error: TimeoutError: took too long",
      additional_kwargs: {
        agent_workspace_tool_meta: {
          status: "error", error_type: "transient", source: "exception", recoverable_by_model: false,
        },
      },
    }),
  ]));
  assert.equal(call.status, "error", "a default 'success' must lose to the tool-meta stamp");
  assert.doesNotMatch(renderOpen(call), /lucide-circle-check/);
});

test("verdict precedence matches the backend's _honest_tool_status", () => {
  const resolve = (message) => honestToolStatus(message);
  // 1. LangChain's own failure marker wins.
  assert.equal(resolve({ status: "error" }), "error");
  // 2. tool meta beats the bare field.
  assert.equal(resolve({ status: "success", additional_kwargs: { agent_workspace_tool_meta: { status: "partial_success" } } }), "partial");
  // 3. the receipt stamp is consulted next.
  assert.equal(resolve({ additional_kwargs: { agent_workspace_tool_receipt: { status: "error" } } }), "error");
  // 4. a structured subagent failure is honoured over a default "success".
  assert.equal(resolve({ status: "success", additional_kwargs: { subagent_status: "timed_out" } }), "error");
  // 5. a bare recognised field is used only when nothing stronger exists.
  assert.equal(resolve({ status: "success" }), "completed");
  assert.equal(resolve({ status: "partial_success" }), "partial");
  assert.equal(resolve({ status: "failed" }), "failed");
  // 6. absent / blank / unrecognised never become success.
  assert.equal(resolve({}), "unknown");
  assert.equal(resolve({ status: "" }), "unknown");
  assert.equal(resolve({ status: "weird" }), "unknown");
  assert.equal(resolve({ status: null }), "unknown");
});

test("a result with no resolvable status is unknown, never success", () => {
  const call = onlyToolCall(reduce([
    aiWithCall("answer", "100-1"),
    toolResult("100-2", { status: undefined, content: "some output" }),
  ]));
  assert.equal(call.status, "unknown");
  assert.doesNotMatch(renderOpen(call), /lucide-circle-check/);
  assert.match(renderOpen(call), /data-tool-status="unknown"/);
  assert.match(renderOpen(call), /some output/);
});

test("a tool result with no tool_call_id is never guessed onto a call", () => {
  const orphan = { event: "messages", id: "100-2", data: [{ type: "tool", id: "t", content: "secret", status: "success" }, {}] };
  const state = reduce([aiWithCall("answer", "100-1"), orphan]);
  assert.deepEqual(onlyToolCall(state), { id: "call-1", name: "search", args: { query: "x" } });
  assert.equal(streamMessages(state).length, 1, "an unattributable tool result is not an answer");
});

/* ══ 5. Streaming stability: no flicker, no unbounded growth ═══════════════ */

test("result delivery converges under replay and reordering — no flicker", () => {
  const frames = [
    aiWithCall("answer", "100-1"),
    toolResult("100-2", { content: "second", status: "error" }),
    { event: "messages", id: "100-3", data: [{ type: "AIMessageChunk", id: "answer", content: "done" }, {}] },
  ];
  const permutations = (items) => items.length
    ? items.flatMap((item, i) => permutations(items.filter((_, j) => i !== j)).map((rest) => [item, ...rest]))
    : [[]];
  const expected = JSON.stringify(streamMessages(reduce([...frames, ...frames])));
  for (const order of permutations(frames)) {
    // Duplicated delivery (reconnect replay) must not change the outcome.
    assert.equal(JSON.stringify(streamMessages(reduce([...order, ...order]))), expected);
  }
  assert.equal(onlyToolCall(reduce(frames)).status, "error");
});

test("the highest event id wins when a call is re-reported", () => {
  const state = reduce([
    aiWithCall("answer", "100-1"),
    toolResult("100-3", { content: "final", status: "error" }),
    toolResult("100-2", { content: "stale", status: "success" }),
  ]);
  assert.equal(onlyToolCall(state).status, "error");
  assert.equal(onlyToolCall(state).output, "final");
});

test("a tool call's status never changes once the message is built", () => {
  // Late text must not drop the result: re-merging re-projects from toolResults.
  const first = reduce([aiWithCall("answer", "100-1"), toolResult("100-2", { content: "r", status: "error" })]);
  const later = reduce([{ event: "messages", id: "100-9", data: [{ type: "AIMessageChunk", id: "answer", content: " more" }, {}] }], first);
  assert.equal(onlyToolCall(later).status, "error");
  assert.equal(onlyToolCall(later).output, "r");
});

test("a result we cannot sequence fails closed rather than being mis-attributed", () => {
  const frame = toolResult("100-2");
  delete frame.id;
  assert.equal(reduce([aiWithCall("answer", "100-1"), frame]).failure, "protocol");
});

test("tool output and the result map are bounded, and truncation is disclosed", () => {
  const huge = "x".repeat(50_000);
  const state = reduce([aiWithCall("answer", "100-1"), toolResult("100-2", { content: huge })]);
  const call = onlyToolCall(state);
  assert.ok(call.output.length < huge.length, "output must be capped");
  assert.match(call.output, /\[\+\d+ chars truncated\]$/, "truncation must be visible, not silent");
  assert.ok(state.toolResults.size <= 2048, "result map must be capped");
});

/* ══ 6. The `event: error` payload reaches the reducer output ══════════════ */

test("an error frame's code, message and correlation id reach the reducer output", () => {
  const state = reduce([{ event: "error", id: "100-7", data: { code: "run_failed", message: "model unavailable", correlation_id: "req-abc-123" } }]);
  assert.equal(state.failure, "server");
  assert.deepEqual(state.error, { code: "run_failed", message: "model unavailable", correlationId: "req-abc-123", eventId: "100-7" });
});

test("the error payload is parsed across the shapes the Gateway sends", () => {
  assert.deepEqual(sseErrorDetail({ detail: "boom" }, "100-1"), { message: "boom", eventId: "100-1" });
  assert.deepEqual(sseErrorDetail({ error: "boom" }), { message: "boom" });
  assert.deepEqual(sseErrorDetail({ error_code: "E42", reason: "nope" }), { code: "E42", message: "nope" });
  assert.deepEqual(sseErrorDetail({ request_id: "r-9" }), { correlationId: "r-9" });
  // No payload, no invented detail.
  assert.deepEqual(sseErrorDetail({}), {});
  assert.deepEqual(sseErrorDetail(null), {});
  assert.deepEqual(sseErrorDetail("not-an-object"), {});
});

test("an oversized error message is capped rather than stored whole", () => {
  const detail = sseErrorDetail({ message: "y".repeat(9000) });
  assert.ok(detail.message.length < 9000);
  assert.match(detail.message, /\[\+\d+ chars truncated\]$/);
});

test("a successful run reports no error detail", () => {
  const state = reduce([aiWithCall("answer", "100-1"), { event: "end", data: null }]);
  assert.equal(state.failure, undefined);
  assert.equal(state.error, undefined);
  assert.equal(state.ended, true);
});

/* ══ 7. The dropped non-message channels are retained ══════════════════════ */

test("custom, updates, debug, tasks and checkpoints are no longer discarded", () => {
  for (const event of SSE_CHANNEL_EVENTS) {
    const state = reduce([{ event, id: `100-${event}`, data: { note: event } }]);
    assert.ok(state.channels[event], `${event} payload was dropped`);
    assert.deepEqual(state.channels[event].data, { note: event });
    assert.equal(state.channels[event].event, event);
    assert.equal(state.channels[event].truncated, false);
  }
  // …and they never leak into the rendered conversation.
  assert.deepEqual(streamMessages(reduce([{ event: "custom", id: "100-1", data: { note: "hi" } }])), []);
});

test("subgraph channels stay excluded and channel payloads stay bounded", () => {
  const state = reduce([{ event: "updates|child", id: "100-1", data: { node: "child" } }]);
  assert.deepEqual(state.channels, {});
  const big = reduce([{ event: "debug", id: "100-2", data: { blob: "z".repeat(50_000) } }]);
  assert.equal(big.channels.debug.truncated, true);
  assert.equal(big.channels.debug.data.truncated, true);
  // Only the latest frame per channel is kept, so the key set stays fixed.
  const latest = reduce([{ event: "custom", id: "100-4", data: { n: 4 } }], reduce([{ event: "custom", id: "100-3", data: { n: 3 } }]));
  assert.equal(latest.channels.custom.eventId, "100-4");
  assert.deepEqual(Object.keys(latest.channels), ["custom"]);
});
