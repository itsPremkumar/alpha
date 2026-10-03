import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_RESTART_POLICY,
  RestartBudget,
  resolvePolicy,
} from '../lib/restart-policy.js';

const noJitter = (policy = {}) =>
  new RestartBudget({ now: () => 0, random: () => 0.5, policy: { jitterRatio: 0, ...policy } });

test('defaults are the conservative shipped values', () => {
  assert.equal(DEFAULT_RESTART_POLICY.baseDelayMs, 1000);
  assert.equal(DEFAULT_RESTART_POLICY.maxDelayMs, 30000);
  assert.equal(DEFAULT_RESTART_POLICY.minHealthyUptimeMs, 30000);
  assert.equal(DEFAULT_RESTART_POLICY.maxRestartsInWindow, 5);
  assert.equal(DEFAULT_RESTART_POLICY.restartWindowMs, 300000);
});

test('nonsense policy overrides are discarded rather than trusted', () => {
  const policy = resolvePolicy({
    baseDelayMs: -5,
    maxDelayMs: 'nope',
    minHealthyUptimeMs: NaN,
    maxRestartsInWindow: 0,
    jitterRatio: 9,
  });
  assert.equal(policy.baseDelayMs, DEFAULT_RESTART_POLICY.baseDelayMs);
  assert.equal(policy.maxDelayMs, DEFAULT_RESTART_POLICY.maxDelayMs);
  assert.equal(policy.minHealthyUptimeMs, DEFAULT_RESTART_POLICY.minHealthyUptimeMs);
  assert.equal(policy.maxRestartsInWindow, DEFAULT_RESTART_POLICY.maxRestartsInWindow);
  // Jitter is a fraction, so it is clamped rather than replaced.
  assert.equal(policy.jitterRatio, 1);
});

test('the first crash restarts fast, then backs off exponentially', () => {
  const budget = noJitter();
  budget.start(0);
  // Uptime 0 is below the healthy threshold, so this counts as a crash.
  const first = budget.decideExit({ now: 1000, uptimeMs: 0 });
  assert.equal(first.action, 'restart');
  assert.equal(first.attempt, 1);
  assert.equal(first.delayMs, 1000);

  budget.start(2000);
  const second = budget.decideExit({ now: 3000, uptimeMs: 0 });
  assert.equal(second.attempt, 2);
  assert.equal(second.delayMs, 2000);

  budget.start(4000);
  const third = budget.decideExit({ now: 5000, uptimeMs: 0 });
  assert.equal(third.attempt, 3);
  assert.equal(third.delayMs, 4000);
});

test('backoff is capped so a crash loop never sleeps for hours', () => {
  // Tested on computeDelay directly: driving it through decideExit() would run
  // into the give-up budget long before reaching the ceiling, which is a
  // different rule with its own test below.
  const budget = noJitter({ baseDelayMs: 1000, maxDelayMs: 10000 });
  const delays = [1, 2, 3, 4, 5, 6, 7, 8, 20].map((attempt) => budget.computeDelay(attempt));
  assert.deepEqual(delays, [1000, 2000, 4000, 8000, 10000, 10000, 10000, 10000, 10000]);
  // Monotonic up to the ceiling, then flat. A jitter-free budget must never
  // decrease, or a longer-running crash loop would retry *sooner*.
  for (let i = 1; i < delays.length; i += 1) {
    assert.ok(delays[i] >= delays[i - 1], `delay ${i} (${delays[i]}) < ${delays[i - 1]}`);
  }
});

test('jitter stays within the configured band and never goes negative', () => {
  // random() -> 0 is the low edge, -> 1 the high edge.
  const low = new RestartBudget({ now: () => 0, random: () => 0 });
  low.start(0);
  const lowDelay = low.decideExit({ now: 0, uptimeMs: 0 }).delayMs;
  assert.equal(lowDelay, 750); // 1000 - 25%

  const high = new RestartBudget({ now: () => 0, random: () => 1 });
  high.start(0);
  const highDelay = high.decideExit({ now: 0, uptimeMs: 0 }).delayMs;
  assert.equal(highDelay, 1250); // 1000 + 25%

  // A base delay smaller than the jitter band must still clamp at 0, not go
  // negative and become an immediate busy-loop.
  const tiny = new RestartBudget({
    now: () => 0,
    random: () => 0,
    policy: { baseDelayMs: 100, jitterRatio: 1 },
  });
  tiny.start(0);
  assert.equal(tiny.decideExit({ now: 0, uptimeMs: 0 }).delayMs, 0);
});

