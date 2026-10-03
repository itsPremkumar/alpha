#!/usr/bin/env node

/**
 * Pre-build gate: do the packaging inputs actually exist and agree?
 *
 * ## Why this exists
 *
 * `npm run dist` was three chained steps with no verification between them:
 * fetch-runtime → build:frontend → electron-builder. Every step can "succeed"
 * while producing nothing usable, and electron-builder does not fail on a
 * missing `extraResources` source — it just produces an installer with a hole in
 * it. The concrete failures this catches, all of which produced a *runnable
 * looking* installer that failed on a user's machine:
 *
 * - `build/runtime` empty → no bundled Node or uv → "Bundled `uv` runtime
 *   missing from the installed app" on first launch.
 * - `.next/standalone` missing → no frontend → "The installed bundle is missing
 *   the frontend server".
 * - `desktop-config.json` nodeVersion disagreeing with the binary actually
 *   fetched → a runtime nobody tested.
 * - The `publish:` block in electron-builder.yml disagreeing with
 *   `updateFeed` in desktop-config.json → auto-update silently disabled forever,
 *   because the client looks for a feed the build never emitted.
 * - A version drift between electron/package.json and the sources
 *   `scripts/verify_versions.sh` checks → a release that CI refuses to publish.
 *
 * Exits non-zero with every problem listed, so a build fails once with the whole
 * story instead of three times with one fact each.
 *
 * Usage:  node scripts/verify-build.mjs
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const electronDir = fileURLToPath(new URL("..", import.meta.url));
const repoRoot = path.resolve(electronDir, "..");
const problems = [];
const notes = [];

const fail = (message) => problems.push(message);
const note = (message) => notes.push(message);

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    fail(`cannot read ${path.relative(repoRoot, file)}: ${error.message}`);
    return null;
  }
}

// --- 1. the bundled runtime -------------------------------------------------
// The installer promises end users need nothing pre-installed. That promise is
// only true if node.exe and uv.exe are actually in build/runtime.
const runtimeDir = path.join(electronDir, "build", "runtime");
const runtimeStamp = path.join(runtimeDir, "versions.json");
if (!fs.existsSync(runtimeStamp)) {
  fail(
    "build/runtime/versions.json is missing — run `npm run fetch-runtime` first " +
      "(the packaged app bundles its own Node.js and uv so users need nothing installed).",
  );
} else {
  const stamp = readJson(runtimeStamp);
  if (stamp) {
    for (const [name, binary] of [["node", "node.exe"], ["uv", "uv.exe"]]) {
      const binaryPath = path.join(runtimeDir, name, binary);
      if (!fs.existsSync(binaryPath)) {
        fail(
          `build/runtime/${name}/${binary} is missing even though versions.json claims ` +
            `${stamp[name]}. Re-run \`npm run fetch-runtime -- --force\`.`,
        );
        continue;
      }
      // A stub is worse than a missing file: the installer ships it and the
      // failure only appears on the user's machine.
      const size = fs.statSync(binaryPath).size;
      if (size < 1024 * 1024) {
        fail(
          `build/runtime/${name}/${binary} is only ${size} bytes; that is not a real ` +
            "runtime binary. Delete build/runtime and re-run `npm run fetch-runtime -- --force`.",
        );
      }
      note(`runtime ${name} ${stamp[name]} (${(size / 1024 / 1024).toFixed(1)} MB)`);
    }

    // The pinned version must be the one that was actually fetched.
    const config = readJson(path.join(electronDir, "desktop-config.json"));
    if (config && stamp.node && stamp.node !== config.nodeVersion) {
      fail(
        `build/runtime has Node ${stamp.node} but desktop-config.json pins ` +
          `${config.nodeVersion}. The bundled runtime must match the pin, because the pin is ` +
          "what CI and the release notes refer to.",
      );
    }
    // The pin must still be a supported major.
    if (config && !/^22\./.test(String(config.nodeVersion))) {
      note(
        `WARNING: nodeVersion is ${config.nodeVersion}; .github/workflows/windows-installer.yml ` +
          "installs Node 22 on the build machine.",
      );
    }
  }
}

// --- 2. the standalone frontend --------------------------------------------
const standalone = path.join(repoRoot, "frontend", ".next", "standalone", "server.js");
if (!fs.existsSync(standalone)) {
  fail(
    "frontend/.next/standalone/server.js is missing — run `npm run build:frontend` first " +
      "(the installer ships the Next.js standalone server as <resources>/frontend-standalone).",
  );
} else {
  note("frontend standalone server present");
  // Next traces its deps into .next/standalone/node_modules, and
  // electron-builder skips node_modules inside extraResources unless listed
  // explicitly in electron-builder.yml. Missing `next` there is the packaged
  // "Cannot find module 'next'" crash.
  const nextInStandalone = path.join(repoRoot, "frontend", ".next", "standalone", "node_modules", "next");
  if (!fs.existsSync(nextInStandalone)) {
    fail(
      "frontend/.next/standalone/node_modules/next is missing. The standalone bundle cannot " +
        "boot without it (this is what makes electron-builder need the explicit node_modules " +
        "entry in extraResources).",
    );
  }
  // The manifest main.js rewrites at runtime to point /api at the Gateway.
  const manifest = path.join(repoRoot, "frontend", ".next", "standalone", ".next", "routes-manifest.json");
  if (!fs.existsSync(manifest)) {
    fail(
      "frontend/.next/standalone/.next/routes-manifest.json is missing. main.js rewrites the /api " +
        "destinations in it at startup and refuses to boot without it.",
    );
  }
}

// --- 3. the backend ---------------------------------------------------------
const backendPyproject = path.join(repoRoot, "backend", "pyproject.toml");
if (!fs.existsSync(backendPyproject)) {
  fail(`backend/pyproject.toml is missing at ${backendPyproject}; the Gateway sources are required.`);
} else {
  // `uv run --locked` resolves against uv.lock, so a missing lock means the app
  // cannot install its dependencies on first launch at all.
  const lock = path.join(repoRoot, "backend", "uv.lock");
  if (!fs.existsSync(lock)) {
    fail(
      "backend/uv.lock is missing. The desktop app spawns `uv run --locked`, so without the " +
        "lock the first launch cannot install the backend environment.",
    );
  } else {
    note("backend pyproject + uv.lock present");
  }
}

// --- 4. config templates ----------------------------------------------------
for (const template of ["config.example.yaml", "extensions_config.example.json"]) {
  if (!fs.existsSync(path.join(repoRoot, template))) {
    fail(
      `${template} is missing at the repo root; it is seeded into the user's project folder on ` +
        "first launch and electron-builder copies it into config-templates/.",
    );
  }
}

// --- 5. the update feed agrees with electron-builder ------------------------
const config = readJson(path.join(electronDir, "desktop-config.json"));
const builderPath = path.join(electronDir, "electron-builder.yml");
const builderText = fs.existsSync(builderPath) ? fs.readFileSync(builderPath, "utf8") : "";
if (config && builderText) {
  const feed = config.updateFeed;
  if (!feed) {
    fail(
      "desktop-config.json has no updateFeed, so the app reports 'updates are disabled' " +
        "regardless of the publish block.",
    );
  } else {
    // Parsed line-wise rather than by one big regex, because this file's comment
    // blocks legitimately contain the word "publish" and a `^publish:` pattern
    // is easy to get subtly wrong. The rule is simple: a top-level key is a line
    // with no leading whitespace, and `publish:` is the last one in the file.
    const lines = builderText.split(/\r?\n/);
    const publishIndex = lines.findIndex((line) => /^publish:\s*$/.test(line));
    if (publishIndex === -1) {
      fail(
        "electron-builder.yml has no publish: block, so electron-builder emits no latest.yml " +
          "and electron-updater has nothing to read. Auto-update would never fire.",
      );
    } else {
      // Collect the indented keys that follow, stopping at the next top-level key.
      const publishKeys = new Map();
      for (let i = publishIndex + 1; i < lines.length; i += 1) {
        const line = lines[i];
        if (/^\S/.test(line)) break; // next top-level key: the section ends
        const match = line.match(/^\s+([A-Za-z]+):\s*(\S+)\s*$/);
        if (match) publishKeys.set(match[1], match[2]);
      }
      for (const key of ["provider", "owner", "repo"]) {
        const value = publishKeys.get(key);
        if (!value) {
          fail(
            `electron-builder.yml has no publish.${key}; electron-updater needs it to emit ` +
              "latest.yml, without which auto-update can never fire.",
          );
        } else if (feed[key] !== value) {
          fail(
            `update feed mismatch on "${key}": desktop-config.json says "${feed[key]}" but ` +
              `electron-builder.yml says "${value}". The client would look for a feed the build ` +
              "never emitted, and auto-update would silently never fire.",
          );
        }
      }
      if (publishKeys.has('provider')) {
        note(`update feed: ${feed.provider} ${feed.owner}/${feed.repo}`);
      }
    }
  }
}

// --- 6. version lockstep ----------------------------------------------------
// The installer name is Alpha-Setup-${version}.exe, and
// scripts/verify_versions.sh blocks publishing on any drift. Re-running it here
// means a local `npm run dist` cannot produce an artifact CI would refuse.
/**
 * Read the version from every source `scripts/verify_versions.sh` checks, and
 * compare them here.
 *
 * Deliberately a re-implementation rather than a `bash verify_versions.sh`
 * call: the desktop build runs on a developer Windows machine where `bash` is
 * usually absent (no WSL, no Git Bash on PATH), so shelling out would skip the
 * check exactly where a drift is most likely — a local `npm run dist`. The
 * canonical gate stays `verify_versions.sh`, run by
 * `.github/workflows/verify-versions.yml` on tags; this is the early warning.
 */
