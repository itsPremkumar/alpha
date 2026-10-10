"""Unit tests for ReplayMod — per-turn edit recording with honest before-state."""

import pytest

from alpha.mods.context import CapabilityContext
from alpha.mods.kernel import ModKernel
from alpha.mods.replay import MAX_STEPS_PER_TURN, ReplayMod, _diff
from alpha.mods.types import AlphaEvent, CorrelationContext, EventOutcome


@pytest.fixture
def kernel():
    return ModKernel()


def _ctx(kernel, caps):
    return CapabilityContext("replay_theater", set(caps), kernel)


def _requested_event(path, content, call_id, run_id="run_1", turn_id="turn_1"):
    return AlphaEvent(
        name="tool.requested",
        payload={"tool_name": "write_to_file", "tool_args": {"TargetFile": path, "ReplacementContent": content}},
        correlation=CorrelationContext.create(run_id=run_id, turn_id=turn_id, tool_call_id=call_id),
    )


def _completed_event(path, content, call_id, run_id="run_1", turn_id="turn_1"):
    return AlphaEvent(
        name="tool.completed",
        payload={
            "tool_name": "write_to_file",
            "tool_args": {"TargetFile": path, "ReplacementContent": content},
            "tool_call_id": call_id,
            "status": "success",
        },
        correlation=CorrelationContext.create(run_id=run_id, turn_id=turn_id, tool_call_id=call_id),
    )


def _replay_event(turn_id="turn_1"):
    return AlphaEvent(
        name="replay.requested",
        payload={"turn_id": turn_id},
        correlation=CorrelationContext.create(run_id="run_1", turn_id=turn_id),
    )


def test_diff_of_none_is_empty_never_a_fabricated_before():
    """A missing before-state must not render as an empty file."""
    assert _diff(None, "new content") == ""


def test_diff_of_an_empty_before_is_a_real_diff():
    assert "new content" in _diff("", "new content")


@pytest.mark.asyncio
async def test_write_records_a_step_with_a_real_before_state(kernel, tmp_path):
    target = tmp_path / "app.py"
    target.write_text("def old():\n    return 1\n", encoding="utf-8")

    ctx = _ctx(kernel, {"fs:read", "storage:write"})
    mod = ReplayMod()

    await mod.handle(ctx, _requested_event(str(target), "def new():\n    return 2\n", "call_1"), lambda e: _pass(e))
    await mod.handle(ctx, _completed_event(str(target), "def new():\n    return 2\n", "call_1"), lambda e: _pass(e))

    report = mod.replay(ctx, "turn_1")
    assert report["recorded"] is True
    assert len(report["steps"]) == 1

    step = report["steps"][0]
    assert step["pre_state_available"] is True
    assert "def old():" in step["before"]
    assert step["after"].startswith("def new():")
    assert "-" in step["diff"] and "+" in step["diff"]


@pytest.mark.asyncio
async def test_missing_before_state_is_disclosed_not_invented(kernel, tmp_path):
    """A file that never existed has no before-state, and the step must say so."""
    target = tmp_path / "brand_new.py"

    ctx = _ctx(kernel, {"fs:read", "storage:write"})
    mod = ReplayMod()

    await mod.handle(ctx, _requested_event(str(target), "print('hi')\n", "call_1"), lambda e: _pass(e))
    await mod.handle(ctx, _completed_event(str(target), "print('hi')\n", "call_1"), lambda e: _pass(e))

    step = mod.replay(ctx, "turn_1")["steps"][0]
    assert step["pre_state_available"] is False
    assert step["before"] is None
    assert step["diff"] == ""
    assert step["pre_state_reason"] == "not a regular file"


