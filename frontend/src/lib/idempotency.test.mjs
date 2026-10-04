import assert from "node:assert/strict";
import test from "node:test";
import { moduleUrl } from "./test-modules.mjs";

const { newIdempotencyKey, isTransportFailure, sendIdempotent } = await import(moduleUrl("idempotency"));
const { ApiClientError } = await import(moduleUrl("api-client"));

const networkFailure = () => new ApiClientError("network");
const httpFailure = () => new ApiClientError("http", 503, "overloaded");
const okResponse = () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
// The backoff timer is scheduled from a promise microtask, so a
// tick must first let that microtask run or it would advance a
// clock nothing is scheduled on yet.
const flush = () => new Promise((resolve) => setImmediate(resolve));

test("a key is a UUID-shaped unique string", () => {
  const key = newIdempotencyKey();
  assert.match(key, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.notEqual(key, newIdempotencyKey());
});

test("only a transport failure is retryable", () => {
  assert.equal(isTransportFailure(networkFailure()), true);
  assert.equal(isTransportFailure(httpFailure()), false);
  assert.equal(isTransportFailure(new ApiClientError("stopped")), false);
  assert.equal(isTransportFailure(new ApiClientError("response")), false);
  assert.equal(isTransportFailure(new ApiClientError("route")), false);
  assert.equal(isTransportFailure(new Error("plain")), false);
  assert.equal(isTransportFailure(null), false);
});

test("the key rides on the request as the Idempotency-Key header", async () => {
  const seen = [];
  await sendIdempotent(
    async (path, init) => {
      seen.push({ path, headers: init?.headers });
      return okResponse();
    },
    { path: "/threads/t1/runs/stream", idempotencyKey: "key-1", init: { method: "POST" } },
  );
  assert.equal(seen.length, 1);
  assert.equal(seen[0].path, "/threads/t1/runs/stream");
  assert.equal(new Headers(seen[0].headers).get("Idempotency-Key"), "key-1");
});

test("a transport failure is retried with the same key, path and body", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const calls = [];
  const body = JSON.stringify({ input: { messages: [{ role: "user", content: "hi" }] } });
  const pending = sendIdempotent(
    async (path, init) => {
      calls.push({ path, key: new Headers(init?.headers).get("Idempotency-Key"), body: init?.body });
      if (calls.length < 3) throw networkFailure();
      return okResponse();
    },
    { path: "/threads/t1/runs/stream", idempotencyKey: "stable-key", init: { method: "POST", body } },
    { attempts: 3, baseDelayMs: 10, maxDelayMs: 20 },
  );
  // Each attempt's backoff timer is scheduled from a microtask, so
  // the clock is stepped one flush→tick pair at a time.
  for (let i = 0; i < 3; i++) {
    await flush();
    t.mock.timers.tick(100);
  }
  await flush();
  const response = await pending;
  assert.equal(response.status, 200);
  assert.equal(calls.length, 3);
  for (const call of calls) {
    assert.equal(call.path, "/threads/t1/runs/stream");
    assert.equal(call.key, "stable-key");
    assert.equal(call.body, body);
  }
});

test("a server answer is never retried, even a 5xx", async () => {
  let calls = 0;
  await assert.rejects(
    sendIdempotent(
      async () => {
        calls++;
        throw httpFailure();
      },
      { path: "/threads/t1/runs/stream", idempotencyKey: "key-1" },
      { attempts: 3, baseDelayMs: 0, maxDelayMs: 0 },
    ),
    (error) => error instanceof ApiClientError && error.kind === "http" && error.status === 503,
  );
  assert.equal(calls, 1);
});

test("a user abort and a non-transport error are never retried", async () => {
  let calls = 0;
  await assert.rejects(
    sendIdempotent(
      async () => {
        calls++;
        throw new ApiClientError("stopped");
      },
      { path: "/p", idempotencyKey: "key-1" },
      { attempts: 3, baseDelayMs: 0, maxDelayMs: 0 },
    ),
    (error) => error.kind === "stopped",
  );
  await assert.rejects(
    sendIdempotent(
      async () => {
        calls++;
        throw new Error("caller bug");
      },
      { path: "/p", idempotencyKey: "key-1" },
      { attempts: 3, baseDelayMs: 0, maxDelayMs: 0 },
    ),
    (error) => error instanceof Error && error.message === "caller bug",
  );
  assert.equal(calls, 2);
});

test("the retry budget is bounded: the last transport failure propagates", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let calls = 0;
  const pending = sendIdempotent(
    async () => {
      calls++;
      throw networkFailure();
    },
    { path: "/p", idempotencyKey: "key-1" },
    { attempts: 3, baseDelayMs: 1, maxDelayMs: 2 },
  );
  const assertion = assert.rejects(pending, (error) => error.kind === "network");
  // Each attempt's backoff timer is scheduled from a microtask, so
  // the clock is stepped one flush→tick pair at a time. The
  // rejection handler is attached before any clock advance so the
  // terminal failure is never an unhandled rejection.
  for (let i = 0; i < 3; i++) {
    await flush();
    t.mock.timers.tick(100);
  }
  await flush();
  await assertion;
  assert.equal(calls, 3);
});

test("aborting during the backoff rejects as a local stop", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const controller = new AbortController();
  let calls = 0;
  const pending = sendIdempotent(
    async () => {
      calls++;
      throw networkFailure();
    },
    { path: "/p", idempotencyKey: "key-1", init: { signal: controller.signal } },
    { attempts: 3, baseDelayMs: 60_000, maxDelayMs: 60_000 },
  );
  // The abort listener rejects the pending backoff directly, so no
  // clock advance is needed for the stop to propagate.
  await flush();
  controller.abort();
  await assert.rejects(pending, (error) => error.kind === "stopped");
  assert.equal(calls, 1);
});

test("equal jitter keeps a real floor: no attempt re-dials immediately", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  // random() -> 0 zeroes the jitter term, so the first backoff
  // is exactly the floor: half of `min(cap, base·2⁰)` = 125 ms.
  // A re-dial before that tick would be the zero-delay bug the
  // ladder exists to remove.
  t.mock.method(Math, "random", () => 0);
  let calls = 0;
  const pending = sendIdempotent(
    async () => {
      calls++;
      throw networkFailure();
    },
    { path: "/p", idempotencyKey: "key-1" },
    { attempts: 4, baseDelayMs: 250, maxDelayMs: 4000 },
  );
  await flush();
  t.mock.timers.tick(124);
  await flush();
  assert.equal(calls, 1);
  t.mock.timers.tick(1);
  await flush();
  assert.equal(calls, 2);
  // Second backoff: ceiling doubles to 500, floor is 250.
  t.mock.timers.tick(249);
  await flush();
  assert.equal(calls, 2);
  t.mock.timers.tick(1);
  await flush();
  assert.equal(calls, 3);
  const assertion = assert.rejects(pending, (error) => error.kind === "network");
  t.mock.timers.runAll();
  await flush();
  await assertion;
  assert.equal(calls, 4);
});
