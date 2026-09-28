"""Gateway wiring for the connectivity monitor and the parked-session registry.

The point of these tests is that the durable pieces are *live* in a real Gateway
process: a monitor that actually polls, a registry that actually persists a park,
and a shutdown that actually stops both. A library that is complete, tested, and
never wired is a feature nobody gets, and ``tests/test_no_orphan_modules.py`` is
the gate that says so.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from alpha.runtime.network.states import NetworkState
from alpha.runtime.network.wait_registry import (
    NetworkWaitService,
    get_network_wait_service,
    park_session_if_available,
    set_network_wait_service,
)


class TestRegistrationAccessors:
    def test_no_service_installed_by_default(self) -> None:
        set_network_wait_service(None)
        assert get_network_wait_service() is None

    def test_a_service_can_be_installed_and_cleared(self) -> None:
        service = object()  # type: ignore[assignment]
        try:
            set_network_wait_service(service)  # type: ignore[arg-type]
            assert get_network_wait_service() is service
        finally:
            set_network_wait_service(None)
        assert get_network_wait_service() is None

    @pytest.mark.asyncio
    async def test_parking_without_a_service_reports_false_and_does_not_raise(self) -> None:
        """A missing optional service must not turn one failure into a second one."""
        set_network_wait_service(None)
        assert await park_session_if_available(thread_id="t1", run_id="r1") is False

    @pytest.mark.asyncio
    async def test_parking_routes_to_the_installed_service(self) -> None:
        recorded: list[str] = []

        class Recorder:
            async def park(self, **kwargs: Any) -> Any:
                recorded.append(kwargs["thread_id"])
                return None

        set_network_wait_service(Recorder())  # type: ignore[arg-type]
        try:
            assert await park_session_if_available(thread_id="t1", run_id="r1", reason="connectivity_lost") is True
        finally:
            set_network_wait_service(None)
        assert recorded == ["t1"]

    @pytest.mark.asyncio
    async def test_a_broken_store_does_not_propagate_out_of_a_park(self) -> None:
        """The caller's real failure is already recorded by the run ledger."""

        class Broken:
            async def park(self, **kwargs: Any) -> Any:
                raise RuntimeError("database unavailable")

        set_network_wait_service(Broken())  # type: ignore[arg-type]
        try:
            assert await park_session_if_available(thread_id="t1") is False
        finally:
            set_network_wait_service(None)


class TestNetworkWaitIsRecoverable:
    def test_a_network_parked_run_is_marked_recoverable(self) -> None:
        """Without this, a network-caused failure is reported as a permanent one.

        That membership is the whole mechanism by which
        ``SafeRunRecoveryService`` keeps a network-parked session alive: its scan
        is stop-reason driven, so a reason outside the recoverable set is simply
        not picked up.
        """
        from alpha.runtime.runs.manager import (
            MODEL_FAILURE_RECOVERY_REASON,
            NETWORK_WAIT_RECOVERY_REASON,
            RECOVERABLE_RUN_STOP_REASONS,
        )

        assert NETWORK_WAIT_RECOVERY_REASON == "network_waiting"
        assert NETWORK_WAIT_RECOVERY_REASON in RECOVERABLE_RUN_STOP_REASONS
        assert MODEL_FAILURE_RECOVERY_REASON in RECOVERABLE_RUN_STOP_REASONS

    def test_the_worker_parks_only_a_definite_link_failure(self) -> None:
        """A timeout proves nothing about the link, and must not park healthy work."""
        from alpha.runtime.network.errors import classify_network_error

        assert classify_network_error(ConnectionRefusedError(10061, "refused")).proves_link_down is True
        assert classify_network_error(TimeoutError("timed out")).proves_link_down is False


