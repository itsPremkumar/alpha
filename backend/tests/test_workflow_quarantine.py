"""Dead-letter quarantine: the operator work queue for nodes that ran out of road.

Properties pinned here:

* a record is written from the engine's real exhaustion decisions (retries
  exhausted, stagnation, retry refused), carrying the real reason, class and
  signature — never a generic label;
* one OPEN record per ``(run, node)``: a repeated failure updates the row the
  operator has not acted on yet, while a failure after a replay is a new row,
  because the operator already made one decision;
* a refused replay leaves the record quarantined and records the refusal —
  closing the row would lose the work;
* the store fails closed on corruption, and a store write failure is disclosed
  as an event rather than swallowed or turned into a second node failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alpha.workflow.models import (
    NodeType,
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRunStatus,
)
from alpha.workflow.quarantine import (
    QuarantineStatus,
    QuarantineStore,
    QuarantineStoreError,
    QuarantineTrigger,
)
from alpha.workflow.runtime import DynamicWorkflowEngine


def _store(tmp_path: Path) -> QuarantineStore:
    return QuarantineStore(tmp_path / "workflow_store")


def _admit(store: QuarantineStore, **overrides) -> dict:
    fields = {
        "run_id": "run_1",
        "workflow_id": "wf_1",
        "node_id": "n1",
        "reason": "provider returned 503 after 3 attempts",
        "trigger": QuarantineTrigger.RECOVERY_EXHAUSTED,
        "failure_class": "provider_error",
        "signature": "provider returned 5xx",
        "attempts": 3,
    }
    fields.update(overrides)
    return store.admit(**fields).to_dict()


# --------------------------------------------------------------------- the store


def test_admit_persists_and_reloads(tmp_path: Path):
    store = _store(tmp_path)
    record = store.admit(run_id="r", workflow_id="w", node_id="n", reason="boom", trigger=QuarantineTrigger.RECOVERY_EXHAUSTED)
    reloaded = QuarantineStore(tmp_path / "workflow_store")
    assert reloaded.get(record.record_id) is not None
    assert reloaded.get(record.record_id).reason == "boom"


def test_second_failure_updates_the_open_record(tmp_path: Path):
    store = _store(tmp_path)
    first = _admit(store)
    second = _admit(store, reason="provider returned 503 after 5 attempts", attempts=5)
    assert second["record_id"] == first["record_id"]
    assert second["attempts"] == 5
    assert store.open_for_run("run_1")[0].reason == "provider returned 503 after 5 attempts"


def test_failure_after_replay_is_a_new_record(tmp_path: Path):
    store = _store(tmp_path)
    first = _admit(store)
    store.mark_replayed(first["record_id"], graph_version=7)
    second = _admit(store, reason="failed again after replay")
    assert second["record_id"] != first["record_id"]
    assert second["status"] == QuarantineStatus.QUARANTINED.value
    assert store.list(status=QuarantineStatus.REPLAYED)[0].replay_graph_version == 7


def test_list_is_owner_scoped_oldest_first_and_bounded(tmp_path: Path):
    store = _store(tmp_path)
    _admit(store, run_id="r1", owner_id="owner-1")
    _admit(store, run_id="r2", owner_id="owner-2")
    _admit(store, run_id="r3", owner_id="owner-1")
    mine = store.list(owner_id="owner-1")
    assert [record.run_id for record in mine] == ["r1", "r3"]
    assert len(store.list(limit=1)) == 1
    assert len(store.list()) == 3  # unscoped administrative read


def test_replay_refusal_keeps_the_record_open(tmp_path: Path):
    store = _store(tmp_path)
    record = _admit(store)
    store.mark_replay_refused(record["record_id"], reason="source run is not resident")
    held = store.get(record["record_id"])
    assert held.status is QuarantineStatus.QUARANTINED
    assert held.replay_refusal == "source run is not resident"


def test_discard_requires_a_reason(tmp_path: Path):
    store = _store(tmp_path)
    record = _admit(store)
    with pytest.raises(ValueError, match="requires a reason"):
        store.discard(record["record_id"], note="   ")
    store.discard(record["record_id"], note="superseded by the manual fix in #4412")
    discarded = store.get(record["record_id"])
    assert discarded.status is QuarantineStatus.DISCARDED
    assert discarded.discarded_at is not None


def test_corrupt_store_fails_closed(tmp_path: Path):
    root = tmp_path / "workflow_store"
    root.mkdir(parents=True)
    (root / "quarantine.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(QuarantineStoreError, match="not valid JSON"):
        QuarantineStore(root)


def test_forget_run_drops_its_records(tmp_path: Path):
    store = _store(tmp_path)
    _admit(store, run_id="r1")
    _admit(store, run_id="r2")
    assert store.forget_run("r1") == 1
    assert [record.run_id for record in store.list()] == ["r2"]


# ---------------------------------------------------------------- engine wiring


def _failing_workflow(*, max_attempts: int = 4) -> WorkflowDefinition:
    graph = WorkflowGraph(
        nodes={
            "n1": WorkflowNode(id="n1", type=NodeType.TOOL, executor="alpha.local.model", timeout_seconds=None),
        },
        edges=[],
    )
    graph.nodes["n1"].retry_policy.max_attempts = max_attempts
    graph.nodes["n1"].retry_policy.retry_on_errors = ["transient"]
    graph.nodes["n1"].retry_policy.initial_delay_seconds = 0.0
    return WorkflowDefinition(id="wf_q", name="quarantine fixture", graph=graph, budget=100000)


_DISTINCT_FAULTS = (
    "transient connection reset by peer",
    "transient upstream replied 503",
    "transient socket hang up",
    "transient gateway did not respond",
    "transient endpoint closed early",
)


def _distinct_failure_runner(node, run):  # noqa: ANN001
    """A retryable failure whose SIGNATURE differs per attempt.

    Necessary because the stagnation detector normalizes digits and paths
    away: re-numbering one message ("fault 1", "fault 2") still collapses to
    the same signature and would stagnate at the detector's limit, never
    reaching the exhausted ceiling this test pins. Different *words* are
    different signatures.
    """
    attempt = int(run.iteration_counts.get("n1", 0)) + 1
    run.iteration_counts["n1"] = attempt
    text = _DISTINCT_FAULTS[min(attempt, len(_DISTINCT_FAULTS)) - 1]
    return {"status": "failed", "output": text, "evidence": "", "tokens_used": 0}


def _always_failing_runner(node, run):  # noqa: ANN001
    return {"status": "failed", "output": "transient provider outage: connection reset", "evidence": "", "tokens_used": 0}


def _engine(tmp_path: Path) -> DynamicWorkflowEngine:
    engine = DynamicWorkflowEngine()
    engine.register_definition(_failing_workflow())
    engine.quarantine = QuarantineStore(tmp_path / "workflow_store")
    return engine


def test_exhausted_retries_quarantine_the_node(tmp_path: Path):
    engine = _engine(tmp_path)
    run = engine.start_run("wf_q")
    run = engine.execute_step(run.run_id, node_runner=_distinct_failure_runner)
    assert run.status is WorkflowRunStatus.FAILED
    records = engine.quarantine.list()
    assert len(records) == 1
    record = records[0]
    assert record.node_id == "n1"
    assert record.trigger is QuarantineTrigger.RECOVERY_EXHAUSTED
    assert record.attempts == 4
    assert record.run_id == run.run_id
    assert "transient" in record.reason
    # The engine also journals the dead-letter, so the queue and the event log
    # can never disagree about whether a node was quarantined.
    event_types = [event.event_type for event in engine.events.get_events(run.run_id)]
    assert "node_quarantined" in event_types


def test_stagnation_quarantines_with_its_own_trigger(tmp_path: Path):
    engine = _engine(tmp_path)
    definition = _failing_workflow(max_attempts=5)
    engine.register_definition(definition, allow_replace=True)
    run = engine.start_run("wf_q")
    engine.execute_step(run.run_id, node_runner=_always_failing_runner)
    records = engine.quarantine.list()
    assert len(records) == 1
    assert records[0].trigger is QuarantineTrigger.STAGNATION
    # The stagnation limit is min(DEFAULT_STAGNATION_LIMIT, attempts) = 3, so
    # the record carries the real attempt at which the streak was declared.
    assert records[0].attempts == 3


def test_a_store_write_failure_is_disclosed_not_swallowed(tmp_path: Path):
    engine = _engine(tmp_path)

    class _BrokenStore(QuarantineStore):
        def admit(self, **kwargs):  # noqa: ANN003
            raise QuarantineStoreError("disk full")

    engine.quarantine = _BrokenStore(tmp_path / "workflow_store")
    run = engine.start_run("wf_q")
    run = engine.execute_step(run.run_id, node_runner=_always_failing_runner)
    # The node's real failure is unchanged by the store outage, and the outage
    # is journalled rather than hidden.
    assert run.status is WorkflowRunStatus.FAILED
    event_types = [event.event_type for event in engine.events.get_events(run.run_id)]
    assert "quarantine_write_failed" in event_types
    failed = [event for event in engine.events.get_events(run.run_id) if event.event_type == "quarantine_write_failed"]
    assert "disk full" in str(failed[0].payload)
