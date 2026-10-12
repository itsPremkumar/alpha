#!/usr/bin/env node
/**
 * Alpha screenshot + console/network capture.
 *
 * Drives the real frontend with headless Chromium and records, **per route**,
 * three things a screenshot alone cannot show:
 *
 *   1. a PNG,
 *   2. every browser **console error** and uncaught page exception, and
 *   3. every **failed network request** — a 4xx/5xx response *or* a request
 *      that never completed (`requestfailed`).
 *
 * (3) is the point. A page that renders perfectly while every `/api/*` call
 * 500s looks identical to a working page in a screenshot; only the network log
 * separates them. So a route is reported `failed` even when the PNG looks fine.
 *
 * Honesty contract
 * ----------------
 * * A route that failed to load is recorded as `failed`, with the reason. The
 *   script never writes a "passing" screenshot of a blank page: blank-ness is
 *   itself a measured check (`blank_render`), and it fails the route.
 * * Each route's verdict is derived from measured facts, never assumed.
 * * Nothing existing is overwritten. Output goes to a fresh
 *   `docs/evidence/<domain>/<date>/<time>-<n>/` directory.
 * * A missing browser is reported as `blocked` with the exact install command,
 *   never as "0 routes failed, all good".
 *
 * Usage
 * -----
 *   node scripts/e2e/screenshots.mjs
 *   node scripts/e2e/screenshots.mjs --base http://127.0.0.1:3000
 *   node scripts/e2e/screenshots.mjs --routes "/,?view=settings,?view=apex"
 *   node scripts/e2e/screenshots.mjs --domain chat-regression
 *   node scripts/e2e/screenshots.mjs --list          # print the route list and exit
 *   node scripts/e2e/screenshots.mjs --check         # parse + list only, no browser
 *
 * Browser resolution order (first hit wins):
 *   1. `--chromium <path>` explicit executable
 *   2. an already-installed Playwright browser (PLAYWRIGHT_BROWSERS_PATH / the
 *      default user cache) matching this Playwright version
 *   3. a system Chrome/Edge on PATH
 *
 * Install (only needed if no browser is present):
 *   pnpm --dir frontend add -D playwright && node node_modules/playwright/cli.js install chromium
 */

