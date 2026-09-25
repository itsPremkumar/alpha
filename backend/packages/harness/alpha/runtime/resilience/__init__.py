"""Resilience / convergence kit: retry, heal and stop - provably, not hopefully.

Alpha is expected to run unattended for years. That makes three things
non-negotiable, and this package is the shared substrate for all three:

1. **Termination is a product feature.** Every loop here has a proof-shaped
   bound - a max attempt count, a deadline, or a state transition that cannot
   repeat - and ``backend/tests/test_runtime_resilience.py`` fuzzes the policy
   space to assert that no configured combination can spin forever.
2. **Honest outcomes.** A failure is a *return value* with a reason drawn from
   a closed status set, never a swallowed exception and never a fake success.
   :func:`recover` only reports ``succeeded`` when the operation really
   returned, and it attaches the original exception to every other status.
3. **Determinism.** All timing goes through an injected
   :class:`~alpha.runtime.resilience.clock.Clock`; a :class:`ManualClock` (plus
   an injected sleeper and jitter) drives retry delays, breaker reset, budget
   expiry and idempotency TTL with zero real sleeping.

What is in the box
------------------
========================= =====================================================
:mod:`.clock`             ``Clock`` protocol, ``SystemClock``, ``ManualClock``,
                          ``Deadline``. The only module allowed to touch
                          :mod:`time`.
:mod:`.idempotency`       ``IdempotencyKey`` + ``IdempotencyRegistry`` with a
                          pluggable backend: at-most-once execution, single
                          winner on concurrent ``begin``, TTL on the injected
                          clock.
:mod:`.retry`             ``RetryPolicy`` (attempts / base / multiplier / cap /
                          injected jitter / injected classifier) and
                          ``retry_call``: non-retryable errors propagate
                          immediately, a deadline cuts the sequence short and
                          says so, and every attempt lands in an
                          ``AttemptRecord`` trail.
:mod:`.circuit`           ``CircuitBreaker``: closed/open/half-open, failure
                          threshold, reset timeout, single-flight half-open
                          probe, generation-fenced permits, per-instance state,
                          disclosed ``state_reason``.
:mod:`.convergence`       ``ConvergenceGuard``: the anti-oscillation layer.
                          Detects repeated actions, A->B->A ping-pong,
                          no-progress runs and alternating failure signatures,
                          and escalates on the fixed ladder ``WARN ->
                          CHANGE_CONTEXT -> REQUEST_DISCRIMINATING_EVIDENCE ->
                          CHANGE_STRATEGY -> DELEGATE -> TERMINATE`` with hard
                          caps and a pinned terminal state. A healing loop that
                          "fixes" a symptom by disabling its own check is
                          representable and is refused as a PROHIBITED
                          transition.
:mod:`.budget`            ``ResourceBudget``: attempts + wall clock + an
                          injected ``CostMeter``. Refuses cleanly when spent;
                          a post-hoc clamp is disclosed, never silent.
:mod:`.recovery`          ``recover(operation, ...)``: the whole ladder behind
                          one closed ``RecoveryStatus`` set.
:mod:`.config`            ``ResilienceConfig`` (pydantic, ``enabled: False`` by
                          default) whose every field has exactly one reader.
========================= =====================================================

Default OFF
-----------
``ResilienceConfig.enabled`` is ``False``. With it off, :func:`recover` is a
strict pass-through (one call, no bounds, failures reported as
``not_retryable`` with the exception type recorded) and every component a
caller builds directly is inert by construction: a breaker admits everything, a
guard never intervenes, a budget is unbounded. Wiring the model into a host
cannot change behaviour before an operator opts in.

No global mutable state, no module-level singletons, no new dependencies, no
network, no secrets. Every component is an instance the caller owns.

Existing modules that should delegate to this kit (adoption patches are
specified in the agent report; none are applied here)
---------------------------------------------------------------------------
These are **candidates**, not dependencies of this package - nothing in
``alpha.runtime.resilience`` imports any of them, so the direction of the
coupling is one-way:

* ``alpha/agents/middlewares/llm_error_handling_middleware.py`` - its
  ``while True`` retry loop with decorrelated jitter (``_build_retry_delay_ms``)
  and its closed/open/half-open breaker (``_check_circuit`` /
  ``_record_failure`` / ``_record_success`` / ``_release_half_open_probe``)
  are the two largest ad-hoc implementations in the tree.
* ``alpha/models/system_one.py::SystemOneClient`` - same breaker shape
  (``_half_open_probe`` + generation fence) plus ``_sleep_backoff`` with a
  ``Retry-After`` parse and a shared deadline.
* ``alpha/models/claude_provider.py::_calc_backoff_ms`` - per-attempt
  exponential backoff with a ``Retry-After`` override.
* ``alpha/models/free_router/catalog.py`` and ``alpha/models/failover.py`` -
  cooldown-with-jitter health state, currently on module-level ``time.time()``
  and (in ``failover``) a module-level cooldown map.
* ``alpha/projects/self_healing_runner.py::SelfHealingTestRunner`` - the
  bounded self-healing attempt loop with ``0.5x-1.5x`` jitter. This is also
  the natural home for the PROHIBITED "disable the failing check" transition.
* ``alpha/perpetual/stagnation.py::StagnationRecoveryWatchdog`` - repeated
  action detection and ad-hoc intervention choice.
* ``alpha/avo/supervisor.py::AVOSupervisor`` - A-B-A oscillation detection and
  stagnation pivoting; the closest existing thing to ``ConvergenceGuard``.
* ``alpha/recovery/policies.py`` - the per-failure-class
  ``(max_attempts, backoff_seconds, terminal_strategy)`` table, which becomes a
  ``RetryPolicy`` factory.
* ``alpha/browser/jev_agent.py`` - loop/oscillation detection and its
  ``no_progress`` terminal status.
* ``alpha/orchestrator/durable_tasks.py`` - exactly-once delivery queue keyed
  by idempotency keys (a candidate durable backend later, not a replacement).
* ``alpha/persistence/run/sql.py`` - the durable ``uq_runs_idempotency_key``
  path; the model for a shared :class:`IdempotencyBackend`.

Behaviour that deliberately differs (do not "fix" these in an adoption patch
without changing this package too)
----------------------------------------------------------------------------
* **Sync core.** There are no ``async`` variants. Async callers (the LLM
  middleware, System One) keep their ``asyncio.sleep`` and adopt the *policy
  math* (``RetryPolicy.delay_for``) and the *breaker* (both are clock-injected
  and loop-agnostic) rather than porting the loop.
* **Jitter shape.** The kit's default is ``FullJitter`` (uniform in
  ``[0, delay]``, Brooker 2015) with the cap applied to the nominal window
  *before* the draw. The LLM middleware uses AWS decorrelated jitter
  (``randint(base, seed*3)``) and the self-healing runner uses multiplicative
  ``0.5-1.5x``. Adopting this kit's jitter changes the delay *distribution* -
  that is a deliberate, disclosed change, not a bug fix.
* **Breaker failure accounting.** ``CircuitBreaker.call`` counts *every*
  exception as a failure. The LLM middleware deliberately does not count
  non-retriable errors or burst-rate 429s; a caller that needs that must use
  the explicit ``acquire()``/``record_success()``/``record_failure()`` API
  instead of ``call()``.
* **Default classifier.** The kit retries only ``TransientError``,
  ``TimeoutError`` and ``ConnectionError`` by default. The middleware's
  pattern-matching classifier (``_classify_error``) stays where it is and is
  injected via ``RetryPolicy.classifier``.
* **Monotone ratchet.** ``ConvergenceGuard`` only escalates and pins itself at
  ``TERMINATE``. ``AVOSupervisor`` and ``StagnationRecoveryWatchdog`` both
  *reset* their counters after an intervention, so the same stall can re-trigger
  the same rung forever. That is exactly the oscillation this package refuses;
  an adoption must accept the stricter behaviour.
* **Prohibited transitions.** Neither existing supervisor models "disable the
  check to make it pass". The kit does, and treats it as terminal. If an
  adoption needs a legitimate weakening (e.g. a load-shed switch), it must be
  expressed as a *different* transition kind, not one of
  ``PROHIBITED_TRANSITION_KINDS``.
* **In-process idempotency only.** ``IdempotencyRegistry`` guarantees
  at-most-once inside one process. It does not replace the durable unique
  index in ``persistence/run/sql.py``; a multi-process host must inject a
  shared backend.
* **No implicit global clock.** ``free_router``/``failover`` read
  ``time.time()`` (wall clock) directly. Everything here is monotonic and
  injected, so an adoption must pass a clock into the new component.

Import shape
------------
Exports are installed lazily (:pep:`562`, via
:func:`alpha.memory._lazy_exports.install_lazy_exports`) so importing one
primitive does not build the whole kit, and a future promotion of
``ResilienceConfig`` into the shared config schema cannot create an import
cycle back through ``alpha.runtime``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    # clock
    "Clock": "clock",
    "Deadline": "clock",
    "ManualClock": "clock",
    "Sleeper": "clock",
    "SystemClock": "clock",
    "SystemSleeper": "clock",
    "coerce_clock": "clock",
    "require_delay": "clock",
    "resolve_sleeper": "clock",
    # errors
    "BudgetExhaustedError": "errors",
    "CircuitOpenError": "errors",
    "ControlSignal": "errors",
    "DeadlineExceeded": "errors",
    "IdempotencyError": "errors",
    "ProhibitedTransitionError": "errors",
    "ResilienceError": "errors",
    "RetryAbortedError": "errors",
    "RetryExhaustedError": "errors",
    "TransientError": "errors",
    # idempotency
    "IdempotencyBackend": "idempotency",
    "IdempotencyClaim": "idempotency",
    "IdempotencyKey": "idempotency",
    "IdempotencyRegistry": "idempotency",
    "IdempotencyStatus": "idempotency",
    "InMemoryIdempotencyBackend": "idempotency",
    "RunOutcome": "idempotency",
    # retry
    "AttemptOutcome": "retry",
    "AttemptRecord": "retry",
    "AttemptTrail": "retry",
    "FullJitter": "retry",
    "Jitter": "retry",
    "NoJitter": "retry",
    "RetryDecision": "retry",
    "RetryPolicy": "retry",
    "RetryResult": "retry",
    "classify_exception": "retry",
    "retry_call": "retry",
    # circuit
    "CircuitBreaker": "circuit",
    "CircuitPermit": "circuit",
    "CircuitSnapshot": "circuit",
    "CircuitState": "circuit",
    # convergence
    "ESCALATION_ORDER": "convergence",
    "PROHIBITED_TRANSITION_KINDS": "convergence",
    "ConvergenceDecision": "convergence",
    "ConvergenceGuard": "convergence",
    "ConvergenceSnapshot": "convergence",
    "GuardStatus": "convergence",
    "Intervention": "convergence",
    "Observation": "convergence",
    "ObservationKind": "convergence",
    "ThrashSignal": "convergence",
    "Transition": "convergence",
    "is_prohibited_transition": "convergence",
    # budget
    "BudgetSnapshot": "budget",
    "CostMeter": "budget",
    "FixedCostMeter": "budget",
    "ResourceBudget": "budget",
    "ZeroCostMeter": "budget",
    # recovery
    "NO_VALUE": "recovery",
    "RecoveryOutcome": "recovery",
    "RecoveryStatus": "recovery",
    "RecoveryStep": "recovery",
    "recover": "recovery",
    # config
    "BudgetSettings": "config",
    "CircuitSettings": "config",
    "ConvergenceSettings": "config",
    "IdempotencySettings": "config",
    "ResilienceConfig": "config",
    "RetrySettings": "config",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    # Explicit ``X as X`` re-exports: the runtime surface is installed lazily
    # above, so this block exists purely for type checkers and IDEs.
    from alpha.runtime.resilience.budget import BudgetSnapshot as BudgetSnapshot
    from alpha.runtime.resilience.budget import CostMeter as CostMeter
    from alpha.runtime.resilience.budget import FixedCostMeter as FixedCostMeter
    from alpha.runtime.resilience.budget import ResourceBudget as ResourceBudget
    from alpha.runtime.resilience.budget import ZeroCostMeter as ZeroCostMeter
    from alpha.runtime.resilience.circuit import CircuitBreaker as CircuitBreaker
    from alpha.runtime.resilience.circuit import CircuitPermit as CircuitPermit
    from alpha.runtime.resilience.circuit import CircuitSnapshot as CircuitSnapshot
    from alpha.runtime.resilience.circuit import CircuitState as CircuitState
    from alpha.runtime.resilience.clock import Clock as Clock
    from alpha.runtime.resilience.clock import Deadline as Deadline
    from alpha.runtime.resilience.clock import ManualClock as ManualClock
    from alpha.runtime.resilience.clock import Sleeper as Sleeper
    from alpha.runtime.resilience.clock import SystemClock as SystemClock
    from alpha.runtime.resilience.clock import SystemSleeper as SystemSleeper
    from alpha.runtime.resilience.clock import coerce_clock as coerce_clock
    from alpha.runtime.resilience.clock import require_delay as require_delay
    from alpha.runtime.resilience.clock import resolve_sleeper as resolve_sleeper
    from alpha.runtime.resilience.config import BudgetSettings as BudgetSettings
    from alpha.runtime.resilience.config import CircuitSettings as CircuitSettings
    from alpha.runtime.resilience.config import ConvergenceSettings as ConvergenceSettings
    from alpha.runtime.resilience.config import IdempotencySettings as IdempotencySettings
    from alpha.runtime.resilience.config import ResilienceConfig as ResilienceConfig
    from alpha.runtime.resilience.config import RetrySettings as RetrySettings
    from alpha.runtime.resilience.convergence import ESCALATION_ORDER as ESCALATION_ORDER
    from alpha.runtime.resilience.convergence import PROHIBITED_TRANSITION_KINDS as PROHIBITED_TRANSITION_KINDS
    from alpha.runtime.resilience.convergence import ConvergenceDecision as ConvergenceDecision
    from alpha.runtime.resilience.convergence import ConvergenceGuard as ConvergenceGuard
    from alpha.runtime.resilience.convergence import ConvergenceSnapshot as ConvergenceSnapshot
    from alpha.runtime.resilience.convergence import GuardStatus as GuardStatus
    from alpha.runtime.resilience.convergence import Intervention as Intervention
    from alpha.runtime.resilience.convergence import Observation as Observation
    from alpha.runtime.resilience.convergence import ObservationKind as ObservationKind
    from alpha.runtime.resilience.convergence import ThrashSignal as ThrashSignal
    from alpha.runtime.resilience.convergence import Transition as Transition
    from alpha.runtime.resilience.convergence import is_prohibited_transition as is_prohibited_transition
    from alpha.runtime.resilience.errors import BudgetExhaustedError as BudgetExhaustedError
    from alpha.runtime.resilience.errors import CircuitOpenError as CircuitOpenError
    from alpha.runtime.resilience.errors import ControlSignal as ControlSignal
    from alpha.runtime.resilience.errors import DeadlineExceeded as DeadlineExceeded
    from alpha.runtime.resilience.errors import IdempotencyError as IdempotencyError
    from alpha.runtime.resilience.errors import ProhibitedTransitionError as ProhibitedTransitionError
    from alpha.runtime.resilience.errors import ResilienceError as ResilienceError
    from alpha.runtime.resilience.errors import RetryAbortedError as RetryAbortedError
    from alpha.runtime.resilience.errors import RetryExhaustedError as RetryExhaustedError
    from alpha.runtime.resilience.errors import TransientError as TransientError
    from alpha.runtime.resilience.idempotency import IdempotencyBackend as IdempotencyBackend
    from alpha.runtime.resilience.idempotency import IdempotencyClaim as IdempotencyClaim
    from alpha.runtime.resilience.idempotency import IdempotencyKey as IdempotencyKey
    from alpha.runtime.resilience.idempotency import IdempotencyRegistry as IdempotencyRegistry
    from alpha.runtime.resilience.idempotency import IdempotencyStatus as IdempotencyStatus
    from alpha.runtime.resilience.idempotency import InMemoryIdempotencyBackend as InMemoryIdempotencyBackend
    from alpha.runtime.resilience.idempotency import RunOutcome as RunOutcome
    from alpha.runtime.resilience.recovery import NO_VALUE as NO_VALUE
    from alpha.runtime.resilience.recovery import RecoveryOutcome as RecoveryOutcome
    from alpha.runtime.resilience.recovery import RecoveryStatus as RecoveryStatus
    from alpha.runtime.resilience.recovery import RecoveryStep as RecoveryStep
    from alpha.runtime.resilience.recovery import recover as recover
    from alpha.runtime.resilience.retry import AttemptOutcome as AttemptOutcome
    from alpha.runtime.resilience.retry import AttemptRecord as AttemptRecord
    from alpha.runtime.resilience.retry import AttemptTrail as AttemptTrail
    from alpha.runtime.resilience.retry import FullJitter as FullJitter
    from alpha.runtime.resilience.retry import Jitter as Jitter
    from alpha.runtime.resilience.retry import NoJitter as NoJitter
    from alpha.runtime.resilience.retry import RetryDecision as RetryDecision
    from alpha.runtime.resilience.retry import RetryPolicy as RetryPolicy
    from alpha.runtime.resilience.retry import RetryResult as RetryResult
    from alpha.runtime.resilience.retry import classify_exception as classify_exception
    from alpha.runtime.resilience.retry import retry_call as retry_call

install_lazy_exports(__name__, _EXPORTS)
