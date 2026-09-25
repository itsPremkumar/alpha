"""Tests for the resilience / convergence kit (``alpha.runtime.resilience``).

The kit's contract is that timing is fully injectable, so **no test in this file
sleeps on real time**: every wait is a :class:`ManualClock` tick. The one
exception is the deliberate ``time.sleep`` trap in
``TestClock.test_package_runs_with_real_sleep_disabled``, which proves the
package cannot reach the real clock from a test at all.

Coverage map (each acceptance criterion has its own test):
* clock: ManualClock drives retry delays, circuit reset, deadline expiry, TTL.
* idempotency: run-once, single winner under concurrency, TTL, failure paths.
* retry: non-retryable runs once, policy schedule, deadline cut, trail, abort.
* circuit: threshold, single-flight probe, close on success, per-instance state.
* convergence: every thrash signal, ping-pong, escalation order, caps,
  prohibited transition.
* budget: refusal (never a silent overshoot), cost meter, wall clock.
* recovery: every status in the closed set, hooks, delegate, disabled
  pass-through, idempotency composition, no fake success.
* config: every field has a reader that actually receives the value.
* termination: a fuzzed policy space where every path must terminate.
"""

from __future__ import annotations

import ast
import itertools
import math
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

import alpha.runtime.resilience as resilience_package
from alpha.runtime.resilience.budget import FixedCostMeter, ResourceBudget
from alpha.runtime.resilience.circuit import CircuitBreaker, CircuitState
from alpha.runtime.resilience.clock import (
    Deadline,
    ManualClock,
    SystemClock,
    SystemSleeper,
    require_delay,
    resolve_sleeper,
)
from alpha.runtime.resilience.config import (
    BudgetSettings,
    CircuitSettings,
    ConvergenceSettings,
    IdempotencySettings,
    ResilienceConfig,
    RetrySettings,
)
from alpha.runtime.resilience.convergence import (
    ESCALATION_ORDER,
    PROHIBITED_TRANSITION_KINDS,
    ConvergenceGuard,
    GuardStatus,
    Intervention,
    ObservationKind,
    ThrashSignal,
    Transition,
    is_prohibited_transition,
)
from alpha.runtime.resilience.errors import (
    BudgetExhaustedError,
    CircuitOpenError,
    DeadlineExceeded,
    IdempotencyError,
    ProhibitedTransitionError,
    ResilienceError,
    RetryAbortedError,
    RetryExhaustedError,
    TransientError,
)
from alpha.runtime.resilience.idempotency import (
    IdempotencyKey,
    IdempotencyRegistry,
    IdempotencyStatus,
    InMemoryIdempotencyBackend,
    RunOutcome,
)
from alpha.runtime.resilience.recovery import NO_VALUE, RecoveryStatus, RecoveryStep, recover
from alpha.runtime.resilience.retry import (
    AttemptOutcome,
    AttemptRecord,
    AttemptTrail,
    FullJitter,
    Jitter,
    NoJitter,
    RetryDecision,
    RetryPolicy,
    classify_exception,
    retry_call,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

#: A jitter that is deterministic and exactly half the nominal delay.
HALF_JITTER = FullJitter(random_fn=lambda: 0.5)


def _transient(message: str = "dependency down") -> TransientError:
    return TransientError(message)


def _always_transient(message: str = "down"):
    def operation() -> str:
        raise _transient(message)

    return operation


def _flaky(failures: int, value: str = "ok", message: str = "transient"):
    state = {"calls": 0}

    def operation() -> str:
        state["calls"] += 1
        if state["calls"] <= failures:
            raise _transient(message)
        return value

    operation.calls = state  # type: ignore[attr-defined]
    return operation


# ---------------------------------------------------------------------------
# clock
# ---------------------------------------------------------------------------


class TestClock:
    def test_manual_clock_advance_and_set_are_monotonic(self):
        clock = ManualClock(start=5.0)
        assert clock.now() == 5.0
        assert clock.advance(2.5) == 7.5
        assert clock.set(9.0) == 9.0
        with pytest.raises(ValueError):
            clock.set(8.0)
        with pytest.raises(ValueError):
            clock.advance(-1.0)
        with pytest.raises(ValueError):
            clock.advance(math.inf)

    def test_manual_clock_sleep_advances_without_waiting(self):
        clock = ManualClock()
        clock.sleep(1.5)
        clock.sleep(0.0)
        assert clock.now() == 1.5
        assert clock.sleeps == [1.5, 0.0]

    def test_system_clock_is_monotonic_and_sleeper_is_resolvable(self):
        clock = SystemClock()
        first = clock.now()
        assert clock.now() >= first
        # A read-only clock still resolves a sleeper rather than failing.
        assert callable(resolve_sleeper(clock))
        assert callable(resolve_sleeper(ManualClock(), lambda seconds: None))
        assert require_delay(0.0) == 0.0
        with pytest.raises(ValueError):
            require_delay(-0.1)
        SystemSleeper().sleep(0.0)

    def test_deadline_expires_on_the_injected_clock(self):
        clock = ManualClock()
        deadline = Deadline.after(clock, 10.0, label="round")
        assert deadline.remaining() == 10.0
        assert deadline.label == "round"
        clock.advance(4.0)
        assert deadline.remaining() == 6.0
        assert deadline.fits(5.9) is True
        assert deadline.fits(6.0) is False
        deadline.check()
        clock.advance(6.0)
        assert deadline.expired() is True
        assert deadline.remaining() == 0.0
        with pytest.raises(DeadlineExceeded) as excinfo:
            deadline.check()
        assert excinfo.value.at == deadline.expires_at
        assert "round" in str(excinfo.value)

    def test_only_clock_module_touches_the_time_module(self):
        """The package's no-real-time invariant, enforced by AST."""
        package_dir = Path(resilience_package.__file__).parent
        time_attributes = {"time", "monotonic", "monotonic_ns", "perf_counter", "sleep"}
        offenders: dict[str, list[int]] = {}
        for module_path in sorted(package_dir.glob("*.py")):
            tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
            hits: list[int] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in time_attributes and isinstance(node.func.value, ast.Name) and node.func.value.id == "time":
                    hits.append(node.lineno)
            if hits:
                offenders[module_path.name] = hits
        assert set(offenders) == {"clock.py"}, f"time.* called outside clock.py: {offenders}"

    def test_package_runs_with_real_sleep_disabled(self, monkeypatch: pytest.MonkeyPatch):
        """Proves the whole ladder is drivable with zero real sleeping."""
        import time as real_time

        def _forbidden(seconds: float) -> None:  # pragma: no cover - must never run
            raise AssertionError(f"real sleeping attempted: {seconds!r}s")

        monkeypatch.setattr(real_time, "sleep", _forbidden)
        clock = ManualClock()
        outcome = recover(
            _always_transient(),
            policy=RetryPolicy(attempts=3, base_delay=1.0, jitter=HALF_JITTER),
            clock=clock,
            breaker=CircuitBreaker(name="sleep-trap", failure_threshold=2, reset_timeout=1.0, clock=clock),
            budget=ResourceBudget(clock=clock, max_attempts=2, wall_clock_timeout=100.0),
            guard=ConvergenceGuard(max_attempts=2),
        )
        assert outcome.status is RecoveryStatus.BUDGET_EXHAUSTED
        assert clock.sleeps  # virtual waits only
        assert real_time.sleep is _forbidden


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_key_serializes_stably_and_rejects_junk(self):
        key = IdempotencyKey("runs", "run-42")
        assert key.serialize() == "runs:run-42"
        assert IdempotencyKey.parse(key.serialize()) == key
        assert key.fingerprint() == IdempotencyKey("runs", "run-42").fingerprint()
        assert len(key.fingerprint()) == 64
        with pytest.raises(ValueError):
            IdempotencyKey("", "v")
        with pytest.raises(ValueError):
            IdempotencyKey("ns", "")
        for malformed in ("no-colon", ":value", "ns:"):
            with pytest.raises(ValueError):
                IdempotencyKey.parse(malformed)

    def test_same_key_runs_the_action_once_and_returns_the_first_outcome(self):
        clock = ManualClock()
        registry = IdempotencyRegistry(clock=clock, ttl=60.0)
        key = IdempotencyKey("runs", "run-1")
        calls: list[str] = []

        def action() -> str:
            calls.append("ran")
            return "first"

        winner = registry.execute(key, action)
        assert winner.status is IdempotencyStatus.ACQUIRED
        assert winner.outcome is not None
        assert winner.outcome.ok is True
        assert winner.outcome.value == "first"

        repeat = registry.execute(key, action)
        assert repeat.status is IdempotencyStatus.DUPLICATE
        assert repeat.outcome is not None
        assert repeat.outcome.value == "first"
        assert calls == ["ran"]

    def test_concurrent_begins_have_exactly_one_winner(self):
        clock = ManualClock()
        registry = IdempotencyRegistry(clock=clock, ttl=60.0)
        key = IdempotencyKey("runs", "race")
        workers = 8
        barrier = threading.Barrier(workers)
        claims: list = []
        lock = threading.Lock()

        def contend() -> None:
            barrier.wait(timeout=5)
            claim = registry.begin(key)
            with lock:
                claims.append(claim)

        threads = [threading.Thread(target=contend) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert len(claims) == workers
        winners = [claim for claim in claims if claim.status is IdempotencyStatus.ACQUIRED]
        assert len(winners) == 1
        assert all(claim.status is IdempotencyStatus.IN_PROGRESS for claim in claims if claim is not winners[0])
        assert all(claim.should_execute is False for claim in claims if claim is not winners[0])

    def test_concurrent_executes_run_the_action_exactly_once(self):
        clock = ManualClock()
        registry = IdempotencyRegistry(clock=clock, ttl=60.0)
        key = IdempotencyKey("runs", "execute-race")
        workers = 6
        barrier = threading.Barrier(workers)
        executions: list[int] = []
        lock = threading.Lock()

        def action() -> int:
            with lock:
                executions.append(1)
            return len(executions)

        def contend() -> None:
            barrier.wait(timeout=5)
            registry.execute(key, action)

        threads = [threading.Thread(target=contend) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert executions == [1]
        assert len(registry) == 1

    def test_ttl_expiry_is_measured_on_the_injected_clock(self):
        clock = ManualClock()
        registry = IdempotencyRegistry(clock=clock, ttl=10.0)
        key = IdempotencyKey("runs", "ttl")
        registry.execute(key, lambda: "value")
        clock.advance(9.999)
        assert registry.begin(key).status is IdempotencyStatus.DUPLICATE
        clock.advance(0.002)
        assert registry.begin(key).status is IdempotencyStatus.ACQUIRED

    def test_failure_is_recorded_and_not_silently_re_run(self):
        clock = ManualClock()
        registry = IdempotencyRegistry(clock=clock, ttl=60.0)
        key = IdempotencyKey("runs", "boom")
        calls: list[int] = []

        def action() -> str:
            calls.append(1)
            raise RuntimeError("side effect may have happened")

        with pytest.raises(RuntimeError):
            registry.execute(key, action)
        repeat = registry.execute(key, action)
        assert repeat.status is IdempotencyStatus.FAILED
        assert repeat.outcome is not None
        assert repeat.outcome.ok is False
        assert "RuntimeError" in str(repeat.outcome.error)
        assert calls == [1]

    def test_fail_with_release_allows_an_explicit_retry(self):
        clock = ManualClock()
        registry = IdempotencyRegistry(clock=clock, ttl=60.0)
        key = IdempotencyKey("runs", "release")
        claim = registry.begin(key)
        assert claim.status is IdempotencyStatus.ACQUIRED
        registry.fail(key, claim, RunOutcome(ok=False, error="did not take effect"), release=True)
        assert registry.peek(key) is None
        assert registry.begin(key).status is IdempotencyStatus.ACQUIRED

    def test_settling_a_stale_claim_is_refused_loudly(self):
        clock = ManualClock()
        registry = IdempotencyRegistry(clock=clock, ttl=5.0)
        key = IdempotencyKey("runs", "stale")
        stale = registry.begin(key)
        clock.advance(6.0)
        fresh = registry.begin(key)
        assert fresh.status is IdempotencyStatus.ACQUIRED
        with pytest.raises(IdempotencyError):
            registry.complete(key, stale, RunOutcome(ok=True, value="ghost"))
        assert registry.peek(key) is not None
        assert registry.peek(key).outcome is None  # type: ignore[union-attr]

    def test_peek_never_creates_a_record_and_len_is_per_registry(self):
        clock = ManualClock()
        registry = IdempotencyRegistry(clock=clock, ttl=60.0)
        key = IdempotencyKey("runs", "peek")
        assert registry.peek(key) is None
        assert len(registry) == 0
        registry.execute(key, lambda: 1)
        peeked = registry.peek(key)
        assert peeked is not None
        assert peeked.status is IdempotencyStatus.DUPLICATE
        assert len(registry) == 1
        assert IdempotencyRegistry(clock=clock).peek(key) is None  # per-instance state

    def test_pluggable_backend_is_delegated_to(self):
        clock = ManualClock()
        backend = InMemoryIdempotencyBackend(clock)
        registry = IdempotencyRegistry(clock=clock, ttl=30.0, backend=backend)
        key = IdempotencyKey("runs", "pluggable")
        registry.execute(key, lambda: "value")
        assert len(backend) == 1
        # A second registry over the same backend sees the record: the seam is
        # real storage, not a per-registry cache.
        sibling = IdempotencyRegistry(clock=clock, ttl=30.0, backend=backend)
        peeked = sibling.peek(key)
        assert peeked is not None
        assert peeked.status is IdempotencyStatus.DUPLICATE
        assert peeked.outcome is not None
        assert peeked.outcome.value == "value"

    def test_registry_rejects_a_non_positive_ttl(self):
        with pytest.raises(ValueError):
            IdempotencyRegistry(clock=ManualClock(), ttl=0)


# ---------------------------------------------------------------------------
# retry
# ---------------------------------------------------------------------------


class TestRetry:
    def test_non_retryable_error_is_attempted_exactly_once(self):
        clock = ManualClock()
        operation = _flaky(0, value="never")
        with pytest.raises(ValueError) as excinfo:
            retry_call(lambda: (_ for _ in ()).throw(ValueError("bug")), RetryPolicy(attempts=5, base_delay=1.0), clock=clock)
        assert isinstance(excinfo.value, ValueError)
        assert clock.sleeps == []
        assert clock.now() == 0.0
        assert operation.calls["calls"] == 0

    def test_trail_records_the_terminal_attempt(self):
        clock = ManualClock()
        trail = AttemptTrail()
        with pytest.raises(ValueError):
            retry_call(
                lambda: (_ for _ in ()).throw(ValueError("bug")),
                RetryPolicy(attempts=4),
                clock=clock,
                trail=trail,
            )
        assert len(trail) == 1
        record = trail.last()
        assert record is not None
        assert record.attempt == 1
        assert record.outcome is AttemptOutcome.NOT_RETRYABLE
        assert record.error_type == "ValueError"
        assert record.succeeded is False

    def test_retryable_error_follows_the_policy_schedule(self):
        clock = ManualClock()
        policy = RetryPolicy(attempts=4, base_delay=1.0, multiplier=2.0, max_delay=10.0, jitter=HALF_JITTER)
        result = retry_call(_flaky(2), policy, clock=clock)
        assert result.value == "ok"
        assert result.attempts == 3
        assert result.succeeded is True
        assert clock.sleeps == [0.5, 1.0]
        assert [record.outcome for record in result.trail] == [
            AttemptOutcome.RETRY_SCHEDULED,
            AttemptOutcome.RETRY_SCHEDULED,
            AttemptOutcome.SUCCEEDED,
        ]

    def test_attempts_are_a_hard_ceiling(self):
        clock = ManualClock()
        policy = RetryPolicy(attempts=3, base_delay=1.0, jitter=NoJitter())
        with pytest.raises(RetryExhaustedError) as excinfo:
            retry_call(_always_transient(), policy, clock=clock)
        assert excinfo.value.reason == "attempts_exhausted"
        assert excinfo.value.attempts == 3
        assert isinstance(excinfo.value.__cause__, TransientError)
        assert len(clock.sleeps) == 2  # never sleeps after the final attempt

    def test_deadline_cuts_the_sequence_short_and_reports_it(self):
        clock = ManualClock()
        policy = RetryPolicy(attempts=9, base_delay=1.0, multiplier=2.0, jitter=NoJitter())
        deadline = Deadline.after(clock, 0.4)
        with pytest.raises(RetryExhaustedError) as excinfo:
            retry_call(_always_transient(), policy, clock=clock, deadline=deadline)
        assert excinfo.value.reason == "deadline"
        assert excinfo.value.attempts == 1
        assert clock.sleeps == []  # the refused backoff never ran

    def test_expired_deadline_refuses_before_the_first_attempt(self):
        clock = ManualClock()
        calls: list[int] = []
        deadline = Deadline.after(clock, 0.0)
        with pytest.raises(RetryExhaustedError) as excinfo:
            retry_call(
                lambda: calls.append(1) or "unused",
                RetryPolicy(attempts=3),
                clock=clock,
                deadline=deadline,
            )
        assert excinfo.value.attempts == 0
        assert calls == []

    def test_abort_signal_stops_the_loop(self):
        clock = ManualClock()
        state = {"calls": 0}

        def operation() -> str:
            state["calls"] += 1
            raise _transient()

        with pytest.raises(RetryAbortedError) as excinfo:
            retry_call(operation, RetryPolicy(attempts=5, base_delay=1.0), clock=clock, abort=lambda: state["calls"] >= 1)
        assert excinfo.value.attempts == 1
        assert state["calls"] == 1

    def test_control_signals_are_never_retried_even_by_a_permissive_classifier(self):
        clock = ManualClock()
        calls: list[int] = []

        def always_retryable(exc: BaseException) -> RetryDecision:
            calls.append(1)
            return RetryDecision.RETRYABLE

        policy = RetryPolicy(attempts=5, base_delay=1.0, jitter=NoJitter(), classifier=always_retryable)
        breaker = CircuitBreaker(name="c", failure_threshold=1, reset_timeout=10.0, clock=clock)
        breaker.record_failure()
        with pytest.raises(CircuitOpenError):
            retry_call(lambda: breaker.call(lambda: "never"), policy, clock=clock)
        assert calls == []  # the classifier is not even consulted
        assert clock.sleeps == []

    def test_returning_none_is_a_success(self):
        clock = ManualClock()
        result = retry_call(lambda: None, RetryPolicy(attempts=2), clock=clock)
        assert result.value is None
        assert result.attempts == 1
        assert result.trail.last().succeeded is True  # type: ignore[union-attr]

    def test_jitter_is_injected_and_deterministic(self):
        clock = ManualClock()
        policy = RetryPolicy(attempts=3, base_delay=2.0, multiplier=2.0, max_delay=8.0, jitter=FullJitter(random_fn=lambda: 0.25))
        with pytest.raises(RetryExhaustedError):
            retry_call(_always_transient(), policy, clock=clock)
        assert clock.sleeps == [0.5, 1.0]

    def test_policy_delay_math_is_capped_and_validated(self):
        policy = RetryPolicy(attempts=4, base_delay=1.0, multiplier=3.0, max_delay=5.0, jitter=NoJitter())
        assert policy.nominal_delay(1) == 1.0
        assert policy.nominal_delay(2) == 3.0
        assert policy.nominal_delay(3) == 5.0  # capped
        assert policy.delay_for(3) == 5.0
        assert RetryPolicy(base_delay=0.0, jitter=NoJitter()).delay_for(2) == 0.0
        with pytest.raises(ValueError):
            policy.nominal_delay(0)
        with pytest.raises(ValueError):
            RetryPolicy(attempts=0)
        with pytest.raises(ValueError):
            RetryPolicy(multiplier=0.5)
        with pytest.raises(ValueError):
            RetryPolicy(base_delay=-1.0)
        with pytest.raises(TypeError):
            RetryPolicy(jitter="nope")
        with pytest.raises(TypeError):
            RetryPolicy(classifier="nope")

    def test_default_classifier_shapes(self):
        assert classify_exception(_transient()) is RetryDecision.RETRYABLE
        assert classify_exception(TimeoutError()) is RetryDecision.RETRYABLE
        assert classify_exception(ConnectionError()) is RetryDecision.RETRYABLE
        assert classify_exception(BudgetExhaustedError("x")) is RetryDecision.ABORT
        assert classify_exception(ValueError("bug")) is RetryDecision.TERMINAL
        assert classify_exception(FileNotFoundError("missing")) is RetryDecision.TERMINAL

    def test_trail_is_bounded_but_keeps_an_honest_total(self):
        trail = AttemptTrail(max_records=3)
        for attempt in range(5):
            trail.add(AttemptRecord(attempt, AttemptOutcome.RETRY_SCHEDULED, 0.0, 0.0, 0.0))
        assert trail.attempts == 5
        assert trail.retained == 3
        assert len(trail) == 3
        assert [record.attempt for record in trail] == [2, 3, 4]

    def test_custom_jitter_decorator_is_injectable(self):
        clock = ManualClock()
        policy = RetryPolicy(attempts=3, base_delay=1.0, multiplier=2.0, max_delay=100.0, jitter=Jitter(fn=lambda nominal, attempt: nominal * attempt))
        with pytest.raises(RetryExhaustedError):
            retry_call(_always_transient(), policy, clock=clock)
        assert clock.sleeps == [1.0, 4.0]
        with pytest.raises(ValueError):
            Jitter(fn=lambda nominal, attempt: -1.0)(1.0, 1)


# ---------------------------------------------------------------------------
# circuit
# ---------------------------------------------------------------------------


class TestCircuit:
    def test_opens_at_the_threshold_with_a_disclosed_reason(self):
        clock = ManualClock()
        breaker = CircuitBreaker(name="provider", failure_threshold=2, reset_timeout=30.0, clock=clock)
        assert breaker.state() is CircuitState.CLOSED
        assert breaker.state_reason().startswith("closed")
        breaker.record_failure("TransientError")
        assert breaker.state() is CircuitState.CLOSED
        snapshot = breaker.record_failure("TransientError")
        assert snapshot.state is CircuitState.OPEN
        assert breaker.state() is CircuitState.OPEN
        assert "2 consecutive failures" in breaker.state_reason()
        assert "retry in 30" in breaker.state_reason()
        assert breaker.allow_request() is False
        with pytest.raises(CircuitOpenError) as excinfo:
            breaker.acquire()
        assert excinfo.value.retry_after == 30.0
        assert "consecutive failures" in excinfo.value.state_reason

    def test_reset_timeout_admits_exactly_one_half_open_probe(self):
        clock = ManualClock()
        breaker = CircuitBreaker(name="provider", failure_threshold=1, reset_timeout=10.0, clock=clock)
        breaker.record_failure()
        assert breaker.state() is CircuitState.OPEN
        clock.advance(9.999)
        assert breaker.state() is CircuitState.OPEN
        clock.advance(0.001)
        assert breaker.state() is CircuitState.HALF_OPEN
        assert "awaiting single probe" in breaker.state_reason()

        first = breaker.acquire()
        assert first.is_probe is True
        assert "probe in flight" in breaker.state_reason()
        with pytest.raises(CircuitOpenError) as excinfo:
            breaker.acquire()
        assert "probe already in flight" in str(excinfo.value)

    def test_probe_is_single_flight_across_threads(self):
        clock = ManualClock()
        breaker = CircuitBreaker(name="provider", failure_threshold=1, reset_timeout=5.0, clock=clock)
        breaker.record_failure()
        clock.advance(5.0)
        probe_taken = threading.Event()
        release = threading.Event()
        errors: list[BaseException] = []

        def probe() -> None:
            breaker.acquire()
            probe_taken.set()
            assert release.wait(timeout=10)

        worker = threading.Thread(target=probe)
        worker.start()
        assert probe_taken.wait(timeout=10)
        try:
            breaker.acquire()
        except CircuitOpenError as exc:
            errors.append(exc)
        release.set()
        worker.join(timeout=10)
        assert len(errors) == 1
        assert not worker.is_alive()

    def test_successful_probe_closes_and_failed_probe_reopens(self):
        clock = ManualClock()
        breaker = CircuitBreaker(name="provider", failure_threshold=2, reset_timeout=5.0, clock=clock)
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state() is CircuitState.OPEN
        clock.advance(5.0)
        failed_probe = breaker.acquire()
        breaker.record_failure("still down", permit=failed_probe)
        assert breaker.state() is CircuitState.OPEN
        assert "probe failed" in breaker.state_reason()
        clock.advance(5.0)
        probe = breaker.acquire()
        closed = breaker.record_success(permit=probe)
        assert closed.state is CircuitState.CLOSED
        assert closed.consecutive_failures == 0
        assert closed.failure_count == 0
        assert breaker.allow_request() is True

    def test_a_late_result_cannot_corrupt_a_newer_window(self):
        """Generation fence: an outcome admitted before a transition is ignored.

        The same hazard is why ``SystemOneClient`` carries a ``_breaker_generation``
        and the LLM middleware a probe token.
        """
        clock = ManualClock()
        breaker = CircuitBreaker(name="provider", failure_threshold=1, reset_timeout=5.0, clock=clock)
        admitted = breaker.acquire()
        assert admitted.is_probe is False
        breaker.record_failure("provider down")  # opens the window, bumps generation
        assert breaker.state() is CircuitState.OPEN
        stale = breaker.record_failure("late result from before the open", permit=admitted)
        assert stale.failure_count == 1  # ignored: the window is unchanged
        assert breaker.state() is CircuitState.OPEN
        stale = breaker.record_success(permit=admitted)  # also ignored
        assert breaker.state() is CircuitState.OPEN
        stale_release = breaker.release_probe(permit=admitted)
        assert stale_release is None
        clock.advance(5.0)
        assert breaker.acquire().is_probe is True

    def test_release_probe_lets_the_next_caller_probe(self):
        clock = ManualClock()
        breaker = CircuitBreaker(name="provider", failure_threshold=1, reset_timeout=1.0, clock=clock)
        breaker.record_failure()
        clock.advance(1.0)
        probe = breaker.acquire()
        breaker.release_probe(probe)
        assert breaker.acquire().is_probe is True

    def test_call_returns_the_real_value_and_counts_failures(self):
        clock = ManualClock()
        breaker = CircuitBreaker(name="provider", failure_threshold=2, reset_timeout=1.0, clock=clock)
        assert breaker.call(lambda: None) is None
        with pytest.raises(TransientError):
            breaker.call(_always_transient())
        with pytest.raises(TransientError):
            breaker.call(_always_transient())
        assert breaker.state() is CircuitState.OPEN
        with pytest.raises(CircuitOpenError):
            breaker.call(lambda: "never runs")
        breaker.reset("operator")
        assert breaker.state() is CircuitState.CLOSED
        assert "operator" in breaker.state_reason()

    def test_state_is_per_instance(self):
        clock = ManualClock()
        first = CircuitBreaker(name="a", failure_threshold=1, reset_timeout=1.0, clock=clock)
        second = CircuitBreaker(name="b", failure_threshold=1, reset_timeout=1.0, clock=clock)
        first.record_failure()
        assert first.state() is CircuitState.OPEN
        assert second.state() is CircuitState.CLOSED
        assert first.snapshot().name == "a"
        assert second.snapshot().name == "b"

    def test_validation(self):
        clock = ManualClock()
        with pytest.raises(ValueError):
            CircuitBreaker(failure_threshold=0, clock=clock)
        with pytest.raises(ValueError):
            CircuitBreaker(reset_timeout=-1.0, clock=clock)


# ---------------------------------------------------------------------------
# convergence
# ---------------------------------------------------------------------------


class TestConvergence:
    def test_healthy_sequence_never_intervenes_but_still_hits_the_cap(self):
        guard = ConvergenceGuard()
        for index in range(guard.max_attempts):
            decision = guard.observe(action=f"step-{index}", state="ok", progressed=True)
            assert decision.status is GuardStatus.CONTINUE
            assert decision.intervention is Intervention.NONE
        assert guard.rung == -1
        assert guard.exhausted is False
        # A loop that keeps "progressing" forever is still bounded: the attempt
        # cap is what stops a healing loop from running until the host dies.
        capped = guard.observe(action="one-too-many", state="ok", progressed=True)
        assert capped.status is GuardStatus.EXHAUSTED
        assert capped.signal is ThrashSignal.CAP_EXCEEDED
        assert capped.intervention is Intervention.TERMINATE
        assert guard.exhausted is True

    def test_repeated_action_signal(self):
        guard = ConvergenceGuard(repeat_threshold=3)
        first = guard.observe(action="restart_api", state="down", progressed=False)
        assert first.status is GuardStatus.CONTINUE
        second = guard.observe(action="restart_api", state="down", progressed=False)
        assert second.status is GuardStatus.CONTINUE
        third = guard.observe(action="restart_api", state="down", progressed=False)
        assert third.status is GuardStatus.INTERVENE
        assert third.signal is ThrashSignal.REPEATED_ACTION
        assert third.intervention is ESCALATION_ORDER[0]
        assert "repeated_action" in third.reason

    def test_ping_pong_state_cycle_is_detected(self):
        guard = ConvergenceGuard(repeat_threshold=99, no_progress_threshold=99)
        guard.observe(action="read", state="A", progressed=True)
        guard.observe(action="write", state="B", progressed=True)
        decision = guard.observe(action="read", state="A", progressed=True)
        assert decision.signal is ThrashSignal.PING_PONG
        assert decision.status is GuardStatus.INTERVENE

    def test_alternating_failure_signatures_are_detected(self):
        guard = ConvergenceGuard(repeat_threshold=99, no_progress_threshold=99)
        guard.observe(action="one", state="s1", failure_signature="ValueError: alpha", progressed=True)
        guard.observe(action="two", state="s2", failure_signature="KeyError: beta", progressed=True)
        decision = guard.observe(action="three", state="s3", failure_signature="ValueError: alpha", progressed=True)
        assert decision.signal is ThrashSignal.ALTERNATING_FAILURES
        assert decision.status is GuardStatus.INTERVENE

    def test_no_progress_run_is_detected(self):
        guard = ConvergenceGuard(repeat_threshold=99, no_progress_threshold=3)
        guard.observe(action="one", state="s1", progressed=False)
        guard.observe(action="two", state="s2", progressed=False)
        decision = guard.observe(action="three", state="s3", progressed=False)
        assert decision.signal is ThrashSignal.NO_PROGRESS
        assert decision.status is GuardStatus.INTERVENE

    def test_escalation_order_is_fixed_and_monotonic(self):
        guard = ConvergenceGuard(repeat_threshold=1, max_attempts=50, max_reflections=50, max_replans=50)
        seen: list[Intervention] = []
        evidence_request = ""
        for _ in range(len(ESCALATION_ORDER)):
            decision = guard.observe(action="retry_same_thing", state="broken", progressed=False)
            seen.append(decision.intervention)
            if decision.intervention is Intervention.REQUEST_DISCRIMINATING_EVIDENCE:
                evidence_request = decision.evidence_request
        assert seen == list(ESCALATION_ORDER)
        assert evidence_request
        assert guard.exhausted is True
        # A pinned terminal state: later observations cannot re-open the loop.
        after = guard.observe(action="retry_same_thing", state="broken", progressed=False)
        assert after.status is GuardStatus.EXHAUSTED
        assert after.intervention is Intervention.TERMINATE
        assert "already terminated" in after.reason
        assert guard.rung == len(ESCALATION_ORDER) - 1

    def test_attempt_cap_produces_exhausted(self):
        guard = ConvergenceGuard(max_attempts=2)
        guard.observe(action="a", state="s", progressed=False)
        guard.observe(action="a", state="s", progressed=False)
        decision = guard.observe(action="a", state="s", progressed=False)
        assert decision.status is GuardStatus.EXHAUSTED
        assert decision.exhausted is True
        assert decision.signal is ThrashSignal.CAP_EXCEEDED
        assert "attempt cap exceeded" in decision.reason
        assert decision.intervention is Intervention.TERMINATE

    def test_reflection_and_replan_caps_are_independent(self):
        guard = ConvergenceGuard(repeat_threshold=1, max_attempts=50, max_reflections=1, max_replans=50)
        guard.observe(action="a", state="s", kind=ObservationKind.ATTEMPT, progressed=False)
        guard.observe(action="a", state="s", kind=ObservationKind.REFLECTION, progressed=False)
        decision = guard.observe(action="a", state="s", kind=ObservationKind.REFLECTION, progressed=False)
        assert decision.status is GuardStatus.EXHAUSTED
        assert "reflection cap exceeded" in decision.reason
        assert guard.count(ObservationKind.REFLECTION) == 2
        assert guard.count(ObservationKind.REPLAN) == 0

    def test_prohibited_transition_is_refused_and_terminates(self):
        guard = ConvergenceGuard()
        transition = Transition("contract_gate", "contract_gate:disabled", "disable_check", detail="tests go green")
        assert is_prohibited_transition(transition) is True
        assert guard.is_prohibited(transition) is True
        with pytest.raises(ProhibitedTransitionError) as excinfo:
            guard.require_transition_allowed(transition)
        assert excinfo.value.kind == "disable_check"

        benign = Transition("service", "service:restart", "restart_service")
        assert guard.require_transition_allowed(benign) is benign

        decision = guard.observe(action="disable_gate", state="green", progressed=True, transition=transition)
        assert decision.status is GuardStatus.EXHAUSTED
        assert decision.signal is ThrashSignal.PROHIBITED_TRANSITION
        assert decision.intervention is Intervention.TERMINATE
        assert "disable_check" in decision.reason
        assert "contract_gate:disabled" in decision.reason
        assert "disable_check" in PROHIBITED_TRANSITION_KINDS

    def test_snapshot_and_reset(self):
        guard = ConvergenceGuard(repeat_threshold=2)
        guard.observe(action="a", state="s", progressed=False)
        guard.observe(action="a", state="s", progressed=False)
        snapshot = guard.snapshot()
        assert snapshot.observations == 2
        assert snapshot.rung == 0
        assert snapshot.counts["attempt"] == 2
        assert snapshot.caps == {"attempts": 8, "reflections": 2, "replans": 2}
        assert snapshot.prohibited_kinds == PROHIBITED_TRANSITION_KINDS
        guard.reset()
        assert guard.snapshot().rung == -1
        assert guard.count(ObservationKind.ATTEMPT) == 0
        assert guard.history == ()

    def test_history_is_bounded(self):
        guard = ConvergenceGuard(max_history=8, repeat_threshold=99, no_progress_threshold=99)
        for index in range(40):
            guard.observe(action=f"a{index}", state=f"s{index}", progressed=True)
        assert len(guard.history) == 8

    def test_validation(self):
        with pytest.raises(ValueError):
            ConvergenceGuard(ping_pong_window=2)
        with pytest.raises(ValueError):
            ConvergenceGuard(alternating_failure_window=2)
        with pytest.raises(ValueError):
            ConvergenceGuard(max_attempts=0)
        with pytest.raises(ValueError):
            ConvergenceGuard(repeat_threshold=0)


# ---------------------------------------------------------------------------
# budget
# ---------------------------------------------------------------------------


class TestBudget:
    def test_attempt_budget_refuses_cleanly(self):
        clock = ManualClock()
        budget = ResourceBudget(clock=clock, max_attempts=2, label="tests")
        assert budget.begin_attempt() == 1
        assert budget.begin_attempt() == 2
        with pytest.raises(BudgetExhaustedError) as excinfo:
            budget.begin_attempt()
        assert excinfo.value.reason == "attempt_budget_exhausted"
        snapshot = budget.snapshot()
        assert snapshot.attempts_used == 2
        assert snapshot.attempts_remaining == 0
        assert snapshot.exhausted is True
        assert budget.exhausted is True

    def test_cost_budget_refuses_without_a_silent_overshoot(self):
        clock = ManualClock()
        budget = ResourceBudget(clock=clock, max_cost=10.0, cost_meter=FixedCostMeter(4.0))
        assert budget.consume_metered("llm_call") == 4.0
        assert budget.consume_metered("llm_call") == 4.0
        with pytest.raises(BudgetExhaustedError) as excinfo:
            budget.consume_metered("llm_call")
        assert excinfo.value.reason == "cost_budget_exhausted"
        snapshot = budget.snapshot()
        assert snapshot.cost_used == 8.0
        assert snapshot.remaining_cost == 2.0
        assert snapshot.cost_clamped is False  # the refusal changed nothing

    def test_post_hoc_accounting_clamps_and_discloses(self):
        clock = ManualClock()
        budget = ResourceBudget(clock=clock, max_cost=10.0)
        budget.consume(8.0)
        assert budget.record(5.0) == 2.0
        snapshot = budget.snapshot()
        assert snapshot.cost_used == 10.0
        assert snapshot.cost_clamped is True
        assert snapshot.exhausted is True

    def test_wall_clock_budget_expires_on_the_injected_clock(self):
        clock = ManualClock()
        budget = ResourceBudget(clock=clock, wall_clock_timeout=5.0, label="round")
        assert budget.deadline is not None
        assert budget.deadline.expires_at == 5.0
        assert budget.snapshot().seconds_remaining == 5.0
        budget.begin_attempt()
        clock.advance(5.0)
        with pytest.raises(BudgetExhaustedError) as excinfo:
            budget.begin_attempt()
        assert excinfo.value.reason == "wall_clock_budget_exhausted"
        assert budget.snapshot().seconds_remaining == 0.0
        assert budget.snapshot().exhausted is True

    def test_unbounded_budget_never_exhausts(self):
        clock = ManualClock()
        budget = ResourceBudget(clock=clock)
        for _ in range(50):
            budget.begin_attempt()
        snapshot = budget.snapshot()
        assert snapshot.max_attempts is None
        assert snapshot.seconds_remaining is None
        assert snapshot.remaining_cost is None
        assert snapshot.exhausted is False
        # The default meter charges nothing, so a cost-less budget is unbounded.
        assert budget.consume_metered("anything") == 0.0
        assert budget.snapshot().cost_used == 0.0

    def test_check_is_a_non_raising_probe(self):
        clock = ManualClock()
        budget = ResourceBudget(clock=clock, max_attempts=1)
        assert budget.check().exhausted is False
        budget.begin_attempt()
        assert budget.check().exhausted is True

    def test_reset_restores_a_fresh_budget(self):
        clock = ManualClock()
        budget = ResourceBudget(clock=clock, max_attempts=1, max_cost=1.0)
        budget.begin_attempt()
        budget.consume(1.0)
        budget.record(5.0)
        budget.reset()
        snapshot = budget.snapshot()
        assert snapshot.attempts_used == 0
        assert snapshot.cost_used == 0.0
        assert snapshot.cost_clamped is False
        assert budget.begin_attempt() == 1

    def test_validation(self):
        clock = ManualClock()
        with pytest.raises(ValueError):
            ResourceBudget(clock=clock, max_attempts=0)
        with pytest.raises(ValueError):
            ResourceBudget(clock=clock, max_cost=-1.0)
        with pytest.raises(ValueError):
            ResourceBudget(clock=clock, wall_clock_timeout=-1.0)
        with pytest.raises(ValueError):
            FixedCostMeter(-1.0)
        with pytest.raises(TypeError):
            ResourceBudget(clock=clock, cost_meter=object())


# ---------------------------------------------------------------------------
# recovery
# ---------------------------------------------------------------------------


class TestRecovery:
    def _policy(self, attempts: int = 2) -> RetryPolicy:
        return RetryPolicy(attempts=attempts, base_delay=1.0, multiplier=2.0, max_delay=4.0, jitter=HALF_JITTER)

    def test_succeeded_returns_the_real_value(self):
        clock = ManualClock()
        outcome = recover(lambda: "value", policy=self._policy(), clock=clock)
        assert outcome.status is RecoveryStatus.SUCCEEDED
        assert outcome.succeeded is True
        assert outcome.returned is True
        assert outcome.value == "value"
        assert outcome.attempts == 1
        assert outcome.reason
        assert outcome.circuit is None
        assert outcome.budget is None

    def test_returning_none_is_still_a_real_success(self):
        clock = ManualClock()
        outcome = recover(lambda: None, policy=self._policy(), clock=clock)
        assert outcome.status is RecoveryStatus.SUCCEEDED
        assert outcome.succeeded is True
        assert outcome.value is None
        assert outcome.returned is True
        assert outcome.value is not NO_VALUE

    def test_not_retryable_records_the_exception_type(self):
        clock = ManualClock()
        boom = ValueError("programming error")
        outcome = recover(
            lambda: (_ for _ in ()).throw(boom),
            policy=self._policy(attempts=5),
            clock=clock,
        )
        assert outcome.status is RecoveryStatus.NOT_RETRYABLE
        assert outcome.succeeded is False
        assert outcome.returned is False
        assert outcome.value is NO_VALUE
        assert outcome.error_type == "ValueError"
        assert outcome.last_error is boom
        assert outcome.attempts == 1
        assert clock.sleeps == []

    def test_unexpected_base_exception_propagates(self):
        clock = ManualClock()
        with pytest.raises(KeyboardInterrupt):
            recover(
                lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
                policy=self._policy(),
                clock=clock,
            )

    def test_deadline_signal_from_the_operation_is_a_budget_outcome(self):
        clock = ManualClock()
        outcome = recover(
            lambda: (_ for _ in ()).throw(DeadlineExceeded("caller deadline", at=1.0)),
            policy=self._policy(attempts=5),
            clock=clock,
        )
        assert outcome.status is RecoveryStatus.BUDGET_EXHAUSTED
        assert outcome.error_type == "DeadlineExceeded"
        assert outcome.attempts == 1

    def test_base_exception_releases_the_idempotency_claim(self):
        clock = ManualClock()
        registry = IdempotencyRegistry(clock=clock, ttl=600.0)
        key = IdempotencyKey("runs", "interrupt")
        with pytest.raises(KeyboardInterrupt):
            recover(
                lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
                policy=self._policy(),
                clock=clock,
                registry=registry,
                key=key,
            )
        assert registry.peek(key) is None
        calls: list[int] = []
        second = recover(
            lambda: calls.append(1) or "ok",
            policy=self._policy(),
            clock=clock,
            registry=registry,
            key=key,
        )
        assert second.status is RecoveryStatus.SUCCEEDED
        assert calls == [1]

    def test_retryable_failures_exhaust_the_guard(self):
        clock = ManualClock()
        breaker = CircuitBreaker(name="provider", failure_threshold=99, reset_timeout=1.0, clock=clock)
        outcome = recover(
            _always_transient(),
            policy=self._policy(attempts=2),
            clock=clock,
            breaker=breaker,
            guard=ConvergenceGuard(max_attempts=2),
        )
        assert outcome.status is RecoveryStatus.EXHAUSTED
        assert "convergence guard terminated" in outcome.reason
        assert outcome.attempts == 6  # 3 rounds x 2 attempts
        assert len(clock.sleeps) == 3  # never sleeps after a round's last attempt
        assert outcome.interventions == (Intervention.TERMINATE,)
        assert outcome.decisions
        assert outcome.decisions[-1].signal is ThrashSignal.CAP_EXCEEDED
        assert outcome.budget is None
        assert outcome.circuit is not None
        assert outcome.circuit.failure_count == 6

    def test_attempt_budget_produces_budget_exhausted(self):
        clock = ManualClock()
        budget = ResourceBudget(clock=clock, max_attempts=1)
        outcome = recover(
            _always_transient(),
            policy=self._policy(attempts=5),
            clock=clock,
            budget=budget,
        )
        assert outcome.status is RecoveryStatus.BUDGET_EXHAUSTED
        assert outcome.attempts == 2
        assert outcome.error_type in {"BudgetExhaustedError", "TransientError"}
        assert outcome.budget is not None
        assert outcome.budget.max_attempts == 1

    def test_wall_clock_budget_produces_budget_exhausted(self):
        clock = ManualClock()
        budget = ResourceBudget(clock=clock, wall_clock_timeout=0.5)
        outcome = recover(
            _always_transient(),
            policy=RetryPolicy(attempts=9, base_delay=10.0, jitter=NoJitter()),
            clock=clock,
            budget=budget,
        )
        assert outcome.status is RecoveryStatus.BUDGET_EXHAUSTED
        assert outcome.attempts == 1
        assert clock.sleeps == []

    def test_circuit_open_is_reported(self):
        clock = ManualClock()
        breaker = CircuitBreaker(name="provider", failure_threshold=1, reset_timeout=60.0, clock=clock)
        breaker.record_failure()
        calls: list[int] = []
        outcome = recover(
            lambda: calls.append(1) or "never",
            policy=self._policy(attempts=5),
            clock=clock,
            breaker=breaker,
        )
        assert outcome.status is RecoveryStatus.CIRCUIT_OPEN
        # The refused admission is still an attempt on the trail (aborted), and
        # the operation itself never ran.
        assert outcome.attempts == 1
        assert calls == []
        assert outcome.trail.last().outcome is AttemptOutcome.ABORTED  # type: ignore[union-attr]
        assert outcome.circuit is not None
        assert outcome.circuit.state is CircuitState.OPEN

    def test_circuit_failure_count_is_recorded_for_real_failures(self):
        clock = ManualClock()
        breaker = CircuitBreaker(name="provider", failure_threshold=2, reset_timeout=1.0, clock=clock)
        outcome = recover(
            _always_transient(),
            policy=self._policy(attempts=1),
            clock=clock,
            breaker=breaker,
        )
        assert outcome.status is RecoveryStatus.EXHAUSTED
        assert outcome.circuit is not None
        assert outcome.circuit.failure_count == 1

    def test_abort_signal_produces_aborted(self):
        clock = ManualClock()
        state = {"calls": 0}

        def operation() -> str:
            state["calls"] += 1
            raise _transient()

        outcome = recover(
            operation,
            policy=self._policy(attempts=5),
            clock=clock,
            abort=lambda: state["calls"] >= 1,
        )
        assert outcome.status is RecoveryStatus.ABORTED
        assert outcome.attempts == 1

    def test_abort_before_the_first_round_never_calls_the_operation(self):
        clock = ManualClock()
        calls: list[int] = []
        outcome = recover(
            lambda: calls.append(1) or "never",
            policy=self._policy(),
            clock=clock,
            abort=lambda: True,
        )
        assert outcome.status is RecoveryStatus.ABORTED
        assert calls == []
        assert outcome.attempts == 0

    def test_escalation_hooks_run_in_ladder_order_and_delegate_succeeds(self):
        clock = ManualClock()
        applied: list[str] = []
        outcome = recover(
            _always_transient(),
            policy=self._policy(attempts=1),
            clock=clock,
            guard=ConvergenceGuard(repeat_threshold=1, max_attempts=50, max_reflections=50, max_replans=50),
            on_change_context=lambda step: applied.append(f"context:round{step.round_index}"),
            on_evidence=lambda: applied.append("evidence"),
            on_change_strategy=lambda: applied.append("strategy"),
            delegate=lambda: "delegated",
        )
        assert applied == ["context:round3", "evidence", "strategy"]
        assert outcome.status is RecoveryStatus.SUCCEEDED
        assert outcome.succeeded is True
        assert outcome.value == "delegated"
        assert outcome.interventions == (
            Intervention.WARN,
            Intervention.CHANGE_CONTEXT,
            Intervention.REQUEST_DISCRIMINATING_EVIDENCE,
            Intervention.CHANGE_STRATEGY,
            Intervention.DELEGATE,
        )

    def test_missing_hooks_and_missing_delegate_are_disclosed(self):
        clock = ManualClock()
        outcome = recover(
            _always_transient(),
            policy=self._policy(attempts=1),
            clock=clock,
            guard=ConvergenceGuard(repeat_threshold=1, max_attempts=50, max_reflections=50, max_replans=50),
        )
        assert outcome.status is RecoveryStatus.EXHAUSTED
        assert "no delegate supplied" in outcome.reason
        # One disclosure per rung the caller left unhandled, including DELEGATE.
        assert len(outcome.notes) == 4
        assert any("change_context" in note for note in outcome.notes)
        assert any("request_discriminating_evidence" in note for note in outcome.notes)
        assert any("change_strategy" in note for note in outcome.notes)
        assert any("DELEGATE" in note for note in outcome.notes)

    def test_a_failing_hook_ends_the_ladder_without_a_fake_success(self):
        clock = ManualClock()

        def broken_hook() -> None:
            raise RuntimeError("hook exploded")

        outcome = recover(
            _always_transient(),
            policy=self._policy(attempts=1),
            clock=clock,
            guard=ConvergenceGuard(repeat_threshold=1, max_attempts=50),
            on_change_context=broken_hook,
        )
        assert outcome.status is RecoveryStatus.EXHAUSTED
        assert "change_context hook failed" in outcome.reason
        assert outcome.error_type == "RuntimeError"
        assert outcome.succeeded is False

    def test_a_failing_delegate_is_reported_not_swallowed(self):
        clock = ManualClock()

        def broken_delegate() -> str:
            raise KeyError("no capacity")

        outcome = recover(
            _always_transient(),
            policy=self._policy(attempts=1),
            clock=clock,
            guard=ConvergenceGuard(repeat_threshold=1, max_attempts=50, max_reflections=50, max_replans=50),
            on_change_context=lambda: None,
            on_evidence=lambda: None,
            on_change_strategy=lambda: None,
            delegate=broken_delegate,
        )
        assert outcome.status is RecoveryStatus.EXHAUSTED
        assert "delegate failed" in outcome.reason
        assert outcome.error_type == "KeyError"

    def test_disabled_is_a_strict_pass_through(self):
        clock = ManualClock()
        breaker = CircuitBreaker(name="provider", failure_threshold=1, reset_timeout=1.0, clock=clock)
        outcome = recover(lambda: "value", policy=self._policy(attempts=5), clock=clock, breaker=breaker, enabled=False)
        assert outcome.status is RecoveryStatus.SUCCEEDED
        assert outcome.value == "value"
        assert outcome.attempts == 1
        assert "disabled pass-through" in outcome.reason
        assert clock.sleeps == []
        assert breaker.state() is CircuitState.CLOSED

        def boom() -> str:
            raise ValueError("caller error")

        failed = recover(boom, policy=self._policy(attempts=5), clock=clock, enabled=False)
        assert failed.status is RecoveryStatus.NOT_RETRYABLE
        assert failed.error_type == "ValueError"
        assert failed.attempts == 1

    def test_operation_may_accept_the_recovery_step(self):
        clock = ManualClock()
        seen: list[RecoveryStep] = []

        def operation(step: RecoveryStep) -> str:
            seen.append(step)
            return "ok"

        outcome = recover(operation, policy=self._policy(), clock=clock)
        assert outcome.succeeded is True
        assert len(seen) == 1
        assert seen[0].round_index == 1
        assert seen[0].rung == -1
        assert seen[0].decision.intervention is Intervention.NONE

    def test_observer_receives_every_attempt_record(self):
        clock = ManualClock()
        observed = []
        outcome = recover(
            _always_transient(),
            policy=self._policy(attempts=2),
            clock=clock,
            guard=ConvergenceGuard(max_attempts=1),
            observer=observed.append,
        )
        assert outcome.status is RecoveryStatus.EXHAUSTED
        assert len(observed) == outcome.attempts == 4
        # ``AttemptRecord.attempt`` is the index inside one retry call, so it
        # restarts per round; ``outcome.attempts`` is the ladder-wide total.
        assert [record.attempt for record in observed] == [1, 2, 1, 2]
        assert [record.outcome for record in observed] == [
            AttemptOutcome.RETRY_SCHEDULED,
            AttemptOutcome.EXHAUSTED,
            AttemptOutcome.RETRY_SCHEDULED,
            AttemptOutcome.EXHAUSTED,
        ]
        assert all(record.error_type == "TransientError" for record in observed)

    def test_idempotency_replays_the_first_outcome_and_blocks_duplicates(self):
        clock = ManualClock()
        registry = IdempotencyRegistry(clock=clock, ttl=600.0)
        key = IdempotencyKey("runs", "recover-me")
        calls: list[int] = []

        def operation() -> str:
            calls.append(1)
            return "charged"

        first = recover(operation, policy=self._policy(), clock=clock, registry=registry, key=key)
        assert first.status is RecoveryStatus.SUCCEEDED
        second = recover(operation, policy=self._policy(), clock=clock, registry=registry, key=key)
        assert second.status is RecoveryStatus.ABORTED
        assert "idempotency duplicate" in second.reason
        assert calls == [1]

    def test_idempotency_releases_a_failed_ladder_for_a_deliberate_retry(self):
        clock = ManualClock()
        registry = IdempotencyRegistry(clock=clock, ttl=600.0)
        key = IdempotencyKey("runs", "recover-fail")
        calls: list[int] = []

        def operation() -> str:
            calls.append(1)
            raise ValueError("nope")

        first = recover(operation, policy=self._policy(), clock=clock, registry=registry, key=key)
        second = recover(operation, policy=self._policy(), clock=clock, registry=registry, key=key)
        assert first.status is RecoveryStatus.NOT_RETRYABLE
        assert second.status is RecoveryStatus.NOT_RETRYABLE
        assert calls == [1, 1]

    def test_in_flight_key_is_reported_as_aborted(self):
        clock = ManualClock()
        registry = IdempotencyRegistry(clock=clock, ttl=600.0)
        key = IdempotencyKey("runs", "in-flight")
        held = registry.begin(key)
        assert held.status is IdempotencyStatus.ACQUIRED
        calls: list[int] = []
        outcome = recover(
            lambda: calls.append(1) or "never",
            policy=self._policy(),
            clock=clock,
            registry=registry,
            key=key,
        )
        assert outcome.status is RecoveryStatus.ABORTED
        assert "in_progress" in outcome.reason
        assert calls == []

    def test_no_guard_runs_exactly_one_round(self):
        clock = ManualClock()
        calls: list[int] = []

        def operation() -> str:
            calls.append(1)
            raise _transient()

        outcome = recover(operation, policy=self._policy(attempts=2), clock=clock)
        assert outcome.status is RecoveryStatus.EXHAUSTED
        assert "one round only" in outcome.reason
        assert calls == [1, 1]
        assert outcome.attempts == 2


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

_FULL_CONFIG = ResilienceConfig(
    enabled=True,
    retry=RetrySettings(attempts=7, base_delay_seconds=1.5, multiplier=3.0, max_delay_seconds=9.0, jitter="none"),
    circuit=CircuitSettings(failure_threshold=4, reset_timeout_seconds=11.0),
    convergence=ConvergenceSettings(
        repeat_threshold=2,
        ping_pong_window=5,
        no_progress_threshold=6,
        alternating_failure_window=4,
        max_attempts=9,
        max_reflections=5,
        max_replans=4,
        history_limit=32,
    ),
    budget=BudgetSettings(max_attempts=5, wall_clock_seconds=30.0, max_cost=12.5),
    idempotency=IdempotencySettings(ttl_seconds=120.0),
)


class TestConfig:
    def test_defaults_are_off(self):
        config = ResilienceConfig()
        assert config.enabled is False
        assert config.retry.attempts == 3
        assert config.circuit.failure_threshold == 3
        assert config.convergence.max_attempts == 8
        assert config.budget.max_attempts is None
        assert config.idempotency.ttl_seconds == 3600.0

    def test_every_config_field_has_a_real_reader(self):
        clock = ManualClock()
        config = _FULL_CONFIG

        policy = config.retry_policy()
        assert policy.attempts == config.retry.attempts
        assert policy.base_delay == config.retry.base_delay_seconds
        assert policy.multiplier == config.retry.multiplier
        assert policy.max_delay == config.retry.max_delay_seconds
        assert isinstance(policy.jitter, NoJitter)
        assert isinstance(ResilienceConfig(retry=RetrySettings(jitter="full")).retry_policy().jitter, FullJitter)

        breaker = config.circuit_breaker(clock, name="probe")
        assert breaker.failure_threshold == config.circuit.failure_threshold
        assert breaker.reset_timeout == config.circuit.reset_timeout_seconds
        assert breaker.name == "probe"

        guard = config.convergence_guard()
        assert guard.repeat_threshold == config.convergence.repeat_threshold
        assert guard.ping_pong_window == config.convergence.ping_pong_window
        assert guard.no_progress_threshold == config.convergence.no_progress_threshold
        assert guard.alternating_failure_window == config.convergence.alternating_failure_window
        assert guard.max_attempts == config.convergence.max_attempts
        assert guard.max_reflections == config.convergence.max_reflections
        assert guard.max_replans == config.convergence.max_replans
        assert guard.history_limit == config.convergence.history_limit

        budget = config.resource_budget(clock, cost_meter=FixedCostMeter(1.0))
        snapshot = budget.snapshot()
        assert snapshot.max_attempts == config.budget.max_attempts
        assert snapshot.max_cost == config.budget.max_cost
        assert snapshot.seconds_remaining == config.budget.wall_clock_seconds
        assert budget.deadline is not None
        assert budget.deadline.expires_at == clock.now() + config.budget.wall_clock_seconds
        assert budget.consume_metered("step") == 1.0  # the meter seam is wired

        registry = config.idempotency_registry(clock)
        assert registry.ttl == config.idempotency.ttl_seconds

        kwargs = config.recovery_kwargs(clock)
        assert kwargs["enabled"] is config.enabled
        assert kwargs["policy"] == policy
        assert kwargs["guard"].max_attempts == config.convergence.max_attempts  # type: ignore[union-attr]
        assert kwargs["breaker"].failure_threshold == config.circuit.failure_threshold  # type: ignore[union-attr]
        assert kwargs["budget"].snapshot().max_cost == config.budget.max_cost  # type: ignore[union-attr]

    def test_disabled_config_makes_recovery_a_pass_through(self):
        clock = ManualClock()
        config = ResilienceConfig()
        outcome = recover(lambda: "value", clock=clock, **config.recovery_kwargs(clock))
        assert outcome.status is RecoveryStatus.SUCCEEDED
        assert outcome.value == "value"
        assert "disabled pass-through" in outcome.reason
        assert clock.sleeps == []

    def test_enabled_config_drives_the_ladder(self):
        """Every knob in the config is visible in the ladder's behaviour."""
        clock = ManualClock()
        config = _FULL_CONFIG
        calls: list[int] = []

        def operation() -> str:
            calls.append(1)
            raise _transient()

        outcome = recover(operation, clock=clock, **config.recovery_kwargs(clock))
        # retry.attempts=7 with base 1.5 / multiplier 3 / cap 9 gives these
        # waits; the breaker threshold (4) opens before the budget (5 attempts).
        assert clock.sleeps == [1.5, 4.5, 9.0, 9.0]
        assert calls == [1, 1, 1, 1]
        assert outcome.status is RecoveryStatus.CIRCUIT_OPEN
        assert outcome.attempts == 5  # the fifth admission was refused, not executed
        assert outcome.circuit is not None
        assert outcome.circuit.failure_count == config.circuit.failure_threshold
        assert outcome.circuit.reset_timeout == config.circuit.reset_timeout_seconds

    def test_validation_and_immutability(self):
        with pytest.raises(ValidationError):
            ResilienceConfig(unknown_knob=1)
        with pytest.raises(ValidationError):
            RetrySettings(attempts=0)
        with pytest.raises(ValidationError):
            CircuitSettings(failure_threshold=0)
        with pytest.raises(ValidationError):
            ConvergenceSettings(ping_pong_window=2)
        with pytest.raises(ValidationError):
            BudgetSettings(max_attempts=0)
        with pytest.raises(ValidationError):
            IdempotencySettings(ttl_seconds=0)
        with pytest.raises(ValidationError):
            RetrySettings(jitter="wild")
        config = ResilienceConfig()
        with pytest.raises(ValidationError):
            config.enabled = True  # frozen


# ---------------------------------------------------------------------------
# termination
# ---------------------------------------------------------------------------


class TestTermination:
    def test_no_policy_space_loops_forever(self):
        """Fuzz the policy space: every path must terminate inside its bound.

        Bounds asserted (each is a documented, monotone counter):
        * attempts  -> ``policy.attempts`` per round;
        * rounds    -> ``guard.max_attempts + 1`` observations before the cap;
        * budget    -> ``budget.max_attempts`` when configured;
        * wall clock-> the deadline handed to the retry loop.
        """
        combinations = itertools.product(
            (1, 2, 3),  # policy attempts
            (0.0, 0.5),  # base delay
            (None, 0.0, 0.25),  # budget wall clock
            (1, 2),  # circuit failure threshold
            (1, 2, 3),  # guard max attempts
            (None, 1, 4),  # budget max attempts
        )
        checked = 0
        for attempts, base_delay, wall_clock, threshold, guard_max, budget_max in combinations:
            clock = ManualClock()
            policy = RetryPolicy(attempts=attempts, base_delay=base_delay, multiplier=2.0, max_delay=base_delay * 4 + 1.0)
            breaker = CircuitBreaker(name="fuzz", failure_threshold=threshold, reset_timeout=1.0, clock=clock)
            budget = ResourceBudget(clock=clock, max_attempts=budget_max, wall_clock_timeout=wall_clock)
            guard = ConvergenceGuard(max_attempts=guard_max, max_reflections=guard_max, max_replans=guard_max)
            calls = {"n": 0}

            def operation() -> str:
                calls["n"] += 1
                raise _transient("fuzz")

            outcome = recover(operation, policy=policy, clock=clock, breaker=breaker, budget=budget, guard=guard)

            round_bound = attempts * (guard_max + 1)
            assert calls["n"] <= round_bound, (attempts, base_delay, wall_clock, threshold, guard_max, budget_max, calls)
            if budget_max is not None:
                assert calls["n"] <= budget_max, (budget_max, calls)
            assert len(clock.sleeps) <= round_bound
            assert math.isfinite(clock.now())
            assert clock.now() <= round_bound * (base_delay * 4 + 1.0) + (wall_clock or 0.0)
            assert outcome.status in set(RecoveryStatus)
            assert outcome.succeeded is False  # the operation never returned
            checked += 1
        assert checked == 3 * 2 * 3 * 2 * 3 * 3

    def test_guard_cannot_be_reopened_after_termination(self):
        guard = ConvergenceGuard(max_attempts=1)
        guard.observe(action="a", state="s", progressed=False)
        terminated = guard.observe(action="a", state="s", progressed=False)
        assert terminated.status is GuardStatus.EXHAUSTED
        rung = guard.rung
        for _ in range(20):
            again = guard.observe(action="a", state="s", progressed=False)
            assert again.status is GuardStatus.EXHAUSTED
            assert guard.rung == rung  # a state transition that cannot repeat

    def test_error_taxonomy_is_catchable_as_one_base(self):
        for error in (
            DeadlineExceeded("x"),
            RetryExhaustedError("x", attempts=1),
            RetryAbortedError("x"),
            CircuitOpenError("x"),
            BudgetExhaustedError("x"),
            IdempotencyError("x"),
            ProhibitedTransitionError("x"),
            TransientError("x"),
        ):
            assert isinstance(error, ResilienceError)


# ---------------------------------------------------------------------------
# package facade
# ---------------------------------------------------------------------------


class TestPackageFacade:
    def test_lazy_exports_resolve(self):
        assert set(resilience_package.__all__) >= {
            "Clock",
            "ConvergenceGuard",
            "CircuitBreaker",
            "IdempotencyRegistry",
            "ManualClock",
            "RecoveryStatus",
            "ResilienceConfig",
            "ResourceBudget",
            "RetryPolicy",
            "recover",
            "retry_call",
        }
        assert resilience_package.__all__ == sorted(resilience_package.__all__)
        assert resilience_package.recover is recover
        assert resilience_package.ManualClock is ManualClock
        assert "ConvergenceGuard" in dir(resilience_package)
        with pytest.raises(AttributeError):
            resilience_package.not_a_real_export  # noqa: B018 - the lookup itself is the assertion

    def test_every_table_entry_imports(self):
        for name in resilience_package.__all__:
            assert getattr(resilience_package, name) is not None

    def test_lazy_installer_is_installed_once(self):
        assert getattr(resilience_package.__getattr__, "_lazy_exports", False) is True