class TestLifespanWiring:
    def test_the_lifespan_starts_the_monitor_and_the_registry(self) -> None:
        source = _deps_source()
        assert "app.state.network_monitor = monitor" in source, "the monitor must be built by the lifespan"
        assert "app.state.network_waits = wait_service" in source, "the parked-session registry must be built by the lifespan"
        assert "set_network_wait_service(wait_service)" in source, "the harness-side accessor must be installed, or nothing can park"
        assert "await monitor.check_once()" in source, "the first probe must publish before the loop starts"

    def test_a_memory_database_backend_skips_the_registry_but_keeps_the_monitor(self) -> None:
        """There is nowhere durable to record a park, so only the measurement runs."""
        source = _deps_source()
        assert 'database_backend != "memory"' in source
        assert "parked-session durability is unavailable" in source, "the degradation must be logged, not silent"

    def test_startup_reclaims_waits_whose_lease_outlived_the_process(self) -> None:
        source = _deps_source()
        assert "_reclaim_stale_network_leases(wait_store)" in source, "a crashed Gateway must not strand its parked sessions"
        assert "reclaimed %d parked session(s)" in source

    def test_shutdown_stops_the_connectivity_services_first(self) -> None:
        source = _deps_source()
        assert "set_network_wait_service(None)" in source, "shutdown must clear the accessor, or a later park targets a dead service"
        assert "close_admission" in source
        assert "ShutdownPhase.ADMISSION_CLOSED,\n                close_admission" in source, "they stop at admission close, before the run drain"

    def test_admission_close_is_registered_exactly_once(self) -> None:
        """Regression guard for a real bug this wiring caused in real time.

        ``PlannedShutdown.register`` replaces an existing step for the same phase,
        so registering the network stop and the recovery stop as two
        ``ADMISSION_CLOSED`` steps silently dropped the first: the network
        services were never stopped and the harness accessor kept pointing at a
        dead service. Everything admission-close needs must be *composed* into one
        step, so counting registrations for a phase is a meaningful check.
        """
        source = _deps_source()
        registrations = source.count("ShutdownPhase.ADMISSION_CLOSED,")
        assert registrations == 1, f"ADMISSION_CLOSED is registered {registrations} times; register() replaces per phase, so the earlier ones are dropped"
        for phase in ("OPERATIONS_DRAINED", "WORKERS_STOPPED"):
            assert source.count("ShutdownPhase." + phase + ",") <= 1, phase + " is registered more than once"

    def test_no_resume_launcher_is_installed(self) -> None:
        """The continuation path belongs to SafeRunRecoveryService alone.

        A launcher here would imply a per-thread resume API that does not exist,
        and inventing one is how a second continuation authority gets created --
        one that would bypass the fail-closed side-effect gate.
        """
        source = _deps_source()
        assert "launcher=" not in source, "the Gateway must not install a resume launcher"
        assert "NetworkWaitService(wait_store, network_state=" in source

    def test_the_poll_loop_starts_even_when_the_host_boots_offline(self) -> None:
        """Regression guard for a real bug this suite caught in real time.

        The wiring once read ``if monitor.state is not NetworkState.OFFLINE:
        monitor.start()`` -- reasoning that a host which had just proved the link
        is down should not poll it. That is exactly backwards: the poll loop is
        the only thing that can ever notice the link coming *back*, so a Gateway
        that booted offline would have sat there forever and no parked session
        would ever have resumed. Politeness about probing a dead link is the
        backoff ladder's job, and that ladder is already bounded.
        """
        source = _deps_source()
        assert "monitor.start()" in source
        assert "if monitor.state is not NetworkState.OFFLINE:" not in source, "the loop must start unconditionally, or an offline host never recovers"
        assert "backoff ladder" in source, "the reasoning must be recorded at the call site, not just in a test"

    def test_the_first_probe_is_awaited_before_the_loop_starts(self) -> None:
        """A park decided before the first reading would act on a guess."""
        source = _deps_source()
        assert source.index("await monitor.check_once()") < source.index("monitor.start()")


class TestServiceWithoutALauncher:
    @pytest.mark.asyncio
    async def test_a_registry_with_no_launcher_claims_nothing(self) -> None:
        """A wait must not be marked as attempted by a pass that cannot attempt it."""

        class Store:
            async def claim_due(self, **kwargs: Any) -> list[dict[str, Any]]:
                raise AssertionError("claim_due must not be reached without a launcher")

            async def list_open(self, **kwargs: Any) -> list[dict[str, Any]]:
                return []

        service = NetworkWaitService(Store(), network_state=lambda: NetworkState.ONLINE)  # type: ignore[arg-type]
        assert await service.resume_due() == []

    @pytest.mark.asyncio
    async def test_a_registry_with_no_launcher_while_offline_claims_nothing_either(self) -> None:
        class Store:
            async def claim_due(self, **kwargs: Any) -> list[dict[str, Any]]:
                raise AssertionError("claim_due must not be reached while offline")

        service = NetworkWaitService(Store(), network_state=lambda: NetworkState.OFFLINE)  # type: ignore[arg-type]
        assert await service.resume_due() == []


def _deps_source() -> str:
    from pathlib import Path

    import app.gateway.deps as deps_module

    return Path(inspect.getfile(deps_module)).read_text(encoding="utf-8")
