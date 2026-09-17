"""Comprehensive tests for Google A2A Protocol and July 2026 MCP Tasks."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent_workspace.protocols.a2a import (
    A2ADelegationRequest,
    A2AProtocolAdapter,
    AgentCapabilityCard,
)
from agent_workspace.protocols.mcp_tasks import (
    MCPTaskManager,
    MCPTaskSpec,
    MCPTaskState,
)
from agent_workspace.tools.builtins.a2a_tool import a2a_tool


def test_a2a_capability_cards_and_delegation():
    adapter = A2AProtocolAdapter()
    card = AgentCapabilityCard(
        agent_id="agent-coder",
        name="Lead Python Coder",
        description="Writes idiomatic, typed Python code and tests.",
        skills=["python", "fastapi", "testing"],
        availability="available",
    )
    adapter.register_card(card)

    # Retrieval
    retrieved = adapter.get_card("agent-coder")
    assert retrieved is not None
    assert retrieved.name == "Lead Python Coder"

    # Skill filtering
    python_agents = adapter.list_cards(skill_filter="python")
    assert len(python_agents) == 1
    rust_agents = adapter.list_cards(skill_filter="rust")
    assert len(rust_agents) == 0

    # Delegation happy path
    req = A2ADelegationRequest(
        sender_agent_id="ceo-agent",
        target_agent_id="agent-coder",
        task_objective="Implement JWT middleware",
    )
    res = adapter.delegate(req)
    assert res.status == "completed"
    assert "JWT middleware" in res.deliverable["summary"]

    # Delegation to unknown agent
    bad_req = A2ADelegationRequest(
        sender_agent_id="ceo-agent",
        target_agent_id="non-existent-agent",
        task_objective="Do something",
    )
    bad_res = adapter.delegate(bad_req)
    assert bad_res.status == "rejected"


def test_mcp_tasks_async_lifecycle():
    manager = MCPTaskManager()
    spec = MCPTaskSpec(
        tool_name="git_diff_analyzer",
        arguments={"branch": "feature/a2a"},
        idempotency_key="idemp-12345",
    )

    # 1. Submit task
    status = manager.submit_task(spec)
    assert status.state == MCPTaskState.RUNNING

    # 2. Idempotent resubmission returns identical task
    status_dup = manager.submit_task(spec)
    assert status_dup.task_id == status.task_id

    # 3. Progress update
    updated = manager.update_progress(
        task_id=status.task_id,
        progress=85.0,
        result={"diff_lines": 42},
    )
    assert updated.progress_percent == 85.0
    assert updated.result == {"diff_lines": 42}

    # 4. Cancellation
    assert manager.cancel_task(status.task_id) is True
    cancelled = manager.get_status(status.task_id)
    assert cancelled is not None
    assert cancelled.state == MCPTaskState.CANCELLED


def test_a2a_tool_invocation():
    # Inspect card
    out = a2a_tool.invoke(
        {
            "action": "inspect_card",
            "agent_id": "agent-researcher",
        }
    )
    assert "Lead Research Specialist" in out

    # Delegate task
    del_out = a2a_tool.invoke(
        {
            "action": "delegate",
            "target_agent_id": "agent-researcher",
            "task_objective": "Gather research papers on multi-agent consensus",
        }
    )
    assert "completed" in del_out


@pytest.mark.asyncio
async def test_gateway_a2a_router():
    from app.gateway.routers import a2a as a2a_router

    # List cards
    cards = await a2a_router.list_capability_cards()
    assert len(cards) >= 1

    # Delegate via router
    req = a2a_router.DelegateTaskRequest(
        target_agent_id="agent-researcher",
        task_objective="Analyze quantum computing breakthroughs",
    )
    resp = await a2a_router.delegate_task(req)
    assert resp["status"] == "completed"


def test_os_subsystems_boundary_integrity():
    """Verify that jobs, supervision, planning/integrity, and protocols NEVER import app.*."""
    backend_root = Path(__file__).resolve().parent.parent
    subsystems = [
        backend_root / "packages" / "harness" / "deerflow" / "jobs",
        backend_root / "packages" / "harness" / "deerflow" / "supervision",
        backend_root / "packages" / "harness" / "deerflow" / "protocols",
        backend_root / "packages" / "harness" / "deerflow" / "planning" / "integrity.py",
    ]

    violations: list[str] = []

    for path in subsystems:
        py_files = [path] if path.is_file() else list(path.rglob("*.py"))
        for f in py_files:
            content = f.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(f))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "app" or alias.name.startswith("app."):
                            violations.append(f"{f.name}:{node.lineno} imports {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module == "app" or (node.module and node.module.startswith("app.")):
                        violations.append(f"{f.name}:{node.lineno} imports from {node.module}")

    assert not violations, f"Boundary firewall violation! Subsystems imported app: {violations}"
