'use strict';

/**
 * Auto-update policy: what to do, when, and how to report it.
 *
 * ## Why this is a policy module
 *
 * `electron-updater` is easy to wire up badly. The three failure modes that
 * matter are all decisions, not API calls:
 *
 * 1. **When to check.** Checking on every launch means every user is told about
 *    an update within seconds of it shipping, so a bad build reaches everyone at
 *    once. Not checking at all is how users end up months behind on a version
 *    that no longer receives fixes. The rule here is a *minimum interval
 *    between checks*, so checking is automatic but never a stampede.
 * 2. **When to download.** Downloading silently during a conversation can
 *    saturate a metered connection and swap a running build's files. Download is
 *    only started once a check has found something, never on a timer.
 * 3. **What "up to date" means.** A provider that returns a malformed feed
 *    must be reported as an *error*, not as "you are up to date". Silently
 *    reporting success for a feed that could not be read is how a user stays on
 *    a vulnerable build while the app insists there is nothing to do.
 *
 * Every function here is pure and takes the current time and the feed as input,
 * so the whole policy is testable with no network and no Electron.
 */

/** States the renderer can observe. */
const UPDATE_STATE = Object.freeze({
  idle: 'idle',
  checking: 'checking',
  unavailable: 'unavailable',
  upToDate: 'up-to-date',
  available: 'update-available',
  downloading: 'downloading',
  ready: 'ready-to-install',
  error: 'error',
  disabled: 'disabled',
});

const DEFAULT_POLICY = Object.freeze({
  /** Never check more often than this, however often the app is launched. */
  minCheckIntervalMs: 6 * 60 * 60 * 1000, // 6 hours
  /** Check once shortly after launch, then only every minCheckIntervalMs. */
  checkAfterStartupMs: 30 * 1000,
  /** Never interrupt: updates are offered, never installed silently on quit. */
  autoInstallOnQuit: false,
  /** Downloads only start after a check found a version. */
  autoDownload: true,
});

function resolvePolicy(overrides = {}) {
  return Object.freeze({
    minCheckIntervalMs: positive(overrides.minCheckIntervalMs, DEFAULT_POLICY.minCheckIntervalMs),
    checkAfterStartupMs: positive(overrides.checkAfterStartupMs, DEFAULT_POLICY.checkAfterStartupMs),
    autoInstallOnQuit: Boolean(overrides.autoInstallOnQuit ?? DEFAULT_POLICY.autoInstallOnQuit),
    autoDownload: Boolean(overrides.autoDownload ?? DEFAULT_POLICY.autoDownload),
  });
}

