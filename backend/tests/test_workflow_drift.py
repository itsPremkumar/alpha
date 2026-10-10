"""Goal-drift detection: direction measured, never acted on.

Properties pinned here:

* terms come from the declared goal by a published rule (stopwords, bounds,
  stable order) — an operator can recompute every score by hand;
* unmeasurable output scores ``None`` and is excluded: a node that logs a
  hash is not a drifted node;
* fewer than the minimum measurable samples yields ``insufficient_evidence``
  with the real count, never a verdict over one data point;
* the engine JOURNALS ``goal_drift_detected`` and projects into ``metrics``
  and changes nothing else — a drifting run is still a valid run, and what
  happens next belongs to the operator.
"""

from __future__ import annotations

from pathlib import Path

from alpha.workflow.drift import (
    DEFAULT_DRIFT_THRESHOLD,
    MAX_DRIFT_SAMPLES,
    MAX_TERMS,
    DriftSample,
    evaluate_trajectory,
    extract_terms,
    measure_node,
    measure_text_overlap,
    resolve_goal,
    samples_from_dicts,
)
from alpha.workflow.models import NodeType, WorkflowDefinition, WorkflowEdge, WorkflowGraph, WorkflowNode
from alpha.workflow.runtime import DynamicWorkflowEngine

# ----------------------------------------------------------------------- policy


def test_terms_drop_stopwords_and_short_tokens_and_stay_bounded():
    goal = "please implement the payment retry workflow and verify the payment refund flow"
    terms = extract_terms(goal)
    assert "the" not in terms and "and" not in terms and "please" not in terms
    assert "payment" in terms and "retry" in terms
    assert len(terms) <= MAX_TERMS
    assert extract_terms(goal) == terms  # stable order


def test_terms_of_a_stopword_only_goal_is_empty():
    assert extract_terms("please make it and then be able to") == ()


def test_overlap_reports_the_hit_and_missed_split():
    score, hit, missed = measure_text_overlap("the payment retry succeeded", ("payment", "retry", "refund"))
    assert score == 2 / 3
    assert hit == ("payment", "retry")
    assert missed == ("refund",)


def test_measure_node_scores_none_for_unmeasurable_text():
    # A truly empty output carries no measurable text — distinct from a digest
    # string, which IS text (and simply scores zero overlap).
    assert measure_node("n1", "", ("payment",)).score is None
    assert measure_node("n2", "   ", ("payment",)).score is None
    digest = measure_node("n3", {"digest": "9f86d081884c7d65"}, ("payment",))
    assert digest.score == 0.0
    assert digest.text_chars > 0


def test_resolve_goal_precedence_and_absence():
    assert resolve_goal(graph_metadata={"goal": "g1"}, run_state={"objective": "g2"}, definition_description="g3") == ("g1", "graph.metadata.goal")
    assert resolve_goal(graph_metadata={}, run_state={"objective": "g2"}, definition_description="g3") == ("g2", "run.state.objective")
    assert resolve_goal(graph_metadata={}, run_state={"goal": "g2"}, definition_description="g3") == ("g2", "run.state.goal")
    assert resolve_goal(graph_metadata={}, run_state={}, definition_description="g3") == ("g3", "definition.description")
    assert resolve_goal(graph_metadata={}, run_state={}, definition_description="") == ("", "")


def _samples(*scores: float | None) -> list[DriftSample]:
    return [DriftSample(node_id=f"n{index}", score=score) for index, score in enumerate(scores)]


def test_too_few_samples_is_insufficient_evidence():
    report = evaluate_trajectory("payment retry", "graph.metadata.goal", _samples(0.0, 0.2), threshold=0.1)
    assert report.verdict == "insufficient_evidence"
    assert "2 measurable" in report.reason
    assert report.trailing_score is None


def test_no_goal_is_reported_as_such():
    report = evaluate_trajectory("", "", _samples(0.0, 0.0, 0.0))
    assert report.verdict == "no_goal"
    assert report.has_goal is False


def test_drifting_verdict_names_the_missed_terms():
    samples = [DriftSample(node_id=f"n{index}", score=score, terms_missed=("payment", "refund")) for index, score in enumerate([0.9, 0.0, 0.0, 0.0])]
    report = evaluate_trajectory("payment retry refund", "graph.metadata.goal", samples, threshold=0.1)
    assert report.verdict == "drifting"
    assert report.trailing_score == 0.0
    assert "payment" in report.reason


def test_on_goal_verdict_when_trailing_overlap_meets_threshold():
    samples = [DriftSample(node_id=f"n{i}", score=score) for i, score in enumerate([0.0, 0.5, 0.4])]
    report = evaluate_trajectory("payment retry", "graph.metadata.goal", samples, threshold=0.1)
    assert report.verdict == "on_goal"
    # Trailing window is the last 3 measurable samples: (0.0 + 0.5 + 0.4) / 3.
    assert report.trailing_score == 0.3


