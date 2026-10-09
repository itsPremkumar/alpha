"""Unit tests for ModKernelMiddleware."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from alpha.mods.context import CapabilityContext
from alpha.mods.kernel import ModKernel
from alpha.mods.middleware import ModKernelMiddleware
from alpha.mods.types import (
    AlphaEvent,
    EventResult,
    ModPriority,
    NextHandler,
)


class InterceptMod:
    def __init__(self, name: str, priority: int, action: str):
        self.name = name
        self.version = "1.0.0"
        self.priority = priority
        self.required_capabilities = set()
        self.subscribed_events = None
        self.action = action

    async def handle(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        if self.action == "deny":
            return EventResult.deny(event, reason="Policy forbids this tool")
        elif self.action == "defer":
            return EventResult.defer(event, reason="Operator must approve", response_payload={"hold_id": "hold-123", "risk_level": "R4"})
        elif self.action == "answer":
            return EventResult.answer(event, response_payload={"output": "Direct answer from mod"})
        elif self.action == "retry":
            return EventResult.retry(event, reason="Retry policy evaluation")
        elif self.action == "escalate":
            return EventResult.escalate(event, reason="Escalation required")
        elif self.action == "observe":
            return EventResult.observe(event)
        return await next_fn(event)


@pytest.mark.asyncio
async def test_middleware_wrap_tool_call_deny():
    kernel = ModKernel()
    kernel.register_mod(InterceptMod("denier", ModPriority.SECURITY, "deny"))
    mw = ModKernelMiddleware(kernel=kernel)

    req = SimpleNamespace(
        name="dangerous_tool",
        args={"foo": "bar"},
        id="call_deny_1",
        runtime=SimpleNamespace(run_id="run_1", trace_id="trc_1", context={}),
    )

    handler_called = False

    async def dummy_handler(r):
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="ok", tool_call_id=r.id)

    res = await mw.awrap_tool_call(req, dummy_handler)
    assert not handler_called
    assert isinstance(res, ToolMessage)
    assert res.status == "error"
    assert "Policy forbids this tool" in res.content


@pytest.mark.asyncio
async def test_middleware_wrap_tool_call_defer():
    kernel = ModKernel()
    kernel.register_mod(InterceptMod("deferrer", ModPriority.SECURITY, "defer"))
    mw = ModKernelMiddleware(kernel=kernel)

    req = SimpleNamespace(
        name="hold_tool",
        args={},
        id="call_defer_1",
        runtime=SimpleNamespace(run_id="run_1", trace_id="trc_1", context={}),
    )

    async def dummy_handler(r):
        return ToolMessage(content="ok", tool_call_id=r.id)

    res = await mw.awrap_tool_call(req, dummy_handler)
    assert isinstance(res, ToolMessage)
    assert res.status == "error"
    assert "Action held for operator approval" in res.content
    assert "hold-123" in res.content
    assert "R4" in res.content
    assert "not executed" in res.content


@pytest.mark.asyncio
async def test_middleware_wrap_tool_call_answer():
    kernel = ModKernel()
    kernel.register_mod(InterceptMod("answerer", ModPriority.EXECUTION, "answer"))
    mw = ModKernelMiddleware(kernel=kernel)

    req = SimpleNamespace(
        name="cached_tool",
        args={},
        id="call_ans_1",
        runtime=SimpleNamespace(run_id="run_1", trace_id="trc_1", context={}),
    )

    async def dummy_handler(r):
        return ToolMessage(content="real execution", tool_call_id=r.id)

    res = await mw.awrap_tool_call(req, dummy_handler)
    assert isinstance(res, ToolMessage)
    assert res.status == "success"
    assert "Direct answer from mod" in res.content


@pytest.mark.asyncio
async def test_middleware_wrap_tool_call_continue():
    kernel = ModKernel()
    mw = ModKernelMiddleware(kernel=kernel)

    req = SimpleNamespace(
        name="normal_tool",
        args={},
        id="call_pass_1",
        runtime=SimpleNamespace(run_id="run_1", trace_id="trc_1", context={}),
    )

    async def dummy_handler(r):
        return ToolMessage(content="executed successfully", tool_call_id=r.id, status="success")

    res = await mw.awrap_tool_call(req, dummy_handler)
    assert isinstance(res, ToolMessage)
    assert res.status == "success"
    assert res.content == "executed successfully"


@pytest.mark.asyncio
async def test_middleware_wrap_tool_call_observe():
    kernel = ModKernel()
    kernel.register_mod(InterceptMod("observer", ModPriority.OBSERVABILITY, "observe"))
    mw = ModKernelMiddleware(kernel=kernel)

    req = SimpleNamespace(
        name="read_tool",
        args={},
        id="call_obs_1",
        runtime=SimpleNamespace(run_id="run_obs", trace_id="trc_obs", context={}),
    )

    async def dummy_handler(r):
        return ToolMessage(content="observed and executed", tool_call_id=r.id, status="success")

    res = await mw.awrap_tool_call(req, dummy_handler)
    assert isinstance(res, ToolMessage)
    assert res.status == "success"
    assert res.content == "observed and executed"


@pytest.mark.asyncio
async def test_middleware_wrap_tool_call_retry():
    kernel = ModKernel()
    kernel.register_mod(InterceptMod("retrier", ModPriority.EXECUTION, "retry"))
    mw = ModKernelMiddleware(kernel=kernel)

    req = SimpleNamespace(
        name="retry_tool",
        args={},
        id="call_retry_1",
        runtime=SimpleNamespace(run_id="run_retry", trace_id="trc_retry", context={}),
    )

    handler_invocations = 0

    async def dummy_handler(r):
        nonlocal handler_invocations
        handler_invocations += 1
        return ToolMessage(content="success on retry", tool_call_id=r.id, status="success")

    res = await mw.awrap_tool_call(req, dummy_handler)
    # No retry scheduler exists in the middleware: RETRY is fail-closed, the
    # tool is not executed and an honest error is returned instead.
    assert handler_invocations == 0
    assert res.status == "error"
    assert "retry" in res.content.lower()


@pytest.mark.asyncio
async def test_middleware_wrap_tool_call_escalate():
    kernel = ModKernel()
    kernel.register_mod(InterceptMod("escalator", ModPriority.RECOVERY, "escalate"))
    mw = ModKernelMiddleware(kernel=kernel)

    req = SimpleNamespace(
        name="sensitive_tool",
        args={},
        id="call_esc_1",
        runtime=SimpleNamespace(run_id="run_esc", trace_id="trc_esc", context={}),
    )

    async def dummy_handler(r):
        return ToolMessage(content="executed", tool_call_id=r.id)

    res = await mw.awrap_tool_call(req, dummy_handler)
    assert isinstance(res, ToolMessage)
    assert res.status == "error"
    assert "Escalation required" in res.content


@pytest.mark.asyncio
async def test_middleware_dispatches_tool_completed_event():
    kernel = ModKernel()
    mw = ModKernelMiddleware(kernel=kernel)

    req = SimpleNamespace(
        name="test_tool",
        args={"CommandLine": "pytest"},
        id="call_comp_1",
        runtime=SimpleNamespace(run_id="run_comp", trace_id="trc_comp", context={}),
    )

    async def dummy_handler(r):
        return ToolMessage(content="5 passed", tool_call_id=r.id, status="success")

    res = await mw.awrap_tool_call(req, dummy_handler)
    assert res.status == "success"

    # Verify tool.completed event was dispatched to the kernel journal
    journal = kernel.get_journal()
    completed = [j for j in journal if j.get("event_name") == "tool.completed"]
    assert len(completed) == 1
    assert completed[0]["correlation"]["run_id"] == "run_comp"


# ---------------------------------------------------------------------------
# Synchronous hooks. `alpha --json` drives `AlphaClient.stream()`, a synchronous
# graph invocation: a hook overridden only in its async form leaves the graph
# node with no sync callable and the run dies before the first token. ModKernel
# is a security control, so the sync hooks must ENFORCE the same verdicts rather
# than no-op -- a passthrough would turn the sync path into a fail-open bypass of
# every DENY/DEFER the async path applies.
# ---------------------------------------------------------------------------


def test_middleware_defines_both_the_sync_and_async_wrap_hooks():
    """Both halves, or the assembled stack is async-only and the sync run dies."""
    from langchain.agents.middleware import AgentMiddleware

    for sync_name, async_name in (("wrap_tool_call", "awrap_tool_call"), ("wrap_model_call", "awrap_model_call")):
        assert getattr(ModKernelMiddleware, sync_name) is not getattr(AgentMiddleware, sync_name), f"sync {sync_name} is not overridden"
        assert getattr(ModKernelMiddleware, async_name) is not getattr(AgentMiddleware, async_name), f"async {async_name} is not overridden"


def test_middleware_sync_wrap_tool_call_deny():
    """The sync path enforces DENY: the tool must never reach the handler."""
    kernel = ModKernel()
    kernel.register_mod(InterceptMod("sync_denier", ModPriority.SECURITY, "deny"))
    mw = ModKernelMiddleware(kernel=kernel)

    req = SimpleNamespace(
        name="dangerous_tool",
        args={"foo": "bar"},
        id="call_sync_deny_1",
        runtime=SimpleNamespace(run_id="run_1", trace_id="trc_1", context={}),
    )

    handler_called = False

    def sync_handler(r):
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="ok", tool_call_id=r.id)

    res = mw.wrap_tool_call(req, sync_handler)
    assert not handler_called, "a denied tool executed on the sync path"
    assert isinstance(res, ToolMessage)
    assert res.status == "error"
    assert "Policy forbids this tool" in res.content


def test_middleware_sync_wrap_tool_call_defer_holds_the_action():
    """DEFER is an operator hold on the sync path too, with the same receipt."""
    kernel = ModKernel()
    kernel.register_mod(InterceptMod("sync_deferrer", ModPriority.SECURITY, "defer"))
    mw = ModKernelMiddleware(kernel=kernel)

    req = SimpleNamespace(
        name="hold_tool",
        args={},
        id="call_sync_defer_1",
        runtime=SimpleNamespace(run_id="run_1", trace_id="trc_1", context={}),
    )

    executed = False

    def sync_handler(r):
        nonlocal executed
        executed = True
        return ToolMessage(content="ok", tool_call_id=r.id)

    res = mw.wrap_tool_call(req, sync_handler)
    assert not executed, "a deferred tool executed on the sync path"
    assert res.status == "error"
    assert "Action held for operator approval" in res.content
    assert "hold-123" in res.content
    assert "not executed" in res.content


def test_middleware_sync_wrap_tool_call_continue_executes_and_journals():
    """CONTINUE still executes, and the completion event still reaches the journal."""
    kernel = ModKernel()
    mw = ModKernelMiddleware(kernel=kernel)

    req = SimpleNamespace(
        name="normal_tool",
        args={"CommandLine": "pytest"},
        id="call_sync_pass_1",
        runtime=SimpleNamespace(run_id="run_sync", trace_id="trc_sync", context={}),
    )

    def sync_handler(r):
        return ToolMessage(content="executed successfully", tool_call_id=r.id, status="success")

    res = mw.wrap_tool_call(req, sync_handler)
    assert isinstance(res, ToolMessage)
    assert res.status == "success"
    assert res.content == "executed successfully"

    journal = kernel.get_journal()
    completed = [j for j in journal if j.get("event_name") == "tool.completed"]
    assert len(completed) == 1, "the sync path skipped the tool.completed dispatch"
    assert completed[0]["correlation"]["run_id"] == "run_sync"


def test_middleware_sync_wrap_model_call_dispatches_turn_complete():
    """The sync model hook reaches the verification gate, not just the handler."""
    kernel = ModKernel()
    mw = ModKernelMiddleware(kernel=kernel)

    req = SimpleNamespace(messages=[], runtime=SimpleNamespace(context={}))

    def sync_handler(r):
        return SimpleNamespace(messages=[AIMessage(content="hello from the sync path")])

    res = mw.wrap_model_call(req, sync_handler)

    journal = kernel.get_journal()
    turns = [j for j in journal if j.get("event_name") == "turn.complete"]
    assert len(turns) == 1, "the sync model hook never reached the kernel"
    assert res.messages[-1].content == "hello from the sync path"


def test_middleware_sync_and_async_tool_hooks_agree_on_the_same_verdict():
    """Byte-parity between the two paths on one kernel: same verdict, same text.

    Guards the shared translation rather than the two hooks separately -- a
    refactor that lets one path drift would otherwise pass both suites.
    """
    import asyncio

    def _build():
        kernel = ModKernel()
        kernel.register_mod(InterceptMod("parity", ModPriority.SECURITY, "deny"))
        return ModKernelMiddleware(kernel=kernel), SimpleNamespace(
            name="dangerous_tool",
            args={"foo": "bar"},
            id="call_parity",
            runtime=SimpleNamespace(run_id="r", trace_id="t", context={}),
        )

    async def _async_handler(r):
        return ToolMessage(content="ok", tool_call_id=r.id)

    def _sync_handler(r):
        return ToolMessage(content="ok", tool_call_id=r.id)

    mw_async, req_async = _build()
    async_result = asyncio.run(mw_async.awrap_tool_call(req_async, _async_handler))

    mw_sync, req_sync = _build()
    sync_result = mw_sync.wrap_tool_call(req_sync, _sync_handler)

    assert sync_result.content == async_result.content
    assert sync_result.status == async_result.status
    assert sync_result.tool_call_id == async_result.tool_call_id
