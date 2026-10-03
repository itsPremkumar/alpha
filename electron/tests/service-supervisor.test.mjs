import test from 'node:test';
import assert from 'node:assert/strict';
import { ServiceSupervisor, STATES, resolveTimings, topologicalOrder } from '../lib/service-supervisor.js';

/**
 * A stand-in for a spawned child process.
 *
 * `die()` mirrors what Node actually does on a real exit: it records
 * `exitCode`/`signalCode` BEFORE emitting `exit`. A fake that only emitted the
 * event would let the supervisor believe a dead process was still alive.
 */
function fakeChild(pid = 4242) {
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
    die(code = 1, signal = null) {
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

/** Poll `predicate` until it holds, or the deadline passes. */
async function waitFor(predicate, timeoutMs, stepMs = 20) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (predicate()) return true;
    if (Date.now() >= deadline) return false;
    await sleep(stepMs);
  }
}

/** A supervisor whose probes, clocks and spawns are all under test control. */
function makeSupervisor(options = {}) {
  const spawned = [];
  const events = [];
  // `spawn` returns the CHILD (that is the child-process contract), so a test
  // that wants the record takes it from `spawned` afterwards.
  const spawn = (key, context) => {
    const child = fakeChild(1000 + spawned.length);
    spawned.push({ key, context, child });
    return child;
  };
  // `overrides.services` may be a function so a custom definition can still use
  // the recording `spawn`.
  // Restarts are exercised with a short, jitter-free policy so a test never
  // waits out a real backoff. It is applied per service, which is the only
  // supported way in: `normalizeService` resolves and freezes the policy once,
  // so assigning to `budget.policy` afterwards reaches past the contract.
  const policy = { baseDelayMs: 10, jitterRatio: 0, ...(options.policy || {}) };
  const declared =
    typeof options.services === 'function'
      ? options.services(spawn, () => options.probe, policy)
      : [
          { key: 'gateway', label: 'Gateway', spawnService: (c) => spawn('gateway', c), probe: () => true },
          {
            key: 'frontend',
            label: 'Frontend',
            dependsOn: ['gateway'],
            spawnService: (c) => spawn('frontend', c),
            probe: () => true,
          },
        ];
  // Probes default to healthy, and a service's own `policy` always wins over the
  // harness policy, so a test can still pin an exact budget.
  const services = declared.map((service) => ({
    probe: () => true,
    ...service,
    policy: service.policy || policy,
  }));
  const supervisor = new ServiceSupervisor({
    services,
    log: (line) => events.push(line),
    onStateChange: (e) => events.push(`state:${e.key}=${e.state}`),
    timings: {
      healthIntervalMs: 60000,
      pollIntervalMs: 20,
      startupTimeoutMs: 2000,
      stopTimeoutMs: 30,
      ...(options.timings || {}),
    },
    random: options.random || (() => 0.5),
    ...(options.supervisor || {}),
  });
  return { supervisor, spawned, events, spawn };
}

test('services start in dependency order and report healthy', async () => {
  const { supervisor, spawned } = makeSupervisor();
  const result = await supervisor.start();
  assert.deepEqual(result.started, ['gateway', 'frontend']);
  assert.deepEqual(spawned.map((s) => s.key), ['gateway', 'frontend']);
  assert.equal(supervisor.state('gateway'), STATES.healthy);
  assert.equal(supervisor.state('frontend'), STATES.healthy);
  assert.equal(supervisor.isHealthy('gateway'), true);
  await supervisor.stop();
});

