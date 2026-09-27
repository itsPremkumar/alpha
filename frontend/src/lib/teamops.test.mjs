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
  await teamops.swarmAction("swm/a", "run-async");
  await teamops.swarmMessages("swm/a", "results");
  await teamops.publishSwarmMessage("swm/a", { content: "note", topic: "results" });
  assert.deepEqual(calls.map((call) => [call.method, call.path]), [
    ["POST", "/swarms/swm%2Fa/run-async"],
    ["GET", "/swarms/swm%2Fa/messages?topic=results"],
    ["POST", "/swarms/swm%2Fa/messages"],
  ]);
  assert.equal(calls[2].payload.sender, "operator");
  assert.equal(calls[2].payload.kind, "observation");
});

// Every lifecycle verb must land on a route segment the Gateway really mounts.
// The router mounts "/run-async" (kebab) in
// backend/app/gateway/routers/swarms.py, so the earlier "run_async" spelling
// 404'd at routing and the Run button could never work. Pin all of them, not
// just the one that was wrong, so a future rename cannot pass unnoticed.
test("every swarm lifecycle verb addresses a mounted /swarms/{id}/<verb> route", async () => {
  const calls = [];
  setHttpHandler((path, method) => {
    calls.push({ path, method });
    return undefined;
  });
  assert.deepEqual([...teamops.SWARM_ACTIONS], ["pause", "resume", "cancel", "step", "run-async"]);
  for (const action of teamops.SWARM_ACTIONS) {
    await teamops.swarmAction("swm-1", action);
  }
  assert.deepEqual(calls.map((c) => [c.method, c.path]), [
    ["POST", "/swarms/swm-1/pause"],
    ["POST", "/swarms/swm-1/resume"],
    ["POST", "/swarms/swm-1/cancel"],
    ["POST", "/swarms/swm-1/step"],
    ["POST", "/swarms/swm-1/run-async"],
  ]);
});

// Guard the specific regression: the client must not reintroduce an underscore
// spelling for the background-run verb, which is not a mounted route. Only
// executable lines are inspected, so the rationale in the doc comment (which
// has to name the wrong spelling to explain it) does not trip the guard.
test("the swarm client does not reintroduce the unmounted run_async spelling", () => {
  const codeOnly = source
    .split("\n")
    .filter((line) => !/^\s*(\/\/|\/\*|\*)/.test(line))
    .join("\n");
  assert.ok(
    !/run_async/.test(codeOnly),
    "lib/teamops.ts code must not use run_async; the router mounts run-async",
  );
  assert.ok(codeOnly.includes("run-async"), "lib/teamops.ts must use the mounted run-async route segment");
});
