"""End-to-end integration tests for Bot Mode execution governed by Alpha Mod Kernel."""

import pytest

from alpha.bots.capability_dispatch import DispatchOutcome, get_leader_dispatcher
from alpha.bots.dm import send_dm
from alpha.bots.profile import BotProfile
from alpha.bots.registry import BotRegistry
from alpha.bots.work_discovery import claim_task
from alpha.groups.runner import GroupRunService
from alpha.mods.controllers.task_router_mod import TaskRouterMod
from alpha.mods.enforcers.estop_mod import FleetEstopMod
from alpha.mods.kernel import ModKernel, set_mod_kernel
from alpha.mods.types import AlphaEvent, EventResult, ModPriority
from alpha.runtime.estop import EmergencyStopManager


@pytest.fixture
def isolated_estop(tmp_path, monkeypatch):
    mgr = EmergencyStopManager(root_dir=tmp_path)
    monkeypatch.setattr("alpha.runtime.estop.get_estop_manager", lambda root_dir=None: mgr)
    return mgr


@pytest.fixture
def test_registry(tmp_path):
    roster_path = tmp_path / "roster.json"
    reg = BotRegistry(storage_path=roster_path)
    reg.register(
        BotProfile(
            name="alice",
            display_name="Alice",
            role="Software Engineer",
            soul="Test coding agent.",
            department="engineering",
            capabilities=["python", "coding"],
            toolsets=["bash"],
            status="active",
        )
    )
    reg.register(
        BotProfile(
            name="bob",
            display_name="Bob",
            role="System Architect",
            soul="Test architecture agent.",
            department="engineering",
            capabilities=["architecture"],
            toolsets=["diagram"],
            status="active",
        )
    )
    return reg


@pytest.fixture
def clean_kernel():
    k = ModKernel()
    k.register_mod(FleetEstopMod())
    set_mod_kernel(k)
    return k


# =========================================================================
# 1. Bot Direct Messaging Integration
# =========================================================================


def test_send_dm_refuses_when_estop_engaged(isolated_estop, test_registry, clean_kernel):
    # When ESTOP is active -> send_dm should be rejected
    isolated_estop.engage("Operator pause")
    assert isolated_estop.is_engaged()

    ack = send_dm("alice", "bob", "Hello Bob!", registry=test_registry, require_bot_chat=False)
    assert ack.status == "rejected"
    assert "FLEET_ESTOP_ACTIVE" in ack.detail or "Fleet ESTOP active" in ack.detail


def test_send_dm_governed_by_mod_kernel_policy(isolated_estop, test_registry):
    class DmPolicyMod:
        name = "dm_blocker"
        version = "1"
        priority = ModPriority.SECURITY
        required_capabilities = set()
        subscribed_events = {"bot.dm_requested"}

        async def handle(self, ctx, event: AlphaEvent, next_fn):
            msg = event.payload.get("message", "")
            if "forbidden" in msg:
                return EventResult.deny(event, reason="Message contains forbidden phrase")
            return await next_fn(event)

    kernel = ModKernel()
    kernel.register_mod(DmPolicyMod())
    set_mod_kernel(kernel)

    # 1. Allowed message
    ack_ok = send_dm("alice", "bob", "Legitimate message", registry=test_registry, require_bot_chat=False)
    assert ack_ok.status == "delivered"

    # 2. Denied message
    ack_denied = send_dm("alice", "bob", "This has forbidden content", registry=test_registry, require_bot_chat=False)
    assert ack_denied.status == "rejected"
    assert "forbidden phrase" in ack_denied.detail


# =========================================================================
# 2. Work Discovery & Task Claiming Integration
# =========================================================================


def test_claim_task_refuses_when_estop_engaged(isolated_estop, test_registry, clean_kernel):
    isolated_estop.engage("Emergency freeze")

    with pytest.raises(ValueError, match="Fleet ESTOP active"):
        claim_task("task_999", "alice", registry=test_registry)


