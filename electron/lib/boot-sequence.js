'use strict';

/**
 * Boot sequence orchestration, independent of Electron.
 *
 * ## What this owns
 *
 * The order in which the desktop app brings its world up, and — more
 * importantly — the rule that a failure is reported with its *cause* instead of
 * a generic timeout. `main.js` used to inline this as a linear sequence with
 * `waitForHealthy(..., abortIf)`; the abort check and the error text lived in
 * different places, so a child that died during startup produced either a
 * ten-minute hang or a message that did not name the service.
 *
 * ## The one rule worth stating
 *
 * **Reuse is only reuse if the service is verified to be *ours*.** A port
 * holding an unrelated process is not a healthy Alpha service, and attaching to
 * it is worse than failing: the app would report `gatewayUrl` it never health-
 * checked while the real Gateway was never started. Every reuse decision is
 * therefore a positive identity check, and every skip is a distinct, named
 * reason rather than a boolean.
 */

const BOOT_PHASES = Object.freeze([
  'prepare-dirs',
  'diagnostics',
  'gateway',
  'frontend',
  'open-window',
]);

/**
 * Decide what to do about a service port.
 *
 * @param {object} options
 * @param {number} options.preferredPort
 * @param {() => Promise<boolean>} options.probeIdentity is this our service?
 * @param {() => Promise<number>} options.findFreePort
 * @param {boolean} [options.skip] true when the user passed --skip-*
 * @param {boolean} [options.forcedPort] true when a port was pinned on the CLI
 * @returns {Promise<{action: 'reuse'|'spawn'|'skip', port: number,
 *                    reason: string, gatewayReused: boolean}>}
 */
async function decideServicePort({
  preferredPort,
  probeIdentity,
  findFreePort,
  skip = false,
  forcedPort = false,
} = {}) {
  if (typeof probeIdentity !== 'function' || typeof findFreePort !== 'function') {
    throw new TypeError('decideServicePort needs probeIdentity() and findFreePort()');
  }

  // A probe that throws means "I could not confirm this is ours", which is not
  // the same as "this is ours". A port that accepts a connection and then drops
  // it must not wedge the boot, and must certainly not be reported as a healthy
  // service.
  let preferredIsOurs = false;
  try {
    preferredIsOurs = Boolean(await probeIdentity(preferredPort));
  } catch {
    preferredIsOurs = false;
  }
  if (preferredIsOurs) {
    return {
      action: skip ? 'skip' : 'reuse',
      port: preferredPort,
      reason: skip
        ? `attaching to the Alpha service already serving on ${preferredPort}`
        : `reusing the Alpha service already serving on ${preferredPort}`,
      gatewayReused: true,
    };
  }

  if (skip) {
    // Attach mode with nothing to attach to. This is a real, distinct outcome
    // and the caller must say so, because the window will load a frontend that
    // has no backend to talk to.
    return {
      action: 'skip',
      port: preferredPort,
      reason:
        `attach mode was requested but no Alpha service is answering on ${preferredPort}; ` +
        'the app will load anyway and API calls will fail until one is started',
      gatewayReused: false,
    };
  }

  // A port pinned on the command line is honoured even when occupied, because
  // silently moving it would make `--gateway-port=8201` a lie. The failure then
  // surfaces from the child, which is the honest place for it.
  const port = forcedPort ? preferredPort : await findFreePort(preferredPort);
  return {
    action: 'spawn',
    port,
    reason: forcedPort
      ? `starting on the requested port ${preferredPort}`
      : port === preferredPort
        ? `starting on the preferred port ${preferredPort}`
        : `preferred port ${preferredPort} was taken by something else; starting on ${port}`,
    gatewayReused: false,
  };
}

/**
 * Run the boot phases, stopping at the first failure with its cause intact.
 *
 * @param {object} options
 * @param {Array<{name: string, run: (ctx: object) => Promise<void>|void}>} options.phases
 * @param {(message: string, detail?: string) => void} [options.onStatus] splash updates
 * @param {(line: string) => void} [options.log]
 * @param {object} [options.context] handed to every phase
 * @returns {Promise<{ok: boolean, completed: string[], failed: {name: string, error: string}|null}>}
 */
async function runBootSequence({ phases, onStatus = () => {}, log = () => {}, context = {} } = {}) {
  const completed = [];
  for (const phase of phases || []) {
    try {
      log(`boot: ${phase.name} starting`);
      // eslint-disable-next-line no-await-in-loop
      await phase.run(context);
      completed.push(phase.name);
      log(`boot: ${phase.name} done`);
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      log(`boot: ${phase.name} failed: ${message}`);
      // The phase name is part of the report. "Startup failed" with no phase
      // is what a user cannot act on; "Gateway failed: <cause>" is.
      return {
        ok: false,
        completed,
        failed: { name: phase.name, error: message },
      };
    }
  }
  return { ok: true, completed, failed: null };
}

/**
 * The user-facing title for a boot failure.
 *
 * @param {{name: string, error: string}|null} failed
 * @param {string} appName
 */
function describeBootFailure(failed, appName = 'Alpha') {
  if (!failed) return `${appName} — startup failed`;
  return `${appName} — ${failed.name} failed`;
}

module.exports = {
  BOOT_PHASES,
  decideServicePort,
  describeBootFailure,
  runBootSequence,
};
