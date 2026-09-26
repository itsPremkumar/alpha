// TeamOps swarm client contract tests: owner-visible reads reject on failure,
// and mutations use the server's goal/lease vocabulary rather than stale aliases.
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
const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;
const stubUrl = toDataUrl(httpStub);
const source = readFileSync(new URL("./teamops.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText.replace(/from\s+"\.\/http"/, `from "${stubUrl}"`);
const teamops = await import(toDataUrl(code));
const { setHttpHandler } = await import(stubUrl);

test("listSwarms surfaces request failures instead of fabricating an empty list", async () => {
  setHttpHandler(() => { throw new Error("gateway unavailable"); });
  await assert.rejects(() => teamops.listSwarms(), /gateway unavailable/);
});

test("createSwarm sends goal and explicit execution-neutral options", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/swarms");
    assert.equal(method, "POST");
    assert.deepEqual(payload, {
      goal: "Audit services",
      mode: "auto",
      max_concurrency: 8,
      items: undefined,
    });
  });
  await teamops.createSwarm("Audit services");
});

test("swarm actions and message helpers use encoded swarm ids and bounded routes", async () => {
  const calls = [];
  setHttpHandler((path, method, payload) => {
    calls.push({ path, method, payload });
    if (path.endsWith("/messages") && method === "GET") return { messages: [] };
    return undefined;
  });
  await teamops.swarmAction("swm/a", "run_async");
  await teamops.swarmMessages("swm/a", "results");
  await teamops.publishSwarmMessage("swm/a", { content: "note", topic: "results" });
  assert.deepEqual(calls.map((call) => [call.method, call.path]), [
    ["POST", "/swarms/swm%2Fa/run_async"],
    ["GET", "/swarms/swm%2Fa/messages?topic=results"],
    ["POST", "/swarms/swm%2Fa/messages"],
  ]);
  assert.equal(calls[2].payload.sender, "operator");
  assert.equal(calls[2].payload.kind, "observation");
});
