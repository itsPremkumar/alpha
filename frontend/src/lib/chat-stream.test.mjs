import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { moduleUrl } from "./test-modules.mjs";

const { consumeChatStream, StreamRunFailure } = await import(moduleUrl("chat-stream"));
const frame = (event, id, data) => `event: ${event}\nid: ${id}\ndata: ${JSON.stringify(data)}\n\n`;
const chunk = (id, text) => frame("messages", id, [{ type: "AIMessageChunk", id: "answer", content: text }, {}]);
const response = (body) => new Response(body, { headers: { "Content-Type": "text/event-stream", "Content-Location": "/threads/thread-1/runs/run-1" } });
const flush = () => new Promise((resolve) => setImmediate(resolve));

test("reconnect waits for retry, deduplicates replay, emits a gap and rejects later content", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const updates = [];
  const events = [];
  const requests = [];
  const pending = consumeChatStream(response(`retry: 125\n\n${chunk("100-1", "Partial")}`), {
    threadId: "thread-1", signal: new AbortController().signal,
    onUpdate: (messages, runId) => updates.push({ messages, runId }),
    onEvent: (event) => events.push(event),
    reconnect: async (path, options) => {
      requests.push({ path, options });
      return response(chunk("100-1", "Partial") + chunk("100-2", " replayed")
        + frame("gap", "100-3", { message: "secret" }) + chunk("100-4", " must not appear") + frame("end", "100-5", null));
    },
  });
  const rejected = assert.rejects(pending, (error) => error.kind === "response");
  await flush();
  assert.equal(requests.length, 0);
  t.mock.timers.tick(124);
  await flush();
  assert.equal(requests.length, 0);
  t.mock.timers.tick(1);
  await rejected;
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, "/threads/thread-1/runs/run-1/join");
  assert.equal(requests[0].options.headers["Last-Event-ID"], "100-1");
  assert.deepEqual(updates.at(-1), { messages: [{ id: "answer", runId: "run-1", content: "Partial replayed" }], runId: "run-1" });
  assert.deepEqual(events, [{ type: "replay-gap", runId: "run-1", lastEventId: "100-2", eventId: "100-3" }]);
});

test("retry delay is capped and retained across connections", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let requests = 0;
  const pending = consumeChatStream(response(`retry: 999999999999999999999\n\n${chunk("1", "A")}`), {
    threadId: "thread-1", signal: new AbortController().signal, onUpdate: () => {},
    reconnect: async () => response(++requests === 1 ? `retry: invalid\n\n${chunk("2", "B")}` : frame("end", "3", null)),
  });
  await flush();
  t.mock.timers.tick(29999);
  await flush();
  assert.equal(requests, 0);
  t.mock.timers.tick(1);
  await flush();
  assert.equal(requests, 1);
  t.mock.timers.tick(29999);
  await flush();
  assert.equal(requests, 1);
  t.mock.timers.tick(1);
  const result = await pending;
  assert.equal(requests, 2);
  assert.equal(result.messages[0].content, "AB");
});

test("Stop during retry cancels the timer without reconnecting", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const controller = new AbortController();
  let requests = 0;
  const pending = consumeChatStream(response(`retry: 30000\n\n${chunk("1", "A")}`), {
    threadId: "thread-1", signal: controller.signal, onUpdate: () => {},
    reconnect: async () => { requests++; return response(frame("end", "2", null)); },
  });
  const rejected = assert.rejects(pending, (error) => error.kind === "stopped");
  await flush();
  controller.abort();
  await rejected;
  t.mock.timers.tick(30000);
  await flush();
  assert.equal(requests, 0);
});

test("structured fields reach stream consumers without becoming answer text", async () => {
  const updates = [];
  const result = await consumeChatStream(response(chunk("1", [
    { type: "thinking", thinking: "Considering" },
    { type: "tool_use", id: "call-1", name: "search", input: { query: "test" } },
    { type: "text", text: "Answer" },
  ]) + frame("end", "2", null)), {
    threadId: "thread-1", signal: new AbortController().signal,
    onUpdate: (messages) => updates.push(messages),
    reconnect: async () => { throw new Error("Unexpected reconnect"); },
  });
  assert.deepEqual(result.messages, [{ id: "answer", runId: "run-1", content: "Answer", thinking: "Considering", toolCalls: [{ id: "call-1", name: "search", args: { query: "test" } }] }]);
  assert.deepEqual(updates.at(-1), result.messages);
});

test("ChatView wires structured fields and a truthful replay-gap notice separately from errors", () => {
  const source = readFileSync(new URL("../components/ChatView.tsx", import.meta.url), "utf8");
  assert.match(source, /thinking: message.thinking/);
  assert.match(source, /toolCalls: message.toolCalls/);
  assert.match(source, /onEvent:[\s\S]*?replay-gap[\s\S]*?flash\("Some streamed events could not be replayed\. This response is incomplete\."\)/);
  assert.match(source, /<ErrorBox\s+message=\{requestError.message\}/);
});

