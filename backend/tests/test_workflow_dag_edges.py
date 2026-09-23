"""Round-trip tests for edge patch operations through workflow_dag_manage
(DY-R2 FIX 2).

Regression targets:
- The tool used to build the patch layer's base WorkflowGraph WITHOUT the
  workflow's existing edges (legacy depends_on was never converted), and the
  sync-back loop only added brand-new nodes - so every edge operation
  (add/remove/update edge, insert_before/after) round-tripped against an empty
  edge list and was silently dropped.
- ``update_edge_condition`` additionally has no handler in the read-only patch
  engine (validator silently allows it, apply() silently drops it); the tool
  must apply it itself and error honestly on unknown edge references.

Assertions cover BOTH sides of every round-trip: the real patch graph objects
(captured via a spy on WorkflowPatchEngine.apply) and the legacy DAGWorkflow
(depends_on topology plus verbatim edge records on ``wf.graph_edges``).
Deterministic: no network, no clock, isolated module state per test.
"""

from __future__ import annotations

import json

import pytest

import alpha.tools.builtins.workflow_dag_tool as dag_tool
from alpha.tools.builtins.workflow_dag_tool import workflow_dag_manage


@pytest.fixture(autouse=True)
def isolated_dag_state():
    """Isolate the module-global DAG engine and version bookkeeping."""
    saved_workflows = dict(dag_tool._GLOBAL_DAG_ENGINE._workflows)
    saved_versions = dict(dag_tool._GRAPH_VERSIONS)
    dag_tool._GLOBAL_DAG_ENGINE._workflows.clear()
    dag_tool._GRAPH_VERSIONS.clear()
    yield
    dag_tool._GLOBAL_DAG_ENGINE._workflows.clear()
    dag_tool._GLOBAL_DAG_ENGINE._workflows.update(saved_workflows)
    dag_tool._GRAPH_VERSIONS.clear()
    dag_tool._GRAPH_VERSIONS.update(saved_versions)


def _create_linear_workflow(key: str) -> None:
    """Legacy workflow n1 <- n2 <- n3 built only from depends_on edges."""
    res = workflow_dag_manage.invoke({"action": "create", "key": key, "name": key})
    assert "Created workflow" in res
    specs = (("n1", None), ("n2", ["n1"]), ("n3", ["n2"]))
    for node_id, deps in specs:
        args = {
            "action": "add_node",
            "key": key,
            "node_id": node_id,
            "prompt": f"step {node_id}",
            "category": "quick",
        }
        if deps:
            args["depends_on"] = deps
        assert workflow_dag_manage.invoke(args).startswith("Added node")


def _apply_patch(key: str, operations: list[dict], base_version: int) -> str:
    patch = {
        "workflow_run_id": f"run_{key}",
        "base_graph_version": base_version,
        "reason": "DY-R2 edge round-trip test",
        "operations": operations,
    }
    return workflow_dag_manage.invoke(
        {"action": "apply_patch", "key": key, "patch_json": json.dumps(patch)}
    )


def _capture_graphs(monkeypatch) -> list[tuple]:
    """Record the (base_graph, new_graph, validation) of every engine apply."""
    captured: list[tuple] = []
    original = dag_tool._GLOBAL_PATCH_ENGINE.apply

    def spy(run, graph, patch):
        new_graph, validation = original(run, graph, patch)
        captured.append((graph, new_graph, validation))
        return new_graph, validation

    monkeypatch.setattr(dag_tool._GLOBAL_PATCH_ENGINE, "apply", spy)
    return captured


def _wf(key: str):
    wf = dag_tool._GLOBAL_DAG_ENGINE.get_workflow(key)
    assert wf is not None
    return wf


def _edge_pairs(pairs_source) -> set[tuple[str, str]]:
    return {(pair[0], pair[1]) for pair in pairs_source}


