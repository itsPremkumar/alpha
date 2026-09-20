"""3-Phase Dreaming Memory Consolidation Phases (Light Sleep, REM, Deep Sleep)."""

from __future__ import annotations

import datetime
import hashlib
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from alpha.memory.dreaming.store import DreamStore, get_dream_store


@dataclass
class MemorySignal:
    """Raw short-term observation or interaction signal."""

    content: str
    source: str = "agent_run"
    category: str = "general"
    timestamp: float = 0.0
    signal_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


@dataclass
class ConsolidationInsight:
    """Synthesized observation or durable rule extracted from signals."""

    title: str
    summary: str
    category: str
    importance_score: float  # 0.0 to 1.0
    signals: list[str] = field(default_factory=list)
    insight_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    promoted_to_memory: bool = False


@dataclass
class DreamReport:
    """Complete summary of a 3-phase dreaming consolidation run."""

    cycle_id: str
    timestamp: str
    signals_processed: int
    insights_generated: list[ConsolidationInsight]
    promoted_count: int
    dreams_markdown: str
    memory_updates: list[str]


def run_light_sleep_phase(signals: Sequence[MemorySignal]) -> list[MemorySignal]:
    """Phase 1: Light Sleep — Ingest, clean, normalize, and deduplicate signals."""
    seen_hashes = set()
    cleaned: list[MemorySignal] = []

    for s in signals:
        text = s.content.strip()
        if not text:
            continue
        # Deduplication hash on normalized content
        norm = re.sub(r"\s+", " ", text.lower())
        h = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
        if h in seen_hashes:
            continue
        seen_hashes.add(h)

        cleaned.append(
            MemorySignal(
                signal_id=s.signal_id,
                content=text,
                source=s.source,
                category=s.category or "general",
                timestamp=s.timestamp,
            )
        )
    return cleaned


def run_rem_sleep_phase(signals: Sequence[MemorySignal]) -> list[ConsolidationInsight]:
    """Phase 2: REM Sleep — Synthesize patterns, recurring themes, and calculate importance scores."""
    # Group by category
    buckets: dict[str, list[MemorySignal]] = {}
    for s in signals:
        buckets.setdefault(s.category, []).append(s)

    insights: list[ConsolidationInsight] = []

    for category, group in buckets.items():
        if not group:
            continue

        # Check for error patterns
        error_signals = [s for s in group if "error" in s.content.lower() or "fail" in s.content.lower()]
        if error_signals:
            summary = " | ".join(s.content for s in error_signals[:3])
            score = min(1.0, 0.65 + 0.1 * len(error_signals))
            insights.append(
                ConsolidationInsight(
                    title=f"Recurring Failure Pattern in [{category}]",
                    summary=summary,
                    category=category,
                    importance_score=score,
                    signals=[s.signal_id for s in error_signals],
                )
            )

        # Standard knowledge synthesis
        standard_signals = [s for s in group if s not in error_signals]
        if standard_signals:
            summary = " | ".join(s.content for s in standard_signals[:3])
            # Higher density of signals raises importance
            score = min(1.0, 0.50 + 0.1 * min(5, len(standard_signals)))
            insights.append(
                ConsolidationInsight(
                    title=f"Consolidated Knowledge in [{category}]",
                    summary=summary,
                    category=category,
                    importance_score=score,
                    signals=[s.signal_id for s in standard_signals],
                )
            )

    return insights


def run_deep_sleep_phase(
    insights: Sequence[ConsolidationInsight],
    threshold: float = 0.70,
) -> tuple[list[ConsolidationInsight], list[ConsolidationInsight]]:
    """Phase 3: Deep Sleep — Evaluate scoring gates and promote durable principles to MEMORY.md."""
    promoted: list[ConsolidationInsight] = []
    retained: list[ConsolidationInsight] = []

    for ins in insights:
        if ins.importance_score >= threshold:
            ins.promoted_to_memory = True
            promoted.append(ins)
        else:
            retained.append(ins)

    return promoted, retained


def run_dream_cycle(
    signals: Sequence[MemorySignal],
    store: DreamStore | None = None,
    threshold: float = 0.70,
) -> DreamReport:
    """Execute complete 3-phase dreaming consolidation cycle."""
    store = store or get_dream_store()
    cycle_id = uuid.uuid4().hex[:8]
    now_str = datetime.datetime.now(datetime.UTC).isoformat()

    # 1. Light Sleep
    cleaned_signals = run_light_sleep_phase(signals)

    # 2. REM Sleep
    insights = run_rem_sleep_phase(cleaned_signals)

    # 3. Deep Sleep
    promoted, retained = run_deep_sleep_phase(insights, threshold=threshold)

    # Format dream journal markdown
    journal_lines = [
        f"### Dream Cycle `{cycle_id}` ({now_str})",
        f"- **Processed Signals**: {len(signals)} raw -> {len(cleaned_signals)} deduplicated",
        f"- **Synthesized Insights**: {len(insights)}",
        f"- **Promoted to MEMORY.md**: {len(promoted)} (score >= {threshold})",
        "",
        "#### Observations:",
    ]
    for ins in insights:
        status_tag = "[PROMOTED]" if ins.promoted_to_memory else "[OBSERVED]"
        journal_lines.append(f"- **{status_tag} {ins.title}** (score: {ins.importance_score:.2f}): {ins.summary}")

    dream_md = "\n".join(journal_lines)

    # Commit to store
    memory_updates = []
    for p in promoted:
        rule_text = f"[{p.category.upper()}] {p.title}: {p.summary}"
        store.append_memory(rule_text)
        memory_updates.append(rule_text)

    store.append_dream(dream_md)

    return DreamReport(
        cycle_id=cycle_id,
        timestamp=now_str,
        signals_processed=len(cleaned_signals),
        insights_generated=list(insights),
        promoted_count=len(promoted),
        dreams_markdown=dream_md,
        memory_updates=memory_updates,
    )