test('a child that dies during startup names the cause instead of timing out', async () => {
  // The old boot loop polled for up to ten minutes and surfaced the real reason
  // only through a separate side channel. This must be the actual error, and it
  // must arrive in about one poll interval, not one timeout.
  const { supervisor } = makeSupervisor({
    probe: () => false, // never becomes healthy, so the death is the outcome
    services: (spawn) => [
      {
        key: 'gateway',
        label: 'Gateway',
        spawnService: (c) => {
          const child = spawn('gateway', c);
          setTimeout(() => child.die(3, null), 5);
          return child;
        },
        probe: () => false,
      },
    ],
  });
  const startedAt = Date.now();
  await assert.rejects(
    () => supervisor.start(),
    (error) => /exited before it became healthy/.test(error.message),
  );
  assert.ok(Date.now() - startedAt < 1500, 'boot waited for the timeout instead of the exit');
  assert.equal(supervisor.state('gateway'), STATES.failed);
  assert.match(supervisor.status()[0].error, /exited before it became healthy/);
});

test('a spawn error is reported with its own message', async () => {
  const supervisor = new ServiceSupervisor({
    services: [
      {
        key: 'gateway',
        label: 'Gateway',
        spawnService: () => {
          throw new Error('Bundled `uv` runtime missing');
        },
        probe: () => true,
      },
    ],
  });
  await assert.rejects(
    () => supervisor.start(),
    (error) => /Could not start Gateway: Bundled `uv` runtime missing/.test(error.message),
  );
  assert.equal(supervisor.state('gateway'), STATES.failed);
  assert.match(supervisor.status()[0].error, /Bundled `uv` runtime missing/);
});

test('an unexpected exit schedules a bounded restart', async () => {
  const { supervisor, spawned } = makeSupervisor({
    services: (spawn) => [
      { key: 'gateway', label: 'Gateway', spawnService: (c) => spawn('gateway', c), probe: () => true },
    ],
  });
  await supervisor.start();
  assert.equal(spawned.length, 1);

  const decision = supervisor.restartService('gateway', { reason: 'test crash', uptimeMs: 0 });
  assert.equal(decision.action, 'restart');
  assert.equal(supervisor.state('gateway'), STATES.waiting);
  // The delay comes from the restart budget, not a magic constant.
  assert.ok(decision.delayMs > 0 && decision.delayMs <= 30000, `delay ${decision.delayMs}`);
  await supervisor.stop();
});

test('a spent budget stops respawning and says so plainly', async () => {
  // The policy is declared on the service, not mutated onto the live budget
  // afterwards: `normalizeService` resolves and freezes it once, so patching
  // `budget.policy` here is reaching past the module's own contract.
  const { supervisor } = makeSupervisor({
    services: (spawn) => [
      {
        key: 'gateway',
        label: 'Gateway',
        spawnService: (c) => spawn('gateway', c),
        probe: () => true,
        policy: { maxRestartsInWindow: 2, jitterRatio: 0, baseDelayMs: 10 },
      },
    ],
  });
  await supervisor.start();

  assert.equal(supervisor.restartService('gateway', { uptimeMs: 0 }).action, 'restart');
  assert.equal(supervisor.restartService('gateway', { uptimeMs: 0 }).action, 'restart');
  const final = supervisor.restartService('gateway', { uptimeMs: 0 });
  assert.equal(final.action, 'give-up');
  assert.equal(supervisor.state('gateway'), STATES.failed);
  // The user has to be able to act on this, so the message names the way out.
  assert.match(supervisor.status()[0].error, /will not be restarted again/);
  assert.match(supervisor.status()[0].error, /Restart services/);
  await supervisor.stop();
});

test('stop() latches so a queued restart cannot fire during shutdown', async () => {
  // The single most common way a Windows desktop app leaves orphan Python
  // processes: a restart timer fires after quit has already begun.
  //
  // The restart delay here is long enough (30s) that observing "nothing
  // happened" for 250ms is conclusive rather than a race: the timer cannot
  // possibly have fired yet even if the latch were broken.
  const { supervisor, spawned } = makeSupervisor({
    services: (spawn) => [
      {
        key: 'gateway',
        label: 'Gateway',
        spawnService: (c) => spawn('gateway', c),
        probe: () => true,
        policy: { baseDelayMs: 30000, jitterRatio: 0 },
      },
    ],
  });
  await supervisor.start();
  const before = spawned.length;

  const decision = supervisor.restartService('gateway', { uptimeMs: 0 });
  assert.equal(decision.action, 'restart');
  assert.equal(decision.delayMs, 30000, 'the test needs a restart delay it can prove was not waited out');
  await supervisor.stop();
  await sleep(250);

  assert.equal(spawned.length, before, 'a restart fired after stop()');
  assert.equal(supervisor.state('gateway'), STATES.stopped);
});

