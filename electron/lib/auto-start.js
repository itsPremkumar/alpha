'use strict';

/**
 * Start-with-Windows state, and the preference file behind it.
 *
 * ## Why a preference file at all
 *
 * `app.getLoginItemSettings()` reads the OS state, but the OS state is not the
 * source of truth for what the app *intends*. Cleaner software, a group policy,
 * or a user poking in Task Scheduler can remove the entry, and a login item that
 * silently stops existing looks identical to one the user turned off. So:
 *
 * - `auto-start.json` records the user's choice.
 * - The OS entry is reconciled to match it on every boot.
 *
 * That way a removed entry is re-registered (the user asked for it and
 * something else took it away), and a deliberate "off" is never re-added.
 *
 * The whole decision is a pure function here; `main.js` supplies Electron's
 * `app` as the two effect callbacks.
 */

const STATE_UNCHANGED = Object.freeze({ supported: false, enabled: false, active: false });

/**
 * Read the stored preference.
 *
 * A missing, unreadable, or corrupt file is `false` — the conservative reading,
 * because "enabled" would cause the app to re-register a login item the user
 * may not want.
 *
 * @param {() => string|null} readFile returns the file contents or null
 * @returns {boolean}
 */
function readPreference(readFile) {
  try {
    const raw = readFile();
    if (typeof raw !== 'string' || raw.trim() === '') return false;
    const parsed = JSON.parse(raw);
    return Boolean(parsed && parsed.enabled === true);
  } catch {
    return false;
  }
}

/** Serialize the preference. Bounded and single-purpose on purpose. */
function serializePreference(enabled) {
  return JSON.stringify({ enabled: Boolean(enabled) }, null, 2);
}

/**
 * The state shown to the user.
 *
 * `supported` is false outside a packaged install, because in a source checkout
 * the executable is the bare Electron binary: a login entry there would launch
 * Electron with no app. That has to be visible in the UI rather than discovered
 * when the user restarts Windows.
 *
 * @param {object} options
 * @param {boolean} options.isPackaged
 * @param {boolean} options.preferenceEnabled what the user chose
 * @param {boolean} options.osActive what the OS currently reports
 * @param {string} [options.reason] why it is unsupported
 */
function describeState({ isPackaged, preferenceEnabled, osActive, reason } = {}) {
  if (!isPackaged) {
    return Object.freeze({
      ...STATE_UNCHANGED,
      reason: reason || 'Start with Windows is available in the installed app only.',
    });
  }
  return Object.freeze({
    supported: true,
    enabled: Boolean(preferenceEnabled),
    active: Boolean(osActive),
    // A preference that the OS does not reflect is a divergence the user should
    // be able to see, and the boot path will reconcile it.
    diverged: Boolean(preferenceEnabled) !== Boolean(osActive),
    reason: null,
  });
}

/**
 * What should happen to the OS login item at boot.
 *
 * @param {object} options
 * @param {boolean} options.isPackaged
 * @param {boolean} options.preferenceEnabled
 * @param {boolean} options.osActive
 * @returns {{action: 'register'|'clear'|'none', reason: string}}
 */
function decideBootReconcile({ isPackaged, preferenceEnabled, osActive } = {}) {
  if (!isPackaged) {
    // Never register from a source checkout: the login item would launch the
    // bare Electron binary with no app.
    return { action: 'none', reason: 'not a packaged install; the login item is not managed here' };
  }
  if (preferenceEnabled && !osActive) {
    // Self-heal: the user opted in and something removed the entry.
    return {
      action: 'register',
      reason:
        'Start with Windows was enabled but the Windows login entry is missing; re-registering it. ' +
        'If it keeps disappearing, a cleaner or group policy may be removing it.',
    };
  }
  if (!preferenceEnabled && osActive) {
    // The user turned it off but the entry survived.
    return { action: 'clear', reason: 'Start with Windows is disabled; removing a leftover login entry' };
  }
  return { action: 'none', reason: 'the Windows login entry already matches the preference' };
}

/**
 * Can the user turn this on right now?
 *
 * An unsupported install must refuse rather than throw, and the refusal has to
 * say why — the renderer shows this text verbatim.
 *
 * @returns {{ok: boolean, state: object, message: string|null}}
 */
function decideToggle({ isPackaged, desiredEnabled } = {}) {
  if (!isPackaged) {
    return {
      ok: false,
      state: describeState({ isPackaged: false }),
      message:
        'Start with Windows is available in the installed app only. A source checkout registers ' +
        'the bare Electron binary, which would launch with no app.',
    };
  }
  return {
    ok: true,
    state: describeState({ isPackaged: true, preferenceEnabled: Boolean(desiredEnabled), osActive: Boolean(desiredEnabled) }),
    message: null,
  };
}

module.exports = {
  STATE_UNCHANGED,
  decideBootReconcile,
  decideToggle,
  describeState,
  readPreference,
  serializePreference,
};