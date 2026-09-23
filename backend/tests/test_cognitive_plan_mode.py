"""Comprehensive test suite for the Cognitive Plan Mode & Autonomous Dispatch Bridge.

Validates the 8-dimensional strategic decision matrix, automated swarm decomposition,
proof obligation generation, safety risk gating, autonomous dispatch execution across all
7 paradigms, the builtin cognitive_plan tool, Gateway REST endpoints, and architectural boundaries.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import alpha.swarm.coordinator as coord_mod
from alpha.planning.bridge import AutonomousDispatchBridge
from alpha.planning.meta_planner import (
    CognitiveMetaPlanner,
    ExecutionParadigm,
)
from alpha.swarm.models import SwarmMode
from alpha.tools.builtins.cognitive_plan_tool import cognitive_plan


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
    # the reported artifact is the coordinator's real checkpoint file
    # (the old code claimed an "…_plan.json" that was never written)
    assert res.artifacts, "swarm dispatch must report its checkpoint file"
    checkpoint = Path(res.artifacts[0])
    assert checkpoint.name == f"{res.execution_id}.json"
    assert checkpoint.exists()


# 5. Autonomous Dispatch: Bot Profile (real handoff, honest failure)
def test_autonomous_dispatch_bot_profile():
    plan = CognitiveMetaPlanner.evaluate_and_plan("Implement user authentication endpoints and JWT verification")
    assert plan.decision.paradigm == ExecutionParadigm.BOT_PROFILE

    res = AutonomousDispatchBridge.dispatch(plan)
    # An accepted handoff routes work to a bot that executes later — that is
    # a dispatch, not a completion (the old code faked "completed" instantly).
    assert res.status == "dispatched"
    assert res.execution_id.startswith("handoff-")
    assert "coder" in res.assigned_agents
    contracts = [a for a in res.artifacts if a.endswith("implementation_contract.md")]
    assert contracts, "the implementation contract must really be written"
    contract_file = Path(contracts[0])
    assert contract_file.exists()
    # the contract's content is the compiled plan report, not a placeholder
    assert plan.markdown_report in contract_file.read_text(encoding="utf-8")
    assert res.details["handoff_status"] == "accepted"
    assert res.details["lead_bot"] == "coder"


def test_bot_profile_handoff_failure_is_honest(monkeypatch):
    plan = CognitiveMetaPlanner.evaluate_and_plan("Implement user authentication endpoints and JWT verification")

    def _raise(**_kwargs):
        raise ValueError("Recipient bot 'coder' is suspended and cannot accept work.")

    monkeypatch.setattr("alpha.bots.handoff.execute_handoff", _raise)
    res = AutonomousDispatchBridge.dispatch(plan)
    assert res.status == "failed"
    assert "ValueError" in res.summary
    assert "suspended" in res.summary
    assert res.details["error_type"] == "ValueError"


# 6. Autonomous Dispatch: MoA, Deep Research, Deep Think, Subagent, Direct
def _stub_invoke(prefix: str):
    def _invoke(model_name, *, system, user):
        return f"{prefix}[{model_name}]: response to '{user[:40]}'"

    return _invoke


def test_autonomous_dispatch_moa_runs_real_deliberation(monkeypatch):
    from alpha.deliberation import invocation as delib_inv

    monkeypatch.setattr(delib_inv, "invoke_model", _stub_invoke("STUB-MOA"))
    plan = CognitiveMetaPlanner.evaluate_and_plan("Multi-LLM committee review to brainstorm consensus")
    assert plan.decision.paradigm == ExecutionParadigm.MOA

    moa_res = AutonomousDispatchBridge.dispatch(plan)
    assert moa_res.status == "completed"
    # the summary is (derived from) the exact model output the engine produced
    assert "STUB-MOA[" in moa_res.summary
    synthesis = [a for a in moa_res.artifacts if a.endswith("moa_consensus_synthesis.md")]
    assert synthesis and Path(synthesis[0]).read_text(encoding="utf-8") == moa_res.summary
    assert moa_res.details["strategy_used"] == "ensemble"
    assert isinstance(moa_res.details["confidence_score"], float)
    assert isinstance(moa_res.details["consensus_percentage"], float)
    # the old dispatcher hardcoded this key with a fabricated True
    assert "consensus_reached" not in moa_res.details


def test_autonomous_dispatch_moa_model_failure_is_honest(monkeypatch):
    from alpha.deliberation import invocation as delib_inv

    def _no_models(model_name, *, system, user):
        raise RuntimeError("No chat models configured; deliberation cannot run.")

    monkeypatch.setattr(delib_inv, "invoke_model", _no_models)
    plan = CognitiveMetaPlanner.evaluate_and_plan("Multi-LLM committee review to brainstorm consensus")
    res = AutonomousDispatchBridge.dispatch(plan)
    assert res.status == "failed"
    assert "No chat models configured" in res.summary


def test_autonomous_dispatch_deep_research_writes_real_report(monkeypatch):
    from alpha.research.engine import DeepResearchEngine

    plan = CognitiveMetaPlanner.evaluate_and_plan("Deep investigate paper citations and market landscape")
    assert plan.decision.paradigm == ExecutionParadigm.DEEP_RESEARCH

    async def _canned_search(query, max_results=5):
        return [
            {
                "title": f"Result for {query[:40]}",
                "url": f"https://research.test/{abs(hash(query))}/",
                "snippet": "Measured evidence with concrete numbers.",
            }
        ]

    async def _canned_fetch(url):
        return f"# Content for {url}\n\nEvidence body used by the pipeline."

    monkeypatch.setattr(
        "alpha.planning.bridge._build_research_engine",
        lambda: DeepResearchEngine(search_fn=_canned_search, fetch_fn=_canned_fetch),
    )

    res = AutonomousDispatchBridge.dispatch(plan)
    assert res.status == "completed"
    report_files = [a for a in res.artifacts if a.endswith("deep_research_report.md")]
    citation_files = [a for a in res.artifacts if a.endswith("verified_citations.json")]
    assert report_files and citation_files
    citations = json.loads(Path(citation_files[0]).read_text(encoding="utf-8"))
    # every count is derived from the report actually written — the old
    # dispatcher reported fixed 12/8/0 no matter what happened.
    assert res.details["citations_verified"] == len(citations)
    assert res.details["sources_inspected"] >= 1
    assert (
        res.details["sources_inspected"],
        res.details["citations_verified"],
        res.details["contradictions_detected"],
    ) != (12, 8, 0)
    assert "research.test" in Path(report_files[0]).read_text(encoding="utf-8")


def test_autonomous_dispatch_deep_research_backend_failure_is_honest(monkeypatch):
    plan = CognitiveMetaPlanner.evaluate_and_plan("Deep investigate paper citations and market landscape")

    def _explode():
        raise RuntimeError("No live search backend available: the `ddgs` package is not installed.")

    monkeypatch.setattr("alpha.planning.bridge._build_research_engine", _explode)
    res = AutonomousDispatchBridge.dispatch(plan)
    assert res.status == "failed"
    assert "No live search backend" in res.summary


def test_autonomous_dispatch_deep_think_writes_real_trace(monkeypatch):
    from alpha.deliberation import invocation as delib_inv

    trace = "STEP 1: assumption X\nSTEP 2: inference Y\nFINAL: position Z"
    monkeypatch.setattr(delib_inv, "invoke_model", lambda model_name, *, system, user: trace)
    plan = CognitiveMetaPlanner.evaluate_and_plan("Prove the formal correctness and mathematical theorem of this algorithm design")
    assert plan.decision.paradigm == ExecutionParadigm.DEEP_THINK

    res = AutonomousDispatchBridge.dispatch(plan)
    assert res.status == "completed"
    assert res.summary == trace  # verbatim model output
    traces = [a for a in res.artifacts if a.endswith("formal_reasoning_trace.md")]
    assert traces and Path(traces[0]).read_text(encoding="utf-8") == trace
    assert res.assigned_agents == [res.details["model"]]
    assert isinstance(res.details["duration_seconds"], float)
    # fabricated fields from the old dispatcher are gone
    assert "confidence_score" not in res.details
    assert "self_critique_passed" not in res.details
    assert "reasoning_steps" not in res.details


def test_autonomous_dispatch_subagent_runs_real_delegation():
    plan = CognitiveMetaPlanner.evaluate_and_plan("Summarize the main points of this meeting transcript")
    # The planner has no keyword branch that selects SUBAGENT, so force the
    # paradigm to exercise this dispatcher directly.
    plan.decision.paradigm = ExecutionParadigm.SUBAGENT
    plan.decision.assigned_specialists = ["architect"]

    res = AutonomousDispatchBridge.dispatch(plan)
    assert res.status in ("completed", "partial", "failed")
    payloads = [a for a in res.artifacts if a.endswith("subagent_output.json")]
    assert payloads, "the delegation contract must really be written"
    payload = json.loads(Path(payloads[0]).read_text(encoding="utf-8"))
    assert res.execution_id == f"subagent-{payload['session_id']}"
    assert res.details["contract_status"] == payload["status"]
    assert res.details["agent_type"] == payload["agent_type"]


def test_autonomous_dispatch_direct_returns_model_output(monkeypatch):
    from alpha.deliberation import invocation as delib_inv

    monkeypatch.setattr(
        delib_inv,
        "invoke_model",
        lambda model_name, *, system, user: f"STUB-SINGLE: direct answer to '{user[:30]}'",
    )
    plan = CognitiveMetaPlanner.evaluate_and_plan("Translate this phrase to French")
    assert plan.decision.paradigm == ExecutionParadigm.DIRECT_AGENT

    res = AutonomousDispatchBridge.dispatch(plan)
    assert res.status == "completed"
    assert res.summary.startswith("STUB-SINGLE:")
    assert res.assigned_agents == [res.details["model"]]
    assert isinstance(res.details["duration_seconds"], float)
    # canned field from the old dispatcher is gone
    assert "coordination_overhead_seconds" not in res.details


def test_autonomous_dispatch_direct_model_failure_is_honest(monkeypatch):
    from alpha.deliberation import invocation as delib_inv

    def _no_models(model_name, *, system, user):
        raise RuntimeError("No chat models configured; deliberation cannot run.")

    monkeypatch.setattr(delib_inv, "invoke_model", _no_models)
    plan = CognitiveMetaPlanner.evaluate_and_plan("Translate this phrase to French")
    res = AutonomousDispatchBridge.dispatch(plan)
    assert res.status == "failed"
    assert "No chat models configured" in res.summary


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
    # the handoff routes the work; the bot executes asynchronously, so the
    # real status is "dispatched" (the old code faked "completed")
    assert disp_resp["dispatch"]["status"] == "dispatched"
    assert disp_resp["dispatch"]["paradigm"] == "bot_profile"


# 9. Strict Harness Boundary Invariant
def test_planning_harness_boundary_integrity():
    """Confirms packages/harness/alpha/planning contains zero forbidden imports from app.*."""
    import pathlib

    planning_dir = pathlib.Path(__file__).parent.parent / "packages" / "harness" / "alpha" / "planning"
    for py_file in planning_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "from app." not in content, f"Boundary violation in {py_file}: contains 'from app.'"
        assert "import app." not in content, f"Boundary violation in {py_file}: contains 'import app.'"
