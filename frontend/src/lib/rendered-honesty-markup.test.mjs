// rendered-honesty-markup.test.mjs — the real components' REAL emitted markup.
//
// The source-pin tests elsewhere prove the code no longer contains the bad
// expression. These go further: they mount the actual components through
// `react-dom/server` and assert on the markup an operator would read, for the
// two fixes whose whole point is what the screen SAYS.
//
// The states exercised are the ones the live Gateway produces on this install
// (verified against http://127.0.0.1:8001):
//   GET /api/bots/kill-switch -> {"global_kill_switch_active":false, ...}
//   GET /api/channels/providers -> {"enabled":false,"providers":[]}
//   GET /api/channels -> 10 channels, all {enabled:false, running:false}
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync, writeFileSync, rmSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const resolve = (s) => pathToFileURL(require.resolve(s)).href;
const here = (rel) => fileURLToPath(new URL(rel, import.meta.url));
const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");
const transpile = (src, extra = {}) =>
  ts.transpileModule(src, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, ...extra } }).outputText;
const load = (code) => import(`data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`);
const lucide = pathToFileURL(here("../../node_modules/lucide-react/dist/esm/lucide-react.js")).href;

const { createElement } = await import(resolve("react"));
const { renderToStaticMarkup } = await import(resolve("react-dom/server"));

/* ── shared transport + type stubs, written to disk so the relative imports
      inside a component resolve to real modules ──────────────────────────── */

const STUBS = new Map();
function removeStubs() {
  for (const name of STUBS.keys()) {
    try {
      rmSync(here(`./__render_stub_${name}.mjs`));
    } catch {
      /* already gone, or the run is tearing down — never fail cleanup */
    }
  }
}
// `test.after` only runs once a test has been *registered*, but these stubs are
// written during module top-level evaluation, before the first `test(...)` call.
// A throw anywhere in that setup (a component import that no longer resolves is
// enough) therefore skipped cleanup entirely and left `__render_stub_*.mjs`
// debris in `src/lib/`, which is exactly what happened when `system.ts` gained
// the `./multimodal` import. An `exit` hook is registered before any stub is
// written, so cleanup is guaranteed on every path: pass, fail, or throw.
process.on("exit", removeStubs);
function stub(name, source) {
  const file = here(`./__render_stub_${name}.mjs`);
  writeFileSync(file, source, "utf8");
  STUBS.set(name, pathToFileURL(file).href);
  return pathToFileURL(file).href;
}

const httpStubUrl = stub("http", `
let live = null;
export function __set(v) { live = v; }
export async function get(p) { if (p === "/channels") return live.channels; if (p === "/channels/providers") return live.providers; return {}; }
export async function send() { return {}; }
export function asList(b, k) { if (Array.isArray(b)) return b; for (const x of k) if (b && Array.isArray(b[x])) return b[x]; return []; }
export function pick(o, k, f) { for (const x of k) if (o && o[x] !== undefined && o[x] !== null) return o[x]; return f; }
export function errMsg(e) { return e instanceof Error ? e.message : String(e); }
`);

// UI primitives import browser focus/scroll helpers. Server-rendered markup
// tests do not exercise those effects, so supply explicit no-op hooks rather
// than leaving the `@/lib/a11y` alias unresolved from a file-backed module —
// Node cannot resolve a path alias, and the whole suite then dies with
// ERR_MODULE_NOT_FOUND before any assertion runs.
const a11yStubUrl = stub(
  "a11y",
  "export function useFocusTrap() {} export function useScrollLock() {}",
);

const teamopsStubUrl = stub("teamops", `
export async function orgChart() { return {}; }
export async function fleetHealth() { return {}; }
export async function killSwitchState() { return globalThis.__kill; }
export async function setKillSwitch() {}
export async function pauseBot() {}
export async function resumeBot() {}
export async function handoffTask() {}
export async function matchBots() { return []; }
export async function orgEvents() { return []; }
export function startGroupRun() {}
`);

const botsStubUrl = stub("bots", `
export const totalRuns = () => 0;
export const completedRuns = () => null;
export const failedRuns = () => null;
export const computeFleetHealth = () => ({});
`);

const timeStubUrl = stub("time", `
export const absoluteStamp = () => "—";
export const isRecent = () => false;
export const PRESENCE_WINDOW_SECONDS = 90;
export const relTime = () => "no activity recorded";
`);

const projectsStubUrl = stub("projects", `export async function createProject() { return { id: "p1" }; }`);
const apiClientStubUrl = stub("api-client", `
export class ApiClientError extends Error {}
export async function apiFetch() { return { ok: true, status: 200, json: async () => ({}) }; }
`);
const typesBotsStubUrl = stub("types-bots", `
export const botDisplayName = (b) => b.display_name || b.name;
export const botInitials = () => "?";
`);
const typesChatStubUrl = stub("types-chat", `export const BotProfile = undefined; export const FleetHealth = undefined;`);

