import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./memory.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

function loadMemory(getImpl) {
  const calls = [];
  const exports = {};
  new Function("exports", "require", compiled)(exports, (dependency) => {
    assert.equal(dependency, "./http");
    return {
      async get(path) {
        calls.push({ path, method: "GET" });
        return getImpl(path);
      },
      async send() {
        throw new Error("unexpected write");
      },
      asList(body, keys) {
        if (Array.isArray(body)) return body;
        for (const key of keys) if (Array.isArray(body?.[key])) return body[key];
        return [];
      },
      pick(body, keys, fallback) {
        for (const key of keys) if (body?.[key] !== undefined && body?.[key] !== null) return body[key];
        return fallback;
      },
    };
  });
  return { memory: exports, calls };
}

test("memory status reads the gateway's bounded L1 capture projection", async () => {
  const response = {
    config: { enabled: true },
    health: {
      status: "degraded",
      extraction_enabled: true,
      model: "auto",
      model_source: "host_llm (app default model)",
      last_error_type: "JSONDecodeError",
      consecutive_failures: 3,
      total_successes: 0,
      total_failures: 3,
    },
    l1_capture: {
      status: "available",
      window_days: 7,
      latest: {
        timestamp: "2026-10-16T11:59:00Z",
        run_status: "succeeded",
        surfaces: [{ surface: "entities", status: "written", written: 2, considered: 3, rejected: 0 }],
      },
    },
  };
  const { memory, calls } = loadMemory((path) => {
    assert.equal(path, "/memory/status");
    return response;
  });

  assert.deepEqual(await memory.memoryStatus(), response);
  assert.deepEqual(calls, [{ path: "/memory/status", method: "GET" }]);
});

test("memory status preserves Gateway failures for an unavailable UI state", async () => {
  const { memory } = loadMemory(() => {
    throw new Error("generation status unavailable");
  });

  await assert.rejects(memory.memoryStatus(), /generation status unavailable/);
});


// ---------------------------------------------------------------------------
// Prospective (reminder / commitment) memory surface — fetch + transitions.
// ---------------------------------------------------------------------------

function loadProspectiveMemory(getImpl, sendImpl) {
  const calls = [];
  const sends = [];
  const exports = {};
  new Function("exports", "require", compiled)(exports, (dependency) => {
    assert.equal(dependency, "./http");
    return {
      async get(path, timeoutMs) {
        calls.push({ path, method: "GET", timeoutMs });
        return getImpl(path, timeoutMs);
      },
      async send(path, method, payload, timeoutMs) {
        sends.push({ path, method, payload, timeoutMs });
        return sendImpl(path, method, payload, timeoutMs);
      },
      asList(body, keys) {
        if (Array.isArray(body)) return body;
        for (const key of keys) if (Array.isArray(body?.[key])) return body[key];
        return [];
      },
      pick(body, keys, fallback) {
        for (const key of keys) if (body?.[key] !== undefined && body?.[key] !== null) return body[key];
        return fallback;
      },
    };
  });
  return { memory: exports, calls, sends };
}

test("fetchProspectiveMemory uses GET /memory/prospective and returns the payload", async () => {
  const response = {
    status: "succeeded",
    items: [
      { id: "r1", content: "remember X", kind: "reminder", status: "pending", priority: 0, due_at: null, created_at: 5, occurrence: 0 },
    ],
    reason: null,
  };
  const { memory, calls } = loadProspectiveMemory(
    (path) => {
      assert.equal(path, "/memory/prospective");
      return response;
    },
    () => {
      throw new Error("unexpected write");
    },
  );

  assert.deepEqual(await memory.fetchProspectiveMemory(), response);
  assert.deepEqual(calls, [{ path: "/memory/prospective", method: "GET", timeoutMs: undefined }]);
});

test("fetchProspectiveMemory rejects with the gateway reason instead of an empty list", async () => {
  const { memory } = loadProspectiveMemory(
    () => {
      throw new Error("reminder store unreadable");
    },
    () => {
      throw new Error("unexpected write");
    },
  );

  await assert.rejects(memory.fetchProspectiveMemory(), /reminder store unreadable/);
});

test("transitionProspectiveMemory uses POST with the recovery reason and encodes the id", async () => {
  const item = {
    id: "a/b",
    content: "x",
    kind: "commitment",
    status: "pending",
    priority: 1,
    due_at: 1000,
    created_at: 1,
    occurrence: 0,
  };
  const { memory, sends } = loadProspectiveMemory(
    () => {
      throw new Error("unexpected read");
    },
    (path, method, payload) => {
      assert.equal(method, "POST");
      return payload;
    },
  );

  await memory.transitionProspectiveMemory(item.id, "complete");
  await memory.transitionProspectiveMemory("plain", "cancel");

  assert.equal(sends.length, 2);
  assert.equal(sends[0].path, "/memory/prospective/a%2Fb/complete");
  assert.equal(sends[0].method, "POST");
  assert.deepEqual(sends[0].payload, { reason: "marked complete from Memory UI" });
  assert.equal(sends[1].path, "/memory/prospective/plain/cancel");
  assert.deepEqual(sends[1].payload, { reason: "marked cancel from Memory UI" });
});

test("transitionProspectiveMemory rejects on gateway failure", async () => {
  const { memory } = loadProspectiveMemory(
    () => {
      throw new Error("unexpected read");
    },
    () => {
      throw new Error("gateway rejected transition");
    },
  );

  await assert.rejects(memory.transitionProspectiveMemory("x", "complete"), /gateway rejected transition/);
});
