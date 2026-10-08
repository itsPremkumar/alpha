"""Filesystem persistence store for the Knowledge Graph."""

from __future__ import annotations

import json
import os
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

        # Stage into a local mapping and only adopt on full success.
        # A partial parse (valid JSON prefix, malformed entry partway through)
        # used to leave a half-populated graph that the next save() wrote back,
        # destroying every node that did parse. If *load* raises, the low-level
        # callers must not treat "load raised" as "empty graph".
        staged: KnowledgeGraph = KnowledgeGraph()
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
                staged.nodes[node.node_id] = node
            staged.edges = [tuple(e) for e in data.get("edges", [])]
        except Exception:
            # Leave the previous graph intact if one was loaded; if none was
            # loaded, return an empty graph (the documented fallback for a
            # missing file). Do NOT adopt a half-populated state and do NOT
            # write it back: that is exactly what destroyed partial data on
            # the previous save().
            return graph

        return staged

    def save(self, graph: KnowledgeGraph) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "nodes": [n.to_dict() for n in graph.nodes.values()],
            "edges": graph.edges,
        }
        # NOTE: LearningGraphStore historically wrote the full graph in one
        # non-atomic `write_text`. That is vulnerable to interruption (a crash
        # mid-write leaves a truncated file that load() silently treats as an
        # empty graph, destroying all nodes). Upgrade to an atomic write: temp
        # file in the same directory, fsync, os.replace. The temp file is
        # removed in a finally block so no stray `.tmp` file is left behind.
        tmp_path = self.file_path.with_name(self.file_path.name + ".tmp")
        try:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise
            os.replace(tmp_path, self.file_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)


_global_graph_store: LearningGraphStore | None = None


def get_learning_graph_store(root_dir: Path | str | None = None) -> LearningGraphStore:
    global _global_graph_store
    if _global_graph_store is None or root_dir is not None:
        _global_graph_store = LearningGraphStore(root_dir)
    return _global_graph_store