@pytest.mark.asyncio
async def test_credential_shaped_pre_state_is_refused_with_a_reason(kernel, tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("API_KEY=supersecret\n", encoding="utf-8")

    ctx = _ctx(kernel, {"fs:read", "storage:write"})
    mod = ReplayMod()

    await mod.handle(ctx, _requested_event(str(secret), "SAFE=1\n", "call_1"), lambda e: _pass(e))
    await mod.handle(ctx, _completed_event(str(secret), "SAFE=1\n", "call_1"), lambda e: _pass(e))

    step = mod.replay(ctx, "turn_1")["steps"][0]
    assert step["pre_state_available"] is False
    assert "credential-shaped" in step["pre_state_reason"]
    assert "supersecret" not in str(step)


@pytest.mark.asyncio
async def test_steps_are_grouped_by_turn(kernel, tmp_path):
    ctx = _ctx(kernel, {"fs:read", "storage:write"})
    mod = ReplayMod()

    for i in range(2):
        await mod.handle(ctx, _requested_event(f"a{i}.py", "x", f"c{i}", turn_id="turn_a"), lambda e: _pass(e))
        await mod.handle(ctx, _completed_event(f"a{i}.py", "x", f"c{i}", turn_id="turn_a"), lambda e: _pass(e))
    await mod.handle(ctx, _requested_event("b.py", "y", "c9", turn_id="turn_b"), lambda e: _pass(e))
    await mod.handle(ctx, _completed_event("b.py", "y", "c9", turn_id="turn_b"), lambda e: _pass(e))

    assert len(mod.replay(ctx, "turn_a")["steps"]) == 2
    assert len(mod.replay(ctx, "turn_b")["steps"]) == 1
    assert mod.replay(ctx, "turn_a")["turns"] == ["turn_a", "turn_b"]


@pytest.mark.asyncio
async def test_replay_defaults_to_the_newest_turn(kernel, tmp_path):
    ctx = _ctx(kernel, {"fs:read", "storage:write"})
    mod = ReplayMod()

    for turn in ("turn_old", "turn_new"):
        await mod.handle(ctx, _requested_event(f"{turn}.py", "x", f"c_{turn}", turn_id=turn), lambda e: _pass(e))
        await mod.handle(ctx, _completed_event(f"{turn}.py", "x", f"c_{turn}", turn_id=turn), lambda e: _pass(e))

    assert mod.replay(ctx)["turn_id"] == "turn_new"


@pytest.mark.asyncio
async def test_replay_without_recorded_steps_says_so(kernel):
    ctx = _ctx(kernel, {"fs:read", "storage:write"})
    mod = ReplayMod()

    report = mod.replay(ctx, "nothing")
    assert report["recorded"] is False
    assert report["reason"] == "NO_RECORDED_STEPS"
    assert report["steps"] == []


@pytest.mark.asyncio
async def test_replay_requested_event_is_answered_directly(kernel, tmp_path):
    ctx = _ctx(kernel, {"fs:read", "storage:write"})
    mod = ReplayMod()
    await mod.handle(ctx, _requested_event("a.py", "x", "c1"), lambda e: _pass(e))
    await mod.handle(ctx, _completed_event("a.py", "x", "c1"), lambda e: _pass(e))

    result = await mod.handle(ctx, _replay_event(), lambda e: _pass(e))

    assert result.outcome == EventOutcome.ANSWER
    assert result.response_payload["recorded"] is True


@pytest.mark.asyncio
async def test_stale_pending_captures_are_dropped_on_turn_completion(kernel, tmp_path):
    """A capture whose write never landed must not attach to a later write."""
    ctx = _ctx(kernel, {"fs:read", "storage:write"})
    mod = ReplayMod()

    await mod.handle(ctx, _requested_event("a.py", "x", "never_completed"), lambda e: _pass(e))
    assert ctx.storage.get("pending")

    await mod.handle(ctx, AlphaEvent(name="turn.complete", payload={}, correlation=CorrelationContext.create()), lambda e: _pass(e))
    assert ctx.storage.get("pending") == {}


@pytest.mark.asyncio
async def test_step_count_is_bounded_per_turn(kernel):
    ctx = _ctx(kernel, {"fs:read", "storage:write"})
    mod = ReplayMod()

    for i in range(MAX_STEPS_PER_TURN + 5):
        await mod.handle(ctx, _completed_event(f"f{i}.py", "x", f"c{i}"), lambda e: _pass(e))

    assert len(mod.replay(ctx, "turn_1")["steps"]) == MAX_STEPS_PER_TURN


@pytest.mark.asyncio
async def test_non_file_tools_are_ignored(kernel):
    ctx = _ctx(kernel, {"fs:read", "storage:write"})
    mod = ReplayMod()

    await mod.handle(
        ctx,
        AlphaEvent(
            name="tool.completed",
            payload={"tool_name": "web_search", "tool_args": {"query": "x"}, "status": "success"},
            correlation=CorrelationContext.create(turn_id="turn_1"),
        ),
        lambda e: _pass(e),
    )

    assert mod.replay(ctx, "turn_1")["steps"] == []


async def _pass(event):
    from alpha.mods.types import EventResult

    return EventResult.continue_(event)
