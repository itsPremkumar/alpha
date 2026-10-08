"""Unit and integration tests for the Alpha Mod Kernel (AMK) and ordered pipeline."""

import pytest

from alpha.mods.context import CapabilityContext
from alpha.mods.kernel import ModKernel
from alpha.mods.types import (
    AlphaEvent,
    CorrelationContext,
    EventOutcome,
    EventResult,
    ModPriority,
    NextHandler,
)


class DummyMod:
    def __init__(
        self,
        name: str,
        priority: int,
        outcome_fn=None,
        subscribed_events=None,
        required_capabilities=None,
    ):
        self.name = name
        self.version = "1.0.0"
        self.priority = priority
        self.outcome_fn = outcome_fn
        self.subscribed_events = subscribed_events
        self.required_capabilities = set(required_capabilities or set())
        self.invoked = False
        self.seen_events = []

    async def handle(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        self.invoked = True
        self.seen_events.append(event)
        if self.outcome_fn:
            return await self.outcome_fn(self, ctx, event, next_fn)
        return await next_fn(event)


@pytest.fixture
def clean_kernel():
    kernel = ModKernel()
    return kernel


@pytest.mark.asyncio
async def test_mod_priority_ordering(clean_kernel):
    execution_order = []

    async def make_hook(mod, ctx, ev, next_fn):
        execution_order.append(mod.name)
        return await next_fn(ev)

    mod_high = DummyMod("high", ModPriority.OBSERVABILITY, make_hook)
    mod_sec = DummyMod("security", ModPriority.SECURITY, make_hook)
    mod_kernel = DummyMod("kernel", ModPriority.KERNEL, make_hook)
    mod_emerg = DummyMod("emerg", ModPriority.EMERGENCY, make_hook)

    # Register in reverse order
    clean_kernel.register_mod(mod_high)
    clean_kernel.register_mod(mod_sec)
    clean_kernel.register_mod(mod_kernel)
    clean_kernel.register_mod(mod_emerg)

    event = AlphaEvent(
        name="test.event",
        payload={"hello": "world"},
        correlation=CorrelationContext.create(),
    )

    res = await clean_kernel.dispatch(event)
    assert res.outcome == EventOutcome.CONTINUE
    # Invariant: Must execute strictly ascending by priority
    assert execution_order == ["kernel", "emerg", "security", "high"]


@pytest.mark.asyncio
async def test_outcome_continue(clean_kernel):
    terminal_called = False

    async def terminal(ev: AlphaEvent) -> EventResult:
        nonlocal terminal_called
        terminal_called = True
        return EventResult.continue_(ev, metadata={"terminal": True})

    mod = DummyMod("observe", ModPriority.EXECUTION)
    clean_kernel.register_mod(mod)

    event = AlphaEvent(name="tool.requested", payload={}, correlation=CorrelationContext.create())
    res = await clean_kernel.dispatch(event, terminal_handler=terminal)

    assert mod.invoked
    assert terminal_called
    assert res.outcome == EventOutcome.CONTINUE
    assert res.metadata.get("terminal") is True


@pytest.mark.asyncio
async def test_outcome_rewrite(clean_kernel):
    async def rewrite_hook(mod, ctx, ev, next_fn):
        mutated = ev.with_payload(injected="policy_reminder")
        return EventResult.rewrite(mutated, reason="policy reminder")

    mod = DummyMod("rewriter", ModPriority.EXECUTION, rewrite_hook)
    clean_kernel.register_mod(mod)

    received_event = None

    async def terminal(ev: AlphaEvent) -> EventResult:
        nonlocal received_event
        received_event = ev
        return EventResult.continue_(ev)

    event = AlphaEvent(name="prompt.compose", payload={"text": "hello"}, correlation=CorrelationContext.create())
    res = await clean_kernel.dispatch(event, terminal_handler=terminal)

    assert received_event is not None
    assert received_event.payload.get("injected") == "policy_reminder"
    assert received_event.payload.get("text") == "hello"
    assert res.outcome == EventOutcome.CONTINUE
    assert res.metadata["mod_rewrites"][0]["mod"] == "rewriter"


@pytest.mark.asyncio
async def test_rewrite_cannot_change_event_identity(clean_kernel):
    async def rewrite_hook(mod, ctx, ev, next_fn):
        return EventResult.rewrite(ev.copy(name="run.admit"), reason="attempted reroute")

    mod = DummyMod("rewriter", ModPriority.EXECUTION, rewrite_hook)
    clean_kernel.register_mod(mod)
    event = AlphaEvent(name="tool.requested", correlation=CorrelationContext.create())

    result = await clean_kernel.dispatch(event)

    assert result.outcome == EventOutcome.DENY
    assert result.reason.startswith("INVALID_REWRITE")


@pytest.mark.asyncio
async def test_outcome_answer_short_circuits(clean_kernel):
    terminal_called = False

    async def terminal(ev: AlphaEvent) -> EventResult:
        nonlocal terminal_called
        terminal_called = True
        return EventResult.continue_(ev)

    async def answer_hook(mod, ctx, ev, next_fn):
        return EventResult.answer(ev, response_payload={"cached": True}, reason="cache hit")

    mod_answer = DummyMod("cache_mod", ModPriority.EXECUTION, answer_hook)
    mod_after = DummyMod("after_mod", ModPriority.VERIFICATION)

    clean_kernel.register_mod(mod_answer)
    clean_kernel.register_mod(mod_after)

    event = AlphaEvent(name="tool.requested", payload={}, correlation=CorrelationContext.create())
    res = await clean_kernel.dispatch(event, terminal_handler=terminal)

    assert res.outcome == EventOutcome.ANSWER
    assert res.response_payload == {"cached": True}
    assert res.reason == "cache hit"
    assert not mod_after.invoked
    assert not terminal_called


@pytest.mark.asyncio
async def test_outcome_deny_halts_pipeline(clean_kernel):
    terminal_called = False

    async def terminal(ev: AlphaEvent) -> EventResult:
        nonlocal terminal_called
        terminal_called = True
        return EventResult.continue_(ev)

    async def deny_hook(mod, ctx, ev, next_fn):
        return EventResult.deny(ev, reason="Unauthorized tool execution")

    mod_deny = DummyMod("guard", ModPriority.SECURITY, deny_hook)
    mod_after = DummyMod("after", ModPriority.EXECUTION)

    clean_kernel.register_mod(mod_deny)
    clean_kernel.register_mod(mod_after)

    event = AlphaEvent(name="tool.requested", payload={}, correlation=CorrelationContext.create())
    res = await clean_kernel.dispatch(event, terminal_handler=terminal)

    assert res.outcome == EventOutcome.DENY
    assert "Unauthorized" in res.reason
    assert not mod_after.invoked
    assert not terminal_called


@pytest.mark.asyncio
async def test_outcome_defer_parks_event(clean_kernel):
    async def defer_hook(mod, ctx, ev, next_fn):
        return EventResult.defer(ev, reason="Awaiting operator confirmation", response_payload={"hold_id": "123"})

    mod_defer = DummyMod("approver", ModPriority.SECURITY, defer_hook)
    clean_kernel.register_mod(mod_defer)

    event = AlphaEvent(name="tool.requested", payload={}, correlation=CorrelationContext.create())
    res = await clean_kernel.dispatch(event)

    assert res.outcome == EventOutcome.DEFER
    assert res.reason == "Awaiting operator confirmation"
    assert res.response_payload == {"hold_id": "123"}


@pytest.mark.asyncio
async def test_fault_isolation_non_critical(clean_kernel):
    """Observability mods that fail must not take down the pipeline (fail open)."""

    async def crash_hook(mod, ctx, ev, next_fn):
        raise ValueError("Telemetry backend down")

    telemetry_mod = DummyMod("telemetry", ModPriority.OBSERVABILITY, crash_hook)
    clean_kernel.register_mod(telemetry_mod)

    terminal_called = False

    async def terminal(ev: AlphaEvent) -> EventResult:
        nonlocal terminal_called
        terminal_called = True
        return EventResult.continue_(ev)

    event = AlphaEvent(name="tool.requested", payload={}, correlation=CorrelationContext.create())
    res = await clean_kernel.dispatch(event, terminal_handler=terminal)

    assert terminal_called
    assert res.outcome == EventOutcome.CONTINUE

    # Verify journal recorded the fault
    journal = clean_kernel.get_journal()
    assert any("FAULT_ISOLATED: telemetry" in j.get("reason", "") for j in journal)


@pytest.mark.asyncio
async def test_fault_isolation_critical_security_fails_closed(clean_kernel):
    """Critical security mods that fail MUST fail closed (return DENY)."""

    async def crash_security_hook(mod, ctx, ev, next_fn):
        raise RuntimeError("Security database unreadable")

    sec_mod = DummyMod("sec_gate", ModPriority.SECURITY, crash_security_hook)
    clean_kernel.register_mod(sec_mod)

    terminal_called = False

    async def terminal(ev: AlphaEvent) -> EventResult:
        nonlocal terminal_called
        terminal_called = True
        return EventResult.continue_(ev)

    event = AlphaEvent(name="tool.requested", payload={}, correlation=CorrelationContext.create())
    res = await clean_kernel.dispatch(event, terminal_handler=terminal)

    # Invariant: Security fault cannot fail open!
    assert not terminal_called
    assert res.outcome == EventOutcome.DENY
    assert "CRITICAL_MOD_FAULT: sec_gate" in res.reason


def test_external_mod_declarations_are_not_implicit_grants(clean_kernel):
    mod = DummyMod(
        "external",
        ModPriority.USER_EXTENSIONS,
        required_capabilities={"tools:read"},
    )
    clean_kernel.register_mod(mod)

    assert clean_kernel.get_granted_capabilities("external") == set()

    clean_kernel.register_mod(mod, granted_capabilities={"tools:read"})
    assert clean_kernel.get_granted_capabilities("external") == {"tools:read"}


@pytest.mark.asyncio
async def test_critical_mod_invalid_result_fails_closed(clean_kernel):
    async def invalid_hook(mod, ctx, ev, next_fn):
        return None

    sec_mod = DummyMod("sec_gate", ModPriority.SECURITY, invalid_hook)
    clean_kernel.register_mod(sec_mod)
    terminal_called = False

    async def terminal(ev: AlphaEvent) -> EventResult:
        nonlocal terminal_called
        terminal_called = True
        return EventResult.continue_(ev)

    result = await clean_kernel.dispatch(
        AlphaEvent(name="tool.requested", correlation=CorrelationContext.create()),
        terminal_handler=terminal,
    )

    assert result.outcome == EventOutcome.DENY
    assert "invalid result NoneType" in result.reason
    assert not terminal_called


@pytest.mark.asyncio
async def test_event_subscription_filtering(clean_kernel):
    tool_mod = DummyMod("tool_only", ModPriority.EXECUTION, subscribed_events={"tool.requested"})
    glob_mod = DummyMod("glob_mod", ModPriority.EXECUTION, subscribed_events={"agent.*"})
    all_mod = DummyMod("all_mod", ModPriority.EXECUTION, subscribed_events=None)

    clean_kernel.register_mod(tool_mod)
    clean_kernel.register_mod(glob_mod)
    clean_kernel.register_mod(all_mod)

    # Dispatch tool.requested
    ev1 = AlphaEvent(name="tool.requested", payload={}, correlation=CorrelationContext.create())
    await clean_kernel.dispatch(ev1)
    assert tool_mod.invoked
    assert not glob_mod.invoked
    assert all_mod.invoked

    # Reset
    tool_mod.invoked = False
    all_mod.invoked = False

    # Dispatch agent.spawn_requested
    ev2 = AlphaEvent(name="agent.spawn_requested", payload={}, correlation=CorrelationContext.create())
    await clean_kernel.dispatch(ev2)
    assert not tool_mod.invoked
    assert glob_mod.invoked
    assert all_mod.invoked


@pytest.mark.asyncio
async def test_correlation_lineage_preserved(clean_kernel):
    corr = CorrelationContext.create(
        trace_id="trc_abc",
        run_id="run_123",
        task_id="tsk_999",
        tool_call_id="call_456",
    )
    event = AlphaEvent(name="test.event", payload={}, correlation=corr)

    seen_corr = None

    async def check_corr(mod, ctx, ev, next_fn):
        nonlocal seen_corr
        seen_corr = ev.correlation
        return await next_fn(ev)

    mod = DummyMod("auditor", ModPriority.KERNEL, check_corr)
    clean_kernel.register_mod(mod)

    await clean_kernel.dispatch(event)
    assert seen_corr is not None
    assert seen_corr.trace_id == "trc_abc"
    assert seen_corr.run_id == "run_123"
    assert seen_corr.task_id == "tsk_999"
    assert seen_corr.tool_call_id == "call_456"


@pytest.mark.asyncio
async def test_outcome_observe(clean_kernel):
    downstream_called = False

    async def observe_hook(mod, ctx, ev, next_fn):
        return EventResult.observe(ev)

    async def downstream_hook(mod, ctx, ev, next_fn):
        nonlocal downstream_called
        downstream_called = True
        return await next_fn(ev)

    mod_obs = DummyMod("observer", ModPriority.OBSERVABILITY, observe_hook)
    mod_down = DummyMod("downstream", ModPriority.USER_EXTENSIONS, downstream_hook)

    clean_kernel.register_mod(mod_obs)
    clean_kernel.register_mod(mod_down)

    event = AlphaEvent(name="test.observe", payload={"key": "val"}, correlation=CorrelationContext.create())
    res = await clean_kernel.dispatch(event)

    assert mod_obs.invoked
    assert downstream_called
    assert res.outcome in (EventOutcome.CONTINUE, EventOutcome.OBSERVE)


@pytest.mark.asyncio
async def test_mod_returning_continue_without_calling_next_fn_executes_downstream(clean_kernel):
    downstream_called = False

    async def direct_continue_hook(mod, ctx, ev, next_fn):
        # Explicitly returns continue WITHOUT invoking next_fn
        return EventResult.continue_(ev)

    async def downstream_hook(mod, ctx, ev, next_fn):
        nonlocal downstream_called
        downstream_called = True
        return await next_fn(ev)

    mod_first = DummyMod("first", ModPriority.KERNEL, direct_continue_hook)
    mod_second = DummyMod("second", ModPriority.EXECUTION, downstream_hook)

    clean_kernel.register_mod(mod_first)
    clean_kernel.register_mod(mod_second)

    event = AlphaEvent(name="test.event", payload={}, correlation=CorrelationContext.create())
    res = await clean_kernel.dispatch(event)

    assert mod_first.invoked
    assert downstream_called
    assert res.outcome == EventOutcome.CONTINUE


@pytest.mark.asyncio
async def test_outcome_retry_and_escalate(clean_kernel):
    async def retry_hook(mod, ctx, ev, next_fn):
        return EventResult.retry(ev, reason="Network rate limit exceeded")

    mod_retry = DummyMod("retry_mod", ModPriority.EXECUTION, retry_hook)
    clean_kernel.register_mod(mod_retry)

    event = AlphaEvent(name="tool.requested", payload={}, correlation=CorrelationContext.create())
    res = await clean_kernel.dispatch(event)
    assert res.outcome == EventOutcome.RETRY
    assert "rate limit" in res.reason

    clean_kernel.unregister_mod("retry_mod")

    async def escalate_hook(mod, ctx, ev, next_fn):
        return EventResult.escalate(ev, reason="Unrecoverable state requires supervisor")

    mod_escalate = DummyMod("escalate_mod", ModPriority.RECOVERY, escalate_hook)
    clean_kernel.register_mod(mod_escalate)

    res_esc = await clean_kernel.dispatch(event)
    assert res_esc.outcome == EventOutcome.ESCALATE
    assert "supervisor" in res_esc.reason
