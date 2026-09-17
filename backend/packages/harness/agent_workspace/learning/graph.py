"""Knowledge Graph modeling learned codebase habits, memory chunks, and skill relations."""

from __future__ import annotations

import re
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any


def _tokenize(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-zA-Z0-9_]+", text.lower()) if len(t) >= 3}


def _lexical_similarity(s1: set[str], s2: set[str]) -> float:
    if not s1 or not s2:
        return 0.0
    return len(s1.intersection(s2)) / len(s1.union(s2))


@dataclass
class KnowledgeNode:
    """A discrete unit of learned intelligence, rule, or skill reference."""

    title: str
    content: str
    category: str = "general"
    source: str = "agent"  # "memory", "skill", "agent", "user"
    node_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    timestamp: float = field(default_factory=time.time)
    use_count: int = 0
    pinned: bool = False
    related: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class KnowledgeGraph:
    """Self-evolving knowledge graph maintaining topological connections between rules and skills."""

    def __init__(self):
        self.nodes: dict[str, KnowledgeNode] = {}
        self.edges: list[tuple[str, str]] = []

    def add_node(self, node: KnowledgeNode) -> None:
        self.nodes[node.node_id] = node
        # Add declared related edges
        for rel in node.related:
            if rel in self.nodes and rel != node.node_id:
                self.add_edge(node.node_id, rel)

    def add_edge(self, source_id: str, target_id: str) -> None:
        if source_id == target_id:
            return
        edge = (min(source_id, target_id), max(source_id, target_id))
        if edge not in self.edges:
            self.edges.append(edge)

    def density_stats(self) -> dict[str, Any]:
        """Compute topological density statistics inspired by Hermes."""
        linked_nodes = {x for edge in self.edges for x in edge}
        categories = Counter(node.category for node in self.nodes.values())
        n = len(self.nodes) or 1
        edges_count = len(self.edges)

        return {
            "total_nodes": len(self.nodes),
            "total_edges": edges_count,
            "edges_per_node": round(edges_count / n, 3),
            "linked_nodes": len(linked_nodes),
            "isolated_percentage": round(100.0 * (len(self.nodes) - len(linked_nodes)) / n, 1),
            "categories_count": len(categories),
            "top_categories": sorted(categories.items(), key=lambda kv: -kv[1])[:5],
            "pinned_nodes": sum(1 for node in self.nodes.values() if node.pinned),
        }

    def auto_link_lexical(self, threshold: float = 0.20) -> int:
        """Derive edges across nodes based on lexical token overlap."""
        new_edges = 0
        node_tokens = {nid: _tokenize(f"{n.title} {n.content}") for nid, n in self.nodes.items()}
        node_ids = list(self.nodes.keys())

        for i in range(len(node_ids)):
            for j in range(i + 1, len(node_ids)):
                id1, id2 = node_ids[i], node_ids[j]
                sim = _lexical_similarity(node_tokens[id1], node_tokens[id2])
                if sim >= threshold:
                    edge = (min(id1, id2), max(id1, id2))
                    if edge not in self.edges:
                        self.edges.append(edge)
                        new_edges += 1
        return new_edges

    def query(self, text: str, category: str | None = None, limit: int = 5) -> list[KnowledgeNode]:
        """Search graph by keyword overlap and category filter."""
        query_tokens = _tokenize(text)
        scored: list[tuple[float, KnowledgeNode]] = []

        for node in self.nodes.values():
            if category and node.category.lower() != category.lower():
                continue
            node_tokens = _tokenize(f"{node.title} {node.content}")
            sim = _lexical_similarity(query_tokens, node_tokens)
            if query_tokens and any(t in node.title.lower() for t in query_tokens):
                sim += 0.5  # Title boost
            if sim > 0:
                scored.append((sim, node))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [node for _, node in scored[:limit]]
        for r in results:
            r.use_count += 1
        return results

    def get_related(self, node_id: str) -> list[KnowledgeNode]:
        """Get all directly linked neighboring nodes."""
        neighbor_ids = set()
        for s, t in self.edges:
            if s == node_id:
                neighbor_ids.add(t)
            elif t == node_id:
                neighbor_ids.add(s)
        return [self.nodes[nid] for nid in neighbor_ids if nid in self.nodes]