import { mkdirSync, writeFileSync, existsSync, readdirSync, statSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");

const SCHEMA = "alpha-e2e-screenshots/v1";

/** Min rendered text (chars) before a page is considered non-blank. */
const BLANK_TEXT_THRESHOLD = 40;

/** A page is considered to have really rendered only if a DOM root exists. */
const REQUIRED_ROOT_SELECTORS = ["#__next", "#__next-workspace-root", "main", "body"];

/**
 * The route list is derived from the frontend's own view registry rather than
 * hand-copied, because `WORKSPACE_VIEW_IDS` is the single source and a stale
 * copy here would silently screenshot a route the UI no longer serves (it
 * silently falls back to `chat` — see frontend/src/lib/workspace-view.ts).
 */
function deriveRoutes() {
  const routes = [
    { name: "home", path: "/", label: "chat (default view)" },
  ];
  let registry = null;
  try {
    registry = readViewIdsFromSource();
  } catch {
    registry = null;
  }
  if (registry && registry.length) {
    for (const view of registry) {
      routes.push({
        name: `view-${view}`,
        path: view === "chat" ? "/?view=chat" : `/?view=${view}`,
        label: `workspace view: ${view}`,
        fromSource: true,
      });
    }
  } else {
    // The registry could not be read. Say so in the route list rather than
    // quietly capturing a smaller set that looks complete.
    routes.push({
      name: "registry-unavailable",
      path: null,
      label: "WORKSPACE_VIEW_IDS could not be read from frontend/src/lib/workspace-view.ts",
      blocked: true,
    });
  }
  return { routes, registrySource: registry ? "frontend/src/lib/workspace-view.ts" : null };
}

function readViewIdsFromSource() {
  const file = join(REPO_ROOT, "frontend", "src", "lib", "workspace-view.ts");
  if (!existsSync(file)) return [];
  const text = readFileUtf8(file);
  const start = text.indexOf("WORKSPACE_VIEW_IDS = [");
  if (start === -1) return [];
  const open = text.indexOf("[", start);
  const close = text.indexOf("]", open);
  if (open === -1 || close === -1) return [];
  // Strip `//` comments before matching: the comments in this file quote real
  // view ids (`isWorkspaceView("run-inspector")`), and a naive scan would add
  // duplicates the registry does not contain — capturing the same route twice
  // and reporting a route count the source never declared.
  const body = text
    .slice(open + 1, close)
    .split("\n")
    .map((line) => line.replace(/\/\/.*$/, ""))
    .join("\n");
  return [...body.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

function readFileUtf8(path) {
  // Explicit encoding: skill/agent text handling in this repo is UTF-8, never
  // the platform locale.
  return readFileSync(path, "utf8");
}

// ---------------------------------------------------------------------------
// argument parsing
// ---------------------------------------------------------------------------

function parseArgs(argv) {
  const args = {
    base: process.env.ALPHA_E2E_FRONTEND || "http://127.0.0.1:3000",
    routes: null,
    domain: "screenshots",
    timeout: 45000,
    settle: 2500,
    viewport: { width: 1440, height: 900 },
    fullPage: true,
    chromium: null,
    onlyConsoleErrors: false,
    list: false,
    check: false,
    failOnConsoleError: true,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => argv[++i];
    switch (arg) {
      case "--base": args.base = next(); break;
      case "--routes": args.routes = String(next()).split(",").map((s) => s.trim()).filter(Boolean); break;
      case "--domain": args.domain = next(); break;
      case "--timeout": args.timeout = Number(next()) * 1000; break;
      case "--settle": args.settle = Number(next()); break;
      case "--width": args.viewport.width = Number(next()); break;
      case "--height": args.viewport.height = Number(next()); break;
      case "--no-full-page": args.fullPage = false; break;
      case "--chromium": args.chromium = next(); break;
      case "--allow-console-errors": args.failOnConsoleError = false; break;
      case "--list": args.list = true; break;
      case "--check": args.check = true; break;
      case "-h":
      case "--help":
        printUsage();
        process.exit(0);
        break;
      default:
        if (arg.startsWith("-")) {
          console.error(`unknown flag: ${arg}`);
          printUsage();
          process.exit(2);
        }
    }
  }
  return args;
}

function printUsage() {
  console.log(readFileUtf8(fileURLToPath(import.meta.url)).split("/**")[0].replace(/^#!/, ""));
}

// ---------------------------------------------------------------------------
// browser resolution
// ---------------------------------------------------------------------------

function resolvePlaywright() {
  try {
    return require("playwright");
  } catch { /* fall through */ }
  try {
    return require("playwright-core");
  } catch { /* fall through */ }
  return null;
}

/** Newest revision directory under a Playwright browsers cache. */
function newestBrowserDir(root, prefix) {
  if (!existsSync(root)) return null;
  const entries = readdirSync(root)
    .filter((name) => name.startsWith(prefix))
    .map((name) => ({ name, rev: parseInt(name.replace(/^.*?-/, ""), 10) || 0 }))
    .sort((a, b) => b.rev - a.rev);
  for (const entry of entries) {
    for (const rel of [
      join("chrome-win64", "chrome.exe"),
      join("chrome-mac", "Google Chrome for Testing.app", "Contents", "MacOS", "Google Chrome for Testing"),
      join("chrome-linux", "chrome"),
      join("chrome-headless-shell-win64", "chrome-headless-shell.exe"),
      join("chrome-headless-shell-linux64", "chrome-headless-shell"),
    ]) {
      const candidate = join(root, entry.name, rel);
      if (existsSync(candidate)) return candidate;
    }
  }
  return null;
}

function findBrowserCacheRoot() {
  if (process.env.PLAYWRIGHT_BROWSERS_PATH) return process.env.PLAYWRIGHT_BROWSERS_PATH;
  if (process.platform === "win32") return join(process.env.LOCALAPPDATA || join(homedir(), "AppData", "Local"), "ms-playwright");
  if (process.platform === "darwin") return join(homedir(), "Library", "Caches", "ms-playwright");
  return join(homedir(), ".cache", "ms-playwright");
}

function resolveChromium(explicit) {
  const tried = [];
  if (explicit) {
    tried.push({ source: "--chromium", path: explicit });
    if (existsSync(explicit)) return { executablePath: explicit, tried };
  }
  try {
    const pw = resolvePlaywright();
    if (pw?.chromium?.executablePath) {
      const bundled = pw.chromium.executablePath();
      tried.push({ source: "playwright-bundled", path: bundled });
      if (existsSync(bundled)) return { executablePath: undefined, tried };
    }
  } catch { /* no bundled browser */ }

  const cache = newestBrowserDir(findBrowserCacheRoot(), "chromium-");
  tried.push({ source: "playwright-cache", path: cache });
  if (cache) return { executablePath: cache, tried };

  return { executablePath: undefined, tried };
}

// ---------------------------------------------------------------------------
// evidence directory
// ---------------------------------------------------------------------------

function newEvidenceDir(domain) {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const day = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const stamp = `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  const parent = join(REPO_ROOT, "docs", "evidence", domain, day);
  mkdirSync(parent, { recursive: true });
  for (let attempt = 1; attempt < 1000; attempt += 1) {
    const candidate = join(parent, `${stamp}-${String(attempt).padStart(2, "0")}`);
    if (!existsSync(candidate)) {
      mkdirSync(candidate, { recursive: true });
      return candidate;
    }
  }
  throw new Error(`could not allocate a fresh evidence directory under ${parent}`);
}

// ---------------------------------------------------------------------------
// per-route capture
// ---------------------------------------------------------------------------

function fileSafe(name) {
  return name.replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "route";
}

async function captureRoute(browser, route, args, outDir) {
  const consoleErrors = [];
  const consoleWarnings = [];
  const pageExceptions = [];
  const failedRequests = [];
  const httpErrors = [];

  const context = await browser.newContext({ viewport: args.viewport, ignoreHTTPSErrors: true });
  const page = await context.newPage();

  page.on("console", (msg) => {
    const entry = {
      type: msg.type(),
      text: msg.text().slice(0, 2000),
      location: msg.location() ? `${msg.location().url}:${msg.location().lineNumber}` : null,
      at: Date.now(),
    };
    if (msg.type() === "error") consoleErrors.push(entry);
    else if (msg.type() === "warning") consoleWarnings.push(entry);
  });

  page.on("pageerror", (err) => {
    pageExceptions.push({ name: err.name, message: String(err.message).slice(0, 2000), stack: String(err.stack || "").slice(0, 2000) });
  });

  page.on("requestfailed", (req) => {
    failedRequests.push({
      url: req.url().slice(0, 1000),
      method: req.method(),
      resourceType: req.resourceType(),
      failure: req.failure()?.errorText || "unknown",
    });
  });

  page.on("response", (res) => {
    if (res.status() >= 400) {
      httpErrors.push({ url: res.url().slice(0, 1000), method: res.request().method(), status: res.status(), statusText: res.statusText() });
    }
  });

  const url = new URL(route.path, args.base).toString();
  const record = {
    name: route.name,
    label: route.label,
    requested_path: route.path,
    url,
    started_at: new Date().toISOString(),
    http_status: null,
    navigation_error: null,
    screenshot: null,
    blank_render: null,
    rendered_text_chars: null,
    root_selector_hit: null,
    console_errors: consoleErrors,
    console_warnings: consoleWarnings,
    page_exceptions: pageExceptions,
    failed_requests: failedRequests,
    http_error_responses: httpErrors,
    verdict: "failed",
    verdict_reason: "",
  };

  try {
    const response = await page.goto(url, { waitUntil: "domcontentloaded", timeout: args.timeout });
    record.http_status = response ? response.status() : null;
    await page.waitForLoadState("networkidle", { timeout: args.settle }).catch(() => {
      /* networkidle is best-effort; long-polling surfaces keep it open */
    });
    await page.waitForTimeout(args.settle);
  } catch (err) {
    record.navigation_error = `${err.name}: ${err.message}`;
  }

  // Measure whether anything actually rendered. A screenshot of a blank page
  // is evidence of a failure, never of a pass.
  try {
    const measured = await page.evaluate((args2) => {
      const rootSelector = args2.selectors.find((sel) => {
        try { return document.querySelector(sel) !== null; } catch { return false; }
      }) || null;
      const text = (document.body?.innerText || "").replace(/\s+/g, " ").trim();
      return {
        rootSelectorHit: rootSelector,
        textChars: text.length,
        textSample: text.slice(0, 400),
        title: document.title || null,
      };
    }, { selectors: REQUIRED_ROOT_SELECTORS });

    record.root_selector_hit = measured.rootSelectorHit;
    record.rendered_text_chars = measured.textChars;
    record.page_title = measured.title;
    record.rendered_text_sample = measured.textSample;
    record.blank_render = measured.textChars < BLANK_TEXT_THRESHOLD || measured.rootSelectorHit === null;
  } catch (err) {
    record.rendered_render_error = `${err.name}: ${err.message}`;
    record.blank_render = true;
  }

  try {
    const file = join(outDir, `${fileSafe(route.name)}.png`);
    await page.screenshot({ path: file, fullPage: args.fullPage });
    record.screenshot = relative(REPO_ROOT, file);
    record.screenshot_bytes = statSync(file).size;
  } catch (err) {
    record.screenshot_error = `${err.name}: ${err.message}`;
  }

  record.verdict_reason = judgeRoute(record, args);
  record.verdict = record.verdict_reason.startsWith("ok:") ? "pass" : "failed";
  record.finished_at = new Date().toISOString();
  record.console_error_count = consoleErrors.length;
  record.failed_request_count = failedRequests.length + httpErrors.length;

  await context.close();
  return record;
}

function judgeRoute(record, args) {
  if (record.navigation_error) return `navigation failed: ${record.navigation_error}`;
  if (record.http_status === null) return "no HTTP response was received for the document";
  if (record.http_status >= 400) return `document responded HTTP ${record.http_status}`;
  if (record.screenshot === null) return "screenshot could not be captured";
  if (record.blank_render) {
    return `page rendered blank (${record.rendered_text_chars ?? 0} chars of text, root=${record.root_selector_hit ?? "none"}) — this is a failure, not a pass`;
  }
  if (record.page_exceptions.length) return `${record.page_exceptions.length} uncaught page exception(s)`;
  if (record.console_errors.length && args.failOnConsoleError) {
    return `${record.console_errors.length} console error(s) — the page rendered but logged errors`;
  }
  if (record.failed_request_count) {
    return `${record.failed_request_count} failed network request(s) — the page rendered but the network failed`;
  }
  const warnings = record.console_warnings.length ? ` (${record.console_warnings.length} warning(s), not treated as failures)` : "";
  return `ok: HTTP ${record.http_status}, ${record.rendered_text_chars} chars rendered, no console errors, no failed requests${warnings}`;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const { routes: derivedRoutes, registrySource } = deriveRoutes();

  let routes = derivedRoutes;
  if (args.routes) {
    routes = args.routes.map((p, i) => ({ name: `custom-${i}-${fileSafe(p)}`, path: p, label: `custom: ${p}` }));
  }

  if (args.list || args.check) {
    console.log(JSON.stringify({ schema: SCHEMA, registry_source: registrySource, count: routes.length, routes }, null, 2));
    return 0;
  }

  const blocked = routes.filter((r) => r.blocked);
  const runnable = routes.filter((r) => !r.blocked && r.path);

  const pw = resolvePlaywright();
  const browserInfo = resolveChromium(args.chromium);
  if (!pw) {
    return report({
      args, routes, registrySource,
      verdict: "blocked",
      reason: "Playwright is not installed, so no browser could be launched. Install it with: pnpm --dir frontend add -D playwright && node frontend/node_modules/playwright/cli.js install chromium",
      outDir: null,
      elapsed: 0,
    });
  }

  const outDir = newEvidenceDir(args.domain);
  console.log(`evidence dir: ${relative(REPO_ROOT, outDir)}`);

  let browser;
  try {
    browser = await pw.chromium.launch({
      headless: true,
      ...(browserInfo.executablePath ? { executablePath: browserInfo.executablePath } : {}),
      args: ["--no-sandbox", "--disable-dev-shm-usage"],
    });
  } catch (err) {
    return report({
      args, routes, registrySource,
      verdict: "blocked",
      reason: `Chromium could not be launched: ${err.message}. Install a browser with: pnpm --dir frontend add -D playwright && node frontend/node_modules/playwright/cli.js install chromium`,
      outDir,
      elapsed: 0,
    });
  }

  const startedAt = Date.now();
  const results = [];
  try {
    for (const route of runnable) {
      process.stdout.write(`  [capture] ${route.name.padEnd(28)} ${route.path}\n`);
      results.push(await captureRoute(browser, route, args, outDir));
    }
  } finally {
    await browser.close().catch(() => {});
  }
  const elapsed = Date.now() - startedAt;

  const failed = results.filter((r) => r.verdict !== "pass");
  const verdict = failed.length === 0 && blocked.length === 0 && runnable.length > 0 ? "pass" : "failed";

  return report({
    args, routes, registrySource, results, outDir, elapsed,
    verdict,
    reason: verdict === "pass"
      ? `all ${results.length} route(s) rendered with no console errors and no failed network requests`
      : `${failed.length} of ${results.length} route(s) failed; ${blocked.length} route(s) could not be enumerated`,
    browser: { ...browserInfo, playwright: pw.name || "playwright", ok: true },
  });
}

async function report({ args, routes, registrySource, results, outDir, elapsed, verdict, reason, browser }) {
  const payload = {
    schema: SCHEMA,
    generated_at: new Date().toISOString(),
    base: args.base,
    verdict,
    verdict_reason: reason,
    verdict_scope:
      "A 'pass' means every enumerated route returned <400, rendered non-blank content, and produced no console error or failed network request. It is not a verification that any feature works correctly.",
    registry_source: registrySource,
    routes_enumerated: routes.length,
    routes_run: results ? results.length : 0,
    routes_blocked: routes.filter((r) => r.blocked).map((r) => ({ name: r.name, reason: r.label })),
    elapsed_ms: elapsed,
    browser,
    summary: results
      ? {
          passed: results.filter((r) => r.verdict === "pass").length,
          failed: results.filter((r) => r.verdict !== "pass").length,
          console_errors: results.reduce((n, r) => n + r.console_error_count, 0),
          failed_requests: results.reduce((n, r) => n + r.failed_request_count, 0),
          page_exceptions: results.reduce((n, r) => n + r.page_exceptions.length, 0),
        }
      : null,
    results: results || [],
  };

  let resultPath = null;
  if (outDir) {
    resultPath = join(outDir, "report.json");
    writeFileSync(resultPath, JSON.stringify(payload, null, 2), "utf8");
    payload.artifacts = { report_json: relative(REPO_ROOT, resultPath) };
    writeFileSync(resultPath, JSON.stringify(payload, null, 2), "utf8");
  }

  console.log("");
  console.log(`verdict : ${verdict}`);
  console.log(`reason  : ${reason}`);
  if (payload.summary) {
    console.log(`summary : ${payload.summary.passed} passed / ${payload.summary.failed} failed · ${payload.summary.console_errors} console error(s) · ${payload.summary.failed_requests} failed request(s) · ${payload.summary.page_exceptions} page exception(s)`);
  }
  if (resultPath) console.log(`report  : ${relative(REPO_ROOT, resultPath)}`);
  if (results) {
    for (const r of results) {
      const flag = r.verdict === "pass" ? "PASS" : "FAIL";
      console.log(`  ${flag}  ${r.name.padEnd(28)} ${r.verdict_reason}`);
    }
  }

  process.exitCode = verdict === "pass" ? 0 : 1;
  return process.exitCode;
}

main().catch((err) => {
  console.error(`screenshots harness error: ${err.stack || err.message}`);
  process.exitCode = 1;
});