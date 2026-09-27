# Durable runtime

> **The process is disposable; the work is not.**

This document describes Alpha's durability layer: the guarantees it makes about
long-running work, the subsystems that implement them, and — just as
importantly — the ones it deliberately does *not* claim.

The central guarantee is negative. Each of these must never turn into a task
failure:

| Failure | Must not become |
|---|---|
| A process failure | a task failure |
| A UI failure | a task failure |
| An internet outage | a task failure |
| A provider outage | a task failure |
| A Windows restart | a task failure |
| A context overflow | a task failure |
| A subagent failure | a task failure |

Everything below exists to make one of those rows true.

## What Alpha already had

This layer is **additive**. The durable runtime is not a rewrite of Alpha's
execution model; it fills the specific gaps that stopped Alpha from keeping its
promises, and it is built on the machinery that already works:

- **Durable task state** — the `runs` table with leases, CAS transitions, the
  `uq_runs_thread_active` partial unique index, and `RunManager` as the *sole*
  lifecycle owner. A run is an `asyncio.Task` decoupled from the HTTP
  connection, and SSE disconnect already defaults to `continue`.
- **Safe crash recovery** — `app.gateway.run_recovery.SafeRunRecoveryService`
  reconstructs and resumes unfinished sessions at startup. It is fail-closed
  around side effects: a pending tool, MCP, shell, browser, write/delete,
  payment, or unknown node becomes `recovery_confirmation_required` rather than
  a blind replay.
- **Graph checkpoints** — full and delta channel modes, with mode compatibility
  enforced before every state read.
- **An append-only event log** — the `run_events` table with DB-enforced
  per-thread monotonic `seq`.
- **Leased durable queues** — scheduled tasks, MCP tasks, and subagent batches
  all use the same lease/reap pattern.

None of that was replaced. Read [`docs/RUN_RECOVERY.md`](../RUN_RECOVERY.md) for
the existing recovery contract in full.

## The gaps this layer closes

Four things were genuinely missing, and each maps to one subsystem.

### 1. A task could not say "I'm alive but parked"

`threads_meta.status` is a free-text `String(20)` that only ever says
`idle` / `running` / `error`. A task waiting for connectivity was
indistinguishable from a task that failed — which is the internet-outage row of
the table above, failing in the most literal way possible.

`alpha.runtime.sessions` adds the missing vocabulary as a **derivation**, never a
second lifecycle owner. `derive_session_state()` folds signals the existing
owners already hold into one of seventeen named states, classified into three
classes a host actually branches on:

| Class | Meaning | Example states |
|---|---|---|
| `ACTIVE` | executing or imminent; holds worker capacity | `running`, `planning`, `compacting`, `recovering` |
| `WAITING` | alive and durable, parked on an external condition | `waiting_network`, `waiting_provider`, `waiting_permission`, `paused` |
| `TERMINAL` | no further automatic progress | `completed`, `failed`, `cancelled`, `blocked` |

`is_resumable` is the property a recovery pass asks: true for `ACTIVE` and
`WAITING`, false for `TERMINAL`. `paused` is `WAITING`, not `TERMINAL` — a user
pause is a park, not a death.

Two precedence rules are load-bearing and are pinned by tests:

- **A terminal run status outranks every live condition.** A network flap during
  teardown must not resurrect finished work as `waiting_network`.
- **A park outranks an in-flight phase.** A session that cannot reach its
  provider is not `recovering`; reporting it as recovering is a lie about
  progress.

`COMPLETED` still means "the run finished", never "the work was verified".
Acceptance remains an overlay
(`alpha.runtime.runs.verification`).

→ `backend/packages/harness/alpha/runtime/sessions/AGENTS.md`

### 2. Nothing knew whether the machine had internet

There was no probe, no network-state store, and no way to represent an outage. It
surfaced as an opaque provider exception, the run terminalized as `error`, and
the work was lost even though nothing about the *task* had failed.

`alpha.runtime.network` makes connectivity a first-class runtime state, with
four states rather than two:

| State | Means | Permits a network attempt? |
|---|---|---|
| `ONLINE` | every target reachable | yes |
| `DEGRADED` | some targets reachable | **yes** — a partial link is not an outage |
| `OFFLINE` | no target reachable, corroborated | no |
| `UNKNOWN` | never determined, or the probe could not run | **yes** |

The last two rows are the design. `UNKNOWN` being a *real* state that still
permits an attempt is what stops a misconfigured probe from parking every
session on a lie, and stops "not knowing" from being treated as "the link is
down".

**Hysteresis.** A single probe never flips anything — except the very first
one. Hysteresis guards against a *flapping* link, and there is nothing to flap
against before the first measurement, so a Gateway booting on a dead network
reports `offline` immediately rather than `unknown` one poll later.

