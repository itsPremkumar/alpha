"""Graph revision diffing.

Honesty pins in this suite:
* **structural vs runtime state are different facts** — a node's status moving
  from ``running`` to ``succeeded`` is NOT a change of plan, and must never be
  reported in `plan_changes`;
* every field of `WorkflowNode`/`WorkflowEdge` is covered by exactly one of the
  two field lists, so no field can silently fail to diff;
* a change always carries a `summary` built from measured facts;
* rendering is **redacted and bounded**, because node config holds credentials
  and node output is unbounded;
* the reason comes from the record and stays empty when nobody wrote one —
  never invented;
* output is byte-identical across runs for identical inputs.
"""

from __future__ import annotations

from alpha.workflow.graph_diff import (
    EDGE_FIELDS,
    NODE_RUNTIME_FIELDS,
    NODE_STRUCTURAL_FIELDS,
    ChangeKind,
    diff_graphs,
    diff_plan_versions,
)
from alpha.workflow.models import (
    EdgeMode,
    NodeStatus,
    RetryPolicy,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
)


def _node(node_id: str, **overrides: object) -> WorkflowNode:
    payload: dict[str, object] = {"id": node_id}
    payload.update(overrides)
    return WorkflowNode(**payload)  # type: ignore[arg-type]


def _graph(nodes: list[WorkflowNode], edges: list[tuple[str, str]] | None = None, *, version: int = 1, **meta: object) -> WorkflowGraph:
    graph = WorkflowGraph(version=version, metadata=dict(meta))
    for node in nodes:
        graph.add_node(node)
    for source, target in edges or []:
        graph.add_edge(WorkflowEdge(source=source, target=target))
    return graph


class TestFieldCoverage:
    """A field missing from these lists would silently never diff."""

    def test_every_node_field_is_classified(self) -> None:
        declared = set(NODE_STRUCTURAL_FIELDS) | set(NODE_RUNTIME_FIELDS)
        assert declared == set(WorkflowNode.model_fields) - {"id"}, "every WorkflowNode field must be classified as structural or runtime"

    def test_structural_and_runtime_are_disjoint(self) -> None:
        assert set(NODE_STRUCTURAL_FIELDS) & set(NODE_RUNTIME_FIELDS) == set()

    def test_runtime_fields_are_the_execution_state(self) -> None:
        """Exactly the fields a re-run mutates are the runtime ones."""
        assert set(NODE_RUNTIME_FIELDS) == {
            "status",
            "evidence",
            "output",
            "tokens_consumed",
            "approval_request_id",
            "approval_requested_at",
        }

    def test_every_edge_field_is_covered(self) -> None:
        assert set(EDGE_FIELDS) == set(WorkflowEdge.model_fields) - {"source", "target"}


class TestStructuralVsRuntime:
    def test_runtime_only_change_is_not_a_plan_change(self) -> None:
        """The core honesty property: a completed node did not change the plan."""
        base = _graph([_node("build")])
        target = _graph([_node("build", status=NodeStatus.SUCCEEDED, output={"files": 3}, tokens_consumed=500)])

        diff = diff_graphs(base, target)

        assert diff.identical is False
        assert diff.has_plan_change is False
        assert diff.plan_changes == ()
        assert len(diff.runtime_changes) == 1

        change = diff.runtime_changes[0]
        assert change.kind is ChangeKind.NODE_STATE_CHANGED
        assert change.structural is False
        assert change.key == "build"
        assert {item.field for item in change.fields} == {"status", "output", "tokens_consumed"}

    def test_structural_change_is_a_plan_change(self) -> None:
        base = _graph([_node("build", timeout_seconds=30.0)])
        target = _graph([_node("build", timeout_seconds=120.0)])

        diff = diff_graphs(base, target)

        assert diff.has_plan_change is True
        assert len(diff.plan_changes) == 1
        assert diff.runtime_changes == ()
        change = diff.plan_changes[0]
        assert change.kind is ChangeKind.NODE_CHANGED
        assert change.fields[0].field == "timeout_seconds"
        assert change.fields[0].before == 30.0
        assert change.fields[0].after == 120.0

    def test_both_kinds_reported_together_nothing_hidden(self) -> None:
        base = _graph([_node("build", timeout_seconds=30.0)])
        target = _graph([_node("build", timeout_seconds=60.0, status=NodeStatus.SUCCEEDED)])

        diff = diff_graphs(base, target)

        assert len(diff.changes) == 1  # one node, both kinds of difference
        change = diff.changes[0]
        assert change.kind is ChangeKind.NODE_CHANGED  # structural dominates
        assert {item.field for item in change.fields} == {"timeout_seconds", "status"}
        assert change.structural is True
        # ...and the summary names BOTH fields, not just the structural one.
        assert "timeout_seconds" in change.summary
        assert "status" in change.summary

    def test_retry_policy_change_is_structural(self) -> None:
        base = _graph([_node("build")])
        target = _graph([_node("build", retry_policy=RetryPolicy(max_attempts=9))])

        diff = diff_graphs(base, target)
        assert diff.has_plan_change is True
        assert diff.plan_changes[0].fields[0].field == "retry_policy"


