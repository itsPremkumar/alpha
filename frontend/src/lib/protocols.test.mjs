// protocols.test.mjs — A2A / agent-messages / deliveries / input-polish clients (wave W-D).
//
// Pins every client function to the REAL gateway contract (exact paths from
// a2a.py, agent_messages.py, deliveries.py, input_polish.py) and the honesty
// inversions this plane can produce: inbox reads default to mark_as_read=false
// (viewing the UI must not consume another agent's messages), absent optional
// fields stay null, failed calls reject with the server's reason, and a
// delivery status the server rejects is never rendered as a successful mark.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const transpile = (file) =>
  ts.transpileModule(readFileSync(new URL(`./${file}`, import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;

const sources = {
  "api-client": transpile("api-client.ts"),
  http: transpile("http.ts"),
  protocols: transpile("protocols.ts"),
};

function load(source, dependencies = {}) {
  const exports = {};
  new Function("exports", "require", "process", "console", "fetch", source)(
    exports,
    (dependency) => {
      assert.ok(Object.hasOwn(dependencies, dependency), `Unexpected dependency: ${dependency}`);
      return dependencies[dependency];
    },
    { env: {} },
    console,
    () => {
      throw new Error("Raw fetch must not be used");
    },
  );
  return exports;
}

const client = load(sources["api-client"]);

function fixture(respond) {
  const calls = [];
  const api = {
    ...client,
    GATEWAY_BASE: "/api",
    apiFetch: client.createApiClient({
      baseUrl: "/api",
      getCookie: () => "",
      fetch: async (url, init) => {
        calls.push({ url, ...init });
        return respond(url, init);
      },
    }),
  };
  const http = load(sources.http, { "./api-client": api });
  return { calls, protocols: load(sources.protocols, { "./http": http }) };
}

const CARD = {
  agent_id: "agent-researcher",
  name: "Lead Research Specialist",
  description: "Deep research.",
  version: "1.0.0",
  skills: ["research", "fact_checking"],
  supported_protocols: ["A2A/1.0", "MCP/2026-07"],
  input_schema: { type: "object" },
  output_schema: { type: "object" },
  auth_mode: "bearer",
  availability: "available",
  endpoint_url: null,
  created_at: 1750000000.0,
};

test("listA2ACards reads the federation registry and keeps optional fields null", async () => {
  const f = fixture(() => Response.json([CARD]));
  const cards = await f.protocols.listA2ACards();
  assert.equal(f.calls[0].url, "/api/protocols/a2a/cards");
  assert.equal(cards.length, 1);
  assert.equal(cards[0].agent_id, "agent-researcher");
  assert.deepEqual(cards[0].skills, ["research", "fact_checking"]);
  assert.equal(cards[0].endpoint_url, null, "absent endpoint_url stays null");
  assert.equal(cards[0].availability, "available");
});

test("listA2ACards forwards the skill filter as a query parameter", async () => {
  const f = fixture(() => Response.json([]));
  await f.protocols.listA2ACards("research");
  assert.equal(f.calls[0].url, "/api/protocols/a2a/cards?skill_filter=research");
});

test("getA2ACard rejects with the registry's 404 reason", async () => {
  const f = fixture(() =>
    new Response(JSON.stringify({ detail: "Agent 'nope' not found in A2A registry." }), {
      status: 404,
      headers: { "content-type": "application/json" },
    }),
  );
  await assert.rejects(() => f.protocols.getA2ACard("nope"), /not found in A2A registry/);
});

test("delegateA2ATask sends the server's own defaults and maps the result", async () => {
  const f = fixture(() =>
    Response.json({
      request_id: "a2a-deadbeef",
      status: "completed",
      deliverable: { summary: "done" },
      evidence: ["e1", "e2"],
      error: null,
      execution_seconds: 1.25,
      timestamp: 1750000000.0,
    }),
  );
  const d = await f.protocols.delegateA2ATask({ target_agent_id: "agent-researcher", task_objective: "research X" });
  assert.equal(f.calls[0].url, "/api/protocols/a2a/delegate");
  const body = JSON.parse(f.calls[0].body);
  assert.equal(body.sender_agent_id, "gateway-client");
  assert.equal(body.deadline_seconds, 120.0);
  assert.deepEqual(body.context_data, {});
  assert.equal(d.request_id, "a2a-deadbeef");
  assert.equal(d.status, "completed");
  assert.deepEqual(d.evidence, ["e1", "e2"]);
  assert.equal(d.error, null);
  assert.equal(d.execution_seconds, 1.25);
});

test("a rejected delegation (400) rejects instead of returning a failed task as success", async () => {
  const f = fixture(() =>
    new Response(JSON.stringify({ detail: "Target agent 'ghost' has no capability card" }), {
      status: 400,
      headers: { "content-type": "application/json" },
    }),
  );
  await assert.rejects(
    () => f.protocols.delegateA2ATask({ target_agent_id: "ghost", task_objective: "x" }),
    /no capability card/,
  );
});

test("listAgentRoster maps the process-local roster envelope", async () => {
  const f = fixture(() =>
    Response.json({
      thread_id: "t1",
      agents: [{ name: "worker-1", role: "worker", status: "idle" }],
      count: 1,
    }),
  );
  const r = await f.protocols.listAgentRoster("t1");
  assert.equal(f.calls[0].url, "/api/threads/t1/agent-messages/roster");
  assert.equal(r.count, 1);
  assert.equal(r.agents[0].name, "worker-1");
});

test("sendAgentMessage posts the server's mode/kind defaults and rejects unknown receivers", async () => {
  const f = fixture(() => Response.json({ thread_id: "t1", status: "delivered" }));
  const sent = await f.protocols.sendAgentMessage("t1", { sender_name: "a", receiver_name: "b", content: "hi" });
  const body = JSON.parse(f.calls[0].body);
  assert.equal(body.mode, "auto");
  assert.equal(body.kind, "message");
  assert.equal(sent.status, "delivered");

  const f404 = fixture(() =>
    new Response(JSON.stringify({ detail: "Unknown agent" }), {
      status: 404,
      headers: { "content-type": "application/json" },
    }),
  );
  await assert.rejects(
    () => f404.protocols.sendAgentMessage("t1", { sender_name: "a", receiver_name: "ghost", content: "hi" }),
    /Unknown agent/,
  );
});

test("getAgentInbox defaults to mark_as_read=false — viewing never consumes messages", async () => {
  const f = fixture(() =>
    Response.json({ thread_id: "t1", agent_name: "worker-1", messages: [{ id: "m1" }], count: 1 }),
  );
  const inbox = await f.protocols.getAgentInbox("t1", "worker-1");
  assert.equal(f.calls[0].url, "/api/threads/t1/agent-messages/inbox?agent_name=worker-1&mark_as_read=false");
  assert.equal(inbox.count, 1);

  await f.protocols.getAgentInbox("t1", "worker-1", true);
  assert.match(f.calls[1].url, /mark_as_read=true$/);
});

test("delivery ledger maps records and mark() sends the exact record fields", async () => {
  const f = fixture((url) => {
    if (url.endsWith("/deliveries")) {
      return Response.json({ task_id: "task-1", deliveries: [{ occurrence_id: "occ-1", status: "delivered" }], count: 1 });
    }
    return Response.json({ occurrence_id: "occ-1", status: "failed", error: "channel 503" });
  });
  const list = await f.protocols.listTaskDeliveries("task-1");
  assert.equal(list.count, 1);
  assert.equal(list.deliveries[0].status, "delivered");

  const marked = await f.protocols.markTaskDelivery("task-1", "occ-1", {
    status: "failed",
    channel: "slack",
    error: "channel 503",
  });
  const body = JSON.parse(f.calls[1].body);
  assert.equal(body.status, "failed");
  assert.equal(body.channel, "slack");
  assert.equal(body.error, "channel 503");
  assert.equal(body.artifact_ref, null);
  assert.equal(marked.status, "failed");
});

test("an invalid delivery status (422) rejects — never a fake successful mark", async () => {
  const f = fixture(() =>
    new Response(JSON.stringify({ detail: "Invalid delivery status." }), {
      status: 422,
      headers: { "content-type": "application/json" },
    }),
  );
  await assert.rejects(
    () => f.protocols.markTaskDelivery("task-1", "occ-1", { status: "nonsense" }),
    /Invalid delivery status/,
  );
});

test("claim sends occurrence_id as a query parameter and reports is_new", async () => {
  const f = fixture(() => Response.json({ delivery: { occurrence_id: "occ-9" }, is_new: true }));
  const claim = await f.protocols.claimTaskDelivery("task-1", "occ-9");
  assert.equal(f.calls[0].url, "/api/scheduled-tasks/task-1/deliveries/claim?occurrence_id=occ-9");
  assert.equal(claim.is_new, true);
  assert.equal(claim.delivery.occurrence_id, "occ-9");
});

test("blueprints list and launch map the real job, and an unknown blueprint rejects", async () => {
  const f = fixture((url) =>
    url.includes("/launch")
      ? Response.json({ id: "job-1", cron_expression: "0 9 * * *", command_or_prompt: "daily digest" })
      : Response.json({ blueprints: [{ id: "daily-digest", name: "Daily digest" }] }),
  );
  const blueprints = await f.protocols.listScheduledBlueprints();
  assert.equal(blueprints[0].id, "daily-digest");
  const job = await f.protocols.launchScheduledBlueprint("daily-digest", { channel: "email" });
  assert.equal(JSON.parse(f.calls[1].body).values.channel, "email");
  assert.equal(job.cron_expression, "0 9 * * *");

  const f404 = fixture(() =>
    new Response(JSON.stringify({ detail: "Blueprint 'ghost' not found." }), {
      status: 404,
      headers: { "content-type": "application/json" },
    }),
  );
  await assert.rejects(() => f404.protocols.launchScheduledBlueprint("ghost"), /not found/);
});

test("incidents list and resolve map records; resolving twice rejects with the server reason", async () => {
  const f = fixture((url) =>
    url.includes("/resolve")
      ? Response.json({ incident_id: "inc-1", resolved: true })
      : Response.json({ task_id: "task-1", incidents: [{ incident_id: "inc-1", resolved: false }], count: 1 }),
  );
  const incidents = await f.protocols.listTaskIncidents("task-1", true);
  assert.match(f.calls[0].url, /unresolved_only=true$/);
  assert.equal(incidents.count, 1);
  const resolved = await f.protocols.resolveTaskIncident("inc-1");
  assert.equal(resolved.resolved, true);

  const f404 = fixture(() =>
    new Response(JSON.stringify({ detail: "Incident not found or already resolved." }), {
      status: 404,
      headers: { "content-type": "application/json" },
    }),
  );
  await assert.rejects(() => f404.protocols.resolveTaskIncident("inc-1"), /already resolved/);
});

test("polishInput maps the rewrite and keeps changed:false false", async () => {
  const f = fixture(() => Response.json({ rewritten_text: "Do X.", changed: false }));
  const r = await f.protocols.polishInput("do x");
  assert.equal(f.calls[0].url, "/api/input-polish");
  const body = JSON.parse(f.calls[0].body);
  assert.equal(body.text, "do x");
  assert.equal(body.locale, null);
  assert.equal(r.changed, false, "changed:false must never flip to true");
  assert.equal(r.rewritten_text, "Do X.");
});

test("contract pin: the four protocol routers really expose these paths", () => {
  const read = (rel) => readFileSync(new URL(`../../../${rel}`, import.meta.url), "utf8");

  const a2a = read("backend/app/gateway/routers/a2a.py");
  assert.match(a2a, /prefix="\/api\/protocols\/a2a"/);
  for (const p of ['"/cards"', '"/cards/{agent_id}"', '"/delegate"']) assert.ok(a2a.includes(p), p);

  const messages = read("backend/app/gateway/routers/agent_messages.py");
  assert.match(messages, /prefix="\/api\/threads\/\{thread_id\}\/agent-messages"/);
  for (const p of ['"/roster"', '"/register"', '"/messages"', '"/inbox"']) assert.ok(messages.includes(p), p);

  const deliveries = read("backend/app/gateway/routers/deliveries.py");
  for (const p of [
    '"/scheduled-tasks/{task_id}/deliveries"',
    '"/scheduled-tasks/{task_id}/deliveries/claim"',
    '"/scheduled-tasks/{task_id}/deliveries/{occurrence_id}/mark"',
    '"/scheduled-tasks/blueprints"',
    '"/scheduled-tasks/blueprints/{blueprint_id}/launch"',
    '"/scheduled-tasks/{task_id}/incidents"',
    '"/scheduled-tasks/incidents/{incident_id}/resolve"',
  ])
    assert.ok(deliveries.includes(p), p);

  const polish = read("backend/app/gateway/routers/input_polish.py");
  assert.ok(polish.includes('"/input-polish"'));
});
