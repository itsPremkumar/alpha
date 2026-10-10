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

**Who keeps trying.** The poll loop runs for the life of the process and does
**not** stop while the link is down — that is the whole reason a host which boots
offline ever recovers. It only slows down: the interval grows by
`backoff_multiplier` toward `backoff_max_seconds` (5s → 300s by default, with
jitter only ever *reducing* it). So a machine that lost its link re-probes
forever at a bounded rate. There is deliberately **no attempt ceiling** here: the
bound on *work* parked on an outage belongs to the registry's `max_attempts`,
and a probe loop that gave up would turn a ten-minute outage into permanent
silence — the outage equivalent of the restart loop the supervisor refuses to
write.

**One measurement at a time.** `check_once()` is the whole transition — probe,
fold, hysteresis, backoff, publish — so it is serialized behind a lock, and
`recheck()` runs that same transition through that same lock. Without the lock a
manual retry landing on the same tick as a poll would count one corroborating
observation twice and collapse `online_after_consecutive` to a single sample,
which is exactly the gate that stops a flapping link from parking and un-parking
the fleet.

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

## Making the parks durable

Two tables turn the measurement layer into something that survives a restart.

### Durable network waits

The monitor knows the link is down and the session vocabulary can say "alive but
parked", but between them a parked session was re-derived from live signals on
every boot. That is fine while the process lives and useless across a restart: a
task that parked on a dead network at 02:00 and whose machine rebooted at 02:01 had
no record that it was ever waiting.

`network_waits` is that record. A *network wait* is one parked session, and the
row records **where the work got to** and nothing more — park and resume remain
the decision of `SafeRunRecoveryService` through the normal `start_run` path.

Four properties are load-bearing:

- **The resume is refused while the link is still down.** A wait exists
  *because* the link was gone, so attempting now would spend the attempt budget
  on a certainty. `UNKNOWN` and `DEGRADED` still permit an attempt.
- **The backoff is durable.** `next_attempt_at` is written when a resume
  *fails*, so a reboot cannot turn a five-minute backoff into a hot retry loop.
- **Attempts are bounded — and the bound is optional.** `max_attempts` ends the
  wait as `gave_up` with a reason. **The default is `0`, meaning unbounded**: a
  wait is a *parked task*, not a retry loop, so a session that did nothing wrong
  must not be abandoned for surviving an outage that outlasted a constant. The
  two bounds that belong elsewhere are untouched: `network.backoff_*` caps how
  loudly the link is re-checked (and never stops polling), and
  `run_ownership.max_resume_attempts` — which also accepts `0` for unbounded —
  bounds a continuation that keeps dying. Set `network_wait.max_attempts` to a
  positive number when an operator is expected to intervene. *When the Gateway
  installs no launcher, none of the registry's own bounds engage at all*, so in
  the wired deployment the table is a **record** (which sessions are parked,
  which were refused a resume); the bounding there is
  `SafeRunRecoveryService`'s own `max_resume_attempts`. A row can therefore sit
  in `waiting` indefinitely; that is visible and enumerable by design, not a
  silent cap.
- **A declined checkpoint is settled, not retried.** If the recovery owner
  refuses — most likely a side-effect-unsafe checkpoint — that checkpoint's safety
  will not change, so retrying is a loop with extra steps.

One open wait per thread is enforced twice (a partial unique index plus a read of
the existing row), `claim_due` is a conditional `UPDATE` so two gateway instances
cannot both take a row, and `max_claims_per_pass` stops a backlog stampeding the
provider the instant the link returns.

### Telling the story back: the outage timeline

A park that survives a restart but cannot be read back is not yet a promise a user
can see. Two columns and one query close that, and the second is the load-bearing
one.

**`terminal_at` is not `updated_at`.** `release()` moves `updated_at` every time a
failed resume attempt writes its next backoff, so reading the connection time off
`updated_at` would report a *scheduled retry* as the moment the link came back. An
open row keeps `terminal_at` `NULL` — "still waiting" is a state, not a zero
timestamp — and the migration that adds it
(`0029_network_waits_terminal_at`) is deliberately **un-backfilled**: a row settled
before the revision has no measured connection time, and inventing one from
`updated_at` would be precisely the wrong number the column prevents.

