"""Comprehensive test suite for the Universal Deliberation Engine.

Stage 4a (honesty) rewritten: every strategy now runs REAL model calls
through ``alpha.deliberation.invocation`` (stubbed offline here), consensus
and confidence are computed from real responses, the verifier EXECUTES
``code_test_command`` instead of stamping ``deterministic_pass`` for any
non-empty string, and Council/Debate refuse single-model rosters instead of
fabricating peer reviews. The assertions that used to pin the fabrications
(``confidence_score == 0.95``, canned answer templates, invented artifacts)
are replaced with teeth against exactly that behavior.

Validates:
1. Deliberation Router: task classification + REAL configured-model rosters.
2. 3-Stage Anonymous Council: blind generation, self-vote exclusion, rubric scoring, chairman synthesis.
3. Minority Report: dissent preservation when consensus is contested.
4. Sparse Multi-Agent Debate: real turns, convergence, parsed judge ruling.
5. Verifier Hierarchy: the command is really executed (pass AND fail paths).
6. Built-in `deliberate` tool actions + honest single-model errors.
7. Gateway REST endpoints (/api/deliberation/evaluate, /api/deliberation/run).
8. Strict architectural boundary firewall (zero imports from app.*).
"""

from __future__ import annotations

import sys

import pytest

from alpha.deliberation import invocation
from alpha.deliberation.council import CouncilEngine
from alpha.deliberation.debate import DebateEngine
from alpha.deliberation.engine import get_master_deliberation_engine
from alpha.deliberation.models import (
    DeliberationConfidence,
    DeliberationStrategy,
)
from alpha.deliberation.router import (
    DeliberationRouter,
    TaskDifficulty,
    TaskRisk,
)
from alpha.deliberation.verifier import DeliberationVerifier
from alpha.tools.builtins.deliberation_tool import deliberation_tool


def _fake_invoke(model_name, *, system, user):
    """Deterministic offline stand-in for one REAL model invocation.

    Dispatches on the system prompt each strategy actually sends, so the
    parsers, similarity math, and aggregation all exercise real code paths.
    """
    s = (system or "").lower()
    if "independent judge" in s:
        return (
            "WINNER: PROPONENT\n"
            "RATIONALE: The affirmative's concrete evidence outweighed the cost concerns."
        )
    if "peer reviewer" in s and "rubric" in s:
        return (
            "CORRECTNESS: 0.90\n"
            "EVIDENCE: 0.90\n"
            "REASONING: 0.90\n"
            "COMPLETENESS: 0.90\n"
            "CLARITY: 0.90\n"
            "CRITIQUE: Sound reasoning backed by citeable evidence.\n"
            "FLAWS: narrow scope | few failure modes covered"
        )
    if "proponent in a structured debate" in s:
        return (
            "STUB-PROPONENT: event sourcing gives a complete, replayable audit trail.\n"
            "EVIDENCE: audit benchmark | replay tooling"
        )
    if "critic (opponent) in a structured debate" in s:
        return (
            "STUB-CRITIC: extra infrastructure cost and projection lag outweigh the audit benefits.\n"
            "EVIDENCE: cost model | lag measurements"
        )
    if "anonymous deliberation" in s:
        return (
            f"STUB-COUNCIL-{model_name}: adopt bounded contexts for the migration.\n"
            "STATED CLAIMS: bounded blast radius | simpler ops | easier rollout\n"
            "SELF CONFIDENCE: 0.88"
        )
    if "parallel ensemble" in s:
        return f"STUB-ENSEMBLE-{model_name}: structured take on: {(user or '')[:60]}"
    if "direct, accurate assistant" in s:
        return f"STUB-SINGLE: the answer to '{(user or '')[:40]}' is 4."
    return f"STUB-MODEL-OUTPUT ({model_name}): {(user or '')[:60]}"


@pytest.fixture(autouse=True)
def _offline_deliberation_models(monkeypatch):
    """Bind the deterministic offline model through the single real seam."""
    monkeypatch.setattr(invocation, "invoke_model", _fake_invoke)
    yield


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


def test_router_rosters_are_real_configured_models():
    """Teeth: rosters used to be invented names that no config could resolve."""
    ev = DeliberationRouter.classify("Design the event-driven architecture for payments")
    assert ev.roster_models == invocation.configured_model_roster()
    assert "candidate-1" not in ev.roster_models
    assert "advocate-alpha" not in ev.roster_models
    assert "lead-model" not in ev.roster_models


