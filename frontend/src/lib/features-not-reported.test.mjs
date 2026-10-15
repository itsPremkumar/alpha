// features-not-reported.test.mjs — "the Gateway did not answer" is not "off".
//
// Three surfaces used to render a configuration diagnosis the server had never
// made, because an unanswered question was collapsed into `false`:
//
//   1. `ChatView` seeded `features` with `agentsApi: false`, so the first
//      render of `AgentsSection` said "Agent management is switched off — set
//      agents_api.enabled=true in config.yaml" for as long as the bootstrap
//      `fetchFeatures()` call took (and forever, if it failed: `fetchFeatures`
//      maps an unreachable Gateway to `null`, which the seed immediately hid).
//   2. `lib/system.ts` built its probe facts with `Boolean(d.agents_api?.enabled)`,
//      so a missing block or a rejected `GET /api/features` became `false`, and
//      the probe then threw the exact sentence a reader would act on:
//      "Switched off (agents_api.enabled=false)".
//   3. The two badge surfaces rendered `ok: null` as an in-flight state
//      ("Checking") or as a red verdict ("not ready") — both claims about a
//      measurement that never happened.
//
// Ground truth for the field: `backend/app/gateway/routers/features.py:100-116`
// always answers `agents_api.enabled`, so the honest value for a *successful*
// read is boolean; only a failed or differently-shaped read can be "not
// reported". `frontend/AGENTS.md`: "No fabricated state … If a field is
// missing, render it as unknown/disabled — never as zero, empty, or a green
// badge."
//
// Pure Node test: source pins plus the real `system.ts` transpiled against a
// stubbed HTTP layer. No server, no browser, no DOM.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;
const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");

/* ══ 1. The probe: three states, three answers ═══════════════════════════ */

function stub(name, body, params = "") {
  return `export function ${name}(${params}) { ${body} }`;
}

/** `/features` answers with `body` (or rejects when `body` is `null`). */
function featuresHttp(body) {
  const features =
    body === null
      ? `return Promise.reject(new Error("Server not reachable"));`
      : `return Promise.resolve(${JSON.stringify(body)});`;
  return stub(
    "get",
    `if (String(p) === "/features") { ${features} }
     if (String(p || "").includes("supervision/fleet")) return Promise.resolve(globalThis.__fleetBody ?? { watching: false, observed: false });
     return Promise.resolve({});`,
    "p",
  );
}

