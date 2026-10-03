import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_POLICY,
  UPDATE_STATE,
  compareVersions,
  decideAvailability,
  decideCheck,
  interpretCheckResult,
  resolvePolicy,
} from '../lib/updater.js';

test('versions compare numerically, not lexicographically', () => {
  // The classic bug: as strings '10.0.0' < '9.0.0', so an app on v10 offers
  // v9 as an "upgrade".
  assert.ok(compareVersions('10.0.0', '9.0.0') > 0, 'v10 was considered older than v9');
  assert.ok(compareVersions('9.0.0', '10.0.0') < 0);
  assert.ok(compareVersions('2.10.0', '2.9.0') > 0, '2.10 sorted below 2.9');
  assert.ok(compareVersions('1.0.100', '1.0.99') > 0);
  assert.equal(compareVersions('2.1.0', '2.1.0'), 0);
});

test('a leading v and missing segments are tolerated', () => {
  assert.equal(compareVersions('v2.1.0', '2.1.0'), 0);
  assert.equal(compareVersions('2.1', '2.1.0'), 0);
  assert.equal(compareVersions('2', '2.0.0'), 0);
  assert.ok(compareVersions('2.1.1', '2.1') > 0);
});

test('a pre-release sorts below its release', () => {
  assert.ok(compareVersions('1.2.0-rc.1', '1.2.0') < 0, 'rc.1 sorted at or above the release');
  assert.ok(compareVersions('1.2.0', '1.2.0-rc.1') > 0);
  assert.ok(compareVersions('1.2.0-rc.1', '1.2.0-rc.2') < 0);
});

test('an unparseable version is treated as older, never as an upgrade', () => {
  assert.ok(compareVersions('', '2.1.0') < 0);
  assert.ok(compareVersions(null, '2.1.0') < 0);
  assert.ok(compareVersions('2.1.0', undefined) > 0);
  assert.equal(compareVersions(null, undefined), 0);
  // Non-numeric segments degrade to 0 rather than to NaN, which would make every
  // comparison false.
  assert.equal(compareVersions('2.x.0', '2.0.0'), 0);
});

test('the first check happens, then the minimum interval is enforced', () => {
  const now = 1_000_000_000;
  const first = decideCheck({ lastCheckAt: null, now });
  assert.equal(first.check, true);
  assert.match(first.reason, /no previous check/);

  // Just checked: no.
  assert.equal(decideCheck({ lastCheckAt: now - 1000, now }).check, false);
  // Well past the interval: yes.
  const stale = decideCheck({ lastCheckAt: now - DEFAULT_POLICY.minCheckIntervalMs - 1, now });
  assert.equal(stale.check, true);
  assert.match(stale.reason, /minimum/);
});

test('a check immediately after launch is deferred', () => {
  // 30s after boot the user is still waiting for services; a network call then
  // competes with the frontend coming up for the same connection budget.
  const now = 1_000_000_000;
  const startupAt = now - 5000;
  const result = decideCheck({ lastCheckAt: now - DEFAULT_POLICY.minCheckIntervalMs * 2, now, startupAt });
  assert.equal(result.check, false);
  assert.match(result.reason, /too soon after launch/);

  // Past the deferral window, the interval rule applies again.
  const later = decideCheck({
    lastCheckAt: now - DEFAULT_POLICY.minCheckIntervalMs * 2,
    now,
    startupAt: now - DEFAULT_POLICY.checkAfterStartupMs - 1,
  });
  assert.equal(later.check, true);
});

test('a clock that moved backwards re-checks instead of locking updates out', () => {
  // NTP correction or a long suspend can put the recorded check in the future.
  // Trusting it would disable updates until wall-clock caught up.
  const now = 1_000_000_000;
  const result = decideCheck({ lastCheckAt: now + 10 * DEFAULT_POLICY.minCheckIntervalMs, now });
  assert.equal(result.check, true);
  assert.match(result.reason, /in the future/);
});

test('nonsense policy overrides are discarded', () => {
  const policy = resolvePolicy({ minCheckIntervalMs: -1, checkAfterStartupMs: 'soon' });
  assert.equal(policy.minCheckIntervalMs, DEFAULT_POLICY.minCheckIntervalMs);
  assert.equal(policy.checkAfterStartupMs, DEFAULT_POLICY.checkAfterStartupMs);
  // Booleans are explicit: autoDownload defaults on, autoInstallOnQuit off.
  assert.equal(policy.autoDownload, true);
  assert.equal(policy.autoInstallOnQuit, false, 'updates must not be installed silently on quit');
});