**Proving a link is down.** `classify_network_error()` separates what broke from
what a caller may act on. `proves_link_down` is true only for DNS failure,
connection refused, and explicit unreachable errors. A `TIMEOUT` is *not* proof:
a saturated link, a cold TLS path, and a slow provider all produce one, and
flipping global state to `OFFLINE` on those would park healthy work. Chain
precedence runs strongest-first, so `RuntimeError from TimeoutError from
gaierror` classifies as `DNS_FAILURE` rather than discarding the only real
evidence in the chain.

**What a probe may do.** TCP connect only — no HTTP request, no TLS handshake,
no payload. A connectivity check runs on a timer for the life of the process
with no user request attached; an HTTP `GET` would both answer a provider-health
question this layer must not answer, and send a request identifying the user to a
third party on a timer.

→ `backend/packages/harness/alpha/runtime/network/AGENTS.md`

### 3. "Might have happened" was a run-level shrug

Alpha refused to blindly replay a side effect, but the signal was
`stop_reason="recovery_confirmation_required"` on a *run*. That cannot say which
external effect is unaccounted for, and it cannot be re-listed or worked off.
"Run 42 might have charged someone" is not actionable; "tool call `call_abc` may
have charged someone" is.

`alpha.runtime.side_effects` adds the per-effect record. The load-bearing rule is
that **a side effect has three honest outcomes** — succeeded, failed, and *we
cannot tell* — and collapsing the third into either of the first two is how a
crash becomes a double charge.

So `UNKNOWN` is a first-class, durable, **enumerable** status. A worker that
dies mid-call is exactly why it exists: the entry is written `PENDING` before
the call, becomes `IN_FLIGHT` with a lease before it, and only a *live* worker
may record the real outcome. An expired lease is therefore proof that the
process which would have known the answer is gone, and `reclaim_expired()`
converts that to `UNKNOWN`. `PENDING` is reclaimed too — dying between the two
writes is exactly as unknown as dying mid-call, and treating it as "nothing ran"
is the optimistic guess the ledger exists to avoid.

`UNKNOWN` has exactly one exit, through `reconcile()`, taking a
`ReconciliationVerdict`. `UNDETERMINED` is a real answer meaning "I looked and
still cannot tell" and it *reopens* the entry; recording it as a failure is
precisely how a duplicate gets created. `UNKNOWN -> COMPLETED` is not in the
transition table at all.

Rows store SHA-256 digests, never arguments or results: these rows are durable,
land in support bundles, and outlive the thread.

→ `backend/packages/harness/alpha/runtime/side_effects/AGENTS.md`

### 4. Nothing bounded how often to restart the backend

The natural supervisor is `while not healthy: start()`. It is a liability: a
backend that cannot start — a bad config, a port already bound, a migration that
will not apply — is restarted forever, burning CPU and making the machine
unusable for the operator who needs to fix it.

`alpha.runtime.supervisor` bounds it. The backoff maths is *reused* from
`alpha.runtime.resilience.RetryPolicy` rather than reimplemented, but the restart
limiter is new, because `RetryPolicy.attempts` and
`resilience.CircuitBreaker` are both *consecutive*-failure counters and neither
can catch the shape that actually kills machines:

> A backend that crashes **once an hour, forever** has one consecutive failure at
> any instant. It sails through a three-attempt ceiling, never trips a
> consecutive-failure breaker, and restarts several times a day indefinitely
> while looking healthy to both.

So the limiter is a **sliding window**: at most `restart_budget` restarts in any
`restart_window_seconds` span. It refills with time, which is what makes "once an
hour forever" stop while "three times in ten seconds" is caught immediately.

Three outcomes — `RESTART`, `SAFE_MODE` (a reduced configuration, tried **once**
per crash episode), `GIVE_UP`. Liveness is not health: a start only succeeds once
`health_check` passes within `health_timeout_seconds`, and a start that never
becomes healthy is recorded as `HEALTH_FAILED` and spends budget like a crash.

→ `backend/packages/harness/alpha/runtime/supervisor/AGENTS.md`

## Planned shutdown

Shutdown is where a durable runtime most easily lies: a sequence of best-effort
steps wrapped in a blanket `except`, ending in "shutdown complete" whether or not
the queue was persisted.

`alpha.runtime.shutdown` runs the eight phases the contract requires — stop
admitting work, drain safe operations, checkpoint, persist
queue/permissions/scheduler, stop workers — with three rules:

- **Order is declared, not implied.** A step may declare prerequisites, and a
  prerequisite that failed — or that nobody registered — *skips* the dependent
  step rather than letting it run against half-persisted state.
