'use strict';

/**
 * The invariant shape of the desktop status object, plus the app-info payload.
 *
 * ## The bug this exists to prevent
 *
 * `runtimeStatus` was initialised as `{ dev, packaged, frontendUrl, gatewayUrl }`
 * and then *replaced* at the end of boot with a nine-key object including
 * `versions`. The splash window calls `getStatus()` during exactly the window
 * where the short form is live, so any consumer reading `status.versions` or
 * `status.userData` got `undefined` — and, worse, a value that CHANGED under it
 * partway through boot. A status object with two shapes is indistinguishable
 * from one that is briefly empty, which is how a "healthy" claim becomes a
 * silent `undefined`.
 *
 * The rule here is the same one the frontend guide enforces for API payloads:
 * a field that is not known is `null`, never absent and never a plausible
 * default. So there is exactly ONE shape, every key is always present, and
 * "not known yet" is `null`.
 */

const STATES = require('./service-supervisor').STATES;

/** Every key the renderer may read, in a stable order. */
const STATUS_KEYS = Object.freeze([
  'dev',
  'packaged',
  'booting',
  'ready',
  'frontendUrl',
  'gatewayUrl',
  'services',
  'userData',
  'logsDir',
  'versions',
]);

const VERSION_KEYS = Object.freeze(['app', 'electron', 'chrome', 'node']);

/**
 * A status object with every key present and every unknown value `null`.
 *
 * @param {object} [overrides]
 * @returns {Readonly<object>}
 */
function createStatus(overrides = {}) {
  const status = {
    dev: false,
    packaged: false,
    booting: true,
    ready: false,
    frontendUrl: null,
    gatewayUrl: null,
    services: [],
    userData: null,
    logsDir: null,
    versions: null,
  };
  // `booting` and `ready` are handled below, not copied through.
  for (const key of STATUS_KEYS) {
    if (key === 'booting' || key === 'ready') continue;
    if (Object.prototype.hasOwnProperty.call(overrides, key) && overrides[key] !== undefined) {
      status[key] = overrides[key];
    }
  }
  if (status.versions) status.versions = createVersions(status.versions);
  if (!Array.isArray(status.services)) status.services = [];
  // Both flags are DERIVED, never accepted from the caller.
  //
  // `ready` means every supervised service is healthy. `booting` is its
  // complement, because the two together are the only honest description: a
  // claim of "ready" that is simultaneously "booting" is the contradiction this
  // object exists to make unrepresentable, and accepting either flag as an
  // input would let a caller build it.
  const allHealthy =
    status.services.length > 0 && status.services.every((service) => service.healthy === true);
  status.ready = allHealthy;
  status.booting = !status.ready;
  return Object.freeze(status);
}

/** The `versions` sub-object, or `null` when the app version is not known yet. */
function createVersions(input = {}) {
  const versions = {};
  for (const key of VERSION_KEYS) {
    const value = input[key];
    versions[key] = typeof value === 'string' && value ? value : null;
  }
  return Object.freeze(versions);
}

/**
 * Project the supervisor's records into the renderer-facing service list.
 *
 * @param {Array<{key: string, label: string, state: string, port: number|null,
 *                pid: number|null, error: string|null, restarts: number}>} records
 * @returns {ReadonlyArray<object>}
 */
function projectServices(records = []) {
  return Object.freeze(
    records.map((record) =>
      Object.freeze({
        key: record.key,
        label: record.label,
        state: record.state,
        healthy: record.state === STATES.healthy,
        port: Number.isFinite(record.port) ? record.port : null,
        pid: Number.isFinite(record.pid) ? record.pid : null,
        restarts: Number.isFinite(record.restarts) ? record.restarts : 0,
        // null, not "" and not "ok": an unknown failure reason is unknown. A
        // whitespace-only string is the absence of a reason, not a reason, and
        // a UI that renders `error ?? "unknown"` would otherwise show three
        // blank characters in place of a diagnosis.
        error: typeof record.error === 'string' && record.error.trim() ? record.error : null,
      }),
    ),
  );
}

/**
 * The static app-info payload shown by Help -> About.
 *
 * Kept separate from `status` because it is constant for the process lifetime:
 * there is no reason for a renderer to poll it, and no reason for it to carry
 * anything that changes (a mutable info object invites a caller to depend on
 * something that will differ between calls).
 */
function buildAppInfo({
  name,
  version,
  electronVersion,
  chromeVersion,
  nodeVersion,
  packaged,
  userData,
  frontendUrl,
  gatewayUrl,
  platform = process.platform,
  arch = process.arch,
} = {}) {
  return Object.freeze({
    name: name || 'Alpha',
    version: version || null,
    platform,
    arch,
    packaged: Boolean(packaged),
    userData: userData || null,
    frontendUrl: frontendUrl || null,
    gatewayUrl: gatewayUrl || null,
    versions: Object.freeze({
      app: version || null,
      electron: electronVersion || null,
      chrome: chromeVersion || null,
      node: nodeVersion || null,
    }),
  });
}

module.exports = {
  STATUS_KEYS,
  VERSION_KEYS,
  buildAppInfo,
  createStatus,
  createVersions,
  projectServices,
};
