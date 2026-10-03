#!/usr/bin/env node

/**
 * Boot smoke test: does `main.js` actually come up?
 *
 * ## Why this exists
 *
 * `main.js` on `main` registered four IPC channels twice each. Electron throws
 * `Attempted to register a second handler for 'alpha:status'` at module load, and
 * the process exits **0** — no window, no splash, no dialog, and nothing in any
 * log. Every unit test in `electron/tests/` passed, because they are pure Node
 * and never load `main.js`. So the app shipped unlaunchable and the test suite
 * said it was fine.
 *
 * That is the class of bug a source-text assertion cannot catch and this script
 * can: it launches the real Electron binary with the real `main.js`, attached to
 * a stub frontend and Gateway so no Python or Node service is needed, and fails
 * unless the app reaches a live window.
 *
 * What it asserts:
 *   1. The process is still alive after boot (catches the exit-0 crash).
 *   2. `main.log` records the boot and the main window becoming ready.
 *   3. The window URL is the stub frontend (proves it loaded that origin).
 *   4. No `uncaughtException` / `unhandledRejection` reached the log.
 *
 * ## Usage
 *
 *   node scripts/smoke-boot.mjs            # 90s budget
 *   node scripts/smoke-boot.mjs --keep     # leave the throwaway profile behind
 *   node scripts/smoke-boot.mjs --timeout 120
 *
 * Requires the Electron binary (`npm install` first). Exits non-zero on failure,
 * so it is usable as a CI gate.
 */

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const electronDir = fileURLToPath(new URL('..', import.meta.url));
const keepProfile = process.argv.includes('--keep');
const timeoutIndex = process.argv.indexOf('--timeout');
const TIMEOUT_MS = timeoutIndex >= 0 ? Number(process.argv[timeoutIndex + 1]) || 90000 : 90000;

// A throwaway userData profile, so the smoke test never touches the real
// install's config, threads, or auto-start preference.
const profileDir = fs.mkdtempSync(path.join(os.tmpdir(), 'alpha-smoke-'));

/**
 * Two free ports, bound to find them and released again.
 *
 * Hardcoded ports made this test fail for anyone whose machine already had
 * something on 45111/45112 — and, worse, made it fail *quietly in the wrong
 * direction*: the app would correctly report "this is not my service" and try to
 * spawn a real Gateway, which then takes minutes. Binding to port 0 and reading
 * back the assigned port makes the collision impossible.
 */
function reserveFreePorts(count) {
  const servers = [];
  const ports = [];
  for (let i = 0; i < count; i += 1) {
    const server = net.createServer();
    server.listen(0, '127.0.0.1');
    servers.push(server);
  }
  return new Promise((resolve, reject) => {
    let pending = servers.length;
    for (const server of servers) {
      server.once('listening', () => {
        ports.push(server.address().port);
        if (--pending === 0) {
          for (const s of servers) s.close();
          resolve(ports);
        }
      });
      server.once('error', reject);
    }
  });
}

const [FRONTEND_PORT, GATEWAY_PORT] = await reserveFreePorts(2);

function fail(message, detail = '') {
  console.error(`\nSMOKE TEST FAILED: ${message}`);
  if (detail) console.error(detail);
  console.error(`\nuserData profile: ${profileDir}`);
  if (keepProfile) console.error('kept for inspection (--keep)');
  process.exit(1);
}

/** Minimal HTTP server that identifies as both Alpha services. */
function startStubs() {
  const gateway = http.createServer((req, res) => {
    if (req.url === '/health') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      // The identity the app's probe requires before it will reuse a port.
      res.end(JSON.stringify({ service: 'alpha-gateway', status: 'ok' }));
      return;
    }
    if (req.url === '/api/models') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ models: [{ name: 'smoke-test' }] }));
      return;
    }
    res.writeHead(404).end();
  });
  const frontend = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    // The lowercase `__next` marker the app's frontend probe looks for, which is
    // why a real Next.js build is identified by it rather than by `__NEXT_DATA__`.
    res.end(
      '<!doctype html><html><body><script id="__NEXT_DATA__" type="application/json">' +
        '{"buildId":"smoke","next":{"version":"15"}}' +
        '</script><link rel="__next_main" href="/"></body></html>',
    );
  });
  return new Promise((resolve, reject) => {
    gateway.once('error', reject);
    frontend.once('error', reject);
    gateway.listen(GATEWAY_PORT, '127.0.0.1', () =>
      frontend.listen(FRONTEND_PORT, '127.0.0.1', () => resolve({ gateway, frontend })),
    );
  });
}

