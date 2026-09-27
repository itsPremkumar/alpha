"""Stigmergic traces and failure-aware telemetry.

Both layers read data the plan already persists, so nothing here can be
manufactured by a model.  The properties pinned are the ones that keep the two
features honest:

* traces **evaporate** (age never strengthens, only independent witnesses do,
  and the amplification curve is deliberately sub-linear), stay **bounded** in
  entries/payload/provenance, and remain **advisory** — they rank context, they
  never gate a decision;
* telemetry reports ``None``/``unknown`` where a signal genuinely is not
  measurable, and expresses deliberation savings as a **round count**, never as
  a converted token figure that no meter would back.
"""

from __future__ import annotations

import pytest

from alpha.swarm.coordinator import SwarmCoordinator
from alpha.swarm.models import SwarmBudget, SwarmMode, SwarmPlan, SwarmTaskNode, TaskNodeState
from alpha.swarm.stigmergy import (
    MAX_PAYLOAD_CHARS,
    MAX_PROVENANCE,
    STRENGTH_FLOOR,
    StigmergicTraceStore,
    TraceCategory,
)
from alpha.swarm.telemetry import SwarmTelemetry, normalized_signature, token_novelty

# --- stigmergy: identity, corroboration, decay -----------------------------


def test_fresh_deposit_starts_at_unit_strength():
    store = StigmergicTraceStore()
    trace = store.deposit(category=TraceCategory.WORK, key="cache", payload="./tmp", provenance="w1", now=0.0)
    assert trace.strength == 1.0
    assert trace.deposits == 1
    assert trace.provenance == ["w1"]
    assert trace.created_at == 0.0


def test_independent_witnesses_amplify_sub_linearly_then_saturate():
    # ``deposit`` hands back the live stored trace, so each assertion reads the
    # store immediately: holding four references would just alias one object.
    store = StigmergicTraceStore()  # alpha=0.5, cap=2.0
    trace_id = StigmergicTraceStore.trace_id_for("work", "cache")
    first = store.deposit(category="work", key="cache", provenance="w1", now=0.0)
    assert first.strength == 1.0

    store.deposit(category="work", key="cache", provenance="w2", now=1.0)
    assert store.get(trace_id).strength == pytest.approx(1.5)  # 1 + 0.5 * (2-1)

    store.deposit(category="work", key="cache", provenance="w3", now=2.0)
    assert store.get(trace_id).strength == pytest.approx(2.0)  # 1 + 0.5 * (3-1)

    store.deposit(category="work", key="cache", provenance="w4", now=3.0)
    assert store.get(trace_id).strength == pytest.approx(2.0)  # saturated at the cap
    assert store.get(trace_id).deposits == 4


def test_one_worker_cannot_farm_its_own_strength():
    store = StigmergicTraceStore()
    store.deposit(category="work", key="cache", provenance="w1", now=0.0)
    again = store.deposit(category="work", key="cache", provenance="w1", now=5.0)
    assert again.deposits == 1
    assert again.strength == 1.0
    assert again.updated_at == 5.0  # only the freshness refreshes


def test_empty_key_is_refused():
    with pytest.raises(ValueError):
        StigmergicTraceStore().deposit(category="work", key="   ")


def test_constructor_bounds_are_enforced():
    with pytest.raises(ValueError):
        StigmergicTraceStore(max_traces=0)
    with pytest.raises(ValueError):
        StigmergicTraceStore(half_life_seconds=0)
    with pytest.raises(ValueError):
        StigmergicTraceStore(alpha=-1.0)
    with pytest.raises(ValueError):
        StigmergicTraceStore(strength_cap=0.5)