**`list_for_thread` includes settled rows.** `list_open` answers "what is
unfinished right now", which is the wrong question for a thread's history: an
outage that ended an hour ago is still part of that conversation. A UI reading only
open rows would show a thread as never having been parked the moment it resumed,
erasing the exact event the user came to read.

`GET /api/threads/{id}/network-waits` projects both, beside the live connectivity
block, and reports `bounded` / `max_attempts` so a client can say whether this
deployment will ever give up rather than hinting at a deadline nobody declared. It
answers `reported: false` — not an empty list — when the process records no
per-thread timeline at all (a `memory` database backend has nowhere durable to
record a park), and `503`, never an empty list, when the store itself cannot be
read.

### In the chat, where the work happened

`NetworkWaitBubbles` renders that timeline inline in the transcript: one bubble per
outage with **both** timestamps and the measured wait between them, a live
client-observed counter while a wait is still open, and the server's own reason
when one was measured. It answers the one question a blank transcript cannot: *is
this working or waiting?* — because a parked session is `waiting_network`, not
`failed`, and a guarantee nobody can see is not a guarantee.

The mount is deliberately **not** gated on a run being in flight: the run that
parked is already terminal from the runtime's point of view, so a loading-only
mount would hide the bubble during exactly the wait the user most needs to see it.
Because the row is durable, a chat reopened tomorrow still shows the outage it
survived.

**The Gateway owns the lifecycle.** `langgraph_runtime()` builds the monitor from
`config.yaml -> network`, runs the first probe *before* starting the poll loop (so
a Gateway booting on a dead network reports `offline` rather than `unknown`), and
stops both services at `ADMISSION_CLOSED` during the drain — before the run drain,
because a probe in flight during shutdown would publish into a tearing-down
process. Startup also reclaims waits whose claim lease outlived the previous
process, so a crashed Gateway cannot strand its parked sessions. A `memory`
database backend gets the monitor but not the registry, and says so in the log.

A run that dies from a *definite* link failure is parked from the worker's
terminal-exception handler, and its stop reason `network_waiting` is in
`RECOVERABLE_RUN_STOP_REASONS` — which is the mechanism by which
`SafeRunRecoveryService` keeps it alive. A **timeout does not park**: it proves
nothing about the link, and parking on one would park healthy work.

→ `backend/packages/harness/alpha/persistence/network_waits/AGENTS.md`

### The side-effect ledger in SQL

`tool_side_effects` makes `UNKNOWN` durable and *enumerable*. Every state change
is an `UPDATE ... WHERE <key> AND status = <expected>`, so the two properties that
matter across processes hold:

- A reaper that moved a row to `UNKNOWN` wins, and a late worker's `complete` is
  refused with `SideEffectTransitionLost` rather than overwriting an unknown with
  a guess.
- Two reconcilers cannot both settle an entry.

That is the whole point: **the process that would have known the answer is the one
that died**, so a later worker has no standing to decide.

**All of the above describes what the schema makes possible, not what a running
Gateway does.** No production module constructs `SqlSideEffectLedger`, so in a
real deployment no effect is ever announced, the table is empty, and the
properties this section describes are exercised only by tests. See *Not yet
implemented* below.

→ `backend/packages/harness/alpha/persistence/side_effects/AGENTS.md`

## Seeing it, and re-probing it

A measurement nobody can read is a measurement that did not happen. The reading
is exposed two ways, both projected from the same place so they cannot drift:

| Route | Answers |
|---|---|
| `GET /api/ops/network` | the four-state link, the **measured round-trip per endpoint**, whether a network operation may be attempted now, how old the reading is, and the automatic re-probe schedule |
| `POST /api/ops/network/recheck` | force one immediate measurement — the manual "Retry" |
| `GET /api/ops/runtime` | the same connectivity block, bundled with the previous process's drain outcome |

Three honesty rules are load-bearing on that surface, because each has a
plausible-looking wrong answer:

