// external-alpha.test.mjs — route, verb and honesty pins for the External Alpha
// transcript client.
//
// Same technique as peer-network.test.mjs: drive the real createApiClient
// through a recording fetch so the asserted URLs are the actual request paths,
// and install a throwing `fetch` so any raw-transport regression fails loudly.
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
  "external-alpha": transpile("external-alpha.ts"),
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
  return { calls, alpha: load(sources["external-alpha"], { "./http": http, "./api-client": api }) };
}

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

// ── routes and verbs ────────────────────────────────────────────────────────

test("the transcript index reads the list route", async () => {
  const { calls, alpha } = fixture(async () =>
    json({ transcripts: [{ conversation_id: "c1", title: "Room", mode: "direct", status: "active", participants: ["a", "b"], counts: { messages: 3, turns: 1 } }], count: 1, enabled: true }),
  );
  const index = await alpha.listTranscripts();
  assert.equal(calls[0].url, "/api/peer-network/transcripts?limit=50");
  assert.equal(index.count, 1);
  assert.equal(index.transcripts[0].conversation_id, "c1");
  assert.equal(index.transcripts[0].counts.messages, 3);
});

test("one conversation reads the transcript route", async () => {
  const { calls, alpha } = fixture(async () =>
    json({ conversation_id: "c1", conversation: { title: "Room" }, peer: { agent_id: "a", name: "Remote" }, entries: [], count: 0, truncated: false, counts: { messages: 0, turns: 0 } }),
  );
  await alpha.getTranscript("c1");
  assert.equal(calls[0].url, "/api/peer-network/transcripts/c1?limit=1000");
});

test("turn forensics reads the turn route", async () => {
  const { calls, alpha } = fixture(async () => json({ conversation_id: "c1", turn: { run_id: "r1" } }));
  const turn = await alpha.getTurnForensics("c1", "r1");
  assert.equal(calls[0].url, "/api/peer-network/transcripts/c1/turns/r1");
  assert.equal(turn.run_id, "r1");
});

test("export reads the export route", async () => {
  const { calls, alpha } = fixture(async () => json({ conversation_id: "c1", entries: [] }));
  await alpha.exportTranscript("c1");
  assert.equal(calls[0].url, "/api/peer-network/transcripts/c1/export");
});

test("a hostile conversation id cannot escape the transcript path", async () => {
  const { calls, alpha } = fixture(async () => json({ conversation_id: "x", entries: [] }));
  // `encodeURIComponent` alone is not the defence: `%2F` can still be decoded
  // back into a separator by something downstream. The shared api-client
  // rejects `..` outright, so the traversal never becomes a request at all.
  await assert.rejects(() => alpha.getTranscript("../admin"), /route/i);
  assert.equal(calls.length, 0, "a traversal attempt must not reach the network");
});

test("an ordinary id with a reserved character is still addressable", async () => {
  const { calls, alpha } = fixture(async () => json({ conversation_id: "c-1", entries: [] }));
  await alpha.getTranscript("c 1");
  assert.equal(calls[0].url, "/api/peer-network/transcripts/c%201?limit=1000");
});

test("read limits are clamped to what the Gateway accepts", async () => {
  const { calls, alpha } = fixture(async () => json({ transcripts: [], count: 0, enabled: true }));
  await alpha.listTranscripts({ limit: 99999 });
  assert.match(calls[0].url, /limit=200$/);
  await alpha.listTranscripts({ limit: 0 });
  assert.match(calls[1].url, /limit=1$/);
});

// ── honesty: bounded is not complete ────────────────────────────────────────

test("truncation is surfaced, not swallowed", async () => {
  const { alpha } = fixture(async () => json({ conversation_id: "c1", entries: [], count: 0, truncated: true, counts: { messages: 99, turns: 9 } }));
  const transcript = await alpha.getTranscript("c1");
  assert.equal(transcript.truncated, true);
});

test("a turn's event truncation is surfaced", async () => {
  const { alpha } = fixture(async () =>
    json({ conversation_id: "c1", turn: { run_id: "r1", events: [], events_truncated: true, events_shown: 5, events_total: 50 } }),
  );
  const turn = await alpha.getTurnForensics("c1", "r1");
  assert.equal(turn.events_truncated, true);
  assert.equal(turn.events_shown, 5);
  assert.equal(turn.events_total, 50);
});

test("a fully-loaded turn reports truncation false", async () => {
  const { alpha } = fixture(async () => json({ conversation_id: "c1", turn: { run_id: "r1", events: [], events_truncated: false, events_total: 2 } }));
  assert.equal((await alpha.getTurnForensics("c1", "r1")).events_truncated, false);
});

// ── honesty: attribution ────────────────────────────────────────────────────

test("remote and local sides keep distinct roles and sides", async () => {
  const { alpha } = fixture(async () =>
    json({
      conversation_id: "c1",
      count: 2,
      truncated: false,
      counts: { messages: 1, turns: 1 },
      entries: [
        { entry_id: "e1", role: "peer_message", side: "peer", text: "from the other install", created_at: "2026-01-01T00:00:00+00:00" },
        { entry_id: "e2", role: "local_reply", side: "local", text: "my reply", created_at: "2026-01-01T00:00:05+00:00", run_id: "r1" },
      ],
    }),
  );
  const transcript = await alpha.getTranscript("c1");
  assert.deepEqual(transcript.entries.map((e) => e.role), ["peer_message", "local_reply"]);
  assert.deepEqual(transcript.entries.map((e) => e.side), ["peer", "local"]);
  assert.equal(transcript.entries[0].text, "from the other install");
  assert.equal(transcript.entries[1].text, "my reply");
  assert.equal(transcript.entries[1].run_id, "r1");
});

