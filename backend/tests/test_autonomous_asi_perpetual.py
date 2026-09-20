"""Comprehensive unit and integration test suite for:
1. Autonomous Perpetual Daemon & Self-Configuration Engine
2. Recursive Agent Meta-Compiler & Self-Replication
3. Never-Ending Autonomous Operation & Stagnation Watchdog
4. Gateway REST API Route Handlers
"""

import pytest

from alpha.autoconfig import (
    ComplexityLevel,
    ModelTier,
    OperatingMode,
    RuntimeTuningUpdate,
    TopologyType,
    get_self_config_engine,
)
from alpha.metacompiler import (
    AgentBlueprint,
    AgentHotSwapCoordinator,
    AgentMetaCompiler,
    BenchmarkScorecard,
    MemoryLayout,
    MetaBenchmarkHarness,
    ReasoningStrategy,
    get_meta_compiler_lineage,
)
from alpha.perpetual import (
    DaemonState,
    PerpetualMemoryConsolidator,
    StagnationRecoveryWatchdog,
    get_perpetual_daemon,
)

# ==============================================================================
# 1. Autonomous Self-Configuration Engine Tests
# ==============================================================================


def test_autoconfig_complex_frontier_goal():
    engine = get_self_config_engine("test_proj_autoconfig_1")
    goal = "Build a world-class ASI agent harness with recursive self-improvement, autonomous perpetual daemon that will never stop, and meta-compiler capable of building its own next version with full verification."
    analysis = engine.analyze_goal(goal)

    assert analysis.complexity == ComplexityLevel.RESEARCH_FRONTIER
    assert analysis.suggested_mode == OperatingMode.AUTONOMOUS
    assert analysis.recommended_model_tier == ModelTier.REASONING_FRONTIER
    assert analysis.recommended_topology == TopologyType.HIERARCHICAL
    assert analysis.reasoning_budget_tokens >= 8192
    assert "code_executor" in analysis.tool_whitelist
    assert "epistemic_belief_graph" in analysis.tool_whitelist
    assert "rsi_engine" in analysis.tool_whitelist

    profile = engine.synthesize_profile(analysis)
    assert profile.operating_mode == OperatingMode.AUTONOMOUS
    assert profile.model_tier == ModelTier.REASONING_FRONTIER
    assert profile.primary_model == "claude-3-7-sonnet-thinking"
    assert profile.autonomous_pivots_enabled is True
    assert profile.reasoning_budget_tokens >= 8192


def test_autoconfig_simple_direct_goal():
    engine = get_self_config_engine("test_proj_autoconfig_2")
    analysis = engine.analyze_goal("Say hello world")

    assert analysis.complexity in {ComplexityLevel.TRIVIAL, ComplexityLevel.SIMPLE}
    assert analysis.suggested_mode == OperatingMode.DIRECT
    assert analysis.recommended_model_tier == ModelTier.FAST_LOCAL
    assert analysis.recommended_topology == TopologyType.SOLO


def test_autoconfig_dynamic_tuning():
    engine = get_self_config_engine("test_proj_autoconfig_3")
    analysis = engine.analyze_goal("Implement API endpoints for data ingestion")
    engine.synthesize_profile(analysis)

    updates = RuntimeTuningUpdate(
        reasoning_budget_tokens=16000,
        context_compaction_threshold=75000,
        loop_detection_limit=5,
        primary_model="o3-mini",
        extra_tools=["custom_profiler"],
    )
    tuned = engine.tune_profile(updates)

    assert tuned.reasoning_budget_tokens == 16000
    assert tuned.context_compaction_threshold == 75000
    assert tuned.loop_detection_limit == 5
    assert tuned.primary_model == "o3-mini"
    assert "custom_profiler" in tuned.active_tools

    status = engine.get_status()
    assert "active_profile" in status
    assert status["analyses_performed"] >= 1


# ==============================================================================
# 2. Recursive Agent Meta-Compiler & Self-Replication Tests
# ==============================================================================


