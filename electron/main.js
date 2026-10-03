'use strict';

/**
 * Alpha Desktop — Electron main process.
 *
 * A single native window over two local services: the Gateway API (FastAPI on
 * 127.0.0.1:8201) and the Next.js frontend (127.0.0.1:3000). No nginx: the
 * Next.js server rewrites /api/* to the Gateway directly (frontend/next.config.mjs),
 * so those two processes are the whole stack.
 *
 * ## Modes
 *
 *   electron . --dev          dev frontend + Gateway, no reload; restart to
 *                            reload the backend
 *   electron .                production: the standalone Next.js server (run
 *                            `npm run build:frontend` first) plus the Gateway
 *   packaged installer        same as production, from bundled resources
 *
 * ## Flags
 *
 *   --frontend-url=<url>   load this URL instead of spawning the frontend
 *                          (e.g. http://127.0.0.1:2026 when `make dev` runs nginx)
 *   --frontend-port=<n>    preferred frontend port (default 3000)
 *   --gateway-port=<n>     preferred gateway port (default 8201)
 *   --skip-backend         attach to an existing Gateway instead of spawning one
 *   --skip-frontend        attach to an existing frontend
 *   --require-login        keep the login and admin-setup screens
 *   --show-lion-pet        open the optional native companion at startup
 *   --no-updates           disable the auto-update check for this run
 *   --log-level=<level>    debug, info, warn, or error (default info)
 *   --verbose              mirror child-process output to the console and use debug logs
 *
 * A preferred port is reused only when a *verified Alpha service* answers on it;
 * otherwise the next free port is taken. "Verified" means the service identifies
 * itself as alpha, because attaching to a foreign process on the same port is
 * worse than failing loudly.
 *
 * ## Layout
 *
 * This file is the composition root and nothing else. Every decision worth
 * testing lives in `lib/`, which is where the tests point:
 *
 *   lib/ipc-routes.js         the channel table (a duplicate is a startup crash)
 *   lib/boot-sequence.js      phase order, and reuse-vs-spawn
 *   lib/service-supervisor.js bounded restart supervision
 *   lib/restart-policy.js     backoff and the crash-loop budget
 *   lib/lifecycle.js          ordered, bounded, observable shutdown
 *   lib/window-policy.js      navigation lockdown, bounds, permissions, CSP
 *   lib/app-info.js           the one status shape, and app info
 *   lib/diagnostics.js        preflight checks that return findings
 *   lib/updater.js            update policy (compare, check cadence, honesty)
 *   lib/auto-start.js         Start with Windows preference vs OS state
 *   lib/app-logger.js         detailed human and JSON event logs with redaction
 *   lib/child-process.js      spawning, logging, and verified tree kills
 *   lib/service-probe.js      "is this port actually our service?" identity checks
 *   lib/ports.js              bounded free-port search
 *
 * Two gates sit outside this file, because neither can be proved by a unit test
 * of a pure module:
 *   scripts/smoke-boot.mjs    launches the real binary and fails if the app dies
 *   scripts/verify-build.mjs  refuses to package missing or disagreeing inputs
 */

const {
  app,
  BrowserWindow,
  clipboard,
  crashReporter,
  dialog,
  ipcMain,
  Menu,
  screen,
  session,
  shell,
} = require('electron');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { resolveStartUrl, rewriteGatewayDestinations } = require('./lib/desktop-utils');
const { LionPetWindow } = require('./lib/lion-pet-window');
const { CHANNELS, EVENTS } = require('./lib/ipc-routes');
const { decideServicePort, describeBootFailure, runBootSequence } = require('./lib/boot-sequence');
const { ServiceSupervisor, STATES } = require('./lib/service-supervisor');
const { buildDesktopShutdownSteps, runShutdown } = require('./lib/lifecycle');
const {
  DEFAULT_WINDOW,
  buildContentSecurityPolicy,
  clampWindowBounds,
  evaluateNavigation,
  evaluatePermission,
  evaluateWindowOpen,
  pickRestoreDisplay,
} = require('./lib/window-policy');
const { buildAppInfo, createStatus, projectServices } = require('./lib/app-info');
const { formatPreflightFailure, runPreflight } = require('./lib/diagnostics');
const {
  UPDATE_STATE,
  decideAvailability,
  decideCheck,
  interpretCheckResult,
} = require('./lib/updater');
const {
  decideBootReconcile,
  decideToggle,
  describeState,
  readPreference,
  serializePreference,
} = require('./lib/auto-start');
const {
  attachChildLogging,
  bundledToolPath,
  defaultIsAlive,
  freeSpaceBytes,
  isChildDead,
  killProcessTree,
  resolveToolOnPath,
  spawnChild,
  waitForChildExit,
} = require('./lib/child-process');
const { LOG_LEVELS, createDesktopLogger } = require('./lib/app-logger');
const { findFreePort: searchFreePort, loopbackBase } = require('./lib/ports');
const { createWarmupProgress } = require('./lib/backend-warmup');
const { buildSupportBundle, summarizeSupportBundle } = require('./lib/support-bundle');
const { ROUTES, assertUniqueChannels, sendChannels } = require('./lib/ipc-routes');
const {
  fetchJson,
  isAlphaFrontend: probeAlphaFrontend,
  isAlphaGateway: probeAlphaGateway,
} = require('./lib/service-probe');
const { shouldGrantDesktopMediaPermission } = require('./lib/desktop-utils');

const DESKTOP_CONFIG = require('./desktop-config.json');
const APP_NAME = DESKTOP_CONFIG.displayName;
const APP_ID = 'ai.alpha.desktop';
const DEFAULT_FRONTEND_PORT = DESKTOP_CONFIG.frontendPort || 3000;
// Deliberately NOT 8001: that is `make dev`'s Gateway port. The desktop app owns
// its own Gateway so it never fights a separately running stack.
const DEFAULT_GATEWAY_PORT = DESKTOP_CONFIG.gatewayPort || 8201;

// The real Alpha lion mark, derived from frontend/src/assets/images/alpha.png by
// `scripts/make-icon.mjs`. Windows reads the window and taskbar icon from here
// rather than from the executable, so every BrowserWindow must be told about it
// or the app falls back to the default Electron icon. A missing file is not
// fatal; the app just runs on the default icon.
const APP_ICON = path.join(__dirname, 'assets', 'alpha-mark.png');
const windowIcon = fs.existsSync(APP_ICON) ? APP_ICON : undefined;

// ---------------------------------------------------------------------------
// CLI args
// ---------------------------------------------------------------------------

/**
 * @param {string[]} argv process.argv
 * @returns {object} parsed flags, with `forcedPorts` recording whether a port
 *   was pinned by the user (which we then honour even if it is occupied,
 *   rather than silently moving and making the flag a lie).
 */
function parseArgs(argv = []) {
  const args = {
    dev: false,
    skipBackend: false,
    skipFrontend: false,
    requireLogin: false,
    frontendUrl: null,
    frontendPort: DEFAULT_FRONTEND_PORT,
    gatewayPort: DEFAULT_GATEWAY_PORT,
    forcedPorts: { frontend: false, gateway: false },
    showLionPet: false,
    updates: true,
    logLevel: 'info',
    verbose: false,
  };
  for (const raw of argv.slice(1)) {
    if (raw === '--dev') args.dev = true;
    else if (raw === '--skip-backend') args.skipBackend = true;
    else if (raw === '--skip-frontend') args.skipFrontend = true;
    else if (raw === '--require-login') args.requireLogin = true;
    else if (raw === '--no-updates') args.updates = false;
    else if (raw.startsWith('--log-level=')) {
      const level = raw.slice('--log-level='.length).trim().toLowerCase();
      if (Object.prototype.hasOwnProperty.call(LOG_LEVELS, level)) args.logLevel = level;
    } else if (raw === '--verbose') args.verbose = true;
    else if (raw === '--show-lion-pet') args.showLionPet = true;
    else if (raw.startsWith('--frontend-url=')) args.frontendUrl = raw.slice('--frontend-url='.length);
    else if (raw.startsWith('--frontend-port=')) {
      const port = Number(raw.slice('--frontend-port='.length));
      if (Number.isFinite(port) && port > 0) {
        args.frontendPort = port;
        args.forcedPorts.frontend = true;
      }
    } else if (raw.startsWith('--gateway-port=')) {
      const port = Number(raw.slice('--gateway-port='.length));
      if (Number.isFinite(port) && port > 0) {
        args.gatewayPort = port;
        args.forcedPorts.gateway = true;
      }
    }
  }
  if (args.frontendUrl) args.skipFrontend = true;
  // Verbose has always meant full diagnostic output, so it selects debug logs
  // rather than only mirroring child output.
  if (args.verbose) args.logLevel = 'debug';
  return args;
}

const args = parseArgs(process.argv);

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

const isPackaged = app.isPackaged;
const repoRoot = isPackaged ? null : path.resolve(__dirname, '..');
const resourcesRoot = isPackaged ? process.resourcesPath : null;

const backendDir = isPackaged ? path.join(resourcesRoot, 'backend') : path.join(repoRoot, 'backend');
const frontendDir = isPackaged ? null : path.join(repoRoot, 'frontend');
const frontendStandaloneDir = isPackaged
  ? path.join(resourcesRoot, 'frontend-standalone')
  : path.join(repoRoot, 'frontend', '.next', 'standalone');
const configTemplatesDir = isPackaged ? path.join(resourcesRoot, 'config-templates') : repoRoot;

const userDataRoot = app.getPath('userData');
const projectDir = path.join(userDataRoot, 'project');
const alphaHomeDir = path.join(userDataRoot, 'alpha-home');
const logsDir = path.join(userDataRoot, 'logs');
const mainLogFile = path.join(logsDir, 'main.log');
const windowStateFile = path.join(userDataRoot, 'window-state.json');
const autoStartPrefFile = path.join(userDataRoot, 'auto-start.json');
const updateStateFile = path.join(userDataRoot, 'update-state.json');

// Per-user Python provisioning. The install directory stays read-only-safe
// (per-machine installs land in Program Files), so uv puts its Python and the
// backend venv under the writable user-data folder instead.
const backendVenvDir = path.join(userDataRoot, 'backend-venv');
const pythonInstallDir = path.join(userDataRoot, 'python');

// The pre-rename state directory. Reported, never migrated: the move relocates
// checkpoints, memory, and per-user data, so it stays an explicit operator
// action rather than something the app does unasked.
const legacyAlphaHomeDir = path.join(userDataRoot, 'agent-workspace-home');

// ---------------------------------------------------------------------------
// Detailed logging
// ---------------------------------------------------------------------------

const MAX_CHILD_LOG_BYTES = 20 * 1024 * 1024;

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function readAppVersion() {
  try {
    return app.getVersion();
  } catch {
    return 'unknown';
  }
}

function createSessionId() {
  try {
    return crypto.randomUUID();
  } catch {
    return `session-${Date.now()}-${process.pid}`;
  }
}

function ensureLogDirectory() {
  try {
    ensureDir(logsDir);
    return true;
  } catch (error) {
    // eslint-disable-next-line no-console
    console.error(`Desktop logging directory unavailable: ${error && error.message ? error.message : error}`);
    return false;
  }
}

const logsWritable = ensureLogDirectory();
const appLogger = createDesktopLogger({
  fs,
  path,
  console,
  now: Date.now,
  pid: process.pid,
  sessionId: createSessionId(),
  appName: APP_NAME,
  appVersion: readAppVersion(),
  logDir: logsDir,
  level: args.logLevel,
});

/**
 * Backward-compatible one-line log call.
 *
 * Existing call sites pass prose plus an optional detail string. New code
 * should call `logEvent` directly with component/event names and structured
 * context. This wrapper preserves human-readable history while also writing the
 * machine-readable desktop event needed for support diagnostics.
 */