test("an unknown role never becomes a speech role", async () => {
  const { alpha } = fixture(async () =>
    json({ conversation_id: "c1", entries: [{ entry_id: "e1", role: "system_override", side: "local", text: "x" }], count: 1, counts: {} }),
  );
  // Falls back to activity so an unrecognised value cannot be rendered as the
  // Agent having said something.
  assert.equal((await alpha.getTranscript("c1")).entries[0].role, "local_event");
});

test("role labels name the speaker honestly", () => {
  const { alpha } = fixture(async () => json({}));
  assert.equal(alpha.transcriptRoleLabel("peer_message"), "From peer Alpha");
  assert.equal(alpha.transcriptRoleLabel("local_reply"), "This Alpha replied");
  assert.equal(alpha.transcriptRoleLabel("local_event"), "Local Agent activity");
});

// ── honesty: missing is not empty ───────────────────────────────────────────

test("an omitted field becomes null, not an empty string", async () => {
  const { alpha } = fixture(async () => json({ conversation_id: "c1", entries: [{ entry_id: "e1", role: "peer_message", side: "peer" }], count: 1, counts: {} }));
  const entry = (await alpha.getTranscript("c1")).entries[0];
  assert.equal(entry.text, "");
  assert.equal(entry.created_at, null);
  assert.equal(entry.status, null);
  assert.equal(entry.delivery_error, null);
});

test("a deleted peer renders as not reported rather than blank", async () => {
  const { alpha } = fixture(async () => json({ conversation_id: "c1", entries: [], count: 0, peer: {}, counts: {} }));
  const peer = (await alpha.getTranscript("c1")).peer;
  assert.equal(peer.agent_id, null);
  assert.equal(peer.name, null);
  assert.equal(peer.auto_reply, false);
});

test("a non-string count does not become NaN in the UI", async () => {
  const { alpha } = fixture(async () => json({ conversation_id: "c1", entries: [], count: 0, counts: { messages: "many", turns: null } }));
  const transcript = await alpha.getTranscript("c1");
  assert.equal(transcript.counts.messages, 0);
  assert.equal(transcript.counts.turns, 0);
});

// ── delivery receipts stay per-recipient ────────────────────────────────────

test("a failed recipient is preserved next to a delivered one", async () => {
  const { alpha } = fixture(async () =>
    json({
      conversation_id: "c1",
      count: 1,
      counts: {},
      entries: [{ entry_id: "e1", role: "peer_message", side: "local", deliveries: [{ recipient_id: "a", status: "delivered" }, { recipient_id: "b", status: "failed", error: "offline" }] }],
    }),
  );
  const receipts = (await alpha.getTranscript("c1")).entries[0].deliveries;
  assert.deepEqual(receipts.map((r) => r.status), ["delivered", "failed"]);
  assert.equal(receipts[1].error, "offline");
});

// ── enabled flag: off is not empty ─────────────────────────────────────────

test("enabled=false is preserved so the UI can say the plane is off", async () => {
  const { alpha } = fixture(async () => json({ transcripts: [], count: 0, enabled: false }));
  assert.equal((await alpha.listTranscripts()).enabled, false);
});

// ── no raw transport ────────────────────────────────────────────────────────

test("the client never calls raw fetch", () => {
  const source = sources["external-alpha"];
  const bare = source.match(/(^|[^.\w])fetch\s*\(/g) ?? [];
  assert.deepEqual(bare, [], `raw fetch call(s) found: ${bare.join(", ")}`);
});

test("the live stream goes through apiFetch, not a bare endpoint string", () => {
  const source = readFileSync(new URL("./external-alpha.ts", import.meta.url), "utf8");
  assert.match(source, /apiFetch\("\/peer-network\/events"/);
});

// ── backend contract pin ───────────────────────────────────────────────────

test("contract pin: the Gateway really declares the transcript routes", () => {
  const read = (rel) => readFileSync(new URL(`../../../${rel}`, import.meta.url), "utf8");
  const router = read("backend/app/gateway/routers/peer_network.py");
  for (const path of ["/transcripts", "/transcripts/{conversation_id}", "/transcripts/{conversation_id}/turns/{run_id}", "/transcripts/{conversation_id}/export"]) {
    assert.ok(router.includes(path), `peer_network.py no longer declares ${path}`);
  }
});

test("contract pin: the transcript projection is an allowlist over peer fields", () => {
  const read = (rel) => readFileSync(new URL(`../../../${rel}`, import.meta.url), "utf8");
  const transcript = read("backend/packages/harness/alpha/peer_network/transcript.py");
  // The safe keys must be named; the unsafe ones must never be read.
  assert.match(transcript, /"agent_id": peer\.get\("agent_id"\)/);
  for (const forbidden of ["outbound_token", "token_hash", "websocket_url", "card_json"]) {
    assert.ok(!transcript.includes(`.get("${forbidden}")`), `transcript.py reads ${forbidden}`);
  }
});

test("contract pin: transcript routes document why owner_check is absent", () => {
  const read = (rel) => readFileSync(new URL(`../../../${rel}`, import.meta.url), "utf8");
  const router = read("backend/app/gateway/routers/peer_network.py");
  // A missing owner_check must read as a decision. If this comment is ever
  // deleted, the authorization model becomes unexplained.
  assert.match(router, /deliberately do NOT use/);
  assert.match(router, /_assert_peer_network_scope/);
});

test("contract pin: the retention floor exists so a 0 cannot wipe history", () => {
  const read = (rel) => readFileSync(new URL(`../../../${rel}`, import.meta.url), "utf8");
  const service = read("backend/packages/harness/alpha/peer_network/service.py");
  assert.match(service, /_MIN_RETENTION_DAYS\s*=\s*7/);
});