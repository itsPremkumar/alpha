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

test("only non-terminal, non-paused swarms need live refresh", () => {
  assert.equal(teamops.hasRefreshableSwarms([]), false);
  assert.equal(teamops.hasRefreshableSwarms([{ status: "completed" }, { status: "failed" }]), false);
  assert.equal(teamops.hasRefreshableSwarms([{ status: "paused" }]), false);
  assert.equal(teamops.hasRefreshableSwarms([{ status: "running" }]), true);
  assert.equal(teamops.hasRefreshableSwarms([{ status: "partial_success" }]), true);
  assert.equal(teamops.hasRefreshableSwarms([{ status: "future_gateway_state" }]), true);
});

test("listSwarms surfaces request failures instead of fabricating an empty list", async () => {
  setHttpHandler(() => { throw new Error("gateway unavailable"); });
  await assert.rejects(() => teamops.listSwarms(), /gateway unavailable/);
});

test("createSwarm sends goal and explicit execution-neutral options", async () => {
  const expectedProjection = {
    projected_tasks: 2,
    projected_wall_seconds: 12.5,
    projected_serial_seconds: 20,
    estimated_speedup: 1.6,
    wall_budget_seconds: null,
    wall_budget_exceeded: null,
    tool_budget_calls: null,
    projected_tokens: null,
    projected_tool_calls: null,
    max_concurrency: 4,
    estimate_method: "critical_path_v1",
    basis: "decomposer estimate",
  };
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/swarms");
    assert.equal(method, "POST");
    assert.deepEqual(payload, {
      goal: "Audit services",
      mode: "auto",
      max_concurrency: 4,
      items: undefined,
      budget: undefined,
    });
    return { cost_projection: expectedProjection };
  });
  assert.deepEqual(await teamops.createSwarm("Audit services"), expectedProjection);
});

test("createSwarm keeps an absent projection distinct from a measured zero", async () => {
  setHttpHandler(() => ({ swarm_id: "s1" }));
  assert.equal(await teamops.createSwarm("Audit services"), null);
});

test("createSwarm sends the selected topology and worker concurrency", async () => {
  setHttpHandler((_path, _method, payload) => {
    assert.equal(payload.mode, "map_reduce");
    assert.equal(payload.max_concurrency, 1000);
    assert.equal(payload.items.length, 1000);
    assert.deepEqual(payload.budget, { max_tasks: 1001 });
  });
  await teamops.createSwarm("Process a document collection", {
    mode: "map_reduce",
    maxConcurrency: 1000,
    items: Array.from({ length: 1000 }, (_, index) => `item-${index + 1}`),
    maxTasks: 1001,
  });
});

test("Team Ops presents every Gateway swarm topology and a safe selectable capacity", () => {
  const component = readFileSync(new URL("../components/sections/TeamOpsSection.tsx", import.meta.url), "utf8");
  for (const mode of ["auto", "parallel", "map_reduce", "scatter_gather", "hierarchical", "debate", "ensemble", "coding_worktree", "read_fanout", "critical_path_split"]) {
    assert.match(component, new RegExp(`\\["${mode}",`));
  }
  assert.match(component, /value=\{swarmConcurrency\}/);
  assert.match(component, /maxConcurrency: swarmConcurrency/);
  assert.match(component, /type="number" min=\{0\} max=\{1000\}/);
  assert.match(component, /up to 1,000/);
  assert.match(component, /task graph width, runtime\/provider limits and budget/);
  assert.match(component, /Pre-run estimate/);
  assert.match(component, /Tokens \{lastSwarmProjection\.projection\.projected_tokens \?\? "not estimated"\}/);
  assert.match(component, /model evidenced \{t\.usedModels\.length > 0 \? t\.usedModels\.join/);
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

// ── Group rooms (2.6 mention composer) ─────────────────────────────────────

test("postGroupMessage names the operator as the sender the route requires", async () => {
  // The route 422s on a missing sender, and the orchestration grammar reads
  // sender == "user" as the human — any other name makes the room read the
  // operator as a bot.
  const calls = [];
  setHttpHandler((path, method, payload) => {
    calls.push({ path, method, payload });
    return undefined;
  });
  await teamops.postGroupMessage("alpha", "@coder status?");
  assert.deepEqual(calls, [
    { path: "/groups/alpha/messages", method: "POST", payload: { message: "@coder status?", sender: "user" } },
  ]);
});

test("fetchGroupRoster reads the roster the grammar resolves against", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/groups/alpha/roster");
    assert.equal(method, "GET");
    return {
      room: "alpha",
      direct: ["coder"],
      inherited: ["reviewer"],
      effective: ["coder", "reviewer"],
      direct_count: 1,
      effective_count: 2,
    };
  });
  const roster = await teamops.fetchGroupRoster("alpha");
  assert.equal(roster.room, "alpha");
  assert.deepEqual(roster.effective, ["coder", "reviewer"]);
  assert.deepEqual(roster.direct, ["coder"]);
  assert.equal(roster.directCount, 1);
  assert.equal(roster.effectiveCount, 2);
});

test("fetchGroupRoster keeps an absent list null rather than an empty one", async () => {
  // "The Gateway sent no list" and "the list is empty" drive opposite composer
  // states — no autocomplete at all versus an empty one.
  setHttpHandler(() => ({ room: "alpha", direct_count: null, effective_count: null }));
  const roster = await teamops.fetchGroupRoster("alpha");
  assert.equal(roster.effective, null);
  assert.equal(roster.direct, null);
  assert.equal(roster.directCount, null);
  assert.equal(roster.effectiveCount, null);
});

test("listGroups reports the routing mode and moderator when the Gateway sends them", async () => {
  setHttpHandler(() => ({ rooms: [{ name: "alpha", members: [], status: "active", mode: "mention", moderator: "coder" }] }));
  const [room] = await teamops.listGroups();
  assert.equal(room.mode, "mention");
  assert.equal(room.moderator, "coder");
});

test("listGroups leaves mode and moderator undefined when the Gateway omits them", async () => {
  // An older Gateway without those fields must not invent a default mode.
  setHttpHandler(() => ({ rooms: [{ name: "alpha", members: [], status: "active" }] }));
  const [room] = await teamops.listGroups();
  assert.equal(room.mode, undefined);
  assert.equal(room.moderator, undefined);
});
