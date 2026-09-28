"""Process supervision: a restart policy that cannot loop forever.

The property under test is the one the durable-runtime contract names: *never
create a blind infinite restart loop*. Concretely, that means a backend which
cannot start must end in a reported, stopped state rather than in an endless
burn, and the shape that actually kills machines — crashing once an hour,
forever — must be caught even though no consecutive-failure counter would see it.
"""

from __future__ import annotations

import asyncio
import sys
import time

import pytest

from alpha.runtime.resilience.clock import ManualClock
from alpha.runtime.resilience.retry import NoJitter, RetryPolicy
from alpha.runtime.supervisor.policy import (
    RestartAction,
    RestartLedger,
    SupervisorPolicy,
    SupervisorReason,
)


def make_policy(**kwargs: object) -> SupervisorPolicy:
    """A policy with deterministic (un-jittered) backoff unless asked otherwise."""
    kwargs.setdefault("backoff", RetryPolicy(attempts=64, base_delay=1.0, multiplier=2.0, max_delay=60.0, jitter=NoJitter()))
    return SupervisorPolicy(**kwargs)  # type: ignore[arg-type]


def crash_ledger(policy: SupervisorPolicy | None = None, *, clock: ManualClock | None = None) -> tuple[RestartLedger, ManualClock]:
    the_clock = clock or ManualClock()
    return RestartLedger(policy or make_policy(), clock=the_clock), the_clock


def instant_crash(ledger: RestartLedger, clock: ManualClock, reason: SupervisorReason = SupervisorReason.CRASHED):  # type: ignore[no-untyped-def]
    """Record a child that died the moment it was started.

    ``started_at`` must be the *current* clock reading. Passing a fixed earlier
    value would report the growing gap as uptime, and a crash-loop test would
    then see every crash as a long healthy run that ends the episode.
    """
    return ledger.record_end(reason=reason, started_at=clock.now())


