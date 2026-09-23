"""Knowledge Graph modeling learned codebase habits, memory chunks, and skill relations."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Directed skill relationship graph (WorkSwarm-style skill orchestration).
#
# A separate structure from KnowledgeGraph above: KnowledgeGraph's edges are
# undirected (source, target) tuples serialized by LearningGraphStore, and its
# existing callers (curator, learning_graph_tool, knowledge_graph_tool) must
# keep that shape. SkillGraph adds directed, typed, evidence-tracked edges
# persisted to runtime_home()/skill_graph.json.
#
# Honesty rules baked into this code:
# - Confidence starts at the neutral documented baseline below. It is NEVER
#   derived or inflated from evidence counts; only an explicit caller-supplied
#   validated confidence may set it. No fabricated "0.9 success rates".
# - evidence_count counts recorded observations only (cumulative; the
#   ``evidence`` list retains at most MAX_STORED_EVIDENCE recent provenance
#   strings).
# - Chain suggestion scores are disclosed heuristics (see
#   suggest_chains' note), never measured outcomes.
# ---------------------------------------------------------------------------

#: Allowed edge types for the directed skill relationship graph.
SKILL_EDGE_TYPES: tuple[str, ...] = ("can_feed", "requires", "enhances", "conflicts_with", "alternative_to", "produces")

#: Neutral documented baseline for a newly declared edge's confidence.
BASELINE_CONFIDENCE = 0.5

#: Disclosure attached wherever BASELINE_CONFIDENCE is used.
BASELINE_CONFIDENCE_NOTE = (
    "Confidence starts at the neutral baseline 0.5 whenever an edge is declared without an explicitly "
    "validated confidence; the baseline is a documented default, not a measured success rate, and "
    "confidence is never derived from evidence_count."
)

#: Disclosed scoring method for chain suggestions (lexical/graph heuristic).
SKILL_GRAPH_SCORE_METHOD = "graph-lexical-heuristic"

#: Base disclosure for suggested chains.
SKILL_GRAPH_CHAIN_NOTE = (
    "Chain score = mean edge confidence, blended 50/50 with lexical task-tag overlap when task_tags are given; "
    "edge confidence is the neutral baseline 0.5 unless a caller explicitly supplied a validated value, and "
    "evidence_count counts recorded observations only. Ties break on total evidence, then shorter chains, then "
    "name order. This is a disclosed lexical/graph heuristic ranking, not a measured success rate."
)

#: Cap on retained per-edge provenance strings (evidence_count stays cumulative).
MAX_STORED_EVIDENCE = 50

#: Budget on candidate paths explored by suggest_chains (bounded, disclosed when hit).
MAX_CHAIN_CANDIDATES = 250


@dataclass
class SkillEdge:
    """One directed, typed skill relationship with evidence metadata."""

    source: str
    target: str
    edge_type: str
    confidence: float = BASELINE_CONFIDENCE
    evidence_count: int = 0
    last_validated: float | None = None
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillEdge:
        """Rebuild an edge, validating rather than trusting persisted values."""
        source = str(data["source"]).strip()
        target = str(data["target"]).strip()
        edge_type = str(data["edge_type"])
        if not source or not target or edge_type not in SKILL_EDGE_TYPES:
            raise ValueError(f"invalid skill edge payload: {data!r}")
        confidence = float(data.get("confidence", BASELINE_CONFIDENCE))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"skill edge confidence out of range: {confidence!r}")
        evidence_count = int(data.get("evidence_count", 0))
        if evidence_count < 0:
            raise ValueError("skill edge evidence_count must be >= 0")
        last_validated_raw = data.get("last_validated")
        last_validated = float(last_validated_raw) if last_validated_raw is not None else None
        evidence = [str(item) for item in data.get("evidence", [])]
        return cls(
            source=source,
            target=target,
            edge_type=edge_type,
            confidence=confidence,
            evidence_count=evidence_count,
            last_validated=last_validated,
            evidence=evidence,
        )


def skill_graph_path() -> Path:
    """Persistence location: ``runtime_home()/skill_graph.json``."""
    return runtime_home() / "skill_graph.json"


class SkillGraph:
    """Directed skill relationship graph with evidence-backed edges.

    Edges are keyed by ``(source, target, edge_type)``; several relationship
    types may connect the same skill pair. :meth:`add_edge` only *records*
    evidence -- it never invents or inflates confidence.
    """

    def __init__(self) -> None:
        self._edges: dict[tuple[str, str, str], SkillEdge] = {}

    @property
    def edges(self) -> list[SkillEdge]:
        """All edges in deterministic (source, target, edge_type) order."""
        return [self._edges[key] for key in sorted(self._edges)]

    def add_edge(
        self,
        source: str,
        target: str,
        edge_type: str,
        *,
        evidence: str | None = None,
        confidence: float | None = None,
    ) -> SkillEdge:
        """Declare or re-confirm a directed edge.

        Re-adding the same (source, target, edge_type) with new ``evidence``
        increments ``evidence_count`` and stamps ``last_validated``; without
        evidence the counts stay untouched. ``confidence`` is only ever set
        from the explicit caller-supplied validated value -- observing more
        evidence never changes it. New edges start at ``BASELINE_CONFIDENCE``.
        """
        source = str(source).strip()
        target = str(target).strip()
        if not source or not target:
            raise ValueError("skill edge source and target must be non-empty")
        if source == target:
            raise ValueError("skill self-edges are not allowed")
        if edge_type not in SKILL_EDGE_TYPES:
            raise ValueError(f"unknown edge_type {edge_type!r}; expected one of {SKILL_EDGE_TYPES}")
        if confidence is not None and not 0.0 <= float(confidence) <= 1.0:
            raise ValueError("confidence must be within [0, 1]")

        key = (source, target, edge_type)
        edge = self._edges.get(key)
        if edge is None:
            edge = SkillEdge(
                source=source,
                target=target,
                edge_type=edge_type,
                confidence=BASELINE_CONFIDENCE if confidence is None else float(confidence),
            )
            self._edges[key] = edge
        elif confidence is not None:
            # Explicitly supplied validated confidence only; never derived.
            edge.confidence = float(confidence)

        has_evidence = evidence is not None and bool(str(evidence).strip())
        if has_evidence:
            edge.evidence_count += 1
            edge.last_validated = time.time()
            edge.evidence.append(str(evidence).strip())
            if len(edge.evidence) > MAX_STORED_EVIDENCE:
                del edge.evidence[:-MAX_STORED_EVIDENCE]
        return edge

    def suggest_chains(
        self,
        *,
        start: str | None = None,
        goal: str | None = None,
        task_tags: Any = (),
        max_depth: int = 4,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Suggest candidate skill chains with disclosed heuristic scores.

        Structural mode uses ``start`` and/or ``goal``; tag mode ranks chains
        lexically against ``task_tags`` (chains with no tag overlap are
        dropped). Returns an honest empty ``chains`` list plus an explanatory
        ``note`` when the graph is empty, no path exists, or nothing matched
        the tags -- never a fabricated chain or score.
        """
        if max_depth < 1:
            raise ValueError("max_depth must be >= 1")
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        tags = [str(tag).strip() for tag in (task_tags or ()) if str(tag).strip()]
        base: dict[str, Any] = {
            "score_method": SKILL_GRAPH_SCORE_METHOD,
            "start": start,
            "goal": goal,
            "task_tags": tags,
            "chains": [],
        }

        if not self._edges:
            base["note"] = (
                "The skill relationship graph is empty: no edges have been recorded, so there is nothing to "
                f"chain. {BASELINE_CONFIDENCE_NOTE}"
            )
            return base
        if start is None and goal is None and not tags:
            base["note"] = "No start, goal, or task_tags supplied; pass at least one so a chain can be grounded. The empty chain list is deliberate."
            return base

        paths, truncated = self._enumerate_paths(start=start, goal=goal, max_depth=max_depth)
        tag_tokens = _tokenize(" ".join(tags)) if tags else set()
        candidates: list[dict[str, Any]] = []
        for nodes, edges in paths:
            confidence_mean = sum(edge.confidence for edge in edges) / len(edges)
            tag_similarity = _lexical_similarity(tag_tokens, _tokenize(" ".join(nodes))) if tag_tokens else 0.0
            if tag_tokens and tag_similarity <= 0.0:
                continue
            score = round(0.5 * confidence_mean + 0.5 * tag_similarity, 3) if tag_tokens else round(confidence_mean, 3)
            total_evidence = sum(edge.evidence_count for edge in edges)
            reasons = [
                (
                    f"{edge.source} -{edge.edge_type}-> {edge.target}: confidence {edge.confidence} "
                    f"({'neutral baseline' if edge.confidence == BASELINE_CONFIDENCE else 'explicitly supplied'}), "
                    f"{edge.evidence_count} recorded observation(s)"
                )
                for edge in edges
            ]
            if tag_tokens:
                reasons.append(f"task tags {tags} lexically overlap chain nodes {list(nodes)} (similarity {round(tag_similarity, 3)})")
            candidates.append(
                {
                    "chain": list(nodes),
                    "edges": [edge.to_dict() for edge in edges],
                    "score": score,
                    "total_evidence": total_evidence,
                    "reasons": reasons,
                }
            )

        if not candidates:
            if not paths:
                if start is not None:
                    known = start in {key[0] for key in self._edges} | {key[1] for key in self._edges}
                    base["note"] = (
                        f"No directed chain found from start '{start}' within max_depth={max_depth}"
                        + (" (unknown skill name: not present in the graph)." if not known else " (no recorded edges continue from it).")
                    )
                elif goal is not None:
                    base["note"] = f"No recorded directed chain reaches goal '{goal}' within max_depth={max_depth}."
                else:  # pragma: no cover - defensive; tags branch below handles overlap misses
                    base["note"] = "No candidate chain could be enumerated from the supplied filters."
            else:
                base["note"] = (
                    f"None of the {len(paths)} recorded candidate chain(s) lexically matched the task tags {tags}; "
                    "returning an honest empty list instead of an unrelated chain."
                )
            return base

        candidates.sort(key=lambda item: (-item["score"], -item["total_evidence"], len(item["chain"]), item["chain"]))
        note = SKILL_GRAPH_CHAIN_NOTE
        if truncated:
            note += f" Chain exploration stopped after {MAX_CHAIN_CANDIDATES} candidate paths (budget); the result set may be incomplete."
        base["note"] = note
        base["chains"] = candidates[:top_k]
        return base

    def _enumerate_paths(self, *, start: str | None, goal: str | None, max_depth: int) -> tuple[list[tuple[tuple[str, ...], tuple[SkillEdge, ...]]], bool]:
        """Enumerate simple directed paths (bounded); returns (paths, truncated)."""
        outgoing: dict[str, list[SkillEdge]] = {}
        for key in sorted(self._edges):
            edge = self._edges[key]
            outgoing.setdefault(edge.source, []).append(edge)
        nodes = sorted({part for key in self._edges for part in (key[0], key[1])})
        seeds = [start] if start is not None else nodes
        results: list[tuple[tuple[str, ...], tuple[SkillEdge, ...]]] = []
        truncated = False

        def walk(path_nodes: tuple[str, ...], path_edges: tuple[SkillEdge, ...]) -> None:
            nonlocal truncated
            if len(results) >= MAX_CHAIN_CANDIDATES:
                truncated = True
                return
            node = path_nodes[-1]
            if len(path_nodes) >= 2 and (goal is None or node == goal):
                results.append((path_nodes, path_edges))
            if goal is not None and node == goal:
                return  # do not extend past a reached goal
            if len(path_edges) >= max_depth:
                return
            for edge in outgoing.get(node, ()):
                if edge.target in path_nodes:
                    continue  # simple paths only
                walk(path_nodes + (edge.target,), path_edges + (edge,))

        for seed in seeds:
            if truncated:
                break
            walk((seed,), ())
        return results, truncated

    def to_dict(self) -> dict[str, Any]:
        """Honest snapshot payload for API responses."""
        edges = self.edges
        note = BASELINE_CONFIDENCE_NOTE
        if not edges:
            note = "The skill relationship graph is empty: no edges have been recorded, so no relationships are claimed. " + BASELINE_CONFIDENCE_NOTE
        return {
            "score_method": SKILL_GRAPH_SCORE_METHOD,
            "edge_types": list(SKILL_EDGE_TYPES),
            "baseline_confidence": BASELINE_CONFIDENCE,
            "note": note,
            "edge_count": len(edges),
            "edges": [edge.to_dict() for edge in edges],
        }

    def save(self, path: Path | None = None) -> Path:
        """Atomically persist to *path* (default ``runtime_home()/skill_graph.json``).

        Writes a temp file in the target directory, fsyncs, then ``os.replace``
        so readers never observe a half-written graph.
        """
        target = Path(path) if path is not None else skill_graph_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "edges": [edge.to_dict() for edge in self.edges]}
        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:  # pragma: no cover - best-effort cleanup
                pass
            raise
        return target

    @classmethod
    def load(cls, path: Path | None = None) -> SkillGraph:
        """Load a graph; missing file -> empty graph, corrupt file -> empty graph (warned)."""
        target = Path(path) if path is not None else skill_graph_path()
        graph = cls()
        if not target.exists():
            return graph
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("skill graph at %s could not be read (%s); starting from an empty graph", target, exc)
            return graph
        for raw in payload.get("edges", []):
            try:
                edge = SkillEdge.from_dict(raw)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("skipping invalid skill edge in %s: %r (%s)", target, raw, exc)
                continue
            graph._edges[(edge.source, edge.target, edge.edge_type)] = edge
        return graph


def load_skill_graph(path: Path | None = None) -> SkillGraph:
    """Load the persisted skill relationship graph (see :meth:`SkillGraph.load`)."""
    return SkillGraph.load(path)
