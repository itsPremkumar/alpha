#!/usr/bin/env node

/**
 * Build the Agent Workspace frontend as a Next.js standalone server for Electron.
 *
 * Steps:
 *   1. Ensure frontend dependencies are installed (corepack pnpm).
 *   2. Run `pnpm build` with NEXT_CONFIG_BUILD_OUTPUT=standalone
 *      (see frontend/next.config.js — this emits .next/standalone/server.js).
 *   3. Copy `public/` and `.next/static` into the standalone output, as
 *      required by Next.js standalone deployments.
 *
 * Run from the `electron/` directory:  npm run build:frontend
 * Run directly:                        node scripts/build-frontend.mjs
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const electronDir = fileURLToPath(new URL("..", import.meta.url));
const repoRoot = path.resolve(electronDir, "..");
const frontendDir = path.join(repoRoot, "frontend");
const desktopConfig = JSON.parse(
  fs.readFileSync(path.join(electronDir, "desktop-config.json"), "utf8"),
);

// Runtime-only deps that Next.js standalone tracing omits (proven by booting
// the bundle with no parent node_modules: the server crashes with
// MODULE_NOT_FOUND without them). Copied from the frontend install below.
//
// styled-jsx: next/dist/server/require-hook.js does
// require.resolve('styled-jsx/package.json') at startup. With pnpm the package
// only exists inside .pnpm (never hoisted to frontend/node_modules), so tracing
// misses it and the packaged app dies with
// "Cannot find module 'styled-jsx/package.json'" before serving anything.
const EXTRA_STANDALONE_DEPS = ["@swc/helpers", "@next/env", "styled-jsx"];

function run(cmd, args, options = {}) {
  console.log(`> ${cmd} ${args.join(" ")}`);
  const result = spawnSync(cmd, args, {
    cwd: frontendDir,
    stdio: "inherit",
    shell: true,
    ...options,
  });
  if (result.status !== 0) {
    throw new Error(`Command failed (${result.status}): ${cmd} ${args.join(" ")}`);
  }
}

function ensureDependencies() {
  const nextBin = path.join(frontendDir, "node_modules", "next", "package.json");
  if (fs.existsSync(nextBin)) {
    console.log("Frontend dependencies already installed, skipping install.");
    return;
  }
  console.log("Installing frontend dependencies (this may take a few minutes)…");
  run("corepack", ["pnpm", "install", "--frozen-lockfile"]);
}

function buildStandalone() {
  console.log("Building frontend (Next.js standalone)…");
  run("corepack", ["pnpm", "build"], {
    env: {
      ...process.env,
      NEXT_CONFIG_BUILD_OUTPUT: "standalone",
      // Bake the desktop Gateway URL into /api rewrite rules so a default
      // production launch needs no manifest patching. Custom ports are still
      // handled at startup by main.js (patchStandaloneGatewayUrl).
      AGENT_WORKSPACE_INTERNAL_GATEWAY_BASE_URL: `http://127.0.0.1:${desktopConfig.gatewayPort}`,
      AGENT_WORKSPACE_INTERNAL_GATEWAY_BASE_URL: `http://127.0.0.1:${desktopConfig.gatewayPort}`,
    },
  });
}

function resolveFrontendPackage(pkgName) {
  const direct = path.join(frontendDir, "node_modules", pkgName);
  if (fs.existsSync(path.join(direct, "package.json"))) return direct;
  // pnpm layout: find the highest-versioned store copy.
  const mangled = `${pkgName.replace("/", "+")}@`;
  const storeDir = path.join(frontendDir, "node_modules", ".pnpm");
  const candidates = fs
    .readdirSync(storeDir)
    .filter((d) => d.startsWith(mangled))
    .sort();
  if (candidates.length === 0) {
    throw new Error(`Cannot locate ${pkgName} in the frontend install`);
  }
  return path.join(
    storeDir,
    candidates[candidates.length - 1],
    "node_modules",
    pkgName,
  );
}

function copyExtraStandaloneDeps(standaloneDir) {
  const destRoot = path.join(standaloneDir, "node_modules");
  for (const pkg of EXTRA_STANDALONE_DEPS) {
    const src = resolveFrontendPackage(pkg);
    if (!fs.existsSync(path.join(src, "package.json"))) {
      throw new Error(`Resolved ${pkg} has no package.json: ${src}`);
    }
    // dereference:true materializes pnpm symlinks so the bundle is portable.
    fs.cpSync(src, path.join(destRoot, pkg), {
      recursive: true,
      dereference: true,
    });
    console.log(`Bundled extra standalone dep: ${pkg}`);
  }
}

function assembleStandalone() {
  const standaloneDir = path.join(frontendDir, ".next", "standalone");
  const serverEntry = path.join(standaloneDir, "server.js");
  if (!fs.existsSync(serverEntry)) {
    throw new Error(
      `Standalone server not found at ${serverEntry}. ` +
        "Ensure NEXT_CONFIG_BUILD_OUTPUT=standalone was honored by the build.",
    );
  }

  // Next.js standalone requires manual copies of public/ and .next/static.
  // The web showcase fixtures (public/demo) are excluded from the desktop
  // bundle (see electron-builder.yml) to keep the installer lean.
  const publicDest = path.join(standaloneDir, "public");
  const publicSrc = path.join(frontendDir, "public");
  if (fs.existsSync(publicSrc)) {
    fs.cpSync(publicSrc, publicDest, {
      recursive: true,
      filter: (src) => !src.includes(`${path.sep}demo${path.sep}`) && !src.endsWith(`${path.sep}demo`),
    });
    console.log(`Copied public/ -> ${publicDest} (excluding demo/)`);
  }
  const staticSrc = path.join(frontendDir, ".next", "static");
  const staticDest = path.join(standaloneDir, ".next", "static");
  const copies = [[staticSrc, staticDest]];
  for (const [src, dest] of copies) {
    if (!fs.existsSync(src)) {
      console.warn(`Warning: expected source missing, skipping copy: ${src}`);
      continue;
    }
    fs.cpSync(src, dest, { recursive: true });
    console.log(`Copied ${src} -> ${dest}`);
  }
  copyExtraStandaloneDeps(standaloneDir);
  console.log(`Standalone frontend ready: ${standaloneDir}`);
}

try {
  ensureDependencies();
  buildStandalone();
  assembleStandalone();
  console.log("build:frontend complete.");
} catch (error) {
  console.error(`build:frontend failed: ${error.message}`);
  process.exit(1);
}
