"""Honest memory consolidation with provenance (bot plan 4.2).

Pins the properties the phase exists to guarantee:

1. **Zeros are real zeros.**  Every count reflects actual work.
2. **No extractor, no facts.**  Without a wired extractor the run reports
   ``unavailable`` with zero writes and a named reason.
3. **Failed extraction writes nothing.**  If the extractor raises, the run
   reports ``failed`` with zero writes and the exception's reason.
4. **Every fact cites a real trace.**  A fact whose source_trace_ids do not
   all exist in the input traces is rejected.
5. **Dedupe is honest.**  A duplicate fact is rejected, not silently dropped.
6. **Opt-in only.**  No daemon, no background loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from alpha.memory.consolidation import ConsolidatedFact, consolidate


def _trace(tid: str, **over: Any) -> Any:
    """A minimal trace stand-in carrying a trace_id."""

    class Trace:
        pass

    t = Trace()
    t.trace_id = tid  # type: ignore[attr-defined]
    for k, v in over.items():
        setattr(t, k, v)
    return t


def _extractor(facts: list[tuple[str, list[str], float]]) -> Any:
    """Return an extractor that yields the given facts."""

    def extract(traces: list[Any], model: str) -> list[tuple[str, list[str], float]]:
        return facts

    return extract


def _failing_extractor(exc: Exception) -> Any:
    def extract(traces: list[Any], model: str) -> list[tuple[str, list[str], float]]:
        raise exc

    return extract


# ---------------------------------------------------------------------------
# No extractor
# ---------------------------------------------------------------------------


class TestNoExtractor:
    def test_no_extractor_reports_unavailable_with_zero_writes(self) -> None:
        traces = [_trace("t1"), _trace("t2")]
        result = consolidate(traces=traces, extractor=None)
        assert result["status"] == "unavailable"
        assert result["traces_read"] == 2
        assert result["facts_written"] == 0
        assert result["facts_rejected"] == 0
        assert result["sources"] == []
        assert "reason" in result

    def test_no_traces_no_extractor(self) -> None:
        result = consolidate(traces=[], extractor=None)
        assert result["status"] == "unavailable"
        assert result["traces_read"] == 0
        assert result["facts_written"] == 0


# ---------------------------------------------------------------------------
# Failed extraction
# ---------------------------------------------------------------------------


class TestFailedExtraction:
    def test_failed_extraction_writes_nothing(self) -> None:
        traces = [_trace("t1")]
        result = consolidate(
            traces=traces,
            extractor=_failing_extractor(RuntimeError("model timeout")),
        )
        assert result["status"] == "failed"
        assert result["traces_read"] == 1
        assert result["facts_written"] == 0
        assert result["facts_rejected"] == 0
        assert result["sources"] == []
        assert "model timeout" in result["reason"]

    def test_failed_extraction_no_journal_write(self, tmp_path: Path) -> None:
        journal = tmp_path / "journal.jsonl"
        traces = [_trace("t1")]
        consolidate(
            traces=traces,
            extractor=_failing_extractor(ValueError("bad input")),
            journal_path=journal,
        )
        assert not journal.exists(), "a failed extraction must not write to the journal"


# ---------------------------------------------------------------------------
# Honest counts
# ---------------------------------------------------------------------------


class TestHonestCounts:
    def test_traces_read_is_actual_count(self) -> None:
        traces = [_trace("t1"), _trace("t2"), _trace("t3")]
        result = consolidate(traces=traces, extractor=_extractor([]))
        assert result["traces_read"] == 3

    def test_facts_written_is_actual_count(self) -> None:
        traces = [_trace("t1"), _trace("t2")]
        facts = [("Fact A", ["t1"], 0.9), ("Fact B", ["t2"], 0.8)]
        result = consolidate(traces=traces, extractor=_extractor(facts))
        assert result["facts_written"] == 2
        assert len(result["sources"]) == 2

    def test_empty_extraction_reports_zero_facts(self) -> None:
        traces = [_trace("t1")]
        result = consolidate(traces=traces, extractor=_extractor([]))
        assert result["status"] == "completed"
        assert result["facts_written"] == 0
        assert result["facts_rejected"] == 0
        assert result["sources"] == []

    def test_zero_traces_zero_facts(self) -> None:
        result = consolidate(traces=[], extractor=_extractor([]))
        assert result["traces_read"] == 0
        assert result["facts_written"] == 0


# ---------------------------------------------------------------------------
# Provenance: every fact cites a real trace
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_fact_citing_unknown_trace_is_rejected(self) -> None:
        traces = [_trace("t1")]
        facts = [("Fact A", ["t1", "t999"], 0.9)]
        result = consolidate(traces=traces, extractor=_extractor(facts))
        assert result["facts_written"] == 0
        assert result["facts_rejected"] == 1
        assert result["sources"] == []

    def test_fact_citing_no_traces_is_rejected(self) -> None:
        traces = [_trace("t1")]
        facts = [("Fact A", [], 0.9)]
        result = consolidate(traces=traces, extractor=_extractor(facts))
        assert result["facts_written"] == 0
        assert result["facts_rejected"] == 1

    def test_fact_citing_valid_trace_is_written(self) -> None:
        traces = [_trace("t1"), _trace("t2")]
        facts = [("Fact A", ["t1", "t2"], 0.9)]
        result = consolidate(traces=traces, extractor=_extractor(facts))
        assert result["facts_written"] == 1
        assert result["sources"][0]["source_trace_ids"] == ["t1", "t2"]

    def test_written_fact_carries_full_provenance(self) -> None:
        traces = [_trace("t1")]
        facts = [("Fact A", ["t1"], 0.85)]
        result = consolidate(traces=traces, extractor=_extractor(facts), extractor_model="gpt-4")
        source = result["sources"][0]
        assert source["fact"] == "Fact A"
        assert source["source_trace_ids"] == ["t1"]
        assert source["extractor_model"] == "gpt-4"
        assert source["confidence"] == 0.85
        assert "created_at" in source

    def test_trace_ids_from_dicts(self) -> None:
        traces = [{"trace_id": "d1"}, {"id": "d2"}]
        facts = [("Fact", ["d1", "d2"], 0.5)]
        result = consolidate(traces=traces, extractor=_extractor(facts))
        assert result["facts_written"] == 1


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


class TestDedupe:
    def test_duplicate_fact_is_rejected(self) -> None:
        traces = [_trace("t1"), _trace("t2")]
        existing = [ConsolidatedFact(fact="Existing fact", source_trace_ids=("t1",), extractor_model="m", confidence=0.5, created_at="2026-01-01T00:00:00Z")]
        facts = [("Existing fact", ["t2"], 0.9)]
        result = consolidate(traces=traces, extractor=_extractor(facts), existing_facts=existing)
        assert result["facts_written"] == 0
        assert result["facts_rejected"] == 1

    def test_duplicate_within_same_run_is_rejected(self) -> None:
        traces = [_trace("t1"), _trace("t2")]
        facts = [("Same fact", ["t1"], 0.9), ("Same fact", ["t2"], 0.8)]
        result = consolidate(traces=traces, extractor=_extractor(facts))
        assert result["facts_written"] == 1
        assert result["facts_rejected"] == 1

    def test_distinct_facts_are_both_written(self) -> None:
        traces = [_trace("t1"), _trace("t2")]
        facts = [("Fact A", ["t1"], 0.9), ("Fact B", ["t2"], 0.8)]
        result = consolidate(traces=traces, extractor=_extractor(facts))
        assert result["facts_written"] == 2
        assert result["facts_rejected"] == 0


# ---------------------------------------------------------------------------
# Dream journal
# ---------------------------------------------------------------------------


class TestDreamJournal:
    def test_journal_appends_written_facts(self, tmp_path: Path) -> None:
        journal = tmp_path / "dream.jsonl"
        traces = [_trace("t1")]
        facts = [("Fact A", ["t1"], 0.9)]
        consolidate(traces=traces, extractor=_extractor(facts), journal_path=journal)
        assert journal.exists()
        lines = journal.read_text().strip().splitlines()
        assert len(lines) == 1
        import json

        entry = json.loads(lines[0])
        assert entry["fact"] == "Fact A"
        assert entry["source_trace_ids"] == ["t1"]

    def test_journal_appends_across_runs(self, tmp_path: Path) -> None:
        journal = tmp_path / "dream.jsonl"
        traces1 = [_trace("t1")]
        traces2 = [_trace("t2")]
        consolidate(traces=traces1, extractor=_extractor([("Fact A", ["t1"], 0.9)]), journal_path=journal)
        consolidate(traces=traces2, extractor=_extractor([("Fact B", ["t2"], 0.8)]), journal_path=journal)
        lines = journal.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_no_journal_path_no_file(self, tmp_path: Path) -> None:
        traces = [_trace("t1")]
        facts = [("Fact A", ["t1"], 0.9)]
        consolidate(traces=traces, extractor=_extractor(facts))
        assert not (tmp_path / "dream.jsonl").exists()

    def test_failed_extraction_no_journal(self, tmp_path: Path) -> None:
        journal = tmp_path / "dream.jsonl"
        traces = [_trace("t1")]
        consolidate(
            traces=traces,
            extractor=_failing_extractor(RuntimeError("boom")),
            journal_path=journal,
        )
        assert not journal.exists()

    def test_no_facts_written_no_journal_entry(self, tmp_path: Path) -> None:
        journal = tmp_path / "dream.jsonl"
        traces = [_trace("t1")]
        # All facts rejected
        consolidate(
            traces=traces,
            extractor=_extractor([("Bad", ["t999"], 0.9)]),
            journal_path=journal,
        )
        assert not journal.exists()


# ---------------------------------------------------------------------------
# Config flag
# ---------------------------------------------------------------------------


class TestConfigFlag:
    def test_consolidation_defaults_to_false(self) -> None:
        from pathlib import Path

        config_path = Path(__file__).parent.parent.parent / "config.example.yaml"
        with open(config_path, encoding="utf-8") as handle:
            content = handle.read()

        assert "consolidation: false" in content


# ---------------------------------------------------------------------------
# ConsolidatedFact round-trip
# ---------------------------------------------------------------------------


class TestConsolidatedFact:
    def test_round_trip(self) -> None:
        fact = ConsolidatedFact(
            fact="test fact",
            source_trace_ids=("t1", "t2"),
            extractor_model="gpt-4",
            confidence=0.85,
            created_at="2026-01-01T00:00:00Z",
        )
        rebuilt = ConsolidatedFact.from_dict(fact.to_dict())
        assert rebuilt == fact

    def test_from_dict_missing_fields(self) -> None:
        fact = ConsolidatedFact.from_dict({})
        assert fact.fact == ""
        assert fact.source_trace_ids == ()
        assert fact.confidence == 0.0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