function positive(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

/**
 * Compare dotted version strings.
 *
 * A lexicographic comparison is the classic bug here: `'10.0.0' < '9.0.0'` is
 * true as a string, so an app on v10 would decide v9 was an upgrade and offer a
 * downgrade. Numeric segments only, and a pre-release suffix sorts BELOW its
 * release (`1.2.0-rc.1` before `1.2.0`).
 *
 * @returns {number} negative if a < b, positive if a > b, 0 if equal
 */
function compareVersions(a, b) {
  const parse = (value) => {
    const text = String(value ?? '').trim().replace(/^v/i, '');
    if (!text) return null;
    const [core, ...preParts] = text.split('-');
    const segments = core.split('.').map((segment) => {
      const parsed = Number.parseInt(segment, 10);
      return Number.isFinite(parsed) ? parsed : 0;
    });
    while (segments.length < 3) segments.push(0);
    return { segments, pre: preParts.join('-') };
  };

  const left = parse(a);
  const right = parse(b);
  if (!left && !right) return 0;
  // An unparseable version is treated as older, so a corrupt feed cannot make
  // the app believe it must downgrade.
  if (!left) return -1;
  if (!right) return 1;

  for (let i = 0; i < Math.max(left.segments.length, right.segments.length); i += 1) {
    const delta = (left.segments[i] || 0) - (right.segments[i] || 0);
    if (delta !== 0) return delta < 0 ? -1 : 1;
  }
  // A pre-release is older than the release it leads to.
  if (left.pre === right.pre) return 0;
  if (!left.pre) return 1;
  if (!right.pre) return -1;
  return left.pre < right.pre ? -1 : 1;
}

/**
 * Should a check run right now?
 *
 * @param {object} options
 * @param {number|null} options.lastCheckAt epoch ms of the last check, null if never
 * @param {number} options.now
 * @param {number} [options.startupAt] when this launch began
 * @param {object} [policy]
 * @returns {{check: boolean, reason: string}}
 */
function decideCheck({ lastCheckAt, now, startupAt, policy: overrides } = {}) {
  const policy = resolvePolicy(overrides);
  if (lastCheckAt === null || lastCheckAt === undefined || !Number.isFinite(lastCheckAt)) {
    return { check: true, reason: 'no previous check on record' };
  }
  const elapsed = now - lastCheckAt;
  if (elapsed < 0) {
    // A clock that moved backwards (NTP correction, suspend) must not lock out
    // updates forever.
    return { check: true, reason: 'the recorded last check is in the future; re-checking' };
  }
  if (Number.isFinite(startupAt) && now - startupAt < policy.checkAfterStartupMs) {
    return {
      check: false,
      reason: `too soon after launch (${Math.round(now - startupAt)}ms of ${policy.checkAfterStartupMs}ms)`,
    };
  }
  if (elapsed < policy.minCheckIntervalMs) {
    return {
      check: false,
      reason: `checked ${formatDuration(elapsed)} ago; the minimum interval is ${formatDuration(
        policy.minCheckIntervalMs,
      )}`,
    };
  }
  return {
    check: true,
    reason: `last check was ${formatDuration(elapsed)} ago, over the ${formatDuration(
      policy.minCheckIntervalMs,
    )} minimum`,
  };
}

/**
 * Interpret an update feed response.
 *
 * @param {object|null} result `electron-updater`'s UpdateCheckResult, or null
 * @param {string} currentVersion
 * @returns {{state: string, version: string|null, releaseNotes: string|null,
 *            message: string, canDownload: boolean}}
 */
function interpretCheckResult(result, currentVersion) {
  if (!result) {
    // The honest reading of "the feed could not be read": unknown, not current.
    return {
      state: UPDATE_STATE.unavailable,
      version: null,
      releaseNotes: null,
      message:
        'The update server could not be reached, so this build cannot be confirmed as up to date. ' +
        'It will keep running; nothing is being downloaded.',
      canDownload: false,
    };
  }

  // A remote version we cannot parse is not evidence of an upgrade.
  const remote = result.remoteVersion ?? result.updateInfo?.version ?? null;
  if (!remote) {
    return {
      state: UPDATE_STATE.unavailable,
      version: null,
      releaseNotes: null,
      message: 'The update feed did not name a version, so there is nothing to compare against.',
      canDownload: false,
    };
  }

  const order = compareVersions(remote, currentVersion);
  if (order === 0) {
    return {
      state: UPDATE_STATE.upToDate,
      version: remote,
      releaseNotes: readReleaseNotes(result),
      message: `Alpha ${remote} is the latest version.`,
      canDownload: false,
    };
  }
  if (order < 0) {
    // A published version OLDER than this build. Not an upgrade.
    return {
      state: UPDATE_STATE.upToDate,
      version: remote,
      releaseNotes: null,
      message:
        `The update feed offers ${remote}, which is older than the running ${currentVersion}. ` +
        'This is a preview or development build, so no update is offered.',
      canDownload: false,
    };
  }
  return {
    state: UPDATE_STATE.available,
    version: remote,
    releaseNotes: readReleaseNotes(result),
    message: `Alpha ${remote} is available (you have ${currentVersion}).`,
    canDownload: true,
  };
}

function readReleaseNotes(result) {
  const notes = result.releaseNotes;
  if (typeof notes === 'string' && notes.trim()) return notes.trim().slice(0, 2000);
  if (notes && typeof notes === 'object') {
    // NSIS feeds carry a version-keyed object.
    const first = Object.values(notes).find((value) => typeof value === 'string' && value.trim());
    if (first) return String(first).trim().slice(0, 2000);
  }
  return null;
}

function formatDuration(ms) {
  if (ms < 60000) return `${Math.round(ms / 1000)}s`;
  if (ms < 3600000) return `${Math.round(ms / 60000)}m`;
  return `${(ms / 3600000).toFixed(1)}h`;
}

/**
 * Why updates are unavailable, when they are.
 *
 * A dev run has no `app-update.yml`, and a packaged build with no `publish`
 * block in electron-builder.yml has no feed. Both are configuration states, and
 * both deserve a stated reason rather than a silent "up to date".
 *
 * @param {object} options
 * @param {boolean} options.isPackaged
 * @param {boolean} [options.publishConfigured]
 * @param {boolean} [options.forcedDisabled] an operator kill switch
 * @returns {{disabled: boolean, reason: string|null}}
 */
function decideAvailability({ isPackaged, publishConfigured = true, forcedDisabled = false } = {}) {
  if (forcedDisabled) {
    return { disabled: true, reason: 'updates are disabled for this installation' };
  }
  if (!isPackaged) {
    return {
      disabled: true,
      reason: 'this is a development run, which has no published update feed',
    };
  }
  if (!publishConfigured) {
    return {
      disabled: true,
      reason:
        'this build was packaged without a `publish` target in electron-builder.yml, so there is ' +
        'no feed to check',
    };
  }
  return { disabled: false, reason: null };
}

module.exports = {
  DEFAULT_POLICY,
  UPDATE_STATE,
  compareVersions,
  decideAvailability,
  decideCheck,
  interpretCheckResult,
  resolvePolicy,
};
