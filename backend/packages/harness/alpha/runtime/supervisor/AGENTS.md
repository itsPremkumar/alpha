"""Process supervision with a bounded, non-looping restart policy.

The problem
-----------
The naive supervisor is ``while not healthy: start()`` with a sleep between
attempts. It is a liability: a backend that cannot start — a bad config, a port
already bound, a migration that will not apply — is restarted forever, burning
CPU, writing the same error line, and making the machine unusable for the
operator who needs to fix it. The durable-runtime contract names this directly:
*never create a blind infinite restart loop.*

=========================  ===================================================
:mod:`.policy`            `SupervisorPolicy`, `RestartLedger`,
                           `SupervisorReason`, `RestartAction`,
                           `RestartDecision` — pure policy, no processes.
:mod:`.supervisor`        `ProcessSupervisor` — start, watch, health-check,
                           decide, collect diagnostics.
=========================  ===================================================

## What is reused, and what had to be new

**Reused:** the backoff maths. `SupervisorPolicy.backoff` *is* an
`alpha.runtime.resilience.RetryPolicy`, so this package adds no sixth backoff
implementation to a package that already warns about having several. Jitter is
injected, so a test pins the exact schedule.

**New:** the restart limiter. `RetryPolicy.attempts` and
`resilience.CircuitBreaker` are both *consecutive*-failure counters, and neither
can catch the shape that actually kills machines. A backend that crashes **once
an hour, forever** has one consecutive failure at any instant — it would sail
through a three-attempt ceiling and never trip a consecutive-failure breaker,
while restarting several times a day indefinitely and looking healthy to both.
That is a real crash loop, so the limiter here is a **sliding window**: at most
`restart_budget` restarts in any `restart_window_seconds` span. It refills with
time, which is what makes "once an hour forever" stop while "three times in ten
seconds" is caught immediately.

## Three outcomes, not two

- `RESTART` — within budget; wait the backoff delay and go.
- `SAFE_MODE` — the budget is spent, but a reduced configuration might still
  boot. Tried **once** per crash episode, then `GIVE_UP`.
- `GIVE_UP` — stop and report. A backend that stays down and says why is a better
  outcome than a machine nobody can type on.

Safe mode is one attempt, not a second ladder rung. A ladder implies it might
work after enough tries, and for a *startup* failure there is nothing between
"this configuration boots" and "it does not". A `SPAWN_FAILED` ending skips safe
mode entirely: the process never ran, so there is no reason to expect the next
attempt to behave differently.

## A requested stop is not a failure, and it is not free to fake one

`stop()` and a supervised shutdown both end the child by terminating it, and the
OS reports that as a nonzero exit — on Windows, reliably. Classifying by exit code
alone therefore recorded every deliberate stop as `CRASHED`, which (a) told an
on-call operator the backend had crashed when they had asked it to stop, and
(b) charged the crash-loop restart budget. The budget exists to stop a backend
that *cannot start*; a deploy, a config reload, or a supervisor restart spends
nothing but would have consumed one, so after a handful of ordinary restarts the
policy walked into `SAFE_MODE` and then `GIVE_UP` — a supervisor refusing to
start the process it exists to keep up, triggered by nothing worse than being
asked to restart.

So `SupervisorReason.STOPPED` is a distinct reason. It is recorded in the history
(how an attempt ended is exactly what that history is for), it does not count
against the budget, and it ends the episode. It deliberately does not return
`RESTART`, which would name a next step the supervisor is not going to take. A
genuine `CRASHED` is untouched, and there is a test pinning that too. Tests:
`tests/test_supervisor_stop_is_not_a_crash.py`, which proves the child was alive
before the stop (a heartbeat file on disk, rather than an inference from a
duration) and runs six clean cycles against a budget of three.

## Uptime ends the episode

A child that stayed up at least `healthy_run_seconds` did not crash-loop, so the
episode ends and the budget is returned. Without that, a service that runs well
for a week between rare crashes would eventually exhaust a window it had already
served its purpose in.

## Liveness is not health

A process that is alive but not serving is *worse* than one that exited: it
accepts connections and fails them, and a liveness-only supervisor reports it as
healthy. So a start is only successful once `health_check` passes within
`health_timeout_seconds`. A start that never becomes healthy is recorded as
`HEALTH_FAILED`, which consumes budget exactly like a crash — to the rest of
Alpha it is the same event.

## The child is not the source of truth

A supervised process is disposable; its work is not, and it is not what this
supervisor protects. The backend persists every task durably (LangGraph
checkpoints, the `runs` table, the `run_events` feed), and
`app.gateway.run_recovery.SafeRunRecoveryService` reconstructs and resumes
unfinished sessions at startup. A supervisor crash therefore costs a restart
delay, not a task — *provided* the backend was allowed to recover. This is also
why the backend must never depend on the desktop UI being open: the UI is a
viewer, and the supervisor's job is to keep the runtime up independently of it.

## Diagnostics answer six questions

`ProcessSupervisor.diagnostics()` returns a structured `SupervisorReport` —
what failed, why, where, what Alpha was doing, what it tried, what happens next
— derived from recorded history, so it is available *after* the supervisor has
given up and not only while it is running. It is structured rather than prose so
a log line, an HTTP body, and a support bundle cannot drift apart.

**Where things live**:
- `runtime/supervisor/policy.py` — `SupervisorPolicy`, `SupervisorReason`,
  `RestartAction`, `RestartDecision`, `RestartLedger`
- `runtime/supervisor/supervisor.py` — `ProcessSupervisor`, `SupervisorStatus`,
  `SupervisorReport`
- Tests: `tests/test_process_supervisor.py`