class TestNodeAndEdgeChanges:
    def test_added_node(self) -> None:
        base = _graph([_node("a")])
        target = _graph([_node("a"), _node("b")])

        diff = diff_graphs(base, target)
        assert [c.kind for c in diff.changes] == [ChangeKind.NODE_ADDED]
        assert diff.changes[0].key == "b"
        assert diff.changes[0].summary == "node added: b"
        assert diff.affected_nodes == ["b"]

    def test_removed_node(self) -> None:
        base = _graph([_node("a"), _node("b")])
        target = _graph([_node("a")])

        diff = diff_graphs(base, target)
        assert [c.kind for c in diff.changes] == [ChangeKind.NODE_REMOVED]
        assert diff.changes[0].summary == "node removed: b"

    def test_identical_graphs_are_identical(self) -> None:
        base = _graph([_node("a"), _node("b")], [("a", "b")], version=7)
        target = _graph([_node("a"), _node("b")], [("a", "b")], version=7)

        diff = diff_graphs(base, target)
        assert diff.identical is True
        assert diff.changes == ()
        assert diff.has_plan_change is False

    def test_edge_added_and_removed(self) -> None:
        base = _graph([_node("a"), _node("b")], [("a", "b")])
        target = _graph([_node("a"), _node("b")], [("b", "a")])

        diff = diff_graphs(base, target)
        kinds = [(c.kind, c.key) for c in diff.changes]
        assert ChangeKind.EDGE_REMOVED in [k for k, _ in kinds]
        assert ChangeKind.EDGE_ADDED in [k for k, _ in kinds]

    def test_edge_condition_change_reported_as_edge_change(self) -> None:
        base_graph = _graph([_node("a"), _node("b")])
        base_graph.add_edge(WorkflowEdge(source="a", target="b"))
        target_graph = _graph([_node("a"), _node("b")])
        target_graph.add_edge(WorkflowEdge(source="a", target="b", condition="state.score > 0.5", mode=EdgeMode.CONDITONAL if False else EdgeMode.CONDITIONAL))

        diff = diff_graphs(base_graph, target_graph)
        changed = [c for c in diff.changes if c.kind is ChangeKind.EDGE_ADDED]
        assert len(changed) == 1
        assert changed[0].key == "a->b"
        assert {f.field for f in changed[0].fields} == {"condition", "mode"}

    def test_duplicate_edges_between_same_pair_are_not_collapsed(self) -> None:
        base_graph = _graph([_node("a"), _node("b")])
        base_graph.add_edge(WorkflowEdge(source="a", target="b"))
        target_graph = _graph([_node("a"), _node("b")])
        target_graph.add_edge(WorkflowEdge(source="a", target="b"))
        target_graph.add_edge(WorkflowEdge(source="a", target="b", priority=10))

        diff = diff_graphs(base_graph, target_graph)
        keys = [c.key for c in diff.changes]
        assert "a->b#1" in keys, "a second edge between the same pair must be visible, not merged away"

    def test_metadata_change(self) -> None:
        base = _graph([_node("a")], max_concurrency=1)
        target = _graph([_node("a")], max_concurrency=8)

        diff = diff_graphs(base, target)
        assert [c.kind for c in diff.changes] == [ChangeKind.METADATA_CHANGED]
        assert diff.changes[0].key == "max_concurrency"
        assert diff.changes[0].fields[0].before == 1
        assert diff.changes[0].fields[0].after == 8


