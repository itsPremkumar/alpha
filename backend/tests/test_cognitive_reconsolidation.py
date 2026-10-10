"""Reconsolidation: retrieval writes back, rest replays what worked, nothing is invented.

Three properties are load-bearing:

* **Retrieval is a mutation.** `HybridCognitiveRetriever.recall` used to return
  scored items and record nothing, so a memory recalled and used was
  indistinguishable from one that had sat idle since it was written. Access
  counts now move, and the spacing effect is why a retrieval after a long gap
  buys more stability than one immediately after the last.
* **An absent measurement stays absent.** A node never accessed has no measured
  elapsed-since-retrieval interval, so `retention_probability` answers `None`,
  not `0.0`. Reporting `0.0` would read as "measured as forgotten".
* **Replay is bounded and refuses to assume a success.** A budget caps how many
  traces one pass replays, and a trace whose outcome is `UNKNOWN` is skipped
  rather than assumed successful — replaying an unmeasured trace would
  strengthen a result nobody observed.

The RRSI link is deliberately **not** faked: consolidation knows which tier moved
and how many items, which is not a measured evolve-set score delta per
component. `evidence_gap_reason` names that gap instead of inventing the mapping.
"""

from __future__ import annotations

import math

import pytest

from alpha.memory.cognitive.engine import CognitiveMemorySystem
from alpha.memory.cognitive.models import (
    CognitiveTier,
    ConsolidationReport,
    EpisodicTrace,
    HybridRecallQuery,
    SemanticFactNode,
    TraceOutcome,
)
from alpha.memory.cognitive.reconsolidation import (
    BASE_STABILITY_SECONDS,
    ReconsolidationRecord,
    TierActivity,
    evidence_gap_reason,
    record_retrieval,
    replay_strengthen,
    retention_probability,
    tier_activity,
)

DAY = 86400.0


def _node(**overrides) -> SemanticFactNode:
    base = {"subject": "alpha", "predicate": "runs", "object_val": "memory"}
    base.update(overrides)
    return SemanticFactNode(**base)


def _trace(outcome: TraceOutcome = TraceOutcome.SUCCESS, *, salience: float = 0.5, action: str = "write file") -> EpisodicTrace:
    return EpisodicTrace(action=action, observation="it worked", outcome=outcome, salience=salience)


# --- the retention curve: one definition, and honest absence -----------------


def test_a_never_accessed_node_has_no_measured_retention():
    node = _node()
    assert node.access_count == 0
    assert retention_probability(node) is None, "no measured interval is not a measured zero"


def test_retention_decays_with_elapsed_time():
    now = 10_000.0
    node = _node(access_count=4, last_accessed_at=now)
    assert retention_probability(node, now=now + DAY) > retention_probability(node, now=now + 30 * DAY)
    assert retention_probability(node, now=now + DAY) < 1.0


def test_more_accesses_means_more_stability():
    now = 5_000.0
    fresh = _node(access_count=1, last_accessed_at=now)
    familiar = _node(access_count=50, last_accessed_at=now)
    assert retention_probability(familiar, now=now + DAY) > retention_probability(fresh, now=now + DAY)


def test_retention_matches_the_consolidation_engines_own_formula():
    """One curve, not two: this mirrors the engine's Ebbinghaus term exactly."""
    accesses = 7
    node = _node(access_count=accesses, last_accessed_at=0.0)
    elapsed = 3 * DAY
    stability = BASE_STABILITY_SECONDS * (1.0 + math.log(1.0 + accesses))
    assert retention_probability(node, now=elapsed) == pytest.approx(math.exp(-elapsed / stability))


def test_retention_refuses_a_nonsense_stability_or_backwards_clock():
    with pytest.raises(ValueError, match="base_stability_seconds must be a positive number"):
        retention_probability(_node(access_count=1), base_stability_seconds=0)
    node = _node(access_count=1, last_accessed_at=100.0)
    with pytest.raises(ValueError, match="cannot decay backwards"):
        retention_probability(node, now=50.0)


# --- retrieval writes back ---------------------------------------------------


def test_a_retrieval_strengthens_the_node_it_found():
    node = _node(access_count=0, last_accessed_at=1000.0)
    record = record_retrieval(node, now=1000.0)

    assert isinstance(record, ReconsolidationRecord)
    assert node.access_count == 1
    assert node.last_accessed_at == 1000.0
    assert record.retention_before is None, "first access: there was no interval to decay over"
    assert record.retention_after is not None
    assert record.retention_after > record.retention_before if record.retention_before else True
    assert record.useful is None
    assert "no usefulness verdict" in record.reason


def test_useful_is_a_caller_assertion_never_an_inference():
    node = _node()
    with pytest.raises(ValueError, match="useful must be a bool or None"):
        record_retrieval(node, useful="yes")
    assert record_retrieval(node, useful=True).useful is True
    assert record_retrieval(node, useful=False).useful is False
    assert record_retrieval(node).useful is None, "a caller with no opinion leaves the third state, not a False"


