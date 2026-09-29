// ui-legibility-shots.mjs — render the workspace header to a standalone HTML
// file, using the app's own compiled Tailwind CSS, so a change can be SEEN
// rather than argued about.
//
//   node src/lib/ui-legibility-shots.mjs <outDir>
//
// It is not a test and starts no server. It server-renders `VitalsStrip` — the
// real component — at the viewport width where the labels used to be hidden,
// writes the markup plus the app's own stylesheet to disk, and prints the path.
// Open that file and you are looking at the shipped look, because the CSS is
// produced by this repo's real `tailwind.config.cjs` over the real source tree.
//
// It exists because the desktop capture tool needs a visible window, which a
// headless session does not have; the DOM evidence in `ui-legibility.test.mjs`
// is the authoritative record and this is its visual companion.
import { mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

const require = createRequire(import.meta.url);
const here = (rel) => fileURLToPath(new URL(rel, import.meta.url));
// This file lives in `src/lib/`; the frontend root is two levels up.
const frontend = resolve(here("."), "..", "..");
const outDir = resolve(process.argv[2] || join(frontend, ".ui-legibility"));
mkdirSync(outDir, { recursive: true });

const resolveUrl = (s) => pathToFileURL(require.resolve(s)).href;
/** Read a file relative to the frontend root. */
const read = (rel) => readFileSync(join(frontend, rel), "utf8");
const dataUrl = (c) => `data:text/javascript;charset=utf-8,${encodeURIComponent(c)}`;
const tr = (s) =>
  ts.transpileModule(s, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;

/* ── the app's own CSS, compiled from its own Tailwind config ───────────── */

// The temp CSS input goes in the output dir: Tailwind needs a real path to
// resolve `@tailwind` against, and this keeps the source tree clean.
const cssIn = join(outDir, "shots.in.css");
writeFileSync(
  cssIn,
  `@tailwind base;\n@tailwind components;\n@tailwind utilities;\n` + read("src/app/globals.css"),
);
// pnpm keeps the real package under `.pnpm/<name>@<version>/node_modules/<name>`
// and `node_modules/<name>` is a symlink into it. Resolving through the store
// path directly means this harness works whether or not the workspace links
// were materialised (a fresh worktree or a copied `node_modules`).
const pnpmEntry = (name, version, sub) =>
  join(frontend, "node_modules", ".pnpm", `${name}@${version}`, "node_modules", ...name.split("/"), ...sub);
const postcss = require(pnpmEntry("postcss", "8.5.28", ["lib", "postcss.js"]));
const tw = require(pnpmEntry("tailwindcss", "3.4.19", ["lib", "index.js"]));
const twConfig = require(join(frontend, "tailwind.config.cjs"));
const result = postcss([tw(twConfig)]).process(readFileSync(cssIn, "utf8"), { from: cssIn, to: join(outDir, "app.css") });
const css = await new Promise((res, rej) => result.then((r) => res(r.css), rej));
writeFileSync(join(outDir, "app.css"), css);
unlinkSync(cssIn);

/* ── the real component ─────────────────────────────────────────────────── */

const uiUrl = dataUrl(
  tr(read("src/components/ui.tsx"))
    .replace(/from\s+"react"/, `from "${resolveUrl("react")}"`)
    .replace(/from\s+"react\/jsx-runtime"/, `from "${resolveUrl("react/jsx-runtime")}"`),
);
const inert = dataUrl(
  ["probeAll", "fetchConsoleStats", "fetchOpsVersion", "fetchSystemVitals"]
    .map((n) => `export const ${n} = async () => { throw new Error("stub"); };`)
    .join("\n"),
);
const { VitalsStrip } = await import(
  dataUrl(
    tr(read("src/components/WorkspaceVitals.tsx"))
      .replace(/from\s+"react"/, `from "${resolveUrl("react")}"`)
      .replace(/from\s+"react\/jsx-runtime"/, `from "${resolveUrl("react/jsx-runtime")}"`)
      .replace(/from\s+"lucide-react"/, `from "${pathToFileURL(join(frontend, "node_modules", "lucide-react", "dist", "esm", "lucide-react.js")).href}"`)
      // The three fetching deps are inert: this harness supplies the state.
      .replace(/from\s+"@\/components\/ui"/, `from "${uiUrl}"`)
      .replace(/from\s+"@\/lib\/system"/, `from "${inert}"`)
      .replace(/from\s+"@\/lib\/workspace"/, `from "${inert}"`)
      .replace(/from\s+"@\/lib\/systemMonitor"/, `from "${inert}"`),
  )
);
const { createElement: h } = await import(resolveUrl("react"));
const { renderToStaticMarkup } = await import(resolveUrl("react-dom/server"));

/* ── fixtures: the payloads the live Gateway returned ───────────────────── */

const PROBES = [
  { key: "gateway", label: "Gateway", blurb: "Core API answering", ok: true, detail: "online", ms: 4 },
  { key: "memory", label: "Memory", blurb: "Facts the agent remembers", ok: true, detail: "5 facts", ms: 7 },
  { key: "skills", label: "Skills", blurb: "Toggleable abilities", ok: true, detail: "24 skills", ms: 5 },
  { key: "scheduled", label: "Scheduler", blurb: "Recurring background work", ok: true, detail: "0 schedules", ms: 6 },
  { key: "channels", label: "Chat channels", blurb: "Telegram / Slack / Discord…", ok: true, detail: "none connected", ms: 9 },
  { key: "mcp", label: "App connections (MCP)", blurb: "External tool servers", ok: true, detail: "5 servers", ms: 8 },
  { key: "watchdog", label: "Safety watchdog", blurb: "Worker health + self-heal", ok: true, detail: "watching", ms: 3 },
  {
    key: "company",
    label: "Autonomous company",
    blurb: "KPIs, board, briefings",
    ok: false,
    detail: "No active organizations found. Bootstrap a company first.",
    ms: 11,
  },
];

const HOST = {
  ram: { total_mb: 5996.1, used_mb: 5028.6, available_mb: 967.5, free_mb: 967.5, percent: 83.9 },
  cpu: { percent: 12, cores: 16 },
};

const CASES = {
  "01-healthy": {
    online: true,
    version: "2.1.0",
    stats: { runs: 49, threads: 49, agents: 0, tokens: 3612844, cost: null, currency: null, raw: {} },
    probes: PROBES,
    probesFailed: false,
    host: HOST,
  },
  "02-stats-failed": {
    online: true, version: "2.1.0", stats: null, probes: PROBES, probesFailed: false, host: HOST,
  },
  "03-probes-failed": {
    online: null, version: "2.1.0",
    stats: { runs: 49, threads: 49, agents: 0, tokens: 3612844, cost: null, currency: null, raw: {} },
    probes: [], probesFailed: true, host: HOST,
  },
  "04-offline-no-host": {
    online: false, version: "unknown", stats: null, probes: [], probesFailed: false, host: null,
  },
  "06-probed-but-empty": {
    // The probe request SUCCEEDED and the Gateway listed no subsurfaces. A real
    // answer that must not render as a `0/0` ratio.
    online: true, version: "2.1.0",
    stats: { runs: 0, threads: 0, agents: 0, tokens: 0, cost: null, currency: null, raw: {} },
    probes: [], probesFailed: false, host: HOST,
  },
  "05-priced": {
    online: true, version: "2.1.0",
    stats: { runs: 49, threads: 49, agents: 3, tokens: 3612844, cost: 12.5, currency: "USD", raw: {} },
    probes: PROBES.map((p) => ({ ...p, ok: true })), probesFailed: false, host: HOST,
  },
};

for (const [name, vitals] of Object.entries(CASES)) {
  const markup = renderToStaticMarkup(h(VitalsStrip, { vitals }));
  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>${name}</title>
<link rel="stylesheet" href="app.css">
<style>
  body { padding: 16px; background: hsl(var(--background)); }
  .hsl { }
</style></head>
<body>
  <p style="font:11px ui-monospace,monospace;color:#94a3b8;margin:0 0 8px">viewport 1000px — the width at which every label was display:none</p>
  <div style="width:1000px;max-width:100%">
    ${markup}
  </div>
</body></html>`;
  writeFileSync(join(outDir, `${name}.html`), html);
  console.log(join(outDir, `${name}.html`));
}
