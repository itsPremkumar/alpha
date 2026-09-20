'use strict';

/**
 * Alpha Desktop — Electron main process.
 *
 * Windows-stable orchestration for the Alpha stack:
 *   Gateway API (FastAPI/uvicorn, default `127.0.0.1:8201`) + Next.js frontend
 *   (default `127.0.0.1:3000`) inside a single native window. No nginx is used
 *   here: the Next.js server rewrites /api/* directly to the Gateway
 *   (see frontend/next.config.js), so only the two processes below are required.
 *
 * Modes:
 *   electron . --dev                 Hot-reload dev servers (Next.js dev + uvicorn,
 *                                   no --reload; restart the app to reload backend).
 *   electron .                       Production: runs the standalone Next.js server
 *                                   (requires `npm run build:frontend` first) plus
 *                                   the Gateway without reload.
 *   Packaged installer               Same as production, using bundled resources.
 *
 * Useful flags:
 *   --frontend-url=<url>   Load this URL instead of spawning the frontend
 *                          (e.g. http://127.0.0.1:2026 when `make dev` runs nginx).
 *   --frontend-port=<n>    Preferred frontend port (default 3000).
 *   --gateway-port=<n>     Preferred gateway port (default 8001).
 *   --skip-backend         Do not spawn the Gateway (attach to an existing one).
 *   --skip-frontend        Do not spawn the frontend (attach to an existing one).
 *   --require-login        Keep the Gateway/frontend login + admin-setup screens.
 *                          By default the desktop app sets AGENT_WORKSPACE_AUTH_DISABLED=1
 *                          (upstream's local single-user mode) so it opens straight
 *                          into the workspace with a synthetic admin user.
 *   --verbose              Mirror child-process output to the console.
 *
 * If a preferred port already serves a healthy endpoint it is reused;
 * otherwise the next free port is picked automatically.
 */

const { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, shell } = require('electron');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');

const { resolveStartUrl, rewriteGatewayDestinations } = require('./lib/desktop-utils');

const APP_NAME = require('./desktop-config.json').displayName;
// Single source of truth for desktop ports: electron/desktop-config.json
// (also read by scripts/build-frontend.mjs so the baked /api rewrites match).
const DESKTOP_DEFAULTS = require('./desktop-config.json');
const DEFAULT_FRONTEND_PORT = DESKTOP_DEFAULTS.frontendPort || 3000;
// NOTE: deliberately NOT 8001 — that is `make dev`'s Gateway port. The desktop
// app owns its own Gateway so it never fights a separately running stack.
const DEFAULT_GATEWAY_PORT = DESKTOP_DEFAULTS.gatewayPort || 8201;
const BACKEND_STARTUP_TIMEOUT_MS = 600000;
const FRONTEND_STARTUP_TIMEOUT_MS = 300000;
const POLL_INTERVAL_MS = 1000;
const MAX_PORT_PROBE_OFFSET = 20;
const MAX_LOG_BYTES = 5 * 1024 * 1024;
const MAX_CHILD_LOG_BYTES = 20 * 1024 * 1024;

// ---------------------------------------------------------------------------
// CLI args
// ---------------------------------------------------------------------------

function parseArgs(argv) {
  const args = {
    dev: false,
    skipBackend: false,
    skipFrontend: false,
    requireLogin: false,
    frontendUrl: null,
    frontendPort: DEFAULT_FRONTEND_PORT,
    gatewayPort: DEFAULT_GATEWAY_PORT,
    verbose: false,
  };
  for (const raw of argv.slice(1)) {
    if (raw === '--dev') args.dev = true;
    else if (raw === '--skip-backend') args.skipBackend = true;
    else if (raw === '--skip-frontend') args.skipFrontend = true;
    else if (raw === '--require-login') args.requireLogin = true;
    else if (raw === '--verbose') args.verbose = true;
    else if (raw.startsWith('--frontend-url=')) args.frontendUrl = raw.slice('--frontend-url='.length);
    else if (raw.startsWith('--frontend-port=')) args.frontendPort = Number(raw.slice('--frontend-port='.length)) || DEFAULT_FRONTEND_PORT;
    else if (raw.startsWith('--gateway-port=')) args.gatewayPort = Number(raw.slice('--gateway-port='.length)) || DEFAULT_GATEWAY_PORT;
  }
  if (args.frontendUrl) args.skipFrontend = true;
  return args;
}

const args = parseArgs(process.argv);

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

const isPackaged = app.isPackaged;
const repoRoot = isPackaged ? null : path.resolve(__dirname, '..');
const resourcesRoot = isPackaged ? process.resourcesPath : null;

const backendDir = isPackaged
  ? path.join(resourcesRoot, 'backend')
  : path.join(repoRoot, 'backend');
