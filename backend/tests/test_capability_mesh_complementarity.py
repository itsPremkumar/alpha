"""Tests for measured pair complementarity (plan §4.2) and correlated-failure risk.

Complementarity is never asserted without samples: a zero-sample estimate is
forced SPECULATIVE, OBSERVED/CONTRADICTED require ``PAIR_MIN_SAMPLES``, and
``complementarity_value`` returns None unless solo AND together quality were
all measured — a fabricated synergy number is refused at the door.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha.capability_mesh.complementarity import (
    PAIR_ESTIMATES_FILE_NAME,
    PAIR_MIN_SAMPLES,
    ComplementarityEvidence,
    CorrelationRisk,
    PairEstimate,
    PairMemory,
    PairMemoryStoreError,
    correlated_failure_risk,
)
from alpha.capability_mesh.graph import CapabilityGraph, EdgeKind, GraphEdge, GraphNode, NodeKind

PV = "p1"


def _pair(**kwargs) -> PairEstimate:
    defaults: dict = {
        "entity_a": "agent:a",
        "entity_b": "agent:b",
        "task_category": "*",
        "sample_count": PAIR_MIN_SAMPLES,
        "evidence": ComplementarityEvidence.OBSERVED,
        "profile_version": PV,
    }
    defaults.update(kwargs)
    return PairEstimate(**defaults)


# ----------------------------------------------------------- PairEstimate refusals


def test_pair_refuses_together_without_solo_a():
    with pytest.raises(ValueError, match="solo_a"):
        _pair(solo_a=None, solo_b=0.5, together=0.8)


def test_pair_refuses_together_without_solo_b():
    with pytest.raises(ValueError, match="solo_b"):
        _pair(solo_a=0.5, solo_b=None, together=0.8)


def test_pair_refuses_same_entity_twice():
    with pytest.raises(ValueError, match="distinct"):
        _pair(entity_b="agent:a")


def test_pair_refuses_empty_task_category():
    with pytest.raises(ValueError, match="task_category must be non-empty"):
        _pair(task_category="")


def test_pair_refuses_unknown_evidence_level():
    with pytest.raises(ValueError, match="unknown evidence"):
        _pair(evidence="measured")


def test_pair_refuses_negative_sample_count():
    with pytest.raises(ValueError, match="sample_count must be an int >= 0"):
        _pair(sample_count=-1)


def test_pair_refuses_rates_out_of_range():
    with pytest.raises(ValueError, match="disagreement_usefulness"):
        _pair(disagreement_usefulness=1.2)
    with pytest.raises(ValueError, match="redundant_call_rate"):
        _pair(redundant_call_rate=-0.1)


def test_pair_refuses_negative_cost_or_latency():
    with pytest.raises(ValueError, match="added_cost_usd"):
        _pair(added_cost_usd=-1.0)
    with pytest.raises(ValueError, match="added_latency_ms"):
        _pair(added_latency_ms=-5)


def test_pair_requires_profile_version_when_any_mean_is_set():
    with pytest.raises(ValueError, match="profile_version"):
        _pair(solo_a=0.5, solo_b=0.5, together=0.6, profile_version="")


def test_pair_observed_requires_the_sample_floor():
    with pytest.raises(ValueError, match="PAIR_MIN_SAMPLES"):
        _pair(sample_count=PAIR_MIN_SAMPLES - 1)


def test_pair_contradicted_also_requires_the_sample_floor():
    with pytest.raises(ValueError, match="PAIR_MIN_SAMPLES"):
        _pair(evidence=ComplementarityEvidence.CONTRADICTED, sample_count=2)


def test_zero_samples_forces_speculative_even_when_observed_was_claimed():
    estimate = _pair(solo_a=0.5, solo_b=0.5, together=0.8, sample_count=0, evidence=ComplementarityEvidence.OBSERVED)
    assert estimate.evidence is ComplementarityEvidence.SPECULATIVE


def test_thin_but_nonzero_samples_stay_speculative():
    estimate = _pair(solo_a=0.5, solo_b=0.5, together=0.8, sample_count=PAIR_MIN_SAMPLES - 2, evidence=ComplementarityEvidence.SPECULATIVE)
    assert estimate.evidence is ComplementarityEvidence.SPECULATIVE


# ------------------------------------------------------- complementarity_value


def test_complementarity_value_needs_all_three_means():
    assert _pair(solo_a=0.5, solo_b=0.5, together=None).complementarity_value() is None
    # together without a solo baseline never reaches this method: the
    # constructor refuses the unanchored record (see the together-without-solo
    # tests above), so a partial record cannot fabricate a synergy number.
    with pytest.raises(ValueError, match="solo_a"):
        _pair(solo_a=None, solo_b=0.5, together=0.8, evidence=ComplementarityEvidence.SPECULATIVE)
    with pytest.raises(ValueError, match="solo_b"):
        _pair(solo_a=0.5, solo_b=None, together=0.8, evidence=ComplementarityEvidence.SPECULATIVE)


def test_complementarity_value_is_together_minus_best_solo():
    assert _pair(solo_a=0.5, solo_b=0.7, together=0.9).complementarity_value() == pytest.approx(0.2)
    # Negative is allowed and honest: the pair did worse than the better member alone.
    assert _pair(solo_a=0.5, solo_b=0.7, together=0.4, evidence=ComplementarityEvidence.CONTRADICTED).complementarity_value() == pytest.approx(-0.3)


def test_pair_round_trips():
    estimate = _pair(solo_a=0.5, solo_b=0.6, together=0.8, disagreement_usefulness=0.4, redundant_call_rate=0.1, added_cost_usd=0.02, added_latency_ms=800)
    assert PairEstimate.from_dict(estimate.to_dict()) == estimate


# ----------------------------------------------------------------- PairMemory


def test_pair_memory_round_trips_symmetrically(tmp_path: Path):
    store = PairMemory(tmp_path)
    store.record(_pair())
    found = store.get("agent:b", "agent:a", "*")  # order of a/b never matters
    assert found is not None
    assert found.entity_a == "agent:a"
    reloaded = PairMemory(tmp_path)
    assert reloaded.get("agent:a", "agent:b", "*") == found


def test_pair_memory_for_entity_includes_both_sides(tmp_path: Path):
    store = PairMemory(tmp_path)
    store.record(_pair())
    store.record(_pair(entity_a="agent:a", entity_b="agent:c"))
    store.record(_pair(entity_a="agent:b", entity_b="agent:c"))
    rows = store.for_entity("agent:a")
    assert sorted({partner for row in rows for partner in (row.entity_a, row.entity_b)} - {"agent:a"}) == ["agent:b", "agent:c"]
    assert store.for_entity("agent:zzz") == ()


def test_pair_memory_record_replaces_the_same_key(tmp_path: Path):
    store = PairMemory(tmp_path)
    store.record(_pair(solo_a=0.5, solo_b=0.5, together=0.6))
    store.record(_pair(solo_a=0.5, solo_b=0.5, together=0.9))
    assert store.get("agent:a", "agent:b", "*").together == 0.9
    assert len(store.for_entity("agent:a")) == 1


def test_pair_memory_missing_file_is_empty_not_corrupt(tmp_path: Path):
    store = PairMemory(tmp_path)
    assert store.get("agent:a", "agent:b", "*") is None


def test_pair_memory_corrupt_file_raises(tmp_path: Path):
    path = tmp_path / PAIR_ESTIMATES_FILE_NAME
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PairMemoryStoreError, match="pair memory"):
        PairMemory(tmp_path)


def test_pair_memory_refuses_a_dishonest_document(tmp_path: Path):
    # A pair with itself is refused at construction, so a document still
    # carrying that row is dishonest rather than merely unusual.
    path = tmp_path / PAIR_ESTIMATES_FILE_NAME
    path.write_text(
        json.dumps({"version": 1, "pairs": {"agent:a|agent:a|*": {"entity_a": "agent:a", "entity_b": "agent:a", "task_category": "*"}}}),
        encoding="utf-8",
    )
    with pytest.raises(PairMemoryStoreError, match="invalid"):
        PairMemory(tmp_path)


# ------------------------------------------------------ correlated failure risk


def _failure_graph(failures: dict[str, tuple[str, ...]]) -> CapabilityGraph:
    graph = CapabilityGraph()
    for entity, modes in failures.items():
        graph.add_node(GraphNode(node_id=f"entity:{entity}", kind=NodeKind.ENTITY, label=entity))
        for mode in modes:
            graph.add_node(GraphNode(node_id=f"failure_mode:{mode}", kind=NodeKind.FAILURE_MODE, label=mode))
            graph.add_edge(GraphEdge(source=f"entity:{entity}", target=f"failure_mode:{mode}", kind=EdgeKind.OFTEN_MISSES, evidence_count=1, profile_version="p1"))
    return graph


def test_correlated_risk_is_unknown_when_either_side_has_no_failure_modes():
    # Both entities are in the graph; one of them has never failed, so
    # nothing was measured about it. (An entity the graph does not contain
    # at all is a different case — a refusal, see below.)
    graph = _failure_graph({"agent:a": ("timeout",), "agent:b": ()})
    risk = correlated_failure_risk(graph, "agent:a", "agent:b")
    assert risk == CorrelationRisk(risk=0.0, shared_failure_modes=(), known=False)
    # Unknown is not safe; it is unknown — the flag is how a caller tells them apart.


def test_correlated_risk_is_jaccard_over_shared_failure_modes():
    graph = _failure_graph({"agent:a": ("timeout", "hallucination"), "agent:b": ("timeout",)})
    risk = correlated_failure_risk(graph, "agent:a", "agent:b")
    assert risk.known is True
    assert risk.shared_failure_modes == ("timeout",)
    assert risk.risk == pytest.approx(0.5)


def test_correlated_risk_zero_overlap_is_known_zero():
    graph = _failure_graph({"agent:a": ("timeout",), "agent:b": ("hallucination",)})
    risk = correlated_failure_risk(graph, "agent:a", "agent:b")
    assert (risk.risk, risk.known, risk.shared_failure_modes) == (0.0, True, ())


def test_correlated_risk_refuses_an_unknown_entity():
    graph = _failure_graph({"agent:a": ("timeout",)})
    with pytest.raises(ValueError, match="unknown node"):
        correlated_failure_risk(graph, "agent:a", "agent:ghost")