async function loadProbeAll(httpStub) {
  const source = readFileSync(new URL("./system.ts", import.meta.url), "utf8");
  const stubs = {
    http: httpStub ?? featuresHttp({}),
    workspace: stub("fetchConsoleStats", "return Promise.resolve({ runs: 0, threads: 0 });"),
    memory: stub("fetchMemory", "return Promise.resolve({ facts: [] });"),
    skills: stub("listSkills", "return Promise.resolve([]);"),
    scheduled: stub("listScheduledTasks", "return Promise.resolve([]);"),
    channels: stub("channelStatus", "return Promise.resolve([]);"),
    supervision: stub(
      "watchdogDetail",
      "return 'no workers reporting';",
    ) + stub("parseFleetWorkers", "return [];") + stub("observedReason", "return '';") + stub("isWatching", "return false;"),
    company: stub("listCompanies", "return Promise.resolve([]);"),
    mcp: stub("fetchMcpConfig", "return Promise.resolve([]);"),
    multimodal: stub("getCapabilities", "return Promise.resolve({ rows: [], voice: { enabled: false } });"),
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

test("an unanswered GET /api/features is 'not reported', never a config diagnosis", async () => {
  const probeAll = await loadProbeAll(featuresHttp(null));
  const probes = await probeAll();

  const gateway = find(probes, "gateway");
  assert.equal(gateway.ok, false, "the Gateway row still fails honestly");
  assert.match(gateway.detail, /not reachable/i);

  for (const key of ["agentsApi", "browser"]) {
    const probe = find(probes, key);
    assert.equal(probe.ok, null, `${key}: an unanswered question has no measured verdict`);
    assert.match(probe.detail, /^not reported/, `${key}: the state leads the sentence`);
    assert.doesNotMatch(
      probe.detail,
      /Switched off|enabled=false|No browser capability/,
      `${key}: a failed read must not produce the sentence an operator would act on`,
    );
  }
});

test("a /features payload without the agents_api block is not 'switched off'", async () => {
  const probeAll = await loadProbeAll(featuresHttp({ browser_control: { enabled: true } }));
  const agents = find(await probeAll(), "agentsApi");
  assert.equal(agents.ok, null);
  assert.match(agents.detail, /not reported/);
  assert.doesNotMatch(agents.detail, /Switched off/);
});

test("agents_api.enabled=false is reported as off, with the server's own words", async () => {
  const probeAll = await loadProbeAll(
    featuresHttp({ agents_api: { enabled: false }, browser_control: { enabled: false } }),
  );
  const probes = await probeAll();

  const agents = find(probes, "agentsApi");
  assert.equal(agents.ok, false, "a reported-off capability is a measured verdict");
  assert.match(agents.detail, /Switched off \(agents_api\.enabled=false\)/);

  // The browser row names the field the server actually sent rather than the
  // old "No browser capability on server", which read as a build property.
  const browser = find(probes, "browser");
  assert.equal(browser.ok, false);
  assert.match(browser.detail, /browser_control\.enabled=false/);
});

test("agents_api.enabled=true is a passing row", async () => {
  const probeAll = await loadProbeAll(
    featuresHttp({ agents_api: { enabled: true }, browser_control: { enabled: true } }),
  );
  const probes = await probeAll();
  assert.equal(find(probes, "agentsApi").ok, true);
  assert.equal(find(probes, "agentsApi").detail, "enabled");
  assert.equal(find(probes, "browser").ok, true);
});

test("the probe fact is no longer coerced with Boolean(...)", () => {
  const src = read("./system.ts");
  assert.doesNotMatch(
    src,
    /Boolean\(\(d\.agents_api/,
    "Boolean() collapses 'not reported' into 'off', which is the defect itself",
  );
  assert.match(src, /type Flag = boolean \| null/);
  assert.match(src, /class Unreported extends Error/);
});

/* ══ 2. ChatView seeds unknown, not off ══════════════════════════════════ */

test("ChatView's initial feature state is 'not reported' for every flag", () => {
  const src = read("../components/ChatView.tsx");
  const seed = src.slice(
    src.indexOf("useState<FeatureFlags>({"),
    src.indexOf("useState<FeatureFlags>({") + 260,
  );
  for (const key of ["agentsApi", "browserControl", "mcpTasks", "subagentBatches"]) {
    assert.match(
      seed,
      new RegExp(`${key}: null`),
      `ChatView must not seed ${key} as a server fact before /features answers`,
    );
    assert.doesNotMatch(
      seed,
      new RegExp(`${key}: false`),
      `${key}: false is a claim about a read that has not happened yet`,
    );
  }
});

/* ══ 3. AgentsSection renders the third state ════════════════════════════ */

test("AgentsSection distinguishes 'not reported' from 'switched off'", () => {
  const src = read("../components/sections/AgentsSection.tsx");
  // Tri-state prop: `boolean` alone cannot carry "did not answer".
  assert.match(src, /export function AgentsSection\(props: \{ enabled: boolean \| null \}\)/);
  assert.match(src, /props\.enabled === null/, "the branch must read the third state explicitly");
  assert.match(src, /Agent management status was not reported/);
  assert.match(src, /AGENTS_API_DISABLED_VIEW\.title/);
  // The config instruction is the consequence of `false` ONLY: it sits in the
  // non-null arm of the ternary, so an unanswered Gateway cannot receive it.
  assert.match(
    src,
    /notReported\s*\?[\s\S]{0,400}?AGENTS_API_DISABLED_VIEW\.hint/,
    "the instruction must be reachable only when the server said `false`",
  );
  const hint = src.slice(src.indexOf("hint={"), src.indexOf("hint={") + 900);
  assert.match(hint, /notReported\s*\?/, "the hint distinguishes the third state");
});

test("AgentsSection never fetches the disabled API", () => {
  // Calling listAgents() while the Gateway reports the route 403s
  // (`routers/agents.py:114-120`) would paint a refusal as a load failure.
  const src = read("../components/sections/AgentsSection.tsx");
  assert.match(src, /if \(props\.enabled\) load\(\);/);
});

/* ══ 4. The badge surfaces render `null` truthfully ══════════════════════ */

test("Settings renders an unreported probe as neither Healthy nor Checking", () => {
  const src = read("../components/sections/SettingsSection.tsx");
  assert.match(src, /p\.ok === null \? "Not reported"/);
  assert.doesNotMatch(src, /p\.ok === null \? "Checking"/, "the probe has already run");
});

test("Subsystem readiness renders 'not reported', not 'not ready'", () => {
  const src = read("../components/sections/VitalsSection.tsx");
  assert.match(src, /p\.ok === null \? "not reported"/);
  assert.match(src, /p\.ok === null \? "bg-muted-foreground"/);
  assert.doesNotMatch(
    src,
    /Badge tone=\{p\.ok \? "green" : "red"\}\>\{p\.ok \? "ready" : "not ready"\}/,
    "a two-way badge would draw an unmeasured subsystem as a red verdict",
  );
});
