import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createDesktopLogger, LOG_LEVELS, redactSupportValue } from '../lib/app-logger.js';

function createMemoryFileSystem() {
  const files = new Map();
  const calls = { appends: [], rotates: [] };
  return {
    files,
    calls,
    appendFileSync(file, text) {
      calls.appends.push({ file, length: text.length });
      files.set(file, `${files.get(file) || ''}${text}`);
    },
    statSync(file) {
      if (!files.has(file)) {
        const error = new Error(`ENOENT: no such file, stat '${file}'`);
        error.code = 'ENOENT';
        throw error;
      }
      return { size: Buffer.byteLength(files.get(file), 'utf8') };
    },
    renameSync(from, to) {
      calls.rotates.push({ from, to });
      files.set(to, files.get(from));
      files.delete(from);
    },
    rmSync(file) {
      if (files.has(file)) files.delete(file);
    },
  };
}

function createLogger(overrides = {}) {
  const memoryFs = createMemoryFileSystem();
  const consoleLines = [];
  const logger = createDesktopLogger({
    fs: memoryFs,
    path,
    console: {
      log: (line) => consoleLines.push(line),
      error: (line) => consoleLines.push(line),
    },
    now: () => new Date('2026-10-03T00:00:00.000Z').getTime(),
    pid: 4242,
    sessionId: 'test-session',
    appName: 'Alpha',
    appVersion: '2.1.0-test',
    logDir: path.join('logs'),
    level: 'info',
    ...overrides,
  });
  return { logger, memoryFs, consoleLines };
}

test('levels have a stable numeric order', () => {
  assert.deepEqual(LOG_LEVELS, { debug: 10, info: 20, warn: 30, error: 40 });
});

test('events below the configured level are discarded everywhere', () => {
  const { logger, memoryFs, consoleLines } = createLogger({ level: 'warn' });
  logger.debug('startup', 'argv', 'parsed arguments');
  logger.info('startup', 'argv', 'parsed arguments');

  assert.equal(consoleLines.length, 0);
  assert.equal(memoryFs.files.size, 0);
});

test('an invalid level falls back to info rather than disabling logging', () => {
  const { logger, memoryFs } = createLogger({ level: 'everything' });
  const entry = logger.info('startup', 'ready', 'ready', { ok: true });

  assert.equal(entry.level, 'info');
  assert.ok(memoryFs.files.has(path.join('logs', 'main.log')));
  assert.ok(memoryFs.files.has(path.join('logs', 'desktop-events.jsonl')));
});

test('each event carries complete diagnostic metadata', () => {
  const { logger, memoryFs } = createLogger();
  const entry = logger.warn('gateway', 'probe', 'Gateway did not answer', { port: 8201, attempt: 3 });
  const text = memoryFs.files.get(path.join('logs', 'main.log'));
  const event = JSON.parse(memoryFs.files.get(path.join('logs', 'desktop-events.jsonl')));

  assert.equal(entry.sequence, 1);
  assert.equal(entry.timestamp, '2026-10-03T00:00:00.000Z');
  assert.equal(entry.pid, 4242);
  assert.equal(entry.sessionId, 'test-session');
  assert.equal(entry.app, 'Alpha');
  assert.equal(entry.appVersion, '2.1.0-test');
  assert.match(text, /\[WARN\] \[pid 4242\] gateway\.probe: Gateway did not answer/);
  assert.match(text, /"port":8201/);
  assert.deepEqual(event.component, 'gateway');
  assert.deepEqual(event.event, 'probe');
  assert.deepEqual(event.context, { port: 8201, attempt: 3 });
});

test('component and event names are validated before an entry can be written', () => {
  const { logger } = createLogger();
  assert.throws(() => logger.info('', 'ready', 'missing component'), /component and event names/);
  assert.throws(() => logger.info('gateway', '', 'missing event'), /component and event names/);
  assert.throws(() => logger.info('gateway probe', 'ready', 'invalid name'), /component and event names/);
});

test('secrets in keys, values, and URLs are redacted without losing the diagnosis', () => {
  const { logger, memoryFs } = createLogger();
  logger.error('gateway', 'config', 'Model request failed', {
    sessionId: 'diagnostic-session',
    OPENROUTER_API_KEY: 'sk-live-secret-value',
    nested: { authorization: 'Bearer abc123', port: 8201 },
    url: 'https://user:hunter2@example.invalid/api?api_key=sk-live-secret-value',
    privateKey: '-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----',
  });
  const event = JSON.parse(memoryFs.files.get(path.join('logs', 'desktop-events.jsonl')));

  assert.equal(event.context.sessionId, 'diagnostic-session');
  assert.equal(event.context.OPENROUTER_API_KEY, '[redacted]');
  assert.equal(event.sessionId, 'test-session');
  assert.equal(event.context.nested.authorization, '[redacted]');
  assert.equal(event.context.privateKey, '[redacted]');
  assert.equal(event.context.nested.port, 8201);
  assert.equal(event.context.url, 'https://[redacted]@example.invalid/api?api_key=[redacted]');
  assert.doesNotMatch(JSON.stringify(event), /sk-live-secret-value|hunter2|abc123/);
});

