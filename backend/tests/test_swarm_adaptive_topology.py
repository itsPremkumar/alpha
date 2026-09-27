"""Structural topology routing, complexity tiers, and candidate-plan selection.

These are the "automatic and dynamic" parts of swarm mode: the plan's own
dependency shape decides the coordination topology, escalating tiers gate the
expensive machinery, and several candidate decompositions are scored against
each other instead of shipping the first one that occurred to the decomposer.

Every assertion here is about a number the code derives, not about a keyword —
that is the point of the layer under test.
"""

from __future__ import annotations

import pytest

from alpha.swarm.decomposer import SwarmTaskDecomposer
from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode, TaskNodeState
from alpha.swarm.planner import build_candidate, score_plan, select_best_candidate
from alpha.swarm.strategy import ComplexityTier, resolve_strategy, tier_for
from alpha.swarm.topology import compute_dag_features, route_topology


def _chain(count: int) -> dict[str, SwarmTaskNode]:
    tasks: dict[str, SwarmTaskNode] = {}
    for index in range(count):
        tasks[f"t{index}"] = SwarmTaskNode(
            task_id=f"t{index}",
            objective=f"stage {index}",
            dependencies=[] if index == 0 else [f"t{index - 1}"],
        )
    return tasks


def _fan_out(width: int) -> dict[str, SwarmTaskNode]:
    """One root -> `width` workers -> one collector."""

    tasks = {"root": SwarmTaskNode(task_id="root", objective="root")}
    middle = []
    for index in range(width):
        task_id = f"w{index}"
        middle.append(task_id)
        tasks[task_id] = SwarmTaskNode(task_id=task_id, objective=f"work {index}", dependencies=["root"])
    tasks["join"] = SwarmTaskNode(task_id="join", objective="join", dependencies=middle)
    return tasks


def _disjoint_pairs(pairs: int) -> dict[str, SwarmTaskNode]:
    """`pairs` independent two-node components, sharing no edges."""

    tasks: dict[str, SwarmTaskNode] = {}
    for index in range(pairs):
        tasks[f"a{index}"] = SwarmTaskNode(task_id=f"a{index}", objective=f"lead {index}")
        tasks[f"b{index}"] = SwarmTaskNode(task_id=f"b{index}", objective=f"follow {index}", dependencies=[f"a{index}"])
    return tasks


# --- feature extraction ----------------------------------------------------


def test_chain_features_measure_depth_equal_to_task_count():
    features = compute_dag_features(_chain(5))
    assert features.task_count == 5
    assert features.edge_count == 4
    assert features.depth == 5
    assert features.max_width == 1
    assert features.root_count == 1
    assert features.leaf_count == 1
    assert features.component_count == 1
    assert features.cyclic is False


def test_fan_out_features_measure_the_widest_level():
    features = compute_dag_features(_fan_out(6))
    assert features.task_count == 8
    assert features.depth == 3
    assert features.max_width == 6
    assert features.max_fan_in == 6
    assert features.max_fan_out == 6
    assert features.root_count == 1
    assert features.leaf_count == 1
    assert features.coupling < 0.5


def test_disjoint_components_are_counted_as_separate_sub_swarms():
    features = compute_dag_features(_disjoint_pairs(4))
    assert features.task_count == 8
    assert features.component_count == 4
    assert features.depth == 2
    assert features.max_width == 4


def test_cyclic_graphs_are_flagged_instead_of_raising():
    tasks = {
        "a": SwarmTaskNode(task_id="a", objective="a", dependencies=["b"]),
        "b": SwarmTaskNode(task_id="b", objective="b", dependencies=["c"]),
        "c": SwarmTaskNode(task_id="c", objective="c", dependencies=["a"]),
        "d": SwarmTaskNode(task_id="d", objective="d", dependencies=[]),
    }
    features = compute_dag_features(tasks)
    assert features.cyclic is True
    # The scheduler owns rejection; feature extraction must still describe
    # whatever it could measure rather than blowing up mid-routing.
    assert features.task_count == 4
    assert features.component_count == 2


def test_empty_plan_has_zero_shape():
    features = compute_dag_features({})
    assert features.task_count == 0
    assert features.depth == 0
    assert features.max_width == 0
    assert features.coupling == 0.0


# --- routing ---------------------------------------------------------------


def test_sub_minimum_graphs_never_claim_a_swarm_benefit():
    route = route_topology(compute_dag_features(_chain(2)))
    assert route.should_swarm is False
    assert route.mode is SwarmMode.PARALLEL
    assert "2 node" in route.rationale


