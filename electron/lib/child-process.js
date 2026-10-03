'use strict';

/**
 * Spawning, log capture, and verified process-tree kills.
 *
 * ## Why the tree kill was wrong
 *
 * The old `killTree` had two problems, both of which leave a Python process
 * holding a port after the user quits:
 *
 * 1. **The POSIX branch could never work.** It called `process.kill(-pid)`,
 *    which requires the child to be a *process-group leader*, and nothing ever
 *    spawned it with `detached: true`. The call threw, and the fallback
 *    `child.kill('SIGTERM')` killed the wrapper while leaving `uv`'s
 *    grandchildren (the uvicorn reloader) alive.
 * 2. **The kill was never verified.** `taskkill` is fire-and-forget, so a
 *    process that ignored it — or that was mid-write — simply stayed alive.
 *
 * So: children are spawned `detached` (making a real process group on POSIX),
 * and killing waits for the process to actually be gone before reporting
 * success. A timeout escalates to the hard kill and, on Windows, to `taskkill /T`
 * which is the only thing that takes a tree.
 */

const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

/**
 * Spawn a long-running child with pipes and its own process group.
 *
 * `detached: true` is what makes a tree kill possible: without it the child is
 * in the parent's group and `kill(-pid)` targets nothing.
 *
 * @returns {import('node:child_process').ChildProcess}
 */
function spawnChild(command, commandArgs, options = {}) {
  return spawn(command, commandArgs, {
    stdio: ['ignore', 'pipe', 'pipe'],
    // Own process group, so the whole tree can be signalled or tree-killed.
    detached: process.platform !== 'win32',
    // Windows: never let a console window flash for a background service.
    windowsHide: true,
    ...options,
  });
}

/** Locate an executable on PATH, or in one of `extraCandidates`. */
function resolveToolOnPath(command, extraCandidates = []) {
  try {
    const probe = process.platform === 'win32' ? 'where' : 'which';
    const result = spawnSync(probe, [command], { encoding: 'utf8' });
    if (result.status === 0 && result.stdout) {
      const first = result.stdout
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean)[0];
      if (first && fs.existsSync(first)) return first;
    }
  } catch {
    // Fall through to the candidates.
  }
  for (const candidate of extraCandidates) {
    try {
      if (candidate && fs.existsSync(candidate)) return candidate;
    } catch {
      // Ignore and keep searching.
    }
  }
  return null;
}

/**
 * A tool bundled inside the installed app, or null.
 *
 * In a source checkout this is always null, so callers fall back to PATH.
 */
function bundledToolPath(resourcesRoot, ...segments) {
  if (!resourcesRoot) return null;
  try {
    const candidate = path.join(resourcesRoot, 'runtime', ...segments);
    if (fs.existsSync(candidate)) return candidate;
  } catch {
    // Fall through to the system lookup.
  }
  return null;
}

/** Free bytes at `target`, or null when the platform cannot report it. */
function freeSpaceBytes(target) {
  try {
    if (typeof fs.statfsSync !== 'function') return null;
    const stats = fs.statfsSync(target);
    const bytes = Number(stats.bfree) * Number(stats.bsize);
    return Number.isFinite(bytes) ? bytes : null;
  } catch {
    return null;
  }
}

/**
 * Keep a log file bounded: rotate aside once it exceeds `maxBytes`.
 *
 * One backup, as before. The rename can fail on Windows when another handle is
 * open, which is why the caller wraps this in try/catch rather than treating a
 * failure as fatal — losing a rotation is not worth crashing over.
 */