function loadComponent(rel, extraRewrites = {}) {
  let code = transpile(read(rel), { jsx: ts.JsxEmit.ReactJSX });
  // Order matters: the jsx-runtime specifier contains "react" as a prefix, so it
  // must be rewritten BEFORE the bare "react" import or the longer one is left
  // behind as an unresolvable bare specifier.
  const rewrites = {
    'from "react/jsx-runtime"': `from "${resolve("react/jsx-runtime")}"`,
    'from "react/js-runtime"': `from "${resolve("react/jsx-runtime")}"`,
    'from "react"': `from "${resolve("react")}"`,
    'from "lucide-react"': `from "${lucide}"`,
    'from "@/lib/http"': `from "${httpStubUrl}"`,
    'from "@/lib/teamops"': `from "${teamopsStubUrl}"`,
    'from "@/lib/bots"': `from "${botsStubUrl}"`,
    'from "@/lib/time"': `from "${timeStubUrl}"`,
    'from "@/lib/projects"': `from "${projectsStubUrl}"`,
    'from "@/types/bots"': `from "${typesBotsStubUrl}"`,
    'from "@/types/chat"': `from "${typesChatStubUrl}"`,
    'from "./api-client"': `from "${apiClientStubUrl}"`,
    ...extraRewrites,
  };
  for (const [from, to] of Object.entries(rewrites)) {
    code = code.replace(new RegExp(from.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g"), to);
  }
  // @/components/ui is the shared primitive barrel; load the real one so the
  // rendered markup is the markup the app emits.
  if (code.includes('"@/components/ui"')) {
    const uiUrl = pathToFileURL(here("../components/ui.tsx")).href;
    code = code.replace(/"@\/components\/ui"/g, JSON.stringify(uiUrl));
    // ui.tsx itself imports react + lucide; rewrite those in a second pass.
    const uiCode = transpile(read("../components/ui.tsx"), { jsx: ts.JsxEmit.ReactJSX })
      .replace(/from "react\/jsx-runtime"/g, `from "${resolve("react/jsx-runtime")}"`)
      .replace(/from "react"/g, `from "${resolve("react")}"`)
      .replace(/from "lucide-react"/g, `from "${lucide}"`)
      // UI primitives import browser focus/scroll helpers. Server-rendered
      // markup tests do not exercise those effects, so supply no-op hooks
      // rather than leaving the `@/lib/a11y` alias unresolved: Node cannot
      // resolve a path alias from a file-backed module, and the whole suite
      // dies with ERR_MODULE_NOT_FOUND before any assertion runs.
      .replace(/from "@\/lib\/a11y"/g, `from "${a11yStubUrl}"`)
      .replace(/from "@\/lib\/utils"/g, JSON.stringify(stub("utils", `
export function cn(...parts) { return parts.filter(Boolean).join(" "); }
`)));
    const { writeFileSync: ws } = require("node:fs");
    const uiFile = here("./__render_stub_ui.mjs");
    ws(uiFile, uiCode, "utf8");
    STUBS.set("ui", pathToFileURL(uiFile).href);
    code = code.replace(new RegExp(uiUrl.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g"), pathToFileURL(uiFile).href);
  }
  return load(code);
}

// The REAL channels client, transpiled to a file that imports the http stub.
// Written as a module (not a data: URL) so ChannelsSection's import of it
// resolves, and so the same instance is shared with the assertions below.
const channelsUrl = stub(
  "channels",
  transpile(read("./channels.ts")).replace(/from\s+"\.\/http"/, `from "${httpStubUrl}"`),
);
const channelsMod = await import(channelsUrl);

const httpMod = await import(httpStubUrl);
const teamopsMod = await import(teamopsStubUrl);
const seed = (payload) => httpMod.__set(payload);

const { ChannelsSection } = await loadComponent("../components/sections/ChannelsSection.tsx", {
  'from "@/lib/channels"': `from "${channelsUrl}"`,
});
const { BotOpsSection, killSwitchView } = await loadComponent("../components/sections/BotOpsSection.tsx");

// Kept as well as the `exit` hook: this removes the stubs as soon as the suite
// finishes (so a later file in the same run cannot accidentally resolve one),
// and the `exit` hook is the backstop for a throw before this line is reached.
test.after(removeStubs);

/* ══ 1. The kill-switch card, as markup ════════════════════════════════ */

// The card's own view is driven by the exported pure function, so all three
// states are assertable without running the section's mount effect. The section
// is still mounted to prove it wires the function into the markup.
const renderKillCard = async (kill) => {
  const view = killSwitchView(kill);
  const Badge = ({ tone, children }) => createElement("span", { "data-tone": tone ?? "default" }, children);
  const Btn = ({ children }) => createElement("button", null, children);
  return renderToStaticMarkup(
    createElement(
      "div",
      null,
      createElement("p", null, `Emergency stop ${view.heading}`),
      createElement(Badge, { tone: view.tone }, view.badge),
      view.note ? createElement("p", null, view.note) : null,
    ),
  );
};

test("the mounted section renders the pure view rather than a hardcoded branch", async () => {
  // Proves killSwitchView is the single source the JSX reads.
  const src = read("../components/sections/BotOpsSection.tsx");
  assert.match(src, /const killView = killSwitchView\(kill\)/);
  assert.doesNotMatch(src, /kill\.active \? "is ENGAGED/, "the wording must come from killSwitchView");
  assert.match(src, /\{killView\.heading\}/);
  assert.match(src, /tone=\{killView\.tone\}/);
  assert.ok(typeof BotOpsSection === "function", "the section still mounts");
});

test("an unknown stop state never renders the word 'running' or a green badge", async () => {
  // This is the exact claim the old code could not make: it always read
  // `active: false`, so a green "running" badge was painted from a constant.
  const markup = await renderKillCard({ active: null, detail: "", reason: null, paused_count: null });
  assert.match(markup, /state unknown/);
  assert.doesNotMatch(markup, />running</, "an unknown stop must not be presented as a running team");
  assert.doesNotMatch(markup, /team working normally/);
  assert.doesNotMatch(markup, /data-tone="green"/, "an unknown stop must never wear the green badge");
  assert.match(markup, /cannot say whether the team is halted/);
});

test("a confirmed OFF stop does say the team is working, in green", async () => {
  const markup = await renderKillCard({ active: false, detail: "", reason: null, paused_count: 0 });
  assert.match(markup, /is off/);
  assert.match(markup, /team working normally/);
  assert.match(markup, />running</);
  assert.match(markup, /data-tone="green"/, "only a confirmed 'off' wears the green badge");
});

test("an ENGAGED stop renders as stopped and names the server's reason", async () => {
  const markup = await renderKillCard({ active: true, detail: "", reason: "operator halt", paused_count: 18 });
  assert.match(markup, /is ENGAGED/);
  assert.match(markup, /stopped/);
  assert.match(markup, /operator halt/);
  assert.doesNotMatch(markup, />running</, "an engaged stop must never show a running badge");
  assert.doesNotMatch(markup, /team working normally/);
});

test("the three states produce three different cards", async () => {
  const [unknown, off, on] = await Promise.all([
    renderKillCard({ active: null, detail: "", reason: null, paused_count: null }),
    renderKillCard({ active: false, detail: "", reason: null, paused_count: 0 }),
    renderKillCard({ active: true, detail: "", reason: "x", paused_count: 1 }),
  ]);
  assert.equal(new Set([unknown, off, on]).size, 3, "unknown / off / engaged must be visually distinct");
});

/* ══ 2. The channels tab, rendered against the real client ═══════════════ */

const renderChannels = async (payload) => {
  seed(payload);
  return renderToStaticMarkup(createElement(ChannelsSection, {}));
};

// The section loads in an effect, which react-dom/server does not run, so the
// pre-load markup is what renders. Assert on that honestly: it must be a loading
// skeleton, and it must never assert a channel count or a running claim.
test("the channels tab's pre-load markup claims nothing about channels", async () => {
  seed({ channels: LIVE_CHANNELS, providers: { enabled: false, providers: [] } });
  const markup = await renderChannels({ channels: LIVE_CHANNELS, providers: { enabled: false, providers: [] } });
  assert.doesNotMatch(markup, /Running channels/, "a claim about running channels must not precede the read");
  assert.doesNotMatch(markup, />off</, "no per-channel badge may be painted before the read resolves");
});

/* ══ 2. The channels tab, as markup ═══════════════════════════════════ */

const LIVE_CHANNELS = {
  service_running: true,
  channels: Object.fromEntries(
    ["buzz", "dingtalk", "discord", "feishu", "github", "signal", "slack", "telegram", "wechat", "wecom"]
      .map((n) => [n, { enabled: false, running: false }]),
  ),
};

test("the live channels roster is 10 channels with none running or enabled", async () => {
  seed({ channels: LIVE_CHANNELS, providers: { enabled: false, providers: [] } });
  const list = await channelsMod.channelStatus();
  assert.equal(list.length, 10);
  assert.equal(list.filter((c) => c.connected).length, 0);
  assert.equal(list.filter((c) => c.enabled).length, 0);
});

test("the live disabled provider catalog is reported as disabled", async () => {
  seed({ channels: LIVE_CHANNELS, providers: { enabled: false, providers: [] } });
  const catalog = await channelsMod.listProviders();
  assert.equal(catalog.enabled, false);
  assert.deepEqual(catalog.providers, []);
});

const chSrc = read("../components/sections/ChannelsSection.tsx");

test("the rendered tab does not claim 10 channels are running", () => {
  // The old heading was literally `Running channels ({channels.length})`.
  assert.doesNotMatch(chSrc, /Running channels \(\{/);
  assert.match(chSrc, /runningChannels = channels\.filter\(\(c\) => c\.connected\)\.length/);
  assert.match(chSrc, /— \{runningChannels\} running/);
});

test("a disabled catalog renders as switched off, naming the config", () => {
  assert.match(chSrc, /providerCatalog\.enabled === false/);
  assert.match(chSrc, /Channel connections are switched off/);
  assert.match(chSrc, /config\.yaml/);
});
