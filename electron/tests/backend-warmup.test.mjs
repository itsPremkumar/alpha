import test from 'node:test';
import assert from 'node:assert/strict';
import { createWarmupProgress, summarizeWarmupLine } from '../lib/backend-warmup.js';

test('warmup output is mapped to user-facing stages', () => {
  assert.deepEqual(summarizeWarmupLine('Downloading cpython-3.12.15-windows-x86_64-none (21.0MiB)'), {
    stage: 'downloading-runtime',
    message: 'Downloading Python runtime…',
  });
  assert.deepEqual(summarizeWarmupLine('Creating virtual environment at: C:\\x\\backend-venv'), {
    stage: 'creating-environment',
    message: 'Creating backend environment…',
  });
  assert.deepEqual(summarizeWarmupLine('Installed 238 packages in 2m 39s'), {
    stage: 'installed',
    message: 'Installed 238 packages in 2m 39s',
  });
  assert.deepEqual(summarizeWarmupLine('error: failed to download Python'), {
    stage: 'failed',
    message: 'error: failed to download Python',
  });
});

test('blank and noisy output never reaches the splash screen', () => {
  assert.equal(summarizeWarmupLine(''), null);
  assert.equal(summarizeWarmupLine('   '), null);
  const long = `x${'y'.repeat(500)}`;
  const summarized = summarizeWarmupLine(long);
  assert.ok(summarized.message.length <= 180, 'a pathological line would overflow the splash');
});

test('progress is throttled but stage changes are always reported', () => {
  let now = 0;
  const reported = [];
  const progress = createWarmupProgress({
    now: () => now,
    minIntervalMs: 5000,
    onStatus: (message, detail) => reported.push({ message, detail }),
  });
  progress.report('Downloading cpython');
  progress.report('Downloading cpython (10MiB)');
  now += 1000;
  progress.report('Downloading cpython (20MiB)');
  assert.equal(reported.length, 1, 'every download chunk would spam the splash and the log');
  now += 5000;
  progress.report('Installed 10 packages');
  assert.equal(reported.length, 2);
  assert.match(reported[1].message, /Installing backend packages/);
  progress.finish('Backend environment ready');
  assert.equal(reported.length, 3);
});
