"""W-N1 durability proofs: append-only log, honest refusals, kill-and-resume.

These tests are the L2/L6/L7 loop requirements made executable:

* every emitted event is durable (append -> read round-trip, monotonic seq);
* a node attempt replayed with the same idempotency key is NOT duplicated;
* a corrupt log TAIL is disclosed with its line number and REFUSES further
  appends (numbering past damage would silently reorder history);
* an unwritable store raises instead of pretending to journal;
* a run projected by one engine is hydrated into a FRESH engine — the
  kill-and-resume proof (no shared in-memory state between A and B);
* a stale projection (durable events beyond its seq) is NOT installed;
* a corrupt projection is reported, never repaired silently;
* an empty store reports ``empty`` — not a fabricated "ok".

No sleeps, no mocked clocks: timestamps are real; ordering is proven by seq.
"""

from __future__ import annotations

import json

import pytest

import alpha.workflow.runtime as runtime_module
from alpha.workflow.event_log import DurableEventLog, DurableEventLogError, node_attempt_idempotency_key
from alpha.workflow.events import WorkflowEvent, WorkflowEventDispatcher
from alpha.workflow.models import WorkflowDefinition, WorkflowGraph
from alpha.workflow.runtime import DynamicWorkflowEngine


def _event(run_id: str, event_type: str, **payload) -> WorkflowEvent:
    return WorkflowEvent(workflow_run_id=run_id, event_type=event_type, payload=payload)


def _definition(workflow_id: str = "wf_durable") -> WorkflowDefinition:
    return WorkflowDefinition(
        id=workflow_id,
        name="Durable proof",
        description="kill-and-resume fixture",
        graph=WorkflowGraph(
            version=1,
            nodes={"only": {"id": "only", "prompt": "do the thing"}},
            edges=[],
        ),
        variables={},
        policies={},
    )


@pytest.fixture
def store(tmp_path) -> DurableEventLog:
    return DurableEventLog(tmp_path / "workflow_store")


def test_append_and_read_roundtrip_is_monotonic(store: DurableEventLog) -> None:
    for index in range(3):
        store.append(_event("run_a", "workflow_started", index=index))

    events, disclosures = store.read_events("run_a")
    assert disclosures == []
    assert [event.payload["index"] for event in events] == [0, 1, 2]
    assert store.last_seq("run_a") == 3
    # A different run has its own sequence space.
    assert store.last_seq("run_b") == 0


def test_idempotency_key_prevents_duplicate_attempt(store: DurableEventLog) -> None:
    key = node_attempt_idempotency_key(
        run_id="run_i", graph_version=1, node_id="n1", attempt=1, input_hash="deadbeef"
    )
    first = store.append(_event("run_i", "node_completed", node_id="n1"), idempotency_key=key)
    second = store.append(_event("run_i", "node_completed", node_id="n1"), idempotency_key=key)

    assert second.seq == first.seq
    assert second.event_id == first.event_id
    records, _ = store.records_for("run_i")
    assert len(records) == 1


def test_corrupt_tail_is_disclosed_and_append_refused(store: DurableEventLog) -> None:
    store.append(_event("run_c", "workflow_started"))
    store.append(_event("run_c", "node_started"))
    with store.event_log_path("run_c").open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")

    events, disclosures = store.read_events("run_c")
    assert [event.event_type for event in events] == ["workflow_started", "node_started"]
    assert len(disclosures) == 1
    assert disclosures[0]["line_number"] == 3
    assert "raw_prefix" in disclosures[0]

    # Appending past a corrupt tail would number events after untrustworthy
    # ordering: refuse instead.
    with pytest.raises(DurableEventLogError, match="corrupt tail"):
        store.append(_event("run_c", "node_completed"))


