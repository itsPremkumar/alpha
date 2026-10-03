#!/usr/bin/env node

/**
 * Standalone end-to-end diagnostics collector.
 *
 * For support cases where the app itself cannot be opened: reads the installed
 * locations, user-data logs, and live health endpoints, then writes the same
 * redacted bundle the in-app Tools menu produces (see electron/lib/support-bundle.js
 * and `collectSupportBundle` in electron/main.js).
 *
 * Usage:  node scripts/collect-diagnostics.mjs [--out <dir>]
 * Never prints config contents or environment values — presence only.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildSupportBundle, summarizeSupportBundle } from "../lib/support-bundle.js";

const electronDir = fileURLToPath(new URL("..", import.meta.url));
const outIndex = process.argv.indexOf("--out");
const outDir = outIndex >= 0 ? process.argv[outIndex + 1] : null;

function exists(file) {
  try {
    fs.accessSync(file);
    return true;
  } catch {
    return false;
  }
}

function readTail(file, maxLines = 200, maxBytes = 256 * 1024) {
  try {
    const stat = fs.statSync(file);
    const start = Math.max(0, stat.size - maxBytes);
    const fd = fs.openSync(file, "r");
    try {
      const buffer = Buffer.alloc(Math.min(stat.size, maxBytes));
      fs.readSync(fd, buffer, 0, buffer.length, start);
      return buffer.toString("utf8").split(/\r?\n/).slice(-maxLines);
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return [];
  }
}

async function fetchJson(url, timeoutMs = 5000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) return { ok: false, status: res.status };
    try {
      return await res.json();
    } catch {
      return { ok: true, status: res.status, body: "unparseable" };
    }
  } catch (error) {
    return { ok: false, error: error && error.message ? error.message : String(error) };
  } finally {
    clearTimeout(timer);
  }
}

const appData = process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming");
const localAppData = process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
const userData = path.join(appData, "Alpha");
const logsDir = path.join(userData, "logs");
const installDir = path.join(localAppData, "Programs", "alpha");
const resourcesDir = path.join(installDir, "resources");

const [gatewayHealth, frontendRoot] = await Promise.all([
  fetchJson("http://127.0.0.1:8201/health"),
  fetchJson("http://127.0.0.1:3000/"),
]);

const bundle = buildSupportBundle({
  generatedAt: new Date().toISOString(),
  app: { name: "Alpha", version: "unknown", packaged: true },
  runtime: { platform: process.platform, arch: process.arch, node: process.versions.node },
  install: {
    executable: path.join(installDir, "Alpha.exe"),
    executablePresent: exists(path.join(installDir, "Alpha.exe")),
    resources: resourcesDir,
    runtimes: {
      uv: exists(path.join(resourcesDir, "runtime", "uv", "uv.exe")),
      node: exists(path.join(resourcesDir, "runtime", "node", "node.exe")),
    },
    backend: exists(path.join(resourcesDir, "backend", "pyproject.toml")),
    frontend: exists(path.join(resourcesDir, "frontend-standalone", "server.js")),
  },
  health: { gateway: gatewayHealth, frontend: frontendRoot },
  services: [],
  logs: {
    main: readTail(path.join(logsDir, "main.log")),
    events: readTail(path.join(logsDir, "desktop-events.jsonl")),
    gateway: readTail(path.join(logsDir, "gateway.log")),
    frontend: readTail(path.join(logsDir, "frontend.log")),
  },
  notes: ["collected without launching the app"],
});

const summary = summarizeSupportBundle(bundle);
const targetDir = outDir || logsDir;
fs.mkdirSync(targetDir, { recursive: true });
const file = path.join(targetDir, `diagnostics-${bundle.generatedAt.replace(/[^0-9]/g, "").slice(0, 14)}.json`);
fs.writeFileSync(file, `${JSON.stringify(bundle, null, 2)}\n`, "utf8");

console.log(summary.lines.join("\n"));
if (summary.problems.length > 0) {
  console.log("\nProblems:");
  for (const problem of summary.problems) console.log(`- ${problem}`);
  process.exitCode = 1;
} else {
  console.log("\nNo problems detected.");
}
console.log(`\nBundle: ${file}`);