def test_unmeasurable_nodes_are_excluded_from_the_mean():
    samples = [DriftSample(node_id="a", score=None), DriftSample(node_id="b", score=None)]
    report = evaluate_trajectory("payment retry", "graph.metadata.goal", samples, threshold=0.1)
    assert report.verdict == "insufficient_evidence"
    assert report.measurable == 0


def test_samples_from_dicts_skips_malformed_entries():
    rebuilt = samples_from_dicts(
        [
            {"node_id": "a", "score": 0.5, "terms_hit": ["x"], "terms_missed": [], "text_chars": 3},
            "not-a-mapping",
            {"node_id": "b", "score": None},
            {"node_id": "c", "score": "bad"},
        ]
    )
    # A non-mapping entry is dropped; a mapping whose score will not parse is
    # kept as UNMEASURABLE (score None) rather than dropped, so the node's
    # identity survives in the trajectory and only its number is withheld.
    assert [sample.node_id for sample in rebuilt] == ["a", "b", "c"]
    assert rebuilt[2].score is None


# ---------------------------------------------------------------------- engine


def _chain_workflow(*, goal: str | None) -> WorkflowDefinition:
    metadata = {"goal": goal} if goal else {}
    nodes = {f"n{index}": WorkflowNode(id=f"n{index}", type=NodeType.TOOL, executor="alpha.local.model") for index in range(1, 5)}
    edges = [WorkflowEdge(source=f"n{index}", target=f"n{index + 1}") for index in range(1, 4)]
    return WorkflowDefinition(id="wf_drift", name="drift fixture", graph=WorkflowGraph(nodes=nodes, edges=edges, metadata=metadata), budget=100000)


def _runner_for(outputs: dict[str, str]):
    def _runner(node, run):  # noqa: ANN001
        return {"status": "completed", "output": outputs.get(node.id, ""), "evidence": "ok", "tokens_used": 0}

    return _runner


def _run_to_end(engine: DynamicWorkflowEngine, run_id: str, outputs: dict[str, str]) -> None:
    """Drive the chain wave by wave: one ``execute_step`` is ONE wave."""
    runner = _runner_for(outputs)
    while True:
        run = engine.execute_step(run_id, node_runner=runner)
        if run.status.value in {"completed", "failed", "cancelled", "budget_exhausted", "aborted"}:
            return


def test_on_goal_run_records_no_drift_event(tmp_path: Path):
    del tmp_path  # the engine keeps its own stores; this test asserts none fire
    engine = DynamicWorkflowEngine()
    engine.register_definition(_chain_workflow(goal="payment retry refund flow"))
    run = engine.start_run("wf_drift")
    _run_to_end(engine, run.run_id, {f"n{index}": "payment retry refund flow analysis" for index in range(1, 5)})
    projected = run.metrics.get("goal_drift")
    assert projected is not None
    assert projected["verdict"] == "on_goal"
    assert projected["goal_source"] == "graph.metadata.goal"
    types = [event.event_type for event in engine.events.get_events(run.run_id)]
    assert "goal_drift_detected" not in types


def test_drifting_run_is_journalled_and_changes_nothing(tmp_path: Path):
    del tmp_path
    engine = DynamicWorkflowEngine()
    engine.register_definition(_chain_workflow(goal="payment retry refund flow"))
    run = engine.start_run("wf_drift")
    # First node on goal, the rest quietly about something else entirely.
    outputs = {"n1": "payment retry refund flow", "n2": "linting configuration notes", "n3": "css selector cleanup", "n4": "rename variables"}
    _run_to_end(engine, run.run_id, outputs)
    events = [event for event in engine.events.get_events(run.run_id) if event.event_type == "goal_drift_detected"]
    assert events, "a drifting trajectory must be journalled"
    payload = events[-1].payload
    assert payload["node_score"] == 0.0
    assert payload["threshold"] == DEFAULT_DRIFT_THRESHOLD
    assert "payment" in payload["reason"]
    assert run.metrics["goal_drift"]["verdict"] == "drifting"
    # The verdict is a proposal: the run itself completed normally, and no
    # node or run state was rewritten by the measurement.
    assert run.status.value == "completed"
    assert len(run.metrics["goal_drift_samples"]) <= MAX_DRIFT_SAMPLES


def test_no_declared_goal_measures_nothing():
    engine = DynamicWorkflowEngine()
    engine.register_definition(_chain_workflow(goal=None))
    run = engine.start_run("wf_drift")
    _run_to_end(engine, run.run_id, {f"n{index}": "anything at all" for index in range(1, 5)})
    assert "goal_drift" not in run.metrics
    assert "goal_drift_samples" not in run.metrics
    types = [event.event_type for event in engine.events.get_events(run.run_id)]
    assert "goal_drift_detected" not in types
