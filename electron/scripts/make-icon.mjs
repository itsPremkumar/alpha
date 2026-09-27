#!/usr/bin/env node

/**
 * Generate the Alpha desktop icon from the real Alpha logo.
 *
 * This is the Electron-side entry point only. The marks themselves — and the
 * frontend favicons, Apple touch icon and PWA icons that must match it — are
 * all produced by the single generator at the repository root:
 *
 *     node scripts/generate-brand-assets.mjs
 *
 * It is kept as a separate entry point because the desktop build documents
 * `node scripts/make-icon.mjs` and `npm run make:icon`, and because the icon
 * is what `electron-builder.yml` packs into the executable and the shortcuts.
 * Both paths derive from `frontend/src/assets/images/alpha.png`, so they cannot
 * disagree about what the logo is.
 *
 * Run:  node scripts/make-icon.mjs   (needs `npm install --no-save sharp`)
 */

import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const electronDir = fileURLToPath(new URL("..", import.meta.url));
const generator = path.resolve(electronDir, "..", "scripts", "generate-brand-assets.mjs");

const result = spawnSync(process.execPath, [generator, ...process.argv.slice(2)], {
  stdio: "inherit",
});

if (result.error) {
  console.error(`ERROR: could not run ${generator}: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status ?? 1);