def test_evaporation_halves_with_the_half_life_and_drops_at_the_floor():
    store = StigmergicTraceStore(half_life_seconds=3600.0)
    store.deposit(category="work", key="stale", provenance="w1", now=0.0)

    assert store.evaporate(now=3600.0) == 0
    assert store.get(StigmergicTraceStore.trace_id_for("work", "stale")).strength == pytest.approx(0.5)

    # 0.5^5 = 0.03125 < STRENGTH_FLOOR: it has decayed into noise.
    removed = store.evaporate(now=18000.0)
    assert removed == 1
    assert store.count() == 0


def test_evaporation_is_idempotent_at_a_fixed_clock():
    store = StigmergicTraceStore(half_life_seconds=100.0)
    store.deposit(category="work", key="k", now=0.0)
    store.evaporate(now=100.0)
    strength_after_first = store.rank(limit=1, now=100.0)[0].strength
    store.evaporate(now=100.0)  # zero elapsed: must not double-decay
    assert store.rank(limit=1, now=100.0)[0].strength == strength_after_first


def test_repeated_reads_do_not_accelerate_decay():
    """Reading a trace must not age it.  ``rank`` evaporates on every call, so
    decay has to be measured from a decay epoch rather than ``updated_at`` --
    otherwise every read re-applies the whole window since the last deposit and
    a popular trace dies in proportion to how often it was consulted.
    """
    store = StigmergicTraceStore(half_life_seconds=1000.0)
    store.deposit(category="work", key="consulted", now=0.0)

    for clock in range(1, 11):
        store.rank(limit=5, now=float(clock))
    # 10 reads across 10 seconds of a 1000s half-life is one decay window.
    assert store.get(StigmergicTraceStore.trace_id_for("work", "consulted")).strength == pytest.approx(0.5 ** (10 / 1000))

    store.rank(limit=5, now=10.0)  # still at t=10: no further decay at all
    assert store.get(StigmergicTraceStore.trace_id_for("work", "consulted")).strength == pytest.approx(0.5 ** (10 / 1000))


def test_invalid_half_life_is_rejected():
    store = StigmergicTraceStore()
    with pytest.raises(ValueError):
        store.evaporate(now=1.0, half_life_seconds=0)


def test_payload_keys_and_provenance_are_bounded():
    store = StigmergicTraceStore()
    trace = store.deposit(category="work", key="k" * 400, payload="p" * 900, provenance="w0", now=0.0)
    assert len(trace.payload) == MAX_PAYLOAD_CHARS
    assert len(trace.key) == 200

    for index in range(MAX_PROVENANCE + 4):
        trace = store.deposit(category="work", key="k" * 400, provenance=f"w{index}", now=float(index + 1))
    assert len(trace.provenance) == MAX_PROVENANCE


def test_entry_count_is_bounded_by_dropping_the_weakest_then_oldest():
    store = StigmergicTraceStore(max_traces=3)
    for index in range(4):
        store.deposit(category="work", key=f"key-{index}", now=float(index))
    assert store.count() == 3
    # The oldest of four equal-strength traces is the one that goes.
    assert store.get(StigmergicTraceStore.trace_id_for("work", "key-0")) is None
    assert store.get(StigmergicTraceStore.trace_id_for("work", "key-3")) is not None


def test_ranking_is_deterministic_and_filters():
    store = StigmergicTraceStore()
    store.deposit(category="work", key="older", provenance="w1", now=0.0)
    store.deposit(category="discovery", key="newer", provenance="w1", now=1.0)

    ranked = store.rank(limit=10, now=1.0)
    assert [trace.key for trace in ranked] == ["newer", "older"]  # strength tie -> recency

    store.deposit(category="work", key="older", provenance="w2", now=2.0)  # corroborate -> strongest
    assert store.rank(limit=10, now=2.0)[0].key == "older"

    assert [trace.key for trace in store.rank(category=TraceCategory.DISCOVERY, limit=10, now=2.0)] == ["newer"]
    assert store.rank(category="conflict", limit=10, now=2.0) == []
    assert store.rank(limit=0, now=2.0) == []
    assert store.count(category="work") == 1


