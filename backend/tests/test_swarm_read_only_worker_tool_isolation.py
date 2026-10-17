"""G7: a read-only specialist worker physically cannot write.

The Phase C gate in ``SWARM_MODE_IMPLEMENTATION_PLAN.md`` reads: "a read-only
worker physically [cannot write]". The worker must not rely on prompt language
for the guarantee — the tool objects that could write, execute, or destructively
act are absent from the pool the executor binds, and every dropped name is
additionally placed on the subagent executor's own refuse-list
(``SubagentConfig.disallowed_tools``), so two independent gates must both be
breached before a call could happen.

Classification precedent is the deployment's own governance rule
(``alpha/tools/governance.py``): an operator ``tool_governance`` entry is
authoritative, first-party declared metadata is next, and untrusted sources
(MCP) may never classify themselves — they drop, exactly as the registry
already refuses to read their self-declared metadata.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from alpha.swarm.runner import AsyncSwarmRunner
from alpha.swarm.worker import (
    SpecialistSubagentWorker,
    read_only_tool_pool,
)


class _Tool:
    def __init__(self, name: str, *, description: str = "", metadata: dict | None = None):
        self.name = name
        self.description = description
        self.metadata = metadata


_UNTRUSTED_PREFIX = "[Untrusted third-party data:"


def _pool() -> list[_Tool]:
    return [
        _Tool("web_search"),
        _Tool("session_search"),
        _Tool("write_file"),
        _Tool("str_replace"),
        _Tool("python_repl"),
        _Tool("legacy_writer", metadata={"governance_risk_class": "write"}),
        _Tool("code_runner", metadata={"governance_risk_class": "execute"}),
        _Tool("purger", metadata={"governance_risk_class": "destructive"}),
        _Tool("mcp_reader", description=f"{_UNTRUSTED_PREFIX} a remote reader."),
        _Tool("mcp_reader_self_declared", description=f"{_UNTRUSTED_PREFIX} claims read-only.", metadata={"governance_risk_class": "read"}),
        _Tool("broken_decl", metadata={"governance_risk_class": "not-a-class"}),
    ]


def test_read_only_pool_filter_drops_every_tool_that_could_write():
    kept, dropped = read_only_tool_pool(_pool())

    names = {tool.name for tool in kept}
    assert names == {"web_search", "session_search"}
    assert set(dropped) == {"write_file", "str_replace", "python_repl", "legacy_writer", "code_runner", "purger", "mcp_reader", "mcp_reader_self_declared", "broken_decl"}


def test_read_only_pool_filter_keeps_a_nonempty_pool_untouched_for_other_tasks():
    # The shared specialist pool object must not be mutated: an empty pool has
    # nothing to filter and returns the same shapes as any caller passed.
    kept, dropped = read_only_tool_pool([])
    assert kept == [] and dropped == []
    kept, dropped = read_only_tool_pool(None)
    assert kept == [] and dropped == []


def test_specialist_worker_filters_its_pool_at_construction():
    pool = _pool()
    worker = SpecialistSubagentWorker("Researcher", tool_pool=pool, read_only=True)
    assert {getattr(tool, "name") for tool in worker.tool_pool} == {"web_search", "session_search"}
    assert worker.read_only_dropped_tools, "the dropped names must be recorded for the refuse-list"
    # The shared pool object a runner hands to every worker stays whole.
    assert len(pool) > len(worker.tool_pool)


def test_specialist_worker_without_read_only_signal_keeps_the_full_pool():
    pool = _pool()
    worker = SpecialistSubagentWorker("Coder", tool_pool=pool)
    assert worker.tool_pool is pool
    assert worker.read_only_dropped_tools == []


def test_read_only_refuse_list_reaches_the_executor_config():
    worker = SpecialistSubagentWorker("Researcher", tool_pool=_pool(), read_only=True)
    config = worker._resolve_agent_config()
    assert "write_file" in config.disallowed_tools
    assert "python_repl" in config.disallowed_tools
    assert "mcp_reader_self_declared" in config.disallowed_tools


def test_a_failed_pool_is_never_rescued_by_a_filtered_subset():
    pool = _pool()
    worker = SpecialistSubagentWorker("Researcher", tool_pool=pool, tool_pool_error="assembler unavailable", read_only=True)
    assert worker.tool_pool is pool
    assert worker.read_only_dropped_tools == []
    with pytest.raises(RuntimeError, match="assembler unavailable"):
        worker.execute_task(object(), object())


def test_runner_routes_read_only_capability_tags_into_the_worker(monkeypatch):
    """The runner derives the signal the same single way the worktree branch does."""

    from alpha.subagents.workspace_version import WORKSPACE_WRITE_TOOLS  # noqa: F401
    from alpha.swarm.models import SwarmTaskNode

    def _fake_roster():
        profile = SimpleNamespace(name="Researcher", agent_type=None, tools=["web_search"], skills=[], system_prompt=None)
        return [profile], {"source": "test"}

    monkeypatch.setattr("alpha.swarm.runner.resolve_roster", _fake_roster)

    runner = AsyncSwarmRunner.__new__(AsyncSwarmRunner)  # bypass __init__; _build_worker needs only the pool fields
    runner._specialist_tool_pool = _pool()
    runner._specialist_tool_pool_error = None
    runner.thread_id = None

    read_only_task = SwarmTaskNode(
        task_id="task-read",
        objective="Survey the implementation",
        worker_type="specialist",
        assigned_worker="Researcher",
        capability_tags=["web_search", "code_review"],
        capability_source="declared",
    )
    worker = runner._build_worker(read_only_task)
    assert worker.read_only is True
    assert {getattr(tool, "name") for tool in worker.tool_pool} == {"web_search", "session_search"}

    writer_task = SwarmTaskNode(
        task_id="task-write",
        objective="Implement the module",
        worker_type="specialist",
        assigned_worker="Researcher",
        capability_tags=["coding"],
        capability_source="declared",
    )
    untouched = runner._build_worker(writer_task)
    assert untouched.read_only is False
    assert untouched.tool_pool is runner._specialist_tool_pool