function log(message, detail, context) {
  const detailContext = detail !== undefined && typeof detail === 'object' && detail !== null
    ? detail
    : detail !== undefined
      ? { detail }
      : undefined;
  appLogger.info('app', 'message', String(message), { ...detailContext, ...context });
}

function logEvent(level, component, event, message, context) {
  const write = appLogger[level] || appLogger.info;
  return write.call(appLogger, component, event, message, context);
}

function describePath(file) {
  try {
    const stat = fs.statSync(file);
    return {
      path: file,
      exists: true,
      sizeBytes: stat.isFile() ? stat.size : null,
      directory: stat.isDirectory(),
    };
  } catch (error) {
    return { path: file, exists: false, error: error && error.code ? error.code : 'unknown' };
  }
}

/**
 * Arguments are diagnostic gold and occasionally sensitive. The parsed option
 * object is safe by construction; raw `process.argv` is not logged because an
 * internal Electron switch or user-supplied URL can carry credentials. The
 * logger redacts values as a second defense.
 */
function sanitizeArguments(parsedArgs = {}) {
  return {
    dev: Boolean(parsedArgs.dev),
    skipBackend: Boolean(parsedArgs.skipBackend),
    skipFrontend: Boolean(parsedArgs.skipFrontend),
    requireLogin: Boolean(parsedArgs.requireLogin),
    frontendUrl: parsedArgs.frontendUrl || null,
    frontendPort: parsedArgs.frontendPort || null,
    gatewayPort: parsedArgs.gatewayPort || null,
    forcedPorts: parsedArgs.forcedPorts || { frontend: false, gateway: false },
    showLionPet: Boolean(parsedArgs.showLionPet),
    updates: parsedArgs.updates !== false,
    logLevel: parsedArgs.logLevel || 'info',
    verbose: Boolean(parsedArgs.verbose),
  };
}

if (fs.existsSync(legacyAlphaHomeDir) && !fs.existsSync(alphaHomeDir)) {
  logEvent('info', 'startup', 'legacy-home', 'Pre-rename state directory found', {
    legacyHome: legacyAlphaHomeDir,
    currentHome: alphaHomeDir,
    remedy: 'Rename it manually to keep existing threads, memory, and artifacts.',
  });
}

