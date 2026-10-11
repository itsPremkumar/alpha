"""Tests for alpha.graph.ids — stable, sortable, prefixed identifiers."""

from __future__ import annotations

import pytest

from alpha.graph.ids import (
    make_id,
    new_artifact_id,
    new_attempt_id,
    new_claim_id,
    new_event_id,
    new_goal_id,
    new_task_id,
    timestamp_of,
)


class TestIdShape:
    def test_make_id_has_prefix_and_timestamp(self) -> None:
        identifier = make_id("goal")
        assert identifier.startswith("goal_")
        remainder = identifier[5:]
        assert len(remainder) >= 19  # 10 time + 4 counter + 5 entropy

    def test_make_id_rejects_empty_prefix(self) -> None:
        with pytest.raises(ValueError, match="alphanumeric"):
            make_id("")

    def test_make_id_rejects_non_alphanumeric_prefix(self) -> None:
        with pytest.raises(ValueError, match="alphanumeric"):
            make_id("not-valid")

    def test_make_id_rejects_negative_timestamp(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            make_id("goal", timestamp_ms=-1)

    def test_make_id_rejects_short_entropy(self) -> None:
        with pytest.raises(ValueError, match="base32"):
            make_id("goal", entropy="")

    def test_make_id_rejects_long_entropy(self) -> None:
        with pytest.raises(ValueError, match="exceeds"):
            make_id("goal", entropy="ABCDEFG")

    def test_make_id_accepts_valid_entropy(self) -> None:
        identifier = make_id("goal", entropy="ABC")
        assert identifier.endswith("00ABC") or "ABC" in identifier


class TestIdUniqueness:
    def test_concurrent_ids_are_unique(self) -> None:
        ids = [make_id("task") for _ in range(1000)]
        assert len(set(ids)) == 1000

    def test_ids_from_different_prefixes_are_distinct(self) -> None:
        goal_id = new_goal_id()
        task_id = new_task_id()
        assert goal_id != task_id
        assert goal_id.startswith("goal_")
        assert task_id.startswith("task_")


class TestIdSortability:
    def test_ids_are_sortable_by_time(self) -> None:
        first = make_id("evt", timestamp_ms=1000)
        second = make_id("evt", timestamp_ms=2000)
        assert first < second

    def test_ids_within_same_millisecond_are_sortable_by_counter(self) -> None:
        first = make_id("evt", timestamp_ms=1000, entropy="00001")
        second = make_id("evt", timestamp_ms=1000, entropy="00002")
        assert first < second

    def test_timestamp_of_recovers_milliseconds(self) -> None:
        identifier = make_id("goal", timestamp_ms=1234567890)
        assert timestamp_of(identifier) == 1234567890

    def test_timestamp_of_returns_none_for_non_id(self) -> None:
        assert timestamp_of("not-an-id") is None
        assert timestamp_of("") is None
        assert timestamp_of("goal_") is None


class TestIdPrefixes:
    def test_all_factory_functions_use_correct_prefix(self) -> None:
        assert new_goal_id().startswith("goal_")
        assert new_task_id().startswith("task_")
        assert new_event_id().startswith("evt_")
        assert new_attempt_id().startswith("attempt_")
        assert new_claim_id().startswith("claim_")
        assert new_artifact_id().startswith("art_")