test('a superseded exit does not spend restart budget', async () => {
  // Windows can deliver 'error' then 'exit' for one process, and a deliberate
  // taskkill can race a natural exit. Neither may count as a crash.
  const { supervisor } = makeSupervisor({
    services: (spawn) => [
      { key: 'gateway', label: 'Gateway', spawnService: (c) => spawn('gateway', c), probe: () => true },
    ],
  });
  await supervisor.start();
  const record = supervisor.records.get('gateway');
  const before = record.budget.restartTimes.length;

  const stale = supervisor.noteExit('gateway', {
    code: 1,
    signal: null,
    generation: record.generation - 1,
  });
  assert.equal(stale, null);
  assert.equal(record.budget.restartTimes.length, before);
  assert.equal(supervisor.state('gateway'), STATES.healthy);
  await supervisor.stop();
});

test('an exit after shutdown is not treated as a crash', async () => {
  const { supervisor } = makeSupervisor({
    services: (spawn) => [
      { key: 'gateway', label: 'Gateway', spawnService: (c) => spawn('gateway', c), probe: () => true },
    ],
  });
  await supervisor.start();
  await supervisor.stop();
  const generation = supervisor.records.get('gateway').generation;
  assert.equal(supervisor.noteExit('gateway', { code: 1, generation }), null);
});

test('a service that stops answering is restarted like a crash', async () => {
  // A hung process never fires 'exit', so an exit-only supervisor would sit on
  // a dead window forever. This is what makes health polling load bearing.
  //
  // The first spawn answers probes and then stops answering; the restart comes
  // back healthy. So the observable claim is narrow and honest: the unhealthy
  // run was torn down and respawned, which an exit-only supervisor could not do.
  // Attempt 1 comes up healthy, then stops answering. Attempt 2 answers again.
  // A hung process never fires 'exit', so only the health timer can notice, and
  // only the restart can recover it.
  let spawnedCount = 0;
  let healthy = true;
  const { supervisor, spawned } = makeSupervisor({
    services: (spawn) => [
      {
        key: 'gateway',
        label: 'Gateway',
        spawnService: (c) => {
          spawnedCount += 1;
          const attempt = spawnedCount;
          const child = spawn('gateway', c);
          if (attempt === 1) setTimeout(() => { healthy = false; }, 40);
          if (attempt === 2) setTimeout(() => { healthy = true; }, 40);
          return child;
        },
        probe: () => healthy,
      },
    ],
    timings: { healthIntervalMs: 25, pollIntervalMs: 10, startupTimeoutMs: 500, stopTimeoutMs: 20 },
  });
  await supervisor.start();
  assert.equal(spawned.length, 1);

  // Wait for the state to settle rather than sleeping a fixed amount: the
  // restart goes through a backoff timer, a respawn, and a startup poll, so a
  // fixed sleep is a race against CI load rather than a measurement.
  const settled = await waitFor(() => spawned.length >= 2 && supervisor.state('gateway') === STATES.healthy, 2000);
  assert.ok(spawned.length >= 2, 'an unhealthy-but-alive service was not restarted');
  assert.ok(settled, `it did not recover after the restart (state=${supervisor.state('gateway')})`);
  await supervisor.stop();
});

