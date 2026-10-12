"""Tests for independence scoring, source independence and verification modes (plan §11).

The load-bearing rules pinned here: a shared model family or provider is a
*documented weak prior* and never a measurement, an unobserved axis is never
assumed shared, ``known=False`` means nothing was measured (never "safe"), a
measured SPECULATIVE pair contributes nothing, a pair measured under another
profile version is excluded, source independence is ``None`` on an empty
citation set, and MAJORITY_VOTE is refused by name as verification.
"""

from __future__ import annotations

import json

import pytest

from alpha.capability_mesh.complementarity import (
    PAIR_MIN_SAMPLES,
    ComplementarityEvidence,
    PairEstimate,
    PairMemory,
)
from alpha.capability_mesh.graph import CapabilityGraph, EdgeKind, GraphEdge, GraphNode, NodeKind, node_id_for
from alpha.capability_mesh.independence import (
    INDEPENDENCE_FILE_NAME,
    MODEL_FAMILY_PRIOR,
    NOTHING_MEASURED_BASIS,
    PROVIDER_PRIOR,
    RISK_PENALTY,
    IndependenceAxis,
    IndependenceNote,
    IndependenceScore,
    IndependenceScoreboard,
    IndependenceStoreError,
    VerificationMethod,
    independence_score,
    source_independence,
    verify_with_independent_method,
)

PV = "p1"
OTHER_PV = "p2"


def _entity(graph: CapabilityGraph, entity_id: str, *, family: str | None = None, provider: str | None = None) -> None:
    attributes: list[tuple[str, str]] = []
    if family is not None:
        attributes.append(("model_family", family))
    if provider is not None:
        attributes.append(("model_provider", provider))
    graph.add_node(
        GraphNode(
            node_id=node_id_for(NodeKind.ENTITY, entity_id),
            kind=NodeKind.ENTITY,
            label=entity_id,
            attributes=tuple(attributes),
        )
    )


def _failure_mode(graph: CapabilityGraph, entity_id: str, mode: str, *, occurrences: int, total: int = 4) -> None:
    target = node_id_for(NodeKind.FAILURE_MODE, mode)
    graph.add_node(GraphNode(node_id=target, kind=NodeKind.FAILURE_MODE, label=mode))
    graph.add_edge(
        GraphEdge(
            source=node_id_for(NodeKind.ENTITY, entity_id),
            target=target,
            kind=EdgeKind.OFTEN_MISSES,
            weight=occurrences / total,
            evidence_count=occurrences,
            profile_version=PV,
        )
    )


def _pair(entity_a: str, entity_b: str, *, together: float | None, samples: int = PAIR_MIN_SAMPLES, evidence: ComplementarityEvidence = ComplementarityEvidence.OBSERVED, profile_version: str = PV) -> PairEstimate:
    return PairEstimate(
        entity_a=entity_a,
        entity_b=entity_b,
        task_category="*",
        solo_a=0.8,
        solo_b=0.8,
        together=together,
        sample_count=samples,
        evidence=evidence,
        profile_version=profile_version,
    )


# ------------------------------------------------------------------- refusals