if (!logsWritable) {
  // eslint-disable-next-line no-console
  console.error(`Desktop file logging is disabled; console output remains available. Logs directory: ${logsDir}`);
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

/** Fetch JSON with a deadline; null on any failure. */
function httpGetJson(url, timeoutMs = 3000) {
  return fetchJson(url, { timeoutMs });
}

/** Can we bind this port on loopback right now? */
function isPortFree(port) {
  return new Promise((resolve) => {
    const server = require('node:net').createServer();
    server.once('error', () => resolve(false));
    server.once('listening', () => server.close(() => resolve(true)));
    server.listen(port, '127.0.0.1');
  });
}

function findFreePort(preferred) {
  return searchFreePort(preferred, { isPortFree, maxOffset: 20 });
}

const isAlphaGateway = (baseUrl) => probeAlphaGateway(baseUrl);
const isAlphaFrontend = (baseUrl) => probeAlphaFrontend(baseUrl, { displayName: APP_NAME });

/**
 * Desktop single-user mode: open straight into the workspace without the login
 * and admin-setup screens. Both services honour ALPHA_AUTH_DISABLED=1 at runtime
 * (synthetic admin user) and both bind to loopback only, so this stays a
 * local-machine trust boundary. `--require-login` keeps the normal auth screens.
 */
function applyDesktopAuthMode(env) {
  if (!args.requireLogin) env.ALPHA_AUTH_DISABLED = '1';
  return env;
}

function seedFileIfMissing(source, dest) {
  try {
    if (!fs.existsSync(dest) && source && fs.existsSync(source)) {
      fs.copyFileSync(source, dest);
      log(`Seeded default config: ${dest}`);
    }
  } catch (error) {
    log(`Warning: could not seed ${dest}: ${error.message}`);
  }
}

function readJsonFile(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

function writeJsonFile(file, data) {
  try {
    fs.writeFileSync(file, JSON.stringify(data, null, 2), 'utf8');
    return true;
  } catch (error) {
    log(`Warning: could not write ${file}: ${error.message}`);
    return false;
  }
}

// ---------------------------------------------------------------------------
// Startup diagnostics
// ---------------------------------------------------------------------------

/**
 * Fail-loud preflight, run before any service is spawned.
 *
 * `runPreflight` returns findings rather than throwing, because the original
 * threw from the middle of log setup — before `main.log` existed — and then
 * pointed the user at a file that was not there. Now the log file is opened
 * first, so the message it names is guaranteed to exist.
 */
function runStartupDiagnostics() {
  const tools = [];
  if (!args.skipBackend) {
    const uvPath = bundledToolPath(resourcesRoot, 'uv', 'uv.exe') || findUvOnPath();
    tools.push({ key: 'uv', label: 'uv', present: Boolean(uvPath), path: uvPath || null });
  }
  if (!args.skipFrontend) {
    const nodePath = bundledToolPath(resourcesRoot, 'node', 'node.exe') || findNodeOnPath();
    tools.push({ key: 'node', label: 'Node.js', present: Boolean(nodePath), path: nodePath || null });
  }

  const result = runPreflight(
    {
      platform: process.platform,
      arch: process.arch,
      osBuild: process.getSystemVersion ? process.getSystemVersion().split('.')[2] : undefined,
      isPackaged,
    },
    {
      freeBytes: () => freeSpaceBytes(userDataRoot),
      tools,
      appName: APP_NAME,
      userData: userDataRoot,
    },
  );

  for (const finding of result.findings) {
    log(`Preflight [${finding.severity}] ${finding.id}: ${finding.message} ${finding.remedy}`);
  }
  logEvent(result.ok ? 'info' : 'error', 'startup', 'preflight', result.ok ? 'Self-diagnostic passed' : 'Self-diagnostic failed', {
    ok: result.ok,
    findings: result.findings,
    tools,
    userData: userDataRoot,
    packaged: isPackaged,
    versions: {
      electron: process.versions.electron,
      node: process.versions.node,
      chrome: process.versions.chrome,
    },
  });
  if (result.ok) {
    log(
      'Self-diagnostic passed',
      `userData=${userDataRoot} packaged=${isPackaged} electron=${process.versions.electron} ` +
        `node=${process.versions.node} chrome=${process.versions.chrome}`,
    );
    return;
  }
  throw new Error(formatPreflightFailure(result, APP_NAME));
}

// ---------------------------------------------------------------------------
// Runtime tool resolution
// ---------------------------------------------------------------------------

function findUvOnPath() {
  const home = os.homedir();
  return resolveToolOnPath(process.platform === 'win32' ? 'uv.exe' : 'uv', [
    path.join(home, '.cargo', 'bin', process.platform === 'win32' ? 'uv.exe' : 'uv'),
    path.join(home, '.local', 'bin', 'uv'),
    process.platform === 'win32' ? path.join(home, 'AppData', 'Local', 'Programs', 'uv', 'uv.exe') : null,
  ]);
}

function findNodeOnPath() {
  return resolveToolOnPath(process.platform === 'win32' ? 'node.exe' : 'node', []);
}

/**
 * Path to a tool bundled inside the installed app, or null.
 *
 * A packaged install must be self-contained, so a missing bundled runtime is a
 * hard error rather than a fallback to whatever is on the user's PATH — a
 * silent fallback is how an install ends up running against a Node version the
 * app was never tested with.
 */
function requireBundledTool(name, winName, posixName, installHint) {
  const bundled = bundledToolPath(
    resourcesRoot,
    name,
    process.platform === 'win32' ? winName : posixName,
  );
  if (bundled) return bundled;
  if (isPackaged) {
    throw new Error(
      `Bundled ${name} runtime missing from the installed app (resources/runtime/${name}). ` +
        `Reinstall ${APP_NAME} — the packaged app must not depend on a system PATH copy.`,
    );
  }
  throw new Error(installHint);
}

function resolveUv() {
  const bundled = bundledToolPath(resourcesRoot, 'uv', 'uv.exe');
  if (bundled) return bundled;
  if (isPackaged) {
    throw new Error(
      `Bundled \`uv\` runtime missing from the installed app (resources/runtime/uv). ` +
        `Reinstall ${APP_NAME} — the packaged app must not depend on a system PATH copy.`,
    );
  }
  return findUvOnPath();
}

function resolveNode() {
  const bundled = bundledToolPath(resourcesRoot, 'node', 'node.exe');
  if (bundled) return bundled;
  if (isPackaged) {
    throw new Error(
      `Bundled Node.js runtime missing from the installed app (resources/runtime/node). ` +
        `Reinstall ${APP_NAME} — the packaged app must not depend on a system PATH copy.`,
    );
  }
  return findNodeOnPath();
}

// ---------------------------------------------------------------------------
// Child services
// ---------------------------------------------------------------------------

const childLogs = {
  gateway: path.join(logsDir, 'gateway.log'),
  frontend: path.join(logsDir, 'frontend.log'),
};

/**
 * Environment for every backend child, warmup and server alike.
 *
 * Defined once so `uv sync` provisions exactly the interpreter and venv layout
 * that `uv run` later serves from. Two different environments here would be a
 * first launch that provisions successfully and then serves from somewhere else.
 */
function backendEnv() {
  return applyDesktopAuthMode({
    ...process.env,
    PYTHONPATH: backendDir,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
    GATEWAY_HOST: '127.0.0.1',
    ALPHA_PROJECT_ROOT: projectDir,
    ALPHA_CONFIG_PATH: path.join(projectDir, 'config.yaml'),
    ALPHA_HOME: alphaHomeDir,
    // Keep all uv-managed writes (downloaded Python, project venv) under the
    // per-user data folder: the install directory may be read-only (per-machine
    // installs) and must never be written at runtime.
    UV_PYTHON_INSTALL_DIR: pythonInstallDir,
    UV_PROJECT_ENVIRONMENT: backendVenvDir,
  });
}

function backendVenvMarker() {
  return path.join(
    backendVenvDir,
    process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
  );
}

const BACKEND_WARMUP_TIMEOUT_MS = 15 * 60 * 1000;

/**
 * Provision the backend environment before the health clock starts.
 *
 * `uv run` does provisioning inline, so the supervisor's startup timeout used
 * to cover both downloading 238 packages AND importing the app. On a slow
 * connection that combined wait exceeded the deadline even though nothing was
 * broken — the exact failure in the screenshot. Warming up here gives
 * provisioning its own generous timeout and its own splash narration; the
 * supervisor then only waits for an already-provisioned server to answer.
 */
async function warmBackendEnvironment() {
  if (!decisions.gateway || decisions.gateway.action !== 'spawn') {
    return { warmed: false, reason: 'no backend spawn requested' };
  }
  if (fs.existsSync(backendVenvMarker())) {
    logEvent('debug', 'backend', 'warmup-skipped', 'Backend environment already provisioned', {
      marker: backendVenvMarker(),
    });
    return { warmed: false, reason: 'existing backend environment found' };
  }
  const uv = resolveUv();
  if (!uv) {
    throw new Error('uv executable not found on PATH');
  }
  broadcastStatus(
    'Preparing Python backend…',
    'First launch downloads Python and backend packages. This can take several minutes.',
  );
  const progress = createWarmupProgress({
    onStatus: (message, detail) => broadcastStatus(message, detail),
  });
  const startedAt = Date.now();
  const child = spawnChild(uv, ['sync', '--locked'], { cwd: backendDir, env: backendEnv() });
  children.gateway = child;
  logEvent('info', 'backend', 'warmup-started', `Provisioning backend environment: ${uv} sync --locked`, {
    command: uv,
    args: ['sync', '--locked'],
    cwd: backendDir,
    pid: child.pid,
  });
  attachChildLogging(child, 'gateway', {
    logFile: childLogs.gateway,
    maxLogBytes: MAX_CHILD_LOG_BYTES,
    verbose: args.verbose,
    log,
    isShuttingDown: () => appQuitting,
    onOutput: (text) => progress.report(text),
  });
  const result = await waitForChildExit(child, {
    timeoutMs: BACKEND_WARMUP_TIMEOUT_MS,
    killTree: () => killProcessTree(child.pid, { log, isAlive: childIsAlive }),
  });
  if (children.gateway === child) children.gateway = null;
  if (result.timedOut) {
    throw new Error(
      `Backend environment provisioning timed out after ${Math.round(BACKEND_WARMUP_TIMEOUT_MS / 60000)} minutes. ` +
        `Check the connection and disk space, then restart. See ${childLogs.gateway} for the last output.`,
    );
  }
  if ((result.code ?? 1) !== 0) {
    throw new Error(
      `Backend environment provisioning failed (code=${result.code ?? 'null'}). ` +
        `See ${childLogs.gateway} for the cause.`,
    );
  }
  progress.finish('Backend environment ready');
  logEvent('info', 'backend', 'warmup-ready', 'Backend environment provisioned', {
    durationMs: Date.now() - startedAt,
    marker: backendVenvMarker(),
  });
  return { warmed: true, durationMs: Date.now() - startedAt };
}

function spawnBackend(port) {
  const uv = resolveUv();
  if (!uv) {
    dialog.showErrorBox(
      `${APP_NAME} — uv not found`,
      'Could not find the `uv` Python package manager on PATH.\n\n' +
        'Install it from https://docs.astral.sh/uv/ (e.g. `winget install astral-sh.uv`),\n' +
        'restart the app, and the Gateway backend will start automatically.',
    );
    throw new Error('uv executable not found on PATH');
  }
  if (!fs.existsSync(path.join(backendDir, 'pyproject.toml'))) {
    throw new Error(`Backend sources not found at ${backendDir}`);
  }

  const env = { ...backendEnv(), GATEWAY_PORT: String(port) };

  const uvArgs = ['run', '--locked', 'uvicorn', 'app.gateway.app:app', '--host', '127.0.0.1', '--port', String(port)];
  logEvent('info', 'gateway', 'spawn', `Starting Gateway: ${uv} ${uvArgs.join(' ')}`, {
    command: uv,
    args: uvArgs,
    cwd: backendDir,
    port,
    backendSources: describePath(path.join(backendDir, 'pyproject.toml')),
    pythonInstallDir,
    backendEnvironment: backendVenvDir,
  });
  return startChild('gateway', () => spawnChild(uv, uvArgs, { cwd: backendDir, env }), childLogs.gateway);
}

/**
 * Point a production Next.js build's /api rewrites at this run's Gateway.
 *
 * Next.js resolves `rewrites()` from next.config.mjs at BUILD time and bakes the
 * concrete URLs into `.next/routes-manifest.json`, so the runtime
 * ALPHA_INTERNAL_GATEWAY_BASE_URL env var alone cannot steer a production server.
 * The desktop app picks its Gateway port dynamically, so it rewrites the baked
 * loopback destinations just before spawning the frontend. Only loopback
 * destinations are touched; anything else is left alone. Throws loudly when the
 * manifest cannot be read, parsed, or written — a silently unwired /api would
 * show the user an app whose every request fails with no explanation.
 *
 * @returns {boolean} true when at least one rule was updated
 */
function patchStandaloneGatewayUrl(standaloneDir, gatewayBaseUrl) {
  const manifestPath = path.join(standaloneDir, '.next', 'routes-manifest.json');
  let raw;
  try {
    raw = fs.readFileSync(manifestPath, 'utf8');
  } catch (error) {
    throw new Error(
      `Cannot point the bundled frontend at the Gateway: missing Next.js routes manifest at ${manifestPath} (${error.message}). ` +
        'Rebuild the frontend (`npm run build:frontend` from electron/) so /api rewrites exist.',
    );
  }
  let manifest;
  try {
    manifest = JSON.parse(raw);
  } catch (error) {
    throw new Error(`Cannot parse Next.js routes manifest at ${manifestPath}: ${error.message}`);
  }
  const { patched, allMatch } = rewriteGatewayDestinations(manifest, gatewayBaseUrl);
  if (patched === 0) {
    if (!allMatch) {
      throw new Error(
        `Cannot point the bundled frontend at the Gateway: no loopback /api rewrite destinations found in ${manifestPath}. ` +
          `Expected /api rewrites to http://127.0.0.1:<port> (built with ALPHA_INTERNAL_GATEWAY_BASE_URL). Rebuild the frontend.`,
      );
    }
    log(`Rewrite destinations already point at Gateway ${gatewayBaseUrl}`);
    return false;
  }
  try {
    fs.writeFileSync(manifestPath, JSON.stringify(manifest), 'utf8');
  } catch (error) {
    throw new Error(
      `Cannot point the bundled frontend at the Gateway (${manifestPath}): ${error.message}. ` +
        'The desktop app needs write access to its own files to wire /api to the Gateway.',
    );
  }
  log(`Patched ${patched} Next.js rewrite rule(s) to Gateway ${gatewayBaseUrl}`);
  return true;
}

function spawnFrontendDev(nodeExe, port, gatewayBaseUrl) {
  const nextBin = path.join(frontendDir, 'node_modules', 'next', 'dist', 'bin', 'next');
  if (!fs.existsSync(nextBin)) {
    dialog.showErrorBox(
      `${APP_NAME} — frontend dependencies missing`,
      `Next.js was not found in ${frontendDir}\\node_modules.\n\n` +
        'Run `corepack pnpm install --frozen-lockfile` inside the frontend/\n' +
        'directory, then restart the app.',
    );
    throw new Error('Frontend node_modules missing');
  }
  const env = applyDesktopAuthMode({
    ...process.env,
    PORT: String(port),
    ALPHA_INTERNAL_GATEWAY_BASE_URL: gatewayBaseUrl,
  });
  // The split-origin guard applies to dev as well as production. It used to
  // exist only in the production spawn, so a globally exported
  // NEXT_PUBLIC_GATEWAY_URL survived into `next dev` (which re-reads it at
  // compile time) and pointed the whole dev UI off-device. The very test that
  // checks the guard only inspected the production function, so the hole was
  // invisible to CI.
  delete env.NEXT_PUBLIC_GATEWAY_URL;

  const fArgs = [nextBin, 'dev', '--port', String(port)];
  logEvent('info', 'frontend', 'spawn', `Starting frontend (dev): ${nodeExe} ${fArgs.join(' ')}`, {
    command: nodeExe,
    args: fArgs,
    cwd: frontendDir,
    port,
    gateway: gatewayBaseUrl,
  });
  return startChild('frontend', () => spawnChild(nodeExe, fArgs, { cwd: frontendDir, env }), childLogs.frontend);
}

function spawnFrontendProd(nodeExe, port, gatewayBaseUrl) {
  const standaloneServer = path.join(frontendStandaloneDir, 'server.js');
  const env = applyDesktopAuthMode({
    ...process.env,
    PORT: String(port),
    HOSTNAME: '127.0.0.1',
    ALPHA_INTERNAL_GATEWAY_BASE_URL: gatewayBaseUrl,
  });
  // Remove split-origin overrides if the user exported them globally: the
  // desktop app always talks to the Gateway through Next.js rewrites.
  //
  // This must name the variable the client actually reads. `GATEWAY_BASE` is
  // built from `NEXT_PUBLIC_GATEWAY_URL` (frontend/src/lib/api-client.ts), so a
  // globally exported copy would otherwise point the whole desktop UI — prompts,
  // files, thread content — at that origin instead of the loopback Gateway.
  delete env.NEXT_PUBLIC_GATEWAY_URL;

  if (fs.existsSync(standaloneServer)) {
    const rewritesPatched = patchStandaloneGatewayUrl(frontendStandaloneDir, gatewayBaseUrl);
    logEvent('info', 'frontend', 'spawn', `Starting frontend (standalone): ${nodeExe} server.js`, {
      command: nodeExe,
      args: [standaloneServer],
      cwd: frontendStandaloneDir,
      port,
      gateway: gatewayBaseUrl,
      rewritesPatched,
    });
    return startChild(
      'frontend',
      () => spawnChild(nodeExe, [standaloneServer], { cwd: frontendStandaloneDir, env }),
      childLogs.frontend,
    );
  }

  const nextBin = isPackaged ? null : path.join(frontendDir, 'node_modules', 'next', 'dist', 'bin', 'next');
  const buildId = isPackaged ? null : path.join(frontendDir, '.next', 'BUILD_ID');
  if (!nextBin || !fs.existsSync(nextBin) || !buildId || !fs.existsSync(buildId)) {
    const hint = isPackaged
      ? `The installed bundle is missing the frontend server. Reinstall ${APP_NAME}.`
      : 'No production frontend build found. Run `npm run build:frontend` from the electron/ directory, then restart.';
    dialog.showErrorBox(`${APP_NAME} — frontend build missing`, hint);
    throw new Error('Frontend production build missing');
  }
  const fArgs = [nextBin, 'start', '-p', String(port)];
  // `next start` serves frontend/.next directly (not the standalone copy).
  const rewritesPatched = patchStandaloneGatewayUrl(frontendDir, gatewayBaseUrl);
  logEvent('info', 'frontend', 'spawn', `Starting frontend (next start): ${nodeExe} ${fArgs.join(' ')}`, {
    command: nodeExe,
    args: fArgs,
    cwd: frontendDir,
    port,
    gateway: gatewayBaseUrl,
    rewritesPatched,
  });
  return startChild('frontend', () => spawnChild(nodeExe, fArgs, { cwd: frontendDir, env }), childLogs.frontend);
}

// ---------------------------------------------------------------------------
// Session state
// ---------------------------------------------------------------------------

let splashWindow = null;
let mainWindow = null;
let appQuitting = false;
let shuttingDown = false;
let supervisor = null;
let frontendUrl = null;
let gatewayUrl = null;
let runtimeState = createStatus({ dev: args.dev, packaged: isPackaged });
let updateState = { state: UPDATE_STATE.idle, message: null, version: null, releaseNotes: null };
let lastCheckAt = null;
let startupAt = Date.now();

const lionPetController = new LionPetWindow({
  log,
  onVisibilityChange: (visible) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      try {
        mainWindow.webContents.send(EVENTS.lionPetVisibility, { visible });
      } catch {
        // The main window may be closing while the native companion is toggled.
      }
      buildMenu();
    }
  },
});

/** Recompute the renderer-facing status from the current state. */
function currentStatus() {
  runtimeState = createStatus({
    dev: args.dev,
    packaged: isPackaged,
    frontendUrl,
    gatewayUrl,
    services: supervisor ? projectServices(supervisor.status()) : [],
    userData: userDataRoot,
    logsDir,
    versions: {
      app: app.getVersion(),
      electron: process.versions.electron,
      chrome: process.versions.chrome,
      node: process.versions.node,
    },
  });
  return runtimeState;
}

function broadcastStatus(message, detail) {
  log(message, detail);
  const payload = { message, detail: detail || '' };
  for (const win of BrowserWindow.getAllWindows()) {
    try {
      win.webContents.send(EVENTS.statusChanged, payload);
    } catch {
      // Window may be closing; ignore.
    }
  }
  if (mainWindow && !mainWindow.isDestroyed()) {
    try {
      mainWindow.webContents.send(EVENTS.statusChanged, currentStatus());
    } catch {
      // Ignore.
    }
  }
}

