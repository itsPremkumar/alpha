### Network-aware execution (`runtime/network/`)

**The gap this closes.** Alpha had *no* internet-connectivity awareness. There
was no probe, no network-state store, and no way to say "this task is alive but
parked because the link is down". An outage surfaced as an opaque provider
exception, the run terminalized as `error`, and the work was lost even though
nothing about the **task** had failed. That is exactly the failure the
durable-runtime contract forbids.

**What lives here is measurement and decision, nothing else.** No module in this
package cancels a run, resumes a run, or writes durable state. Parking and
resuming a session is `alpha.runtime.sessions` (the lifecycle vocabulary) plus
`app.gateway.run_recovery.SafeRunRecoveryService` (the only safe-continuation
owner). This package supplies the fact they need and the honest reading of it.

| Module | Role |
|---|---|
| `states.py` | The four-state vocabulary and what each state permits |
| `errors.py` | Transport failure → a claim about the *link*, never about the work |
| `probe.py` | Bounded TCP reachability. No payload, no user data |
| `monitor.py` | Poll, fold, apply hysteresis, back off, publish |

## The four rules that matter

**1. `UNKNOWN` is a real state and is never rounded to `OFFLINE`.** A probe that
cannot run is a *broken probe*. Announcing an outage because the probe is
misconfigured would park every session on a lie, so a probe that raises, or
returns nothing, reports `UNKNOWN` with the failure counted. It reports that no
matter how many times it repeats.

**2. `UNKNOWN` still allows a network attempt.** `NetworkState.allows_network_attempt`
is true for `ONLINE`, `DEGRADED` **and** `UNKNOWN`. Not knowing is not the same
as knowing the link is down, and the ordinary provider retry path handles a
failure better than a global state flip does. Only a *confirmed* `OFFLINE`
refuses an attempt up front.

**3. `DEGRADED` never parks a session.** Partial connectivity — some targets
reachable, some not — is a provider-health question, not an outage. It publishes
on the first observation precisely because it is not a stop-the-world decision.

**4. A single probe never flips anything — except the first one.** Hysteresis
exists to stop a *flapping* link from parking and un-parking the fleet, and
there is nothing to flap against before the first measurement. So
`offline_after_consecutive` (default 2) and `online_after_consecutive` (default
2) corroborate every move *after* the first, while a cold start publishes its
first reading at once — a Gateway that boots on a dead network should say
`offline` now, not `unknown` one poll later. `unknown_after_consecutive`
(default 3) is corroborated the same way, so a single transient probe error
cannot report a connectivity regression on a link measured healthy moments ago.

## Proving a link is down

`errors.py` classifies a failure on two axes: what broke
(`NetworkFailureKind`) and — the only one a caller may act on —
`proves_link_down`. A `TIMEOUT` is **not** proof: a saturated link, a cold TLS
path, and a slow provider all produce one. Only DNS failure, connection refused,
and explicit unreachable errors count.

**Precedence in the cause chain runs strongest-first, not outermost-first.** A
chain like `RuntimeError from TimeoutError from gaierror` classifies as
`DNS_FAILURE`. Reading it outermost-first would report a timeout, conclude
nothing, and throw away the one piece of real evidence in the chain. A timeout
that is all there is stays a timeout, and stays non-definitive. The walk is
cycle-safe and reports a short mechanism-only `detail` — never the raw exception
message, which can carry a URL with a query token.

## What a probe may do

A connectivity check runs on a timer for the life of the process with no user
request attached, so it is a poor place to be careless:

- **TCP connect only.** No HTTP request, no TLS handshake, no payload. A socket
  to `host:443` proves a route exists and costs one syscall pair. An HTTP `GET`
  would additionally prove a *server* is healthy — a provider-health question
  this layer must not answer — and would send a request identifying the user to
  a third party on a timer.
- **Only operator-declared endpoints.** Nothing is resolved from user data; no
  thread id, run id, prompt, or hostname reaches a probe. Defaults are two
  independent public resolvers, so one provider being down yields `DEGRADED`
  rather than a false `OFFLINE`.
- **Every connect is bounded**, per target and per probe, so a black-holed route
  cannot hold the poll loop.
- **No caching across calls.** A stale "it was up" is worse than a fresh
  measurement for a liveness signal.

## Backoff, and why the drawn value is stored

While connectivity is not `ONLINE` the poll interval grows by
`backoff_multiplier` toward `backoff_max_seconds`, and shrinks to
`poll_interval_seconds` on a confirmed recovery. The schedule is **use-then-grow**
— the first offline poll still uses `backoff_initial_seconds` rather than
skipping a rung — and jitter is a proportional *reduction* applied after the
ceiling, so `backoff_max_seconds` is a real ceiling.

