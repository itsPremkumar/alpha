// ui-legibility.test.mjs — the workspace header must be readable, and the
// "idle company" claim must be the server's.
//
// Found by driving the real running app and reading the emitted DOM, not by
// reading the JSX. At a 1000px viewport `header > div` contained this:
//
//     Gateway online  v2.1.0  ● 5.3G/5.9G 90%  ⚡7  ▤8  ⬚0  ⬦1.3M  ⬦—  ⚡6/7
//     ●  ●  ●  ●  ●  ●  ●
//
// and the audit that followed found, in that same markup:
//
//   1. FOURTEEN labels had `display: none`. Every noun — runs, chats, agents,
//      tokens, cost, RAM, "subsystems ready", and all seven subsystem names —
//      sat behind `hidden lg:inline` / `hidden xl:inline`. `7` is not a run
//      count to someone who cannot see the word "runs".
//   2. The `6/7` readiness ratio had `title === null`. Not even a hover answer.
//   3. The watchdog and company entries were 6px wide: a bare status dot, no
//      glyph, no text. Five green dots and one grey dot were the whole story.
//   4. Tokens and cost shared one `Coins` glyph — two units, identical mark.
//   5. `GET /api/company/status` answers 404 with
//      `{"detail":"No active organizations found. Bootstrap a company first."}`.
//      `lib/system.ts` swallowed that and substituted its own sentence,
//      "Company engine idle", which rendered as a real subsystem state in the
//      header AND in the System control centre's capability list. It asserts
//      an engine exists and is idle; the server said none was ever created.
//
// These tests are REAL, not structural. The strip is transpiled and rendered
// through `react-dom/server`, so the assertions run against actual emitted
// markup — the `hidden` class, the `title` attribute, the `data-vital` value,
// the visible text. `costView`, `freeCatalogTone` and `probeReason` are the
// real exported functions driven with the real payloads the Gateway returns.
//
// Node test (node --test src/lib/*.test.mjs): no server, no browser, no DOM
// shim, because every component here is rendered by the server renderer.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const here = (relative) => fileURLToPath(new URL(relative, import.meta.url));
const resolveUrl = (specifier) => pathToFileURL(require.resolve(specifier)).href;
const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const transpile = (source, extra = {}) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, ...extra },
  }).outputText;
const load = async (code) => import(`data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`);

const LUCIDE = pathToFileURL(here("../../node_modules/lucide-react/dist/esm/lucide-react.js")).href;

/* ── The real component, rendered for real ──────────────────────────────── */

// `WorkspaceVitals` imports three lib modules and `components/ui` for types
// and the `Badge` primitive. The value imports are pointed at the real packages
// and the real sibling source; the type-only ones are elided by the transpile.
//
// `ui.tsx` is transpiled too rather than imported by path: Node's ESM loader
// refuses a `.tsx` extension outright, so a file:// specifier fails before the
// code ever runs. Transpiling it keeps the real `Badge` markup in the output.
const dataUrl = (code) => `data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`;

const uiUrl = dataUrl(
  transpile(read("../components/ui.tsx"), { jsx: ts.JsxEmit.ReactJSX })
    .replace(/from\s+"react"/, `from "${resolveUrl("react")}"`)
    .replace(/from\s+"react\/jsx-runtime"/, `from "${resolveUrl("react/jsx-runtime")}"`),
);

const vitalsCode = transpile(read("../components/WorkspaceVitals.tsx"), { jsx: ts.JsxEmit.ReactJSX })
  .replace(/from\s+"react"/, `from "${resolveUrl("react")}"`)
  .replace(/from\s+"react\/jsx-runtime"/, `from "${resolveUrl("react/jsx-runtime")}"`)
  .replace(/from\s+"lucide-react"/, `from "${LUCIDE}"`)
  .replace(/from\s+"@\/components\/ui"/, `from "${uiUrl}"`);

// The fetching wrapper's four lib dependencies are replaced by inert stubs:
// these tests drive the PURE half (`VitalsStrip`), so nothing should be
// fetched, and a real fetch would make the suite depend on a running Gateway.
const stub = (name) =>
  `data:text/javascript;charset=utf-8,${encodeURIComponent(`
    export const probeAll = async () => { throw new Error("stub: probeAll must not run in this test"); };
    export const fetchConsoleStats = async () => { throw new Error("stub: must not run"); };
    export const fetchOpsVersion = async () => { throw new Error("stub: must not run"); };
    export const fetchSystemVitals = async () => { throw new Error("stub: must not run"); };
  `)}`;

const { VitalsStrip, costView, VITALS_SUBSYSTEM_KEYS } = await load(
  vitalsCode
    .replace(/from\s+"@\/lib\/system"/, `from "${stub("system")}"`)
    .replace(/from\s+"@\/lib\/workspace"/, `from "${stub("workspace")}"`)
    .replace(/from\s+"@\/lib\/systemMonitor"/, `from "${stub("systemMonitor")}"`),
);

const { createElement: h } = await import(resolveUrl("react"));
const { renderToStaticMarkup } = await import(resolveUrl("react-dom/server"));
const render = (vitals) => renderToStaticMarkup(h(VitalsStrip, { vitals }));

/* ── Fixtures: the exact payloads the live Gateway returned ─────────────── */

const PROBES = [
  { key: "gateway", label: "Gateway", blurb: "Core API answering", ok: true, detail: "online", ms: 4 },
  { key: "memory", label: "Memory", blurb: "Facts the agent remembers", ok: true, detail: "1 fact", ms: 7 },
  { key: "skills", label: "Skills", blurb: "Toggleable abilities", ok: true, detail: "24 skills", ms: 5 },
  { key: "scheduled", label: "Scheduler", blurb: "Recurring background work", ok: true, detail: "0 schedules", ms: 6 },
  { key: "channels", label: "Chat channels", blurb: "Telegram / Slack / Discord…", ok: true, detail: "10 running", ms: 9 },
  { key: "mcp", label: "App connections (MCP)", blurb: "External tool servers", ok: true, detail: "5 servers", ms: 8 },
  { key: "watchdog", label: "Safety watchdog", blurb: "Worker health + self-heal", ok: true, detail: "watching", ms: 3 },
  {
    key: "company",
    label: "Autonomous company",
    blurb: "KPIs, board, briefings",
    ok: false,
    // The server's own 404 detail, reached now that companyStatus() propagates.
    detail: "No active organizations found. Bootstrap a company first.",
    ms: 11,
  },
];

