"""Integration tests verifying Alpha Mod Kernel wiring in RunManager and AutonomySupervisor."""

import pytest

from alpha.mods.enforcers.estop_mod import FleetEstopMod
from alpha.mods.kernel import ModKernel, set_mod_kernel
from alpha.runtime.estop import EmergencyStopManager
from alpha.runtime.runs.manager import ConflictError, RunManager


@pytest.fixture
def isolated_estop(tmp_path, monkeypatch):
    mgr = EmergencyStopManager(root_dir=tmp_path)
    monkeypatch.setattr("alpha.runtime.estop.get_estop_manager", lambda root_dir=None: mgr)
    return mgr


@pytest.mark.asyncio
async def test_run_manager_admission_estop_refusal(isolated_estop):
    kernel = ModKernel()
    kernel.register_mod(FleetEstopMod())
    set_mod_kernel(kernel)

    manager = RunManager()

    # 1. Normal create succeeds when ESTOP is inactive
    assert not isolated_estop.is_engaged()
    run_rec = await manager.create("thread_test_1")
    assert run_rec.run_id is not None

    # 2. Engage ESTOP
    isolated_estop.engage("Operator emergency pause")
    assert isolated_estop.is_engaged()

    # Calling create must now be denied with ConflictError!
    with pytest.raises(ConflictError) as exc_info:
        await manager.create("thread_test_2")
    assert "FLEET_ESTOP_ACTIVE" in str(exc_info.value)

    # Calling create_or_reject must also be denied with ConflictError!
    with pytest.raises(ConflictError) as exc_info_reject:
        await manager.create_or_reject("thread_test_3")
    assert "FLEET_ESTOP_ACTIVE" in str(exc_info_reject.value)

    # 3. Disengage ESTOP
    isolated_estop.disengage()
    resumed_rec = await manager.create("thread_test_4")
    assert resumed_rec.run_id is not None


@pytest.mark.asyncio
async def test_run_manager_refuses_non_continue_mod_outcome(monkeypatch):
    from alpha.mods.types import AlphaEvent, EventResult, ModPriority

    class DeferringMod:
        name = "approval_gate"
        version = "1"
        priority = ModPriority.SECURITY
        required_capabilities = set()
        subscribed_events = {"run.admit"}

        async def handle(self, ctx, event: AlphaEvent, next_fn):
            return EventResult.defer(event, reason="operator approval required")

    kernel = ModKernel()
    kernel.register_mod(DeferringMod())
    monkeypatch.setattr("alpha.mods.kernel.get_mod_kernel", lambda: kernel)

    with pytest.raises(ConflictError, match="defer.*operator approval"):
        await RunManager().create("thread_deferred")


@pytest.mark.asyncio
async def test_run_manager_refuses_when_policy_dispatch_raises(monkeypatch):
    kernel = ModKernel()

    async def broken_dispatch(event, terminal_handler=None):
        raise RuntimeError("policy store unavailable")

    monkeypatch.setattr(kernel, "dispatch", broken_dispatch)
    monkeypatch.setattr("alpha.mods.kernel.get_mod_kernel", lambda: kernel)

    with pytest.raises(ConflictError, match="could not evaluate run.admit"):
        await RunManager().create("thread_policy_fault")


@pytest.mark.asyncio
async def test_autonomy_supervisor_tick_mod_admission(isolated_estop):
    from alpha.config.autonomy_config import AutonomyConfig
    from app.gateway.autonomy.supervisor import AutonomySupervisor, LoopSpec

    kernel = ModKernel()
    kernel.register_mod(FleetEstopMod())
    set_mod_kernel(kernel)

    tick_ran = False

    def dummy_tick():
        nonlocal tick_ran
        tick_ran = True
        return {"status": "ok"}

    spec = LoopSpec(
        loop_id="test_loop",
        description="Test loop for mod admission",
        tick=dummy_tick,
        default_interval_seconds=1.0,
    )

    supervisor = AutonomySupervisor(AutonomyConfig(enabled=True))
    supervisor.register(spec)

    state = supervisor._state["test_loop"]
    state.enabled = True
    cfg = supervisor._config.loop_config("test_loop")

    # 1. ESTOP disengaged -> tick executes
    await supervisor._tick_once("test_loop", cfg, dummy_tick, state)
    assert tick_ran is True

    # 2. Engage ESTOP -> tick must be refused
    tick_ran = False
    isolated_estop.engage("Fleet wide stop")
    await supervisor._tick_once("test_loop", cfg, dummy_tick, state)
    assert tick_ran is False  # Tick was refused by Mod Kernel / ESTOP

    # 3. Disengage ESTOP -> tick executes again
    isolated_estop.disengage()
    await supervisor._tick_once("test_loop", cfg, dummy_tick, state)
    assert tick_ran is True


@pytest.mark.asyncio
async def test_autonomy_supervisor_skips_tick_when_mod_kernel_fails(monkeypatch):
    from alpha.config.autonomy_config import AutonomyConfig
    from app.gateway.autonomy.supervisor import AutonomySupervisor, LoopSpec

    kernel = ModKernel()

    async def broken_dispatch(event, terminal_handler=None):
        raise RuntimeError("policy store unavailable")

    monkeypatch.setattr(kernel, "dispatch", broken_dispatch)
    monkeypatch.setattr("alpha.mods.kernel.get_mod_kernel", lambda: kernel)
    supervisor = AutonomySupervisor(AutonomyConfig(enabled=True))
    tick_ran = False

    def tick():
        nonlocal tick_ran
        tick_ran = True

    supervisor.register(LoopSpec(loop_id="test_loop", description="test", tick=tick))
    state = supervisor._state["test_loop"]
    state.enabled = True

    await supervisor._tick_once("test_loop", supervisor._config.loop_config("test_loop"), tick, state)

    assert not tick_ran


@pytest.mark.asyncio
async def test_require_mod_admission_allows_observe_and_continue():
    from alpha.mods.kernel import ModKernel, require_mod_admission
    from alpha.mods.types import AlphaEvent, CorrelationContext, EventOutcome, EventResult, ModPriority

    class ObserverMod:
        name = "observer"
        version = "1.0.0"
        priority = int(ModPriority.OBSERVABILITY)
        required_capabilities = set()
        subscribed_events = {"run.admit"}

        async def handle(self, ctx, event, next_fn):
            return EventResult.observe(event)

    kernel = ModKernel()
    kernel.register_mod(ObserverMod())

    ev = AlphaEvent(name="run.admit", correlation=CorrelationContext.create())
    res = await require_mod_admission(kernel, ev)
    assert res.outcome in (EventOutcome.CONTINUE, EventOutcome.OBSERVE)