def test_pure_chain_routes_to_hierarchical_and_admits_no_parallelism():
    route = route_topology(compute_dag_features(_chain(6)))
    assert route.mode is SwarmMode.HIERARCHICAL
    assert route.should_swarm is False
    assert route.confidence >= 0.9
    assert "pure chain" in route.rationale
    assert "width 1" in route.rationale


def test_wide_shallow_fan_out_routes_to_map_reduce_when_items_exist():
    route = route_topology(compute_dag_features(_fan_out(6)), items=[f"item-{i}" for i in range(6)])
    assert route.mode is SwarmMode.MAP_REDUCE
    assert route.should_swarm is True
    assert "width 6" in route.rationale


def test_wide_shallow_fan_out_without_items_routes_to_scatter_gather():
    route = route_topology(compute_dag_features(_fan_out(6)))
    assert route.mode is SwarmMode.SCATTER_GATHER
    assert route.should_swarm is True


def test_disjoint_components_route_to_scatter_gather_not_ensemble():
    """Redundancy is a claim about intent; shape alone cannot make it."""

    route = route_topology(compute_dag_features(_disjoint_pairs(4)))
    assert route.mode is SwarmMode.SCATTER_GATHER
    assert route.should_swarm is True
    assert "disjoint components" in route.rationale


def test_high_coupling_routes_to_hierarchical():
    # K2,2: two layers where every lower node depends on every upper node.
    # That is coupling 4/12 = 0.33 — within reach of a DAG's ~0.33 ceiling and
    # the densest acyclic shape this builder can make.
    nodes = {
        "a1": SwarmTaskNode(task_id="a1", objective="a1"),
        "a2": SwarmTaskNode(task_id="a2", objective="a2"),
        "b1": SwarmTaskNode(task_id="b1", objective="b1", dependencies=["a1", "a2"]),
        "b2": SwarmTaskNode(task_id="b2", objective="b2", dependencies=["a1", "a2"]),
    }
    features = compute_dag_features(nodes)
    assert features.coupling >= 0.30
    assert features.cyclic is False
    route = route_topology(features)
    assert route.mode is SwarmMode.HIERARCHICAL
    assert route.should_swarm is True
    assert "coupling" in route.rationale


def test_deep_wide_graph_routes_to_hierarchical():
    tasks = _chain(6)
    # Widen the tail so depth stays >= 4 while width reaches 3+.
    tasks["extra"] = SwarmTaskNode(task_id="extra", objective="extra", dependencies=["t1"])
    tasks["extra2"] = SwarmTaskNode(task_id="extra2", objective="extra2", dependencies=["t1"])
    tasks["extra3"] = SwarmTaskNode(task_id="extra3", objective="extra3", dependencies=["t2"])
    route = route_topology(compute_dag_features(tasks))
    assert route.features.depth >= 4
    assert route.features.max_width >= 3
    assert route.mode is SwarmMode.HIERARCHICAL


def test_cyclic_shape_falls_back_without_claiming_confidence():
    tasks = {
        "a": SwarmTaskNode(task_id="a", objective="a", dependencies=["b"]),
        "b": SwarmTaskNode(task_id="b", objective="b", dependencies=["a"]),
        "c": SwarmTaskNode(task_id="c", objective="c", dependencies=["a"]),
    }
    route = route_topology(compute_dag_features(tasks))
    assert route.confidence == 0.0
    assert route.should_swarm is False
    assert route.mode is SwarmMode.PARALLEL


def test_routing_is_deterministic_for_the_same_shape():
    features = compute_dag_features(_fan_out(5))
    first = route_topology(features, items=["a", "b", "c"])
    second = route_topology(compute_dag_features(_fan_out(5)), items=["a", "b", "c"])
    assert first.mode == second.mode
    assert first.rationale == second.rationale


# --- strategy precedence ---------------------------------------------------


def test_explicit_mode_is_never_overridden_by_structure():
    features = compute_dag_features(_chain(6))
    resolution = resolve_strategy("Debate the pros and cons", mode=SwarmMode.DEBATE, features=features)
    assert resolution.mode is SwarmMode.DEBATE
    assert resolution.source == "explicit"
    assert resolution.confidence == 1.0
    assert "caller specified mode" in resolution.rationale