# 2. 3-Stage Anonymous Council Engine & Self-Vote Exclusion
def test_3_stage_anonymous_council_and_self_vote_exclusion():
    query = "Should we adopt Rust or Go for high-throughput networking sidecars?"
    result = CouncilEngine.run_council(query, roster=["model-a", "model-b", "model-c"])

    assert result.strategy_used == DeliberationStrategy.COUNCIL
    assert result.confidence_score >= 0.80
    assert len(result.candidate_rankings) == 3
    assert "Consensus Recommendation" in result.final_answer
    # Teeth: the winner's response is the model's real output, not a canned
    # "Architectural Recommendation: ..." template keyed by list index.
    assert "STUB-COUNCIL-model-" in result.final_answer

    # Verify self-vote exclusion invariant
    candidates = CouncilEngine._stage1_blind_generation(query, ["model-a", "model-b", "model-c"])
    reviews = CouncilEngine._stage2_peer_review(query, candidates)

    assert reviews, "peer reviews must actually run"
    for rev in reviews:
        # Invariant: A model must NEVER review its own answer!
        assert rev.reviewer_candidate_id != rev.target_candidate_id, "Violation: Self-voting detected in peer review!"
        assert rev.composite_score > 0.0
        # Teeth: scores are parsed from the reviewer model's real rubric
        # output, not the old constants (0.90/0.88/0.86/0.85/0.92).
        assert set(rev.rubric_scores) == {"correctness", "evidence", "reasoning", "completeness", "clarity"}
        assert rev.critique and "Sound reasoning" in rev.critique


def test_council_and_debate_refuse_single_model_rosters():
    """Teeth: a one-model 'council' has zero possible peer reviews; the old
    code ran it anyway and synthesized a fabricated 0.8 score for the
    candidate nobody reviewed."""
    with pytest.raises(RuntimeError, match="at least 2"):
        CouncilEngine.run_council("Design the payment ledger", roster=["only-one"])
    with pytest.raises(RuntimeError, match="at least 2"):
        DebateEngine.run_debate("Event sourcing vs CRUD", roster=["only-one"])


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
    # Teeth: the ruling is parsed from the judge model's actual output —
    # the old engine hardcoded winner_label = "Proponent" with a canned
    # rationale, and evidence was ["Empirical scale benchmark", ...] always.
    assert "Judge (judge-3) Verdict" in result.final_answer
    assert result.key_evidence, "debaters' real EVIDENCE lines must be parsed"
    assert result.minority_dissent is not None
    # Turn arguments are real model outputs, not round-templated strings.
    assert "STUB-PROPONENT" in result.final_answer


def test_judge_verdict_parsing_refuses_to_invent():
    """Teeth: prose that never states a WINNER line must yield no ruling."""
    from alpha.deliberation.parsing import parse_judge_verdict

    winner, rationale = parse_judge_verdict("I think the proponent did well overall.")
    assert winner is None
    assert rationale == ""


# 5. Verifier Hierarchy (Deterministic Tests > Consensus) — really executed
def test_verifier_hierarchy_executes_the_real_command():
    engine = get_master_deliberation_engine()
    res = engine.deliberate(
        "Refactor string parser function",
        strategy=DeliberationStrategy.COUNCIL,
        roster=["model-a", "model-b", "model-c"],
    )

    # Without a test command -> honestly labelled as model consensus only.
    assert res.verification_status in ("verified", "consensus_supported")

    # A real passing command -> elevates to deterministic_pass.
    import copy

    passing = f'"{sys.executable}" -c "raise SystemExit(0)"'
    ok = DeliberationVerifier.verify_and_calibrate(copy.deepcopy(res), code_test_command=passing)
    assert ok.verification_status == "deterministic_pass"
    assert ok.confidence_level == DeliberationConfidence.HIGH_CONFIDENCE

    # Teeth: a FAILING command must not be stamped as a pass — the old
    # implementation never ran the command and returned deterministic_pass
    # for any non-empty string (the old test used a path that does not exist).
    # deepcopy: verify_and_calibrate mutates in place and returns its input,
    # so comparing two calls on the same object would alias to one value.
    failing = f'"{sys.executable}" -c "raise SystemExit(1)"'
    bad = DeliberationVerifier.verify_and_calibrate(copy.deepcopy(res), code_test_command=failing)
    assert bad.verification_status == "deterministic_fail"
    assert bad.confidence_score < ok.confidence_score
    assert bad.confidence_level == DeliberationConfidence.CONTESTED


