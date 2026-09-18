"""Comprehensive test suite for the Universal Deliberation Engine.

Validates:
1. Deliberation Router: Task difficulty classification, risk tiering, and worthwhile prediction.
2. 3-Stage Anonymous Council: Blind generation, self-vote exclusion, rubric scoring, chairman synthesis.
3. Minority Report: Dissent preservation when consensus is contested.
4. Sparse Multi-Agent Debate: Ring topology, early convergence stopping, independent judge ruling.
5. Verifier Hierarchy: Deterministic tests and external evidence outranking pure consensus.
6. Built-in `deliberate` tool actions.
7. Gateway REST endpoints (/api/deliberation/evaluate, /api/deliberation/run).
8. Strict architectural boundary firewall (zero imports from app.*).
"""

from __future__ import annotations

import pytest

from agent_workspace.deliberation.council import CouncilEngine
from agent_workspace.deliberation.debate import DebateEngine
from agent_workspace.deliberation.engine import get_master_deliberation_engine
from agent_workspace.deliberation.models import (
    DeliberationConfidence,
    DeliberationStrategy,
)
from agent_workspace.deliberation.router import (
    DeliberationRouter,
    TaskDifficulty,
    TaskRisk,
)
from agent_workspace.deliberation.verifier import DeliberationVerifier
from agent_workspace.tools.builtins.deliberation_tool import deliberation_tool


# 1. Deliberation Router & Worthwhile Predictor
def test_deliberation_router_classification():
    # Trivial task -> SINGLE model, worthwhile=False
    triv_eval = DeliberationRouter.classify("Hello there, format this string to lowercase")
    assert triv_eval.difficulty in (TaskDifficulty.TRIVIAL, TaskDifficulty.SIMPLE)
    assert triv_eval.strategy == DeliberationStrategy.SINGLE
    assert triv_eval.worthwhile is False

    # Trade-off comparative query -> DEBATE, worthwhile=True
    debate_eval = DeliberationRouter.classify("Compare SQLite vs PostgreSQL pros and cons for offline-first apps")
    assert debate_eval.strategy == DeliberationStrategy.DEBATE
    assert debate_eval.worthwhile is True
    assert "debate" in debate_eval.rationale.lower()

    # High-impact architectural decision -> COUNCIL, worthwhile=True
    council_eval = DeliberationRouter.classify("Design the event-driven microservices architecture for financial payments")
    assert council_eval.strategy == DeliberationStrategy.COUNCIL
    assert council_eval.risk in (TaskRisk.HIGH, TaskRisk.CRITICAL)
    assert council_eval.worthwhile is True

    # User strategy override
    override_eval = DeliberationRouter.classify("Simple query", user_strategy=DeliberationStrategy.DEBATE)
    assert override_eval.strategy == DeliberationStrategy.DEBATE


# 2. 3-Stage Anonymous Council Engine & Self-Vote Exclusion
def test_3_stage_anonymous_council_and_self_vote_exclusion():
    query = "Should we adopt Rust or Go for high-throughput networking sidecars?"
    result = CouncilEngine.run_council(query, roster=["model-a", "model-b", "model-c"])

    assert result.strategy_used == DeliberationStrategy.COUNCIL
    assert result.confidence_score >= 0.80
    assert len(result.candidate_rankings) == 3
    assert "Consensus Recommendation" in result.final_answer

    # Verify self-vote exclusion invariant
    candidates = CouncilEngine._stage1_blind_generation(query, ["model-a", "model-b", "model-c"])
    reviews = CouncilEngine._stage2_peer_review(query, candidates)

    for rev in reviews:
        # Invariant: A model must NEVER review its own answer!
        assert rev.reviewer_candidate_id != rev.target_candidate_id, "Violation: Self-voting detected in peer review!"
        assert rev.composite_score > 0.0


# 3. Minority Report & Dissent Preservation
def test_minority_dissent_preservation():
    query = "Evaluate whether to migrate from monolith to microservices"
    result = CouncilEngine.run_council(query, roster=["model-a", "model-b"])

    # If scores are close, minority dissent is explicitly recorded
    assert result.consensus_percentage > 0.0
    if result.minority_dissent:
        assert "dissents" in result.minority_dissent.lower() or "caution" in result.minority_dissent.lower()


