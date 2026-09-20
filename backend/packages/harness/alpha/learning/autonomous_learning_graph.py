"""Autonomous Learning Graph.

Assembles and visualizes the agent's procedural skills and episodic memory
units as an interconnected graph based on semantic overlap and procedural references.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


@dataclass
class LearningNode:
    id: str
    node_type: str  # "skill" or "memory"
    title: str
    content: str = ""
    category: str = "general"
    use_count: int = 0
    timestamp: float = field(default_factory=time.time)
    pinned: bool = False
    related: list[str] = field(default_factory=list)


@dataclass
class LearningEdge:
    source_id: str
    target_id: str
    weight: float = 1.0
    relation_type: str = "procedural"  # "procedural", "lexical", "hierarchical"


class AutonomousLearningGraph:
    """Interconnected graph of procedural skills and declarative memories."""

    def __init__(self):
        self.nodes: dict[str, LearningNode] = {}
        self.edges: list[LearningEdge] = []

    def add_node(
        self,
        id: str,
        node_type: str,
        title: str,
        content: str = "",
        category: str = "general",
        pinned: bool = False,
        use_count: int = 0,
        related: list[str] | None = None,
    ) -> LearningNode:
        node = LearningNode(
            id=id,
            node_type=node_type,
            title=title,
            content=content,
            category=category,
            pinned=pinned,
            use_count=use_count,
            related=related or [],
        )
        self.nodes[id] = node
        return node

    def add_edge(self, source_id: str, target_id: str, weight: float = 1.0, relation_type: str = "procedural") -> LearningEdge:
        edge = LearningEdge(
            source_id=source_id,
            target_id=target_id,
            weight=weight,
            relation_type=relation_type,
        )
        self.edges.append(edge)
        return edge

    def link_memory_to_skills(self, memory_id: str, memory_text: str, threshold: float = 0.15) -> list[LearningEdge]:
        mem_tokens = self._tokenize(memory_text)
        new_edges = []

        for node_id, node in self.nodes.items():
            if node.node_type != "skill":
                continue
            skill_tokens = self._tokenize(f"{node.title} {node.content}")
            sim = self._jaccard_similarity(mem_tokens, skill_tokens)
            if sim >= threshold:
                edge = self.add_edge(source_id=memory_id, target_id=node_id, weight=round(sim, 3), relation_type="lexical")
                new_edges.append(edge)

        return new_edges

    def prune_decayed_nodes(self, max_age_seconds: float = 30 * 86400.0, min_use_count: int = 1) -> int:
        now = time.time()
        to_remove = set()

        for nid, node in self.nodes.items():
            if node.pinned:
                continue
            age = now - node.timestamp
            if age > max_age_seconds and node.use_count < min_use_count:
                to_remove.add(nid)

        for nid in to_remove:
            del self.nodes[nid]

        self.edges = [e for e in self.edges if e.source_id in self.nodes and e.target_id in self.nodes]
        return len(to_remove)

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        words = re.findall(r"[a-zA-Z0-9_]{3,}", text.lower())
        stopwords = {"the", "and", "for", "with", "this", "that", "from", "are", "was", "has", "have"}
        return {w for w in words if w not in stopwords}

    @staticmethod
    def _jaccard_similarity(set1: set[str], set2: set[str]) -> float:
        if not set1 or not set2:
            return 0.0
        intersection = len(set1.intersection(set2))
        union = len(set1.union(set2))
        return intersection / union if union > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [asdict(n) for n in self.nodes.values()],
            "edges": [asdict(e) for e in self.edges],
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
        }