class TestPolicyValidation:
    def test_a_supervisor_that_may_never_restart_is_not_a_supervisor(self) -> None:
        with pytest.raises(ValueError, match="restart_budget"):
            SupervisorPolicy(restart_budget=0)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"restart_window_seconds": 0.0},
            {"healthy_run_seconds": -1.0},
            {"safe_mode_attempts": 0},
            {"health_timeout_seconds": 0.0},
        ],
    )
    def test_out_of_range_values_are_refused(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            SupervisorPolicy(**kwargs)  # type: ignore[arg-type]

    def test_backoff_is_reused_from_the_resilience_kit_not_reimplemented(self) -> None:
        policy = make_policy()
        assert isinstance(policy.backoff, RetryPolicy)
        assert policy.backoff_delay(1) == 1.0
        assert policy.backoff_delay(2) == 2.0
        assert policy.backoff_delay(3) == 4.0
        assert policy.backoff_delay(20) == 60.0, "backoff must stay capped"

    def test_jitter_is_injectable_so_the_schedule_is_pinned(self) -> None:
        policy = make_policy(backoff=RetryPolicy(attempts=64, base_delay=10.0, multiplier=2.0, max_delay=100.0, jitter=lambda nominal, attempt: nominal / 2))
        assert policy.backoff_delay(1) == 5.0


class TestReasonAccounting:
    def test_a_normal_exit_spends_no_budget(self) -> None:
        """An operator stopping the service must not burn the crash budget."""
        assert SupervisorReason.NORMAL.counts_against_budget is False

    @pytest.mark.parametrize("reason", [SupervisorReason.CRASHED, SupervisorReason.HEALTH_FAILED, SupervisorReason.SPAWN_FAILED])
    def test_every_abnormal_ending_spends_budget(self, reason: SupervisorReason) -> None:
        assert reason.counts_against_budget is True


class TestBudgetAndBackoff:
    def test_a_single_crash_is_restarted_after_a_backoff(self) -> None:
        ledger, clock = crash_ledger()
        decision = instant_crash(ledger, clock)
        assert decision.action is RestartAction.RESTART
        assert decision.delay_seconds == 1.0
        assert decision.restarts_in_window == 1
        assert decision.crash_loop is False

    def test_the_backoff_grows_with_each_failure(self) -> None:
        ledger, clock = crash_ledger()
        delays = [instant_crash(ledger, clock).delay_seconds for _ in range(3)]
        assert delays == [1.0, 2.0, 4.0]

    def test_restarts_are_allowed_up_to_the_budget(self) -> None:
        policy = make_policy(restart_budget=3, restart_window_seconds=300.0, safe_mode_enabled=False)
        ledger, clock = crash_ledger(policy)
        actions = [instant_crash(ledger, clock).action for _ in range(3)]
        assert actions == [RestartAction.RESTART, RestartAction.RESTART, RestartAction.RESTART]

    def test_the_window_refills_with_time(self) -> None:
        """A service that crashes rarely must not exhaust a window it has already served."""
        policy = make_policy(restart_budget=2, restart_window_seconds=60.0, safe_mode_enabled=False, healthy_run_seconds=60.0)
        ledger, clock = crash_ledger(policy)
        assert instant_crash(ledger, clock).action is RestartAction.RESTART
        assert instant_crash(ledger, clock).action is RestartAction.RESTART
        clock.advance(61.0)
        assert ledger.restarts_in_window() == 0
        assert instant_crash(ledger, clock).action is RestartAction.RESTART

    def test_a_crash_once_an_hour_forever_is_eventually_caught(self) -> None:
        """The shape a consecutive-failure breaker and an attempt ceiling both miss."""
        policy = make_policy(restart_budget=5, restart_window_seconds=3600.0, safe_mode_enabled=False, healthy_run_seconds=60.0)
        ledger, clock = crash_ledger(policy)
        actions = []
        for _ in range(12):
            actions.append(instant_crash(ledger, clock).action)
            clock.advance(600.0)  # every ten minutes: never 3 in a row, always inside the hour
        assert RestartAction.GIVE_UP in actions, "twelve crashes in two hours must not all be restarted"
        assert actions.count(RestartAction.GIVE_UP) >= 1

    def test_a_sparse_crash_pattern_is_never_tripped(self) -> None:
        policy = make_policy(restart_budget=2, restart_window_seconds=60.0, safe_mode_enabled=False, healthy_run_seconds=60.0)
        ledger, clock = crash_ledger(policy)
        for _ in range(20):
            assert instant_crash(ledger, clock).action is RestartAction.RESTART
            clock.advance(3600.0)  # hourly: always outside the window


class TestUptimeEndsTheEpisode:
    def test_a_long_healthy_run_returns_the_budget(self) -> None:
        policy = make_policy(restart_budget=2, restart_window_seconds=3600.0, safe_mode_enabled=False, healthy_run_seconds=60.0)
        ledger, clock = crash_ledger(policy)
        instant_crash(ledger, clock)
        instant_crash(ledger, clock)
        assert ledger.restarts_in_window() == 2
        clock.advance(600.0)
        # started_at stays at 0, so this child is measured as having run 600s.
        decision = ledger.record_end(reason=SupervisorReason.CRASHED, started_at=0.0)
        assert decision.action is RestartAction.RESTART
        assert ledger.restarts_in_window() == 1, "the 600s run ends the episode and the budget is returned"

    def test_a_normal_exit_spends_nothing(self) -> None:
        ledger, clock = crash_ledger(make_policy(restart_budget=1, safe_mode_enabled=False))
        decision = ledger.record_end(reason=SupervisorReason.NORMAL, started_at=0.0)
        assert decision.action is RestartAction.RESTART
        assert decision.restarts_in_window == 0

    def test_episodes_are_counted_so_diagnostics_can_be_correlated(self) -> None:
        policy = make_policy(healthy_run_seconds=10.0)
        ledger, clock = crash_ledger(policy)
        instant_crash(ledger, clock)
        assert ledger.episode == 0
        clock.advance(20.0)
        ledger.record_end(reason=SupervisorReason.CRASHED, started_at=0.0)  # measured as a 20s run
        assert ledger.episode == 1


class TestSafeMode:
    def test_a_spent_budget_tries_safe_mode_once(self) -> None:
        policy = make_policy(restart_budget=1, safe_mode_enabled=True, safe_mode_attempts=1)
        ledger, clock = crash_ledger(policy)
        assert instant_crash(ledger, clock).action is RestartAction.RESTART
        decision = instant_crash(ledger, clock)
        assert decision.action is RestartAction.SAFE_MODE
        assert decision.crash_loop is True
        assert decision.safe_mode_attempts_used == 1

    def test_safe_mode_failure_gives_up_rather_than_looping(self) -> None:
        """For a startup failure there is nothing between 'boots' and 'does not'."""
        policy = make_policy(restart_budget=1, safe_mode_enabled=True, safe_mode_attempts=1)
        ledger, clock = crash_ledger(policy)
        instant_crash(ledger, clock)
        assert instant_crash(ledger, clock).action is RestartAction.SAFE_MODE
        final = instant_crash(ledger, clock)
        assert final.action is RestartAction.GIVE_UP
        assert final.should_restart is False

    def test_safe_mode_can_be_disabled(self) -> None:
        policy = make_policy(restart_budget=1, safe_mode_enabled=False)
        ledger, clock = crash_ledger(policy)
        instant_crash(ledger, clock)
        decision = instant_crash(ledger, clock)
        assert decision.action is RestartAction.GIVE_UP
        assert "disabled" in decision.reason

    def test_a_spawn_failure_skips_safe_mode_entirely(self) -> None:
        """The process never ran, so another attempt cannot behave differently."""
        policy = make_policy(restart_budget=1, safe_mode_enabled=True)
        ledger, clock = crash_ledger(policy)
        assert ledger.record_end(reason=SupervisorReason.SPAWN_FAILED, started_at=0.0).action is RestartAction.RESTART
        decision = ledger.record_end(reason=SupervisorReason.SPAWN_FAILED, started_at=0.0)
        assert decision.action is RestartAction.GIVE_UP
        assert decision.safe_mode_attempts_used == 0

    def test_a_healthy_safe_mode_run_ends_the_episode_and_clears_the_latch(self) -> None:
        policy = make_policy(restart_budget=1, safe_mode_enabled=True, healthy_run_seconds=30.0)
        ledger, clock = crash_ledger(policy)
        instant_crash(ledger, clock)
        instant_crash(ledger, clock)
        assert ledger.safe_mode_used == 1
        clock.advance(120.0)
        ledger.record_end(reason=SupervisorReason.CRASHED, started_at=0.0)  # measured as a 120s run
        assert ledger.safe_mode_used == 0, "a healthy run ends the episode, so safe mode is available again later"

    def test_restart_after_a_give_up_can_be_forced_by_the_operator(self) -> None:
        policy = make_policy(restart_budget=1, safe_mode_enabled=False)
        ledger, clock = crash_ledger(policy)
        instant_crash(ledger, clock)
        assert instant_crash(ledger, clock).action is RestartAction.GIVE_UP
        ledger.reset()
        assert instant_crash(ledger, clock).action is RestartAction.RESTART


class TestDecisionShape:
    def test_a_decision_reports_its_evidence(self) -> None:
        policy = make_policy(restart_budget=2)
        ledger, clock = crash_ledger(policy)
        payload = ledger.record_end(reason=SupervisorReason.HEALTH_FAILED, started_at=0.0).to_dict()
        assert payload["action"] == "restart"
        assert payload["restarts_in_window"] == 1
        assert payload["restart_budget"] == 2
        assert "health_failed" in payload["reason"]
        assert "1/2" in payload["reason"], "an operator must be able to see the budget filling up"

    def test_a_give_up_carries_no_delay(self) -> None:
        policy = make_policy(restart_budget=1, safe_mode_enabled=False)
        ledger, clock = crash_ledger(policy)
        instant_crash(ledger, clock)
        assert instant_crash(ledger, clock).delay_seconds == 0.0

    def test_deciding_without_recording_a_failure_is_available(self) -> None:
        ledger, clock = crash_ledger(make_policy())
        assert ledger.decide_next().action is RestartAction.RESTART

    def test_the_recorded_restart_history_is_bounded(self) -> None:
        ledger, clock = crash_ledger(make_policy(restart_budget=10_000, restart_window_seconds=1e9, safe_mode_enabled=False))
        for index in range(1500):
            ledger.record_end(reason=SupervisorReason.CRASHED, started_at=float(index))
        assert len(ledger.restarts()) <= 1024


class TestSupervisorLifecycle:
    """The real spawn/watch path, against trivially-exiting children."""

    @pytest.mark.asyncio
    async def test_a_child_that_exits_zero_is_a_normal_end(self) -> None:
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        supervisor = ProcessSupervisor(name="t", argv=(sys.executable, "-c", "pass"), policy=make_policy(healthy_run_seconds=60.0))
        decision, reason = await supervisor._attempt_once()  # noqa: SLF001 - lifecycle test
        assert reason is SupervisorReason.NORMAL
        assert decision is not None
        assert decision.action is RestartAction.RESTART
        assert decision.restarts_in_window == 0, "a clean exit must not spend the budget"

    @pytest.mark.asyncio
    async def test_a_child_that_exits_nonzero_is_a_crash(self) -> None:
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        supervisor = ProcessSupervisor(name="t", argv=(sys.executable, "-c", "raise SystemExit(3)"), policy=make_policy(healthy_run_seconds=60.0))
        decision, reason = await supervisor._attempt_once()  # noqa: SLF001
        assert reason is SupervisorReason.CRASHED
        assert decision is not None
        assert decision.restarts_in_window == 1

    @pytest.mark.asyncio
    async def test_an_unlaunchable_command_is_a_spawn_failure(self) -> None:
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        supervisor = ProcessSupervisor(name="t", argv=("alpha-no-such-binary-xyzzy",), policy=make_policy())
        decision, reason = await supervisor._attempt_once()  # noqa: SLF001
        assert reason is SupervisorReason.SPAWN_FAILED
        assert decision is not None
        assert "not found" in supervisor.diagnostics().where_it_failed

    @pytest.mark.asyncio
    async def test_a_child_that_exits_zero_but_never_becomes_healthy_is_a_health_failure(self) -> None:
        """A process that is alive but not serving is worse than one that exited."""
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        supervisor = ProcessSupervisor(
            name="t",
            argv=(sys.executable, "-c", "import time; time.sleep(5)"),
            policy=make_policy(health_timeout_seconds=0.2, healthy_run_seconds=60.0),
            health_check=lambda: False,
        )
        task = supervisor._attempt_once()  # noqa: SLF001
        decision, reason = await task
        assert reason is SupervisorReason.HEALTH_FAILED
        assert decision is not None
        assert decision.restarts_in_window == 1, "a start that never became healthy spends budget like a crash"

    @pytest.mark.asyncio
    async def test_a_passing_health_check_keeps_the_start_healthy(self) -> None:
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        supervisor = ProcessSupervisor(
            name="t",
            argv=(sys.executable, "-c", "import time; time.sleep(30)"),
            policy=make_policy(health_timeout_seconds=5.0, healthy_run_seconds=60.0),
            health_check=lambda: True,
        )
        task = asyncio.create_task(supervisor._attempt_once())  # noqa: SLF001
        try:
            # Explicit synchronization on the spawn boundary, not a sleep.
            assert await supervisor.wait_spawned(timeout=10.0) is True
            assert supervisor.running is True
            assert supervisor.pid() is not None
        finally:
            supervisor.stop()
            await task

    @pytest.mark.asyncio
    async def test_a_crash_loop_ends_in_a_reported_stop_not_an_endless_loop(self) -> None:
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        # Real clock: uptime is real time, and a child that dies immediately must
        # not look like a healthy run.
        policy = make_policy(restart_budget=1, safe_mode_enabled=False, healthy_run_seconds=60.0)
        supervisor = ProcessSupervisor(name="t", argv=(sys.executable, "-c", "raise SystemExit(1)"), policy=policy)
        await supervisor.run()
        assert supervisor.running is False
        assert supervisor.status().action is RestartAction.GIVE_UP
        assert supervisor.status().crash_loop is True
        assert "stopped restarting" in supervisor.diagnostics().what_happens_next

    @pytest.mark.asyncio
    async def test_stopping_terminates_a_running_child(self) -> None:
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        supervisor = ProcessSupervisor(name="t", argv=(sys.executable, "-c", "import time; time.sleep(600)"), policy=make_policy())
        task = asyncio.create_task(supervisor.run())
        try:
            assert await supervisor.wait_spawned(timeout=10.0) is True
            assert supervisor.running is True
            pid = supervisor.pid()
            assert pid is not None
            supervisor.stop(reason="operator asked")
            await asyncio.wait_for(task, timeout=15.0)
            assert supervisor.running is False
        finally:
            supervisor.stop()
            supervisor.terminate()

    @pytest.mark.asyncio
    async def test_starting_twice_is_idempotent(self) -> None:
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        supervisor = ProcessSupervisor(name="t", argv=(sys.executable, "-c", "import time; time.sleep(30)"), policy=make_policy())
        first = supervisor.start()
        try:
            assert supervisor.start() is first
            assert await supervisor.wait_spawned(timeout=10.0) is True
        finally:
            supervisor.stop()
            await supervisor.wait(timeout=15.0)


class TestStopSemantics:
    """Two real bugs this suite caught, both invisible to a happy-path test.

    ``stop()`` documented itself as non-blocking while calling a blocking
    ``Popen.wait(timeout=5)``, and ``run()`` re-created the stop event, so a
    ``stop()`` issued between ``start()`` and the first iteration was silently
    discarded and the loop went on to restart the child.
    """

    @pytest.mark.asyncio
    async def test_stop_returns_promptly_even_while_a_child_is_running(self) -> None:
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        supervisor = ProcessSupervisor(name="t", argv=(sys.executable, "-c", "import time; time.sleep(600)"), policy=make_policy())
        task = asyncio.create_task(supervisor.run())
        try:
            assert await supervisor.wait_spawned(timeout=10.0) is True
            started = time.perf_counter()
            supervisor.stop()
            elapsed = time.perf_counter() - started
            # A blocking terminate would cost up to its 5s (or 10s with the kill
            # ladder) here, and would stall the event loop while doing it.
            assert elapsed < 0.5, f"stop() blocked for {elapsed:.2f}s; it claims not to block"
            await asyncio.wait_for(task, timeout=15.0)
        finally:
            supervisor.stop()

    @pytest.mark.asyncio
    async def test_a_stop_issued_before_the_loop_starts_is_not_lost(self) -> None:
        """The event must not be re-created by run(), or the stop evaporates."""
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        supervisor = ProcessSupervisor(name="t", argv=(sys.executable, "-c", "import time; time.sleep(600)"), policy=make_policy())
        # start() creates the event; stop() sets it; run() must not replace it.
        supervisor.start()
        supervisor.stop()
        await supervisor.wait(timeout=15.0)
        assert supervisor.running is False
        assert supervisor.pid() is None, "a stop that was lost would have started a child"

    def test_stopping_without_a_loop_still_reaps_the_child(self) -> None:
        """A script that never called start() has no loop to notice the flag.

        Synchronous on purpose: this is the no-running-loop fallback, so the
        whole thing has to run outside a loop. The child is placed directly rather
        than through ``_attempt_once``, which would block inside ``_await_exit``
        waiting for a child that is never going to exit on its own.
        """
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        argv = [sys.executable, "-c", "import time; time.sleep(600)"]
        supervisor = ProcessSupervisor(name="t", argv=tuple(argv), policy=make_policy())
        supervisor._process = supervisor._spawn(argv, None, None)  # noqa: SLF001
        try:
            assert supervisor.running is True
            started = time.perf_counter()
            supervisor.stop()
            elapsed = time.perf_counter() - started
            assert supervisor.running is False, "stop() without a loop must still reap the child"
            assert elapsed < 6.0, f"the synchronous fallback took {elapsed:.2f}s, which is longer than the terminate budget"
        finally:
            supervisor.terminate()

    @pytest.mark.asyncio
    async def test_a_second_run_after_a_stop_returns_immediately(self) -> None:
        """A supervisor that cannot be re-armed is a one-way door, by design."""
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        supervisor = ProcessSupervisor(name="t", argv=(sys.executable, "-c", "import time; time.sleep(600)"), policy=make_policy())
        supervisor.start()
        assert await supervisor.wait_spawned(timeout=10.0) is True
        supervisor.stop()
        await supervisor.wait(timeout=15.0)
        first_pid = supervisor.pid()
        # run() again: the event is still set, so nothing may be started.
        await supervisor.run()
        assert supervisor.pid() is None
        assert first_pid is None or not supervisor.running


class TestDiagnostics:
    @pytest.mark.asyncio
    async def test_the_report_answers_all_six_questions(self) -> None:
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        policy = make_policy(restart_budget=1, safe_mode_enabled=False)
        supervisor = ProcessSupervisor(name="alpha-backend", argv=("alpha-no-such-binary-xyzzy",), policy=policy)
        await supervisor._attempt_once()  # noqa: SLF001 - one real attempt, so the report has evidence
        report = supervisor.diagnostics()
        assert "not running" in report.what_failed
        assert "could not be started" in report.why_it_failed
        assert "not found on PATH" in report.where_it_failed
        assert "alpha-backend" in report.what_alpha_was_doing
        assert report.what_happens_next

    def test_the_report_says_what_happens_next_before_the_first_start(self) -> None:
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        supervisor = ProcessSupervisor(name="t", argv=("alpha-no-such-binary-xyzzy",), policy=make_policy(), clock=ManualClock())
        assert "about to start" in supervisor.diagnostics().what_alpha_was_doing
        assert supervisor.status().to_dict()["action"] == "restart"
        assert "will restart" in supervisor.diagnostics().what_happens_next

    @pytest.mark.asyncio
    async def test_a_crash_loop_report_tells_the_operator_to_stop_looping(self) -> None:
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        policy = make_policy(restart_budget=1, safe_mode_enabled=False, healthy_run_seconds=60.0)
        supervisor = ProcessSupervisor(name="t", argv=(sys.executable, "-c", "raise SystemExit(1)"), policy=policy)
        await supervisor.run()
        report = supervisor.diagnostics()
        assert "stopped restarting" in report.what_happens_next
        assert "exited unexpectedly" in report.why_it_failed
        assert report.what_alpha_tried, "the report must list what was already tried"

    def test_the_report_renders_as_text_for_a_support_bundle(self) -> None:
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        supervisor = ProcessSupervisor(name="t", argv=("alpha-no-such-binary-xyzzy",), policy=make_policy())
        text = supervisor.diagnostics().to_text()
        for label in ("WHAT FAILED:", "WHY IT FAILED:", "WHERE IT FAILED:", "ALPHA WAS DOING:", "ALPHA TRIED:", "WHAT HAPPENS NEXT:"):
            assert label in text

    def test_the_status_serializes_for_an_health_endpoint(self) -> None:
        from alpha.runtime.supervisor.supervisor import ProcessSupervisor

        supervisor = ProcessSupervisor(name="t", argv=("alpha-no-such-binary-xyzzy",), policy=make_policy())
        payload = supervisor.status().to_dict()
        assert set(payload) >= {"running", "action", "reason", "restarts_in_window", "restart_budget", "crash_loop", "safe_mode", "episode"}