function broadcastServiceState(change) {
  const status = currentStatus();
  for (const win of BrowserWindow.getAllWindows()) {
    try {
      win.webContents.send(EVENTS.statusChanged, status);
    } catch {
      // Ignore.
    }
  }
  logEvent(change.state === STATES.failed ? 'warn' : 'info', 'supervisor', 'state', `${change.label} is ${change.state}`, {
    service: change.key,
    state: change.state,
    port: change.port,
    error: change.error || null,
  });
  // A service that has given up is the one state the user must see immediately,
  // not only in the log. `detail` carries the actionable reason.
  if (change.state === STATES.failed && change.error) {
    broadcastStatus(`${change.label} needs attention`, change.error);
  }
}

// ---------------------------------------------------------------------------
// Windows
// ---------------------------------------------------------------------------

function createSplash() {
  splashWindow = new BrowserWindow({
    width: 440,
    height: 340,
    resizable: false,
    frame: false,
    alwaysOnTop: true,
    show: false,
    backgroundColor: '#0b0f14',
    icon: windowIcon,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  splashWindow.loadFile(path.join(__dirname, 'splash.html'), { query: { displayName: APP_NAME } });
  splashWindow.once('ready-to-show', () => splashWindow && splashWindow.show());
  splashWindow.on('closed', () => {
    splashWindow = null;
  });
}

/**
 * Read saved window bounds, clamped onto a display that still exists.
 *
 * Restoring bounds verbatim after an undock produces an invisible-but-open
 * window: the app looks hung and the user has to kill the process. Every
 * restore goes through the clamp, which is why that outcome is unreachable.
 */
function resolveWindowBounds() {
  const saved = readJsonFile(windowStateFile);
  const displays = screen.getAllDisplays();
  const { displayIndex, workArea } = pickRestoreDisplay(saved, displays, screen.getPrimaryDisplay()?.id !== undefined ? 0 : 0);
  logEvent('debug', 'window', 'restore', 'Resolved window bounds', {
    saved,
    displayIndex,
    displayCount: displays.length,
    workArea,
  });
  const clamped = workArea ? clampWindowBounds(saved, workArea, DEFAULT_WINDOW) : null;
  if (clamped) {
    try {
      const target = displays[displayIndex];
      if (target) return { ...clamped, display: displayIndex };
    } catch {
      // Fall through to defaults.
    }
  }
  return { ...DEFAULT_WINDOW, display: displayIndex };
}

/** Persist bounds so the window reopens where the user left it. */
function persistWindowBounds(window) {
  if (!window || window.isDestroyed()) return;
  // Never save while maximized or fullscreen: the restored window would then be
  // stuck at the size of the screen and could not be un-maximized.
  if (window.isMaximized() || window.isFullScreen()) return;
  try {
    const bounds = window.getNormalBounds();
    writeJsonFile(windowStateFile, { ...bounds, savedAt: Date.now() });
  } catch (error) {
    log(`Warning: could not persist window bounds: ${error.message}`);
  }
}

/**
 * Lock a window down to the local Alpha frontend.
 *
 * This is the security boundary for the whole app: the renderer holds a
 * privileged preload bridge, so nothing else may navigate it or open a child
 * window. `http(s)` targets are handed to the user's browser rather than loaded,
 * and `file:`/`data:`/`blob:`/`javascript:` are refused outright — passing one of
 * those to `shell.openExternal` would be its own vulnerability.
 */
function hardenWebContents(webContents, trustedUrl) {
  webContents.setWindowOpenHandler(({ url }) => {
    const decision = evaluateWindowOpen(url, { trustedFrontendUrl: trustedUrl });
    logEvent('info', 'security', 'window-open', `Blocked window.open: ${url}`, {
      url,
      reason: decision.reason,
      externalUrl: decision.externalUrl,
    });
    if (decision.externalUrl) openExternalSafely(decision.externalUrl);
    return { action: decision.action };
  });

  webContents.on('will-navigate', (event, url) => {
    const decision = evaluateNavigation(url, { trustedFrontendUrl: trustedUrl });
    if (decision.allowed) return;
    event.preventDefault();
    logEvent('info', 'security', 'navigation', `Blocked navigation: ${url}`, {
      url,
      reason: decision.reason,
      externalUrl: decision.externalUrl,
    });
    if (decision.externalUrl) openExternalSafely(decision.externalUrl);
  });

  // A link that opens in the same window via target=_self still arrives here.
  webContents.on('will-redirect', (event, url) => {
    if (evaluateNavigation(url, { trustedFrontendUrl: trustedUrl }).allowed) return;
    event.preventDefault();
    logEvent('info', 'security', 'redirect', `Blocked redirect: ${url}`, { url });
  });

  // A `<webview>` would be a second, unmonitored renderer.
  webContents.on('will-attach-webview', (event) => {
    event.preventDefault();
    logEvent('info', 'security', 'webview', 'Blocked a <webview> attach', {});
  });

  // Every window the app creates gets the same treatment, not just the main one.
  webContents.on('did-create-window', (child) => {
    child.setWindowOpenHandler(() => ({ action: 'deny' }));
  });
}

function openExternalSafely(url) {
  try {
    shell.openExternal(url, { activate: true });
  } catch (error) {
    log(`Warning: could not open ${url}: ${error.message}`);
  }
}

/**
 * Permission policy.
 *
 * Media is handled by the existing microphone-only rule (desktop-utils), and
 * everything else goes through an explicit allowlist. The previous handler
 * answered `callback(true)` for any permission it did not recognize, which
 * granted geolocation, MIDI, pointer lock, background sync, and openExternal to
 * a local page; an allowlist is the only version of this that stays safe as
 * Electron adds permissions.
 */
function configurePermissions(webContents, trustedUrl) {
  const s = webContents.session;
  s.setPermissionRequestHandler((requestingWebContents, permission, callback, details) => {
    const origin = requestingWebContents?.getURL() || trustedUrl;
    const isMedia = permission === 'media' || permission === 'audioCapture' || permission === 'microphone';
    if (isMedia) {
      // Microphone-only capture, from the exact local origin. Camera and mixed
      // audio/video requests fail closed.
      const granted = shouldGrantDesktopMediaPermission({
        permission,
        requestingOrigin: origin,
        trustedFrontendUrl: trustedUrl,
        details,
      });
      log(`Desktop ${permission} permission ${granted ? 'granted' : 'denied'}`, origin);
      callback(granted);
      return;
    }
    if (permission === 'camera' || permission === 'videoCapture') {
      log(`Desktop ${permission} permission denied`, origin);
      callback(false);
      return;
    }
    const decision = evaluatePermission(permission, { trustedFrontendUrl: trustedUrl, requestingOrigin: origin });
    log(`Desktop ${permission} permission ${decision.granted ? 'granted' : 'denied'}`, decision.reason);
    callback(decision.granted);
  });

  s.setPermissionCheckHandler((checkingWebContents, permission, requestingOrigin) => {
    const origin = requestingOrigin || checkingWebContents?.getURL() || trustedUrl;
    const isMedia = permission === 'media' || permission === 'audioCapture' || permission === 'microphone';
    if (isMedia) {
      return shouldGrantDesktopMediaPermission({
        permission,
        requestingOrigin: origin,
        trustedFrontendUrl: trustedUrl,
      });
    }
    if (permission === 'camera' || permission === 'videoCapture') return false;
    return evaluatePermission(permission, { trustedFrontendUrl: trustedUrl, requestingOrigin: origin }).granted;
  });
}

/**
 * Serve the app's own CSP.
 *
 * Without it, a successful HTML injection into a chat transcript runs script in
 * a page that holds the privileged bridge. `connect-src` includes the Gateway
 * because the renderer talks to it directly for some routes.
 */
function configureContentSecurityPolicy(trustedUrl, gatewayBase) {
  const policy = buildContentSecurityPolicy({
    gatewayOrigin: gatewayBase ? new URL(gatewayBase).origin : null,
    devServer: args.dev && !isPackaged,
  });
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    if (!details.url.startsWith(new URL(trustedUrl).origin)) {
      callback({});
      return;
    }
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [policy],
      },
    });
  });
}

function createMainWindow(targetUrl) {
  const bounds = resolveWindowBounds();
  mainWindow = new BrowserWindow({
    width: bounds.width,
    height: bounds.height,
    x: bounds.x,
    y: bounds.y,
    minWidth: DEFAULT_WINDOW.minWidth,
    minHeight: DEFAULT_WINDOW.minHeight,
    title: APP_NAME,
    backgroundColor: '#0b0f14',
    show: false,
    icon: windowIcon,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      // No `allowRunningInsecureContent` and no `webviewTag`: both are ways to
      // turn a loopback page into an arbitrary-content page.
      allowRunningInsecureContent: false,
      webviewTag: false,
      spellcheck: true,
    },
  });
  lionPetController.setMainWindow(mainWindow);
  configurePermissions(mainWindow.webContents, targetUrl);
  hardenWebContents(mainWindow.webContents, targetUrl);
  const loadStartedAt = Date.now();
  logEvent('info', 'window', 'load', 'Loading main window', { targetUrl, bounds });
  mainWindow.loadURL(targetUrl);

  // A window that never paints leaves the splash up forever with no
  // explanation. `ready-to-show` alone is not a deadline.
  const loadDeadline = setTimeout(() => {
    if (!mainWindow || mainWindow.isDestroyed() || mainWindow.isVisible()) return;
    log('Main window did not become visible within 60s', targetUrl);
    broadcastStatus('The window is taking longer than expected to open…', 'see logs/frontend.log');
  }, 60000);
  if (loadDeadline.unref) loadDeadline.unref();

  mainWindow.once('ready-to-show', () => {
    clearTimeout(loadDeadline);
    if (splashWindow) splashWindow.close();
    if (mainWindow) mainWindow.show();
    logEvent('info', 'window', 'ready', 'Main window ready', {
      targetUrl,
      durationMs: Date.now() - loadStartedAt,
    });
  });

  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedUrl) => {
    // -3 is ERR_ABORTED, which fires for a navigation the app itself cancelled
    // (a blocked redirect). Reporting it as a failure would be noise.
    if (errorCode === -3) return;
    logEvent('error', 'window', 'load-failed', `Window failed to load ${validatedUrl}: [${errorCode}] ${errorDescription}`, {
      url: validatedUrl,
      errorCode,
      errorDescription,
    });
  });

  // A crashed renderer is a recoverable event: reload it rather than leaving a
  // permanently blank window with no explanation.
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    logEvent('error', 'window', 'renderer-gone', `Renderer process gone: ${details.reason} (exitCode ${details.exitCode})`, {
      reason: details.reason,
      exitCode: details.exitCode,
    });
    broadcastStatus('The interface crashed and is reloading…', `reason: ${details.reason}`);
    if (details.reason === 'clean-exit' || appQuitting) return;
    setTimeout(() => {
      if (!mainWindow || mainWindow.isDestroyed() || appQuitting) return;
      mainWindow.reload();
    }, 1000);
  });

  mainWindow.webContents.on('unresponsive', () => {
    logEvent('warn', 'window', 'unresponsive', 'Renderer is unresponsive', { targetUrl });
    broadcastStatus('The interface is busy…', 'if this persists, force-reload from the File menu');
  });
  mainWindow.webContents.on('responsive', () => logEvent('info', 'window', 'responsive', 'Renderer became responsive again', { targetUrl }));

  // Bound the persistence writes: 'resize' and 'move' fire continuously during
  // a drag, and a synchronous disk write per event is a visible stutter.
  let saveTimer = null;
  const scheduleSave = () => {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      saveTimer = null;
      persistWindowBounds(mainWindow);
    }, 800);
    if (saveTimer.unref) saveTimer.unref();
  };
  mainWindow.on('resize', scheduleSave);
  mainWindow.on('move', scheduleSave);
  mainWindow.on('close', () => {
    if (saveTimer) clearTimeout(saveTimer);
    persistWindowBounds(mainWindow);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
    lionPetController.dispose();
  });
  return mainWindow;
}

