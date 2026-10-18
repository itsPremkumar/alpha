// The run-scoped task projection client (Bot Mode 3.5): the query is asked
// with the run id the caller named, engine errors travel verbatim, and an
// absent field is `null` — never a guessed zero.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const httpStub = `
let handler = (path, method, payload) => { throw new Error("no stub response configured"); };
export function setHttpHandler(fn) { handler = fn; }
export async function get(path) { return handler(path, "GET", undefined); }
export async function send(path, method, payload) { return handler(path, method, payload); }
export function pick(obj, keys, fallback) {
  if (obj && typeof obj === "object") {
    for (const key of keys) if (obj[key] !== undefined && obj[key] !== null) return obj[key];
  }
  return fallback;
}
export function asList(body, keys) {
  if (Array.isArray(body)) return body;
  for (const key of keys) if (body && typeof body === "object" && Array.isArray(body[key])) return body[key];
  return [];
}
`;
const toDataUrl = (code) => `data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`;
const httpStubUrl = toDataUrl(httpStub);
const source = ts
  .transpileModule(readFileSync(new URL("./tasks.ts", import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  .outputText.replace(/from\s+"\.\/http"/, `from "${httpStubUrl}"`);
const tasks = await import(toDataUrl(source));
const { setHttpHandler } = await import(httpStubUrl);

test("fetchRunTasks asks for the named run and carries the projection through", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/tasks?run_id=run-1");
    assert.equal(method, "GET");
    return {
      run_id: "run-1",
      count: 2,
      engines: { graph: 1, swarm: 1 },
      engine_errors: {},
      tasks: [
        { task_id: "g-task", run_id: "run-1", engine: "graph", status: "pending", native_status: "pending", title: "Draft" },
        { task_id: "t1", run_id: "run-1", engine: "swarm", status: "running", native_status: "running", attempt_count: 1, max_attempts: 3 },
      ],
    };
  });
  const projection = await tasks.fetchRunTasks("run-1");
  assert.equal(projection.run_id, "run-1");
  assert.equal(projection.count, 2);
  assert.deepEqual(projection.engines, { graph: 1, swarm: 1 });
  assert.deepEqual(projection.tasks.map((t) => t.engine), ["graph", "swarm"]);
  assert.equal(projection.tasks[1].attempt_count, 1);
});

test("the run id is encoded into the query", async () => {
  let seen = null;
  setHttpHandler((path) => {
    seen = path;
    return { run_id: "a b/c", count: 0, engines: {}, engine_errors: {}, tasks: [] };
  });
  await tasks.fetchRunTasks("a b/c");
  assert.equal(seen, "/tasks?run_id=a%20b%2Fc");
});

test("an engine error is a reason, not a zero", async () => {
  setHttpHandler(() => ({
    run_id: "run-1",
    count: 1,
    engines: { swarm: 1 },
    engine_errors: { graph: "OSError: ledger volume is gone" },
    tasks: [{ task_id: "t1", engine: "swarm", status: "pending", native_status: "pending" }],
  }));
  const projection = await tasks.fetchRunTasks("run-1");
  assert.equal(projection.engines.graph, undefined, "a failed engine contributed no count");
  assert.equal(projection.engine_errors.graph, "OSError: ledger volume is gone");
});

test("absent fields normalize to null, never to a guessed zero", () => {
  const row = tasks.normalizeRunTask({ task_id: "t1" });
  assert.equal(row.engine, null);
  assert.equal(row.status, null);
  assert.equal(row.attempt_count, null);
  assert.equal(row.max_attempts, null);
  const projection = tasks.normalizeRunTasksProjection({}, "run-9");
  assert.equal(projection.count, null);
  assert.deepEqual(projection.engines, {});
  assert.deepEqual(projection.engine_errors, {});
  assert.deepEqual(projection.tasks, []);
  assert.equal(projection.run_id, "run-9");
});

test("a non-count engine entry is dropped; a non-string reason is dropped", () => {
  const projection = tasks.normalizeRunTasksProjection(
    { engines: { graph: 1, bad: "x", worse: 7 }, engine_errors: { graph: "boom", bad: 42 } },
    "run-1",
  );
  assert.deepEqual(projection.engines, { graph: 1, worse: 7 }, "counts stay, non-numbers do not");
  assert.deepEqual(projection.engine_errors, { graph: "boom" }, "a non-string reason never becomes prose");
});