def test_ranking_respects_a_minimum_strength():
    store = StigmergicTraceStore()
    store.deposit(category="work", key="weak", provenance="w1", now=0.0)
    store.deposit(category="work", key="strong", provenance="w1", now=0.0)
    store.deposit(category="work", key="strong", provenance="w2", now=0.0)
    assert [trace.key for trace in store.rank(min_strength=1.4, now=0.0)] == ["strong"]


def test_store_round_trips_through_a_mapping():
    store = StigmergicTraceStore(max_traces=32, half_life_seconds=600.0, alpha=0.25, strength_cap=1.75)
    store.deposit(category="work", key="a", provenance="w1", now=1.0)
    store.deposit(category="work", key="a", provenance="w2", now=2.0)

    restored = StigmergicTraceStore.from_dict(store.to_dict())
    assert restored.max_traces == 32
    assert restored.half_life_seconds == 600.0
    assert restored.alpha == 0.25
    assert restored.strength_cap == 1.75
    assert restored.count() == 1
    trace = restored.rank(limit=1, now=2.0)[0]
    assert trace.deposits == 2
    assert trace.provenance == ["w1", "w2"]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"max_traces": "not-a-number"},
        {"traces": "not-a-list"},
        {"traces": ["not-a-mapping"]},
        {"traces": [{"trace_id": "", "category": "work", "key": "x"}]},  # no id
        {"traces": [{"trace_id": "tr-x", "category": "work", "key": "x", "strength": "bad"}]},
        {"traces": [{"trace_id": "tr-x", "category": "work", "key": "x", "strength": STRENGTH_FLOOR / 10}]},
    ],
)
def test_malformed_or_expired_stores_degrade_to_empty(payload):
    store = StigmergicTraceStore.from_dict(payload)
    assert store.count() == 0
    assert store.rank(limit=10) == []


def test_get_misses_safely():
    store = StigmergicTraceStore()
    assert store.get("tr-does-not-exist") is None


def test_traces_are_advisory_and_never_gate_a_decision():
    """A store holds no verdict: it can only rank, and ranking is reversible."""
    store = StigmergicTraceStore()
    store.deposit(category="dead_end", key="approach-a", provenance="w1", now=0.0)
    store.deposit(category="work", key="approach-b", provenance="w1", now=0.0)
    ranked = store.rank(limit=10, now=0.0)
    # Both survive; a dead-end hint does not delete anything, it just sorts.
    assert {trace.category for trace in ranked} == {"dead_end", "work"}
    store.clear()
    assert store.count() == 0


# --- telemetry: signals from persisted state -------------------------------


def test_signature_normalisation_and_novelty():
    assert normalized_signature("Hello,   World!") == "hello world"
    assert token_novelty("a b c", "a b c") == 0.0
    assert token_novelty("a b c", "x y z") == 1.0
    assert token_novelty("", "x") is None
    assert token_novelty("a", "") is None


def _plan(**kwargs) -> SwarmPlan:
    return SwarmPlan(swarm_id="swm-t", goal="g", mode=SwarmMode.PARALLEL, **kwargs)


def test_budget_pressure_reports_unknown_ceiling_as_unbounded():
    pressure = SwarmTelemetry.budget_pressure(_plan(budget=SwarmBudget()))
    assert pressure.state == "unbounded"
    assert pressure.tokens is None
    assert pressure.tool_calls is None
    assert pressure.exhausted_reason is None


@pytest.mark.parametrize(
    ("used", "expected"),
    [(100, "ok"), (750, "warning"), (950, "critical")],
)
def test_budget_pressure_escalates_with_measured_usage(used, expected):
    pressure = SwarmTelemetry.budget_pressure(_plan(budget=SwarmBudget(max_tokens=1000, used_tokens=used)))
    assert pressure.tokens == pytest.approx(used / 1000)
    assert pressure.state == expected