test('a restart that cannot come up is reported failed, not retried silently', async () => {
  // The counterpart to the test above: supervision must not spin. A service
  // that dies again on every respawn ends in a `failed` state with a reason the
  // user can act on, rather than an invisible loop.
  // Attempt 1 comes up healthy, then dies. Every attempt after that crashes
  // during startup, so the run never reaches a serving state again.
  let spawnedCount = 0;
  const { supervisor, spawned } = makeSupervisor({
    services: (spawn) => [
      {
        key: 'gateway',
        label: 'Gateway',
        spawnService: (c) => {
          spawnedCount += 1;
          const attempt = spawnedCount;
          const child = spawn('gateway', c);
          if (attempt === 1) setTimeout(() => child.die(9, null), 20);
          if (attempt > 1) setTimeout(() => child.die(9, null), 5);
          return child;
        },
        probe: () => true,
      },
    ],
    timings: { healthIntervalMs: 60000, pollIntervalMs: 10, startupTimeoutMs: 200, stopTimeoutMs: 20 },
    policy: { maxRestartsInWindow: 2 },
  });
  await supervisor.start();

  const gaveUp = await waitFor(() => supervisor.state('gateway') === STATES.failed, 3000);
  assert.ok(gaveUp, `never reached failed (state=${supervisor.state('gateway')})`);
  assert.ok(spawned.length > 1, 'nothing was retried');
  assert.ok(spawned.length <= 4, `respawned ${spawned.length} times; the budget did not stop it`);
  assert.match(supervisor.status()[0].error, /will not be restarted again|could not be restarted/);
  await supervisor.stop();
});

test('a dependency restart takes its dependents with it, dependency first', async () => {
  // A Gateway back on a different port leaves the frontend's baked /api
  // rewrites stale, so a frontend left alone is broken in a way that looks like
  // a frontend bug.
  const { supervisor, spawned } = makeSupervisor({
    timings: { healthIntervalMs: 60000, pollIntervalMs: 10, startupTimeoutMs: 2000, stopTimeoutMs: 20 },
  });
  await supervisor.start();
  assert.deepEqual(spawned.map((s) => s.key), ['gateway', 'frontend']);
  const before = spawned.length;

  supervisor.restartService('gateway', { uptimeMs: 0 });
  assert.equal(supervisor.state('frontend'), STATES.waiting);

  const recovered = await waitFor(
    () => supervisor.state('gateway') === STATES.healthy && supervisor.state('frontend') === STATES.healthy && spawned.length >= before + 2,
    3000,
  );
  assert.ok(recovered, `dependent restart never settled (${supervisor.state('gateway')}/${supervisor.state('frontend')})`);
  const after = spawned.slice(before).map((s) => s.key);
  assert.ok(after.includes('gateway'), 'gateway was not respawned');
  assert.ok(after.includes('frontend'), 'dependent frontend was not respawned');
  // The gateway must come back first, or the frontend points at a dead port.
  assert.ok(after.indexOf('gateway') < after.indexOf('frontend'), `order was ${after.join(',')}`);
  await supervisor.stop();
});

test('the dependency graph is validated at construction, not at first exit', () => {
  assert.throws(
    () => topologicalOrder([{ key: 'a', dependsOn: ['nope'] }]),
    /depends on unknown service nope/,
  );
  assert.throws(() => topologicalOrder([{ key: 'a', dependsOn: ['a'] }]), /depends on itself/);
  assert.throws(
    () =>
      topologicalOrder([
        { key: 'a', dependsOn: ['b'] },
        { key: 'b', dependsOn: ['a'] },
      ]),
    /dependency cycle/,
  );
  assert.throws(
    () =>
      topologicalOrder([
        { key: 'a', dependsOn: ['c'] },
        { key: 'b', dependsOn: ['a'] },
        { key: 'c', dependsOn: ['b'] },
      ]),
    /dependency cycle/,
  );
});