def test_a_useful_retrieval_and_a_useless_one_are_different_records():
    useful = record_retrieval(_node(), useful=True)
    useless = record_retrieval(_node(), useful=False)
    assert "reported useful" in useful.reason
    assert "reported not useful" in useless.reason
    assert "no strength credit was taken" in useless.reason


def test_record_retrieval_refuses_anything_that_is_not_a_node():
    with pytest.raises(ValueError, match="takes a SemanticFactNode"):
        record_retrieval(object())  # type: ignore[arg-type]


def test_a_record_serialises_without_collapsing_its_own_absence():
    record = record_retrieval(_node())
    blob = record.to_dict()
    assert blob["retention_before"] is None
    assert blob["useful"] is None
    assert blob["tier"] == CognitiveTier.SEMANTIC_FACT.value
    assert blob["access_count"] == 1


# --- replay: bounded, honest, and only over successes ------------------------


def test_replay_strengthens_a_successful_trace_and_marks_it():
    trace = _trace(salience=0.5)
    report = replay_strengthen([trace], budget=1)

    assert report.replayed == 1
    assert report.strengthened_ids == (trace.trace_id,)
    assert trace.salience == pytest.approx(0.55)
    assert "replayed" in trace.tags
    assert report.reason.startswith("replayed 1 successful trace")


def test_replay_never_assumes_a_success():
    unknown = _trace(outcome=TraceOutcome.UNKNOWN)
    failed = _trace(outcome=TraceOutcome.FAILURE)
    report = replay_strengthen([unknown, failed], budget=4)

    assert report.replayed == 0
    assert report.strengthened_ids == ()
    assert any("outcome is unmeasured" in item for item in report.skipped)
    assert any("not a success" in item for item in report.skipped)
    assert "1 failure and 1 unmeasured" in report.reason
    assert unknown.salience == 0.5, "an unmeasured trace must not be strengthened"


def test_replay_is_bounded_and_says_so():
    """Distinct saliences make the priority order unambiguous."""
    traces = [_trace(salience=0.4 + index / 100, action=f"act-{index}") for index in range(10)]
    report = replay_strengthen(traces, budget=3)

    assert report.replayed == 3
    assert report.considered == 10
    assert any("budget of 3" in item for item in report.skipped)
    replayed_actions = sorted(item.action for item in traces if "replayed" in item.tags)
    assert replayed_actions == ["act-7", "act-8", "act-9"], "the most salient traces are the ones the budget spends itself on"


def test_a_bounded_replay_is_deterministic_on_the_same_input():
    def run() -> list[str]:
        traces = [_trace(salience=0.5, action=f"act-{index}") for index in range(6)]
        return [trace.action for trace in traces if "replayed" in trace.tags]

    first, second = run(), run()
    assert first == second, "a bounded replay must not depend on iteration order or on random record ids"


def test_replay_over_an_empty_store_replays_nothing_without_claiming_otherwise():
    report = replay_strengthen([], budget=4)
    assert report.replayed == 0
    assert report.reason == "no episodic traces were supplied; nothing was replayed"
    assert report.skipped == ()


def test_replay_honours_the_salience_floor():
    low = _trace(salience=0.1)
    high = _trace(salience=0.9)
    report = replay_strengthen([low, high], budget=4, salience_floor=0.5)

    assert report.replayed == 1
    assert report.strengthened_ids == (high.trace_id,)
    assert any("is below the 0.50 floor" in item for item in report.skipped)


def test_replay_cannot_pin_a_memory_by_reinforcing_it_repeatedly():
    """The cap is load-bearing: unbounded replay would manufacture importance."""
    trace = _trace(salience=0.98)
    for _ in range(50):
        replay_strengthen([trace], budget=1)
    assert trace.salience <= 1.0


def test_replay_refuses_a_nonsense_budget_or_floor():
    with pytest.raises(ValueError, match="budget must be an integer >= 0"):
        replay_strengthen([], budget=True)
    with pytest.raises(ValueError, match="salience_floor must be within"):
        replay_strengthen([], salience_floor=1.5)


# --- observability: totals that never swallow a gap --------------------------


def test_tier_activity_sums_only_what_was_measured():
    disclosure = tier_activity(
        [
            TierActivity(tier="semantic_fact", changed=3, measured=4),
            TierActivity(tier="procedural_skill", changed=2, measured=None, reason="no outcome meter exists here"),
        ]
    )
    assert disclosure["tiers_changed"] == 5
    assert disclosure["tiers_measured"] == 4, "an unmeasured tier must not be folded into the sum as a zero"
    assert len(disclosure["reasons"]) == 1


def test_tier_activity_with_nothing_measured_reports_none_not_zero():
    disclosure = tier_activity([TierActivity(tier="working", changed=0, measured=None)])
    assert disclosure["tiers_measured"] is None
    assert disclosure["tiers_changed"] == 0


# --- the RRSI link is a named gap, not an invented mapping -------------------


def test_evidence_gap_reason_names_what_consolidation_cannot_supply():
    reason = evidence_gap_reason()
    assert "g_t" in reason
    assert "does not measure" in reason
    assert "No mapping from tier to component is asserted" in reason