const frontendDir = isPackaged ? null : path.join(repoRoot, 'frontend');
const frontendStandaloneDir = isPackaged
  ? path.join(resourcesRoot, 'frontend-standalone')
  : path.join(repoRoot, 'frontend', '.next', 'standalone');
const configTemplatesDir = isPackaged
  ? path.join(resourcesRoot, 'config-templates')
  : repoRoot;

const userDataRoot = app.getPath('userData');
const projectDir = path.join(userDataRoot, 'project');
const agent_workspaceHomeDir = fs.existsSync(path.join(userDataRoot, 'alpha-home'))
  ? path.join(userDataRoot, 'alpha-home')
  : path.join(userDataRoot, 'agent-workspace-home');
const logsDir = path.join(userDataRoot, 'logs');
const mainLogFile = path.join(logsDir, 'main.log');
// Per-user Python provisioning: the install directory stays read-only-safe
// (per-machine installs land in Program Files), so uv puts its Python and the
// backend venv under the writable user-data folder instead.
const backendVenvDir = path.join(userDataRoot, 'backend-venv');
const pythonInstallDir = path.join(userDataRoot, 'python');
// Own preference file for "Start with Windows" (source of truth). The OS
// login-item state is read back for display, but this file decides what boot
// enforces — so an externally removed entry is re-registered, never silently
// dropped.
const autoStartPrefFile = path.join(userDataRoot, 'auto-start.json');

function readAutoStartPref() {
  try {
    const data = JSON.parse(fs.readFileSync(autoStartPrefFile, 'utf8'));
    return data && data.enabled === true;
  } catch {
    return false;
  }
}

function writeAutoStartPref(enabled) {
  try {
    fs.writeFileSync(autoStartPrefFile, JSON.stringify({ enabled: Boolean(enabled) }), 'utf8');
  } catch (error) {
    log(`Warning: could not persist auto-start preference: ${error.message}`);
  }
}

/**
 * Windows always-on: register or clear the login item for this app.
 *
 * Installed-app only: in a source checkout the executable is the bare
 * Electron binary, so a login entry would launch without the app. Throws in
 * that case instead of writing a broken startup entry.
 *
 * @returns {boolean} the OS-reported login-item state after applying.
 */
function applyAutoStartSetting(enabled) {
  if (!isPackaged) {
    throw new Error(
      'Start with Windows is available in the installed app. ' +
        `Install ${APP_NAME} (electron-builder) to use it.`,
    );
  }
  app.setLoginItemSettings({ openAtLogin: Boolean(enabled) });
  writeAutoStartPref(enabled);
  let actual = false;
  try {
    actual = Boolean(app.getLoginItemSettings().openAtLogin);
  } catch {
    actual = Boolean(enabled);
  }
  log(`Start with Windows ${actual ? 'enabled' : 'disabled'}`);
  return actual;
}

function getAutoStartState() {
  let active = false;
  try {
    active = Boolean(app.getLoginItemSettings().openAtLogin);
  } catch {
    active = false;
  }
  return { supported: isPackaged, enabled: readAutoStartPref(), active };
}

// ---------------------------------------------------------------------------
// Logging (never logs environment values — they may contain API keys)
// ---------------------------------------------------------------------------

let fileLoggingReady = false;

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

/** Keep logs bounded: rotate a file aside once it exceeds maxBytes. */
function rotateLogFile(file, maxBytes) {
  try {
    const stat = fs.statSync(file);
    if (stat.size > maxBytes) {
      try {
        fs.rmSync(`${file}.1`, { force: true });
      } catch {
        // Ignore cleanup failures; the rename below still frees the active file.
      }
      fs.renameSync(file, `${file}.1`);
    }
  } catch {
    // Missing file — nothing to rotate.
  }
}

function log(message, detail) {
  const line = `[${new Date().toISOString()}] ${message}${detail ? ` — ${detail}` : ''}`;
  // eslint-disable-next-line no-console
  console.log(line);
  if (fileLoggingReady) {
    try {
      fs.appendFileSync(mainLogFile, `${line}\n`, 'utf8');
    } catch {
      // Logging must never crash the app.
    }
  }
}