def test_structure_outranks_the_keyword_heuristic_when_both_available():
    # Text-only: the heuristic sees no batch signal and says do not swarm.
    text_only = resolve_strategy("Coordinate the release checklist across three owners")
    # The same goal once measured as a wide fan-out: structure wins.
    structural = resolve_strategy(
        "Coordinate the release checklist across three owners",
        features=compute_dag_features(_fan_out(6)),
    )
    assert text_only.source == "heuristic"
    assert text_only.should_swarm is False
    assert structural.source == "topology"
    assert structural.should_swarm is True
    assert structural.confidence > 0.5


def test_adversarial_request_wins_over_structure_but_records_the_route():
    resolution = resolve_strategy(
        "Red team the payment service and argue both sides",
        features=compute_dag_features(_fan_out(4)),
    )
    assert resolution.mode is SwarmMode.DEBATE
    assert resolution.source == "semantic"
    assert resolution.route is not None
    assert resolution.route.mode is SwarmMode.MAP_REDUCE or resolution.route.mode is SwarmMode.SCATTER_GATHER


def test_comparison_goal_does_not_infer_debate():
    """Research shows interactive debate can lose to independent answers."""

    resolution = resolve_strategy("Compare React and Vue for the dashboard")
    assert resolution.mode is not SwarmMode.DEBATE


def test_consensus_request_forces_full_tier_regardless_of_shape():
    resolution = resolve_strategy("Ship it", mode=SwarmMode.PARALLEL, requires_consensus=True)
    assert resolution.tier is ComplexityTier.FULL
    assert resolution.allows_deliberation is True


# --- complexity tiers ------------------------------------------------------


def test_tiers_escalate_with_measured_shape():
    assert tier_for(should_swarm=False, features=None) is ComplexityTier.DIRECT
    assert tier_for(should_swarm=True, features=None) is ComplexityTier.SIMPLE
    assert tier_for(should_swarm=True, features=compute_dag_features(_fan_out(4))) is ComplexityTier.ADAPT
    assert tier_for(should_swarm=True, features=compute_dag_features(_disjoint_pairs(4))) is ComplexityTier.MEDIUM


def test_tier_gates_are_a_superset_of_each_other():
    full = resolve_strategy("x", mode=SwarmMode.PARALLEL, requires_consensus=True)
    simple = resolve_strategy("x", mode=SwarmMode.PARALLEL)
    # Escalating gates: each higher tier keeps every lower tier's capability.
    assert full.allows_stigmergy and full.allows_candidate_planning and full.allows_structural_routing
    assert not simple.allows_deliberation
    assert not simple.allows_stigmergy
    assert simple.allows_structural_routing is False


def test_direct_tier_reports_no_swarm():
    resolution = resolve_strategy("Fix a typo in the README")
    assert resolution.tier is ComplexityTier.DIRECT
    assert resolution.should_swarm is False
    assert resolution.mode is SwarmMode.PARALLEL


def test_unmeasurable_shape_downgrades_to_the_cheapest_terminating_tier():
    tasks = {
        "a": SwarmTaskNode(task_id="a", objective="a", dependencies=["b"]),
        "b": SwarmTaskNode(task_id="b", objective="b", dependencies=["a"]),
        "c": SwarmTaskNode(task_id="c", objective="c", dependencies=["a"]),
    }
    assert tier_for(should_swarm=True, features=compute_dag_features(tasks)) is ComplexityTier.SIMPLE


# --- plan scoring ----------------------------------------------------------


def test_scoring_prefers_wide_shallow_over_a_chain_for_the_same_goal():
    wide = SwarmPlan(swarm_id="w", goal="g", mode=SwarmMode.MAP_REDUCE, tasks=_fan_out(4))
    chain = SwarmPlan(swarm_id="c", goal="g", mode=SwarmMode.HIERARCHICAL, tasks=_chain(5))
    wide_score = score_plan(wide, max_concurrency=4)
    chain_score = score_plan(chain, max_concurrency=4)
    assert wide_score.total > chain_score.total
    assert chain_score.parallelism == 0.0
    assert wide_score.parallelism > 0.0


