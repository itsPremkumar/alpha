'use strict';

/**
 * Restart policy for the desktop's child services.
 *
 * ## The gap this exists to close
 *
 * `main.js` spawned the Gateway and the frontend once. If either process died
 * while the app was open, `attachChildLogging` broadcast "process stopped
 * unexpectedly" and nothing more: the window kept showing a dead app until
 * the user noticed and restarted Alpha by hand. `recovery/watchdog.ps1` has
 * Layers 2-4 for the `start.ps1` launcher, but the desktop app spawns its
 * services itself and had no equivalent, so the one place a user is most
 * likely to be mid-conversation was the one place with no self-healing.
 *
 * ## Why restart-on-exit alone is not the fix
 *
 * Naively respawning a process that dies immediately is a fork bomb with a
 * 1-second period: it burns CPU, fills the log with thousands of lines, and
 * hides the actual error. So this is a *bounded* policy, not a retry loop:
 *
 *   - A service that stayed up long enough to be considered healthy resets its
 *     own counter. One crash a day is a crash, not a crash loop.
 *   - A service that keeps dying inside the window exhausts the budget and the
 *     policy returns `give-up`. That is a deliberate, user-visible state: the
 *     app stops pretending to work and points at the log, because the cause is
 *     almost always configuration (a bad model API key, a missing dependency)
 *     and no amount of respawning will fix it.
 *
 * Everything here is a pure function of its arguments, so the decision table is
 * unit-testable without spawning a process, opening a port, or importing
 * Electron.
 */

/** Conservative defaults. Overridable per service at construction. */
const DEFAULT_RESTART_POLICY = Object.freeze({
  /** Delay before the first restart, doubled per consecutive attempt. */
  baseDelayMs: 1000,
  /** Ceiling for the exponential backoff. */
  maxDelayMs: 30000,
  /** Fractional jitter applied to the computed delay, 0 disables it. */
  jitterRatio: 0.25,
  /**
   * How long a service must stay up before it is considered to have booted
   * successfully. Below this, an exit counts as a crash for budget purposes.
   * Generous enough to clear a slow first-launch `uv sync` / Next.js boot.
   */
  minHealthyUptimeMs: 30000,
  /** Restarts allowed inside `restartWindowMs` before giving up. */
  maxRestartsInWindow: 5,
  /** The sliding window `maxRestartsInWindow` is counted over. */
  restartWindowMs: 5 * 60 * 1000,
});

function positiveInt(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback;
}

function nonNegativeNumber(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}

function ratio(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}

/** Merge caller overrides over the defaults, discarding nonsense values. */
function resolvePolicy(overrides = {}) {
  const d = DEFAULT_RESTART_POLICY;
  return Object.freeze({
    baseDelayMs: positiveInt(overrides.baseDelayMs, d.baseDelayMs),
    maxDelayMs: positiveInt(overrides.maxDelayMs, d.maxDelayMs),
    jitterRatio: Math.min(1, ratio(overrides.jitterRatio, d.jitterRatio)),
    minHealthyUptimeMs: nonNegativeNumber(overrides.minHealthyUptimeMs, d.minHealthyUptimeMs),
    maxRestartsInWindow: positiveInt(overrides.maxRestartsInWindow, d.maxRestartsInWindow),
    restartWindowMs: positiveInt(overrides.restartWindowMs, d.restartWindowMs),
  });
}

/**
 * Tracked restart history for one service.
 *
 * Holds only timestamps, so it is safe to persist or to inspect from a test
 * without any process handles.
 */
class RestartBudget {
  constructor(options = {}) {
    this.policy = resolvePolicy(options.policy);
    this.now = typeof options.now === 'function' ? options.now : Date.now;
    this.random = typeof options.random === 'function' ? options.random : Math.random;
    /** Timestamps of restarts still inside the sliding window. */
    this.restartTimes = [];
    /** Consecutive attempts since the last healthy run. */
    this.attempt = 0;
    /** `performance.now()`-ish mark of the current run, or null when stopped. */
    this.startedAt = null;
  }

