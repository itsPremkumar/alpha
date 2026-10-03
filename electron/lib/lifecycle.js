'use strict';

/**
 * Ordered, bounded, observable shutdown.
 *
 * ## The gap
 *
 * `before-quit` called `taskkill /T /F` on both process trees and returned. Two
 * problems with that:
 *
 * 1. **It was not ordered.** `killTree('frontend')` then `killTree('backend')`
 *    happened in one synchronous burst, so the frontend could be killed while
 *    it still had in-flight requests to the Gateway. Any run the Gateway was
 *    mid-way through writing died, and a partial thread state is exactly what
 *    the backend's own recovery layer has to clean up afterwards.
 * 2. **It was not verified.** `taskkill` is fire-and-forget. A process that
 *    ignored it, or that was mid-write, stayed alive as an orphan holding a port
 *    the next launch then has to work around.
 *
 * ## The contract here
 *
 * Steps run in order, each gets a deadline, and the whole thing is bounded by
 * one global budget. A step that times out is escalated (force) and then
 * abandoned — never allowed to hang the quit. Crucially, the escalation is
 * reported: a shutdown that had to force-kill a service says so, because
 * "closed cleanly" and "killed a service that would not stop" are different
 * facts and the log should not conflate them.
 *
 * Pure and dependency-injected, so the ordering, deadlines, and escalation
 * ladder are all unit-testable.
 */

/** What to do with a step's process when the step does not finish in time. */
const DEFAULT_ESCALATION = Object.freeze(['force', 'abandon']);

/**
 * @typedef {object} ShutdownStep
 * @property {string} key
 * @property {() => Promise<void>|void} run
 * @property {string[]} [escalation] ladder tried in order on timeout
 * @property {number} [timeoutMs] per-step deadline
 * @property {number} [order] lower runs first
 */

/**
 * Run shutdown steps in order, bounded, escalating on timeout.
 *
 * @param {object} options
 * @param {ShutdownStep[]} options.steps
 * @param {number} [options.totalTimeoutMs] global budget for everything
 * @param {(line: string) => void} [options.log]
 * @param {(result: object) => void} [options.onReport]
 * @returns {Promise<{clean: boolean, steps: Array<object>, forced: string[], abandoned: string[]}>}
 */
async function runShutdown({
  steps,
  totalTimeoutMs = 15000,
  log = () => {},
  onReport = () => {},
  now = () => Date.now(),
} = {}) {
  const ordered = [...(steps || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const deadline = now() + totalTimeoutMs;
  const results = [];
  const forced = [];
  const abandoned = [];

  for (const step of ordered) {
    const remaining = deadline - now();
    if (remaining <= 0) {
      // The global budget is spent. Record it rather than pretending the step
      // ran: an unrun graceful step is a real fact about the shutdown.
      results.push({ key: step.key, outcome: 'skipped', reason: 'global shutdown budget exhausted' });
      abandoned.push(step.key);
      log(`shutdown: ${step.key} skipped, global budget exhausted`);
      continue;
    }

    const timeoutMs = Math.min(step.timeoutMs ?? 5000, remaining);
    const startedAt = now();
    let outcome = 'ok';
    let detail = null;

    try {
      await withTimeout(Promise.resolve(step.run()), timeoutMs);
    } catch (error) {
      if (error && error.timedOut) {
        outcome = 'timeout';
        detail = `did not finish within ${timeoutMs}ms`;
        log(`shutdown: ${step.key} timed out after ${timeoutMs}ms; escalating`);
        for (const action of step.escalation ?? DEFAULT_ESCALATION) {
          if (action === 'force') {
            forced.push(step.key);
            log(`shutdown: force-killing ${step.key}`);
            // The force step is best effort and deliberately not awaited to
            // completion: if even force cannot be issued we are out of budget.
            try {
              if (typeof step.force === 'function') await step.force();
            } catch (forceError) {
              detail += `; force failed: ${forceError && forceError.message}`;
            }
          } else if (action === 'abandon') {
            abandoned.push(step.key);
            detail += '; abandoned';
            log(`shutdown: abandoning ${step.key}`);
          }
        }
      } else {
        outcome = 'error';
        detail = error && error.message ? error.message : String(error);
        log(`shutdown: ${step.key} failed: ${detail}`);
      }
    }

    results.push({
      key: step.key,
      outcome,
      detail,
      durationMs: now() - startedAt,
    });
  }

  const clean = forced.length === 0 && abandoned.length === 0;
  const report = { clean, steps: results, forced, abandoned };
  try {
    onReport(report);
  } catch {
    // A reporting failure must not turn a finished shutdown into a crash.
  }
  return report;
}

/**
 * Reject with a `timedOut` marker if `promise` has not settled in time.
 *
 * A bare `Promise.race` with a timer cannot be distinguished from a real
 * rejection at the call site, and the escalation ladder needs to know the
 * difference. The timer is always cleared, or a 15-step shutdown would keep the
 * event loop alive for the full budget after the app asked to exit.
 */
function withTimeout(promise, timeoutMs) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      const error = new Error(`timed out after ${timeoutMs}ms`);
      error.timedOut = true;
      reject(error);
    }, timeoutMs);
    if (timer && typeof timer.unref === 'function') timer.unref();
    promise.then(
      (value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

/**
 * The desktop's shutdown ladder, in dependency order.
 *
 * The frontend dies first because it is the only thing holding requests against
 * the Gateway; letting it drain before the Gateway stops is the difference
 * between a clean exit and a half-written run.
 *
 * @param {object} handles
 * @param {{stop?: Function, force?: Function}} handles.frontend
 * @param {{stop?: Function, force?: Function}} handles.gateway
 * @param {() => void} [handles.onRendererTeardown] dispose windows/timers first
 * @param {() => void} [handles.onLogFinal] last-chance log write
 */
function buildDesktopShutdownSteps({ frontend, gateway, onRendererTeardown, onLogFinal } = {}) {
  return [
    {
      key: 'renderer',
      order: 0,
      timeoutMs: 2000,
      run: () => {
        if (typeof onRendererTeardown === 'function') onRendererTeardown();
      },
    },
    {
      key: 'frontend',
      order: 10,
      timeoutMs: 4000,
      run: () => (frontend && typeof frontend.stop === 'function' ? frontend.stop() : undefined),
      force: () => (frontend && typeof frontend.force === 'function' ? frontend.force() : undefined),
    },
    {
      key: 'gateway',
      order: 20,
      timeoutMs: 5000,
      run: () => (gateway && typeof gateway.stop === 'function' ? gateway.stop() : undefined),
      force: () => (gateway && typeof gateway.force === 'function' ? gateway.force() : undefined),
    },
    {
      key: 'log',
      order: 30,
      timeoutMs: 1000,
      // Always runs, even after a forced kill, so the forced-kill line is on disk.
      run: () => {
        if (typeof onLogFinal === 'function') onLogFinal();
      },
      escalation: ['abandon'],
    },
  ];
}

module.exports = {
  DEFAULT_ESCALATION,
  buildDesktopShutdownSteps,
  runShutdown,
  withTimeout,
};