// ---------------------------------------------------------------------------
// Menu
// ---------------------------------------------------------------------------

/** Read the last lines of a log file without loading a huge file into memory. */
function readLogTail(file, maxLines = 200, maxBytes = 256 * 1024) {
  try {
    const stat = fs.statSync(file);
    const start = Math.max(0, stat.size - maxBytes);
    const descriptor = fs.openSync(file, 'r');
    try {
      const buffer = Buffer.alloc(Math.min(stat.size, maxBytes));
      fs.readSync(descriptor, buffer, 0, buffer.length, start);
      return buffer.toString('utf8').split(/\r?\n/).slice(-maxLines);
    } finally {
      fs.closeSync(descriptor);
    }
  } catch {
    return [];
  }
}

/**
 * Collect the redacted end-to-end diagnostics bundle.
 *
 * This is the artifact support asks for instead of a screenshot: service
 * states, install contents, health results, and redacted log tails in one
 * file, plus a named-problem summary. Config contents and environment values
 * are never included — only their presence.
 */
async function collectSupportBundle() {
  const startedAt = Date.now();
  const [gatewayHealth, frontendReachable] = await Promise.all([
    httpGetJson(gatewayUrl ? `${gatewayUrl}/health` : 'http://127.0.0.1:8201/health', 5000),
    isAlphaFrontend(frontendUrl || 'http://127.0.0.1:3000/', { displayName: APP_NAME }).catch(() => false),
  ]);
  const frontendHealth = { reachable: Boolean(frontendReachable), url: frontendUrl || null };
  const bundle = buildSupportBundle({
    generatedAt: new Date().toISOString(),
    app: { name: APP_NAME, version: app.getVersion(), packaged: isPackaged },
    runtime: {
      platform: process.platform,
      arch: process.arch,
      electron: process.versions.electron,
      node: process.versions.node,
      chrome: process.versions.chrome,
    },
    install: {
      executable: process.execPath,
      resources: resourcesRoot,
      runtimes: {
        uv: Boolean(bundledToolPath(resourcesRoot, 'uv', 'uv.exe') || (!isPackaged && findUvOnPath())),
        node: Boolean(bundledToolPath(resourcesRoot, 'node', 'node.exe') || (!isPackaged && findNodeOnPath())),
      },
      backend: describePath(path.join(backendDir, 'pyproject.toml')),
      frontend: describePath(path.join(frontendStandaloneDir, 'server.js')),
    },
    services: supervisor ? supervisor.status() : [],
    health: { gateway: gatewayHealth, frontend: frontendHealth },
    logs: {
      main: readLogTail(mainLogFile),
      events: readLogTail(path.join(logsDir, 'desktop-events.jsonl')),
      gateway: readLogTail(childLogs.gateway),
      frontend: readLogTail(childLogs.frontend),
    },
    notes: [],
  });
  const summary = summarizeSupportBundle(bundle);
  const fileName = `diagnostics-${bundle.generatedAt.replace(/[^0-9]/g, '').slice(0, 14)}.json`;
  const file = path.join(logsDir, fileName);
  writeJsonFile(file, bundle);
  logEvent(summary.problems.length ? 'warn' : 'info', 'support', 'bundle', 'Diagnostics bundle collected', {
    file,
    durationMs: Date.now() - startedAt,
    problems: summary.problems,
    lines: summary.lines,
  });
  return { file, summary };
}

async function collectSupportBundleFromMenu() {
  try {
    const { file, summary } = await collectSupportBundle();
    const choice = await dialog.showMessageBox({
      type: summary.problems.length ? 'warning' : 'info',
      buttons: ['Open logs folder', 'OK'],
      defaultId: 1,
      cancelId: 1,
      title: `${APP_NAME} — diagnostics`,
      message: summary.problems.length ? 'Diagnostics found problems.' : 'Diagnostics look healthy.',
      detail: `${summary.lines.join('\n')}\n\n${summary.problems.join('\n')}\n\nBundle: ${file}`,
    });
    if (choice.response === 0) await shell.openPath(logsDir);
  } catch (error) {
    dialog.showErrorBox(
      `${APP_NAME} — diagnostics`,
      `Could not collect the diagnostics bundle: ${error && error.message ? error.message : error}`,
    );
  }
}

