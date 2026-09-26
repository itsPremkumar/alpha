// channels.test.mjs — pins `lib/channels.ts` against the REAL backend contract.
//
// GET /api/channels returns `channels` as a MAP keyed by channel name, not an
// array:
//   {"service_running": true, "channels": {"slack": {"enabled": true, "running": true}, ...}}
// (backend/app/channels/service.py:505-506; routers/channels.py types it
//  ChannelStatusResponse; backend/tests/test_channels.py:8155 asserts
//  get_status()["channels"]["slack"]["running"] is True).
//
// The client used to pipe that dict through `asList`, which only accepts
// arrays, so `channelStatus()` returned [] for every deployment and the
// Channels section permanently claimed "No channels running".
//
// Pure Node test: transpiles the real http.ts + channels.ts and rewires their
// relative imports to data: URL stubs — no server, no browser.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";
import { moduleUrl } from "./test-modules.mjs";

const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;
const transpile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
const read = (url) => readFileSync(new URL(url, import.meta.url), "utf8");

/* ── Stub: api-client backed by a queue of canned Responses ─────────────── */
const apiClientStub = `
export class ApiClientError extends Error {
  // Mirrors api-client.ts:33-45 exactly, message construction included, so an
  // assertion on the error text proves the REAL text a caller would see.
  constructor(kind, status = 0, detail = null) {
    const validStatus = Number.isInteger(status) && status >= 100 && status <= 599 ? status : 0;
    super(kind === "http"
      ? \`Request failed\${validStatus ? \` (HTTP \${validStatus})\` : ""}.\${detail ? \` \${detail}\` : ""}\`
      : kind === "stopped" ? "Request stopped locally."
      : kind === "response" ? "The server returned an unreadable response."
      : kind === "route" ? "Invalid API route."
      : "The request could not be completed. Check your connection.");
    this.name = "ApiClientError";
    this.kind = kind;
    this.status = validStatus;
    this.detail = detail;
  }
}
export const GATEWAY_BASE = "http://gateway.test/api";
let queue = [];
export function setResponses(responses) { queue = [...responses]; }
export async function apiFetch(_path, _init) {
  const r = queue.length > 0 ? queue.shift() : undefined;
  if (!r) throw new Error("no canned response queued for " + _path);
  if (r.error) throw r.error;
  const status = r.status ?? 200;
  const res = new Response(JSON.stringify(r.body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
  // Faithful to the real api-client (api-client.ts:106-125): a non-2xx never
  // comes back as a Response, it throws. Without this the stub would let a 503
  // flow through as data, which production never does.
  if (!res.ok) throw new ApiClientError("http", status, r.detail ?? (r.body && r.body.detail) ?? null);
  return res;
}
`;
const apiClientUrl = toDataUrl(apiClientStub);

/* ── REAL http.ts, only its api-client import swapped for the stub ───────── */
let httpCode = transpile(read("./http.ts"));
httpCode = httpCode.replace(/from\s+"\.\/api-client"/g, `from "${apiClientUrl}"`);
assert.equal(httpCode.includes('"./api-client"'), false, "http.ts api-client specifier survived");
const httpUrl = toDataUrl(httpCode);

/* ── REAL channels.ts, its ./http import swapped for the real http module ── */
let channelsCode = transpile(read("./channels.ts"));
channelsCode = channelsCode.replace(/from\s+"\.\/http"/g, `from "${httpUrl}"`);
assert.equal(channelsCode.includes('"./http"'), false, "channels.ts http specifier survived");
const { channelStatus, listProviders, listConnections, larkStatus } = await import(toDataUrl(channelsCode));
const { setResponses, ApiClientError } = await import(apiClientUrl);

// Verbatim from the live Gateway (GET /api/channels, 10 channels, all disabled).
const LIVE_CHANNELS = {
  service_running: true,
  channels: {
    buzz: { enabled: false, running: false },
    dingtalk: { enabled: false, running: false },
    discord: { enabled: false, running: false },
    feishu: { enabled: false, running: false },
    github: { enabled: false, running: false },
    signal: { enabled: false, running: false },
    slack: { enabled: true, running: true },
    telegram: { enabled: false, running: false },
    wechat: { enabled: false, running: false },
    wecom: { enabled: false, running: false },
  },
};