def test_exhausted_budget_is_reported_as_exhausted_not_merely_critical():
    pressure = SwarmTelemetry.budget_pressure(_plan(budget=SwarmBudget(max_tool_calls=10, used_tool_calls=4, exhausted_reason="tool_calls")))
    assert pressure.state == "exhausted"
    assert pressure.exhausted_reason == "tool_calls"


def test_wall_pressure_is_unknown_without_a_clock():
    budget = SwarmBudget(max_wall_seconds=60.0, started_at=1000.0)
    assert SwarmTelemetry.budget_pressure(_plan(budget=budget), now=1030.0).wall_seconds == pytest.approx(0.5)
    # No clock supplied: "unknown", never a reassuring zero.
    assert SwarmTelemetry.budget_pressure(_plan(budget=budget)).wall_seconds is None


def test_loop_indicator_flags_duplicated_objectives():
    tasks = {
        "a": SwarmTaskNode(task_id="a", objective="Process the same record"),
        "b": SwarmTaskNode(task_id="b", objective="Process  the same, record!"),
    }
    signal = SwarmTelemetry.loop_indicator(_plan(tasks=tasks))
    assert signal["flagged"] is True
    assert len(signal["repeated_objectives"]) == 1


def test_loop_indicator_is_quiet_for_distinct_work():
    tasks = {
        "a": SwarmTaskNode(task_id="a", objective="Extract the schema"),
        "b": SwarmTaskNode(task_id="b", objective="Write the migration"),
    }
    signal = SwarmTelemetry.loop_indicator(_plan(tasks=tasks))
    assert signal["flagged"] is False
    assert signal["repeated_objectives"] == []
    assert signal["retried_task_ids"] == []


def test_loop_indicator_flags_retries_and_duplicated_results():
    tasks = {
        "a": SwarmTaskNode(task_id="a", objective="one", attempts=3, state=TaskNodeState.COMPLETED, result_summary="identical answer"),
        "b": SwarmTaskNode(task_id="b", objective="two", state=TaskNodeState.COMPLETED, result_summary="identical answer"),
    }
    signal = SwarmTelemetry.loop_indicator(_plan(tasks=tasks))
    assert signal["retried_task_ids"] == ["a"]
    assert len(signal["repeated_results"]) == 1


def test_tool_instability_stays_unknown_without_outcome_evidence():
    plan = _plan(tasks={"a": SwarmTaskNode(task_id="a", objective="x")})
    signal = SwarmTelemetry.tool_instability(plan)
    assert signal["tool_error_rate"] is None  # no declared outcomes -> no rate
    assert signal["retry_rate"] == 0.0
    assert signal["top_failing_tools"] == []


def test_tool_instability_concentrates_failures_by_tool():
    tasks = {
        "a": SwarmTaskNode(task_id="a", objective="x", attempts=3, evidence=[{"tool": "web_search", "failed": True}, {"tool": "web_search", "status": "ok"}]),
        "b": SwarmTaskNode(task_id="b", objective="y", attempts=1, evidence=[{"tool": "web_search", "error": "timeout"}, {"tool": "read_file"}]),
    }
    signal = SwarmTelemetry.tool_instability(_plan(tasks=tasks))
    assert signal["evidence_rows"] == 4
    assert signal["tool_error_rate"] == pytest.approx(0.5)
    assert signal["top_failing_tools"][0] == {"tool": "web_search", "errors": 2}
    assert signal["retries"] == 2  # 3 + 1 attempts across 2 tasks
    assert signal["retry_rate"] == pytest.approx(0.5)


def test_information_gain_is_unknown_with_too_few_results():
    plan = _plan(tasks={"a": SwarmTaskNode(task_id="a", objective="x", state=TaskNodeState.COMPLETED, result_summary="only one")})
    signal = SwarmTelemetry.information_gain(plan)
    assert signal["mean_novelty"] is None
    assert signal["state"] == "unknown"
    assert signal["samples"] == 0


