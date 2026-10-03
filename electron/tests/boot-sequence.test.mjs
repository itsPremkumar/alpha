import test from 'node:test';
import assert from 'node:assert/strict';
import {
  BOOT_PHASES,
  decideServicePort,
  describeBootFailure,
  runBootSequence,
} from '../lib/boot-sequence.js';

const never = async () => false;
const always = async () => true;

test('a verified Alpha service on the preferred port is reused', async () => {
  let freePortAsked = false;
  const decision = await decideServicePort({
    preferredPort: 8201,
    probeIdentity: always,
    findFreePort: async () => {
      freePortAsked = true;
      return 9999;
    },
  });
  assert.equal(decision.action, 'reuse');
  assert.equal(decision.port, 8201);
  assert.equal(decision.gatewayReused, true);
  assert.equal(freePortAsked, false, 'a port was searched for despite a verified service');
  assert.match(decision.reason, /reusing/);
});

test('an unknown occupant of the port is never mistaken for our Gateway', async () => {
  // The bug this prevents: a foreign process on 8201 reported as a healthy
  // Alpha service, leaving the app pointing `gatewayUrl` at something it never
  // health-checked while the real Gateway was never started.
  const decision = await decideServicePort({
    preferredPort: 8201,
    probeIdentity: never, // something is there, but it is not ours
    findFreePort: async () => 8202,
  });
  assert.equal(decision.action, 'spawn');
  assert.equal(decision.port, 8202);
  assert.equal(decision.gatewayReused, false);
  assert.match(decision.reason, /taken by something else/);
});

test('a free preferred port is used without commentary', async () => {
  const decision = await decideServicePort({
    preferredPort: 8201,
    probeIdentity: never,
    findFreePort: async () => 8201,
  });
  assert.equal(decision.port, 8201);
  assert.match(decision.reason, /preferred port 8201/);
});

test('attach mode with nothing to attach to is an explicit, honest outcome', async () => {
  // Not an error, but not a success either: the window will load with no
  // backend. The reason has to say that, because nothing else will.
  const decision = await decideServicePort({
    preferredPort: 8201,
    probeIdentity: never,
    findFreePort: async () => 8300,
    skip: true,
  });
  assert.equal(decision.action, 'skip');
  assert.equal(decision.gatewayReused, false);
  assert.match(decision.reason, /no Alpha service is answering/);
  assert.match(decision.reason, /API calls will fail/);
});

test('attach mode onto a verified service is a skip, not a reuse', async () => {
  const decision = await decideServicePort({
    preferredPort: 8201,
    probeIdentity: always,
    findFreePort: async () => 9999,
    skip: true,
  });
  assert.equal(decision.action, 'skip');
  assert.equal(decision.gatewayReused, true, 'a verified service was still identified as reused');
  assert.match(decision.reason, /attaching/);
});

test('an explicitly pinned port is honoured even when occupied', async () => {
  // Silently moving `--gateway-port=8201` to another port would make the flag a
  // lie. The failure then comes from the child, which is where it belongs.
  let freePortAsked = false;
  const decision = await decideServicePort({
    preferredPort: 8201,
    probeIdentity: never,
    findFreePort: async () => {
      freePortAsked = true;
      return 8301;
    },
    forcedPort: true,
  });
  assert.equal(decision.port, 8201);
  assert.equal(freePortAsked, false);
  assert.match(decision.reason, /requested port/);
});

test('a probe that throws is treated as "not ours", not as a crash', async () => {
  // A port that accepts a connection but never answers /health must not wedge
  // the boot; it is simply not our service.
  const decision = await decideServicePort({
    preferredPort: 8201,
    probeIdentity: async () => {
      throw new Error('socket hang up');
    },
    findFreePort: async () => 8202,
  });
  assert.equal(decision.action, 'spawn');
  assert.equal(decision.port, 8202);
});