test('two instances do not retry in lockstep', () => {
  // The whole point of jitter: without it, a service restarted by a relaunch
  // and one restarted by supervision would collide on the same port at the
  // same instant, forever.
  const a = new RestartBudget({ now: () => 0, random: () => 0.1 });
  const b = new RestartBudget({ now: () => 0, random: () => 0.9 });
  a.start(0);
  b.start(0);
  const delayA = a.decideExit({ now: 0, uptimeMs: 0 }).delayMs;
  const delayB = b.decideExit({ now: 0, uptimeMs: 0 }).delayMs;
  assert.notEqual(delayA, delayB);
  assert.ok(delayA < delayB);
});

test('a run that lasted past the healthy threshold starts a fresh sequence', () => {
  // One crash a day is a crash, not a crash loop. Without this reset the
  // attempt counter would climb forever across a long-lived install and a
  // service would eventually refuse to restart at all.
  const budget = noJitter();
  budget.start(0);
  budget.decideExit({ now: 100, uptimeMs: 0 });
  budget.start(200);
  budget.decideExit({ now: 300, uptimeMs: 0 });
  assert.equal(budget.attempt, 2);

  // Now the service runs for 10 minutes, well past minHealthyUptimeMs.
  budget.start(400);
  const afterHealthyRun = budget.decideExit({ now: 400 + 600000, uptimeMs: 600000 });
  assert.equal(afterHealthyRun.attempt, 1);
  assert.equal(afterHealthyRun.action, 'restart');
});

test('a genuine crash loop is given up on instead of respawned forever', () => {
  const budget = noJitter({ maxRestartsInWindow: 3, restartWindowMs: 60000 });
  for (let i = 0; i < 3; i += 1) {
    budget.start(i * 1000);
    const decision = budget.decideExit({ now: i * 1000, uptimeMs: 0 });
    assert.equal(decision.action, 'restart', `restart ${i + 1} should still be allowed`);
  }
  budget.start(3000);
  const final = budget.decideExit({ now: 3000, uptimeMs: 0 });
  assert.equal(final.action, 'give-up');
  assert.equal(final.delayMs, 0);
  assert.match(final.reason, /gave up after 3 restarts/);
});

test('the window slides, so a slow trickle of crashes is still recovered', () => {
  // The budget is a sliding window, not a lifetime cap. A service that crashes
  // hourly must be restarted forever, because that is a working app with an
  // occasional bad day, not a crash loop.
  const budget = noJitter({ maxRestartsInWindow: 3, restartWindowMs: 60000 });
  const minute = 60000;
  for (let hour = 0; hour < 4; hour += 1) {
    const at = hour * 60 * minute;
    budget.start(at);
    const decision = budget.decideExit({ now: at, uptimeMs: 0 });
    assert.equal(decision.action, 'restart', `hour ${hour} should still be restarted`);
  }
});

test('uptime is measured from the recorded start and zero when stopped', () => {
  let clock = 1000;
  const budget = new RestartBudget({ now: () => clock });
  assert.equal(budget.uptimeMs(), 0, 'not running yet');
  budget.start();
  clock = 4000;
  assert.equal(budget.uptimeMs(), 3000);
  budget.stop();
  assert.equal(budget.uptimeMs(), 0);
});

test('a clock that goes backwards cannot produce a negative uptime', () => {
  // Suspend/resume and NTP corrections both do this on Windows. A negative
  // uptime would read as "crash-looping" and trigger a needless give-up.
  let clock = 100000;
  const budget = new RestartBudget({ now: () => clock });
  budget.start();
  clock = 90000;
  assert.equal(budget.uptimeMs(), 0);
});