  /**
   * Record that a run began.
   *
   * Deliberately does NOT touch the attempt counter. Whether a run counts as
   * healthy can only be judged once it ends, when the uptime is known, so the
   * whole decision lives in `decideExit`. Resetting here as well would mean two
   * places deciding the same thing, and they would disagree for a service that
   * exits without a matching `start`.
   */
  start(now = this.now()) {
    this.startedAt = now;
    return this;
  }

  /** How long the current run lasted, or 0 when not running. */
  uptimeMs(now = this.now()) {
    if (this.startedAt === null) return 0;
    return Math.max(0, now - this.startedAt);
  }

  /** Stop the run without treating it as a failure (deliberate shutdown). */
  stop() {
    this.startedAt = null;
    return this;
  }

  /** Restarts still counted against the window budget. */
  restartsInWindow(now = this.now()) {
    const cutoff = now - this.policy.restartWindowMs;
    this.restartTimes = this.restartTimes.filter((at) => at > cutoff);
    return this.restartTimes.length;
  }

  /**
   * Decide what to do about a service that just exited.
   *
   * Two numbers are reported and they are deliberately not the same thing:
   * `attempt` is the count of consecutive unhealthy exits (1-based, for logs
   * and for the crash-loop budget), while the backoff step is derived from it
   * so the first restart of a sequence always uses `baseDelayMs`. A run that
   * lasted past `minHealthyUptimeMs` therefore reports `attempt: 1` and still
   * backs off from the base delay, rather than continuing a stale sequence.
   *
   * @param {object} [options]
   * @param {number} [options.now] current time, injectable for tests
   * @param {number|null} [options.uptimeMs] override the measured uptime
   * @returns {{action: 'restart'|'give-up', delayMs: number, attempt: number,
   *            restartsInWindow: number, reason: string}}
   */
  decideExit(options = {}) {
    const now = options.now === undefined ? this.now() : options.now;
    const uptime = options.uptimeMs === undefined ? this.uptimeMs(now) : options.uptimeMs;

    // A run that lasted past the healthy threshold is not part of a crash loop,
    // so it starts a fresh sequence instead of continuing the previous one.
    const wasHealthyRun = uptime >= this.policy.minHealthyUptimeMs;
    const attempt = wasHealthyRun ? 1 : this.attempt + 1;

    if (this.restartsInWindow(now) >= this.policy.maxRestartsInWindow) {
      return Object.freeze({
        action: 'give-up',
        delayMs: 0,
        attempt,
        restartsInWindow: this.restartTimes.length,
        reason:
          `gave up after ${this.restartTimes.length} restarts within ` +
          `${Math.round(this.policy.restartWindowMs / 1000)}s`,
      });
    }

    this.attempt = attempt;
    this.restartTimes.push(now);
    this.startedAt = null;

    return Object.freeze({
      action: 'restart',
      delayMs: this.computeDelay(attempt),
      attempt,
      restartsInWindow: this.restartTimes.length,
      reason: `restart ${attempt} of ${this.policy.maxRestartsInWindow} budgeted in ${Math.round(
        this.policy.restartWindowMs / 1000,
      )}s`,
    });
  }

  /**
   * Exponential backoff with symmetric jitter.
   *
   * `attempt` is 1-based, so attempt 1 sleeps `baseDelayMs`, attempt 2 sleeps
   * twice that, and so on up to `maxDelayMs`.
   *
   * Jitter matters more than the exponent here: two Alpha instances started by
   * the same Windows user profile (or a service plus a relaunch) would
   * otherwise retry in lockstep and re-collide on the same port.
   */
  computeDelay(attempt) {
    const { baseDelayMs, maxDelayMs, jitterRatio } = this.policy;
    const step = Math.max(1, Math.floor(attempt));
    const exponential = Math.min(maxDelayMs, baseDelayMs * 2 ** (step - 1));
    if (jitterRatio === 0) return Math.round(exponential);
    const spread = exponential * jitterRatio;
    const offset = (this.random() * 2 - 1) * spread;
    return Math.max(0, Math.round(exponential + offset));
  }
}

module.exports = {
  DEFAULT_RESTART_POLICY,
  RestartBudget,
  resolvePolicy,
};
