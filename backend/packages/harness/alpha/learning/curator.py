"""Curator for pruning decayed nodes and merging duplicates in the Knowledge Graph."""

from __future__ import annotations

import time

from alpha.learning.graph import KnowledgeGraph, _lexical_similarity, _tokenize


class LearningGraphCurator:
    """Active curator maintaining graph cleanliness, density, and relevance."""

    def prune_decayed(
        self,
        graph: KnowledgeGraph,
        max_age_seconds: float = 30 * 86400.0,
        min_use_count: int = 1,
    ) -> int:
        """Remove unpinned, stale, unused nodes and their edges."""
        now = time.time()
        to_remove = []

        for nid, node in graph.nodes.items():
            if node.pinned:
                continue
            age = now - node.timestamp
            if age > max_age_seconds and node.use_count < min_use_count:
                to_remove.append(nid)

        for nid in to_remove:
            del graph.nodes[nid]

        # Clean edges
        graph.edges = [
            (s, t) for (s, t) in graph.edges if s in graph.nodes and t in graph.nodes
        ]
        return len(to_remove)

    def merge_duplicates(
        self,
        graph: KnowledgeGraph,
        similarity_threshold: float = 0.85,
    ) -> int:
        """Merge near-duplicate nodes to preserve semantic density."""
        node_ids = list(graph.nodes.keys())
        merged_count = 0
        removed = set()

        for i in range(len(node_ids)):
            id1 = node_ids[i]
            if id1 in removed:
                continue
            for j in range(i + 1, len(node_ids)):
                id2 = node_ids[j]
                if id2 in removed:
                    continue

                n1, n2 = graph.nodes[id1], graph.nodes[id2]
                tok1 = _tokenize(f"{n1.title} {n1.content}")
                tok2 = _tokenize(f"{n2.title} {n2.content}")
                sim = _lexical_similarity(tok1, tok2)

                if sim >= similarity_threshold:
                    # Merge n2 into n1
                    n1.use_count += n2.use_count
                    n1.pinned = n1.pinned or n2.pinned
                    for rel in n2.related:
                        if rel not in n1.related and rel != id1:
                            n1.related.append(rel)
                    removed.add(id2)
                    merged_count += 1

        for r_id in removed:
            del graph.nodes[r_id]

        graph.edges = [
            (s, t) for (s, t) in graph.edges if s in graph.nodes and t in graph.nodes
        ]
        return merged_count
