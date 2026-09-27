"""Tests for workflow templates and evidence-driven improvement proposals.

Two properties carry the weight here, because both prevent a specific and
expensive failure:

- **A template cannot be promoted without evidence.** A draft that never ran, or
  that only ever ran unsuccessfully, must not reach the library looking endorsed.
  Promotion therefore re-checks the run COMPLETED, that the run's graph is the
  template's graph, and that every succeeded node carried real evidence.
- **A suggestion is a proposal, never an action.** Nothing in the improvement
  module may mutate a run, a graph, or a template, and no rate may be reported
  without its sample count.
"""

from __future__ import annotations

import json

import pytest

from alpha.workflow.events import WorkflowEventDispatcher
from alpha.workflow.models import (
    NodeType,
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRunStatus,
)
from alpha.workflow.runtime import DynamicWorkflowEngine
from alpha.workflow.self_improvement import (
    analyze_corpus,
    collect_signals,
    suggest_improvements,
)
from alpha.workflow.templates import (
    TemplateError,
    TemplateState,
    TemplateStore,
    set_template_store,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    store = TemplateStore(store_dir=tmp_path / "templates")
    set_template_store(store)
    yield store
    set_template_store(None)


def _ok(node, run):
    return {"status": "completed", "output": {"node": node.id}, "evidence": f"real work on {node.id}", "tokens_used": 3}


def _no_evidence(node, run):
    """A runner that reports success with NO evidence; the engine must refuse it."""
    return {"status": "completed", "output": {"node": node.id}, "evidence": "", "tokens_used": 0}


def _chain(length: int = 2) -> dict[str, WorkflowNode]:
    """A genuine linear chain: each node depends on the one before it."""
    nodes: dict[str, WorkflowNode] = {}
    for i in range(length):
        node = WorkflowNode(id=f"n{i}", prompt=f"step {i}")
        if i:
            node.depends_on = [f"n{i - 1}"]
        nodes[f"n{i}"] = node
    return nodes


def _fanout(*ids: str) -> dict[str, WorkflowNode]:
    """Independent roots: no node depends on another, so they can overlap."""
    return {nid: WorkflowNode(id=nid, prompt="p", write_scope=[nid]) for nid in ids}


def _engine(nodes: dict[str, WorkflowNode], workflow_id: str = "wf") -> DynamicWorkflowEngine:
    engine = DynamicWorkflowEngine()
    engine.events = WorkflowEventDispatcher(durable_sink=None)
    engine.register_definition(WorkflowDefinition(id=workflow_id, name=workflow_id, graph=WorkflowGraph(nodes=nodes), policies={}))
    return engine


def _completed_run(engine: DynamicWorkflowEngine, steps: int = 3) -> str:
    run = engine.start_run("wf")
    for _ in range(steps):
        engine.execute_step(run.run_id, node_runner=_ok)
    assert run.status is WorkflowRunStatus.COMPLETED
    return run.run_id


# ----------------------------------------------------------------- lifecycle


def test_capture_always_yields_a_draft_even_for_a_successful_run():
    store = TemplateStore()
    engine = _engine(_chain(2))
    run_id = _completed_run(engine)

    template = store.capture_from_run(engine, run_id, template_id="t1")

    assert template.state is TemplateState.DRAFT
    assert template.provenance.verified_at is None
    assert template.provenance.source_run_ids == []
    # Capturing says the graph exists; it says nothing about outcomes.
    assert template.provenance.created_from_run_id == run_id


def test_a_draft_cannot_be_promoted():
    store = TemplateStore()
    engine = _engine(_chain(2))
    run_id = _completed_run(engine)
    store.capture_from_run(engine, run_id, template_id="t1")

    with pytest.raises(TemplateError, match="unverified draft"):
        store.promote("t1")


def test_a_draft_cannot_be_instantiated():
    store = TemplateStore()
    engine = _engine(_chain(2))
    run_id = _completed_run(engine)
    store.capture_from_run(engine, run_id, template_id="t1")

    with pytest.raises(TemplateError, match="unverified draft"):
        store.instantiate("t1", engine=engine)


def test_verify_then_promote_walks_the_lifecycle_forward():
    store = TemplateStore()
    engine = _engine(_chain(2))
    run_id = _completed_run(engine)
    store.capture_from_run(engine, run_id, template_id="t1")

    verified = store.verify(engine, "t1", run_id)
    assert verified.state is TemplateState.VERIFIED
    assert run_id in verified.provenance.source_run_ids
    assert verified.provenance.verified_at is not None

    promoted = store.promote("t1")
    assert promoted.state is TemplateState.PROMOTED
    assert promoted.provenance.promoted_at is not None


def test_verify_refuses_a_run_that_did_not_complete():
    store = TemplateStore()
    engine = _engine(_chain(2))
    run = engine.start_run("wf")
    engine.execute_step(run.run_id, node_runner=lambda n, _r: {"status": "failed", "output": "no", "evidence": "", "tokens_used": 0})
    store.capture_from_run(engine, run.run_id, template_id="t1")

    with pytest.raises(TemplateError, match="not completed"):
        store.verify(engine, "t1", run.run_id)
    assert store.get("t1").state is TemplateState.DRAFT


def test_verify_refuses_a_run_of_a_different_graph():
    """A run can only vouch for the exact graph it executed."""
    store = TemplateStore()
    engine = _engine(_chain(2))
    run_id = _completed_run(engine)
    store.capture_from_run(engine, run_id, template_id="t1")

    # Change the template's graph so it no longer matches the run's.
    template = store.get("t1")
    template.graph.nodes["extra"] = WorkflowNode(id="extra", prompt="injected")
    store.save(template)

    with pytest.raises(TemplateError, match="different graph"):
        store.verify(engine, "t1", run_id)


def test_verify_refuses_a_completion_with_no_evidence():
    store = TemplateStore()
    engine = _engine(_chain(1))
    run = engine.start_run("wf")
    # The engine refuses an unevidenced completion outright, so the run fails and
    # can never be promoted: an unevidenced success cannot vouch for anything.
    engine.execute_step(run.run_id, node_runner=_no_evidence)
    store.capture_from_run(engine, run.run_id, template_id="t1")

    assert run.status is not WorkflowRunStatus.COMPLETED
    with pytest.raises(TemplateError, match="not completed"):
        store.verify(engine, "t1", run.run_id)


def test_verify_twice_is_refused_because_state_moved_on():
    store = TemplateStore()
    engine = _engine(_chain(2))
    run_id = _completed_run(engine)
    store.capture_from_run(engine, run_id, template_id="t1")
    store.verify(engine, "t1", run_id)

    with pytest.raises(TemplateError, match="only a draft can be verified"):
        store.verify(engine, "t1", run_id)


def test_instantiation_returns_an_independent_definition():
    store = TemplateStore()
    engine = _engine(_chain(2))
    run_id = _completed_run(engine)
    store.capture_from_run(engine, run_id, template_id="t1")
    store.verify(engine, "t1", run_id)
    store.promote("t1")

    first = store.instantiate("t1", engine=engine)
    second = store.instantiate("t1", engine=engine)

    assert first.id != second.id, "two instantiations must not share one identity"
    # Mutating one must not touch the library or the other.
    first.graph.nodes["n0"].prompt = "MUTATED"
    assert store.get("t1").graph.nodes["n0"].prompt != "MUTATED"
    assert second.graph.nodes["n0"].prompt != "MUTATED"


def test_a_verified_but_unpromoted_template_says_so():
    store = TemplateStore()
    engine = _engine(_chain(2))
    run_id = _completed_run(engine)
    store.capture_from_run(engine, run_id, template_id="t1")
    store.verify(engine, "t1", run_id)

    definition = store.instantiate("t1", engine=engine)

    assert "[verified, not promoted]" in definition.description


def test_store_survives_a_reload(tmp_path):
    engine = _engine(_chain(2))
    run_id = _completed_run(engine)
    path = tmp_path / "tpl"
    first = TemplateStore(store_dir=path)
    first.capture_from_run(engine, run_id, template_id="t1", name="Persisted")
    first.verify(engine, "t1", run_id)
    first.promote("t1")

    reloaded = TemplateStore(store_dir=path)
    template = reloaded.get("t1")

    assert template is not None
    assert template.name == "Persisted"
    assert template.state is TemplateState.PROMOTED
    assert template.provenance.source_run_ids == [run_id]


def test_a_corrupt_library_is_a_loud_failure_not_a_silent_empty_start(tmp_path):
    path = tmp_path / "broken"
    path.mkdir()
    (path / "templates.json").write_text("{not json", encoding="utf-8")

    store = TemplateStore(store_dir=path)
    with pytest.raises(TemplateError, match="unreadable"):
        store.list()


def test_stats_reports_state_breakdown():
    store = TemplateStore()
    engine = _engine(_chain(2))
    run_id = _completed_run(engine)
    store.capture_from_run(engine, run_id, template_id="t1")

    stats = store.stats()

    assert stats["total"] == 1
    assert stats["by_state"]["draft"] == 1
    assert stats["verified_with_evidence"] == 0


# ------------------------------------------------------------------- signals


def test_collect_signals_measures_a_completed_run():
    engine = _engine(_chain(3))
    run_id = _completed_run(engine, steps=3)

    signals = collect_signals(engine, run_id)

    assert signals.success is True
    assert signals.unproven is False
    assert signals.completed_nodes == 3
    assert signals.failed_nodes == 0
    assert signals.tokens_consumed == 9
    assert signals.waves_dispatched >= 1
    assert signals.total_measured_seconds > 0.0


def test_collect_signals_of_an_unknown_run_raises():
    engine = _engine(_chain(1))
    with pytest.raises(KeyError):
        collect_signals(engine, "run_nope")


def test_signals_report_a_failed_run_as_a_failure():
    engine = _engine(_chain(2))
    run = engine.start_run("wf")
    engine.execute_step(run.run_id, node_runner=lambda n, _r: {"status": "failed", "output": "nope", "evidence": "", "tokens_used": 0})

    signals = collect_signals(engine, run.run_id)

    assert signals.success is False
    assert signals.failed_nodes >= 1


# --------------------------------------------------------------- suggestions


def test_a_clean_run_produces_no_suggestions():
    engine = _engine(_chain(2))
    run_id = _completed_run(engine)

    report = suggest_improvements(engine, run_id)

    assert report["suggestions"] == []
    assert report["applied"] is False
    assert "proposal" in report["note"]


def test_a_timed_out_node_produces_a_timeout_suggestion():
    engine = DynamicWorkflowEngine()
    engine.events = WorkflowEventDispatcher(durable_sink=None)
    graph = WorkflowGraph(nodes={"slow": WorkflowNode(id="slow", prompt="p", timeout_seconds=0.02)})
    engine.register_definition(WorkflowDefinition(id="wf", name="wf", graph=graph, policies={}))
    run = engine.start_run("wf")
    import time

    engine.execute_step(run.run_id, node_runner=lambda n, _r: time.sleep(1.0) or _ok(n, _r))

    report = suggest_improvements(engine, run.run_id)
    kinds = {item["kind"]: item for item in report["suggestions"]}

    assert "timeout_tuning" in kinds
    assert kinds["timeout_tuning"]["subject"] == "slow"
    assert kinds["timeout_tuning"]["evidence"]["measured"] == "node_timeout"
    assert kinds["timeout_tuning"]["applied"] is False
    assert kinds["timeout_tuning"]["requires_human_review"] is True


def test_stagnation_recovery_produces_a_deadlock_suggestion():
    """A run that needed remediation patches is analysed from its recorded metric.

    The metric is written directly because a graph that deadlocks cannot be
    authored through the public API: ``validate_workflow_graph`` correctly
    refuses a dangling dependency, so reaching the recovery path legitimately
    requires a runtime-patched graph. Testing the ANALYSIS against the recorded
    metric isolates it from that construction problem.
    """
    engine = _engine(_chain(2))
    run = engine.start_run("wf")
    engine.execute_step(run.run_id, node_runner=_ok)
    run.metrics["stagnation_recovery_attempts"] = 2

    report = suggest_improvements(engine, run.run_id)
    deadlock = [item for item in report["suggestions"] if item["kind"] == "graph_deadlock"]

    assert len(deadlock) == 1
    assert deadlock[0]["evidence"]["measured"] == "stagnation_recovery_attempted"
    assert deadlock[0]["evidence"]["attempts"] == 2
    assert deadlock[0]["applied"] is False


def test_a_linear_chain_does_not_get_a_useless_parallelisation_hint():
    """The last node of a chain always dominates and has no sibling to overlap.

    Suggesting concurrency there is noise, so the signal requires a real
    independent sibling before it fires.
    """
    engine = _engine(_chain(3))
    run_id = _completed_run(engine, steps=3)

    report = suggest_improvements(engine, run_id)

    assert [item for item in report["suggestions"] if item["kind"] == "parallelisation_candidate"] == []


def test_an_independent_slow_sibling_does_get_a_parallelisation_hint():
    """Two roots with disjoint write scopes give the slow node a real sibling."""
    engine = DynamicWorkflowEngine()
    engine.events = WorkflowEventDispatcher(durable_sink=None)
    graph = WorkflowGraph(nodes=_fanout("fast", "slow"))
    engine.register_definition(WorkflowDefinition(id="wf", name="wf", graph=graph, policies={}))
    run = engine.start_run("wf")
    import time

    def runner(node, _run):
        if node.id == "slow":
            time.sleep(0.4)
        return _ok(node, _run)

    engine.execute_step(run.run_id, node_runner=runner)

    report = suggest_improvements(engine, run.run_id)
    hints = [item for item in report["suggestions"] if item["kind"] == "parallelisation_candidate"]

    assert len(hints) == 1
    assert hints[0]["subject"] == "slow"
    assert hints[0]["evidence"]["independent_siblings"] == ["fast"]
    assert hints[0]["evidence"]["share"] > 0.5


def test_every_suggestion_cites_a_measured_signal_and_is_marked_unapplied():
    engine = DynamicWorkflowEngine()
    engine.events = WorkflowEventDispatcher(durable_sink=None)
    graph = WorkflowGraph(nodes={"slow": WorkflowNode(id="slow", prompt="p", timeout_seconds=0.02)})
    engine.register_definition(WorkflowDefinition(id="wf", name="wf", graph=graph, policies={}))
    run = engine.start_run("wf")
    import time

    engine.execute_step(run.run_id, node_runner=lambda n, _r: time.sleep(1.0) or _ok(n, _r))

    report = suggest_improvements(engine, run.run_id)

    assert report["suggestions"], "this run should produce at least one suggestion"
    for item in report["suggestions"]:
        assert item["evidence"], "a suggestion without measured evidence is noise"
        assert item["evidence"].get("measured"), "every suggestion must name the signal it came from"
        assert item["applied"] is False
        assert item["requires_human_review"] is True
        assert item["samples"] >= 1
        assert item["confidence"] in ("low", "medium", "high")


def test_suggestions_do_not_mutate_the_run_or_its_graph():
    engine = _engine(_chain(2))
    run_id = _completed_run(engine)
    run = engine.get_run(run_id)
    before_status = run.status
    before_completed = list(run.completed_nodes)
    before_version = run.graph_version
    before_nodes = set(engine._run_graph_for(run).nodes)

    suggest_improvements(engine, run_id)

    assert run.status is before_status
    assert run.completed_nodes == before_completed
    assert run.graph_version == before_version
    assert set(engine._run_graph_for(run).nodes) == before_nodes


def test_suggestions_are_capped():
    engine = DynamicWorkflowEngine()
    engine.events = WorkflowEventDispatcher(durable_sink=None)
    nodes = {f"n{i}": WorkflowNode(id=f"n{i}", prompt="p", timeout_seconds=0.01) for i in range(20)}
    engine.register_definition(WorkflowDefinition(id="wf", name="wf", graph=WorkflowGraph(nodes=nodes), policies={}))
    run = engine.start_run("wf")
    import time

    engine.execute_step(run.run_id, node_runner=lambda n, _r: time.sleep(0.5) or _ok(n, _r))

    report = suggest_improvements(engine, run.run_id)

    assert report["suggestion_count"] <= report["max_suggestions"]
    assert report["truncated"] is True


# -------------------------------------------------------------------- corpus


def test_corpus_reports_a_rate_with_its_sample_count():
    engine = _engine(_chain(1))
    run_ids = [_completed_run(engine, steps=1) for _ in range(3)]

    report = analyze_corpus(engine, run_ids, workflow_id="wf")

    assert report["samples"] == 3
    assert report["successes"] == 3
    assert report["success_rate"] == 1.0
    assert "sample count" in report["note"]


def test_corpus_with_no_measured_runs_reports_no_rate():
    engine = _engine(_chain(1))

    report = analyze_corpus(engine, ["run_nope"], workflow_id="wf")

    assert report["samples"] == 0
    assert report["success_rate"] is None
    assert "no rate is reported" in report["note"]


def test_corpus_counts_an_unproven_success_separately():
    """A success asserted without evidence is not folded into the success rate."""
    engine = _engine({"cond": WorkflowNode(id="cond", type=NodeType.CONDITION, condition="1 == 1")})
    run = engine.start_run("wf")
    engine.execute_step(run.run_id)

    signals = collect_signals(engine, run.run_id)
    report = analyze_corpus(engine, [run.run_id], workflow_id="wf")

    # A CONDITION node's success IS evidenced (it appends the evaluated
    # expression), so this documents the accounting rather than a defect.
    assert signals.success is True
    assert report["unproven_successes"] == (1 if signals.unproven else 0)
    assert set(report) >= {"samples", "successes", "failures", "unproven_successes", "success_rate"}


def test_template_json_is_readable_and_versioned(tmp_path):
    engine = _engine(_chain(1))
    run_id = _completed_run(engine, steps=1)
    store = TemplateStore(store_dir=tmp_path / "tpl")
    store.capture_from_run(engine, run_id, template_id="t1")

    payload = json.loads(store.path.read_text(encoding="utf-8"))

    assert payload["version"] == 1
    assert payload["templates"][0]["id"] == "t1"
    assert payload["templates"][0]["state"] == "draft"
