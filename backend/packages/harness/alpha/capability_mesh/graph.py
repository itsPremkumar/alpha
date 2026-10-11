"""The capability graph (plan §4.1): who can do what, with what evidence.

A leaderboard answers "who scored highest?" A capability graph answers
"which group produces the best coverage with the least redundant
effort?". The graph is a plain in-memory structure of two frozen
value types (:class:`GraphNode`, :class:`GraphEdge`) plus one mutable
container (:class:`CapabilityGraph`), because a graph that cannot be
inspected node-by-node cannot be reasoned about.

Five rules decide the shape of this module:

* **Node ids follow one convention.** ``"<kind-value>:<name>"`` (e.g.
  ``"entity:agent:a"``, ``"capability:code_review"``), minted by
  :func:`node_id_for` and enforced in ``GraphNode.__post_init__`` —
  an id that does not carry its kind's prefix is refused, so a reader
  always knows what a node is from its id alone.
* **A duplicate id with a different kind is a refusal, not a merge.**
  :meth:`CapabilityGraph.add_node` keeps the first node and refuses a
  same-id/different-kind re-add by name; the same id and kind is an
  idempotent no-op. Identity is the id; nothing is silently merged.
* **Edges validate their endpoints at add time.** An edge whose source
  or target is absent is refused by name — "add them explicitly or
  refuse by name". A dangling edge is a fabrication about a node that
  does not exist.
* **An edge with no observations is a hypothesis.** ``evidence_count
  == 0`` edges travel with ``speculative=True`` in :meth:`to_dict`
  (and :func:`is_speculative` exposes it), and ``evidence_count > 0``
  requires a non-empty ``profile_version`` — the "no score without
  its profile version" rule, extended to edges.
* **Builders never fabricate.** :func:`build_from_passports` gives a
  measured estimate its ``quality_mean`` as the edge weight and its
  ``sample_count`` as the evidence count, but an UNMEASURED estimate
  produces a speculative edge at the ``UNMEASURED_PRIOR`` weight —
  and a declared ``required_tools`` entry is *configuration*, not an
  observation, so ``REQUIRES_TOOL`` edges are speculative until a run
  actually requires the tool.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from alpha.capability_mesh.fit import UNMEASURED_PRIOR
from alpha.capability_mesh.memory import CapabilityMemory, CapabilityRecord, OutcomeKind
from alpha.capability_mesh.models import CapabilityPassport


class NodeKind(StrEnum):
    """What one graph node represents (plan §4.1's node types)."""

    ENTITY = "entity"
    CAPABILITY = "capability"
    TASK_CATEGORY = "task_category"
    TOOL = "tool"
    SKILL = "skill"
    FAILURE_MODE = "failure_mode"
    OUTCOME = "outcome"
    TEAM = "team"


class EdgeKind(StrEnum):
    """One empirical relationship between two nodes (plan §4.1)."""

    DEMONSTRATED_CAPABILITY = "demonstrated_capability"
    REQUIRES_TOOL = "requires_tool"
    IMPROVES_WITH_SKILL = "improves_with_skill"
    OFTEN_MISSES = "often_misses"
    COMPLEMENTS = "complements"
    DUPLICATES = "duplicates"
    CONFLICTS_WITH = "conflicts_with"
    VERIFIED_ON = "verified_on"


def node_id_for(kind: NodeKind, name: str) -> str:
    """Mint the canonical ``"<kind-value>:<name>"`` node id."""
    if not name:
        raise ValueError("a node name must be non-empty")
    return f"{NodeKind(kind).value}:{name}"


@dataclass(frozen=True)
class GraphNode:
    """One node in the capability graph.

    ``node_id`` MUST follow the ``"<kind-value>:<name>"`` convention —
    the constructor refuses an id whose prefix does not match the
    node's kind, so the vocabulary is enforced rather than hoped for.
    ``attributes`` are sorted ``(key, value)`` string pairs: JSON-safe
    by construction, deduplicated by key, and always returned sorted
    so two nodes describing the same thing compare equal.
    """

    node_id: str
    kind: NodeKind
    label: str
    attributes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.node_id:
            raise ValueError("node_id must be non-empty")
        if not self.label:
            raise ValueError("label must be non-empty")
        try:
            object.__setattr__(self, "kind", NodeKind(str(self.kind)))
        except ValueError as error:
            allowed = ", ".join(member.value for member in NodeKind)
            raise ValueError(f"unknown kind {self.kind!r}; expected one of: {allowed}") from error
        prefix = f"{self.kind.value}:"
        if not self.node_id.startswith(prefix):
            raise ValueError(f"node_id {self.node_id!r} violates the '<kind-value>:<name>' convention: a '{self.kind.value}' node id must start with {prefix!r}")
        pairs = tuple(self.attributes)
        seen: set[str] = set()
        for pair in pairs:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError(f"attributes must be (key, value) string pairs, got {pair!r}")
            key, value = pair
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError(f"attribute keys and values must be strings, got {pair!r}")
            if not key:
                raise ValueError("attribute keys must be non-empty strings")
            if key in seen:
                raise ValueError(f"duplicate attribute key {key!r}: attributes are one value per key")
            seen.add(key)
        object.__setattr__(self, "attributes", tuple(sorted(pairs)))

    def attribute(self, key: str) -> str | None:
        """The value for one attribute key, or None when absent."""
        for pair_key, value in self.attributes:
            if pair_key == key:
                return value
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind.value,
            "label": self.label,
            "attributes": [[key, value] for key, value in self.attributes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphNode:
        return cls(
            node_id=str(data["node_id"]),
            # Coerced (and validated with the friendly message) in __post_init__.
            kind=data["kind"],
            label=str(data["label"]),
            attributes=tuple((str(entry[0]), str(entry[1])) for entry in data.get("attributes", ())),
        )


@dataclass(frozen=True)
class GraphEdge:
    """One empirical relationship between two graph nodes.

    ``weight`` is the empirical strength of the relationship in [0, 1]
    — a measured or derived number, never an opinion. ``evidence_count``
    is how many observations support the edge; ``evidence_count == 0``
    marks the edge as a *hypothesis* (see :func:`is_speculative`), and
    any positive count requires the ``profile_version`` of the rubric
    that produced the observations — an edge with observations but no
    rubric is a score without its provenance.
    """

    source: str
    target: str
    kind: EdgeKind
    weight: float = 1.0
    evidence_count: int = 0
    profile_version: str = ""

    def __post_init__(self) -> None:
        for field in ("source", "target"):
            if not getattr(self, field):
                raise ValueError(f"{field} must be non-empty")
        try:
            object.__setattr__(self, "kind", EdgeKind(str(self.kind)))
        except ValueError as error:
            allowed = ", ".join(member.value for member in EdgeKind)
            raise ValueError(f"unknown kind {self.kind!r}; expected one of: {allowed}") from error
        if isinstance(self.weight, bool) or not isinstance(self.weight, (int, float)) or not math.isfinite(float(self.weight)) or not 0.0 <= float(self.weight) <= 1.0:
            raise ValueError(f"weight must be a finite number in [0, 1], got {self.weight!r}")
        object.__setattr__(self, "weight", float(self.weight))
        if isinstance(self.evidence_count, bool) or not isinstance(self.evidence_count, int) or self.evidence_count < 0:
            raise ValueError(f"evidence_count must be an int >= 0, got {self.evidence_count!r}")
        if self.evidence_count > 0 and not self.profile_version:
            raise ValueError("evidence_count > 0 requires a non-empty profile_version (no score without its profile version)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind.value,
            "weight": self.weight,
            "evidence_count": self.evidence_count,
            "profile_version": self.profile_version,
            # Derived, never stored separately: an edge with no
            # observations is a hypothesis by definition.
            "speculative": self.evidence_count == 0,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphEdge:
        return cls(
            source=str(data["source"]),
            target=str(data["target"]),
            # Coerced (and validated with the friendly message) in __post_init__.
            kind=data["kind"],
            weight=float(data.get("weight", 1.0)),
            evidence_count=int(data.get("evidence_count", 0)),
            profile_version=str(data.get("profile_version", "")),
        )


def is_speculative(edge: GraphEdge) -> bool:
    """True when the edge rests on zero observations — a hypothesis.

    An edge nobody observed is a *claim about* a relationship, not
    evidence of one. Callers must not present a speculative edge as a
    measured one; the marker travels in ``to_dict`` for exactly that
    reason.
    """
    if not isinstance(edge, GraphEdge):
        raise ValueError(f"is_speculative expects a GraphEdge, got {edge!r}")
    return edge.evidence_count == 0


class CapabilityGraph:
    """The in-memory capability graph: nodes plus empirically-backed edges.

    Mutation is explicit and validated: :meth:`add_node` refuses a
    duplicate id carrying a different kind, and :meth:`add_edge`
    refuses endpoints that were never added. Adding the same
    ``(source, target, kind)`` edge twice replaces it — the newest
    observation wins, and there is exactly one edge per triple.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, GraphNode] = {}
        self._edges: dict[tuple[str, str, EdgeKind], GraphEdge] = {}

    # ---------------------------------------------------------------- nodes

    def add_node(self, node: GraphNode) -> GraphNode:
        """Add one node; refuse a same-id/different-kind collision by name.

        Re-adding an existing id with the same kind is an idempotent
        no-op returning the node already in the graph: identity is the
        id, and silently replacing a node's label or attributes would
        make every prior edge ambiguous about what it pointed at.
        """
        if not isinstance(node, GraphNode):
            raise ValueError(f"add_node expects a GraphNode, got {node!r}")
        existing = self._nodes.get(node.node_id)
        if existing is not None:
            if existing.kind is not node.kind:
                raise ValueError(f"node_id {node.node_id!r} already exists as kind '{existing.kind.value}': refusing to re-add it as kind '{node.kind.value}' (a duplicate id with a different kind is a collision, not a merge)")
            return existing
        self._nodes[node.node_id] = node
        return node

    def node(self, node_id: str) -> GraphNode | None:
        """The node with exactly this id, or None."""
        return self._nodes.get(node_id)

    def nodes(self, kind: NodeKind | None = None) -> tuple[GraphNode, ...]:
        """All nodes, deterministically sorted by ``node_id``."""
        selected = [node for node in self._nodes.values() if kind is None or node.kind is kind]
        return tuple(sorted(selected, key=lambda node: node.node_id))

    # ---------------------------------------------------------------- edges

    def add_edge(self, edge: GraphEdge) -> GraphEdge:
        """Add one edge, validating that both endpoints exist.

        An edge whose source or target was never added is refused by
        name — add the endpoints explicitly, or do not add the edge.
        There is exactly one edge per ``(source, target, kind)``
        triple: adding the same triple again replaces it (the newest
        observation wins).
        """
        if not isinstance(edge, GraphEdge):
            raise ValueError(f"add_edge expects a GraphEdge, got {edge!r}")
        missing = [endpoint for endpoint in (edge.source, edge.target) if endpoint not in self._nodes]
        if missing:
            raise ValueError(f"edge ({edge.source!r} -> {edge.target!r}, {edge.kind.value}) references absent node(s): {', '.join(missing)} — add them explicitly or refuse by name")
        self._edges[(edge.source, edge.target, edge.kind)] = edge
        return edge

    def edges(
        self,
        kind: EdgeKind | None = None,
        node_id: str | None = None,
    ) -> tuple[GraphEdge, ...]:
        """All edges, deterministically sorted by ``(source, target, kind)``.

        ``kind`` filters by edge kind; ``node_id`` filters to edges
        touching that node (as source OR target). An unknown
        ``node_id`` is refused by name rather than answered with
        nothing — a typo must not read as an empty result.
        """
        if node_id is not None and node_id not in self._nodes:
            raise ValueError(f"unknown node_id {node_id!r}: edges are only queryable for nodes the graph contains")
        selected = [edge for edge in self._edges.values() if (kind is None or edge.kind is kind) and (node_id is None or edge.source == node_id or edge.target == node_id)]
        return tuple(sorted(selected, key=lambda edge: (edge.source, edge.target, edge.kind.value)))

    def neighbors(self, node_id: str, kind: EdgeKind | None = None) -> tuple[str, ...]:
        """The ids of nodes adjacent to ``node_id``, sorted and unique.

        Adjacency is bidirectional (an edge's endpoints are adjacent
        whatever direction it is recorded in); ``kind`` restricts to one
        relationship type. An unknown ``node_id`` is refused by name.
        """
        if node_id not in self._nodes:
            raise ValueError(f"unknown node_id {node_id!r}: neighbors are only queryable for nodes the graph contains")
        adjacent: set[str] = set()
        for edge in self._edges.values():
            if kind is not None and edge.kind is not kind:
                continue
            if edge.source == node_id:
                adjacent.add(edge.target)
            elif edge.target == node_id:
                adjacent.add(edge.source)
        return tuple(sorted(adjacent))

    # -------------------------------------------------------------- durability

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe projection: sorted nodes, then sorted edges."""
        return {
            "nodes": [node.to_dict() for node in self.nodes()],
            "edges": [edge.to_dict() for edge in self.edges()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityGraph:
        """Rebuild a graph from :meth:`to_dict` output.

        Every row is re-validated through the constructors, so a
        hand-edited document cannot smuggle a dangling edge or a
        mis-prefixed node id past the door.
        """
        graph = cls()
        for entry in data.get("nodes", ()):
            graph.add_node(GraphNode.from_dict(entry))
        for entry in data.get("edges", ()):
            graph.add_edge(GraphEdge.from_dict(entry))
        return graph


# ------------------------------------------------------------------ builders


def build_from_passports(
    passports: Iterable[CapabilityPassport],
    *,
    profile_version: str,
    graph: CapabilityGraph | None = None,
) -> CapabilityGraph:
    """Derive a graph from passports: what each entity demonstrates (plan §4.1).

    Every passport contributes an ENTITY node, one CAPABILITY node per
    declared capability, one TOOL node per required tool, and two edge
    kinds:

    * ``DEMONSTRATED_CAPABILITY`` (entity -> capability): weight is the
      estimate's ``quality_mean`` when the estimate is measured, else
      the ``UNMEASURED_PRIOR`` (0.25 — a prior, never evidence);
      ``evidence_count`` is the ``sample_count``. An UNMEASURED
      estimate therefore produces a *speculative* edge: zero
      observations, zero fabricated strength.
    * ``REQUIRES_TOOL`` (entity -> tool): a *declared* tool
      requirement is configuration, not an observation, so it is
      recorded with ``evidence_count == 0`` — speculative until a run
      actually requires the tool.

    ``profile_version`` names the rubric the graph is built under and
    back-fills an edge's provenance when the estimate carried none; a
    measured estimate's own ``profile_version`` always wins, because a
    score is traceable to the rubric that produced it.
    """
    if not profile_version:
        raise ValueError("build_from_passports requires a non-empty profile_version (no score without its profile version)")
    result = graph if graph is not None else CapabilityGraph()
    if not isinstance(result, CapabilityGraph):
        raise ValueError(f"graph must be a CapabilityGraph, got {graph!r}")
    for passport in passports:
        if not isinstance(passport, CapabilityPassport):
            raise ValueError(f"passports must contain CapabilityPassport rows, got {passport!r}")
        attributes: list[tuple[str, str]] = [("entity_type", passport.entity_type.value), ("version", passport.version)]
        if passport.model_provider:
            attributes.append(("model_provider", passport.model_provider))
        if passport.model_family:
            attributes.append(("model_family", passport.model_family))
        entity_node = GraphNode(
            node_id=node_id_for(NodeKind.ENTITY, passport.entity_id),
            kind=NodeKind.ENTITY,
            label=passport.entity_id,
            attributes=tuple(attributes),
        )
        result.add_node(entity_node)
        for estimate in passport.capabilities:
            capability = estimate.capability
            result.add_node(GraphNode(node_id=node_id_for(NodeKind.CAPABILITY, capability), kind=NodeKind.CAPABILITY, label=capability))
            measured = estimate.quality_mean is not None
            # No measured quality -> the unmeasured prior (0.25), never a fabricated strength.
            weight = estimate.quality_mean if measured else UNMEASURED_PRIOR
            evidence = estimate.sample_count
            version = (estimate.profile_version or profile_version) if evidence > 0 else ""
            result.add_edge(
                GraphEdge(
                    source=entity_node.node_id,
                    target=node_id_for(NodeKind.CAPABILITY, capability),
                    kind=EdgeKind.DEMONSTRATED_CAPABILITY,
                    weight=weight,
                    evidence_count=evidence,
                    profile_version=version,
                )
            )
        for tool in passport.required_tools:
            result.add_node(GraphNode(node_id=node_id_for(NodeKind.TOOL, tool), kind=NodeKind.TOOL, label=tool))
            result.add_edge(
                GraphEdge(
                    source=entity_node.node_id,
                    target=node_id_for(NodeKind.TOOL, tool),
                    kind=EdgeKind.REQUIRES_TOOL,
                    # Declared, not observed: configuration is a hypothesis until a run requires it.
                    weight=1.0,
                    evidence_count=0,
                    profile_version="",
                )
            )
    return result


def build_from_memory(
    memory: CapabilityMemory,
    *,
    profile_version: str,
    graph: CapabilityGraph | None = None,
) -> CapabilityGraph:
    """Derive failure and verification edges from capability memory (plan §4.1).

    ``OFTEN_MISSES`` (entity -> failure_mode) edges carry the number of
    records that failed with that mode as ``evidence_count`` and the
    observed miss rate (occurrences / the entity's records) as the
    weight. ``VERIFIED_ON`` (entity -> outcome-category) edges do the
    same for SELECTED and REPAIRED records — the outcomes in which the
    contribution made it into the deliverable and was kept or fixed.

    The profile-version-comparability rule holds: only records measured
    under ``profile_version`` (or records carrying no profile version at
    all — an observation with no measured quality is rubric-agnostic)
    count toward evidence. An edge with observations carries the
    build-time ``profile_version`` so it stays traceable to a rubric.
    """
    if not profile_version:
        raise ValueError("build_from_memory requires a non-empty profile_version (no score without its profile version)")
    if not isinstance(memory, CapabilityMemory):
        raise ValueError(f"memory must be a CapabilityMemory, got {memory!r}")
    result = graph if graph is not None else CapabilityGraph()
    if not isinstance(result, CapabilityGraph):
        raise ValueError(f"graph must be a CapabilityGraph, got {graph!r}")
    scoped: list[CapabilityRecord] = [record for record in memory.records() if record.profile_version in ("", profile_version)]
    by_entity: dict[str, list[CapabilityRecord]] = {}
    for record in scoped:
        by_entity.setdefault(record.entity_id, []).append(record)
    for entity_id in sorted(by_entity):
        rows = by_entity[entity_id]
        entity_node_id = node_id_for(NodeKind.ENTITY, entity_id)
        result.add_node(GraphNode(node_id=entity_node_id, kind=NodeKind.ENTITY, label=entity_id))
        total = len(rows)
        misses: dict[str, int] = {}
        for row in rows:
            if row.failure_mode:
                misses[row.failure_mode] = misses.get(row.failure_mode, 0) + 1
        for mode in sorted(misses):
            count = misses[mode]
            target = node_id_for(NodeKind.FAILURE_MODE, mode)
            result.add_node(GraphNode(node_id=target, kind=NodeKind.FAILURE_MODE, label=mode))
            result.add_edge(
                GraphEdge(
                    source=entity_node_id,
                    target=target,
                    kind=EdgeKind.OFTEN_MISSES,
                    # The observed miss rate: how often this entity's records failed this way.
                    weight=count / total,
                    evidence_count=count,
                    profile_version=profile_version,
                )
            )
        verified: dict[str, int] = {}
        for row in rows:
            # SELECTED/REPAIRED: the contribution was delivered and kept or fixed (plan §4.1).
            if row.outcome in (OutcomeKind.SELECTED, OutcomeKind.REPAIRED):
                verified[row.outcome.value] = verified.get(row.outcome.value, 0) + 1
        for category in sorted(verified):
            count = verified[category]
            target = node_id_for(NodeKind.OUTCOME, category)
            result.add_node(GraphNode(node_id=target, kind=NodeKind.OUTCOME, label=category))
            result.add_edge(
                GraphEdge(
                    source=entity_node_id,
                    target=target,
                    kind=EdgeKind.VERIFIED_ON,
                    weight=count / total,
                    evidence_count=count,
                    profile_version=profile_version,
                )
            )
    return result


def merge_graphs(a: CapabilityGraph, b: CapabilityGraph) -> CapabilityGraph:
    """Union two graphs, refusing identity collisions and combining edges honestly.

    Node collisions are only allowed when both graphs agree on the
    kind: a same-id/different-kind collision is refused by name, and
    same-kind duplicates keep one row (the graphs agree on the node's
    identity). For an edge triple present in both graphs, the row with
    the **most evidence wins** (``evidence_count``), because more
    observations is a strictly stronger claim; on an evidence tie the
    higher weight wins, and a full tie keeps the first row. The rule
    is deterministic: same inputs, same merged graph.
    """
    if not isinstance(a, CapabilityGraph) or not isinstance(b, CapabilityGraph):
        raise ValueError(f"merge_graphs expects two CapabilityGraphs, got {a!r} and {b!r}")
    merged = CapabilityGraph()
    for graph in (a, b):
        for node in graph.nodes():
            # add_node refuses a same-id/different-kind collision by name.
            merged.add_node(node)
    combined: dict[tuple[str, str, EdgeKind], list[GraphEdge]] = {}
    for graph in (a, b):
        for edge in graph.edges():
            combined.setdefault((edge.source, edge.target, edge.kind), []).append(edge)
    for rows in combined.values():
        if len(rows) == 1:
            winner = rows[0]
        else:
            # Most evidence wins; tie -> higher weight (documented rule).
            winner = max(rows, key=lambda edge: (edge.evidence_count, edge.weight))
        merged.add_edge(winner)
    return merged


__all__ = [
    "CapabilityGraph",
    "EdgeKind",
    "GraphEdge",
    "GraphNode",
    "NodeKind",
    "build_from_memory",
    "build_from_passports",
    "is_speculative",
    "merge_graphs",
    "node_id_for",
]