test("channelStatus surfaces the backend's channel map instead of reporting zero channels", async () => {
  setResponses([{ body: LIVE_CHANNELS }]);
  const channels = await channelStatus();

  assert.equal(channels.length, 10, `expected 10 channels from the live contract, got ${channels.length}`);
  assert.deepEqual(
    channels.map((c) => c.name).sort(),
    ["buzz", "dingtalk", "discord", "feishu", "github", "signal", "slack", "telegram", "wechat", "wecom"],
  );

  const slack = channels.find((c) => c.name === "slack");
  assert.equal(slack.enabled, true, "enabled flag must come through");
  assert.equal(slack.connected, true, "`running` must map to `connected` for the badge");

  const discord = channels.find((c) => c.name === "discord");
  assert.equal(discord.enabled, false);
  assert.equal(discord.connected, false);
});

test("channelStatus keeps a genuinely empty channel map as an empty list", async () => {
  setResponses([{ body: { service_running: true, channels: {} } }]);
  assert.deepEqual(await channelStatus(), []);
});

test("channelStatus still tolerates an array-shaped channels payload", async () => {
  setResponses([
    {
      body: {
        service_running: true,
        channels: [
          { name: "slack", enabled: true, running: true },
          { channel: "discord", enabled: false, running: false },
        ],
      },
    },
  ]);
  const channels = await channelStatus();
  assert.deepEqual(
    channels.map((c) => [c.name, c.connected]),
    [
      ["slack", true],
      ["discord", false],
    ],
  );
});

test("listProviders and listConnections read their envelope arrays", async () => {
  setResponses([
    { body: { enabled: true, providers: [{ id: "slack", name: "Slack", configured: true }] } },
    { body: { connections: [{ id: "c1", provider: "slack", label: "team" }] } },
  ]);
  const providers = await listProviders();
  const connections = await listConnections();
  assert.deepEqual(providers.map((p) => p.id), ["slack"]);
  assert.equal(providers[0].configured, true);
  assert.deepEqual(connections.map((c) => c.id), ["c1"]);
});

/* ── F11: a FAILED request must reject, never resolve as an empty list ────
 *
 * `frontend/src/AGENTS.md` (Client honesty rules): "Do not catch-and-empty a
 * failed request. Surface the reason; an empty list must mean 'the server said
 * there is nothing', not 'the call failed'."
 *
 * Before this, `channelStatus` / `listProviders` / `listConnections` wrapped
 * their call in `try { … } catch { return [] }`, so an unreachable Gateway
 * produced the same `[]` as a healthy one — which made `system.ts:68` report
 * `ok:true, "none linked"` for a DOWN server and left `ChannelsSection.tsx:28`'s
 * `catch → setError` unreachable.
 * ─────────────────────────────────────────────────────────────────────────── */

// The real transport failure: api-client.ts:104 throws ApiClientError("network")
// when fetch itself rejects (connection refused, DNS, offline).
const unreachable = () => ({ error: new ApiClientError("network") });

test("channelStatus rejects when the gateway is unreachable instead of resolving []", async () => {
  setResponses([unreachable()]);
  await assert.rejects(() => channelStatus(), /could not be completed/);
});

test("channelStatus rejects on a 503 instead of rendering an empty channel list", async () => {
  setResponses([{ status: 503, body: { detail: "Channels subsystem unavailable" } }]);
  await assert.rejects(() => channelStatus(), /HTTP 503.*Channels subsystem unavailable/);
});

test("listProviders rejects when the gateway is unreachable instead of resolving []", async () => {
  setResponses([unreachable()]);
  await assert.rejects(() => listProviders(), /could not be completed/);
});

test("listConnections rejects when the gateway is unreachable instead of resolving []", async () => {
  setResponses([unreachable()]);
  await assert.rejects(() => listConnections(), /could not be completed/);
});

test("larkStatus rejects when the gateway is unreachable instead of resolving null", async () => {
  setResponses([unreachable()]);
  await assert.rejects(() => larkStatus(), /could not be completed/);
});

test("a genuinely empty list is still [] — empty means the server said empty, not that it failed", async () => {
  setResponses([
    { body: { enabled: true, providers: [] } },
    { body: { connections: [] } },
  ]);
  assert.deepEqual(await listProviders(), []);
  assert.deepEqual(await listConnections(), []);
});

test("the channels probe reports a dead gateway as down, not as a healthy subsystem with no channels", () => {
  // system.ts must keep channelStatus inside runProbe (which turns a rejection
  // into ok:false + the real reason) and must NOT pre-catch it back into [].
  const src = read("./system.ts");
  assert.match(src, /runProbe\("channels"/);
  assert.match(src, /async \(\) => channelStatus\(\)/);
  assert.doesNotMatch(src, /channelStatus\(\)\s*\.catch\(\s*\(\)\s*=>\s*\[\]/);
  // ChannelsSection's catch is the surface that must become reachable again.
  const section = read("../components/sections/ChannelsSection.tsx");
  assert.match(section, /catch \(e\) \{\s*\n\s*setError\(errMsg\(e\)\)/);
});
