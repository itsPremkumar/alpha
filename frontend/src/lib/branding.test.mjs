// branding.test.mjs — every browser-facing surface must show the real Alpha
// lion, and the icon set must stay generated from the one real logo.
//
// The failure this pins is a silent one: `frontend/public/` did not exist, so
// the tab, the bookmarks, the home screen and the installed PWA all fell back
// to the browser/Next.js default glyph while the app's own header showed the
// correct lion. Nothing errored, so nothing was reported.
//
// Three things have to hold together, and none of them is visible from any one
// file:
//   1. `branding.icons` is the only place a path is named,
//   2. `scripts/generate-brand-assets.mjs` is the only place an icon is made,
//   3. those two lists agree in both directions, and the files exist on disk.
//
// Direction (2 -> 1) is the one that catches the real regression: an asset the
// generator emits but nothing references is dead weight, and more importantly a
// rename on either side that leaves the other pointing at a missing file shows
// up here instead of as a 404 in a browser tab nobody is testing.
//
// Pure Node test (node --test src/lib/branding.test.mjs): reads files from disk
// only. No network, no browser, no build step, no image decoding.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(fileURLToPath(new URL("../..", import.meta.url)));
const publicDir = path.join(frontendRoot, "public");
const generatorSource = readFileSync(
  path.join(frontendRoot, "..", "scripts", "generate-brand-assets.mjs"),
  "utf8",
);
const layoutSource = readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
const manifestSource = readFileSync(new URL("../app/manifest.ts", import.meta.url), "utf8");

const brandingSource = readFileSync(new URL("./branding.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(brandingSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
});
const { branding } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);

/** The `file:` entries the generator writes, as repo-relative paths. */
const generated = [...generatorSource.matchAll(/\{\s*file:\s*"([^"]+)"/g)].map((match) => match[1]);

test("the real logo is the single source every mark is derived from", () => {
  assert.match(generatorSource, /frontend.*assets.*images.*alpha\.png/);
  // The generator must not draw a mark of its own — that is exactly how the
  // geometric "flow orbit" placeholder got in and stayed there.
  assert.doesNotMatch(generatorSource, /flow orbit/i);
  assert.ok(existsSync(path.join(frontendRoot, "src", "assets", "images", "alpha.png")));
});

test("branding.icons names the browser-facing icon set", () => {
  assert.equal(branding.icons.favicon, "/favicon.ico");
  assert.equal(branding.icons.apple, "/apple-touch-icon.png");
  assert.equal(branding.icons.manifest, "/manifest.webmanifest");
  assert.ok(branding.icons.pwa192 && branding.icons.pwa512 && branding.icons.pwaMaskable);
});

test("branding.icons and the generator agree in both directions", () => {
  // Every path branding advertises must be produced by the generator...
  const advertised = new Set(
    Object.values(branding.icons)
      .filter((value) => value !== branding.icons.manifest)
      .map((value) => `frontend/public/${value.replace(/^\//, "")}`),
  );
  for (const file of advertised) {
    assert.ok(generated.includes(file), `branding.icons advertises ${file}, which the generator never writes`);
  }

  // ...and every icon the generator writes under public/ must be advertised,
  // or it ships as an orphan nothing can reach.
  const publicTargets = generated.filter((file) => file.startsWith("frontend/public/"));
  assert.ok(publicTargets.length >= 6, "expected the full favicon/apple/PWA icon set");
  for (const file of publicTargets) {
    assert.ok(advertised.has(file), `${file} is generated but no surface references it`);
  }
});

test("every advertised icon exists on disk", () => {
  for (const value of Object.values(branding.icons)) {
    if (value === branding.icons.manifest) continue; // served by app/manifest.ts
    const file = path.join(publicDir, value.replace(/^\//, ""));
    assert.ok(existsSync(file), `missing generated icon: ${file}`);
  }
});

test("the document metadata wires the icon set into the tab and the install", () => {
  // Without these the tab shows Next.js's default favicon, which is the exact
  // bug this file exists to prevent.
  assert.match(layoutSource, /manifest:\s*branding\.icons\.manifest/);
  assert.match(layoutSource, /icons:\s*\{/);
  assert.match(layoutSource, /branding\.icons\.favicon16/);
  assert.match(layoutSource, /branding\.icons\.favicon32/);
  assert.match(layoutSource, /branding\.icons\.favicon\b/);
  assert.match(layoutSource, /apple:\s*\[\{\s*url:\s*branding\.icons\.apple/);
  // Icon paths must come from branding, never be spelled out inline.
  assert.doesNotMatch(layoutSource, /"\/favicon\.ico"/);
});

test("the web manifest declares the install icons, maskable included", () => {
  assert.match(manifestSource, /branding\.icons\.pwa192/);
  assert.match(manifestSource, /branding\.icons\.pwa512/);
  assert.match(manifestSource, /branding\.icons\.pwaMaskable/);
  // A maskable icon is cropped to a circle by the launcher; without this the
  // mark would be clipped by the mask.
  assert.match(manifestSource, /purpose:\s*"maskable"/);
  assert.match(manifestSource, /name:\s*branding\.name/);
  assert.doesNotMatch(manifestSource, /"\/icon-192\.png"/);
});