The drawn, jittered value is **stored** in `_next_poll_seconds` rather than
handed to the loop and forgotten. `wait_decision().next_poll_seconds` therefore
reports the exact value the loop will wait, instead of an un-jittered ideal
nobody sleeps on. Jitter is injectable, which is the whole reason the decision
sits behind `alpha.runtime.resilience.clock` rather than inline.

## One measurement at a time, and the operator's "retry now"

`check_once()` is the **entire** state transition: probe, fold, apply
hysteresis, advance the backoff ladder, publish. It is therefore serialized
behind `_probe_lock`, and `recheck()` is the operator-facing entry point that
runs the *same* transition through that same lock.

Without the lock a `recheck()` landing on the same tick as a poll interleaves
those steps: two probes in flight, corroboration counted twice, and a state
published that the ladder has not seen twice. That is not a cosmetic bug — it
collapses `online_after_consecutive` to a single sample, which is precisely the
gate that stops a flapping link from parking and un-parking the fleet. Both
readings still happen; they are serialized, not dropped.

`recheck()` deliberately does **not** bypass hysteresis and does **not** restart
or reschedule the poll loop. It adds one reading to the same history every other
reading went into. A caller that has to explain a refused publish to a human
reads `pending_confirmations` for how many confirmations are outstanding —
`NetworkObservation` cannot carry it, because it reports the branch the probe
took (which is `0` whenever the probe itself ran).

`observation_age_seconds()` exists for the same reason. `observed_at` is stamped
from the injected clock, and the production `SystemClock` is `time.monotonic`, so
no consumer outside this package can turn it into an age without mixing two
timelines. Wall time minus a monotonic reading is negative on any host; clamping
it to `0` would render the wrongness as "measured just now", which is the most
confident lie available. **Publish the age, never the stamp.**

## Who keeps trying, and why there is no attempt ceiling

The poll loop runs for the life of the process and does **not** stop while the
link is down. It only slows: `backoff_initial_seconds` → `backoff_max_seconds`
(default 5s → 300s, jitter only ever reducing it). So a machine that lost its
link re-probes forever at a bounded rate, and the only thing that can notice the
link returning is that loop.

There is deliberately **no attempt ceiling here**, and the durable registry that
holds *parked work* now matches it.

## A parked session waits indefinitely by default

`NetworkWaitPolicy.max_attempts` defaults to `UNBOUNDED_ATTEMPTS` (`0`), and the
`gave_up` branch is skipped entirely under that policy.

This is a deliberate reversal of the instinct that everything needs a ceiling. A
**wait is a parked task, not a retry loop**: the task did nothing wrong and the
internet did. The two bounds that genuinely belong elsewhere are still where they
have always been:

- `network.backoff_*` caps how loudly Alpha re-checks the link (300s by default)
  and **never stops polling**, so an unbounded wait cannot mean an unbounded
  request rate;
- `run_ownership.max_resume_attempts` bounds how many times a *continuation that
  keeps dying* is relaunched — and `0` is now accepted there too, meaning
  unbounded, for the same reason.

Charging the *wait* against a fixed count is what turns "the internet was gone for
an hour" into "gave up after 24 tries": a session abandoned for surviving an
outage that outlasted a constant. An operator who genuinely wants a ceiling sets
`network_wait.max_attempts` to a positive number, and exhaustion is then reported
as `gave_up` **with its reason** — never a silent drop.

`UNBOUNDED_ATTEMPTS` is `0` rather than a large number deliberately: a wait that
must not exhaust has to be *unrepresentable as a count*, or "unlimited = 100000"
has only moved the cliff. A negative budget is refused outright.

**A declined checkpoint is still settled immediately, bounded or not.** That
refusal will not change on retry, so retrying it is a loop with extra steps.

## The per-thread outage timeline (`network_waits.terminal_at`, `list_for_thread`)

The park was durable but *unreadable*: a UI could learn that *something* was
parked, never when this thread's link died or when it came back. Two additions
close that, and the second is the load-bearing one.

**`terminal_at` is not `updated_at`.** `release()` moves `updated_at` every time
a failed resume attempt writes its next backoff, so reading the recovery time off
it would report a *scheduled retry* as the moment the link came back. An open row
keeps `terminal_at` `NULL`, and "still waiting" is therefore a state, not a zero
timestamp. Migration `0029_network_waits_terminal_at`; purely additive, no
backfill — a row settled before the revision has no measured connection time, and
inventing one from `updated_at` would be the wrong number the column exists to
prevent.

**`list_for_thread` includes settled rows on purpose.** `list_open` answers "what
is unfinished right now", which is the wrong question for a thread's history: an
outage that ended an hour ago is still part of that conversation's story. A UI
reading only open rows would show a thread as never having been parked the moment
it resumed, erasing exactly the event the user is asking about. The method is
owner-scoped by the *route*, not here — the repository is the harness layer.

