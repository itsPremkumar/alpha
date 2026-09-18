import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { moduleUrl } from "./test-modules.mjs";

const { consumeChatStream } = await import(moduleUrl("chat-stream"));
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