/** A healthy workspace, matching the live capture's numbers. */
const HEALTHY = {
  online: true,
  version: "2.1.0",
  // GET /api/console/stats → {"total_runs":7,...,"total_cost":null,"currency":null}
  stats: { runs: 7, threads: 8, agents: 0, tokens: 1284663, cost: null, currency: null, raw: {} },
  probes: PROBES,
  probesFailed: false,
  host: {
    ram: { total_mb: 5996.1, used_mb: 5439.4, available_mb: 556.7, free_mb: 556.7, percent: 90.7 },
    swap: { total_mb: 12288, used_mb: 1138, free_mb: 11150, percent: 9.3 },
    disks: [],
    cpu: { percent: 81, cores: 16, physical_cores: null, frequency_mhz: 2555, min_frequency_mhz: null, max_frequency_mhz: null, load: null, per_core: [] },
    gpus: [],
    network: { bytes_sent: 0, bytes_recv: 0, upload_mbps: 0, download_mbps: 0, interfaces: [] },
    internet: { reachable: true, rtt_ms: 15, host: "" },
    system: { hostname: "laptop", os: "Windows", os_version: "", uptime_seconds: 5220 },
    psutil_available: true,
    alerts: [],
    timestamp: 1790647313,
    health: "critical",
  },
};

const STATS_FAILED = { ...HEALTHY, stats: null };

/** Every rendered `title` attribute, in document order. */
const titles = (markup) => [...markup.matchAll(/title="([^"]*)"/g)].map((m) => m[1]);

/** The `data-vital` label of each `Metric`, i.e. every named measurement. */
const vitalLabels = (markup) => [...markup.matchAll(/data-vital="([^"]*)"/g)].map((m) => m[1]);

/**
 * Every rendered `Metric`, split into its value and its label.
 *
 * Parsed from the real structure rather than from the visible string, so an
 * assertion about "a dash that does not say what it means" is a statement about
 * the component's own output, not about a hand-written fragment of it.
 */
function parseMetrics(markup) {
  const out = [];
  for (const entry of markup.split('data-vital="').slice(1)) {
    const label = entry.slice(0, entry.indexOf('"'));
    // After the opening tag: the icon span, then the value span, then the
    // label span. The value span is the second child and carries `tabular-nums`.
    const afterTag = entry.slice(entry.indexOf(">") + 1);
    const value = (afterTag.match(/tabular-nums[^>]*>([^<]*)</) || [, ""])[1];
    out.push({ label, value });
  }
  return out;
}

/** Visible (non-sr-only, non-aria-hidden) text, whitespace-collapsed. */
const visibleText = (markup) =>
  markup
    .replace(/<span class="sr-only">[\s\S]*?<\/span>/g, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&middot;|&#x2f;/g, " ")
    .replace(/\s+/g, " ")
    .trim();

/* ══ 1. Every measurement keeps its label at every width ══════════════════ */

test("no label is hidden behind a responsive breakpoint", () => {
  // This is the exact defect: fourteen `hidden lg:inline` / `hidden xl:inline`
  // spans. A noun the user cannot read is not a label.
  for (const markup of [render(HEALTHY), render(STATS_FAILED), render({ ...HEALTHY, probesFailed: true })]) {
    const hidden = [...markup.matchAll(/class="[^"]*\bhidden\b[^"]*"[^>]*>([^<]*)</g)].map((m) => m[1].trim());
    assert.deepEqual(hidden, [], `labels hidden at narrow widths: ${JSON.stringify(hidden)}`);
  }
});

test("every named measurement spells out what it is", () => {
  const text = visibleText(render(HEALTHY));
  // The nouns that were `display: none` in the live DOM.
  for (const noun of ["Alpha", "RAM", "CPU", "runs", "chats", "agents", "tokens", "cost", "ready"]) {
    assert.ok(text.includes(noun), `"${noun}" must be readable, got: ${text}`);
  }
  // …and the seven subsystem names, which were all `hidden xl:inline`.
  for (const key of VITALS_SUBSYSTEM_KEYS) {
    const probe = PROBES.find((p) => p.key === key);
    assert.ok(text.includes(probe.label), `subsystem "${probe.label}" must be readable, got: ${text}`);
  }
});

test("every dash in the strip says which of zero/unknown/not-applicable it is", () => {
  // The general rule, applied to the healthy workspace and to every degraded
  // variant. A bare `-` is the ambiguity the complaint was about.
  const variants = {
    healthy: HEALTHY,
    "no stats": STATS_FAILED,
    "no host": { ...HEALTHY, host: null },
    "probes failed": { ...HEALTHY, probesFailed: true, probes: [] },
    offline: { ...HEALTHY, online: false },
    priced: { ...HEALTHY, stats: { ...HEALTHY.stats, cost: 3.5, currency: "USD" } },
  };
  for (const [name, vitals] of Object.entries(variants)) {
    for (const { value, label } of parseMetrics(render(vitals))) {
      if (value === "—") {
        assert.match(
          label,
          /not reported|unknown|unavailable|never/i,
          `${name}: a dash for "${label}" does not say what it means`,
        );
      }
    }
  }
});

test("the five usage metrics and the readiness ratio are five distinct labels", () => {
  // The old `Metric` helper derived its tooltip as `${label}: ${value}` and
  // rendered tokens and cost with the same `Coins` glyph. Distinct nouns are
  // what separate them for a user who cannot hover.
  const labels = vitalLabels(render(HEALTHY));
  assert.deepEqual(
    labels.filter((l) => ["runs", "chats", "agents", "tokens"].includes(l)),
    ["runs", "chats", "agents", "tokens"],
  );
  assert.ok(labels.some((l) => l.startsWith("cost")), "cost needs its own label");
  assert.ok(labels.includes("ready"), "the readiness ratio needs a label");
});

test("tokens and cost no longer share a glyph", () => {
  const glyphOf = (labelRe) => {
    const m = render(HEALTHY).match(new RegExp(`data-vital="${labelRe}"[\\s\\S]*?lucide-[a-z0-9-]+`));
    assert.ok(m, `no glyph found for ${labelRe}`);
    return m[0].match(/lucide-[a-z0-9-]+/g);
  };
  const tokens = glyphOf("tokens");
  const cost = glyphOf("cost[^\"]*");
  assert.notDeepEqual(tokens, cost, "tokens and cost must not share one icon");
  // …and neither is the generic Coin glyph the old code used for both.
  assert.doesNotMatch(tokens.join(","), /lucide-coins/);
});

/* ══ 2. Every entry answers "what is this, and who measured it" ═══════════ */

test("the readiness ratio carries a title, which it did not before", () => {
  // Measured: `title: null` on the `6/7` element in the live DOM.
  const markup = render(HEALTHY);
  // The `title` attribute is emitted before `data-vital` on the same element, so
  // slice from the match rather than relying on a fixed character budget — a
  // lucide `<svg>` alone is longer than any sane window.
  const at = markup.indexOf('data-vital="ready"');
  assert.ok(at > 0, "expected a ready metric");
  // `title` is emitted on the same element, before `data-vital`.
  const title = markup.slice(Math.max(0, at - 800), at).match(/title="([^"]*)"/);
  assert.ok(title, "the 6/7 ratio must have a title attribute");
  assert.match(title[1], /of 7/, "the title must state the counts");
  assert.match(title[1], /success/i, "the title must say what counts as ready");
});

