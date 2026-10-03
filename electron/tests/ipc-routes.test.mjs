import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {
  CHANNELS,
  EVENTS,
  ROUTES,
  assertUniqueChannels,
  invokeChannels,
  sendChannels,
} from '../lib/ipc-routes.js';

test('the shipped route table is itself valid', () => {
  assert.equal(assertUniqueChannels(ROUTES), ROUTES);
});

test('the route table pins the renderer bridge contract', () => {
  // These names are the preloads' public surface (electron/preload.js and
  // electron/pet-preload.js). Renaming one silently breaks the renderer, so
  // they are asserted rather than merely derived.
  assert.equal(CHANNELS.status, 'alpha:status');
  assert.equal(CHANNELS.openUserData, 'alpha:open-user-data');
  assert.equal(CHANNELS.getAutoStart, 'alpha:get-auto-start');
  assert.equal(CHANNELS.setAutoStart, 'alpha:set-auto-start');
  assert.equal(CHANNELS.lionPetState, 'alpha:lion-pet-state');
  assert.equal(CHANNELS.lionPetPerform, 'alpha:lion-pet-perform');
  assert.equal(CHANNELS.lionPetVisible, 'alpha:lion-pet-visible');
});

test('a duplicated channel is refused with the Electron error it replaces', () => {
  // This is the regression test for the bug that made the app unlaunchable:
  // `ipcMain.handle` throws "Attempted to register a second handler for
  // 'alpha:status'" at module load, and Electron exits 0 with no window and no
  // log line. `main.js` registered four channels twice each.
  const duplicated = [
    { channel: 'alpha:status', kind: 'invoke' },
    { channel: 'alpha:status', kind: 'invoke' },
  ];
  assert.throws(
    () => assertUniqueChannels(duplicated),
    (error) =>
      error instanceof Error &&
      /second handler for 'alpha:status'/.test(error.message),
  );
});

test('a duplicate is refused even when the two entries differ in kind', () => {
  assert.throws(
    () =>
      assertUniqueChannels([
        { channel: 'alpha:status', kind: 'invoke' },
        { channel: 'alpha:status', kind: 'on' },
      ]),
    /second handler/,
  );
});

test('an unknown route kind is refused instead of registering nothing', () => {
  // A typo'd `kind` would mean `ipcMain.handle` is never called for that
  // channel, so the renderer's `invoke` would hang with no log line explaining
  // why. Refusing at boot is the only place this is visible.
  assert.throws(
    () => assertUniqueChannels([{ channel: 'alpha:status', kind: 'handle' }]),
    /unknown kind "handle"/,
  );
});

test('channels outside the alpha: namespace are refused', () => {
  assert.throws(
    () => assertUniqueChannels([{ channel: 'status', kind: 'invoke' }]),
    /outside the alpha: namespace/,
  );
});

test('malformed routes are refused', () => {
  assert.throws(() => assertUniqueChannels('not-an-array'), /must be an array/);
  assert.throws(() => assertUniqueChannels([null]), /missing a channel/);
  assert.throws(() => assertUniqueChannels([{ kind: 'invoke' }]), /missing a channel/);
  assert.throws(() => assertUniqueChannels([{ channel: '', kind: 'invoke' }]), /missing a channel/);
});

test('invoke and send channels are separated by kind', () => {
  const invokes = invokeChannels();
  const sends = sendChannels();
  assert.ok(invokes.includes('alpha:status'));
  assert.ok(invokes.includes('alpha:lion-pet-perform'));
  assert.ok(!invokes.includes('alpha:lion-pet-state'));
  assert.deepEqual(sends, ['alpha:lion-pet-state']);
  // No channel may be both awaitable and fire-and-forget.
  const overlap = invokes.filter((channel) => sends.includes(channel));
  assert.deepEqual(overlap, []);
});

test('every event channel is distinct from every registered route', () => {
  // `webContents.send` and `ipcMain.handle` on one channel name is a category
  // error: the renderer would receive a push it never asked for, and the
  // handler would answer a push that is not a request.
  const routes = new Set(ROUTES.map((route) => route.channel));
  for (const channel of Object.values(EVENTS)) {
    assert.equal(routes.has(channel), false, `${channel} is both an event and a route`);
  }
});

test('the main process registers IPC from the table, not from repeated literals', () => {
  const source = fs.readFileSync(new URL('../main.js', import.meta.url), 'utf8');

  // The structural fix: routing is driven by the table.
  assert.match(source, /require\('\.\/lib\/ipc-routes'\)/);
  assert.match(source, /assertUniqueChannels/);

  // The literal guard: the four channels that were registered twice each.
  for (const channel of [
    'alpha:status',
    'alpha:open-user-data',
    'alpha:get-auto-start',
    'alpha:set-auto-start',
  ]) {
    const literalRegistrations = source.split(`ipcMain.handle('${channel}'`).length - 1;
    assert.equal(
      literalRegistrations,
      0,
      `main.js registers '${channel}' literally ${literalRegistrations} time(s); ` +
        'every handler must come from the ROUTES table so a duplicate is impossible',
    );
  }

  // And the only `ipcMain.handle`/`ipcMain.on` calls are the two inside the
  // registration loop, which take their channel from a route rather than a
  // literal. That is the whole structural guarantee: there is nowhere in
  // main.js to write a second registration for one channel.
  const callSites = source
    .split('\n')
    .map((line, index) => ({ line: line.trim(), number: index + 1 }))
    .filter(({ line }) => /ipcMain\.(handle|on)\(/.test(line));
  assert.equal(
    callSites.length,
    2,
    `expected exactly the two registration calls inside the loop, found ${callSites.length}: ` +
      callSites.map((s) => `line ${s.number}: ${s.line}`).join(' | '),
  );
  for (const site of callSites) {
    assert.doesNotMatch(
      site.line,
      /['"]alpha:/,
      `main.js line ${site.number} registers a literal channel (${site.line}); ` +
        'every channel must come from ROUTES',
    );
  }

  // The loop must dispatch on the route's kind, so `on` vs `invoke` is decided
  // by the table rather than by which call site someone edited.
  assert.match(source, /for \(const route of ROUTES\)/);
  assert.match(source, /route\.kind === 'invoke'/);
});
