import test from 'node:test';
import assert from 'node:assert/strict';
import {
  CHECK_ORDER,
  MIN_USER_DATA_FREE_BYTES,
  MIN_WINDOWS_BUILD,
  SEVERITY,
  formatBytes,
  formatPreflightFailure,
  runPreflight,
} from '../lib/diagnostics.js';

const HEALTHY_WIN = {
  platform: 'win32',
  arch: 'x64',
  osBuild: '22631',
  isPackaged: true,
};

test('a healthy Windows machine produces no findings', () => {
  const result = runPreflight(HEALTHY_WIN, {
    freeBytes: () => 50 * 1024 * 1024 * 1024,
    tools: [
      { key: 'uv', label: 'uv', present: true },
      { key: 'node', label: 'Node.js', present: true },
    ],
  });
  assert.deepEqual(result.findings, []);
  assert.equal(result.ok, true);
  assert.equal(result.fatal.length, 0);
});

test('checks never throw; they return findings the caller can act on', () => {
  // The original threw from the middle of the logging setup, before the log
  // file it told the user to read existed.
  const result = runPreflight({ ...HEALTHY_WIN, osBuild: '17763' }, { freeBytes: () => 0 });
  assert.equal(typeof result.ok, 'boolean');
  assert.ok(result.findings.length > 0);
  for (const finding of result.findings) {
    assert.ok(finding.message, 'a finding without a message is not actionable');
    assert.ok(finding.remedy, `finding ${finding.id} has no remedy`);
    assert.ok([SEVERITY.fatal, SEVERITY.warning, SEVERITY.info].includes(finding.severity));
  }
});

test('too little free space is fatal, and names the path and the requirement', () => {
  const result = runPreflight(HEALTHY_WIN, {
    freeBytes: () => 512 * 1024 * 1024,
    userData: 'C:/Users/x/AppData/Roaming/Alpha',
    tools: [{ key: 'uv', label: 'uv', present: true }],
  });
  assert.equal(result.ok, false);
  const finding = result.fatal.find((f) => f.id === 'free-space');
  assert.ok(finding, 'a disk-space failure was not detected');
  assert.match(finding.message, /512 MiB free/);
  assert.match(finding.message, /C:\/Users\/x\/AppData\/Roaming\/Alpha/);
  assert.match(finding.remedy, /1 GiB/);
});

test('a disk probe that is unavailable is not treated as a failure', () => {
  // The original wrapped the statfs call and logged a warning; a machine where
  // the probe fails must still be allowed to start.
  const result = runPreflight(HEALTHY_WIN, {
    freeBytes: () => null,
    tools: [{ key: 'uv', label: 'uv', present: true }],
  });
  assert.equal(result.ok, true);
  assert.deepEqual(result.findings, []);
});

test('an exactly-full disk passes the check', () => {
  // The threshold is inclusive: exactly 1 GiB is enough, and being off by one
  // byte should not block a boot.
  const result = runPreflight(HEALTHY_WIN, {
    freeBytes: () => MIN_USER_DATA_FREE_BYTES,
    tools: [{ key: 'uv', label: 'uv', present: true }],
  });
  assert.equal(result.ok, true);
  const justUnder = runPreflight(HEALTHY_WIN, {
    freeBytes: () => MIN_USER_DATA_FREE_BYTES - 1,
    tools: [{ key: 'uv', label: 'uv', present: true }],
  });
  assert.equal(justUnder.ok, false);
});

test('a 32-bit process is refused before the confusing spawn error', () => {
  const result = runPreflight({ ...HEALTHY_WIN, arch: 'ia32' }, {
    freeBytes: () => 50e9,
    tools: [{ key: 'uv', label: 'uv', present: true }],
  });
  const finding = result.fatal.find((f) => f.id === 'architecture');
  assert.ok(finding, 'a 32-bit process was allowed to boot');
  assert.match(finding.message, /ia32/);
  assert.match(finding.remedy, /64-bit/);
});

test('arm64 is supported and x64 is supported', () => {
  for (const arch of ['x64', 'arm64']) {
    const result = runPreflight({ ...HEALTHY_WIN, arch }, {
      freeBytes: () => 50e9,
      tools: [{ key: 'uv', label: 'uv', present: true }],
    });
    assert.equal(result.ok, true, `${arch} was refused`);
  }
});

test('a Windows build below the floor is fatal', () => {
  const result = runPreflight({ ...HEALTHY_WIN, osBuild: '17763' }, {
    freeBytes: () => 50e9,
    tools: [{ key: 'uv', label: 'uv', present: true }],
  });
  const finding = result.fatal.find((f) => f.id === 'os-version');
  assert.ok(finding, 'an unsupported Windows build was allowed to boot');
  assert.match(finding.message, new RegExp(String(MIN_WINDOWS_BUILD)));
  assert.match(finding.remedy, /Windows Update/);
});

