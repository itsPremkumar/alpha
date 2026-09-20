"""Semantic Fact & Belief Graph Engine.

Maintains epistemic belief nodes (Subject-Predicate-Object) with confidence levels,
provenance evidence, conflict reconciliation, and topological relationship traversal.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from alpha.memory.cognitive.models import (
    BeliefStatus,
    SemanticFactNode,
    SemanticRelationEdge,
)


class SemanticBeliefGraph:
    """Directed topological graph of epistemic facts, beliefs, and concept relationships."""

    def __init__(self, max_nodes: int = 2000) -> None:
        self.max_nodes = max_nodes
        self._nodes: dict[str, SemanticFactNode] = {}
        self._edges: dict[str, SemanticRelationEdge] = {}
        self._adj: dict[str, set[str]] = defaultdict(set)
        self._rev_adj: dict[str, set[str]] = defaultdict(set)

    def add_belief(
        self,
        subject: str,
        predicate: str,
        object_val: str,
        confidence: float = 0.8,
        status: BeliefStatus | str = BeliefStatus.ACTIVE,
        evidence: list[str] | None = None,
        valid_from: float | None = None,
        valid_to: float | None = None,
        salience: float = 0.7,
        tags: list[str] | None = None,
        node_id: str | None = None,
        revision: int = 1,
        access_count: int = 1,
        last_accessed_at: float | None = None,
        created_at: float | None = None,
        superseded_by: str | None = None,
    ) -> SemanticFactNode:
        """Add or update an epistemic belief fact in the graph."""
        if isinstance(status, str):
            try:
                status = BeliefStatus(status.lower())
            except ValueError:
                status = BeliefStatus.ACTIVE

        now = time.time()
        c_at = created_at or now
        l_acc = last_accessed_at or c_at

        # Check if identical belief exists (exact subject, predicate, object) unless explicit node_id is provided
        if not node_id:
            for existing in self._nodes.values():
                if (
                    existing.subject.lower() == subject.strip().lower()
                    and existing.predicate.lower() == predicate.strip().lower()
                    and existing.object_val.lower() == object_val.strip().lower()
                    and existing.status == BeliefStatus.ACTIVE
                ):
                    # Reinforce existing belief
                    existing.confidence = min(1.0, existing.confidence + 0.05)
                    existing.revision += 1
                    existing.access_count += 1
                    existing.last_accessed_at = now
                    if evidence:
                        existing.evidence.extend(evidence)
                    return existing

        node = SemanticFactNode(
            subject=subject.strip(),
            predicate=predicate.strip(),
            object_val=object_val.strip(),
            confidence=max(0.0, min(1.0, confidence)),
            status=status,
            evidence=evidence or [],
            valid_from=valid_from or c_at,
            valid_to=valid_to,
            revision=revision,
            access_count=access_count,
            last_accessed_at=l_acc,
            salience=max(0.0, min(1.0, salience)),
            created_at=c_at,
            superseded_by=superseded_by,
            tags=tags or [],
        )
        if node_id:
            node.node_id = node_id

        self._nodes[node.node_id] = node
        self._enforce_node_capacity()
        return node

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        relation: str = "relates_to",
        weight: float = 1.0,
        metadata: dict[str, Any] | None = None,
        edge_id: str | None = None,
    ) -> SemanticRelationEdge | None:
        """Add directed relation between two belief nodes."""
        if source_id not in self._nodes or target_id not in self._nodes or source_id == target_id:
            return None

        # Check existing edge
        for edge in self._edges.values():
            if (
                edge.source_id == source_id
                and edge.target_id == target_id
                and edge.relation.lower() == relation.lower()
            ):
                edge.weight = max(edge.weight, weight)
                return edge

        edge = SemanticRelationEdge(
            source_id=source_id,
            target_id=target_id,
            relation=relation.strip(),
            weight=weight,
            metadata=metadata or {},
        )
        if edge_id:
            edge.edge_id = edge_id
        self._edges[edge.edge_id] = edge
        self._adj[source_id].add(target_id)
        self._rev_adj[target_id].add(source_id)
        return edge

    def get_node(self, node_id: str) -> SemanticFactNode | None:
        node = self._nodes.get(node_id)
        if node:
            node.access_count += 1
            node.last_accessed_at = time.time()
        return node

    def list_nodes(
        self,
        status: BeliefStatus | None = None,
        subject: str | None = None,
        limit: int = 100,
    ) -> list[SemanticFactNode]:
        nodes = list(self._nodes.values())
        if status:
            nodes = [n for n in nodes if n.status == status]
        if subject:
            s_low = subject.lower()
            nodes = [n for n in nodes if s_low in n.subject.lower()]
        nodes.sort(key=lambda n: (n.confidence, n.salience), reverse=True)
        return nodes[:limit]

    def detect_conflicts(self) -> list[tuple[SemanticFactNode, SemanticFactNode, str]]:
        """Identify conflicting beliefs (e.g. same subject & predicate with contradictory objects)."""
        active_nodes = [n for n in self._nodes.values() if n.status == BeliefStatus.ACTIVE]
        conflicts = []

        for i in range(len(active_nodes)):
            for j in range(i + 1, len(active_nodes)):
                n1, n2 = active_nodes[i], active_nodes[j]
                if n1.subject.lower() == n2.subject.lower():
                    # Same predicate but different object
                    if (
                        n1.predicate.lower() == n2.predicate.lower()
                        and n1.object_val.lower() != n2.object_val.lower()
                    ):
                        reason = f"Direct value contradiction: '{n1.object_val}' vs '{n2.object_val}' for predicate '{n1.predicate}'"
                        conflicts.append((n1, n2, reason))

        return conflicts

    def reconcile_conflicts(self) -> int:
        """Resolve conflicting beliefs using temporal recency and confidence weighting."""
        conflicts = self.detect_conflicts()
        reconciled_count = 0

        for n1, n2, _ in conflicts:
            if n1.status != BeliefStatus.ACTIVE or n2.status != BeliefStatus.ACTIVE:
                continue

            # If one is significantly newer and has equal or higher confidence
            if n1.created_at > n2.created_at and n1.confidence >= (n2.confidence - 0.15):
                n2.status = BeliefStatus.SUPERSEDED
                n2.superseded_by = n1.node_id
                self.add_edge(n1.node_id, n2.node_id, relation="supersedes", weight=1.0)
                reconciled_count += 1
            elif n2.created_at > n1.created_at and n2.confidence >= (n1.confidence - 0.15):
                n1.status = BeliefStatus.SUPERSEDED
                n1.superseded_by = n2.node_id
                self.add_edge(n2.node_id, n1.node_id, relation="supersedes", weight=1.0)
                reconciled_count += 1
            else:
                # Ambiguous: mark both as CONTESTED for human/agent review
                n1.status = BeliefStatus.CONTESTED
                n2.status = BeliefStatus.CONTESTED
                self.add_edge(n1.node_id, n2.node_id, relation="contradicts", weight=1.0)
                reconciled_count += 1

        return reconciled_count

    def traverse(self, start_node_id: str, max_depth: int = 2, decay_per_hop: float = 0.5) -> list[tuple[SemanticFactNode, float]]:
        """Multi-hop breadth-first graph traversal returning reachable nodes with proximity weights."""
        if start_node_id not in self._nodes:
            return []

        visited = {start_node_id}
        queue = [(start_node_id, 1.0, 0)]
        results: list[tuple[SemanticFactNode, float]] = []

        while queue:
            curr_id, current_weight, depth = queue.pop(0)
            if depth > 0:
                results.append((self._nodes[curr_id], current_weight))

            if depth >= max_depth:
                continue

            # Check outbound and inbound neighbors
            neighbors = self._adj.get(curr_id, set()) | self._rev_adj.get(curr_id, set())
            for neighbor_id in neighbors:
                if neighbor_id not in visited and neighbor_id in self._nodes:
                    visited.add(neighbor_id)
                    queue.append((neighbor_id, current_weight * decay_per_hop, depth + 1))

        return results

    def density_metrics(self) -> dict[str, Any]:
        """Graph topological summary metrics."""
        total_nodes = len(self._nodes)
        total_edges = len(self._edges)
        status_counts = defaultdict(int)
        for n in self._nodes.values():
            status_counts[n.status.value] += 1

        connected_nodes = len([n for n in self._nodes if self._adj[n] or self._rev_adj[n]])

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "connected_nodes": connected_nodes,
            "active_beliefs": status_counts[BeliefStatus.ACTIVE.value],
            "contested_beliefs": status_counts[BeliefStatus.CONTESTED.value],
            "superseded_beliefs": status_counts[BeliefStatus.SUPERSEDED.value],
            "deprecated_beliefs": status_counts[BeliefStatus.DEPRECATED.value],
            "density": round((total_edges / max(1, total_nodes * (total_nodes - 1))), 4) if total_nodes > 1 else 0.0,
        }

    def _enforce_node_capacity(self) -> None:
        if len(self._nodes) <= self.max_nodes:
            return
        sorted_keys = sorted(
            self._nodes.keys(),
            key=lambda k: (self._nodes[k].salience, self._nodes[k].confidence, self._nodes[k].last_accessed_at),
        )
        excess = len(self._nodes) - self.max_nodes
        for k in sorted_keys[:excess]:
            self.delete_node(k)

    def delete_node(self, node_id: str) -> bool:
        if node_id not in self._nodes:
            return False
        del self._nodes[node_id]
        # Clean edges
        edges_to_remove = [
            eid for eid, e in self._edges.items()
            if e.source_id == node_id or e.target_id == node_id
        ]
        for eid in edges_to_remove:
            del self._edges[eid]
        self._adj.pop(node_id, None)
        self._rev_adj.pop(node_id, None)
        for s in self._adj.values():
            s.discard(node_id)
        for s in self._rev_adj.values():
            s.discard(node_id)
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": self.density_metrics(),
            "nodes": [n.to_dict() for n in self.list_nodes(limit=100)],
            "edges": [e.to_dict() for e in self._edges.values()][:100],
        }
