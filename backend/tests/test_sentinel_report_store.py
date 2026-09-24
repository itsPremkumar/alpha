"""Fail-closed tests for the durable Sentinel report journal.

Invariants under test (mirrors ``test_scheduler_job_memory`` discipline):
* append is durable (flush + fsync) and preserves real UTF-8 output verbatim;
* a corrupt, non-UTF-8, or schema-violating line raises a typed error naming
  the file and the 1-based line number — bytes are never repaired or skipped;
* an unreadable journal never reads as an empty history;
* capped reads disclose the cap, the total on disk, and the source path;
* invalid entries are rejected before any byte is written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha.runtime.sentinel.report_store import (
    ReportHistory,
    ReportStoreCorruptError,
    ReportStoreReadError,
    ReportStoreWriteError,
    SentinelReportStore,
    default_sentinel_report_store,
)


def _entry(recorded_at: str = "2026-09-24T00:00:00+00:00", **overrides) -> dict:
    entry = {
        "recorded_at": recorded_at,
        "trigger": "test",
        "auto_heal": False,
        "report": {"duration_s": 0.001, "scanned": 0, "summary": "scanned 0 signal(s)", "fixed": 0, "reverted": 0, "escalated": 0, "errors": [], "outcomes": []},
    }
    entry.update(overrides)
    return entry


def test_append_then_read_roundtrip_preserves_utf8_and_is_durable(tmp_path: Path) -> None:
    store = SentinelReportStore(tmp_path / "sentinel-reports")
    entry = _entry(report={"scanned": 1, "summary": "scanned 1 signal(s): 0 fixed — non-ASCII ✓ intact", "errors": ["résumé: файл"]})

    store.append(entry)

    # A brand-new instance (fresh handle, as after a restart) reads the same bytes.
    reread = SentinelReportStore(tmp_path / "sentinel-reports").read_all()
    assert reread == [entry]
    assert "✓" in reread[0]["report"]["summary"]
    assert "файл" in reread[0]["report"]["errors"][0]
    # Written as UTF-8, one JSON object per line.
    raw = (tmp_path / "sentinel-reports" / "reports.jsonl").read_bytes()
    assert raw.endswith(b"\n")
    assert json.loads(raw.decode("utf-8").splitlines()[0])["recorded_at"] == entry["recorded_at"]


def test_missing_journal_reads_as_empty_and_discloses_absence(tmp_path: Path) -> None:
    store = SentinelReportStore(tmp_path / "sentinel-reports")

    assert store.read_all() == []
    history = store.history(limit=10)
    assert history.entries == ()
    assert history.total == 0
    assert history.absent is True
    disclosures = history.disclosures()
    assert any("absent — no pass has been recorded yet" in line for line in disclosures)
    assert any("no passes recorded yet" in line for line in disclosures)


def test_corrupt_json_line_fails_closed_naming_file_and_line_and_never_repairs(tmp_path: Path) -> None:
    store = SentinelReportStore(tmp_path / "sentinel-reports")
    store.append(_entry(recorded_at="2026-09-24T00:00:00+00:00"))
    journal = tmp_path / "sentinel-reports" / "reports.jsonl"
    with journal.open("ab") as handle:
        handle.write(b'{"recorded_at": "2026-09-24T00:00:01+00:00", "report": {truncated\n')
    before = journal.read_bytes()

    with pytest.raises(ReportStoreCorruptError) as exc_info:
        store.read_all()

    message = str(exc_info.value)
    assert "reports.jsonl" in message
    assert "line 2" in message
    assert "invalid JSON" in message
    # Bytes are never repaired, skipped, or rewritten by a failed read.
    assert journal.read_bytes() == before


def test_non_utf8_line_fails_closed(tmp_path: Path) -> None:
    journal = tmp_path / "sentinel-reports"
    journal.mkdir(parents=True)
    path = journal / "reports.jsonl"
    path.write_bytes(b'{"recorded_at": "\xff\xfe", "report": {}}\n')

    with pytest.raises(ReportStoreCorruptError) as exc_info:
        SentinelReportStore(journal).read_all()

    assert "line 1" in str(exc_info.value)
    assert "not valid UTF-8" in str(exc_info.value)


@pytest.mark.parametrize(
    ("entry", "fragment"),
    [
        ("not a dict", "must be a dict"),
        ({"recorded_at": "", "report": {}}, "'recorded_at'"),
        ({"report": {}}, "'recorded_at'"),
        ({"recorded_at": "2026-09-24T00:00:00+00:00"}, "'report'"),
        ({"recorded_at": "2026-09-24T00:00:00+00:00", "report": []}, "'report'"),
        (_entry(trigger=""), "'trigger'"),
        (_entry(auto_heal="yes"), "'auto_heal'"),
    ],
)
def test_invalid_entries_are_rejected_before_any_byte_is_written(tmp_path: Path, entry, fragment: str) -> None:
    store = SentinelReportStore(tmp_path / "sentinel-reports")

    with pytest.raises(ReportStoreWriteError) as exc_info:
        store.append(entry)

    assert fragment in str(exc_info.value)
    assert not (tmp_path / "sentinel-reports" / "reports.jsonl").exists()


def test_non_serializable_report_is_rejected_without_partial_write(tmp_path: Path) -> None:
    store = SentinelReportStore(tmp_path / "sentinel-reports")

    with pytest.raises(ReportStoreWriteError) as exc_info:
        store.append(_entry(report={"scanned": object()}))

    assert "not JSON-serializable" in str(exc_info.value)
    assert not (tmp_path / "sentinel-reports" / "reports.jsonl").exists()


def test_capped_history_returns_newest_entries_in_journal_order_with_disclosures(tmp_path: Path) -> None:
    store = SentinelReportStore(tmp_path / "sentinel-reports")
    for index in range(5):
        store.append(_entry(recorded_at=f"2026-09-24T00:00:0{index}+00:00", report={"scanned": index}))

    history = store.history(limit=2)

    assert isinstance(history, ReportHistory)
    assert history.total == 5
    assert history.cap == 2
    assert [entry["report"]["scanned"] for entry in history.entries] == [3, 4]
    assert history.absent is False
    assert any("newest 2 of 5 recorded pass(es)" in line for line in history.disclosures())
    assert any("never rewritten" in line for line in history.disclosures())
    assert history.source.endswith("reports.jsonl")

    uncapped = store.history()
    assert uncapped.cap is None
    assert uncapped.total == 5
    assert len(uncapped.entries) == 5
    assert any("all 5 recorded pass(es)" in line for line in uncapped.disclosures())


def test_read_limit_below_one_fails_closed(tmp_path: Path) -> None:
    store = SentinelReportStore(tmp_path / "sentinel-reports")

    with pytest.raises(ReportStoreReadError) as exc_info:
        store.history(limit=0)

    assert "limit must be >= 1" in str(exc_info.value)


def test_schema_violating_line_fails_closed_with_line_number(tmp_path: Path) -> None:
    journal = tmp_path / "sentinel-reports"
    journal.mkdir(parents=True)
    path = journal / "reports.jsonl"
    path.write_text('["recorded_at", "looks like a list"]\n', encoding="utf-8")

    with pytest.raises(ReportStoreCorruptError) as exc_info:
        SentinelReportStore(journal).read_all()
    assert "expected a JSON object" in str(exc_info.value)
    assert "line 1" in str(exc_info.value)

    path.write_text('{"recorded_at": "2026-09-24T00:00:00+00:00"}\n', encoding="utf-8")
    with pytest.raises(ReportStoreCorruptError) as exc_info:
        SentinelReportStore(journal).read_all()
    assert "'report' field" in str(exc_info.value)


def test_default_store_root_follows_runtime_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(home))

    store = default_sentinel_report_store()

    assert store.path == str(home / "sentinel-reports" / "reports.jsonl")
    store.append(_entry())
    assert (home / "sentinel-reports" / "reports.jsonl").is_file()
