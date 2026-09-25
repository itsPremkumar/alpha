import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";
import { moduleUrl } from "./test-modules.mjs";

const { createApiClient, ApiClientError } = await import(moduleUrl("api-client"));
const { consumeChatStream } = await import(moduleUrl("chat-stream"));

const compile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const helperSource = readFileSync(new URL("./chat-request-error.ts", import.meta.url), "utf8");
const { chatRequestErrorMessage } = await import(`data:text/javascript;base64,${Buffer.from(compile(helperSource)).toString("base64")}`);
const source = readFileSync(new URL("../components/ChatView.tsx", import.meta.url), "utf8");
const ast = ts.createSourceFile("ChatView.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let sendSource;
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(ast) === "sendMessage") {
    sendSource = node.initializer.getText(ast);
  }
  ts.forEachChild(node, visit);
}
visit(ast);
assert.ok(sendSource);
assert.match(sendSource, /on_disconnect:\s*["']continue["']/);

async function send(fetchResponse, { draft = "  retry me  ", newerDraft = "", abort = false, failPostStream = false } = {}) {
  const state = { messages: [], saved: [], input: draft, error: null, loading: false, suggestions: [], followUps: 0, runs: 0, autoplay: 0 };
  const setter = (key) => (value) => { state[key] = typeof value === "function" ? value(state[key]) : value; };
  const abortRef = { current: null };
  let controllerAborted = false;
  const dependencies = {
    activeThreadId: "thread-1", isLoading: false, activeBot: null, selectedModel: "model", planMode: false,
    suggestionsOn: true, messages: [], abortRef,
    setInput: setter("input"), setIsLoading: setter("loading"), setRequestError: setter("error"),
    setMessages: setter("messages"), setSuggestions: setter("suggestions"), setUsage: () => {},
    autoTriggerCommand: async () => null,
    appendLocalMessages: (_tid, messages) => state.saved.push(...messages),
    chatRequestErrorMessage,
    listThreadRuns: async () => {
      state.runs++;
      return [];
    },
    fetchTokenUsage: async () => {
      if (failPostStream) {
        abortRef.current.abort();
        controllerAborted = abortRef.current.signal.aborted;
        throw new Error("secret usage diagnostics");
      }
      return null;
    },
    suggestFollowUps: async () => { state.followUps++; return ["Next?"]; },
    // Module-scope bindings sendMessage now references (TTS autoplay). Injected
    // default OFF — mirrors the shipped config/localStorage default, so the
    // real consumer must never fire in any scenario below.
    readAutoplayEnabled: () => false,
    autoplaySpeak: async () => { state.autoplay++; return false; },
    updateLion: () => {},
    fetch: async () => {
      if (newerDraft) state.input = newerDraft;
      if (abort) abortRef.current.abort();
      return fetchResponse(abortRef.current);
    },
  };
  Object.assign(dependencies, {
    ApiClientError,
    apiFetch: createApiClient({ fetch: dependencies.fetch, getCookie: () => "" }),
    consumeChatStream: (response, options) => consumeChatStream(response, {
      ...options,
      reconnect: async () => { throw new Error("Unexpected reconnect in legacy fixture"); },
    }),
  });
  const run = new Function(...Object.keys(dependencies), `${compile(`const sendMessage = ${sendSource};`)}; return sendMessage;`)(...Object.values(dependencies));
  await run(draft);
  if (failPostStream) assert.equal(controllerAborted, true);
  assert.equal(state.loading, false);
  assert.equal(abortRef.current, null);
  assert.equal(state.autoplay, 0); // autoplay default OFF ⇒ consumer never invoked
  return state;
}

function assertFailed(state) {
  assert.equal(state.messages.filter((message) => message.role === "assistant").length, 0);
  assert.equal(state.saved.filter((message) => message.role === "assistant").length, 0);
  assert.equal(state.followUps, 0);
  assert.equal(state.runs, 0);
  assert.equal(state.error.threadId, "thread-1");
  assert.equal(state.error.draft, "  retry me  ");
}

for (const status of [400, 401, 403, 404, 429, 500, 502, 503]) {
  test(`HTTP ${status} never creates or persists invented success or reads the error body`, async () => {
    const state = await send(() => ({
      ok: false, status,
      get statusText() { throw new Error("secret status text read"); },
      get body() { throw new Error("secret HTML body read"); },
      text() { throw new Error("secret body read"); },
    }));
    assertFailed(state);
    assert.match(state.error.message, new RegExp(`HTTP ${status}`));
    assert.equal(state.input, "  retry me  ");
    assert.equal(state.error.partial, "");
    assert.doesNotMatch(state.error.message, /secret|evaluated the workflow/);
  });
}

test("invalid statuses and untrusted details cannot enter the message", () => {
  for (const status of [undefined, NaN, Infinity, -1, 600, 500.5, "<script>secret</script>", { toString() { throw Error("coercion"); } }]) {
    const message = chatRequestErrorMessage({ kind: "http", status, message: "secret", body: "<html>secret</html>" });
    assert.doesNotMatch(message, /HTTP|secret|html|script/);
  }
});

test("fetch exceptions are visible, sanitized, and preserve a newer draft", async () => {
  const state = await send(() => { throw new Error("Bearer secret <html>private</html>"); }, { newerDraft: "new draft" });
  assertFailed(state);
  assert.equal(state.input, "new draft");
  assert.match(state.error.message, /connection/);
  assert.doesNotMatch(state.error.message, /Bearer|secret|html|private/);
});

for (const partial of ["", "actual partial answer"]) {
  test(`stream failure with ${partial ? "partial content" : "no content"} is not saved as success`, async () => {
    let reads = 0;
    let released = false;
    const state = await send(() => ({ ok: true, body: { getReader: () => ({
      read: async () => {
        if (partial && reads++ === 0) return { done: false, value: new TextEncoder().encode(partial) };
        throw new Error("secret transport diagnostics");
      },
      releaseLock: () => { released = true; },
    }) } }));
    assertFailed(state);
    assert.equal(state.error.partial, partial);
    assert.match(state.error.message, /interrupted.*incomplete.*not been saved/);
    assert.doesNotMatch(state.error.message, /secret/);
    assert.equal(released, true);
  });
}

for (const body of [null, new ReadableStream({ start(controller) { controller.close(); } })]) {
  test(`successful HTTP with ${body ? "empty" : "missing"} body is not an assistant success`, async () => {
    const state = await send(() => ({ ok: true, body }));
    assertFailed(state);
    assert.match(state.error.message, /no response content/);
  });
}

test("local abort is reported without claiming confirmed server cancellation", async () => {
  const state = await send(() => { throw new DOMException("secret", "AbortError"); }, { abort: true });
  assertFailed(state);
  assert.match(state.error.message, /stopped locally.*cancellation is not confirmed/);
});

test("late Stop during post-stream metadata failure keeps and saves the delivered answer", async () => {
  const state = await send(() => new Response("complete answer"), { failPostStream: true });
  assert.equal(state.error, null);
  assert.equal(state.messages.at(-1).content, "complete answer");
  assert.equal(state.saved.filter((message) => message.role === "assistant").length, 1);
  assert.equal(state.input, "");
});

test("mid-stream Stop with partial content shows stopped error with partial preserved", async () => {
  let reads = 0;
  const state = await send((controller) => ({ ok: true, body: { getReader: () => ({
    read: async () => {
      if (reads++ === 0) return { done: false, value: new TextEncoder().encode("partial before stop") };
      controller.abort();
      throw new DOMException("secret", "AbortError");
    },
    releaseLock: () => {},
  }) } }));
  assertFailed(state);
  assert.equal(state.error.partial, "partial before stop");
  assert.match(state.error.message, /stopped locally.*not been saved/);
  assert.equal(state.input, "  retry me  ");
});

test("successful streamed content is preserved and saved once", async () => {
  const state = await send(() => new Response("real answer"));
  assert.equal(state.error, null);
  assert.equal(state.input, "");
  assert.equal(state.messages.at(-1).content, "real answer");
  assert.equal(state.saved.filter((message) => message.role === "assistant").length, 1);
  assert.equal(state.saved.at(-1).content, "real answer");
  assert.equal(state.followUps, 1);
});

test("error UI is thread-scoped, accessible, and uses plain text for incomplete content", () => {
  assert.match(source, /requestError && requestError.threadId === activeThreadId/);
  assert.match(source, /role="alert"/);
  assert.match(source, /<ErrorBox\s+message=\{requestError.message\}/);
  assert.match(source, /<pre[^>]*>\{requestError.partial\}<\/pre>/);
  assert.doesNotMatch(source, /has received your request and evaluated the workflow/);
});
