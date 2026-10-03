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

// ── advanced surface: search, analytics, trace ──────────────────────────────

test("search sends the query and preserves the FTS-availability flag", async () => {
  const { calls, alpha } = fixture(async () =>
    json({ query: "deploy", entries: [{ entry_id: "e1", role: "peer_message", side: "peer", text: "deploy log" }], count: 1, fts_available: false, empty_query: false }),
  );
  const result = await alpha.searchTranscripts({ q: "deploy" });
  assert.match(calls[0].url, /\/api\/peer-network\/transcripts\/search\?q=deploy/);
  assert.equal(result.count, 1);
  // False means a substring scan ran; the UI must be able to say so.
  assert.equal(result.fts_available, false);
  assert.equal(result.entries[0].role, "peer_message");
});

test("an empty query is still sent, so the Gateway decides what empty means", async () => {
  const { calls, alpha } = fixture(async () => json({ query: "", entries: [], count: 0, fts_available: true, empty_query: true }));
  const result = await alpha.searchTranscripts({ q: "" });
  assert.match(calls[0].url, /q=/);
  assert.equal(result.empty_query, true);
  assert.deepEqual(result.entries, []);
});

test("search filters are only sent when meaningful", async () => {
  const { calls, alpha } = fixture(async () => json({ entries: [], count: 0, fts_available: true, empty_query: false }));
  await alpha.searchTranscripts({ q: "x", conversation_id: "conv 1", direction: "inbound" });
  assert.match(calls[0].url, /conversation_id=conv\+1/);
  assert.match(calls[0].url, /direction=inbound/);

  await alpha.searchTranscripts({ q: "x", direction: "sideways" });
  assert.ok(!calls[1].url.includes("direction"), "an unsupported direction must not be forwarded");
});

test("search limit is clamped to the Gateway's ceiling", async () => {
  const { calls, alpha } = fixture(async () => json({ entries: [], count: 0, fts_available: true, empty_query: false }));
  await alpha.searchTranscripts({ q: "x", limit: 99999 });
  assert.match(calls[0].url, /limit=200/);
});

test("analytics maps every histogram and survives a non-object payload", async () => {
  const { calls, alpha } = fixture(async () =>
    json({ totals: { messages: 12, peers: 2 }, conversations: 3, modes: { direct: 3 }, kinds: null, directions: { inbound: 7 }, statuses: { delivered: 12 }, fts_available: true, retention_days: 90 }),
  );
  const analytics = await alpha.getTranscriptAnalytics();
  assert.equal(calls[0].url, "/api/peer-network/transcripts/analytics");
  assert.equal(analytics.totals.messages, 12);
  assert.equal(analytics.conversations, 3);
  // A null histogram becomes {}, never undefined — the UI must not have to tell
  // "no data" apart from "field missing".
  assert.deepEqual(analytics.kinds, {});
  assert.deepEqual(analytics.directions, { inbound: 7 });
  assert.equal(analytics.retention_days, 90);
});

test("analytics drops non-numeric counts instead of rendering NaN", async () => {
  const { alpha } = fixture(async () => json({ totals: { messages: "lots" }, kinds: { chat: "many" }, conversations: 2 }));
  const analytics = await alpha.getTranscriptAnalytics();
  assert.equal(analytics.totals.messages, undefined);
  assert.deepEqual(analytics.kinds, {});
});

test("the turn trace route is read and its honesty note preserved", async () => {
  const { calls, alpha } = fixture(async () =>
    json({ conversation_id: "c1", run_id: "r1", traces: [{ event_id: "e1", severity: "error", event_type: "tool.failed" }], count: 1, scanned: 3, has_more: false, after_seq: 9, note: "An empty list is not proof the turn did no work." }),
  );
  const trace = await alpha.getTurnTrace("c1", "r1");
  assert.equal(calls[0].url, "/api/peer-network/transcripts/c1/turns/r1/trace");
  assert.equal(trace.count, 1);
  assert.equal(trace.traces[0].severity, "error");
  assert.equal(trace.after_seq, 9);
  // The Gateway's caveat is carried to the UI verbatim.
  assert.match(trace.note, /not proof/);
});

test("an empty trace still carries the note rather than a bare list", async () => {
  const { alpha } = fixture(async () => json({ conversation_id: "c1", run_id: "r1", traces: [], count: 0, has_more: false, note: "only runs whose writer emitted them" }));
  const trace = await alpha.getTurnTrace("c1", "r1");
  assert.deepEqual(trace.traces, []);
  assert.equal(trace.count, 0);
  assert.ok(trace.note.length > 0);
});

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
  for (const path of [
    "/transcripts",
    "/transcripts/search",
    "/transcripts/analytics",
    "/transcripts/{conversation_id}",
    "/transcripts/{conversation_id}/turns/{run_id}",
    "/transcripts/{conversation_id}/turns/{run_id}/trace",
    "/transcripts/{conversation_id}/export",
  ]) {
    assert.ok(router.includes(path), `peer_network.py no longer declares ${path}`);
  }
});

test("contract pin: literal sub-paths are declared before the parameterised route", () => {
  // Starlette matches in declaration order. If `/transcripts/{conversation_id}`
  // came first it would swallow `/transcripts/search` and answer
  // "Conversation 'search' not found", making search unreachable from the UI.
  const read = (rel) => readFileSync(new URL(`../../../${rel}`, import.meta.url), "utf8");
  const router = read("backend/app/gateway/routers/peer_network.py");
  const searchAt = router.indexOf('@router.get("/transcripts/search"');
  const analyticsAt = router.indexOf('@router.get("/transcripts/analytics"');
  const catchAllAt = router.indexOf('@router.get("/transcripts/{conversation_id}"');
  assert.ok(searchAt > 0 && analyticsAt > 0 && catchAllAt > 0, "a transcript route is missing");
  assert.ok(searchAt < catchAllAt, "/transcripts/search must precede the {conversation_id} catch-all");
  assert.ok(analyticsAt < catchAllAt, "/transcripts/analytics must precede the {conversation_id} catch-all");
});

test("contract pin: the FTS index keeps itself current rather than trusting a count", () => {
  const read = (rel) => readFileSync(new URL(`../../../${rel}`, import.meta.url), "utf8");
  const storage = read("backend/packages/harness/alpha/peer_network/storage.py");
  // External-content FTS tables are not self-populating.
  for (const trigger of ["messages_fts_ai", "messages_fts_ad", "messages_fts_au"]) {
    assert.ok(storage.includes(trigger), `missing ${trigger} — an external-content index would never update`);
  }
  assert.match(storage, /'rebuild'/);
  // The backfill guard must not be a count: count(*) on an external-content FTS
  // table reads the shadow tables and reports a plausible number for an empty
  // live index, so search would silently break.
  assert.ok(!/count\(\*\)\s*FROM messages_fts/.test(storage), "the backfill guard must not count FTS rows");
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