def test_information_gain_detects_a_plateau():
    tasks = {
        "a": SwarmTaskNode(task_id="a", objective="x", state=TaskNodeState.COMPLETED, completed_at=1.0, result_summary="the same conclusion again"),
        "b": SwarmTaskNode(task_id="b", objective="y", state=TaskNodeState.COMPLETED, completed_at=2.0, result_summary="the same conclusion again"),
    }
    signal = SwarmTelemetry.information_gain(_plan(tasks=tasks))
    assert signal["mean_novelty"] == 0.0
    assert signal["state"] == "low"


def test_information_gain_is_ok_when_results_are_genuinely_new():
    tasks = {
        "a": SwarmTaskNode(task_id="a", objective="x", state=TaskNodeState.COMPLETED, completed_at=1.0, result_summary="alpha beta gamma"),
        "b": SwarmTaskNode(task_id="b", objective="y", state=TaskNodeState.COMPLETED, completed_at=2.0, result_summary="delta epsilon zeta"),
    }
    signal = SwarmTelemetry.information_gain(_plan(tasks=tasks))
    assert signal["mean_novelty"] == 1.0
    assert signal["state"] == "ok"


def test_coordination_overhead_is_unknown_before_any_completion():
    plan = _plan(
        blackboard_context={"messages_state": {"messages": [{"m": 1}, {"m": 2}, {"m": 3}]}},
        tasks={"a": SwarmTaskNode(task_id="a", objective="x")},
    )
    signal = SwarmTelemetry.coordination_overhead(plan)
    assert signal["messages"] == 3
    assert signal["completed_tasks"] == 0
    assert signal["messages_per_completed_task"] is None


def test_coordination_overhead_is_normalised_by_completions():
    plan = _plan(
        blackboard_context={"messages_state": {"messages": [{"m": 1}, {"m": 2}]}},
        tasks={
            "a": SwarmTaskNode(task_id="a", objective="x", state=TaskNodeState.COMPLETED),
            "b": SwarmTaskNode(task_id="b", objective="y", state=TaskNodeState.COMPLETED),
        },
    )
    assert SwarmTelemetry.coordination_overhead(plan)["messages_per_completed_task"] == 1.0


def test_snapshot_aggregates_risk_signals():
    plan = _plan(
        budget=SwarmBudget(max_tokens=100, used_tokens=99),
        tasks={
            # 5 + 1 attempts over 2 tasks -> 4 retries -> 0.667 > the 0.5 bar.
            "a": SwarmTaskNode(task_id="a", objective="same goal", attempts=5, state=TaskNodeState.COMPLETED, completed_at=1.0, result_summary="same answer"),
            "b": SwarmTaskNode(task_id="b", objective="same goal", attempts=1, state=TaskNodeState.COMPLETED, completed_at=2.0, result_summary="same answer"),
        },
    )
    snapshot = SwarmTelemetry.snapshot(plan)
    assert set(snapshot["risk_signals"]) >= {"rework_loop", "budget_pressure", "retry_pressure", "low_information_gain"}
    assert "no estimated token conversion" in snapshot["basis"]


def test_deliberation_savings_are_rounds_not_converted_cost():
    report = {
        "status": "approved",
        "rounds_used": 3,
        "policy": {"max_rounds": 5},
    }
    snapshot = SwarmTelemetry.snapshot(_plan(), deliberation=report)
    assert snapshot["deliberation_rounds_saved"] == 2
    assert snapshot["deliberation"]["status"] == "approved"
    assert "token" not in str(snapshot["deliberation_rounds_saved"])


def test_deliberation_savings_stay_unknown_without_a_round_budget():
    snapshot = SwarmTelemetry.snapshot(_plan(), deliberation={"rounds_used": 2})
    assert snapshot["deliberation_rounds_saved"] is None


def test_snapshot_without_deliberation_reports_none():
    snapshot = SwarmTelemetry.snapshot(_plan())
    assert snapshot["deliberation"] is None
    assert snapshot["deliberation_rounds_saved"] is None


# --- coordinator wiring ----------------------------------------------------


