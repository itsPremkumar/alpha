// system-probe-honesty.test.mjs — the System grid must not let one subsystem's
// health stand in for another's.
//
// The defect this pins: `lib/system.ts` summarised IM channel links as
// `${c.length} running`, which counted the size of the *configured* channel set
// rather than the links that were actually connected. On a default install
// `/api/channels` returns all ten supported platforms with
// `{enabled: false, running: false}`, so the row rendered a green
// "10 running" while nothing was connected. Sitting in the same grid as the
// composer, that row is what made voice's own honest
// "no speech models installed" state look like a failure of something else.
//
// The fix counts connected links only, names the platforms in the blurb, and
// adds a voice row sourced from voice's OWN capabilities report — because a
// speech request must read the speech subsystem, never a channel link.
//
// Pure Node test: transpiles the real system.ts and rewires its imports to
// data: URL stubs — no server, no browser.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;

/** A probe dependency that returns a fixed value (or throws the server's reason). */
function stub(name, body) {
  return `export function ${name}() { ${body} }`;
}

const channelFixture = JSON.stringify({
  service_running: true,
  channels: {
    buzz: { enabled: false, running: false },
    dingtalk: { enabled: false, running: false },
    discord: { enabled: false, running: false },
    feishu: { enabled: false, running: false },
    github: { enabled: false, running: false },
    signal: { enabled: false, running: false },
    slack: { enabled: false, running: false },
    telegram: { enabled: false, running: false },
    wechat: { enabled: false, running: false },
    wecom: { enabled: false, running: false },
  },
});

/** Voice report shaped exactly like GET /api/multimodal/capabilities. */
function capabilities(overrides = {}) {
  const { enabled = true, stt = "not_installed", tts = "not_installed" } = overrides;
  return {
    rows: [
      { capability: "stt", tier: "T1", engine: "(none)", status: "policy_disabled", detail: "" },
      { capability: "stt", tier: "T3", engine: "faster-whisper", status: stt, detail: "" },
      { capability: "tts", tier: "T1", engine: "(none)", status: "policy_disabled", detail: "" },
      { capability: "tts", tier: "T3", engine: "piper", status: tts, detail: "" },
    ],
    voice: { enabled, routing: { mode: "local_only" } },
    note: "statuses are import/config observations only",
  };
}

/** Load the real `lib/system.ts` with every probe dependency stubbed. */
async function loadProbeAll(deps) {
  const source = readFileSync(new URL("./system.ts", import.meta.url), "utf8");
  const stubs = {
    http: stub("get", "return Promise.resolve({});"),
    workspace: stub("fetchConsoleStats", "return Promise.resolve({ runs: 0, threads: 0 });"),
    memory: stub("fetchMemory", "return Promise.resolve({ facts: [] });"),
    skills: stub("listSkills", "return Promise.resolve([]);"),
    scheduled: stub("listScheduledTasks", "return Promise.resolve([]);"),
    channels: stub("channelStatus", "return Promise.resolve(globalThis.__channels);"),
    supervision: [
      // Every named export `./supervision` actually provides. ESM validates
      // named imports at link time, so a stub missing one is a link-time
      // SyntaxError in a file that has nothing to do with the probe - which is
      // how a stale stub turns a suite red for the wrong reason. Three of these
      // arrived with the watchdog fix that made the fleet state honest; this stub
      // predates it and carried only the first.
      stub("supervisionFleet", "return Promise.resolve({});"),
      stub("supervisionAnomalies", "return Promise.resolve([]);"),
      stub("recoverWorker", "return Promise.resolve('');"),
      stub("adoptOrphans", "return Promise.resolve('');"),
      stub("parseFleetWorkers", "return [];"),
      stub("fetchFleetWorkers", "return Promise.resolve([]);"),
      stub("watchdogDetail", "return 'no workers reporting';"),
      stub("fetchAnomaliesStrict", "return Promise.resolve([]);"),
    ].join("\n"),
    teamops: stub("companyStatus", "return Promise.resolve({});"),
    mcp: stub("fetchMcpConfig", "return Promise.resolve([]);"),
    multimodal: stub("getCapabilities", "return Promise.resolve(globalThis.__capabilities);"),
    ...deps,
  };
  let code = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
  code = code.replace(/from "\.\/([a-z-]+)"/g, (_, dep) => `from "${toDataUrl(stubs[dep] ?? "export {};")}"`);
  const mod = await import(toDataUrl(code));
  return mod.probeAll;
}

function find(probes, key) {
  const probe = probes.find((p) => p.key === key);
  assert.ok(probe, `expected a "${key}" probe, got ${probes.map((p) => p.key).join(", ")}`);
  return probe;
}

