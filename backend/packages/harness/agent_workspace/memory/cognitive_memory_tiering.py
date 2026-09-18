"""Cognitive Memory Consolidation and Tiered Storage Engine.

A three-tier cognitive architecture modeled on human memory:

1. **Working memory** - an ephemeral, capacity-bounded volatile scratchpad
   holding intermediate plan steps. Items expire on a TTL and are evicted
   least-recently-used when capacity is exceeded.
2. **Episodic memory** - an append-only chronological log of task
   trajectories: goals, tool calls, observations, results and outcomes.
3. **Semantic memory** - distilled heuristics, patterns, architecture
   knowledge and invariant rules, produced by the consolidation daemon.

The **autonomous consolidation daemon** compresses long episodic traces into
semantic rules once a context threshold or an idle-cycle threshold is reached.
Retrieval blends BM25 lexical relevance with cosine vector similarity and
applies exponential relevance decay so recent, frequently accessed memories
outrank stale ones.

Exposed agent tools
-------------------
``consolidate_cognitive_memory`` and ``recall_agent_memory``.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from collections import Counter, deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_WORKING_CAPACITY: int = 64
DEFAULT_WORKING_TTL_SEC: float = 1800.0
DEFAULT_EPISODIC_THRESHOLD: int = 24
DEFAULT_IDLE_THRESHOLD_SEC: float = 300.0
DEFAULT_RELEVANCE_HALF_LIFE_SEC: float = 3600.0
BM25_K1: float = 1.5
BM25_B: float = 0.75
DEFAULT_RECALL_LIMIT: int = 10

_TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")

_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "and", "or", "but", "if", "then", "else", "for",
        "to", "of", "in", "on", "at", "by", "with", "from", "is", "was",
        "are", "were", "be", "been", "this", "that", "these", "those",
        "it", "its", "as", "into", "not", "no", "do", "does", "did",
    }
)


class MemoryTier(str, Enum):
    """The three cognitive memory tiers."""

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class EpisodeOutcome(str, Enum):
    """Terminal outcome of an episodic trajectory."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


def tokenize(text: str) -> list[str]:
    """Tokenize ``text`` into lowercase content terms."""
    if not text:
        return []
    return [
        token.lower()
        for token in _TOKEN_PATTERN.findall(text)
        if token.lower() not in _STOPWORDS and len(token) > 1
    ]


def relevance_decay(age_sec: float, half_life_sec: float = DEFAULT_RELEVANCE_HALF_LIFE_SEC) -> float:
    """Exponential relevance decay ``2 ** (-age / half_life)``."""
    if half_life_sec <= 0:
        return 1.0
    return 2.0 ** (-(max(age_sec, 0.0) / half_life_sec))


def cosine_similarity(left: Counter, right: Counter) -> float:
    """Cosine similarity between two token frequency vectors."""
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    if not shared:
        return 0.0
    dot = sum(left[token] * right[token] for token in shared)
    norm_left = math.sqrt(sum(value * value for value in left.values()))
    norm_right = math.sqrt(sum(value * value for value in right.values()))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)


@dataclass
class MemoryItem:
    """A single memory record shared by every tier."""

    memory_id: str
    tier: MemoryTier
    content: str
    created_at: float
    metadata: dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5
    access_count: int = 0
    last_accessed_at: float = 0.0
    tokens: Counter = field(default_factory=Counter)

    def __post_init__(self) -> None:
        """Derive the token vector and initialise access timestamps."""
        if not self.tokens:
            self.tokens = Counter(tokenize(self.content))
        if not self.last_accessed_at:
            self.last_accessed_at = self.created_at

    def touch(self) -> None:
        """Record an access for recency-weighted retrieval."""
        self.access_count += 1
        self.last_accessed_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "tier": self.tier.value,
            "content": self.content,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
            "importance": round(self.importance, 4),
            "access_count": self.access_count,
            "last_accessed_at": self.last_accessed_at,
        }


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------


