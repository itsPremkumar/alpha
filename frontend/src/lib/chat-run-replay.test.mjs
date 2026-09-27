// Replay a REAL captured Gateway run stream through the frontend's own SSE
// reducer.
//
// The fixture in ./fixtures/chat-run-capture.json is verbatim bytes from a live
// `POST /api/threads/{id}/runs/stream` (captured with _f3_capture.py), not a
// hand-written approximation. It exists because the reducer's subgraph filter
// was wrong in a way only real traffic revealed: the Gateway tags the ROOT
// agent node's frames with `langgraph_checkpoint_ns: "model:<task-id>"`, and the
// reducer used to discard every frame carrying any namespace. The answer then
// survived only inside a `values` snapshot, which is deliberately hidden, so
// `streamMessages()` returned [] and the composer reported
// "The server returned no response content" for every real turn.
//
// This test fails if the reducer ever stops rendering a genuine answer again.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const capture = JSON.parse(
  readFileSync(new URL("./fixtures/chat-run-capture.json", import.meta.url), "utf8"),
);

const source = readFileSync(new URL("./sse-reducer.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const reducer = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`);
const { createSseState, createSseDecoder, reduceSse, streamMessages, runIdFromLocation, isNestedSubgraphFrame } = reducer;

/** Replay exactly as ChatView does: seed the run id from Content-Location. */
function replay() {
  let state = createSseState(runIdFromLocation(capture.content_location, capture.thread_id));
  const parser = createSseDecoder((frame) => {
    state = reduceSse(state, frame);
  });
  // Verbatim bytes, as the browser's stream reader delivers them.
  parser.push(new TextEncoder().encode(capture.raw_sse));
  parser.finish();
  return state;
}

test("the capture fixture is a real successful run", () => {
  assert.ok(capture.raw_sse.length > 0, "fixture must contain the raw SSE body");
  assert.match(capture.raw_sse, /event: end/, "a real run ends with an `end` frame");
  assert.match(capture.content_location, /\/threads\/[^/]+\/runs\/[^/]+$/);
});

test("a real captured run renders its assistant answer", () => {
  const state = replay();
  const messages = streamMessages(state);

  assert.ok(
    messages.length > 0,
    "streamMessages() was empty for a real successful run -> the composer shows no answer",
  );
  const answer = messages.map((m) => String(m.content ?? "")).join("").trim();
  assert.ok(answer.length > 0, "the assistant message is empty -> the composer shows no answer");
  assert.match(answer, /pong/, `expected the captured answer, got ${JSON.stringify(answer)}`);
  for (const message of messages) {
    assert.ok(message.id, "a delivered message must carry an id the UI can key on");
    assert.ok(message.runId, "a delivered message must carry its run id");
  }
});

test("a real captured run is not reported as a protocol/transport failure", () => {
  const state = replay();
  assert.equal(state.failure ?? null, null, `reducer recorded a failure: ${JSON.stringify(state.failure)}`);
  assert.equal(state.ended, true, "the run reached `end`");
});

test("the root agent node is on the primary channel, a nested subgraph is not", () => {
  // Namespaces measured on the live Gateway for this very run.
  assert.equal(isNestedSubgraphFrame({ langgraph_checkpoint_ns: "model:42db5631-a124-3bed-115d-f483820460ba" }), false);
  assert.equal(isNestedSubgraphFrame({ langgraph_checkpoint_ns: "child:123" }), true);
  // A nested path is `|`-joined, whatever the node is called.
  assert.equal(isNestedSubgraphFrame({ langgraph_checkpoint_ns: "model:abc|worker:def" }), true);
  assert.equal(isNestedSubgraphFrame({ checkpoint_ns: "other:1" }), true);
  // Middleware injections are not the assistant's answer either.
  assert.equal(isNestedSubgraphFrame({ langgraph_checkpoint_ns: "DynamicContextMiddleware.before_agent:872c4a95" }), true);
  // No namespace at all is the plain root channel.
  assert.equal(isNestedSubgraphFrame({}), false);
  assert.equal(isNestedSubgraphFrame({ langgraph_checkpoint_ns: "" }), false);
});