- **`latency_ms` is `null`, never `0`,** when no endpoint answered. `0` is the
  fastest possible link, so it would draw the worst possible result as the best
  one. The mean is taken over the *reachable* targets only — averaging a live
  endpoint together with a timed-out one describes neither.
- **`allows_network_attempt` is read from the monitor's own decision**, not
  re-derived from the state string in the Gateway, so `unknown` keeps permitting
  an attempt instead of being quietly rounded into an outage.
- **The payload carries `observed_age_seconds`, not the observation's
  `observed_at`.** That stamp comes from `SystemClock`, which is
  `time.monotonic`, so publishing it as wall-clock time would be a confident
  wrong number — and clamping the subtraction would render the wrongness as
  "measured just now".

A recheck goes through `NetworkMonitor.recheck()` — the same serialized
transition the poll loop runs — rather than a bespoke path beside it, and a
recheck that hysteresis declines to publish reports how many confirmations are
still outstanding instead of silently doing nothing.

The workspace header renders this in its "Backend connection" cluster: the
measured latency, the state, the backend's own retry sentence, and a **Retry**
button that is offered only when a recheck could change the answer.

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
network:                             # the MEASUREMENT policy
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

network_wait:                        # the PATIENCE policy
  enabled: true
  max_attempts: 0                    # 0 = UNBOUNDED (the default). A parked task
                                     # waits until the link returns, however long
                                     # that takes; a positive number surrenders it
                                     # as `gave_up` with its reason.
  backoff_initial_seconds: 15.0
  backoff_max_seconds: 900.0
  backoff_multiplier: 2.0
  poll_interval_seconds: 30.0        # how often due waits are swept
  claim_lease_seconds: 30.0
  max_claims_per_pass: 5             # a backlog must not stampede the provider