class WorkingMemory:
    """Ephemeral, capacity-bounded volatile scratchpad."""

    def __init__(self, capacity: int = DEFAULT_WORKING_CAPACITY, ttl_sec: float = DEFAULT_WORKING_TTL_SEC):
        self.capacity = max(int(capacity), 1)
        self.ttl_sec = float(ttl_sec)
        self._items: "deque[MemoryItem]" = deque()
        self._lock = threading.RLock()

    def put(self, content: str, metadata: Optional[dict[str, Any]] = None, importance: float = 0.5) -> MemoryItem:
        """Insert a scratchpad entry, evicting the oldest when full."""
        item = MemoryItem(
            memory_id=f"wm-{uuid.uuid4().hex[:12]}",
            tier=MemoryTier.WORKING,
            content=content,
            created_at=time.time(),
            metadata=dict(metadata or {}),
            importance=importance,
        )
        with self._lock:
            self.expire()
            self._items.append(item)
            while len(self._items) > self.capacity:
                self._items.popleft()
        return item

    def expire(self, now: Optional[float] = None) -> int:
        """Drop entries older than the TTL, returning the count removed."""
        current = time.time() if now is None else now
        removed = 0
        with self._lock:
            while self._items and (current - self._items[0].created_at) > self.ttl_sec:
                self._items.popleft()
                removed += 1
        return removed

    def items(self) -> list[MemoryItem]:
        """Return every live scratchpad entry."""
        with self._lock:
            self.expire()
            return list(self._items)

    def clear(self) -> int:
        """Clear the scratchpad, returning the number of entries dropped."""
        with self._lock:
            count = len(self._items)
            self._items.clear()
            return count

    def __len__(self) -> int:
        """Number of live entries."""
        return len(self.items())