def test_metacompiler_compilation_and_specialization():
    store = get_meta_compiler_lineage("test_proj_meta_1")
    parent = store.active_head
    assert parent.generation == 0

    # Compile next-generation candidate (Gen 1)
    candidate_gen1 = AgentMetaCompiler.compile_next_generation(
        parent=parent,
        optimization_target="epistemic_deliberation",
        mutation_notes="Upgraded reasoning strategy to epistemic falsification",
    )
    assert candidate_gen1.generation == 1
    assert candidate_gen1.parent_id == parent.blueprint_id
    assert candidate_gen1.reasoning_strategy in {
        ReasoningStrategy.EPISTEMIC_FALSIFICATION,
        ReasoningStrategy.DUAL_PROCESS_DELIBERATION,
        ReasoningStrategy.MCTS_DELIBERATION,
        ReasoningStrategy.HIERARCHICAL_DECOMPOSITION,
    }
    assert candidate_gen1.hyperparameters["max_reasoning_tokens"] > parent.hyperparameters["max_reasoning_tokens"]

    # Synthesize specialist
    swe_spec = AgentMetaCompiler.synthesize_specialist("swe_coding", parent)
    assert swe_spec.specialization == "swe_debugger_specialist"
    assert "code_executor" in swe_spec.tool_bindings


def test_metacompiler_benchmarking_and_hotswap():
    store = get_meta_compiler_lineage("test_proj_meta_2")
    parent = store.active_head

    candidate = AgentMetaCompiler.compile_next_generation(parent, "performance_and_reasoning")
    store.register_blueprint(candidate)

    # Benchmark candidate
    scorecard = MetaBenchmarkHarness.evaluate_blueprint(candidate, baseline_score=0.75)
    assert scorecard.composite_score >= 0.75
    assert scorecard.passed_regression_suite is False
    assert scorecard.evidence_kind == "simulated"
    assert scorecard.preview_passed is True
    assert len(scorecard.details) == 5
    store.record_benchmark(scorecard)

    outcome = store.promote_blueprint(candidate.blueprint_id)
    assert outcome.success is False
    assert outcome.new_head_id == parent.blueprint_id
    assert outcome.generation == parent.generation
    assert outcome.migrated_tasks == 0
    assert outcome.promoted_at == ""
    assert outcome.telemetry["deployed"] is False
    assert store.active_head.blueprint_id == parent.blueprint_id

    # Test rollback
    rollback_ok = store.rollback(parent.blueprint_id)
    assert rollback_ok is True
    assert store.active_head.blueprint_id == parent.blueprint_id

    # Pareto frontier
    frontier = store.get_pareto_frontier()
    assert len(frontier) >= 1


# ==============================================================================
# 3. Perpetual Never-Ending Daemon & Stagnation Watchdog Tests
# ==============================================================================


def test_stagnation_watchdog_loop_detection_and_intervention():
    watchdog = StagnationRecoveryWatchdog(loop_threshold=3)

    # Normal non-repeating actions
    watchdog.record_action("read_file_config.py")
    watchdog.record_action("edit_file_config.py")
    assert watchdog.check_stagnation() is None

    # Trigger repeated action loop
    stalled_action = "failing_test_execution_timeout"
    watchdog.record_action(stalled_action)
    watchdog.record_action(stalled_action)
    watchdog.record_action(stalled_action)

    incident = watchdog.check_stagnation()
    assert incident is not None
    assert incident.repeated_action == stalled_action
    assert incident.consecutive_failures == 3
    assert "backtrack" in incident.recovery_action_taken or "intervention" in incident.recovery_action_taken
    assert len(watchdog.get_incidents()) == 1


def test_perpetual_memory_consolidation():
    consolidator = PerpetualMemoryConsolidator("test_proj_perpetual_mem")
    report = consolidator.consolidate(trace_count=10)

    assert report.traces_analyzed == 10
    assert report.facts_extracted >= 1
    assert report.skills_indexed >= 1
    assert report.pruned_tokens > 0
    assert consolidator.get_latest_report().report_id == report.report_id


