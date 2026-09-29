// channels-disabled-honesty.test.mjs — a subsystem switched off by config.
//
// Live `GET /api/channels/providers` on a default install answers:
//
//   {"enabled": false, "providers": []}
//
// `enabled` is the channel-connections master switch, and it is the *cause* of
// the empty provider list: backend/app/gateway/routers/channel_connections.py:647
// filters the catalog down to the providers the config has enabled, so with the
// switch off no provider qualifies. The client returned a bare `[]`, which made
// "switched off by configuration" render exactly like "this build has no chat
// apps" — the reader is sent to debug the wrong thing.
//
// The same tab also headed its roster "Running channels (N)", where N counted
// every channel the Gateway knows about. The live response is 10 channels with
// `enabled:false, running:false` on all of them, so that heading read
// "Running channels (10)" while nothing was running.
//
// frontend/AGENTS.md: "Defaults that are off must look off. An autonomy loop
// that is disabled by configuration renders as disabled with the reason, not as
// idle/healthy."
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;
const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const transpile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;

const httpStub = `
export async function get(path) {
  if (path === "/channels") return { service_running: true, channels: {
    buzz: { enabled: false, running: false }, dingtalk: { enabled: false, running: false },
    discord: { enabled: false, running: false }, feishu: { enabled: false, running: false },
    github: { enabled: false, running: false }, signal: { enabled: false, running: false },
    slack: { enabled: false, running: false }, telegram: { enabled: false, running: false },
    wechat: { enabled: false, running: false }, wecom: { enabled: false, running: false } } };
  if (path === "/channels/providers") return { enabled: false, providers: [] };
  if (path === "/channels/connections") return { connections: [] };
  return {};
}
export async function send() { return {}; }
export function asList(body, keys) {
  for (const k of keys) if (Array.isArray(body?.[k])) return body[k];
  return Array.isArray(body) ? body : [];
}
export function pick(obj, keys, fallback) {
  for (const k of keys) if (obj?.[k] !== undefined && obj[k] !== null) return obj[k];
  return fallback;
}
`;
const httpUrl = toDataUrl(httpStub);

let code = transpile(read("./channels.ts")).replace(/from\s+"\.\/http"/, `from "${httpUrl}"`);
const { channelStatus, listProviders } = await import(toDataUrl(code));

/* ══ 1. The master switch is no longer discarded ════════════════════════ */

test("listProviders reports the subsystem as DISABLED, not as an empty catalog", async () => {
  // This is the live default-install response.
  const catalog = await listProviders();
  assert.equal(catalog.enabled, false, "the switch is the reason the list is empty and must survive the read");
  assert.deepEqual(catalog.providers, []);
});

test("a switch that is on is distinguished from one that was never reported", async () => {
  // `enabled: true` with providers is the working case.
  const httpStub2 = httpStub.replace(
    'if (path === "/channels/providers") return { enabled: false, providers: [] };',
    'if (path === "/channels/providers") return { enabled: true, providers: [{ id: "slack", name: "Slack", configured: false }] };',
  );
  const url2 = toDataUrl(httpStub2);
  const mod2 = await import(
    toDataUrl(transpile(read("./channels.ts")).replace(/from\s+"\.\/http"/, `from "${url2}"`))
  );
  const catalog = await mod2.listProviders();
  assert.equal(catalog.enabled, true);
  assert.equal(catalog.providers.length, 1);
});

test("an omitted enabled field is unknown, never guessed to either value", async () => {
  const stub = httpStub.replace(
    'if (path === "/channels/providers") return { enabled: false, providers: [] };',
    'if (path === "/channels/providers") return { providers: [] };',
  );
  const url = toDataUrl(stub);
  const mod = await import(toDataUrl(transpile(read("./channels.ts")).replace(/from\s+"\.\/http"/, `from "${url}"`)));
  const catalog = await mod.listProviders();
  assert.equal(catalog.enabled, null, "the Gateway said nothing; the client must not invent a switch state");
});

test("a disabled subsystem is still a real, non-failing read", async () => {
  // Shape 2 guard: "switched off" must not become "the call failed". `[]` and
  // `enabled:false` together still mean a successful read.
  const catalog = await listProviders();
  assert.equal(typeof catalog.enabled, "boolean");
  assert.ok(Array.isArray(catalog.providers));
});

/* ══ 2. The roster heading no longer claims every channel is running ════ */

test("the live roster is 10 channels with none running", async () => {
  const channels = await channelStatus();
  assert.equal(channels.length, 10);
  assert.equal(channels.filter((c) => c.connected).length, 0);
  assert.equal(channels.filter((c) => c.enabled).length, 0);
});

test("ChannelsSection does not head a non-running roster with 'Running channels'", () => {
  const src = read("../components/sections/ChannelsSection.tsx");
  assert.doesNotMatch(src, />Running channels \(\{/, "the heading must not present the total count as a running count");
  // It must state the running count separately, and disclose a zero.
  assert.match(src, /runningChannels/);
  assert.match(src, /is running|None of these/);
});

test("ChannelsSection keeps the switch and renders a disabled catalog with its reason", () => {
  const src = read("../components/sections/ChannelsSection.tsx");
  assert.match(src, /providerCatalog\.enabled === false/, "an empty catalog with the switch off must say 'switched off'");
  assert.match(src, /switched off/i, "the reason the list is empty must be named");
});

test("ChannelsSection disables the connect control while a connect is in flight", () => {
  // Shape 5: POST /channels/{provider}/connect mints a pairing code server-side,
  // so an unguarded double-click issues two connect attempts.
  const src = read("../components/sections/ChannelsSection.tsx");
  assert.match(src, /if \(busyAction\) return;/, "the handler must synchronously refuse a second click");
  assert.match(src, /setBusyAction\(busyKey\("connect", providerId\)\)/);
  assert.match(src, /disabled=\{isBusy\(busyKey\("connect", p\.id\)\)\}/);
  assert.match(src, /finally \{[\s\S]*setBusyAction\(null\)/);
});

test("restart and unlink hold the same lock as connect", () => {
  // Same defect class in the same section: a second restart POSTs twice, and a
  // second DELETE for a link the server already removed 404s.
  const src = read("../components/sections/ChannelsSection.tsx");
  for (const action of ["restart", "unplug"]) {
    assert.match(src, new RegExp(`if \\(busyAction\\) return;`), `${action} must be guarded`);
    assert.match(src, new RegExp(`busyKey\\("${action}"`), `${action} must be keyed in the busy lock`);
  }
  assert.match(src, /const onRestart = async/);
  assert.match(src, /const onDisconnect = async/);
  // Restart and unlink must re-read rather than assume their own effect.
  const restart = src.slice(src.indexOf("const onRestart"), src.indexOf("const onDisconnect"));
  assert.ok(restart.indexOf("await restartChannel") < restart.lastIndexOf("await load()"), "restart must re-read the roster");
  const unplug = src.slice(src.indexOf("const onDisconnect"));
  assert.ok(unplug.indexOf("await disconnectConnection") < unplug.indexOf("await load()"), "unlink must re-read the list");
});
