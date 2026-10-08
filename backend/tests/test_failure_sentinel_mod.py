"""Unit tests for FailureSentinelMod (Error Fingerprinting, Anti-Loop, Auto-Repair)."""

import pytest

from alpha.mods.controllers.failure_sentinel_mod import FailureSentinelMod
from alpha.mods.kernel import ModKernel
from alpha.mods.types import AlphaEvent, CorrelationContext, EventOutcome


@pytest.fixture
def sentinel_mod():
    return FailureSentinelMod(
        loop_threshold=3,
        initial_backoff_seconds=1.0,
        max_backoff_seconds=10.0,
    )


@pytest.fixture
def kernel(sentinel_mod):
    k = ModKernel()
    k.register_mod(sentinel_mod)
    return k


def test_failure_fingerprint_normalization(sentinel_mod):
    err1 = "ConnectionError: connection timed out at 0x7f8a9b1c while calling https://api.openai.com at 2026-10-08T12:00:00Z"
    err2 = "ConnectionError: connection timed out at 0x11223344 while calling https://api.openai.com at 2026-10-08T13:45:21Z"

    fp1 = sentinel_mod.compute_fingerprint(err1, tool_name="fetch_url", bot_name="researcher")
    fp2 = sentinel_mod.compute_fingerprint(err2, tool_name="fetch_url", bot_name="researcher")

    # Invariant: Despite different memory addresses and timestamps, normalized fingerprint MUST match!
    assert fp1 == fp2


@pytest.mark.asyncio
async def test_failure_sentinel_transient_retry(kernel):
    corr = CorrelationContext.create(run_id="run_retry_test", attempt=1)
    ev_rate_limit = AlphaEvent(
        name="tool.completed",
        payload={
            "tool_name": "run_command",
            "exit_code": 1,
            "status": "error",
            "error": "RateLimitError: 429 Too Many Requests. Try again in 2s.",
            "reason_code": "provider_rate_limit",
        },
        correlation=corr,
    )

    res = await kernel.dispatch(ev_rate_limit)
    assert res.outcome == EventOutcome.RETRY
    assert "TRANSIENT_FAILURE" in res.reason
    assert res.metadata["retry_delay"] >= 1.0


@pytest.mark.asyncio
async def test_failure_sentinel_anti_loop_detection(kernel, sentinel_mod):
    corr = CorrelationContext.create(run_id="run_loop_test", agent_id="coder")
    ev_fail = AlphaEvent(
        name="tool.completed",
        payload={
            "tool_name": "run_command",
            "exit_code": 1,
            "status": "error",
            "error": "Fatal: database table 'customers' does not exist",
        },
        correlation=corr,
    )

    # Attempt 1 -> classified as permanent or error
    res1 = await kernel.dispatch(ev_fail)
    assert res1.outcome in (EventOutcome.RETRY, EventOutcome.DENY, EventOutcome.ESCALATE)

    # Attempt 2
    res2 = await kernel.dispatch(ev_fail)
    assert res2.outcome in (EventOutcome.RETRY, EventOutcome.DENY, EventOutcome.ESCALATE)

    # Attempt 3: reaches loop_threshold (3) -> ANTI_LOOP_TRIGGERED!
    res3 = await kernel.dispatch(ev_fail)
    assert res3.outcome == EventOutcome.ESCALATE
    assert "ANTI_LOOP_TRIGGERED" in res3.reason
    assert res3.metadata.get("anti_loop") is True

    # Verify active loops recorded
    active_loops = sentinel_mod.get_active_loops()
    assert len(active_loops) >= 1


@pytest.mark.asyncio
async def test_failure_sentinel_capability_missing_escalates(kernel):
    corr = CorrelationContext.create(run_id="run_cap_test", agent_id="tester")
    ev_blocked = AlphaEvent(
        name="tool.completed",
        payload={
            "tool_name": "deploy_service",
            "exit_code": 1,
            "status": "error",
            "error": "PermissionDenied: agent lacks deployment permission",
            "reason_code": "agent_blocked",
        },
        correlation=corr,
    )

    res = await kernel.dispatch(ev_blocked)
    assert res.outcome == EventOutcome.ESCALATE
    assert "CAPABILITY_FAILURE" in res.reason
    assert res.metadata.get("auto_repair") == "reassign"


@pytest.mark.asyncio
async def test_failure_sentinel_ignores_successful_tools(kernel):
    corr = CorrelationContext.create(run_id="run_ok_test")
    ev_ok = AlphaEvent(
        name="tool.completed",
        payload={
            "tool_name": "view_file",
            "exit_code": 0,
            "status": "success",
            "content": "file contents",
        },
        correlation=corr,
    )

    res = await kernel.dispatch(ev_ok)
    assert res.outcome == EventOutcome.CONTINUE
