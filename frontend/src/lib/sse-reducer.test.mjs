import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./sse-reducer.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { createSseState, reduceSse, streamMessages, createSseDecoder, runIdFromLocation } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const delta = (id, content, messageId = "answer") => ({ event: "messages", id, data: [{ type: "AIMessageChunk", id: messageId, content }, {}] });
const snapshot = (id, content) => ({ event: "values", id, data: { messages: [{ type: "ai", id: "answer", content }] } });
const reduce = (frames, state = createSseState("run-1")) => frames.reduce(reduceSse, state);

test("Gateway message deltas sort by numeric event ID and replay once, including repeated text", () => {
  const state = reduce([delta("100-10", " world"), delta("100-2", "Hello"), delta("100-3", "Hello"), delta("100-2", "Hello")]);
  assert.equal(streamMessages(state)[0].content, "HelloHello world");
  assert.equal(state.lastEventId, "100-10");
});

test("all delivery permutations converge across snapshots and duplicate frames", () => {
  const frames = [delta("100-1", "Hello"), delta("100-2", " world"), snapshot("100-3", "Hello world!"), delta("100-4", " Again")];
  const permutations = (items) => items.length ? items.flatMap((item, i) => permutations(items.filter((_, j) => i !== j)).map((rest) => [item, ...rest])) : [[]];
  for (const order of permutations(frames)) {
    const state = reduce([...order, ...order]);
    assert.equal(streamMessages(state)[0].content, "Hello world! Again");
  }
});

test("metadata arriving after messages assigns the exact run identity", () => {
  const state = reduce([delta("100-1", "Hello"), { event: "metadata", id: "100-0", data: { run_id: "run-1", thread_id: "thread-1" } }], createSseState());
  assert.deepEqual(streamMessages(state), [{ id: "answer", runId: "run-1", content: "Hello" }]);
  assert.equal(state.pending.length, 0);
});

test("message IDs stay independent and reducer input stays immutable", () => {
  const initial = createSseState("run-1");
  const first = reduceSse(initial, delta("100-1", "A", "one"));
  const state = reduceSse(first, delta("100-2", "B", "two"));
  assert.equal(initial.messages.size, 0);
  assert.equal(first.messages.size, 1);
  assert.deepEqual(streamMessages(state).map((m) => m.content), ["A", "B"]);
  assert.equal(reduceSse(state, { event: "metadata", data: { run_id: "other-run" } }).failure, "protocol");
});

test("values history, tool results and subgraph chunks are not new assistant answers", () => {
  const state = reduce([
    snapshot("100-0", "Old answer"),
    { event: "messages", id: "100-1", data: [{ type: "tool", id: "tool", content: "private tool output" }, {}] },
    { event: "messages|child", id: "100-2", data: [{ type: "ai", id: "child", content: "Child answer" }, {}] },
    { event: "messages", id: "100-3", data: [{ type: "ai", id: "child", content: "Child answer" }, { langgraph_checkpoint_ns: "child:123" }] },
  ]);
  assert.deepEqual(streamMessages(state), []);
});

test("only text blocks render; unknown payloads never become text", () => {
  const state = reduce([delta("100-1", [{ type: "text", text: "Visible" }, { type: "tool_use", text: "Hidden" }, { type: "reasoning", text: "Hidden" }])]);
  assert.equal(streamMessages(state)[0].content, "Visible");
});

for (const [event, failure] of [["gap", "gap"], ["error", "server"]]) {
  test(`${event} is a failure even when followed by end`, () => {
    const state = reduce([delta("100-1", "Partial"), { event, data: { message: "secret" } }, { event: "end", data: null }]);
    assert.equal(state.failure, failure);
    assert.equal(state.ended, false);
    assert.equal(streamMessages(state)[0].content, "Partial");
  });
}

test("identity-free deltas fail closed instead of guessing replay deduplication", () => {
  const frame = delta("100-1", "answer");
  delete frame.id;
  assert.equal(reduce([frame]).failure, "protocol");
});

test("SSE decoder handles every byte boundary, CRLF, multiline data and UTF-8", () => {
  const bytes = new TextEncoder().encode(': heartbeat\r\n\r\nid: 100-1\r\nevent: messages\r\ndata: [{"type":"ai","id":"answer",\r\ndata: "content":"Café"},{}]\r\n\r\nevent: end\r\ndata: null\r\n\r\n');
  for (let split = 0; split <= bytes.length; split++) {
    const frames = [];
    const decoder = createSseDecoder((frame) => frames.push(frame));
    decoder.push(bytes.slice(0, split));
    decoder.push(bytes.slice(split));
    decoder.finish();
    assert.equal(frames.length, 2);
    assert.equal(frames[0].data[0].content, "Café");
    assert.equal(frames[1].id, undefined);
  }
});

