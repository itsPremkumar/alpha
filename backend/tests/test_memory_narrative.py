"""Hermetic contract tests for Alpha's narrative-memory subsystem."""

from __future__ import annotations

import ast
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from alpha.memory.narrative import (
    IngestStatus,
    NarrativeConfig,
    NarrativeEvent,
    NarrativeMemory,
    NarrativeStore,
    StoryDocument,
    SynthesisStatus,
    build_chapters,
    choose_bucket,
    events_path,
    parse_model_response,
    read_entries,
    story_path,
)


class FakeModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def invoke(self, prompt: str, config: dict[str, Any] | None = None) -> str:
        self.prompts.append(prompt)
        return self.response


class BrokenModel:
    def invoke(self, prompt: str, config: dict[str, Any] | None = None) -> str:
        raise RuntimeError("fake model unavailable")


def make_config(tmp_path: Path, **overrides: Any) -> NarrativeConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "storage_path": str(tmp_path / "narrative"),
        "max_events": 20,
        "max_chars": 2_000,
        "max_chapters": 12,
        "min_importance": 0.0,
        "lookback_days": 100_000.0,
    }
    values.update(overrides)
    return NarrativeConfig(**values)


def make_event(
    event_id: str,
    start: float,
    *,
    title: str = "A project milestone",
    summary: str = "The team made a durable change",
    outcomes: list[str] | None = None,
    importance: float = 80.0,
    scope: str = "user",
) -> NarrativeEvent:
    return NarrativeEvent(
        id=event_id,
        scope=scope,
        period_start=start,
        period_end=start,
        title=title,
        summary=summary,
        outcomes=outcomes or ["shipped"],
        importance=importance,
        source_refs=[f"source-{event_id}"],
        created_at=start,
    )


def make_memory(tmp_path: Path, **overrides: Any) -> NarrativeMemory:
    return NarrativeMemory(config=make_config(tmp_path, **overrides))


# ---------------------------------------------------------------------------
# Configuration, gate, models, and source seam
# ---------------------------------------------------------------------------


def test_narrative_gate_is_off_by_default_and_has_no_shared_config_dependency() -> None:
    config = NarrativeConfig()
    assert config.enabled is False
    assert config.max_chars == 8_000
    package = Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "memory" / "narrative"
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").endswith("memory_config")
            if isinstance(node, ast.Import):
                assert all(not alias.name.endswith("memory_config") for alias in node.names)


def test_every_narrative_config_key_has_a_reader() -> None:
    fields = set(NarrativeConfig.model_fields)
    assert fields == {
        "enabled",
        "max_events",
        "max_chars",
        "max_chapters",
        "min_importance",
        "lookback_days",
        "synthesis_model",
        "enable_model_synthesis",
        "storage_path",
    }
    package = Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "memory" / "narrative"
    blob = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    unread = sorted(field for field in fields if field not in blob)
    assert unread == []