test('a service definition missing its parts is refused', () => {
  assert.throws(() => new ServiceSupervisor({ services: [{}] }), /needs a string key/);
  assert.throws(
    () => new ServiceSupervisor({ services: [{ key: 'a' }] }),
    /needs a spawnService\(\) function/,
  );
  assert.throws(
    () => new ServiceSupervisor({ services: [{ key: 'a', spawnService: () => {} }] }),
    /needs a probe\(\) function/,
  );
});

test('a status consumer that throws cannot break supervision', async () => {
  const supervisor = new ServiceSupervisor({
    services: [{ key: 'gateway', label: 'Gateway', spawnService: () => fakeChild(), probe: () => true }],
    onStateChange: () => {
      throw new Error('renderer went away');
    },
    log: () => {},
  });
  await supervisor.start();
  assert.equal(supervisor.state('gateway'), STATES.healthy);
  await supervisor.stop();
});

test('unmanaged services are reported as skipped, not silently dropped', async () => {
  const { supervisor, spawned } = makeSupervisor({
    services: (spawn) => [
      {
        key: 'gateway',
        label: 'Gateway',
        managed: false, // attach mode: a Gateway we did not start
        spawnService: (c) => spawn('gateway', c),
        probe: () => true,
      },
      {
        key: 'frontend',
        label: 'Frontend',
        dependsOn: ['gateway'],
        spawnService: (c) => spawn('frontend', c),
        probe: () => true,
      },
    ],
  });
  const result = await supervisor.start();
  assert.deepEqual(result.skipped, ['gateway']);
  assert.ok(!spawned.some((s) => s.key === 'gateway'));
  assert.ok(spawned.some((s) => s.key === 'frontend'));
  await supervisor.stop();
});

test('a reused service is not also spawned, and its dependents still start', async () => {
  // The bug this pins: boot decided "reuse" while printing it, then spawned
  // anyway — so a second Gateway came up on a port just reported as belonging to
  // a healthy one, and two Gateways fought over the same state directory.
  // `managed` is derived from the port decision for exactly this reason.
  const { supervisor, spawned } = makeSupervisor({
    services: (spawn) => [
      {
        key: 'gateway',
        label: 'Gateway',
        managed: false, // decideServicePort returned action 'reuse'
        spawnService: (c) => spawn('gateway', c),
        probe: () => true,
      },
      {
        key: 'frontend',
        label: 'Frontend',
        dependsOn: ['gateway'],
        managed: true,
        spawnService: (c) => spawn('frontend', c),
        probe: () => true,
      },
    ],
  });
  const result = await supervisor.start();
  assert.deepEqual(result.skipped, ['gateway']);
  assert.equal(spawned.filter((s) => s.key === 'gateway').length, 0, 'a reused Gateway was spawned anyway');
  assert.equal(spawned.filter((s) => s.key === 'frontend').length, 1);
  // And the frontend reaches healthy, because it did not wait on a Gateway the
  // app does not own.
  assert.equal(supervisor.state('frontend'), STATES.healthy);
  await supervisor.stop();
});

test('status() reports a usable snapshot for the renderer and the menu', async () => {
  const { supervisor } = makeSupervisor();
  await supervisor.start();
  const status = supervisor.status();
  assert.equal(status.length, 2);
  const gateway = status.find((s) => s.key === 'gateway');
  assert.equal(gateway.state, STATES.healthy);
  assert.equal(typeof gateway.pid, 'number');
  assert.equal(gateway.error, null);
  assert.equal(typeof gateway.restarts, 'number');
  await supervisor.stop();
});

test('teardown prefers a graceful stop and still kills when it hangs', async () => {
  const stopCalls = [];
  const child = fakeChild();
  child.stop = () =>
    new Promise(() => {
      stopCalls.push('stop'); // never resolves
    });
  const supervisor = new ServiceSupervisor({
    services: [{ key: 'gateway', label: 'Gateway', spawnService: () => child, probe: () => true }],
    timings: { healthIntervalMs: 60000, pollIntervalMs: 10, startupTimeoutMs: 1000, stopTimeoutMs: 30 },
  });
  await supervisor.start();
  await supervisor.stop();
  assert.deepEqual(stopCalls, ['stop']);
  assert.equal(child.killed, true, 'a hung graceful stop must still be killed');
});

