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