test("malformed JSON and truncated frames are rejected without echoing content", () => {
  const decoder = createSseDecoder(() => {});
  assert.throws(() => decoder.push(new TextEncoder().encode("event: messages\ndata: secret\n\n")), /Invalid SSE data/);
  const truncated = createSseDecoder(() => {});
  truncated.push(new TextEncoder().encode('data: {"secret":true}'));
  assert.throws(() => truncated.finish(), /Incomplete SSE frame/);
});

test("structured-only chunks expose tools and thinking without changing text extraction", () => {
  const state = reduce([delta("100-1", [
    { type: "thinking", thinking: "Considering" },
    { type: "reasoning", text: " options" },
    { type: "tool_use", id: "call-1", name: "search", input: { query: "test" } },
  ])]);
  assert.deepEqual(streamMessages(state), [{
    id: "answer", runId: "run-1", content: "", thinking: "Considering options",
    toolCalls: [{ id: "call-1", name: "search", args: { query: "test" } }],
  }]);
});

test("tool argument fragments and thinking converge across replay, ordering and snapshots", () => {
  const first = delta("100-1", [{ type: "thinking", thinking: "A" }]);
  first.data[0].tool_call_chunks = [{ id: "call-1", index: 0, name: "search", args: '{"q":' }];
  first.data[0].tool_calls = [{ id: "call-1", name: "search", args: {} }];
  const second = delta("100-2", [{ type: "thinking_delta", thinking: "B" }]);
  second.data[0].tool_call_chunks = [{ index: 0, args: '"value"}' }];
  const before = reduce([first]);
  const state = reduce([second, first, second], before);
  assert.equal(streamMessages(before)[0].thinking, "A");
  assert.deepEqual(streamMessages(before)[0].toolCalls[0].args, {});
  assert.equal(streamMessages(state)[0].thinking, "AB");
  assert.deepEqual(streamMessages(state)[0].toolCalls, [{ id: "call-1", name: "search", args: { q: "value" } }]);
  assert.deepEqual(streamMessages(reduce([second, first])), streamMessages(state));
  const full = snapshot("100-3", [{ type: "text", text: "Done" }, { type: "thinking", thinking: "AB" }]);
  full.data.messages[0].tool_calls = [{ id: "call-1", name: "search", args: { q: "final" } }];
  assert.deepEqual(streamMessages(reduce([full, second, first, full])), [{
    id: "answer", runId: "run-1", content: "Done", thinking: "AB",
    toolCalls: [{ id: "call-1", name: "search", args: { q: "final" } }],
  }]);
});

test("structured identity-free chunks fail closed and unknown blocks stay hidden", () => {
  const frame = delta("100-1", [{ type: "thinking", thinking: "A" }]);
  delete frame.id;
  assert.equal(reduce([frame]).failure, "protocol");
  assert.deepEqual(streamMessages(reduce([delta("100-1", [{ type: "unknown", text: "hidden" }])])), []);
});

test("replay gaps are explicit even before run metadata and never expose server details", () => {
  const frame = { event: "gap", id: "100-2", data: { message: "secret" } };
  const state = reduce([delta("100-1", "Partial"), frame]);
  assert.deepEqual(state.replayGap, { type: "replay-gap", runId: "run-1", lastEventId: "100-1", eventId: "100-2" });
  assert.equal(state.lastEventId, "100-1");
  assert.equal(reduceSse(createSseState(), frame).replayGap.type, "replay-gap");
});

test("decoder reports retry-only hints at every byte boundary and ignores invalid hints", () => {
  const bytes = new TextEncoder().encode("retry: 125\r\n\r\nretry: -1\nretry: 1.5\nretry: +2\nretry: nope\nretry: \nretry: 0\n\nretry: 999999999999999999999999\n\ndata: null\n\n");
  for (let split = 0; split <= bytes.length; split++) {
    const retries = [];
    const frames = [];
    const decoder = createSseDecoder((frame) => frames.push(frame), (delay) => retries.push(delay));
    decoder.push(bytes.slice(0, split));
    decoder.push(bytes.slice(split));
    decoder.finish();
    assert.deepEqual(retries, [125, 0, 30000]);
    assert.equal(frames.length, 1);
  }
});

test("run Content-Location must refer to the requested thread", () => {
  assert.equal(runIdFromLocation("/api/threads/thread-1/runs/run-1", "thread-1"), "run-1");
  assert.equal(runIdFromLocation("/api/threads/thread-2/runs/run-1", "thread-1"), undefined);
  assert.equal(runIdFromLocation("/api/threads/thread-1/runs/%", "thread-1"), undefined);
});
