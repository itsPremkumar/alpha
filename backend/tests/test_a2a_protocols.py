"""Comprehensive tests for Google A2A Protocol and July 2026 MCP Tasks."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from alpha.protocols.a2a import (
    A2ADelegationRequest,
    A2ADelegationResponse,
    A2AProtocolAdapter,
    AgentCapabilityCard,
)
from alpha.protocols.mcp_tasks import (
    MCPTaskManager,
    MCPTaskSpec,
    MCPTaskState,
)
from alpha.tools.builtins.a2a_tool import a2a_tool


class _RecordingTransport:
    """A transport that actually dispatches.

    The adapter ships without one and refuses to report work it did not do, so
    a test that wants a `completed` delegation has to inject something capable
    of earning that status. It deliberately returns the *wrong* `request_id`,
    which is the only way to cover the adapter's adoption of the caller's id.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def dispatch(self, card: AgentCapabilityCard, request: A2ADelegationRequest) -> A2ADelegationResponse:
        self.calls.append((card.agent_id, request.request_id))
        return A2ADelegationResponse(
            request_id="transport-writes-its-own-id",
            status="completed",
            deliverable={"summary": f"Implemented {request.task_objective}"},
            evidence=["dispatched by the injected transport"],
        )


def test_a2a_capability_cards_and_delegation():
    transport = _RecordingTransport()
    adapter = A2AProtocolAdapter(transport)
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
    # The injected transport is what earned that status, and the adapter
    # adopted its response under the *request's* id rather than the
    # transport's own.
    assert transport.calls == [("agent-coder", req.request_id)]
    assert res.request_id == req.request_id

    # Delegation to unknown agent
    bad_req = A2ADelegationRequest(
        sender_agent_id="ceo-agent",
        target_agent_id="non-existent-agent",
        task_objective="Do something",
    )
    bad_res = adapter.delegate(bad_req)
    assert bad_res.status == "rejected"


def test_a2a_without_a_transport_refuses_to_claim_work_it_did_not_do():
    """No transport means no dispatch, and `not_dispatched` has to say so.

    This adapter used to interpolate the requested objective back into a
    sentence, return `completed` for work that never ran, and attach evidence
    reading "Verified by A2A endpoint: local-bus" when the card had no URL.
    Both the registered router and a real agent tool could tell a caller that
    work completed with verified evidence when nothing had been dispatched.

    The refusal is the fix, so the test pins it: a status of `completed` here
    would be the fabrication the default `"rejected"` on `A2ADelegationResponse`
    exists to prevent.
    """
    adapter = A2AProtocolAdapter()
    adapter.register_card(
        AgentCapabilityCard(
            agent_id="agent-coder",
            name="Lead Python Coder",
            description="Writes idiomatic, typed Python code and tests.",
            skills=["python"],
        )
    )

    res = adapter.delegate(
        A2ADelegationRequest(
            sender_agent_id="ceo-agent",
            target_agent_id="agent-coder",
            task_objective="Implement JWT middleware",
        )
    )

    assert res.status == "not_dispatched"
    assert res.deliverable is None
    assert res.evidence == []
    # The refusal names the missing piece instead of describing work as done.
    assert "NOT dispatched" in (res.error or "")
    assert "no work was performed" in (res.error or "")


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
    # The tool builds its adapter with no transport, so this is a refusal.
    # The old assertion was `assert "completed" in del_out`, which passed on
    # the refusal message's own words "before a delegation can be reported as
    # completed" — green without ever reading the status the tool reported.
    payload = json.loads(del_out)
    assert payload["status"] == "not_dispatched"
    assert payload["deliverable"] is None
    assert "NOT dispatched" in (payload["error"] or "")


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
    # `_GLOBAL_A2A` is constructed without a transport, so the route can only
    # return the honest refusal. The handler escalates on "rejected" only;
    # "not_dispatched" is a 200 that carries the reason for real work not
    # having run. Asserting "completed" here demanded a fabrication.
    assert resp["status"] == "not_dispatched"
    assert resp["deliverable"] is None
    assert "NOT dispatched" in (resp["error"] or "")


def test_os_subsystems_boundary_integrity():
    """Verify that jobs, supervision, planning/integrity, and protocols NEVER import app.*."""
    backend_root = Path(__file__).resolve().parent.parent
    subsystems = [
        backend_root / "packages" / "harness" / "alpha" / "jobs",
        backend_root / "packages" / "harness" / "alpha" / "supervision",
        backend_root / "packages" / "harness" / "alpha" / "protocols",
        backend_root / "packages" / "harness" / "alpha" / "planning" / "integrity.py",
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
