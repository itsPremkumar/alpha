import assert from "node:assert/strict";
import test from "node:test";
import { moduleUrl } from "./test-modules.mjs";

const { chatSupportId, chatSupportLine } = await import(moduleUrl("chat-support-id"));
const { chatRequestErrorMessage } = await import(moduleUrl("chat-request-error"));

/**
 * The defect these tests exist for
 * -------------------------------
 * `sse-reducer.ts` parsed `code` / `correlation_id` off `event: error` and
 * `tool-status-honesty.test.mjs` pinned that the parse was correct. Nothing
 * ever read the result, so every failed run rendered the same sentence. These
 * tests pin the second half: the parsed identity must survive to the UI.
 */

test("a server-reported code and correlation id both survive to the rendered line", () => {
  const line = chatSupportLine({ code: "run_failed", correlationId: "req-abc-123" });
  assert.match(line, /run_failed/);
  assert.match(line, /req-abc-123/);
  assert.match(line, /Error code/, "the code is labelled, so it cannot be mistaken for prose");
  assert.match(line, /Support id/, "the correlation id is labelled as the support handle");
});

test("either token alone is still worth rendering", () => {
  assert.match(chatSupportLine({ code: "tool_timeout" }), /tool_timeout/);
  assert.match(chatSupportLine({ correlationId: "trace-9" }), /trace-9/);
});

test("no identity renders nothing at all, rather than an empty labelled line", () => {
  // An empty "Error code:  ·  Support id: ." is a visible artefact on the one
  // surface whose job is to state what is actually known.
  assert.equal(chatSupportLine(null), null);
  assert.equal(chatSupportLine(undefined), null);
  assert.equal(chatSupportLine({}), null);
  assert.equal(chatSupportLine({ code: "", correlationId: "" }), null);
  assert.equal(chatSupportId({}), null);
});

test("a malformed identifier is refused, never cleaned up into shape", () => {
  // Trimming `<img src=x>` into something renderable would be fabricating an
  // id the server never sent. Refusal renders no line, which is correct.
  assert.equal(chatSupportId({ code: "<script>alert(1)</script>" }), null);
  assert.equal(chatSupportId({ correlationId: "a b" }), null);
  assert.equal(chatSupportId({ code: 'quote"and`tick' }), null);
  assert.equal(chatSupportId({ code: "line\nbreak" }), null);
  assert.equal(chatSupportId({ code: "<b>" }), null);
});

test("identifiers are bounded, so a hostile server cannot flood the transcript", () => {
  assert.equal(chatSupportId({ code: "x".repeat(97) }), null, "97 chars is over the 96 cap");
  assert.ok(chatSupportId({ code: "x".repeat(96) }), "96 is exactly the cap and is allowed");
});

test("non-string values are refused rather than coerced", () => {
  assert.equal(chatSupportId({ code: 42 }), null);
  assert.equal(chatSupportId({ correlationId: { nested: true } }), null);
  assert.equal(chatSupportId({ code: ["a"] }), null);
});

test("surrounding whitespace is trimmed, because that is still the reported id", () => {
  assert.deepEqual(chatSupportId({ code: "  run_failed  " }), { code: "run_failed" });
});

test("the server's failure MESSAGE is never promoted to a support id", () => {
  // This is the security property. The parsed `message` is derived from tool
  // output and provider text, so it is untrusted content; only server-generated
  // tokens may be displayed. If `message` ever became renderable here, a run
  // failure could inject arbitrary text into the transcript.
  assert.equal(chatSupportId({ message: "secret-token-abc" }), null);
  assert.equal(chatSupportLine({ message: "<script>alert(1)</script>" }), null);
});

test("every failure sentence carries the support line when one exists", () => {
  for (const kind of ["http", "network", "stream", "empty", "stopped"]) {
    const withId = chatRequestErrorMessage({ kind, supportId: { code: "run_failed", correlationId: "req-1" } });
    assert.match(withId, /run_failed/, `${kind} must carry the code`);
    assert.match(withId, /req-1/, `${kind} must carry the support id`);
    // The sanitized sentence must survive intact, not be replaced by the id.
    assert.ok(withId.length > 80, `${kind} kept only the id, losing the honest sentence`);
  }
});

test("a failure with no identity renders byte-identically to the pre-fix sentence", () => {
  // The regression guarantee: nothing changed for the majority of failures,
  // which carry no server identity at all.
  for (const kind of ["http", "network", "stream", "empty", "stopped"]) {
    assert.equal(
      chatRequestErrorMessage({ kind }),
      chatRequestErrorMessage({ kind, supportId: null }),
      `${kind} gained or lost text when no identity was reported`,
    );
  }
});

test("the support line cannot reintroduce untrusted detail into a failure message", () => {
  const message = chatRequestErrorMessage({
    kind: "stream",
    // A caller that wrongly forwards the server body as a support id must not
    // get it rendered; `chatSupportLine` filters before anything is appended.
    supportId: { code: "run_failed", correlationId: "<script>alert(1)</script>" },
  });
  assert.match(message, /run_failed/);
  assert.doesNotMatch(message, /<script>/, "the rejected correlation id must not be rendered");
});

test("the stream sentence still refuses a response body carrying markup", () => {
  // Pins the pre-existing contract this module must not weaken.
  const message = chatRequestErrorMessage({ kind: "http", status: 500, supportId: { code: "e", correlationId: "c" } });
  assert.match(message, /HTTP 500/);
  assert.match(message, /No assistant response was received/);
});
