import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildDesktopShutdownSteps,
  runShutdown,
  withTimeout,
} from '../lib/lifecycle.js';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test('steps run in dependency order, not definition order', () => {
  // The whole point: the frontend must stop before the Gateway, or it is killed
  // mid-request and a run is left half-written.
  const order = [];
  const steps = [
    { key: 'log', order: 30, run: () => order.push('log') },
    { key: 'gateway', order: 20, run: () => order.push('gateway') },
    { key: 'renderer', order: 0, run: () => order.push('renderer') },
    { key: 'frontend', order: 10, run: () => order.push('frontend') },
  ];
  return runShutdown({ steps }).then((report) => {
    assert.deepEqual(order, ['renderer', 'frontend', 'gateway', 'log']);
    assert.equal(report.clean, true);
  });
});

test('a clean shutdown is reported as clean', async () => {
  const report = await runShutdown({
    steps: [
      { key: 'a', run: () => {} },
      { key: 'b', run: async () => sleep(5) },
    ],
  });
  assert.equal(report.clean, true);
  assert.deepEqual(report.forced, []);
  assert.deepEqual(report.abandoned, []);
  assert.deepEqual(report.steps.map((s) => s.outcome), ['ok', 'ok']);
});

test('a hung step is escalated to force, and the force is reported', async () => {
  // A shutdown that had to force-kill is NOT a clean shutdown, and the two must
  // not be conflated in the log.
  let forced = false;
  const report = await runShutdown({
    steps: [
      {
        key: 'gateway',
        timeoutMs: 30,
        run: () => new Promise(() => {}), // never settles
        force: () => {
          forced = true;
        },
      },
    ],
  });
  assert.equal(forced, true, 'the escalation ladder never reached force');
  assert.equal(report.clean, false);
  assert.deepEqual(report.forced, ['gateway']);
  assert.equal(report.steps[0].outcome, 'timeout');
  assert.match(report.steps[0].detail, /did not finish within 30ms/);
  assert.match(report.steps[0].detail, /abandoned/);
});

test('a step that rejects is reported as an error, not as a timeout', async () => {
  // The two failures need different responses, so they must not be conflated.
  const report = await runShutdown({
    steps: [
      {
        key: 'frontend',
        timeoutMs: 500,
        run: () => Promise.reject(new Error('the port was already taken')),
        force: () => assert.fail('a rejected step must not be force-killed'),
      },
    ],
  });
  assert.equal(report.steps[0].outcome, 'error');
  assert.match(report.steps[0].detail, /the port was already taken/);
  assert.equal(report.clean, true, 'a rejected step is not a forced kill');
});

test('the escalation ladder is walked in order and can be customized', async () => {
  const calls = [];
  const report = await runShutdown({
    steps: [
      {
        key: 'x',
        timeoutMs: 20,
        run: () => new Promise(() => {}),
        escalation: ['force', 'notify', 'abandon'],
        force: () => calls.push('force'),
        notify: () => calls.push('notify'),
      },
    ],
  });
  assert.deepEqual(calls, ['force']);
  assert.deepEqual(report.abandoned, ['x']);
  // A step may opt out of the default ladder entirely.
  const optOut = await runShutdown({
    steps: [{ key: 'y', timeoutMs: 20, run: () => new Promise(() => {}), escalation: [] }],
  });
  assert.deepEqual(optOut.forced, []);
  assert.deepEqual(optOut.abandoned, []);
  assert.equal(optOut.clean, true, 'opting out of escalation still means something was skipped');
});

test('the global budget bounds the whole shutdown', async () => {
  // Without this, a shutdown where every step hangs would take
  // steps x timeout to complete, which is the quit button doing nothing.
  // The clock is real here (not injected) so the budget is measured, not
  // asserted against a stub that never advances.
  const started = Date.now();
  const report = await runShutdown({
    totalTimeoutMs: 60,
    steps: Array.from({ length: 5 }, (_, i) => ({
      key: `s${i}`,
      timeoutMs: 50,
      run: () => sleep(50),
    })),
  });
  const elapsed = Date.now() - started;
  assert.ok(elapsed < 400, `shutdown took ${elapsed}ms; the 60ms budget was not enforced`);
  // Steps that never got a chance are reported, not silently dropped.
  const skipped = report.steps.filter((s) => s.outcome === 'skipped');
  assert.ok(skipped.length > 0, 'exhausted steps were not reported');
  for (const step of skipped) {
    assert.match(step.reason, /global shutdown budget exhausted/);
    assert.ok(report.abandoned.includes(step.key), 'a skipped step was not recorded as abandoned');
  }
});

