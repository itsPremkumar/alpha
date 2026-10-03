import test from 'node:test';
import assert from 'node:assert/strict';
import { buildSupportBundle, summarizeSupportBundle } from '../lib/support-bundle.js';

function healthyBundle() {
  return buildSupportBundle({
    generatedAt: '2026-10-03T00:00:00.000Z',
    app: { name: 'Alpha', version: '2.1.0', packaged: true },
    runtime: { platform: 'win32', arch: 'x64', electron: '44.3.0', node: '24.20.0', chrome: '152' },
    install: {
      executable: 'C:\\Apps\\Alpha\\Alpha.exe',
      resources: 'C:\\Apps\\Alpha\\resources',
      runtimes: { uv: true, node: true },
    },
    services: [
      { key: 'gateway', label: 'Gateway', state: 'healthy', port: 8201, error: null, restarts: 0 },
      { key: 'frontend', label: 'Frontend', state: 'healthy', port: 3000, error: null, restarts: 0 },
    ],
    logs: {
      main: ['Main window ready'],
      events: ['{"component":"window","event":"ready"}'],
      gateway: ['Installed 238 packages in 2m 39s'],
    },
    notes: [],
  });
}

test('a healthy bundle reports no problems', () => {
  const summary = summarizeSupportBundle(healthyBundle());
  assert.deepEqual(summary.problems, []);
  assert.match(summary.lines.join('\n'), /Gateway healthy/);
  assert.match(summary.lines.join('\n'), /Frontend healthy/);
});

test('the screenshot failure is diagnosed with its cause and log pointer', () => {
  const bundle = {
    ...healthyBundle(),
    services: [
      {
        key: 'gateway',
        label: 'Gateway',
        state: 'failed',
        port: 8201,
        error: 'Gateway did not become healthy within 300s. See gateway.log.',
        restarts: 0,
      },
    ],
    logs: { main: ['Startup failed: Gateway did not become healthy within 300s'] },
  };
  const summary = summarizeSupportBundle(bundle);
  assert.ok(summary.problems.length >= 1, 'the boot timeout produced no diagnosis');
  assert.match(summary.problems.join('\n'), /did not become healthy/);
  assert.match(summary.problems.join('\n'), /gateway\.log/);
});

test('log tails are redacted before they leave the machine', () => {
  const bundle = buildSupportBundle({
    generatedAt: '2026-10-03T00:00:00.000Z',
    app: { name: 'Alpha', version: '2.1.0', packaged: true },
    runtime: {},
    install: {},
    services: [],
    logs: {
      main: ['using https://user:hunter2@example.invalid/?api_key=sk-live-value'],
    },
    notes: [],
  });
  const text = JSON.stringify(bundle);
  assert.doesNotMatch(text, /hunter2|sk-live-value/);
  assert.match(text, /\[redacted\]/);
});

test('missing bundled runtimes are reported as install problems', () => {
  const bundle = {
    ...healthyBundle(),
    install: { executable: 'C:\\Apps\\Alpha\\Alpha.exe', runtimes: { uv: false, node: true } },
  };
  const summary = summarizeSupportBundle(bundle);
  assert.match(summary.problems.join('\n'), /bundled uv runtime is missing/);
});
