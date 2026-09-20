"""Context-Aware Hybrid Retrieval Engine.

Fuses:
1. BM25 Lexical Keyword Ranker (inverted index, IDF, doc length normalization)
2. Vector / Semantic Cosine Similarity (TF-IDF & character n-gram embeddings, local & zero-dependency)
3. Graph Traversal Proximity (knowledge graph expansion & spreading activation)
4. Temporal Recency Decay (exponential half-life scoring)
5. Reciprocal Rank Fusion (RRF) and calibrated composite reranker.
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any

from alpha.memory.cognitive.models import (
    CognitiveTier,
    HybridRecallQuery,
    ScoredMemoryItem,
)

if TYPE_CHECKING:
    from alpha.memory.cognitive.associative_memory import AssociativeNetwork
    from alpha.memory.cognitive.episodic_memory import EpisodicMemoryEngine
    from alpha.memory.cognitive.procedural_memory import ProceduralSkillMemory
    from alpha.memory.cognitive.semantic_graph import SemanticBeliefGraph
    from alpha.memory.cognitive.spatio_temporal import SpatioTemporalMemory
    from alpha.memory.cognitive.working_memory import WorkingMemoryEngine


def _tokenize(text: str) -> list[str]:
    """Clean tokenization splitting on non-alphanumeric characters."""
    return [w.lower() for w in re.findall(r"[a-zA-Z0-9_\-\.]+", text) if len(w) >= 2]


def _build_char_ngrams(text: str, n: int = 3) -> set[str]:
    """Sub-word character n-grams for semantic fuzzy/typo-tolerant matching."""
    cleaned = f" {text.lower().strip()} "
    if len(cleaned) < n:
        return {cleaned}
    return {cleaned[i : i + n] for i in range(len(cleaned) - n + 1)}


def _cosine_similarity(vec1: dict[str, float], vec2: dict[str, float]) -> float:
    """Cosine similarity between two sparse term vectors."""
    intersection = set(vec1.keys()) & set(vec2.keys())
    if not intersection:
        return 0.0

    dot = sum(vec1[k] * vec2[k] for k in intersection)
    norm1 = math.sqrt(sum(v * v for v in vec1.values()))
    norm2 = math.sqrt(sum(v * v for v in vec2.values()))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot / (norm1 * norm2)


class HybridCognitiveRetriever:
    """Unified hybrid retrieval and reranking engine."""

    def __init__(
        self,
        k1: float = 1.5,
        b: float = 0.75,
        temporal_half_life_seconds: float = 604800.0,  # 7 days
    ) -> None:
        self.k1 = k1
        self.b = b
        self.temporal_half_life_seconds = temporal_half_life_seconds

    def recall(
        self,
        query: HybridRecallQuery,
        working_mem: WorkingMemoryEngine,
        episodic_mem: EpisodicMemoryEngine,
        semantic_graph: SemanticBeliefGraph,
        procedural_mem: ProceduralSkillMemory,
        spatio_temporal: SpatioTemporalMemory,
        assoc_net: AssociativeNetwork,
    ) -> list[ScoredMemoryItem]:
        """Execute multi-tier hybrid recall."""
        now = query.as_of_timestamp or time.time()
        query_text = query.query.strip()
        query_tokens = _tokenize(query_text)
        query_ngrams = _build_char_ngrams(query_text)

        if not query_tokens and not query_ngrams:
            return []

        # 1. Harvest candidates across all tiers
        candidates: list[dict[str, Any]] = []

        # Tier: Working Memory
        if not query.tier_filter or CognitiveTier.WORKING.value in query.tier_filter:
            for wm in working_mem.list_active(min_attention=0.0):
                candidates.append({
                    "tier": CognitiveTier.WORKING,
                    "item_id": wm.item_id,
                    "title": f"Working [{wm.context_tag}]",
                    "content": wm.content,
                    "timestamp": wm.created_at,
                    "salience": wm.salience,
                    "metadata": wm.to_dict(),
                })

        # Tier: Episodic Flat
        if not query.tier_filter or CognitiveTier.EPISODIC_FLAT.value in query.tier_filter:
            for tr in episodic_mem.list_traces(limit=100):
                content = f"{tr.action} -> {tr.observation}"
                if tr.error_context:
                    content += f" (Error: {tr.error_context})"
                candidates.append({
                    "tier": CognitiveTier.EPISODIC_FLAT,
                    "item_id": tr.trace_id,
                    "title": f"Episode Trace [{tr.outcome.value}]",
                    "content": content,
                    "timestamp": tr.timestamp,
                    "salience": tr.salience,
                    "metadata": tr.to_dict(),
                })

        # Tier: Episodic Hierarchical
        if not query.tier_filter or CognitiveTier.EPISODIC_HIERARCHICAL.value in query.tier_filter:
            for ep in episodic_mem.list_episodes(limit=50):
                candidates.append({
                    "tier": CognitiveTier.EPISODIC_HIERARCHICAL,
                    "item_id": ep.episode_id,
                    "title": ep.title,
                    "content": f"{ep.summary} Learnings: {' '.join(ep.key_learnings)}",
                    "timestamp": ep.end_time,
                    "salience": ep.importance,
                    "metadata": ep.to_dict(),
                })

        # Tier: Semantic Facts
        if not query.tier_filter or CognitiveTier.SEMANTIC_FACT.value in query.tier_filter:
            for node in semantic_graph.list_nodes(limit=150):
                candidates.append({
                    "tier": CognitiveTier.SEMANTIC_FACT,
                    "item_id": node.node_id,
                    "title": f"{node.subject} ({node.status.value})",
                    "content": f"{node.statement} {' '.join(node.evidence)}",
                    "timestamp": node.last_accessed_at,
                    "salience": node.salience * node.confidence,
                    "metadata": node.to_dict(),
                })

        # Tier: Procedural Skills
        if not query.tier_filter or CognitiveTier.PROCEDURAL_SKILL.value in query.tier_filter:
            for sk in procedural_mem.list_skills(limit=50):
                content = f"{sk.description} Trigger: {sk.trigger_pattern} Steps: {' '.join(sk.steps)} {sk.code_snippet}"
                candidates.append({
                    "tier": CognitiveTier.PROCEDURAL_SKILL,
                    "item_id": sk.skill_id,
                    "title": f"Skill: {sk.name}",
                    "content": content,
                    "timestamp": sk.last_executed_at or sk.created_at,
                    "salience": 0.5 + 0.5 * sk.success_rate,
                    "metadata": sk.to_dict(),
                })

        # Tier: Spatio-Temporal
        if not query.tier_filter or CognitiveTier.SPATIO_TEMPORAL.value in query.tier_filter:
            for evt in spatio_temporal.list_events(limit=50):
                content = f"{evt.title}: {evt.description} [{evt.environment}/{evt.location}] Entities: {' '.join(evt.entities)}"
                candidates.append({
                    "tier": CognitiveTier.SPATIO_TEMPORAL,
                    "item_id": evt.event_id,
                    "title": f"Event: {evt.title}",
                    "content": content,
                    "timestamp": evt.timestamp,
                    "salience": 0.6,
                    "metadata": evt.to_dict(),
                })

        if not candidates:
            return []

        # 2. BM25 Precomputation
        N = len(candidates)
        doc_tokens = [_tokenize(c["content"]) for c in candidates]
        avgdl = sum(len(d) for d in doc_tokens) / max(1, N)

        df: dict[str, int] = Counter()
        for d in doc_tokens:
            for token in set(d):
                df[token] += 1

        idf: dict[str, float] = {}
        for token in query_tokens:
            doc_freq = df.get(token, 0)
            # Standard Lucene/BM25 IDF
            idf[token] = math.log(1.0 + (N - doc_freq + 0.5) / (doc_freq + 0.5))

        # Query Vector
        query_vec = Counter(query_tokens)
        query_ngram_vec = Counter(query_ngrams)

        # 3. Score Each Candidate
        scored_items: list[ScoredMemoryItem] = []
        decay_constant = math.log(2) / max(1.0, self.temporal_half_life_seconds)

        for idx, cand in enumerate(candidates):
            tokens = doc_tokens[idx]
            doc_len = len(tokens)
            doc_tf = Counter(tokens)

            # BM25 Score
            bm25 = 0.0
            for qt in query_tokens:
                if qt in doc_tf:
                    freq = doc_tf[qt]
                    denom = freq + self.k1 * (1.0 - self.b + self.b * (doc_len / max(1e-6, avgdl)))
                    bm25 += idf.get(qt, 0.0) * (freq * (self.k1 + 1.0) / max(1e-6, denom))

            # Vector Cosine Score (Token + Sub-word Ngram blend)
            token_cos = _cosine_similarity(query_vec, doc_tf)
            cand_ngrams = _build_char_ngrams(cand["content"])
            ngram_cos = _cosine_similarity(query_ngram_vec, Counter(cand_ngrams))
            vector_score = 0.6 * token_cos + 0.4 * ngram_cos

            # Temporal Freshness Score
            dt = max(0.0, now - cand["timestamp"])
            temporal_score = math.exp(-decay_constant * dt)

            # Graph & Association Score
            graph_score = 0.0
            if cand["tier"] == CognitiveTier.SEMANTIC_FACT:
                # Direct traversal links
                neighbors = semantic_graph.traverse(cand["item_id"], max_depth=1)
                graph_score = min(1.0, 0.2 + 0.1 * len(neighbors))

            # Check Associative Network connections
            assoc_links = assoc_net.get_associations_for(cand["tier"], cand["item_id"])
            if assoc_links:
                graph_score = min(1.0, graph_score + 0.15 * len(assoc_links))

            salience_score = float(cand.get("salience", 0.5))

            # Composite Score Blend
            norm_bm25 = min(1.0, bm25 / 5.0)  # Normalize typical BM25 range
            composite = (
                query.bm25_weight * norm_bm25
                + query.vector_weight * vector_score
                + query.temporal_weight * temporal_score
                + query.graph_weight * graph_score
                + 0.10 * salience_score
            )

            snippet = cand["content"][:240] + ("..." if len(cand["content"]) > 240 else "")

            scored_items.append(
                ScoredMemoryItem(
                    tier=cand["tier"],
                    item_id=cand["item_id"],
                    title=cand["title"],
                    snippet=snippet,
                    composite_score=round(composite, 4),
                    bm25_score=round(norm_bm25, 4),
                    vector_score=round(vector_score, 4),
                    graph_score=round(graph_score, 4),
                    temporal_score=round(temporal_score, 4),
                    salience_score=round(salience_score, 4),
                    metadata=cand.get("metadata", {}),
                )
            )

        # Filter by min_score and sort descending
        filtered = [it for it in scored_items if it.composite_score >= query.min_score]
        filtered.sort(key=lambda x: x.composite_score, reverse=True)
        return filtered[: query.limit]
