// brand-assets.test.mjs — the desktop shell must ship the real Alpha lion.
//
// The placeholder this pins: `electron/build/icon-512.png` was a geometric
// "flow orbit" mark that looked like a generic git-client logo, and it was the
// only icon the Windows build knew about — so the installer, the Start Menu
// entry, the desktop shortcut, the splash screen and the taskbar all showed it
// while the running app showed the real lion in its own header.
//
// The fix is structural rather than cosmetic. The marks are now *tracked* in
// `electron/assets/` (derived from `frontend/src/assets/images/alpha.png` by
// `scripts/generate-brand-assets.mjs`), so a fresh clone already has the right
// icon and nothing depends on remembering to run a generator before packaging.
// These tests hold the wiring in place: a surface that stops naming the asset,
// or a builder that stops shipping it, fails here instead of shipping a wrong
// icon to a user.
//
// Pure Node test (npm test): reads files from disk only. No build, no Electron.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const electronDir = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const repoRoot = path.resolve(electronDir, '..');
const read = (...parts) => fs.readFileSync(path.join(electronDir, ...parts), 'utf8');

const MARK_PNG = path.join(electronDir, 'assets', 'alpha-mark.png');
const MARK_ICO = path.join(electronDir, 'assets', 'alpha.ico');

/** Read the size list out of an ICONDIR without decoding the images. */
function icoSizes(file) {
  const buffer = fs.readFileSync(file);
  assert.equal(buffer.readUInt16LE(0), 0, 'ICONDIR reserved field must be 0');
  assert.equal(buffer.readUInt16LE(2), 1, 'ICONDIR type must be 1 (icon)');
  const count = buffer.readUInt16LE(4);
  const sizes = [];
  for (let index = 0; index < count; index += 1) {
    const at = 6 + index * 16;
    // 0 is the on-disk encoding of 256.
    const width = buffer.readUInt8(at) || 256;
    const height = buffer.readUInt8(at + 1) || 256;
    assert.equal(width, height, `entry ${index} must be square`);
    const bytes = buffer.readUInt32LE(at + 8);
    const offset = buffer.readUInt32LE(at + 12);
    assert.equal(
      buffer.subarray(offset, offset + 4).toString('hex'),
      '89504e47',
      `entry ${index} must be a PNG payload (Vista+ .ico form)`,
    );
    assert.ok(bytes > 0, `entry ${index} must carry image data`);
    sizes.push(width);
  }
  return sizes;
}

test('the tracked brand marks exist in the checkout', () => {
  // Tracked, not generated-at-package-time: a fresh clone has to build the
  // right icon without anyone remembering to run the generator first.
  assert.ok(fs.existsSync(MARK_PNG), 'missing electron/assets/alpha-mark.png');
  assert.ok(fs.existsSync(MARK_ICO), 'missing electron/assets/alpha.ico');
  assert.ok(fs.existsSync(path.join(repoRoot, 'frontend', 'src', 'assets', 'images', 'alpha.png')));
});

test('alpha-mark.png is a real 512x512 PNG', () => {
  const buffer = fs.readFileSync(MARK_PNG);
  assert.equal(buffer.subarray(0, 8).toString('hex'), '89504e470d0a1a0a', 'PNG signature');
  assert.equal(buffer.readUInt32BE(16), 512, 'width');
  assert.equal(buffer.readUInt32BE(20), 512, 'height');
});

test('alpha.ico is multi-resolution so every Windows surface picks a legible size', () => {
  const sizes = icoSizes(MARK_ICO);
  for (const required of [16, 24, 32, 48, 64, 128, 256]) {
    assert.ok(sizes.includes(required), `alpha.ico is missing the ${required}px size (has ${sizes})`);
  }
});

test('the Windows build packs the tracked .ico and ships the mark to the app', () => {
  const builder = read('electron-builder.yml');
  assert.match(builder, /^\s*icon:\s*assets\/alpha\.ico\s*$/m);
  // Without `assets` in `files`, electron-builder leaves the .png out of the
  // package and every window falls back to the default Electron icon.
  assert.match(builder, /^\s*-\s*assets\/\*\*\s*$/m);
  // The removed placeholder and its build-time path must not come back.
  assert.doesNotMatch(builder, /icon-512\.png/);
  assert.doesNotMatch(builder, /shapes-only|flow orbit/i);
});

test('both BrowserWindows are given the app icon', () => {
  const main = read('main.js');
  assert.match(main, /const APP_ICON = path\.join\(__dirname, 'assets', 'alpha-mark\.png'\)/);
  // Two windows (splash + main) means two `icon:` assignments. A missing file
  // must degrade to the default icon rather than throw, so `windowIcon` is
  // guarded by existsSync.
  assert.match(main, /fs\.existsSync\(APP_ICON\) \? APP_ICON : undefined/);
  const iconAssignments = main.match(/icon: windowIcon/g) ?? [];
  assert.equal(iconAssignments.length, 2, 'the splash and main windows both need the icon');
});

test('the splash screen shows the lion instead of a text-only wordmark', () => {
  const splash = read('splash.html');
  assert.match(splash, /<img src="assets\/alpha-mark\.png"/);
  // The old markup hardcoded "Agent Workspace" while the product is Alpha and
  // the name already arrives as the displayName query parameter.
  assert.doesNotMatch(splash, /Agent\s*<span>Workspace/);
  assert.match(splash, /get\("displayName"\)/);
});

test('the Windows shortcuts use the Alpha icon, not a Windows system glyph', () => {
  const shortcuts = fs.readFileSync(
    path.join(repoRoot, 'installer', 'create-shortcuts.ps1'),
    'utf8',
  );
  assert.match(shortcuts, /electron\\assets\\alpha\.ico/);
  assert.match(shortcuts, /\$shortcut\.IconLocation = \$iconLocation/);
  // The system glyph may only survive as the documented fallback when the
  // asset is absent, never as the icon itself.
  assert.doesNotMatch(shortcuts, /\$shortcut\.IconLocation = 'shell32\.dll,13'/);
});

test('the Electron icon entry point defers to the single generator', () => {
  const makeIcon = read('scripts', 'make-icon.mjs');
  assert.match(makeIcon, /generate-brand-assets\.mjs/);
  // A second place that rasterizes a logo is how the two drifted before.
  assert.doesNotMatch(makeIcon, /sharp\(|<svg/);
  assert.ok(fs.existsSync(path.join(repoRoot, 'scripts', 'generate-brand-assets.mjs')));
});
