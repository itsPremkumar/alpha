
from alpha.planning.compiler import (
    CognitiveCompiler,
    ExecutionPlanIR,
    PlanValidityMonitor,
    RecoveryFallbackTree,
    ReplanDecision,
    StrategyArchetype,
    generate_strategy_candidates,
    partition_execution_waves,
)


def test_strategy_candidates_generation_and_scoring():
    candidates = generate_strategy_candidates("Implement distributed query cache", risk_tier="R2")
    assert len(candidates) == 3
    archetypes = [c.archetype for c in candidates]
    assert StrategyArchetype.MINIMALIST in archetypes
    assert StrategyArchetype.STAGED in archetypes
    assert StrategyArchetype.SWARM in archetypes

    for c in candidates:
        s = c.score()
        assert isinstance(s, float)


def test_execution_wave_partitioning():
    dag = {
        "A": [],
        "B": [],
        "C": ["A"],
        "D": ["A", "B"],
        "E": ["C", "D"],
    }
    waves = partition_execution_waves(dag)
    assert len(waves) == 3

    # Wave 1: A, B (no dependencies)
    assert waves[0].wave_index == 1
    assert set(waves[0].task_ids) == {"A", "B"}
    assert waves[0].parallel_allowed is True

    # Wave 2: C, D (depends on A, B)
    assert waves[1].wave_index == 2
    assert set(waves[1].task_ids) == {"C", "D"}

    # Wave 3: E (depends on C, D)
    assert waves[2].wave_index == 3
    assert set(waves[2].task_ids) == {"E"}


def test_recovery_fallback_tree():
    tree = RecoveryFallbackTree(
        primary_strategy="Run primary patch",
        fallback_a="Run worktree isolated fallback",
        fallback_b="Provide non-breaking stub",
    )
    assert tree.resolve_action(0) == "Run primary patch"
    assert tree.resolve_action(1) == "Run worktree isolated fallback"
    assert tree.resolve_action(2) == "Provide non-breaking stub"
    assert "Escalate" in tree.resolve_action(3)


def test_plan_validity_monitor():
    # Nominal
    rep_nom = PlanValidityMonitor.evaluate(passed_tasks=9, failed_tasks=1, total_tasks=10)
    assert rep_nom.decision == ReplanDecision.NOMINAL
    assert rep_nom.validity_score >= 0.70

    # Local Replan
    rep_loc = PlanValidityMonitor.evaluate(passed_tasks=6, failed_tasks=1, total_tasks=10, consecutive_failures=1)
    assert rep_loc.decision == ReplanDecision.LOCAL_REPLAN

    # Global Replan on invariant breach
    rep_glob = PlanValidityMonitor.evaluate(passed_tasks=8, failed_tasks=0, total_tasks=10, invariant_breach=True)
    assert rep_glob.decision == ReplanDecision.GLOBAL_REPLAN
    assert rep_glob.validity_score == 0.0


def test_cognitive_compiler_full_compilation():
    compiler = CognitiveCompiler()
    ir = compiler.compile(
        goal="Add ACID transaction support",
        task_dag={"scan": [], "write": ["scan"], "verify": ["write"]},
        risk_tier="R2",
    )
    assert isinstance(ir, ExecutionPlanIR)
    assert ir.goal == "Add ACID transaction support"
    assert ir.risk_tier == "R2"
    assert len(ir.execution_waves) == 3
    assert ir.chosen_strategy is not None
    assert len(ir.proof_obligations) >= 2
    d = ir.to_dict()
    assert "plan_id" in d
    assert "execution_waves" in d