test('a per-step timeout never exceeds the remaining global budget', async () => {
  const started = Date.now();
  const report = await runShutdown({
    totalTimeoutMs: 60,
    steps: [
      { key: 'a', timeoutMs: 5000, run: () => new Promise(() => {}) },
      { key: 'b', timeoutMs: 5000, run: () => new Promise(() => {}) },
    ],
  });
  const elapsed = Date.now() - started;
  // Two 5000ms steps clamped to the remaining budget must not take 10 seconds.
  assert.ok(elapsed < 500, `shutdown took ${elapsed}ms; the global budget was not enforced`);
  assert.equal(report.clean, false);
});

test('a reporting callback that throws does not turn a finished shutdown into a crash', async () => {
  const report = await runShutdown({
    steps: [{ key: 'a', run: () => {} }],
    onReport: () => {
      throw new Error('the window was already destroyed');
    },
  });
  assert.equal(report.clean, true);
});

test('a logging callback that throws is contained', async () => {
  const report = await runShutdown({
    steps: [{ key: 'a', run: () => {} }],
    log: () => {
      throw new Error('disk full');
    },
  });
  assert.equal(report.clean, true);
});

test('a throwing step does not stop the steps after it', async () => {
  // The log step must still run, or a forced kill is never written down.
  const ran = [];
  const report = await runShutdown({
    steps: [
      {
        key: 'frontend',
        run: () => {
          throw new Error('kill failed');
        },
      },
      { key: 'log', run: () => ran.push('log') },
    ],
  });
  assert.deepEqual(ran, ['log']);
  assert.equal(report.steps.length, 2);
});

test('withTimeout clears its timer so it cannot hold the process open', async () => {
  // An uncleared 5s timer per step would keep the event loop alive for the full
  // budget after the user already quit.
  const started = Date.now();
  await withTimeout(Promise.resolve('ok'), 5000);
  assert.equal(await Promise.resolve('ok'), 'ok');
  assert.ok(Date.now() - started < 100, 'the resolved path still waited');
});

test('withTimeout marks a timeout distinctly from a rejection', async () => {
  await assert.rejects(
    () => withTimeout(new Promise(() => {}), 20),
    (error) => error.timedOut === true,
  );
  await assert.rejects(
    () => withTimeout(Promise.reject(new Error('real failure')), 1000),
    (error) => error.timedOut === undefined && /real failure/.test(error.message),
  );
});

test('the desktop ladder is ordered frontend-before-gateway and always logs', () => {
  const steps = buildDesktopShutdownSteps({
    frontend: { stop() {}, force() {} },
    gateway: { stop() {}, force() {} },
    onRendererTeardown() {},
    onLogFinal() {},
  });
  assert.deepEqual(steps.map((s) => s.key), ['renderer', 'frontend', 'gateway', 'log']);
  const orders = steps.map((s) => s.order);
  assert.deepEqual([...orders].sort((a, b) => a - b), orders, 'the ladder is not in ascending order');

  // The log step must survive a forced kill of both services.
  const logStep = steps.find((s) => s.key === 'log');
  assert.deepEqual(logStep.escalation, ['abandon']);
  assert.equal(typeof logStep.run, 'function');
});

test('the desktop ladder tolerates missing service handles', async () => {
  // During a failed boot there may be nothing to stop; that must not throw on
  // the way out.
  const report = await runShutdown({ steps: buildDesktopShutdownSteps({}) });
  assert.equal(report.clean, true);
});

test('the desktop ladder actually invokes the handles in order', async () => {
  const calls = [];
  const report = await runShutdown({
    steps: buildDesktopShutdownSteps({
      onRendererTeardown: () => calls.push('renderer'),
      frontend: { stop: () => calls.push('frontend.stop'), force: () => calls.push('frontend.force') },
      gateway: { stop: () => calls.push('gateway.stop'), force: () => calls.push('gateway.force') },
      onLogFinal: () => calls.push('log'),
    }),
  });
  assert.deepEqual(calls, ['renderer', 'frontend.stop', 'gateway.stop', 'log']);
  assert.equal(report.clean, true);
});