class EpisodicMemory:
    """Append-only chronological log of task trajectories."""

    def __init__(self, max_episodes: int = 5000):
        self.max_episodes = max(int(max_episodes), 1)
        self._episodes: list[MemoryItem] = []
        self._lock = threading.RLock()

    def record(
        self,
        content: str,
        task: str = "",
        tool_calls: Optional[list[dict[str, Any]]] = None,
        outcome: str = EpisodeOutcome.UNKNOWN.value,
        metadata: Optional[dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> MemoryItem:
        """Append one trajectory episode."""
        merged = dict(metadata or {})
        merged.update(
            {
                "task": task,
                "tool_calls": list(tool_calls or []),
                "outcome": outcome,
            }
        )
        episode = MemoryItem(
            memory_id=f"ep-{uuid.uuid4().hex[:12]}",
            tier=MemoryTier.EPISODIC,
            content=content,
            created_at=time.time() if timestamp is None else timestamp,
            metadata=merged,
        )
        with self._lock:
            self._episodes.append(episode)
            if len(self._episodes) > self.max_episodes:
                overflow = len(self._episodes) - self.max_episodes
                self._episodes = self._episodes[overflow:]
        return episode

    def episodes(self, since: Optional[float] = None) -> list[MemoryItem]:
        """Return episodes, optionally filtered by a lower time bound."""
        with self._lock:
            items = list(self._episodes)
        if since is not None:
            items = [item for item in items if item.created_at >= since]
        return items

    def clear(self) -> int:
        """Clear the episodic log, returning the number of episodes dropped."""
        with self._lock:
            count = len(self._episodes)
            self._episodes.clear()
            return count

    def __len__(self) -> int:
        """Number of recorded episodes."""
        with self._lock:
            return len(self._episodes)


@dataclass
class SemanticRule:
    """A distilled heuristic stored in semantic memory."""

    rule_id: str
    statement: str
    category: str
    confidence: float
    support: int
    evidence: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "statement": self.statement,
            "category": self.category,
            "confidence": round(self.confidence, 4),
            "support": self.support,
            "evidence": list(self.evidence),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SemanticMemory:
    """Distilled heuristics, patterns and invariant rules."""

    def __init__(self) -> None:
        self._rules: dict[str, SemanticRule] = {}
        self._lock = threading.RLock()

    def upsert(
        self,
        statement: str,
        category: str,
        confidence: float,
        support: int,
        evidence: Optional[Iterable[str]] = None,
    ) -> SemanticRule:
        """Insert or reinforce a distilled rule.

        Repeated evidence raises confidence asymptotically towards ``1.0`` and
        accumulates support, so a rule distilled from many episodes is trusted
        more than one distilled from a single trace.
        """
        key = _rule_key(statement)
        with self._lock:
            existing = self._rules.get(key)
            if existing is None:
                rule = SemanticRule(
                    rule_id=f"sr-{uuid.uuid4().hex[:12]}",
                    statement=statement,
                    category=category,
                    confidence=max(0.0, min(float(confidence), 1.0)),
                    support=max(int(support), 1),
                    evidence=list(evidence or []),
                )
                self._rules[key] = rule
                return rule

            existing.support += max(int(support), 1)
            existing.confidence = min(
                1.0,
                existing.confidence + (1.0 - existing.confidence) * max(float(confidence), 0.0) * 0.5,
            )
            for item in evidence or []:
                if item not in existing.evidence:
                    existing.evidence.append(item)
            if len(existing.evidence) > 20:
                existing.evidence = existing.evidence[-20:]
            existing.updated_at = time.time()
            return existing

    def rules(self) -> list[SemanticRule]:
        """Return every rule ordered by confidence then support."""
        with self._lock:
            return sorted(
                self._rules.values(),
                key=lambda rule: (rule.confidence, rule.support),
                reverse=True,
            )

    def clear(self) -> int:
        """Clear semantic memory, returning the number of rules dropped."""
        with self._lock:
            count = len(self._rules)
            self._rules.clear()
            return count

    def __len__(self) -> int:
        """Number of distilled rules."""
        with self._lock:
            return len(self._rules)


def _rule_key(statement: str) -> str:
    """Stable identity key for a rule statement."""
    return re.sub(r"\s+", " ", (statement or "").strip().lower())


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


class HybridRetriever:
    """BM25 + cosine hybrid retrieval with exponential relevance decay."""

    def __init__(
        self,
        bm25_weight: float = 0.6,
        cosine_weight: float = 0.4,
        half_life_sec: float = DEFAULT_RELEVANCE_HALF_LIFE_SEC,
    ):
        self.bm25_weight = float(bm25_weight)
        self.cosine_weight = float(cosine_weight)
        self.half_life_sec = float(half_life_sec)

    def bm25(self, query_tokens: list[str], corpus: list[MemoryItem]) -> list[float]:
        """Okapi BM25 scores for ``query_tokens`` against ``corpus``."""
        if not corpus:
            return []
        lengths = [max(sum(item.tokens.values()), 1) for item in corpus]
        average_length = sum(lengths) / len(lengths)
        document_frequency: Counter = Counter()
        for item in corpus:
            for token in set(item.tokens):
                document_frequency[token] += 1

        total_documents = len(corpus)
        scores: list[float] = []
        for index, item in enumerate(corpus):
            score = 0.0
            for token in query_tokens:
                frequency = item.tokens.get(token, 0)
                if not frequency:
                    continue
                df = document_frequency.get(token, 0)
                idf = math.log(1.0 + (total_documents - df + 0.5) / (df + 0.5))
                denominator = frequency + BM25_K1 * (
                    1.0 - BM25_B + BM25_B * (lengths[index] / average_length)
                )
                score += idf * (frequency * (BM25_K1 + 1.0)) / denominator
            scores.append(score)
        return scores

    def rank(
        self,
        query: str,
        corpus: list[MemoryItem],
        limit: int = DEFAULT_RECALL_LIMIT,
        now: Optional[float] = None,
        min_score: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Rank ``corpus`` against ``query`` with decay-weighted hybrid scoring.

        Args:
            query: Natural language query.
            corpus: Candidate memory items.
            limit: Maximum number of hits to return.
            now: Evaluation timestamp override.
            min_score: Minimum hybrid score for a hit to be returned.

        Returns:
            Ranked hit dictionaries containing score components.
        """
        if not corpus:
            return []
        current = time.time() if now is None else now
        query_tokens = tokenize(query)
        query_vector = Counter(query_tokens)
        lexical = self.bm25(query_tokens, corpus)
        max_lexical = max(lexical) if lexical else 0.0

        hits: list[dict[str, Any]] = []
        for index, item in enumerate(corpus):
            normalized_lexical = (lexical[index] / max_lexical) if max_lexical > 0 else 0.0
            semantic = cosine_similarity(query_vector, item.tokens)
            decay = relevance_decay(current - item.created_at, self.half_life_sec)
            recency_boost = 1.0 + math.log1p(item.access_count) * 0.1
            score = (
                self.bm25_weight * normalized_lexical + self.cosine_weight * semantic
            ) * decay * recency_boost * (0.5 + item.importance * 0.5)

            if score < min_score:
                continue
            hits.append(
                {
                    "memory_id": item.memory_id,
                    "tier": item.tier.value,
                    "content": item.content,
                    "score": round(score, 6),
                    "lexical_score": round(normalized_lexical, 6),
                    "semantic_score": round(semantic, 6),
                    "decay": round(decay, 6),
                    "importance": round(item.importance, 4),
                    "created_at": item.created_at,
                    "metadata": dict(item.metadata),
                }
            )

        hits.sort(key=lambda entry: entry["score"], reverse=True)
        return hits[: max(int(limit), 0)] if limit else hits


# ---------------------------------------------------------------------------
# Consolidation
# ---------------------------------------------------------------------------


@dataclass
class ConsolidationReport:
    """Outcome of one consolidation cycle."""

    triggered: bool = False
    reason: str = ""
    episodes_scanned: int = 0
    episodes_compacted: int = 0
    rules_created: int = 0
    rules_updated: int = 0
    rules: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "reason": self.reason,
            "episodes_scanned": self.episodes_scanned,
            "episodes_compacted": self.episodes_compacted,
            "rules_created": self.rules_created,
            "rules_updated": self.rules_updated,
            "rules": list(self.rules),
            "elapsed_sec": round(self.elapsed_sec, 6),
        }


class CognitiveMemoryConsolidator:
    """Autonomous daemon compressing episodic traces into semantic rules.

    Trigger policy: run when the number of episodes accumulated since the last
    cycle reaches ``episodic_threshold``, or when the memory system has been
    idle for longer than ``idle_threshold_sec``.
    """

    def __init__(
        self,
        episodic: EpisodicMemory,
        semantic: SemanticMemory,
        episodic_threshold: int = DEFAULT_EPISODIC_THRESHOLD,
        idle_threshold_sec: float = DEFAULT_IDLE_THRESHOLD_SEC,
        min_support: int = 2,
    ):
        self.episodic = episodic
        self.semantic = semantic
        self.episodic_threshold = max(int(episodic_threshold), 1)
        self.idle_threshold_sec = float(idle_threshold_sec)
        self.min_support = max(int(min_support), 1)
        self._last_consolidated_at: float = time.time()
        self._last_episode_count: int = 0
        self._last_activity_at: float = time.time()
        self._lock = threading.RLock()

    def should_consolidate(self, force: bool = False, now: Optional[float] = None) -> tuple[bool, str]:
        """Decide whether a consolidation cycle should run.

        Returns:
            ``(decision, reason)`` where reason explains the trigger.
        """
        if force:
            return True, "forced"
        current = time.time() if now is None else now
        pending = len(self.episodic) - self._last_episode_count
        if pending >= self.episodic_threshold:
            return True, f"context threshold reached ({pending} pending episodes)"
        if (current - self._last_activity_at) >= self.idle_threshold_sec and pending > 0:
            return True, f"idle cycle reached ({round(current - self._last_activity_at, 1)}s idle)"
        return False, "thresholds not reached"

    def note_activity(self, now: Optional[float] = None) -> None:
        """Record memory activity, resetting the idle-cycle timer."""
        self._last_activity_at = time.time() if now is None else now

    def consolidate(self, force: bool = False, now: Optional[float] = None) -> ConsolidationReport:
        """Run a consolidation cycle if triggered (or forced).

        Distillation strategy (deterministic, no model calls):

        * **Tool reliability**: for every tool invoked at least ``min_support``
          times, emit a rule describing its observed success rate.
        * **Trajectory patterns**: for every consecutive tool pair observed at
          least ``min_support`` times, emit an ordering rule.
        * **Failure modes**: for tools that always failed, emit an avoidance
          rule.
        """
        started = time.time()
        current = time.time() if now is None else now
        decision, reason = self.should_consolidate(force=force, now=current)
        report = ConsolidationReport(triggered=decision, reason=reason)
        if not decision:
            report.elapsed_sec = time.time() - started
            return report

        with self._lock:
            episodes = self.episodic.episodes()
            report.episodes_scanned = len(episodes)

            tool_totals: Counter = Counter()
            tool_success: Counter = Counter()
            transitions: Counter = Counter()
            outcome_by_tool: dict[str, Counter] = {}

            for episode in episodes:
                calls = episode.metadata.get("tool_calls") or []
                outcome = str(episode.metadata.get("outcome", EpisodeOutcome.UNKNOWN.value))
                names = [
                    str(call.get("tool", call.get("name", "")))
                    for call in calls
                    if isinstance(call, dict)
                ]
                names = [name for name in names if name]
                for name in names:
                    tool_totals[name] += 1
                    outcome_by_tool.setdefault(name, Counter())[outcome] += 1
                    if outcome == EpisodeOutcome.SUCCESS.value:
                        tool_success[name] += 1
                for left, right in zip(names, names[1:]):
                    transitions[(left, right)] += 1

            created = 0
            updated = 0
            before_keys = {_rule_key(rule.statement) for rule in self.semantic.rules()}

            for tool_name, total in tool_totals.items():
                if total < self.min_support:
                    continue
                successes = tool_success.get(tool_name, 0)
                rate = successes / total
                if rate >= 0.8:
                    statement = (
                        f"Tool '{tool_name}' is reliable: {successes}/{total} "
                        f"episodes succeeded ({rate:.0%})."
                    )
                    category = "tool_reliability"
                    confidence = rate
                elif rate <= 0.2:
                    statement = (
                        f"Tool '{tool_name}' is unreliable: only {successes}/{total} "
                        f"episodes succeeded ({rate:.0%}); add guards or prefer an alternative."
                    )
                    category = "failure_mode"
                    confidence = 1.0 - rate
                else:
                    statement = (
                        f"Tool '{tool_name}' has mixed outcomes: {successes}/{total} "
                        f"episodes succeeded ({rate:.0%}); validate results before relying on them."
                    )
                    category = "tool_variance"
                    confidence = 0.5
                evidence = [
                    episode.memory_id
                    for episode in episodes
                    if tool_name
                    in {
                        str(call.get("tool", call.get("name", "")))
                        for call in (episode.metadata.get("tool_calls") or [])
                        if isinstance(call, dict)
                    }
                ][:8]
                self.semantic.upsert(
                    statement=statement,
                    category=category,
                    confidence=confidence,
                    support=total,
                    evidence=evidence,
                )
                if _rule_key(statement) in before_keys:
                    updated += 1
                else:
                    created += 1

            for (left, right), count in transitions.items():
                if count < self.min_support:
                    continue
                statement = (
                    f"Trajectory pattern: '{left}' is commonly followed by "
                    f"'{right}' (observed {count} times)."
                )
                self.semantic.upsert(
                    statement=statement,
                    category="trajectory_pattern",
                    confidence=min(1.0, count / (count + 2.0)),
                    support=count,
                )
                if _rule_key(statement) in before_keys:
                    updated += 1
                else:
                    created += 1

            report.rules_created = created
            report.rules_updated = updated
            report.episodes_compacted = max(len(episodes) - self._last_episode_count, 0)
            report.rules = [rule.statement for rule in self.semantic.rules()[:20]]
            report.elapsed_sec = time.time() - started

            self._last_episode_count = len(episodes)
            self._last_consolidated_at = time.time()
            self._last_activity_at = time.time()
        return report


# ---------------------------------------------------------------------------
# System facade
# ---------------------------------------------------------------------------


class CognitiveMemorySystem:
    """Unified facade over the three tiers, retriever and consolidator."""

    def __init__(
        self,
        working_capacity: int = DEFAULT_WORKING_CAPACITY,
        episodic_threshold: int = DEFAULT_EPISODIC_THRESHOLD,
        idle_threshold_sec: float = DEFAULT_IDLE_THRESHOLD_SEC,
        relevance_half_life_sec: float = DEFAULT_RELEVANCE_HALF_LIFE_SEC,
    ):
        self.working = WorkingMemory(capacity=working_capacity)
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()
        self.retriever = HybridRetriever(half_life_sec=relevance_half_life_sec)
        self.consolidator = CognitiveMemoryConsolidator(
            episodic=self.episodic,
            semantic=self.semantic,
            episodic_threshold=episodic_threshold,
            idle_threshold_sec=idle_threshold_sec,
        )
        self._lock = threading.RLock()

    # -- writing -----------------------------------------------------------

    def remember_working(
        self, content: str, metadata: Optional[dict[str, Any]] = None, importance: float = 0.5
    ) -> MemoryItem:
        """Store an intermediate plan step in working memory."""
        with self._lock:
            return self.working.put(content, metadata, importance)

    def remember_episode(
        self,
        content: str,
        task: str = "",
        tool_calls: Optional[list[dict[str, Any]]] = None,
        outcome: str = EpisodeOutcome.UNKNOWN.value,
        metadata: Optional[dict[str, Any]] = None,
    ) -> MemoryItem:
        """Append a trajectory episode and note consolidation activity."""
        with self._lock:
            episode = self.episodic.record(
                content=content,
                task=task,
                tool_calls=tool_calls,
                outcome=outcome,
                metadata=metadata,
            )
            self.consolidator.note_activity()
            return episode

    def remember_rule(
        self,
        statement: str,
        category: str = "manual",
        confidence: float = 0.75,
        support: int = 1,
    ) -> SemanticRule:
        """Directly inject a distilled rule into semantic memory."""
        with self._lock:
            return self.semantic.upsert(
                statement=statement, category=category, confidence=confidence, support=support
            )

    # -- reading -----------------------------------------------------------

    def _corpus(self, tiers: Optional[list[str]]) -> list[MemoryItem]:
        """Assemble the retrieval corpus for the requested tiers."""
        requested = {str(tier).strip().lower() for tier in tiers} if tiers else {
            tier.value for tier in MemoryTier
        }
        corpus: list[MemoryItem] = []
        if MemoryTier.WORKING.value in requested:
            corpus.extend(self.working.items())
        if MemoryTier.EPISODIC.value in requested:
            corpus.extend(self.episodic.episodes())
        if MemoryTier.SEMANTIC.value in requested:
            corpus.extend(
                MemoryItem(
                    memory_id=rule.rule_id,
                    tier=MemoryTier.SEMANTIC,
                    content=rule.statement,
                    created_at=rule.updated_at,
                    metadata={
                        "category": rule.category,
                        "confidence": rule.confidence,
                        "support": rule.support,
                        "evidence": list(rule.evidence),
                    },
                    importance=rule.confidence,
                )
                for rule in self.semantic.rules()
            )
        return corpus

    def recall(
        self,
        query: str,
        tiers: Optional[list[str]] = None,
        limit: int = DEFAULT_RECALL_LIMIT,
        min_score: float = 0.0,
    ) -> dict[str, Any]:
        """Hybrid recall across the requested tiers with relevance decay."""
        corpus = self._corpus(tiers)
        hits = self.retriever.rank(query, corpus, limit=limit, min_score=min_score)
        for item in corpus:
            if item.memory_id in {hit["memory_id"] for hit in hits}:
                item.touch()
        return {
            "success": True,
            "query": query,
            "tiers": tiers or [tier.value for tier in MemoryTier],
            "count": len(hits),
            "results": hits,
        }

    def consolidate(self, force: bool = False) -> ConsolidationReport:
        """Run the autonomous consolidation daemon."""
        with self._lock:
            return self.consolidator.consolidate(force=force)

    def status(self) -> dict[str, Any]:
        """Return tier occupancy and daemon state."""
        return {
            "working_items": len(self.working),
            "episodic_items": len(self.episodic),
            "semantic_rules": len(self.semantic),
            "episodic_threshold": self.consolidator.episodic_threshold,
            "idle_threshold_sec": self.consolidator.idle_threshold_sec,
            "relevance_half_life_sec": self.retriever.half_life_sec,
            "should_consolidate": self.consolidator.should_consolidate()[0],
        }

    # -- persistence -------------------------------------------------------

    def save(self, path: str | Path) -> str:
        """Persist episodic and semantic memory to a JSON snapshot."""
        payload = {
            "episodes": [item.to_dict() for item in self.episodic.episodes()],
            "rules": [rule.to_dict() for rule in self.semantic.rules()],
        }
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(target)

    def load(self, path: str | Path) -> int:
        """Load a JSON snapshot, returning the number of records restored."""
        source = Path(path)
        if not source.exists():
            return 0
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        restored = 0
        for episode in payload.get("episodes", []) or []:
            self.episodic.record(
                content=str(episode.get("content", "")),
                metadata=dict(episode.get("metadata", {}) or {}),
                timestamp=float(episode.get("created_at", time.time())),
            )
            restored += 1
        for rule in payload.get("rules", []) or []:
            self.semantic.upsert(
                statement=str(rule.get("statement", "")),
                category=str(rule.get("category", "manual")),
                confidence=float(rule.get("confidence", 0.5)),
                support=int(rule.get("support", 1)),
                evidence=list(rule.get("evidence", []) or []),
            )
            restored += 1
        return restored


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------

_SYSTEM_LOCK = threading.Lock()
_SYSTEM: Optional[CognitiveMemorySystem] = None


def get_cognitive_memory_system() -> CognitiveMemorySystem:
    """Return the process-wide cognitive memory system."""
    global _SYSTEM
    with _SYSTEM_LOCK:
        if _SYSTEM is None:
            _SYSTEM = CognitiveMemorySystem()
        return _SYSTEM