def test_base_graph_loads_existing_edges_and_add_edge_round_trips(monkeypatch):
    key = "dag_wf_add_edge"
    _create_linear_workflow(key)
    captured = _capture_graphs(monkeypatch)

    res = _apply_patch(
        key,
        [{"op": "add_edge", "args": {"edge": {"source": "n1", "target": "n3"}}}],
        base_version=1,
    )
    assert "Patch successfully committed" in res
    assert "Total edges: 3" in res
    assert "New graph version: 2" in res

    assert len(captured) == 1
    base_graph, new_graph, validation = captured[0]
    assert validation.allowed

    # ROOT-CAUSE FIX: the base graph now carries the pre-existing legacy
    # edges (derived from depends_on) instead of being edgeless.
    assert _edge_pairs((e.source, e.target) for e in base_graph.edges) == {
        ("n1", "n2"),
        ("n2", "n3"),
    }
    assert base_graph.version == 1

    # The new edge survives onto the patched graph...
    assert ("n1", "n3") in _edge_pairs((e.source, e.target) for e in new_graph.edges)
    assert new_graph.version == 2

    # ...and onto the legacy object: both depends_on topology and the verbatim
    # edge records.
    wf = _wf(key)
    assert "n1" in wf.nodes["n3"].depends_on
    assert wf.nodes["n2"].depends_on == ["n1"]  # untouched node unchanged
    assert _edge_pairs((e["source"], e["target"]) for e in wf.graph_edges) == {
        ("n1", "n2"),
        ("n2", "n3"),
        ("n1", "n3"),
    }


def test_remove_edge_round_trips_and_is_not_resurrected(monkeypatch):
    key = "dag_wf_remove_edge"
    _create_linear_workflow(key)
    captured = _capture_graphs(monkeypatch)

    res = _apply_patch(
        key,
        [{"op": "remove_edge", "args": {"source": "n2", "target": "n3"}}],
        base_version=1,
    )
    assert "Patch successfully committed" in res
    assert "Total edges: 1" in res  # base had 2 edges, one removed

    base_graph, new_graph, _ = captured[0]
    # The edge was present in the base graph (loaded from legacy)...
    assert ("n2", "n3") in _edge_pairs((e.source, e.target) for e in base_graph.edges)
    # ...and is gone from the patched graph...
    assert ("n2", "n3") not in _edge_pairs((e.source, e.target) for e in new_graph.edges)
    # ...and from the legacy object.
    wf = _wf(key)
    assert "n2" not in wf.nodes["n3"].depends_on
    assert wf.nodes["n3"].depends_on == []
    assert ("n2", "n3") not in _edge_pairs((e["source"], e["target"]) for e in wf.graph_edges)

    # Non-resurrection: the NEXT patch rebuilds its base graph from the legacy
    # object, which no longer carries the removed edge - asking to update its
    # condition must be an honest error, not a silent success or a re-appearing
    # edge.
    res2 = _apply_patch(
        key,
        [
            {
                "op": "update_edge_condition",
                "args": {"source": "n2", "target": "n3", "condition": "x > 1"},
            }
        ],
        base_version=2,
    )
    assert res2.startswith(
        "Error applying patch: update_edge_condition: edge 'n2' -> 'n3' does not exist"
    )
    assert len(captured) == 2
    rebuilt_base, _, _ = captured[1]
    assert ("n2", "n3") not in _edge_pairs((e.source, e.target) for e in rebuilt_base.edges)
    assert "n2" not in _wf(key).nodes["n3"].depends_on


def test_update_edge_condition_round_trips(monkeypatch):
    key = "dag_wf_update_condition"
    _create_linear_workflow(key)
    captured = _capture_graphs(monkeypatch)

    res = _apply_patch(
        key,
        [
            {
                "op": "update_edge_condition",
                "args": {"source": "n1", "target": "n2", "condition": "state.score > 0.8"},
            }
        ],
        base_version=1,
    )
    # Would be a silent no-op in the read-only patch engine; the tool must
    # actually apply it for this to commit with the condition present below.
    assert "Patch successfully committed" in res

    _, new_graph, _ = captured[0]
    edge = next(e for e in new_graph.edges if (e.source, e.target) == ("n1", "n2"))
    assert edge.condition == "state.score > 0.8"

    wf = _wf(key)
    conditions = {(e["source"], e["target"]): e["condition"] for e in wf.graph_edges}
    assert conditions[("n1", "n2")] == "state.score > 0.8"
    assert conditions[("n2", "n3")] is None
    # Topology unchanged by a condition update.
    assert wf.nodes["n2"].depends_on == ["n1"]
    assert wf.nodes["n3"].depends_on == ["n2"]


