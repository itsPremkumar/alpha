// peer-network.test.mjs — real Gateway route and honesty pins.
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
  "peer-network": transpile("peer-network.ts"),
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
  return { calls, peer: load(sources["peer-network"], { "./http": http }) };
}

const PEER = {
  agent_id: "alpha-b",
  name: "Alpha B",
  description: "A second Alpha",
  version: "2.1.0",
  url: "http://192.168.1.20:8001",
  websocket_url: "ws://192.168.1.20:8001/api/peer-network/ws",
  preferred_transport: "WebSocket",
  capabilities: ["code", "review"],
  skills: [],
  source: "udp:192.168.1.20:8743",
  trust: "discovered",
  first_seen: "2026-09-25T10:00:00Z",
  last_seen: "2026-09-25T10:00:00Z",
  paired_at: null,
  card: { agent_id: "alpha-b" },
};

test("status preserves unavailable provider state and a real pairing code", async () => {
  const f = fixture(() =>
    Response.json({
      enabled: true,
      identity: { agent_id: "alpha-a", name: "Alpha" },
      discovery: { mdns: { available: false, running: false, last_error: "not installed" } },
      transports: { http: { enabled: true } },
      persistence: { backend: "sqlite" },
      pairing_code: "pairing-code-from-gateway",
      limits: { max_recipients: 50 },
    }),
  );
  const status = await f.peer.fetchPeerNetworkStatus();
  assert.equal(f.calls[0].url, "/api/peer-network/status");
  assert.equal(status.identity.agent_id, "alpha-a");
  assert.equal(status.discovery.mdns.available, false);
  assert.equal(status.pairing_code, "pairing-code-from-gateway");
  assert.equal(status.limits.max_recipients, 50);
});

test("listPeers preserves unknown optional fields as null", async () => {
  const f = fixture(() => Response.json({ peers: [PEER], count: 1 }));
  const peers = await f.peer.listPeers({ skill: "code", trust: "discovered" });
  assert.match(f.calls[0].url, /skill=code/);
  assert.match(f.calls[0].url, /trust=discovered/);
  assert.equal(peers[0].agent_id, "alpha-b");
  assert.equal(peers[0].paired_at, null);
  assert.equal(peers[0].trust, "discovered");
});

test("discover posts the real endpoint and maps the peer list", async () => {
  const f = fixture(() => Response.json({ peers: [PEER], count: 1, discovery: "local-first" }));
  const peers = await f.peer.discoverPeers();
  assert.equal(f.calls[0].url, "/api/peer-network/discover");
  assert.equal(f.calls[0].method, "POST");
  assert.equal(peers.length, 1);
});

test("publishes an Agent Card only through the explicit GitHub action", async () => {
  const f = fixture(() => Response.json({ published: true, result: { path: ".alpha-network/peers/alpha-a.json" } }));
  await f.peer.publishPeerCardToGitHub();
  assert.equal(f.calls[0].url, "/api/peer-network/github/publish");
  assert.equal(f.calls[0].method, "POST");
});


test("pair sends the endpoint and shared code, then maps the server-confirmed peer", async () => {
  const f = fixture(() => Response.json({ status: "paired", peer: { ...PEER, trust: "paired" } }));
  const peer = await f.peer.pairPeer({ endpoint: "http://192.168.1.20:8001", pairing_code: "shared-secret" });
  const body = JSON.parse(f.calls[0].body);
  assert.equal(f.calls[0].url, "/api/peer-network/pair");
  assert.equal(body.endpoint, "http://192.168.1.20:8001");
  assert.equal(body.pairing_code, "shared-secret");
  assert.equal(body.expected_agent_id, null);
  assert.equal(peer.trust, "paired");
});

test("conversation and message clients pin topology and idempotency fields", async () => {
  const f = fixture((url) => {
    if (url.includes("/messages")) {
      return Response.json({
        message_id: "m-1",
        conversation_id: "conv-1",
        sender_id: "alpha-a",
        recipients: ["alpha-b", "alpha-c"],
        kind: "chat",
        text: "hello",
        payload: {},
        status: "queued",
        direction: "outbound",
        created_at: "2026-09-25T10:00:00Z",
        delivered_at: null,
        read_at: null,
        delivery_error: null,
        deliveries: [],
      });
    }
    return Response.json({
      conversation_id: "conv-1",
      mode: "one_to_many",
      title: "Release crew",
      status: "active",
      participants: ["alpha-a", "alpha-b", "alpha-c"],
      metadata: {},
      created_at: "2026-09-25T10:00:00Z",
      updated_at: "2026-09-25T10:00:00Z",
    });
  });
  const conversation = await f.peer.createPeerConversation({
    title: "Release crew",
    mode: "one_to_many",
    participants: ["alpha-a", "alpha-b", "alpha-c"],
  });
  assert.equal(conversation.mode, "one_to_many");
  const message = await f.peer.sendPeerMessage({
    conversation_id: conversation.conversation_id,
    recipients: ["alpha-b", "alpha-c"],
    text: "hello",
    idempotency_key: "release-1",
  });
  const body = JSON.parse(f.calls[1].body);
  assert.equal(body.mode, null);
  assert.equal(body.idempotency_key, "release-1");
  assert.equal(message.deliveries.length, 0);
  assert.equal(message.delivery_error, null);
});

test("failed peer reads reject rather than looking like an empty network", async () => {
  const f = fixture(() =>
    new Response(JSON.stringify({ detail: "Peer network storage unavailable" }), {
      status: 503,
      headers: { "content-type": "application/json" },
    }),
  );
  await assert.rejects(() => f.peer.listPeers(), /Peer network storage unavailable/);
});

test("contract pin: the backend really exposes the separate peer-network module", () => {
  const read = (rel) => readFileSync(new URL(`../../../${rel}`, import.meta.url), "utf8");
  const router = read("backend/app/gateway/routers/peer_network.py");
  for (const path of ["/status", "/peers", "/discover", "/pair", "/github/publish", "/conversations", "/messages", "/events"]) {
    assert.ok(router.includes(path), path);
  }
  assert.ok(read("backend/packages/harness/alpha/peer_network/service.py").includes("class PeerNetworkService"));
});