class TestSummaryAndCounts:
    def test_summary_counts_every_kind_including_zeros(self) -> None:
        base = _graph([_node("a")])
        target = _graph([_node("a"), _node("b")])

        summary = diff_graphs(base, target).summary
        assert summary["node_added"] == 1
        # every kind is present so a zero reads as an explicit zero, not absence
        for kind in ChangeKind:
            assert kind.value in summary

    def test_counts_travel_with_the_diff(self) -> None:
        base = _graph([_node("a")])
        target = _graph([_node("a"), _node("b"), _node("c")])

        diff = diff_graphs(base, target)
        assert diff.base_node_count == 1
        assert diff.target_node_count == 3
        assert diff.base_edge_count == 0

    def test_direction_is_preserved_not_symmetrised(self) -> None:
        """Diffing backwards must describe going backwards, not be normalised."""
        v1 = _graph([_node("a")], version=1)
        v3 = _graph([_node("a"), _node("b")], version=3)

        forward = diff_graphs(v1, v3)
        backward = diff_graphs(v3, v1)

        assert [c.kind for c in forward.changes] == [ChangeKind.NODE_ADDED]
        assert [c.kind for c in backward.changes] == [ChangeKind.NODE_REMOVED]
        assert backward.base_version == 3
        assert backward.target_version == 1


class TestRendering:
    def test_config_credentials_are_redacted(self) -> None:
        base = _graph([_node("call", config={"endpoint": "https://x"})])
        target = _graph([_node("call", config={"endpoint": "https://y", "api_key": "sk-super-secret", "token": "abc123"})])

        rendered = diff_graphs(base, target).to_dict()
        text = str(rendered)
        assert "sk-super-secret" not in text, "a diff endpoint must not become the side door the event log closes"
        assert "abc123" not in text
        # the non-secret part of the change must survive redaction
        assert "https://y" in text

    def test_long_output_is_bounded_with_an_explicit_marker(self) -> None:
        huge = "x" * 50_000
        base = _graph([_node("build", output="small")])
        target = _graph([_node("build", output=huge)])

        rendered = diff_graphs(base, target).to_dict()
        changes = rendered["changes"]
        output_field = next(f for f in changes[0]["fields"] if f["field"] == "output")
        assert len(output_field["after"]) < 3_000, "an unbounded diff is not renderable or transmissible"
        assert "truncated" in output_field["after"]
        assert "50000" in output_field["after"], "the true length must be disclosed, not implied"

    def test_runtime_changes_can_be_excluded_from_rendering(self) -> None:
        base = _graph([_node("build", status=NodeStatus.PENDING)])
        target = _graph([_node("build", status=NodeStatus.SUCCEEDED)])

        diff = diff_graphs(base, target)
        assert diff.to_dict(include_runtime=False)["changes"] == []
        assert len(diff.to_dict(include_runtime=True)["changes"]) == 1

    def test_rendering_is_deterministic(self) -> None:
        base = _graph([_node("a", timeout_seconds=1.0), _node("b")])
        target = _graph([_node("a", timeout_seconds=2.0), _node("b"), _node("c")])

        first = diff_graphs(base, target).to_dict()
        second = diff_graphs(base, target).to_dict()
        assert first == second
        assert [c["kind"] for c in first["changes"]] == sorted(
            [c["kind"] for c in first["changes"]],
            key=lambda k: [kind.value for kind in ChangeKind].index(k),
        )


class TestProvenance:
    def test_reason_comes_from_the_record_and_is_attached(self) -> None:
        base = _graph([_node("a")])
        target = _graph([_node("a"), _node("b")])

        diff = diff_graphs(base, target, reason="inserted a verification node after the flaky build", source="patch")
        assert diff.reason == "inserted a verification node after the flaky build"
        assert diff.source == "patch"

    def test_absent_reason_stays_empty_never_invented(self) -> None:
        diff = diff_graphs(_graph([_node("a")]), _graph([_node("a")]))
        assert diff.reason == ""
        assert diff.source is None

    def test_diff_plan_versions_reads_the_target_note(self) -> None:
        class _Record:
            def __init__(self, graph: WorkflowGraph, note: str, source: str) -> None:
                self.graph = graph
                self.note = note
                self.source = source

        base = _Record(_graph([_node("a")], version=1), note="initial registration", source="register")
        target = _Record(_graph([_node("a"), _node("b")], version=2), note="replan: add verification", source="patch")

        diff = diff_plan_versions(base, target)
        assert diff.base_version == 1
        assert diff.target_version == 2
        assert diff.reason == "replan: add verification"
        assert diff.source == "patch"
        assert diff.has_plan_change is True

    def test_diff_plan_versions_with_no_note_reports_empty(self) -> None:
        class _Record:
            def __init__(self, graph: WorkflowGraph) -> None:
                self.graph = graph
                self.note = ""
                self.source = "register"

        diff = diff_plan_versions(_Record(_graph([_node("a")])), _Record(_graph([_node("a")])))
        assert diff.reason == ""