def test_width_beyond_the_concurrency_limit_is_never_rewarded():
    """A 16-way fan-out on 4 slots is four rounds of work, not 16-way parallel."""

    narrow = SwarmPlan(swarm_id="n", goal="g", mode=SwarmMode.PARALLEL, tasks={f"t{i}": SwarmTaskNode(task_id=f"t{i}", objective=f"t{i}") for i in range(4)})
    wide = SwarmPlan(swarm_id="w", goal="g", mode=SwarmMode.PARALLEL, tasks={f"t{i}": SwarmTaskNode(task_id=f"t{i}", objective=f"t{i}") for i in range(16)})
    assert score_plan(narrow, max_concurrency=4).total > score_plan(wide, max_concurrency=4).total
    # Both saturate the four slots, so only the extra rounds differ — the wider
    # plan is not credited with parallelism it cannot actually overlap.
    assert score_plan(narrow, max_concurrency=4).exposed_width == score_plan(wide, max_concurrency=4).exposed_width == 4
    assert score_plan(narrow, max_concurrency=4).lower_bound_rounds == 1
    assert score_plan(wide, max_concurrency=4).lower_bound_rounds == 4


def test_endorsement_is_bounded_cannot_flip_an_unusable_plan():
    chain = SwarmPlan(swarm_id="c", goal="g", mode=SwarmMode.HIERARCHICAL, tasks=_chain(6))
    plain = score_plan(chain, max_concurrency=4, endorsed=False)
    endorsed = score_plan(chain, max_concurrency=4, endorsed=True)
    assert endorsed.total - plain.total == pytest.approx(0.15, abs=1e-6)
    assert endorsed.total <= 1.0


def test_selection_tie_break_is_deterministic_and_prefers_fewer_tasks():
    tasks_a = {f"t{i}": SwarmTaskNode(task_id=f"t{i}", objective=f"t{i}") for i in range(4)}
    tasks_b = {f"t{i}": SwarmTaskNode(task_id=f"t{i}", objective=f"t{i}") for i in range(8)}
    first = build_candidate(SwarmPlan(swarm_id="a", goal="g", mode=SwarmMode.PARALLEL, tasks=tasks_a), max_concurrency=4)
    second = build_candidate(SwarmPlan(swarm_id="b", goal="g", mode=SwarmMode.HIERARCHICAL, tasks=tasks_b), max_concurrency=4)
    assert select_best_candidate([second, first]) is first
    assert select_best_candidate([first, second]) is first
    assert select_best_candidate([]) is None


# --- integrated decomposition ---------------------------------------------


def test_automatic_decomposition_records_the_resolved_strategy():
    plan = SwarmTaskDecomposer.decompose("Scan these repositories for vulnerabilities", items=[f"repo-{i}" for i in range(6)])
    strategy = plan.metrics.get("strategy")
    assert isinstance(strategy, dict)
    assert strategy["source"] in {"topology", "heuristic", "semantic", "explicit"}
    assert strategy["tier"] in {tier.value for tier in ComplexityTier}
    assert strategy["rationale"]


def test_automatic_decomposition_records_every_losing_candidate():
    plan = SwarmTaskDecomposer.decompose("Scan these repositories for vulnerabilities", items=[f"repo-{i}" for i in range(6)])
    candidates = plan.metrics.get("strategy_candidates")
    assert isinstance(candidates, list)
    assert len(candidates) >= 2
    # The losers are described too, so a rejected mode is auditable.
    for candidate in candidates:
        assert candidate["mode"] in {mode.value for mode in SwarmMode}
        assert "score" in candidate and "features" in candidate
    assert any(candidate["mode"] == plan.mode.value for candidate in candidates)


def test_explicit_mode_is_left_alone_by_candidate_selection():
    plan = SwarmTaskDecomposer.decompose("Compare approaches", mode=SwarmMode.DEBATE)
    assert plan.mode is SwarmMode.DEBATE
    assert "strategy_candidates" not in plan.metrics
    assert {task.task_id for task in plan.tasks.values()} == {"task-pro", "task-con", "task-evidence", "task-judge"}


def test_explicit_batch_mode_keeps_its_task_shape():
    plan = SwarmTaskDecomposer.decompose("Batch report generation", mode=SwarmMode.MAP_REDUCE, items=["A", "B"])
    assert plan.mode is SwarmMode.MAP_REDUCE
    assert len(plan.tasks) == 3
    assert "strategy_candidates" not in plan.metrics


def test_small_automatic_batch_keeps_the_existing_map_reduce_shape():
    """Two items never justify coordination, but the items still exist."""

    plan = SwarmTaskDecomposer.decompose("resume my swarm work", items=["alpha", "beta"])
    assert len(plan.tasks) == 3
    assert plan.tasks["task-reduce"].state is TaskNodeState.PENDING
    assert plan.tasks["task-reduce"].dependencies == ["task-map-1", "task-map-2"]
