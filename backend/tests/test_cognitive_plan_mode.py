"""Comprehensive test suite for the Cognitive Plan Mode & Autonomous Dispatch Bridge.

Validates the 8-dimensional strategic decision matrix, automated swarm decomposition,
proof obligation generation, safety risk gating, autonomous dispatch execution across all
7 paradigms, the builtin cognitive_plan tool, Gateway REST endpoints, and architectural boundaries.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import agent_workspace.swarm.coordinator as coord_mod
from agent_workspace.planning.bridge import AutonomousDispatchBridge
from agent_workspace.planning.meta_planner import (
    CognitiveMetaPlanner,
    ExecutionParadigm,
)
from agent_workspace.swarm.models import SwarmMode
from agent_workspace.tools.builtins.cognitive_plan_tool import cognitive_plan


@pytest.fixture(autouse=True)
def _isolated_swarm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    coord_mod._GLOBAL_COORDINATOR = None
    yield
    coord_mod._GLOBAL_COORDINATOR = None


# 1. 8-Dimensional Decision Matrix Evaluation
def test_cognitive_meta_planner_8_dimensions():
    # A. Swarm Map-Reduce for Batch Processing
    items = [f"Company-{i}" for i in range(12)]
    swarm_plan = CognitiveMetaPlanner.evaluate_and_plan("Research and extract revenue for all target entities", items=items)
    assert swarm_plan.decision.paradigm == ExecutionParadigm.SWARM
    assert swarm_plan.decision.swarm_mode == SwarmMode.MAP_REDUCE
    assert swarm_plan.decision.estimated_speedup > 2.0
    assert swarm_plan.decision.workforce_type == "hybrid"
    assert len(swarm_plan.execution_waves) == 13  # 12 maps + 1 reduce
    assert swarm_plan.status == "ready"

    # B. Deep Research
    research_plan = CognitiveMetaPlanner.evaluate_and_plan("Deep investigate market landscape, papers, and sources for agent architectures")
    assert research_plan.decision.paradigm == ExecutionParadigm.DEEP_RESEARCH
    assert research_plan.decision.reasoning_tier == "extended_reflection"
    assert research_plan.decision.model_tier == "frontier"
    assert "researcher" in research_plan.decision.assigned_specialists
    assert any("verified citations" in po for po in research_plan.proof_obligations)

    # C. Deep Think
    think_plan = CognitiveMetaPlanner.evaluate_and_plan("Prove the formal correctness and mathematical theorem of this algorithm design")
    assert think_plan.decision.paradigm == ExecutionParadigm.DEEP_THINK
    assert think_plan.decision.reasoning_tier == "extended_reflection"
    assert think_plan.decision.model_tier == "frontier"

    # D. Mixture of Agents (MoA)
    moa_plan = CognitiveMetaPlanner.evaluate_and_plan("Multi-LLM committee review with diverse perspectives to brainstorm consensus")
    assert moa_plan.decision.paradigm == ExecutionParadigm.MOA
    assert moa_plan.decision.reasoning_tier == "adversarial_audit"
    assert "architect" in moa_plan.decision.assigned_specialists

    # E. Bot Profile / Code Mode
    code_plan = CognitiveMetaPlanner.evaluate_and_plan("Refactor backend endpoint routing and write unit tests")
    assert code_plan.decision.paradigm == ExecutionParadigm.BOT_PROFILE
    assert code_plan.decision.workspace_isolation == "git_worktree"
    assert "coder" in code_plan.decision.assigned_specialists
    assert code_plan.verification_command == "uv run pytest"

    # F. Direct Fast Agent
    direct_plan = CognitiveMetaPlanner.evaluate_and_plan("Summarize the main points of this meeting transcript")
    assert direct_plan.decision.paradigm == ExecutionParadigm.DIRECT_AGENT
    assert direct_plan.decision.estimated_speedup == 1.0
    assert direct_plan.decision.workforce_type == "single_agent"


# 2. Plan Serialization & Integrity
def test_meta_plan_serialization():
    plan = CognitiveMetaPlanner.evaluate_and_plan("Build a high performance rust extension")
    data = plan.to_dict()

    assert "plan_id" in data
    assert "prompt" in data
    assert "decision" in data
    assert "execution_waves" in data
    assert "proof_obligations" in data
    assert "markdown_report" in data
    assert "status" in data
    assert data["decision"]["paradigm"] in [p.value for p in ExecutionParadigm]
    assert len(plan.markdown_report) > 100


# 3. Safety Gate Invariant: Block Destructive R5/R6 Prompts
def test_autonomous_dispatch_safety_gate():
    destructive_plan = CognitiveMetaPlanner.evaluate_and_plan("Run rm -rf / and drop table production deploy database")
    assert destructive_plan.decision.risk_tier == "R5"
    assert destructive_plan.status == "blocked"

    # Dispatch must refuse and return blocked_human_gate
    res = AutonomousDispatchBridge.dispatch(destructive_plan)
    assert res.status == "blocked_human_gate"
    assert res.execution_id is None
    assert "blocked by Risk Gate" in res.summary


# 4. Autonomous Dispatch: Swarm
def test_autonomous_dispatch_swarm():
    items = ["Alpha Corp", "Beta LLC", "Gamma Inc"]
    plan = CognitiveMetaPlanner.evaluate_and_plan("Analyze financial filings for companies", items=items)
    assert plan.decision.paradigm == ExecutionParadigm.SWARM

    res = AutonomousDispatchBridge.dispatch(plan)
    assert res.status == "dispatched"
    assert res.execution_id is not None
    assert res.execution_id.startswith("swm-")
    assert "Autonomous Swarm spawned" in res.summary
    assert res.details["mode"] == "map_reduce"


# 5. Autonomous Dispatch: Bot Profile
def test_autonomous_dispatch_bot_profile():
    plan = CognitiveMetaPlanner.evaluate_and_plan("Implement user authentication endpoints and JWT verification")
    assert plan.decision.paradigm == ExecutionParadigm.BOT_PROFILE

    res = AutonomousDispatchBridge.dispatch(plan)
    assert res.status == "completed"
    assert res.execution_id.startswith("handoff-")
    assert "coder" in res.assigned_agents
    assert "implementation_contract.md" in res.artifacts


# 6. Autonomous Dispatch: MoA, Deep Research & Deep Think
def test_autonomous_dispatch_cognitive_paradigms():
    # MoA
    moa_plan = CognitiveMetaPlanner.evaluate_and_plan("Multi-LLM committee review to brainstorm consensus")
    moa_res = AutonomousDispatchBridge.dispatch(moa_plan)
    assert moa_res.status == "completed"
    assert "moa_consensus_synthesis.md" in moa_res.artifacts
    assert moa_res.details["consensus_reached"] is True

    # Deep Research
    res_plan = CognitiveMetaPlanner.evaluate_and_plan("Deep investigate paper citations and market landscape")
    res_res = AutonomousDispatchBridge.dispatch(res_plan)
    assert res_res.status == "completed"
    assert "verified_citations.json" in res_res.artifacts
    assert res_res.details["citations_verified"] > 0

    # Deep Think
    think_plan = CognitiveMetaPlanner.evaluate_and_plan("Prove the complex logic puzzle and mathematical algorithm")
    think_res = AutonomousDispatchBridge.dispatch(think_plan)
    assert think_res.status == "completed"
    assert "formal_reasoning_trace.md" in think_res.artifacts
    assert think_res.details["self_critique_passed"] is True

    # Direct Agent
    direct_plan = CognitiveMetaPlanner.evaluate_and_plan("Translate this phrase to French")
    direct_res = AutonomousDispatchBridge.dispatch(direct_plan)
    assert direct_res.status == "completed"
    assert direct_res.details["coordination_overhead_seconds"] == 0.0


# 7. Cognitive Plan Builtin Tool
def test_cognitive_plan_builtin_tool():
    # Evaluate action
    eval_output = cognitive_plan.invoke(
        {
            "action": "evaluate",
            "prompt": "Deep research quantum computing breakthroughs and citations",
        }
    )
    assert "Cognitive Plan" in eval_output
    assert "Execution Paradigm" in eval_output
    assert "deep_research" in eval_output

    # Plan action (JSON)
    plan_output = cognitive_plan.invoke(
        {
            "action": "plan",
            "prompt": "Implement a new OAuth provider",
        }
    )
    data = json.loads(plan_output)
    assert "plan_id" in data
    assert data["decision"]["paradigm"] == "bot_profile"

    # Dispatch action
    dispatch_output = cognitive_plan.invoke(
        {
            "action": "dispatch",
            "prompt": "Benchmark 10 database systems",
            "items_json": json.dumps(["PostgreSQL", "SQLite", "DuckDB"]),
        }
    )
    assert "Autonomous Dispatch Executed" in dispatch_output
    assert "swarm" in dispatch_output


# 8. Gateway REST Endpoints
@pytest.mark.asyncio
async def test_gateway_plan_mode_router():
    from app.gateway.routers import plan_mode

    admin_req = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(system_role="admin")))

    # 1. Evaluate endpoint
    eval_resp = await plan_mode.evaluate_plan_mode(plan_mode.PlanEvaluateRequest(prompt="Deep investigate quantum algorithms with verified citations"))
    assert eval_resp["decision"]["paradigm"] == "deep_research"
    assert len(eval_resp["proof_obligations"]) > 0

    # 2. Dispatch endpoint
    disp_resp = await plan_mode.dispatch_plan_mode(
        plan_mode.PlanDispatchRequest(
            prompt="Refactor database layer and add migrations",
        ),
        admin_req,
    )
    assert "plan" in disp_resp
    assert "dispatch" in disp_resp
    assert disp_resp["dispatch"]["status"] == "completed"
    assert disp_resp["dispatch"]["paradigm"] == "bot_profile"


# 9. Strict Harness Boundary Invariant
def test_planning_harness_boundary_integrity():
    """Confirms packages/harness/agent_workspace/planning contains zero forbidden imports from app.*."""
    import pathlib

    planning_dir = pathlib.Path(__file__).parent.parent / "packages" / "harness" / "agent_workspace" / "planning"
    for py_file in planning_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "from app." not in content, f"Boundary violation in {py_file}: contains 'from app.'"
        assert "import app." not in content, f"Boundary violation in {py_file}: contains 'import app.'"