function checkVersionLockstep() {
  const sources = [
    ["deploy/helm/alpha/Chart.yaml", /^\s*version:\s*"?([\d.]+)"?/m],
    ["backend/pyproject.toml", /^\s*version\s*=\s*"([\d.]+)"/m],
    ["backend/packages/harness/pyproject.toml", /^\s*version\s*=\s*"([\d.]+)"/m],
    ["frontend/package.json", /"version"\s*:\s*"([\d.]+)"/],
    ["package.json", /"version"\s*:\s*"([\d.]+)"/],
    ["electron/package.json", /"version"\s*:\s*"([\d.]+)"/],
  ];
  const seen = new Map();
  for (const [relative, pattern] of sources) {
    const file = path.join(repoRoot, relative);
    if (!fs.existsSync(file)) {
      fail(`version source ${relative} is missing; scripts/verify_versions.sh requires it.`);
      continue;
    }
    const match = fs.readFileSync(file, "utf8").match(pattern);
    if (!match) {
      fail(`could not read a version out of ${relative}.`);
      continue;
    }
    seen.set(relative, match[1]);
  }
  const distinct = new Set(seen.values());
  if (distinct.size > 1) {
    const detail = [...seen.entries()].map(([file, v]) => `${file}=${v}`).join(", ");
    fail(
      `the version sources disagree (${detail}). The installer is named from ` +
        "electron/package.json, so a drift produces an artifact named for a version the rest " +
        "of the product does not have. Run `scripts/bump_version.sh <ver>` to align them.",
    );
  } else if (distinct.size === 1) {
    note(`version sources agree at ${[...distinct][0]}`);
  }
}

checkVersionLockstep();

// --- 7. the brand marks -----------------------------------------------------
for (const asset of ["assets/alpha.ico", "assets/alpha-mark.png"]) {
  const file = path.join(electronDir, asset);
  if (!fs.existsSync(file)) {
    fail(
      `${asset} is missing. The tracked brand marks are what makes the installer carry the Alpha ` +
        "icon; run `node scripts/make-icon.mjs`.",
    );
  }
}

// --- report -----------------------------------------------------------------
console.log("Build preflight\n");
for (const message of notes) console.log(`  ok    ${message}`);
if (problems.length === 0) {
  console.log("\nAll packaging inputs are present and consistent.");
  process.exit(0);
}
console.error(`\n${problems.length} problem(s) block packaging:\n`);
for (const problem of problems) console.error(`  ✗ ${problem}\n`);
console.error("Run the steps above, then `npm run dist`.");
process.exit(1);