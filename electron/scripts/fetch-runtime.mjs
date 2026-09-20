#!/usr/bin/env node

/**
 * Fetch the bundled desktop runtime (portable Node.js + uv) into
 * `electron/build/runtime/` so the Windows installer is self-contained:
 * end-user machines need NOTHING pre-installed (no Node, no uv, no Python —
 * uv provisions CPython on first launch).
 *
 * Layout produced:
 *   build/runtime/node/node.exe
 *   build/runtime/uv/uv.exe (+ uvx.exe when shipped in the archive)
 *
 * Idempotent: skips work when build/runtime/versions.json matches
 * desktop-config.json and the binaries verify. Pass --force to re-fetch.
 *
 * Run:  node scripts/fetch-runtime.mjs [--force]
 * (also runs automatically as the first step of `npm run dist`)
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const electronDir = fileURLToPath(new URL("..", import.meta.url));
const config = JSON.parse(
  fs.readFileSync(path.join(electronDir, "desktop-config.json"), "utf8"),
);
const RUNTIME_DIR = path.join(electronDir, "build", "runtime");
const STAMP_FILE = path.join(RUNTIME_DIR, "versions.json");
const FORCE = process.argv.includes("--force");

const NODE_VERSION = config.nodeVersion;
const TARGETS = [
  {
    name: "node",
    version: NODE_VERSION,
    url: `https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-win-x64.zip`,
    binaries: ["node.exe"],
    verify: { file: "node.exe", args: ["--version"], expectPrefix: "v22." },
  },
  {
    name: "uv",
    version: "latest",
    url: "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip",
    binaries: ["uv.exe", "uvx.exe"],
    verify: { file: "uv.exe", args: ["--version"], expectPrefix: "uv " },
  },
];

function findFileRecursive(dir, fileName) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isFile() && entry.name.toLowerCase() === fileName.toLowerCase()) {
      return full;
    }
    if (entry.isDirectory()) {
      const found = findFileRecursive(full, fileName);
      if (found) return found;
    }
  }
  return null;
}

async function downloadToFile(url, destFile) {
  console.log(`Downloading ${url}`);
  const response = await fetch(url, { redirect: "follow" });
  if (!response.ok) {
    throw new Error(`Download failed (${response.status}): ${url}`);
  }
  const buffer = Buffer.from(await response.arrayBuffer());
  if (buffer.length < 1024 * 1024) {
    throw new Error(
      `Download suspiciously small (${buffer.length} bytes): ${url}`,
    );
  }
  fs.writeFileSync(destFile, buffer);
  console.log(`  saved ${(buffer.length / 1024 / 1024).toFixed(1)} MB`);
}

function extractZip(zipFile, destDir) {
  fs.mkdirSync(destDir, { recursive: true });
  if (process.platform === "win32") {
    const result = spawnSync(
      "powershell.exe",
      [
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        `Expand-Archive -LiteralPath '${zipFile}' -DestinationPath '${destDir}' -Force`,
      ],
      { stdio: "inherit" },
    );
    if (result.status !== 0) {
      throw new Error(`Expand-Archive failed for ${zipFile}`);
    }
    return;
  }
  const result = spawnSync("unzip", ["-q", "-o", zipFile, "-d", destDir], {
    stdio: "inherit",
  });
  if (result.status !== 0) {
    throw new Error(
      `Could not extract ${zipFile}: install 'unzip' or build on Windows.`,
    );
  }
}

function verifyBinary(file, args, expectPrefix) {
  const result = spawnSync(file, args, { encoding: "utf8" });
  const output = `${result.stdout || ""}${result.stderr || ""}`.trim();
  if (result.status !== 0 || !output.startsWith(expectPrefix)) {
    throw new Error(
      `Verification failed for ${file}: expected output starting with ` +
        `"${expectPrefix}", got status=${result.status} output=${JSON.stringify(output.slice(0, 200))}`,
    );
  }
  console.log(`  verified: ${output.split("\n")[0]}`);
}

function readStamp() {
  try {
    return JSON.parse(fs.readFileSync(STAMP_FILE, "utf8"));
  } catch {
    return null;
  }
}

async function fetchTarget(target) {
  const targetDir = path.join(RUNTIME_DIR, target.name);
  const stamp = readStamp();
  const stampOk =
    !FORCE &&
    stamp &&
    stamp[target.name] === target.version &&
    target.binaries.every((b) => {
      const file = path.join(targetDir, b);
      // uvx.exe is optional (older uv archives may not ship it).
      if (b !== target.verify.file && !fs.existsSync(file)) return true;
      return fs.existsSync(file);
    });
  if (stampOk) {
    console.log(`${target.name}: already fetched (${target.version}), skipping.`);
    return;
  }

  console.log(`${target.name}: fetching ${target.version}…`);
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), `alpha-${target.name}-`));
  try {
    const zipFile = path.join(tmpRoot, `${target.name}.zip`);
    await downloadToFile(target.url, zipFile);
    const extractDir = path.join(tmpRoot, "extracted");
    extractZip(zipFile, extractDir);
    fs.mkdirSync(targetDir, { recursive: true });
    for (const binary of target.binaries) {
      const found = findFileRecursive(extractDir, binary);
      if (!found) {
        if (binary === target.verify.file) {
          throw new Error(`${binary} not found inside ${target.url}`);
        }
        console.log(`  optional ${binary} not in archive, skipping.`);
        continue;
      }
      fs.copyFileSync(found, path.join(targetDir, binary));
      console.log(`  installed ${binary}`);
    }
    verifyBinary(
      path.join(targetDir, target.verify.file),
      target.verify.args,
      target.verify.expectPrefix,
    );
    const nextStamp = { ...(readStamp() || {}), [target.name]: target.version };
    fs.writeFileSync(STAMP_FILE, JSON.stringify(nextStamp, null, 2));
  } finally {
    fs.rmSync(tmpRoot, { recursive: true, force: true });
  }
}

try {
  for (const target of TARGETS) {
    await fetchTarget(target);
  }
  console.log("fetch-runtime complete.");
} catch (error) {
  console.error(`fetch-runtime failed: ${error.message}`);
  process.exit(1);
}
