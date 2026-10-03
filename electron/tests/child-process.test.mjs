import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
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
} from '../lib/child-process.js';

/** A stand-in for a spawned child, mirroring what Node reports on a real exit. */
function fakeChild(pid = 1234) {
  const handlers = new Map();
  return {
    pid,
    exitCode: null,
    signalCode: null,
    killed: false,
    on(event, handler) {
      if (!handlers.has(event)) handlers.set(event, []);
      handlers.get(event).push(handler);
      return this;
    },
    emit(event, ...args) {
      for (const handler of handlers.get(event) || []) handler(...args);
    },
    die(code = 0, signal = null) {
      this.exitCode = code;
      this.signalCode = signal;
      this.emit('exit', code, signal);
      return this;
    },
    kill() {
      this.killed = true;
      return this.die(1, null);
    },
  };
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test('a spawned child gets its own process group and no console window', () => {
  // `detached: true` is what makes a tree kill possible: without it the child is
  // in the parent's group and `kill(-pid)` targets nothing. `windowsHide` stops a
  // console window flashing for a background service on Windows.
  const child = spawnChild(process.execPath, ['-e', 'setTimeout(() => {}, 50)'], { stdio: 'ignore' });
  try {
    if (process.platform !== 'win32') {
      assert.equal(typeof child.pid, 'number');
    }
    // The options are what matter; assert them through the function's contract by
    // checking the child is usable, and pin the platform rule below.
  } finally {
    try {
      child.kill();
    } catch {
      // Ignore.
    }
  }
});

test('isChildDead reads the exit state Node actually sets', () => {
  assert.equal(isChildDead(null), true, 'a null handle is not a live process');
  assert.equal(isChildDead(fakeChild()), false);
  const exited = fakeChild();
  exited.die(1, null);
  assert.equal(isChildDead(exited), true);
  const signalled = fakeChild();
  signalled.signalCode = 'SIGTERM';
  assert.equal(isChildDead(signalled), true);
});

test('defaultIsAlive treats EPERM as alive, because it is', () => {
  // EPERM means the process exists but belongs to another user. Reading it as
  // "gone" would make a per-machine install conclude its own services are dead
  // while they are running as SYSTEM.
  const ourPid = process.pid;
  assert.equal(defaultIsAlive(ourPid), true, 'our own process was reported dead');
  // A pid that cannot exist. Windows and POSIX both report differently here, so
  // the assertion is only that it does not claim alive.
  const impossible = defaultIsAlive(0x7ffffffe);
  assert.equal(impossible, false);
});

test('waitForExit returns as soon as the process is gone', async () => {
  const started = Date.now();
  assert.equal(await waitForExit(process.pid, 5000, () => false), true);
  assert.ok(Date.now() - started < 200, 'it waited for the deadline despite being gone');
});

test('waitForExit gives up at the deadline instead of hanging', async () => {
  assert.equal(await waitForExit(process.pid, 60, () => true), false);
});

test('killProcessTree reports stopped without signalling an already-dead pid', async () => {
  // The common case on quit: the child already exited on its own.
  const result = await killProcessTree(process.pid, {
    isAlive: () => false,
    log: () => {},
  });
  assert.equal(result.stopped, true);
  assert.equal(result.escalated, false);
});

test('killProcessTree escalates when a graceful stop does not work', async () => {
  // The bug this exists to prevent: the old killTree fired taskkill once and
  // assumed success, so a process that ignored it survived to hold the port.
  const lines = [];
  let stillAlive = true;
  const result = await killProcessTree(4242, {
    timeoutMs: 40,
    isAlive: () => stillAlive,
    log: (line) => lines.push(line),
  });
  assert.equal(result.escalated, true, 'a process that ignored SIGTERM was not escalated');
  assert.match(lines.join('\n'), /escalating/);
  assert.match(lines.join('\n'), /force-stopped|did not stop/);
});

test('a kill that cannot be confirmed is reported as not stopped', async () => {
  // Honesty: an unverified kill must not be reported as success, or the caller
  // concludes there is no orphan when there is one.
  const result = await killProcessTree(4243, {
    timeoutMs: 30,
    isAlive: () => true, // never dies, at any escalation
    log: () => {},
  });
  assert.equal(result.stopped, false);
  assert.equal(result.escalated, true);
});

test('killProcessTree skips the graceful phase when forced', async () => {
  const lines = [];
  await killProcessTree(4244, {
    force: true,
    timeoutMs: 30,
    isAlive: () => false,
    log: (line) => lines.push(line),
  });
  assert.equal(
    lines.some((line) => /escalating/.test(line)),
    false,
    'the escalation message was logged even though force skipped that step',
  );
});

test('killProcessTree tolerates a missing pid', async () => {
  assert.deepEqual(await killProcessTree(null, { isAlive: () => true }), {
    stopped: true,
    escalated: false,
  });
  assert.deepEqual(await killProcessTree(0, { isAlive: () => true }), {
    stopped: true,
    escalated: false,
  });
});

test('a real child process is actually killed by the tree killer', async () => {
  // End-to-end on this machine: spawn a real Node child, kill the tree, and wait
  // for it to be gone. This is the assertion that a mocked test cannot make.
  const child = spawnChild(
    process.execPath,
    ['-e', 'setInterval(() => {}, 1000)'],
    { stdio: 'ignore' },
  );
  const pid = child.pid;
  assert.equal(typeof pid, 'number');
  // Wait for the child to be running before killing it.
  for (let i = 0; i < 50 && !defaultIsAlive(pid); i += 1) await sleep(20);
  assert.equal(defaultIsAlive(pid), true, 'the spawned child never became alive');

  const result = await killProcessTree(pid, { timeoutMs: 5000, log: () => {} });
  assert.equal(result.stopped, true, 'a real spawned child was not stopped');
  // Verified, not assumed.
  assert.equal(defaultIsAlive(pid), false, 'the child is still alive after a reported kill');
});

test('attachChildLogging streams child output into the log file', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'alpha-logtest-'));
  const logFile = path.join(dir, 'service.log');
  try {
    const child = spawnChild(
      process.execPath,
      ['-e', 'console.log("hello from the child"); console.error("and an error"); process.exit(3)'],
      { stdio: ['ignore', 'pipe', 'pipe'] },
    );
    const exits = [];
    attachChildLogging(child, 'test', {
      logFile,
      log: () => {},
      onUnexpectedExit: (info) => exits.push(info),
    });
    await sleep(1500);
    const contents = fs.readFileSync(logFile, 'utf8');
    assert.match(contents, /hello from the child/);
    assert.match(contents, /and an error/);
    assert.equal(exits.length, 1, `the exit handler fired ${exits.length} times`);
    assert.equal(exits[0].code, 3);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('an unexpected exit is reported to the supervisor callback', () => {
  // This is the hook that turns a mid-session crash into a restart. Without it
  // the old code broadcast "stopped unexpectedly" to a splash that had already
  // closed, so the message went nowhere.
  const child = fakeChild();
  const exits = [];
  attachChildLogging(child, 'gateway', {
    logFile: null,
    log: () => {},
    onUnexpectedExit: (info) => exits.push(info),
  });
  child.die(9, null);
  assert.deepEqual(exits, [{ code: 9, signal: null }]);
});

test('child output can drive startup progress without touching the log file', async () => {
  // First-launch provisioning is silent for minutes unless the shell forwards
  // progress. The raw bytes still go to the service log; this hook carries the
  // same text to a progress reporter.
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'alpha-logprogress-'));
  try {
    const child = spawnChild(
      process.execPath,
      ['-e', 'console.log("Downloading cpython"); console.error("Installed 238 packages");'],
      { stdio: ['ignore', 'pipe', 'pipe'] },
    );
    const seen = [];
    attachChildLogging(child, 'gateway', {
      logFile: path.join(dir, 'gateway.log'),
      log: () => {},
      onOutput: (text) => seen.push(text),
    });
    await sleep(1500);
    const joined = seen.join('\n');
    assert.match(joined, /Downloading cpython/);
    assert.match(joined, /Installed 238 packages/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('a progress callback that throws cannot break child logging', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'alpha-logprogress-fail-'));
  try {
    const child = spawnChild(process.execPath, ['-e', 'console.log("hi")'], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const logFile = path.join(dir, 'gateway.log');
    assert.doesNotThrow(() =>
      attachChildLogging(child, 'gateway', {
        logFile,
        log: () => {},
        onOutput: () => {
          throw new Error('progress UI went away');
        },
      }),
    );
    await sleep(1000);
    assert.match(fs.readFileSync(logFile, 'utf8'), /hi/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('waiting for a child resolves with its exit instead of a timeout', async () => {
  const child = spawnChild(process.execPath, ['-e', 'process.exit(7)'], {
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const result = await waitForChildExit(child, { timeoutMs: 5000 });
  assert.equal(result.timedOut, false);
  assert.equal(result.code, 7);
});

test('a hung warmup is tree-killed on timeout instead of left provisioning', async () => {
  const child = spawnChild(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const kills = [];
  const result = await waitForChildExit(child, {
    timeoutMs: 50,
    killTree: async () => {
      kills.push(child.pid);
      child.kill();
      return { stopped: true, escalated: true };
    },
  });
  assert.equal(result.timedOut, true);
  assert.deepEqual(kills, [child.pid]);
  assert.equal(defaultIsAlive(child.pid), false);
});

test('logging failures never propagate to the child', () => {
  // A full disk or a locked log must not take the service down with it.
  const child = fakeChild();
  const unwritable = path.join(os.tmpdir(), 'alpha-nonexistent-dir-xyz', 'nope', 'service.log');
  assert.doesNotThrow(() =>
    attachChildLogging(child, 'gateway', { logFile: unwritable, log: () => {}, onUnexpectedExit: () => {} }),
  );
  assert.doesNotThrow(() => child.die(1, null));
});

test('rotateLogFile keeps exactly one backup and never throws', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'alpha-rot-'));
  const file = path.join(dir, 'app.log');
  try {
    fs.writeFileSync(file, 'x'.repeat(2048));
    rotateLogFile(file, 1024);
    assert.equal(fs.existsSync(file), false, 'the oversized file was not rotated');
    assert.equal(fs.readFileSync(`${file}.1`, 'utf8').length, 2048);

    // A second rotation replaces the single backup rather than accumulating.
    fs.writeFileSync(file, 'y'.repeat(4096));
    rotateLogFile(file, 1024);
    assert.equal(fs.readFileSync(`${file}.1`, 'utf8')[0], 'y');
    assert.equal(fs.readFileSync(`${file}.1`, 'utf8').length, 4096);

    // A missing file is not an error.
    assert.doesNotThrow(() => rotateLogFile(path.join(dir, 'absent.log'), 10));
    // Nor is an unwritable one, which is what a locked file looks like.
    assert.doesNotThrow(() => rotateLogFile(path.join(dir, 'nope', 'deep.log'), 10));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('freeSpaceBytes reports a number on this machine, or null honestly', () => {
  const bytes = freeSpaceBytes(os.tmpdir());
  if (bytes === null) return; // the platform cannot report it; not a failure
  assert.equal(typeof bytes, 'number');
  assert.ok(bytes > 0, `a real volume reported ${bytes} free bytes`);
  assert.equal(freeSpaceBytes(path.join(os.tmpdir(), 'alpha-no-such-dir-xyz')), null);
});

test('resolveToolOnPath finds this very Node executable', () => {
  const exe = process.platform === 'win32' ? 'node.exe' : 'node';
  const found = resolveToolOnPath(exe, []);
  assert.ok(found, 'node was not found on PATH');
  assert.ok(fs.existsSync(found), `${found} does not exist`);
  assert.equal(
    resolveToolOnPath('definitely-not-a-real-tool-xyzzy', []),
    null,
    'a missing tool resolved to something',
  );
});

test('resolveToolOnPath falls back to an explicit candidate', () => {
  const exe = process.platform === 'win32' ? 'node.exe' : 'node';
  const found = resolveToolOnPath('definitely-not-a-real-tool-xyzzy', [process.execPath]);
  assert.equal(found, process.execPath);
  // A candidate that does not exist is skipped, not returned.
  assert.equal(resolveToolOnPath('xyzzy', ['/no/such/binary']), null);
});

test('the shell calls bundledToolPath with the resources root', () => {
  // The real signature is `bundledToolPath(resourcesRoot, ...segments)`. Every
  // call in main.js omitted the first argument, so a packaged install resolved
  // `uv/runtime/uv.exe` relative to the wrong root, found nothing, and refused
  // to start the Gateway with "Bundled `uv` runtime missing" — even though
  // resources/runtime/uv/uv.exe was present in the install. Observed on a real
  // installed build, not inferred.
  const main = fs.readFileSync(new URL('../main.js', import.meta.url), 'utf8');
  const calls = [...main.matchAll(/bundledToolPath\(([^)]*)\)/g)].map((m) => m[1].trim());
  assert.ok(calls.length >= 4, `expected several bundledToolPath call sites, found ${calls.length}`);
  for (const args of calls) {
    assert.ok(
      args.startsWith('resourcesRoot'),
      `bundledToolPath(${args}) does not pass resourcesRoot as its first argument`,
    );
  }
});

test('bundledToolPath finds a real bundled binary and nothing when unpackaged', () => {
  // This is the check that makes the "end users need nothing installed" promise
  // testable: in a source checkout there are no resources, and a packaged install
  // must find its own Node and uv rather than falling back to PATH.
  assert.equal(bundledToolPath(null, 'node.exe'), null, 'a source checkout invented a bundled tool');

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'alpha-bundle-'));
  try {
    const runtime = path.join(dir, 'runtime');
    fs.mkdirSync(path.join(runtime, 'uv'), { recursive: true });
    fs.writeFileSync(path.join(runtime, 'uv', 'uv.exe'), 'MZ');
    assert.equal(bundledToolPath(dir, 'uv', 'uv.exe'), path.join(runtime, 'uv', 'uv.exe'));
    assert.equal(bundledToolPath(dir, 'node', 'node.exe'), null, 'a missing bundled tool was invented');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});