test('teardown uses the injected tree killer instead of only killing the parent', async () => {
  // Regression from a real failed first launch: supervisor teardown called
  // `child.kill()` on the `uv` wrapper, which exited while its Python/uvicorn
  // grandchildren kept running. The orphan then held port 8201 while the app
  // reported a startup failure.
  const child = fakeChild(4242);
  const terminated = [];
  const supervisor = new ServiceSupervisor({
    services: [{ key: 'gateway', label: 'Gateway', spawnService: () => child, probe: () => true }],
    timings: { healthIntervalMs: 60000, pollIntervalMs: 10, startupTimeoutMs: 1000, stopTimeoutMs: 30 },
    terminate: async (handle, info) => {
      terminated.push({ pid: handle.pid, key: info && info.key });
      handle.killed = true;
      handle.exitCode = 1;
    },
  });
  await supervisor.start();
  await supervisor.teardown(supervisor.records.get('gateway'));
  assert.deepEqual(terminated, [{ pid: 4242, key: 'gateway' }]);
  assert.equal(child.killed, true);
});

test('stop() tears down dependents before their dependencies', async () => {
  // Reverse order matters: a frontend still holding a request to a Gateway we
  // already killed produces a confusing crash report on the way out.
  const stopOrder = [];
  const withStop = (key) => {
    const child = fakeChild();
    child.stop = () => {
      stopOrder.push(key);
      return Promise.resolve();
    };
    return child;
  };
  const supervisor = new ServiceSupervisor({
    services: [
      { key: 'gateway', label: 'Gateway', spawnService: () => withStop('gateway'), probe: () => true },
      {
        key: 'frontend',
        label: 'Frontend',
        dependsOn: ['gateway'],
        spawnService: () => withStop('frontend'),
        probe: () => true,
      },
    ],
    timings: { healthIntervalMs: 60000, pollIntervalMs: 10, startupTimeoutMs: 1000, stopTimeoutMs: 30 },
  });
  await supervisor.start();
  await supervisor.stop();
  assert.deepEqual(stopOrder, ['frontend', 'gateway']);
});

test('per-service startup budgets survive normalization', () => {
  // Regression: main.js passed per-service `startupTimeoutMs` values, but the
  // supervisor silently discarded every field except the restart policy. A slow
  // first launch therefore used the generic five-minute timeout despite the
  // Gateway explicitly allowing ten minutes for Python provisioning.
  const supervisor = new ServiceSupervisor({
    log: () => {},
    timings: { startupTimeoutMs: 1000 },
    services: [
      {
        key: 'gateway',
        spawnService: () => fakeChild(),
        probe: () => true,
        timings: { startupTimeoutMs: 600000, pollIntervalMs: 250 },
      },
      { key: 'frontend', spawnService: () => fakeChild(), probe: () => true },
    ],
  });
  assert.equal(supervisor.records.get('gateway').timings.startupTimeoutMs, 600000);
  assert.equal(supervisor.records.get('gateway').timings.pollIntervalMs, 250);
  assert.equal(supervisor.records.get('frontend').timings.startupTimeoutMs, 1000);
});

test('invalid timing overrides fall back instead of stopping the clock', () => {
  const timings = resolveTimings({
    healthIntervalMs: -1,
    pollIntervalMs: 0,
    startupTimeoutMs: Number.NaN,
    stopTimeoutMs: 'soon',
  });
  assert.equal(timings.healthIntervalMs, 10000);
  assert.equal(timings.pollIntervalMs, 1000);
  assert.equal(timings.startupTimeoutMs, 300000);
  assert.equal(timings.stopTimeoutMs, 5000);
});