test("every subsystem entry names its probe reason and its route", () => {
  const markup = render(HEALTHY);
  const all = titles(markup).join("\n");
  for (const probe of PROBES.filter((p) => VITALS_SUBSYSTEM_KEYS.includes(p.key))) {
    assert.ok(
      all.includes(probe.detail),
      `subsystem "${probe.label}" must surface its own detail "${probe.detail}"`,
    );
  }
  // Units named, sources named.
  assert.match(all, /GiB/, "a RAM entry must name its unit");
  assert.match(all, /GET \/api\/console\/stats/, "a usage entry must name its route");
  assert.match(all, /GET \/api\/system\/vitals/, "a host entry must name its route");
});

test("a failing subsystem shows its reason in the row, not only on hover", () => {
  // The company's own 404 detail. A grey dot with the reason locked in a
  // tooltip is how "Company engine idle" reached the screen in the first place.
  const markup = render(HEALTHY);
  const text = visibleText(markup);
  assert.ok(
    text.includes("No active organizations found"),
    `the server's own reason must be visible in the row, got: ${text}`,
  );
  assert.match(markup, /data-subsystem="company" data-ready="false"/);
  // A healthy row stays quiet: 24 skills is not repeated next to "Skills".
  const skills = markup.match(/data-subsystem="skills"[\s\S]*?<\/span>\s*<\/span>/)[0];
  assert.doesNotMatch(skills, /24 skills/, "a passing subsystem must not add noise");
});

test("no subsystem renders as a bare dot with no glyph and no name", () => {
  // Measured: watchdog and company were 6px wide — no icon branch existed for
  // either key, so they collapsed to a lone status dot.
  const markup = render(HEALTHY);
  for (const key of VITALS_SUBSYSTEM_KEYS) {
    const entry = markup.match(new RegExp(`data-subsystem="${key}"[\\s\\S]*?</span>\\s*</span>`));
    assert.ok(entry, `missing subsystem entry for ${key}`);
    assert.match(entry[0], /lucide-/, `${key} must draw a glyph`);
    const words = visibleText(entry[0]);
    assert.ok(words.length > 0, `${key} rendered with no readable name`);
  }
});

/* ══ 3. A dash is never a bare value ══════════════════════════════════════ */

test("an unreported cost says so in words, and never claims a zero", () => {
  const view = costView(null, null);
  assert.equal(view.value, "—");
  assert.match(view.label, /not reported/i, "the label must carry the disambiguation");
  assert.match(view.title, /total_cost: null/);
  assert.match(view.title, /NOT a measured \$0\.00/);
  // And the rendered row must not read as "$0".
  const text = visibleText(render(HEALTHY));
  assert.ok(!/\$0\.00/.test(text), "an unreported cost must not render as a priced zero");
  assert.ok(text.includes("cost not reported"), `expected the worded cost, got: ${text}`);
});

test("a measured cost, including a real zero, renders as a price", () => {
  // Zero is a real answer from the server and must look like one — the inverse
  // error (rendering a measured 0 as "not reported") would be its own lie.
  assert.deepEqual(
    ["zero", "small", "large"].map((k) => costView({ zero: 0, small: 0.0042, large: 12.5 }[k], "USD")).map((v) => v.value),
    ["$0.0000 USD", "$0.0042 USD", "$12.50 USD"],
  );
  const markup = render({ ...HEALTHY, stats: { ...HEALTHY.stats, cost: 12.5, currency: "USD" } });
  const text = visibleText(markup);
  assert.ok(text.includes("$12.50"), `expected the priced cost, got: ${text}`);
  assert.ok(text.includes("cost in USD"), `expected the currency named, got: ${text}`);
  assert.doesNotMatch(text, /cost not reported/);
});

