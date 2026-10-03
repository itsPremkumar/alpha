import test from 'node:test';
import assert from 'node:assert/strict';
import { STATES } from '../lib/service-supervisor.js';
import {
  STATUS_KEYS,
  VERSION_KEYS,
  buildAppInfo,
  createStatus,
  createVersions,
  projectServices,
} from '../lib/app-info.js';

test('a status object has one shape whether or not boot has finished', () => {
  // The regression: runtimeStatus used to be a 4-key object during boot and a
  // 9-key object after it, so a renderer reading `versions` mid-boot got
  // `undefined` and then saw the value change under it.
  const booting = createStatus({ dev: false, packaged: true });
  const ready = createStatus({ dev: false, packaged: true, booting: false, ready: true });

  assert.deepEqual(Object.keys(booting).sort(), Object.keys(ready).sort());
  assert.deepEqual(Object.keys(booting).sort(), [...STATUS_KEYS].sort());
});

test('every key is present even when its value is unknown', () => {
  const status = createStatus();
  for (const key of STATUS_KEYS) {
    assert.ok(key in status, `status is missing ${key}`);
  }
  // Unknown is null, never absent and never a plausible default.
  assert.equal(status.frontendUrl, null);
  assert.equal(status.gatewayUrl, null);
  assert.equal(status.userData, null);
  assert.equal(status.logsDir, null);
  assert.equal(status.versions, null);
  assert.deepEqual(status.services, []);
});

test('an explicit undefined does not overwrite a known value with undefined', () => {
  // `{...base, versions: undefined}` is the shape a spread-merge produces when
  // a caller has not computed a field yet. It must not blank the key.
  const status = createStatus({ userData: 'C:/x', versions: undefined });
  assert.equal(status.userData, 'C:/x');
  assert.equal(status.versions, null, 'undefined became the value rather than "unknown"');
});

test('the versions sub-object is complete or null, never partial', () => {
  const partial = createVersions({ app: '2.1.0', electron: '44.3.0' });
  assert.deepEqual(Object.keys(partial).sort(), [...VERSION_KEYS].sort());
  assert.equal(partial.app, '2.1.0');
  // Chrome and Node are genuinely unknown here, so they are null.
  assert.equal(partial.chrome, null);
  assert.equal(partial.node, null);

  // A non-string or empty value is unknown, not passed through.
  const junk = createVersions({ app: 42, electron: '', chrome: null, node: undefined });
  assert.deepEqual(junk, { app: null, electron: null, chrome: null, node: null });
});

test('ready is derived from the services and booting is its complement', () => {
  const booting = createStatus();
  assert.equal(booting.booting, true);
  assert.equal(booting.ready, false);

  // Both flags are ignored as inputs, so the contradictory combination cannot
  // be constructed: no caller can make a status that claims to be ready and
  // booting at the same time.
  const contradictory = createStatus({ booting: false, ready: true, services: [] });
  assert.equal(contradictory.ready, false, 'ready was claimed with no services at all');
  assert.equal(contradictory.booting, true);

  const healthy = [{ key: 'gateway', healthy: true }, { key: 'frontend', healthy: true }];
  const trulyReady = createStatus({ services: healthy });
  assert.equal(trulyReady.ready, true);
  assert.equal(trulyReady.booting, false);

  // One unhealthy service is enough to withhold "ready", even if a caller tries.
  const degraded = createStatus({ ready: true, services: [{ healthy: true }, { healthy: false }] });
  assert.equal(degraded.ready, false);
  assert.equal(degraded.booting, true);

  // A service that is merely present but not marked healthy is not healthy.
  const unmarked = createStatus({ services: [{ key: 'gateway' }] });
  assert.equal(unmarked.ready, false);
});

test('service records are projected with honest fields', () => {
  const services = projectServices([
    {
      key: 'gateway',
      label: 'Gateway',
      state: STATES.healthy,
      port: 8201,
      pid: 1234,
      error: null,
      restarts: 0,
    },
    {
      key: 'frontend',
      label: 'Frontend',
      state: STATES.failed,
      port: null,
      pid: null,
      error: '   ',
      restarts: 3,
    },
  ]);

  assert.equal(services.length, 2);
  assert.equal(services[0].healthy, true);
  assert.equal(services[0].port, 8201);
  assert.equal(services[0].pid, 1234);
  assert.equal(services[0].error, null);

  assert.equal(services[1].healthy, false);
  assert.equal(services[1].port, null, 'an unknown port became something else');
  // A whitespace-only error is not a reason; it is an absence of one.
  assert.equal(services[1].error, null);
  assert.equal(services[1].restarts, 3);
});

test('a garbage service record cannot produce a false healthy claim', () => {
  const services = projectServices([
    { key: 'gateway', label: 'Gateway', state: undefined, port: 'soon', pid: 'x', restarts: 'many', error: 5 },
  ]);
  assert.equal(services[0].state, undefined);
  assert.equal(services[0].healthy, false, 'an unknown state was reported as healthy');
  assert.equal(services[0].port, null);
  assert.equal(services[0].pid, null);
  assert.equal(services[0].restarts, 0);
  assert.equal(services[0].error, null);
});

test('projecting no records yields an empty list, not a failure', () => {
  assert.deepEqual(projectServices([]), []);
  assert.deepEqual(projectServices(), []);
  assert.deepEqual(createStatus({ services: 'oops' }).services, []);
});

test('app info is complete and constant', () => {
  const info = buildAppInfo({
    name: 'Alpha',
    version: '2.1.0',
    electronVersion: '44.3.0',
    chromeVersion: '152.0.7977.78',
    nodeVersion: '24.20.0',
    packaged: true,
    userData: 'C:/Users/x/AppData/Roaming/Alpha',
    frontendUrl: 'http://127.0.0.1:3000',
    gatewayUrl: 'http://127.0.0.1:8201',
  });
  assert.equal(info.name, 'Alpha');
  assert.equal(info.version, '2.1.0');
  assert.equal(info.packaged, true);
  assert.equal(info.versions.chrome, '152.0.7977.78');
  assert.equal(info.versions.node, '24.20.0');
  // Frozen: a renderer cannot mutate the object every other call returns.
  assert.equal(Object.isFrozen(info), true);
  assert.equal(Object.isFrozen(info.versions), true);
});

test('app info does not invent a version it was not given', () => {
  const info = buildAppInfo({ name: 'Alpha' });
  assert.equal(info.version, null);
  assert.equal(info.versions.electron, null);
  assert.equal(info.userData, null);
  assert.equal(info.frontendUrl, null);
  assert.equal(info.packaged, false);
  // The name has a real fallback so the About box is never blank.
  assert.equal(info.name, 'Alpha');
});