function rotateLogFile(file, maxBytes) {
  try {
    if (fs.statSync(file).size > maxBytes) {
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

/**
 * Attach logging and lifecycle reporting to a child process.
 *
 * `onUnexpectedExit` is what turns a child that dies mid-session into a
 * supervised restart; without it the old code broadcast "process stopped
 * unexpectedly" to a splash window that had already closed, so the message went
 * nowhere and nothing was restarted.
 *
 * @param {import('node:child_process').ChildProcess} child
 * @param {string} label matches a key in the log-file table
 * @param {object} options
 */
function attachChildLogging(child, label, { logFile, maxLogBytes = 20 * 1024 * 1024, verbose = false, log = () => {}, onUnexpectedExit = null, isShuttingDown = () => false, onOutput = null } = {}) {
  if (logFile) rotateLogFile(logFile, maxLogBytes);

  const write = (chunk) => {
    const text = String(chunk);
    if (verbose) {
      // eslint-disable-next-line no-console
      process.stdout.write(`[${label}] ${text}`);
    }
    if (typeof onOutput === 'function') {
      try {
        onOutput(text);
      } catch {
        // Progress reporting must never break log capture.
      }
    }
    if (!logFile) return;
    try {
      fs.appendFileSync(logFile, text, 'utf8');
    } catch {
      // Never crash on logging.
    }
  };
  if (child.stdout) child.stdout.on('data', write);
  if (child.stderr) child.stderr.on('data', write);

  child.on('exit', (code, signal) => {
    log(`${label} process exited`, `code=${code} signal=${signal}`);
    if (typeof onUnexpectedExit === 'function') onUnexpectedExit({ code, signal });
    else if (!isShuttingDown()) log(`${label} stopped unexpectedly`);
  });
  child.on('error', (error) => {
    log(`${label} process error: ${error.message}`);
  });
  return child;
}

/** Is this child process already gone? */
function isChildDead(child) {
  if (!child) return true;
  if (typeof child.exitCode === 'number') return true;
  if (child.signalCode) return true;
  return false;
}

/**
 * Kill a process tree and WAIT for it to be gone.
 *
 * @param {number} pid
 * @param {object} [options]
 * @param {number} [options.timeoutMs] how long to wait for a graceful exit
 * @param {boolean} [options.force] skip the graceful phase
 * @param {(line: string) => void} [options.log]
 * @returns {Promise<{stopped: boolean, escalated: boolean}>}
 */
async function killProcessTree(pid, { timeoutMs = 3000, force = false, log = () => {}, isAlive = defaultIsAlive } = {}) {
  if (!pid) return { stopped: true, escalated: false };

  const signalTree = (hard) => {
    if (process.platform === 'win32') {
      // `/T` takes the whole tree (uvicorn's reloader, npm wrappers), which is
      // the only thing that works on Windows; `/F` is required because a
      // console process has no window to close politely.
      const result = spawnSync('taskkill', ['/pid', String(pid), '/T', ...(hard ? ['/F'] : [])], {
        stdio: 'ignore',
      });
      return result.status === 0 || hard;
    }
    try {
      // Negative pid targets the process group, which exists because
      // spawnChild sets `detached: true`.
      process.kill(-pid, hard ? 'SIGKILL' : 'SIGTERM');
      return true;
    } catch {
      try {
        process.kill(pid, hard ? 'SIGKILL' : 'SIGTERM');
        return true;
      } catch {
        return false;
      }
    }
  };

  if (!force) {
    signalTree(false);
    if (await waitForExit(pid, timeoutMs, isAlive)) {
      log(`stopped pid ${pid}`);
      return { stopped: true, escalated: false };
    }
    log(`pid ${pid} did not stop within ${timeoutMs}ms; escalating`);
  }

  const escalated = signalTree(true);
  // Verified, not assumed: a kill that was never confirmed is how an orphan
  // survives to hold the port on the next launch.
  if (await waitForExit(pid, timeoutMs, isAlive)) {
    log(`force-stopped pid ${pid}`);
    return { stopped: true, escalated: true };
  }
  log(`pid ${pid} could not be stopped even with a forced tree kill`);
  return { stopped: false, escalated };
}

/**
 * Wait for a spawned child handle to exit, with a bounded timeout.
 *
 * On timeout the caller-supplied `killTree` runs (a verified tree kill in
 * production) and the wait resolves as timed out rather than hanging boot.
 * The exit listener always wins a race it reaches first, so a process that
 * exits exactly as the deadline passes is reported by its real exit code.
 */
function waitForChildExit(child, { timeoutMs = 0, killTree = null } = {}) {
  return new Promise((resolve) => {
    if (!child || typeof child.once !== 'function') {
      resolve({ code: null, signal: null, timedOut: false });
      return;
    }
    let settled = false;
    let timer = null;
    const cleanup = () => {
      if (timer) clearTimeout(timer);
      timer = null;
      try {
        child.removeListener('exit', onExit);
      } catch {
        // Listener cleanup is best effort.
      }
      try {
        child.removeListener('error', onError);
      } catch {
        // Listener cleanup is best effort.
      }
    };
    const onExit = (code, signal) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve({ code: code ?? null, signal: signal ?? null, timedOut: false });
    };
    const onError = () => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve({ code: child.exitCode ?? null, signal: child.signalCode ?? null, timedOut: false });
    };
    child.once('exit', onExit);
    child.once('error', onError);
    if (Number.isFinite(timeoutMs) && timeoutMs > 0) {
      timer = setTimeout(async () => {
        if (settled) return;
        settled = true;
        let killResult = null;
        try {
          if (typeof killTree === 'function') killResult = await killTree();
          else child.kill();
        } catch {
          // A failed kill is reported through the timeout, not thrown.
        }
        cleanup();
        resolve({
          code: child.exitCode ?? null,
          signal: child.signalCode ?? null,
          timedOut: true,
          killResult,
        });
      }, timeoutMs);
      if (timer && typeof timer.unref === 'function') timer.unref();
    }
  });
}

/** Poll until `pid` is gone, or the deadline passes. */
async function waitForExit(pid, timeoutMs, isAlive) {
  const deadline = Date.now() + timeoutMs;
  // A short interval so the common case (already gone) returns immediately.
  for (;;) {
    if (!isAlive(pid)) return true;
    if (Date.now() >= deadline) return false;
    await new Promise((resolve) => {
      const timer = setTimeout(resolve, 50);
      if (timer.unref) timer.unref();
    });
  }
}

/**
 * Is a pid alive?
 *
 * `process.kill(pid, 0)` throws EPERM when the process exists but belongs to
 * another user, which still means it is alive — so EPERM is a "yes", not a "no".
 * That distinction is what stops a per-machine install from concluding its own
 * services are dead while they are running as SYSTEM.
 */
function defaultIsAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return Boolean(error && error.code === 'EPERM');
  }
}

module.exports = {
  attachChildLogging,
  bundledToolPath,
  defaultIsAlive,
  freeSpaceBytes,
  isChildDead,
  killProcessTree,
  resolveToolOnPath,
  rotateLogFile,
  spawnChild,
  waitForChildExit,
  waitForExit,
};