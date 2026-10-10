"""Unit tests for ContextBudgetMod — the per-run token ceiling."""

import pytest

from alpha.mods.context import CapabilityContext
from alpha.mods.context_budget import CHARS_PER_TOKEN, ContextBudgetMod, estimate_tokens
from alpha.mods.kernel import ModKernel
from alpha.mods.types import AlphaEvent, CorrelationContext, EventOutcome


@pytest.fixture
def kernel():
    return ModKernel()


def _ctx(kernel, caps):
    return CapabilityContext("context_budget", set(caps), kernel)


def _model_event(run_id="run_1", messages=None, usage=None):
    payload = {"messages": messages if messages is not None else []}
    if usage is not None:
        payload["usage"] = usage
    return AlphaEvent(name="model.requested", payload=payload, correlation=CorrelationContext.create(run_id=run_id))


async def _pass(event):
    from alpha.mods.types import EventResult

    return EventResult.continue_(event)


def test_estimate_tokens_is_coarse_but_nonzero():
    assert estimate_tokens("") == 1
    assert estimate_tokens("x" * 400) == 100
    assert estimate_tokens("x" * 7) == 1


@pytest.mark.asyncio
async def test_measurable_usage_is_labelled_measured(kernel):
    mod = ContextBudgetMod()
    ctx = _ctx(kernel, {"evidence:record"})

    await mod.handle(
        ctx,
        _model_event(usage={"prompt_tokens": 1200, "completion_tokens": 300}),
        _pass,
    )

    run = mod.get_run("run_1")
    assert run["basis"] == "measured"
    assert run["total_tokens"] == 1500


@pytest.mark.asyncio
async def test_absent_usage_is_labelled_estimated_and_accumulates(kernel):
    mod = ContextBudgetMod()
    ctx = _ctx(kernel, {"evidence:record"})

    await mod.handle(ctx, _model_event(messages=["x" * 400]), _pass)
    await mod.handle(ctx, _model_event(messages=["x" * 400]), _pass)

    run = mod.get_run("run_1")
    assert run["basis"] == "estimated"
    assert run["model_calls"] == 2
    # The estimate is a running total, so two calls cost more than one.
    assert run["prompt_tokens"] == 2 * (400 // CHARS_PER_TOKEN)


@pytest.mark.asyncio
async def test_run_over_the_ceiling_is_denied(kernel):
    mod = ContextBudgetMod(max_tokens_per_run=1000)
    ctx = _ctx(kernel, {"evidence:record"})

    result = await mod.handle(ctx, _model_event(usage={"prompt_tokens": 5000, "completion_tokens": 0}), _pass)

    assert result.outcome == EventOutcome.DENY
    assert "BUDGET_EXHAUSTED" in result.reason
    assert result.metadata["basis"] == "measured"


@pytest.mark.asyncio
async def test_run_under_the_ceiling_continues(kernel):
    mod = ContextBudgetMod(max_tokens_per_run=10_000)
    ctx = _ctx(kernel, {"evidence:record"})

    result = await mod.handle(ctx, _model_event(usage={"prompt_tokens": 10, "completion_tokens": 0}), _pass)

    assert result.outcome == EventOutcome.CONTINUE


@pytest.mark.asyncio
async def test_run_admitted_with_a_wider_budget_is_clamped_not_raised(kernel):
    """A run cannot raise its own ceiling by asking for a smaller one."""
    mod = ContextBudgetMod(max_tokens_per_run=1000)
    ctx = _ctx(kernel, {"evidence:record"})

    await mod.handle(ctx, AlphaEvent(name="run.admit", payload={"max_tokens_per_run": 5000}, correlation=CorrelationContext.create()), _pass)
    assert mod.snapshot()["max_tokens_per_run"] == 1000

    await mod.handle(ctx, AlphaEvent(name="run.admit", payload={"max_tokens_per_run": 400}, correlation=CorrelationContext.create()), _pass)
    assert mod.snapshot()["max_tokens_per_run"] == 400


@pytest.mark.asyncio
async def test_run_admitted_with_a_nonsense_budget_is_refused(kernel):
    mod = ContextBudgetMod()
    ctx = _ctx(kernel, {"evidence:record"})

    result = await mod.handle(
        ctx,
        AlphaEvent(name="run.admit", payload={"max_tokens_per_run": "not a number"}, correlation=CorrelationContext.create()),
        _pass,
    )
    assert result.outcome == EventOutcome.DENY

    zero = await mod.handle(
        ctx,
        AlphaEvent(name="run.admit", payload={"max_tokens_per_run": 0}, correlation=CorrelationContext.create()),
        _pass,
    )
    assert zero.outcome == EventOutcome.DENY


@pytest.mark.asyncio
async def test_warning_fires_once_and_does_not_deny(kernel):
    mod = ContextBudgetMod(max_tokens_per_run=1000, warn_fraction=0.5)
    ctx = _ctx(kernel, {"evidence:record"})

    first = await mod.handle(ctx, _model_event(usage={"prompt_tokens": 600, "completion_tokens": 0}), _pass)
    second = await mod.handle(ctx, _model_event(usage={"prompt_tokens": 900, "completion_tokens": 0}), _pass)

    assert first.outcome == EventOutcome.CONTINUE
    assert second.outcome == EventOutcome.CONTINUE
    assert mod.get_run("run_1")["warned"] is True


@pytest.mark.asyncio
async def test_budget_query_is_answered_directly(kernel):
    mod = ContextBudgetMod()
    ctx = _ctx(kernel, {"evidence:record"})

    await mod.handle(ctx, _model_event(usage={"prompt_tokens": 42, "completion_tokens": 0}), _pass)
    result = await mod.handle(
        ctx,
        AlphaEvent(name="budget.query", payload={}, correlation=CorrelationContext.create(run_id="run_1")),
        _pass,
    )

    assert result.outcome == EventOutcome.ANSWER
    assert result.response_payload["total_tokens"] == 42


@pytest.mark.asyncio
async def test_run_completed_records_a_final_receipt_and_forgets_the_run(kernel):
    mod = ContextBudgetMod()
    ctx = _ctx(kernel, {"evidence:record"})

    await mod.handle(ctx, _model_event(usage={"prompt_tokens": 10, "completion_tokens": 0}), _pass)
    await mod.handle(ctx, AlphaEvent(name="run.completed", payload={}, correlation=CorrelationContext.create(run_id="run_1")), _pass)

    assert mod.get_run("run_1") is None


@pytest.mark.asyncio
async def test_tracked_runs_are_bounded(kernel):
    mod = ContextBudgetMod()
    ctx = _ctx(kernel, {"evidence:record"})

    for i in range(300):
        await mod.handle(ctx, _model_event(run_id=f"run_{i}", usage={"prompt_tokens": 1, "completion_tokens": 0}), _pass)

    assert mod.snapshot()["tracked_runs"] <= 256


def test_snapshot_reports_nothing_as_zero():
    mod = ContextBudgetMod()
    snapshot = mod.snapshot()
    assert snapshot["tracked_runs"] == 0
    assert snapshot["runs"] == []
    assert snapshot["max_tokens_per_run"] > 0