test('a missing dependency is a programming error, not a silent default', async () => {
  // Rejects rather than throws, because the function is async; either way it is
  // a named failure instead of a `TypeError` from deep inside the port search.
  await assert.rejects(
    () =>
      decideServicePort({
        preferredPort: 8201,
        probeIdentity: null,
        findFreePort: async () => 1,
      }),
    /needs probeIdentity\(\) and findFreePort\(\)/,
  );
});

test('boot phases run in order and stop at the first failure', async () => {
  const ran = [];
  const result = await runBootSequence({
    phases: [
      { name: 'prepare-dirs', run: () => ran.push('prepare-dirs') },
      { name: 'gateway', run: () => ran.push('gateway') },
      { name: 'frontend', run: () => ran.push('frontend') },
    ],
  });
  // Phases run in the order given, which is the dependency order the real boot
  // uses: the Gateway must serve before the frontend is started, because the
  // frontend's /api rewrites point at it.
  assert.deepEqual(ran, ['prepare-dirs', 'gateway', 'frontend']);
  assert.equal(result.ok, true);
  assert.deepEqual(result.completed, ['prepare-dirs', 'gateway', 'frontend']);
  assert.equal(result.failed, null);
  // The canonical phase list is the documented order, and the test above uses a
  // subset of it, so a rename or reorder in the module is caught here.
  assert.deepEqual(BOOT_PHASES, ['prepare-dirs', 'diagnostics', 'gateway', 'frontend', 'open-window']);
  for (const phase of ['prepare-dirs', 'gateway', 'frontend']) {
    assert.ok(BOOT_PHASES.includes(phase), `${phase} is missing from BOOT_PHASES`);
  }
});

test('a failing phase aborts the boot and names itself', async () => {
  const ran = [];
  const result = await runBootSequence({
    phases: [
      { name: 'prepare-dirs', run: () => ran.push('prepare-dirs') },
      {
        name: 'gateway',
        run: () => {
          throw new Error('Bundled `uv` runtime missing from the installed app');
        },
      },
      { name: 'frontend', run: () => ran.push('frontend') },
    ],
  });
  assert.equal(result.ok, false);
  // The frontend must not be attempted: without a Gateway it cannot work, and
  // its own error would bury the real cause.
  assert.deepEqual(ran, ['prepare-dirs']);
  assert.equal(result.failed.name, 'gateway');
  assert.match(result.failed.error, /Bundled `uv` runtime missing/);
  assert.deepEqual(result.completed, ['prepare-dirs']);
});

test('a phase that rejects asynchronously is caught like a synchronous throw', async () => {
  const result = await runBootSequence({
    phases: [{ name: 'gateway', run: async () => Promise.reject(new Error('port in use')) }],
  });
  assert.equal(result.ok, false);
  assert.equal(result.failed.name, 'gateway');
  assert.match(result.failed.error, /port in use/);
});

test('a non-Error rejection still produces a readable message', async () => {
  const result = await runBootSequence({
    phases: [{ name: 'gateway', run: async () => Promise.reject('just a string') }],
  });
  assert.equal(result.failed.error, 'just a string');
});

test('a phase receives the shared boot context', async () => {
  let seen = null;
  await runBootSequence({
    context: { userData: 'C:/x' },
    phases: [
      {
        name: 'prepare-dirs',
        run: (ctx) => {
          seen = ctx;
        },
      },
    ],
  });
  assert.deepEqual(seen, { userData: 'C:/x' });
});

test('an empty phase list is a success, not a crash', async () => {
  const result = await runBootSequence({ phases: [] });
  assert.equal(result.ok, true);
  assert.deepEqual(result.completed, []);
});

test('the failure title names the phase that failed', () => {
  // "Startup failed" with no phase is what a user cannot act on.
  assert.equal(describeBootFailure({ name: 'gateway', error: 'x' }), 'Alpha — gateway failed');
  assert.equal(describeBootFailure(null), 'Alpha — startup failed');
  assert.equal(describeBootFailure(null, 'Beta'), 'Beta — startup failed');
});
