// test_fe_audit_next_output_dirs.test.mjs — the dev server and the production
// build must not share one output directory.
//
// MEASURED mechanism (Next 15.5.25, this checkout):
//   next/dist/bin/next:49          defaultEnv = commandName === 'dev' ? 'development' : 'production'
//   next/dist/esm/server/dev/hot-reloader-webpack.js:581   await this.clean(startSpan)
//   next/dist/esm/server/dev/hot-reloader-webpack.js:426   recursiveDelete(join(this.dir, this.config.distDir), /^cache/)
// So `next dev` deletes everything under its distDir except `cache/` at startup.
// With one shared distDir (`.next`) that means starting the dev server destroys
// the production build: `.next/BUILD_ID`, `.next/server` and `.next/static` go.
// Two things then break, both measured on this repo:
//   * start.ps1:629 and scripts/watchdog.ps1:436 both gate the production path on
//     `Test-Path frontend\.next\BUILD_ID`, so after any `next dev` run the build
//     looks absent forever and every boot silently falls back to `next dev` plus
//     its ~880 s cold compile (the board's F9 measurement).
//   * the next `next build` then consumes dev-written bookkeeping in the same
//     directory and dies in "Collecting page data" with
//     `PageNotFoundError: Cannot find module for page: /_document` (board F11).
//
// The fix is a phase-scoped `distDir` in next.config.mjs. Next passes the phase
// to a config *function*, and the phase is exact (measured):
//   next/constants.js -> PHASE_DEVELOPMENT_SERVER = 'phase-development-server'
//                        PHASE_PRODUCTION_BUILD   = 'phase-production-build'
//                        PHASE_PRODUCTION_SERVER  = 'phase-production-server'
//
// Two invariants this pins, because breaking either is worse than the bug:
//   1. The production distDir stays `.next` — start.ps1 / watchdog.ps1 key on it.
//   2. `next build` and `next start` must resolve to the SAME directory, or a
//      build is invisible to the server meant to serve it.
//
// Pure Node test: imports the real next.config.mjs. No build, no server.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const FRONTEND = fileURLToPath(new URL("../..", import.meta.url));
const { PHASE_DEVELOPMENT_SERVER, PHASE_PRODUCTION_BUILD, PHASE_PRODUCTION_SERVER, PHASE_EXPORT, PHASE_TEST, PHASE_INFO } =
  await import("next/constants.js");

const nextConfigUrl = new URL("../../next.config.mjs", import.meta.url);
const exported = (await import(nextConfigUrl.href)).default;

/** Next accepts either a config object or a (phase) => config function. */
const resolve = async (phase) => (typeof exported === "function" ? await exported(phase) : exported);

test("next.config.mjs still exports a Next-acceptable config for every phase Next defines", async () => {
  assert.ok(
    typeof exported === "function" || (typeof exported === "object" && exported !== null),
    `next.config.mjs must export an object or a (phase) => object function, got ${typeof exported}`,
  );
  for (const phase of [PHASE_DEVELOPMENT_SERVER, PHASE_PRODUCTION_BUILD, PHASE_PRODUCTION_SERVER, PHASE_EXPORT, PHASE_TEST, PHASE_INFO]) {
    const config = await resolve(phase);
    assert.ok(config && typeof config === "object", `phase ${phase} must resolve to an object`);
    // The gateway proxy is the whole API surface; losing it breaks every call.
    assert.equal(typeof config.rewrites, "function", `phase ${phase} must keep the /api/* -> gateway rewrites`);
    assert.equal(config.reactStrictMode, true, `phase ${phase} must keep reactStrictMode`);
  }
});

test("the dev server and the production build use different output directories", async () => {
  const dev = await resolve(PHASE_DEVELOPMENT_SERVER);
  const prod = await resolve(PHASE_PRODUCTION_BUILD);

  assert.ok(dev.distDir, "the dev phase must declare a distDir");
  assert.ok(prod.distDir, "the production build phase must declare a distDir");
  assert.notEqual(
    dev.distDir,
    prod.distDir,
    `dev and prod share distDir "${dev.distDir}": starting the dev server deletes the production build ` +
      `(hot-reloader-webpack.js:581 -> clean() -> recursiveDelete(join(dir, distDir), /^cache/))`,
  );
});

test("the production build and the production server share one directory", async () => {
  const build = await resolve(PHASE_PRODUCTION_BUILD);
  const serve = await resolve(PHASE_PRODUCTION_SERVER);
  assert.equal(
    serve.distDir,
    build.distDir,
    "`next start` would look in a directory `next build` never wrote, so a successful build is invisible",
  );
});

test("the production output directory stays .next, which is what the launcher gates on", async () => {
  // start.ps1:629 and scripts/watchdog.ps1:436 both read
  // `frontend\.next\BUILD_ID`. Moving the production build elsewhere would make
  // the launcher believe no build exists on every boot.
  for (const phase of [PHASE_PRODUCTION_BUILD, PHASE_PRODUCTION_SERVER]) {
    const config = await resolve(phase);
    assert.equal(config.distDir, ".next", `phase ${phase} must keep the production distDir at .next`);
  }
});

test("the development output directory is already covered by the shipped .gitignore", async () => {
  // A separate distDir that is not ignored leaves an untracked build tree in
  // `git status` forever. frontend/.gitignore only lists `.next/`, so the dev
  // directory has to live inside `.next` (which that rule covers at any depth)
  // or this test is the thing that tells us to add a rule.
  const gitignore = readFileSync(join(FRONTEND, ".gitignore"), "utf8");
  const rules = gitignore
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#"));
  const devDir = (await resolve(PHASE_DEVELOPMENT_SERVER)).distDir;

  const covered = rules.some((rule) => {
    const body = rule.replace(/^\//, "").replace(/\/$/, "");
    if (body.includes("*")) return false; // a glob would need real matching; be explicit instead
    // A directory rule matches this path when the dev dir is the rule's name or
    // sits underneath it (`.next/` covers `.next/dev`).
    return devDir === body || devDir.startsWith(`${body}/`);
  });
  assert.ok(covered, `dev distDir "${devDir}" is not covered by any frontend/.gitignore rule (${rules.join(", ")})`);
});
