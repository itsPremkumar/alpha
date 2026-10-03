'use strict';

/**
 * The desktop IPC route table, in one place, with duplicate channels refused.
 *
 * ## Why this module exists
 *
 * `main.js` used to call `ipcMain.handle` for `alpha:status`,
 * `alpha:open-user-data`, `alpha:get-auto-start` and `alpha:set-auto-start`
 * TWICE each, in adjacent lines. Electron's `ipcMain.handle` throws on a
 * second registration for the same channel:
 *
 *     Attempted to register a second handler for 'alpha:status'
 *
 * That throw happens at *module load*, inside the `else` branch of the
 * single-instance lock, before `app.whenReady()` resolves. Electron's main
 * script wrapper swallows it and the process exits with code 0: no splash, no
 * window, no error dialog, nothing in any log. Launching the app just appeared
 * to do nothing at all. (Verified on Electron 44.3.0 / Windows, against
 * `main` HEAD.)
 *
 * A comment cannot prevent a duplicated line. A table can, because
 * `assertUniqueChannels` makes the duplicate a startup error that a unit test
 * can see without launching a window.
 *
 * ## Why the table is data, not functions taking `ipcMain`
 *
 * `main.js` builds the table at boot because the handlers close over
 * boot-scoped state (the live service registry, the updater, the logger). The
 * *channel names and their kinds* — the part that is actually bug-prone — are
 * declared here as a frozen constant, so this module stays pure and testable
 * with no Electron import at all.
 */

/** Channel names, frozen so a typo cannot create a second spelling of one. */
const CHANNELS = Object.freeze({
  status: 'alpha:status',
  statusSubscribe: 'alpha:status-changed',
  openUserData: 'alpha:open-user-data',
  getAutoStart: 'alpha:get-auto-start',
  setAutoStart: 'alpha:set-auto-start',
  getAppInfo: 'alpha:get-app-info',
  checkForUpdates: 'alpha:check-for-updates',
  installUpdate: 'alpha:install-update',
  restartServices: 'alpha:restart-services',
  openLogFolder: 'alpha:open-log-folder',
  lionPetState: 'alpha:lion-pet-state',
  lionPetPerform: 'alpha:lion-pet-perform',
  lionPetVisible: 'alpha:lion-pet-visible',
});

/** Event channels the main process pushes to the renderer. */
const EVENTS = Object.freeze({
  statusChanged: CHANNELS.statusSubscribe,
  lionPetVisibility: 'alpha:lion-pet-visibility',
  updateState: 'alpha:update-state',
});

/**
 * The authoritative route table.
 *
 * `kind` is either:
 *   - `invoke` — paired with `ipcMain.handle`; the renderer awaits a value.
 *   - `on`     — paired with `ipcMain.on`; fire-and-forget, no return value.
 *
 * Every renderer-callable channel is listed exactly once. `events` are
 * main -> renderer pushes and are deliberately NOT part of this table: they
 * are sent with `webContents.send`, never registered on `ipcMain`, and
 * registering one of them as a handler would be a category error.
 */
const ROUTES = Object.freeze([
  Object.freeze({ channel: CHANNELS.status, kind: 'invoke' }),
  Object.freeze({ channel: CHANNELS.openUserData, kind: 'invoke' }),
  Object.freeze({ channel: CHANNELS.getAutoStart, kind: 'invoke' }),
  Object.freeze({ channel: CHANNELS.setAutoStart, kind: 'invoke' }),
  Object.freeze({ channel: CHANNELS.getAppInfo, kind: 'invoke' }),
  Object.freeze({ channel: CHANNELS.checkForUpdates, kind: 'invoke' }),
  Object.freeze({ channel: CHANNELS.installUpdate, kind: 'invoke' }),
  Object.freeze({ channel: CHANNELS.restartServices, kind: 'invoke' }),
  Object.freeze({ channel: CHANNELS.openLogFolder, kind: 'invoke' }),
  Object.freeze({ channel: CHANNELS.lionPetState, kind: 'on' }),
  Object.freeze({ channel: CHANNELS.lionPetPerform, kind: 'invoke' }),
  Object.freeze({ channel: CHANNELS.lionPetVisible, kind: 'invoke' }),
]);

const VALID_KINDS = new Set(['invoke', 'on']);

/**
 * Throw on anything that would make the table wrong at registration time.
 *
 * Three distinct defects are checked, because each has been a real failure
 * mode in this app or in Electron itself:
 *
 *   1. A duplicated channel — the crash above. Electron throws, silently.
 *   2. An unknown `kind` — a typo would register nothing at all, and the
 *      renderer would hang on an unhandled `invoke` with no log line.
 *   3. A malformed channel name — `ipcMain` accepts any string, so a missing
 *      `alpha:` prefix would create a channel the preload never exposes.
 *
 * @param {ReadonlyArray<{channel: string, kind: string}>} routes
 * @returns {ReadonlyArray<{channel: string, kind: string}>} the same array
 */
function assertUniqueChannels(routes) {
  if (!Array.isArray(routes)) {
    throw new TypeError(`IPC route table must be an array, got ${typeof routes}`);
  }
  const seen = new Set();
  for (const route of routes) {
    if (!route || typeof route.channel !== 'string' || route.channel === '') {
      throw new TypeError(`IPC route is missing a channel: ${JSON.stringify(route)}`);
    }
    if (!VALID_KINDS.has(route.kind)) {
      throw new TypeError(
        `IPC route ${route.channel} has unknown kind ${JSON.stringify(route.kind)}; ` +
          `expected one of ${[...VALID_KINDS].join(', ')}`,
      );
    }
    if (!route.channel.startsWith('alpha:')) {
      throw new TypeError(
        `IPC channel ${route.channel} is outside the alpha: namespace reserved for the desktop bridge`,
      );
    }
    if (seen.has(route.channel)) {
      throw new Error(
        `Attempted to register a second handler for '${route.channel}'. ` +
          'Electron throws on this at module load and exits 0 with no window, ' +
          'so it is refused here where a test can see it.',
      );
    }
    seen.add(route.channel);
  }
  return routes;
}

/** Channel names the renderer may invoke, in table order. */
function invokeChannels(routes = ROUTES) {
  return routes.filter((route) => route.kind === 'invoke').map((route) => route.channel);
}

/** Channel names the renderer may send, in table order. */
function sendChannels(routes = ROUTES) {
  return routes.filter((route) => route.kind === 'on').map((route) => route.channel);
}

module.exports = {
  CHANNELS,
  EVENTS,
  ROUTES,
  assertUniqueChannels,
  invokeChannels,
  sendChannels,
};
