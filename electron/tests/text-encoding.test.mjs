import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const electronDir = fileURLToPath(new URL('..', import.meta.url));

/**
 * Files the desktop app ships, and therefore shows to a user.
 *
 * `main.js` is the one that matters most: it writes the splash status lines and
 * every native dialog title, so a mangled character there is visible on the
 * first-run surface. It had 25 of them (18 em dashes, 7 ellipses) committed as
 * double-encoded UTF-8, and the defect was invisible to CI because every
 * existing electron test asserts on ASCII substrings.
 */
const SHIPPED_TEXT_FILES = [
  'main.js',
  'preload.js',
  'pet-preload.js',
  'splash.html',
  'pet.html',
  'desktop-config.json',
  'electron-builder.yml',
  'lib/desktop-utils.js',
  'lib/lion-pet-window.js',
  'lib/ipc-routes.js',
  'lib/restart-policy.js',
  'lib/service-supervisor.js',
];

/** The byte sequences a UTF-8 em dash / ellipsis turn into when re-decoded as latin-1. */
const MOJIBAKE_MARKERS = [
  { name: 'em dash', bad: '\u00e2\u20ac\u201d', good: '\u2014' },
  { name: 'ellipsis', bad: '\u00e2\u20ac\u00a6', good: '\u2026' },
  // A left/right double quote and an apostrophe survive the same round trip and
  // are the usual next casualties, so they are pinned too.
  { name: 'right double quote', bad: '\u00e2\u20ac\u0153', good: '\u201d' },
  { name: 'left double quote', bad: '\u00e2\u20ac\u201c', good: '\u201c' },
];

for (const relative of SHIPPED_TEXT_FILES) {
  test(`${relative} is valid UTF-8 with no double-encoded characters`, () => {
    const full = path.join(electronDir, relative);
    assert.ok(fs.existsSync(full), `${relative} is missing from the desktop app`);

    const bytes = fs.readFileSync(full);
    const text = bytes.toString('utf8');

    // A decode failure inserts U+FFFD, which is how a truncated or
    // non-UTF-8 file announces itself. Its absence is necessary, not
    // sufficient, so the marker scan below is the real assertion.
    assert.equal(
      text.includes('\uFFFD'),
      false,
      `${relative} decodes with replacement characters; it is not valid UTF-8`,
    );

    // Round-trip: re-encoding the decoded text must reproduce the exact bytes.
    // This catches a file that happens to decode without U+FFFD but was written
    // from a different encoding.
    assert.deepEqual(
      Buffer.from(text, 'utf8'),
      bytes,
      `${relative} is not byte-stable UTF-8; it was probably written as latin-1 or cp1252`,
    );

    for (const marker of MOJIBAKE_MARKERS) {
      assert.equal(
        text.includes(marker.bad),
        false,
        `${relative} contains a double-encoded ${marker.name} (${JSON.stringify(
          marker.bad,
        )}); it should be ${JSON.stringify(marker.good)}. This is the mojibake that made the ` +
          'splash read "Starting Gateway API\\u00e2\\u20ac\\u00a6" and every dialog title read ' +
          '"Alpha \\u00e2\\u20ac\\u201d ...".',
      );
    }
  });
}

test('the desktop app ships real punctuation, not just the absence of mojibake', () => {
  // The inverse check. A "fix" that deleted every non-ASCII character would
  // satisfy the scan above while quietly shipping "Alpha - startup failed", so
  // the readable characters are asserted to actually be present.
  const main = fs.readFileSync(path.join(electronDir, 'main.js'), 'utf8');
  assert.match(main, /\u2014/, 'main.js should contain em dashes in its prose');
  assert.match(main, /\u2026/, 'main.js should contain ellipses in its user-facing strings');
  // And a specific user-visible string, verbatim.
  assert.match(
    main,
    /broadcastStatus\('Preparing local data directory\u2026'/,
    'the splash status line should read "Preparing local data directory…"',
  );
});

test('the splash shell is the surface these strings are read on', () => {
  // Pins WHY main.js's user-facing strings matter: splash.html renders the
  // messages broadcastStatus() sends. If that wiring is removed the encoding
  // gate still passes but the strings are no longer reachable, and this test
  // says so.
  // The channel names live in the preload, not in splash.html: the page calls
  // `window.alpha.onStatus(...)` and the preload does the `ipcRenderer.on`.
  const splash = fs.readFileSync(path.join(electronDir, 'splash.html'), 'utf8');
  assert.match(splash, /window\.alpha/, 'the splash must reach the desktop bridge');
  assert.match(splash, /api\.onStatus\(render\)/, 'the splash must subscribe to status messages');
  assert.match(
    splash,
    /api\.getStatus\(\)\.then\(render\)/,
    'the splash must replay current status, or the first message it renders is a late one',
  );

  const preload = fs.readFileSync(path.join(electronDir, 'preload.js'), 'utf8');
  assert.match(preload, /ipcRenderer\.on\('alpha:status'/, 'the preload must bind the status channel');
});