function broadcastStatus(message, detail) {
  log(message, detail);
  const payload = { message, detail: detail || '' };
  for (const win of BrowserWindow.getAllWindows()) {
    try {
      win.webContents.send('agent-workspace:status', payload);
      win.webContents.send('alpha:status', payload);
    } catch {
      // Window may be closing; ignore.
    }
  }
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function findOnPath(command) {
  try {
    const probe = process.platform === 'win32' ? 'where' : 'which';
    const result = spawnSync(probe, [command], { encoding: 'utf8' });
    if (result.status === 0 && result.stdout) {
      const first = result.stdout.split(/\r?\n/).map((s) => s.trim()).filter(Boolean)[0];
      if (first && fs.existsSync(first)) return first;
    }
  } catch {
    // Fall through to null.
  }
  return null;
}

/**
 * Path to a tool bundled inside the installed app (electron-builder
 * extraResources `runtime/`), or null when running from sources / missing.
 */
function bundledToolPath(...segments) {
  if (!isPackaged) return null;
  try {
    const candidate = path.join(resourcesRoot, 'runtime', ...segments);
    if (fs.existsSync(candidate)) return candidate;
  } catch {
    // Fall through to system lookup.
  }
  return null;
}

function resolveTool(command, extraCandidates) {
  const viaPath = findOnPath(command);
  if (viaPath) return viaPath;
  for (const candidate of extraCandidates || []) {
    try {
      if (candidate && fs.existsSync(candidate)) return candidate;
    } catch {
      // Ignore and keep searching.
    }
  }
  return null;
}

function httpGetStatus(url, timeoutMs) {
  return new Promise((resolve) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    fetch(url, { signal: controller.signal })
      .then((res) => resolve(res.status))
      .catch(() => resolve(null))
      .finally(() => clearTimeout(timer));
  });
}

async function isAgentWorkspaceGateway(gatewayBaseUrl) {
  // Verify identity, not just liveness: a foreign service on the same port
  // must never be mistaken for our Gateway.
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    let data = null;
    try {
      const res = await fetch(`${gatewayBaseUrl}/health`, { signal: controller.signal });
      if (!res.ok) return false;
      data = await res.json();
    } finally {
      clearTimeout(timer);
    }
    // Service identity as reported by app/gateway/app.py health_check.
    return !!data && (data.service === 'agent-workspace-gateway' || data.service === 'agent-workspace-gateway');
  } catch {
    return false;
  }
}

async function isAgentWorkspaceFrontend(frontendUrl) {
  // Same identity rule for the frontend: only reuse a port when it actually
  // serves this app (Next.js marker), never a foreign occupant.
  const status = await httpGetStatus(frontendUrl, 3000);
  if (status === null || status >= 500) return false;
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    let text = '';
    try {
      const res = await fetch(frontendUrl, { signal: controller.signal });
      if (!res.ok) return false;
      text = await res.text();
    } finally {
      clearTimeout(timer);
    }
    // '__next' is the reliable Next.js marker; the display name is a secondary
    // signal. It was previously listed twice with the old name, so after the
    // rename to Alpha both copies were dead and the probe silently rested on
    // '__next' alone.
    return text.includes('__next') || text.includes(APP_NAME);
  } catch {
    return false;
  }
}

function isPortFree(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.once('listening', () => server.close(() => resolve(true)));
    server.listen(port, '127.0.0.1');
  });
}

async function findFreePort(preferred) {
  for (let offset = 0; offset <= MAX_PORT_PROBE_OFFSET; offset += 1) {
    const port = preferred + offset;
    // eslint-disable-next-line no-await-in-loop
    if (await isPortFree(port)) return port;
  }
  throw new Error(`No free port found in range ${preferred}-${preferred + MAX_PORT_PROBE_OFFSET}`);
}

async function waitForHealthy(label, checkFn, timeoutMs, progressFn, abortIf) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    let alive = false;
    try {
      alive = await checkFn();
    } catch {
      alive = false;
    }
    if (alive) return;
    if (abortIf) {
      const abortReason = abortIf();
      if (abortReason) throw new Error(abortReason);
    }
    if (Date.now() >= deadline) {
      throw new Error(`${label} did not become healthy within ${Math.round(timeoutMs / 1000)}s. See ${mainLogFile}`);
    }
    if (progressFn) progressFn();
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }
}

/**
 * Desktop single-user mode: open straight into the workspace without the
 * login / admin-setup screens. Both the Gateway and the Next.js server honor
 * AGENT_WORKSPACE_AUTH_DISABLED=1 at runtime (synthetic admin user), and both bind
 * to loopback only, so this stays a local-machine trust boundary.
 * Pass --require-login to keep the normal auth screens.
 */
function applyDesktopAuthMode(env) {
  if (!args.requireLogin) {
    env.AGENT_WORKSPACE_AUTH_DISABLED = '1';
    env.AGENT_WORKSPACE_AUTH_DISABLED = '1';
  }
  return env;
}

const MIN_USER_DATA_FREE_BYTES = 1 * 1024 * 1024 * 1024;

/**
 * Fail-loud install/health self-check. Runs on every boot before services
 * spawn so a broken host (disk-full user data, missing bundled runtimes in a
 * packaged install) surfaces one clear error instead of cascading failures.
 * Throws on fatal conditions; logs advisory lines otherwise.
 */