test('a newer remote version is an available update', () => {
  const result = interpretCheckResult({ remoteVersion: '2.2.0' }, '2.1.0');
  assert.equal(result.state, UPDATE_STATE.available);
  assert.equal(result.version, '2.2.0');
  assert.equal(result.canDownload, true);
  assert.match(result.message, /2\.2\.0 is available/);
});

test('an unreadable feed is reported unavailable, never as up to date', () => {
  // The honesty rule: "the feed could not be read" and "there is nothing to do"
  // are opposite answers, and reporting the second keeps a user on a build with
  // known fixes while the app insists it is current.
  const result = interpretCheckResult(null, '2.1.0');
  assert.equal(result.state, UPDATE_STATE.unavailable);
  assert.equal(result.canDownload, false);
  assert.notEqual(result.state, UPDATE_STATE.upToDate);
  assert.match(result.message, /could not be reached/);
  assert.match(result.message, /cannot be confirmed as up to date/);
});

test('a feed with no version is unavailable, not a phantom update', () => {
  const result = interpretCheckResult({}, '2.1.0');
  assert.equal(result.state, UPDATE_STATE.unavailable);
  assert.equal(result.version, null);
  assert.equal(result.canDownload, false);
});

test('the same version is up to date', () => {
  const result = interpretCheckResult({ remoteVersion: '2.1.0' }, '2.1.0');
  assert.equal(result.state, UPDATE_STATE.upToDate);
  assert.equal(result.canDownload, false);
  assert.match(result.message, /latest version/);
});

test('an older published version never reads as an upgrade', () => {
  // This is the case that makes the numeric comparison load bearing: a preview
  // or dev build must not be told to "upgrade" to the previous release.
  const result = interpretCheckResult({ remoteVersion: '2.0.0' }, '2.1.0');
  assert.equal(result.state, UPDATE_STATE.upToDate);
  assert.equal(result.canDownload, false);
  assert.match(result.message, /older than the running/);
  assert.match(result.message, /preview or development build/);
});

test('release notes are read from either feed shape and are bounded', () => {
  const asString = interpretCheckResult({ remoteVersion: '2.2.0', releaseNotes: 'Fixes.' }, '2.1.0');
  assert.equal(asString.releaseNotes, 'Fixes.');

  // NSIS feeds carry a version-keyed object.
  const asObject = interpretCheckResult(
    { remoteVersion: '2.2.0', releaseNotes: { '2.2.0': 'Notes for 2.2.0' } },
    '2.1.0',
  );
  assert.equal(asObject.releaseNotes, 'Notes for 2.2.0');

  // Missing or blank notes are null, not "".
  assert.equal(interpretCheckResult({ remoteVersion: '2.2.0' }, '2.1.0').releaseNotes, null);
  assert.equal(
    interpretCheckResult({ remoteVersion: '2.2.0', releaseNotes: '   ' }, '2.1.0').releaseNotes,
    null,
  );

  // A huge body is truncated rather than pushed into an IPC payload.
  const huge = interpretCheckResult(
    { remoteVersion: '2.2.0', releaseNotes: 'x'.repeat(9000) },
    '2.1.0',
  );
  assert.equal(huge.releaseNotes.length, 2000);
});

test('an older feed carries no release notes', () => {
  // Notes for a version this build is newer than would be actively misleading.
  const result = interpretCheckResult(
    { remoteVersion: '2.0.0', releaseNotes: 'old notes' },
    '2.1.0',
  );
  assert.equal(result.releaseNotes, null);
});

test('updateInfo.version is accepted as the version source', () => {
  const result = interpretCheckResult({ updateInfo: { version: '2.2.0' } }, '2.1.0');
  assert.equal(result.state, UPDATE_STATE.available);
  assert.equal(result.version, '2.2.0');
});

test('a dev run is disabled with a stated reason', () => {
  const result = decideAvailability({ isPackaged: false });
  assert.equal(result.disabled, true);
  assert.match(result.reason, /development run/);
});

test('a build with no publish target is disabled with a stated reason', () => {
  const result = decideAvailability({ isPackaged: true, publishConfigured: false });
  assert.equal(result.disabled, true);
  assert.match(result.reason, /without a `publish` target/);
});

test('an operator kill switch wins over a working feed', () => {
  const result = decideAvailability({ isPackaged: true, publishConfigured: true, forcedDisabled: true });
  assert.equal(result.disabled, true);
  assert.match(result.reason, /disabled for this installation/);
});

test('a packaged build with a feed is available', () => {
  const result = decideAvailability({ isPackaged: true, publishConfigured: true });
  assert.equal(result.disabled, false);
  assert.equal(result.reason, null);
});