test("a heartbeat comment is liveness without ever becoming a frame", async () => {
  // The Gateway writes `: heartbeat` whenever no event has arrived for 15s.
  // The decoder must discard it — but discarding it is exactly why the silence
  // notice cannot listen for parsed frames: during the quiet period it exists
  // to catch, those never fire.
  let activity = 0;
  const updates = [];
  const result = await consumeChatStream(response(": heartbeat\n\n: heartbeat\n\n" + frame("end", "9", null)), {
    threadId: "thread-1", signal: new AbortController().signal,
    onUpdate: (messages) => updates.push(messages),
    onActivity: () => { activity += 1; },
    reconnect: async () => { throw new Error("Unexpected reconnect"); },
  });
  assert.ok(activity > 0, "a comment line is still a byte on the wire and must count as liveness");
  assert.deepEqual(result.messages, [], "a comment must never become answer text");
  assert.deepEqual(updates.at(-1), [], "nor an update");
});

test("onActivity is optional, so callers that do not measure silence are unaffected", async () => {
  const result = await consumeChatStream(response(chunk("1", "Answer") + frame("end", "2", null)), {
    threadId: "thread-1", signal: new AbortController().signal,
    onUpdate: () => {},
    reconnect: async () => { throw new Error("Unexpected reconnect"); },
  });
  assert.equal(result.messages[0].content, "Answer");
});

test("a server error frame survives the throw, so the UI can name the failure", async () => {
  // The defect this pins: the reducer parsed code/correlationId off this frame
  // and every throw site dropped it, so all failed runs rendered one sentence.
  const error = frame("error", "100-9", { code: "run_failed", message: "model unavailable", correlation_id: "req-abc-123" });
  await assert.rejects(
    consumeChatStream(response(chunk("100-1", "Partial") + error), {
      threadId: "thread-1", signal: new AbortController().signal, onUpdate: () => {},
      reconnect: async () => { throw new Error("Unexpected reconnect"); },
    }),
    (thrown) => {
      // Still an ApiClientError of the same kind, so every existing caller and
      // every existing assertion on `error.kind` is untouched.
      assert.equal(thrown.kind, "response");
      assert.equal(thrown.name, "StreamRunFailure");
      assert.equal(thrown.sseError.code, "run_failed");
      assert.equal(thrown.sseError.correlationId, "req-abc-123");
      return true;
    },
  );
});

test("the thrown error never carries the server's message as its own text", () => {
  // `detail`/message are untrusted content derived from tool output. They stay
  // off the thrown error so no caller can splatter them into the transcript.
  const thrown = new StreamRunFailure({ code: "run_failed", message: "Bearer sk-secret", correlationId: "c1" });
  assert.equal(thrown.detail, null);
  assert.doesNotMatch(thrown.message, /sk-secret/);
});

test("a stream failure with no server identity is still a plain response error", () => {
  const thrown = new StreamRunFailure(null);
  assert.equal(thrown.kind, "response");
  assert.equal(thrown.sseError, null);
});

test("a dropped stream rejoins up to 5 times, so a brief blip does not lose the answer", async (t) => {
  // The budget used to be 2: a laptop that slept for two seconds lost the rest
  // of an answer irreversibly, even though every later byte was still on the
  // server waiting behind Last-Event-ID.
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let requests = 0;
  const pending = consumeChatStream(response(chunk("1", "A")), {
    threadId: "thread-1", signal: new AbortController().signal, onUpdate: () => {},
    reconnect: async () => { requests++; return response(chunk("2", "B")); },
  });
  const rejected = assert.rejects(pending, (error) => error.kind === "response");
  // Drive the ladder: no `retry:` frame, so each wait is a client backoff.
  for (let i = 0; i < 12; i++) {
    await flush();
    t.mock.timers.tick(8000);
    await flush();
  }
  await rejected;
  assert.equal(requests, 5, "exactly the ladder budget, then an honest give-up");
});

test("a dropped stream with no server retry hint waits before its first rejoin", async (t) => {
  // `retryDelay` starts at 0, so before the ladder this re-dialed with NO wait
  // at all — hammering the very dependency that had just failed to deliver.
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const requests = [];
  const pending = consumeChatStream(response(chunk("1", "A")), {
    threadId: "thread-1", signal: new AbortController().signal, onUpdate: () => {},
    reconnect: async (path, options) => { requests.push({ path, options }); return response(frame("end", "2", null)); },
  });
  await flush();
  assert.equal(requests.length, 0, "no rejoin is attempted synchronously");
  // Equal jitter on a 500ms ceiling is uniform over [250, 500), so 200ms is
  // guaranteed short of it. This is the assertion that would fail under full
  // jitter, whose range includes ~0 — the immediate re-dial this replaced.
  t.mock.timers.tick(200);
  await flush();
  assert.equal(requests.length, 0, "the first backoff has a real floor, not a ~0ms one");
  t.mock.timers.tick(300);
  await flush();
  assert.equal(requests.length, 1, "and fires once the wait has actually elapsed");
  await pending;
});

test("ChatView surfaces the support identity instead of discarding it", () => {
  // The wiring pin. Without it the parsed detail is parsed, tested and dropped
  // again — which is exactly the state this change set exists to end.
  const source = readFileSync(new URL("../components/ChatView.tsx", import.meta.url), "utf8");
  assert.match(source, /error instanceof StreamRunFailure \? chatSupportId\(error\.sseError\)/);
  assert.match(source, /supportId/);
});