test("a fully-disabled channel roster reports nothing connected, not \"10 running\"", async () => {
  globalThis.__channels = [
    { name: "slack", enabled: false, connected: false, status: "stopped" },
    { name: "telegram", enabled: false, connected: false, status: "stopped" },
    { name: "discord", enabled: false, connected: false, status: "stopped" },
  ];
  globalThis.__capabilities = capabilities();
  try {
    const probeAll = await loadProbeAll({});
    const channels = find(await probeAll(), "channels");
    assert.equal(channels.ok, true);
    assert.doesNotMatch(channels.detail, /running/);
    assert.equal(channels.detail, "none connected");
  } finally {
    delete globalThis.__channels;
    delete globalThis.__capabilities;
  }
});

test("the channel row counts connected links and names the platforms", async () => {
  globalThis.__channels = [
    { name: "slack", enabled: true, connected: true, status: "running" },
    { name: "telegram", enabled: true, connected: true, status: "running" },
    { name: "discord", enabled: false, connected: false, status: "stopped" },
  ];
  globalThis.__capabilities = capabilities();
  try {
    const probeAll = await loadProbeAll({});
    const channels = find(await probeAll(), "channels");
    // 2 of 3 are connected: the count is the live subset, not the roster size.
    assert.equal(channels.detail, "2 connected");
    assert.match(channels.blurb, /Telegram \/ Slack \/ Discord/);
  } finally {
    delete globalThis.__channels;
    delete globalThis.__capabilities;
  }
});

test("an empty channel roster and an all-stopped roster read differently", async () => {
  const read = async (channels) => {
    globalThis.__channels = channels;
    globalThis.__capabilities = capabilities();
    try {
      return find(await (await loadProbeAll({}))(), "channels").detail;
    } finally {
      delete globalThis.__channels;
      delete globalThis.__capabilities;
    }
  };
  // "nothing linked" and "linked but stopped" are different operator problems.
  assert.equal(await read([]), "no platforms linked");
  assert.equal(await read([{ name: "slack", enabled: false, connected: false, status: "stopped" }]), "none connected");
});

test("voice health is probed from the voice report and never from the channel row", async () => {
  globalThis.__channels = [{ name: "slack", enabled: true, connected: true, status: "running" }];
  globalThis.__capabilities = capabilities();
  try {
    const probeAll = await loadProbeAll({});
    const probes = await probeAll();
    const voice = find(probes, "voice");
    // A perfectly healthy channel link cannot make voice look ready.
    assert.equal(find(probes, "channels").ok, true);
    assert.equal(voice.ok, false);
    assert.match(voice.detail, /make voice-setup/);
    assert.match(voice.blurb, /Separate from the IM channel links/);
  } finally {
    delete globalThis.__channels;
    delete globalThis.__capabilities;
  }
});

test("a voice-enabled install with both local engines reports ready", async () => {
  globalThis.__channels = [];
  globalThis.__capabilities = capabilities({ stt: "available", tts: "available" });
  try {
    const voice = find(await (await loadProbeAll({}))(), "voice");
    assert.equal(voice.ok, true);
    assert.equal(voice.detail, "local STT + TTS ready");
  } finally {
    delete globalThis.__channels;
    delete globalThis.__capabilities;
  }
});

test("voice disabled by config says so and does not blame missing models", async () => {
  globalThis.__channels = [];
  globalThis.__capabilities = capabilities({ enabled: false });
  try {
    const voice = find(await (await loadProbeAll({}))(), "voice");
    assert.equal(voice.ok, false);
    assert.match(voice.detail, /voice\.enabled=false/);
    assert.doesNotMatch(voice.detail, /make voice-setup/);
  } finally {
    delete globalThis.__channels;
    delete globalThis.__capabilities;
  }
});

test("a partial install names which engine is ready instead of claiming both", async () => {
  globalThis.__channels = [];
  globalThis.__capabilities = capabilities({ stt: "available", tts: "not_configured" });
  try {
    const voice = find(await (await loadProbeAll({}))(), "voice");
    assert.equal(voice.ok, true);
    assert.equal(voice.detail, "local STT only");
  } finally {
    delete globalThis.__channels;
    delete globalThis.__capabilities;
  }
});

test("a failed voice report fails its own row rather than reading as nothing wrong", async () => {
  globalThis.__channels = [];
  globalThis.__capabilities = capabilities();
  try {
    const probeAll = await loadProbeAll({
      multimodal: stub("getCapabilities", 'throw new Error("Gateway unreachable");'),
    });
    const voice = find(await probeAll(), "voice");
    assert.equal(voice.ok, false);
    assert.match(voice.detail, /Gateway unreachable/);
  } finally {
    delete globalThis.__channels;
    delete globalThis.__capabilities;
  }
});

test("the pinned gateway payload shape is what the fixtures model", () => {
  // Guards the fixture itself: if /api/channels ever stops returning a map of
  // {enabled, running} per platform, the assertions above are testing fiction.
  const parsed = JSON.parse(channelFixture);
  assert.equal(Object.keys(parsed.channels).length, 10);
  assert.ok(Object.values(parsed.channels).every((c) => c.enabled === false && c.running === false));
});