def test_perpetual_daemon_heartbeat_and_discovery():
    daemon = get_perpetual_daemon("test_proj_perpetual_daemon")
    assert daemon.state == DaemonState.RUNNING

    # Run discovery explicitly
    discovered = daemon.trigger_discovery()
    assert len(discovered) >= 1

    # Run heartbeat steps
    step1 = daemon.step_heartbeat()
    assert step1["heartbeat"] >= 1
    assert "state" in step1
    assert "goal_progress_percent" in step1

    # Add a custom goal
    goal = daemon.create_goal("Self-Sustaining Autonomous Evolution", "Continuous audit and verification", priority=1)
    assert goal.goal_id == daemon.active_goal.goal_id

    # Test pause and resume
    daemon.stop()
    assert daemon.state == DaemonState.PAUSED
    daemon.start()
    assert daemon.state == DaemonState.RUNNING

    # Check telemetry
    telemetry = daemon.get_telemetry()
    assert telemetry.heartbeat_count >= 1
    assert telemetry.total_tasks_discovered >= 1

    status = daemon.get_status()
    assert "telemetry" in status
    assert "active_goal" in status
    assert len(status["all_goals"]) >= 1


def test_hotswap_prevents_regression():
    store = get_meta_compiler_lineage("test_proj_meta_regress")
    head = store.active_head

    # Create high-scoring head
    high_scorecard = BenchmarkScorecard(
        blueprint_id=head.blueprint_id,
        composite_score=0.95,
        robustness_score=0.92,
        passed_regression_suite=True,
    )
    store.record_benchmark(high_scorecard)

    # Create lower-scoring candidate (0.82 is > 0.80 baseline, but < 0.95 current head)
    candidate = AgentMetaCompiler.compile_next_generation(head, "fast_testing")
    store.register_blueprint(candidate)
    low_scorecard = BenchmarkScorecard(
        blueprint_id=candidate.blueprint_id,
        generation=candidate.generation,
        composite_score=0.82,
        robustness_score=0.85,
        passed_regression_suite=True,
    )
    store.record_benchmark(low_scorecard)

    # Regular promotion MUST be rejected to prevent regression
    rejected_outcome = store.promote_blueprint(candidate.blueprint_id, force=False)
    assert rejected_outcome.success is False
    assert "unknown evidence" in rejected_outcome.telemetry["rejection_reason"].lower()
    assert store.active_head.blueprint_id == head.blueprint_id

    forced_outcome = store.promote_blueprint(candidate.blueprint_id, force=True)
    assert forced_outcome.success is False
    assert forced_outcome.migrated_tasks == 0
    assert store.active_head.blueprint_id == head.blueprint_id


def test_never_ending_daemon_continuous_discovery():
    daemon = get_perpetual_daemon("test_proj_perpetual_never_stop")

    # Run 12 successive heartbeats (far exceeding the 5 initial static tasks)
    for _ in range(12):
        step = daemon.step_heartbeat()
        assert step["heartbeat"] >= 1
        assert "state" in step

    # Daemon must not stall: it must have discovered more tasks than initial 5
    telemetry = daemon.get_telemetry()
    assert telemetry.total_tasks_discovered > 5
    assert telemetry.tasks_completed_count >= 10
    # Pending tasks must be discoverable continuously
    assert daemon.state != DaemonState.STOPPED


def test_stagnation_intervention_auto_tunes_profile():
    daemon = get_perpetual_daemon("test_proj_stagnation_healing")
    from alpha.autoconfig import get_self_config_engine

    cfg_engine = get_self_config_engine("test_proj_stagnation_healing")

    # Initial budget is standard
    assert cfg_engine.get_profile().reasoning_budget_tokens == 8192

    # Simulate 3 consecutive tool stalls to trigger stagnation
    for _ in range(3):
        daemon.watchdog.record_action("stalled_external_api_call")

    step = daemon.step_heartbeat()
    assert step["stagnation_incident"] is not None

    # Verify closed-loop healing: self-config profile was dynamically escalated
    escalated_profile = cfg_engine.get_profile()
    assert escalated_profile.reasoning_budget_tokens == 16384
    assert escalated_profile.thought_depth == "deep"


