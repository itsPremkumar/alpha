"""Filesystem persistence store for the Knowledge Graph."""

from __future__ import annotations

import json
from pathlib import Path

from alpha.learning.graph import KnowledgeGraph, KnowledgeNode


class LearningGraphStore:
    """Persists knowledge graph nodes and topological edges to JSON."""

    def __init__(self, root_dir: Path | str | None = None):
        root = Path(root_dir or Path.cwd())
        self.file_path = root / ".alpha" / "learning" / "graph.json"
        self._graph: KnowledgeGraph | None = None

    def get_graph(self) -> KnowledgeGraph:
        if self._graph is None:
            self._graph = self.load()
        return self._graph

    def load(self) -> KnowledgeGraph:
        graph = KnowledgeGraph()
        if not self.file_path.exists():
            return graph

        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
            for nd in data.get("nodes", []):
                node = KnowledgeNode(
                    node_id=nd["node_id"],
                    title=nd["title"],
                    content=nd["content"],
                    category=nd.get("category", "general"),
                    source=nd.get("source", "agent"),
                    timestamp=nd.get("timestamp", 0.0),
                    use_count=nd.get("use_count", 0),
                    pinned=nd.get("pinned", False),
                    related=nd.get("related", []),
                )
                graph.nodes[node.node_id] = node
            graph.edges = [tuple(e) for e in data.get("edges", [])]
        except Exception:
            pass
        return graph

    def save(self, graph: KnowledgeGraph) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "nodes": [n.to_dict() for n in graph.nodes.values()],
            "edges": graph.edges,
        }
        self.file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


_global_graph_store: LearningGraphStore | None = None


def get_learning_graph_store(root_dir: Path | str | None = None) -> LearningGraphStore:
    global _global_graph_store
    if _global_graph_store is None or root_dir is not None:
        _global_graph_store = LearningGraphStore(root_dir)
    return _global_graph_store
