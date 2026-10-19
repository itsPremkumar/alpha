"""Honest memory consolidation with provenance (bot plan 4.2).

Replaces the fabricated arithmetic path in ``perpetual/memory_consolidator.py``
with a real consolidation surface: it reads genuine episodic traces, extracts
facts through a caller-supplied extractor, and writes each fact with full
provenance (``{fact, source_trace_ids, extractor_model, confidence,
created_at}``), deduplicating against the existing fact set and appending
every written fact to a "dream journal" JSONL.

The module owns the *shape* and *honesty* of the result, never the model
call: the extractor is injected (like the swarm judge's ``judge_invoke``) so
this module never opens a model call itself, and the Gateway owns wiring.

Honesty rules, pinned by tests:

* **Zeros are real zeros.** ``traces_read`` is the count of traces actually
  passed in; ``facts_written`` is the count actually written; ``facts_rejected``
  is the count actually rejected. No arithmetic, no estimates.
* **No extractor, no facts.** Without a wired extractor the run reports
  ``status: unavailable`` with zero writes and a named reason. It never
  synthesizes facts.
* **Failed extraction writes nothing.** If the extractor raises, the run
  reports ``status: failed`` with zero writes and the exception's reason.
* **Every fact cites a real trace.** A fact whose ``source_trace_ids`` do not
  all exist in the input traces is rejected and counted in
  ``facts_rejected`` — it is never written.
* **Dedupe is honest.** A fact whose text already exists in the provided
  ``existing_facts`` (or in the facts written earlier this run) is rejected
  as a duplicate, not silently dropped.
* **Opt-in only.** No daemon, no background loop. The caller decides when to
  run. ``memory.consolidation`` defaults to ``false``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ConsolidatedFact:
    """One fact written by consolidation, with full provenance."""

    fact: str
    source_trace_ids: tuple[str, ...]
    extractor_model: str
    confidence: float
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact": self.fact,
            "source_trace_ids": list(self.source_trace_ids),
            "extractor_model": self.extractor_model,
            "confidence": self.confidence,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConsolidatedFact:
        return cls(
            fact=str(data.get("fact", "")),
            source_trace_ids=tuple(str(t) for t in data.get("source_trace_ids", [])),
            extractor_model=str(data.get("extractor_model", "")),
            confidence=float(data.get("confidence", 0.0)),
            created_at=str(data.get("created_at", "")),
        )


#: Callable type for the extractor.  Takes the list of traces and the model
#: name; returns a list of (fact_text, source_trace_ids, confidence) tuples.
ExtractorFn = Callable[[list[Any], str], list[tuple[str, list[str], float]]]


def _trace_id(trace: Any) -> str:
    """Extract the trace id from a trace object or dict."""
    if isinstance(trace, dict):
        return str(trace.get("trace_id") or trace.get("id") or "")
    return str(getattr(trace, "trace_id", None) or getattr(trace, "id", None) or "")


def consolidate(
    *,
    traces: Sequence[Any] = (),
    extractor: ExtractorFn | None = None,
    extractor_model: str = "",
    journal_path: Path | None = None,
    existing_facts: Sequence[ConsolidatedFact] = (),
    now: str | None = None,
) -> dict[str, Any]:
    """Run one honest consolidation pass.

    Args:
        traces: The episodic traces to consolidate.  Each must carry a
            ``trace_id`` (attribute or dict key).
        extractor: Caller-supplied extractor.  Takes ``(traces, model_name)``
            and returns a list of ``(fact_text, source_trace_ids, confidence)``
            tuples.  ``None`` means "no extractor wired" — the run reports
            ``unavailable`` with zero writes.
        extractor_model: The model name recorded on each written fact.
        journal_path: If provided, written facts are appended as JSONL.
        existing_facts: Facts already in the store, used for dedup.
        now: ISO-8601 timestamp for ``created_at``.  Defaults to UTC now.

    Returns:
        ``{status, traces_read, facts_written, facts_rejected, sources}``
    """
    traces = list(traces)
    traces_read = len(traces)
    valid_trace_ids = {_trace_id(t) for t in traces if _trace_id(t)}

    if extractor is None:
        return {
            "status": "unavailable",
            "traces_read": traces_read,
            "facts_written": 0,
            "facts_rejected": 0,
            "sources": [],
            "reason": "No consolidation extractor is wired: no traces were read and no facts were written.",
        }

    # Extract facts — a failure writes nothing.
    try:
        raw_facts = extractor(traces, extractor_model)
    except Exception as exc:
        logger.exception("Memory consolidation extraction failed")
        return {
            "status": "failed",
            "traces_read": traces_read,
            "facts_written": 0,
            "facts_rejected": 0,
            "sources": [],
            "reason": f"Extraction failed: {exc}",
        }

    if now is None:
        now = datetime.now(UTC).isoformat()

    existing_texts = {f.fact for f in existing_facts}
    written: list[ConsolidatedFact] = []
    facts_rejected = 0
    rejected_reasons: list[str] = []

    for item in raw_facts:
        # Each item is (fact_text, source_trace_ids, confidence)
        if not isinstance(item, (tuple, list)) or len(item) < 2:
            facts_rejected += 1
            rejected_reasons.append("malformed extraction result")
            continue

        fact_text = str(item[0])
        source_trace_ids = [str(t) for t in item[1]]
        confidence = float(item[2]) if len(item) > 2 else 0.0

        # Validate: every cited trace must exist.
        if not source_trace_ids or not all(tid in valid_trace_ids for tid in source_trace_ids):
            facts_rejected += 1
            rejected_reasons.append(f"fact cites unknown trace id(s): {source_trace_ids}")
            continue

        # Dedupe: against existing facts and facts written earlier this run.
        if fact_text in existing_texts:
            facts_rejected += 1
            rejected_reasons.append(f"duplicate fact: {fact_text}")
            continue

        fact = ConsolidatedFact(
            fact=fact_text,
            source_trace_ids=tuple(source_trace_ids),
            extractor_model=extractor_model,
            confidence=confidence,
            created_at=now,
        )
        written.append(fact)
        existing_texts.add(fact_text)

    # Append to dream journal.
    if journal_path is not None and written:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with journal_path.open("a", encoding="utf-8") as handle:
            for fact in written:
                handle.write(json.dumps(fact.to_dict(), ensure_ascii=False) + "\n")
                handle.flush()

    return {
        "status": "completed",
        "traces_read": traces_read,
        "facts_written": len(written),
        "facts_rejected": facts_rejected,
        "sources": [f.to_dict() for f in written],
        "rejected_reasons": rejected_reasons,
    }


__all__ = ["ConsolidatedFact", "ExtractorFn", "consolidate"]