test('circular diagnostic context cannot break logging', () => {
  const { logger, memoryFs } = createLogger();
  const context = { port: 8201 };
  context.self = context;
  const entry = logger.info('gateway', 'probe', 'checked', context);

  assert.equal(entry.context.self, '[circular]');
  assert.ok(memoryFs.files.has(path.join('logs', 'desktop-events.jsonl')));
});

test('rotation preserves bounded history instead of deleting diagnostics', () => {
  const memoryFs = createMemoryFileSystem();
  const textFile = path.join('logs', 'main.log');
  const eventsFile = path.join('logs', 'desktop-events.jsonl');
  memoryFs.files.set(textFile, 'x'.repeat(1024));
  memoryFs.files.set(eventsFile, 'y'.repeat(1024));
  const logger = createDesktopLogger({
    fs: memoryFs,
    path,
    console: { log: () => {}, error: () => {} },
    now: Date.now,
    pid: 1,
    sessionId: 'rotation',
    appName: 'Alpha',
    appVersion: 'test',
    logDir: path.join('logs'),
    maxBytes: 1024,
    backups: 2,
  });

  logger.info('gateway', 'probe', 'rotated probe', { port: 8201 });

  assert.equal(memoryFs.files.get(`${textFile}.1`).length, 1024);
  assert.equal(memoryFs.files.get(`${eventsFile}.1`).length, 1024);
  assert.ok(memoryFs.files.get(textFile).includes('rotated probe'));
  assert.ok(memoryFs.files.get(eventsFile).includes('rotated probe'));
});

test('timers record elapsed time for slow operations', () => {
  let now = 1000;
  const { logger } = createLogger({ now: () => now });
  const done = logger.time('gateway', 'probe', { port: 8201 });
  now += 275;
  const entry = done('answered');

  assert.equal(entry.context.durationMs, 275);
  assert.equal(entry.message, 'answered');
});

test('service logging binds the service name automatically', () => {
  const { logger, memoryFs } = createLogger();
  const gateway = logger.service('gateway');
  gateway.info('spawned', 'Gateway process started', { pid: 9999 });

  assert.match(memoryFs.files.get(path.join('logs', 'main.log')), /gateway\.spawned/);
  const event = JSON.parse(memoryFs.files.get(path.join('logs', 'desktop-events.jsonl')));
  assert.deepEqual(event.context, { service: 'gateway', pid: 9999 });
});

test('filesystem failures are contained and surfaced once on the console', () => {
  const consoleLines = [];
  const logger = createDesktopLogger({
    fs: {
      appendFileSync() {
        throw new Error('ENOSPC: no space left on device');
      },
      statSync() {
        throw new Error('ENOSPC: no space left on device');
      },
      renameSync() {},
      rmSync() {},
    },
    path,
    console: { log: () => {}, error: (line) => consoleLines.push(line) },
    now: Date.now,
    pid: 1,
    sessionId: 'failing-disk',
    appName: 'Alpha',
    appVersion: 'test',
    logDir: path.join('logs'),
  });

  assert.doesNotThrow(() => logger.error('startup', 'disk', 'cannot write', { path: 'x' }));
  assert.equal(consoleLines.length, 1);
  assert.match(consoleLines[0], /desktop logging failed/i);
});

test('support tooling reuses the same redactor as the log files', () => {
  assert.equal(
    redactSupportValue('https://user:secret@example.invalid/api?token=abc'),
    'https://[redacted]@example.invalid/api?token=[redacted]',
  );
});

test('a real log directory receives both human and machine-readable logs', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'alpha-logger-'));
  try {
    const logger = createDesktopLogger({
      fs,
      path,
      console: { log: () => {}, error: () => {} },
      now: Date.now,
      pid: process.pid,
      sessionId: 'real-fs',
      appName: 'Alpha',
      appVersion: 'test',
      logDir: dir,
    });
    logger.info('startup', 'ready', 'logger smoke test', { ok: true });

    const text = fs.readFileSync(path.join(dir, 'main.log'), 'utf8');
    const event = JSON.parse(fs.readFileSync(path.join(dir, 'desktop-events.jsonl'), 'utf8'));
    assert.match(text, /logger smoke test/);
    assert.equal(event.event, 'ready');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