function runStartupDiagnostics() {
  // Writable user-data with room to breathe: the backend venv, Python,
  // databases, logs, and artifacts all live here.
  let freeBytes = null;
  try {
    if (typeof fs.statfsSync === 'function') {
      const stats = fs.statfsSync(userDataRoot);
      freeBytes = Number(stats.bfree) * Number(stats.bsize);
    }
  } catch (error) {
    log(`Diagnostic: disk probe unavailable (${error.message}); skipping free-space gate`);
  }
  if (freeBytes !== null && freeBytes < MIN_USER_DATA_FREE_BYTES) {
    throw new Error(
      `Not enough free disk space for ${APP_NAME} data (${Math.round(freeBytes / 1024 / 1024)} MiB free at ${userDataRoot}; ` +
        'at least 1024 MiB is required). Free some space and restart the app.',
    );
  }
  log(
    'Self-diagnostic passed',
    `userData=${userDataRoot} free=${freeBytes === null ? 'unknown' : `${Math.round(freeBytes / 1024 / 1024)} MiB`} ` +
      `packaged=${isPackaged} electron=${process.versions.electron} node=${process.versions.node}`,
  );
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

// ---------------------------------------------------------------------------
// Child processes
// ---------------------------------------------------------------------------

const children = { backend: null, frontend: null };
// Last unexpected exit per service, used to fail fast during startup waits.
const childExitInfo = { backend: null, frontend: null };
const childLogs = {
  backend: path.join(logsDir, 'gateway.log'),
  frontend: path.join(logsDir, 'frontend.log'),
};

function childAbortedDuringStartup(kind, label) {
  const info = childExitInfo[kind];
  if (!info) return null;
  return (
    `${label} exited during startup (code=${info.code} signal=${info.signal}). ` +
    `See ${childLogs[kind]} for details.`
  );
}

function attachChildLogging(child, label) {
  rotateLogFile(childLogs[label], MAX_CHILD_LOG_BYTES);
  childExitInfo[label] = null;
  const file = childLogs[label];
  const write = (chunk) => {
    const text = String(chunk);
    if (args.verbose) {
      // eslint-disable-next-line no-console
      process.stdout.write(`[${label}] ${text}`);
    }
    try {
      fs.appendFileSync(file, text, 'utf8');
    } catch {
      // Never crash on logging.
    }
  };
  if (child.stdout) child.stdout.on('data', write);
  if (child.stderr) child.stderr.on('data', write);
  child.on('exit', (code, signal) => {
    childExitInfo[label] = { code, signal };
    log(`${label} process exited`, `code=${code} signal=${signal}`);
    if (!appQuitting && mainWindow) {
      // If a service dies while the app is running, tell the user.
      broadcastStatus(`${label === 'backend' ? 'Gateway' : 'Frontend'} process stopped unexpectedly`, `code=${code} — see ${file}`);
    }
  });
  child.on('error', (error) => {
    log(`${label} process error: ${error.message}`);
  });
}

function killTree(kind) {
  const child = children[kind];
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const pid = child.pid;
  try {
    if (process.platform === 'win32') {
      // taskkill /T terminates the whole process tree (uvicorn reloader, npm wrappers).
      spawnSync('taskkill', ['/pid', String(pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      try {
        process.kill(-pid, 'SIGTERM');
      } catch {
        child.kill('SIGTERM');
      }
    }
  } catch {
    try {
      child.kill();
    } catch {
      // Best effort.
    }
  }
}

// ---------------------------------------------------------------------------
// Backend (Gateway API)
// ---------------------------------------------------------------------------

function isExplicitProdEnv() {
  const value = (process.env.AGENT_WORKSPACE_ENV || process.env.AGENT_WORKSPACE_ENV || process.env.ENVIRONMENT || '').trim().toLowerCase();
  return value === 'prod' || value === 'production';
}

/**
 * Warn when a machine-wide production marker would silently switch the
 * direct-open behavior off: both services ignore AGENT_WORKSPACE_AUTH_DISABLED in
 * an explicit production environment, so the login screen would appear.
 */
async function warnIfProdEnvDisablesDirectOpen() {
  if (args.requireLogin || !isExplicitProdEnv()) return;
  log('Warning: AGENT_WORKSPACE_ENV/ENVIRONMENT marks production; services will ignore AGENT_WORKSPACE_AUTH_DISABLED');
  const choice = await dialog.showMessageBox({
    type: 'warning',
    buttons: ['Continue anyway', 'Quit'],
    defaultId: 0,
    cancelId: 1,
    title: `${APP_NAME} — production environment detected`,
    message: 'This machine declares a production environment.',
    detail:
      'AGENT_WORKSPACE_ENV (or ENVIRONMENT) is set to a production value, so the ' +
      'Gateway and frontend will IGNORE the desktop single-user mode and show ' +
      'the login screen.\n\nUnset that variable (or start with --require-login) ' +
      'to keep the direct-open behavior.',
  });
  if (choice.response === 1) {
    app.quit();
    throw new Error('Quit by user (production environment marker present)');
  }
}

/**
 * First-run guidance: when the Gateway reports zero configured models,
 * chatting will fail with provider errors. Point at the config folder once
 * instead of leaving the user to discover it. Advisory only — never blocks.
 */
async function warnIfNoModels(gatewayBaseUrl) {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    let data = null;
    try {
      const res = await fetch(`${gatewayBaseUrl}/api/models`, { signal: controller.signal });
      if (!res.ok) return;
      data = await res.json();
    } finally {
      clearTimeout(timer);
    }
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
    if (choice.response === 0) {
      await shell.openPath(projectDir);
    }
  } catch (error) {
    log(`Model pre-flight check skipped: ${error.message}`);
  }
}

function resolveUv() {
  // Packaged installs must be self-contained: use the installer-bundled
  // runtime, never the system PATH. Source checkouts may fall back to PATH.
  const bundled = bundledToolPath('uv', process.platform === 'win32' ? 'uv.exe' : 'uv');
  if (bundled) return bundled;
  if (isPackaged) {
    throw new Error(
      'Bundled `uv` runtime missing from the installed app (resources/runtime/uv). ' +
        `Reinstall ${APP_NAME} — the packaged app must not depend on a system PATH copy.`,
    );
  }
  const home = os.homedir();
  return resolveTool(process.platform === 'win32' ? 'uv.exe' : 'uv', [
    path.join(home, '.cargo', 'bin', process.platform === 'win32' ? 'uv.exe' : 'uv'),
    path.join(home, '.local', 'bin', 'uv'),
    process.platform === 'win32' ? path.join(home, 'AppData', 'Local', 'Programs', 'uv', 'uv.exe') : null,
  ]);
}

function resolveNode() {
  const bundled = bundledToolPath('node', process.platform === 'win32' ? 'node.exe' : 'node');
  if (bundled) return bundled;
  if (isPackaged) {
    throw new Error(
      'Bundled Node.js runtime missing from the installed app (resources/runtime/node). ' +
        `Reinstall ${APP_NAME} — the packaged app must not depend on a system PATH copy.`,
    );
  }
  return resolveTool(process.platform === 'win32' ? 'node.exe' : 'node', []);
}

function spawnBackend(gatewayPort) {
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

  const env = applyDesktopAuthMode({
    ...process.env,
    PYTHONPATH: backendDir,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
    GATEWAY_HOST: '127.0.0.1',
    GATEWAY_PORT: String(gatewayPort),
    AGENT_WORKSPACE_PROJECT_ROOT: projectDir,
    AGENT_WORKSPACE_CONFIG_PATH: path.join(projectDir, 'config.yaml'),
    AGENT_WORKSPACE_HOME: agent_workspaceHomeDir,
    AGENT_WORKSPACE_PROJECT_ROOT: projectDir,
    AGENT_WORKSPACE_CONFIG_PATH: path.join(projectDir, 'config.yaml'),
    AGENT_WORKSPACE_HOME: agent_workspaceHomeDir,
    // Keep all uv-managed writes (downloaded Python, project venv) under the
    // per-user data folder: the install directory may be read-only
    // (per-machine installs) and must never be written at runtime.
    UV_PYTHON_INSTALL_DIR: pythonInstallDir,
    UV_PROJECT_ENVIRONMENT: backendVenvDir,
  });

  const uvArgs = ['run', '--locked', 'uvicorn', 'app.gateway.app:app', '--host', '127.0.0.1', '--port', String(gatewayPort)];
  log(`Starting Gateway: ${uv} ${uvArgs.join(' ')}`, `cwd=${backendDir}`);
  const child = spawn(uv, uvArgs, { cwd: backendDir, env, stdio: ['ignore', 'pipe', 'pipe'] });
  children.backend = child;
  attachChildLogging(child, 'backend');
  return child;
}

// ---------------------------------------------------------------------------
// Frontend (Next.js)
// ---------------------------------------------------------------------------

function spawnFrontendDev(nodeExe, frontendPort, gatewayBaseUrl) {
  const nextBin = path.join(frontendDir, 'node_modules', 'next', 'dist', 'bin', 'next');
  if (!fs.existsSync(nextBin)) {
    dialog.showErrorBox(
      `${APP_NAME} — frontend dependencies missing`,
      `Next.js was not found in ${frontendDir}\\node_modules.\n\n` +
        'Run `corepack pnpm install --frozen-lockfile` inside the frontend/\n' +
        'directory (from frontend/: corepack pnpm install), then restart the app.',
    );
    throw new Error('Frontend node_modules missing');
  }
  const env = applyDesktopAuthMode({
    ...process.env,
    PORT: String(frontendPort),
    AGENT_WORKSPACE_INTERNAL_GATEWAY_BASE_URL: gatewayBaseUrl,
    AGENT_WORKSPACE_INTERNAL_GATEWAY_BASE_URL: gatewayBaseUrl,
  });
  const fArgs = [nextBin, 'dev', '--port', String(frontendPort)];
  log(`Starting frontend (dev): ${nodeExe} ${fArgs.join(' ')}`, `cwd=${frontendDir}`);
  const child = spawn(nodeExe, fArgs, { cwd: frontendDir, env, stdio: ['ignore', 'pipe', 'pipe'] });
  children.frontend = child;
  attachChildLogging(child, 'frontend');
  return child;
}

/**
 * Point a production Next.js build's /api rewrites at this run's Gateway.
 *
 * Next.js resolves `rewrites()` from next.config.js at BUILD time and bakes
 * the concrete URLs into `.next/routes-manifest.json`, so the runtime
 * AGENT_WORKSPACE_INTERNAL_GATEWAY_BASE_URL env var alone cannot steer a production
 * server (it only affects dev SSR paths). The desktop app picks its Gateway
 * port dynamically, so it rewrites the baked loopback destinations to the
 * effective Gateway URL just before spawning the frontend. Only loopback
 * destinations are touched; anything else is left alone. Throws loudly when
 * the manifest cannot be read, parsed, or written.
 *
 * @returns {boolean} true when at least one rule was updated.
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
          `Expected /api rewrites to http://127.0.0.1:<port> (built with AGENT_WORKSPACE_INTERNAL_GATEWAY_BASE_URL). Rebuild the frontend.`,
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

function spawnFrontendProd(nodeExe, frontendPort, gatewayBaseUrl) {
  const standaloneServer = path.join(frontendStandaloneDir, 'server.js');
  const env = applyDesktopAuthMode({
    ...process.env,
    PORT: String(frontendPort),
    HOSTNAME: '127.0.0.1',
    AGENT_WORKSPACE_INTERNAL_GATEWAY_BASE_URL: gatewayBaseUrl,
    AGENT_WORKSPACE_INTERNAL_GATEWAY_BASE_URL: gatewayBaseUrl,
  });
  // Remove split-origin overrides if the user exported them globally: the
  // desktop app always talks to the Gateway through Next.js rewrites.
  delete env.NEXT_PUBLIC_BACKEND_BASE_URL;
  delete env.NEXT_PUBLIC_LANGGRAPH_BASE_URL;

  if (fs.existsSync(standaloneServer)) {
    patchStandaloneGatewayUrl(frontendStandaloneDir, gatewayBaseUrl);
    log(`Starting frontend (standalone): ${nodeExe} server.js`, `cwd=${frontendStandaloneDir}`);
    const child = spawn(nodeExe, [standaloneServer], { cwd: frontendStandaloneDir, env, stdio: ['ignore', 'pipe', 'pipe'] });
    children.frontend = child;
    attachChildLogging(child, 'frontend');
    return child;
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
  const fArgs = [nextBin, 'start', '-p', String(frontendPort)];
  // `next start` serves frontend/.next directly (not the standalone copy).
  patchStandaloneGatewayUrl(frontendDir, gatewayBaseUrl);
  log(`Starting frontend (next start): ${nodeExe} ${fArgs.join(' ')}`, `cwd=${frontendDir}`);
  const child = spawn(nodeExe, fArgs, { cwd: frontendDir, env, stdio: ['ignore', 'pipe', 'pipe'] });
  children.frontend = child;
  attachChildLogging(child, 'frontend');
  return child;
}

// ---------------------------------------------------------------------------
// Windows
// ---------------------------------------------------------------------------

let splashWindow = null;
let mainWindow = null;
let appQuitting = false;
let runtimeStatus = { dev: args.dev, packaged: isPackaged, frontendUrl: null, gatewayUrl: null };

function createSplash() {
  splashWindow = new BrowserWindow({
    width: 440,
    height: 340,
    resizable: false,
    frame: false,
    alwaysOnTop: true,
    show: false,
    backgroundColor: '#0b0f14',
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

function createMainWindow(targetUrl) {
  mainWindow = new BrowserWindow({
    width: 1320,
    height: 880,
    minWidth: 1024,
    minHeight: 640,
    title: APP_NAME,
    backgroundColor: '#0b0f14',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.loadURL(targetUrl);
  mainWindow.once('ready-to-show', () => {
    if (splashWindow) splashWindow.close();
    if (mainWindow) mainWindow.show();
    log('Main window ready', targetUrl);
  });
  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedUrl) => {
    log(`Window failed to load ${validatedUrl}: [${errorCode}] ${errorDescription}`);
  });
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
  return mainWindow;
}

function buildMenu() {
  const template = [
    {
      label: '&File',
      submenu: [
        { role: 'reload', label: '&Reload' },
        { role: 'forceReload', label: 'Force R&eload' },
        { role: 'toggleDevTools', label: '&Toggle Developer Tools' },
        { type: 'separator' },
        { role: 'quit', label: `&Quit ${APP_NAME}` },
      ],
    },
    {
      label: '&Tools',
      submenu: [
        {
          label: '&Open user-data folder',
          click: () => shell.openPath(userDataRoot),
        },
        {
          label: 'Open &Gateway health check',
          enabled: Boolean(runtimeStatus.gatewayUrl),
          click: () => {
            if (runtimeStatus.gatewayUrl) shell.openExternal(`${runtimeStatus.gatewayUrl}/health`);
          },
        },
        {
          label: '&Copy app URLs',
          click: () => {
            clipboard.writeText(
              `${APP_NAME} frontend: ${runtimeStatus.frontendUrl || '(none)'}\n${APP_NAME} gateway: ${runtimeStatus.gatewayUrl || '(none)'}\n`,
            );
          },
        },
      ],
    },
    {
      label: '&Help',
      submenu: [
        {
          label: `&About ${APP_NAME}`,
          click: () => {
            dialog.showMessageBox({
              type: 'info',
              title: `About ${APP_NAME}`,
              message: `${APP_NAME} Desktop v${app.getVersion()}`,
              detail:
                `Frontend: ${runtimeStatus.frontendUrl || '(not running)'}\n` +
                `Gateway: ${runtimeStatus.gatewayUrl || '(not running)'}\n` +
                `Electron ${process.versions.electron} / Chrome ${process.versions.chrome} / Node ${process.versions.node}\n\n` +
                `User data: ${userDataRoot}`,
            });
          },
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ---------------------------------------------------------------------------
// Boot sequence
// ---------------------------------------------------------------------------

async function boot() {
  ensureDir(projectDir);
  ensureDir(agent_workspaceHomeDir);
  ensureDir(logsDir);
  runStartupDiagnostics();
  fileLoggingReady = true;
  rotateLogFile(mainLogFile, MAX_LOG_BYTES);
  try {
    fs.appendFileSync(mainLogFile, `--- ${APP_NAME} Desktop v${app.getVersion()} started (${new Date().toISOString()}) ---\n`, 'utf8');
  } catch {
    // Ignore.
  }

  // Self-heal the login item: if the user opted into "Start with Windows"
  // but the OS entry is gone (cleaner tools, manual removal), re-register it.
  // Best-effort and packaged-only; never blocks boot.
  if (isPackaged && readAutoStartPref()) {
    try {
      app.setLoginItemSettings({ openAtLogin: true });
      log('Re-applied Start with Windows login item');
    } catch (error) {
      log(`Warning: could not re-apply Start with Windows: ${error.message}`);
    }
  }
  if (app.getLoginItemSettings().wasOpenedAtLogin) {
    log('Opened at login (Windows startup)');
  }

  log(`Mode: ${args.dev ? 'development' : 'production'}${isPackaged ? ' (packaged)' : ' (from sources)'}`);
  log(
    args.requireLogin
      ? 'Auth: login/setup screens enabled (--require-login)'
      : 'Auth: local single-user mode, app opens directly (AGENT_WORKSPACE_AUTH_DISABLED=1)',
  );
  await warnIfProdEnvDisablesDirectOpen();

  // Seed per-user config on first launch (never overwrites existing files).
  seedFileIfMissing(path.join(configTemplatesDir, 'config.example.yaml'), path.join(projectDir, 'config.yaml'));
  seedFileIfMissing(
    path.join(configTemplatesDir, 'extensions_config.example.json'),
    path.join(projectDir, 'extensions_config.json'),
  );
  broadcastStatus('Preparing local data directory…', projectDir);

  // --- Gateway ---------------------------------------------------------
  let gatewayPort = args.gatewayPort;
  let gatewayBaseUrl = `http://127.0.0.1:${gatewayPort}`;
  let gatewayReused = false;

  if (!args.skipBackend && (await isAgentWorkspaceGateway(gatewayBaseUrl))) {
    gatewayReused = true;
    log(`Reusing existing Gateway at ${gatewayBaseUrl}`);
  } else if (args.skipBackend && (await isAgentWorkspaceGateway(gatewayBaseUrl))) {
    // Explicit attach mode: still prefer the live Gateway on the preferred port.
    gatewayReused = true;
    log(`Attaching to existing Gateway at ${gatewayBaseUrl}`);
  } else {
    gatewayPort = await findFreePort(args.gatewayPort);
    gatewayBaseUrl = `http://127.0.0.1:${gatewayPort}`;
    if (!args.skipBackend) {
      broadcastStatus('Starting Gateway API…', `port ${gatewayPort}`);
      spawnBackend(gatewayPort);
      await waitForHealthy(
        'Gateway API',
        () => isAgentWorkspaceGateway(gatewayBaseUrl),
        BACKEND_STARTUP_TIMEOUT_MS,
        () =>
          broadcastStatus('Starting Gateway API…', 'installing Python deps on first launch can take a few minutes'),
        () => childAbortedDuringStartup('backend', 'Gateway API'),
      );
      log(`Gateway healthy at ${gatewayBaseUrl}`);
    } else {
      log('Backend spawn skipped (--skip-backend); expecting a Gateway at', gatewayBaseUrl);
    }
  }

  // --- Frontend --------------------------------------------------------
  let frontendUrl = args.frontendUrl;
  if (frontendUrl) {
    log(`Using explicit frontend URL (no spawn): ${frontendUrl}`);
  } else {
    const preferredUrl = `http://127.0.0.1:${args.frontendPort}`;
    if (await isAgentWorkspaceFrontend(preferredUrl)) {
      frontendUrl = preferredUrl;
      log(`${args.skipFrontend ? 'Attaching to' : 'Reusing'} existing frontend at ${frontendUrl}`);
    } else {
      const frontendPort = await findFreePort(args.frontendPort);
      frontendUrl = `http://127.0.0.1:${frontendPort}`;
      if (!args.skipFrontend) {
        const nodeExe = resolveNode();
        if (!nodeExe) {
          dialog.showErrorBox(
            `${APP_NAME} — Node.js not found`,
            'Could not find Node.js 22+ on PATH.\n\n' +
              'Install the LTS release from https://nodejs.org/ (or `winget install OpenJS.NodeJS.LTS`),\n' +
              'then restart the app.',
          );
          throw new Error('node executable not found on PATH');
        }
        if (args.dev && !isPackaged) {
          broadcastStatus('Starting frontend (dev)…', `port ${frontendPort}`);
          spawnFrontendDev(nodeExe, frontendPort, gatewayBaseUrl);
        } else {
          broadcastStatus('Starting frontend…', `port ${frontendPort}`);
          spawnFrontendProd(nodeExe, frontendPort, gatewayBaseUrl);
        }
        await waitForHealthy(
          'Frontend',
          () => isAgentWorkspaceFrontend(frontendUrl),
          FRONTEND_STARTUP_TIMEOUT_MS,
          () =>
            broadcastStatus('Starting frontend…', 'first launch can take a while — check logs/frontend.log'),
          () => childAbortedDuringStartup('frontend', 'Frontend'),
        );
        log(`Frontend healthy at ${frontendUrl}`);
      } else {
        log('Frontend spawn skipped (--skip-frontend); expecting a frontend at', frontendUrl);
      }
    }
  }

  runtimeStatus = {
    dev: args.dev,
    packaged: isPackaged,
    frontendUrl,
    gatewayUrl: gatewayBaseUrl,
    backendReused: gatewayReused,
    backendPid: children.backend ? children.backend.pid : null,
    frontendPid: children.frontend ? children.frontend.pid : null,
    userData: userDataRoot,
    versions: {
      app: app.getVersion(),
      electron: process.versions.electron,
      chrome: process.versions.chrome,
      node: process.versions.node,
    },
  };

  buildMenu();
  broadcastStatus(`Opening ${APP_NAME}…`, frontendUrl);
  createMainWindow(resolveStartUrl(frontendUrl));
  // Advisory first-run check; never blocks the UI.
  warnIfNoModels(gatewayBaseUrl).catch((error) => log(`Model pre-flight check failed: ${error.message}`));
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  ipcMain.handle('agent-workspace:status', () => runtimeStatus);
  ipcMain.handle('alpha:status', () => runtimeStatus);
  ipcMain.handle('agent-workspace:open-user-data', () => shell.openPath(userDataRoot));
  ipcMain.handle('alpha:open-user-data', () => shell.openPath(userDataRoot));
  ipcMain.handle('agent-workspace:get-auto-start', () => getAutoStartState());
  ipcMain.handle('alpha:get-auto-start', () => getAutoStartState());
  ipcMain.handle('agent-workspace:set-auto-start', (_event, enabled) => applyAutoStartSetting(enabled));
  ipcMain.handle('alpha:set-auto-start', (_event, enabled) => applyAutoStartSetting(enabled));

  app.whenReady().then(() => {
    createSplash();
    boot().catch((error) => {
      log(`Startup failed: ${error && error.message ? error.message : error}`);
      try {
        if (splashWindow) splashWindow.close();
      } catch {
        // Ignore.
      }
      if (!appQuitting) {
        dialog.showErrorBox(
          `${APP_NAME} — startup failed`,
          `${error && error.message ? error.message : error}\n\nSee ${mainLogFile} for details.`,
        );
      }
      app.quit();
    });
  });

  app.on('window-all-closed', () => {
    // Closing the window stops the local services too, so no orphan
    // Python/Node processes are left behind on Windows.
    app.quit();
  });

  app.on('before-quit', () => {
    appQuitting = true;
    killTree('frontend');
    killTree('backend');
  });
}
