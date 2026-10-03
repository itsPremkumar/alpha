import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { ROUTES, CHANNELS, assertUniqueChannels, sendChannels } from '../lib/ipc-routes.js';
import { STATES } from '../lib/service-supervisor.js';

const main = fs.readFileSync(new URL('../main.js', import.meta.url), 'utf8');
const preload = fs.readFileSync(new URL('../preload.js', import.meta.url), 'utf8');

/**
 * `main.js` is the one file in the desktop app that cannot be loaded by a pure
 * Node test, because it requires `electron`. These assertions therefore read its
 * source.
 *
 * That is normally a weak technique, and it is used here deliberately and
 * sparingly: for the properties that are about WIRING (does the app set the
 * AppUserModelID, does it register crash handling, does it load the supervisor)
 * rather than about logic. Everything with a decision in it was extracted into
 * `lib/` and tested for real, and `scripts/smoke-boot.mjs` launches the real
 * binary to cover what source inspection cannot.
 */
test('the route table is what the shell registers, and every route has a handler', () => {
  assert.doesNotThrow(() => assertUniqueChannels(ROUTES));
  assert.ok(ROUTES.length >= 10, 'the route table lost channels');

  // Every channel in the table must be handled in main.js by its constant name,
  // so adding a route without an implementation fails loudly at boot.
  for (const route of ROUTES) {
    const constant = Object.entries(CHANNELS).find(([, value]) => value === route.channel);
    assert.ok(constant, `${route.channel} has no CHANNELS constant`);
    assert.match(
      main,
      new RegExp(`\\[CHANNELS\\.${constant[0]}\\]`),
      `main.js has no handler for ${route.channel} (CHANNELS.${constant[0]})`,
    );
  }
});

test('the shell refuses IPC from a window it does not own', () => {
  // The renderer holds a privileged bridge, and the app now blocks non-Alpha
  // navigations, so an unrecognized sender must not be able to invoke anything.
  assert.match(main, /const isTrustedSender = \(event\) => trustedSenders\(\)\.includes\(event\.sender\)/);
  assert.match(main, /Refused an IPC call from an unrecognized sender/);
  // And the splash is explicitly trusted, because it exists before the main
  // window and is what renders boot progress.
  assert.match(main, /if \(splashWindow && !splashWindow\.isDestroyed\(\)\) senders\.push\(splashWindow\.webContents\)/);
  assert.match(main, /if \(mainWindow && !mainWindow\.isDestroyed\(\)\) senders\.push\(mainWindow\.webContents\)/);
});

test('the shell locks windows down to the local frontend', () => {
  // The renderer is a privileged loopback page. Without these, one hostile link
  // replaces the trusted UI with an arbitrary page that still holds the bridge.
  assert.match(main, /setWindowOpenHandler/);
  assert.match(main, /will-navigate/);
  assert.match(main, /will-redirect/);
  assert.match(main, /will-attach-webview/);
  // And the window options themselves.
  assert.match(main, /webSecurity: true/);
  assert.match(main, /allowRunningInsecureContent: false/);
  assert.match(main, /webviewTag: false/);
  assert.match(main, /contextIsolation: true/);
  assert.match(main, /nodeIntegration: false/);
  assert.match(main, /sandbox: true/);
});

test('the shell serves a Content-Security-Policy for the app origin', () => {
  assert.match(main, /buildContentSecurityPolicy/);
  assert.match(main, /onHeadersReceived/);
  assert.match(main, /'Content-Security-Policy'/);
});

test('the shell declares its Windows identity', () => {
  // Without an AppUserModelID, Windows groups the window under "Electron" and
  // shows notifications under the wrong name.
  assert.match(main, /app\.setAppUserModelId\(APP_ID\)/);
  assert.match(main, /const APP_ID = 'ai\.alpha\.desktop'/);
  // It must be the SAME id the installer declares, or the taskbar grouping is
  // correct only in a source run and wrong in the shipped one.
  const builder = fs.readFileSync(new URL('../electron-builder.yml', import.meta.url), 'utf8');
  assert.match(builder, /appId:\s*ai\.alpha\.desktop/);
  // electron-builder 26 has no `appUserModelId` key (it was rejected by the schema
  // during a real build), so the installer side of the identity comes from appId
  // alone. Asserting the absent key would be asserting against a build failure.
  assert.doesNotMatch(
    builder,
    /^appUserModelId:/m,
    'electron-builder 26 rejects an `appUserModelId` key; the identity comes from appId plus the in-process setAppUserModelId call',
  );
});

