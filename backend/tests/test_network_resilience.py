"""Network-aware execution: the monitor, the probe policy, and error honesty.

The property under test throughout is the one the durable-runtime contract
names: **an internet outage must not become a task failure**. Concretely, that
means the monitor must distinguish "no link" from "one endpoint is down" from
"our probe is broken", and must never announce an outage it has not corroborated.
"""

from __future__ import annotations

import asyncio
import errno
import socket
import ssl

import pytest
from pydantic import ValidationError

from alpha.config.app_config import AppConfig
from alpha.config.network_resilience_config import NetworkResilienceConfig, to_monitor_config
from alpha.config.reload_boundary import is_startup_only_field
from alpha.errors.registry import ERROR_CODES, ErrorSeverity, classify, get_definition
from alpha.runtime.network import (
    DEFAULT_PROBE_TARGETS,
    NetworkFailureKind,
    NetworkMonitor,
    NetworkMonitorConfig,
    NetworkState,
    ProbeTarget,
    ScriptedProbe,
    TcpConnectivityProbe,
    classify_network_error,
    is_connected,
    is_offline,
    normalize_targets,
    proves_link_unavailable,
)
from alpha.runtime.network.monitor import NETWORK_LOST_EVENT, NETWORK_RESTORED_EVENT
from alpha.runtime.resilience.clock import ManualClock

TWO_TARGETS = (ProbeTarget(name="a", host="192.0.2.1"), ProbeTarget(name="b", host="192.0.2.2"))


def make_monitor(script: list[list[bool]], **kwargs: object) -> NetworkMonitor:
    """Build a monitor over a scripted probe and a virtual clock."""
    kwargs.setdefault("clock", ManualClock())
    return NetworkMonitor(kwargs.pop("config", NetworkMonitorConfig()) or NetworkMonitorConfig(), probe=ScriptedProbe(script=[list(entry) for entry in script]), targets=TWO_TARGETS, **kwargs)  # type: ignore[arg-type]


class TestStateVocabulary:
    def test_unknown_is_not_offline(self) -> None:
        assert NetworkState.UNKNOWN is not NetworkState.OFFLINE
        assert not is_offline(NetworkState.UNKNOWN)

    def test_unknown_still_allows_an_attempt(self) -> None:
        """Not knowing is not the same as knowing the link is down."""
        assert is_connected(NetworkState.UNKNOWN)
        assert is_connected(NetworkState.ONLINE)
        assert is_connected(NetworkState.DEGRADED)
        assert not is_connected(NetworkState.OFFLINE)

    def test_degraded_is_never_treated_as_an_outage(self) -> None:
        assert not is_offline(NetworkState.DEGRADED)
        assert is_connected(NetworkState.DEGRADED)