def test_unwritable_store_refuses_and_probe_reports(tmp_path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("I am a file", encoding="utf-8")
    store = DurableEventLog(blocker / "workflow_store")

    writable, detail = store.probe_writable()
    assert writable is False
    assert detail  # the real reason, never empty

    with pytest.raises(DurableEventLogError):
        store.append(_event("run_u", "workflow_started"))


def test_projection_roundtrip_preserves_run_and_graphs(store: DurableEventLog, tmp_path) -> None:
    definition = _definition()
    engine = DynamicWorkflowEngine()
    engine.register_definition(definition)
    run = engine.start_run("wf_durable", initial_state={"env": "test"})
    records, _ = store.records_for(run.run_id)
    store.append(_event(run.run_id, "workflow_started", workflow_id="wf_durable", state={"env": "test"}))
    records, _ = store.records_for(run.run_id)

    snapshot = store.project(
        run=run,
        definition=definition,
        graphs={f"wf_durable:v{definition.graph.version}": definition.graph},
        last_event=records[-1],
        event_count=len(records),
    )
    loaded = store.load_snapshot(run.run_id)
    assert loaded is not None
    assert loaded.run.run_id == run.run_id
    assert loaded.definition is not None and loaded.definition.id == "wf_durable"
    assert "wf_durable:v1" in loaded.graphs
    assert loaded.last_seq == records[-1].seq == snapshot.last_seq
    assert loaded.source_event["event_type"] == "workflow_started"


def test_kill_and_resume_hydrates_fresh_engine(store: DurableEventLog, monkeypatch) -> None:
    """Process A journals and projects; process B (fresh engine) hydrates."""
    definition = _definition("wf_resume")

    # --- process A: its own dispatcher wired straight to the durable log ---
    dispatcher_a = WorkflowEventDispatcher(durable_sink=store.append)
    monkeypatch.setattr(runtime_module, "get_event_dispatcher", lambda: dispatcher_a)
    engine_a = DynamicWorkflowEngine()
    engine_a.register_definition(definition)
    run = engine_a.start_run("wf_resume", initial_state={"phase": "one"})

    records, disclosures = store.records_for(run.run_id)
    assert disclosures == []
    assert [record.event_type for record in records] == ["workflow_started"]
    store.project(
        run=run,
        definition=definition,
        graphs=engine_a.graphs,
        last_event=records[-1],
        event_count=len(records),
    )

    # --- process B: brand-new dispatcher (no sink) and engine; nothing shared
    # in memory with A except the store on disk.
    monkeypatch.setattr(runtime_module, "get_event_dispatcher", lambda: WorkflowEventDispatcher())
    engine_b = DynamicWorkflowEngine()
    report = store.hydrate(engine_b)

    assert report.status == "ok"
    assert report.hydrated_runs == [run.run_id]
    resumed = engine_b.get_run(run.run_id)
    assert resumed is not None
    assert resumed.state == {"phase": "one"}
    assert set(resumed.node_states) == {"only"}
    assert engine_b.get_definition("wf_resume") is not None
    assert "wf_resume:v1" in engine_b.graphs
    # Hydration is idempotent: a second pass skips what is already installed.
    second = store.hydrate(engine_b)
    assert second.skipped_existing == [run.run_id]
    assert second.hydrated_runs == []


def test_hydration_refuses_stale_projection(store: DurableEventLog, monkeypatch) -> None:
    definition = _definition("wf_stale")
    # This run's events are journaled for real (the engine's dispatcher is
    # sink-backed), so "the log is ahead of the projection" is genuine state.
    monkeypatch.setattr(runtime_module, "get_event_dispatcher", lambda: WorkflowEventDispatcher(durable_sink=store.append))
    engine = DynamicWorkflowEngine()
    engine.register_definition(definition)
    run = engine.start_run("wf_stale")

    records, _ = store.records_for(run.run_id)
    store.project(run=run, definition=definition, graphs=engine.graphs, last_event=records[-1], event_count=len(records))
    # The run keeps going after the projection was taken: the durable log is
    # now AHEAD of the snapshot.
    store.append(_event(run.run_id, "node_started", node_id="only"))

    fresh = DynamicWorkflowEngine()
    report = store.hydrate(fresh)
    assert report.status == "degraded"
    assert report.hydrated_runs == []
    assert report.stale_projections[0]["run_id"] == run.run_id
    assert "seq" in report.stale_projections[0]["detail"]
    assert fresh.get_run(run.run_id) is None


def test_hydration_reports_corrupt_projection_without_repairing(store: DurableEventLog) -> None:
    store.run_projection_path("run_bad").parent.mkdir(parents=True, exist_ok=True)
    store.run_projection_path("run_bad").write_text('{"schema_version": 1, "run": ', encoding="utf-8")

    fresh = DynamicWorkflowEngine()
    report = store.hydrate(fresh)
    assert report.status == "degraded"
    assert report.corrupt_runs[0]["run_id"] == "run_bad"
    assert report.hydrated_runs == []
    # The damaged file is left exactly as found — never silently rewritten.
    assert store.run_projection_path("run_bad").read_text(encoding="utf-8") == '{"schema_version": 1, "run": '


def test_hydration_of_empty_store_reports_empty(store: DurableEventLog) -> None:
    report = store.hydrate(DynamicWorkflowEngine())
    assert report.status == "empty"
    assert report.hydrated_runs == []
    assert report.disclosures == []


def test_missing_graph_is_disclosed_not_hidden(store: DurableEventLog, monkeypatch) -> None:
    definition = _definition("wf_partial")
    monkeypatch.setattr(runtime_module, "get_event_dispatcher", lambda: WorkflowEventDispatcher(durable_sink=store.append))
    engine = DynamicWorkflowEngine()
    engine.register_definition(definition)
    run = engine.start_run("wf_partial")
    records, _ = store.records_for(run.run_id)
    # Project WITHOUT the graph revision the run points at.
    store.project(run=run, definition=definition, graphs={}, last_event=records[-1], event_count=len(records))

    fresh = DynamicWorkflowEngine()
    report = store.hydrate(fresh)
    assert report.status == "degraded"
    assert report.missing_graphs == [run.run_id]
    assert any("continuation may refuse honestly" in note for note in report.disclosures)
    assert fresh.get_run(run.run_id) is not None  # the run itself is real state


def test_dispatcher_sink_failure_is_counted_not_swallowed(store: DurableEventLog) -> None:
    def exploding_sink(_event: WorkflowEvent) -> None:
        raise OSError("disk on fire")

    dispatcher = WorkflowEventDispatcher(durable_sink=exploding_sink)
    dispatcher.emit("workflow_started", "run_x", state={})
    status = dispatcher.durable_status()
    assert status == {"attached": True, "write_failures": 1, "last_error": "OSError: disk on fire"}


def test_events_are_jsonl_one_line_per_event(store: DurableEventLog) -> None:
    store.append(_event("run_j", "workflow_started", state={"a": 1}))
    store.append(_event("run_j", "node_completed", node_id="n"))
    lines = store.event_log_path("run_j").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    payloads = [json.loads(line) for line in lines]
    assert [item["seq"] for item in payloads] == [1, 2]
    assert all(item["schema_version"] == 1 for item in payloads)
    assert payloads[0]["recorded_at"]  # writer-assigned, never empty