# 6. Master Deliberation Engine Fast Paths
def test_master_deliberation_engine_fast_paths():
    engine = get_master_deliberation_engine()

    # SINGLE strategy
    single_res = engine.deliberate("What is 2+2?", strategy=DeliberationStrategy.SINGLE)
    assert single_res.strategy_used == DeliberationStrategy.SINGLE
    # Teeth: real model output replaces the old canned template, and an
    # unverified single answer sits at the neutral baseline — not the old
    # fabricated 0.95/HIGH/"verified".
    assert single_res.final_answer.startswith("STUB-SINGLE")
    assert single_res.confidence_score == 0.5
    assert single_res.verification_status in ("verified", "consensus_supported")
    assert single_res.duration_seconds < 1.0

    # ENSEMBLE strategy
    ens_res = engine.deliberate("Brainstorm 3 naming options", strategy=DeliberationStrategy.ENSEMBLE)
    assert ens_res.strategy_used == DeliberationStrategy.ENSEMBLE
    assert len(ens_res.candidate_rankings) >= 2
    # Teeth: the answer contains the real responses, not the old fiction
    # ("Model Alpha contributed foundational structure").
    assert "STUB-ENSEMBLE" in ens_res.final_answer
    assert "Model Alpha contributed" not in ens_res.final_answer


def test_auto_downranks_council_when_only_one_model(monkeypatch):
    """Teeth: with a single configured model an AUTO council request must
    honestly fall back to an ensemble (disclosed) instead of fabricating
    peer reviews from one model reviewing itself."""
    monkeypatch.setattr(invocation, "configured_model_roster", lambda: ["only-one"])
    engine = get_master_deliberation_engine()
    res = engine.deliberate("Design the payment architecture", strategy=DeliberationStrategy.AUTO)
    assert res.strategy_used == DeliberationStrategy.ENSEMBLE


# 7. Built-in Agent Tool `deliberate`
def test_deliberation_tool_invocation(monkeypatch):
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

    # Teeth: forcing a debate on a single-model config must fail honestly —
    # the old engine returned a fully fabricated debate (canned arguments,
    # hardcoded Proponent win, confidence 0.91) without calling any model.
    monkeypatch.setattr(invocation, "configured_model_roster", lambda: ["only-one"])
    with pytest.raises(RuntimeError, match="at least 2"):
        deliberation_tool.invoke(
            {
                "action": "debate",
                "prompt": "Debate monolithic vs serverless",
                "max_rounds": 2,
            }
        )


# 8. Gateway REST Endpoints
@pytest.mark.asyncio
async def test_gateway_deliberation_router(monkeypatch):
    from fastapi import HTTPException

    from app.gateway.routers import deliberation as delib_router

    # 1. Evaluate endpoint
    eval_resp = await delib_router.evaluate_deliberation_feasibility(delib_router.DeliberationEvaluateRequest(prompt="Debate monolith vs microservices"))
    assert eval_resp["strategy"] == "debate"
    assert eval_resp["worthwhile"] is True

    # 2. Run endpoint — ensemble executes for real (offline stub).
    run_resp = await delib_router.run_deliberation(
        delib_router.DeliberationRunRequest(
            prompt="Architectural review of user token authentication",
            strategy="ensemble",
        )
    )
    assert run_resp["strategy_used"] == "ensemble"
    assert run_resp["confidence_score"] >= 0.80
    assert len(run_resp["candidate_rankings"]) > 0

    # 3. Teeth: a forced council on a one-model config surfaces the real
    # reason as an honest 503 instead of a fabricated 3-model review.
    monkeypatch.setattr(invocation, "configured_model_roster", lambda: ["only-one"])
    with pytest.raises(HTTPException) as excinfo:
        await delib_router.run_deliberation(
            delib_router.DeliberationRunRequest(
                prompt="Architectural review of user token authentication",
                strategy="council",
            )
        )
    assert excinfo.value.status_code == 503
    assert "at least 2" in str(excinfo.value.detail)


# 9. Strict Harness Boundary Invariant
def test_deliberation_boundary_integrity():
    """Confirms packages/harness/alpha/deliberation contains zero forbidden imports from app.*."""
    import pathlib

    delib_dir = pathlib.Path(__file__).parent.parent / "packages" / "harness" / "alpha" / "deliberation"
    for py_file in delib_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "from app." not in content, f"Boundary violation in {py_file}: contains 'from app.'"
        assert "import app." not in content, f"Boundary violation in {py_file}: contains 'import app.'"