function electronBinary() {
  const exe = path.join(
    electronDir,
    'node_modules',
    'electron',
    'dist',
    process.platform === 'win32' ? 'electron.exe' : 'electron',
  );
  if (!fs.existsSync(exe)) {
    console.error(
      `Electron binary not found at ${exe}.\n` +
        'npm install must run to completion (npm >= 11 blocks dependency install scripts ' +
        'by default — run `npm install-scripts approve electron` if the binary is absent).',
    );
    process.exit(1);
  }
  return exe;
}

const logFile = path.join(profileDir, 'logs', 'main.log');
const gatewayBase = `http://127.0.0.1:${GATEWAY_PORT}`;
const readLog = () => {
  try {
    return fs.readFileSync(logFile, 'utf8');
  } catch {
    return '';
  }
};

const servers = await startStubs();
console.log(`stubs listening on ${FRONTEND_PORT} (frontend) and ${GATEWAY_PORT} (gateway)`);

const child = spawn(
  electronBinary(),
  [
    electronDir,
    `--user-data-dir=${profileDir}`,
    '--no-sandbox',
    '--no-updates',
    `--gateway-port=${GATEWAY_PORT}`,
    // Explicit: the default preferred port is 3000, which a developer's own
    // `make dev` stack may already hold. Without this the app would correctly
    // reuse THAT frontend and the test would be asserting about the wrong one.
    `--frontend-port=${FRONTEND_PORT}`,
    '--require-login',
  ],
  {
    cwd: electronDir,
    env: {
      ...process.env,
      // A GUI app on Windows does not inherit stdout reliably; force it so the
      // console output is capturable at all.
      ELECTRON_ENABLE_LOGGING: '1',
      ELECTRON_ENABLE_STACK_DUMPING: '1',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  },
);

const consoleLines = [];
child.stdout.on('data', (chunk) => consoleLines.push(String(chunk)));
child.stderr.on('data', (chunk) => consoleLines.push(String(chunk)));

let exited = null;
child.on('exit', (code, signal) => {
  exited = { code, signal };
});

console.log(`electron pid ${child.pid}; waiting up to ${TIMEOUT_MS}ms for the window…`);

const deadline = Date.now() + TIMEOUT_MS;
let ready = false;
while (Date.now() < deadline) {
  if (exited) break;
  if (readLog().includes('Main window ready')) {
    ready = true;
    break;
  }
  await new Promise((resolve) => setTimeout(resolve, 250));
}

const log = readLog();
const console_ = consoleLines.join('');

// --- 1. the process is still alive ----------------------------------------
// This is the assertion that catches the duplicate-ipcMain bug: the old code
// exited here with code 0 before any window existed.
if (exited && !ready) {
  const detail = [
    `exit code: ${exited.code}`,
    `signal: ${exited.signal}`,
    '',
    '--- main.log ---',
    log || '(main.log was never written)',
    '',
    '--- stdout/stderr ---',
    console_.slice(-4000) || '(none)',
  ].join('\n');
  fail(
    `the app exited before the window opened (code ${exited.code})`,
    detail,
  );
}

if (!ready) {
  fail('the window never became ready before the timeout', [
    '--- main.log ---',
    log || '(main.log was never written)',
    '',
    '--- stdout/stderr ---',
    console_.slice(-4000) || '(none)',
  ].join('\n'));
}

// --- 2. the boot actually progressed ---------------------------------------
if (!log.includes('Alpha Desktop v')) {
  fail('main.log does not record a session header', log);
}
if (!log.includes('Main window ready')) {
  fail('the main window never reported ready', log);
}

// --- 3. it attached to the stubs, and did not spawn anything --------------
if (!log.includes(`127.0.0.1:${FRONTEND_PORT}`)) {
  fail(
    `the app did not load the stub frontend on ${FRONTEND_PORT}`,
    'A window that opened on a different origin would mean it attached to ' +
      'something it did not verify.',
  );
}
if (!log.includes(`127.0.0.1:${GATEWAY_PORT}`)) {
  fail(`the app did not resolve the Gateway to the stub on ${GATEWAY_PORT}`, log);
}
// Both stubs were already serving a verified Alpha identity, so the app must
// have REUSED them. Spawning here is the double-service bug: a second Gateway on
// a port just reported as belonging to a healthy one, with two processes
// contending for the same state directory.
if (log.includes('Starting Gateway:')) {
  fail(
    'the app spawned a Gateway even though a verified one was already listening',
    'The port decision said "reuse" but the service was started anyway.\n\n' + log,
  );
}
if (!log.includes('reusing the Alpha service already serving on')) {
  fail('the app never reported reusing an existing service', log);
}

// --- 4. no unhandled error reached the log --------------------------------
for (const marker of ['Uncaught exception in the main process', 'Unhandled rejection']) {
  if (log.includes(marker)) {
    fail(`the log contains "${marker}"`, log);
  }
}

// --- 5. the startup diagnostics ran ---------------------------------------
if (!log.includes('Self-diagnostic passed')) {
  fail('the preflight diagnostics did not report success', log);
}

// --- teardown -------------------------------------------------------------
// Ask the app to quit properly rather than killing it, so the shutdown ladder
// actually runs. `taskkill` would bypass it entirely and this test would prove
// nothing about orderly shutdown.
child.kill('SIGTERM');
const exitedCleanly = await new Promise((resolve) => {
  const timer = setTimeout(() => resolve(false), 20000);
  child.on('exit', () => {
    clearTimeout(timer);
    resolve(true);
  });
});
if (!exitedCleanly) {
  console.warn('warning: the app did not exit within 20s of SIGTERM; forcing it.');
  child.kill('SIGKILL');
} else {
  console.log('the app exited cleanly after a quit request.');
}

if (!keepProfile) fs.rmSync(profileDir, { recursive: true, force: true, maxRetries: 5 });
// The stubs close only after the app is gone, so a reused service can never be
// killed by the app shutting down first.

console.log('\nSMOKE TEST PASSED');
console.log(`  the app launched, attached to the verified stub services on :${FRONTEND_PORT}/:${GATEWAY_PORT},`);
console.log('  opened a window, and shut down without an unhandled error.');
console.log(`\n--- main.log (${log.length} bytes) ---`);
console.log(log);

// ---------------------------------------------------------------------------
// Cleanup assertion: the whole point of an ordered shutdown is that it stops the
// services it started. This run started nothing (both services were reused), so
// the stubs must still be listening — if the app had killed them, that would mean
// it tore down a service it does not own.
// ---------------------------------------------------------------------------
console.log('verifying the reused services were left running…');
try {
  const health = await fetch(`${gatewayBase}/health`, { signal: AbortSignal.timeout(3000) });
  if (health.status !== 200) throw new Error(`status ${health.status}`);
  const data = await health.json();
  if (data.service !== 'alpha-gateway') throw new Error(`identity was ${data.service}`);
} catch (error) {
  fail(
    'the app killed a service it had reused rather than one it started',
    `The stub Gateway on ${GATEWAY_PORT} no longer answers: ${error.message}\n` +
      'A reused service belongs to whoever started it.',
  );
}
console.log('  the reused stub services are still running, as they should be.');

servers.gateway.close();
servers.frontend.close();
console.log('\nSMOKE TEST PASSED');