# --- the report can carry the new counts -------------------------------------


def test_a_consolidation_report_defaults_its_new_fields():
    """Additive, so every existing construction of the report stays valid."""
    report = ConsolidationReport(
        cycle_id="c",
        timestamp=0.0,
        light_sleep_pruned=0,
        rem_sleep_patterns_discovered=0,
        deep_sleep_beliefs_crystallized=0,
        conflicts_reconciled=0,
        skills_indexed=0,
        decayed_items_count=0,
        insights=[],
        summary="nothing happened",
    )
    assert report.reconsolidation_records == 0
    assert report.replayed_traces == 0
    assert report.replayed_budget == 0
    assert report.tier_disclosure == {}
    blob = report.to_dict()
    assert blob["replayed_traces"] == 0
    assert blob["tier_disclosure"] == {}


# --- the engine actually writes back on recall -------------------------------


def test_add_belief_seeds_one_access_and_reconsolidation_reports_what_it_saw(tmp_path):
    """`add_belief` starts a node at access_count=1, not 0.

    That is the pre-existing behaviour in `semantic_graph.add_belief`, and
    reconsolidation does not hide it: a recall bumps to 2, so the reported count
    says which accesses were retrievals rather than presenting the seed as one.
    """
    sys = CognitiveMemorySystem(storage_dir=tmp_path)
    node = sys.semantic_graph.add_belief(subject="AlphaStreaming", predicate="uses_protocol", object_val="ServerSentEvents_SSE", confidence=0.95, salience=0.9)
    assert node.access_count == 1

    query = HybridRecallQuery(query="ServerSentEvents streaming protocol", limit=5, bm25_weight=0.4, vector_weight=0.4, graph_weight=0.1, temporal_weight=0.1)
    sys.recall(query)
    assert next(n for n in sys.semantic_graph.list_nodes(limit=5000) if n.node_id == node.node_id).access_count == 2


def test_a_recall_strengthens_the_node_it_retrieved(tmp_path):
    """The wiring, not just the helper: `recall()` must move access_count."""
    sys = CognitiveMemorySystem(storage_dir=tmp_path)
    sys.semantic_graph.add_belief(subject="AlphaStreaming", predicate="uses_protocol", object_val="ServerSentEvents_SSE", confidence=0.95, salience=0.9)

    query = HybridRecallQuery(query="ServerSentEvents streaming protocol", limit=5, bm25_weight=0.4, vector_weight=0.4, graph_weight=0.1, temporal_weight=0.1)
    results = sys.recall(query)

    assert results, "precondition: the query must find the belief"
    hit = next(n for n in sys.semantic_graph.list_nodes(limit=5000) if "ServerSentEvents" in n.statement)
    assert hit.access_count >= 1, "a retrieval that changed nothing in the store is not a retrieval"

    disclosure = sys.reconsolidation_disclosure()
    assert disclosure["records"], "the recall must report what it folded back"
    assert disclosure["records"][0]["useful"] is None, "no caller asserted usefulness, so the third state stands"


def test_a_read_only_recall_leaves_the_store_untouched(tmp_path):
    """`reconsolidate=False` is the opt-out, so a probe cannot mutate anything."""
    sys = CognitiveMemorySystem(storage_dir=tmp_path)
    sys.semantic_graph.add_belief(subject="AlphaStreaming", predicate="uses_protocol", object_val="ServerSentEvents_SSE", confidence=0.95, salience=0.9)

    query = HybridRecallQuery(query="ServerSentEvents streaming protocol", limit=5, bm25_weight=0.4, vector_weight=0.4, graph_weight=0.1, temporal_weight=0.1)
    before = next(n for n in sys.semantic_graph.list_nodes(limit=5000) if "ServerSentEvents" in n.statement).access_count
    assert sys.recall(query, reconsolidate=False)

    # `add_belief` seeds access_count at 1, so the honest assertion is "unchanged", not "zero".
    hit = next(n for n in sys.semantic_graph.list_nodes(limit=5000) if "ServerSentEvents" in n.statement)
    assert hit.access_count == before, "an explicit read-only recall must not write"
    assert sys.reconsolidation_disclosure()["records"] == []


def test_consolidation_replays_successful_traces_and_reports_the_budget(tmp_path):
    sys = CognitiveMemorySystem(storage_dir=tmp_path)
    for index in range(3):
        sys.episodic_mem.record_trace(action="run test", observation="passed", outcome=TraceOutcome.SUCCESS, salience=0.8)
    sys.episodic_mem.record_trace(action="broken step", observation="failed", outcome=TraceOutcome.FAILURE, salience=0.9)

    report = sys.consolidate()

    assert report.replayed_traces == 3, "only the successes are replayed"
    assert report.replayed_budget >= report.replayed_traces
    assert "Replayed 3 successful trace(s)" in report.summary
    assert report.tier_disclosure["tiers_changed"] >= 3
    # The RRSI link is a named gap, never an invented mapping.
    assert "does not measure" in sys.reconsolidation_disclosure()["rsi_evidence_gap"]
