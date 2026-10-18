"""Truth suite for Bot Mode and multi-agent status honesty.

Each test here pins a promise that used to be false, so a regression is caught
by behaviour rather than by reading a docstring:

1. ``message_agent`` exists as a real tool (it was schema-only).
2. It refuses outside a bot session and delivers inside one.
3. A swarm that executed no work cannot be ``completed``.
4. Keyword overlap with the goal cannot produce a terminal success.
5. ``alpha.local.bot`` refuses an unknown bot with a named reason.
6. Memory consolidation reports real zeros, not arithmetic estimates.
7. A swarm created from a run records that run.
"""

from __future__ import annotations

import json

import pytest
from langchain.tools import ToolRuntime

from alpha.bots.inbox import BotInbox
from alpha.orchestrator.executors import BOT_EXECUTOR, local_bot_executor
from alpha.perpetual.memory_consolidator import PerpetualMemoryConsolidator
from alpha.swarm.aggregator import SwarmAggregator
from alpha.swarm.coordinator import get_swarm_coordinator
from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode, TaskNodeState
from alpha.tools.builtins.message_agent_tool import message_agent_tool
from alpha.workflow.models import WorkflowNode, WorkflowRun


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    import alpha.bots.inbox as inbox_mod
    import alpha.bots.registry as bot_reg

    monkeypatch.setattr(bot_reg, "_global_registry", None)
    monkeypatch.setattr(bot_reg, "_global_registry_path", None)
    monkeypatch.setattr(inbox_mod, "_inboxes", {})
    yield


def _runtime(**context) -> ToolRuntime:
    return ToolRuntime(
        state={},
        context=context,
        config={},
        stream_writer=lambda _chunk: None,
        tool_call_id="tool-call-truth",
        store=None,
    )


# ---------------------------------------------------------------------------
# 1-2. message_agent is a real, contained tool
# ---------------------------------------------------------------------------


def test_message_agent_is_a_registered_builtin_tool():
    assert message_agent_tool.name == "message_agent"
    from alpha.tools.tools import get_available_tools

    names = {tool.name for tool in get_available_tools()}
    assert "message_agent" in names, "the DM tool the lead prompt names must actually be bound"


def test_message_agent_refuses_without_a_bot_identity():
    out = json.loads(message_agent_tool.invoke({"runtime": _runtime(thread_id="t1"), "target": "coder", "message": "hi"}))
    assert out["status"] == "rejected"
    assert out["reason_code"] == "not_a_bot_chat"
    # A refusal must not write anything.
    assert BotInbox("coder").unread_count() == 0


def test_message_agent_delivers_inside_a_bot_session_and_acks_the_reply():
    # A message waiting in the sender's own inbox, which the reply should ack.
    original = BotInbox("architect").deliver("coder", "please review auth.py")

    out = json.loads(
        message_agent_tool.invoke(
            {
                "runtime": _runtime(thread_id="bot-chat-architect", bot_name="architect"),
                "target": "coder",
                "message": "Reviewed; see the diff in the thread.",
                "reply_to": original.delivery_id,
            }
        )
    )
    assert out["status"] == "delivered"
    assert out["reply_acknowledged"] is True
    # Ack is observable and idempotent: a second ack finds nothing left to ack.
    assert BotInbox("architect").ack(original.delivery_id) is None


# ---------------------------------------------------------------------------
# 3-4. Swarm status derives from task facts, not keyword overlap
# ---------------------------------------------------------------------------


def test_zero_task_swarm_cannot_be_completed():
    plan = SwarmPlan(swarm_id="s-zero", goal="Ship the migration safely", mode=SwarmMode.AUTO)
    SwarmAggregator.aggregate(plan)
    assert plan.status != "completed"
    assert plan.status == "partial_success"
    assert plan.terminal_reason == "no_work"


def test_keyword_overlap_cannot_turn_a_failed_swarm_into_success():
    plan = SwarmPlan(swarm_id="s-fail", goal="Ship the migration safely", mode=SwarmMode.AUTO)
    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="do it", state=TaskNodeState.FAILED)
    result = SwarmAggregator.aggregate(plan)
    # The report echoes the goal verbatim; that must not decide anything.
    assert plan.goal in result["deliverable"]
    assert plan.status != "completed"
    assert result["quality_gate"]["advisory"] is True
    assert result["quality_gate"]["method"] == "keyword_overlap"


def test_unverified_acceptance_is_partial_not_completed():
    plan = SwarmPlan(swarm_id="s-unv", goal="Ship it", mode=SwarmMode.AUTO)
    plan.tasks["t1"] = SwarmTaskNode(
        task_id="t1",
        objective="do it",
        state=TaskNodeState.COMPLETED,
        result_summary="done",
        acceptance_criteria=["tests pass"],
    )
    SwarmAggregator.aggregate(plan)
    assert plan.status == "partial_success"
    assert plan.terminal_reason == "acceptance_unverified"


# ---------------------------------------------------------------------------
# 5. The bot workflow executor refuses honestly
# ---------------------------------------------------------------------------


def test_bot_executor_refuses_an_unknown_bot_with_a_named_reason():
    node = WorkflowNode(id="n1", executor=BOT_EXECUTOR, prompt="do a thing", config={"bot": "no-such-bot"})
    result = local_bot_executor(node, WorkflowRun(run_id="r1", workflow_id="w1"))
    assert result["status"] == "failed"
    assert "no-such-bot" in result["output"]
    assert result["tokens_used"] == 0


def test_bot_executor_requires_a_bot_name():
    node = WorkflowNode(id="n1", executor=BOT_EXECUTOR, prompt="do a thing", config={})
    result = local_bot_executor(node, WorkflowRun(run_id="r1", workflow_id="w1"))
    assert result["status"] == "failed"
    assert "bot" in result["output"].lower()


# ---------------------------------------------------------------------------
# 6. Consolidation reports real zeros
# ---------------------------------------------------------------------------


def test_memory_consolidation_reports_unavailable_with_real_zeros():
    report = PerpetualMemoryConsolidator("p1").consolidate(trace_count=9)
    assert report.status == "unavailable"
    assert (report.traces_analyzed, report.facts_extracted, report.skills_indexed, report.pruned_tokens) == (0, 0, 0, 0)
    assert report.unavailable_reason


# ---------------------------------------------------------------------------
# 7. Run correlation
# ---------------------------------------------------------------------------


def test_create_swarm_records_the_requesting_run():
    plan = get_swarm_coordinator().create_swarm(
        goal="Compare three vector databases",
        run_id="run-truth-1",
        thread_id="thread-truth-1",
        requested_by="coder",
    )
    assert plan.run_id == "run-truth-1"
    assert plan.thread_id == "thread-truth-1"
    assert plan.requested_by == "coder"
    assert plan.metrics["run"] == {
        "run_id": "run-truth-1",
        "thread_id": "thread-truth-1",
        "requested_by": "coder",
    }


def test_cancel_for_run_cancels_only_that_runs_swarms():
    coordinator = get_swarm_coordinator()
    mine = coordinator.create_swarm(goal="Mine to cancel", run_id="run-a")
    other = coordinator.create_swarm(goal="Not mine", run_id="run-b")

    cancelled = coordinator.cancel_for_run("run-a", reason="run_cancelled")

    assert mine.swarm_id in cancelled
    assert coordinator.get_swarm(mine.swarm_id).status == "cancelled"
    assert coordinator.get_swarm(other.swarm_id).status == "running"