def test_score_requires_a_profile_version(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a")
    with pytest.raises(ValueError, match="profile_version"):
        independence_score(graph, PairMemory(tmp_path), "agent:a", "agent:b", profile_version="")


def test_score_refuses_an_entity_against_itself(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a")
    with pytest.raises(ValueError, match="distinct entities"):
        independence_score(graph, PairMemory(tmp_path), "agent:a", "agent:a", profile_version=PV)


def test_score_refuses_an_empty_task_category(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a")
    _entity(graph, "agent:b")
    with pytest.raises(ValueError, match="task_category must be non-empty"):
        independence_score(graph, PairMemory(tmp_path), "agent:a", "agent:b", task_category="", profile_version=PV)


def test_score_refuses_an_unknown_graph_entity():
    graph = CapabilityGraph()
    _entity(graph, "agent:a")
    with pytest.raises(ValueError, match="unknown node 'entity:agent:ghost'"):
        independence_score(graph, PairMemory.__new__(PairMemory), "agent:a", "agent:ghost", profile_version=PV)


def test_score_refuses_a_non_pair_memory():
    graph = CapabilityGraph()
    _entity(graph, "agent:a")
    _entity(graph, "agent:b")
    with pytest.raises(ValueError, match="pair_memory must be a PairMemory"):
        independence_score(graph, {}, "agent:a", "agent:b", profile_version=PV)


# --------------------------------------------------------------- nothing measured


def test_nothing_measured_is_unknown_never_independent(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a")
    _entity(graph, "agent:b")
    memory = PairMemory(tmp_path)

    row = independence_score(graph, memory, "agent:a", "agent:b", profile_version=PV)

    assert row.score == 0.0
    assert row.known is False
    assert row.shared_axes == ()
    assert row.basis == NOTHING_MEASURED_BASIS


# ------------------------------------------------------------------ weak priors


def test_shared_model_family_is_a_weak_prior_not_a_measurement(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a", family="llama", provider="openrouter")
    _entity(graph, "agent:b", family="llama", provider="groq")
    memory = PairMemory(tmp_path)

    row = independence_score(graph, memory, "agent:a", "agent:b", profile_version=PV)

    assert row.shared_axes == (IndependenceAxis.MODEL_FAMILY,)
    assert row.score == pytest.approx(1.0 - MODEL_FAMILY_PRIOR)
    # A prior is not an observation: nothing here was measured.
    assert row.known is False
    assert "MODEL_FAMILY_PRIOR" in row.basis
    assert "a prior, not a measurement" in row.basis


def test_shared_provider_is_a_weak_prior_and_compounds_with_the_family(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a", family="llama", provider="openrouter")
    _entity(graph, "agent:b", family="llama", provider="openrouter")
    memory = PairMemory(tmp_path)

    row = independence_score(graph, memory, "agent:a", "agent:b", profile_version=PV)

    assert row.shared_axes == (IndependenceAxis.MODEL_FAMILY, IndependenceAxis.PROVIDER)
    expected = max(0.0, 1.0 - MODEL_FAMILY_PRIOR - PROVIDER_PRIOR)
    assert row.score == pytest.approx(expected)
    assert row.known is False


def test_known_axes_are_never_invented_from_a_prior(tmp_path):
    """PROMPT/CONTEXT/SOURCE/TOOL_PATH/METHOD are only ever marked by an observation."""
    graph = CapabilityGraph()
    _entity(graph, "agent:a", family="llama", provider="openrouter")
    _entity(graph, "agent:b", family="llama", provider="openrouter")
    memory = PairMemory(tmp_path)

    row = independence_score(graph, memory, "agent:a", "agent:b", profile_version=PV)

    invented = {IndependenceAxis.PROMPT, IndependenceAxis.CONTEXT, IndependenceAxis.SOURCE, IndependenceAxis.TOOL_PATH, IndependenceAxis.METHOD}
    assert not (set(row.shared_axes) & invented)


def test_a_different_family_and_provider_share_no_prior_axis(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a", family="llama", provider="openrouter")
    _entity(graph, "agent:b", family="phi", provider="local")
    memory = PairMemory(tmp_path)

    row = independence_score(graph, memory, "agent:a", "agent:b", profile_version=PV)

    assert row.shared_axes == ()
    # Nothing was observed either, so the row stays unknown rather than 1.0.
    assert row.score == 0.0
    assert row.known is False


def test_an_unknown_family_on_one_side_marks_nothing(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a", family="llama")
    _entity(graph, "agent:b", provider="openrouter")
    memory = PairMemory(tmp_path)

    row = independence_score(graph, memory, "agent:a", "agent:b", profile_version=PV)

    assert row.shared_axes == ()
    assert row.known is False


# -------------------------------------------------------------- measured penalty


def test_measured_shared_failure_modes_penalise_and_mark_the_row_known(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a")
    _entity(graph, "agent:b")
    _failure_mode(graph, "agent:a", "timeout", occurrences=3)
    _failure_mode(graph, "agent:b", "timeout", occurrences=3)
    memory = PairMemory(tmp_path)

    row = independence_score(graph, memory, "agent:a", "agent:b", profile_version=PV)

    # Both entities record exactly one failure mode and it is the same one:
    # Jaccard overlap 1.0, penalised at RISK_PENALTY.
    assert row.known is True
    assert row.score == pytest.approx(max(0.0, 1.0 - 1.0 * RISK_PENALTY))
    assert "shared recorded failure modes ['timeout']" in row.basis
    assert f"RISK_PENALTY {RISK_PENALTY}" in row.basis


def test_a_measured_zero_risk_is_still_a_measurement(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a")
    _entity(graph, "agent:b")
    _failure_mode(graph, "agent:a", "timeout", occurrences=3)
    _failure_mode(graph, "agent:b", "hallucination", occurrences=2)
    memory = PairMemory(tmp_path)

    row = independence_score(graph, memory, "agent:a", "agent:b", profile_version=PV)

    assert row.known is True
    assert row.score == pytest.approx(1.0)
    assert "share none" in row.basis


def test_prior_and_measured_penalties_combine_and_both_are_named(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a", family="llama")
    _entity(graph, "agent:b", family="llama")
    _failure_mode(graph, "agent:a", "timeout", occurrences=2)
    _failure_mode(graph, "agent:b", "timeout", occurrences=2)
    memory = PairMemory(tmp_path)

    row = independence_score(graph, memory, "agent:a", "agent:b", profile_version=PV)

    expected = max(0.0, 1.0 - MODEL_FAMILY_PRIOR - 1.0 * RISK_PENALTY)
    assert row.score == pytest.approx(expected)
    assert row.known is True
    assert "MODEL_FAMILY_PRIOR" in row.basis
    assert "RISK_PENALTY" in row.basis


# ---------------------------------------------------------------- measured pair


def test_a_measured_pair_below_its_best_solo_contributes_its_shortfall(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a")
    _entity(graph, "agent:b")
    memory = PairMemory(tmp_path)
    # together 0.5 against solo 0.8 -> a 0.3 shortfall, measured.
    memory.record(_pair("agent:a", "agent:b", together=0.5))

    row = independence_score(graph, memory, "agent:a", "agent:b", profile_version=PV)

    assert row.known is True
    assert row.score == pytest.approx(1.0 - 0.3)
    assert "falls 0.300 short" in row.basis


def test_a_speculative_pair_contributes_nothing_and_says_so(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a")
    _entity(graph, "agent:b")
    memory = PairMemory(tmp_path)
    memory.record(_pair("agent:a", "agent:b", together=0.1, samples=0, evidence=ComplementarityEvidence.SPECULATIVE))

    row = independence_score(graph, memory, "agent:a", "agent:b", profile_version=PV)

    assert row.score == 0.0
    assert row.known is False
    assert "SPECULATIVE" in row.basis
    assert "contributes 0" in row.basis


def test_a_pair_measured_under_another_profile_version_is_excluded(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a")
    _entity(graph, "agent:b")
    memory = PairMemory(tmp_path)
    memory.record(_pair("agent:a", "agent:b", together=0.2, profile_version=OTHER_PV))

    row = independence_score(graph, memory, "agent:a", "agent:b", profile_version=PV)

    assert row.score == 0.0
    assert row.known is False
    assert OTHER_PV in row.basis
    assert "comparability" in row.basis


def test_a_pair_without_a_synergy_number_is_disclosed(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a")
    _entity(graph, "agent:b")
    memory = PairMemory(tmp_path)
    memory.record(_pair("agent:a", "agent:b", together=None))

    row = independence_score(graph, memory, "agent:a", "agent:b", profile_version=PV)

    assert row.known is True
    assert row.score == pytest.approx(1.0)
    assert "no synergy number" in row.basis


def test_a_pair_with_a_positive_complementarity_adds_no_penalty(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a")
    _entity(graph, "agent:b")
    memory = PairMemory(tmp_path)
    memory.record(_pair("agent:a", "agent:b", together=0.9))

    row = independence_score(graph, memory, "agent:a", "agent:b", profile_version=PV)

    assert row.known is True
    assert row.score == pytest.approx(1.0)
    assert "complementarity +0.100" in row.basis


def test_the_pair_lookup_is_unordered(tmp_path):
    graph = CapabilityGraph()
    _entity(graph, "agent:a")
    _entity(graph, "agent:b")
    memory = PairMemory(tmp_path)
    memory.record(_pair("agent:a", "agent:b", together=0.5))

    row = independence_score(graph, memory, "agent:b", "agent:a", profile_version=PV)

    assert row.score == pytest.approx(1.0 - 0.3)
    assert (row.entity_a, row.entity_b) == ("agent:b", "agent:a")


# ------------------------------------------------------------ source independence


def test_source_independence_is_none_when_either_side_cited_nothing():
    assert source_independence(sources_a=(), sources_b=("https://a",)) is None
    assert source_independence(sources_a=("https://a",), sources_b=()) is None
    assert source_independence(sources_a=(), sources_b=()) is None
    assert source_independence(sources_a=("", "   "), sources_b=("https://a",)) is None


def test_source_independence_scores_the_overlap_of_the_cited_sets():
    assert source_independence(sources_a=("https://a", "https://b"), sources_b=("https://b", "https://c")) == pytest.approx(1 - 1 / 3)
    assert source_independence(sources_a=("https://a",), sources_b=("https://a",)) == pytest.approx(0.0)
    assert source_independence(sources_a=("https://a",), sources_b=("https://b",)) == pytest.approx(1.0)
    # Duplicates and surrounding whitespace are not extra citations.
    assert source_independence(sources_a=(" https://a ", "https://a"), sources_b=("https://a",)) == pytest.approx(0.0)


# ------------------------------------------------------------- verification modes


def test_majority_vote_is_not_independent_verification():
    note = verify_with_independent_method(method=VerificationMethod.MAJORITY_VOTE)

    assert note.method is VerificationMethod.MAJORITY_VOTE
    assert "NOT independent verification" in note.note
    assert "decides which candidates deserve attention" in note.note
    assert "factual acceptance requires evidence" in note.note


@pytest.mark.parametrize(
    "method",
    [
        VerificationMethod.LLM_REVIEW_PLUS_TEST,
        VerificationMethod.MODEL_PLUS_PRIMARY_SOURCE,
        VerificationMethod.CODE_PLUS_COMPILER,
        VerificationMethod.EXPLANATION_PLUS_CALCULATION,
        VerificationMethod.RESEARCH_PLUS_INDEPENDENT_SEARCH,
        VerificationMethod.PATCH_PLUS_CLEAN_WORKTREE,
    ],
)
def test_every_real_verification_method_states_what_it_proves(method):
    note = verify_with_independent_method(method=method)

    assert note.note.startswith("independent verification:")
    assert note.note


def test_an_unknown_verification_method_is_refused_by_name():
    with pytest.raises(ValueError, match="unknown verification method"):
        verify_with_independent_method(method="vibes")


def test_the_only_non_independent_method_is_the_vote():
    from alpha.capability_mesh.independence import INDEPENDENT_METHODS

    assert VerificationMethod.MAJORITY_VOTE not in INDEPENDENT_METHODS
    assert len(INDEPENDENT_METHODS) == len(VerificationMethod) - 1


def test_the_method_may_be_named_positionally_or_by_keyword():
    assert verify_with_independent_method(VerificationMethod.CODE_PLUS_COMPILER) == verify_with_independent_method(method=VerificationMethod.CODE_PLUS_COMPILER)


# -------------------------------------------------------------- the value types


def test_note_refuses_an_empty_note():
    with pytest.raises(ValueError, match="note must be non-empty"):
        IndependenceNote(method=VerificationMethod.CODE_PLUS_COMPILER, note="")


def test_score_refuses_an_empty_basis_and_out_of_range_scores():
    with pytest.raises(ValueError, match="basis must be non-empty"):
        IndependenceScore(entity_a="a", entity_b="b", task_category="*", score=1.0, known=True, shared_axes=(), basis="")
    with pytest.raises(ValueError, match="score must be a finite number"):
        IndependenceScore(entity_a="a", entity_b="b", task_category="*", score=1.5, known=True, shared_axes=(), basis="x")
    with pytest.raises(ValueError, match="known must be a bool"):
        IndependenceScore(entity_a="a", entity_b="b", task_category="*", score=1.0, known="yes", shared_axes=(), basis="x")


def test_score_refuses_an_entity_against_itself_at_construction():
    with pytest.raises(ValueError, match="distinct entities"):
        IndependenceScore(entity_a="a", entity_b="a", task_category="*", score=1.0, known=True, shared_axes=(), basis="x")


def test_score_axes_are_sorted_deduplicated_and_validated():
    row = IndependenceScore(
        entity_a="a",
        entity_b="b",
        task_category="*",
        score=0.4,
        known=False,
        shared_axes=(IndependenceAxis.PROVIDER, IndependenceAxis.MODEL_FAMILY, IndependenceAxis.PROVIDER),
        basis="x",
    )
    assert row.shared_axes == (IndependenceAxis.MODEL_FAMILY, IndependenceAxis.PROVIDER)

    with pytest.raises(ValueError, match="unknown independence axis"):
        IndependenceScore(entity_a="a", entity_b="b", task_category="*", score=0.4, known=False, shared_axes=("lineage",), basis="x")


def test_score_round_trips_through_dict():
    row = IndependenceScore(entity_a="a", entity_b="b", task_category="code", score=0.42, known=True, shared_axes=(IndependenceAxis.MODEL_FAMILY,), basis="measured: x")
    assert IndependenceScore.from_dict(row.to_dict()) == row


# ------------------------------------------------------------------- durability


def test_board_round_trips_a_row(tmp_path):
    board = IndependenceScoreboard(tmp_path)
    row = IndependenceScore(entity_a="agent:a", entity_b="agent:b", task_category="*", score=0.4, known=False, shared_axes=(IndependenceAxis.MODEL_FAMILY,), basis="weak prior")

    board.record(row)

    assert board.path is not None and board.path.name == INDEPENDENCE_FILE_NAME
    reloaded = IndependenceScoreboard(tmp_path)
    assert reloaded.scores() == (row,)
    assert reloaded.get("agent:b", "agent:a") == row
    assert reloaded.get("agent:b", "agent:a", "code") is None
    assert reloaded.for_entity("agent:a") == (row,)


def test_an_in_memory_board_never_touches_the_filesystem_and_refuses_to_save(tmp_path):
    board = IndependenceScoreboard()
    board.record(IndependenceScore(entity_a="a", entity_b="b", task_category="*", score=1.0, known=True, shared_axes=(), basis="measured"))

    assert board.durable is False
    assert board.path is None
    assert len(board.scores()) == 1
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError, match="no directory"):
        board.save()


def test_a_missing_file_is_an_empty_board(tmp_path):
    assert IndependenceScoreboard(tmp_path).scores() == ()


def test_a_corrupt_document_is_refused_by_name(tmp_path):
    (tmp_path / INDEPENDENCE_FILE_NAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(IndependenceStoreError, match="not valid JSON"):
        IndependenceScoreboard(tmp_path)

    (tmp_path / INDEPENDENCE_FILE_NAME).write_text(json.dumps({"scores": {"a": {"entity_a": "x"}}}), encoding="utf-8")
    with pytest.raises(IndependenceStoreError, match="entry 'a' is invalid"):
        IndependenceScoreboard(tmp_path)


def test_a_dishonest_document_shape_is_refused(tmp_path):
    (tmp_path / INDEPENDENCE_FILE_NAME).write_text(json.dumps({"scores": []}), encoding="utf-8")
    with pytest.raises(IndependenceStoreError, match="must be a JSON object with a 'scores' mapping"):
        IndependenceScoreboard(tmp_path)


def test_record_refuses_a_non_score():
    board = IndependenceScoreboard()
    with pytest.raises(ValueError, match="record\\(\\) expects an IndependenceScore"):
        board.record({"entity_a": "a"})
