import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

/**
 * `lib/swarm-events.ts` — consuming one swarm's SSE stream.
 *
 * The contract that matters is not "does it parse SSE" but what each outcome
 * MEANS to the board that consumes it:
 *
 *  - every frame (whatever its runtime event type) is one `onEvent` — the
 *    refresh signal — including frames split across chunk boundaries;
 *  - `event: end` is `onEnd`, and so is a clean EOF: the server's 60s window
 *    closes mid-run WITHOUT an end frame (`swarms.py:370-393`), and treating
 *    that as "nothing more will ever happen" would freeze a running swarm;
 *  - a real read/connect failure is `onError` with the reason, never a silent
 *    stop and never `onEnd`;
 *  - `close()` is final: no handler fires after it.
 */

const transpile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
const read = (name) => readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8");
const encoder = new TextEncoder();

/** Mirrors lib/api-client.ts ApiClientError message construction, so the
 * assertion proves the REAL text a caller sees. */
class ApiClientError extends Error {
  constructor(kind, status = 0, detail = null) {
    const validStatus = Number.isInteger(status) && status >= 100 && status <= 599 ? status : 0;
    super(
      kind === "http"
        ? `Request failed${validStatus ? ` (HTTP ${validStatus})` : ""}.${detail ? ` ${detail}` : ""}`
        : kind === "stopped"
          ? "Request stopped locally."
          : "The request could not be completed. Check your connection.",
    );
    this.name = "ApiClientError";
    this.kind = kind;
    this.status = validStatus;
  }
}

const apiClientSurface = {
  apiFetch: async () => {
    throw new Error("apiFetch must not be called by errMsg");
  },
  ApiClientError,
};

/** The REAL http.ts, with its api-client import answered by the stub surface. */
const httpExports = (() => {
  const exports = {};
  new Function("exports", "require", "process", "console", "fetch", transpile(read("http")))(
    exports,
    (dependency) => {
      assert.equal(dependency, "./api-client");
      return apiClientSurface;
    },
    { env: {} },
    { error: () => {} },
    () => {
      throw new Error("Raw fetch must not be used");
    },
  );
  return exports;
})();

function loadSwarmEvents(state) {
  const exports = {};
  new Function("exports", "require", "process", "console", "fetch", transpile(read("swarm-events")))(
    exports,
    (dependency) => {
      if (dependency === "./api-client") {
        return {
          apiFetch: async (path, init = {}) => {
            state.calls.push({ path, hasSignal: Boolean(init.signal) });
            if (state.failure) throw state.failure;
            return { body: state.body };
          },
          ApiClientError,
        };
      }
      if (dependency === "./http") return httpExports;
      throw new Error(`Unexpected dependency: ${dependency}`);
    },
    { env: {} },
    { error: () => {} },
    () => {
      throw new Error("Raw fetch must not be used");
    },
  );
  return exports;
}

const chunkReader = (chunks) => {
  let i = 0;
  return {
    getReader: () => ({
      read: async () => (i < chunks.length ? { done: false, value: encoder.encode(chunks[i++]) } : { done: true }),
    }),
  };
};

const failingReader = (chunks, error) => {
  let i = 0;
  return {
    getReader: () => ({
      read: async () => {
        if (i < chunks.length) return { done: false, value: encoder.encode(chunks[i++]) };
        throw error;
      },
    }),
  };
};

/** Resolves with { events, ends, errors } once the subscription settles. */
function settle(mod, swarmId, state) {
  return new Promise((resolve) => {
    const out = { events: 0, ends: 0, errors: [] };
    const handle = mod.subscribeSwarmEvents(swarmId, {
      onEvent: () => {
        out.events += 1;
      },
      onEnd: () => {
        out.ends += 1;
        resolve(out);
      },
      onError: (reason) => {
        out.errors.push(reason);
        resolve(out);
      },
    });
    out.handle = handle;
  });
}