class TestErrorClassification:
    def test_dns_failure_proves_the_link_is_down(self) -> None:
        failure = classify_network_error(socket.gaierror(-2, "Name or service not known"))
        assert failure.kind is NetworkFailureKind.DNS_FAILURE
        assert proves_link_unavailable(failure)

    def test_connection_refused_proves_the_link_is_down(self) -> None:
        failure = classify_network_error(ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"))
        assert failure.kind is NetworkFailureKind.CONNECTION_REFUSED
        assert failure.proves_link_down

    def test_unreachable_route_proves_the_link_is_down(self) -> None:
        assert classify_network_error(OSError(errno.ENETUNREACH, "Network is unreachable")).proves_link_down
        assert classify_network_error(OSError(errno.EHOSTUNREACH, "No route to host")).proves_link_down

    def test_a_timeout_does_not_prove_the_link_is_down(self) -> None:
        """A saturated link, a cold TLS path, or a slow provider all time out."""
        failure = classify_network_error(TimeoutError("timed out"))
        assert failure.kind is NetworkFailureKind.TIMEOUT
        assert not failure.proves_link_down

    def test_a_tls_failure_does_not_prove_the_link_is_down(self) -> None:
        """TLS got far enough to be spoken to, so a route existed."""
        assert not classify_network_error(ssl.SSLError("handshake failure")).proves_link_down

    def test_an_unrecognised_error_proves_nothing(self) -> None:
        failure = classify_network_error(RuntimeError("something odd"))
        assert failure.kind is NetworkFailureKind.UNKNOWN
        assert not failure.proves_link_down

    def test_a_definitive_inner_signal_beats_an_outer_timeout(self) -> None:
        """Reading the chain outermost-first would discard the only real evidence in it."""
        outer = RuntimeError("provider call failed")
        middle = TimeoutError("read timeout")
        inner = socket.gaierror(-2, "no name")
        middle.__cause__ = inner
        outer.__cause__ = middle
        failure = classify_network_error(outer)
        assert failure.kind is NetworkFailureKind.DNS_FAILURE
        assert failure.proves_link_down

    def test_a_timeout_with_nothing_definitive_under_it_stays_a_timeout(self) -> None:
        outer = RuntimeError("provider call failed")
        middle = TimeoutError("read timeout")
        middle.__cause__ = RuntimeError("inner wrapper")
        outer.__cause__ = middle
        failure = classify_network_error(outer)
        assert failure.kind is NetworkFailureKind.TIMEOUT
        assert not failure.proves_link_down

    def test_a_cyclic_exception_graph_does_not_hang_the_classifier(self) -> None:
        first = ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
        second = ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
        first.__cause__ = second
        second.__cause__ = first
        assert classify_network_error(first).kind is NetworkFailureKind.CONNECTION_REFUSED

    def test_the_reported_detail_never_carries_the_raw_message(self) -> None:
        failure = classify_network_error(OSError(errno.ECONNREFUSED, "refused for https://x.test/?token=secret"))
        assert "secret" not in failure.detail

    def test_failure_serializes_for_observability(self) -> None:
        payload = classify_network_error(ConnectionRefusedError(errno.ECONNREFUSED, "x")).to_dict()
        assert payload == {"kind": "connection_refused", "proves_link_down": True, "detail": f"errno={errno.ECONNREFUSED}", "exception_type": "ConnectionRefusedError"}


class TestStateDerivation:
    @pytest.mark.asyncio
    async def test_all_targets_reachable_is_online(self) -> None:
        monitor = make_monitor([[True, True]])
        observation = await monitor.check_once()
        assert observation.state is NetworkState.ONLINE
        assert observation.reachable_targets == ("a", "b")

    @pytest.mark.asyncio
    async def test_some_targets_reachable_is_degraded_not_offline(self) -> None:
        monitor = make_monitor([[True, False]])
        observation = await monitor.check_once()
        assert observation.state is NetworkState.DEGRADED
        assert observation.unreachable_targets == ("b",)
        assert is_connected(observation.state)

    @pytest.mark.asyncio
    async def test_a_cold_start_publishes_its_first_reading_immediately(self) -> None:
        """Hysteresis guards against flapping; it must not delay a truthful boot reading."""
        monitor = make_monitor([[False, False]])
        observation = await monitor.check_once()
        assert observation.state is NetworkState.OFFLINE
        assert observation.changed is True
        assert not is_connected(observation.state)


class TestHysteresis:
    @pytest.mark.asyncio
    async def test_a_single_failed_probe_never_publishes_offline(self) -> None:
        """One dropped packet must not park the fleet."""
        monitor = make_monitor([[True, True], [False, False]])
        await monitor.check_once()
        blip = await monitor.check_once()
        assert blip.state is NetworkState.ONLINE
        assert blip.changed is False

    @pytest.mark.asyncio
    async def test_a_single_good_probe_after_offline_does_not_declare_recovery(self) -> None:
        monitor = make_monitor([[False, False], [False, False], [True, True], [True, True]])
        await monitor.check_once()
        assert (await monitor.check_once()).state is NetworkState.OFFLINE
        first_back = await monitor.check_once()
        assert first_back.state is NetworkState.OFFLINE, "recovery needs corroboration too"
        assert (await monitor.check_once()).state is NetworkState.ONLINE

    @pytest.mark.asyncio
    async def test_degraded_publishes_on_the_first_observation(self) -> None:
        """A degraded link is not a stop-the-world decision, so it is not gated."""
        monitor = make_monitor([[True, False]])
        observation = await monitor.check_once()
        assert observation.state is NetworkState.DEGRADED
        assert observation.changed is True

    @pytest.mark.asyncio
    async def test_the_confirmation_count_is_configurable(self) -> None:
        config = NetworkMonitorConfig(offline_after_consecutive=1, online_after_consecutive=1)
        monitor = make_monitor([[False, False]], config=config)
        observation = await monitor.check_once()
        assert observation.state is NetworkState.OFFLINE

    @pytest.mark.asyncio
    async def test_confirmation_of_one_is_forced_to_at_least_one(self) -> None:
        with pytest.raises(ValueError):
            NetworkMonitorConfig(offline_after_consecutive=0)
        with pytest.raises(ValueError):
            NetworkMonitorConfig(online_after_consecutive=0)


class TestBrokenProbe:
    @pytest.mark.asyncio
    async def test_a_probe_that_cannot_run_reports_unknown_never_offline(self) -> None:
        """Announcing an outage because the probe is broken would park every session on a lie."""
        monitor = NetworkMonitor(NetworkMonitorConfig(offline_after_consecutive=1), probe=_ExplodingProbe(), targets=TWO_TARGETS, clock=ManualClock())
        observation = await monitor.check_once()
        assert observation.state is NetworkState.UNKNOWN
        assert is_connected(observation.state)
        assert observation.consecutive_probe_failures == 1
        assert "could not be executed" in observation.detail

    @pytest.mark.asyncio
    async def test_a_broken_probe_never_publishes_offline_however_often_it_repeats(self) -> None:
        monitor = NetworkMonitor(NetworkMonitorConfig(offline_after_consecutive=1), probe=_ExplodingProbe(), targets=TWO_TARGETS, clock=ManualClock())
        for _ in range(5):
            assert (await monitor.check_once()).state is NetworkState.UNKNOWN

    @pytest.mark.asyncio
    async def test_repeated_probe_failures_degrade_a_known_good_link_to_unknown(self) -> None:
        config = NetworkMonitorConfig(online_after_consecutive=1, unknown_after_consecutive=2)
        monitor = NetworkMonitor(config, probe=_FlakyProbe(fail_after=1), targets=TWO_TARGETS, clock=ManualClock())
        assert (await monitor.check_once()).state is NetworkState.ONLINE
        assert (await monitor.check_once()).state is NetworkState.ONLINE, "one failure is tolerated"
        degraded = await monitor.check_once()
        assert degraded.state is NetworkState.UNKNOWN
        assert is_connected(degraded.state), "unknown still permits an attempt"

    @pytest.mark.asyncio
    async def test_an_empty_probe_result_is_unknown_not_online(self) -> None:
        monitor = NetworkMonitor(NetworkMonitorConfig(), probe=_EmptyProbe(), targets=TWO_TARGETS, clock=ManualClock())
        observation = await monitor.check_once()
        assert observation.state is NetworkState.UNKNOWN
        assert "no results" in observation.detail


class TestBackoff:
    @pytest.mark.asyncio
    async def test_the_interval_grows_while_offline_and_is_bounded(self) -> None:
        config = NetworkMonitorConfig(poll_interval_seconds=1.0, backoff_initial_seconds=2.0, backoff_max_seconds=8.0, backoff_multiplier=2.0, offline_after_consecutive=1, backoff_jitter_ratio=0.0)
        monitor = make_monitor([[False, False]], config=config)
        observed = [(await monitor.check_once(), monitor.wait_decision().next_poll_seconds) for _ in range(6)]
        delays = [delay for _, delay in observed]
        assert delays[0] == 2.0
        assert delays[:4] == [2.0, 4.0, 8.0, 8.0], f"expected doubling to a ceiling of 8, got {delays[:4]}"
        assert max(delays) <= 8.0

    @pytest.mark.asyncio
    async def test_jitter_stays_within_the_declared_ceiling(self) -> None:
        config = NetworkMonitorConfig(poll_interval_seconds=1.0, backoff_initial_seconds=4.0, backoff_max_seconds=4.0, backoff_multiplier=1.0, offline_after_consecutive=1, backoff_jitter_ratio=0.5)
        values = iter([0.0, 0.5, 0.99, 0.25])
        monitor = NetworkMonitor(config, probe=ScriptedProbe(script=[[False, False]]), targets=TWO_TARGETS, clock=ManualClock(), random_fn=lambda: next(values))
        delays = [(await monitor.check_once(), monitor.wait_decision().next_poll_seconds)[1] for _ in range(4)]
        assert all(0.0 < delay <= 4.0 for delay in delays), delays
        assert len(set(delays)) > 1, "jitter must actually vary the delay"

    @pytest.mark.asyncio
    async def test_jitter_never_pushes_the_delay_below_the_poll_interval(self) -> None:
        config = NetworkMonitorConfig(poll_interval_seconds=5.0, backoff_initial_seconds=5.0, backoff_max_seconds=5.0, backoff_multiplier=1.0, offline_after_consecutive=1, backoff_jitter_ratio=1.0)
        monitor = NetworkMonitor(config, probe=ScriptedProbe(script=[[False, False]]), targets=TWO_TARGETS, clock=ManualClock(), random_fn=lambda: 1.0)
        await monitor.check_once()
        assert monitor.wait_decision().next_poll_seconds == 5.0

    @pytest.mark.asyncio
    async def test_recovery_resets_the_interval_immediately(self) -> None:
        config = NetworkMonitorConfig(poll_interval_seconds=3.0, backoff_initial_seconds=2.0, backoff_max_seconds=60.0, offline_after_consecutive=1, online_after_consecutive=1)
        monitor = make_monitor([[False, False]], config=config)
        for _ in range(5):
            await monitor.check_once()
        assert monitor.wait_decision().next_poll_seconds > 3.0
        monitor._probe = ScriptedProbe(script=[[True, True]])  # noqa: SLF001 - deliberate state injection
        await monitor.check_once()
        assert monitor.state is NetworkState.ONLINE
        assert monitor.wait_decision().next_poll_seconds == 3.0


class TestWaitDecision:
    @pytest.mark.asyncio
    async def test_the_unknown_state_admits_work(self) -> None:
        monitor = make_monitor([[True, True]])
        decision = monitor.wait_decision()
        assert decision.admit_network_work is True
        assert decision.resume_parked_work is False
        assert decision.reason == "connectivity_unknown"

    @pytest.mark.asyncio
    async def test_offline_refuses_admission_and_names_the_reason(self) -> None:
        monitor = make_monitor([[False, False]], config=NetworkMonitorConfig(offline_after_consecutive=1))
        await monitor.check_once()
        decision = monitor.wait_decision()
        assert decision.admit_network_work is False
        assert decision.reason == "connectivity_lost"
        assert "resume automatically" in decision.message

    @pytest.mark.asyncio
    async def test_recovery_is_a_rising_edge(self) -> None:
        monitor = make_monitor([[False, False], [True, True]], config=NetworkMonitorConfig(offline_after_consecutive=1, online_after_consecutive=1))
        await monitor.check_once()
        assert monitor.wait_decision().resume_parked_work is False
        await monitor.check_once()
        decision = monitor.wait_decision()
        assert decision.resume_parked_work is True
        assert decision.reason == "connectivity_restored"

    @pytest.mark.asyncio
    async def test_leaving_degraded_does_not_re_trigger_a_resume(self) -> None:
        """Nothing was parked for a degraded link, so nothing should resume."""
        monitor = make_monitor([[True, False], [True, True]], config=NetworkMonitorConfig(online_after_consecutive=1))
        await monitor.check_once()
        assert monitor.state is NetworkState.DEGRADED
        await monitor.check_once()
        assert monitor.state is NetworkState.ONLINE
        assert monitor.wait_decision().resume_parked_work is False

    @pytest.mark.asyncio
    async def test_recovery_from_offline_through_degraded_still_resumes(self) -> None:
        config = NetworkMonitorConfig(offline_after_consecutive=1)
        monitor = make_monitor([[False, False], [True, False]], config=config)
        await monitor.check_once()
        assert monitor.state is NetworkState.OFFLINE
        await monitor.check_once()
        assert monitor.state is NetworkState.DEGRADED
        assert monitor.wait_decision().resume_parked_work is True
        assert monitor.wait_decision().admit_network_work is True

    @pytest.mark.asyncio
    async def test_the_decision_serializes_for_the_waiting_banner(self) -> None:
        monitor = make_monitor([[False, False]], config=NetworkMonitorConfig(offline_after_consecutive=1, poll_interval_seconds=1.0, backoff_initial_seconds=1.0))
        await monitor.check_once()
        payload = monitor.wait_decision().to_dict()
        assert payload["state"] == "offline"
        assert payload["admit_network_work"] is False
        assert payload["message"]


class TestObservers:
    @pytest.mark.asyncio
    async def test_a_subscriber_learns_the_current_state_immediately(self) -> None:
        monitor = make_monitor([[True, True]])
        seen: list[NetworkState] = []
        monitor.subscribe(lambda observation: seen.append(observation.state))
        assert seen == [NetworkState.UNKNOWN], "a late subscriber must not wait a poll to learn the state"

    @pytest.mark.asyncio
    async def test_observers_fire_on_change_only(self) -> None:
        monitor = make_monitor([[False, False], [False, False]])
        seen: list[NetworkState] = []
        monitor.subscribe(lambda observation: seen.append(observation.state))
        await monitor.check_once()
        await monitor.check_once()
        assert seen == [NetworkState.UNKNOWN, NetworkState.OFFLINE]

    @pytest.mark.asyncio
    async def test_unsubscribing_stops_delivery(self) -> None:
        monitor = make_monitor([[False, False], [False, False]])
        seen: list[NetworkState] = []
        unsubscribe = monitor.subscribe(lambda observation: seen.append(observation.state))
        await monitor.check_once()
        unsubscribe()
        await monitor.check_once()
        assert seen == [NetworkState.UNKNOWN, NetworkState.OFFLINE]

    @pytest.mark.asyncio
    async def test_a_broken_observer_cannot_break_the_monitor(self) -> None:
        config = NetworkMonitorConfig(offline_after_consecutive=1)
        monitor = make_monitor([[True, True], [False, False]], config=config)

        def explode(observation: object) -> None:
            raise RuntimeError("observer bug")

        monitor.subscribe(explode)
        monitor.subscribe(lambda observation: None)
        assert (await monitor.check_once()).state is NetworkState.ONLINE
        assert (await monitor.check_once()).state is NetworkState.OFFLINE

    @pytest.mark.asyncio
    async def test_transitions_are_published_on_the_bus(self) -> None:
        bus = _RecordingBus()
        config = NetworkMonitorConfig(offline_after_consecutive=1, online_after_consecutive=1)
        monitor = NetworkMonitor(config, probe=ScriptedProbe(script=[[False, False], [True, True]]), targets=TWO_TARGETS, clock=ManualClock(), bus=bus)
        await monitor.check_once()
        await monitor.check_once()
        await asyncio.sleep(0)
        assert [name for name, _ in bus.events] == [NETWORK_LOST_EVENT, NETWORK_RESTORED_EVENT]
        assert bus.events[1][1]["state"] == "online"


class TestLoopLifecycle:
    @pytest.mark.asyncio
    async def test_the_loop_polls_until_stopped(self) -> None:
        probe = ScriptedProbe(script=[[True, True]])
        monitor = NetworkMonitor(NetworkMonitorConfig(poll_interval_seconds=0.01), probe=probe, targets=TWO_TARGETS, clock=ManualClock())
        monitor.start()
        assert monitor.running
        await asyncio.sleep(0.05)
        await monitor.stop(timeout=2.0)
        assert not monitor.running
        assert len(probe.calls) >= 2

    @pytest.mark.asyncio
    async def test_starting_twice_is_idempotent(self) -> None:
        monitor = NetworkMonitor(NetworkMonitorConfig(poll_interval_seconds=0.01), probe=ScriptedProbe(), targets=TWO_TARGETS, clock=ManualClock())
        first = monitor.start()
        assert monitor.start() is first
        await monitor.stop(timeout=2.0)

    @pytest.mark.asyncio
    async def test_a_disabled_monitor_refuses_to_start_a_loop(self) -> None:
        monitor = NetworkMonitor(NetworkMonitorConfig(enabled=False), probe=ScriptedProbe(), targets=TWO_TARGETS, clock=ManualClock())
        with pytest.raises(RuntimeError, match="disabled"):
            monitor.start()

    @pytest.mark.asyncio
    async def test_stopping_a_monitor_that_never_ran_is_a_no_op(self) -> None:
        monitor = NetworkMonitor(NetworkMonitorConfig(), probe=ScriptedProbe(), targets=TWO_TARGETS, clock=ManualClock())
        await monitor.stop(timeout=0.1)


class TestProbeTargetValidation:
    def test_default_targets_are_valid_and_unique(self) -> None:
        assert len({target.name for target in DEFAULT_PROBE_TARGETS}) == len(DEFAULT_PROBE_TARGETS)
        assert all(target.port == 443 for target in DEFAULT_PROBE_TARGETS), "the default probe must only touch provider ports"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"name": "", "host": "h"},
            {"name": "n", "host": ""},
            {"name": "n", "host": "h", "port": 0},
            {"name": "n", "host": "h", "port": 70000},
            {"name": "n", "host": "h", "timeout_seconds": 0.0},
            {"name": "n", "host": "h", "timeout_seconds": 999.0},
        ],
    )
    def test_invalid_targets_are_refused(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            ProbeTarget(**kwargs)  # type: ignore[arg-type]

    def test_normalizing_deduplicates_and_orders_deterministically(self) -> None:
        targets = normalize_targets([ProbeTarget(name="z", host="h1"), ProbeTarget(name="a", host="h2"), ProbeTarget(name="z", host="h3")])
        assert [target.name for target in targets] == ["a", "z"]
        assert targets[1].host == "h1", "first definition wins for a duplicate name"

    def test_normalizing_nothing_uses_the_defaults(self) -> None:
        assert normalize_targets(None) == DEFAULT_PROBE_TARGETS

    def test_normalizing_to_nothing_is_refused(self) -> None:
        with pytest.raises(ValueError):
            normalize_targets([])


class TestTcpProbe:
    @pytest.mark.asyncio
    async def test_a_refused_connect_is_a_result_not_an_exception(self) -> None:
        """An unreachable endpoint is an answer, so the probe never raises."""
        import errno

        def refuse(*args: object, **kwargs: object) -> None:
            raise OSError(errno.ECONNREFUSED, "Connection refused")

        original = asyncio.open_connection
        asyncio.open_connection = refuse  # type: ignore[assignment]
        try:
            outcomes = await TcpConnectivityProbe().probe(TWO_TARGETS)
        finally:
            asyncio.open_connection = original  # type: ignore[assignment]
        assert len(outcomes) == 2
        assert all(not outcome.reachable for outcome in outcomes)
        assert all(outcome.failure_kind is NetworkFailureKind.CONNECTION_REFUSED for outcome in outcomes)

    @pytest.mark.asyncio
    async def test_probing_nothing_returns_nothing(self) -> None:
        assert await TcpConnectivityProbe().probe(()) == ()

    @pytest.mark.asyncio
    async def test_one_outcome_per_target_in_order(self) -> None:
        import errno

        def refuse(*args: object, **kwargs: object) -> None:
            raise OSError(errno.ECONNREFUSED, "Connection refused")

        original = asyncio.open_connection
        asyncio.open_connection = refuse  # type: ignore[assignment]
        try:
            outcomes = await TcpConnectivityProbe(max_concurrency=2).probe(TWO_TARGETS)
        finally:
            asyncio.open_connection = original  # type: ignore[assignment]
        assert [outcome.target for outcome in outcomes] == ["a", "b"]


class TestObservationHistory:
    @pytest.mark.asyncio
    async def test_history_is_bounded_and_oldest_first(self) -> None:
        config = NetworkMonitorConfig(offline_after_consecutive=1, online_after_consecutive=1, max_observations=3)
        monitor = make_monitor([[True, True], [False, False], [True, True]], config=config)
        for _ in range(5):
            await monitor.check_once()
        history = monitor.observations()
        assert len(history) == 3
        assert [observation.observed_at for observation in history] == sorted(observation.observed_at for observation in history)

    @pytest.mark.asyncio
    async def test_the_observation_serializes_every_field_a_ui_needs(self) -> None:
        monitor = make_monitor([[True, False]])
        payload = (await monitor.check_once()).to_dict()
        assert payload["state"] == "degraded"
        assert payload["reachable_targets"] == ["a"]
        assert payload["unreachable_targets"] == ["b"]
        assert payload["outcomes"][0]["target"] == "a"
        assert payload["detail"]


class TestOperatorConfig:
    def test_the_default_policy_is_usable_without_any_configuration(self) -> None:
        config = NetworkResilienceConfig()
        monitor_config = to_monitor_config(config)
        assert monitor_config.enabled is True
        assert config.to_probe_targets() == DEFAULT_PROBE_TARGETS

    def test_operators_can_declare_their_own_endpoints(self) -> None:
        config = NetworkResilienceConfig(targets=[{"name": "corp", "host": "proxy.corp.test", "port": 8443}])
        targets = config.to_probe_targets()
        assert [(target.name, target.host, target.port) for target in targets] == [("corp", "proxy.corp.test", 8443)]

    def test_an_empty_target_list_would_strand_connectivity_so_it_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="omitted or non-empty"):
            NetworkResilienceConfig(targets=[])

    def test_duplicate_target_names_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="duplicate names"):
            NetworkResilienceConfig(targets=[{"name": "a", "host": "h1"}, {"name": "a", "host": "h2"}])

    def test_an_inverted_backoff_ladder_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="backoff_max_seconds"):
            NetworkResilienceConfig(backoff_initial_seconds=60.0, backoff_max_seconds=5.0)

    def test_a_typo_in_a_field_is_an_error_rather_than_a_silent_default(self) -> None:
        with pytest.raises(ValidationError):
            NetworkResilienceConfig(offline_after_consecuive=2)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"poll_interval_seconds": 0.0},
            {"offline_after_consecutive": 0},
            {"online_after_consecutive": 0},
            {"unknown_after_consecutive": 0},
            {"backoff_multiplier": 0.5},
            {"backoff_jitter_ratio": 1.5},
            {"max_observations": 0},
        ],
    )
    def test_out_of_range_values_are_refused(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            NetworkResilienceConfig(**kwargs)

    def test_the_policy_translates_onto_the_runtime_config(self) -> None:
        monitor_config = to_monitor_config(NetworkResilienceConfig(poll_interval_seconds=30.0, offline_after_consecutive=4, backoff_jitter_ratio=0.5))
        assert monitor_config.poll_interval_seconds == 30.0
        assert monitor_config.offline_after_consecutive == 4
        assert monitor_config.backoff_jitter_ratio == 0.5

    def test_the_section_is_restart_required(self) -> None:
        assert is_startup_only_field("network")

    def test_app_config_exposes_the_section(self) -> None:
        field = AppConfig.model_fields["network"]
        assert field.default_factory is not None
        assert field.default_factory().enabled is True, "the section must be useful with no configuration at all"


class TestErrorRegistryFamily:
    """Connectivity needed its own error family, not a repurpose of a provider code."""

    def test_a_network_family_exists_with_its_own_correlation_id(self) -> None:
        assert get_definition("NETWORK_UNAVAILABLE").correlation_id == "alpha.errors.network"

    def test_a_lost_link_is_a_warning_because_the_task_is_alive_and_parked(self) -> None:
        definition = get_definition("NETWORK_UNAVAILABLE")
        assert definition.severity is ErrorSeverity.WARNING
        assert definition.retryable is True
        assert definition.http_status == 503

    def test_a_connection_refused_classifies_as_a_network_failure(self) -> None:
        assert classify(ConnectionRefusedError(errno.ECONNREFUSED, "refused")).code == "NETWORK_UNAVAILABLE"

    def test_a_dns_failure_classifies_as_a_network_failure(self) -> None:
        assert classify(socket.gaierror(-2, "no name")).code == "NETWORK_UNAVAILABLE"

    def test_a_timeout_stays_a_timeout_rather_than_claiming_the_link_is_down(self) -> None:
        """A timeout proves nothing about the link, so it must not park work."""
        assert classify(TimeoutError("timed out")).code != "NETWORK_UNAVAILABLE"

    def test_the_network_code_does_not_displace_the_single_degradation_code(self) -> None:
        """A degraded link that still succeeded is DEGRADED_MODE, not a second 2xx meaning."""
        two_xx = {code for code, definition in ERROR_CODES.items() if 200 <= definition.http_status < 300}
        assert two_xx == {"DEGRADED_MODE"}
        assert "NETWORK_UNAVAILABLE" not in two_xx

    def test_the_network_code_is_documented_in_the_registry(self) -> None:
        assert get_definition("NETWORK_UNAVAILABLE").notes, "the code must explain itself to a reader"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _ExplodingProbe:
    async def probe(self, targets: object) -> tuple[object, ...]:
        raise RuntimeError("probe misconfigured")


class _EmptyProbe:
    async def probe(self, targets: object) -> tuple[object, ...]:
        return ()


class _FlakyProbe:
    def __init__(self, *, fail_after: int) -> None:
        self._fail_after = fail_after
        self._calls = 0

    async def probe(self, targets: object) -> tuple[object, ...]:
        from alpha.runtime.network.probe import ProbeOutcome

        self._calls += 1
        if self._calls <= self._fail_after:
            return (ProbeOutcome(target="a", reachable=True, elapsed_ms=1.0), ProbeOutcome(target="b", reachable=True, elapsed_ms=1.0))
        raise RuntimeError("probe misconfigured")


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    async def publish(self, name: str, payload: dict[str, object] | None = None, *, source: str = "") -> None:
        self.events.append((name, dict(payload or {})))