# 4. Sparse Multi-Agent Debate with Early Stopping
def test_sparse_debate_engine_and_judge_ruling():
    query = "Debate event sourcing vs relational CRUD for order audit compliance"
    result = DebateEngine.run_debate(query, roster=["advocate-1", "critic-2", "judge-3"], max_rounds=3)

    assert result.strategy_used == DeliberationStrategy.DEBATE
    assert "Debate Verdict" in result.final_answer
    assert "Judge Ruling" in result.final_answer
    assert len(result.key_evidence) >= 1
    assert result.minority_dissent is not None


# 5. Verifier Hierarchy (Deterministic Tests > Consensus)
def test_verifier_hierarchy():
    engine = get_master_deliberation_engine()
    res = engine.deliberate("Refactor string parser function", strategy=DeliberationStrategy.COUNCIL)

    # Without test command -> verified via peer consensus
    assert res.verification_status in ("verified", "consensus_supported")

    # With deterministic test command -> elevates verification to deterministic_pass
    verified_res = DeliberationVerifier.verify_and_calibrate(res, code_test_command="pytest tests/test_parser.py")
    assert verified_res.verification_status == "deterministic_pass"
    assert verified_res.confidence_level == DeliberationConfidence.HIGH_CONFIDENCE


# 6. Master Deliberation Engine Fast Paths
def test_master_deliberation_engine_fast_paths():
    engine = get_master_deliberation_engine()

    # SINGLE strategy
    single_res = engine.deliberate("What is 2+2?", strategy=DeliberationStrategy.SINGLE)
    assert single_res.strategy_used == DeliberationStrategy.SINGLE
    assert single_res.confidence_score == 0.95
    assert single_res.duration_seconds < 1.0

    # ENSEMBLE strategy
    ens_res = engine.deliberate("Brainstorm 3 naming options", strategy=DeliberationStrategy.ENSEMBLE)
    assert ens_res.strategy_used == DeliberationStrategy.ENSEMBLE
    assert len(ens_res.candidate_rankings) >= 2


# 7. Built-in Agent Tool `deliberate`
def test_deliberation_tool_invocation():
    # Evaluate action
    eval_output = deliberation_tool.invoke(
        {
            "action": "evaluate",
            "prompt": "Evaluate migrating to Kubernetes",
        }
    )
    assert "Deliberation Pre-Flight Evaluation" in eval_output
    assert "Recommended Strategy" in eval_output

    # Deliberate action (auto)
    delib_output = deliberation_tool.invoke(
        {
            "action": "deliberate",
            "prompt": "Choose between MongoDB and Cassandra for time-series logs",
        }
    )
    assert "Multi-LLM Deliberation Result" in delib_output
    assert "Confidence Score" in delib_output

    # Forced debate action
    debate_output = deliberation_tool.invoke(
        {
            "action": "debate",
            "prompt": "Debate monolithic vs serverless",
            "max_rounds": 2,
        }
    )
    assert "DEBATE" in debate_output


# 8. Gateway REST Endpoints
@pytest.mark.asyncio
async def test_gateway_deliberation_router():
    from app.gateway.routers import deliberation as delib_router

    # 1. Evaluate endpoint
    eval_resp = await delib_router.evaluate_deliberation_feasibility(delib_router.DeliberationEvaluateRequest(prompt="Debate monolith vs microservices"))
    assert eval_resp["strategy"] == "debate"
    assert eval_resp["worthwhile"] is True

    # 2. Run endpoint
    run_resp = await delib_router.run_deliberation(
        delib_router.DeliberationRunRequest(
            prompt="Architectural review of user token authentication",
            strategy="council",
        )
    )
    assert run_resp["strategy_used"] == "council"
    assert run_resp["confidence_score"] >= 0.80
    assert len(run_resp["candidate_rankings"]) > 0


# 9. Strict Harness Boundary Invariant
def test_deliberation_boundary_integrity():
    """Confirms packages/harness/agent_workspace/deliberation contains zero forbidden imports from app.*."""
    import pathlib

    delib_dir = pathlib.Path(__file__).parent.parent / "packages" / "harness" / "agent_workspace" / "deliberation"
    for py_file in delib_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "from app." not in content, f"Boundary violation in {py_file}: contains 'from app.'"
        assert "import app." not in content, f"Boundary violation in {py_file}: contains 'import app.'"