def test_metacompiler_hierarchical_decomposition_and_memory():
    store = get_meta_compiler_lineage("test_proj_hierarchical")
    parent = store.active_head

    candidate = AgentMetaCompiler.compile_next_generation(
        parent=parent,
        optimization_target="hierarchical_decomposition_and_modular_planning",
    )
    assert candidate.reasoning_strategy == ReasoningStrategy.HIERARCHICAL_DECOMPOSITION
    assert candidate.memory_layout == MemoryLayout.CONSOLIDATED_SEMANTIC

    # Evaluate blueprint with hierarchical decomposition
    sc = MetaBenchmarkHarness.evaluate_blueprint(candidate)
    assert sc.reasoning_score >= 0.88
    assert sc.composite_score >= 0.80


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("kind", ["simulated", "unknown", "measured"])
def test_hotswap_never_claims_deployment_without_adapter(force, kind):
    from alpha.metacompiler.benchmark import SimulatedBenchmarkScorecard

    parent = AgentBlueprint()
    candidate = AgentMetaCompiler.compile_next_generation(parent)
    scorecard = SimulatedBenchmarkScorecard(
        blueprint_id=candidate.blueprint_id,
        generation=candidate.generation,
        evidence_kind=kind,
        passed_regression_suite=True,
        composite_score=0.99,
    )
    outcome = AgentHotSwapCoordinator.execute_hotswap(parent, candidate, scorecard, force=force)
    assert not outcome.success
    assert outcome.new_head_id == parent.blueprint_id
    assert outcome.migrated_tasks == 0
    assert outcome.promoted_at == ""
    assert not outcome.telemetry["deployed"]
    assert not outcome.telemetry["promotion_verified"]
    if kind == "measured":
        assert "No deployment adapter" in outcome.telemetry["rejection_reason"]


def test_metacompiler_preview_serialization_and_legacy_scorecard():
    import json
    from dataclasses import fields

    from alpha.metacompiler.benchmark import SimulatedBenchmarkScorecard

    candidate = AgentBlueprint()
    scorecard = MetaBenchmarkHarness.evaluate_blueprint(candidate)
    payload = json.loads(json.dumps(scorecard.to_dict()))
    assert payload["evidence_kind"] == "simulated"
    assert payload["passed_regression_suite"] is False
    assert all(detail["status"] == "simulated" for detail in payload["details"])
    assert SimulatedBenchmarkScorecard(**payload).to_dict() == payload
    legacy = BenchmarkScorecard(**{field.name: payload[field.name] for field in fields(BenchmarkScorecard)})
    outcome = AgentHotSwapCoordinator.execute_hotswap(candidate, candidate, legacy, force=True)
    assert not outcome.success
    assert outcome.telemetry["evidence_kind"] == "unknown"
    assert not MetaBenchmarkHarness.evaluate_blueprint(candidate, baseline_score=1.0).preview_passed


@pytest.mark.parametrize("mismatch", ["blueprint_id", "generation"])
def test_hotswap_rejects_mismatched_scorecard(mismatch):
    parent = AgentBlueprint()
    candidate = AgentMetaCompiler.compile_next_generation(parent)
    scorecard = MetaBenchmarkHarness.evaluate_blueprint(candidate)
    setattr(scorecard, mismatch, "other" if mismatch == "blueprint_id" else 99)
    outcome = AgentHotSwapCoordinator.execute_hotswap(parent, candidate, scorecard, force=True)
    assert not outcome.success
    assert not outcome.telemetry["preview_qualifies"]
    assert "does not match" in outcome.telemetry["rejection_reason"]


@pytest.mark.parametrize("score", [0.1, float("nan"), float("inf"), -1.0, 2.0])
def test_hotswap_rejects_regressed_or_invalid_preview_even_with_force(score):
    parent = AgentBlueprint()
    candidate = AgentMetaCompiler.compile_next_generation(parent)
    scorecard = MetaBenchmarkHarness.evaluate_blueprint(candidate)
    scorecard.composite_score = score
    outcome = AgentHotSwapCoordinator.execute_hotswap(parent, candidate, scorecard, force=True)
    assert not outcome.success
    assert not outcome.telemetry["preview_qualifies"]