def test_ingest_accepts_plain_episode_dicts_and_preserves_order(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    result = memory.ingest(
        [
            {
                "id": "trace-2",
                "action": "Restored service",
                "observation": "The outage was recovered",
                "outcome": "success",
                "timestamp": 200.0,
                "salience": 0.9,
            },
            {
                "id": "trace-1",
                "action": "Migrated billing",
                "observation": "Billing moved to the new service",
                "outcome": "shipped",
                "timestamp": 100.0,
                "salience": 0.8,
            },
        ],
        now=300.0,
    )
    assert result.status is IngestStatus.STORED
    assert [event.id for event in memory.timeline()] == ["trace-1", "trace-2"]
    assert memory.timeline()[0].importance == 80.0


def test_model_validation_orders_periods_and_discloses_importance_clamp() -> None:
    with pytest.raises(ValidationError):
        NarrativeEvent(
            id="bad",
            scope="user",
            period_start=30.0,
            period_end=10.0,
            title="bad",
            summary="bad",
        )
    event = NarrativeEvent(
        id="clamped",
        scope="user",
        period_start=1,
        period_end=2,
        title="x",
        summary="y",
        importance=140,
    )
    assert event.importance == 100.0
    assert "importance" in event.clamped_fields
    assert event.clamp_disclosure.startswith("clamped:")


# ---------------------------------------------------------------------------
# Bucketing, deterministic synthesis, bounds, and incremental behavior
# ---------------------------------------------------------------------------


def test_period_buckets_follow_span_day_week_month() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    day_events = [make_event("d1", base), make_event("d2", base + 86_400)]
    week_events = [make_event("w1", base), make_event("w2", base + 10 * 86_400)]
    month_events = [make_event("m1", base), make_event("m2", base + 100 * 86_400)]
    assert choose_bucket(day_events) == "day"
    assert choose_bucket(week_events) == "week"
    assert choose_bucket(month_events) == "month"
    assert [chapter.period for chapter in build_chapters(day_events)] == ["2026-01-01", "2026-01-02"]
    assert len(build_chapters(week_events)) == 2
    assert len(build_chapters(month_events)) == 2


def test_deterministic_fallback_is_marked_and_contains_top_outcomes(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.ingest(
        [
            make_event("one", 100, title="Migrated billing", outcomes=["shipped", "verified"]),
            make_event("two", 101, title="Standardized deploys", outcomes=["documented"]),
        ],
        now=200,
    )
    result = memory.regenerate(now=200)
    assert result.status == "ok"
    assert result.document is not None
    assert result.document.synthesis == "deterministic"
    assert all(chapter.synthesis == "deterministic" for chapter in result.document.chapters)
    text = result.document.render()
    assert "Migrated billing" in text
    assert "shipped" in text
    assert "documented" in text


def test_story_char_cap_is_enforced_with_disclosure(tmp_path: Path) -> None:
    memory = make_memory(tmp_path, max_chars=180)
    memory.ingest(
        make_event("long", 100, title="Long " * 100, summary="Summary " * 100, outcomes=["Outcome " * 30]),
        now=200,
    )
    result = memory.regenerate(now=200)
    assert result.document is not None
    assert result.document.total_chars <= 180
    assert len(result.document.render()) <= 180
    assert result.document.truncated is True
    assert "max_chars" in result.document.disclosures


def test_incremental_model_refinement_calls_only_changed_chapters(tmp_path: Path) -> None:
    config = make_config(tmp_path, enable_model_synthesis=True, synthesis_model="fake")
    memory = NarrativeMemory(config=config, model=FakeModel(json.dumps([{"entries": ["polished chapter"]}])))
    memory.ingest(
        [
            make_event("old", 100, title="Old chapter"),
            make_event("new", 10 * 86_400 + 100, title="New chapter"),
        ],
        now=10 * 86_400 + 200,
    )
    first = memory.regenerate(now=10 * 86_400 + 200)
    assert first.status == "ok"
    first_prompt_count = len(memory.model.prompts)
    memory.ingest(make_event("later", 20 * 86_400 + 100, title="Later chapter"), now=20 * 86_400 + 200)
    second = memory.regenerate(now=20 * 86_400 + 200)
    assert second.status == "ok"
    assert len(memory.model.prompts) == first_prompt_count + 1
    assert second.changed_chapters == ["1970-W04"]


@pytest.mark.parametrize(
    ("response", "status"),
    [
        ("not json", SynthesisStatus.PARSE_FAIL),
        ('{"summary": "not an array"}', SynthesisStatus.NOT_ARRAY),
        ("[]", SynthesisStatus.EMPTY),
    ],
)
def test_every_model_parse_failure_uses_closed_status(response: str, status: SynthesisStatus) -> None:
    outcome = parse_model_response(
        response,
        period="2026-01",
        heading="January",
        event_ids=["event"],
    )
    assert outcome.status is status
    assert outcome.chapters == []


@pytest.mark.parametrize("response", ["not json", '{"summary": "not an array"}', "[]", ""])
def test_model_failures_leave_previous_story_intact(tmp_path: Path, response: str) -> None:
    base = make_memory(tmp_path)
    base.ingest(make_event("old", 100), now=200)
    original = base.regenerate(now=200)
    assert original.document is not None
    base.ingest(make_event("new", 10 * 86_400 + 100), now=10 * 86_400 + 200)
    config = make_config(tmp_path, enable_model_synthesis=True, synthesis_model="fake")
    model = FakeModel(response) if response else BrokenModel()
    memory = NarrativeMemory(config=config, store=base.store, model=model)
    result = memory.regenerate(now=10 * 86_400 + 200)
    assert result.story_unchanged is True
    assert result.document is not None
    assert result.document.render() == original.document.render()
    assert result.status in {"no_json", "parse_fail", "not_array", "empty", "llm_error"}


# ---------------------------------------------------------------------------
# Store isolation, corruption, eviction, recall, timeline, and concurrency
# ---------------------------------------------------------------------------


def test_scope_isolation_and_deterministic_bounded_eviction(tmp_path: Path) -> None:
    store = NarrativeStore(str(tmp_path / "store"), config=make_config(tmp_path, max_events=2))
    store.append_events([make_event("a", 1, importance=10), make_event("b", 2, importance=20)], "user")
    store.append_events([make_event("c", 3, importance=30)], "user")
    assert [event.id for event in store.list_events("user")] == ["b", "c"]
    store.append_events([make_event("p", 4, scope="project")], "project")
    assert [event.id for event in store.list_events("user")] == ["b", "c"]
    assert [event.id for event in store.list_events("project")] == ["p"]


def test_corrupt_event_and_story_documents_are_preserved(tmp_path: Path) -> None:
    store = NarrativeStore(str(tmp_path / "store"))
    event_file = events_path(store.root, "user")
    event_file.parent.mkdir(parents=True, exist_ok=True)
    event_file.write_bytes(b"{broken")
    assert store.list_events("user") == []
    assert store.read_status("user") == "corrupt_document_preserved"
    assert len(list(event_file.parent.glob("events.json.corrupt-*"))) == 1

    story_file = story_path(store.root, "project")
    story_file.parent.mkdir(parents=True, exist_ok=True)
    story_file.write_bytes(b"not a story")
    assert store.get_story("project") is None
    assert store.story_status("project") == "corrupt_document_preserved"
    assert len(list(story_file.parent.glob("story.json.corrupt-*"))) == 1


def test_story_block_is_bounded_and_empty_without_a_story(tmp_path: Path) -> None:
    memory = make_memory(tmp_path, max_chars=180)
    assert memory.story_block("user") == ""
    memory.ingest([make_event(str(index), 100 + index, title=f"Event {index}") for index in range(8)], now=200)
    memory.regenerate(now=200)
    block = memory.story_block("user", limit=2)
    assert len(block.splitlines()) <= 2
    assert len(block) <= 180
    assert "Event" in block


def test_timeline_range_and_query_filters(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.ingest(
        [
            make_event("early", 100, title="Billing migration", summary="Move billing"),
            make_event("late", 200, title="Outage recovery", summary="Restore service"),
        ],
        now=300,
    )
    assert [event.id for event in memory.timeline(start=150, end=250)] == ["late"]
    assert memory.moments(top_k=1, query="outage")[0].id == "late"
    assert "Outage recovery" in memory.range_summary(start=150, end=250)


def test_concurrent_regeneration_leaves_one_consistent_document(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.ingest([make_event(str(index), 100 + index) for index in range(5)], now=200)
    results: list[Any] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        try:
            barrier.wait()
            results.append(memory.regenerate(now=200))
        except BaseException as exc:  # pragma: no cover - assertion reports the failure
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(results) == 2
    document = memory.get_story("user")
    assert document is not None
    assert document.total_chars == len(document.render())
    assert document.source_event_count == 5


def test_provenance_records_ingest_and_regeneration_counts(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.ingest([make_event("a", 100)], now=100)
    result = memory.regenerate(now=100)
    entries = read_entries("1970-01-01", scope="user", storage_path=str(memory.store.root))
    assert [entry["action"] for entry in entries] == ["event_ingest", "story_regeneration"]
    assert entries[0]["event_count"] == 1
    assert entries[1]["chapters_changed_count"] == len(result.changed_chapters)
    assert entries[1]["chars"] == result.chars
    assert entries[1]["synthesis"] == "deterministic"


def test_store_round_trip_and_document_order_validation(tmp_path: Path) -> None:
    store = NarrativeStore(str(tmp_path / "store"))
    store.append_events([make_event("a", 2), make_event("b", 1)], "user")
    fresh = NarrativeStore(str(tmp_path / "store"))
    assert [event.id for event in fresh.list_events("user")] == ["b", "a"]
    with pytest.raises(ValidationError):
        StoryDocument(
            scope="user",
            chapters=[
                {"heading": "later", "period": "b", "period_start": 20},
                {"heading": "earlier", "period": "a", "period_start": 10},
            ],
            total_chars=1,
        )