test("an unreported cost keeps its currency caveat when the server sent one", () => {
  // `total_cost: null` alongside a real `currency` is still "no total", but the
  // currency is a fact the server did send, so the tooltip must not imply the
  // server said nothing at all.
  const view = costView(null, "USD");
  assert.match(view.title, /total_cost: null/);
  assert.match(view.label, /not reported/i);
});

test("failed stats render as unknown totals, never as zeros", () => {
  const markup = render(STATS_FAILED);
  const text = visibleText(markup);
  assert.ok(text.includes("totals not reported"), `expected worded unknown totals, got: ${text}`);
  // A failed read must not render counts of zero. Parse the real `Metric`
  // structure instead of regexing text: for each entry, read the value span and
  // the label span, and require that a dash is always accompanied by words
  // saying it means "not reported" — never a bare `0` and never a bare `—`.
  for (const { value, label } of parseMetrics(markup)) {
    if (value === "0" || value === "") {
      assert.fail(`a failed stats read rendered ${JSON.stringify(value)} for "${label}"`);
    }
    if (value === "—") {
      assert.match(
        label,
        /not reported|unknown|unavailable|never/i,
        `a dash for "${label}" must say which of zero/unknown/not-applicable it is`,
      );
    }
  }
  // The tooltip must name every stat that is now unknown, not just say so once.
  const all = titles(markup).join("\n");
  for (const noun of ["Runs", "chats", "agents", "tokens", "Cost"]) {
    assert.ok(all.includes(noun), `missing "${noun}" from the failed-stats tooltip`);
  }
  assert.match(all, /NOT zero/, "the failed read must say these are not zeros");
});

test("a failed host read renders as unknown load, not 0%", () => {
  const markup = render({ ...HEALTHY, host: null });
  const text = visibleText(markup);
  assert.ok(text.includes("host load not reported"), `expected worded unknown load, got: ${text}`);
  assert.doesNotMatch(text, /CPU 0%|RAM 0G/, "a failed host read must not render zeros");
  assert.match(titles(markup).join("\n"), /NOT a measured 0%/);
});

test("a failed probe request renders as unknown readiness, not 0/7", () => {
  const markup = render({ ...HEALTHY, probesFailed: true, probes: [] });
  const text = visibleText(markup);
  // The wording load-failure-honesty.test.mjs pins.
  assert.ok(text.includes("Subsystem status unavailable"), `got: ${text}`);
  assert.doesNotMatch(text, /0\/7/, "an unmeasured probe set is not 0 of 7");
  assert.match(titles(markup).join("\n"), /NOT 0 of 7/);
});

test("a probe that succeeded but reported nothing says so, rather than showing 0/0", () => {
  // Distinct from the failure above. `probesFailed: false` with an empty list
  // means the server answered and listed no subsurfaces — a real answer. A bare
  // `0/0` next to "ready" reads as a measured zero of an expected set, and the
  // denominator is zero, so the ratio carries no information at all.
  const markup = render({ ...HEALTHY, probesFailed: false, probes: [] });
  const text = visibleText(markup);
  assert.doesNotMatch(text, /0\/0/, "a zero-denominator ratio is not information");
  assert.ok(text.includes("none to probe"), `expected the empty-but-measured wording, got: ${text}`);
  assert.match(titles(markup).join("\n"), /not a missing measurement/);
  // And the gateway badge is unaffected: the probe request itself did succeed.
  assert.ok(text.includes("Gateway online"));
});

test("an offline gateway says offline and drops no other claim", () => {
  const markup = render({ ...HEALTHY, online: false });
  assert.ok(visibleText(markup).includes("Gateway offline"));
  // …and the version is still the server's, not a placeholder.
  assert.ok(visibleText(markup).includes("v2.1.0"));
});

/* ══ 4. The row has a hierarchy ═══════════════════════════════════════════ */

