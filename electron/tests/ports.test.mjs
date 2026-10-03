import test from 'node:test';
import assert from 'node:assert/strict';
import { findFreePort, loopbackBase } from '../lib/ports.js';

test('the preferred port is returned when it is free', async () => {
  const probed = [];
  const port = await findFreePort(8201, {
    isPortFree: async (candidate) => {
      probed.push(candidate);
      return true;
    },
  });
  assert.equal(port, 8201);
  // No point probing further once the preferred port is free.
  assert.deepEqual(probed, [8201]);
});

test('the search walks forward until it finds a free port', async () => {
  // The real case: `make dev` already holds 3000, so the desktop app has to move
  // rather than fail.
  const probed = [];
  const port = await findFreePort(3000, {
    isPortFree: async (candidate) => {
      probed.push(candidate);
      return candidate >= 3003;
    },
  });
  assert.equal(port, 3003);
  assert.deepEqual(probed, [3000, 3001, 3002, 3003]);
});

test('a fully occupied range fails loudly and names the range', async () => {
  // A named range is actionable; "no free port" is not.
  await assert.rejects(
    () => findFreePort(8201, { isPortFree: async () => false, maxOffset: 5 }),
    (error) => {
      assert.match(error.message, /8201-8206/);
      return true;
    },
  );
});

test('the search honours a custom window', async () => {
  await assert.rejects(
    () => findFreePort(3000, { isPortFree: async () => false, maxOffset: 2 }),
    /3000-3002/,
  );
});

test('a probe that throws is treated as occupied, not as a crash', async () => {
  // A port probe can fail for reasons that are not "busy" (EPERM, a firewall).
  // Treating that as occupied and moving on is safer than aborting the boot.
  let calls = 0;
  const port = await findFreePort(8201, {
    isPortFree: async () => {
      calls += 1;
      if (calls === 1) throw new Error('EPERM');
      return true;
    },
  });
  assert.equal(port, 8202);
});

test('an out-of-range preferred port is refused', () => {
  // A typo in `--gateway-port=` must not become a silent bind on 0.
  // Synchronous by design: validation runs before the promise exists, so this
  // throws at the call site rather than as a rejection from inside the search.
  for (const bad of [0, -1, 70000, NaN, 'abc', null, undefined]) {
    assert.throws(
      () => findFreePort(bad, { isPortFree: async () => true }),
      /Invalid preferred port/,
      `${JSON.stringify(bad)} was accepted`,
    );
  }
});

test('the search never walks past the last valid port', async () => {
  // 65535 + 1 is not a port. The search must stop, not wrap into privileged
  // territory or spin.
  let lastProbed = 0;
  await assert.rejects(
    () =>
      findFreePort(65534, {
        maxOffset: 20,
        isPortFree: async (candidate) => {
          lastProbed = candidate;
          return false;
        },
      }),
    /No free port found/,
  );
  assert.ok(lastProbed <= 65535, `probed port ${lastProbed}, which is not a port`);
});

test('a missing probe function is a programming error, not a silent default', () => {
  // Both guards are synchronous, so these are throws rather than rejections.
  assert.throws(() => findFreePort(8201, {}), /needs an isPortFree\(\) function/);
  assert.throws(() => findFreePort(8201, { isPortFree: 'nope' }), /needs an isPortFree\(\) function/);
});

test('probes are sequential, never parallel', async () => {
  // Two parallel probes of the same port can both see it free and both return
  // it, so the caller gets a port that is not.
  let inFlight = 0;
  let maxConcurrent = 0;
  await findFreePort(9000, {
    maxOffset: 6,
    isPortFree: async (candidate) => {
      inFlight += 1;
      maxConcurrent = Math.max(maxConcurrent, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 5));
      inFlight -= 1;
      return candidate === 9006;
    },
  });
  assert.equal(maxConcurrent, 1, 'port probes overlapped');
});

test('loopbackBase builds the origin the probes expect', () => {
  assert.equal(loopbackBase(8201), 'http://127.0.0.1:8201');
  assert.equal(loopbackBase(1), 'http://127.0.0.1:1');
});