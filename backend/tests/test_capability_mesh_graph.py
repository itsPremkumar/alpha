"""Tests for the capability graph (plan §4.1): nodes, edges, speculative rule, derivation.

An edge with no observations is a hypothesis and must carry the
``speculative`` marker; ``build_from_passports`` never fabricates (an
UNMEASURED estimate yields a 0.25-prior speculative edge); node-id collisions
with different kinds are refused by name; and every read is deterministically
sorted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alpha.capability_mesh.graph import (
    CapabilityGraph,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    build_from_memory,
    build_from_passports,
    merge_graphs,
)
from alpha.capability_mesh.memory import CapabilityMemory, CapabilityRecord, OutcomeKind
from alpha.capability_mesh.models import CapabilityEstimate, CapabilityPassport, EntityKind, EvidenceLevel

PV = "p1"


def _node(node_id: str, kind: NodeKind = NodeKind.ENTITY, label: str = "label") -> GraphNode:
    return GraphNode(node_id=node_id, kind=kind, label=label)


def _edge(source: str, target: str, kind: EdgeKind = EdgeKind.DEMONSTRATED_CAPABILITY, **kwargs) -> GraphEdge:
    return GraphEdge(source=source, target=target, kind=kind, **kwargs)


def _graph_with_two_nodes() -> CapabilityGraph:
    graph = CapabilityGraph()
    graph.add_node(_node("entity:a"))
    graph.add_node(_node("capability:code", NodeKind.CAPABILITY))
    return graph


def _estimate(capability: str, *, quality: float | None = None, samples: int = 0, level: EvidenceLevel = EvidenceLevel.UNMEASURED, category: str = "*") -> CapabilityEstimate:
    return CapabilityEstimate(capability=capability, task_category=category, quality_mean=quality, sample_count=samples, evidence_level=level, profile_version=PV if samples else "")


def _passport(entity_id: str = "agent:a", *, capabilities: tuple[CapabilityEstimate, ...] = (), required_tools: tuple[str, ...] = (), enabled: bool = True) -> CapabilityPassport:
    return CapabilityPassport(entity_id=entity_id, entity_type=EntityKind.AGENT, version="v1", capabilities=capabilities, required_tools=required_tools, enabled=enabled)


# ------------------------------------------------------------------- nodes


def test_graph_node_refuses_empty_node_id():
    with pytest.raises(ValueError, match="node_id must be non-empty"):
        GraphNode(node_id="", kind=NodeKind.ENTITY, label="x")


def test_graph_node_refuses_unknown_kind():
    with pytest.raises(ValueError, match="unknown kind"):
        GraphNode(node_id="entity:a", kind="bogus", label="x")


def test_graph_node_attributes_are_sorted_pairs():
    node = GraphNode(node_id="entity:a", kind=NodeKind.ENTITY, label="a", attributes=(("z", "1"), ("a", "2")))
    assert node.attributes == (("a", "2"), ("z", "1"))


def test_graph_node_refuses_duplicate_attribute_keys():
    with pytest.raises(ValueError, match="duplicate attribute key"):
        GraphNode(node_id="entity:a", kind=NodeKind.ENTITY, label="a", attributes=(("k", "1"), ("k", "2")))


def test_graph_node_round_trips():
    node = GraphNode(node_id="capability:code", kind=NodeKind.CAPABILITY, label="code", attributes=(("a", "b"),))
    assert GraphNode.from_dict(node.to_dict()) == node


# ------------------------------------------------------------------- edges


def test_graph_edge_refuses_empty_endpoints():
    with pytest.raises(ValueError, match="source must be non-empty"):
        GraphEdge(source="", target="capability:code", kind=EdgeKind.DEMONSTRATED_CAPABILITY)
    with pytest.raises(ValueError, match="target must be non-empty"):
        GraphEdge(source="entity:a", target="", kind=EdgeKind.DEMONSTRATED_CAPABILITY)


def test_graph_edge_refuses_weight_out_of_range():
    for weight in (-0.1, 1.1):
        with pytest.raises(ValueError, match="weight must be a finite number in \\[0, 1\\]"):
            GraphEdge(source="entity:a", target="capability:code", kind=EdgeKind.DEMONSTRATED_CAPABILITY, weight=weight)


def test_graph_edge_refuses_negative_evidence_count():
    with pytest.raises(ValueError, match="evidence_count must be an int >= 0"):
        GraphEdge(source="entity:a", target="capability:code", kind=EdgeKind.DEMONSTRATED_CAPABILITY, evidence_count=-1)


def test_graph_edge_requires_profile_version_when_evidenced():
    with pytest.raises(ValueError, match="profile_version"):
        GraphEdge(source="entity:a", target="capability:code", kind=EdgeKind.DEMONSTRATED_CAPABILITY, evidence_count=3)
    # A zero-evidence edge is a hypothesis: no score exists, so no profile is owed.
    edge = GraphEdge(source="entity:a", target="capability:code", kind=EdgeKind.DEMONSTRATED_CAPABILITY, evidence_count=0)
    assert edge.profile_version == ""


def test_graph_edge_round_trips():
    edge = _edge("entity:a", "capability:code", evidence_count=3, profile_version=PV)
    assert GraphEdge.from_dict(edge.to_dict()) == edge


# ------------------------------------------------------------------- store


def test_add_node_refuses_duplicate_id_with_different_kind():
    graph = _graph_with_two_nodes()
    # The "<kind-value>:<name>" convention makes a same-id/different-kind
    # node unconstructible, so the reachable guard is the prefix check
    # itself: a TOOL node can never carry an "entity:" id, and therefore
    # can never collide with an existing entity node inside add_node.
    with pytest.raises(ValueError, match="violates the '<kind-value>:<name>' convention"):
        graph.add_node(_node("entity:a", NodeKind.TOOL))


def test_add_node_same_kind_is_idempotent():
    graph = _graph_with_two_nodes()
    graph.add_node(_node("entity:a", NodeKind.ENTITY, label="again"))
    assert len(graph.nodes()) == 2


def test_add_edge_refuses_absent_endpoints():
    graph = _graph_with_two_nodes()
    with pytest.raises(ValueError, match="missing endpoint"):
        graph.add_edge(_edge("entity:a", "entity:ghost"))
    with pytest.raises(ValueError, match="missing endpoint"):
        graph.add_edge(_edge("entity:ghost", "capability:code"))


def test_add_edge_refuses_duplicate_triple():
    graph = _graph_with_two_nodes()
    graph.add_edge(_edge("entity:a", "capability:code"))
    with pytest.raises(ValueError, match="duplicate edge"):
        graph.add_edge(_edge("entity:a", "capability:code"))


def test_nodes_and_edges_are_deterministically_sorted():
    graph = CapabilityGraph()
    graph.add_node(_node("entity:b"))
    graph.add_node(_node("entity:a"))
    graph.add_node(_node("capability:code", NodeKind.CAPABILITY))
    graph.add_edge(_edge("entity:b", "capability:code", evidence_count=1, profile_version=PV))
    graph.add_edge(_edge("entity:a", "capability:code"))
    assert [node.node_id for node in graph.nodes()] == ["capability:code", "entity:a", "entity:b"]
    assert [(edge.source, edge.target) for edge in graph.edges()] == [("entity:a", "capability:code"), ("entity:b", "capability:code")]
    assert [node.node_id for node in graph.nodes(kind=NodeKind.ENTITY)] == ["entity:a", "entity:b"]
    assert [edge.source for edge in graph.edges(kind=EdgeKind.DEMONSTRATED_CAPABILITY)] == ["entity:a", "entity:b"]


def test_edges_by_node_filters_both_directions_and_unknown_node_is_refused():
    graph = _graph_with_two_nodes()
    graph.add_edge(_edge("entity:a", "capability:code"))
    # The edge is found whether the source or the target is queried.
    assert [edge.source for edge in graph.edges(node_id="entity:a")] == ["entity:a"]
    assert [edge.source for edge in graph.edges(node_id="capability:code")] == ["entity:a"]
    with pytest.raises(ValueError, match="unknown node"):
        graph.edges(node_id="entity:ghost")


def test_neighbors_returns_sorted_unique_ids():
    graph = _graph_with_two_nodes()
    graph.add_node(_node("tool:shell", NodeKind.TOOL))
    graph.add_edge(_edge("entity:a", "capability:code"))
    graph.add_edge(_edge("entity:a", "tool:shell", EdgeKind.REQUIRES_TOOL))
    assert graph.neighbors("entity:a") == ("capability:code", "tool:shell")
    assert graph.neighbors("entity:a", kind=EdgeKind.REQUIRES_TOOL) == ("tool:shell",)
    with pytest.raises(ValueError, match="unknown node"):
        graph.neighbors("entity:ghost")


# --------------------------------------------------------- speculative rule


def test_is_speculative_and_to_dict_marker():
    graph = _graph_with_two_nodes()
    speculative = _edge("entity:a", "capability:code")
    evidenced = _edge("entity:a", "capability:code", EdgeKind.VERIFIED_ON, evidence_count=2, profile_version=PV)
    graph.add_edge(speculative)
    graph.add_edge(evidenced)
    assert graph.is_speculative(speculative) is True
    assert graph.is_speculative(evidenced) is False
    document = graph.to_dict()
    flags = {(entry["source"], entry["target"], entry["kind"]): entry.get("speculative") for entry in document["edges"]}
    assert flags[("entity:a", "capability:code", "demonstrated_capability")] is True
    assert ("entity:a", "capability:code", "verified_on") not in {key for key, value in flags.items() if value}


def test_graph_round_trips_through_dict():
    graph = _graph_with_two_nodes()
    graph.add_edge(_edge("entity:a", "capability:code"))
    graph.add_edge(_edge("entity:a", "capability:code", EdgeKind.VERIFIED_ON, evidence_count=2, profile_version=PV))
    restored = CapabilityGraph.from_dict(graph.to_dict())
    assert restored.to_dict() == graph.to_dict()
    assert [node.node_id for node in restored.nodes()] == [node.node_id for node in graph.nodes()]


def test_from_dict_refuses_a_marker_that_lies():
    graph = _graph_with_two_nodes()
    graph.add_edge(_edge("entity:a", "capability:code"))
    document = graph.to_dict()
    document["edges"][0]["speculative"] = False  # evidence_count is 0; the marker cannot be argued away
    with pytest.raises(ValueError, match="speculative"):
        CapabilityGraph.from_dict(document)


def test_from_dict_refuses_kind_collision():
    document = {
        "version": 1,
        "nodes": [
            {"node_id": "entity:a", "kind": "entity", "label": "a", "attributes": {}},
            {"node_id": "entity:a", "kind": "tool", "label": "t", "attributes": {}},
        ],
        "edges": [],
    }
    with pytest.raises(ValueError, match="entity:a"):
        CapabilityGraph.from_dict(document)


# ---------------------------------------------------------- build_from_passports


def test_build_from_passports_requires_profile_version():
    with pytest.raises(ValueError, match="profile_version"):
        build_from_passports([], profile_version="")


def test_build_from_passports_derives_honest_edges():
    passports = [
        _passport(
            "agent:a",
            capabilities=(
                _estimate("code", quality=0.9, samples=10, level=EvidenceLevel.BENCHMARKED),
                _estimate("docs", samples=0),  # declared but unmeasured
            ),
            required_tools=("shell",),
        )
    ]
    graph = build_from_passports(passports, profile_version=PV)
    assert {node.node_id for node in graph.nodes()} == {"entity:agent:a", "capability:code", "capability:docs", "tool:shell"}
    demonstrated = {(edge.source, edge.target): edge for edge in graph.edges(kind=EdgeKind.DEMONSTRATED_CAPABILITY)}
    measured = demonstrated[("entity:agent:a", "capability:code")]
    assert (measured.weight, measured.evidence_count, measured.profile_version) == (0.9, 10, PV)
    assert graph.is_speculative(measured) is False
    prior = demonstrated[("entity:agent:a", "capability:docs")]
    assert (prior.weight, prior.evidence_count) == (0.25, 0)  # UNMEASURED prior, never a fabricated measurement
    assert graph.is_speculative(prior) is True
    tool_edge = graph.edges(kind=EdgeKind.REQUIRES_TOOL)[0]
    assert (tool_edge.source, tool_edge.target) == ("entity:agent:a", "tool:shell")


def test_build_from_passports_keeps_the_strongest_estimate_per_capability():
    passports = [
        _passport(
            "agent:a",
            capabilities=(
                _estimate("code", samples=0),
                _estimate("code", quality=0.7, samples=4, level=EvidenceLevel.OBSERVED, category="coding"),
            ),
        )
    ]
    graph = build_from_passports(passports, profile_version=PV)
    edges = graph.edges(kind=EdgeKind.DEMONSTRATED_CAPABILITY)
    assert len(edges) == 1
    assert (edges[0].weight, edges[0].evidence_count) == (0.7, 4)


# ------------------------------------------------------------- build_from_memory


def _memory_with(tmp_path: Path, rows: list[dict]) -> CapabilityMemory:
    memory = CapabilityMemory(tmp_path)
    for index, row in enumerate(rows):
        memory.record(
            CapabilityRecord(
                record_id=f"r{index}",
                task_fingerprint="fp_0011223344556677",
                task_category=row.get("category", "*"),
                entity_id=row["entity_id"],
                capability=row.get("capability", "code"),
                outcome=row.get("outcome", OutcomeKind.SELECTED),
                failure_mode=row.get("failure_mode"),
                profile_version=PV,
                recorded_at=f"2025-06-0{index + 1}T00:00:00+00:00",
            )
        )
    return memory


def test_build_from_memory_counts_failure_occurrences_and_verified_outcomes(tmp_path: Path):
    memory = _memory_with(
        tmp_path,
        [
            {"entity_id": "agent:a", "failure_mode": "timeout", "outcome": OutcomeKind.SELECTED, "category": "coding"},
            {"entity_id": "agent:a", "failure_mode": "timeout", "outcome": OutcomeKind.SELECTED, "category": "coding"},
            {"entity_id": "agent:a", "outcome": OutcomeKind.REJECTED, "category": "writing"},
            {"entity_id": "agent:a", "outcome": OutcomeKind.REPAIRED, "category": "writing"},
        ],
    )
    graph = build_from_memory(memory, profile_version=PV)
    misses = graph.edges(kind=EdgeKind.OFTEN_MISSES)
    assert len(misses) == 1
    assert (misses[0].source, misses[0].target) == ("entity:agent:a", "failure_mode:timeout")
    assert misses[0].evidence_count == 2
    verified = {(edge.target): edge for edge in graph.edges(kind=EdgeKind.VERIFIED_ON)}
    assert verified["outcome:coding"].evidence_count == 2
    assert verified["outcome:writing"].evidence_count == 1  # REPAIRED counts; REJECTED does not


def test_build_from_memory_extends_an_existing_graph(tmp_path: Path):
    memory = _memory_with(tmp_path, [{"entity_id": "agent:a", "failure_mode": "timeout"}])
    graph = _graph_with_two_nodes()
    build_from_memory(memory, profile_version=PV, graph=graph)
    # The single delivered record also earns a VERIFIED_ON edge to the task
    # category it was delivered in ("*" here, because the caller declared none).
    assert {node.node_id for node in graph.nodes()} == {"entity:a", "capability:code", "entity:agent:a", "failure_mode:timeout", "outcome:*"}


# ---------------------------------------------------------------- merge


def test_merge_graphs_unions_and_refuses_kind_collisions():
    first = _graph_with_two_nodes()
    second = CapabilityGraph()
    second.add_node(_node("entity:a", NodeKind.ENTITY))
    second.add_node(_node("tool:shell", NodeKind.TOOL))
    merged = merge_graphs(first, second)
    assert {node.node_id for node in merged.nodes()} == {"entity:a", "capability:code", "tool:shell"}

    # A same-id/different-kind node is unconstructible (the id prefix carries
    # the kind), so the reachable collision surface is a hand-edited document.
    document = {
        "version": 1,
        "nodes": [
            {"node_id": "entity:a", "kind": "entity", "label": "a", "attributes": []},
            {"node_id": "entity:a", "kind": "tool", "label": "t", "attributes": []},
        ],
        "edges": [],
    }
    with pytest.raises(ValueError, match="entity:a"):
        CapabilityGraph.from_dict(document)


def test_merge_graphs_combines_edges_by_evidence_then_weight():
    def graph_with(edge: GraphEdge) -> CapabilityGraph:
        graph = _graph_with_two_nodes()
        graph.add_edge(edge)
        return graph

    weaker = graph_with(_edge("entity:a", "capability:code", evidence_count=3, profile_version=PV, weight=0.9))
    stronger = graph_with(_edge("entity:a", "capability:code", evidence_count=5, profile_version=PV, weight=0.4))
    merged = merge_graphs(weaker, stronger)
    kept = merged.edges()[0]
    assert (kept.weight, kept.evidence_count) == (0.4, 5)  # more evidence wins outright

    tied_a = graph_with(_edge("entity:a", "capability:code", evidence_count=3, profile_version=PV, weight=0.5))
    tied_b = graph_with(_edge("entity:a", "capability:code", evidence_count=3, profile_version=PV, weight=0.7))
    tied = merge_graphs(tied_a, tied_b).edges()[0]
    assert tied.weight == 0.7  # evidence tie -> higher weight wins