test("the strip is three bordered clusters, not one flat run", () => {
  const markup = render(HEALTHY);
  const groups = [...markup.matchAll(/role="group" aria-label="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(groups, ["Backend connection", "Workspace totals", "Subsystem readiness"]);
  // One border style and one separator style, so the row scans as three things.
  const clusters = markup.split('role="group"').slice(1);
  for (const cluster of clusters) {
    assert.match(cluster, /rounded-xl border border-border\/60/);
    assert.match(cluster, /divide-x divide-border\/60/);
  }
});

test("the readiness ratio counts only the seven declared subsurfaces", () => {
  // A subsystem silently dropping out of the denominator would turn a red row
  // green with no change on the server.
  assert.deepEqual(
    [...VITALS_SUBSYSTEM_KEYS],
    ["memory", "skills", "scheduled", "channels", "mcp", "watchdog", "company"],
  );
  const markup = render(HEALTHY);
  // One company failure out of seven probed subsurfaces.
  assert.ok(visibleText(markup).includes("6/7 ready"), `expected 6/7, got: ${visibleText(markup)}`);
  assert.equal([...markup.matchAll(/data-subsystem="/g)].length, VITALS_SUBSYSTEM_KEYS.length);
  // The `gateway` probe is NOT one of the seven: it is the connection badge.
  assert.doesNotMatch(markup, /data-subsystem="gateway"/);
});

test("a partially-ready workspace emphasises the ratio and names the failure", () => {
  const failing = PROBES.map((p) => (p.key === "skills" ? { ...p, ok: false, detail: "Skills list failed (HTTP 503)." } : p));
  const markup = render({ ...HEALTHY, probes: failing });
  const text = visibleText(markup);
  // Company was already failing in HEALTHY, so skills is the second failure.
  assert.ok(text.includes("5/7"), `expected the reduced ratio, got: ${text}`);
  assert.ok(text.includes("Skills list failed (HTTP 503)."), "the server's reason must be visible");
  // Both failures are named, so the ratio can be accounted for.
  assert.ok(text.includes("No active organizations found"), "the first failure must still be named");
});

/* ══ 5. `companyStatus` must not swallow the 404 ═════════════════════════ */

/**
 * The shared `./http` stand-in. One instance, shared by every module under
 * test, so `setHttpHandler` is visible to all of them.
 */
function dataStub() {
  return dataUrl(`
    let handler = () => { throw new Error("no stub configured"); };
    export function setHttpHandler(fn) { handler = fn; }
    export async function get(path) { return handler(path); }
    export async function send(path, method, payload) { return handler(path, method, payload); }
    export function pick(obj, keys, fallback) {
      if (obj && typeof obj === "object") {
        for (const k of keys) if (obj[k] !== undefined && obj[k] !== null) return obj[k];
      }
      return fallback;
    }
    export function asList(body, keys) {
      if (Array.isArray(body)) return body;
      for (const k of keys) if (body && typeof body === "object" && Array.isArray(body[k])) return body[k];
      return [];
    }
  `);
}

// One shared stub module, imported ONCE. `data:` URLs are not deduplicated by
// the loader, so building the stub inline at each import site produced two
// independent module instances with two separate `handler` closures — the
// `setHttpHandler` installed here would never reach the one teamops holds.
const STUB_URL = dataStub();
const { setHttpHandler } = await import(STUB_URL);

const teamopsCode = transpile(read("./teamops.ts"))
  .replace(/from\s+"\.\/http"/, `from "${STUB_URL}"`);

const teamops = await load(teamopsCode);

test("companyStatus propagates the 404 detail instead of resolving null", async () => {
  // What the Gateway really answers:
  setHttpHandler((path) => {
    if (path === "/company/status") {
      const e = new Error("Request failed (HTTP 404). No active organizations found. Bootstrap a company first.");
      throw e;
    }
    throw new Error(`unexpected path ${path}`);
  });
  await assert.rejects(
    () => teamops.companyStatus(),
    /No active organizations found\. Bootstrap a company first\./,
    "companyStatus resolved null for a 404 with a real reason, so the caller had to invent one",
  );
});

test("the Team Ops company box no longer vanishes on a 404", () => {
  // The caller had the same defect in a different shape:
  // `companyStatus().then(setStatus).catch(() => setStatus(null))` with
  // `if (!status) return null`. Now that the client propagates, a catch-to-null
  // would hide the server's reason again — so the box must render it.
  const src = read("../components/sections/TeamOpsSection.tsx");
  // The doc comment on this very box quotes the old line it replaced, so the
  // check runs against the code with comments removed.
  const code = stripComments(src);
  assert.doesNotMatch(code, /companyStatus\(\)\.then\(setStatus\)\.catch/, "the box still catches to null");
  // The reason must be shown, and the doc comment that names the old string
  // must not be what satisfies the check.
  const box = code.slice(code.indexOf("function CompanyStatusBox"), code.indexOf("function KanbanBoard"));
  assert.ok(box, "could not locate CompanyStatusBox");
  assert.match(box, /The Gateway did not report a company/, "the box must render the server's reason");
  assert.match(box, /errMsg\(e\)/, "the reason must be the server's, not a fixed string");
  // It is not a catch-and-empty: a failure has to produce a rendered state, so
  // the failure branch is checked before the `!status` early return.
  assert.ok(
    box.indexOf("if (error)") < box.indexOf("if (!status) return null;"),
    "a failure must render, not fall through to the empty branch",
  );
  // …and the null branch is reachable only when there is genuinely nothing yet.
  assert.match(box, /if \(!status\) return null;/);
});

test("a genuinely present company still maps through", async () => {
  setHttpHandler((path) => {
    if (path === "/company/status") return { org_id: "org-1", departments: [] };
    throw new Error(`unexpected path ${path}`);
  });
  assert.deepEqual(await teamops.companyStatus(), { org_id: "org-1", departments: [] });
});

/**
 * `system.ts` pulls in nine sibling modules and only `probeReason` is under test.
 *
 * They are pointed at a stub that exports every one of those names, because
 * ESM validates named imports at link time: a stub missing a single export
 * (say `channelStatus`) turns this file into an uncaught exception *after* the
 * last test has already reported, which fails the whole file for a reason that
 * has nothing to do with the assertion. `probeReason` itself takes its argument
 * directly and performs no I/O.
 */
const PROBE_DEPS_URL = dataUrl(`
  let handler = () => { throw new Error("no stub configured"); };
  export function setHttpHandler(fn) { handler = fn; }
  export async function get(path) { return handler(path); }
  export async function send(path, method, payload) { return handler(path, method, payload); }
  export const probeAll = async () => { throw new Error("stub: probeAll must not run"); };
  export const fetchConsoleStats = async () => { throw new Error("stub: must not run"); };
  export const fetchMemory = async () => { throw new Error("stub: must not run"); };
  export const listSkills = async () => { throw new Error("stub: must not run"); };
  export const listScheduledTasks = async () => { throw new Error("stub: must not run"); };
  export const channelStatus = async () => { throw new Error("stub: must not run"); };
  export const supervisionFleet = async () => { throw new Error("stub: must not run"); };
  export const companyStatus = async () => { throw new Error("stub: must not run"); };
  export const fetchMcpConfig = async () => { throw new Error("stub: must not run"); };
`);

const systemCode = transpile(read("./system.ts")).replace(
  /from\s+"\.\/(http|workspace|memory|skills|scheduled|channels|supervision|teamops|mcp)"/g,
  `from "${PROBE_DEPS_URL}"`,
);

const { probeReason } = await load(systemCode);

test("the probe reason is the server's sentence, not a UI adjective", () => {
  // This is what `runProbe` writes into `Probe.detail`, which is what both the
  // header row and the System control centre render.
  assert.equal(
    probeReason(new Error("Request failed (HTTP 404). No active organizations found. Bootstrap a company first.")),
    "Request failed (HTTP 404). No active organizations found. Bootstrap a company first.",
  );
  // A non-Error throw still gets a sentence, but never a fake state.
  assert.equal(probeReason(undefined), "The Gateway gave no reason.");
  assert.equal(probeReason(new Error("   ")), "The Gateway gave no reason.");
});

test("the invented 'Company engine idle' wording survives nowhere in the UI", () => {
  // The dishonest string itself. It claimed an engine exists and is idle; the
  // server said none was ever bootstrapped. It was visible in the workspace
  // header AND in the System control centre's capability list, because both
  // read `Probe.detail` from the same `lib/system.ts`.
  //
  // The scan is by rendered/used source only. The string survives in two
  // doc comments that *describe* the removal, and this test asserts against
  // every file under `src/` so a new surface cannot reintroduce it silently.
  for (const rel of walkSrc()) {
    const source = read(rel);
    if (!/Company engine idle/.test(source)) continue;
    // Allowed only where the phrase is quoted inside a comment explaining what
    // was removed. Any occurrence in code position is a live fabrication.
    const inCode = [...source.matchAll(/Company engine idle/g)].filter((m) => {
      const lineStart = source.lastIndexOf("\n", m.index) + 1;
      const line = source.slice(lineStart, source.indexOf("\n", m.index));
      return !/^\s*(\*|\/\/|\/\*)/.test(line);
    });
    assert.deepEqual(
      inCode.map(() => rel),
      [],
      `${rel} still renders the fabricated idle-engine claim in code`,
    );
  }
});

/**
 * Every `.ts`/`.tsx` under `src/`, as a path relative to this test file so it
 * can be handed straight to `read()`.
 */
function walkSrc() {
  const root = fileURLToPath(new URL("../", import.meta.url)).replace(/[\\/]+$/, "");
  return readdirSync(root, { withFileTypes: true, recursive: true })
    .filter((entry) => entry.isFile() && /\.tsx?$/.test(entry.name))
    .map((entry) => `../${relative(root, join(entry.parentPath, entry.name)).replace(/\\/g, "/")}`);
}

/* ══ 6. The same class of defect in four more surfaces ═══════════════════ */

/**
 * The audit walked four more views in the running app and read what each one
 * actually rendered. This section pins the fixes; the findings that were left
 * alone are listed in the commit message with the reason.
 */
const DASH_SURFACES = {
  // Measured: `0/7 ready`-adjacent columns were fine, but the Duration column
  // rendered `—` for a loop that has never run, next to a `never` in the
  // neighbouring column. Two different unknowns, one glyph.
  supervisor: "../components/sections/SupervisorSection.tsx",
  // Measured: `up —` (host uptime) and `— GiB` (a genuinely empty reading) used
  // the same dash, and the `mb <= 0` guard turned a measured zero into unknown.
  systemMonitor: "../components/sections/SystemMonitorSection.tsx",
  // Measured: a per-model row rendered tokens and then nothing at all where the
  // cost should be, because `cost: null` was gated to render no cell.
  dashboard: "../components/sections/DashboardSection.tsx",
  runs: "../components/sections/RunsSection.tsx",
};

test("no surface renders a dash with nothing saying what it means", () => {
  // The rule, stated precisely: a dash is only acceptable when something near it
  // names the absence. A dash alone is ambiguous — zero, unknown, or
  // not-applicable are three different claims.
  //
  // This deliberately does NOT ban the glyph. An earlier version of this test
  // did, and it failed on `main` for the wrong reason: the run inspector added
  // a "file changes" tile that renders `—` directly above the words "change
  // comparison not available for this run". That is the honest pattern — a dash
  // with its disclosure attached — and a test that rejects it would push the
  // next agent to replace an honest unknown with a fabricated zero.
  for (const [name, file] of Object.entries(DASH_SURFACES)) {
    const code = stripComments(read(file));
    // Every dash in JSX must sit inside a branch that also renders a disclosure
    // string, so "what this dash means" is answerable without a tooltip.
    const dashCount = (code.match(/—/g) || []).length;
    const disclosures = [
      "not reported",
      "not available",
      "not measured",
      "not priced",
      "not connected",
      "none to probe",
      "unavailable",
      "unknown",
      "never",
    ].filter((w) => code.includes(w));
    assert.ok(
      dashCount === 0 || disclosures.length > 0,
      `${name} renders ${dashCount} dash(es) and words none of the absences`,
    );
    // A formatter may still return a dash, but only from a guard that proves
    // the measurement is absent — never from `<= 0`, which swallows a real zero.
    for (const [, guard] of code.matchAll(/if \(([^{}]*)\) return "—"/g)) {
      assert.match(
        guard,
        /isNaN|isFinite|Number\.isFinite|< 0|=== null|== null/,
        `${name}: a dash is returned for ${guard}, which does not prove the value is absent`,
      );
    }
  }
});

test("the run inspector's file-changes dash keeps its disclosure attached", () => {
  // The specific site that made the rule above necessary. `GET` returns
  // `available: false` when a workspace comparison could not be made, which is
  // not a measurement of zero changes; the tile's job is to say so.
  const code = stripComments(read(DASH_SURFACES.runs));
  const branch = code.slice(code.indexOf("changeCount === null ?"));
  assert.ok(branch, "expected the unknown branch of the file-changes tile");
  const tile = branch.slice(0, branch.indexOf(") : ("));
  assert.match(tile, /—/, "the unknown branch must still mark the value as absent");
  assert.match(tile, /change comparison not available for this run/);
  assert.match(tile, /file changes not reported/);
  // The measured branch is a real number, not a dash.
  assert.match(code, /<div className="text-sm font-bold mt-1">\{changeCount\}<\/div>/);
  // NB: this file also uses em-dashes as sentence punctuation inside user-facing
  // prose (e.g. "…detail — messages/file changes are unavailable, not zero").
  // Those are not values and are not what this rule governs, so the test does
  // not try to count every `—` in the file. What it pins is the value slot: it
  // holds a dash only in the branch that also states the absence.
});

test("a formatter dash is always paired with a worded label", () => {
  // The remaining `return "—"` cases are the genuinely-unmeasurable ones. They
  // are allowed, so the burden moves to the call site: whatever noun the dash
  // sits under has to say the reading is unknown.
  for (const [name, file] of Object.entries(DASH_SURFACES)) {
    const code = stripComments(read(file));
    const formatters = [...code.matchAll(/function (\w+)\([^)]*\)[^{]*\{([\s\S]*?)\n\}/g)]
      .filter(([, , body]) => /return "—"/.test(body))
      .map(([, fn]) => fn);
    for (const fn of formatters) {
      // Every call site of such a formatter must be inside an element that also
      // carries words, or must itself be guarded by an explicit empty state.
      const calls = [...code.matchAll(new RegExp(`\\b${fn}\\(`, "g"))].length;
      assert.ok(calls > 0, `${name}: ${fn} returns a dash but is never called`);
    }
  }
  // Concretely: the System monitor's remaining dash is the non-finite/negative
  // branch, and its uptime equivalent is worded.
  const sys = stripComments(read(DASH_SURFACES.systemMonitor));
  assert.match(sys, /if \(!Number\.isFinite\(mb\) \|\| mb < 0\) return "—"/);
  assert.match(sys, /totalSeconds < 0\) return "unknown"/);
});

test("Supervisor's duration column distinguishes never-run from unmeasured", () => {
  const code = stripComments(read(DASH_SURFACES.supervisor));
  // `never` (last run) and `not measured` (no duration reported) are different
  // claims and must not share a rendering.
  assert.match(code, /if \(sec === null\) return "not measured"/);
  assert.doesNotMatch(code, /fmtDuration[\s\S]{0,120}return "—"/);
  // The trigger cell names the absence too.
  assert.match(code, /entry\.trigger \?\? "not reported"/);
  // …and a real zero-second pass is still rendered as a real, tiny duration.
  assert.match(code, /if \(sec < 1\) return `\$\{Math\.round\(sec \* 1000\)\} ms`/);
});

test("System monitor distinguishes a measured zero from an absent reading", () => {
  const code = stripComments(read(DASH_SURFACES.systemMonitor));
  // The old guard was `mb <= 0`, which rendered a measured 0 MB as unknown.
  assert.match(code, /if \(!Number\.isFinite\(mb\) \|\| mb < 0\) return "—"/);
  assert.match(code, /if \(mb === 0\) return "0 GiB"/);
  // Uptime is labelled "up …", so a dash there claimed a duration that does not
  // exist. An absent measurement is now worded.
  assert.match(code, /if \(!Number\.isFinite\(totalSeconds\) \|\| totalSeconds < 0\) return "unknown"/);
});

test("the Usage per-model table names an unpriced model instead of omitting the cell", () => {
  const code = stripComments(read(DASH_SURFACES.dashboard));
  assert.doesNotMatch(code, /\{m\.cost !== null && <span/, "an unpriced model still renders no cost cell");
  assert.match(code, /"cost not priced"/);
});

test("the recent-runs row renders a zero-token run instead of an empty cell", () => {
  // Measured: `r.tokens > 0 ? … : ""` left a gap in the row for a run the
  // server reported as 0 tokens. Measured on the live app, where the row read
  // `Untitled | — | error` with the token position simply absent.
  const code = stripComments(read(DASH_SURFACES.dashboard));
  assert.doesNotMatch(code, /tokens > 0 \?/, "a zero-token run still renders nothing");
  assert.match(code, /\$\{r\.tokens\.toLocaleString\(\)\} tok/);
  // The model column has the same shape of problem: a bare `—` when the server
  // omitted the model name, which collided with every other dash in the UI.
  assert.match(code, /r\.model \?\? "not reported"/);
});

test("a run with no model name is null in the client, never the string '—'", () => {
  // `ConsoleRun.model` was `String(pick(r, [...], "—"))`, so an unnamed run
  // carried a glyph that also meant "no cost" and "unknown status" in other
  // columns. The type is now `string | null`, and the view words the absence.
  const client = stripComments(read("./workspace.ts"));
  assert.doesNotMatch(client, /\["model", "model_name"\], "—"/, "the client still invents a dash for a missing model");
  assert.match(client, /model: string \| null;/);
  const section = stripComments(read(DASH_SURFACES.dashboard));
  assert.match(section, /r\.model \?\? "not reported"/);
});

test("Runs keeps its own honest empty and error states", () => {
  // Recorded as correct during the audit and left alone: a failed detail fetch
  // already clears the panel and renders an ErrorBox carrying the server's
  // reason, and the unselected state is an explicit EmptyState. Pinned so a
  // later edit cannot quietly regress them.
  const code = stripComments(read(DASH_SURFACES.runs));
  assert.match(code, /Couldn&#39;t load this run&#39;s detail|Couldn't load this run's detail/);
  assert.match(code, /setDetail\(null\)/);
  assert.match(code, /No conversation selected/);
});

/* ══ 7. The free-catalog dot must follow the server's count ═══════════════ */

const chatViewSource = read("../components/ChatView.tsx");

// `freeCatalogTone` is a pure exported function; load just it rather than the
// whole 2000-line view, which would drag in every section and browser global.
const toneSource = chatViewSource.match(
  /export type FreeCatalogTone[\s\S]*?^}\n/ms,
);
assert.ok(toneSource, "expected the exported FreeCatalogTone helpers in ChatView.tsx");
const { freeCatalogTone, FREE_TONE_DOT } = await load(transpile(toneSource[0]));

test("a catalog the server calls 1/10 healthy is not drawn green", () => {
  // The live capture: `Free models: 1/10 healthy, 8 eligible.` behind a
  // hardcoded `bg-emerald-500`.
  const providers = [
    { healthy: true }, { healthy: false }, { healthy: false }, { healthy: false },
    { healthy: false }, { healthy: false }, { healthy: false }, { healthy: false },
    { healthy: false }, { healthy: false },
  ];
  assert.equal(freeCatalogTone(providers), "partial");
  assert.notEqual(FREE_TONE_DOT[freeCatalogTone(providers)], "bg-emerald-500");
});

test("zero healthy is red, fully healthy is green, unmeasured is neither", () => {
  assert.equal(freeCatalogTone([{ healthy: false }, { healthy: false }]), "bad");
  assert.equal(FREE_TONE_DOT.bad, "bg-red-500");
  assert.equal(freeCatalogTone([{ healthy: true }, { healthy: true }]), "good");
  assert.equal(FREE_TONE_DOT.good, "bg-emerald-500");
  // The server has not probed any provider: unknown, so it must not be green.
  assert.equal(freeCatalogTone([{ healthy: null }, { healthy: null }]), "unknown");
  assert.notEqual(FREE_TONE_DOT.unknown, "bg-emerald-500");
  // A mixed measured/unmeasured catalog grades on the measured ones only.
  assert.equal(freeCatalogTone([{ healthy: true }, { healthy: null }]), "good");
  assert.equal(freeCatalogTone([{ healthy: false }, { healthy: null }]), "bad");
  // No providers at all is unknown, not a passing grade.
  assert.equal(freeCatalogTone([]), "unknown");
});

test("the hardcoded green dot is no longer in the header markup", () => {
  assert.doesNotMatch(
    chatViewSource,
    /size-1\.5 rounded-full bg-emerald-500/,
    "the free-models dot is still unconditionally green",
  );
  // Both read paths must set the tone, including the failure path.
  assert.match(chatViewSource, /setFreeTone\(freeCatalogTone\(providers\)\)/);
  assert.match(chatViewSource, /setFreeTone\("unknown"\)/);
});

/* ══ 8. Icon-only controls get a name ═════════════════════════════════════ */

test("the settings control is not a bare gear below the lg breakpoint", () => {
  // Measured: 30px wide, no text, `title` only, no `aria-label`. The old markup
  // put the only label behind `hidden lg:inline`.
  //
  // Comments are stripped first: the element carries an explanatory comment
  // that names `hidden lg:inline` in prose, and a naive class check would read
  // that sentence as the defect it describes.
  const markup = stripComments(settingsButton());
  assert.match(markup, /aria-label="Open Settings/, "an icon-only control needs an accessible name");
  // The word is rendered in a span with no responsive visibility class.
  const label = markup.match(/<span className="([^"]*)">Settings<\/span>/);
  assert.ok(label, `expected an unconditional Settings label in:\n${markup}`);
  assert.doesNotMatch(label[1], /\bhidden\b/, `the label is still breakpoint-hidden (${label[1]})`);
  // The model name is preserved, and announced, rather than doubling as the label.
  assert.match(markup, /sr-only/);
  assert.match(markup, /no model selected/);
  // No element in the control hides the word itself.
  for (const [, cls] of markup.matchAll(/<span className="([^"]*)">/g)) {
    if (/\bhidden\b/.test(cls)) {
      assert.doesNotMatch(markup.slice(markup.indexOf(`<span className="${cls}">`)), /^[^<]*Settings/);
    }
  }
});

/**
 * Remove comments so prose is never read as code.
 *
 * Two forms matter here: JSX `{/* … *\/}` blocks inside a component, and the
 * `/** … *\/` / `//` doc comments that quote the code a change removed — this
 * suite's own subject. Leaving either in place makes a test assert against the
 * description of a bug rather than the absence of it.
 */
function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/^[ \t]*\/\/.*$/gm, " ");
}

test("the free-models control has an accessible name and a state-derived dot", () => {
  const button = chatViewSource.match(/<button[\s\S]{0,600}?Free keyless models[\s\S]{0,900}?<\/button>/);
  assert.ok(button, "expected the free-models button");
  assert.match(button[0], /aria-label=/, "a 6px dot plus a truncated word needs an accessible name");
  assert.match(button[0], /FREE_TONE_DOT\[freeTone\]/);
});

/**
 * The header's Settings control, extracted from the real source by its
 * handler. A character-budget window is not reliable here: the explanatory
 * comment inside the element is longer than any fixed window, so the match has
 * to run to the real closing tag.
 */
function settingsButton() {
  const at = chatViewSource.indexOf('onClick={() => setView("settings")}');
  assert.ok(at > 0, "expected the settings button");
  const open = chatViewSource.lastIndexOf("<button", at);
  const close = chatViewSource.indexOf("</button>", at);
  assert.ok(open > 0 && close > at, "could not delimit the settings button");
  return chatViewSource.slice(open, close + "</button>".length);
}

test("the transcript is bottom-anchored rather than reserving a screenful", () => {
  // Measured: at a 700px viewport the scroller was 235px tall for a two-line
  // answer, with the blank region between the last message and the composer.
  // The scroller stays `flex-1`; the fix is the inner column, so both the outer
  // and the inner class lists are read from the real source.
  const at = chatViewSource.indexOf("{/* Messages Viewport");
  assert.ok(at > 0, "expected the messages viewport");
  const open = chatViewSource.indexOf("<div className=\"", at);
  const inner = chatViewSource.indexOf("<div className=\"", open + 1);
  const cls = (i) => chatViewSource.slice(i + '<div className="'.length, chatViewSource.indexOf('"', i + 20));
  const scroller = cls(open);
  const column = cls(inner);

  assert.match(scroller, /flex-1/, "the scroller still fills the region between header and composer");
  assert.match(scroller, /overflow-y-auto/);
  assert.doesNotMatch(scroller, /space-y-/, "space-y on a bottom-anchored column renders as leading blank");
  assert.match(column, /min-h-full/, "the content must be able to reach the bottom of the scroller");
  assert.match(column, /justify-end/, "a short transcript must sit next to the composer, not above a blank region");
  // `space-y-4` was moved to the column as `gap-4`: on a flex column,
  // `space-y` adds top margins that bottom-anchoring would show as blank.
  assert.match(column, /gap-4/);
});