def test_insert_before_round_trips_onto_graph_and_legacy(monkeypatch):
    key = "dag_wf_insert_before"
    _create_linear_workflow(key)
    captured = _capture_graphs(monkeypatch)

    res = _apply_patch(
        key,
        [
            {
                "op": "insert_before",
                "args": {
                    "target_node_id": "n2",
                    "node": {"id": "n1b", "prompt": "inserted verification step"},
                },
            }
        ],
        base_version=1,
    )
    assert "Patch successfully committed" in res

    base_graph, new_graph, _ = captured[0]
    assert ("n1", "n2") in _edge_pairs((e.source, e.target) for e in base_graph.edges)
    graph_pairs = _edge_pairs((e.source, e.target) for e in new_graph.edges)
    assert ("n1", "n1b") in graph_pairs  # incoming edge redirected
    assert ("n1b", "n2") in graph_pairs  # new edge inserted
    assert ("n1", "n2") not in graph_pairs
    assert ("n2", "n3") in graph_pairs  # downstream untouched

    wf = _wf(key)
    assert "n1b" in wf.nodes
    assert wf.nodes["n1b"].prompt == "inserted verification step"
    assert wf.nodes["n1b"].depends_on == ["n1"]
    assert wf.nodes["n2"].depends_on == ["n1b"]
    assert wf.nodes["n3"].depends_on == ["n2"]
    legacy_pairs = _edge_pairs((e["source"], e["target"]) for e in wf.graph_edges)
    assert legacy_pairs == {("n1", "n1b"), ("n1b", "n2"), ("n2", "n3")}


def test_invalid_edge_ops_surface_real_errors(monkeypatch):
    key = "dag_wf_edge_errors"
    _create_linear_workflow(key)
    captured = _capture_graphs(monkeypatch)

    # 1. Invalid edge reference -> the validator's real rejection reason.
    res = _apply_patch(
        key,
        [{"op": "add_edge", "args": {"edge": {"source": "ghost", "target": "n1"}}}],
        base_version=1,
    )
    assert res == "Patch rejected: add_edge: Source 'ghost' not found."

    # 2. Version conflict -> optimistic concurrency violation, real numbers.
    res = _apply_patch(
        key,
        [{"op": "add_edge", "args": {"edge": {"source": "n1", "target": "n3"}}}],
        base_version=42,
    )
    assert res.startswith(
        "Patch rejected: Optimistic concurrency violation: patch base version 42"
    )

    # 3. update_edge_condition against an edge that never existed.
    res = _apply_patch(
        key,
        [{"op": "update_edge_condition", "args": {"source": "n1", "target": "n9", "condition": "x"}}],
        base_version=1,
    )
    assert res.startswith(
        "Error applying patch: update_edge_condition: edge 'n1' -> 'n9' does not exist"
    )

    # 4. An operation no layer implements must not report a fake commit.
    res = _apply_patch(key, [{"op": "fan_out", "args": {"node_id": "n1"}}], base_version=1)
    assert res.startswith("Error applying patch: unsupported operation(s)")
    assert "fan_out" in res

    # Every rejection left the legacy workflow and version bookkeeping untouched.
    wf = _wf(key)
    assert set(wf.nodes) == {"n1", "n2", "n3"}
    assert wf.nodes["n2"].depends_on == ["n1"]
    assert wf.nodes["n3"].depends_on == ["n2"]
    # wf.graph_edges is only (re)written by a successful sync-back; no rejected
    # patch ever reached that point, so no edge payload was ever attached.
    assert not getattr(wf, "graph_edges", None)
    # The engine is reached only by patches that pass the tool's pre-scan
    # (cases 1-3; the unsupported fan_out is rejected before apply):
    #   1. ghost edge      -> validator rejects (allowed=False)
    #   2. stale version   -> optimistic-concurrency reject (allowed=False)
    #   3. unknown cond. edge -> the read-only validator has NO branch for
    #      update_edge_condition, so the engine reports allowed=True; the
    #      tool's post-apply check rejects it before any sync-back.
    # No rejected patch reached sync-back: legacy untouched, version never
    # advanced past its initial (unset) state.
    assert len(captured) == 3
    assert [validation.allowed for _, _, validation in captured] == [False, False, True]
    assert dag_tool._GRAPH_VERSIONS.get(key) is None
