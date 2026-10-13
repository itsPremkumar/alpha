import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const stub = `
let handler = () => undefined;
export function setHandler(fn) { handler = fn; }
export async function get(path) { return handler(path, "GET"); }
export async function send(path, method, payload, timeout) { return handler(path, method, payload, timeout); }
`;
const stubUrl = `data:text/javascript;charset=utf-8,${encodeURIComponent(stub)}`;
const source = readFileSync(new URL("./arena.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
  .replace(/from\s+"\.\/http"/, `from "${stubUrl}"`);
const arena = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(js)}`);
const { setHandler } = await import(stubUrl);

test("Arena custom counts omit the named profile so the Gateway resolves a real custom run", async () => {
  setHandler((path, method, payload) => {
    assert.equal(path, "/arena/runs");
    assert.equal(method, "POST");
    assert.deepEqual(payload, { task: "Compare implementations", mode: "decide", agents: 12 });
    return { run_id: "arena-1", status: "draft" };
  });
  await arena.createArenaRun("Compare implementations", { mode: "decide", profile: "custom", agents: 12 });
});

test("Arena start passes the explicit confirmation and uses the encoded route", async () => {
  setHandler((path, method, payload, timeout) => {
    assert.equal(path, "/arena/runs/run%2Fa/start");
    assert.equal(method, "POST");
    assert.deepEqual(payload, { confirm: true });
    assert.equal(timeout, 120_000);
    return { run_id: "run/a", status: "running" };
  });
  await arena.startArenaRun("run/a", true);
});

test("the Arena UI is driven by backend-declared modes and configured competitor limits", () => {
  const panel = readFileSync(new URL("../components/sections/ArenaPanel.tsx", import.meta.url), "utf8");
  assert.match(panel, /implemented_modes\.includes\(item\.mode\)/);
  assert.match(panel, /config\?\.max_agents/);
  assert.match(panel, /Confirm spend & start/);
  assert.match(panel, /Estimate only/);
  assert.match(panel, /profile !== "custom"/);
});
