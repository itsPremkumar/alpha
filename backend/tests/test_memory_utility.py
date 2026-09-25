"""Hermetic contract tests for the opt-in memory-utility feedback plane."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from alpha.memory.utility.config import UtilityConfig
from alpha.memory.utility.dedup import DedupPolicy, suggest_dedup
from alpha.memory.utility.feedback import MemoryUtility
from alpha.memory.utility.models import (
    BudgetState,
    DedupSuggestion,
    FeedbackPolicy,
    RetentionAction,
    RetentionDecision,
    ScoreLabel,
    SignalStatus,
    UtilityObservation,
    UtilityRecord,
)
from alpha.memory.utility.paths import document_path, utility_root
from alpha.memory.utility.retention import RetentionPolicy, policy_decision, retention_decisions
from alpha.memory.utility.scoring import calibrate, calibrated_score, score_observations, score_records
from alpha.memory.utility.signals import FeedbackNormalizer, normalize_feedback, normalize_signal
from alpha.memory.utility.store import UtilityStore


class FakeClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def make_config(tmp_path: Path, **overrides: Any) -> UtilityConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "storage_path": str(tmp_path / "utility-state"),
        "decay_half_life": 10.0,
        "min_calibration_samples": 3,
        "max_observations_per_scope": 20,
    }
    values.update(overrides)
    return UtilityConfig.from_mapping(values)


def make_observation(
    observation_id: str,
    record_id: str = "record-1",
    event: str = "clicked",
    observed_at: float = 1_000.0,
    source: str = "ui",
) -> UtilityObservation:
    return UtilityObservation(
        observation_id=observation_id,
        record_id=record_id,
        event=event,
        source=source,
        observed_at=observed_at,
    )


def make_record(
    record_id: str,
    score: float,
    *,
    first_seen: float = 100.0,
    last_seen: float = 100.0,
    contradiction_count: int = 0,
) -> UtilityRecord:
    return UtilityRecord(
        record_id=record_id,
        score=score,
        observation_count=1,
        first_seen=first_seen,
        last_seen=last_seen,
        confidence=0.5,
        disclosure="heuristic test fixture",
        contradiction_count=contradiction_count,
        event_counts={"contradicted": contradiction_count} if contradiction_count else {},
    )


def test_config_defaults_and_every_key_has_a_reader(tmp_path: Path) -> None:
    default = UtilityConfig()
    assert default.enabled is False
    assert default.storage_path is None

    values = {
        "enabled": True,
        "decay_half_life": 12.5,
        "keep_threshold": 0.7,
        "demote_threshold": 0.4,
        "evict_threshold": 0.2,
        "min_calibration_samples": 4,
        "max_observations_per_scope": 9,
        "demote_budget_pressure": 0.45,
        "evict_budget_pressure": 0.75,
        "storage_path": str(tmp_path / "state"),
    }
    config = UtilityConfig.from_mapping(values)
    assert config.read_all_keys() == values
    for key, value in values.items():
        assert config.read_key(key) == value
    assert config.read_key("not_a_key", "fallback") == "fallback"
    assert config.resolved_storage_path() == utility_root(values["storage_path"])

    with pytest.raises(ValueError):
        UtilityConfig(keep_threshold=0.2, demote_threshold=0.3, evict_threshold=0.4)
    with pytest.raises(ValueError):
        UtilityConfig(decay_half_life=0.0)


def test_signals_explicit_override_clock_and_idempotency() -> None:
    clock = FakeClock()
    normalizer = FeedbackNormalizer(clock=clock)
    payload = {
        "observation_id": "provider-1",
        "record_id": "r1",
        "event": "click",
        "source": "test-ui",
    }
    first = normalizer.normalize(payload)
    second = normalizer.normalize(payload)
    assert first.status == SignalStatus.ACCEPTED
    assert first.observation is not None
    assert first.observation.event == "clicked"
    assert first.observation.observed_at == 1_000.0
    assert second.status == SignalStatus.DUPLICATE
    assert "score was not changed" in second.disclosure

    batch = normalizer.normalize_many([payload, payload])
    assert [item.status for item in batch] == [SignalStatus.DUPLICATE, SignalStatus.DUPLICATE]


def test_signals_reject_malformed_and_disclose_missing_source_or_clock() -> None:
    assert normalize_signal({"record_id": "r", "event": "not-a-real-event", "source": "ui", "observed_at": 1.0}).status == SignalStatus.REJECTED
    missing_source = normalize_signal({"record_id": "r", "event": "clicked", "observed_at": 1.0})
    assert missing_source.status == SignalStatus.UNAVAILABLE
    assert "feedback source" in missing_source.disclosure

    missing_clock = normalize_signal({"record_id": "r", "event": "clicked", "source": "ui"})
    assert missing_clock.status == SignalStatus.UNAVAILABLE
    assert "clock" in missing_clock.disclosure
    assert normalize_signal({"record_id": "r", "event": "clicked", "source": "ui", "weight": float("nan")}).status == SignalStatus.REJECTED


def test_decay_half_life_boundary_and_confirmation_bonus() -> None:
    config = UtilityConfig(decay_half_life=10.0)
    recent = score_observations([make_observation("recent", observed_at=1_000.0)], now=1_000.0, config=config)
    old = score_observations([make_observation("old", observed_at=990.0)], now=1_000.0, config=config)
    assert recent.sample_size == old.sample_size == 1
    assert recent.score is not None and old.score is not None
    assert recent.score > old.score
    assert old.decay == pytest.approx(0.5)
    assert old.terms["positive_evidence"] == pytest.approx(recent.terms["positive_evidence"] * 0.5)

    ordinary = score_observations([make_observation("ordinary", event="surfaced")], now=1_000.0, config=config)
    confirmed = score_observations(
        [make_observation("confirmed", event="confirmed", source="explicit")],
        now=1_000.0,
        config=config,
    )
    assert confirmed.score is not None and ordinary.score is not None
    assert confirmed.score > ordinary.score
    assert confirmed.terms["confirmation_bonus"] > 0.0


def test_contradiction_penalty_and_quarantine_without_deletion(tmp_path: Path) -> None:
    positive = score_observations(
        [make_observation("positive"), make_observation("confirm", event="confirmed")],
        now=1_000.0,
    )
    contradicted = score_observations(
        [make_observation("positive-2"), make_observation("conflict", event="contradicted")],
        now=1_000.0,
    )
    assert positive.score is not None and contradicted.score is not None
    assert contradicted.score < positive.score
    assert contradicted.contradiction_count == 1

    config = make_config(tmp_path)
    utility = MemoryUtility(config, clock=FakeClock())
    assert utility.observe({"observation_id": "c1", "record_id": "conflict", "event": "clicked", "source": "ui", "observed_at": 1_000.0}).accepted
    assert utility.observe({"observation_id": "x1", "record_id": "conflict", "event": "contradicted", "source": "reviewer", "observed_at": 1_000.0}).accepted
    decision = utility.retention_decisions()[0]
    assert decision.action == RetentionAction.QUARANTINE
    assert "contradictory evidence" in decision.reason
    assert utility.store.count() == 1


def test_calibration_refuses_small_samples_and_flips_only_after_minimum() -> None:
    samples = [make_observation(f"sample-{index}", observed_at=1_000.0 + index) for index in range(3)]
    config = UtilityConfig(min_calibration_samples=3)
    refused = calibrate(samples[:2], config=config)
    assert refused.calibrated is False
    assert refused.label == ScoreLabel.HEURISTIC
    assert refused.sample_size == 2
    assert "calibration_refused" in refused.disclosure
    assert "minimum of 3" in refused.disclosure
    refused_score = score_observations(samples[:2], now=1_002.0, config=config, calibration=refused)
    assert refused_score.label == ScoreLabel.HEURISTIC
    assert "heuristic" in refused_score.disclosure

    accepted = calibrate(samples, config=config)
    assert accepted.calibrated is True
    assert accepted.label == ScoreLabel.CALIBRATED
    assert accepted.sample_size == 3
    calibrated = score_observations(samples, now=1_002.0, config=config, calibration=accepted)
    assert calibrated.label == ScoreLabel.CALIBRATED
    assert calibrated.record is not None
    assert calibrated.record.calibration_sample_size == 3


def test_retention_bands_boundaries_quarantine_and_determinism() -> None:
    config = UtilityConfig(
        keep_threshold=0.65,
        demote_threshold=0.35,
        evict_threshold=0.10,
        demote_budget_pressure=0.50,
        evict_budget_pressure=0.80,
    )
    records = [
        make_record("keep-boundary", 0.65),
        make_record("demote-boundary", 0.35),
        make_record("evict-boundary", 0.10),
        make_record("just-below-demote", 0.349999),
        make_record("just-below-evict", 0.099999),
        make_record("contradictory", 0.99, contradiction_count=1),
    ]
    decisions = retention_decisions(records, config=config)
    assert [item.record_id for item in decisions] == sorted(item.record_id for item in decisions)
    by_id = {item.record_id: item for item in decisions}
    assert by_id["keep-boundary"].action == RetentionAction.KEEP
    assert by_id["demote-boundary"].action == RetentionAction.DEMOTE
    assert by_id["evict-boundary"].action == RetentionAction.DEMOTE
    assert by_id["just-below-demote"].action == RetentionAction.DEMOTE
    assert by_id["just-below-evict"].action == RetentionAction.EVICT
    assert by_id["contradictory"].action == RetentionAction.QUARANTINE
    assert all(item.reason and item.disclosure for item in decisions)
    assert retention_decisions(records, config=config) == decisions

    softened = retention_decisions(
        [make_record("keep", 0.65)],
        budget_state=BudgetState(total=100.0, used=50.0),
        config=config,
    )
    assert softened[0].action == RetentionAction.DEMOTE
    pressured = retention_decisions(
        [make_record("demote", 0.35)],
        budget_state=BudgetState(total=100.0, used=80.0),
        config=config,
    )
    assert pressured[0].action == RetentionAction.EVICT
    assert BudgetState(total=10.0, used=5.0).pressure == pytest.approx(0.5)
    policy = RetentionPolicy(config).decisions([make_record("x", 0.65)])
    assert policy[0].action == RetentionAction.KEEP
    direct_policy = FeedbackPolicy(keep_threshold=0.65, demote_threshold=0.35, evict_threshold=0.1)
    assert direct_policy.keep_threshold == 0.65


def test_dedup_suggests_survivor_and_never_removes_store_records(tmp_path: Path) -> None:
    store = UtilityStore(tmp_path, max_observations_per_scope=10)
    records = [
        make_record("a", 0.40, first_seen=1.0, last_seen=1.0),
        make_record("b", 0.80, first_seen=2.0, last_seen=2.0),
        make_record("c", 0.80, first_seen=3.0, last_seen=3.0),
    ]
    store.put_records(records)
    before = {item.record_id for item in store.list_records()}
    suggestions = suggest_dedup(
        records,
        similarity={"a|b": 0.95, "a|c": 0.2, "b|c": 0.2},
        threshold=0.85,
    )
    assert suggestions == [
        DedupSuggestion(
            record_ids=["a", "b"],
            suggested_survivor="b",
            why=suggestions[0].why,
            merged_utility=0.8,
        )
    ]
    assert store.list_records() != []
    assert {item.record_id for item in store.list_records()} == before

    tied = suggest_dedup(
        [make_record("old", 0.5, first_seen=1.0), make_record("new", 0.5, first_seen=9.0)],
        similarity=lambda left, right: 0.99,
        threshold=0.85,
    )
    assert tied[0].suggested_survivor == "old"
    assert "no records removed" in tied[0].why


def test_store_round_trip_bounded_eviction_and_disclosure(tmp_path: Path) -> None:
    store = UtilityStore(tmp_path, max_observations_per_scope=2)
    records = [
        make_record("a", 0.10, first_seen=1.0, last_seen=1.0),
        make_record("b", 0.10, first_seen=1.0, last_seen=1.0),
        make_record("high", 0.90, first_seen=2.0, last_seen=2.0),
    ]
    assert store.put_records(records) == 3
    assert [item.record_id for item in store.list_records()] == ["b", "high"]
    notices = store.evictions()
    assert [item.record_id for item in notices] == ["a"]
    assert "lowest_utility_oldest_tie_id" in notices[0].reason
    fresh = UtilityStore(tmp_path, max_observations_per_scope=2)
    assert [item.record_id for item in fresh.list_records()] == ["b", "high"]
    assert fresh.snapshot()["max_observations_per_scope"] == 2
    assert json.loads(store._path().read_text(encoding="utf-8"))["schema"] == 1


def test_store_preserves_corrupt_document_and_recovers_empty(tmp_path: Path) -> None:
    store = UtilityStore(tmp_path, max_observations_per_scope=5)
    store.put(make_record("safe", 0.5))
    path = store._path()
    original = b"{ definitely not json"
    path.write_bytes(original)
    recovered = UtilityStore(tmp_path, max_observations_per_scope=5)
    assert recovered.list_records() == []
    backups = list(path.parent.glob(f"{path.name}.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert recovered.corruption_events()


def test_store_concurrent_writes_are_safe(tmp_path: Path) -> None:
    store = UtilityStore(tmp_path, max_observations_per_scope=100)
    records = [make_record(f"r-{index:03d}", index / 100.0, first_seen=float(index), last_seen=float(index)) for index in range(40)]

    def write(record: UtilityRecord) -> int:
        return store.put(record)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(write, records))
    assert sum(results) == 40
    assert store.count() == 40
    assert len(json.loads(store._path().read_text(encoding="utf-8"))["records"]) == 40


def test_facade_observe_idempotency_score_rank_apply_and_reset(tmp_path: Path) -> None:
    config = make_config(tmp_path, max_observations_per_scope=10)
    clock = FakeClock()
    utility = MemoryUtility(config, clock=clock)
    accepted = utility.observe(
        {"observation_id": "one", "record_id": "r1", "event": "click", "source": "ui"},
        now=1_000.0,
    )
    duplicate = utility.observe(
        {"observation_id": "one", "record_id": "r1", "event": "click", "source": "ui"},
        now=1_000.0,
    )
    assert accepted.status == SignalStatus.ACCEPTED
    assert duplicate.status == SignalStatus.DUPLICATE
    assert utility.score("r1", now=1_000.0).score == accepted.score
    assert utility.store.count() == 1

    utility.apply(
        [
            make_record("r2", 0.2, first_seen=10.0, last_seen=10.0),
            RetentionDecision(
                record_id="host-only",
                action=RetentionAction.EVICT,
                reason="must not be applied",
                utility=0.0,
                threshold=0.1,
                budget_pressure=0.0,
            ),
        ]
    )
    assert utility.store.count() == 2
    assert "no memory record was deleted" in utility.last_apply_disclosure
    assert [item.record_id for item in utility.rank()] == ["r1", "r2"]
    snapshot = utility.snapshot()
    assert snapshot["enabled"] is True
    assert len(snapshot["records"]) == 2
    assert snapshot["config"]["max_observations_per_scope"] == 10
    assert utility.reset_scope() is True
    assert utility.store.count() == 0


def test_facade_does_not_mutate_an_external_host_document(tmp_path: Path) -> None:
    host_document = tmp_path / "host-records.json"
    host_document.write_text('{"records":["host"]}', encoding="utf-8")
    utility = MemoryUtility(make_config(tmp_path), clock=FakeClock())
    utility.apply(make_record("utility-only", 0.4))
    assert host_document.read_text(encoding="utf-8") == '{"records":["host"]}'
    assert utility.store.get("utility-only") is not None


def test_default_off_facade_is_a_no_op_for_every_entry_point(tmp_path: Path) -> None:
    state = tmp_path / "disabled-state"
    utility = MemoryUtility(UtilityConfig(storage_path=str(state)), clock=FakeClock())
    result = utility.observe({"record_id": "r", "event": "clicked", "source": "ui"})
    assert result.status == SignalStatus.DISABLED
    assert utility.observe_many([{"record_id": "r", "event": "clicked", "source": "ui"}]) == []
    assert utility.score("r") is None
    assert utility.rank() == []
    assert utility.retention_decisions(budget_state=BudgetState(total=1.0, used=1.0)) == []
    assert utility.dedup_suggestions(similarity=lambda _left, _right: 1.0) == []
    assert utility.apply(make_record("r", 0.5)) == 0
    assert utility.reset_scope() is False
    assert utility.snapshot()["records"] == []
    assert utility.calibrate([make_observation("x")]).calibrated is False
    assert not state.exists()


def test_missing_clock_is_unavailable_in_enabled_facade(tmp_path: Path) -> None:
    utility = MemoryUtility(make_config(tmp_path))
    outcome = utility.observe({"record_id": "r", "event": "clicked", "source": "ui"})
    assert outcome.status == SignalStatus.UNAVAILABLE
    assert "clock" in outcome.disclosure
    assert utility.store.count() == 0
    unknown = utility.score("does-not-exist")
    assert unknown is not None
    assert unknown.label == ScoreLabel.UNAVAILABLE
    assert unknown.score is None


def test_paths_scope_safety_and_atomic_replacement(tmp_path: Path) -> None:
    root = utility_root(tmp_path)
    path = document_path(root, "../user/name", "agent/name")
    assert ".." not in path.name
    assert path.parent.parent.name == "user_name"
    assert path.suffix == ".json"
    path.parent.mkdir(parents=True, exist_ok=True)
    from alpha.memory.utility.paths import atomic_write_text

    atomic_write_text(path, "first")
    atomic_write_text(path, "second")
    assert path.read_text(encoding="utf-8") == "second"
    assert not list(path.parent.glob("*.tmp"))


def test_public_seams_aliases_and_cross_instance_store_refresh(tmp_path: Path) -> None:
    import alpha.memory.utility as package

    assert package.MemoryUtility is MemoryUtility
    assert package.UtilityFeedback is MemoryUtility
    assert package.OVERRIDE_MAP["click"] == "clicked"
    samples = [make_observation(f"public-{index}", observed_at=10.0 + index) for index in range(3)]
    grouped = score_records(samples, now=12.0)
    assert set(grouped) == {"record-1"}
    calibrated = calibrated_score(samples[:1], samples, now=12.0, config=UtilityConfig(min_calibration_samples=3))
    assert calibrated.label == ScoreLabel.CALIBRATED
    assert calibrated.calibration_sample_size == 3
    batch = normalize_feedback(
        [
            {"observation_id": "batch-1", "record_id": "r", "event": "recall", "source": "ui"},
            {"observation_id": "batch-1", "record_id": "r", "event": "recall", "source": "ui"},
        ],
        clock=FakeClock(12.0),
    )
    assert [item.status for item in batch] == [SignalStatus.ACCEPTED, SignalStatus.DUPLICATE]
    assert policy_decision(make_record("policy", 0.7), config=UtilityConfig()).action == RetentionAction.KEEP
    proposals = DedupPolicy(similarity=lambda _left, _right: 1.0).suggest([make_record("p1", 0.4), make_record("p2", 0.5)])
    assert proposals[0].suggested_survivor == "p2"

    config_file = tmp_path / "utility.json"
    config_file.write_text(json.dumps({"enabled": True, "storage_path": str(tmp_path / "state")}), encoding="utf-8")
    from alpha.memory.utility.config import load_config

    assert load_config(config_file).enabled is True

    first_store = UtilityStore(tmp_path / "shared")
    second_store = UtilityStore(tmp_path / "shared")
    first_store.upsert(make_record("shared-1", 0.4))
    second_store.upsert(make_record("shared-2", 0.5))
    assert {item.record_id for item in first_store.all_records()} == {"shared-1", "shared-2"}
    assert first_store.eviction_log() == []


def test_facade_calibration_method_and_restart_decay(tmp_path: Path) -> None:
    config = make_config(tmp_path, min_calibration_samples=2)
    clock = FakeClock(1_000.0)
    utility = MemoryUtility(config, clock=clock)
    utility.observe({"observation_id": "a", "record_id": "r", "event": "clicked", "source": "ui"})
    utility.observe({"observation_id": "b", "record_id": "r", "event": "confirmed", "source": "explicit"})
    calibration = utility.calibrate([make_observation("c1"), make_observation("c2")])
    assert calibration.calibrated is True
    fresh = MemoryUtility(config, clock=clock)
    current = fresh.score("r", now=1_000.0)
    assert current is not None
    clock.value = 1_010.0
    decayed = fresh.score("r", now=clock())
    assert decayed is not None and current.score is not None
    assert decayed.score < current.score
    assert utility.snapshot()["calibration"]["sample_size"] == 2