function buildMenu() {
  const status = currentStatus();
  const template = [
    {
      label: '&File',
      submenu: [
        { role: 'reload', label: '&Reload' },
        { role: 'forceReload', label: 'Force R&eload' },
        { role: 'toggleDevTools', label: '&Toggle Developer Tools' },
        { type: 'separator' },
        { label: '&Restart services', click: () => restartServices() },
        { type: 'separator' },
        { role: 'quit', label: `&Quit ${APP_NAME}` },
      ],
    },
    {
      label: '&Tools',
      submenu: [
        {
          label: lionPetController.isVisible() ? 'Hide &desktop lion' : 'Show &desktop lion',
          click: () => lionPetController.setVisible(!lionPetController.isVisible()),
        },
        {
          label: getAutoStartPrefLabel(),
          enabled: isPackaged,
          click: () => toggleAutoStartFromMenu(),
        },
        { type: 'separator' },
        { label: '&Open user-data folder', click: () => shell.openPath(userDataRoot) },
        { label: 'Open &logs folder', click: () => shell.openPath(logsDir) },
        { label: 'Collect &diagnostics bundle', click: () => collectSupportBundleFromMenu() },
        {
          label: 'Open &Gateway health check',
          enabled: Boolean(status.gatewayUrl),
          click: () => {
            if (status.gatewayUrl) openExternalSafely(`${status.gatewayUrl}/health`);
          },
        },
        {
          label: '&Check for updates',
          enabled: !updateAvailability().disabled,
          click: () => checkForUpdates({ manual: true }),
        },
        {
          label: '&Copy app URLs',
          click: () => {
            clipboard.writeText(
              `${APP_NAME} frontend: ${status.frontendUrl || '(none)'}\n${APP_NAME} gateway: ${status.gatewayUrl || '(none)'}\n`,
            );
          },
        },
      ],
    },
    {
      label: '&Help',
      submenu: [
        { label: `&About ${APP_NAME}`, click: () => showAbout() },
        {
          label: '&Service diagnostics',
          click: () => showServiceDiagnostics(),
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function showAbout() {
  const info = buildAppInfo({
    name: APP_NAME,
    version: app.getVersion(),
    electronVersion: process.versions.electron,
    chromeVersion: process.versions.chrome,
    nodeVersion: process.versions.node,
    packaged: isPackaged,
    userData: userDataRoot,
    frontendUrl,
    gatewayUrl,
  });
  dialog.showMessageBox({
    type: 'info',
    title: `About ${APP_NAME}`,
    message: `${APP_NAME} Desktop v${info.version}`,
    detail:
      `Frontend: ${info.frontendUrl || '(not running)'}\n` +
      `Gateway: ${info.gatewayUrl || '(not running)'}\n` +
      `Electron ${info.versions.electron} / Chrome ${info.versions.chrome} / Node ${info.versions.node}\n\n` +
      `User data: ${info.userData}`,
  });
}

/**
 * The one screen that answers "why is the app not working?".
 *
 * Each service reports its real state, its port, its pid, how many times it has
 * been restarted, and its actual failure reason. A state that is merely "not
 * healthy" is shown as such rather than as an error, and a service that has
 * never started says so.
 */
function showServiceDiagnostics() {
  const status = currentStatus();
  if (!status.services.length) {
    dialog.showMessageBox({
      type: 'info',
      title: `${APP_NAME} — service diagnostics`,
      message: 'No services are registered yet.',
      detail: 'The app has not finished starting. Check logs/main.log.',
    });
    return;
  }
  const lines = status.services.map((service) => {
    const parts = [`  ${service.label}: ${service.state}`];
    if (service.port) parts.push(`port ${service.port}`);
    if (service.pid) parts.push(`pid ${service.pid}`);
    if (service.restarts > 0) parts.push(`${service.restarts} restart(s)`);
    return parts.join(', ');
  });
  const failures = status.services.filter((service) => service.error);
  dialog.showMessageBox({
    type: failures.length ? 'warning' : 'info',
    title: `${APP_NAME} — service diagnostics`,
    message: failures.length ? 'One or more services are not healthy.' : 'All services are healthy.',
    detail:
      `${lines.join('\n')}` +
      (failures.length ? `\n\n${failures.map((s) => `${s.label}: ${s.error}`).join('\n')}` : '') +
      `\n\nLogs: ${logsDir}`,
  });
}

// ---------------------------------------------------------------------------
// Auto-start
// ---------------------------------------------------------------------------

function readAutoStartPref() {
  return readPreference(() => {
    try {
      return fs.readFileSync(autoStartPrefFile, 'utf8');
    } catch {
      return null;
    }
  });
}

function writeAutoStartPref(enabled) {
  try {
    fs.writeFileSync(autoStartPrefFile, serializePreference(enabled), 'utf8');
  } catch (error) {
    log(`Warning: could not persist auto-start preference: ${error.message}`);
  }
}

function getAutoStartState() {
  const preferenceEnabled = readAutoStartPref();
  let osActive = false;
  try {
    osActive = Boolean(app.getLoginItemSettings().openAtLogin);
  } catch {
    osActive = false;
  }
  return describeState({ isPackaged, preferenceEnabled, osActive });
}

function getAutoStartPrefLabel() {
  const state = getAutoStartState();
  if (!state.supported) return 'Start with &Windows (installed app only)';
  return state.enabled ? 'Do not start with &Windows' : 'Start with &Windows';
}

/**
 * Windows always-on: register or clear the login item for this app.
 *
 * Installed-app only. In a source checkout the executable is the bare Electron
 * binary, so a login entry would launch without the app — that case is refused
 * with an explanation rather than writing a broken startup entry.
 *
 * @returns {boolean} the OS-reported state after applying
 */
function applyAutoStartSetting(enabled) {
  const toggle = decideToggle({ isPackaged, desiredEnabled: enabled });
  if (!toggle.ok) throw new Error(toggle.message);
  app.setLoginItemSettings({ openAtLogin: Boolean(enabled) });
  writeAutoStartPref(enabled);
  let actual = false;
  try {
    actual = Boolean(app.getLoginItemSettings().openAtLogin);
  } catch {
    actual = Boolean(enabled);
  }
  log(`Start with Windows ${actual ? 'enabled' : 'disabled'}`);
  buildMenu();
  return actual;
}

function toggleAutoStartFromMenu() {
  const current = getAutoStartState();
  if (!current.supported) {
    dialog.showMessageBox({
      type: 'info',
      title: `${APP_NAME} — Start with Windows`,
      message: 'Not available in this build.',
      detail: current.reason || '',
    });
    return;
  }
  try {
    applyAutoStartSetting(!current.enabled);
  } catch (error) {
    dialog.showErrorBox(`${APP_NAME} — Start with Windows`, error.message);
  }
}

/**
 * Reconcile the OS login item with the stored preference at boot.
 *
 * The preference is the source of truth. A login entry that disappeared (cleaner
 * software, group policy, a user in Task Scheduler) is re-registered, because
 * losing it silently is indistinguishable from the user turning it off; a
 * leftover entry after turning it off is cleared.
 */
function reconcileAutoStart() {
  const preferenceEnabled = readAutoStartPref();
  let osActive = false;
  try {
    osActive = Boolean(app.getLoginItemSettings().openAtLogin);
  } catch {
    osActive = false;
  }
  const decision = decideBootReconcile({ isPackaged, preferenceEnabled, osActive });
  if (decision.action === 'none') return decision;
  try {
    app.setLoginItemSettings({ openAtLogin: decision.action === 'register' });
    log(`Start with Windows: ${decision.action}`, decision.reason);
  } catch (error) {
    // Best-effort and never fatal: a machine that forbids login items can still
    // run the app perfectly well from a shortcut.
    log(`Warning: could not ${decision.action} the login item: ${error.message}`);
  }
  return decision;
}

// ---------------------------------------------------------------------------
// Updates
// ---------------------------------------------------------------------------

/**
 * Is a feed actually configured?
 *
 * Read from `desktop-config.json -> updateFeed`, whose values must match the
 * `publish:` block in electron-builder.yml: that block is what makes
 * electron-builder emit `latest.yml`, and this is what makes the client look for
 * it. If they drift, the app reports "no feed" and silently never updates —
 * which is why `scripts/verify-build.mjs` compares them.
 */
function updateAvailability() {
  const feed = DESKTOP_CONFIG.updateFeed;
  const configured = Boolean(feed && feed.provider && feed.owner && feed.repo);
  return decideAvailability({
    isPackaged,
    publishConfigured: configured,
    forcedDisabled: !args.updates,
  });
}

function broadcastUpdateState() {
  for (const win of BrowserWindow.getAllWindows()) {
    try {
      win.webContents.send(EVENTS.updateState, updateState);
    } catch {
      // Ignore.
    }
  }
}

function setUpdateState(next) {
  updateState = { ...updateState, ...next };
  logEvent(
    updateState.state === UPDATE_STATE.error ? 'error' : updateState.state === UPDATE_STATE.downloading ? 'debug' : 'info',
    'updater',
    'state',
    updateState.message || `Update state: ${updateState.state}`,
    updateState,
  );
  broadcastUpdateState();
  buildMenu();
}

/**
 * Check the update feed, subject to the cadence policy.
 *
 * The feed is read through `electron-updater` when it is present. It is an
 * optional dependency so a source checkout and a packaged build without a
 * configured feed both run without it.
 */
/**
 * The `electron-updater` singleton, or null when it is not usable.
 *
 * Resolved once and cached, because constructing it touches the Electron app
 * adapter and a throw there must not become a per-check failure. The require is
 * guarded because `electron-updater` is a production dependency but the desktop
 * shell is also loaded in contexts (tests, tooling) where it is absent.
 *
 * @returns {object|null}
 */
let cachedUpdater;
function loadAutoUpdater() {
  if (cachedUpdater !== undefined) return cachedUpdater;
  try {
    // eslint-disable-next-line global-require
    const { autoUpdater } = require('electron-updater');
    // Route electron-updater's own logging through our appender so an update
    // failure lands in main.log instead of only the console.
    autoUpdater.logger = createUpdaterLogger();
    cachedUpdater = autoUpdater;
  } catch (error) {
    log(`The updater is unavailable: ${error && error.message ? error.message : error}`);
    cachedUpdater = null;
  }
  return cachedUpdater;
}

function createUpdaterLogger() {
  // electron-updater wants an object with {info, warn, error, debug}. Mapping it
  // onto our `log()` is what makes "why did my update fail" answerable from
  // main.log rather than only from a console nobody sees in production.
  return {
    info: (message) => log(`updater: ${message}`),
    warn: (message) => log(`updater: ${message}`),
    error: (message) => log(`updater: ${message}`),
    debug: (message) => log(`updater: ${message}`),
  };
}

async function checkForUpdates({ manual = false } = {}) {
  const availability = updateAvailability();
  if (availability.disabled) {
    setUpdateState({ state: UPDATE_STATE.disabled, message: availability.reason });
    return { state: UPDATE_STATE.disabled, message: availability.reason, version: null, releaseNotes: null };
  }

  const decision = decideCheck({ lastCheckAt, now: Date.now(), startupAt });
  if (!decision.check && !manual) {
    return { state: updateState.state, skipped: true, reason: decision.reason };
  }

  setUpdateState({ state: UPDATE_STATE.checking, message: 'Checking for updates…' });

  const updater = loadAutoUpdater();
  if (!updater) {
    setUpdateState({
      state: UPDATE_STATE.disabled,
      message:
        'The updater is not bundled in this build, so no update check is possible. ' +
        'Download a new version manually.',
    });
    return { state: UPDATE_STATE.disabled, message: 'updater not bundled', version: null, releaseNotes: null };
  }

  try {
    const result = await updater.checkForUpdates();
    lastCheckAt = Date.now();
    writeJsonFile(updateStateFile, { lastCheckAt });
    const interpreted = interpretCheckResult(result, app.getVersion());
    setUpdateState(interpreted);
    return interpreted;
  } catch (error) {
    lastCheckAt = Date.now();
    // An unreadable feed is reported as unavailable, never as up to date.
    const interpreted = interpretCheckResult(null, app.getVersion());
    setUpdateState({
      ...interpreted,
      message: `${interpreted.message} (${error && error.message ? error.message : error})`,
    });
    return interpreted;
  }
}

async function installUpdate() {
  const availability = updateAvailability();
  if (availability.disabled) {
    return { state: UPDATE_STATE.disabled, message: availability.reason };
  }
  if (updateState.state !== UPDATE_STATE.ready && updateState.state !== UPDATE_STATE.available) {
    return { state: updateState.state, message: 'no downloaded update to install' };
  }
  const autoUpdater = loadAutoUpdater();
  if (!autoUpdater) {
    return { state: UPDATE_STATE.disabled, message: 'the updater is not available in this build' };
  }
  try {
    // `isSilent=false` so the user sees the installer rather than the app
    // vanishing; `isForceRunAfter=true` so the update actually takes effect
    // instead of waiting for the next manual launch.
    autoUpdater.quitAndInstall(false, true);
    return { state: UPDATE_STATE.ready, message: 'restarting to install' };
  } catch (error) {
    const message = `could not install the update: ${error && error.message ? error.message : error}`;
    setUpdateState({ state: UPDATE_STATE.error, message });
    return { state: UPDATE_STATE.error, message };
  }
}

function scheduleUpdateCheck() {
  const availability = updateAvailability();
  if (availability.disabled) {
    log('Auto-update not active', availability.reason);
    return;
  }
  const stored = readJsonFile(updateStateFile);
  if (stored && Number.isFinite(stored.lastCheckAt)) lastCheckAt = stored.lastCheckAt;

  const timer = setInterval(
    () => {
      checkForUpdates().catch((error) => log(`Update check failed: ${error && error.message}`));
    },
    60 * 60 * 1000,
  );
  // The interval must never be the reason the app cannot exit.
  if (timer.unref) timer.unref();

  // One check shortly after launch, so a fresh install hears about updates
  // without waiting six hours.
  const first = setTimeout(
    () => {
      checkForUpdates().catch((error) => log(`Update check failed: ${error && error.message}`));
    },
    60 * 1000,
  );
  if (first.unref) first.unref();

  if (autoUpdaterOnEvent) autoUpdaterOnEvent();
}

/**
 * Wire the updater's progress events into the renderer-facing state.
 *
 * Downloads are automatic once a check finds a version, but installation is
 * never automatic on quit: a user mid-conversation should not have the app swap
 * out from under them. The UI offers the restart.
 */
function autoUpdaterOnEvent() {
  const autoUpdater = loadAutoUpdater();
  if (!autoUpdater) return;
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.on('update-downloaded', (info) => {
    setUpdateState({
      state: UPDATE_STATE.ready,
      version: info && info.version ? info.version : null,
      message: `Alpha ${info && info.version ? info.version : ''} is ready to install. Restart to apply.`,
    });
  });
  autoUpdater.on('download-progress', (progress) => {
    setUpdateState({
      state: UPDATE_STATE.downloading,
      message: `Downloading update: ${Math.round(progress.percent || 0)}%`,
    });
  });
  autoUpdater.on('error', (error) => {
    setUpdateState({
      state: UPDATE_STATE.error,
      message: `Update error: ${error && error.message ? error.message : error}`,
    });
  });
}

// ---------------------------------------------------------------------------
// Service control
// ---------------------------------------------------------------------------

/** Restart both services on user request, preserving the chosen ports. */
async function restartServices() {
  if (!supervisor) {
    dialog.showMessageBox({
      type: 'info',
      title: `${APP_NAME} — restart services`,
      message: 'No services are running yet.',
      detail: 'The app is still starting. Try again once the window is open.',
    });
    return;
  }
  const ports = { gateway: supervisor.port('gateway'), frontend: supervisor.port('frontend') };
  broadcastStatus('Restarting services…', 'the window will reconnect automatically');
  await supervisor.stop();
  supervisor = buildSupervisor();
  try {
    await supervisor.start({ gatewayPort: ports.gateway, frontendPort: ports.frontend });
    gatewayUrl = ports.gateway ? `http://127.0.0.1:${ports.gateway}` : gatewayUrl;
    frontendUrl = ports.frontend ? `http://127.0.0.1:${ports.frontend}` : frontendUrl;
    broadcastStatus('Services restarted', '');
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.reload();
  } catch (error) {
    broadcastStatus('Could not restart the services', error && error.message);
    dialog.showErrorBox(
      `${APP_NAME} — restart failed`,
      `${error && error.message ? error.message : error}\n\nSee ${logsDir} for details.`,
    );
  }
}

/**
 * Probe a service while recording why the supervisor believes it is up or down.
 *
 * Health checks are debug-level by design: during a ten-minute first launch the
 * Gateway is legitimately unhealthy for hundreds of polls. The transitions
 * themselves remain info-level in the supervisor state log.
 */
async function probeWithLogging(service, port, probeFn) {
  const startedAt = Date.now();
  try {
    const healthy = Boolean(await probeFn({ port }));
    logEvent('debug', service, 'probe', healthy ? 'Service answered its health probe' : 'Service did not answer yet', {
      port,
      healthy,
      durationMs: Date.now() - startedAt,
    });
    return healthy;
  } catch (error) {
    logEvent('debug', service, 'probe-error', 'Service probe failed', {
      port,
      durationMs: Date.now() - startedAt,
      error: error && error.message ? error.message : String(error),
    });
    return false;
  }
}

/**
 * Build the supervisor with the real spawners and probes.
 *
 * The supervisor itself is Electron-free and unit-tested; this function is the
 * only place the two are joined.
 */
/**
 * Terminate a supervised child as a verified tree, not just its wrapper.
 *
 * A failed Gateway start previously called `uv.kill()`, which exited while the
 * provisioned Python/uvicorn grandchildren kept listening on 8201. The next
 * launch then found a port it did not own. Waiting for the tree to be gone is
 * what makes "teardown" true.
 */
async function terminateServiceTree(key, child) {
  if (!child) return;
  if (!child.pid) {
    try {
      child.kill();
    } catch (error) {
      logEvent('warn', key, 'kill-failed', `Could not terminate ${key} without a pid`, {
        error: error && error.message ? error.message : String(error),
      });
    }
    return;
  }
  const result = await killProcessTree(child.pid, {
    log: (message) => logEvent('info', key, 'tree-kill', message, { pid: child.pid }),
    isAlive: childIsAlive,
  });
  if (!result.stopped) {
    logEvent('error', key, 'tree-kill-failed', `The ${key} process tree survived termination`, {
      pid: child.pid,
      escalated: result.escalated,
    });
  }
  if (children[key] === child) children[key] = null;
}

function buildSupervisor() {
  const supervisorInstance = new ServiceSupervisor({
    log: (line) => log(line),
    terminate: (child, info) => terminateServiceTree(info && info.key, child),
    onStateChange: broadcastServiceState,
    services: [
      {
        key: 'gateway',
        label: 'Gateway',
        // Derived from the port decision, not just the flag: a verified Alpha
        // Gateway already on the port is reused, never also spawned.
        managed: decisions.gateway ? decisions.gateway.action === 'spawn' : !args.skipBackend,
        // A first launch provisions Python and ~200 wheels, so the startup
        // deadline is generous; a restart off a warm cache is quick. These are
        // supervisor timings, not restart-policy fields: passing them inside
        // `policy` silently discarded them and left the five-minute default.
        timings: { startupTimeoutMs: 600000 },
        policy: { minHealthyUptimeMs: 30000 },
        spawnService: ({ port }) => spawnBackend(port),
        probe: ({ port }) => probeWithLogging('gateway', port, () => isAlphaGateway(`http://127.0.0.1:${port}`)),
      },
      {
        key: 'frontend',
        label: 'Frontend',
        dependsOn: ['gateway'],
        managed: decisions.frontend ? decisions.frontend.action === 'spawn' : !args.skipFrontend,
        timings: { startupTimeoutMs: 300000 },
        policy: { minHealthyUptimeMs: 30000 },
        spawnService: ({ port }) => {
          const nodeExe = resolveNode();
          if (!nodeExe) {
            throw new Error(
              'Node.js 22+ not found. Install the LTS release from https://nodejs.org/ ' +
                '(or `winget install OpenJS.NodeJS.LTS`), then restart the app.',
            );
          }
          if (args.dev && !isPackaged) return spawnFrontendDev(nodeExe, port, gatewayUrl);
          return spawnFrontendProd(nodeExe, port, gatewayUrl);
        },
        probe: ({ port }) => probeWithLogging('frontend', port, () => isAlphaFrontend(`http://127.0.0.1:${port}`)),
      },
    ],
  });
  logEvent('debug', 'supervisor', 'configured', 'Service supervision configured', {
    services: supervisorInstance.services.map((service) => ({
      key: service.key,
      managed: service.managed,
      dependsOn: service.dependsOn,
      timings: service.timings,
      restartBudget: {
        maxRestartsInWindow: service.policy.maxRestartsInWindow,
        restartWindowMs: service.policy.restartWindowMs,
        minHealthyUptimeMs: service.policy.minHealthyUptimeMs,
      },
    })),
  });
  return supervisorInstance;
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

/**
 * Warn when a machine-wide production marker would re-enable the login screen.
 *
 * Both services ignore ALPHA_AUTH_DISABLED in an explicit production
 * environment, so the app would open on a login form the user did not ask for.
 */
async function warnIfProdEnvDisablesDirectOpen() {
  if (args.requireLogin) return;
  const value = (process.env.ALPHA_ENV || process.env.ENVIRONMENT || '').trim().toLowerCase();
  if (value !== 'prod' && value !== 'production') return;
  log('Warning: ALPHA_ENV/ENVIRONMENT marks production; services will ignore ALPHA_AUTH_DISABLED');
  const choice = await dialog.showMessageBox({
    type: 'warning',
    buttons: ['Continue anyway', 'Quit'],
    defaultId: 0,
    cancelId: 1,
    title: `${APP_NAME} — production environment detected`,
    message: 'This machine declares a production environment.',
    detail:
      'ALPHA_ENV (or ENVIRONMENT) is set to a production value, so the ' +
      'Gateway and frontend will IGNORE the desktop single-user mode and show ' +
      'the login screen.\n\nUnset that variable (or start with --require-login) ' +
      'to keep the direct-open behavior.',
  });
  if (choice.response === 1) {
    appQuitting = true;
    app.quit();
    throw new Error('Quit by user (production environment marker present)');
  }
}

/**
 * First-run guidance when the Gateway reports zero configured models.
 *
 * Chatting will otherwise fail with a provider error. Advisory only, and it runs
 * after the window is open so it never delays first paint.
 */
async function warnIfNoModels(baseUrl) {
  const data = await httpGetJson(`${baseUrl}/api/models`, 10000);
  if (!data || !Array.isArray(data.models) || data.models.length > 0) return;
  log('No AI models configured; showing first-run guidance');
  const choice = await dialog.showMessageBox({
    type: 'info',
    buttons: ['Open config folder', 'Later'],
    defaultId: 0,
    cancelId: 1,
    title: `${APP_NAME} — add an AI model to get started`,
    message: 'No AI models are configured yet.',
    detail:
      `Add at least one model (API key) to this file, then restart ${APP_NAME}:\n` +
      `${path.join(projectDir, 'config.yaml')}\n\n` +
      'See the models section in config.example.yaml for provider examples.',
  });
  if (choice.response === 0) await shell.openPath(projectDir);
}

async function boot() {
  startupAt = Date.now();

  // Dirs and logging come FIRST, so the error report for anything below can
  // point at a log file that exists. The original ran diagnostics before
  // opening the log, so its most likely failure pointed at a missing file.
  ensureDir(projectDir);
  ensureDir(alphaHomeDir);
  ensureDir(logsDir);
  appLogger.setLevel(args.logLevel);
  logEvent('info', 'startup', 'session', `--- ${APP_NAME} Desktop v${app.getVersion()} started (${new Date().toISOString()}) ---`, {
    sessionId: appLogger.sessionId,
    logLevel: appLogger.level,
    arguments: sanitizeArguments(args),
    runtime: {
      platform: process.platform,
      arch: process.arch,
      packaged: isPackaged,
      executable: process.execPath,
      resourcesRoot,
      versions: {
        app: app.getVersion(),
        electron: process.versions.electron,
        chrome: process.versions.chrome,
        node: process.versions.node,
      },
    },
    directories: {
      project: describePath(projectDir),
      home: describePath(alphaHomeDir),
      logs: describePath(logsDir),
    },
    logFiles: appLogger.paths,
  });

  reconcileAutoStart();
  try {
    if (app.getLoginItemSettings().wasOpenedAtLogin) log('Opened at login (Windows startup)');
  } catch (error) {
    log(`Warning: could not read login-item state: ${error.message}`);
  }

  log(`Mode: ${args.dev ? 'development' : 'production'}${isPackaged ? ' (packaged)' : ' (from sources)'}`);
  log(
    args.requireLogin
      ? 'Auth: login/setup screens enabled (--require-login)'
      : 'Auth: local single-user mode, app opens directly (ALPHA_AUTH_DISABLED=1)',
  );

  runStartupDiagnostics();
  await warnIfProdEnvDisablesDirectOpen();

  seedFileIfMissing(path.join(configTemplatesDir, 'config.example.yaml'), path.join(projectDir, 'config.yaml'));
  seedFileIfMissing(
    path.join(configTemplatesDir, 'extensions_config.example.json'),
    path.join(projectDir, 'extensions_config.json'),
  );
  broadcastStatus('Preparing local data directory…', projectDir);

  // Port selection fills in the module-level `decisions` object declared next to
  // the single-instance lock, which is what derives each service's `managed`
  // flag when the supervisor is built.
  if (args.frontendUrl) {
    frontendUrl = args.frontendUrl;
    log(`Using explicit frontend URL (no spawn): ${frontendUrl}`);
    decisions.gateway = await decideServicePort({
      preferredPort: args.gatewayPort,
      probeIdentity: (port) => isAlphaGateway(loopbackBase(port)),
      findFreePort,
      skip: args.skipBackend,
      forcedPort: args.forcedPorts.gateway,
    });
    gatewayUrl = loopbackBase(decisions.gateway.port);
    logEvent('info', 'boot', 'gateway-port', `Gateway: ${decisions.gateway.reason}`, decisions.gateway);
    // The frontend was named explicitly, so it is never managed.
    decisions.frontend = { action: 'skip', managed: false, port: null, reason: 'an explicit frontend URL was given' };
  } else {
    const result = await runBootSequence({
      log: (line) => log(line),
      context: {},
      phases: [
        {
          name: 'gateway',
          run: async () => {
            const decision = await decideServicePort({
              preferredPort: args.gatewayPort,
              probeIdentity: (port) => isAlphaGateway(loopbackBase(port)),
              findFreePort,
              skip: args.skipBackend,
              forcedPort: args.forcedPorts.gateway,
            });
            decisions.gateway = decision;
            gatewayUrl = loopbackBase(decision.port);
            logEvent('info', 'boot', 'gateway-port', `Gateway: ${decision.reason}`, decision);
            if (decision.action === 'spawn') {
              broadcastStatus('Starting Gateway API…', `port ${decision.port}`);
            } else if (decision.action === 'skip') {
              // Say it out loud: attach mode with nothing to attach to produces
              // a window whose every API call fails, and that is not a state the
              // user should discover by trying to chat.
              broadcastStatus('No Gateway found — the app will open without a backend', decision.reason);
            }
          },
        },
        {
          name: 'frontend',
          run: async () => {
            const decision = await decideServicePort({
              preferredPort: args.frontendPort,
              probeIdentity: (port) => isAlphaFrontend(loopbackBase(port)),
              findFreePort,
              skip: args.skipFrontend,
              forcedPort: args.forcedPorts.frontend,
            });
            decisions.frontend = decision;
            frontendUrl = loopbackBase(decision.port);
            logEvent('info', 'boot', 'frontend-port', `Frontend: ${decision.reason}`, decision);
            if (decision.action === 'spawn') {
              broadcastStatus(
                args.dev && !isPackaged ? 'Starting frontend (dev)…' : 'Starting frontend…',
                `port ${decision.port}`,
              );
            }
          },
        },
      ],
    });
    if (!result.ok) {
      throw new Error(`${result.failed.name}: ${result.failed.error}`);
    }
  }

  await warmBackendEnvironment();

  configureContentSecurityPolicy(frontendUrl, gatewayUrl);

  // Bring the services up through the supervisor, so a service that dies later
  // is restarted instead of leaving a permanently broken window.
  supervisor = buildSupervisor();
  broadcastStatus('Starting services…', '');
  const servicesTimer = appLogger.time('supervisor', 'start', { gatewayUrl, frontendUrl });
  try {
    await supervisor.start({
      gatewayPort: gatewayUrl ? Number(new URL(gatewayUrl).port) : undefined,
      frontendPort: frontendUrl ? Number(new URL(frontendUrl).port) : undefined,
    });
    servicesTimer('All services healthy', { gatewayUrl, frontendUrl, services: supervisor.status() });
    log('All services healthy', `${gatewayUrl} | ${frontendUrl}`);
  } catch (error) {
    // The supervisor has already recorded the per-service failure; this adds
    // the boot-level context the user needs.
    const detail = supervisor
      .status()
      .filter((service) => service.error)
      .map((service) => `${service.label}: ${service.error}`)
      .join('\n');
    error.message = `${error.message}${detail ? `\n\n${detail}` : ''}`;
    throw error;
  }

  buildMenu();
  broadcastStatus(`Opening ${APP_NAME}…`, frontendUrl);
  createMainWindow(resolveStartUrl(frontendUrl));
  if (args.showLionPet) lionPetController.setVisible(true);

  scheduleUpdateCheck();
  // Advisory first-run check; never blocks the UI.
  if (!args.skipBackend) {
    warnIfNoModels(gatewayUrl).catch((error) => log(`Model pre-flight check failed: ${error.message}`));
  }
}

// ---------------------------------------------------------------------------
// IPC
// ---------------------------------------------------------------------------

/**
 * Register every channel from the single route table.
 *
 * A table rather than repeated `ipcMain.handle` calls because a duplicate
 * registration throws at module load and Electron exits 0 with no window, no
 * splash, and no log line — the app simply appeared to do nothing. `main.js` did
 * have that bug: four channels registered twice each.
 *
 * Every handler also validates its sender, because the preload bridge is
 * privileged and the app now refuses non-Alpha navigations: only the main window
 * and the companion may call in.
 */
function registerIpcRoutes() {
  assertUniqueChannels(ROUTES);

  // The splash is a trusted sender too: it renders boot progress from
  // `alpha:status` and `getStatus()`, and it exists precisely because the main
  // window has not been created yet. Without it, every splash status call is
  // refused and the boot screen silently shows nothing.
  const trustedSenders = () => {
    const senders = [];
    if (splashWindow && !splashWindow.isDestroyed()) senders.push(splashWindow.webContents);
    if (mainWindow && !mainWindow.isDestroyed()) senders.push(mainWindow.webContents);
    const petWindow = lionPetController.window;
    if (petWindow && !petWindow.isDestroyed()) senders.push(petWindow.webContents);
    return senders;
  };

  const isTrustedSender = (event) => trustedSenders().includes(event.sender);

  const guard = (channel) => (handler) => async (event, ...handlerArgs) => {
    const startedAt = Date.now();
    if (!isTrustedSender(event)) {
      logEvent('warn', 'ipc', 'rejected', 'Refused an IPC call from an unrecognized sender', { channel });
      throw new Error('This IPC channel is not available to this window.');
    }
    try {
      const result = await handler(event, ...handlerArgs);
      logEvent('debug', 'ipc', 'completed', `IPC ${channel} completed`, {
        channel,
        durationMs: Date.now() - startedAt,
      });
      return result;
    } catch (error) {
      logEvent('warn', 'ipc', 'failed', `IPC ${channel} failed`, {
        channel,
        durationMs: Date.now() - startedAt,
        error: error && error.message ? error.message : String(error),
      });
      throw error;
    }
  };

  const rawHandlers = {
    [CHANNELS.status]: () => currentStatus(),
    [CHANNELS.openUserData]: async () => shell.openPath(userDataRoot),
    [CHANNELS.getAutoStart]: () => getAutoStartState(),
    [CHANNELS.setAutoStart]: async (_event, enabled) => applyAutoStartSetting(enabled),
    [CHANNELS.getAppInfo]: () =>
      buildAppInfo({
        name: APP_NAME,
        version: app.getVersion(),
        electronVersion: process.versions.electron,
        chromeVersion: process.versions.chrome,
        nodeVersion: process.versions.node,
        packaged: isPackaged,
        userData: userDataRoot,
        frontendUrl,
        gatewayUrl,
      }),
    [CHANNELS.checkForUpdates]: () => checkForUpdates({ manual: true }),
    [CHANNELS.installUpdate]: () => installUpdate(),
    [CHANNELS.restartServices]: () => restartServices(),
    [CHANNELS.openLogFolder]: () => shell.openPath(logsDir),
    [CHANNELS.lionPetState]: (event, payload) => lionPetController.handleState(event.sender, payload),
    [CHANNELS.lionPetPerform]: (event, action) => lionPetController.handleAction(event.sender, action),
    [CHANNELS.lionPetVisible]: (event, visible) => lionPetController.handleVisibility(event.sender, visible),
  };

  for (const route of ROUTES) {
    const rawHandler = rawHandlers[route.channel];
    if (typeof rawHandler !== 'function') {
      throw new Error(`Route ${route.channel} has no handler in main.js`);
    }
    const handler = guard(route.channel)(rawHandler);
    if (route.kind === 'invoke') {
      ipcMain.handle(route.channel, handler);
    } else {
      ipcMain.on(route.channel, async (event, payload) => {
        // Awaited here so an asynchronous sender failure is logged by the
        // guard instead of becoming an unhandled rejection.
        await handler(event, payload);
      });
    }
  }
  log(`IPC routes registered: ${sendChannels().length} send, ${Object.keys(rawHandlers).length - sendChannels().length} invoke`);
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Child handle table
// ---------------------------------------------------------------------------

/**
 * Live child handles, keyed by service.
 *
 * The shutdown ladder stops services through the supervisor, but the final
 * last-resort kill works from raw pids, and `killProcessTree` waits for the
 * process to actually be gone. Without this table there is no pid to verify,
 * which is how an orphan used to survive a quit holding a port.
 */
const children = { frontend: null, gateway: null };

function recordChild(key, child) {
  children[key] = child;
  return child;
}

/** The `windowsHide`/`detached` spawn plus log capture, per service. */
function startChild(key, spawnFn, logFile) {
  const startedAt = Date.now();
  const child = spawnFn();
  const service = appLogger.service(key);
  service.info('spawned', 'Child process spawned', { pid: child.pid, logFile });
  if (child && typeof child.once === 'function') {
    child.once('exit', (code, signal) => {
      service.info('exit', 'Child process exited', {
        pid: child.pid,
        code,
        signal,
        durationMs: Date.now() - startedAt,
        logFile,
        shuttingDown: appQuitting,
      });
    });
    child.once('error', (error) => {
      service.error('spawn-error', 'Child process reported a spawn error', {
        pid: child.pid,
        error: error && error.message ? error.message : String(error),
        durationMs: Date.now() - startedAt,
        logFile,
      });
    });
  }
  attachChildLogging(child, key, {
    logFile,
    maxLogBytes: MAX_CHILD_LOG_BYTES,
    verbose: args.verbose,
    log,
    isShuttingDown: () => appQuitting,
  });
  return recordChild(key, child);
}

/**
 * Per-service port decisions, filled in during boot.
 *
 * `managed` for each supervised service is DERIVED from these, not from the CLI
 * flags alone. A service whose port already serves a verified Alpha instance is
 * reused and must not also be spawned — otherwise boot prints "reusing" and then
 * starts a second Gateway on that same port, leaving two Gateways contending
 * over one state directory.
 */
const decisions = { gateway: null, frontend: null };

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  // Windows needs an explicit AppUserModelID for the taskbar to group the app
  // correctly and for notifications to carry the app's identity. Without it the
  // window groups under "Electron" and toasts show the wrong name.
  app.setAppUserModelId(APP_ID);

  app.on('second-instance', (_event, argv) => {
    // The argv of the second launch is forwarded, so `--show-lion-pet` or a
    // different `--frontend-url` actually takes effect instead of being dropped.
    const forwarded = parseArgs(argv || []);
    if (forwarded.showLionPet && !args.showLionPet) lionPetController.setVisible(true);
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  // Chromium writes its own crash dumps; pointing them at the user-data folder
  // keeps them out of the install directory, which may be read-only.
  try {
    crashReporter.start({
      productName: APP_NAME,
      companyName: 'Alpha',
      // Uploads are OFF: this build has no crash-collection endpoint, and
      // silently POSTing crash data to a default URL would be worse than not
      // collecting it.
      uploadToServer: false,
      compress: false,
      crashDirectory: path.join(userDataRoot, 'crash-dumps'),
    });
  } catch (error) {
    log(`Warning: crash reporter unavailable: ${error.message}`);
  }

  // An uncaught exception in the main process would otherwise terminate the app
  // with no dialog and no log line, which is the "it just closed" class of bug.
  process.on('uncaughtException', (error) => {
    logEvent('error', 'app', 'uncaught-exception', `Uncaught exception in the main process: ${error && error.message ? error.message : error}`, {
      error: error && error.stack ? error.stack : String(error),
    });
    log(`Uncaught exception in the main process: ${error && error.stack ? error.stack : error}`);
    try {
      dialog.showErrorBox(
        `${APP_NAME} — unexpected error`,
        `${error && error.message ? error.message : error}\n\nThe app will close. See ${mainLogFile} for details.`,
      );
    } catch {
      // The dialog can fail during teardown; the log line above is the record.
    }
    app.exit(1);
  });
  process.on('unhandledRejection', (reason) => {
    logEvent('error', 'app', 'unhandled-rejection', 'Unhandled rejection in the main process', {
      reason: reason && reason.stack ? reason.stack : String(reason),
    });
    log(`Unhandled rejection in the main process: ${reason && reason.stack ? reason.stack : reason}`);
  });

  app.whenReady().then(() => {
    registerIpcRoutes();
    createSplash();
    boot().catch((error) => {
      const message = error && error.message ? error.message : String(error);
      logEvent('error', 'startup', 'failed', `Startup failed: ${message}`, {
        error: message,
        stack: error && error.stack ? error.stack : null,
        gatewayUrl,
        frontendUrl,
        services: supervisor ? supervisor.status() : [],
      });
      log(`Startup failed: ${message}`);
      try {
        if (splashWindow) splashWindow.close();
      } catch {
        // Ignore.
      }
      if (!appQuitting) {
        dialog.showErrorBox(describeBootFailure(null, APP_NAME), `${message}\n\nSee ${mainLogFile} for details.`);
      }
      app.quit();
    });
  });

  app.on('window-all-closed', () => {
    // Closing the window stops the local services too, so no orphan
    // Python/Node processes are left behind on Windows.
    app.quit();
  });

  /**
   * Ordered, bounded, verified shutdown.
   *
   * The original fired `taskkill /T /F` at both trees in one burst: unordered,
   * so the frontend could be killed mid-request, and unverified, so a process
   * that ignored it stayed alive holding a port. Now the frontend stops first so
   * it can drain, each step has a deadline, the whole thing is bounded, and a
   * forced kill is reported rather than described as a clean exit.
   */
  app.on('before-quit', (event) => {
    if (shuttingDown) return;
    event.preventDefault();
    appQuitting = true;
    shuttingDown = true;
    lionPetController.dispose();

    const services = supervisor ? supervisor.status() : [];
    logEvent('info', 'shutdown', 'started', 'Shutdown requested', { services });
    runShutdown({
      totalTimeoutMs: 15000,
      log,
      steps: buildDesktopShutdownSteps({
        frontend: {
          stop: () => teardownService('frontend'),
          force: () => teardownService('frontend', true),
        },
        gateway: {
          stop: () => teardownService('gateway'),
          force: () => teardownService('gateway', true),
        },
        onRendererTeardown: () => {
          if (splashWindow && !splashWindow.isDestroyed()) splashWindow.destroy();
        },
        onLogFinal: () => {
          const detail = services
            .map((service) => `${service.key}=${service.state}${service.restarts ? ` restarts=${service.restarts}` : ''}`)
            .join(' ');
          try {
            fs.appendFileSync(
              mainLogFile,
              `--- shutdown complete ${new Date().toISOString()} ${detail} ---\n`,
              'utf8',
            );
          } catch {
            // Ignore.
          }
        },
      }),
      onReport: (report) => {
        logEvent(report.clean ? 'info' : 'warn', 'shutdown', 'report', report.clean ? 'Shutdown completed cleanly' : 'Shutdown was not clean', {
          clean: report.clean,
          forced: report.forced,
          abandoned: report.abandoned,
          steps: report.steps,
          services,
        });
        if (!report.clean) {
          log('Shutdown was not clean', `forced=${report.forced.join(',')} abandoned=${report.abandoned.join(',')}`);
        } else {
          log('Shutdown completed cleanly');
        }
      },
    }).finally(() => {
      // Verified last resort for anything the ladder could not stop.
      // `killProcessTree` waits for the process to actually be gone, so a
      // survivor reported here is a real orphan, not an unconfirmed kill.
      Promise.all(
        Object.entries(children).map(async ([key, child]) => {
          if (!child || !child.pid) return;
          const result = await killProcessTree(child.pid, { log, isAlive: childIsAlive });
          if (!result.stopped) log(`WARNING: ${key} (pid ${child.pid}) survived the shutdown`);
          children[key] = null;
        }),
      ).finally(() => app.exit(0));
    });
  });
}

/** Liveness for a pid we spawned: its handle is authoritative. */
function childIsAlive(pid) {
  for (const child of Object.values(children)) {
    if (child && child.pid === pid && !isChildDead(child)) return true;
  }
  // Not one of ours (or already reaped): defer to the generic check.
  return defaultIsAlive(pid);
}

/**
 * Stop one supervised service.
 *
 * Returns undefined rather than a value so the shutdown ladder reads a resolved
 * step as success; `force` skips the graceful `stop()` and kills immediately.
 */
function teardownService(key, force = false) {
  if (!supervisor) return undefined;
  const record = supervisor.records.get(key);
  if (!record) return undefined;
  const child = record.handle;
  const teardown = supervisor.teardown(record);
  if (!force || !child || !child.pid) return teardown;
  return Promise.resolve(teardown).then(() => killProcessTree(child.pid, { force: true, log }));
}