test('the shell recovers from a crashed renderer instead of showing a dead window', () => {
  assert.match(main, /render-process-gone/);
  assert.match(main, /mainWindow\.reload\(\)/);
  assert.match(main, /unresponsive/);
  assert.match(main, /responsive/);
});

test('the shell bounds how long the window may stay invisible', () => {
  // `ready-to-show` alone is not a deadline: a frontend that answers the probe
  // and then fails to render leaves the splash up forever.
  assert.match(main, /did not become visible within/);
  assert.match(main, /const loadDeadline = setTimeout/);
});

test('the shell persists window bounds, and never while maximized', () => {
  assert.match(main, /persistWindowBounds/);
  assert.match(main, /window\.isMaximized\(\) \|\| window\.isFullScreen\(\)/);
  // Writing on every 'move'/'resize' event would stutter a drag.
  assert.match(main, /const scheduleSave = \(\) => \{/);
  assert.match(main, /window-state\.json/);
});

test('the shell opens the log file before anything that can report an error', () => {
  // The original ran its diagnostics before `fileLoggingReady = true`, so the
  // most likely failure (a full disk) pointed the user at a main.log that did not
  // exist yet.
  //
  // Index the CALL SITE inside boot(), not the definition: `runStartupDiagnostics`
  // is declared above `boot`, so a naive `indexOf` finds the declaration first
  // and this assertion passes or fails for the wrong reason.
  const bootStart = main.indexOf('async function boot()');
  assert.ok(bootStart > 0, 'main.js no longer has an async boot()');
  const boot = main.slice(bootStart);

  const prepareDirs = boot.search(/^\s*ensureDir\(projectDir\)/m);
  const loggerReady = boot.search(/^\s*appLogger\.setLevel\(args\.logLevel\)/m);
  const session = boot.search(/startup.*session/s);
  const diagnostics = boot.search(/^\s*runStartupDiagnostics\(\)/m);
  assert.ok(prepareDirs >= 0, 'boot no longer prepares its directories');
  assert.ok(loggerReady >= 0, 'boot no longer enables detailed file logging');
  assert.ok(session >= 0, 'boot no longer records a detailed session event');
  assert.ok(diagnostics >= 0, 'boot no longer runs startup diagnostics');
  assert.ok(prepareDirs < loggerReady, 'logging is enabled before the directories exist');
  assert.ok(loggerReady < diagnostics, 'diagnostics run before the log file is open');
});

test('the shell derives service ownership from the port decision', () => {
  // Boot used to print "reusing" and then spawn anyway, leaving two Gateways
  // contending for one state directory.
  assert.match(main, /managed: decisions\.gateway \? decisions\.gateway\.action === 'spawn'/);
  assert.match(main, /managed: decisions\.frontend \? decisions\.frontend\.action === 'spawn'/);
  assert.match(main, /const decisions = \{ gateway: null, frontend: null \}/);
});

test('the shell supervises the services it starts', () => {
  assert.match(main, /new ServiceSupervisor\(/);
  assert.match(main, /await supervisor\.start\(/);
  // ...and does not tear down services it only attached to.
  assert.match(main, /if \(!result\.stopped\) log\(`WARNING: \$\{key\}/);
  // Failed starts must terminate the whole process tree: killing only the `uv`
  // wrapper orphaned a healthy Python Gateway on 8201 after a boot timeout.
  assert.match(main, /terminate: \(child, info\) => terminateServiceTree\(info && info\.key, child\)/);
  assert.match(main, /await killProcessTree\(child\.pid/);
});

test('the shell shuts down in order and reports an unclean exit', () => {
  assert.match(main, /buildDesktopShutdownSteps/);
  assert.match(main, /runShutdown\(/);
  assert.match(main, /Shutdown was not clean/);
  assert.match(main, /Shutdown completed cleanly/);
  // The last resort waits for the processes to actually be gone.
  assert.match(main, /killProcessTree\(child\.pid/);
});

test('the shell handles uncaught exceptions in the main process', () => {
  // An unhandled throw here terminated the app with no dialog and no log line.
  assert.match(main, /process\.on\('uncaughtException'/);
  assert.match(main, /process\.on\('unhandledRejection'/);
});

test('the shell keeps its update check honest about being unavailable', () => {
  assert.match(main, /decideAvailability/);
  assert.match(main, /Auto-update not active/);
  assert.match(main, /updateFeed/);
  // A failed feed must be reported as unavailable, never as "up to date".
  assert.match(main, /interpretCheckResult\(null, app\.getVersion\(\)\)/);
});

test('the shell reconciles Start with Windows against the stored preference', () => {
  assert.match(main, /decideBootReconcile/);
  assert.match(main, /auto-start\.json/);
  assert.match(main, /serializePreference/);
});

test('the bridge the preload exposes is a subset of the route table', () => {
  // A preload method that invokes a channel not in the table would fail at
  // runtime with "No handler registered", which the smoke test cannot see.
  const invoked = [...preload.matchAll(/ipcRenderer\.(?:invoke|send)\('([^']+)'/g)].map((m) => m[1]);
  assert.ok(invoked.length > 0, 'no IPC channel found in the preload');
  const registered = new Set(ROUTES.map((route) => route.channel));
  for (const channel of invoked) {
    assert.ok(registered.has(channel), `preload invokes ${channel}, which is not in the route table`);
  }
  // And the fire-and-forget channel is a `send`, never an `invoke`.
  assert.ok(sendChannels().length >= 1, 'no fire-and-forget channel is declared');
});

test('the supervisor state names the shell reports are the ones it defines', () => {
  assert.equal(STATES.failed, 'failed');
  assert.match(main, /change\.state === STATES\.failed && change\.error/);
});

test('detailed logs are structured, secret-safe, and level controlled', () => {
  assert.match(main, /require\('\.\/lib\/app-logger'\)/);
  assert.match(main, /createDesktopLogger\(\{/);
  assert.match(main, /appLogger\.setLevel\(args\.logLevel\)/);
  assert.match(main, /--log-level=.*debug.*info.*warn.*error/s);
  assert.match(main, /if \(args\.verbose\) args\.logLevel = 'debug'/);
  // Structured naming replaces anonymous prose at the points support needs.
  assert.match(main, /logEvent\('info', 'startup', 'session'/);
  assert.match(main, /logEvent\('info', 'boot', 'gateway-port'/);
  assert.match(main, /logEvent\('info', 'boot', 'frontend-port'/);
  assert.match(main, /logEvent\('warn', 'ipc', 'failed'/);
  assert.match(main, /logEvent\('error', 'startup', 'failed'/);
  // Raw arguments are not logged; parsed arguments are sanitized by construction.
  assert.doesNotMatch(main, /process\.argv\.join/);
});

test('first-launch provisioning gets its own timeout before the health clock', () => {
  // The screenshot failure: `uv run` downloaded Python and 238 packages inside
  // the supervisor's health wait, so a healthy-but-slow first launch timed out
  // at exactly 300s. Provisioning now happens first with its own deadline.
  assert.match(main, /await warmBackendEnvironment\(\);/);
  assert.match(main, /const BACKEND_WARMUP_TIMEOUT_MS = 15 \* 60 \* 1000/);
  assert.match(main, /\['sync', '--locked'\]/);
  assert.match(main, /createWarmupProgress\(\{/);
  assert.match(main, /waitForChildExit\(child, \{/);
});

test('the debugger collects a redacted bundle from the Tools menu', () => {
  assert.match(main, /require\('\.\/lib\/support-bundle'\)/);
  assert.match(main, /async function collectSupportBundle\(\)/);
  assert.match(main, /Collect &diagnostics bundle/);
  assert.match(main, /diagnostics-.*\.json/);
  // Config contents and environment values never enter the bundle.
  assert.doesNotMatch(main, /readFileSync\(.*config\.yaml/);
  assert.doesNotMatch(main, /process\.env\}/);
});

test('service deadlines are passed where the supervisor can honor them', () => {
  // The Gateway timeout used to travel inside `policy`, where the supervisor
  // ignored it. A slow Python install would then fail at five minutes even when
  // the shell claimed to allow ten.
  assert.match(main, /timings: \{ startupTimeoutMs: 600000 \}/);
  assert.match(main, /timings: \{ startupTimeoutMs: 300000 \}/);
  assert.match(main, /probeWithLogging\('gateway'/);
  assert.match(main, /probeWithLogging\('frontend'/);
  assert.match(main, /logEvent\('debug', 'supervisor', 'configured'/);
});