def test_trace_bookkeeping_is_gated_by_complexity_tier():
    assert SwarmCoordinator._stigmergy_enabled(_plan(metrics={"strategy": {"tier": "medium"}})) is True
    assert SwarmCoordinator._stigmergy_enabled(_plan(metrics={"strategy": {"tier": "full"}})) is True
    assert SwarmCoordinator._stigmergy_enabled(_plan(metrics={"strategy": {"tier": "simple"}})) is False
    assert SwarmCoordinator._stigmergy_enabled(_plan(metrics={"strategy": {"tier": "direct"}})) is False
    # A pre-upgrade checkpoint with no recorded strategy keeps the cheap default.
    assert SwarmCoordinator._stigmergy_enabled(_plan()) is False


def test_completing_a_task_leaves_rankable_traces(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan(
        status="running",
        metrics={"strategy": {"tier": "medium"}},
        tasks={
            "task-1": SwarmTaskNode(
                task_id="task-1",
                objective="read the manifest",
                state=TaskNodeState.RUNNING,
                lease_id="L1",
                capability_tags=["yaml", "schema"],
                output_artifacts=["out/manifest.json"],
            )
        },
    )
    coordinator._swarms[plan.swarm_id] = plan
    updated = coordinator.complete_task(plan.swarm_id, "task-1", result_summary="manifest read", lease_id="L1")
    assert updated is not None

    stored = plan.blackboard_context.get("stigmergy")
    assert stored is not None
    store = StigmergicTraceStore.from_dict(stored)
    assert store.count() == 3
    assert {trace.category for trace in store.rank(limit=10)} == {"work", "artifact"}
    assert {trace.key for trace in store.rank(limit=10)} == {"yaml", "schema", "out/manifest.json"}


def test_simple_plans_do_not_pay_for_trace_bookkeeping(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan(
        status="running",
        metrics={"strategy": {"tier": "simple"}},
        tasks={"task-1": SwarmTaskNode(task_id="task-1", objective="x", state=TaskNodeState.RUNNING, lease_id="L2", capability_tags=["yaml"])},
    )
    coordinator._swarms[plan.swarm_id] = plan
    assert coordinator.complete_task(plan.swarm_id, "task-1", result_summary="done", lease_id="L2") is not None
    assert plan.blackboard_context.get("stigmergy") is None


def test_deposited_traces_are_persisted_and_owner_scoped(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan(owner_id="alice")
    coordinator._swarms[plan.swarm_id] = plan

    trace = coordinator.deposit_trace(plan.swarm_id, category="discovery", key="port-map", payload="gateway is 8001", provenance="w1")
    assert trace["strength"] == 1.0
    assert coordinator.traces(plan.swarm_id, category="discovery")[0]["key"] == "port-map"

    # Owner scoping: a caller claiming a different owner sees nothing.
    assert coordinator.traces(plan.swarm_id, owner_id="mallory") == []
    with pytest.raises(KeyError):
        coordinator.deposit_trace(plan.swarm_id, category="work", key="k", owner_id="mallory")

    reloaded = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    assert [trace["key"] for trace in reloaded.traces(plan.swarm_id)] == ["port-map"]


def test_unknown_swarm_operations_fail_closed(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    assert coordinator.traces("swm-missing") == []
    with pytest.raises(KeyError):
        coordinator.deposit_trace("swm-missing", category="work", key="k")


def test_metrics_exposes_strategy_and_telemetry(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan(metrics={"strategy": {"tier": "medium", "mode": "map_reduce", "source": "topology"}})
    coordinator._swarms[plan.swarm_id] = plan

    metrics = coordinator.metrics(plan.swarm_id)
    assert metrics["strategy"]["source"] == "topology"
    telemetry = metrics["telemetry"]
    assert "risk_signals" in telemetry
    assert "budget_pressure" in telemetry
    assert "loop_indicator" in telemetry
    assert telemetry["deliberation"] is None