test("the stream call site is the router's declared route, complete and unglued", () => {
  const src = read("swarm-events");
  const literal = /`(\/swarms\/[^`]*)`/.exec(src)?.[1];
  assert.equal(literal, "/swarms/${encodeURIComponent(swarmId)}/events/stream");
  const router = readFileSync(new URL("../../../backend/app/gateway/routers/swarms.py", import.meta.url), "utf8");
  assert.match(router, /@router\.get\(\s*"\/\{swarm_id\}\/events\/stream"/);
  // The audit's canonical subject (the template text with ${...} intact, no
  // slashes inside the expression) must match the declared path's pattern.
  assert.match("/api" + literal, /^\/api\/swarms\/[^/]+\/events\/stream$/);
});

test("every frame is one onEvent — named events, and one split across chunk boundaries", async () => {
  const state = {
    calls: [],
    body: chunkReader([
      'event: SWARM_STARTED\ndata: {"sequence":1}\n\n',
      "event: TASK_COMPLET", // deliberately split mid-frame
      'ED\ndata: {"sequence":2}\n\n',
      'event: end\ndata: {"status":"completed"}\n\n',
    ]),
  };
  const mod = loadSwarmEvents(state);
  const out = await settle(mod, "swm/a", state);
  assert.deepEqual(state.calls.map((c) => c.path), ["/swarms/swm%2Fa/events/stream"]);
  assert.equal(state.calls[0].hasSignal, true, "the subscription must be abortable");
  assert.equal(out.events, 2);
  assert.equal(out.ends, 1);
  assert.deepEqual(out.errors, []);
});

test("a clean EOF with no end frame is still onEnd — the server's 60s window closing is not a failure", async () => {
  const state = { calls: [], body: chunkReader(['event: SWARM_RUNNING\ndata: {"sequence":9}\n\n']) };
  const mod = loadSwarmEvents(state);
  const out = await settle(mod, "swm-1", state);
  assert.equal(out.events, 1);
  assert.equal(out.ends, 1, "EOF must let the caller re-read and decide, not freeze");
  assert.deepEqual(out.errors, []);
});

test("a mid-stream read failure reports the real reason and never fakes an end", async () => {
  const state = {
    calls: [],
    body: failingReader(['event: SWARM_STARTED\ndata: {}\n\n'], new Error("The stream was terminated.")),
  };
  const mod = loadSwarmEvents(state);
  const out = await settle(mod, "swm-1", state);
  assert.equal(out.events, 1);
  assert.equal(out.ends, 0);
  assert.deepEqual(out.errors, ["The stream was terminated."]);
});

test("a refused connection reports the Gateway's own refusal", async () => {
  const state = { calls: [], failure: new ApiClientError("http", 404, "Swarm 'swm-1' not found.") };
  const mod = loadSwarmEvents(state);
  const out = await settle(mod, "swm-1", state);
  assert.equal(out.events, 0);
  assert.equal(out.ends, 0);
  assert.equal(out.errors.length, 1);
  assert.match(out.errors[0], /HTTP 404.*not found/);
});

test("a body-less response is an error, not a silent end", async () => {
  const state = { calls: [], body: undefined };
  const mod = loadSwarmEvents(state);
  const out = await settle(mod, "swm-1", state);
  assert.equal(out.ends, 0);
  assert.match(out.errors[0], /no event-stream body/);
});

test("close() is final: a frame delivered after it never fires a handler", async () => {
  let deliver;
  const reader = {
    getReader: () => ({
      read: () => new Promise((resolve) => (deliver = resolve)),
    }),
  };
  const state = { calls: [], body: reader };
  const mod = loadSwarmEvents(state);
  const out = { events: 0, ends: 0, errors: [] };
  const handle = mod.subscribeSwarmEvents("swm-1", {
    onEvent: () => {
      out.events += 1;
    },
    onEnd: () => {
      out.ends += 1;
    },
    onError: (reason) => {
      out.errors.push(reason);
    },
  });
  await new Promise((resolve) => setImmediate(resolve)); // attach the reader
  handle.close();
  deliver({ done: false, value: encoder.encode("event: SWARM_STARTED\ndata: {}\n\n") });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(out.events, 0);
  assert.equal(out.ends, 0);
  assert.deepEqual(out.errors, []);
});
