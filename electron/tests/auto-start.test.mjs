import test from 'node:test';
import assert from 'node:assert/strict';
import {
  STATE_UNCHANGED,
  decideBootReconcile,
  decideToggle,
  describeState,
  readPreference,
  serializePreference,
} from '../lib/auto-start.js';

test('a missing or corrupt preference file reads as disabled', () => {
  // "Enabled" here would make the app re-register a login item the user may not
  // have asked for, so an unreadable file is the conservative answer.
  assert.equal(readPreference(() => null), false);
  assert.equal(readPreference(() => ''), false);
  assert.equal(readPreference(() => '   '), false);
  assert.equal(readPreference(() => 'not json'), false);
  assert.equal(readPreference(() => '[]'), false);
  assert.equal(readPreference(() => 'null'), false);
  assert.equal(readPreference(() => '{"enabled":"yes"}'), false);
  assert.equal(readPreference(() => {
    throw new Error('EACCES');
  }), false);
});

test('a stored preference round-trips', () => {
  assert.equal(readPreference(() => serializePreference(true)), true);
  assert.equal(readPreference(() => serializePreference(false)), false);
  // Only the literal boolean `true` counts. A hand-edited `"enabled": 1` is
  // refused rather than coerced, so the file has exactly one true encoding and a
  // stray value cannot register a login item the user never asked for.
  assert.equal(readPreference(() => '{"enabled":1}'), false);
  assert.equal(readPreference(() => '{"enabled":"true"}'), false);
});

test('the preference file stays small and single-purpose', () => {
  const serialized = serializePreference(true);
  assert.deepEqual(JSON.parse(serialized), { enabled: true });
  assert.ok(serialized.length < 64, `preference file is ${serialized.length} bytes`);
});

test('auto-start is unsupported outside a packaged install, and says why', () => {
  // In a source checkout the executable is the bare Electron binary, so a login
  // entry would launch with no app.
  const state = describeState({ isPackaged: false, preferenceEnabled: true, osActive: true });
  assert.equal(state.supported, false);
  assert.equal(state.enabled, false);
  assert.equal(state.active, false);
  assert.match(state.reason, /installed app/);
});

test('a packaged install reports the preference and the OS state separately', () => {
  const agreed = describeState({ isPackaged: true, preferenceEnabled: true, osActive: true });
  assert.equal(agreed.supported, true);
  assert.equal(agreed.enabled, true);
  assert.equal(agreed.active, true);
  assert.equal(agreed.diverged, false);

  const missing = describeState({ isPackaged: true, preferenceEnabled: true, osActive: false });
  assert.equal(missing.enabled, true);
  assert.equal(missing.active, false);
  // A preference the OS does not reflect is a visible divergence, not a silent
  // disagreement.
  assert.equal(missing.diverged, true);

  const leftover = describeState({ isPackaged: true, preferenceEnabled: false, osActive: true });
  assert.equal(leftover.diverged, true);
});

test('a removed login entry is re-registered, because the preference is the truth', () => {
  // Cleaner software, group policy, or a user in Task Scheduler can remove the
  // entry. Silently losing "start with Windows" looks identical to the user
  // turning it off, so it is healed instead.
  const decision = decideBootReconcile({
    isPackaged: true,
    preferenceEnabled: true,
    osActive: false,
  });
  assert.equal(decision.action, 'register');
  assert.match(decision.reason, /missing/);
  assert.match(decision.reason, /cleaner or group policy/);
});

test('a leftover login entry is cleared after the user turns the feature off', () => {
  const decision = decideBootReconcile({
    isPackaged: true,
    preferenceEnabled: false,
    osActive: true,
  });
  assert.equal(decision.action, 'clear');
  assert.match(decision.reason, /removing a leftover/);
});

test('a matching login entry is left alone', () => {
  assert.equal(
    decideBootReconcile({ isPackaged: true, preferenceEnabled: true, osActive: true }).action,
    'none',
  );
  assert.equal(
    decideBootReconcile({ isPackaged: true, preferenceEnabled: false, osActive: false }).action,
    'none',
  );
});

test('a source checkout never registers a login item', () => {
  // The important asymmetry: even with the preference set to true, an unpackaged
  // run does nothing.
  for (const osActive of [true, false]) {
    const decision = decideBootReconcile({
      isPackaged: false,
      preferenceEnabled: true,
      osActive,
    });
    assert.equal(decision.action, 'none', `a source checkout tried to touch the login item`);
  }
});

test('toggling in a source checkout is refused with an explanation, not a throw', () => {
  const result = decideToggle({ isPackaged: false, desiredEnabled: true });
  assert.equal(result.ok, false);
  assert.match(result.message, /bare Electron binary/);
  assert.equal(result.state.supported, false);
});

test('toggling in a packaged install reports the post-toggle state', () => {
  const on = decideToggle({ isPackaged: true, desiredEnabled: true });
  assert.equal(on.ok, true);
  assert.equal(on.message, null);
  assert.equal(on.state.enabled, true);
  assert.equal(on.state.active, true);
  assert.equal(on.state.diverged, false);

  const off = decideToggle({ isPackaged: true, desiredEnabled: false });
  assert.equal(off.state.enabled, false);
  assert.equal(off.state.active, false);
});

test('the unsupported-state constant is the honest zero value', () => {
  assert.deepEqual(STATE_UNCHANGED, { supported: false, enabled: false, active: false });
});