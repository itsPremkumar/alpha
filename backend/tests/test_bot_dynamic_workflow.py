"""Tests for Dynamic Workflows in Bot Mode, Bot Cloning, and Evolutionary Breeding.

DY-R1 honesty contract for BOT nodes: with no executor bound the node fails
with the real reason and no clone is created; with an executor bound the bot
task runs through the node_runner seam and any specialist clone is a real
bot-registry side effect - never fabricated evidence text.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

import alpha.workflow.runtime as runtime_module
from alpha.bots.cloning import BotCloneEngine, CloneMode
from alpha.bots.profile import BotProfile
from alpha.bots.registry import BotRegistry
from alpha.workflow.models import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRunStatus,
)
from alpha.workflow.runtime import DynamicWorkflowEngine
from app.gateway.routers.bots import (
    BotCloneApiRequest,
    BotEvolveApiRequest,
    clone_bot_endpoint,
    evolve_bot_endpoint,
)


@pytest.fixture(scope="module", autouse=True)
def _isolate_agent_workspace(tmp_path_factory):
    """Point AGENT_WORKSPACE_HOME at one temp dir for this whole module.

    Module scope (not per test) because ``get_bot_clone_engine()`` caches the
    registry it was constructed with: a per-test env change would split the
    clone engine's registry away from ``get_bot_registry()``, breaking both
    the clone-exists assertion and the endpoint test's evolve step.
    """
    workspace = tmp_path_factory.mktemp("agent_workspace")
    previous = os.environ.get("AGENT_WORKSPACE_HOME")
    os.environ["AGENT_WORKSPACE_HOME"] = str(workspace)
    yield
    if previous is None:
        os.environ.pop("AGENT_WORKSPACE_HOME", None)
    else:
        os.environ["AGENT_WORKSPACE_HOME"] = previous


def test_bot_clone_engine_exact_and_specialist_fork():
    registry = BotRegistry(storage_path=None)
    base_bot = BotProfile(
        name="lead_dev",
        display_name="Lead Developer",
        role="Senior Engineer",
        soul="You write robust Python code.",
        toolsets=["code_editor", "terminal"],
        skills=["python"],
        department="engineering",
    )
    registry.register(base_bot)

    clone_engine = BotCloneEngine(registry=registry)

    # 1. Exact copy
    copy_bot = clone_engine.clone_bot(
        source_name="lead_dev",
        target_name="dev_copy_1",
        mode=CloneMode.EXACT_COPY,
        ttl_seconds=0,
    )
    assert copy_bot.name == "dev_copy_1"
    assert set(copy_bot.toolsets) == {"code_editor", "terminal"}
    assert copy_bot.metadata["cloned_from"] == "lead_dev"

    # 2. Specialist fork with directive and added skills
    specialist = clone_engine.clone_bot(
        source_name="lead_dev",
        target_name="k8s_specialist",
        mode=CloneMode.SPECIALIST_FORK,
        specialist_directive="Focus strictly on Kubernetes Helm chart deployments.",
        skills_to_add=["k8s", "helm"],
        tools_to_add=["k8s_tool"],
        ttl_seconds=1800,
    )
    assert specialist.name == "k8s_specialist"
    assert "helm" in specialist.skills
    assert "k8s_tool" in specialist.toolsets
    assert "SPECIALIST MISSION DIRECTIVE" in specialist.soul


def test_bot_clone_engine_evolution():
    registry = BotRegistry(storage_path=None)
    base_bot = BotProfile(
        name="researcher",
        display_name="Market Research Analyst",
        role="Analyst",
        soul="Gather accurate market signals.",
        department="growth",
        reputation_score=0.8,
    )
    registry.register(base_bot)

    clone_engine = BotCloneEngine(registry=registry)
    evolved = clone_engine.evolve_bot(
        source_name="researcher",
        performance_delta={"accuracy": "+15%", "tokens": "-20%"},
        improvement_directive="Filter out low-citation sources before summarizing.",
        promoted_skills=["citation_scoring"],
    )
    assert evolved.name == "researcher_v2"
    assert evolved.version == 2
    assert "citation_scoring" in evolved.skills
    assert "EVOLUTIONARY DIRECTIVE" in evolved.soul
    # Evolution NEVER inflates reputation: the caller's performance_delta is an
    # unverified claim, so the new generation inherits the source's score and
    # moves only through observed task outcomes (alpha.bots.performance).
    assert evolved.reputation_score == 0.8
    assert evolved.metadata["reputation_basis"].startswith("inherited from researcher")


def test_workflow_bot_nodes_fail_honestly_without_executor(monkeypatch):
    """With no executor bound a BOT node fails with the real reason - no fake run, no clone."""
    from alpha.bots.registry import get_bot_registry

    monkeypatch.setattr(runtime_module, "_NODE_RUNNER", None)  # seam explicitly unbound

    dwe = DynamicWorkflowEngine()
    bot_node = WorkflowNode(
        id="arch_review",
        type=NodeType.BOT,
        prompt="Review system architecture",
        config={"bot_name": "architect"},
    )
    graph = WorkflowGraph(version=1, nodes={"arch_review": bot_node}, edges=[])
    defn = WorkflowDefinition(id="wf_bot_unbound", name="Bot Workflow Unbound", graph=graph)
    dwe.register_definition(defn)
    run = dwe.start_run("wf_bot_unbound")

    dwe.execute_step(run.run_id)  # no per-call runner and no module-level binding

    node = graph.nodes["arch_review"]
    assert node.status == NodeStatus.FAILED
    assert run.node_states["arch_review"] == NodeStatus.FAILED
    assert "arch_review" in run.failed_nodes

    output = node.output
    assert isinstance(output, dict)
    assert output["status"] == "failed"
    reason = output["reason"]
    assert "no node_runner bound to execute node 'arch_review' (kind=bot)" in reason
    assert "no bot executor performed a model/tool call" in reason

    # Zero fabricated evidence: nothing executed, so nothing is claimed.
    assert node.evidence == []
    blob = f"{output} {node.evidence}"
    assert "Executed by bot" not in blob
    assert "verified output with capability epoch" not in blob

    # No registry side effect either: a clone is only created when an executor
    # exists. (The default roster seeds a bot named exactly "architect"; only a
    # clone created during this run would be named architect_clone_*.)
    assert not [b.name for b in get_bot_registry().list_bots() if b.name.startswith("architect_clone_")]

    assert run.status != WorkflowRunStatus.COMPLETED


def test_workflow_bot_nodes_execute_through_runner_seam():
    """BOT nodes run through the real seam; clone_bot=True registers a real specialist clone."""
    from alpha.bots.registry import get_bot_registry

    dwe = DynamicWorkflowEngine()
    bot_node = WorkflowNode(
        id="arch_review",
        type=NodeType.BOT,
        prompt="Review system architecture",
        config={"bot_name": "architect"},
    )
    specialist_node = WorkflowNode(
        id="db_migrate",
        type=NodeType.BOT,
        prompt="Execute PostgreSQL zero-downtime schema migration",
        config={
            "bot_name": "developer",
            "clone_bot": True,
            "specialist_directive": "Prioritize locking safety and reversible up/down migrations.",
            "skills": ["postgresql", "alembic"],
            "ttl_seconds": 3600,
        },
        depends_on=["arch_review"],
    )
    graph = WorkflowGraph(
        version=1,
        nodes={"arch_review": bot_node, "db_migrate": specialist_node},
        edges=[WorkflowEdge(source="arch_review", target="db_migrate")],
    )
    defn = WorkflowDefinition(id="wf_bot_mesh", name="Bot Dynamic Workflow", graph=graph)
    dwe.register_definition(defn)
    run = dwe.start_run("wf_bot_mesh")

    calls: list[str] = []

    def stub_runner(node, _run):
        calls.append(node.id)
        return {
            "status": "completed",
            "output": f"bot task done: {node.prompt}",
            "evidence": f"executor evidence for {node.id}",
            "tokens_used": 0,
        }

    dwe.execute_step(run.run_id, node_runner=stub_runner)  # wave 1: arch_review
    dwe.execute_step(run.run_id, node_runner=stub_runner)  # wave 2: db_migrate (+ specialist clone)

    # Both bot tasks really executed, in order, through the seam.
    assert calls == ["arch_review", "db_migrate"]
    assert run.status == WorkflowRunStatus.COMPLETED
    assert graph.nodes["arch_review"].output == "bot task done: Review system architecture"
    assert graph.nodes["arch_review"].evidence == ["executor evidence for arch_review"]
    assert graph.nodes["db_migrate"].output == "bot task done: Execute PostgreSQL zero-downtime schema migration"
    assert graph.nodes["db_migrate"].evidence == ["executor evidence for db_migrate"]

    blob = (
        f"{graph.nodes['arch_review'].output}{graph.nodes['db_migrate'].output}"
        f"{graph.nodes['arch_review'].evidence}{graph.nodes['db_migrate'].evidence}"
    )
    assert "Executed by bot" not in blob
    assert "verified output with capability epoch" not in blob

    # The specialist clone is a real bot-registry side effect, not fabricated
    # prose inside node output (which is what the old fake asserted).
    clones = [b.name for b in get_bot_registry().list_bots() if b.name.startswith("developer_clone_")]
    assert clones, "clone_bot=True must register a real specialist clone in the bot registry"


@pytest.mark.asyncio
async def test_bot_clone_and_evolve_endpoints():
    req = MagicMock()

    # Ensure developer bot exists
    from alpha.bots.registry import get_bot_registry
    from alpha.bots.templates import BOT_TEMPLATES, generate_default_soul

    reg = get_bot_registry()
    if not reg.get_bot("developer"):
        tmpl = BOT_TEMPLATES["developer"]
        reg.register(
            BotProfile(
                name="developer",
                display_name=tmpl["display"],
                role=tmpl["role"],
                soul=generate_default_soul("developer", tmpl["role"]),
                department=tmpl["department"],
            )
        )

    # 1. Clone endpoint
    clone_req = BotCloneApiRequest(
        target_name="fastapi_specialist",
        specialist_directive="Optimize async route handlers and connection pooling.",
        skills_to_add=["fastapi_profiling"],
    )
    cloned_res = await clone_bot_endpoint("developer", clone_req, req)
    assert cloned_res["name"] == "fastapi_specialist"
    assert "fastapi_profiling" in cloned_res["skills"]

    # 2. Evolve endpoint
    evolve_req = BotEvolveApiRequest(
        improvement_directive="Enforce typing and strict Pydantic v2 schemas.",
        performance_delta={"lint_errors": 0},
    )
    evolved_res = await evolve_bot_endpoint("developer", evolve_req, req)
    assert evolved_res["name"] == "developer_v2"
    assert evolved_res["version"] == 2