```

Both sections are **startup-only**, registered in `alpha.config.reload_boundary`
(`network` and `network_wait`). The monitor owns a background task, an in-flight
backoff ladder, and a published state other subsystems have already read; the
registry owns a recovery pass and the policy a row is admitted under, so a
mid-flight swap would split the process across two answers to "how long do we
wait?". Validation is fail-closed (`extra="forbid"`) on both, and an empty
`targets` list is refused because it would leave connectivity permanently
`UNKNOWN`.

`run_ownership.max_resume_attempts` also accepts `0` for unbounded, and keeps its
own default of `3`: that bound is about a *continuation that keeps dying*, which
is a genuinely different question from "how long will you wait for the internet".

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
| Operator surface + manual re-probe | `app.gateway.routers.ops`, `app.gateway.ops_runtime` | `tests/test_ops_network_router.py` |
| Durable parked sessions | `alpha.runtime.network.wait_registry`, `alpha.persistence.network_waits` | `tests/test_network_wait_registry.py`, `tests/test_network_wiring.py` |
| How long a park waits (`network_wait`) | `alpha.config.network_wait_config`, `NetworkWaitPolicy` | `tests/test_network_wait_timeline.py` |
| Per-thread outage timeline + route | `app.gateway.routers.thread_runs`, `NetworkWaitRepository.list_for_thread` | `tests/test_network_wait_timeline.py` |
| The in-chat network bubble | `frontend/src/lib/network-wait.ts`, `network-wait-view.ts` | `frontend/src/lib/network-wait.test.mjs` |
| Side-effect ledger (semantics) | `alpha.runtime.side_effects` | `tests/test_side_effect_ledger.py` |
| Side-effect ledger (durable) | `alpha.persistence.side_effects` | `tests/test_side_effect_ledger_sql.py` |
| Process supervision and crash-loop policy | `alpha.runtime.supervisor` | `tests/test_process_supervisor.py` |
| Ordered, honestly-reported shutdown | `alpha.runtime.shutdown` | `tests/test_planned_shutdown.py` |
| Existing safe recovery contract | `app.gateway.run_recovery` | `tests/test_safe_run_recovery.py` |
| `config.yaml -> network` | `alpha.config.network_resilience_config` | `tests/test_network_resilience.py` |
| `config.yaml -> network_wait` | `alpha.config.network_wait_config` | `tests/test_network_wait_timeline.py` |
| `NETWORK_UNAVAILABLE` error code | `alpha.errors.registry` | `tests/test_error_codes.py` |
| Durable mission memory (the re-read that stops drift) | `alpha.runtime.missions` | `tests/test_mission_memory.py`, `tests/test_mission_memory_wiring.py` |

Each subsystem's own `AGENTS.md` next to the code is the normative contract;
this page is the map.

operator), which records a measured result here. This never infers an outcome
from a model's summary. The durable-runtime loops and APEX own *scheduling*;
this package is the memory those loops dispatch *into*, and its `mission`
watchdog loop (registered in `AutonomySupervisor`) enforces the brake even when
no agent session is alive — it *tightens only* (parks stuck/blocked missions) and
never dispatches, so it is not a second executor. Contract: the module's own
`runtime/missions/AGENTS.md`.

**The cognition of durability is separate from the durability itself.** Everything
above makes the *process* survive an outage, crash or restart. What none of it
supplies is the objective once it scrolls out of the context window: a run can be
durable and still drift. `alpha.runtime.missions` is that missing half — a
per-`(owner, thread)` spec/plan/status/scratchpad stack re-read before every model
turn, with milestones that only advance on a *measured* result (stop-and-fix). It is
additive and owns no lifecycle, dispatch or recovery; it is the memory the loops
below dispatch *into*. Its own `AGENTS.md`:
`backend/packages/harness/alpha/runtime/missions/AGENTS.md`.

## Verifying it actually works

Unit tests prove the parts; they do not prove the wiring. Two things in this repo
check the difference, and both were worth running.

**`backend/scripts/realtime_gateway_check.py`** boots a real Gateway as a real OS
process against a real `config.yaml` and a real SQLite database, serves real HTTP
requests, then **`taskkill /F`**s it mid-flight and starts it again:

```bash
cd backend && uv run python scripts/realtime_gateway_check.py
```

It asserts `/health` and `/health/ready` return 200, that a parked session is
written through the real repository, that 26 tables plus the parked row survive an
abrupt kill, and that the process comes back healthy with the state intact.

**`tests/test_durable_runtime_realtime.py`** (6 tests) uses real sockets rather
than a scripted probe. The offline half connects to a black-holed address
(`192.0.2.1`, RFC 5737 TEST-NET-1) and waits for a real connect timeout; the
online half connects to a loopback listener the test itself opens. It also spawns
a real child process, kills it from outside, and confirms the side-effect ledger
converts the abandoned effect to `UNKNOWN`.

Measured on this machine:

| Measurement | Result |
|---|---|
| Real offline probe (TEST-NET-1) | 1005 ms, 989 ms → `offline` |
| Real online probe (loopback) | 6.7 ms, 5.8 ms → `online` |
| Real lifespan boot | monitor polled 3×, state `offline` |
| Real child killed | effect `call_real` → `unknown` |
| Real crash loop | 3 restarts in 5.0 s → `give_up` |
| Real shutdown with a broken store | `queue_persisted: failed`, `scheduler_persisted: skipped` |

The cost difference between the two probes is the evidence that the monitor
measured something rather than being told an answer.

### Two real bugs this found

Both were found by the real-time run and neither was visible to a unit test,
which is the argument for running it.

**1. A host that booted offline would never have recovered.** The wiring read
`if monitor.state is not NetworkState.OFFLINE: monitor.start()`, reasoning that a
host which had just proved the link was down should not poll it. That is exactly
backwards: the poll loop is the *only* thing that can ever notice the link coming
back, so such a Gateway would have sat there forever and no parked session would
ever have resumed. The loop now starts unconditionally; politeness about probing
a dead link is the backoff ladder's job, and that ladder is already bounded.

**2. Registering two `ADMISSION_CLOSED` shutdown steps silently dropped the first.**
`PlannedShutdown.register` replaces an existing step for the same phase — a
deliberate rule, since two writers to the same state is a race. But the Gateway
has two components that both want admission closed (the safe-recovery service and
the connectivity services), and registering them separately meant the network stop
was replaced and **never ran**: the services stayed up and the harness accessor
kept pointing at a dead one. Everything admission-close needs is now composed into
a single step, and `tests/test_network_wiring.py` counts the registrations per
phase so the mistake cannot recur.

## Not yet implemented

Stated plainly so nobody reads a guarantee into this page that the code does not
make:

- **The Python `ProcessSupervisor` is not wired into the Windows launcher.**
  Windows startup and automatic restart are handled by the separate four-layer
  chain in `start.ps1` and `recovery/`; it does not use `ProcessSupervisor`'s
  restart budget or diagnostics. The launcher/watchdog paths have focused
  contract tests, but proving the installed chain still requires a deliberate
  reboot/crash recovery drill.
- **One effect family writes the side-effect ledger; the rest still do not.**
  This gap is easy to state wrongly in *either* direction, so here it is with
  its evidence. On a SQL backend `app/gateway/deps.py` constructs
  `SqlSideEffectLedger` (migration `0027_side_effect_ledger`) alongside a
  `SideEffectReclaimer`, and exactly one production site calls
  `announce_effect`: the durable MCP-task *submit* in
  `app/mcp_tasks/service.py`. `tests/test_side_effect_unrecorded_ratchet.py`
  pins that count to one and keeps every remaining family named in
  `UNRECORDED_IRREVERSIBLE_EFFECTS` (shell, ordinary MCP calls, workspace file
  writes, git publish, host computer-use, IM outbound, …), so a second wired
  site cannot appear without editing the ratchet and a site unwired from the
  list cannot quietly stay unwired. A `database.backend: memory` deployment
  installs no ledger at all. **Cross-process exactly-once therefore exists only
  for the one wired family**; everything else about leases, reclaim and
  reconciliation remains a *proven contract* rather than an observed behaviour,
  and an empty journal means "nothing announced", never "nothing went wrong".
- **The queue has an API and a UI, and neither writes an effect.**
  `GET /api/side-effects/summary`, `GET /api/side-effects`,
  `GET /api/side-effects/{tool_call_id}` and
  `POST /api/side-effects/{tool_call_id}/reconcile`
  (`app/gateway/routers/side_effects.py`) are the route; the `effects`
  workspace view (`frontend/src/lib/side-effects.ts` +
  `components/sections/EffectsSection.tsx`) is the human surface. Both read and
  reconcile only: neither announces an effect, neither cancels, resumes or
  replays a run, and both carry SHA-256 digests rather than payloads.
  Reconciliation stays a caller decision and a verdict is a record of what
  *was* — an operator recording `confirmed_success` does not tell the runtime
  anything about a run.
- **No `replay(session_id)` over the run-event log.** The workflow/DWE log is
  replayable (`alpha.orchestrator.replay.replay_run`); the thread `run_events`
  feed is a message feed and audit trace, and nothing folds it back into a
  session state. `alpha.runtime.sessions` derives that state from live signals
  instead.
- **The parked-session registry has no per-thread resume API.** The
  continuation path exists and stays fail-closed: `SafeRunRecoveryService`'s
  scan is stop-reason driven, and `network_waiting` is in
  `RECOVERABLE_RUN_STOP_REASONS`, so a network-parked run is picked up by the
  service that already owns safe continuation. The registry records and bounds
  attempts; it does not create a second continuation authority.
- **A `memory` database backend gets no parked-session registry.** There is
  nowhere durable to record a park, so only the connectivity measurement runs and
  the degradation is logged rather than silently absorbed.
- **`AWAITING` subagent reconciliation** is served by the existing
  `recovery_confirmation_required` stop reason, not by a dedicated per-subagent
  ledger.
- **Filesystem snapshots for undo/redo are not part of this layer.** Alpha has
  `workspace_changes` recording and Git-native development; the
  snapshot/undo/compare surface the contract describes does not exist, and a
  filesystem snapshot could not undo a remote call in any case — that is what the
  side-effect ledger's `UNKNOWN` state is for.