def test_claim_task_governed_by_mod_kernel(isolated_estop, test_registry):
    class TaskClaimGuard:
        name = "claim_guard"
        version = "1"
        priority = ModPriority.SECURITY
        required_capabilities = set()
        subscribed_events = {"bot.task_claimed"}

        async def handle(self, ctx, event: AlphaEvent, next_fn):
            if event.payload.get("task_id") == "prohibited_task":
                return EventResult.deny(event, reason="Task ID is prohibited by compliance policy")
            return await next_fn(event)

    kernel = ModKernel()
    kernel.register_mod(TaskClaimGuard())
    set_mod_kernel(kernel)

    # 1. Normal claim succeeds
    res_ok = claim_task("normal_task", "alice", registry=test_registry)
    assert res_ok["claimed_by"] == "alice"

    # 2. Prohibited task claim raises ValueError
    with pytest.raises(ValueError, match="Task ID is prohibited by compliance policy"):
        claim_task("prohibited_task", "alice", registry=test_registry)


# =========================================================================
# 3. Capability Dispatch Integration
# =========================================================================


def test_capability_dispatcher_refuses_when_estop_engaged(isolated_estop, test_registry, clean_kernel):
    dispatcher = get_leader_dispatcher("task_1", registry=test_registry)

    # 1. Dispatch succeeds when ESTOP is inactive
    dec_ok = dispatcher.dispatch("task_1", "write a python function", required_capability_tags=["python"])
    assert dec_ok.outcome == DispatchOutcome.DISPATCHED

    # 2. Engage ESTOP -> must be refused
    isolated_estop.engage("Fleet stop")
    dec_refused = get_leader_dispatcher("task_2", registry=test_registry).dispatch("task_2", "write a python function", required_capability_tags=["python"])
    assert dec_refused.outcome == DispatchOutcome.REFUSED
    assert "fleet emergency stop active" in dec_refused.reason


def test_capability_dispatcher_advises_from_task_router_mod(test_registry):
    router = TaskRouterMod(registry=test_registry)
    kernel = ModKernel()
    kernel.register_mod(router)
    set_mod_kernel(kernel)

    dispatcher = get_leader_dispatcher("task_router_test", registry=test_registry)
    decision = dispatcher.dispatch(
        "task_router_test",
        "Design system architecture diagram",
        required_capability_tags=["system_design"],
    )
    assert decision.outcome == DispatchOutcome.DISPATCHED
    assert decision.target == "architect"


# =========================================================================
# 4. Group Runner Integration
# =========================================================================


def test_group_runner_refuses_when_estop_engaged(isolated_estop, test_registry, clean_kernel, tmp_path):
    runner = GroupRunService(storage_path=tmp_path / "runs.json")

    isolated_estop.engage("Fleet ESTOP engaged")
    with pytest.raises(RuntimeError, match="Fleet ESTOP active"):
        runner.start_run("alpha-team", "Implement feature", members=["alice", "bob"])


# =========================================================================
# 5. Channel Dispatch Resolution Integration
# =========================================================================


@pytest.mark.asyncio
async def test_channel_dispatch_refuses_when_estop_engaged(isolated_estop):
    from alpha.channels.mentions import MentionResolution, MentionTarget
    from alpha.channels.routing import TARGET_REFUSED, dispatch_resolution

    isolated_estop.engage("Fleet Stop")

    resolution = MentionResolution(
        text="Hello team",
        targets=[
            MentionTarget(
                kind="bot",
                selector="alice",
                resolved=("alice",),
                via="mention",
            )
        ],
        unresolved=[],
    )

    async def dummy_handler(target, body, res):
        return "ok"

    report = await dispatch_resolution(resolution, dummy_handler)
    assert not report.ok
    assert len(report.outcomes) == 1
    assert report.outcomes[0].status == TARGET_REFUSED
    assert "Fleet ESTOP active" in report.outcomes[0].error