- **A partial shutdown is reported as partial.** Every step is exactly one of
  `completed` / `skipped` / `failed` / `timed_out`, and `is_clean` is true only
  when all of them completed. A step that raises is recorded with its reason and
  the drain continues, because one broken store must not strand the steps that
  protect other work.
- **Deadlines are real.** Each step is bounded, the drain is bounded, and the
  emergency path is capped at 5s regardless of configuration so a service
  manager's kill deadline is never overrun.

The Gateway's lifespan drain runs through it, so a partial drain is now logged
in full instead of being indistinguishable from a clean one.

## Configuration

```yaml
network:
  enabled: true                      # no background poll task when false
  poll_interval_seconds: 15.0        # base interval while ONLINE
  offline_after_consecutive: 2       # corroborate before publishing OFFLINE
  online_after_consecutive: 2        # corroborate before publishing ONLINE
  unknown_after_consecutive: 3       # tolerate probe errors on a good link
  backoff_initial_seconds: 5.0
  backoff_max_seconds: 300.0         # a real ceiling; jitter only reduces
  backoff_multiplier: 2.0
  backoff_jitter_ratio: 0.25         # stops a fleet retrying in lockstep
  targets:                           # omit to use the built-in resolvers
    - name: corp
      host: proxy.corp.test
      port: 8443
      timeout_seconds: 2.0
```

`network` is **startup-only**, registered in
`alpha.config.reload_boundary`. The monitor owns a background task, an in-flight
backoff ladder, and a published state other subsystems have already read, so a
mid-flight swap would split the process across two policies. Validation is
fail-closed (`extra="forbid"`), and an empty `targets` list is refused because it
would leave connectivity permanently `UNKNOWN`.

## What is deliberately not claimed

Honesty about the boundary is part of the feature.

- **No cross-process exactly-once.** The in-memory ledger and probe are
  process-local. Only the DB-enforced unique and partial-unique *indexes* give
  cross-process exclusion, and that is the pattern to reuse rather than
  reinvent.
- **A supervisor crash costs a restart delay, not a task** — *provided* the
  backend was allowed to recover. The durable records are what make that true.
- **A `reconciled` side effect is not a `completed` one.** Status records only
  that someone looked and reached a conclusion; the verdict records which.
- **`COMPLETED` is not "verified".** Acceptance is an overlay and is reported
  separately.
- **Filesystem state is not a side effect.** Alpha cannot automatically undo
  processes, databases, or remote calls; the side-effect ledger *records* them
  for reconciliation, it does not roll them back.
- **`UNKNOWN` connectivity is not `OFFLINE`**, and a `DEGRADED` link is not an
  outage. A probe that cannot run is a broken probe.

## Where things live

| Concern | Module | Tests |
|---|---|---|
| Session lifecycle vocabulary | `alpha.runtime.sessions` | `tests/test_durable_session_state_machine.py` |
| Connectivity state, probe, monitor | `alpha.runtime.network` | `tests/test_network_resilience.py` |
| Side-effect ledger and reconciliation | `alpha.runtime.side_effects` | `tests/test_side_effect_ledger.py` |
| Process supervision and crash-loop policy | `alpha.runtime.supervisor` | `tests/test_process_supervisor.py` |
| Ordered, honestly-reported shutdown | `alpha.runtime.shutdown` | `tests/test_planned_shutdown.py` |
| Existing safe recovery contract | `app.gateway.run_recovery` | `tests/test_safe_run_recovery.py` |
| `config.yaml -> network` | `alpha.config.network_resilience_config` | `tests/test_network_resilience.py` |
| `NETWORK_UNAVAILABLE` error code | `alpha.errors.registry` | `tests/test_error_codes.py` |

Each subsystem's own `AGENTS.md` next to the code is the normative contract;
this page is the map.

## Not yet implemented

Stated plainly so nobody reads a guarantee into this page that the code does not
make:

- **No durable `network_waits` table.** A session's parked state is currently
  *derived* from live signals plus the existing run ledger; parking a session
  across a restart reuses `SafeRunRecoveryService` rather than a new queue.
  `alpha.runtime.sessions` supplies the vocabulary and the monitor supplies the
  fact; the durable registry that would join them is future work.
- **The side-effect ledger has no SQL repository yet.** The protocol and the
  reference implementation are complete and tested, and the semantics (lease,
  reclaim, reconciliation) are the contract a durable backend must satisfy — but
  entries are process-local today.
- **The supervisor is not wired into the Windows launcher.** It is a complete,
  tested library with a production-referenced contract; `start.ps1` still owns
  process startup.
- **`AWAITING` subagent reconciliation** is served by the existing
  `recovery_confirmation_required` stop reason, not by a dedicated per-subagent
  ledger.