test('an unreadable OS build is not treated as too old', () => {
  // Absent or unparseable must not read as "0", which is below every floor.
  for (const osBuild of [undefined, null, '', 'unknown', 'not-a-number']) {
    const result = runPreflight({ ...HEALTHY_WIN, osBuild }, {
      freeBytes: () => 50e9,
      tools: [{ key: 'uv', label: 'uv', present: true }],
    });
    assert.equal(result.ok, true, `osBuild=${JSON.stringify(osBuild)} was treated as too old`);
  }
});

test('a missing bundled runtime is fatal in a packaged app, a warning in a dev run', () => {
  // The distinction is the whole point of bundling: a packaged app must not
  // silently fall back to a system PATH copy, but a developer running from
  // sources can reasonably be told to install it.
  const tools = [{ key: 'uv', label: 'uv', present: false }];
  const packaged = runPreflight(HEALTHY_WIN, { freeBytes: () => 50e9, tools });
  const packagedFinding = packaged.findings.find((f) => f.id === 'runtime-uv');
  assert.equal(packagedFinding.severity, SEVERITY.fatal);
  assert.match(packagedFinding.remedy, /Reinstall/);

  const dev = runPreflight({ ...HEALTHY_WIN, isPackaged: false }, { freeBytes: () => 50e9, tools });
  const devFinding = dev.findings.find((f) => f.id === 'runtime-uv');
  assert.equal(devFinding.severity, SEVERITY.warning);
  assert.equal(dev.ok, true, 'a dev run is blocked by a missing PATH tool');
  assert.match(devFinding.remedy, /Install uv/);
});

test('a non-Windows platform warns rather than pretending to be supported', () => {
  for (const platform of ['darwin', 'linux']) {
    const result = runPreflight({ ...HEALTHY_WIN, platform }, {
      freeBytes: () => 50e9,
      tools: [{ key: 'uv', label: 'uv', present: true }],
    });
    assert.equal(result.ok, true, `${platform} was treated as fatal`);
    const finding = result.findings.find((f) => f.id === 'platform');
    assert.equal(finding.severity, SEVERITY.warning);
    assert.match(finding.remedy, /make dev/);
  }
});

test('every fatal finding is shown, not just the first', () => {
  // A machine that is both out of disk and on too old a Windows must be told
  // both, or fixing one just produces the other on the next launch.
  const result = runPreflight({ ...HEALTHY_WIN, osBuild: '17763', arch: 'ia32' }, {
    freeBytes: () => 0,
    tools: [{ key: 'uv', label: 'uv', present: false }],
  });
  assert.ok(result.fatal.length >= 4, `only ${result.fatal.length} fatal findings reported`);
  const text = formatPreflightFailure(result, 'Alpha');
  for (const finding of result.fatal) {
    assert.ok(text.includes(finding.message), `"${finding.message}" is missing from the report`);
    assert.ok(text.includes(finding.remedy), `the remedy for ${finding.id} is missing`);
  }
  assert.match(text, /^Alpha cannot start on this computer\./);
});

test('findings are ordered by check, then by severity', () => {
  const result = runPreflight({ ...HEALTHY_WIN, arch: 'ia32', osBuild: '17763' }, {
    freeBytes: () => 0,
    tools: [{ key: 'uv', label: 'uv', present: false }],
  });
  const ids = result.findings.map((f) => f.id);
  // architecture before os-version before free-space, per CHECK_ORDER.
  assert.ok(ids.indexOf('architecture') < ids.indexOf('os-version'));
  assert.ok(ids.indexOf('os-version') < ids.indexOf('free-space'));
  for (const id of ids) {
    const base = id.split('-').slice(0, 2).join('-');
    assert.ok(CHECK_ORDER.includes(base) || base.startsWith('runtime'), `unknown check ${id}`);
  }
});

test('byte formatting is readable at both scales', () => {
  assert.equal(formatBytes(512 * 1024 * 1024), '512 MiB');
  assert.equal(formatBytes(2 * 1024 * 1024 * 1024), '2.0 GiB');
  assert.equal(formatBytes(50 * 1024 * 1024 * 1024), '50.0 GiB');
});

test('a finding-free run still formats without crashing', () => {
  const result = runPreflight(HEALTHY_WIN, { freeBytes: () => 50e9, tools: [] });
  assert.equal(formatPreflightFailure(result, 'Alpha'), 'Alpha cannot start on this computer.');
});

test('the documented floor is the one the rest of the repo states', () => {
  // Windows 10 2004. Asserted so a change here is a deliberate one, matching
  // installer/pins.json's documented floor.
  assert.equal(MIN_WINDOWS_BUILD, 19041);
});