`GET /api/threads/{thread_id}/network-waits` projects both, beside the live
connectivity block, and reports `bounded` / `max_attempts` so a client can say
whether this deployment will ever give up rather than hinting at a deadline
nobody declared. It answers `reported: false` (not an empty list) when the
process records no per-thread timeline at all, and `503` — never an empty list —
when the store itself cannot be read. The human surface is
`frontend/src/lib/network-wait.ts` + `components/NetworkWaitBubbles.tsx`.

## Testability

`check_once()` performs exactly one probe, folds it, and returns an immutable
`NetworkObservation`; it never sleeps and never raises. `run()` is a thin loop
over it. Every decision is therefore testable by calling `check_once()` a fixed
number of times with `ScriptedProbe` and `ManualClock` — no real time passes, and
`ScriptedProbe.calls` makes "the monitor did not re-probe while offline"
assertable. `wait_decision()` is the single question a run/thread owner asks,
and it returns a complete, already-reasoned answer including the user-facing
line, so no surface reword "waiting for network" for itself. `resume_parked_work`
is a **rising edge**: it fires on the transition into a usable state from
`OFFLINE`, and leaving `DEGRADED` for `ONLINE` does not re-trigger it, because
nothing was parked for a degraded link.

Transitions are published to `alpha.events.bus` as `network.lost`,
`network.restored`, and `network.state.changed`.

## Configuration

`config.yaml -> network` is **startup-only**, registered in
`alpha.config.reload_boundary` as `network`. The monitor owns a background task,
an in-flight backoff ladder, and a published state other subsystems have already
read, so a mid-flight swap would split the process across two policies. Defaults
are production-usable, so a deployment that declares nothing still gets a
working monitor. `enabled: false` starts no poll task; a one-shot `check_once()`
is still available. Validation is fail-closed (`extra="forbid"`), and an empty
`targets` list is refused because it would leave connectivity permanently
`UNKNOWN`.

`config/network_resilience_config.py` imports from `alpha.runtime` **inside
function bodies only**. A module-level import is a cycle: `alpha.runtime` pulls
in the agent/sandbox chain, which imports `alpha.config` back.

## Error registry

One code was **appended** to `alpha/errors/registry.py` (never a repurpose of
an existing one — that registry is append-only):

- `NETWORK_UNAVAILABLE` — **severity `WARNING`**, not error. The task is alive
  and parked; recovery is a *resume*, not a re-execution. Correlation id
  `alpha.errors.network`.

It exists because the registry previously had no network class at all, so a lost
link surfaced as `MODEL_PROVIDER_UNAVAILABLE` and the run failed. A caller
distinguishes the two by asking `classify_network_error(...).proves_link_down`,
not by reading message text — a timeout must keep staying a provider problem.

**Deliberately one code, not two.** A degraded link that still let the request
through is already exactly what `DEGRADED_MODE` describes — "the request
succeeded, and you should know why" — and that code is the registry's *single*
disclosed-degradation 2xx by design (`tests/test_error_codes.py` pins it). A
second 2xx meaning the same thing would split one meaning across two codes,
which is the mistake the registry exists to prevent. A degraded link that *did*
fail the request is `NETWORK_UNAVAILABLE`, or a provider code, depending on what
the provider said.

**Where things live**:
- `runtime/network/states.py` — `NetworkState`, `is_connected`, `is_offline`,
  `NETWORK_STATE_DETAIL` (the shared operator-facing wording)
- `runtime/network/errors.py` — `NetworkFailureKind`, `NetworkFailure`,
  `classify_network_error`, `DEFINITIVE_LINK_FAILURE_KINDS`
- `runtime/network/probe.py` — `ProbeTarget`, `ConnectivityProbe`,
  `TcpConnectivityProbe`, `ScriptedProbe`, `normalize_targets`
- `runtime/network/monitor.py` — `NetworkMonitor`, `NetworkMonitorConfig`,
  `NetworkObservation`, `NetworkWaitDecision`, `recheck()`/`pending_confirmations`,
  and the bus event names
- `config/network_resilience_config.py` — the `config.yaml -> network` section
- `config/network_wait_config.py` — the `config.yaml -> network_wait` section
  (patience policy; `max_attempts: 0` = unbounded) and `to_wait_policy`
- `app.gateway.routers.ops` — `GET /api/ops/network` (the reading, its measured
  round-trip, and the automatic re-probe schedule) and
  `POST /api/ops/network/recheck` (the manual retry), both projected by
  `app.gateway.ops_runtime.network_snapshot`
- `app.gateway.routers.thread_runs` — `GET /api/threads/{id}/network-waits`
  (this thread's outage timeline beside the live reading)
- Tests: `tests/test_network_resilience.py`, `tests/test_ops_network_router.py`,
  `tests/test_network_wait_registry.py`, `tests/test_network_wait_timeline.py`
